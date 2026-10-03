"""Visual Editorial QA & Acceptance Domain Model for P22-D.

Defines the final visual quality gate contracts, typed finding taxonomy,
subsystems, severities, recommendations, and evaluation results.
Pure domain logic: zero I/O, zero network, zero database mutations.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class VisualQAStatus(str, enum.Enum):
    """Final visual acceptance gate verdict."""

    PASS = "PASS"
    REVISE = "REVISE"
    FAIL = "FAIL"


class VisualQASeverity(str, enum.Enum):
    """Severity classification for visual QA findings."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKER = "BLOCKER"

    @property
    def rank(self) -> int:
        ranks = {
            VisualQASeverity.INFO: 1,
            VisualQASeverity.WARNING: 2,
            VisualQASeverity.ERROR: 3,
            VisualQASeverity.BLOCKER: 4,
        }
        return ranks.get(self, 0)

    def __ge__(self, other: VisualQASeverity) -> bool:
        if not isinstance(other, VisualQASeverity):
            return NotImplemented
        return self.rank >= other.rank

    def __gt__(self, other: VisualQASeverity) -> bool:
        if not isinstance(other, VisualQASeverity):
            return NotImplemented
        return self.rank > other.rank

    def __le__(self, other: VisualQASeverity) -> bool:
        if not isinstance(other, VisualQASeverity):
            return NotImplemented
        return self.rank <= other.rank

    def __lt__(self, other: VisualQASeverity) -> bool:
        if not isinstance(other, VisualQASeverity):
            return NotImplemented
        return self.rank < other.rank


class VisualQASubsystem(str, enum.Enum):
    """Subsystem emitting visual QA findings."""

    CONTINUITY = "CONTINUITY"
    ASSET = "ASSET"
    COMPOSITION = "COMPOSITION"
    CAMERA = "CAMERA"
    TRANSITION = "TRANSITION"
    EXPLANATORY_VISUAL = "EXPLANATORY_VISUAL"
    GROUNDING = "GROUNDING"
    READABILITY = "READABILITY"
    DATA_FIDELITY = "DATA_FIDELITY"
    EDITORIAL = "EDITORIAL"
    REPETITION = "REPETITION"
    TIMING = "TIMING"
    CHANNEL_STYLE = "CHANNEL_STYLE"
    ARTIFACT = "ARTIFACT"


