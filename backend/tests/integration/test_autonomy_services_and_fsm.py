"""Integration tests for OMEGA-014 Autonomous Loop services, FSM, and REST API."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.approval_service import AutonomyApprovalService
from omega.application.autonomy.budget_service import (
    AutonomyBudgetService,
    HardBudgetExceededError,
)
from omega.application.autonomy.dispatch_service import AutonomyDispatchService
from omega.application.autonomy.guardian_context import (
    GuardianConfigurationError,
    build_canonical_guardian_context,
)
from omega.application.autonomy.loop_service import (
    AutonomyLoopService,
    RateLimitCooldownError,
    RateLimitExceededError,
)
from omega.application.autonomy.observation_service import AutonomyObservationService
from omega.application.autonomy.plan_service import AutonomyPlanService
from omega.domain.autonomy import (
    ActionAttemptOutcomeStatus,
    ActionType,
    ApprovalActorType,
    ApprovalDecisionValue,
    AutonomyLevel,
    AutonomyLoopState,
    BudgetReservationStatus,
)
from omega.infrastructure.models import (
    AutonomyActionAttemptOutcome,
    AutonomyActionPlan,
    AutonomyBudgetLedger,
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
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    TopicCandidate,
)
from omega.main import app

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def autonomy_env(db_session: AsyncSession) -> dict[str, Any]:
    """Setup base entities for autonomy loop tests."""
    chan = Channel(
        id=uuid4(),
        slug=f"autonomy-chan-{uuid4().hex[:8]}",
        name="Autonomy Channel",
        platform="YOUTUBE",
    )
    db_session.add(chan)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=chan.id,
        version=1,
        snapshot={"name": "Autonomy Channel DNA"},
        change_reason="Initial snapshot",
    )
    db_session.add(dna)

    mission = Mission(
        id=uuid4(),
        channel_id=chan.id,
        title="Autonomous Mission",
        objective="Autonomously execute mission loop",
        state="ACTIVE",
        guardian_epoch=1,
    )
    db_session.add(mission)

    # Deactivate existing active rulesets to guarantee exact single-active invariant
    await db_session.execute(
        text("UPDATE guardian_rulesets SET status = 'INACTIVE' WHERE status = 'ACTIVE'")
    )

    # Guardian Ruleset (Single ACTIVE ruleset required)
    ruleset = GuardianRuleSet(
        id=uuid4(),
        version=f"v-{uuid4().hex[:6]}",
        status="ACTIVE",
        effective_at=datetime.now(UTC),
        rules_config={"rules": []},
        checksum=hashlib.sha256(b"autonomy-default-ruleset").hexdigest(),
    )
    db_session.add(ruleset)

    # Guardian Check and Decision
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

    # Initial Guardian Gate State Transition to OPEN
    trans = GuardianStateTransition(
        id=uuid4(),
        mission_id=mission.id,
        checkpoint="PRE_TASK_DISPATCH",
        from_gate_state="WAITING_GUARDIAN",
        to_gate_state="OPEN",
        decision_id=decision.id,
        guardian_epoch=1,
        reason="Initial setup pass",
    )
    db_session.add(trans)

    await db_session.commit()

    return {
        "channel": chan,
        "dna": dna,
        "mission": mission,
        "ruleset": ruleset,
        "check": check,
        "decision": decision,
    }


# ============================================================================
# 1. LOOP CREATION & ADVISORY LOCK IDEMPOTENCY
# ============================================================================


@pytest.mark.asyncio
async def test_loop_creation_and_idempotency(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify loop creation creates all required tables and returns existing on duplicate."""
    chan = autonomy_env["channel"]
    mission = autonomy_env["mission"]

    loop1 = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
        autonomy_level=AutonomyLevel.SUPERVISED,
        daily_cap_usd=Decimal("15.0000"),
        lifetime_cap_usd=Decimal("150.0000"),
    )
    await db_session.commit()

    assert loop1.id is not None
    assert loop1.autonomy_level == AutonomyLevel.SUPERVISED.value

    # Verify latest pointer was created
    stmt_ptr = select(AutonomyLoopLatestPointer).where(
        AutonomyLoopLatestPointer.loop_id == loop1.id
    )
    pointer = (await db_session.execute(stmt_ptr)).scalar_one()
    assert pointer.operational_state == AutonomyLoopState.IDLE.value
    assert pointer.current_iteration_sequence == 0

    # Verify policy snapshot v1
    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loop1.id)
    policy = (await db_session.execute(stmt_pol)).scalar_one()
    assert policy.policy_version == 1

    # Verify budget ledger
    stmt_led = select(AutonomyBudgetLedger).where(AutonomyBudgetLedger.loop_id == loop1.id)
    ledger = (await db_session.execute(stmt_led)).scalar_one()
    assert ledger.daily_cap_usd == Decimal("15.0000")
    assert ledger.lifetime_cap_usd == Decimal("150.0000")

    # Idempotent call with same mission + channel
    loop2 = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
    )
    assert loop1.id == loop2.id


