"""Guardian domain models, enums, schemas, and canonical hashing functions.

Defines Guardian checkpoints, gate states, actions, severities, risk types,
detector run statuses, failure policies, resolution types, and Pydantic schemas.
This is the domain layer — zero infrastructure dependencies.
"""

from __future__ import annotations

import enum
import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GuardianCheckpoint(enum.StrEnum):
    """Protected boundary checkpoints where Guardian gates are evaluated.

    Checkpoints:
    - PRE_TASK_DISPATCH: Evaluated before task dispatch in Orchestrator.
    - PRE_RENDER: Evaluated before media rendering and FFmpeg staging in Production Engine.
    - POST_RENDER: Evaluated after rendering before media artifacts are committed.
    - PRE_EXTERNAL_SIDE_EFFECT: Dormant protected boundary ready for future consumers
      (e.g., OMEGA-011 Publisher). OMEGA-008 defines the contract and detector support.
    - MISSION_TERMINAL: Evaluated in Orchestrator before Mission transitions to SUCCEEDED.

    Roadmap Reference:
    - OMEGA-009 = Network Manager
    - OMEGA-010 = Smart Scheduler
    - OMEGA-011 = Publisher
    - OMEGA-012 = Analytics
    - OMEGA-013 = Learning
    - OMEGA-014 = Autonomous Loop
    """

    PRE_TASK_DISPATCH = "PRE_TASK_DISPATCH"
    PRE_RENDER = "PRE_RENDER"
    POST_RENDER = "POST_RENDER"
    PRE_EXTERNAL_SIDE_EFFECT = "PRE_EXTERNAL_SIDE_EFFECT"
    MISSION_TERMINAL = "MISSION_TERMINAL"


class GuardianGateState(enum.StrEnum):
    """Resulting gate state determining whether workflow execution can advance."""

    OPEN = "OPEN"
    RESTRICTED = "RESTRICTED"
    BLOCKED = "BLOCKED"
    WAITING_GUARDIAN = "WAITING_GUARDIAN"


class GuardianAction(enum.StrEnum):
    """Deterministic actions emitted by the Guardian Decision Engine."""

    ALLOW = "ALLOW"
    ALLOW_WITH_WARNING = "ALLOW_WITH_WARNING"
    PAUSE = "PAUSE"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"
    FORCE_FAIL = "FORCE_FAIL"


class GuardianSeverity(enum.StrEnum):
    """Severity classification of a Guardian finding."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class GuardianRiskType(enum.StrEnum):
    """Categorical risk taxonomy for findings."""

    CONTENT_QUALITY = "CONTENT_QUALITY"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    COPYRIGHT_LICENSE = "COPYRIGHT_LICENSE"
    MEDIA_CORRUPTION = "MEDIA_CORRUPTION"
    HARD_BUDGET_OVERRUN = "HARD_BUDGET_OVERRUN"
    COST_ANOMALY = "COST_ANOMALY"
    SYSTEM_CAPACITY = "SYSTEM_CAPACITY"
    SYSTEM_TELEMETRY = "SYSTEM_TELEMETRY"
    PIPELINE_RUNAWAY = "PIPELINE_RUNAWAY"


class DetectorRunStatus(enum.StrEnum):
    """Lifecycle status of an individual detector run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class DetectorFailurePolicy(enum.StrEnum):
    """Declared failure policy if the detector itself errors or times out."""

    FAIL_OPEN_WITH_WARNING = "FAIL_OPEN_WITH_WARNING"
    FAIL_CLOSED = "FAIL_CLOSED"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"


class CheckTriggerType(enum.StrEnum):
    """How or why a GuardianCheck evaluation was initiated."""

    PRE_TASK_DISPATCH = "PRE_TASK_DISPATCH"
    PRE_RENDER = "PRE_RENDER"
    POST_RENDER = "POST_RENDER"
    PRE_EXTERNAL_SIDE_EFFECT = "PRE_EXTERNAL_SIDE_EFFECT"
    MISSION_TERMINAL = "MISSION_TERMINAL"
    RESUME_RECHECK = "RESUME_RECHECK"
    MANUAL = "MANUAL"
    API = "API"


class GuardianResolutionType(enum.StrEnum):
    """Resolution classification for operator actions on findings or decisions."""

    OVERRIDE_APPROVED = "OVERRIDE_APPROVED"
    EXCEPTION_APPLIED = "EXCEPTION_APPLIED"
    EXCEPTION_REVOKED = "EXCEPTION_REVOKED"
    RETRY_REQUESTED = "RETRY_REQUESTED"
    FALSE_POSITIVE_DISMISSED = "FALSE_POSITIVE_DISMISSED"
    MITIGATED = "MITIGATED"
    TERMINAL_ACCEPTED = "TERMINAL_ACCEPTED"


