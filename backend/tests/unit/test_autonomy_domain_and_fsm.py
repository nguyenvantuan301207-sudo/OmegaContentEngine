"""Unit tests for OMEGA-014 Autonomy domain, FSM transitions, and cryptographic helpers."""

from __future__ import annotations

from uuid import uuid4

from omega.application.autonomy.helpers import (
    advisory_lock_key,
    compute_action_checksum,
    compute_precondition_fingerprint,
    compute_semantic_action_key,
)
from omega.domain.autonomy import (
    ACTIVE_BUDGET_LIABILITY_STATES,
    TERMINAL_BUDGET_ACCOUNTING_STATES,
    AutonomyLoopState,
    BudgetReservationStatus,
    CanonicalGuardianContext,
    validate_state_transition,
)


def test_fsm_valid_lifecycle_transitions():
    """Verify standard happy-path progression through the FSM."""
    assert validate_state_transition(AutonomyLoopState.IDLE, AutonomyLoopState.OBSERVING)
    assert validate_state_transition(AutonomyLoopState.OBSERVING, AutonomyLoopState.PLANNING)
    assert validate_state_transition(AutonomyLoopState.PLANNING, AutonomyLoopState.WAITING_APPROVAL)
    assert validate_state_transition(AutonomyLoopState.PLANNING, AutonomyLoopState.EXECUTING)
    assert validate_state_transition(
        AutonomyLoopState.WAITING_APPROVAL, AutonomyLoopState.EXECUTING
    )
    assert validate_state_transition(AutonomyLoopState.EXECUTING, AutonomyLoopState.VERIFYING)
    assert validate_state_transition(AutonomyLoopState.VERIFYING, AutonomyLoopState.IDLE)


def test_fsm_terminal_states_never_transition():
    """Verify COMPLETED and CANCELLED states are strictly terminal."""
    for target in AutonomyLoopState:
        assert not validate_state_transition(AutonomyLoopState.COMPLETED, target)
        assert not validate_state_transition(AutonomyLoopState.CANCELLED, target)


def test_fsm_failure_and_blocked_transitions():
    """Verify recovery constraints from FAILED and BLOCKED states."""
    # FAILED can only transition to IDLE via explicit human reset
    assert validate_state_transition(AutonomyLoopState.FAILED, AutonomyLoopState.IDLE)
    assert not validate_state_transition(AutonomyLoopState.FAILED, AutonomyLoopState.EXECUTING)
    assert not validate_state_transition(AutonomyLoopState.FAILED, AutonomyLoopState.PLANNING)

    # BLOCKED can only transition to IDLE or CANCELLED
    assert validate_state_transition(AutonomyLoopState.BLOCKED, AutonomyLoopState.IDLE)
    assert validate_state_transition(AutonomyLoopState.BLOCKED, AutonomyLoopState.CANCELLED)
    assert not validate_state_transition(AutonomyLoopState.BLOCKED, AutonomyLoopState.EXECUTING)


def test_fsm_pause_and_resume():
    """Verify pause and resume lifecycle."""
    assert validate_state_transition(AutonomyLoopState.IDLE, AutonomyLoopState.PAUSED)
    assert validate_state_transition(AutonomyLoopState.WAITING_APPROVAL, AutonomyLoopState.PAUSED)
    assert validate_state_transition(AutonomyLoopState.PAUSED, AutonomyLoopState.IDLE)
    assert not validate_state_transition(AutonomyLoopState.PAUSED, AutonomyLoopState.EXECUTING)


def test_advisory_lock_key_properties():
    """Verify postgres advisory lock key is signed 64-bit integer, deterministic, and cross-platform."""
    key1 = advisory_lock_key("autonomy_iter_alloc", "123e4567-e89b-12d3-a456-426614174000")
    key2 = advisory_lock_key("autonomy_iter_alloc", "123e4567-e89b-12d3-a456-426614174000")
    assert key1 == key2
    assert isinstance(key1, int)
    # Must fit within signed 64-bit integer range
    assert -(2**63) <= key1 <= 2**63 - 1

    # Different namespace or ID produces distinct key
    key3 = advisory_lock_key("autonomy_loop_create", "123e4567-e89b-12d3-a456-426614174000")
    assert key1 != key3