# ============================================================================
# 2. RATE LIMITING & COOLDOWN ENFORCEMENT
# ============================================================================


@pytest.mark.asyncio
async def test_iteration_rate_limit_and_cooldown(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify iteration allocation enforces hourly limit and minimum interval cooldown in DB time."""
    chan = autonomy_env["channel"]
    mission = autonomy_env["mission"]

    loop = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
        max_iterations_per_hour=2,
        min_iteration_interval_seconds=10,
    )
    await db_session.commit()

    obs = await AutonomyObservationService.capture_snapshot(db_session, loop.id)
    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loop.id)
    policy = (await db_session.execute(stmt_pol)).scalar_one()

    loop_id = loop.id
    obs_id = obs.id
    policy_id = policy.id

    # Iteration 1
    it1 = await AutonomyLoopService.allocate_iteration(
        session=db_session,
        loop_id=loop_id,
        observation_snapshot_id=obs_id,
        policy_snapshot_id=policy_id,
    )
    await db_session.commit()
    assert it1.iteration_sequence == 1

    # Finish iteration 1 legally via FSM to trigger cooldown tracking
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop_id,
        new_state=AutonomyLoopState.PLANNING,
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop_id,
        new_state=AutonomyLoopState.EXECUTING,
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop_id,
        new_state=AutonomyLoopState.VERIFYING,
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop_id,
        new_state=AutonomyLoopState.IDLE,
    )
    await db_session.commit()

    # Immediate attempt 2 should fail cooldown
    with pytest.raises(RateLimitCooldownError):
        await AutonomyLoopService.allocate_iteration(
            session=db_session,
            loop_id=loop_id,
            observation_snapshot_id=obs_id,
            policy_snapshot_id=policy_id,
        )
    await db_session.rollback()

    # Simulate elapsed cooldown by setting last_iteration_finished_at in past
    await db_session.execute(
        text(
            "UPDATE autonomy_loop_latest_pointers SET last_iteration_finished_at = NOW() - INTERVAL '15 seconds' WHERE loop_id = :lid"
        ),
        {"lid": loop_id},
    )
    await db_session.commit()

    # Iteration 2 should succeed
    it2 = await AutonomyLoopService.allocate_iteration(
        session=db_session,
        loop_id=loop_id,
        observation_snapshot_id=obs_id,
        policy_snapshot_id=policy_id,
    )
    await db_session.commit()
    assert it2.iteration_sequence == 2

    # Finish iteration 2 legally via FSM
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop_id,
        new_state=AutonomyLoopState.PLANNING,
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop_id,
        new_state=AutonomyLoopState.EXECUTING,
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop_id,
        new_state=AutonomyLoopState.VERIFYING,
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop_id,
        new_state=AutonomyLoopState.IDLE,
    )
    # Bypass cooldown
    await db_session.execute(
        text(
            "UPDATE autonomy_loop_latest_pointers SET last_iteration_finished_at = NOW() - INTERVAL '15 seconds' WHERE loop_id = :lid"
        ),
        {"lid": loop_id},
    )
    await db_session.commit()

    # Attempt 3 exceeds hourly cap (max 2 per hour)
    with pytest.raises(RateLimitExceededError):
        await AutonomyLoopService.allocate_iteration(
            session=db_session,
            loop_id=loop_id,
            observation_snapshot_id=obs_id,
            policy_snapshot_id=policy_id,
        )
    await db_session.rollback()


# ============================================================================
# 3. BUDGET RESERVATION, LIABILITY ACCUMULATION & HARD CAP
# ============================================================================


@pytest.mark.asyncio
async def test_budget_reservation_and_hard_cap(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify reservations accumulate liability and hard-cap is strictly enforced."""
    chan = autonomy_env["channel"]
    mission = autonomy_env["mission"]

    loop = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
        daily_cap_usd=Decimal("5.0000"),
        lifetime_cap_usd=Decimal("10.0000"),
    )
    await db_session.commit()

    obs = await AutonomyObservationService.capture_snapshot(db_session, loop.id)
    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loop.id)
    policy = (await db_session.execute(stmt_pol)).scalar_one()

    it = await AutonomyLoopService.allocate_iteration(
        session=db_session,
        loop_id=loop.id,
        observation_snapshot_id=obs.id,
        policy_snapshot_id=policy.id,
    )
    await db_session.commit()

    plan1 = await AutonomyPlanService.create_plan(db_session, it.id)
    plan2 = AutonomyActionPlan(
        id=uuid4(),
        iteration_id=it.id,
        plan_sequence=2,
        action_type=ActionType.GENERATE_CONTENT.value,
        risk_class="REVERSIBLE_INTERNAL",
        target_subsystem="content",
        target_entity_id=None,
        parameters={},
        estimated_cost_usd=Decimal("3.0000"),
        precondition_fingerprint="fingerprint2",
        action_checksum="checksum2",
        requires_approval=False,
    )
    db_session.add(plan2)
    await db_session.commit()

    # Reserve $3.00 (within $5 daily cap)
    res1 = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan1.id,
        semantic_action_key="sem_key_1",
        estimated_amount=Decimal("3.0000"),
    )
    await db_session.commit()
    res1_id = res1.id
    loop_id = loop.id

    # Idempotent re-reserve of exact key produces same reservation
    res1_again = await AutonomyBudgetService.reserve_budget(
        session=db_session,
        loop_id=loop_id,
        action_plan_id=plan1.id,
        semantic_action_key="sem_key_1",
        estimated_amount=Decimal("3.0000"),
    )
    assert res1_again.id == res1_id

    # Attempt to reserve $3.00 more (3 + 3 = 6 > 5 daily cap) -> must fail!
    with pytest.raises(HardBudgetExceededError):
        await AutonomyBudgetService.reserve_budget(
            session=db_session,
            loop_id=loop_id,
            action_plan_id=plan2.id,
            semantic_action_key="sem_key_2",
            estimated_amount=Decimal("3.0000"),
        )
    await db_session.rollback()

    # Capture first reservation ($3.00)
    res1_captured = await AutonomyBudgetService.capture_reservation(
        session=db_session,
        reservation_id=res1_id,
        captured_amount=Decimal("2.5000"),
    )
    await db_session.commit()
    assert res1_captured.status == BudgetReservationStatus.CAPTURED.value
    assert res1_captured.captured_amount == Decimal("2.5000")

    # Verify ledger spend updated
    stmt_led = select(AutonomyBudgetLedger).where(AutonomyBudgetLedger.loop_id == loop_id)
    ledger = (await db_session.execute(stmt_led)).scalar_one()
    assert ledger.daily_spend_usd == Decimal("2.5000")
    assert ledger.lifetime_spend_usd == Decimal("2.5000")


