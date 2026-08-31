"""Core Autonomy Loop lifecycle, concurrency coordination, and state machine FSM."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.budget_service import AutonomyBudgetService
from omega.application.autonomy.helpers import (
    advisory_lock_key,
    compute_event_dedupe_key,
)
from omega.domain.autonomy import (
    TERMINAL_STATES,
    AutonomyEventType,
    AutonomyLevel,
    AutonomyLoopState,
    validate_state_transition,
)
from omega.infrastructure.models import (
    AutonomyEvent,
    AutonomyLoop,
    AutonomyLoopIteration,
    AutonomyLoopLatestPointer,
    AutonomyPolicySnapshot,
    Channel,
    Mission,
)


class RateLimitExceededError(Exception):
    """Raised when the hourly iteration limit has been reached."""

    pass


class RateLimitCooldownError(Exception):
    """Raised when the minimum iteration interval has not yet elapsed."""

    pass


class IllegalStateTransitionError(Exception):
    """Raised when an illegal FSM transition is attempted."""

    pass


class ActiveIterationInProgressError(Exception):
    """Raised when attempting to allocate an iteration while an active iteration is still in progress."""

    pass


class StaleIterationCompletionError(Exception):
    """Raised when a stale worker attempts to complete or update an iteration that is no longer authoritative."""

    pass


class AutonomyLoopService:
    """Service orchestrating autonomous loops, iteration allocation, rate limits, and FSM transitions."""

    @classmethod
    async def create_loop(
        cls,
        session: AsyncSession,
        mission_id: UUID,
        channel_id: UUID,
        autonomy_level: AutonomyLevel = AutonomyLevel.SUPERVISED,
        daily_cap_usd: Decimal = Decimal("10.0000"),
        lifetime_cap_usd: Decimal = Decimal("100.0000"),
        max_iterations_per_hour: int = 30,
        min_iteration_interval_seconds: int = 60,
    ) -> AutonomyLoop:
        """Create a new autonomous loop under an advisory lock with complete initialization."""
        # 1. Advisory lock on loop creation
        lock_k = advisory_lock_key("autonomy_loop_create", str(mission_id), str(channel_id))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_k},
        )

        # 2. Check for existing loop
        dedupe_key = hashlib.sha256(f"loop:{mission_id}:{channel_id}".encode()).hexdigest()
        stmt_existing = select(AutonomyLoop).where(AutonomyLoop.loop_dedupe_key == dedupe_key)
        existing = (await session.execute(stmt_existing)).scalar_one_or_none()
        if existing:
            return existing

        # Verify Mission and Channel exist
        m_res = await session.execute(select(Mission).where(Mission.id == mission_id))
        if not m_res.scalar_one_or_none():
            raise ValueError(f"Mission '{mission_id}' not found.")

        c_res = await session.execute(select(Channel).where(Channel.id == channel_id))
        if not c_res.scalar_one_or_none():
            raise ValueError(f"Channel '{channel_id}' not found.")

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        # 3. Create AutonomyLoop
        loop = AutonomyLoop(
            id=uuid4(),
            mission_id=mission_id,
            channel_id=channel_id,
            autonomy_level=autonomy_level.value,
            loop_dedupe_key=dedupe_key,
            created_at=db_now,
        )
        session.add(loop)
        await session.flush()

        # 4. Create Policy Snapshot v1
        policy_rules = {
            "max_iterations_per_hour": max_iterations_per_hour,
            "min_iteration_interval_seconds": min_iteration_interval_seconds,
            "daily_budget_cap_usd": str(daily_cap_usd),
            "lifetime_budget_cap_usd": str(lifetime_cap_usd),
        }
        policy_checksum = hashlib.sha256(
            f"{max_iterations_per_hour}:{min_iteration_interval_seconds}:{daily_cap_usd}:{lifetime_cap_usd}".encode()
        ).hexdigest()

        policy = AutonomyPolicySnapshot(
            id=uuid4(),
            loop_id=loop.id,
            policy_version=1,
            max_iterations_per_hour=max_iterations_per_hour,
            min_iteration_interval_seconds=min_iteration_interval_seconds,
            daily_budget_cap_usd=daily_cap_usd,
            lifetime_budget_cap_usd=lifetime_cap_usd,
            policy_rules=policy_rules,
            policy_checksum=policy_checksum,
            created_at=db_now,
        )
        session.add(policy)

        # 5. Create Budget Ledger
        await AutonomyBudgetService.get_or_create_ledger(
            session=session,
            loop_id=loop.id,
            lifetime_cap_usd=lifetime_cap_usd,
            daily_cap_usd=daily_cap_usd,
        )

        # 6. Create Sole Authoritative Mutable Pointer
        pointer = AutonomyLoopLatestPointer(
            loop_id=loop.id,
            operational_state=AutonomyLoopState.IDLE.value,
            current_iteration_id=None,
            current_iteration_sequence=0,
            current_action_attempt_id=None,
            active_claim_token=None,
            active_claim_generation=0,
            lease_expires_at=None,
            last_iteration_finished_at=None,
            updated_at=db_now,
        )
        session.add(pointer)

        # 7. Record Event
        ev_key = compute_event_dedupe_key(
            loop.id, AutonomyEventType.LOOP_CREATED.value, str(loop.id)
        )
        ev = AutonomyEvent(
            id=uuid4(),
            loop_id=loop.id,
            iteration_id=None,
            event_type=AutonomyEventType.LOOP_CREATED.value,
            payload={"autonomy_level": autonomy_level.value, "mission_id": str(mission_id)},
            event_dedupe_key=ev_key,
            occurred_at=db_now,
        )
        session.add(ev)
        await session.flush()
        return loop

    @classmethod
    async def allocate_iteration(
        cls,
        session: AsyncSession,
        loop_id: UUID,
        observation_snapshot_id: UUID,
        policy_snapshot_id: UUID,
    ) -> AutonomyLoopIteration:
        """Atomically enforce rate limits and allocate next monotonic iteration sequence in one transaction."""
        # 1. Advisory lock on iteration allocation for this loop
        lock_k = advisory_lock_key("autonomy_iter_alloc", str(loop_id))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_k},
        )

        # 2. SELECT latest projection pointer FOR UPDATE
        stmt_ptr = (
            select(AutonomyLoopLatestPointer)
            .where(AutonomyLoopLatestPointer.loop_id == loop_id)
            .with_for_update()
        )
        pointer = (await session.execute(stmt_ptr)).scalar_one_or_none()
        if not pointer:
            raise ValueError(f"Operational pointer for loop '{loop_id}' not found.")

        # Check if loop is in a terminal or non-schedulable state
        current_st = AutonomyLoopState(pointer.operational_state)
        if current_st in TERMINAL_STATES:
            raise IllegalStateTransitionError(
                f"Cannot allocate iteration for terminal loop in state '{current_st.value}'."
            )
        if current_st in (
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.BLOCKED,
            AutonomyLoopState.FAILED,
        ):
            raise IllegalStateTransitionError(
                f"Cannot allocate iteration while loop is in '{current_st.value}'."
            )

        # 3. Load latest policy snapshot
        stmt_policy = select(AutonomyPolicySnapshot).where(
            AutonomyPolicySnapshot.id == policy_snapshot_id
        )
        policy = (await session.execute(stmt_policy)).scalar_one_or_none()
        if not policy:
            raise ValueError(f"Policy snapshot '{policy_snapshot_id}' not found.")

        # 4. Enforce Hourly Rate Limit using DB NOW()
        stmt_hourly = select(
            func.count(AutonomyLoopIteration.id),
            func.now(),
            func.now() - text("INTERVAL '1 hour'"),
        ).where(
            AutonomyLoopIteration.loop_id == loop_id,
            AutonomyLoopIteration.started_at >= func.now() - text("INTERVAL '1 hour'"),
        )
        iter_count_1h, db_now, _ = (await session.execute(stmt_hourly)).first()

        if iter_count_1h >= policy.max_iterations_per_hour:
            raise RateLimitExceededError(
                f"Hourly iteration limit {policy.max_iterations_per_hour} reached (current: {iter_count_1h})."
            )

        # 5. Active Iteration Invariant: At most one active iteration per loop
        # Active operational states that must not create another iteration:
        # OBSERVING, PLANNING, WAITING_APPROVAL, EXECUTING, VERIFYING, WAITING_SIGNAL
        if current_st in (
            AutonomyLoopState.OBSERVING,
            AutonomyLoopState.PLANNING,
            AutonomyLoopState.WAITING_APPROVAL,
            AutonomyLoopState.EXECUTING,
            AutonomyLoopState.VERIFYING,
            AutonomyLoopState.WAITING_SIGNAL,
        ):
            raise ActiveIterationInProgressError(
                f"Active iteration '{pointer.current_iteration_id}' is still in progress (state: {current_st.value})."
            )

        # IDLE is eligible only if previous iteration is authoritatively completed/closed
        if pointer.current_iteration_id is not None:
            stmt_curr = (
                select(AutonomyLoopIteration)
                .where(AutonomyLoopIteration.id == pointer.current_iteration_id)
                .with_for_update()
            )
            curr_iter = (await session.execute(stmt_curr)).scalar_one_or_none()
            if curr_iter and curr_iter.completed_at is None:
                raise ActiveIterationInProgressError(
                    f"Active iteration '{pointer.current_iteration_id}' is not yet completed/closed."
                )

        # 5. Enforce Minimum Iteration Interval (Cooldown) against DB NOW()
        if pointer.last_iteration_finished_at is not None:
            interval_sec = int(policy.min_iteration_interval_seconds)
            stmt_cooldown = text(
                "SELECT (CAST(:finished_at AS TIMESTAMP WITH TIME ZONE) + (:interval * INTERVAL '1 second')) > NOW()"
            )
            in_cooldown = (
                await session.execute(
                    stmt_cooldown,
                    {
                        "finished_at": pointer.last_iteration_finished_at,
                        "interval": interval_sec,
                    },
                )
            ).scalar()
            if in_cooldown:
                raise RateLimitCooldownError("Minimum iteration interval cooldown has not elapsed.")

        # 6. Allocate Next Monotonic Sequence
        next_seq = pointer.current_iteration_sequence + 1
        dedupe_key = hashlib.sha256(f"iter:{loop_id}:{next_seq}".encode()).hexdigest()

        iteration = AutonomyLoopIteration(
            id=uuid4(),
            loop_id=loop_id,
            observation_snapshot_id=observation_snapshot_id,
            policy_snapshot_id=policy_snapshot_id,
            iteration_sequence=next_seq,
            state=AutonomyLoopState.OBSERVING.value,
            iteration_dedupe_key=dedupe_key,
            started_at=db_now,
        )
        session.add(iteration)
        await session.flush()

        # 7. Update Operational Projection
        pointer.current_iteration_id = iteration.id
        pointer.current_iteration_sequence = next_seq
        pointer.operational_state = AutonomyLoopState.OBSERVING.value
        pointer.updated_at = db_now

        # 8. Record Event
        ev_key = compute_event_dedupe_key(
            loop_id, AutonomyEventType.OBSERVATION_CAPTURED.value, str(iteration.id)
        )
        ev = AutonomyEvent(
            id=uuid4(),
            loop_id=loop_id,
            iteration_id=iteration.id,
            event_type=AutonomyEventType.OBSERVATION_CAPTURED.value,
            payload={"iteration_sequence": next_seq},
            event_dedupe_key=ev_key,
            occurred_at=db_now,
        )
        session.add(ev)
        await session.flush()
        return iteration

    @classmethod
    async def update_operational_state(
        cls,
        session: AsyncSession,
        loop_id: UUID,
        new_state: AutonomyLoopState,
        stop_reason: str | None = None,
        expected_iteration_id: UUID | None = None,
    ) -> AutonomyLoopLatestPointer:
        """Atomically transition loop state according to legal FSM matrix."""
        stmt_ptr = (
            select(AutonomyLoopLatestPointer)
            .where(AutonomyLoopLatestPointer.loop_id == loop_id)
            .with_for_update()
        )
        pointer = (await session.execute(stmt_ptr)).scalar_one_or_none()
        if not pointer:
            raise ValueError(f"Operational pointer for loop '{loop_id}' not found.")

        # Stale worker protection: if an expected iteration is specified, verify it matches authoritative pointer
        if (
            expected_iteration_id is not None
            and pointer.current_iteration_id != expected_iteration_id
        ):
            raise StaleIterationCompletionError(
                f"Stale worker cannot update loop operational state for iteration '{expected_iteration_id}'. "
                f"Current authoritative iteration is '{pointer.current_iteration_id}'."
            )

        current_st = AutonomyLoopState(pointer.operational_state)
        if not validate_state_transition(current_st, new_state):
            raise IllegalStateTransitionError(
                f"Illegal transition from '{current_st.value}' to '{new_state.value}'."
            )

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        pointer.operational_state = new_state.value
        pointer.updated_at = db_now
        if new_state in (
            AutonomyLoopState.IDLE,
            AutonomyLoopState.COMPLETED,
            AutonomyLoopState.CANCELLED,
        ):
            pointer.last_iteration_finished_at = db_now

        # If iteration completed
        if pointer.current_iteration_id and new_state in (
            AutonomyLoopState.IDLE,
            AutonomyLoopState.COMPLETED,
            AutonomyLoopState.CANCELLED,
            AutonomyLoopState.FAILED,
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.BLOCKED,
        ):
            stmt_iter = (
                select(AutonomyLoopIteration)
                .where(AutonomyLoopIteration.id == pointer.current_iteration_id)
                .with_for_update()
            )
            iter_obj = (await session.execute(stmt_iter)).scalar_one_or_none()
            if iter_obj:
                iter_obj.state = new_state.value
                iter_obj.completed_at = db_now
                iter_obj.stop_reason = stop_reason

        # Append state transition event
        event_type = (
            AutonomyEventType.LOOP_PAUSED.value
            if new_state == AutonomyLoopState.PAUSED
            else (
                AutonomyEventType.LOOP_BLOCKED.value
                if new_state == AutonomyLoopState.BLOCKED
                else (
                    AutonomyEventType.LOOP_COMPLETED.value
                    if new_state == AutonomyLoopState.COMPLETED
                    else (
                        AutonomyEventType.LOOP_CANCELLED.value
                        if new_state == AutonomyLoopState.CANCELLED
                        else (
                            AutonomyEventType.LOOP_FAILED.value
                            if new_state == AutonomyLoopState.FAILED
                            else (AutonomyEventType.ITERATION_COMPLETED.value)
                        )
                    )
                )
            )
        )
        ev_key = compute_event_dedupe_key(
            loop_id, event_type, f"{pointer.current_iteration_sequence}:{new_state.value}"
        )
        ev = AutonomyEvent(
            id=uuid4(),
            loop_id=loop_id,
            iteration_id=pointer.current_iteration_id,
            event_type=event_type,
            payload={
                "from_state": current_st.value,
                "to_state": new_state.value,
                "stop_reason": stop_reason,
            },
            event_dedupe_key=ev_key,
            occurred_at=db_now,
        )
        session.add(ev)
        await session.flush()
        return pointer

    @classmethod
    async def complete_iteration(
        cls,
        session: AsyncSession,
        loop_id: UUID,
        iteration_id: UUID,
        new_state: AutonomyLoopState = AutonomyLoopState.IDLE,
        stop_reason: str | None = None,
    ) -> AutonomyLoopLatestPointer:
        """Transactionally complete an iteration and transition operational state, fenced against stale workers."""
        # 1. Lock latest projection FOR UPDATE
        stmt_ptr = (
            select(AutonomyLoopLatestPointer)
            .where(AutonomyLoopLatestPointer.loop_id == loop_id)
            .with_for_update()
        )
        pointer = (await session.execute(stmt_ptr)).scalar_one_or_none()
        if not pointer:
            raise ValueError(f"Operational pointer for loop '{loop_id}' not found.")

        # 2. Verify current_iteration_id matches finishing iteration
        if pointer.current_iteration_id != iteration_id:
            raise StaleIterationCompletionError(
                f"Stale worker cannot complete iteration '{iteration_id}'. "
                f"Current authoritative iteration is '{pointer.current_iteration_id}'."
            )

        current_st = AutonomyLoopState(pointer.operational_state)
        if not validate_state_transition(current_st, new_state):
            raise IllegalStateTransitionError(
                f"Illegal transition from '{current_st.value}' to '{new_state.value}'."
            )

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        # 3. Mark historical iteration completed
        stmt_iter = (
            select(AutonomyLoopIteration)
            .where(AutonomyLoopIteration.id == iteration_id)
            .with_for_update()
        )
        iter_obj = (await session.execute(stmt_iter)).scalar_one_or_none()
        if iter_obj:
            iter_obj.state = new_state.value
            iter_obj.completed_at = db_now
            iter_obj.stop_reason = stop_reason

        # 4. Set last_iteration_finished_at using DB NOW() and transition operational state
        pointer.last_iteration_finished_at = db_now
        pointer.operational_state = new_state.value
        pointer.updated_at = db_now

        # 5. Record Event
        ev_key = compute_event_dedupe_key(
            loop_id,
            AutonomyEventType.ITERATION_COMPLETED.value,
            f"{pointer.current_iteration_sequence}:{iteration_id}",
        )
        ev = AutonomyEvent(
            id=uuid4(),
            loop_id=loop_id,
            iteration_id=iteration_id,
            event_type=AutonomyEventType.ITERATION_COMPLETED.value,
            payload={
                "iteration_sequence": pointer.current_iteration_sequence,
                "completed_state": new_state.value,
                "stop_reason": stop_reason,
            },
            event_dedupe_key=ev_key,
            occurred_at=db_now,
        )
        session.add(ev)
        await session.flush()
        return pointer

    @classmethod
    async def reset_failure(
        cls,
        session: AsyncSession,
        loop_id: UUID,
        reset_reason: str,
    ) -> AutonomyLoopLatestPointer:
        """Reset a FAILED loop back to IDLE after human diagnosis."""
        stmt_ptr = (
            select(AutonomyLoopLatestPointer)
            .where(AutonomyLoopLatestPointer.loop_id == loop_id)
            .with_for_update()
        )
        pointer = (await session.execute(stmt_ptr)).scalar_one_or_none()
        if not pointer:
            raise ValueError(f"Operational pointer for loop '{loop_id}' not found.")

        current_st = AutonomyLoopState(pointer.operational_state)
        if current_st != AutonomyLoopState.FAILED:
            raise IllegalStateTransitionError(
                f"Cannot reset failure for loop in state '{current_st.value}'."
            )

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        pointer.operational_state = AutonomyLoopState.IDLE.value
        pointer.updated_at = db_now

        ev_key = compute_event_dedupe_key(
            loop_id, AutonomyEventType.FAILURE_RESET.value, str(pointer.current_iteration_sequence)
        )
        ev = AutonomyEvent(
            id=uuid4(),
            loop_id=loop_id,
            iteration_id=pointer.current_iteration_id,
            event_type=AutonomyEventType.FAILURE_RESET.value,
            payload={"reset_reason": reset_reason},
            event_dedupe_key=ev_key,
            occurred_at=db_now,
        )
        session.add(ev)
        await session.flush()
        return pointer
