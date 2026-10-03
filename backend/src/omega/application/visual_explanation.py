"""Grounded planning, validation, layout, and asset adaptation for P22-C."""

from __future__ import annotations

import base64
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from uuid import UUID

from omega.application.mechanism_diagram import resolve_mechanism_diagram_spec
from omega.application.visual_asset_binding import BoundVisualAsset
from omega.application.visual_direction import VisualAssetKind
from omega.domain.channel_dna import VisualStyle
from omega.domain.visual_beat import VisualBeat, VisualRole
from omega.domain.visual_explanation import (
    ChartSpec,
    DataPoint,
    DiagramEdge,
    DiagramLayout,
    DiagramNode,
    DiagramOrientation,
    DiagramSpec,
    EvidenceVisualSpec,
    ExplanationType,
    GeneratedVisualArtifact,
    GroundedEvidenceItem,
    GroundingState,
    LayoutBox,
    VisualExplanationPlan,
    VisualizationFinding,
    VisualizationFindingCode,
    VisualizationStyle,
)

SUPPORTED_DIAGRAM_TYPES = frozenset(
    {
        ExplanationType.PROCESS_FLOW,
        ExplanationType.SYSTEM_DIAGRAM,
        ExplanationType.RELATIONSHIP_MAP,
        ExplanationType.TIMELINE,
    }
)
SUPPORTED_CHART_TYPES = frozenset({ExplanationType.BAR_CHART, ExplanationType.LINE_CHART})
MAX_NODES = 8
MAX_SERIES = 4
MAX_CATEGORIES = 10


def style_from_channel_dna(style: VisualStyle | None) -> VisualizationStyle:
    """Project existing ChannelDNA visual style without creating competing style authority."""
    if style is None:
        return VisualizationStyle()
    colors = [color for color in style.color_preferences if re.fullmatch(r"#[0-9A-Fa-f]{6}", color)]
    fonts = [font.strip() for font in style.font_preferences if font.strip()]
    return VisualizationStyle(
        background=colors[0] if colors else "#0F172A",
        accent=colors[1] if len(colors) > 1 else "#38BDF8",
        foreground=colors[2] if len(colors) > 2 else "#F8FAFC",
        secondary=colors[3] if len(colors) > 3 else "#A78BFA",
        font_family=f"{fonts[0]}, Arial, sans-serif" if fonts else "Inter, Arial, sans-serif",
        dense="DENSE" in style.visual_theme.upper(),
    )


def wrap_label(text: str, *, max_chars: int = 24, max_lines: int = 3) -> tuple[str, ...]:
    """Deterministically wrap labels while enforcing a readable line-count bound."""
    words = " ".join(text.split()).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word[:max_chars]
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return tuple(lines)


class ChartValidator:
    @classmethod
    def validate(
        cls, spec: ChartSpec, *, known_claim_ids: frozenset[UUID] | None = None
    ) -> tuple[VisualizationFinding, ...]:
        findings: list[VisualizationFinding] = []
        if spec.explanation_type not in SUPPORTED_CHART_TYPES:
            findings.append(cls._finding(VisualizationFindingCode.UNSUPPORTED_CHART_TYPE))
        if not spec.data_points:
            findings.append(cls._finding(VisualizationFindingCode.EMPTY_DATASET))
            return tuple(findings)
        numeric: list[float] = []
        for point in spec.data_points:
            try:
                numeric.append(float(point.value))
            except (TypeError, ValueError):
                findings.append(
                    cls._finding(VisualizationFindingCode.NON_NUMERIC_VALUE, point.label)
                )
            if point.grounding_state != GroundingState.VERIFIED:
                findings.append(
                    cls._finding(VisualizationFindingCode.UNVERIFIED_EVIDENCE, point.label)
                )
            if known_claim_ids is not None and point.source_claim_id not in known_claim_ids:
                findings.append(
                    cls._finding(VisualizationFindingCode.UNGROUNDED_DATUM, point.label)
                )
            if " ".join(wrap_label(point.label, max_chars=18, max_lines=2)) != " ".join(
                point.label.split()
            ):
                findings.append(cls._finding(VisualizationFindingCode.TEXT_OVERFLOW, point.label))
        units = {point.unit for point in spec.data_points if point.unit}
        if len(units) > 1:
            findings.append(cls._finding(VisualizationFindingCode.MIXED_INCOMPATIBLE_UNITS))
        categories = [point.category or point.label for point in spec.data_points]
        per_series = Counter(
            (point.series, point.category or point.label) for point in spec.data_points
        )
        if any(count > 1 for count in per_series.values()):
            findings.append(cls._finding(VisualizationFindingCode.DUPLICATE_CATEGORY))
        if len(set(point.series for point in spec.data_points)) > MAX_SERIES:
            findings.append(cls._finding(VisualizationFindingCode.EXCESSIVE_SERIES_COUNT))
        if len(set(categories)) > MAX_CATEGORIES:
            findings.append(cls._finding(VisualizationFindingCode.EXCESSIVE_CATEGORY_COUNT))
        if spec.explanation_type == ExplanationType.LINE_CHART:
            if len(spec.data_points) < 2:
                findings.append(cls._finding(VisualizationFindingCode.INSUFFICIENT_POINTS))
            coordinates = [point.time_coordinate for point in spec.data_points]
            if any(value is None for value in coordinates) or coordinates != sorted(coordinates):
                findings.append(cls._finding(VisualizationFindingCode.INVALID_TIME_ORDER))
        if numeric and spec.zero_baseline is False and min(numeric) > 0:
            findings.append(cls._finding(VisualizationFindingCode.MISLEADING_AXIS_RANGE))
        return tuple(findings)

    @staticmethod
    def _finding(code: VisualizationFindingCode, item: str | None = None) -> VisualizationFinding:
        return VisualizationFinding(
            code=code,
            message=code.value.replace("_", " ").title(),
            item_ids=(item,) if item else (),
        )


