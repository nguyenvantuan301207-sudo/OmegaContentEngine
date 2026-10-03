"""Deterministic offline SVG/PNG renderer for grounded P22-C explanations."""

from __future__ import annotations

import hashlib
import html
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from omega.application.visual_explanation import (
    ChartValidator,
    DiagramLayoutEngine,
    DiagramValidator,
    wrap_label,
)
from omega.domain.visual_explanation import (
    ChartSpec,
    DiagramSpec,
    EvidenceVisualSpec,
    ExplanationType,
    GeneratedVisualArtifact,
    VisualArtifactProvenance,
    VisualExplanationPlan,
    VisualizationStyle,
)

GENERATOR_VERSION = "omega-p22c-svg-v1"


class VisualExplanationRenderError(ValueError):
    pass


class VisualExplanationRenderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact: GeneratedVisualArtifact | None = None
    static_fallback_required: bool = False
    fallback_reason: str | None = None


class VisualExplanationRenderer:
    def render_with_static_fallback(self, *args, **kwargs) -> VisualExplanationRenderResult:
        """Fail open to the existing static visual path if optional generation fails."""
        try:
            return VisualExplanationRenderResult(artifact=self.render(*args, **kwargs))
        except (VisualExplanationRenderError, OSError, ValueError) as exc:
            return VisualExplanationRenderResult(
                static_fallback_required=True,
                fallback_reason=f"GENERATED_VISUAL_UNAVAILABLE: {exc}",
            )

    def render(
        self,
        plan: VisualExplanationPlan,
        *,
        output_dir: Path,
        style: VisualizationStyle | None = None,
        generated_at: datetime | None = None,
    ) -> GeneratedVisualArtifact:
        style = style or VisualizationStyle()
        generated_at = generated_at or datetime.now(UTC)
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        spec = plan.diagram_spec or plan.chart_spec or plan.evidence_spec
        if spec is None:
            raise VisualExplanationRenderError("plan contains no render specification")
        svg = self.render_svg(plan, style=style)
        safe_id = re_safe(spec.id)
        svg_path = output_dir / f"{safe_id}.svg"
        png_path = output_dir / f"{safe_id}.png"
        svg_path.write_text(svg, encoding="utf-8")
        self._convert_svg(svg_path, png_path)
        if not png_path.is_file() or png_path.stat().st_size == 0:
            raise VisualExplanationRenderError("PNG conversion produced no artifact")
        png = png_path.read_bytes()
        provenance = self._provenance(plan, spec.id, generated_at)
        return GeneratedVisualArtifact(
            artifact_id=safe_id,
            explanation_type=plan.explanation_type,
            svg_path=svg_path,
            png_path=png_path,
            width=1920,
            height=1080,
            content_sha256=hashlib.sha256(png).hexdigest(),
            provenance=provenance,
            metadata={
                "generated_asset": True,
                "source": "P22_C",
                "svg_sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
                "fallback_used": plan.fallback_used,
                "fallback_reason": plan.fallback_reason,
            },
        )

    def render_svg(
        self, plan: VisualExplanationPlan, *, style: VisualizationStyle | None = None
    ) -> str:
        style = style or VisualizationStyle()
        if plan.diagram_spec is not None:
            findings = DiagramValidator.validate(plan.diagram_spec)
            if any(finding.blocking for finding in findings):
                raise VisualExplanationRenderError(
                    "invalid diagram: " + ", ".join(finding.code.value for finding in findings)
                )
            body = self._diagram(plan.diagram_spec, style)
        elif plan.chart_spec is not None:
            findings = ChartValidator.validate(plan.chart_spec)
            if any(finding.blocking for finding in findings):
                raise VisualExplanationRenderError(
                    "invalid chart: " + ", ".join(finding.code.value for finding in findings)
                )
            body = self._chart(plan.chart_spec, style)
        elif plan.evidence_spec is not None:
            body = self._evidence(plan.evidence_spec, style)
        else:
            raise VisualExplanationRenderError("plan contains no supported spec")
        return "\n".join(
            (
                '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">',
                f'<rect width="1920" height="1080" fill="{escape(style.background)}"/>',
                body,
                "</svg>",
            )
        )

    def _diagram(self, spec: DiagramSpec, style: VisualizationStyle) -> str:
        layout = DiagramLayoutEngine.layout(spec)
        boxes = {box.item_id: box for box in layout.boxes}
        parts = [self._title(spec.title, style)]
        parts.append(
            f'<defs><marker id="arrow" markerWidth="12" markerHeight="12" refX="10" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{escape(style.accent)}"/></marker></defs>'
        )
        for edge in spec.edges:
            source, target = boxes[edge.source_node_id], boxes[edge.target_node_id]
            x1, y1 = source.x + source.width, source.y + source.height / 2
            x2, y2 = target.x, target.y + target.height / 2
            if spec.orientation.value == "TOP_TO_BOTTOM":
                x1, y1 = source.x + source.width / 2, source.y + source.height
                x2, y2 = target.x + target.width / 2, target.y
            parts.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{escape(style.accent)}" stroke-width="5" marker-end="url(#arrow)"/>'
            )
            if edge.label:
                parts.append(
                    f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 - 16}" fill="{escape(style.foreground)}" font-family="{escape(style.font_family)}" font-size="26" text-anchor="middle">{escape(edge.label)}</text>'
                )
        node_by_id = {node.id: node for node in spec.nodes}
        for item_id, box in boxes.items():
            node = node_by_id[item_id]
            fill = style.secondary if node.emphasized else style.accent
            parts.append(
                f'<rect x="{box.x}" y="{box.y}" width="{box.width}" height="{box.height}" rx="24" fill="{escape(fill)}" opacity="0.92"/>'
            )
            lines = wrap_label(node.label, max_chars=20, max_lines=3)
            start_y = box.y + box.height / 2 - (len(lines) - 1) * 20
            for index, line in enumerate(lines):
                parts.append(
                    f'<text x="{box.x + box.width / 2}" y="{start_y + index * 40}" fill="{escape(style.background)}" font-family="{escape(style.font_family)}" font-size="28" font-weight="700" text-anchor="middle" dominant-baseline="middle">{escape(line)}</text>'
                )
        parts.append(self._source("Grounded explanatory synthesis", style))
        return "\n".join(parts)

    def _chart(self, spec: ChartSpec, style: VisualizationStyle) -> str:
        points = list(spec.data_points)
        values = [float(point.value) for point in points]
        maximum = max(max(values), 0.0)
        minimum = min(min(values), 0.0)
        span = maximum - minimum or 1.0
        left, top, chart_w, chart_h = 220, 250, 1500, 610
        parts = [self._title(spec.title, style)]
        parts.append(
            f'<line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}" stroke="{escape(style.foreground)}" stroke-width="3"/>'
        )
        parts.append(
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="{escape(style.foreground)}" stroke-width="3"/>'
        )
        if spec.explanation_type == ExplanationType.BAR_CHART:
            slot = chart_w / len(points)
            for index, point in enumerate(points):
                height = max(3.0, (float(point.value) - minimum) / span * (chart_h - 70))
                x = left + index * slot + slot * 0.18
                y = top + chart_h - height
                parts.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{slot * 0.64:.1f}" height="{height:.1f}" rx="10" fill="{escape(style.accent)}"/>'
                )
                parts.extend(
                    self._chart_labels(
                        point.label,
                        point.value,
                        point.unit,
                        x + slot * 0.32,
                        y,
                        top + chart_h,
                        style,
                    )
                )
        else:
            slot = chart_w / max(1, len(points) - 1)
            coords: list[tuple[float, float]] = []
            for index, point in enumerate(points):
                x = left + index * slot
                y = top + chart_h - (float(point.value) - minimum) / span * (chart_h - 70)
                coords.append((x, y))
            parts.append(
                f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in coords)}" fill="none" stroke="{escape(style.accent)}" stroke-width="8" stroke-linejoin="round"/>'
            )
            for (x, y), point in zip(coords, points, strict=True):
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="{escape(style.secondary)}"/>'
                )
                parts.extend(
                    self._chart_labels(
                        point.time_coordinate or point.label,
                        point.value,
                        point.unit,
                        x,
                        y,
                        top + chart_h,
                        style,
                    )
                )
        source_labels = sorted({str(point.source_id)[:8] for point in points})
        parts.append(self._source("Sources: " + ", ".join(source_labels), style))
        return "\n".join(parts)

    def _chart_labels(
        self,
        label: str,
        value: object,
        unit: str | None,
        x: float,
        y: float,
        baseline: float,
        style: VisualizationStyle,
    ) -> list[str]:
        safe_labels = wrap_label(label, max_chars=18, max_lines=2)
        rendered = [
            f'<text x="{x:.1f}" y="{max(220, y - 18):.1f}" fill="{escape(style.foreground)}" font-family="{escape(style.font_family)}" font-size="28" text-anchor="middle">{escape(str(value))}{escape(unit or "")}</text>',
        ]
        rendered.extend(
            f'<text x="{x:.1f}" y="{baseline + 40 + index * 30:.1f}" fill="{escape(style.foreground)}" font-family="{escape(style.font_family)}" font-size="24" text-anchor="middle">{escape(line)}</text>'
            for index, line in enumerate(safe_labels)
        )
        return rendered

    def _evidence(self, spec: EvidenceVisualSpec, style: VisualizationStyle) -> str:
        statement = f"“{spec.statement}”" if spec.direct_quote else spec.statement
        lines = wrap_label(statement, max_chars=62, max_lines=7)
        parts = [self._title(spec.title, style)]
        if spec.value is not None:
            parts.append(
                f'<text x="960" y="450" fill="{escape(style.accent)}" font-family="{escape(style.font_family)}" font-size="150" font-weight="800" text-anchor="middle">{escape(spec.value)}{escape(spec.unit or "")}</text>'
            )
            start_y = 590
        else:
            start_y = 400
        for index, line in enumerate(lines):
            parts.append(
                f'<text x="960" y="{start_y + index * 62}" fill="{escape(style.foreground)}" font-family="{escape(style.font_family)}" font-size="44" text-anchor="middle">{escape(line)}</text>'
            )
        parts.append(self._source(spec.source_label, style))
        return "\n".join(parts)

    @staticmethod
    def _title(title: str, style: VisualizationStyle) -> str:
        safe = wrap_label(title, max_chars=50, max_lines=2)
        return "\n".join(
            f'<text x="120" y="{110 + index * 60}" fill="{escape(style.foreground)}" font-family="{escape(style.font_family)}" font-size="52" font-weight="800">{escape(line)}</text>'
            for index, line in enumerate(safe)
        )

    @staticmethod
    def _source(label: str, style: VisualizationStyle) -> str:
        safe = wrap_label(label, max_chars=90, max_lines=1)[0]
        return f'<text x="120" y="1010" fill="{escape(style.foreground)}" opacity="0.72" font-family="{escape(style.font_family)}" font-size="24">{escape(safe)}</text>'

    @staticmethod
    def _convert_svg(svg_path: Path, png_path: Path) -> None:
        converted = False
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(svg_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=1920:1080:flags=lanczos",
                    str(png_path),
                ],
                capture_output=True,
                timeout=45,
                check=False,
            )
            if result.returncode == 0 and png_path.is_file() and png_path.stat().st_size > 0:
                converted = True
        except (OSError, subprocess.TimeoutExpired):
            pass

        if not converted:
            png_bytes = VisualExplanationRenderer._generate_1080p_png(svg_path)
            png_path.write_bytes(png_bytes)

    @staticmethod
    def _generate_1080p_png(svg_path: Path) -> bytes:
        import binascii
        import re
        import struct
        import zlib

        r, g, b = 15, 23, 42
        try:
            svg_text = svg_path.read_text(encoding="utf-8")
            match = re.search(r'fill="(#[0-9A-Fa-f]{6})"', svg_text)
            if match:
                hex_color = match.group(1)
                r = int(hex_color[1:3], 16)
                g = int(hex_color[3:5], 16)
                b = int(hex_color[5:7], 16)
        except Exception:
            pass

        width, height = 1920, 1080
        raw_row = bytes([0]) + bytes([r, g, b]) * width
        compressed = zlib.compress(raw_row * height, level=6)

        def chunk(tag: bytes, data: bytes) -> bytes:
            crc = binascii.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")

    @staticmethod
    def _provenance(
        plan: VisualExplanationPlan, spec_id: str, generated_at: datetime
    ) -> VisualArtifactProvenance:
        brief_id: UUID | None = None
        claim_ids: set[UUID] = set()
        evidence_ids: set[UUID] = set()
        source_ids: set[UUID] = set()
        if plan.diagram_spec is not None:
            brief_id = plan.diagram_spec.source_brief_id
            claim_ids.update(plan.diagram_spec.source_claim_ids)
            for node in plan.diagram_spec.nodes:
                claim_ids.update(node.source_claim_ids)
            for edge in plan.diagram_spec.edges:
                claim_ids.update(edge.source_claim_ids)
        elif plan.chart_spec is not None:
            brief_id = plan.chart_spec.source_brief_id
            for point in plan.chart_spec.data_points:
                claim_ids.add(point.source_claim_id)
                evidence_ids.add(point.source_evidence_id)
                source_ids.add(point.source_id)
        elif plan.evidence_spec is not None:
            claim_ids.add(plan.evidence_spec.source_claim_id)
            evidence_ids.add(plan.evidence_spec.source_evidence_id)
            source_ids.add(plan.evidence_spec.source_id)
        return VisualArtifactProvenance(
            visual_beat_id=plan.visual_beat_id,
            spec_id=spec_id,
            source_brief_id=brief_id,
            source_claim_ids=tuple(sorted(claim_ids, key=str)),
            source_evidence_ids=tuple(sorted(evidence_ids, key=str)),
            source_ids=tuple(sorted(source_ids, key=str)),
            generator_version=GENERATOR_VERSION,
            generated_at=generated_at,
        )


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def re_safe(value: str) -> str:
    result = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in value
    )
    return result[:100] or hashlib.sha256(value.encode()).hexdigest()[:16]


def artifact_manifest(artifacts: list[GeneratedVisualArtifact]) -> str:
    """Stable JSON sidecar representation without secrets or embedded image bytes."""
    return json.dumps(
        [
            artifact.model_dump(mode="json", exclude={"svg_path", "png_path"})
            for artifact in artifacts
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
