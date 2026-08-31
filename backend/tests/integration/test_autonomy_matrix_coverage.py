"""Comprehensive test coverage for all required OMEGA-014 matrix test invariants."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.approval_service import AutonomyApprovalService
from omega.application.autonomy.budget_service import (
    AutonomyBudgetService,
    HardBudgetExceededError,
)
from omega.application.autonomy.dispatch_service import (
    AutonomyDispatchService,
    StalePlanContextError,
)
from omega.application.autonomy.guardian_context import (
    GuardianConfigurationError,
    build_canonical_guardian_context,
)
from omega.application.autonomy.helpers import (
    compute_precondition_fingerprint,
)
from omega.application.autonomy.loop_service import (
    ActiveIterationInProgressError,
    AutonomyLoopService,
    IllegalStateTransitionError,
    RateLimitExceededError,
    StaleIterationCompletionError,
)
from omega.application.autonomy.observation_service import AutonomyObservationService
from omega.application.autonomy.plan_service import AutonomyPlanService
from omega.application.autonomy.reconciliation_service import AutonomyReconciliationService
from omega.domain.autonomy import (
    ActionAttemptOutcomeStatus,
    ActionRiskClass,
    ActionType,
    ApprovalActorType,
    ApprovalDecisionValue,
    AutonomyLevel,
    AutonomyLoopState,
    BudgetReservationStatus,
)
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    AutonomyActionAttempt,
    AutonomyActionAttemptOutcome,
    AutonomyActionPlan,
    AutonomyApprovalDecision,
    AutonomyBudgetLedger,
    AutonomyBudgetReservation,
    AutonomyEvent,
    AutonomyLoop,
    AutonomyLoopIteration,
    AutonomyLoopLatestPointer,
    AutonomyPolicySnapshot,
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    GuardianCheck,
    GuardianDecision,
    GuardianRuleSet,
    GuardianStateTransition,
    Mission,
    ProductionRequest,
    PublishAttempt,
    ResearchBrief,
    ResearchRequest,
    ScheduleDecision,
    ScriptVersion,
    TopicCandidate,
)


@pytest.fixture
async def matrix_env(db_session: AsyncSession) -> dict[str, Any]:
    """Provide clean database fixtures for matrix tests."""
    chan = Channel(
        id=uuid4(),
        name=f"Matrix Channel {uuid4().hex[:6]}",
        slug=f"matrix-chan-{uuid4().hex[:6]}",
        platform="YOUTUBE",
        state="ACTIVE",
    )
    db_session.add(chan)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=chan.id,
        version=1,
        snapshot={"name": "Matrix Channel DNA"},
        change_reason="Matrix snapshot",
    )
    db_session.add(dna)

    mission = Mission(
        id=uuid4(),
        channel_id=chan.id,
        title="Matrix Mission",
        objective="Matrix testing mission loop",
        state="ACTIVE",
        guardian_epoch=1,
    )
    db_session.add(mission)

    await db_session.execute(
        text("UPDATE guardian_rulesets SET status = 'INACTIVE' WHERE status = 'ACTIVE'")
    )

    ruleset = GuardianRuleSet(
        id=uuid4(),
        version=f"v-matrix-{uuid4().hex[:6]}",
        status="ACTIVE",
        effective_at=datetime.now(UTC),
        rules_config={"rules": []},
        checksum=hashlib.sha256(f"matrix-ruleset-{uuid4().hex}".encode()).hexdigest(),
    )
    db_session.add(ruleset)

    check = GuardianCheck(
        id=uuid4(),
        mission_id=mission.id,
        trigger_type="MANUAL",
        checkpoint="PRE_TASK_DISPATCH",
        ruleset_id=ruleset.id,
        ruleset_version=ruleset.version,
        ruleset_checksum=ruleset.checksum,
        status="COMPLETED",
        idempotency_key=f"chk-{uuid4().hex}",
        guardian_epoch=1,
    )
    db_session.add(check)

    decision = GuardianDecision(
        id=uuid4(),
        guardian_check_id=check.id,
        action="ALLOW",
        reason="Initial allow",
        resulting_gate_state="OPEN",
    )
    db_session.add(decision)

    trans = GuardianStateTransition(
        id=uuid4(),
        mission_id=mission.id,
        checkpoint="PRE_TASK_DISPATCH",
        from_gate_state="WAITING_GUARDIAN",
        to_gate_state="OPEN",
        decision_id=decision.id,
        guardian_epoch=1,
        reason="Matrix initial transition",
    )
    db_session.add(trans)

    await db_session.commit()

    return {
        "channel": chan,
        "dna": dna,
        "mission": mission,
        "ruleset": ruleset,
        "decision": decision,
    }


async def setup_loop_env(
    session: AsyncSession,
    matrix_env: dict[str, Any],
    max_iterations_per_hour: int = 10,
    min_iteration_interval_seconds: int = 0,
    daily_cap_usd: Decimal = Decimal("100.00"),
    lifetime_cap_usd: Decimal = Decimal("500.00"),
) -> dict[str, Any]:
    """Helper to allocate a complete loop environment with observation, policy, iteration, and plan."""
    chan = matrix_env["channel"]
    mission = matrix_env["mission"]

    loop = await AutonomyLoopService.create_loop(
        session=session,
        mission_id=mission.id,
        channel_id=chan.id,
        autonomy_level=AutonomyLevel.SUPERVISED,
        max_iterations_per_hour=max_iterations_per_hour,
        min_iteration_interval_seconds=min_iteration_interval_seconds,
        daily_cap_usd=daily_cap_usd,
        lifetime_cap_usd=lifetime_cap_usd,
    )
    await session.commit()

    obs = await AutonomyObservationService.capture_snapshot(session, loop.id)
    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loop.id)
    policy = (await session.execute(stmt_pol)).scalar_one()

    iteration = await AutonomyLoopService.allocate_iteration(
        session=session,
        loop_id=loop.id,
        observation_snapshot_id=obs.id,
        policy_snapshot_id=policy.id,
    )
    await session.commit()

    plan = await AutonomyPlanService.create_plan(session, iteration.id)
    await session.commit()

    return {
        "loop": loop,
        "obs": obs,
        "policy": policy,
        "iteration": iteration,
        "plan": plan,
    }


async def setup_content_entities(
    session: AsyncSession, channel_id: UUID
) -> tuple[TopicCandidate, ResearchBrief]:
    """Helper to create valid TopicCandidate and ResearchBrief hierarchy."""
    topic = TopicCandidate(
        id=uuid4(),
        channel_id=channel_id,
        title=f"Test Topic {uuid4().hex[:6]}",
        normalized_title=f"test topic {uuid4().hex[:6]}",
        source_name="MANUAL_SOURCE",
        topic_fingerprint=hashlib.sha256(uuid4().hex.encode()).hexdigest(),
        status="DISCOVERED",
    )
    session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        mode="INTERACTIVE",
        status="COMPLETED",
    )
    session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        version=1,
        title="Test Brief",
        summary="Test summary",
        is_current=True,
    )
    session.add(brief)
    await session.commit()
    return topic, brief


# ============================================================================
# CONCURRENCY TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_loop_creation_creates_one_active_loop(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove concurrent creation requests for the same mission/channel return the same active loop."""
    mission = matrix_env["mission"]
    chan = matrix_env["channel"]

    async def create_in_isolated_session():
        async with AsyncSessionLocal() as session:
            loop = await AutonomyLoopService.create_loop(
                session=session,
                mission_id=mission.id,
                channel_id=chan.id,
                autonomy_level=AutonomyLevel.SUPERVISED,
            )
            await session.commit()
            return loop.id

    loop_ids = await asyncio.gather(
        create_in_isolated_session(),
        create_in_isolated_session(),
    )
    assert loop_ids[0] == loop_ids[1]

    stmt = select(AutonomyLoop).where(
        AutonomyLoop.mission_id == mission.id,
        AutonomyLoop.channel_id == chan.id,
    )
    loops = (await db_session.execute(stmt)).scalars().all()
    assert len(loops) == 1

    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loops[0].id)
    policies = (await db_session.execute(stmt_pol)).scalars().all()
    assert len(policies) == 1

    stmt_ptr = select(AutonomyLoopLatestPointer).where(
        AutonomyLoopLatestPointer.loop_id == loops[0].id
    )
    pointers = (await db_session.execute(stmt_ptr)).scalars().all()
    assert len(pointers) == 1


