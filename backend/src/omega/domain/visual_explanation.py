"""Renderer-neutral grounded visualization contracts for P22-C."""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExplanationType(enum.StrEnum):
    PROCESS_FLOW = "PROCESS_FLOW"
    SYSTEM_DIAGRAM = "SYSTEM_DIAGRAM"
    RELATIONSHIP_MAP = "RELATIONSHIP_MAP"
    TIMELINE = "TIMELINE"
    COMPARISON = "COMPARISON"
    BAR_CHART = "BAR_CHART"
    LINE_CHART = "LINE_CHART"
    PROPORTION = "PROPORTION"
    EVIDENCE_CARD = "EVIDENCE_CARD"
    QUOTE_CARD = "QUOTE_CARD"
    KEY_VALUE = "KEY_VALUE"
    TABLE_SUMMARY = "TABLE_SUMMARY"


class GroundingState(enum.StrEnum):
    VERIFIED = "VERIFIED"
    UNCERTAIN = "UNCERTAIN"


class DiagramOrientation(enum.StrEnum):
    LEFT_TO_RIGHT = "LEFT_TO_RIGHT"
    TOP_TO_BOTTOM = "TOP_TO_BOTTOM"


class VisualizationFindingCode(enum.StrEnum):
    EMPTY_DATASET = "EMPTY_DATASET"
    NON_NUMERIC_VALUE = "NON_NUMERIC_VALUE"
    MIXED_INCOMPATIBLE_UNITS = "MIXED_INCOMPATIBLE_UNITS"
    INVALID_TIME_ORDER = "INVALID_TIME_ORDER"
    DUPLICATE_CATEGORY = "DUPLICATE_CATEGORY"
    UNSUPPORTED_CHART_TYPE = "UNSUPPORTED_CHART_TYPE"
    INSUFFICIENT_POINTS = "INSUFFICIENT_POINTS"
    MISLEADING_AXIS_RANGE = "MISLEADING_AXIS_RANGE"
    ORPHAN_NODE = "ORPHAN_NODE"
    INVALID_EDGE = "INVALID_EDGE"
    SELF_REFERENCE = "SELF_REFERENCE"
    EMPTY_DIAGRAM = "EMPTY_DIAGRAM"
    UNRESOLVED_LABEL = "UNRESOLVED_LABEL"
    EXCESSIVE_NODE_COUNT = "EXCESSIVE_NODE_COUNT"
    AMBIGUOUS_DIRECTION = "AMBIGUOUS_DIRECTION"
    UNSUPPORTED_DIAGRAM_TYPE = "UNSUPPORTED_DIAGRAM_TYPE"
    TEXT_OVERFLOW = "TEXT_OVERFLOW"
    UNGROUNDED_DATUM = "UNGROUNDED_DATUM"
    UNVERIFIED_EVIDENCE = "UNVERIFIED_EVIDENCE"
    EXCESSIVE_SERIES_COUNT = "EXCESSIVE_SERIES_COUNT"
    EXCESSIVE_CATEGORY_COUNT = "EXCESSIVE_CATEGORY_COUNT"


class VisualizationFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: VisualizationFindingCode
    message: str
    blocking: bool = True
    item_ids: tuple[str, ...] = ()


class DataPoint(BaseModel):
    """Visualization projection of a datum; research entities remain authoritative."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1, max_length=120)
    value: float | str
    unit: str | None = Field(default=None, max_length=32)
    series: str = Field(default="Primary", min_length=1, max_length=80)
    category: str | None = Field(default=None, max_length=120)
    time_coordinate: str | None = Field(default=None, max_length=80)
    source_claim_id: UUID
    source_evidence_id: UUID
    source_id: UUID
    grounding_state: GroundingState = GroundingState.VERIFIED


class GroundedEvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: UUID
    evidence_id: UUID
    source_id: UUID
    statement: str = Field(min_length=1, max_length=1000)
    source_label: str = Field(min_length=1, max_length=160)
    claim_type: str = Field(default="FACT", max_length=40)
    grounding_state: GroundingState = GroundingState.VERIFIED
    direct_quote: bool = False
    explanatory_synthesis_allowed: bool = False


class DiagramNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    group: str | None = Field(default=None, max_length=80)
    order: int = Field(ge=0)
    emphasized: bool = False
    source_claim_ids: tuple[UUID, ...] = ()


class DiagramEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_node_id: str
    target_node_id: str
    label: str | None = Field(default=None, max_length=80)
    explanatory_synthesis: bool = False
    source_claim_ids: tuple[UUID, ...] = ()


class DiagramSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=100)
    visual_beat_id: UUID
    explanation_type: ExplanationType
    title: str = Field(min_length=1, max_length=120)
    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...] = ()
    orientation: DiagramOrientation = DiagramOrientation.LEFT_TO_RIGHT
    source_brief_id: UUID | None = None
    source_claim_ids: tuple[UUID, ...] = ()


class ChartSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=100)
    visual_beat_id: UUID
    explanation_type: ExplanationType
    title: str = Field(min_length=1, max_length=120)
    x_axis_label: str = Field(default="", max_length=80)
    y_axis_label: str = Field(default="", max_length=80)
    data_points: tuple[DataPoint, ...]
    zero_baseline: bool = True
    emphasized_labels: tuple[str, ...] = ()
    source_brief_id: UUID | None = None


class EvidenceVisualSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    visual_beat_id: UUID
    explanation_type: ExplanationType
    title: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=420)
    direct_quote: bool = False
    value: str | None = Field(default=None, max_length=80)
    unit: str | None = Field(default=None, max_length=32)
    source_label: str = Field(min_length=1, max_length=160)
    source_claim_id: UUID
    source_evidence_id: UUID
    source_id: UUID


class VisualExplanationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    visual_beat_id: UUID
    source_editorial_beat_indices: tuple[int, ...]
    explanation_type: ExplanationType
    diagram_spec: DiagramSpec | None = None
    chart_spec: ChartSpec | None = None
    evidence_spec: EvidenceVisualSpec | None = None
    findings: tuple[VisualizationFinding, ...] = ()
    fallback_used: bool = False
    fallback_reason: str | None = None

    @model_validator(mode="after")
    def exactly_one_spec(self) -> VisualExplanationPlan:
        if (
            sum(x is not None for x in (self.diagram_spec, self.chart_spec, self.evidence_spec))
            != 1
        ):
            raise ValueError(
                "visual explanation plan must contain exactly one render specification"
            )
        return self


class LayoutBox(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class DiagramLayout(BaseModel):
    model_config = ConfigDict(frozen=True)

    width: int = 1920
    height: int = 1080
    boxes: tuple[LayoutBox, ...]


class VisualizationStyle(BaseModel):
    model_config = ConfigDict(frozen=True)

    background: str = "#0F172A"
    foreground: str = "#F8FAFC"
    accent: str = "#38BDF8"
    secondary: str = "#A78BFA"
    font_family: str = "Inter, Arial, sans-serif"
    dense: bool = False


class VisualArtifactProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    visual_beat_id: UUID
    spec_id: str
    source_brief_id: UUID | None = None
    source_claim_ids: tuple[UUID, ...]
    source_evidence_ids: tuple[UUID, ...]
    source_ids: tuple[UUID, ...]
    generator_version: str
    generated_at: datetime


class GeneratedVisualArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str
    explanation_type: ExplanationType
    svg_path: Path
    png_path: Path
    width: int
    height: int
    mime_type: str = "image/png"
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: VisualArtifactProvenance
    metadata: dict[str, Any] = Field(default_factory=dict)