class AlertChannel(enum.StrEnum):
    """Supported channels for the transactional alert outbox."""

    IN_APP = "IN_APP"
    TELEGRAM = "TELEGRAM"
    EMAIL = "EMAIL"


class AlertOutboxStatus(enum.StrEnum):
    """Delivery status of an alert in the outbox."""

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    RETRY = "RETRY"
    DEAD = "DEAD"


class RuleSetStatus(enum.StrEnum):
    """Lifecycle status of a versioned ruleset."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class CostType(enum.StrEnum):
    """Categorization of operational resource costs."""

    LLM_TOKEN = "LLM_TOKEN"
    TTS = "TTS"
    COMPUTE_RENDER = "COMPUTE_RENDER"
    MEDIA_STORAGE = "MEDIA_STORAGE"
    EXTERNAL_API = "EXTERNAL_API"


class GuardianTargetType(enum.StrEnum):
    """Canonical target entity types evaluated by Guardian."""

    MISSION = "MISSION"
    TASK = "TASK"
    PRODUCTION_REQUEST = "PRODUCTION_REQUEST"
    MEDIA_ARTIFACT = "MEDIA_ARTIFACT"


def compute_canonical_check_key(
    target_type: GuardianTargetType | str,
    target_id: UUID | str,
    checkpoint: GuardianCheckpoint | str,
    trigger_type: CheckTriggerType | str,
    guardian_epoch: int,
    ruleset_version: str,
    caller_key: str = "default",
) -> str:
    """Compute deterministic SHA-256 canonical idempotency key for a GuardianCheck.

    Ensures that evaluations under different epochs, rulesets, or caller nonces
    never collide with or reuse a stale check.
    """
    raw = (
        f"{str(target_type).upper()}:{str(target_id)}:{str(checkpoint).upper()}:"
        f"{str(trigger_type).upper()}:{int(guardian_epoch)}:{str(ruleset_version)}:{str(caller_key)}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_rules_config_checksum(rules_config: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 checksum for ruleset configuration."""
    import json

    canonical_json = json.dumps(rules_config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ── Schemas ──


class GuardianRuleSetCreate(BaseModel):
    """Schema for creating a new GuardianRuleSet version."""

    version: str = Field(..., min_length=1, max_length=50)
    effective_at: datetime
    rules_config: dict[str, Any] = Field(default_factory=dict)


class GuardianRuleSetResponse(BaseModel):
    """Schema for returning GuardianRuleSet details."""

    id: UUID
    version: str
    status: RuleSetStatus
    effective_at: datetime
    checksum: str
    rules_config: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GuardianDetectorRunResponse(BaseModel):
    """Schema for returning individual detector execution lifecycle details."""

    id: UUID
    guardian_check_id: UUID
    detector_type: str
    detector_version: str
    status: DetectorRunStatus
    failure_policy: DetectorFailurePolicy
    attempt: int
    max_attempts: int
    idempotency_key: str
    error_data: dict[str, Any] | None = None
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GuardianFindingData(BaseModel):
    """Value object for findings emitted by detectors before DB persistence."""

    rule_id: str = Field(..., min_length=1, max_length=100)
    severity: GuardianSeverity
    risk_type: GuardianRiskType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict)
    location_reference: dict[str, Any] = Field(default_factory=dict)
    message: str = Field(..., min_length=1)


class GuardianFindingResponse(BaseModel):
    """Schema for returning immutable GuardianFinding details."""

    id: UUID
    guardian_check_id: UUID
    detector_run_id: UUID
    detector_type: str
    detector_version: str
    rule_id: str
    severity: GuardianSeverity
    risk_type: GuardianRiskType
    confidence: float
    evidence: dict[str, Any]
    location_reference: dict[str, Any]
    message: str
    created_at: datetime
    applied_exception_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class GuardianDecisionResponse(BaseModel):
    """Schema for returning immutable GuardianDecision details."""

    id: UUID
    guardian_check_id: UUID
    action: GuardianAction
    reason: str
    resulting_gate_state: GuardianGateState
    actor: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GuardianCheckCreate(BaseModel):
    """Schema for requesting a Guardian evaluation."""

    mission_id: UUID
    checkpoint: GuardianCheckpoint
    trigger_type: CheckTriggerType = CheckTriggerType.API
    task_id: UUID | None = None
    production_request_id: UUID | None = None
    media_artifact_id: UUID | None = None
    caller_key: str = "default"
    diagnostic_context: dict[str, Any] = Field(default_factory=dict)


