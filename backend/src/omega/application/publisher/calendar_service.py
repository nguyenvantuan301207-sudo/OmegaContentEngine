"""Publish Calendar Service for OMEGA-010 Smart Scheduler integration.

Provides an application facade for scheduling, rescheduling, cancelling, and holding
publisher intents via the canonical OMEGA-010 scheduler primitives:
- ScheduleDecision
- ScheduleReservation
- ScheduleStateTransition
- SchedulerDispatchOutbox

Strict invariants:
1. Reuses OMEGA-010 models directly — zero new columns on PublishIntent.
2. Defends against stale or unapproved dispatch.
3. Decoupled from background Celery dispatch (dispatch is executed exclusively by
   SchedulerSweepService and OutboxRelayService).
4. Full auditability via ScheduleStateTransition.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.scheduler.policy_service import SchedulePolicyService
from omega.domain.publisher import PublishIntentState
from omega.domain.scheduler import (
    ReservationState,
    ScheduleAction,
    SchedulePolicyConfig,
    ScheduleTargetType,
    ScheduleWorkloadCategory,
    compute_policy_checksum,
)
from omega.infrastructure.models import (
    PublishIntent,
    ScheduleDecision,
    ScheduleReservation,
    ScheduleStateTransition,
)
from omega.logging import get_logger

logger = get_logger(service="omega-publish-calendar")


class PublishCalendarError(Exception):
    """Domain error raised during calendar operations."""


class PublishCalendarService:
    """Application facade for publication scheduling using canonical OMEGA-010 models."""

    @staticmethod
    def normalize_schedule_timestamp(
        dt: datetime,
        *,
        now: datetime | None = None,
        allow_past_grace: bool = True,
    ) -> datetime:
        """Validate timezone awareness, normalize to UTC, and apply past-timestamp policy.

        Policy:
        - Naive datetime: REJECT.
        - Timestamp > 1 minute in the past: REJECT.
        - Timestamp within 1 minute grace in the past: ACCEPT (becomes immediately due).
        - Future timestamp: ACCEPT.
        """
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            raise ValueError("Scheduled timestamp must be timezone-aware.")

        dt_utc = dt.astimezone(UTC)
        current_now = now or datetime.now(UTC)

        if allow_past_grace:
            grace_limit = current_now - timedelta(minutes=1)
            if dt_utc < grace_limit:
                raise ValueError(
                    f"Scheduled time cannot be older than 1 minute in the past "
                    f"({dt_utc.isoformat()} < {grace_limit.isoformat()})."
                )

        return dt_utc

    @classmethod
    async def schedule_intent(
        cls,
        session: AsyncSession,
        *,
        intent_id: UUID,
        scheduled_start_at: datetime,
        actor: str,
        reason: str = "Scheduled for publication",
        estimated_duration_seconds: int = 300,
        priority_score: float = 100.0,
        now: datetime | None = None,
    ) -> tuple[ScheduleDecision, ScheduleReservation]:
        """Schedule an APPROVED PublishIntent for publication at scheduled_start_at."""
        current_now = now or datetime.now(UTC)
        start_utc = cls.normalize_schedule_timestamp(scheduled_start_at, now=current_now)
        end_utc = start_utc + timedelta(seconds=estimated_duration_seconds)

        # 1. Lock and verify PublishIntent
        intent_res = await session.execute(
            select(PublishIntent).where(PublishIntent.id == intent_id).with_for_update()
        )
        intent = intent_res.scalar_one_or_none()
        if not intent:
            raise PublishCalendarError(f"PublishIntent {intent_id} not found.")

        if intent.state != PublishIntentState.APPROVED.value:
            raise PublishCalendarError(
                f"Cannot schedule PublishIntent in '{intent.state}' state; must be 'APPROVED'."
            )

        # 2. Verify no active reservation currently exists for this intent
        existing_res = await session.execute(
            select(ScheduleReservation).where(
                ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                ScheduleReservation.target_id == intent_id,
                ScheduleReservation.state == ReservationState.ACTIVE.value,
            )
        )
        if existing_res.scalar_one_or_none():
            raise PublishCalendarError(
                f"An active schedule reservation already exists for PublishIntent {intent_id}. "
                "Use reschedule_intent instead."
            )

        # 3. Resolve active SchedulePolicy for EXTERNAL_PUBLISH
        policy = await SchedulePolicyService.get_active_policy(
            session, ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value
        )
        if not policy:
            # Create a default active policy if none exists (e.g. initial setup/test)
            default_config = {
                "allowed_workloads": [ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value],
                "global_concurrency_limit": 10,
                "channel_concurrency_limit": 2,
                "min_gap_between_channel_items_seconds": 60,
                "max_scheduled_items_per_day": 50,
            }
            policy = await SchedulePolicyService.create_policy(
                session,
                workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
                version="1.0.0-calendar-default",
                policy_config=default_config,
                activate=True,
            )

        policy_config = SchedulePolicyConfig(**policy.policy_config)
        checksum = compute_policy_checksum(policy_config)

        # 4. Generate IDs and create ScheduleDecision
        decision_id = uuid4()
        reservation_id = uuid4()
        raw_key = f"decision:publish:{intent.id}:{start_utc.isoformat()}:{uuid4().hex}"
        idempotency_key = hashlib.sha256(raw_key.encode()).hexdigest()

        decision = ScheduleDecision(
            id=decision_id,
            mission_id=intent.mission_id,
            task_id=intent.task_id,
            channel_id=intent.channel_id,
            target_type=ScheduleTargetType.PUBLISH_INTENT.value,
            target_id=intent.id,
            workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
            action=ScheduleAction.SCHEDULE.value,
            scheduled_start_at=start_utc,
            scheduled_end_at=end_utc,
            reason=reason,
            policy_id=policy.id,
            policy_version=policy.version,
            policy_checksum=checksum,
            channel_dna_revision_id=intent.channel_dna_revision_id,
            reservation_id=reservation_id,
            guardian_epoch=1,
            idempotency_key=idempotency_key,
            diagnostic_context={
                "scheduled_by": actor,
                "original_requested_at": scheduled_start_at.isoformat(),
            },
            evaluated_at=current_now,
            expires_at=end_utc + timedelta(hours=2),
            created_at=current_now,
        )
        session.add(decision)

        # 5. Create ScheduleReservation
        reservation = ScheduleReservation(
            id=reservation_id,
            decision_id=decision_id,
            channel_id=intent.channel_id,
            mission_id=intent.mission_id,
            target_type=ScheduleTargetType.PUBLISH_INTENT.value,
            target_id=intent.id,
            workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
            scheduled_start_at=start_utc,
            scheduled_end_at=end_utc,
            state=ReservationState.ACTIVE.value,
            priority_score=priority_score,
            policy_id=policy.id,
            policy_version=policy.version,
            policy_checksum=checksum,
            channel_dna_revision_id=intent.channel_dna_revision_id,
            guardian_epoch=1,
            version=1,
            expires_at=end_utc + timedelta(hours=2),
            created_at=current_now,
            updated_at=current_now,
        )
        session.add(reservation)

        # 6. Audit state transition
        transition = ScheduleStateTransition(
            id=uuid4(),
            reservation_id=reservation_id,
            from_state="CREATED",
            to_state=ReservationState.ACTIVE.value,
            reason=reason,
            actor=actor,
            created_at=current_now,
        )
        session.add(transition)

        logger.info(
            "publish_intent_scheduled",
            intent_id=str(intent.id),
            reservation_id=str(reservation_id),
            scheduled_start_at=start_utc.isoformat(),
            actor=actor,
        )
        return decision, reservation

    @classmethod
    async def reschedule_intent(
        cls,
        session: AsyncSession,
        *,
        intent_id: UUID,
        new_scheduled_start_at: datetime,
        actor: str,
        reason: str = "Rescheduled publication",
        now: datetime | None = None,
    ) -> tuple[ScheduleDecision, ScheduleReservation]:
        """Reschedule an existing ACTIVE publication reservation using row locking and supersession fencing."""
        current_now = now or datetime.now(UTC)
        new_start_utc = cls.normalize_schedule_timestamp(new_scheduled_start_at, now=current_now)

        # 1. Lock current reservation
        res_stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                ScheduleReservation.target_id == intent_id,
                ScheduleReservation.state == ReservationState.ACTIVE.value,
            )
            .with_for_update()
        )
        old_res = (await session.execute(res_stmt)).scalar_one_or_none()
        if not old_res:
            raise PublishCalendarError(
                f"No active schedule reservation found to reschedule for PublishIntent {intent_id}."
            )

        # 2. Release old reservation under row lock
        from_state = old_res.state
        old_res.state = ReservationState.RELEASED.value
        old_res.released_at = current_now
        old_res.updated_at = current_now
        old_res.version += 1

        release_transition = ScheduleStateTransition(
            id=uuid4(),
            reservation_id=old_res.id,
            from_state=from_state,
            to_state=ReservationState.RELEASED.value,
            reason=f"RESCHEDULED: {reason}",
            actor=actor,
            created_at=current_now,
        )
        session.add(release_transition)

        # 3. Calculate new window
        duration_seconds = max(
            int((old_res.scheduled_end_at - old_res.scheduled_start_at).total_seconds()),
            300,
        )
        new_end_utc = new_start_utc + timedelta(seconds=duration_seconds)

        # 4. Create new ScheduleDecision + ScheduleReservation
        new_decision_id = uuid4()
        new_reservation_id = uuid4()
        raw_key = f"decision:publish:{intent_id}:{new_start_utc.isoformat()}:{uuid4().hex}"
        idempotency_key = hashlib.sha256(raw_key.encode()).hexdigest()

        new_decision = ScheduleDecision(
            id=new_decision_id,
            mission_id=old_res.mission_id,
            task_id=None,
            channel_id=old_res.channel_id,
            target_type=ScheduleTargetType.PUBLISH_INTENT.value,
            target_id=intent_id,
            workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
            action=ScheduleAction.SCHEDULE.value,
            scheduled_start_at=new_start_utc,
            scheduled_end_at=new_end_utc,
            reason=f"Rescheduled from {old_res.scheduled_start_at.isoformat()}: {reason}",
            policy_id=old_res.policy_id,
            policy_version=old_res.policy_version,
            policy_checksum=old_res.policy_checksum,
            channel_dna_revision_id=old_res.channel_dna_revision_id,
            reservation_id=new_reservation_id,
            guardian_epoch=old_res.guardian_epoch,
            idempotency_key=idempotency_key,
            diagnostic_context={
                "rescheduled_by": actor,
                "superseded_reservation_id": str(old_res.id),
                "previous_scheduled_start_at": old_res.scheduled_start_at.isoformat(),
            },
            evaluated_at=current_now,
            expires_at=new_end_utc + timedelta(hours=2),
            created_at=current_now,
        )
        session.add(new_decision)

        new_res = ScheduleReservation(
            id=new_reservation_id,
            decision_id=new_decision_id,
            channel_id=old_res.channel_id,
            mission_id=old_res.mission_id,
            target_type=ScheduleTargetType.PUBLISH_INTENT.value,
            target_id=intent_id,
            workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
            scheduled_start_at=new_start_utc,
            scheduled_end_at=new_end_utc,
            state=ReservationState.ACTIVE.value,
            priority_score=old_res.priority_score,
            policy_id=old_res.policy_id,
            policy_version=old_res.policy_version,
            policy_checksum=old_res.policy_checksum,
            channel_dna_revision_id=old_res.channel_dna_revision_id,
            guardian_epoch=old_res.guardian_epoch,
            version=1,
            expires_at=new_end_utc + timedelta(hours=2),
            created_at=current_now,
            updated_at=current_now,
        )
        session.add(new_res)

        new_transition = ScheduleStateTransition(
            id=uuid4(),
            reservation_id=new_reservation_id,
            from_state=ReservationState.RELEASED.value,
            to_state=ReservationState.ACTIVE.value,
            reason=f"RESCHEDULED_ACTIVE: {reason}",
            actor=actor,
            created_at=current_now,
        )
        session.add(new_transition)

        logger.info(
            "publish_intent_rescheduled",
            intent_id=str(intent_id),
            old_reservation_id=str(old_res.id),
            new_reservation_id=str(new_reservation_id),
            new_scheduled_start_at=new_start_utc.isoformat(),
            actor=actor,
        )
        return new_decision, new_res

    @classmethod
    async def cancel_schedule(
        cls,
        session: AsyncSession,
        *,
        intent_id: UUID,
        actor: str,
        reason: str = "Publication schedule cancelled",
        now: datetime | None = None,
    ) -> ScheduleReservation:
        """Cancel an ACTIVE publication reservation under row lock."""
        current_now = now or datetime.now(UTC)

        res_stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                ScheduleReservation.target_id == intent_id,
                ScheduleReservation.state == ReservationState.ACTIVE.value,
            )
            .with_for_update()
        )
        res = (await session.execute(res_stmt)).scalar_one_or_none()
        if not res:
            raise PublishCalendarError(
                f"No active schedule reservation found to cancel for PublishIntent {intent_id}."
            )

        from_state = res.state
        res.state = ReservationState.CANCELLED.value
        res.released_at = current_now
        res.updated_at = current_now
        res.version += 1

        transition = ScheduleStateTransition(
            id=uuid4(),
            reservation_id=res.id,
            from_state=from_state,
            to_state=ReservationState.CANCELLED.value,
            reason=f"CANCEL: {reason}",
            actor=actor,
            created_at=current_now,
        )
        session.add(transition)

        logger.info(
            "publish_schedule_cancelled",
            intent_id=str(intent_id),
            reservation_id=str(res.id),
            actor=actor,
            reason=reason,
        )
        return res

    @classmethod
    async def set_manual_hold(
        cls,
        session: AsyncSession,
        *,
        intent_id: UUID,
        actor: str,
        reason: str = "Manual hold placed by operator",
        now: datetime | None = None,
    ) -> ScheduleReservation:
        """Place an ACTIVE publication reservation on manual hold (transitions to RELEASED with MANUAL_HOLD audit)."""
        current_now = now or datetime.now(UTC)

        res_stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                ScheduleReservation.target_id == intent_id,
                ScheduleReservation.state == ReservationState.ACTIVE.value,
            )
            .with_for_update()
        )
        res = (await session.execute(res_stmt)).scalar_one_or_none()
        if not res:
            raise PublishCalendarError(
                f"No active schedule reservation found to place on hold for PublishIntent {intent_id}."
            )

        from_state = res.state
        res.state = ReservationState.RELEASED.value
        res.released_at = current_now
        res.updated_at = current_now
        res.version += 1

        transition = ScheduleStateTransition(
            id=uuid4(),
            reservation_id=res.id,
            from_state=from_state,
            to_state=ReservationState.RELEASED.value,
            reason=f"MANUAL_HOLD: {reason}",
            actor=actor,
            created_at=current_now,
        )
        session.add(transition)

        logger.info(
            "publish_schedule_manual_hold_set",
            intent_id=str(intent_id),
            reservation_id=str(res.id),
            actor=actor,
            reason=reason,
        )
        return res

    @classmethod
    async def release_manual_hold(
        cls,
        session: AsyncSession,
        *,
        intent_id: UUID,
        actor: str,
        reason: str = "Manual hold released by operator",
        new_scheduled_start_at: datetime | None = None,
        now: datetime | None = None,
    ) -> tuple[ScheduleDecision, ScheduleReservation]:
        """Release a manual hold and create a new ACTIVE reservation."""
        current_now = now or datetime.now(UTC)

        # 1. Verify no active reservation exists
        active_check = await session.execute(
            select(ScheduleReservation).where(
                ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                ScheduleReservation.target_id == intent_id,
                ScheduleReservation.state == ReservationState.ACTIVE.value,
            )
        )
        if active_check.scalar_one_or_none():
            raise PublishCalendarError(
                f"PublishIntent {intent_id} already has an active reservation."
            )

        # 2. Find the held reservation
        held_stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                ScheduleReservation.target_id == intent_id,
                ScheduleReservation.state == ReservationState.RELEASED.value,
            )
            .order_by(ScheduleReservation.created_at.desc())
            .with_for_update()
        )
        held_candidates = list((await session.execute(held_stmt)).scalars().all())
        held_res = None
        for cand in held_candidates:
            # Check if this reservation has a MANUAL_HOLD transition into RELEASED
            t_stmt = (
                select(ScheduleStateTransition)
                .where(
                    ScheduleStateTransition.reservation_id == cand.id,
                    ScheduleStateTransition.to_state == ReservationState.RELEASED.value,
                    ScheduleStateTransition.reason.like("MANUAL_HOLD%"),
                )
                .limit(1)
            )
            hold_t = (await session.execute(t_stmt)).scalar_one_or_none()
            if hold_t:
                held_res = cand
                break

        if not held_res:
            raise PublishCalendarError(
                f"No held schedule reservation found for PublishIntent {intent_id}."
            )

        # 3. Determine start time
        if new_scheduled_start_at is not None:
            start_utc = cls.normalize_schedule_timestamp(new_scheduled_start_at, now=current_now)
        elif held_res.scheduled_start_at > current_now:
            start_utc = held_res.scheduled_start_at
        else:
            start_utc = current_now

        duration_seconds = max(
            int((held_res.scheduled_end_at - held_res.scheduled_start_at).total_seconds()),
            300,
        )
        end_utc = start_utc + timedelta(seconds=duration_seconds)

        # 4. Create new ScheduleDecision + ScheduleReservation
        new_decision_id = uuid4()
        new_reservation_id = uuid4()
        raw_key = f"decision:publish:{intent_id}:{start_utc.isoformat()}:{uuid4().hex}"
        idempotency_key = hashlib.sha256(raw_key.encode()).hexdigest()

        new_decision = ScheduleDecision(
            id=new_decision_id,
            mission_id=held_res.mission_id,
            task_id=None,
            channel_id=held_res.channel_id,
            target_type=ScheduleTargetType.PUBLISH_INTENT.value,
            target_id=intent_id,
            workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
            action=ScheduleAction.SCHEDULE.value,
            scheduled_start_at=start_utc,
            scheduled_end_at=end_utc,
            reason=f"Released manual hold: {reason}",
            policy_id=held_res.policy_id,
            policy_version=held_res.policy_version,
            policy_checksum=held_res.policy_checksum,
            channel_dna_revision_id=held_res.channel_dna_revision_id,
            reservation_id=new_reservation_id,
            guardian_epoch=held_res.guardian_epoch,
            idempotency_key=idempotency_key,
            diagnostic_context={
                "hold_released_by": actor,
                "held_reservation_id": str(held_res.id),
            },
            evaluated_at=current_now,
            expires_at=end_utc + timedelta(hours=2),
            created_at=current_now,
        )
        session.add(new_decision)

        new_res = ScheduleReservation(
            id=new_reservation_id,
            decision_id=new_decision_id,
            channel_id=held_res.channel_id,
            mission_id=held_res.mission_id,
            target_type=ScheduleTargetType.PUBLISH_INTENT.value,
            target_id=intent_id,
            workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
            scheduled_start_at=start_utc,
            scheduled_end_at=end_utc,
            state=ReservationState.ACTIVE.value,
            priority_score=held_res.priority_score,
            policy_id=held_res.policy_id,
            policy_version=held_res.policy_version,
            policy_checksum=held_res.policy_checksum,
            channel_dna_revision_id=held_res.channel_dna_revision_id,
            guardian_epoch=held_res.guardian_epoch,
            version=1,
            expires_at=end_utc + timedelta(hours=2),
            created_at=current_now,
            updated_at=current_now,
        )
        session.add(new_res)

        new_transition = ScheduleStateTransition(
            id=uuid4(),
            reservation_id=new_reservation_id,
            from_state=ReservationState.RELEASED.value,
            to_state=ReservationState.ACTIVE.value,
            reason=f"RELEASE_MANUAL_HOLD: {reason}",
            actor=actor,
            created_at=current_now,
        )
        session.add(new_transition)

        logger.info(
            "publish_schedule_manual_hold_released",
            intent_id=str(intent_id),
            held_reservation_id=str(held_res.id),
            new_reservation_id=str(new_reservation_id),
            new_scheduled_start_at=start_utc.isoformat(),
            actor=actor,
        )
        return new_decision, new_res

    # ── Queries ──

    @classmethod
    async def get_upcoming_publications(
        cls,
        session: AsyncSession,
        *,
        limit: int = 50,
        from_time: datetime | None = None,
    ) -> list[ScheduleReservation]:
        """Query ACTIVE publication reservations scheduled after from_time."""
        t = from_time or datetime.now(UTC)
        stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.workload_category
                == ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
                ScheduleReservation.state == ReservationState.ACTIVE.value,
                ScheduleReservation.scheduled_start_at > t,
            )
            .order_by(ScheduleReservation.scheduled_start_at.asc())
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_due_publications(
        cls,
        session: AsyncSession,
        *,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[ScheduleReservation]:
        """Query ACTIVE publication reservations that are due for dispatch."""
        current_now = now or datetime.now(UTC)
        stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.workload_category
                == ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
                ScheduleReservation.state == ReservationState.ACTIVE.value,
                ScheduleReservation.scheduled_start_at <= current_now,
            )
            .order_by(ScheduleReservation.scheduled_start_at.asc())
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_recently_dispatched_publications(
        cls,
        session: AsyncSession,
        *,
        limit: int = 50,
    ) -> list[ScheduleReservation]:
        """Query publication reservations in DISPATCHING or CONSUMED state."""
        stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.workload_category
                == ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
                ScheduleReservation.state.in_(
                    [ReservationState.DISPATCHING.value, ReservationState.CONSUMED.value]
                ),
            )
            .order_by(desc(ScheduleReservation.dispatching_at))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_manual_hold_publications(
        cls,
        session: AsyncSession,
        *,
        limit: int = 50,
    ) -> list[ScheduleReservation]:
        """Query publication reservations that are currently held via manual hold."""
        stmt = (
            select(ScheduleReservation)
            .join(
                ScheduleStateTransition,
                ScheduleStateTransition.reservation_id == ScheduleReservation.id,
            )
            .where(
                ScheduleReservation.workload_category
                == ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
                ScheduleReservation.state == ReservationState.RELEASED.value,
                ScheduleStateTransition.reason.like("MANUAL_HOLD%"),
            )
            .order_by(desc(ScheduleReservation.released_at))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_calendar_entry(
        cls,
        session: AsyncSession,
        intent_id: UUID,
    ) -> dict[str, Any] | None:
        """Fetch canonical calendar status and transitions for a given PublishIntent."""
        res_stmt = (
            select(ScheduleReservation)
            .where(
                ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                ScheduleReservation.target_id == intent_id,
            )
            .order_by(desc(ScheduleReservation.created_at))
            .limit(1)
        )
        reservation = (await session.execute(res_stmt)).scalar_one_or_none()
        if not reservation:
            return None

        # Fetch transitions
        t_stmt = (
            select(ScheduleStateTransition)
            .where(ScheduleStateTransition.reservation_id == reservation.id)
            .order_by(ScheduleStateTransition.created_at.asc())
        )
        transitions = list((await session.execute(t_stmt)).scalars().all())

        # Fetch decision
        dec_stmt = select(ScheduleDecision).where(ScheduleDecision.id == reservation.decision_id)
        decision = (await session.execute(dec_stmt)).scalar_one_or_none()

        return {
            "intent_id": intent_id,
            "reservation": reservation,
            "decision": decision,
            "transitions": transitions,
            "current_state": reservation.state,
            "scheduled_start_at": reservation.scheduled_start_at,
            "scheduled_end_at": reservation.scheduled_end_at,
            "dispatching_at": reservation.dispatching_at,
            "released_at": reservation.released_at,
        }