class DiagramValidator:
    @classmethod
    def validate(
        cls, spec: DiagramSpec, *, known_claim_ids: frozenset[UUID] | None = None
    ) -> tuple[VisualizationFinding, ...]:
        findings: list[VisualizationFinding] = []
        if spec.explanation_type not in SUPPORTED_DIAGRAM_TYPES:
            findings.append(cls._finding(VisualizationFindingCode.UNSUPPORTED_DIAGRAM_TYPE))
        if not spec.nodes:
            findings.append(cls._finding(VisualizationFindingCode.EMPTY_DIAGRAM))
            return tuple(findings)
        if len(spec.nodes) > MAX_NODES:
            findings.append(cls._finding(VisualizationFindingCode.EXCESSIVE_NODE_COUNT))
        node_ids = {node.id for node in spec.nodes}
        connected: set[str] = set()
        for node in spec.nodes:
            if not node.label.strip():
                findings.append(cls._finding(VisualizationFindingCode.UNRESOLVED_LABEL, node.id))
            if known_claim_ids is not None and any(
                claim_id not in known_claim_ids for claim_id in node.source_claim_ids
            ):
                findings.append(cls._finding(VisualizationFindingCode.UNGROUNDED_DATUM, node.id))
            if " ".join(wrap_label(node.label, max_chars=20, max_lines=3)) != " ".join(
                node.label.split()
            ):
                findings.append(cls._finding(VisualizationFindingCode.TEXT_OVERFLOW, node.id))
        for edge in spec.edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                findings.append(cls._finding(VisualizationFindingCode.INVALID_EDGE))
                continue
            connected.update((edge.source_node_id, edge.target_node_id))
            if edge.source_node_id == edge.target_node_id:
                findings.append(cls._finding(VisualizationFindingCode.SELF_REFERENCE))
            if not edge.source_claim_ids and not edge.explanatory_synthesis:
                findings.append(cls._finding(VisualizationFindingCode.UNGROUNDED_DATUM))
        if len(spec.nodes) > 1:
            for node_id in sorted(node_ids - connected):
                findings.append(cls._finding(VisualizationFindingCode.ORPHAN_NODE, node_id))
        if (
            spec.explanation_type in (ExplanationType.PROCESS_FLOW, ExplanationType.TIMELINE)
            and not spec.edges
        ):
            findings.append(cls._finding(VisualizationFindingCode.AMBIGUOUS_DIRECTION))
        return tuple(findings)

    @staticmethod
    def _finding(code: VisualizationFindingCode, item: str | None = None) -> VisualizationFinding:
        return VisualizationFinding(
            code=code,
            message=code.value.replace("_", " ").title(),
            item_ids=(item,) if item else (),
        )


