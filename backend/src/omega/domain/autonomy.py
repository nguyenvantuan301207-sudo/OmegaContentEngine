"""Domain models, enums, and validation contracts for OMEGA-014 Autonomous Loop."""

from __future__ import annotations

import enum
import hashlib
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AutonomyLevel(enum.StrEnum):
    """Autonomy operating modes for an autonomous loop."""

    MANUAL = "MANUAL"
    SUPERVISED = "SUPERVISED"
    BOUNDED_AUTONOMOUS = "BOUNDED_AUTONOMOUS"


class AutonomyLoopState(enum.StrEnum):
    """Operational states of the deterministic control loop FSM."""

    IDLE = "IDLE"
    OBSERVING = "OBSERVING"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    WAITING_SIGNAL = "WAITING_SIGNAL"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES: frozenset[AutonomyLoopState] = frozenset(
    {
        AutonomyLoopState.COMPLETED,
        AutonomyLoopState.CANCELLED,
    }
)

RECOVERABLE_STOP_STATES: frozenset[AutonomyLoopState] = frozenset(
    {
        AutonomyLoopState.PAUSED,
        AutonomyLoopState.BLOCKED,
    }
)

RECOVERABLE_FAILURE_STATES: frozenset[AutonomyLoopState] = frozenset(
    {
        AutonomyLoopState.FAILED,
    }
)