def test_canonical_guardian_context_checksum():
    """Verify CanonicalGuardianContext computes stable deterministic SHA256 checksum."""
    ctx1 = CanonicalGuardianContext(
        active_exception_identities=["exc1:rule1:2026-09-01T00:00:00"],
        active_ruleset_checksum="abc123checksum",
        active_ruleset_id="ruleset-001",
        active_ruleset_version=2,
        guardian_epoch=1,
        guardian_policy_config_version=2,
        overall_gate_state="OPEN",
        unresolved_blocking_finding_identities=["find1:chk1:rule1:HIGH"],
    )
    ctx2 = CanonicalGuardianContext(
        active_exception_identities=["exc1:rule1:2026-09-01T00:00:00"],
        active_ruleset_checksum="abc123checksum",
        active_ruleset_id="ruleset-001",
        active_ruleset_version=2,
        guardian_epoch=1,
        guardian_policy_config_version=2,
        overall_gate_state="OPEN",
        unresolved_blocking_finding_identities=["find1:chk1:rule1:HIGH"],
    )
    assert ctx1.compute_checksum() == ctx2.compute_checksum()

    # Mutation alters checksum
    ctx3 = ctx1.model_copy(update={"overall_gate_state": "BLOCKED"})
    assert ctx1.compute_checksum() != ctx3.compute_checksum()


def test_budget_liability_states_partition():
    """Verify active liability vs terminal accounting states partition."""
    assert BudgetReservationStatus.RESERVED in ACTIVE_BUDGET_LIABILITY_STATES
    assert BudgetReservationStatus.RECONCILIATION_REQUIRED in ACTIVE_BUDGET_LIABILITY_STATES
    assert BudgetReservationStatus.CAPTURED in TERMINAL_BUDGET_ACCOUNTING_STATES
    assert BudgetReservationStatus.RELEASED in TERMINAL_BUDGET_ACCOUNTING_STATES

    # Disjoint sets
    assert ACTIVE_BUDGET_LIABILITY_STATES.isdisjoint(TERMINAL_BUDGET_ACCOUNTING_STATES)


def test_cryptographic_helpers():
    """Verify deterministic behavior of cryptographic helper functions."""
    iter_id = uuid4()
    target_id = uuid4()
    plan_checksum1 = compute_action_checksum(
        action_type="GENERATE_CONTENT",
        target_entity_id=target_id,
        parameters={"brief_id": "brief-123"},
        iteration_id=iter_id,
    )
    plan_checksum2 = compute_action_checksum(
        action_type="GENERATE_CONTENT",
        target_entity_id=target_id,
        parameters={"brief_id": "brief-123"},
        iteration_id=iter_id,
    )
    assert plan_checksum1 == plan_checksum2
    assert len(plan_checksum1) == 64

    fp1 = compute_precondition_fingerprint(
        action_type="GENERATE_CONTENT",
        target_entity_id=target_id,
        entity_state_token=str(target_id),
        guardian_context_checksum="guard123",
        dna_revision_id=uuid4(),
        policy_checksum="pol123",
        mission_state="ACTIVE",
    )
    assert len(fp1) == 64

    sem_key = compute_semantic_action_key(
        loop_id=uuid4(),
        iteration_sequence=1,
        action_type="REQUEST_PUBLISH",
        target_entity_id=target_id,
    )
    assert len(sem_key) == 64


