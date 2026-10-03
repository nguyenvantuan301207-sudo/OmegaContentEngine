"""Domain entities and types for P21-C Retention & Pacing Intelligence.

Defines PacingProfile, PacingPlan, SectionTimingDetail, OpenLoopMetric,
RevealStage, and structured PacingFinding codes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from omega.domain.narrative_plan import InformationDensity, NarrativeFormatProfile, NarrativeSectionRole


class PacingProfile(enum.StrEnum):
    """Pacing profile controlling narrative velocity, context allowance, and loop tension."""

    FAST = "FAST"
    BALANCED = "BALANCED"
    DELIBERATE = "DELIBERATE"


class RevealStage(enum.StrEnum):
    """Progressive reveal stages for narrative pacing."""

    SETUP = "SETUP"
    PARTIAL_REVEAL = "PARTIAL_REVEAL"
    EVIDENCE_PROGRESSION = "EVIDENCE_PROGRESSION"
    MAJOR_REVEAL = "MAJOR_REVEAL"
    FINAL_PAYOFF = "FINAL_PAYOFF"


class PacingFindingCode(enum.StrEnum):
    """Deterministic pacing and retention finding rule codes."""

    DEAD_SECTION = "DEAD_SECTION"
    LOW_INFORMATION_PROGRESS = "LOW_INFORMATION_PROGRESS"
    OVERLONG_CONTEXT = "OVERLONG_CONTEXT"
    STALLED_DEVELOPMENT = "STALLED_DEVELOPMENT"
    REPEATED_CLAIM = "REPEATED_CLAIM"
    REPEATED_OBJECTIVE = "REPEATED_OBJECTIVE"
    DUPLICATE_KEY_INFO = "DUPLICATE_KEY_INFO"
    DUPLICATE_PROMISE = "DUPLICATE_PROMISE"
    REPEATED_TAKEAWAY = "REPEATED_TAKEAWAY"
    LOOP_RESOLVED_TOO_QUICKLY = "LOOP_RESOLVED_TOO_QUICKLY"
    LOOP_HELD_TOO_LONG = "LOOP_HELD_TOO_LONG"
    MULTIPLE_UNRESOLVED_LOOPS = "MULTIPLE_UNRESOLVED_LOOPS"
    PREMATURE_REVEAL = "PREMATURE_REVEAL"
    DELAYED_REVEAL = "DELAYED_REVEAL"
    FLAT_ESCALATION = "FLAT_ESCALATION"
    REVERSED_ESCALATION = "REVERSED_ESCALATION"
    DURATION_IMBALANCE = "DURATION_IMBALANCE"
    DENSITY_UNDERLOADED = "DENSITY_UNDERLOADED"
    DENSITY_OVERLOADED = "DENSITY_OVERLOADED"


class PacingFindingSeverity(enum.StrEnum):
    """Severity classification of a retention pacing finding."""

    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class PacingFinding(BaseModel):
    """Structured pacing observation or defect finding."""

    code: PacingFindingCode
    severity: PacingFindingSeverity
    message: str
    section_order: int | None = None
    metric_value: float | None = None

    model_config = ConfigDict(frozen=True)


class SectionTimingDetail(BaseModel):
    """Per-section timing, density, and reveal allocation detail."""

    section_id: UUID
    section_order: int
    role: NarrativeSectionRole
    current_duration_seconds: int
    recommended_duration_seconds: int
    target_information_density: InformationDensity
    reveal_stage: RevealStage
    intensity_score: float = Field(ge=0.0, le=1.0, description="Normalized narrative intensity score")

    model_config = ConfigDict(frozen=True)


class OpenLoopMetric(BaseModel):
    """Tracking of promise/payoff open-loop lifetime and delay."""

    promise_id: str
    promise_section_order: int
    payoff_section_order: int | None = None
    elapsed_duration_seconds: int
    section_span: int
    is_resolved: bool
    acceptable_min_seconds: int
    acceptable_max_seconds: int
    status: Literal["OPTIMAL", "TOO_FAST", "TOO_SLOW", "UNRESOLVED"]

    model_config = ConfigDict(frozen=True)


class PacingAdjustment(BaseModel):
    """Recommended concrete pacing adjustment for a section."""

    section_order: int
    adjustment_type: str
    duration_delta_seconds: int
    density_override: InformationDensity | None = None
    rationale: str

    model_config = ConfigDict(frozen=True)


class PacingPlan(BaseModel):
    """Immutable derived pacing and retention plan for a NarrativePlan."""

    id: UUID = Field(default_factory=uuid.uuid4)
    narrative_plan_id: UUID
    narrative_plan_version: int
    pacing_profile: PacingProfile
    format_profile: NarrativeFormatProfile
    target_duration_seconds: int
    initial_duration_sum_seconds: int
    optimized_duration_sum_seconds: int
    section_timings: list[SectionTimingDetail]
    open_loop_metrics: list[OpenLoopMetric]
    findings: list[PacingFinding]
    recommended_adjustments: list[PacingAdjustment]
    escalation_curve: list[float]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(from_attributes=True, frozen=True)
