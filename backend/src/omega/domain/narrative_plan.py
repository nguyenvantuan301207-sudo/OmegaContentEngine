"""NarrativePlan domain authority.

Defines the pre-script story structure model, canonical section roles,
format profiles, promise/payoff contracts, source grounding lineage,
and deterministic validation schemas for P21 narrative architecture.
"""

from __future__ import annotations

import enum
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NarrativePlanStatus(enum.StrEnum):
    """Lifecycle status of a NarrativePlan."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class NarrativeSectionRole(enum.StrEnum):
    """Canonical semantic roles for ordered narrative sections."""

    HOOK = "HOOK"
    PROMISE = "PROMISE"
    CONTEXT = "CONTEXT"
    DEVELOPMENT = "DEVELOPMENT"
    ESCALATION = "ESCALATION"
    PAYOFF = "PAYOFF"
    TAKEAWAY = "TAKEAWAY"
    CLOSING = "CLOSING"
    CTA = "CTA"


class NarrativeFormatProfile(enum.StrEnum):
    """Target narrative structure and duration format profiles."""

    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"


class InformationDensity(enum.StrEnum):
    """Information density target for a narrative section."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class GroundingType(enum.StrEnum):
    """Classification of research grounding reference."""

    FACTUAL = "FACTUAL"
    BACKGROUND = "BACKGROUND"
    DATA_POINT = "DATA_POINT"
    QUOTE = "QUOTE"


class NarrativeRuleCode(enum.StrEnum):
    """Deterministic validation rule codes for NarrativePlan."""

    EMPTY_PLAN = "EMPTY_PLAN"
    DUPLICATE_SECTION_ORDER = "DUPLICATE_SECTION_ORDER"
    ORDERING_GAP = "ORDERING_GAP"
    INVALID_ROLE_COMBINATION = "INVALID_ROLE_COMBINATION"
    MISSING_REQUIRED_HOOK = "MISSING_REQUIRED_HOOK"
    HOOK_NOT_FIRST = "HOOK_NOT_FIRST"
    HOOK_DURATION_EXCEEDED = "HOOK_DURATION_EXCEEDED"
    MISSING_REQUIRED_ROLE = "MISSING_REQUIRED_ROLE"
    UNRESOLVED_PROMISE = "UNRESOLVED_PROMISE"
    ORPHAN_PAYOFF = "ORPHAN_PAYOFF"
    PAYOFF_BEFORE_PROMISE = "PAYOFF_BEFORE_PROMISE"
    DUPLICATE_PROMISE_ID = "DUPLICATE_PROMISE_ID"
    DUPLICATE_PAYOFF_LINK = "DUPLICATE_PAYOFF_LINK"
    INVALID_DURATION_ALLOCATION = "INVALID_DURATION_ALLOCATION"
    DURATION_OUT_OF_BOUNDS = "DURATION_OUT_OF_BOUNDS"
    DURATION_SUM_MISMATCH = "DURATION_SUM_MISMATCH"
    MISSING_GROUNDING = "MISSING_GROUNDING"
    INVALID_CTA_PLACEMENT = "INVALID_CTA_PLACEMENT"
    EXCESSIVE_CTA_COUNT = "EXCESSIVE_CTA_COUNT"
    SECTION_COUNT_OUT_OF_BOUNDS = "SECTION_COUNT_OUT_OF_BOUNDS"


