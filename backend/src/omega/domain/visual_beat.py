"""Domain models for P22-A Visual Continuity & Editorial Beat Architecture.

Defines the typed VisualBeat representation, VisualRole taxonomy,
continuity decisions, asset reuse policies, continuity motifs/groups,
document and comparison continuity states, and continuity diagnostic findings.
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from omega.domain.narrative_plan import NarrativeSectionRole


class VisualRole(enum.StrEnum):
    """Editorial purpose classification for visual content."""

    ESTABLISH = "ESTABLISH"
    EXPLAIN = "EXPLAIN"
    EVIDENCE = "EVIDENCE"
    COMPARE = "COMPARE"
    EMPHASIZE = "EMPHASIZE"
    REVEAL = "REVEAL"
    CONTEXTUALIZE = "CONTEXTUALIZE"
    DOCUMENT = "DOCUMENT"
    DATA = "DATA"
    DIAGRAM = "DIAGRAM"
    BROLL = "BROLL"
    QUOTE = "QUOTE"
    SUMMARY = "SUMMARY"
    CTA = "CTA"


class ContinuityDecisionType(enum.StrEnum):
    """Editorial continuity directives across adjacent visual beats."""

    KEEP = "KEEP"
    REUSE = "REUSE"
    REFRAME_LATER = "REFRAME_LATER"
    REPLACE = "REPLACE"
    RETURN_TO_MOTIF = "RETURN_TO_MOTIF"
    PROGRESS_DOCUMENT = "PROGRESS_DOCUMENT"
    PROGRESS_DIAGRAM = "PROGRESS_DIAGRAM"
    SWITCH_CONTEXT = "SWITCH_CONTEXT"


class AssetReusePolicy(enum.StrEnum):
    """Explicit asset reuse semantics."""

    INTENTIONAL_REUSE = "INTENTIONAL_REUSE"
    ACCIDENTAL_REPEAT = "ACCIDENTAL_REPEAT"
    CONCEPTUAL_VARIATION = "CONCEPTUAL_VARIATION"
    CONTINUITY_ANCHOR = "CONTINUITY_ANCHOR"
    CALLBACK_VISUAL = "CALLBACK_VISUAL"
    NEW_ACQUISITION = "NEW_ACQUISITION"


class DocumentProgressStage(enum.StrEnum):
    """Progressive visual stages for primary evidence/document inspection."""

    OVERVIEW = "OVERVIEW"
    SECTION = "SECTION"
    DETAIL = "DETAIL"
    RETURN_TO_CONTEXT = "RETURN_TO_CONTEXT"


class ComparisonSide(enum.StrEnum):
    """Spatial placement assignment for comparative entities."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"
    CENTER = "CENTER"


class ContinuityFindingCode(enum.StrEnum):
    """Diagnostic codes for visual continuity defects."""

    BROKEN_SUBJECT_CONTINUITY = "BROKEN_SUBJECT_CONTINUITY"
    ACCIDENTAL_ASSET_REPEAT = "ACCIDENTAL_ASSET_REPEAT"
    EXCESSIVE_VISUAL_HOLD = "EXCESSIVE_VISUAL_HOLD"
    EXCESSIVE_VISUAL_CHURN = "EXCESSIVE_VISUAL_CHURN"
    MISSING_VISUAL_PROGRESS = "MISSING_VISUAL_PROGRESS"
    COMPARISON_SIDE_SWAP = "COMPARISON_SIDE_SWAP"
    DOCUMENT_CONTEXT_LOST = "DOCUMENT_CONTEXT_LOST"
    MOTIF_DRIFT = "MOTIF_DRIFT"
    VISUAL_INTENT_MISMATCH = "VISUAL_INTENT_MISMATCH"
    VISUAL_HOLD_TOO_LONG = "VISUAL_HOLD_TOO_LONG"
    VISUAL_CUT_TOO_FAST = "VISUAL_CUT_TOO_FAST"
    INSUFFICIENT_VISUAL_CHANGE = "INSUFFICIENT_VISUAL_CHANGE"