class GuardianCheckResponse(BaseModel):
    """Schema for returning GuardianCheck evaluation details."""

    id: UUID
    mission_id: UUID
    task_id: UUID | None = None
    production_request_id: UUID | None = None
    media_artifact_id: UUID | None = None
    trigger_type: CheckTriggerType
    checkpoint: GuardianCheckpoint
    ruleset_id: UUID
    ruleset_version: str
    ruleset_checksum: str
    status: str
    idempotency_key: str
    guardian_epoch: int
    diagnostic_context: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime
    decision: GuardianDecisionResponse | None = None
    findings: list[GuardianFindingResponse] = Field(default_factory=list)
    detector_runs: list[GuardianDetectorRunResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GuardianResolutionCreate(BaseModel):
    """Schema for recording an operator resolution on a finding or decision."""

    resolution_type: GuardianResolutionType
    actor: str = Field(..., min_length=1, max_length=100)
    reason: str = Field(..., min_length=1)
    finding_id: UUID | None = None
    decision_id: UUID | None = None

    @field_validator("decision_id")
    @classmethod
    def validate_target(cls, v: UUID | None, info: Any) -> UUID | None:
        finding_id = info.data.get("finding_id")
        if not finding_id and not v:
            raise ValueError("Resolution must reference at least one of finding_id or decision_id")
        return v


class GuardianResolutionEventResponse(BaseModel):
    """Schema for returning append-only GuardianResolutionEvent details."""

    id: UUID
    finding_id: UUID | None = None
    decision_id: UUID | None = None
    resolution_type: GuardianResolutionType
    actor: str
    reason: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


GuardianResolutionResponse = GuardianResolutionEventResponse


class GuardianExceptionCreate(BaseModel):
    """Schema for creating a scoped, auditable GuardianException."""

    rule_id: str | None = Field(default=None, max_length=100)
    risk_type: GuardianRiskType | None = None
    channel_id: UUID | None = None
    mission_id: UUID | None = None
    expires_at: datetime
    created_by: str = Field(..., min_length=1, max_length=100)
    created_reason: str = Field(..., min_length=1)

    @field_validator("risk_type")
    @classmethod
    def validate_scope(cls, v: GuardianRiskType | None, info: Any) -> GuardianRiskType | None:
        rule_id = info.data.get("rule_id")
        if not rule_id and not v:
            raise ValueError("Exception must specify at least rule_id or risk_type")
        return v


class GuardianExceptionRevoke(BaseModel):
    """Schema for revoking an active exception."""

    revoked_by: str = Field(..., min_length=1, max_length=100)
    revocation_reason: str = Field(..., min_length=1)


class GuardianExceptionResponse(BaseModel):
    """Schema for returning GuardianException details."""

    id: UUID
    rule_id: str | None = None
    risk_type: str | None = None
    channel_id: UUID | None = None
    mission_id: UUID | None = None
    expires_at: datetime
    created_by: str
    created_reason: str
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revocation_reason: str | None = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CostRecordCreate(BaseModel):
    """Schema for creating an idempotent operational cost record."""

    mission_id: UUID
    task_id: UUID | None = None
    production_request_id: UUID | None = None
    cost_type: CostType
    amount_usd: Decimal = Field(..., ge=0)
    units: int = Field(default=0, ge=0)
    source_type: str = Field(..., min_length=1, max_length=100)
    source_id: str = Field(..., min_length=1, max_length=255)
    idempotency_key: str = Field(..., min_length=1, max_length=255)


class CostRecordResponse(BaseModel):
    """Schema for returning CostRecord details."""

    id: UUID
    mission_id: UUID
    task_id: UUID | None = None
    production_request_id: UUID | None = None
    cost_type: CostType
    amount_usd: Decimal
    units: int
    source_type: str
    source_id: str
    idempotency_key: str
    recorded_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GuardianStatusResponse(BaseModel):
    """Consolidated Guardian status for mission UI / API."""

    mission_id: UUID
    guardian_epoch: int
    current_gate_state: GuardianGateState
    latest_check_id: UUID | None = None
    latest_checkpoint: GuardianCheckpoint | None = None
    latest_action: GuardianAction | None = None
    latest_decision_reason: str | None = None
    active_findings_count: int = 0
    critical_findings_count: int = 0
    blocking_checkpoints: list[GuardianCheckpoint] = Field(default_factory=list)
    unresolved_findings: list[GuardianFindingResponse] = Field(default_factory=list)
    updated_at: datetime


class GuardianConsolidatedStatus(BaseModel):
    """Consolidated mission gate status, metrics, and budget tracking."""

    mission_id: UUID
    guardian_epoch: int
    overall_gate_state: GuardianGateState
    checkpoint_states: dict[str, GuardianGateState] = Field(default_factory=dict)
    blocking_checkpoints: list[GuardianCheckpoint] = Field(default_factory=list)
    open_findings_count: int = 0
    accumulated_cost_usd: Decimal = Decimal("0.00")
    budget_ceiling_usd: Decimal = Decimal("50.00")
    remaining_budget_usd: Decimal = Decimal("50.00")
