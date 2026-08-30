"""Slot allocator with globally ordered advisory locking and capacity enforcement.

Implements collision-free slot allocation using PostgreSQL transaction-scoped
advisory locks (acquired in deterministic ascending order) and temporal overlap
capacity queries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.scheduler import (
    ReservationState,
    ScheduleAction,
    SchedulePolicyConfig,
    compute_policy_checksum,
    compute_schedule_idempotency_key,
    schedule_advisory_lock_key,
)
from omega.infrastructure.models import (
    ScheduleDecision,
    SchedulePolicy,
    ScheduleReservation,
)
from omega.logging import get_logger

logger = get_logger(service="omega-scheduler-allocator")


class SlotAllocator:
    """Allocates collision-free schedule slots using PostgreSQL advisory locks."""

    @staticmethod
    async def allocate_slot(
        session: AsyncSession,
        *,
        policy: SchedulePolicy,
        policy_config: SchedulePolicyConfig,
        mission_id: UUID,
        task_id: UUID | None,
        target_type: str,
        target_id: UUID,
        workload_category: str,
        channel_id: UUID | None,
        channel_dna_revision_id: UUID | None,
        guardian_epoch: int,
        candidate_start: datetime,
        candidate_end: datetime,
        priority: int,
        estimated_duration_seconds: int,
        deadline_at: datetime | None,
        earliest_start_at: datetime | None,
        latest_start_at: datetime | None,
        caller_key: str,
        priority_score: float,
        diagnostic_context: dict[str, Any] | None = None,
    ) -> tuple[ScheduleDecision, ScheduleReservation | None]:
        """Allocate a slot using deterministic ascending advisory locks for serialization.

        Global lock ordering: derive all required lock keys and acquire them
        in strict ASCENDING numeric order to prevent deadlocks across all paths.

        Returns (decision, reservation_or_None).
        """
        checksum = compute_policy_checksum(policy_config)

        # 1. Derive advisory lock keys and acquire in deterministic ASCENDING order
        lock_keys: list[int] = []
        if channel_id:
            lock_keys.append(schedule_advisory_lock_key(channel_id, workload_category))
        lock_keys.append(schedule_advisory_lock_key(None, workload_category))

        for lk in sorted(lock_keys):
            await session.execute(text(f"SELECT pg_advisory_xact_lock({lk})"))

        # 2. Check global workload capacity (temporal overlap)
        global_overlap_count = await _count_overlapping_reservations(
            session,
            workload_category=workload_category,
            candidate_start=candidate_start,
            candidate_end=candidate_end,
            channel_id=None,
        )
        if global_overlap_count >= policy_config.global_concurrency_limit:
            decision = await _create_or_get_decision(
                session,
                action=ScheduleAction.WAITING_CAPACITY,
                reason=f"Global capacity exceeded: {global_overlap_count}/{policy_config.global_concurrency_limit} concurrent {workload_category}",
                policy=policy,
                checksum=checksum,
                mission_id=mission_id,
                task_id=task_id,
                target_type=target_type,
                target_id=target_id,
                workload_category=workload_category,
                channel_id=channel_id,
                channel_dna_revision_id=channel_dna_revision_id,
                guardian_epoch=guardian_epoch,
                priority=priority,
                estimated_duration_seconds=estimated_duration_seconds,
                deadline_at=deadline_at,
                earliest_start_at=earliest_start_at,
                latest_start_at=latest_start_at,
                caller_key=caller_key,
                diagnostic_context=diagnostic_context,
            )
            return decision, None

        # 3. Check channel capacity (temporal overlap)
        if channel_id:
            channel_overlap_count = await _count_overlapping_reservations(
                session,
                workload_category=workload_category,
                candidate_start=candidate_start,
                candidate_end=candidate_end,
                channel_id=channel_id,
            )
            if channel_overlap_count >= policy_config.channel_concurrency_limit:
                decision = await _create_or_get_decision(
                    session,
                    action=ScheduleAction.WAITING_CAPACITY,
                    reason=f"Channel capacity exceeded: {channel_overlap_count}/{policy_config.channel_concurrency_limit}",
                    policy=policy,
                    checksum=checksum,
                    mission_id=mission_id,
                    task_id=task_id,
                    target_type=target_type,
                    target_id=target_id,
                    workload_category=workload_category,
                    channel_id=channel_id,
                    channel_dna_revision_id=channel_dna_revision_id,
                    guardian_epoch=guardian_epoch,
                    priority=priority,
                    estimated_duration_seconds=estimated_duration_seconds,
                    deadline_at=deadline_at,
                    earliest_start_at=earliest_start_at,
                    latest_start_at=latest_start_at,
                    caller_key=caller_key,
                    diagnostic_context=diagnostic_context,
                )
                return decision, None

            # 4. Check min-gap between channel items
            if policy_config.min_gap_between_channel_items_seconds > 0:
                gap_violation = await _check_min_gap(
                    session,
                    channel_id=channel_id,
                    candidate_start=candidate_start,
                    candidate_end=candidate_end,
                    min_gap_seconds=policy_config.min_gap_between_channel_items_seconds,
                )
                if gap_violation:
                    decision = await _create_or_get_decision(
                        session,
                        action=ScheduleAction.BLOCKED_SCHEDULE,
                        reason=f"Min gap violation: {policy_config.min_gap_between_channel_items_seconds}s required between channel items",
                        policy=policy,
                        checksum=checksum,
                        mission_id=mission_id,
                        task_id=task_id,
                        target_type=target_type,
                        target_id=target_id,
                        workload_category=workload_category,
                        channel_id=channel_id,
                        channel_dna_revision_id=channel_dna_revision_id,
                        guardian_epoch=guardian_epoch,
                        priority=priority,
                        estimated_duration_seconds=estimated_duration_seconds,
                        deadline_at=deadline_at,
                        earliest_start_at=earliest_start_at,
                        latest_start_at=latest_start_at,
                        caller_key=caller_key,
                        diagnostic_context=diagnostic_context,
                    )
                    return decision, None

            # 5. Check daily channel throughput cap
            daily_count = await _count_channel_daily_reservations(
                session,
                channel_id=channel_id,
                date=candidate_start.date(),
            )
            if daily_count >= policy_config.max_scheduled_items_per_day:
                decision = await _create_or_get_decision(
                    session,
                    action=ScheduleAction.WAITING_CAPACITY,
                    reason=f"Daily channel cap exceeded: {daily_count}/{policy_config.max_scheduled_items_per_day}",
                    policy=policy,
                    checksum=checksum,
                    mission_id=mission_id,
                    task_id=task_id,
                    target_type=target_type,
                    target_id=target_id,
                    workload_category=workload_category,
                    channel_id=channel_id,
                    channel_dna_revision_id=channel_dna_revision_id,
                    guardian_epoch=guardian_epoch,
                    priority=priority,
                    estimated_duration_seconds=estimated_duration_seconds,
                    deadline_at=deadline_at,
                    earliest_start_at=earliest_start_at,
                    latest_start_at=latest_start_at,
                    caller_key=caller_key,
                    diagnostic_context=diagnostic_context,
                )
                return decision, None

        # 6. All capacity checks pass — allocate reservation and record decision
        now = datetime.now(UTC)
        reservation_id = uuid4()
        decision_id = uuid4()

        effective_idempotency_key = await _resolve_effective_idempotency_key(
            session,
            target_type=target_type,
            target_id=target_id,
            mission_id=mission_id,
            task_id=task_id,
            workload_category=workload_category,
            channel_id=channel_id,
            policy_id=policy.id,
            policy_version=policy.version,
            policy_checksum=checksum,
            channel_dna_revision_id=channel_dna_revision_id,
            guardian_epoch=guardian_epoch,
            earliest_start_at=earliest_start_at,
            latest_start_at=latest_start_at,
            deadline_at=deadline_at,
            estimated_duration_seconds=estimated_duration_seconds,
            priority=priority,
            caller_key=caller_key,
        )

        decision = ScheduleDecision(
            id=decision_id,
            mission_id=mission_id,
            task_id=task_id,
            channel_id=channel_id,
            target_type=target_type,
            target_id=target_id,
            workload_category=workload_category,
            action=ScheduleAction.SCHEDULE.value,
            scheduled_start_at=candidate_start,
            scheduled_end_at=candidate_end,
            reason="Slot allocated: all capacity checks passed",
            policy_id=policy.id,
            policy_version=policy.version,
            policy_checksum=checksum,
            channel_dna_revision_id=channel_dna_revision_id,
            reservation_id=reservation_id,
            guardian_epoch=guardian_epoch,
            idempotency_key=effective_idempotency_key,
            diagnostic_context=diagnostic_context,
            evaluated_at=now,
            expires_at=candidate_end + timedelta(minutes=5),
        )
        session.add(decision)

        reservation = ScheduleReservation(
            id=reservation_id,
            decision_id=decision_id,
            channel_id=channel_id,
            mission_id=mission_id,
            target_type=target_type,
            target_id=target_id,
            workload_category=workload_category,
            scheduled_start_at=candidate_start,
            scheduled_end_at=candidate_end,
            state=ReservationState.ACTIVE.value,
            priority_score=priority_score,
            policy_id=policy.id,
            policy_version=policy.version,
            policy_checksum=checksum,
            channel_dna_revision_id=channel_dna_revision_id,
            guardian_epoch=guardian_epoch,
            expires_at=candidate_end + timedelta(minutes=5),
        )
        session.add(reservation)

        logger.info(
            "reservation_allocated",
            reservation_id=str(reservation_id),
            channel_id=str(channel_id) if channel_id else None,
            slot_start=candidate_start.isoformat(),
            slot_end=candidate_end.isoformat(),
            priority_score=priority_score,
        )

        return decision, reservation


async def _count_overlapping_reservations(
    session: AsyncSession,
    *,
    workload_category: str,
    candidate_start: datetime,
    candidate_end: datetime,
    channel_id: UUID | None,
) -> int:
    """Count active/dispatching reservations temporally overlapping the candidate interval."""
    stmt = select(func.count(ScheduleReservation.id)).where(
        ScheduleReservation.workload_category == workload_category,
        ScheduleReservation.state.in_(
            [
                ReservationState.ACTIVE.value,
                ReservationState.DISPATCHING.value,
            ]
        ),
        ScheduleReservation.scheduled_start_at < candidate_end,
        ScheduleReservation.scheduled_end_at > candidate_start,
    )
    if channel_id:
        stmt = stmt.where(ScheduleReservation.channel_id == channel_id)
    result = await session.execute(stmt)
    return result.scalar() or 0


async def _check_min_gap(
    session: AsyncSession,
    *,
    channel_id: UUID,
    candidate_start: datetime,
    candidate_end: datetime,
    min_gap_seconds: int,
) -> bool:
    """Check if candidate violates minimum gap between channel items.

    Returns True if a gap violation exists.
    """
    gap = timedelta(seconds=min_gap_seconds)
    expanded_start = candidate_start - gap
    expanded_end = candidate_end + gap

    count_result = await session.execute(
        select(func.count(ScheduleReservation.id)).where(
            ScheduleReservation.channel_id == channel_id,
            ScheduleReservation.state.in_(
                [
                    ReservationState.ACTIVE.value,
                    ReservationState.DISPATCHING.value,
                    ReservationState.CONSUMED.value,
                ]
            ),
            ScheduleReservation.scheduled_start_at < expanded_end,
            ScheduleReservation.scheduled_end_at > expanded_start,
        )
    )
    count = count_result.scalar() or 0
    return count > 0


async def _count_channel_daily_reservations(
    session: AsyncSession,
    *,
    channel_id: UUID,
    date: Any,
) -> int:
    """Count reservations for a channel on a specific date (all active/consumed states)."""
    from datetime import time

    day_start = datetime.combine(date, time.min, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    result = await session.execute(
        select(func.count(ScheduleReservation.id)).where(
            ScheduleReservation.channel_id == channel_id,
            ScheduleReservation.state.in_(
                [
                    ReservationState.ACTIVE.value,
                    ReservationState.DISPATCHING.value,
                    ReservationState.CONSUMED.value,
                ]
            ),
            ScheduleReservation.scheduled_start_at >= day_start,
            ScheduleReservation.scheduled_start_at < day_end,
        )
    )
    return result.scalar() or 0


async def _resolve_effective_idempotency_key(
    session: AsyncSession,
    *,
    target_type: str,
    target_id: UUID,
    mission_id: UUID,
    task_id: UUID | None,
    workload_category: str,
    channel_id: UUID | None,
    policy_id: UUID,
    policy_version: str,
    policy_checksum: str,
    channel_dna_revision_id: UUID | None,
    guardian_epoch: int,
    earliest_start_at: datetime | None,
    latest_start_at: datetime | None,
    deadline_at: datetime | None,
    estimated_duration_seconds: int,
    priority: int,
    caller_key: str,
) -> str:
    """Resolve an effective idempotency key that satisfies DB uniqueness while allowing fresh re-evaluation after terminal invalidation."""
    base_key = compute_schedule_idempotency_key(
        target_type=target_type,
        target_id=target_id,
        mission_id=mission_id,
        task_id=task_id,
        workload_category=workload_category,
        channel_id=channel_id,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_checksum=policy_checksum,
        channel_dna_revision_id=channel_dna_revision_id,
        guardian_epoch=guardian_epoch,
        earliest_start_at=earliest_start_at,
        latest_start_at=latest_start_at,
        deadline_at=deadline_at,
        estimated_duration_seconds=estimated_duration_seconds,
        priority=priority,
        caller_key=caller_key,
    )

    # Check if base key exists
    existing = await session.execute(
        select(ScheduleDecision).where(ScheduleDecision.idempotency_key == base_key)
    )
    existing_decision = existing.scalar_one_or_none()
    if not existing_decision:
        return base_key

    # If existing decision's reservation is terminal (or no reservation), generate attempt discriminator
    attempt = 1
    while True:
        candidate_key = compute_schedule_idempotency_key(
            target_type=target_type,
            target_id=target_id,
            mission_id=mission_id,
            task_id=task_id,
            workload_category=workload_category,
            channel_id=channel_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_checksum=policy_checksum,
            channel_dna_revision_id=channel_dna_revision_id,
            guardian_epoch=guardian_epoch,
            earliest_start_at=earliest_start_at,
            latest_start_at=latest_start_at,
            deadline_at=deadline_at,
            estimated_duration_seconds=estimated_duration_seconds,
            priority=priority,
            caller_key=f"{caller_key}:attempt-{attempt}",
        )
        check_existing = await session.execute(
            select(ScheduleDecision).where(ScheduleDecision.idempotency_key == candidate_key)
        )
        if not check_existing.scalar_one_or_none():
            return candidate_key
        attempt += 1


async def _create_or_get_decision(
    session: AsyncSession,
    *,
    action: ScheduleAction,
    reason: str,
    policy: SchedulePolicy,
    checksum: str,
    mission_id: UUID,
    task_id: UUID | None,
    target_type: str,
    target_id: UUID,
    workload_category: str,
    channel_id: UUID | None,
    channel_dna_revision_id: UUID | None,
    guardian_epoch: int,
    priority: int,
    estimated_duration_seconds: int,
    deadline_at: datetime | None,
    earliest_start_at: datetime | None,
    latest_start_at: datetime | None,
    caller_key: str,
    diagnostic_context: dict[str, Any] | None = None,
) -> ScheduleDecision:
    """Create or return an existing non-scheduled decision record."""
    now = datetime.now(UTC)
    idempotency_key = await _resolve_effective_idempotency_key(
        session,
        target_type=target_type,
        target_id=target_id,
        mission_id=mission_id,
        task_id=task_id,
        workload_category=workload_category,
        channel_id=channel_id,
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=checksum,
        channel_dna_revision_id=channel_dna_revision_id,
        guardian_epoch=guardian_epoch,
        earliest_start_at=earliest_start_at,
        latest_start_at=latest_start_at,
        deadline_at=deadline_at,
        estimated_duration_seconds=estimated_duration_seconds,
        priority=priority,
        caller_key=caller_key,
    )

    decision = ScheduleDecision(
        id=uuid4(),
        mission_id=mission_id,
        task_id=task_id,
        channel_id=channel_id,
        target_type=target_type,
        target_id=target_id,
        workload_category=workload_category,
        action=action.value,
        reason=reason,
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=checksum,
        channel_dna_revision_id=channel_dna_revision_id,
        guardian_epoch=guardian_epoch,
        idempotency_key=idempotency_key,
        diagnostic_context=diagnostic_context,
        evaluated_at=now,
    )
    session.add(decision)
    return decision