class ContinuityFindingSeverity(enum.StrEnum):
    """Severity levels for visual continuity findings."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ContinuityFinding(BaseModel):
    """Diagnostic finding regarding visual progression or continuity."""

    code: ContinuityFindingCode
    severity: ContinuityFindingSeverity
    explanation: str
    affected_beat_indices: list[int] = Field(default_factory=list)
    remediation: str | None = None

    model_config = ConfigDict(frozen=True)


class ContinuityGroup(BaseModel):
    """Explicit continuity motif or group binding related visual beats together."""

    id: str = Field(description="Unique motif identifier (e.g. 'motif_raft_architecture')")
    motif_type: str = Field(description="Semantic type: ENTITY, MAP, DOCUMENT, DIAGRAM, COMPARISON")
    primary_subject: str
    active_section_orders: list[int] = Field(default_factory=list)
    anchor_asset_id: str | None = None

    model_config = ConfigDict(frozen=True)


class DocumentContinuityState(BaseModel):
    """Tracks document-level evidence drill-down state across beats."""

    document_id: str
    document_title: str
    current_stage: DocumentProgressStage
    page_or_section: str | None = None
    target_quote_or_data: str | None = None

    model_config = ConfigDict(frozen=True)


class ComparisonContinuityState(BaseModel):
    """Tracks stable entity-to-side mapping for comparison narratives."""

    comparison_id: str
    entity_a: str
    entity_b: str
    side_a: ComparisonSide = ComparisonSide.LEFT
    side_b: ComparisonSide = ComparisonSide.RIGHT

    model_config = ConfigDict(frozen=True)


class VisualBeat(BaseModel):
    """Visual continuity projection of one materialized editorial beat interval.

    EditorialBeatSpec remains authoritative for semantic segmentation and timing.
    A VisualBeat adds visual-role and continuity information while retaining the
    complete set of source editorial beat indices.
    """

    id: UUID = Field(default_factory=uuid4)
    scene_id: str | int = Field(description="Parent scene sequence identifier")
    parent_scene_index: int = Field(ge=1, description="1-indexed sequence order of parent scene")
    beat_index: int = Field(ge=0, description="0-indexed position within the parent scene")
    source_editorial_beat_indices: tuple[int, ...] = Field(
        default_factory=tuple,
        description="Ordered source EditorialBeatSpec indices represented by this visual interval",
    )

    start_offset_ms: int = Field(ge=0, description="Start millisecond offset relative to scene start")
    end_offset_ms: int = Field(ge=0, description="End millisecond offset relative to scene start")
    duration_ms: int = Field(ge=0, description="Duration of this beat in milliseconds")

    narrative_section_role: NarrativeSectionRole | None = Field(
        default=None, description="Semantic role from NarrativePlan section"
    )
    script_statement_ids: list[int] = Field(
        default_factory=list, description="IDs of script statements driving this beat"
    )
    narration_text: str = Field(default="", description="Verbatim narration span covered by this beat")

    visual_intent: str = Field(description="What the viewer should see and understand")
    information_goal: str = Field(description="Core explanation or evidentiary point delivered")
    visual_role: VisualRole = Field(description="Editorial purpose of the visual")

    continuity_group_id: str | None = Field(
        default=None, description="Explicit continuity motif binding across beats"
    )
    preferred_asset_type: str = Field(
        default="IMAGE", description="IMAGE, BROLL, SCREENSHOT, DIAGRAM, DATA_CARD, DOCUMENT"
    )
    asset_reuse_policy: AssetReusePolicy = Field(
        default=AssetReusePolicy.NEW_ACQUISITION,
        description="Explicit policy for asset persistence or re-acquisition",
    )
    asset_query_hint: str | None = Field(
        default=None, description="Search query hint for external asset acquisition if needed"
    )

    text_overlay_intent: str | None = Field(
        default=None, description="Proposed on-screen graphic/text label"
    )
    grounding_references: list[dict[str, Any]] = Field(
        default_factory=list, description="Claim citations and research provenance backing this beat"
    )
    importance: str = Field(default="NORMAL", description="LOW, NORMAL, HIGH, CRITICAL")

    continuity_decision: ContinuityDecisionType | None = Field(
        default=None, description="Continuity decision evaluated relative to previous beat"
    )

    model_config = ConfigDict(frozen=True)


class VisualBeatSequence(BaseModel):
    """Complete sequence of planned visual beats for a Storyboard scene."""

    parent_scene_index: int = Field(ge=1)
    scene_id: str | int
    beats: list[VisualBeat] = Field(default_factory=list)
    total_duration_ms: int = Field(ge=0)
    continuity_findings: list[ContinuityFinding] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)

    @property
    def beat_count(self) -> int:
        return len(self.beats)