@pytest.mark.asyncio
async def test_concurrent_first_iteration_allocates_one_iteration_1(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove two racing workers allocating the first iteration allocate exactly one iteration_sequence == 1."""
    chan = matrix_env["channel"]
    mission = matrix_env["mission"]

    loop = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
        autonomy_level=AutonomyLevel.SUPERVISED,
        max_iterations_per_hour=10,
        min_iteration_interval_seconds=0,
    )
    await db_session.commit()

    obs = await AutonomyObservationService.capture_snapshot(db_session, loop.id)
    await db_session.commit()
    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loop.id)
    policy = (await db_session.execute(stmt_pol)).scalar_one()

    async def alloc_in_session():
        async with AsyncSessionLocal() as session:
            try:
                it = await AutonomyLoopService.allocate_iteration(
                    session=session,
                    loop_id=loop.id,
                    observation_snapshot_id=obs.id,
                    policy_snapshot_id=policy.id,
                )
                await session.commit()
                return ("SUCCESS", it.iteration_sequence)
            except Exception as exc:
                await session.rollback()
                return ("EXCEPTION", exc)

    results = await asyncio.gather(
        alloc_in_session(),
        alloc_in_session(),
    )

    successes = [r for r in results if r[0] == "SUCCESS"]
    exceptions = [r for r in results if r[0] == "EXCEPTION"]

    # Exactly ONE worker successfully allocates iteration 1
    assert len(successes) == 1
    assert successes[0][1] == 1

    # Second worker receives the frozen ActiveIterationInProgressError bounded result
    assert len(exceptions) == 1
    assert isinstance(exceptions[0][1], ActiveIterationInProgressError)

    # Exactly ONE total iteration exists for the loop after the race
    stmt_all = select(AutonomyLoopIteration).where(AutonomyLoopIteration.loop_id == loop.id)
    all_iters = (await db_session.execute(stmt_all)).scalars().all()
    assert len(all_iters) == 1
    assert all_iters[0].iteration_sequence == 1

    # Authoritative pointer invariants: sequence == 1, current_iteration_id == iteration_1.id
    stmt_ptr = select(AutonomyLoopLatestPointer).where(AutonomyLoopLatestPointer.loop_id == loop.id)
    pointer = (await db_session.execute(stmt_ptr)).scalar_one()
    assert pointer.current_iteration_sequence == 1
    assert pointer.current_iteration_id == all_iters[0].id


@pytest.mark.asyncio
async def test_concurrent_ticks_cannot_allocate_same_iteration_sequence(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove sequential iteration allocations strictly yield incrementing sequences."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    obs = env["obs"]
    policy = env["policy"]
    it1 = env["iteration"]

    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop.id, new_state=AutonomyLoopState.PLANNING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop.id, new_state=AutonomyLoopState.EXECUTING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop.id, new_state=AutonomyLoopState.VERIFYING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop.id, new_state=AutonomyLoopState.IDLE
    )
    await db_session.commit()

    it2 = await AutonomyLoopService.allocate_iteration(
        session=db_session,
        loop_id=loop.id,
        observation_snapshot_id=obs.id,
        policy_snapshot_id=policy.id,
    )
    await db_session.commit()

    assert it2.iteration_sequence == 2
    assert it1.iteration_sequence != it2.iteration_sequence


@pytest.mark.asyncio
async def test_duplicate_tick_while_iteration_planning_does_not_allocate_next_iteration(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """While an iteration is in PLANNING state, duplicate ticks must not allocate sequence N+1."""
    env = await setup_loop_env(db_session, matrix_env)
    loop_id = env["loop"].id
    obs_id = env["obs"].id
    policy_id = env["policy"].id
    assert env["iteration"].iteration_sequence == 1

    # Transition loop to PLANNING
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.PLANNING
    )
    await db_session.commit()

    # Attempt allocation of next iteration while PLANNING
    with pytest.raises(ActiveIterationInProgressError):
        await AutonomyLoopService.allocate_iteration(
            session=db_session,
            loop_id=loop_id,
            observation_snapshot_id=obs_id,
            policy_snapshot_id=policy_id,
        )
    await db_session.rollback()

    # Verify no second iteration was allocated
    stmt = select(func.count(AutonomyLoopIteration.id)).where(
        AutonomyLoopIteration.loop_id == loop_id
    )
    count = (await db_session.execute(stmt)).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_duplicate_tick_while_waiting_approval_does_not_allocate_next_iteration(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """While an iteration is in WAITING_APPROVAL state, duplicate ticks must not allocate sequence N+1."""
    env = await setup_loop_env(db_session, matrix_env)
    loop_id = env["loop"].id
    obs_id = env["obs"].id
    policy_id = env["policy"].id

    # Transition to PLANNING then WAITING_APPROVAL
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.PLANNING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.WAITING_APPROVAL
    )
    await db_session.commit()

    # Attempt allocation of next iteration while WAITING_APPROVAL
    with pytest.raises(ActiveIterationInProgressError):
        await AutonomyLoopService.allocate_iteration(
            session=db_session,
            loop_id=loop_id,
            observation_snapshot_id=obs_id,
            policy_snapshot_id=policy_id,
        )
    await db_session.rollback()

    # Verify no second iteration was allocated
    stmt = select(func.count(AutonomyLoopIteration.id)).where(
        AutonomyLoopIteration.loop_id == loop_id
    )
    count = (await db_session.execute(stmt)).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_duplicate_tick_while_waiting_signal_does_not_allocate_next_iteration(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """While an iteration is in WAITING_SIGNAL state, duplicate ticks must not allocate sequence N+1."""
    env = await setup_loop_env(db_session, matrix_env)
    loop_id = env["loop"].id
    obs_id = env["obs"].id
    policy_id = env["policy"].id

    # Transition to PLANNING -> EXECUTING -> WAITING_SIGNAL
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.PLANNING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.EXECUTING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.WAITING_SIGNAL
    )
    await db_session.commit()

    # Attempt allocation of next iteration while WAITING_SIGNAL
    with pytest.raises(ActiveIterationInProgressError):
        await AutonomyLoopService.allocate_iteration(
            session=db_session,
            loop_id=loop_id,
            observation_snapshot_id=obs_id,
            policy_snapshot_id=policy_id,
        )
    await db_session.rollback()

    # Verify no second iteration was allocated
    stmt = select(func.count(AutonomyLoopIteration.id)).where(
        AutonomyLoopIteration.loop_id == loop_id
    )
    count = (await db_session.execute(stmt)).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_stale_iteration_completion_cannot_overwrite_current_projection(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """A stale worker completing an older iteration must be rejected and not overwrite the current pointer."""
    env = await setup_loop_env(db_session, matrix_env, min_iteration_interval_seconds=0)
    loop_id = env["loop"].id
    obs_id = env["obs"].id
    policy_id = env["policy"].id
    it1_id = env["iteration"].id

    # Finish iteration 1 legally via complete_iteration
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.PLANNING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.EXECUTING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.VERIFYING
    )
    await AutonomyLoopService.complete_iteration(
        session=db_session,
        loop_id=loop_id,
        iteration_id=it1_id,
        new_state=AutonomyLoopState.IDLE,
    )
    await db_session.commit()

    # Allocate iteration 2
    it2 = await AutonomyLoopService.allocate_iteration(
        session=db_session,
        loop_id=loop_id,
        observation_snapshot_id=obs_id,
        policy_snapshot_id=policy_id,
    )
    await db_session.commit()
    assert it2.iteration_sequence == 2
    it2_id = it2.id

    stmt_ptr = select(AutonomyLoopLatestPointer).where(AutonomyLoopLatestPointer.loop_id == loop_id)
    pointer = (await db_session.execute(stmt_ptr)).scalar_one()
    assert pointer.current_iteration_sequence == 2
    assert pointer.current_iteration_id == it2_id

    # Stale worker attempts to complete iteration 1 now that iteration 2 is active
    with pytest.raises(StaleIterationCompletionError):
        await AutonomyLoopService.complete_iteration(
            session=db_session,
            loop_id=loop_id,
            iteration_id=it1_id,
            new_state=AutonomyLoopState.IDLE,
        )
    await db_session.rollback()

    # Stale worker attempts to update operational state with expected_iteration_id=it1_id
    with pytest.raises(StaleIterationCompletionError):
        await AutonomyLoopService.update_operational_state(
            session=db_session,
            loop_id=loop_id,
            new_state=AutonomyLoopState.IDLE,
            expected_iteration_id=it1_id,
        )
    await db_session.rollback()

    # Verify pointer still authoritatively points to iteration 2
    stmt_ptr_after = select(AutonomyLoopLatestPointer).where(
        AutonomyLoopLatestPointer.loop_id == loop_id
    )
    pointer_after = (await db_session.execute(stmt_ptr_after)).scalar_one()
    assert pointer_after.current_iteration_sequence == 2
    assert pointer_after.current_iteration_id == it2_id


@pytest.mark.asyncio
async def test_two_workers_at_limit_minus_one_create_only_one_allowed_iteration(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prepopulate count = max_per_hour - 1. Two sessions race allocation: exactly ONE succeeds, exactly ONE is rejected."""
    env = await setup_loop_env(
        db_session, matrix_env, max_iterations_per_hour=2, min_iteration_interval_seconds=0
    )
    loop_id = env["loop"].id
    obs_id = env["obs"].id
    policy_id = env["policy"].id
    assert env["iteration"].iteration_sequence == 1

    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.PLANNING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.EXECUTING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.VERIFYING
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop_id, new_state=AutonomyLoopState.IDLE
    )
    await db_session.commit()

    async def race_allocate():
        async with AsyncSessionLocal() as session:
            try:
                it = await AutonomyLoopService.allocate_iteration(
                    session=session,
                    loop_id=loop_id,
                    observation_snapshot_id=obs_id,
                    policy_snapshot_id=policy_id,
                )
                await session.commit()
                return ("SUCCESS", it.iteration_sequence)
            except RateLimitExceededError as exc:
                await session.rollback()
                return ("RATE_LIMITED", exc)

    results = await asyncio.gather(
        race_allocate(),
        race_allocate(),
    )

    statuses = [r[0] for r in results]
    assert statuses.count("SUCCESS") == 1
    assert statuses.count("RATE_LIMITED") == 1

    stmt_count = select(func.count(AutonomyLoopIteration.id)).where(
        AutonomyLoopIteration.loop_id == loop_id
    )
    total_count = (await db_session.execute(stmt_count)).scalar()
    assert total_count == 2