class VisualQAFindingCode(str, enum.Enum):
    """Comprehensive typed finding taxonomy for Visual Editorial QA."""

    # Subsystem: CONTINUITY (P22-A promotions)
    BROKEN_SUBJECT_CONTINUITY = "BROKEN_SUBJECT_CONTINUITY"
    ACCIDENTAL_ASSET_REPEAT = "ACCIDENTAL_ASSET_REPEAT"
    MOTIF_DRIFT = "MOTIF_DRIFT"
    DOCUMENT_CONTEXT_LOST = "DOCUMENT_CONTEXT_LOST"
    COMPARISON_SIDE_SWAP = "COMPARISON_SIDE_SWAP"
    EXCESSIVE_VISUAL_CHURN = "EXCESSIVE_VISUAL_CHURN"
    EXCESSIVE_VISUAL_HOLD = "EXCESSIVE_VISUAL_HOLD"
    MISSING_VISUAL_PROGRESS = "MISSING_VISUAL_PROGRESS"

    # Subsystem: ASSET (Appropriateness & Intent)
    ASSET_INTENT_MISMATCH = "ASSET_INTENT_MISMATCH"
    IRRELEVANT_BROLL = "IRRELEVANT_BROLL"
    WRONG_ENTITY = "WRONG_ENTITY"
    WRONG_CONTEXT = "WRONG_CONTEXT"
    UNSUPPORTED_VISUAL_CLAIM = "UNSUPPORTED_VISUAL_CLAIM"

    # Subsystem: GROUNDING & PROVENANCE
    UNGROUNDED_VISUAL_DATA = "UNGROUNDED_VISUAL_DATA"
    SOURCE_LINEAGE_MISSING = "SOURCE_LINEAGE_MISSING"
    GENERATED_ASSET_PROVENANCE_MISSING = "GENERATED_ASSET_PROVENANCE_MISSING"
    ARTIFACT_HASH_MISSING = "ARTIFACT_HASH_MISSING"
    EVIDENCE_VISUAL_MISMATCH = "EVIDENCE_VISUAL_MISMATCH"

    # Subsystem: READABILITY & EXPLANATORY_VISUAL
    UNREADABLE_LABEL = "UNREADABLE_LABEL"
    TEXT_OVERFLOW = "TEXT_OVERFLOW"
    NODE_OVERLAP = "NODE_OVERLAP"
    AXIS_UNREADABLE = "AXIS_UNREADABLE"
    DATA_MARK_OBSCURED = "DATA_MARK_OBSCURED"
    SOURCE_LABEL_MISSING = "SOURCE_LABEL_MISSING"
    EXCESSIVE_VISUAL_COMPLEXITY = "EXCESSIVE_VISUAL_COMPLEXITY"

    # Subsystem: DATA_FIDELITY
    DATA_VALUE_MISMATCH = "DATA_VALUE_MISMATCH"
    DATA_UNIT_MISMATCH = "DATA_UNIT_MISMATCH"
    DATA_LABEL_MISMATCH = "DATA_LABEL_MISMATCH"
    DATA_TIME_ORDER_MISMATCH = "DATA_TIME_ORDER_MISMATCH"
    COMPARISON_MAPPING_MISMATCH = "COMPARISON_MAPPING_MISMATCH"
    QUOTE_PARAPHRASE_CONFUSION = "QUOTE_PARAPHRASE_CONFUSION"

    # Subsystem: CAMERA (P22-B evaluation)
    CAMERA_OUT_OF_BOUNDS = "CAMERA_OUT_OF_BOUNDS"
    SUBJECT_CROPPED = "SUBJECT_CROPPED"
    MOTION_TOO_AGGRESSIVE = "MOTION_TOO_AGGRESSIVE"
    EXCESSIVE_CAMERA_ACTIVITY = "EXCESSIVE_CAMERA_ACTIVITY"
    RAPID_DIRECTION_REVERSAL = "RAPID_DIRECTION_REVERSAL"
    UNJUSTIFIED_MOTION = "UNJUSTIFIED_MOTION"
    DOCUMENT_FOCUS_MISSED = "DOCUMENT_FOCUS_MISSED"

    # Subsystem: TRANSITION
    UNSUPPORTED_TRANSITION_REQUEST = "UNSUPPORTED_TRANSITION_REQUEST"
    TRANSITION_FALLBACK_USED = "TRANSITION_FALLBACK_USED"
    EXCESSIVE_TRANSITION_DURATION = "EXCESSIVE_TRANSITION_DURATION"
    TRANSITION_CONTEXT_MISMATCH = "TRANSITION_CONTEXT_MISMATCH"
    TRANSITION_CONTENT_LOSS = "TRANSITION_CONTENT_LOSS"

    # Subsystem: COMPOSITION & SAFE FRAME
    SAFE_FRAME_VIOLATION = "SAFE_FRAME_VIOLATION"
    OVERLAY_COLLISION = "OVERLAY_COLLISION"
    INVALID_CANVAS_DIMENSIONS = "INVALID_CANVAS_DIMENSIONS"
    OFF_FRAME_FOCAL_AREA = "OFF_FRAME_FOCAL_AREA"
    EMPTY_VISUAL_REGION = "EMPTY_VISUAL_REGION"

    # Subsystem: REPETITION
    EXCESSIVE_ASSET_HOLD = "EXCESSIVE_ASSET_HOLD"
    ROLE_REPETITION_EXCESSIVE = "ROLE_REPETITION_EXCESSIVE"
    UNPROGRESSIVE_BROLL = "UNPROGRESSIVE_BROLL"
    REDUNDANT_TEMPLATE_REPETITION = "REDUNDANT_TEMPLATE_REPETITION"
    DISGUISED_STATIC_REPETITION = "DISGUISED_STATIC_REPETITION"

    # Subsystem: ARTIFACT & PHYSICAL INTEGRITY
    MISSING_ARTIFACT = "MISSING_ARTIFACT"
    ZERO_BYTE_ARTIFACT = "ZERO_BYTE_ARTIFACT"
    INVALID_ARTIFACT_DIMENSIONS = "INVALID_ARTIFACT_DIMENSIONS"
    BLANK_FRAME_DETECTED = "BLANK_FRAME_DETECTED"
    CORRUPT_ARTIFACT = "CORRUPT_ARTIFACT"
    RENDERER_FAILURE_ARTIFACT = "RENDERER_FAILURE_ARTIFACT"

    # Subsystem: TIMING
    TIMING_GAP = "TIMING_GAP"
    TIMING_OVERLAP = "TIMING_OVERLAP"
    DURATION_DRIFT = "DURATION_DRIFT"
    BEAT_TIMELINE_MISALIGNMENT = "BEAT_TIMELINE_MISALIGNMENT"

    # Subsystem: CHANNEL_STYLE
    VISUAL_STYLE_MISMATCH = "VISUAL_STYLE_MISMATCH"
    MOTION_STYLE_MISMATCH = "MOTION_STYLE_MISMATCH"
    GRAPHIC_DENSITY_MISMATCH = "GRAPHIC_DENSITY_MISMATCH"


