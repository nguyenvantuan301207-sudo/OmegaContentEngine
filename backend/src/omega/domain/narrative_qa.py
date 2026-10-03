"""Narrative QA & Acceptance Domain Model.

Defines the final editorial quality gate contracts, typed finding taxonomy,
subsystems, severities, recommendations, and evaluation results.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class NarrativeQAStatus(str, enum.Enum):
    """Final acceptance gate verdict for a NarrativePlan."""

    PASS = "PASS"
    REVISE = "REVISE"
    FAIL = "FAIL"


class NarrativeQASeverity(str, enum.Enum):
    """Severity classification for QA findings."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKER = "BLOCKER"

    @property
    def rank(self) -> int:
        ranks = {
            NarrativeQASeverity.INFO: 1,
            NarrativeQASeverity.WARNING: 2,
            NarrativeQASeverity.ERROR: 3,
            NarrativeQASeverity.BLOCKER: 4,
        }
        return ranks.get(self, 0)

    def __ge__(self, other: NarrativeQASeverity) -> bool:
        if not isinstance(other, NarrativeQASeverity):
            return NotImplemented
        return self.rank >= other.rank

    def __gt__(self, other: NarrativeQASeverity) -> bool:
        if not isinstance(other, NarrativeQASeverity):
            return NotImplemented
        return self.rank > other.rank

    def __le__(self, other: NarrativeQASeverity) -> bool:
        if not isinstance(other, NarrativeQASeverity):
            return NotImplemented
        return self.rank <= other.rank

    def __lt__(self, other: NarrativeQASeverity) -> bool:
        if not isinstance(other, NarrativeQASeverity):
            return NotImplemented
        return self.rank < other.rank


class NarrativeQASubsystem(str, enum.Enum):
    """Source evaluation subsystem generating the QA finding."""

    STRUCTURE = "STRUCTURE"
    GROUNDING = "GROUNDING"
    PACING = "PACING"
    EDITORIAL = "EDITORIAL"
    CHANNEL_FIT = "CHANNEL_FIT"


class NarrativeQAFindingCode(str, enum.Enum):
    """Comprehensive typed finding taxonomy for Narrative QA."""

    # Subsystem: STRUCTURE
    EMPTY_PLAN = "EMPTY_PLAN"
    MISSING_REQUIRED_ROLE = "MISSING_REQUIRED_ROLE"
    INVALID_ROLE_ORDER = "INVALID_ROLE_ORDER"
    ORPHAN_PAYOFF = "ORPHAN_PAYOFF"
    UNRESOLVED_PROMISE = "UNRESOLVED_PROMISE"
    INVALID_DURATION = "INVALID_DURATION"
    INVALID_GROUNDING = "INVALID_GROUNDING"
    INVALID_CTA_PLACEMENT = "INVALID_CTA_PLACEMENT"
    LINEAGE_INCONSISTENCY = "LINEAGE_INCONSISTENCY"

    # Subsystem: GROUNDING
    WEAK_GROUNDING_COVERAGE = "WEAK_GROUNDING_COVERAGE"
    EXCESSIVE_CITATION_REUSE = "EXCESSIVE_CITATION_REUSE"
    UNSUPPORTED_KEY_INFO = "UNSUPPORTED_KEY_INFO"
    UNCERTAIN_CLAIM_PROMOTED = "UNCERTAIN_CLAIM_PROMOTED"
    PAYOFF_LACKING_EVIDENCE = "PAYOFF_LACKING_EVIDENCE"
    INCONSISTENT_RESEARCH_EVIDENCE = "INCONSISTENT_RESEARCH_EVIDENCE"

    # Subsystem: PROMISE / PAYOFF
    WEAK_PROMISE = "WEAK_PROMISE"
    VAGUE_PROMISE = "VAGUE_PROMISE"
    PROMISE_PAYOFF_MISMATCH = "PROMISE_PAYOFF_MISMATCH"
    PAYOFF_INCOMPLETE = "PAYOFF_INCOMPLETE"
    MULTIPLE_REDUNDANT_PROMISES = "MULTIPLE_REDUNDANT_PROMISES"
    PAYOFF_NOT_SUPPORTED = "PAYOFF_NOT_SUPPORTED"

    # Subsystem: HOOK
    WEAK_HOOK = "WEAK_HOOK"
    MISLEADING_HOOK = "MISLEADING_HOOK"
    OVERLONG_HOOK = "OVERLONG_HOOK"
    HOOK_PAYOFF_MISMATCH = "HOOK_PAYOFF_MISMATCH"

    # Subsystem: COHERENCE / REPETITION
    NARRATIVE_REPETITION = "NARRATIVE_REPETITION"
    LOGIC_GAP = "LOGIC_GAP"
    ABRUPT_TRANSITION = "ABRUPT_TRANSITION"
    CIRCULAR_DEVELOPMENT = "CIRCULAR_DEVELOPMENT"

    # Subsystem: PACING
    OVERLONG_CONTEXT = "OVERLONG_CONTEXT"
    DEAD_ZONE = "DEAD_ZONE"
    OVERLOADED_SECTION = "OVERLOADED_SECTION"
    UNDERLOADED_SECTION = "UNDERLOADED_SECTION"
    OPEN_LOOP_TOO_LONG = "OPEN_LOOP_TOO_LONG"
    PAYOFF_TOO_EARLY = "PAYOFF_TOO_EARLY"
    PAYOFF_TOO_LATE = "PAYOFF_TOO_LATE"
    FLAT_ESCALATION = "FLAT_ESCALATION"

    # Subsystem: CHANNEL_FIT
    CHANNEL_TONE_MISMATCH = "CHANNEL_TONE_MISMATCH"
    AUDIENCE_DEPTH_MISMATCH = "AUDIENCE_DEPTH_MISMATCH"
    PACING_STYLE_MISMATCH = "PACING_STYLE_MISMATCH"
    CTA_STYLE_MISMATCH = "CTA_STYLE_MISMATCH"

    # Subsystem: EDITORIAL_COMPLETENESS
    MISSING_CONTEXT = "MISSING_CONTEXT"
    DEVELOPMENT_TOO_THIN = "DEVELOPMENT_TOO_THIN"
    TAKEAWAY_DISCONNECTED = "TAKEAWAY_DISCONNECTED"
    CLOSING_ABRUPT = "CLOSING_ABRUPT"
    CTA_DISCONNECTED = "CTA_DISCONNECTED"