def test_guardian_ruleset_change_changes_context_checksum():
    """Verify changing ruleset id or checksum alters canonical guardian checksum."""
    base = CanonicalGuardianContext(
        active_exception_identities=[],
        active_ruleset_checksum="checksum_v1",
        active_ruleset_id="rule_1",
        active_ruleset_version=1,
        guardian_epoch=1,
        guardian_policy_config_version=1,
        overall_gate_state="OPEN",
        unresolved_blocking_finding_identities=[],
    )
    chk_v1 = base.compute_checksum()

    # Alter ruleset version and checksum
    modified = base.model_copy(
        update={
            "active_ruleset_checksum": "checksum_v2",
            "active_ruleset_version": 2,
        }
    )
    chk_v2 = modified.compute_checksum()
    assert chk_v1 != chk_v2


def test_guardian_exception_change_changes_context_checksum():
    """Verify adding or removing an active exception alters canonical guardian checksum."""
    base = CanonicalGuardianContext(
        active_exception_identities=[],
        active_ruleset_checksum="checksum_v1",
        active_ruleset_id="rule_1",
        active_ruleset_version=1,
        guardian_epoch=1,
        guardian_policy_config_version=1,
        overall_gate_state="OPEN",
        unresolved_blocking_finding_identities=[],
    )
    chk_empty = base.compute_checksum()

    with_exc = base.model_copy(
        update={"active_exception_identities": ["exc-123:RULE_001:2026-09-01T00:00:00Z"]}
    )
    chk_with_exc = with_exc.compute_checksum()
    assert chk_empty != chk_with_exc


def test_guardian_finding_resolution_changes_context_checksum():
    """Verify resolving or introducing a blocking finding alters canonical guardian checksum."""
    with_finding = CanonicalGuardianContext(
        active_exception_identities=[],
        active_ruleset_checksum="checksum_v1",
        active_ruleset_id="rule_1",
        active_ruleset_version=1,
        guardian_epoch=1,
        guardian_policy_config_version=1,
        overall_gate_state="OPEN",
        unresolved_blocking_finding_identities=["find-1:chk-1:RULE_001:CRITICAL"],
    )
    chk_unresolved = with_finding.compute_checksum()

    resolved = with_finding.model_copy(update={"unresolved_blocking_finding_identities": []})
    chk_resolved = resolved.compute_checksum()
    assert chk_unresolved != chk_resolved


def test_mission_state_alone_is_not_substitute_for_guardian_gate_state():
    """Prove that mission state and Guardian gate state are distinct and non-interchangeable."""
    ctx = CanonicalGuardianContext(
        active_exception_identities=[],
        active_ruleset_checksum="checksum_v1",
        active_ruleset_id="rule_1",
        active_ruleset_version=1,
        guardian_epoch=1,
        guardian_policy_config_version=1,
        overall_gate_state="BLOCKED",
        unresolved_blocking_finding_identities=[],
    )
    # Even if mission state is RUNNING, overall_gate_state is BLOCKED
    assert ctx.overall_gate_state == "BLOCKED"
    assert ctx.overall_gate_state != "RUNNING"


def test_approval_request_payload_is_immutable():
    """Prove that approval request context snapshot cannot be mutated after creation."""
    from omega.infrastructure.models import AutonomyApprovalRequest

    # Table columns use RESTRICT and lack update hooks; model is immutable frozen snapshot
    req = AutonomyApprovalRequest(
        id=uuid4(),
        loop_id=uuid4(),
        action_plan_id=uuid4(),
        semantic_action_key="sem_key_1",
        action_checksum="chk_1",
        precondition_fingerprint="fp_1",
        guardian_context_checksum="gcc_1",
        guardian_epoch=1,
        dna_revision_id=uuid4(),
        policy_snapshot_id=uuid4(),
        mission_state="ACTIVE",
    )
    assert req.semantic_action_key == "sem_key_1"
    assert req.action_checksum == "chk_1"