class NarrativeSeverity(enum.StrEnum):
    """Severity of a NarrativePlan validation finding."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKING = "BLOCKING"


class NarrativeValidationStatus(enum.StrEnum):
    """Overall outcome of NarrativePlan validation."""

    PASSED = "PASSED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
    BLOCKED = "BLOCKED"


class GroundingReference(BaseModel):
    """Provenance citation linking a narrative section to research artifacts."""

    research_brief_id: UUID
    claim_id: UUID | None = None
    evidence_id: UUID | None = None
    source_id: UUID | None = None
    grounding_type: GroundingType = GroundingType.FACTUAL
    description: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(from_attributes=True, frozen=True)


class NarrativeSection(BaseModel):
    """Structured, ordered section of a NarrativePlan."""

    id: UUID = Field(default_factory=uuid.uuid4)
    section_order: int = Field(ge=1, description="1-indexed sequence order")
    role: NarrativeSectionRole
    objective: str = Field(min_length=3, max_length=500, description="Editorial goal of this section")
    key_information: list[str] = Field(default_factory=list, description="Key facts or points to cover")
    grounding_references: list[GroundingReference] = Field(
        default_factory=list, description="Research claims/sources supporting this section"
    )
    target_duration_seconds: int = Field(ge=1, le=1800, description="Allocated target duration in seconds")
    target_information_density: InformationDensity = InformationDensity.MEDIUM
    open_loop_intent: str | None = Field(default=None, max_length=300, description="Description of curiosity loop opened")
    promise_id: str | None = Field(default=None, max_length=100, description="Identifier of promise introduced here")
    payoff_reference: str | None = Field(
        default=None, max_length=100, description="promise_id resolved by this payoff section"
    )
    notes: str | None = Field(default=None, max_length=500, description="Editorial constraints or directions")

    model_config = ConfigDict(from_attributes=True)

    @field_validator("objective")
    @classmethod
    def clean_objective(cls, v: str) -> str:
        s = v.strip()
        if len(s) < 3:
            raise ValueError("Section objective must have at least 3 characters.")
        return s

    def get_grounding_claim_ids(self) -> list[UUID]:
        """Return all unique claim IDs attached to this section."""
        return [g.claim_id for g in self.grounding_references if g.claim_id is not None]


class FormatProfileConstraints(BaseModel):
    """Deterministic structural boundaries for a narrative format profile."""

    format_profile: NarrativeFormatProfile
    allowed_roles: set[NarrativeSectionRole]
    required_roles: set[NarrativeSectionRole]
    min_sections: int
    max_sections: int
    min_duration_seconds: int
    max_duration_seconds: int
    default_duration_seconds: int
    max_hook_duration_seconds: int
    duration_tolerance_pct: float = 0.20
    cta_allowed: bool = True
    cta_max_count: int = 1

    model_config = ConfigDict(frozen=True)


# Deterministic constraints catalog
FORMAT_PROFILE_CONSTRAINTS: dict[NarrativeFormatProfile, FormatProfileConstraints] = {
    NarrativeFormatProfile.SHORT: FormatProfileConstraints(
        format_profile=NarrativeFormatProfile.SHORT,
        allowed_roles={
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
            NarrativeSectionRole.CTA,
        },
        required_roles={
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
        },
        min_sections=3,
        max_sections=6,
        min_duration_seconds=15,
        max_duration_seconds=60,
        default_duration_seconds=45,
        max_hook_duration_seconds=10,
        duration_tolerance_pct=0.20,
        cta_allowed=True,
        cta_max_count=1,
    ),
    NarrativeFormatProfile.MEDIUM: FormatProfileConstraints(
        format_profile=NarrativeFormatProfile.MEDIUM,
        allowed_roles={
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.ESCALATION,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
            NarrativeSectionRole.CTA,
        },
        required_roles={
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        },
        min_sections=5,
        max_sections=14,
        min_duration_seconds=180,
        max_duration_seconds=480,
        default_duration_seconds=300,
        max_hook_duration_seconds=30,
        duration_tolerance_pct=0.15,
        cta_allowed=True,
        cta_max_count=2,
    ),
    NarrativeFormatProfile.LONG: FormatProfileConstraints(
        format_profile=NarrativeFormatProfile.LONG,
        allowed_roles={
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.ESCALATION,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
            NarrativeSectionRole.CTA,
        },
        required_roles={
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.ESCALATION,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        },
        min_sections=8,
        max_sections=30,
        min_duration_seconds=480,
        max_duration_seconds=1800,
        default_duration_seconds=720,
        max_hook_duration_seconds=60,
        duration_tolerance_pct=0.15,
        cta_allowed=True,
        cta_max_count=3,
    ),
}


class NarrativePlanFinding(BaseModel):
    """Structured finding emitted by NarrativePlan validation."""

    rule_code: NarrativeRuleCode
    severity: NarrativeSeverity
    message: str
    section_order: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class NarrativePlanValidationResult(BaseModel):
    """Outcome of validating a NarrativePlan."""

    plan_id: UUID
    status: NarrativeValidationStatus
    findings: list[NarrativePlanFinding] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(from_attributes=True)

    @property
    def is_valid(self) -> bool:
        """Returns True if validation did not produce blocking errors."""
        return self.status in (
            NarrativeValidationStatus.PASSED,
            NarrativeValidationStatus.PASSED_WITH_WARNINGS,
        )


class NarrativePlan(BaseModel):
    """Authoritative pre-script story structure.

    Describes WHAT narrative structure should occur before script prose generation,
    enforcing deterministic format profiles, promise/payoff contracts, and source grounding.
    """

    id: UUID = Field(default_factory=uuid.uuid4)
    content_generation_request_id: UUID
    topic_candidate_id: UUID | None = None
    research_brief_id: UUID | None = None
    channel_dna_revision_id: UUID
    version: int = Field(default=1, ge=1, description="Monotonically increasing revision number")
    status: NarrativePlanStatus = NarrativePlanStatus.DRAFT
    format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM
    target_duration_seconds: int = Field(ge=15, le=3600)
    estimated_duration_seconds: int = Field(ge=0)
    is_current: bool = True
    supersedes_plan_id: UUID | None = None
    schema_version: int = Field(default=1, description="Immutable schema format version")
    sections: list[NarrativeSection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def compute_estimated_duration(self) -> NarrativePlan:
        """Calculate total estimated duration from sections if not explicitly provided."""
        if not self.estimated_duration_seconds and self.sections:
            self.estimated_duration_seconds = sum(s.target_duration_seconds for s in self.sections)
        return self

    def get_section_by_order(self, order: int) -> NarrativeSection | None:
        """Return section at 1-indexed order."""
        for s in self.sections:
            if s.section_order == order:
                return s
        return None

    def get_sections_by_role(self, role: NarrativeSectionRole) -> list[NarrativeSection]:
        """Return all sections fulfilling a specific semantic role."""
        return [s for s in self.sections if s.role == role]

    def get_all_grounding_references(self) -> list[GroundingReference]:
        """Collect all grounding references across all sections."""
        refs: list[GroundingReference] = []
        for s in self.sections:
            refs.extend(s.grounding_references)
        return refs

    def get_promise_map(self) -> dict[str, NarrativeSection]:
        """Map of declared promise_id to section."""
        return {s.promise_id: s for s in self.sections if s.promise_id}

    def get_payoff_map(self) -> dict[str, NarrativeSection]:
        """Map of payoff_reference to section."""
        return {s.payoff_reference: s for s in self.sections if s.payoff_reference}

    def create_revision(
        self,
        new_sections: list[NarrativeSection] | None = None,
        new_target_duration_seconds: int | None = None,
        notes: str | None = None,
    ) -> NarrativePlan:
        """Create a new immutable revision N+1 superseding this plan."""
        sections = new_sections if new_sections is not None else [s.model_copy() for s in self.sections]
        target_dur = new_target_duration_seconds or self.target_duration_seconds
        new_est_dur = sum(s.target_duration_seconds for s in sections)

        rev_meta = dict(self.metadata)
        if notes:
            rev_meta["revision_notes"] = notes
        rev_meta["superseded_at"] = datetime.now(UTC).isoformat()

        new_plan = NarrativePlan(
            id=uuid.uuid4(),
            content_generation_request_id=self.content_generation_request_id,
            topic_candidate_id=self.topic_candidate_id,
            research_brief_id=self.research_brief_id,
            channel_dna_revision_id=self.channel_dna_revision_id,
            version=self.version + 1,
            status=NarrativePlanStatus.DRAFT,
            format_profile=self.format_profile,
            target_duration_seconds=target_dur,
            estimated_duration_seconds=new_est_dur,
            is_current=True,
            supersedes_plan_id=self.id,
            schema_version=self.schema_version,
            sections=sections,
            metadata=rev_meta,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        return new_plan

    def to_serializable_dict(self) -> dict[str, Any]:
        """Export stable, deterministic dictionary with ISO timestamps and string UUIDs."""
        return json.loads(self.model_dump_json())

    @classmethod
    def from_serializable_dict(cls, data: dict[str, Any]) -> NarrativePlan:
        """Construct plan from serialized dictionary."""
        return cls.model_validate(data)