class NarrativeQAFinding(BaseModel):
    """Individual QA finding detailing a specific editorial, structural, or pacing issue."""

    code: NarrativeQAFindingCode
    severity: NarrativeQASeverity
    subsystem: NarrativeQASubsystem
    explanation: str
    affected_section_orders: list[int] = Field(default_factory=list)
    affected_section_ids: list[UUID] = Field(default_factory=list)
    contributing_sources: list[str] = Field(default_factory=list)
    recommended_remediation: str | None = None

    model_config = ConfigDict(frozen=True)


class NarrativeQARecommendationAction(str, enum.Enum):
    """Actionable remediation directives proposed by Narrative QA."""

    SHORTEN_CONTEXT = "SHORTEN_CONTEXT"
    STRENGTHEN_PAYOFF_EVIDENCE = "STRENGTHEN_PAYOFF_EVIDENCE"
    MERGE_SECTIONS = "MERGE_SECTIONS"
    DELAY_PAYOFF = "DELAY_PAYOFF"
    ADVANCE_PAYOFF = "ADVANCE_PAYOFF"
    ADD_GROUNDING = "ADD_GROUNDING"
    TIGHTEN_HOOK = "TIGHTEN_HOOK"
    REMOVE_REDUNDANT_CTA = "REMOVE_REDUNDANT_CTA"
    REBALANCE_DURATION = "REBALANCE_DURATION"
    EXPAND_DEVELOPMENT = "EXPAND_DEVELOPMENT"
    SPLIT_OVERLOADED_SECTION = "SPLIT_OVERLOADED_SECTION"


class NarrativeQARecommendation(BaseModel):
    """Structured remediation proposal for REVISE plans."""

    action: NarrativeQARecommendationAction
    affected_section_orders: list[int] = Field(default_factory=list)
    remediation: str
    suggested_duration_delta_seconds: int | None = None

    model_config = ConfigDict(frozen=True)


class NarrativeQAGateError(RuntimeError):
    """Raised when script generation is attempted against a plan blocked by Narrative QA."""

    def __init__(self, message: str, qa_result: NarrativeQAResult | None = None) -> None:
        super().__init__(message)
        self.qa_result = qa_result


class NarrativeQAResult(BaseModel):
    """Comprehensive evaluation outcome from Narrative QA."""

    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    plan_version: int
    status: NarrativeQAStatus
    highest_severity: NarrativeQASeverity = NarrativeQASeverity.INFO
    findings: list[NarrativeQAFinding] = Field(default_factory=list)
    recommendations: list[NarrativeQARecommendation] = Field(default_factory=list)
    pacing_plan_id: UUID | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(frozen=True)

    @property
    def is_accepted(self) -> bool:
        return self.status == NarrativeQAStatus.PASS

    @property
    def is_blocked(self) -> bool:
        return self.status == NarrativeQAStatus.FAIL

    @property
    def requires_revision(self) -> bool:
        return self.status == NarrativeQAStatus.REVISE

    @property
    def blocker_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == NarrativeQASeverity.BLOCKER)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == NarrativeQASeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == NarrativeQASeverity.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == NarrativeQASeverity.INFO)