class DiagramLayoutEngine:
    """Stable bounded 1920x1080 layout for the four supported diagram families."""

    @staticmethod
    def layout(spec: DiagramSpec, *, width: int = 1920, height: int = 1080) -> DiagramLayout:
        if width != 1920 or height != 1080:
            raise ValueError(
                "P22-C physical rendering currently supports canonical 16:9 1920x1080 only"
            )
        ordered = sorted(spec.nodes, key=lambda node: (node.order, node.id))
        if not ordered or len(ordered) > MAX_NODES:
            raise ValueError("diagram node count is outside supported bounds")
        margin_x, top, bottom = 140, 260, 150
        node_w, node_h = 260, 150
        boxes: list[LayoutBox] = []
        if spec.orientation == DiagramOrientation.LEFT_TO_RIGHT:
            available = width - 2 * margin_x - node_w
            step = available / max(1, len(ordered) - 1)
            for index, node in enumerate(ordered):
                boxes.append(
                    LayoutBox(
                        item_id=node.id,
                        x=round(margin_x + index * step),
                        y=480,
                        width=node_w,
                        height=node_h,
                    )
                )
        else:
            available = height - top - bottom - node_h
            step = available / max(1, len(ordered) - 1)
            for index, node in enumerate(ordered):
                boxes.append(
                    LayoutBox(
                        item_id=node.id,
                        x=830,
                        y=round(top + index * step),
                        width=node_w,
                        height=node_h,
                    )
                )
        return DiagramLayout(width=width, height=height, boxes=tuple(boxes))