# ============================================================================
# 4. APPROVAL SERIALIZATION & CONTEXT BINDING
# ============================================================================


@pytest.mark.asyncio
async def test_approval_serialization_and_invalidation(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify approval requests freeze context and invalidate on drift."""
    chan = autonomy_env["channel"]
    mission = autonomy_env["mission"]
    dna = autonomy_env["dna"]

    loop = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
    )
    await db_session.commit()

    obs = await AutonomyObservationService.capture_snapshot(db_session, loop.id)
    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loop.id)
    policy = (await db_session.execute(stmt_pol)).scalar_one()

    it = await AutonomyLoopService.allocate_iteration(
        session=db_session,
        loop_id=loop.id,
        observation_snapshot_id=obs.id,
        policy_snapshot_id=policy.id,
    )
    await db_session.commit()

    plan = await AutonomyPlanService.create_plan(db_session, it.id)
    await db_session.commit()

    # Create approval request with frozen context
    req = await AutonomyApprovalService.create_request(
        session=db_session,
        loop_id=loop.id,
        action_plan_id=plan.id,
        semantic_action_key="action_sem_key_1",
        action_checksum="action_chk_123",
        precondition_fingerprint="fingerprint_v1",
        guardian_context_checksum="guardian_chk_v1",
        guardian_epoch=1,
        dna_revision_id=dna.id,
        policy_snapshot_id=policy.id,
        mission_state="ACTIVE",
    )
    await db_session.commit()
    assert req.id is not None

    # Decision when context drifted -> must result in INVALIDATED
    decision = await AutonomyApprovalService.decide_request(
        session=db_session,
        approval_request_id=req.id,
        decision=ApprovalDecisionValue.APPROVED,
        actor_type=ApprovalActorType.HUMAN,
        reviewer_user_id="operator-1",
        review_reason="Approved",
        current_guardian_context_checksum="guardian_chk_v2_drifted",
    )
    await db_session.commit()
    assert decision.decision == ApprovalDecisionValue.INVALIDATED.value
    assert "Invalidated" in decision.review_reason


# ============================================================================
# 5. SAFE 4-PHASE EXECUTION PROTOCOL
# ============================================================================


@pytest.mark.asyncio
async def test_safe_dispatch_protocol(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify preparation TX, intent commit, and final outcome recording."""
    chan = autonomy_env["channel"]
    mission = autonomy_env["mission"]

    loop = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
        autonomy_level=AutonomyLevel.BOUNDED_AUTONOMOUS,
    )
    await db_session.commit()

    obs = await AutonomyObservationService.capture_snapshot(db_session, loop.id)
    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loop.id)
    policy = (await db_session.execute(stmt_pol)).scalar_one()

    iteration = await AutonomyLoopService.allocate_iteration(
        session=db_session,
        loop_id=loop.id,
        observation_snapshot_id=obs.id,
        policy_snapshot_id=policy.id,
    )
    await db_session.commit()

    plan = await AutonomyPlanService.create_plan(db_session, iteration.id)
    await db_session.commit()

    # Transition loop to PLANNING then EXECUTING for legal dispatch state
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop.id,
        new_state=AutonomyLoopState.PLANNING,
    )
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop.id,
        new_state=AutonomyLoopState.EXECUTING,
    )
    await db_session.commit()

    attempt, outcome = await AutonomyDispatchService.prepare_and_dispatch(
        session=db_session,
        action_plan_id=plan.id,
        worker_id="test-worker-1",
    )

    assert outcome.status == ActionAttemptOutcomeStatus.RESULT_CONFIRMED.value

    # Verify all 3 outcome stages are durably stored in sequence
    stmt_outcomes = (
        select(AutonomyActionAttemptOutcome)
        .where(AutonomyActionAttemptOutcome.action_attempt_id == attempt.id)
        .order_by(AutonomyActionAttemptOutcome.outcome_sequence.asc())
    )
    outcomes = (await db_session.execute(stmt_outcomes)).scalars().all()
    assert len(outcomes) == 3
    assert outcomes[0].status == ActionAttemptOutcomeStatus.PREPARED.value
    assert outcomes[1].status == ActionAttemptOutcomeStatus.DISPATCH_INTENT_COMMITTED.value
    assert outcomes[2].status == ActionAttemptOutcomeStatus.RESULT_CONFIRMED.value