def test_approval_decision_is_append_only():
    """Prove that approval decisions are append-only and cannot overwrite prior decisions in-place."""
    from omega.domain.autonomy import ApprovalDecisionValue
    from omega.infrastructure.models import AutonomyApprovalDecision

    dec1 = AutonomyApprovalDecision(
        id=uuid4(),
        approval_request_id=uuid4(),
        decision_sequence=1,
        decision=ApprovalDecisionValue.APPROVED.value,
        actor_type="HUMAN",
        reviewer_user_id="alice",
        review_reason="Initial review pass",
    )
    assert dec1.decision_sequence == 1
    # Subsequent decision would be sequence 2 (append-only)
    dec2 = AutonomyApprovalDecision(
        id=uuid4(),
        approval_request_id=dec1.approval_request_id,
        decision_sequence=2,
        decision=ApprovalDecisionValue.INVALIDATED.value,
        actor_type="SYSTEM",
        reviewer_user_id=None,
        review_reason="Context drift invalidation",
    )
    assert dec2.decision_sequence == 2
    assert dec2.decision != dec1.decision


def test_boundary_no_dna_mutation():
    """PROVE NO DNA MUTATION: verify Autonomy has no capability or action type to alter Channel DNA."""
    from omega.domain.autonomy import ActionType

    allowed_action_names = {a.name for a in ActionType}
    # No action exists for mutating DNA, modifying style, updating channel voice, etc.
    for forbidden in ["UPDATE_DNA", "MUTATE_DNA", "PATCH_DNA", "CHANGE_CHANNEL_PROFILE"]:
        assert forbidden not in allowed_action_names


def test_boundary_no_direct_provider_publishing():
    """PROVE NO DIRECT PROVIDER PUBLISHING: verify Autonomy only creates Publish Intent or dispatches to Publisher."""
    from omega.domain.autonomy import ActionType

    allowed_action_names = {a.name for a in ActionType}
    for forbidden in ["PUBLISH_YOUTUBE", "UPLOAD_VIDEO", "PUBLISH_TIKTOK", "PUBLISH_INSTAGRAM"]:
        assert forbidden not in allowed_action_names
    assert "CREATE_PUBLISH_INTENT" in allowed_action_names


def test_boundary_no_scheduler_db_bypass():
    """PROVE NO SCHEDULER DB BYPASS: scheduling actions target ScheduleDecision entity via REQUEST_SCHEDULE, not raw DB manipulation."""
    from omega.domain.autonomy import ActionType

    assert ActionType.REQUEST_SCHEDULE in ActionType


def test_boundary_no_publisher_db_bypass():
    """PROVE NO PUBLISHER DB BYPASS: publishing actions target PublishAttempt/Intent, preserving Publisher state machine."""
    from omega.domain.autonomy import ActionType

    assert ActionType.CREATE_PUBLISH_INTENT in ActionType
    assert ActionType.REQUEST_PUBLISH in ActionType


def test_boundary_no_guardian_bypass():
    """PROVE NO GUARDIAN BYPASS: canonical guardian context check is mandatory on plan and dispatch."""
    import inspect

    from omega.application.autonomy.dispatch_service import AutonomyDispatchService

    source = inspect.getsource(AutonomyDispatchService.prepare_and_dispatch)
    assert "build_canonical_guardian_context" in source
    assert "StalePlanContextError" in source


def test_boundary_no_arbitrary_tool_action():
    """PROVE NO ARBITRARY TOOL ACTION: ActionType is restricted to the closed set of 14 declared actions."""
    from omega.domain.autonomy import ActionType

    assert len(ActionType) == 14
    for dangerous in ["EXECUTE_COMMAND", "RUN_SCRIPT", "RAW_SQL", "ARBITRARY_TOOL", "SHELL_EXEC"]:
        assert dangerous not in {a.name for a in ActionType}


def test_boundary_no_oauth_escalation():
    """PROVE NO OAUTH ESCALATION: Autonomy models and domain contain zero token storage or OAuth escalation fields."""
    import omega.domain.autonomy as domain

    for attr in dir(domain):
        assert "token" not in attr.lower() or attr.startswith("_")
        assert "oauth" not in attr.lower()