class VisualExplanationPlanner:
    @classmethod
    def plan(
        cls,
        beat: VisualBeat,
        *,
        data_points: Sequence[DataPoint] = (),
        evidence: Sequence[GroundedEvidenceItem] = (),
        source_brief_id: UUID | None = None,
        aspect_ratio: str = "16:9",
    ) -> VisualExplanationPlan:
        if aspect_ratio != "16:9":
            return cls._fallback(beat, evidence, "UNSUPPORTED_ASPECT_RATIO")
        known_claim_ids = frozenset(item.claim_id for item in evidence)
        if any(point.source_claim_id not in known_claim_ids for point in data_points):
            return cls._fallback(beat, evidence, "UNGROUNDED_DATA_POINT")
        if any(point.grounding_state != GroundingState.VERIFIED for point in data_points) or any(
            item.grounding_state != GroundingState.VERIFIED for item in evidence
        ):
            return cls._fallback(
                beat,
                tuple(item for item in evidence if item.grounding_state == GroundingState.VERIFIED),
                "UNVERIFIED_EVIDENCE",
            )

        lower = f"{beat.information_goal} {beat.narration_text}".lower()
        if data_points:
            has_time = all(point.time_coordinate for point in data_points)
            chart_type = (
                ExplanationType.LINE_CHART
                if has_time and len(data_points) >= 2
                else ExplanationType.BAR_CHART
            )
            if len(data_points) == 1:
                return cls._key_value(beat, data_points[0], evidence)
            spec = ChartSpec(
                id=f"chart-{beat.id}",
                visual_beat_id=beat.id,
                explanation_type=chart_type,
                title=beat.information_goal[:120],
                x_axis_label="Time" if has_time else "Category",
                y_axis_label=data_points[0].unit or "Value",
                data_points=tuple(data_points),
                source_brief_id=source_brief_id,
            )
            findings = ChartValidator.validate(spec, known_claim_ids=known_claim_ids)
            if any(finding.blocking for finding in findings):
                return cls._fallback(beat, evidence, "INVALID_CHART_DATA", findings)
            return VisualExplanationPlan(
                visual_beat_id=beat.id,
                source_editorial_beat_indices=beat.source_editorial_beat_indices,
                explanation_type=chart_type,
                chart_spec=spec,
            )

        mechanism = resolve_mechanism_diagram_spec(beat.narration_text)
        if beat.visual_role == VisualRole.DIAGRAM or mechanism is not None:
            if mechanism is None or not evidence:
                return cls._fallback(beat, evidence, "DIAGRAM_RELATION_NOT_GROUNDED")
            claim_ids = tuple(item.claim_id for item in evidence)
            nodes = tuple(
                DiagramNode(
                    id=f"node-{index}", label=label, order=index, source_claim_ids=claim_ids
                )
                for index, label in enumerate(mechanism.nodes)
            )
            node_by_label = {node.label: node.id for node in nodes}
            edges = tuple(
                DiagramEdge(
                    source_node_id=node_by_label[edge.from_node],
                    target_node_id=node_by_label[edge.to_node],
                    label=edge.label,
                    source_claim_ids=claim_ids,
                )
                for edge in mechanism.edges
            )
            if any(cue in lower for cue in ("timeline", "chronology", "first ")):
                diagram_type = ExplanationType.TIMELINE
            elif any(cue in lower for cue in ("system", "architecture", "component")):
                diagram_type = ExplanationType.SYSTEM_DIAGRAM
            elif any(cue in lower for cue in ("network", "relationship", "graph", "interacting")):
                diagram_type = ExplanationType.RELATIONSHIP_MAP
            else:
                diagram_type = ExplanationType.PROCESS_FLOW
            spec = DiagramSpec(
                id=f"diagram-{beat.id}",
                visual_beat_id=beat.id,
                explanation_type=diagram_type,
                title=beat.information_goal[:120],
                nodes=nodes,
                edges=edges,
                source_brief_id=source_brief_id,
                source_claim_ids=claim_ids,
            )
            findings = DiagramValidator.validate(spec, known_claim_ids=known_claim_ids)
            if any(finding.blocking for finding in findings):
                return cls._fallback(beat, evidence, "INVALID_DIAGRAM", findings)
            return VisualExplanationPlan(
                visual_beat_id=beat.id,
                source_editorial_beat_indices=beat.source_editorial_beat_indices,
                explanation_type=diagram_type,
                diagram_spec=spec,
            )

        if evidence:
            item = evidence[0]
            kind = (
                ExplanationType.QUOTE_CARD if item.direct_quote else ExplanationType.EVIDENCE_CARD
            )
            spec = EvidenceVisualSpec(
                id=f"evidence-{beat.id}",
                visual_beat_id=beat.id,
                explanation_type=kind,
                title=beat.information_goal[:120],
                statement=item.statement,
                direct_quote=item.direct_quote,
                source_label=item.source_label,
                source_claim_id=item.claim_id,
                source_evidence_id=item.evidence_id,
                source_id=item.source_id,
            )
            return VisualExplanationPlan(
                visual_beat_id=beat.id,
                source_editorial_beat_indices=beat.source_editorial_beat_indices,
                explanation_type=kind,
                evidence_spec=spec,
            )
        return cls._fallback(beat, evidence, "NO_GROUNDED_VISUAL_EVIDENCE")

    @classmethod
    def _key_value(
        cls, beat: VisualBeat, point: DataPoint, evidence: Sequence[GroundedEvidenceItem]
    ) -> VisualExplanationPlan:
        item = next(item for item in evidence if item.claim_id == point.source_claim_id)
        spec = EvidenceVisualSpec(
            id=f"key-value-{beat.id}",
            visual_beat_id=beat.id,
            explanation_type=ExplanationType.KEY_VALUE,
            title=beat.information_goal[:120],
            statement=item.statement,
            value=str(point.value),
            unit=point.unit,
            source_label=item.source_label,
            source_claim_id=item.claim_id,
            source_evidence_id=item.evidence_id,
            source_id=item.source_id,
        )
        return VisualExplanationPlan(
            visual_beat_id=beat.id,
            source_editorial_beat_indices=beat.source_editorial_beat_indices,
            explanation_type=ExplanationType.KEY_VALUE,
            evidence_spec=spec,
        )

    @classmethod
    def _fallback(
        cls,
        beat: VisualBeat,
        evidence: Sequence[GroundedEvidenceItem],
        reason: str,
        findings: Iterable[VisualizationFinding] = (),
    ) -> VisualExplanationPlan:
        if evidence:
            item = evidence[0]
            spec = EvidenceVisualSpec(
                id=f"fallback-{beat.id}",
                visual_beat_id=beat.id,
                explanation_type=ExplanationType.EVIDENCE_CARD,
                title=beat.information_goal[:120],
                statement=item.statement,
                source_label=item.source_label,
                source_claim_id=item.claim_id,
                source_evidence_id=item.evidence_id,
                source_id=item.source_id,
            )
        else:
            # A non-factual explanatory label is safe; it contains no invented datum.
            nil = UUID(int=0)
            spec = EvidenceVisualSpec(
                id=f"fallback-{beat.id}",
                visual_beat_id=beat.id,
                explanation_type=ExplanationType.EVIDENCE_CARD,
                title="Explanation unavailable",
                statement="No verified visual evidence is available for this beat.",
                source_label="No sourced data displayed",
                source_claim_id=nil,
                source_evidence_id=nil,
                source_id=nil,
            )
        return VisualExplanationPlan(
            visual_beat_id=beat.id,
            source_editorial_beat_indices=beat.source_editorial_beat_indices,
            explanation_type=ExplanationType.EVIDENCE_CARD,
            evidence_spec=spec,
            findings=tuple(findings),
            fallback_used=True,
            fallback_reason=reason,
        )


class GeneratedVisualAssetAdapter:
    @staticmethod
    def to_bound_visual_asset(artifact: GeneratedVisualArtifact) -> BoundVisualAsset:
        png = artifact.png_path.read_bytes()
        return BoundVisualAsset(
            asset_id=f"generated:{artifact.artifact_id}",
            kind=VisualAssetKind.IMAGE,
            mime_type="image/png",
            content_sha256=artifact.content_sha256,
            data_uri="data:image/png;base64," + base64.b64encode(png).decode("ascii"),
            width=artifact.width,
            height=artifact.height,
        )