# ============================================================================
# 6. GUARDIAN FAIL-CLOSED VALIDATION
# ============================================================================


@pytest.mark.asyncio
async def test_guardian_fail_closed_on_invalid_rulesets(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify Guardian context raises GuardianConfigurationError when 0 or >1 active rulesets exist."""
    mission = autonomy_env["mission"]

    # Active ruleset count is currently 1 -> success
    ctx, chk = await build_canonical_guardian_context(db_session, mission.id)
    assert ctx.overall_gate_state == "OPEN"
    assert len(chk) == 64

    # Add second ACTIVE ruleset -> must fail closed
    ruleset2 = GuardianRuleSet(
        id=uuid4(),
        version=f"v2-{uuid4().hex[:6]}",
        status="ACTIVE",
        effective_at=datetime.now(UTC),
        rules_config={"rules": []},
        checksum=hashlib.sha256(b"ruleset-2").hexdigest(),
    )
    db_session.add(ruleset2)
    await db_session.commit()

    with pytest.raises(GuardianConfigurationError):
        await build_canonical_guardian_context(db_session, mission.id)

    # Cleanup: restore single-active invariant for other tests
    ruleset2.status = "INACTIVE"
    await db_session.commit()


# ============================================================================
# 7. CONTENT & PRODUCTION IDEMPOTENCY KEY DEDUPLICATION
# ============================================================================


@pytest.mark.asyncio
async def test_content_and_production_idempotency_keys(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify Content and Production requests enforce unique idempotency_key."""
    chan = autonomy_env["channel"]
    dna = autonomy_env["dna"]

    # Setup parent entities for foreign keys
    topic = TopicCandidate(
        id=uuid4(),
        channel_id=chan.id,
        title="Testing Topic",
        normalized_title="testing topic",
        source_name="MANUAL_SOURCE",
        topic_fingerprint=hashlib.sha256(b"testing topic").hexdigest(),
        status="DISCOVERED",
    )
    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        mode="INTERACTIVE",
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        version=1,
        title="Testing Brief",
        summary="Brief summary",
        is_current=True,
    )
    db_session.add(brief)
    await db_session.commit()

    # Verify ContentGenerationRequest with unique idempotency_key
    idem_content = f"content-idem-{uuid4().hex[:12]}"
    req1 = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        mode="INTERACTIVE",
        status="DRAFT",
        content_type="YOUTUBE_LONGFORM",
        idempotency_key=idem_content,
    )
    db_session.add(req1)
    await db_session.commit()

    # Query by idempotency_key returns req1
    stmt = select(ContentGenerationRequest).where(
        ContentGenerationRequest.idempotency_key == idem_content
    )
    found = (await db_session.execute(stmt)).scalar_one()
    assert found.id == req1.id

    req1_id = req1.id
    chan_id = chan.id
    dna_id = dna.id

    # Duplicate idempotency_key raises IntegrityError
    req_dup = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan_id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna_id,
        mode="INTERACTIVE",
        status="DRAFT",
        content_type="YOUTUBE_LONGFORM",
        idempotency_key=idem_content,
    )
    db_session.add(req_dup)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Setup ScriptVersion parent for ProductionRequest
    script_id = uuid4()
    script = ScriptVersion(
        id=script_id,
        content_request_id=req1_id,
        version=1,
        title="Testing Script",
        hook_text="Test hook",
        closing_text="Test closing",
        cta_text="Test CTA",
    )
    db_session.add(script)
    await db_session.commit()

    # Verify ProductionRequest with unique idempotency_key
    idem_prod = f"prod-idem-{uuid4().hex[:12]}"
    prod1_id = uuid4()
    prod1 = ProductionRequest(
        id=prod1_id,
        channel_id=chan_id,
        script_version_id=script_id,
        content_request_id=req1_id,
        channel_dna_revision_id=dna_id,
        mode="INTERACTIVE",
        status="DRAFT",
        idempotency_key=idem_prod,
    )
    db_session.add(prod1)
    await db_session.commit()

    stmt_p = select(ProductionRequest).where(ProductionRequest.idempotency_key == idem_prod)
    found_p = (await db_session.execute(stmt_p)).scalar_one()
    assert found_p.id == prod1_id

    prod_dup = ProductionRequest(
        id=uuid4(),
        channel_id=chan_id,
        script_version_id=script_id,
        content_request_id=req1_id,
        channel_dna_revision_id=dna_id,
        mode="INTERACTIVE",
        status="DRAFT",
        idempotency_key=idem_prod,
    )
    db_session.add(prod_dup)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# ============================================================================
