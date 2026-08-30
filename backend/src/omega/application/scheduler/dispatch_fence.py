"""Pre-dispatch validation fence for OMEGA-010.

Executes atomic revalidation of all pinned context (Guardian epoch, policy version/checksum,
Channel DNA revision, task state, mission state, expiration, dependencies) immediately
before transitioning a reservation to DISPATCHING.

Default Core v1 policy: any stale pin -> RELEASE reservation + transition audit.
Never dispatch stale work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.mission import MissionState
from omega.domain.scheduler import (
    DispatchFenceResult,
    ReservationState,
    SchedulePolicyStatus,
)
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    ChannelDNARevision,
    Mission,
    SchedulePolicy,
    ScheduleReservation,
    ScheduleStateTransition,
    Task,
)
from omega.logging import get_logger

logger = get_logger(service="omega-scheduler-fence")


class DispatchFence:
    """Pre-dispatch validation fence executing under row locks."""

    @staticmethod
    async def evaluate_fence(
        session: AsyncSession,
        reservation: ScheduleReservation,
        now: datetime | None = None,
    ) -> tuple[DispatchFenceResult, str]:
        """Revalidate all pins and predicates for an ACTIVE reservation before dispatch.

        Returns (result, reason).
        """
        if now is None:
            now = datetime.now(UTC)

        # 1. Verify reservation state is ACTIVE
        if reservation.state != ReservationState.ACTIVE.value:
            return (
                DispatchFenceResult.STALE_RELEASED,
                f"Reservation is in {reservation.state} state, not ACTIVE",
            )

        # 2. Verify reservation has not expired
        if reservation.expires_at is not None and reservation.expires_at <= now:
            await _release_reservation(
                session,
                reservation,
                reason=f"Reservation expired at {reservation.expires_at.isoformat()}",
            )
            return (
                DispatchFenceResult.STALE_RELEASED,
                "Reservation has expired",
            )

        # 3. Verify Mission exists and state is RUNNING
        mission_res = await session.execute(
            select(Mission).where(Mission.id == reservation.mission_id).with_for_update()
        )
        mission = mission_res.scalar_one_or_none()
        if not mission:
            await _release_reservation(session, reservation, reason="Mission not found")
            return (DispatchFenceResult.MISSION_INELIGIBLE, "Mission not found")

        if mission.state != MissionState.RUNNING.value:
            await _release_reservation(
                session,
                reservation,
                reason=f"Mission not in RUNNING state (current state: {mission.state})",
            )
            return (
                DispatchFenceResult.MISSION_INELIGIBLE,
                f"Mission state is {mission.state}, not RUNNING",
            )

        # 4. Verify Guardian epoch matches exactly
        if mission.guardian_epoch != reservation.guardian_epoch:
            await _release_reservation(
                session,
                reservation,
                reason=f"Guardian epoch mismatch: mission epoch is {mission.guardian_epoch}, reservation pinned {reservation.guardian_epoch}",
            )
            return (
                DispatchFenceResult.STALE_RELEASED,
                f"Guardian epoch changed from {reservation.guardian_epoch} to {mission.guardian_epoch}",
            )

        # 5. If task-scoped, verify task exists and is READY
        if reservation.target_type == "TASK_EXECUTION" or reservation.decision.task_id:
            task_id = (
                reservation.target_id
                if reservation.target_type == "TASK_EXECUTION"
                else reservation.decision.task_id
            )
            task_res = await session.execute(
                select(Task).where(Task.id == task_id).with_for_update()
            )
            task = task_res.scalar_one_or_none()
            if not task:
                await _release_reservation(session, reservation, reason="Target task not found")
                return (DispatchFenceResult.TASK_INELIGIBLE, "Target task not found")

            if task.state != TaskState.READY.value:
                await _release_reservation(
                    session,
                    reservation,
                    reason=f"Task is in {task.state} state, not READY",
                )
                return (
                    DispatchFenceResult.TASK_INELIGIBLE,
                    f"Task state is {task.state}, not READY",
                )

        # 6. Verify SchedulePolicy is still active and matches pinned checksum
        policy_res = await session.execute(
            select(SchedulePolicy).where(
                SchedulePolicy.workload_category == reservation.workload_category,
                SchedulePolicy.status == SchedulePolicyStatus.ACTIVE.value,
            )
        )
        active_policy = policy_res.scalar_one_or_none()
        if not active_policy:
            await _release_reservation(
                session,
                reservation,
                reason=f"No active policy found for workload category {reservation.workload_category}",
            )
            return (
                DispatchFenceResult.STALE_RELEASED,
                "No active schedule policy for category",
            )

        if (
            active_policy.id != reservation.policy_id
            or active_policy.checksum != reservation.policy_checksum
        ):
            await _release_reservation(
                session,
                reservation,
                reason=f"Schedule policy changed: active version is {active_policy.version} (checksum {active_policy.checksum[:8]}), pinned {reservation.policy_version} (checksum {reservation.policy_checksum[:8]})",
            )
            return (
                DispatchFenceResult.STALE_RELEASED,
                "Schedule policy revision mismatch",
            )

        # 7. Verify Channel DNA revision still matches (if channel-scoped)
        if reservation.channel_id and reservation.channel_dna_revision_id:
            dna_res = await session.execute(
                select(ChannelDNARevision)
                .where(ChannelDNARevision.channel_id == reservation.channel_id)
                .order_by(ChannelDNARevision.version.desc())
                .limit(1)
            )
            latest_dna = dna_res.scalar_one_or_none()
            if latest_dna and latest_dna.id != reservation.channel_dna_revision_id:
                await _release_reservation(
                    session,
                    reservation,
                    reason=f"Channel DNA revision changed: latest is v{latest_dna.version} ({str(latest_dna.id)[:8]}), pinned {str(reservation.channel_dna_revision_id)[:8]}",
                )
                return (
                    DispatchFenceResult.STALE_RELEASED,
                    "Channel DNA revision changed",
                )

        # 8. Verify no newer ACTIVE reservation supersedes this one
        newer_res = await session.execute(
            select(ScheduleReservation).where(
                ScheduleReservation.target_type == reservation.target_type,
                ScheduleReservation.target_id == reservation.target_id,
                ScheduleReservation.state == ReservationState.ACTIVE.value,
                ScheduleReservation.created_at > reservation.created_at,
            )
        )
        if newer_res.scalar_one_or_none():
            await _release_reservation(
                session,
                reservation,
                reason="Superseded by a newer active reservation for the same target",
            )
            return (
                DispatchFenceResult.STALE_RELEASED,
                "Superseded by newer reservation",
            )

        # All checks passed
        return (DispatchFenceResult.PROCEED, "All dispatch fence predicates satisfied")


async def _release_reservation(
    session: AsyncSession,
    reservation: ScheduleReservation,
    reason: str,
) -> None:
    """Transition a reservation to RELEASED and log the audit transition."""
    now = datetime.now(UTC)
    from_state = reservation.state
    reservation.state = ReservationState.RELEASED.value
    reservation.released_at = now
    reservation.updated_at = now

    transition = ScheduleStateTransition(
        id=uuid4(),
        reservation_id=reservation.id,
        from_state=from_state,
        to_state=ReservationState.RELEASED.value,
        reason=reason,
        actor="DISPATCH_FENCE",
        created_at=now,
    )
    session.add(transition)

    logger.warning(
        "dispatch_fence_rejected",
        reservation_id=str(reservation.id),
        from_state=from_state,
        to_state=ReservationState.RELEASED.value,
        reason=reason,
    )