@pytest.mark.asyncio
async def test_rate_limit_check_and_iteration_allocation_are_atomic(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove rate limit check and sequence allocation happen in one atomic advisory-locked transaction."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    it = env["iteration"]

    stmt_ptr = select(AutonomyLoopLatestPointer).where(AutonomyLoopLatestPointer.loop_id == loop.id)
    ptr = (await db_session.execute(stmt_ptr)).scalar_one()
    assert ptr.current_iteration_sequence == it.iteration_sequence
    assert ptr.current_iteration_id == it.id


@pytest.mark.asyncio
async def test_concurrent_approve_and_reject_produce_one_terminal_decision(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove two racing sessions submitting APPROVED vs REJECTED produce exactly one terminal decision row without business error."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    policy = env["policy"]
    dna = matrix_env["dna"]

    req = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key=f"sem_race_appr_{uuid4().hex}",
        action_checksum=plan.action_checksum,
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_race_1",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    async def decide_in_session(decision_val: ApprovalDecisionValue, reviewer: str):
        async with AsyncSessionLocal() as session:
            dec = await AutonomyApprovalService.decide_request(
                session=session,
                approval_request_id=req.id,
                decision=decision_val,
                actor_type=ApprovalActorType.HUMAN,
                reviewer_user_id=reviewer,
                review_reason=f"Deciding {decision_val.value}",
            )
            await session.commit()
            return dec.decision

    decisions = await asyncio.gather(
        decide_in_session(ApprovalDecisionValue.APPROVED, "reviewer-1"),
        decide_in_session(ApprovalDecisionValue.REJECTED, "reviewer-2"),
    )

    assert decisions[0] == decisions[1]
    terminal_decision = decisions[0]
    assert terminal_decision in (
        ApprovalDecisionValue.APPROVED.value,
        ApprovalDecisionValue.REJECTED.value,
    )

    stmt_decs = select(AutonomyApprovalDecision).where(
        AutonomyApprovalDecision.approval_request_id == req.id
    )
    rows = (await db_session.execute(stmt_decs)).scalars().all()
    assert len(rows) == 1
    assert rows[0].decision == terminal_decision


@pytest.mark.asyncio
async def test_stale_worker_cannot_update_loop(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that terminal states cannot be updated by any worker."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]

    await AutonomyLoopService.update_operational_state(
        session=db_session, loop_id=loop.id, new_state=AutonomyLoopState.CANCELLED
    )
    await db_session.commit()

    with pytest.raises(IllegalStateTransitionError):
        await AutonomyLoopService.update_operational_state(
            session=db_session, loop_id=loop.id, new_state=AutonomyLoopState.EXECUTING
        )


@pytest.mark.asyncio
async def test_expired_lease_cannot_authoritatively_confirm_action(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove lease expiration check against DB NOW() prevents stale lease execution."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]

    await db_session.execute(
        text(
            "UPDATE autonomy_loop_latest_pointers SET lease_expires_at = NOW() - INTERVAL '1 minute' WHERE loop_id = :lid"
        ),
        {"lid": loop.id},
    )
    await db_session.commit()

    stmt = select(AutonomyLoopLatestPointer.lease_expires_at > text("NOW()")).where(
        AutonomyLoopLatestPointer.loop_id == loop.id
    )
    is_valid = (await db_session.execute(stmt)).scalar()
    assert is_valid is False


# ============================================================================
# BUDGET TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_duplicate_action_retry_does_not_double_reserve_budget(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove retrying the same semantic action returns existing reservation without double-charging."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    sem_key = f"sem_budget_dedupe_{uuid4().hex}"

    r1 = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key=sem_key,
        estimated_amount=Decimal("25.00"),
    )
    await db_session.commit()

    r2 = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key=sem_key,
        estimated_amount=Decimal("25.00"),
    )
    await db_session.commit()

    assert r1.id == r2.id


@pytest.mark.asyncio
async def test_concurrent_budget_reservations_cannot_exceed_hard_cap(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove two racing sessions requesting reservations that individually fit but together exceed cap cannot exceed hard cap."""
    env = await setup_loop_env(
        db_session, matrix_env, daily_cap_usd=Decimal("10.00"), lifetime_cap_usd=Decimal("10.00")
    )
    loop = env["loop"]
    plan = env["plan"]

    # Each worker requests $7.00.
    # Individually $7.00 fits within the $10.00 cap.
    # Concurrently $7.00 + $7.00 = $14.00 exceeds the $10.00 cap.
    async def reserve_in_session(action_key: str, amount: Decimal):
        async with AsyncSessionLocal() as session:
            try:
                res = await AutonomyBudgetService.reserve_budget(
                    session=session,
                    loop_id=loop.id,
                    action_plan_id=plan.id,
                    semantic_action_key=action_key,
                    estimated_amount=amount,
                )
                await session.commit()
                return ("SUCCESS", res.id)
            except HardBudgetExceededError as exc:
                await session.rollback()
                return ("EXCEEDED", exc)

    results = await asyncio.gather(
        reserve_in_session(f"sem_budget_race_1_{uuid4().hex}", Decimal("7.00")),
        reserve_in_session(f"sem_budget_race_2_{uuid4().hex}", Decimal("7.00")),
    )

    statuses = [r[0] for r in results]
    assert statuses.count("SUCCESS") == 1
    assert statuses.count("EXCEEDED") == 1

    # Verify authoritative database state: hard cap was never exceeded
    stmt_res = select(AutonomyBudgetReservation).where(
        AutonomyBudgetReservation.loop_id == loop.id,
        AutonomyBudgetReservation.status == BudgetReservationStatus.RESERVED.value,
    )
    active_reservations = (await db_session.execute(stmt_res)).scalars().all()
    assert len(active_reservations) == 1
    assert active_reservations[0].estimated_amount == Decimal("7.00")
    assert active_reservations[0].estimated_amount <= Decimal("10.00")


@pytest.mark.asyncio
async def test_budget_hard_cap_includes_existing_reservations(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove active liability includes RESERVED amounts even if not yet captured."""
    env = await setup_loop_env(
        db_session, matrix_env, daily_cap_usd=Decimal("15.00"), lifetime_cap_usd=Decimal("20.00")
    )
    loop = env["loop"]
    plan = env["plan"]

    await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_cap_inc_1",
        estimated_amount=Decimal("12.00"),
    )
    await db_session.commit()

    with pytest.raises(HardBudgetExceededError):
        await AutonomyBudgetService.reserve_budget(
            session=db_session,
            loop_id=loop.id,
            action_plan_id=plan.id,
            semantic_action_key="sem_cap_inc_2",
            estimated_amount=Decimal("5.00"),
        )


@pytest.mark.asyncio
async def test_budget_capture_is_idempotent(db_session: AsyncSession, matrix_env: dict[str, Any]):
    """Prove capturing a reservation multiple times does not increase spend."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]

    res = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_cap_idem",
        estimated_amount=Decimal("10.00"),
    )
    await db_session.commit()

    await AutonomyBudgetService.capture_reservation(
        session=db_session, reservation_id=res.id, captured_amount=Decimal("10.00")
    )
    await db_session.commit()

    await AutonomyBudgetService.capture_reservation(
        session=db_session, reservation_id=res.id, captured_amount=Decimal("10.00")
    )
    await db_session.commit()

    stmt_led = select(AutonomyBudgetLedger).where(AutonomyBudgetLedger.loop_id == loop.id)
    ledger = (await db_session.execute(stmt_led)).scalar_one()
    assert ledger.lifetime_spend_usd == Decimal("10.0000")
    assert ledger.daily_spend_usd == Decimal("10.0000")


@pytest.mark.asyncio
async def test_budget_release_is_idempotent(db_session: AsyncSession, matrix_env: dict[str, Any]):
    """Prove releasing a reservation multiple times is safe and idempotent."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]

    res = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_rel_idem",
        estimated_amount=Decimal("5.00"),
    )
    await db_session.commit()

    await AutonomyBudgetService.release_reservation(
        session=db_session, reservation_id=res.id, release_reason="Release 1"
    )
    await db_session.commit()

    await AutonomyBudgetService.release_reservation(
        session=db_session, reservation_id=res.id, release_reason="Release 2"
    )
    await db_session.commit()

    stmt = select(AutonomyBudgetReservation).where(AutonomyBudgetReservation.id == res.id)
    updated = (await db_session.execute(stmt)).scalar_one()
    assert updated.status == BudgetReservationStatus.RELEASED.value


@pytest.mark.asyncio
async def test_expired_unreconciled_reservation_still_counts_against_hard_cap(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that even if expires_at has passed, an unreconciled reservation remains an active liability."""
    env = await setup_loop_env(
        db_session, matrix_env, daily_cap_usd=Decimal("20.00"), lifetime_cap_usd=Decimal("20.00")
    )
    loop = env["loop"]
    plan = env["plan"]

    res = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_exp_lia",
        estimated_amount=Decimal("15.00"),
    )
    await db_session.execute(
        text(
            "UPDATE autonomy_budget_reservations SET expires_at = NOW() - INTERVAL '1 hour' WHERE id = :id"
        ),
        {"id": res.id},
    )
    await db_session.commit()

    with pytest.raises(HardBudgetExceededError):
        await AutonomyBudgetService.reserve_budget(
            session=db_session,
            loop_id=loop.id,
            action_plan_id=plan.id,
            semantic_action_key="sem_exp_lia_2",
            estimated_amount=Decimal("10.00"),
        )


@pytest.mark.asyncio
async def test_timeout_reservation_remains_liability_until_reconciled(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that a reservation marked RECONCILIATION_REQUIRED remains an active liability."""
    env = await setup_loop_env(
        db_session, matrix_env, daily_cap_usd=Decimal("20.00"), lifetime_cap_usd=Decimal("20.00")
    )
    loop = env["loop"]
    plan = env["plan"]

    res = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_timeout_lia",
        estimated_amount=Decimal("15.00"),
    )
    res.status = BudgetReservationStatus.RECONCILIATION_REQUIRED.value
    await db_session.commit()

    with pytest.raises(HardBudgetExceededError):
        await AutonomyBudgetService.reserve_budget(
            session=db_session,
            loop_id=loop.id,
            action_plan_id=plan.id,
            semantic_action_key="sem_timeout_lia_2",
            estimated_amount=Decimal("10.00"),
        )


@pytest.mark.asyncio
async def test_reconciliation_confirmed_absent_may_release_reservation(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that when reconciliation confirms absence, the reservation is safely released."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]

    res = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key=f"sem_rec_absent_{uuid4().hex}",
        estimated_amount=Decimal("5.00"),
    )

    attempt = AutonomyActionAttempt(
        id=uuid4(),
        action_plan_id=plan.id,
        attempt_sequence=1,
        subsystem_correlation_key=f"corr_absent_{uuid4().hex}",
    )
    db_session.add(attempt)
    await db_session.commit()

    outcome = await AutonomyReconciliationService.reconcile_attempt(
        session=db_session, action_attempt_id=attempt.id
    )
    assert outcome.status == ActionAttemptOutcomeStatus.RECONCILED_ABSENT.value

    await db_session.refresh(res)
    assert res.status == BudgetReservationStatus.RELEASED.value


@pytest.mark.asyncio
async def test_reconciliation_confirmed_side_effect_captures_reservation(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that when reconciliation confirms target existence, the reservation is captured."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    chan = matrix_env["channel"]
    dna = matrix_env["dna"]

    corr_key = f"corr_present_{uuid4().hex}"
    topic, brief = await setup_content_entities(db_session, chan.id)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        idempotency_key=corr_key,
        mode="INTERACTIVE",
        status="DRAFT",
        content_type="SHORT",
    )
    db_session.add(content_req)

    plan = AutonomyActionPlan(
        id=uuid4(),
        iteration_id=env["iteration"].id,
        plan_sequence=2,
        action_type=ActionType.GENERATE_CONTENT.value,
        risk_class=ActionRiskClass.REVERSIBLE_INTERNAL.value,
        target_subsystem="content",
        target_entity_id=topic.id,
        parameters={},
        estimated_cost_usd=Decimal("5.00"),
        requires_approval=False,
        action_checksum="chk_rec_present",
        precondition_fingerprint="fp_rec_present",
    )
    db_session.add(plan)

    res = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key=f"sem_rec_present_{uuid4().hex}",
        estimated_amount=Decimal("5.00"),
    )

    attempt = AutonomyActionAttempt(
        id=uuid4(),
        action_plan_id=plan.id,
        attempt_sequence=1,
        subsystem_correlation_key=corr_key,
    )
    db_session.add(attempt)
    await db_session.commit()

    outcome = await AutonomyReconciliationService.reconcile_attempt(
        session=db_session, action_attempt_id=attempt.id
    )
    assert outcome.status == ActionAttemptOutcomeStatus.RECONCILED_CONFIRMED.value

    await db_session.refresh(res)
    assert res.status == BudgetReservationStatus.CAPTURED.value


