"""Periodic sweep services for OMEGA-010 Smart Scheduler.

Implements background worker jobs for:
1. Dispatch sweep (due ACTIVE reservations -> DISPATCHING + outbox)
2. Outbox relay (processes pending/retry outbox rows)
3. Expiration sweep (past-due ACTIVE reservations -> EXPIRED)
4. Stale DISPATCHING recovery (reconciles stalled dispatches based on task/outbox state)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.scheduler.dispatch_fence import DispatchFence
from omega.domain.scheduler import (
    DispatchFenceResult,
    DispatchOutboxStatus,
    ReservationState,
)
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    SchedulerDispatchOutbox,
    ScheduleReservation,
    ScheduleStateTransition,
    Task,
)
from omega.logging import get_logger

logger = get_logger(service="omega-scheduler-sweep")


class SchedulerSweepService:
    """Orchestrates periodic background sweeps for the scheduler subsystem."""

    @staticmethod
    async def run_dispatch_sweep(
        session: AsyncSession,
        *,
        batch_size: int = 20,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Claim due ACTIVE reservations, execute Dispatch Fence, and create transactional outbox items."""
        if now is None:
            now = datetime.now(UTC)

        stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.state == ReservationState.ACTIVE.value,
                ScheduleReservation.scheduled_start_at <= now,
            )
            .order_by(
                ScheduleReservation.priority_score.desc(),
                ScheduleReservation.scheduled_start_at.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        res = await session.execute(stmt)
        due_reservations = list(res.scalars().all())

        dispatched_count = 0
        rejected_count = 0

        for reservation in due_reservations:
            # 1. Execute Dispatch Fence
            fence_result, reason = await DispatchFence.evaluate_fence(session, reservation, now=now)

            if fence_result != DispatchFenceResult.PROCEED:
                rejected_count += 1
                logger.info(
                    "dispatch_sweep_fence_rejected",
                    reservation_id=str(reservation.id),
                    fence_result=fence_result.value,
                    reason=reason,
                )
                continue

            # 2. Transition reservation: ACTIVE -> DISPATCHING
            reservation.state = ReservationState.DISPATCHING.value
            reservation.dispatching_at = now
            reservation.updated_at = now

            # 3. Transition Task to QUEUED if applicable
            task_id = (
                reservation.target_id
                if reservation.target_type == "TASK_EXECUTION"
                else reservation.decision.task_id
            )
            if task_id:
                task_res = await session.execute(
                    select(Task).where(Task.id == task_id).with_for_update()
                )
                task = task_res.scalar_one_or_none()
                if task:
                    task.state = TaskState.QUEUED.value
                    task.queued_at = now
                    task.dispatched_epoch = reservation.guardian_epoch
                    task.updated_at = now

            # 4. Insert transition audit
            transition = ScheduleStateTransition(
                id=uuid4(),
                reservation_id=reservation.id,
                from_state=ReservationState.ACTIVE.value,
                to_state=ReservationState.DISPATCHING.value,
                reason="Due reservation claimed by dispatch sweep",
                actor="DISPATCH_SWEEP",
                created_at=now,
            )
            session.add(transition)

            # 5. Insert SchedulerDispatchOutbox PENDING row
            # Format task name and args
            celery_task_name = "omega.tasks.execute"
            celery_args = {"args": [str(task_id)] if task_id else [str(reservation.target_id)]}

            outbox_item = SchedulerDispatchOutbox(
                id=uuid4(),
                reservation_id=reservation.id,
                task_id=task_id or reservation.target_id,
                mission_id=reservation.mission_id,
                celery_task_name=celery_task_name,
                celery_args=celery_args,
                idempotency_key=f"outbox:{str(reservation.id)}",
                status=DispatchOutboxStatus.PENDING.value,
                attempt_count=0,
                max_attempts=5,
                scheduled_send_at=now,
                created_at=now,
            )
            session.add(outbox_item)
            dispatched_count += 1

            logger.info(
                "reservation_dispatching",
                reservation_id=str(reservation.id),
                task_id=str(task_id) if task_id else None,
                outbox_id=str(outbox_item.id),
            )

        await session.commit()
        return {
            "claimed": len(due_reservations),
            "dispatched": dispatched_count,
            "rejected": rejected_count,
        }

    @staticmethod
    async def run_expiration_sweep(
        session: AsyncSession,
        *,
        batch_size: int = 50,
        now: datetime | None = None,
    ) -> int:
        """Expire ACTIVE reservations that have exceeded their expires_at timestamp."""
        if now is None:
            now = datetime.now(UTC)

        stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.state == ReservationState.ACTIVE.value,
                ScheduleReservation.expires_at.is_not(None),
                ScheduleReservation.expires_at <= now,
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        res = await session.execute(stmt)
        expired_reservations = list(res.scalars().all())

        for reservation in expired_reservations:
            reservation.state = ReservationState.EXPIRED.value
            reservation.updated_at = now

            transition = ScheduleStateTransition(
                id=uuid4(),
                reservation_id=reservation.id,
                from_state=ReservationState.ACTIVE.value,
                to_state=ReservationState.EXPIRED.value,
                reason=f"Reservation expired at {reservation.expires_at.isoformat() if reservation.expires_at else 'unknown'}",
                actor="EXPIRATION_SWEEP",
                created_at=now,
            )
            session.add(transition)

            logger.info(
                "reservation_expired",
                reservation_id=str(reservation.id),
                expires_at=reservation.expires_at.isoformat() if reservation.expires_at else None,
            )

        await session.commit()
        return len(expired_reservations)

    @staticmethod
    async def run_stale_dispatching_recovery_sweep(
        session: AsyncSession,
        *,
        timeout_seconds: int = 300,
        batch_size: int = 50,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Reconcile reservations stuck in DISPATCHING state based on task and outbox status."""
        if now is None:
            now = datetime.now(UTC)

        cutoff = now - timedelta(seconds=timeout_seconds)

        stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.state == ReservationState.DISPATCHING.value,
                ScheduleReservation.dispatching_at <= cutoff,
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        res = await session.execute(stmt)
        stale_reservations = list(res.scalars().all())

        consumed_count = 0
        released_count = 0
        requeued_count = 0

        for reservation in stale_reservations:
            # Query associated task
            task_id = (
                reservation.target_id
                if reservation.target_type == "TASK_EXECUTION"
                else reservation.decision.task_id
            )
            task = None
            if task_id:
                task_res = await session.execute(
                    select(Task).where(Task.id == task_id).with_for_update()
                )
                task = task_res.scalar_one_or_none()

            # Query associated outbox items
            outbox_res = await session.execute(
                select(SchedulerDispatchOutbox)
                .where(SchedulerDispatchOutbox.reservation_id == reservation.id)
                .order_by(SchedulerDispatchOutbox.created_at.desc())
            )
            latest_outbox = outbox_res.scalars().first()

            # Recovery Matrix:
            # 1. Task is RUNNING or SUCCEEDED -> work is active/done, mark reservation CONSUMED
            if task and task.state in (TaskState.RUNNING.value, TaskState.SUCCEEDED.value):
                reservation.state = ReservationState.CONSUMED.value
                reservation.consumed_at = task.started_at or now
                reservation.updated_at = now
                session.add(
                    ScheduleStateTransition(
                        id=uuid4(),
                        reservation_id=reservation.id,
                        from_state=ReservationState.DISPATCHING.value,
                        to_state=ReservationState.CONSUMED.value,
                        reason=f"Task already {task.state}, recovered reservation to CONSUMED",
                        actor="STALE_DISPATCH_RECOVERY",
                        created_at=now,
                    )
                )
                consumed_count += 1

            # 2. Outbox is DEAD_LETTER -> permanent failure, release reservation
            elif latest_outbox and latest_outbox.status == DispatchOutboxStatus.DEAD_LETTER.value:
                reservation.state = ReservationState.RELEASED.value
                reservation.released_at = now
                reservation.updated_at = now
                session.add(
                    ScheduleStateTransition(
                        id=uuid4(),
                        reservation_id=reservation.id,
                        from_state=ReservationState.DISPATCHING.value,
                        to_state=ReservationState.RELEASED.value,
                        reason=f"Outbox dispatch permanently failed ({latest_outbox.last_error})",
                        actor="STALE_DISPATCH_RECOVERY",
                        created_at=now,
                    )
                )
                released_count += 1

            # 3. Outbox was SENT but task is still QUEUED -> message was likely lost in broker; reset outbox to PENDING to redeliver
            elif (
                latest_outbox
                and latest_outbox.status == DispatchOutboxStatus.SENT.value
                and task
                and task.state == TaskState.QUEUED.value
            ):
                latest_outbox.status = DispatchOutboxStatus.PENDING.value
                latest_outbox.scheduled_send_at = now
                latest_outbox.sent_at = None
                session.add(
                    ScheduleStateTransition(
                        id=uuid4(),
                        reservation_id=reservation.id,
                        from_state=ReservationState.DISPATCHING.value,
                        to_state=ReservationState.DISPATCHING.value,
                        reason="Task remained QUEUED past timeout; reset outbox to PENDING for re-delivery",
                        actor="STALE_DISPATCH_RECOVERY",
                        created_at=now,
                    )
                )
                requeued_count += 1

            # 4. Outbox is still PENDING / RETRY -> outbox relay is still active, leave in DISPATCHING

        await session.commit()
        return {
            "recovered": len(stale_reservations),
            "consumed": consumed_count,
            "released": released_count,
            "requeued": requeued_count,
        }