# Legal state machine transitions
LEGAL_TRANSITIONS: dict[AutonomyLoopState, frozenset[AutonomyLoopState]] = {
    AutonomyLoopState.IDLE: frozenset(
        {
            AutonomyLoopState.OBSERVING,
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.OBSERVING: frozenset(
        {
            AutonomyLoopState.PLANNING,
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.BLOCKED,
            AutonomyLoopState.FAILED,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.PLANNING: frozenset(
        {
            AutonomyLoopState.WAITING_APPROVAL,
            AutonomyLoopState.EXECUTING,
            AutonomyLoopState.WAITING_SIGNAL,
            AutonomyLoopState.COMPLETED,
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.BLOCKED,
            AutonomyLoopState.FAILED,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.WAITING_APPROVAL: frozenset(
        {
            AutonomyLoopState.EXECUTING,
            AutonomyLoopState.PLANNING,
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.BLOCKED,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.EXECUTING: frozenset(
        {
            AutonomyLoopState.VERIFYING,
            AutonomyLoopState.WAITING_SIGNAL,
            AutonomyLoopState.FAILED,
            AutonomyLoopState.BLOCKED,
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.VERIFYING: frozenset(
        {
            AutonomyLoopState.WAITING_SIGNAL,
            AutonomyLoopState.OBSERVING,
            AutonomyLoopState.IDLE,
            AutonomyLoopState.EXECUTING,
            AutonomyLoopState.FAILED,
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.BLOCKED,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.WAITING_SIGNAL: frozenset(
        {
            AutonomyLoopState.OBSERVING,
            AutonomyLoopState.IDLE,
            AutonomyLoopState.PAUSED,
            AutonomyLoopState.BLOCKED,
            AutonomyLoopState.FAILED,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.PAUSED: frozenset(
        {
            AutonomyLoopState.IDLE,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.BLOCKED: frozenset(
        {
            AutonomyLoopState.IDLE,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.FAILED: frozenset(
        {
            AutonomyLoopState.IDLE,
            AutonomyLoopState.CANCELLED,
        }
    ),
    AutonomyLoopState.COMPLETED: frozenset(),
    AutonomyLoopState.CANCELLED: frozenset(),
}


def validate_state_transition(from_state: AutonomyLoopState, to_state: AutonomyLoopState) -> bool:
    """Validate whether an FSM state transition is legal."""
    if from_state in TERMINAL_STATES:
        return False
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


class ActionType(enum.StrEnum):
    """Typed catalog of actions executable by OMEGA-014."""

    EVALUATE_TOPICS = "EVALUATE_TOPICS"
    REQUEST_RESEARCH = "REQUEST_RESEARCH"
    GENERATE_CONTENT = "GENERATE_CONTENT"
    REQUEST_PRODUCTION = "REQUEST_PRODUCTION"
    REQUEST_QA = "REQUEST_QA"
    CREATE_PUBLISH_INTENT = "CREATE_PUBLISH_INTENT"
    REQUEST_SCHEDULE = "REQUEST_SCHEDULE"
    REQUEST_PUBLISH = "REQUEST_PUBLISH"
    REFRESH_ANALYTICS = "REFRESH_ANALYTICS"
    SWEEP_LEARNING = "SWEEP_LEARNING"
    WAIT = "WAIT"
    REQUEST_HUMAN_APPROVAL = "REQUEST_HUMAN_APPROVAL"
    PAUSE_LOOP = "PAUSE_LOOP"
    COMPLETE_MISSION = "COMPLETE_MISSION"


class ActionRiskClass(enum.StrEnum):
    """Deterministic risk categorization for planned actions."""

    READ_ONLY = "READ_ONLY"
    REVERSIBLE_INTERNAL = "REVERSIBLE_INTERNAL"
    EXTERNAL_SIDE_EFFECT = "EXTERNAL_SIDE_EFFECT"
    IRREVERSIBLE_OR_HIGH_IMPACT = "IRREVERSIBLE_OR_HIGH_IMPACT"


class ActionAttemptOutcomeStatus(enum.StrEnum):
    """Durable attempt lifecycle evidence statuses."""

    PREPARED = "PREPARED"
    DISPATCH_INTENT_COMMITTED = "DISPATCH_INTENT_COMMITTED"
    RESULT_CONFIRMED = "RESULT_CONFIRMED"
    DETERMINISTIC_FAILURE = "DETERMINISTIC_FAILURE"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILED_CONFIRMED = "RECONCILED_CONFIRMED"
    RECONCILED_ABSENT = "RECONCILED_ABSENT"


class BudgetReservationStatus(enum.StrEnum):
    """Lifecycle states of durable budget reservations."""

    RESERVED = "RESERVED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"


ACTIVE_BUDGET_LIABILITY_STATES: frozenset[BudgetReservationStatus] = frozenset(
    {
        BudgetReservationStatus.RESERVED,
        BudgetReservationStatus.RECONCILIATION_REQUIRED,
    }
)

TERMINAL_BUDGET_ACCOUNTING_STATES: frozenset[BudgetReservationStatus] = frozenset(
    {
        BudgetReservationStatus.CAPTURED,
        BudgetReservationStatus.RELEASED,
    }
)


class ApprovalDecisionValue(enum.StrEnum):
    """Decision values for human or system approval outcomes."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class ApprovalActorType(enum.StrEnum):
    """Actor types capable of recording approval decisions."""

    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class AutonomyEventType(enum.StrEnum):
    """Append-only audit event types for autonomy execution."""

    LOOP_CREATED = "LOOP_CREATED"
    OBSERVATION_CAPTURED = "OBSERVATION_CAPTURED"
    PLAN_PROPOSED = "PLAN_PROPOSED"
    PLAN_VALIDATED = "PLAN_VALIDATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_DECIDED = "APPROVAL_DECIDED"
    ACTION_PREPARED = "ACTION_PREPARED"
    DISPATCH_INTENT_COMMITTED = "DISPATCH_INTENT_COMMITTED"
    RESULT_CONFIRMED = "RESULT_CONFIRMED"
    DETERMINISTIC_FAILURE = "DETERMINISTIC_FAILURE"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILED_CONFIRMED = "RECONCILED_CONFIRMED"
    RECONCILED_ABSENT = "RECONCILED_ABSENT"
    ITERATION_COMPLETED = "ITERATION_COMPLETED"
    LOOP_PAUSED = "LOOP_PAUSED"
    LOOP_BLOCKED = "LOOP_BLOCKED"
    LOOP_RESUMED = "LOOP_RESUMED"
    LOOP_COMPLETED = "LOOP_COMPLETED"
    LOOP_FAILED = "LOOP_FAILED"
    LOOP_CANCELLED = "LOOP_CANCELLED"
    FAILURE_RESET = "FAILURE_RESET"


class StrategyMode(enum.StrEnum):
    """Strategy operating mode for learning evidence consumption."""

    EXPLOIT = "EXPLOIT"
    EXPLORE = "EXPLORE"
    ABSTAIN = "ABSTAIN"


class CanonicalGuardianContext(BaseModel):
    """Frozen canonical Guardian safety context contract."""

    model_config = ConfigDict(frozen=True)

    active_exception_identities: list[str] = Field(default_factory=list)
    active_ruleset_checksum: str
    active_ruleset_id: str
    active_ruleset_version: int | str
    guardian_epoch: int
    guardian_policy_config_version: int | str
    overall_gate_state: str
    unresolved_blocking_finding_identities: list[str] = Field(default_factory=list)

    def compute_checksum(self) -> str:
        """Compute deterministic SHA-256 over canonical JSON serialization."""
        payload = json.dumps(
            self.model_dump(),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AutonomyActionDefinition(BaseModel):
    """Definition of a planned action within an iteration."""

    model_config = ConfigDict(frozen=True)

    action_type: ActionType
    target_subsystem: str
    target_entity_id: UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_class: ActionRiskClass
    required_autonomy_level: AutonomyLevel
    requires_approval: bool
    estimated_cost_usd: Decimal = Decimal("0.0000")
    precondition_fingerprint: str
    action_checksum: str
    semantic_action_key: str


class AutonomyPlanProposal(BaseModel):
    """Structured proposal generated by deterministic rules or advisory LLM."""

    model_config = ConfigDict(frozen=True)

    action_type: ActionType
    target_subsystem: str
    target_entity_id: UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(max_length=500)
    strategy_mode: StrategyMode = StrategyMode.EXPLOIT
    learning_hypothesis_ids: list[UUID] = Field(default_factory=list)
    model_name: str | None = None
    prompt_template_version: str | None = None
    prompt_checksum: str | None = None
    metadata_: dict[str, Any] = Field(default_factory=dict)
