"""Composition Frame Renderer Engine."""

import html
import re

from omega.application.motion_runtime import LayerFrameState
from omega.application.scene_composition import Layer, LayerType, SceneComposition


class CompositionFrameRenderer:
    def _escape(self, text: str) -> str:
        if text is None:
            return ""
        return html.escape(str(text))

    def _render_layer(self, layer: Layer, state: LayerFrameState | None) -> str:
        opacity = state.opacity if state else 1.0
        scale = state.scale if state else 1.0
        tx = state.translate_x if state else 0.0
        ty = state.translate_y if state else 0.0
        reveal = state.reveal_progress if state else 1.0
        highlight = state.highlight_progress if state else 1.0
        counter = state.counter_progress if state else 1.0
        type_on = state.type_on_progress if state else 1.0

        if state and not state.visible:
            return ""

        cx = layer.x + layer.width / 2.0
        cy = layer.y + layer.height / 2.0

        transform = f"translate({tx:.2f} {ty:.2f}) translate({cx:.2f} {cy:.2f}) scale({scale:.2f}) translate({-cx:.2f} {-cy:.2f})"

        clip_path_def = ""
        clip_attr = ""
        if reveal < 1.0:
            clip_id = f"clip_{layer.id}"
            clip_w = layer.width * reveal
            clip_path_def = f'<clipPath id="{clip_id}"><rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{clip_w:.2f}" height="{layer.height:.2f}" /></clipPath>'
            clip_attr = f'clip-path="url(#{clip_id})"'

        stroke = "#000000"
        stroke_width = 1
        if highlight > 0.0 and layer.type in (LayerType.DIAGRAM_NODE, LayerType.SHAPE, LayerType.IMAGE_SLOT, LayerType.VIDEO_SLOT):
            stroke = "#FFD700" if highlight > 0.5 else "#333333"
            stroke_width = 3 if highlight > 0.5 else 1

        content_svg = ""
        if layer.type == LayerType.TEXT:
            text_val = layer.content.get("text", "")
            if type_on < 1.0:
                char_count = int(len(text_val) * type_on)
                text_val = text_val[:char_count]
            content_svg = f'<text x="{layer.x:.2f}" y="{layer.y + layer.height/2:.2f}" font-family="sans-serif" font-size="24" fill="#FFFFFF">{self._escape(text_val)}</text>'

        elif layer.type == LayerType.CODE_BLOCK:
            code_val = layer.content.get("code", "")
            if type_on < 1.0:
                char_count = int(len(code_val) * type_on)
                code_val = code_val[:char_count]
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#1E1E1E" rx="8" ry="8" />\n'
            lines = code_val.split("\n")
            line_y = layer.y + 30
            for line in lines:
                content_svg += f'  <text x="{layer.x + 20:.2f}" y="{line_y:.2f}" font-family="monospace" font-size="16" fill="#D4D4D4">{self._escape(line)}</text>\n'
                line_y += 24

        elif layer.type == LayerType.DIAGRAM_NODE:
            label = layer.content.get("label", "")
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#4A90E2" stroke="{stroke}" stroke-width="{stroke_width}" rx="10" ry="10" />\n'
            content_svg += f'  <text x="{cx:.2f}" y="{cy:.2f}" font-family="sans-serif" font-size="20" fill="#FFFFFF" text-anchor="middle" dominant-baseline="middle">{self._escape(label)}</text>'

        elif layer.type == LayerType.DIAGRAM_EDGE:
            content_svg = f'<line x1="{layer.x:.2f}" y1="{layer.y:.2f}" x2="{layer.x + layer.width:.2f}" y2="{layer.y + layer.height:.2f}" stroke="#FFFFFF" stroke-width="4" />'

        elif layer.type == LayerType.METRIC:
            val = str(layer.content.get("value", ""))
            if counter < 1.0:
                m = re.match(r"^([^\d]*)([\d\.,]+)([^\d]*)$", val)
                if m:
                    prefix = m.group(1)
                    num_str = m.group(2).replace(',', '')
                    suffix = m.group(3)
                    try:
                        num = float(num_str)
                        curr_num = num * counter
                        curr_str = str(int(curr_num)) if "." not in num_str else f"{curr_num:.1f}"
                        val = f"{prefix}{curr_str}{suffix}"
                    except ValueError:
                        pass
            content_svg = f'<text x="{cx:.2f}" y="{cy:.2f}" font-family="sans-serif" font-size="48" fill="#FFFFFF" text-anchor="middle" dominant-baseline="middle">{self._escape(val)}</text>'

        elif layer.type == LayerType.SHAPE:
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#333333" />'

        elif layer.type == LayerType.DIVIDER:
            content_svg = f'<line x1="{layer.x:.2f}" y1="{layer.y + layer.height/2:.2f}" x2="{layer.x + layer.width:.2f}" y2="{layer.y + layer.height/2:.2f}" stroke="#666666" stroke-width="2" />'

        elif layer.type == LayerType.ICON:
            r = min(layer.width, layer.height) / 2.0
            content_svg = f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="#888888" />'

        elif layer.type in (LayerType.IMAGE_SLOT, LayerType.VIDEO_SLOT):
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#222222" stroke="{stroke}" stroke-width="{stroke_width}" data-layer-id="{self._escape(layer.id)}" />'

        elif layer.type == LayerType.CHART:
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#111111" />'

        else:
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#FF00FF" />'

        group_svg = ""
        if clip_path_def:
            group_svg += f"  <defs>{clip_path_def}</defs>\n"

        group_svg += f'  <g id="layer_{self._escape(layer.id)}" transform="{transform}" opacity="{opacity:.2f}" {clip_attr}>\n    {content_svg}\n  </g>'

        return group_svg

    def render_svg(self, composition: SceneComposition, states: list[LayerFrameState] | None = None) -> str:
        state_map = {s.layer_id: s for s in states} if states else {}

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {composition.width} {composition.height}" width="{composition.width}" height="{composition.height}">',
            '  <!-- COMPOSITION FRAME -->',
            f'  <rect width="{composition.width}" height="{composition.height}" fill="#000000" />'
        ]

        for layer in composition.layers:
            svg_parts.append(self._render_layer(layer, state_map.get(layer.id)))

        svg_parts.append("</svg>")
        return "\n".join(svg_parts)