# 8. REST API INTEGRATION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_autonomy_rest_api_lifecycle(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify REST endpoints for loop creation, inspection, pause, resume, and cancel."""
    chan = autonomy_env["channel"]
    mission = autonomy_env["mission"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create loop
        create_res = await client.post(
            "/api/v1/autonomy/loops",
            json={
                "mission_id": str(mission.id),
                "channel_id": str(chan.id),
                "autonomy_level": "SUPERVISED",
                "daily_cap_usd": "20.0000",
                "lifetime_cap_usd": "200.0000",
            },
        )
        assert create_res.status_code == 201
        loop_id = create_res.json()["id"]

        # Get loop status
        get_res = await client.get(f"/api/v1/autonomy/loops/{loop_id}")
        assert get_res.status_code == 200
        assert get_res.json()["operational_state"] == "IDLE"

        # Pause loop
        pause_res = await client.post(f"/api/v1/autonomy/loops/{loop_id}/pause")
        assert pause_res.status_code == 200
        assert pause_res.json()["status"] == "PAUSED"

        # Resume loop
        resume_res = await client.post(f"/api/v1/autonomy/loops/{loop_id}/resume")
        assert resume_res.status_code == 200
        assert resume_res.json()["status"] == "RESUMED"

        # Cancel loop
        cancel_res = await client.post(f"/api/v1/autonomy/loops/{loop_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "CANCELLED"
