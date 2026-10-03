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
                    "-pix_fmt",
                    "rgb24",
                    str(png_path),
                ],
                capture_output=True,
                timeout=45,
                check=False,
            )
            if result.returncode == 0 and png_path.is_file() and png_path.stat().st_size > 1000:
                converted = True
        except (OSError, subprocess.TimeoutExpired):
            pass

        if not converted:
            svg_text = svg_path.read_text(encoding="utf-8")
            png_bytes = VisualExplanationRenderer._rasterize_svg_content(svg_text)
            png_path.write_bytes(png_bytes)

    @staticmethod
    def _rasterize_svg_content(svg_text: str, width: int = 1920, height: int = 1080) -> bytes:
        """Deterministically rasterizes SVG diagram and chart elements into genuine physical pixels."""
        import binascii
        import re
        import struct
        import xml.etree.ElementTree as ET
        import zlib
        import numpy as np

        font_8x8 = {
            ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            '!': [0x18, 0x18, 0x18, 0x18, 0x18, 0x00, 0x18, 0x00],
            '"': [0x66, 0x66, 0x24, 0x00, 0x00, 0x00, 0x00, 0x00],
            '#': [0x6c, 0x6c, 0xfe, 0x6c, 0xfe, 0x6c, 0x6c, 0x00],
            '$': [0x18, 0x7e, 0x18, 0x3e, 0x60, 0x3c, 0x18, 0x00],
            '%': [0x00, 0x63, 0x66, 0x0c, 0x18, 0x33, 0x63, 0x00],
            '&': [0x38, 0x6c, 0x38, 0x76, 0xdc, 0xcc, 0x76, 0x00],
            "'": [0x18, 0x18, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00],
            '(': [0x0c, 0x18, 0x30, 0x30, 0x30, 0x18, 0x0c, 0x00],
            ')': [0x30, 0x18, 0x0c, 0x0c, 0x0c, 0x18, 0x30, 0x00],
            '*': [0x00, 0x66, 0x3c, 0xff, 0x3c, 0x66, 0x00, 0x00],
            '+': [0x00, 0x18, 0x18, 0x7e, 0x18, 0x18, 0x00, 0x00],
            ',': [0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x30],
            '-': [0x00, 0x00, 0x00, 0x7e, 0x00, 0x00, 0x00, 0x00],
            '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00],
            '/': [0x06, 0x0c, 0x18, 0x30, 0x60, 0xc0, 0x80, 0x00],
            '0': [0x3c, 0x66, 0x6e, 0x76, 0x66, 0x66, 0x3c, 0x00],
            '1': [0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x7e, 0x00],
            '2': [0x3c, 0x66, 0x06, 0x0c, 0x18, 0x30, 0x7e, 0x00],
            '3': [0x3c, 0x66, 0x06, 0x1c, 0x06, 0x66, 0x3c, 0x00],
            '4': [0x0c, 0x1c, 0x3c, 0x6c, 0xfe, 0x0c, 0x0c, 0x00],
            '5': [0x7e, 0x60, 0x7c, 0x06, 0x06, 0x66, 0x3c, 0x00],
            '6': [0x3c, 0x66, 0x60, 0x7c, 0x66, 0x66, 0x3c, 0x00],
            '7': [0x7e, 0x66, 0x0c, 0x18, 0x18, 0x18, 0x18, 0x00],
            '8': [0x3c, 0x66, 0x66, 0x3c, 0x66, 0x66, 0x3c, 0x00],
            '9': [0x3c, 0x66, 0x66, 0x3e, 0x06, 0x66, 0x3c, 0x00],
            ':': [0x00, 0x18, 0x18, 0x00, 0x18, 0x18, 0x00, 0x00],
            ';': [0x00, 0x18, 0x18, 0x00, 0x18, 0x18, 0x30, 0x00],
            '<': [0x0c, 0x18, 0x30, 0x60, 0x30, 0x18, 0x0c, 0x00],
            '=': [0x00, 0x7e, 0x00, 0x7e, 0x00, 0x00, 0x00, 0x00],
            '>': [0x30, 0x18, 0x0c, 0x06, 0x0c, 0x18, 0x30, 0x00],
            '?': [0x3c, 0x66, 0x06, 0x0c, 0x18, 0x00, 0x18, 0x00],
            '@': [0x3c, 0x66, 0x6e, 0x6a, 0x6e, 0x60, 0x3c, 0x00],
            'A': [0x3c, 0x66, 0x66, 0x7e, 0x66, 0x66, 0x66, 0x00],
            'B': [0x7c, 0x66, 0x66, 0x7c, 0x66, 0x66, 0x7c, 0x00],
            'C': [0x3c, 0x66, 0x60, 0x60, 0x60, 0x66, 0x3c, 0x00],
            'D': [0x78, 0x6c, 0x66, 0x66, 0x66, 0x6c, 0x78, 0x00],
            'E': [0x7e, 0x60, 0x60, 0x7c, 0x60, 0x60, 0x7e, 0x00],
            'F': [0x7e, 0x60, 0x60, 0x7c, 0x60, 0x60, 0x60, 0x00],
            'G': [0x3c, 0x66, 0x60, 0x6e, 0x66, 0x66, 0x3a, 0x00],
            'H': [0x66, 0x66, 0x66, 0x7e, 0x66, 0x66, 0x66, 0x00],
            'I': [0x3c, 0x18, 0x18, 0x18, 0x18, 0x18, 0x3c, 0x00],
            'J': [0x0e, 0x06, 0x06, 0x06, 0x06, 0x66, 0x3c, 0x00],
            'K': [0x66, 0x6c, 0x78, 0x70, 0x78, 0x6c, 0x66, 0x00],
            'L': [0x60, 0x60, 0x60, 0x60, 0x60, 0x60, 0x7e, 0x00],
            'M': [0x63, 0x77, 0x7f, 0x6b, 0x63, 0x63, 0x63, 0x00],
            'N': [0x66, 0x76, 0x7e, 0x7e, 0x6e, 0x66, 0x66, 0x00],
            'O': [0x3c, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3c, 0x00],
            'P': [0x7c, 0x66, 0x66, 0x7c, 0x60, 0x60, 0x60, 0x00],
            'Q': [0x3c, 0x66, 0x66, 0x66, 0x6a, 0x6c, 0x36, 0x00],
            'R': [0x7c, 0x66, 0x66, 0x7c, 0x6c, 0x66, 0x66, 0x00],
            'S': [0x3c, 0x66, 0x60, 0x3c, 0x06, 0x66, 0x3c, 0x00],
            'T': [0x7e, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x00],
            'U': [0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3c, 0x00],
            'V': [0x66, 0x66, 0x66, 0x66, 0x66, 0x3c, 0x18, 0x00],
            'W': [0x63, 0x63, 0x63, 0x6b, 0x7f, 0x77, 0x63, 0x00],
            'X': [0x66, 0x66, 0x3c, 0x18, 0x3c, 0x66, 0x66, 0x00],
            'Y': [0x66, 0x66, 0x66, 0x3c, 0x18, 0x18, 0x18, 0x00],
            'Z': [0x7e, 0x06, 0x0c, 0x18, 0x30, 0x60, 0x7e, 0x00],
            '[': [0x3c, 0x30, 0x30, 0x30, 0x30, 0x30, 0x3c, 0x00],
            '\\': [0x60, 0x30, 0x18, 0x0c, 0x06, 0x03, 0x01, 0x00],
            ']': [0x3c, 0x0c, 0x0c, 0x0c, 0x0c, 0x0c, 0x3c, 0x00],
            '^': [0x18, 0x3c, 0x66, 0x00, 0x00, 0x00, 0x00, 0x00],
            '_': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff],
            '`': [0x30, 0x18, 0x0c, 0x00, 0x00, 0x00, 0x00, 0x00],
            'a': [0x00, 0x00, 0x3c, 0x06, 0x3e, 0x66, 0x3e, 0x00],
            'b': [0x60, 0x60, 0x7c, 0x66, 0x66, 0x66, 0x7c, 0x00],
            'c': [0x00, 0x00, 0x3c, 0x66, 0x60, 0x66, 0x3c, 0x00],
            'd': [0x06, 0x06, 0x3e, 0x66, 0x66, 0x66, 0x3e, 0x00],
            'e': [0x00, 0x00, 0x3c, 0x66, 0x7e, 0x60, 0x3c, 0x00],
            'f': [0x1c, 0x30, 0x30, 0x7c, 0x30, 0x30, 0x30, 0x00],
            'g': [0x00, 0x00, 0x3e, 0x66, 0x66, 0x3e, 0x06, 0x3c],
            'h': [0x60, 0x60, 0x7c, 0x66, 0x66, 0x66, 0x66, 0x00],
            'i': [0x18, 0x00, 0x38, 0x18, 0x18, 0x18, 0x3c, 0x00],
            'j': [0x0c, 0x00, 0x1c, 0x0c, 0x0c, 0x0c, 0x6c, 0x38],
            'k': [0x60, 0x60, 0x66, 0x6c, 0x78, 0x6c, 0x66, 0x00],
            'l': [0x38, 0x18, 0x18, 0x18, 0x18, 0x18, 0x3c, 0x00],
            'm': [0x00, 0x00, 0x76, 0x7f, 0x6b, 0x6b, 0x6b, 0x00],
            'n': [0x00, 0x00, 0x7c, 0x66, 0x66, 0x66, 0x66, 0x00],
            'o': [0x00, 0x00, 0x3c, 0x66, 0x66, 0x66, 0x3c, 0x00],
            'p': [0x00, 0x00, 0x7c, 0x66, 0x66, 0x7c, 0x60, 0x60],
            'q': [0x00, 0x00, 0x3e, 0x66, 0x66, 0x3e, 0x06, 0x06],
            'r': [0x00, 0x00, 0x7c, 0x66, 0x60, 0x60, 0x60, 0x00],
            's': [0x00, 0x00, 0x3e, 0x60, 0x3c, 0x06, 0x7c, 0x00],
            't': [0x18, 0x18, 0x7e, 0x18, 0x18, 0x18, 0x0e, 0x00],
            'u': [0x00, 0x00, 0x66, 0x66, 0x66, 0x66, 0x3e, 0x00],
            'v': [0x00, 0x00, 0x66, 0x66, 0x66, 0x3c, 0x18, 0x00],
            'w': [0x00, 0x00, 0x63, 0x6b, 0x7f, 0x77, 0x63, 0x00],
            'x': [0x00, 0x00, 0x66, 0x3c, 0x18, 0x3c, 0x66, 0x00],
            'y': [0x00, 0x00, 0x66, 0x66, 0x66, 0x3e, 0x06, 0x3c],
            'z': [0x00, 0x00, 0x7e, 0x0c, 0x18, 0x30, 0x7e, 0x00],
            '{': [0x0e, 0x18, 0x18, 0x70, 0x18, 0x18, 0x0e, 0x00],
            '|': [0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x00],
            '}': [0x70, 0x18, 0x18, 0x0e, 0x18, 0x18, 0x70, 0x00],
            '~': [0x76, 0xdc, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            '“': [0x66, 0x66, 0x24, 0x00, 0x00, 0x00, 0x00, 0x00],
            '”': [0x24, 0x66, 0x66, 0x00, 0x00, 0x00, 0x00, 0x00],
            '—': [0x00, 0x00, 0x00, 0xff, 0x00, 0x00, 0x00, 0x00],
        }

        def parse_color(c: str) -> tuple[int, int, int]:
            if not c or c == "none":
                return (0, 0, 0)
            c = c.strip()
            if c.startswith("#"):
                h = c[1:]
                if len(h) == 3:
                    h = "".join(x + x for x in h)
                if len(h) == 6:
                    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            named = {
                "white": (255, 255, 255),
                "black": (0, 0, 0),
                "red": (255, 0, 0),
                "green": (0, 255, 0),
                "blue": (0, 0, 255),
            }
            return named.get(c.lower(), (255, 255, 255))

        img = np.zeros((height, width, 3), dtype=np.uint8)

        def draw_rect(x: float, y: float, w: float, h: float, color: tuple[int, int, int], opacity: float = 1.0):
            x1 = max(0, min(width, int(round(x))))
            x2 = max(0, min(width, int(round(x + w))))
            y1 = max(0, min(height, int(round(y))))
            y2 = max(0, min(height, int(round(y + h))))
            if x1 >= x2 or y1 >= y2:
                return
            if opacity >= 0.999:
                img[y1:y2, x1:x2] = color
            else:
                curr = img[y1:y2, x1:x2].astype(np.float32)
                c = np.array(color, dtype=np.float32)
                blended = (curr * (1.0 - opacity) + c * opacity).astype(np.uint8)
                img[y1:y2, x1:x2] = blended

        def draw_line(x1: float, y1: float, x2: float, y2: float, color: tuple[int, int, int], stroke_w: int = 1):
            dx = x2 - x1
            dy = y2 - y1
            steps = int(max(abs(dx), abs(dy), 1))
            hw = stroke_w / 2.0
            for i in range(steps + 1):
                t = i / max(1, steps)
                px = x1 + t * dx
                py = y1 + t * dy
                ix1 = max(0, min(width, int(px - hw)))
                ix2 = max(0, min(width, int(px + hw + 0.5)))
                iy1 = max(0, min(height, int(py - hw)))
                iy2 = max(0, min(height, int(py + hw + 0.5)))
                if ix1 < ix2 and iy1 < iy2:
                    img[iy1:iy2, ix1:ix2] = color

        def draw_circle(cx: float, cy: float, r: float, color: tuple[int, int, int]):
            x1 = max(0, min(width, int(cx - r)))
            x2 = max(0, min(width, int(cx + r + 1)))
            y1 = max(0, min(height, int(cy - r)))
            y2 = max(0, min(height, int(cy + r + 1)))
            if x1 >= x2 or y1 >= y2:
                return
            Y, X = np.ogrid[y1:y2, x1:x2]
            mask = (X - cx) ** 2 + (Y - cy) ** 2 <= r ** 2
            img[y1:y2, x1:x2][mask] = color

        def draw_text(text: str, x: float, y: float, font_size: float, color: tuple[int, int, int], anchor: str, baseline: str):
            scale = max(1, int(round(font_size / 8.0)))
            total_w = len(text) * 8 * scale
            total_h = 8 * scale
            start_x = int(round(x - total_w / 2.0)) if anchor == "middle" else (int(round(x - total_w)) if anchor == "end" else int(round(x)))
            start_y = int(round(y - total_h / 2.0)) if baseline == "middle" else int(round(y - total_h))
            for idx, ch in enumerate(text):
                bitmap = font_8x8.get(ch, font_8x8.get('?'))
                ch_x = start_x + idx * 8 * scale
                for r_idx in range(8):
                    bits = bitmap[r_idx]
                    for c_idx in range(8):
                        if (bits >> (7 - c_idx)) & 1:
                            py1 = max(0, min(height, start_y + r_idx * scale))
                            py2 = max(0, min(height, start_y + (r_idx + 1) * scale))
                            px1 = max(0, min(width, ch_x + c_idx * scale))
                            px2 = max(0, min(width, ch_x + (c_idx + 1) * scale))
                            if px1 < px2 and py1 < py2:
                                img[py1:py2, px1:px2] = color

        cleaned_svg = re.sub(r'\sxmlns="[^"]+"', '', svg_text, count=1)
        try:
            root = ET.fromstring(cleaned_svg)
        except Exception:
            root = ET.Element("svg")

        for elem in root.iter():
            tag = elem.tag.split('}')[-1]
            if tag == "rect":
                rx = float(elem.attrib.get("x", 0))
                ry = float(elem.attrib.get("y", 0))
                rw = float(elem.attrib.get("width", 0))
                rh = float(elem.attrib.get("height", 0))
                fill = parse_color(elem.attrib.get("fill", "#000000"))
                opacity = float(elem.attrib.get("opacity", 1.0))
                draw_rect(rx, ry, rw, rh, fill, opacity)
            elif tag == "line":
                x1 = float(elem.attrib.get("x1", 0))
                y1 = float(elem.attrib.get("y1", 0))
                x2 = float(elem.attrib.get("x2", 0))
                y2 = float(elem.attrib.get("y2", 0))
                stroke = parse_color(elem.attrib.get("stroke", "#ffffff"))
                stroke_w = int(round(float(elem.attrib.get("stroke-width", 1))))
                draw_line(x1, y1, x2, y2, stroke, max(1, stroke_w))
            elif tag == "polyline":
                pts_str = elem.attrib.get("points", "")
                stroke = parse_color(elem.attrib.get("stroke", "#ffffff"))
                stroke_w = int(round(float(elem.attrib.get("stroke-width", 1))))
                pts: list[tuple[float, float]] = []
                for pair in pts_str.strip().split():
                    if "," in pair:
                        px, py = pair.split(",", 1)
                        pts.append((float(px), float(py)))
                for i in range(len(pts) - 1):
                    draw_line(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1], stroke, max(1, stroke_w))
            elif tag == "circle":
                cx = float(elem.attrib.get("cx", 0))
                cy = float(elem.attrib.get("cy", 0))
                r = float(elem.attrib.get("r", 0))
                fill = parse_color(elem.attrib.get("fill", "#ffffff"))
                draw_circle(cx, cy, r, fill)
            elif tag == "text":
                text_content = (elem.text or "").strip()
                if text_content:
                    tx = float(elem.attrib.get("x", 0))
                    ty = float(elem.attrib.get("y", 0))
                    fill = parse_color(elem.attrib.get("fill", "#ffffff"))
                    font_size = float(elem.attrib.get("font-size", 24))
                    anchor = elem.attrib.get("text-anchor", "start")
                    baseline = elem.attrib.get("dominant-baseline", "alphabetic")
                    draw_text(text_content, tx, ty, font_size, fill, anchor, baseline)

        raw_rows = bytearray()
        for row in range(height):
            raw_rows.append(0)
            raw_rows.extend(img[row].tobytes())
        compressed = zlib.compress(bytes(raw_rows), level=6)

        def chunk(tag_name: bytes, data: bytes) -> bytes:
            crc = binascii.crc32(tag_name + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag_name + data + struct.pack(">I", crc)

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