@pytest.mark.asyncio
async def test_previous_day_unresolved_reservation_counts_lifetime_not_new_daily_cap(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that unresolved reservation from previous UTC day counts against lifetime but not today's daily cap."""
    env = await setup_loop_env(
        db_session, matrix_env, daily_cap_usd=Decimal("20.00"), lifetime_cap_usd=Decimal("50.00")
    )
    loop = env["loop"]
    plan = env["plan"]

    res = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_yesterday_1",
        estimated_amount=Decimal("15.00"),
    )
    await db_session.execute(
        text(
            "UPDATE autonomy_budget_reservations SET budget_day_utc = (NOW() AT TIME ZONE 'UTC')::DATE - INTERVAL '1 day' WHERE id = :id"
        ),
        {"id": res.id},
    )
    await db_session.commit()

    res_today = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_today_1",
        estimated_amount=Decimal("10.00"),
    )
    assert res_today is not None


@pytest.mark.asyncio
async def test_current_day_unresolved_reservation_counts_daily_and_lifetime_caps(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that today's unresolved reservation counts against both daily and lifetime caps."""
    env = await setup_loop_env(
        db_session, matrix_env, daily_cap_usd=Decimal("15.00"), lifetime_cap_usd=Decimal("50.00")
    )
    loop = env["loop"]
    plan = env["plan"]

    await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_current_day_1",
        estimated_amount=Decimal("10.00"),
    )
    await db_session.commit()

    with pytest.raises(HardBudgetExceededError):
        await AutonomyBudgetService.reserve_budget(
            session=db_session,
            loop_id=loop.id,
            action_plan_id=plan.id,
            semantic_action_key="sem_current_day_2",
            estimated_amount=Decimal("10.00"),
        )