class VisualQAFinding(BaseModel):
    """Individual QA finding detailing a specific visual defect."""

    model_config = ConfigDict(frozen=True)

    code: VisualQAFindingCode
    severity: VisualQASeverity
    subsystem: VisualQASubsystem
    scene_index: int | None = None
    beat_index: int | None = None
    asset_id: str | None = None
    explanation: str
    recommended_remediation: str | None = None
    contributing_sources: list[str] = Field(default_factory=list)


class VisualQARecommendationAction(str, enum.Enum):
    """Structured remediation actions for visual revisions."""

    REPLACE_ASSET = "REPLACE_ASSET"
    REUSE_MOTIF = "REUSE_MOTIF"
    SHORTEN_VISUAL_HOLD = "SHORTEN_VISUAL_HOLD"
    REDUCE_CAMERA_MOTION = "REDUCE_CAMERA_MOTION"
    CHANGE_FOCUS_REGION = "CHANGE_FOCUS_REGION"
    SIMPLIFY_DIAGRAM = "SIMPLIFY_DIAGRAM"
    SPLIT_CHART = "SPLIT_CHART"
    INCREASE_LABEL_SIZE = "INCREASE_LABEL_SIZE"
    REMOVE_REDUNDANT_VISUAL = "REMOVE_REDUNDANT_VISUAL"
    CHANGE_TRANSITION_TO_CUT = "CHANGE_TRANSITION_TO_CUT"
    RESTORE_DOCUMENT_CONTEXT = "RESTORE_DOCUMENT_CONTEXT"
    CORRECT_DATA_VALUE = "CORRECT_DATA_VALUE"
    ADJUST_SAFE_MARGINS = "ADJUST_SAFE_MARGINS"


class VisualQARecommendation(BaseModel):
    """Actionable recommendation for revising visual treatment."""

    model_config = ConfigDict(frozen=True)

    action: VisualQARecommendationAction
    scene_index: int | None = None
    beat_indices: list[int] = Field(default_factory=list)
    asset_id: str | None = None
    remediation: str


class VisualRenderGateError(RuntimeError):
    """Raised when downstream production or rendering is attempted on an unaccepted visual treatment."""

    def __init__(self, message: str, qa_result: VisualQAResult | None = None) -> None:
        super().__init__(message)
        self.qa_result = qa_result


class VisualQAResult(BaseModel):
    """Comprehensive visual evaluation outcome from Visual Editorial QA."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    scene_index: int | None = None
    status: VisualQAStatus
    highest_severity: VisualQASeverity = VisualQASeverity.INFO
    findings: list[VisualQAFinding] = Field(default_factory=list)
    recommendations: list[VisualQARecommendation] = Field(default_factory=list)
    pre_render_findings: list[VisualQAFinding] = Field(default_factory=list)
    post_render_findings: list[VisualQAFinding] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime = Field(
        default_factory=lambda: datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    )

    @property
    def is_accepted(self) -> bool:
        return self.status == VisualQAStatus.PASS

    @property
    def is_blocked(self) -> bool:
        return self.status == VisualQAStatus.FAIL

    @property
    def requires_revision(self) -> bool:
        return self.status == VisualQAStatus.REVISE

    @property
    def blocker_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == VisualQASeverity.BLOCKER)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == VisualQASeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == VisualQASeverity.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == VisualQASeverity.INFO)