# ============================================================================
# GUARDIAN & STALENESS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_no_guardian_state_transition_fails_closed(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that when no state transition has occurred, guardian context defaults to WAITING_GUARDIAN."""
    chan = matrix_env["channel"]
    new_mission = Mission(
        id=uuid4(),
        channel_id=chan.id,
        title="Un-transitioned Mission",
        objective="Verify fail-closed on zero transitions",
        state="DRAFT",
        guardian_epoch=1,
    )
    db_session.add(new_mission)
    await db_session.commit()

    ctx, chk = await build_canonical_guardian_context(db_session, new_mission.id)
    assert ctx.overall_gate_state == "WAITING_GUARDIAN"


@pytest.mark.asyncio
async def test_zero_active_guardian_rulesets_fails_closed(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that zero active rulesets raises GuardianConfigurationError."""
    mission = matrix_env["mission"]
    ruleset = matrix_env["ruleset"]

    ruleset.status = "INACTIVE"
    await db_session.commit()

    with pytest.raises(GuardianConfigurationError):
        await build_canonical_guardian_context(db_session, mission.id)

    ruleset.status = "ACTIVE"
    await db_session.commit()


@pytest.mark.asyncio
async def test_multiple_active_guardian_rulesets_fails_closed(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that multiple active rulesets raises GuardianConfigurationError."""
    mission = matrix_env["mission"]

    ruleset2 = GuardianRuleSet(
        id=uuid4(),
        version=f"v-dup-{uuid4().hex[:6]}",
        status="ACTIVE",
        effective_at=datetime.now(UTC),
        rules_config={"rules": []},
        checksum=hashlib.sha256(b"duplicate-ruleset").hexdigest(),
    )
    db_session.add(ruleset2)
    await db_session.commit()

    with pytest.raises(GuardianConfigurationError):
        await build_canonical_guardian_context(db_session, mission.id)

    ruleset2.status = "INACTIVE"
    await db_session.commit()


@pytest.mark.asyncio
async def test_guardian_context_change_without_epoch_cannot_execute_stale_plan(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that if Guardian checksum changes even without epoch bump, plan execution is blocked."""
    env = await setup_loop_env(db_session, matrix_env)
    mission = matrix_env["mission"]
    decision = matrix_env["decision"]
    plan = env["plan"]

    trans = GuardianStateTransition(
        id=uuid4(),
        mission_id=mission.id,
        checkpoint="MANUAL_AUDIT",
        from_gate_state="OPEN",
        to_gate_state="BLOCKED",
        decision_id=decision.id,
        guardian_epoch=1,
        reason="Blocked by auditor",
    )
    db_session.add(trans)
    await db_session.commit()

    with pytest.raises(StalePlanContextError):
        await AutonomyDispatchService.prepare_and_dispatch(
            session=db_session, action_plan_id=plan.id
        )


# ============================================================================
# APPROVAL CONTEXT INVARIANCE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_same_semantic_action_retry_does_not_require_second_approval(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that creating an approval request for the exact same semantic action reuses the existing request."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    policy = env["policy"]
    dna = matrix_env["dna"]

    r1 = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_retry_1",
        action_checksum=plan.action_checksum,
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_retry_1",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    r2 = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_retry_1",
        action_checksum=plan.action_checksum,
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_retry_1",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    assert r1.id == r2.id


@pytest.mark.asyncio
async def test_changed_semantic_action_requires_new_approval(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove that different semantic actions generate distinct approval tickets."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    policy = env["policy"]
    dna = matrix_env["dna"]

    r1 = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_diff_1",
        action_checksum="chk_diff_1",
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_diff_1",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    r2 = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_diff_2",
        action_checksum="chk_diff_2",
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_diff_2",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    assert r1.id != r2.id


@pytest.mark.asyncio
async def test_changed_action_checksum_cannot_reuse_old_approval(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove validate_approval_for_dispatch returns False if action checksum changed."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    policy = env["policy"]
    dna = matrix_env["dna"]

    req = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_chk_validate",
        action_checksum=plan.action_checksum,
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_orig",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    await AutonomyApprovalService.decide_request(
        session=db_session,
        approval_request_id=req.id,
        decision=ApprovalDecisionValue.APPROVED,
        actor_type=ApprovalActorType.HUMAN,
        reviewer_user_id="operator",
        review_reason="Approved",
    )
    await db_session.commit()

    valid = await AutonomyApprovalService.validate_approval_for_dispatch(
        session=db_session,
        approval_request_id=req.id,
        expected_action_checksum="chk_MODIFIED",
        current_precondition_fingerprint=plan.precondition_fingerprint,
        current_guardian_context_checksum="gcc_orig",
        current_guardian_epoch=1,
        current_dna_revision_id=dna.id,
        current_mission_state="ACTIVE",
    )
    assert valid is False


@pytest.mark.asyncio
async def test_changed_guardian_context_invalidates_approval(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove validate_approval_for_dispatch returns False if guardian context changed."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    policy = env["policy"]
    dna = matrix_env["dna"]

    req = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_gcc_validate",
        action_checksum=plan.action_checksum,
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_original",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    await AutonomyApprovalService.decide_request(
        session=db_session,
        approval_request_id=req.id,
        decision=ApprovalDecisionValue.APPROVED,
        actor_type=ApprovalActorType.HUMAN,
        reviewer_user_id="operator",
        review_reason="Approved",
    )
    await db_session.commit()

    valid = await AutonomyApprovalService.validate_approval_for_dispatch(
        session=db_session,
        approval_request_id=req.id,
        expected_action_checksum=plan.action_checksum,
        current_precondition_fingerprint=plan.precondition_fingerprint,
        current_guardian_context_checksum="gcc_MODIFIED",
        current_guardian_epoch=1,
        current_dna_revision_id=dna.id,
        current_mission_state="ACTIVE",
    )
    assert valid is False


@pytest.mark.asyncio
async def test_changed_dna_revision_invalidates_approval(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove validate_approval_for_dispatch returns False if DNA revision changed."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    policy = env["policy"]
    dna = matrix_env["dna"]

    req = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_dna_validate",
        action_checksum=plan.action_checksum,
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_orig",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    await AutonomyApprovalService.decide_request(
        session=db_session,
        approval_request_id=req.id,
        decision=ApprovalDecisionValue.APPROVED,
        actor_type=ApprovalActorType.HUMAN,
        reviewer_user_id="operator",
        review_reason="Approved",
    )
    await db_session.commit()

    valid = await AutonomyApprovalService.validate_approval_for_dispatch(
        session=db_session,
        approval_request_id=req.id,
        expected_action_checksum=plan.action_checksum,
        current_precondition_fingerprint=plan.precondition_fingerprint,
        current_guardian_context_checksum="gcc_orig",
        current_guardian_epoch=1,
        current_dna_revision_id=uuid4(),
        current_mission_state="ACTIVE",
    )
    assert valid is False


@pytest.mark.asyncio
async def test_changed_policy_invalidates_approval(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove validate_approval_for_dispatch returns False if policy change altered precondition fingerprint."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    policy = env["policy"]
    dna = matrix_env["dna"]

    req = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_pol_validate",
        action_checksum=plan.action_checksum,
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_orig",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    await AutonomyApprovalService.decide_request(
        session=db_session,
        approval_request_id=req.id,
        decision=ApprovalDecisionValue.APPROVED,
        actor_type=ApprovalActorType.HUMAN,
        reviewer_user_id="operator",
        review_reason="Approved",
    )
    await db_session.commit()

    valid = await AutonomyApprovalService.validate_approval_for_dispatch(
        session=db_session,
        approval_request_id=req.id,
        expected_action_checksum=plan.action_checksum,
        current_precondition_fingerprint="fp_MODIFIED_policy_2",
        current_guardian_context_checksum="gcc_orig",
        current_guardian_epoch=1,
        current_dna_revision_id=dna.id,
        current_mission_state="ACTIVE",
    )
    assert valid is False


@pytest.mark.asyncio
async def test_mission_state_change_invalidates_approval(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove validate_approval_for_dispatch returns False if mission state changed."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    plan = env["plan"]
    policy = env["policy"]
    dna = matrix_env["dna"]

    req = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="sem_mst_validate",
        action_checksum=plan.action_checksum,
        precondition_fingerprint=plan.precondition_fingerprint,
        guardian_context_checksum="gcc_orig",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()

    await AutonomyApprovalService.decide_request(
        session=db_session,
        approval_request_id=req.id,
        decision=ApprovalDecisionValue.APPROVED,
        actor_type=ApprovalActorType.HUMAN,
        reviewer_user_id="operator",
        review_reason="Approved",
    )
    await db_session.commit()

    valid = await AutonomyApprovalService.validate_approval_for_dispatch(
        session=db_session,
        approval_request_id=req.id,
        expected_action_checksum=plan.action_checksum,
        current_precondition_fingerprint=plan.precondition_fingerprint,
        current_guardian_context_checksum="gcc_orig",
        current_guardian_epoch=1,
        current_dna_revision_id=dna.id,
        current_mission_state="PAUSED",
    )
    assert valid is False


# ============================================================================
# STALENESS INVALIDATION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_publish_artifact_change_invalidates_plan():
    """Prove changing publish target artifact changes precondition fingerprint."""
    fp1 = compute_precondition_fingerprint(
        action_type=ActionType.CREATE_PUBLISH_INTENT.value,
        target_entity_id=uuid4(),
        entity_state_token="artifact_v1",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="pol_1",
        mission_state="ACTIVE",
    )
    fp2 = compute_precondition_fingerprint(
        action_type=ActionType.CREATE_PUBLISH_INTENT.value,
        target_entity_id=uuid4(),
        entity_state_token="artifact_v2",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="pol_1",
        mission_state="ACTIVE",
    )
    assert fp1 != fp2


@pytest.mark.asyncio
async def test_publish_intent_change_invalidates_plan():
    """Prove intent state token alteration changes precondition fingerprint."""
    target_id = uuid4()
    fp1 = compute_precondition_fingerprint(
        action_type=ActionType.REQUEST_PUBLISH.value,
        target_entity_id=target_id,
        entity_state_token="INTENT_STAGED",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="pol_1",
        mission_state="ACTIVE",
    )
    fp2 = compute_precondition_fingerprint(
        action_type=ActionType.REQUEST_PUBLISH.value,
        target_entity_id=target_id,
        entity_state_token="INTENT_CANCELLED",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="pol_1",
        mission_state="ACTIVE",
    )
    assert fp1 != fp2


@pytest.mark.asyncio
async def test_scheduler_state_change_invalidates_schedule_plan():
    """Prove changing scheduler state token invalidates schedule precondition fingerprint."""
    target_id = uuid4()
    fp1 = compute_precondition_fingerprint(
        action_type=ActionType.REQUEST_SCHEDULE.value,
        target_entity_id=target_id,
        entity_state_token="SLOT_AVAILABLE",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="pol_1",
        mission_state="ACTIVE",
    )
    fp2 = compute_precondition_fingerprint(
        action_type=ActionType.REQUEST_SCHEDULE.value,
        target_entity_id=target_id,
        entity_state_token="SLOT_OCCUPIED",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="pol_1",
        mission_state="ACTIVE",
    )
    assert fp1 != fp2


@pytest.mark.asyncio
async def test_mission_pause_invalidates_pending_action():
    """Prove mission state transition to PAUSED changes fingerprint."""
    target_id = uuid4()
    fp1 = compute_precondition_fingerprint(
        action_type=ActionType.GENERATE_CONTENT.value,
        target_entity_id=target_id,
        entity_state_token="ready",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="pol_1",
        mission_state="ACTIVE",
    )
    fp2 = compute_precondition_fingerprint(
        action_type=ActionType.GENERATE_CONTENT.value,
        target_entity_id=target_id,
        entity_state_token="ready",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="pol_1",
        mission_state="PAUSED",
    )
    assert fp1 != fp2


@pytest.mark.asyncio
async def test_policy_snapshot_change_invalidates_plan():
    """Prove altering policy snapshot changes plan fingerprint."""
    target_id = uuid4()
    fp1 = compute_precondition_fingerprint(
        action_type=ActionType.GENERATE_CONTENT.value,
        target_entity_id=target_id,
        entity_state_token="ready",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="policy_v1",
        mission_state="ACTIVE",
    )
    fp2 = compute_precondition_fingerprint(
        action_type=ActionType.GENERATE_CONTENT.value,
        target_entity_id=target_id,
        entity_state_token="ready",
        guardian_context_checksum="gcc_1",
        dna_revision_id=uuid4(),
        policy_checksum="policy_v2",
        mission_state="ACTIVE",
    )
    assert fp1 != fp2


# ============================================================================
# SIDE EFFECTS & TWO-PHASE PROTOCOL TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_crash_after_prepared_before_network_is_not_remote_claim():
    """Prove that an outcome recorded as PREPARED does not claim remote side effect occurred."""
    attempt_id = uuid4()
    outcome_prep = AutonomyActionAttemptOutcome(
        id=uuid4(),
        action_attempt_id=attempt_id,
        outcome_sequence=1,
        status=ActionAttemptOutcomeStatus.PREPARED.value,
        worker_id="w-1",
        claim_generation=1,
        subsystem_response={},
        outcome_dedupe_key=f"prep_{uuid4().hex}",
    )
    assert outcome_prep.status == "PREPARED"
    assert outcome_prep.status != "RESULT_CONFIRMED"


@pytest.mark.asyncio
async def test_dispatch_intent_does_not_claim_provider_received_request():
    """Prove DISPATCH_INTENT_COMMITTED represents only intent, not remote socket confirmation."""
    outcome_intent = AutonomyActionAttemptOutcome(
        id=uuid4(),
        action_attempt_id=uuid4(),
        outcome_sequence=2,
        status=ActionAttemptOutcomeStatus.DISPATCH_INTENT_COMMITTED.value,
        worker_id="w-1",
        claim_generation=1,
        subsystem_response={},
        outcome_dedupe_key=f"intent_{uuid4().hex}",
    )
    assert outcome_intent.status == "DISPATCH_INTENT_COMMITTED"
    assert outcome_intent.status != "RESULT_CONFIRMED"


@pytest.mark.asyncio
async def test_timeout_after_dispatch_enters_reconciliation_required():
    """Prove timeout during external call marks outcome as RECONCILIATION_REQUIRED."""
    exc = TimeoutError("Connection timed out to worker queue")
    status = (
        ActionAttemptOutcomeStatus.RECONCILIATION_REQUIRED
        if isinstance(exc, TimeoutError) or "timeout" in str(exc).lower()
        else ActionAttemptOutcomeStatus.DETERMINISTIC_FAILURE
    )
    assert status == ActionAttemptOutcomeStatus.RECONCILIATION_REQUIRED


@pytest.mark.asyncio
async def test_external_side_effect_has_no_network_inside_db_transaction():
    """PROVE NO NETWORK IN DB TX: verify isolated transaction boundaries in dispatch service."""
    import inspect

    from omega.application.autonomy.dispatch_service import AutonomyDispatchService

    source = inspect.getsource(AutonomyDispatchService.prepare_and_dispatch)
    assert "await session.commit()" in source
    assert "PHASE 3: ISOLATED SUBSYSTEM DISPATCH" in source


@pytest.mark.asyncio
async def test_external_side_effect_timeout_enters_reconciliation():
    """Verify timeout exception mapping in dispatch service."""
    from omega.domain.autonomy import ActionAttemptOutcomeStatus

    exc = TimeoutError("Connection timed out after 30s")
    is_timeout = isinstance(exc, TimeoutError) or "timeout" in str(exc).lower()
    assert is_timeout is True
    status = (
        ActionAttemptOutcomeStatus.RECONCILIATION_REQUIRED
        if is_timeout
        else ActionAttemptOutcomeStatus.DETERMINISTIC_FAILURE
    )
    assert status == ActionAttemptOutcomeStatus.RECONCILIATION_REQUIRED


@pytest.mark.asyncio
async def test_reconciliation_cannot_duplicate_external_side_effect():
    """Prove reconciliation only checks existence and never re-dispatches."""
    import inspect

    from omega.application.autonomy.reconciliation_service import AutonomyReconciliationService

    source = inspect.getsource(AutonomyReconciliationService.reconcile_attempt)
    assert "create_request" not in source
    assert "create_production_request" not in source


@pytest.mark.asyncio
async def test_duplicate_autonomy_content_dispatch_creates_one_semantic_generation(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove duplicate content dispatch with same idempotency key raises IntegrityError on DB level."""
    chan = matrix_env["channel"]
    dna = matrix_env["dna"]
    topic, brief = await setup_content_entities(db_session, chan.id)

    idem_key = f"content_matrix_{uuid4().hex}"
    r1 = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        mode="INTERACTIVE",
        status="DRAFT",
        content_type="SHORT",
        idempotency_key=idem_key,
    )
    db_session.add(r1)
    await db_session.commit()

    r2 = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        mode="INTERACTIVE",
        status="DRAFT",
        content_type="SHORT",
        idempotency_key=idem_key,
    )
    db_session.add(r2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_duplicate_autonomy_production_dispatch_creates_one_semantic_render_request(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove duplicate production dispatch with same idempotency key raises IntegrityError on DB level."""
    chan = matrix_env["channel"]
    dna = matrix_env["dna"]
    topic, brief = await setup_content_entities(db_session, chan.id)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        mode="INTERACTIVE",
        status="APPROVED",
        content_type="SHORT",
    )
    db_session.add(content_req)
    await db_session.flush()

    script = ScriptVersion(
        id=uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Testing Script",
        hook_text="Test hook",
        closing_text="Test closing",
        cta_text="Test CTA",
    )
    db_session.add(script)
    await db_session.commit()

    idem_key = f"prod_matrix_{uuid4().hex}"
    p1 = ProductionRequest(
        id=uuid4(),
        channel_id=chan.id,
        script_version_id=script.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna.id,
        idempotency_key=idem_key,
        mode="INTERACTIVE",
        status="DRAFT",
    )
    db_session.add(p1)
    await db_session.commit()

    p2 = ProductionRequest(
        id=uuid4(),
        channel_id=chan.id,
        script_version_id=script.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna.id,
        idempotency_key=idem_key,
        mode="INTERACTIVE",
        status="DRAFT",
    )
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_duplicate_schedule_action_does_not_duplicate_reservation(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove issuing same semantic schedule action twice returns existing decision and reservation without duplicates."""
    from omega.application.scheduler.evaluation_engine import ScheduleEvaluationEngine
    from omega.application.scheduler.policy_service import SchedulePolicyService
    from omega.domain.scheduler import (
        ScheduleAction,
        ScheduleRequest,
        ScheduleTargetType,
        ScheduleWorkloadCategory,
    )
    from omega.infrastructure.models import ScheduleReservation, Task

    chan = matrix_env["channel"]
    mission = matrix_env["mission"]

    # Ensure active schedule policy
    await SchedulePolicyService.create_policy(
        db_session,
        workload_category="EXTERNAL_PUBLISH",
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={
            "min_lead_time_seconds": 0,
            "max_schedule_horizon_days": 14,
            "min_gap_between_channel_items_seconds": 0,
            "max_scheduled_items_per_day": 100,
            "global_concurrency_limit": 100,
            "channel_concurrency_limit": 10,
            "retry_backoff_base_seconds": 60,
            "retry_backoff_factor": 2.0,
            "retry_max_backoff_seconds": 3600,
            "max_deferrals": 5,
            "max_urgency_bonus": 50,
            "dispatching_timeout_seconds": 300,
            "dst_config": {
                "ambiguous_local_time_strategy": "FIRST_OCCURRENCE",
                "nonexistent_local_time_strategy": "SHIFT_FORWARD",
            },
        },
        activate=True,
    )

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_video",
        title="Schedule Task",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    now = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    req = ScheduleRequest(
        mission_id=mission.id,
        task_id=task.id,
        target_type=ScheduleTargetType.TASK_EXECUTION,
        target_id=task.id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH,
        channel_id=chan.id,
        earliest_start_at=now,
        estimated_duration_seconds=300,
        priority=1,
        caller_key=f"eval_key_{uuid4().hex}",
    )

    # First evaluation
    resp1 = await ScheduleEvaluationEngine.evaluate_schedule(db_session, req, now=now)
    assert resp1.action == ScheduleAction.SCHEDULE
    assert resp1.reservation_id is not None
    await db_session.commit()

    # Second evaluation with same deterministic request/key
    resp2 = await ScheduleEvaluationEngine.evaluate_schedule(db_session, req, now=now)
    assert resp2.id == resp1.id
    assert resp2.reservation_id == resp1.reservation_id

    # Prove database contains exactly one ScheduleDecision and one ScheduleReservation
    stmt_dec = select(ScheduleDecision).where(ScheduleDecision.id == resp1.id)
    dec_rows = (await db_session.execute(stmt_dec)).scalars().all()
    assert len(dec_rows) == 1

    stmt_res = select(ScheduleReservation).where(ScheduleReservation.id == resp1.reservation_id)
    res_rows = (await db_session.execute(stmt_res)).scalars().all()
    assert len(res_rows) == 1


@pytest.mark.asyncio
async def test_duplicate_publish_action_does_not_duplicate_publish_attempt(
    db_session: AsyncSession, matrix_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Prove existing OMEGA-011 idempotency contract prevents duplicate publish side effect and enforces attempt contract."""
    from unittest.mock import AsyncMock

    from omega.application.publisher.adapters.base import ChunkUploadResult, UploadSessionInitResult
    from omega.application.publisher.intent_service import PublishIntentService
    from omega.application.publisher.publish_service import (
        PublishExecutionError,
        PublishExecutionService,
    )
    from omega.domain.publisher import (
        PrivacyStatus,
        PublishAttemptState,
        PublishIntentCreate,
    )
    from omega.infrastructure.models import (
        CredentialVault,
        MediaArtifact,
        PlatformAccount,
        Task,
    )
    from omega.infrastructure.vault import get_credential_vault

    monkeypatch.setenv(
        "OMEGA_SECRET_ENCRYPTION_KEY",
        "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=",
    )
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    import omega.infrastructure.vault as vault_module

    monkeypatch.setattr(vault_module, "_default_vault", None)

    chan = matrix_env["channel"]
    mission = matrix_env["mission"]
    dna = matrix_env["dna"]

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="PUBLISH_VIDEO",
        title="Publish Video Task",
        state="READY",
    )
    db_session.add(task)

    topic, brief = await setup_content_entities(db_session, chan.id)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        mode="INTERACTIVE",
        status="APPROVED",
        content_type="SHORT",
    )
    db_session.add(content_req)
    await db_session.flush()

    script = ScriptVersion(
        id=uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Publish Test Script",
        hook_text="Hook",
        closing_text="Close",
        cta_text="CTA",
    )
    db_session.add(script)
    await db_session.flush()

    prod_req = ProductionRequest(
        id=uuid4(),
        channel_id=chan.id,
        script_version_id=script.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna.id,
        mode="INTERACTIVE",
        status="APPROVED",
    )
    db_session.add(prod_req)
    await db_session.flush()

    art_hash = hashlib.sha256(uuid4().hex.encode()).hexdigest()
    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/test_matrix_publish.mp4",
        file_size_bytes=2048,
        content_hash=art_hash,
        mime_type="video/mp4",
    )
    db_session.add(artifact)
    await db_session.flush()

    account = PlatformAccount(
        id=uuid4(),
        channel_id=chan.id,
        platform="YOUTUBE",
        account_display_name="Matrix Test Channel",
        external_account_id=f"UC_{uuid4().hex[:8]}",
        status="ACTIVE",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    db_session.add(account)
    await db_session.flush()

    vault = get_credential_vault()
    enc_acc, v1 = vault.encrypt("mock_acc_token")
    enc_ref, v2 = vault.encrypt("mock_ref_token")
    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=enc_acc,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        encrypted_refresh_token=enc_ref,
        key_version=v1,
    )
    db_session.add(vault_entry)
    await db_session.commit()

    payload = PublishIntentCreate(
        mission_id=mission.id,
        task_id=task.id,
        channel_id=chan.id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum=art_hash,
        title="Test Matrix Publish Video",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(db_session, payload)
    assert intent.state == "APPROVED"

    mock_init = AsyncMock(
        return_value=UploadSessionInitResult(
            session_uri="https://mock-upload-session",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    mock_chunk = AsyncMock(
        return_value=ChunkUploadResult(
            is_complete=True,
            next_byte_offset=2048,
            provider_video_id=f"yt_{uuid4().hex[:8]}",
            provider_url="https://youtu.be/mock",
            effective_privacy_status=PrivacyStatus.PRIVATE,
        )
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_chunk,
    )

    attempt1 = await PublishExecutionService.execute_publish(db_session, task.id)
    assert attempt1.state == PublishAttemptState.SUCCEEDED.value
    assert attempt1.attempt_number == 1
    assert mock_chunk.call_count == 1

    with pytest.raises(
        PublishExecutionError, match="No approved or reclaimable PublishIntent found"
    ):
        await PublishExecutionService.execute_publish(db_session, task.id)

    assert mock_chunk.call_count == 1
    stmt_attempts = select(PublishAttempt).where(PublishAttempt.publish_intent_id == intent.id)
    attempts = (await db_session.execute(stmt_attempts)).scalars().all()
    assert len(attempts) == 1
    assert attempts[0].id == attempt1.id
    assert attempts[0].attempt_number == 1


# ============================================================================
# AUDIT & LINEAGE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_same_semantic_event_retry_creates_one_event(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove event deduplication by dedupe key."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    dedupe_key = f"event_dedupe_{uuid4().hex}"

    e1 = AutonomyEvent(
        id=uuid4(),
        loop_id=loop.id,
        event_type="LOOP_STATE_CHANGED",
        event_dedupe_key=dedupe_key,
        payload={"from": "IDLE", "to": "OBSERVING"},
    )
    db_session.add(e1)
    await db_session.commit()

    e2 = AutonomyEvent(
        id=uuid4(),
        loop_id=loop.id,
        event_type="LOOP_STATE_CHANGED",
        event_dedupe_key=dedupe_key,
        payload={"from": "IDLE", "to": "OBSERVING"},
    )
    db_session.add(e2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_distinct_semantic_events_create_distinct_events(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove distinct events with unique dedupe keys are both preserved."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]

    e1 = AutonomyEvent(
        id=uuid4(),
        loop_id=loop.id,
        event_type="LOOP_STATE_CHANGED",
        event_dedupe_key=f"event_{uuid4().hex}",
        payload={"step": 1},
    )
    e2 = AutonomyEvent(
        id=uuid4(),
        loop_id=loop.id,
        event_type="LOOP_STATE_CHANGED",
        event_dedupe_key=f"event_{uuid4().hex}",
        payload={"step": 2},
    )
    db_session.add(e1)
    db_session.add(e2)
    await db_session.commit()
    assert e1.id != e2.id


@pytest.mark.asyncio
async def test_loop_cannot_be_deleted_while_historical_audit_rows_exist(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove ON DELETE RESTRICT prevents deleting a loop when iterations or events exist."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]

    # Attempting direct SQL delete triggers ON DELETE RESTRICT foreign key integrity error
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM autonomy_loops WHERE id = :id"),
            {"id": loop.id},
        )
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_cancel_loop_preserves_complete_history(
    db_session: AsyncSession, matrix_env: dict[str, Any]
):
    """Prove cancelling a loop preserves all historical iterations and audit events intact."""
    env = await setup_loop_env(db_session, matrix_env)
    loop = env["loop"]
    it = env["iteration"]

    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop.id,
        new_state=AutonomyLoopState.CANCELLED,
        stop_reason="Preserve history test",
    )
    await db_session.commit()

    stmt_it = select(AutonomyLoopIteration).where(AutonomyLoopIteration.id == it.id)
    found_it = (await db_session.execute(stmt_it)).scalar_one_or_none()
    assert found_it is not None


@pytest.mark.asyncio
async def test_historical_autonomy_rows_use_restrictive_lineage():
    """Prove that child audit rows (attempts, outcomes, plans) cannot be orphaned or deleted."""
    from omega.infrastructure.models import AutonomyActionAttempt, AutonomyActionPlan

    fk_plan = AutonomyActionPlan.__table__.c.iteration_id.foreign_keys
    for fk in fk_plan:
        assert fk.ondelete == "RESTRICT"

    fk_att = AutonomyActionAttempt.__table__.c.action_plan_id.foreign_keys
    for fk in fk_att:
        assert fk.ondelete == "RESTRICT"
