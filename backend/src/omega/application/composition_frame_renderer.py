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

    def _is_light_color(self, hex_color: str) -> bool:
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 3:
            hex_color = ''.join(c*2 for c in hex_color)
        if len(hex_color) != 6:
            return True
        try:
            r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
            luminance = (0.299*r + 0.587*g + 0.114*b) / 255
            return luminance > 0.5
        except ValueError:
            return True

    def _wrap_text(self, text: str, max_width: float, font_size: float) -> list[str]:
        words = str(text).split()
        if not words:
            return []
        char_width = font_size * 0.6
        max_chars = max(10, int(max_width / char_width))
        lines = []
        curr_line = []
        curr_len = 0
        for w in words:
            if curr_len + len(w) + 1 > max_chars and curr_line:
                lines.append(" ".join(curr_line))
                curr_line = [w]
                curr_len = len(w)
            else:
                curr_line.append(w)
                curr_len += len(w) + 1
        if curr_line:
            lines.append(" ".join(curr_line))
        return lines

    def _render_layer(self, layer: Layer, state: LayerFrameState | None, default_fg: str = "#FFFFFF") -> str:
        state_opacity = state.opacity if state else 1.0
        scale = state.scale if state else 1.0
        tx = state.translate_x if state else 0.0
        ty = state.translate_y if state else 0.0
        reveal = state.reveal_progress if state else 1.0
        highlight = state.highlight_progress if state else 0.0
        counter = state.counter_progress if state else 1.0
        type_on = state.type_on_progress if state else 1.0

        if state and not state.visible:
            return ""

        final_opacity = max(0.0, min(1.0, layer.opacity * state_opacity * layer.style.get("opacity", 1.0)))

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

        stroke = layer.style.get("border_color", "transparent")
        stroke_width = 0
        has_explicit_border = False
        border_str = layer.style.get("border", "")
        if border_str:
            m = re.match(r"(\d+)px solid (.*)", border_str)
            if m:
                stroke_width = int(m.group(1))
                stroke = m.group(2)
                has_explicit_border = True

        if highlight > 0.0 and layer.type in (LayerType.DIAGRAM_NODE, LayerType.SHAPE, LayerType.IMAGE_SLOT, LayerType.VIDEO_SLOT, LayerType.CODE_BLOCK):
            if has_explicit_border:
                stroke_width += int(highlight * 2)
            else:
                stroke = "#FFD700" if highlight > 0.5 else stroke
                stroke_width = 3 if highlight > 0.5 else stroke_width

        content_svg = ""

        if layer.type == LayerType.TEXT:
            text_val = layer.content.get("text", "")
            if type_on < 1.0:
                char_count = int(len(text_val) * type_on)
                text_val = text_val[:char_count]

            font_size = layer.content.get("font_size", 24)
            weight = layer.content.get("weight", "normal")
            color = layer.content.get("color", layer.style.get("color", default_fg))
            align = layer.content.get("align", "left")

            if align in ("center", "middle"):
                x_pos = cx
                anchor = "middle"
            elif align in ("right", "end"):
                x_pos = layer.x + layer.width
                anchor = "end"
            else:
                x_pos = layer.x
                anchor = "start"

            lines = self._wrap_text(text_val, layer.width, font_size)
            if lines:
                content_svg = f'<text x="{x_pos:.2f}" y="{layer.y + font_size:.2f}" font-family="sans-serif" font-size="{font_size}" font-weight="{weight}" fill="{self._escape(color)}" text-anchor="{anchor}">'
                for i, line in enumerate(lines):
                    dy = font_size * 1.2 if i > 0 else 0
                    content_svg += f'<tspan x="{x_pos:.2f}" dy="{dy:.2f}">{self._escape(line)}</tspan>'
                content_svg += '</text>'

        elif layer.type == LayerType.CODE_BLOCK:
            code_val = layer.content.get("code", "")
            if type_on < 1.0:
                char_count = int(len(code_val) * type_on)
                code_val = code_val[:char_count]

            bg = layer.style.get("bg", "#1E1E1E")
            radius = layer.style.get("radius", 8)
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="{self._escape(bg)}" rx="{radius}" ry="{radius}" stroke="{stroke}" stroke-width="{stroke_width}" />\n'
            lines = code_val.split("\n")
            line_y = layer.y + 30
            for line in lines:
                content_svg += f'  <text x="{layer.x + 20:.2f}" y="{line_y:.2f}" font-family="monospace" font-size="16" fill="#D4D4D4">{self._escape(line)}</text>\n'
                line_y += 24

        elif layer.type == LayerType.DIAGRAM_NODE:
            label = layer.content.get("label", "")
            if stroke == "transparent" and stroke_width == 0:
                stroke = default_fg
                stroke_width = 1
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#4A90E2" stroke="{stroke}" stroke-width="{stroke_width}" rx="10" ry="10" />\n'
            content_svg += f'  <text x="{cx:.2f}" y="{cy:.2f}" font-family="sans-serif" font-size="20" fill="#FFFFFF" text-anchor="middle" dominant-baseline="middle">{self._escape(label)}</text>'

        elif layer.type == LayerType.DIAGRAM_EDGE:
            edge_stroke = stroke if has_explicit_border else default_fg
            marker_def = f'<marker id="arrow_{self._escape(layer.id)}" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="{self._escape(edge_stroke)}" /></marker>'
            clip_path_def += marker_def
            content_svg = f'<line x1="{layer.x:.2f}" y1="{layer.y:.2f}" x2="{layer.x + layer.width:.2f}" y2="{layer.y + layer.height:.2f}" stroke="{self._escape(edge_stroke)}" stroke-width="4" marker-end="url(#arrow_{self._escape(layer.id)})" />'

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

            font_size = layer.content.get("font_size", 48)
            color = layer.content.get("color", layer.style.get("color", default_fg))
            align = layer.content.get("align", "center")

            if align in ("left", "start"):
                x_pos = layer.x
                anchor = "start"
            elif align in ("right", "end"):
                x_pos = layer.x + layer.width
                anchor = "end"
            else:
                x_pos = cx
                anchor = "middle"

            content_svg = f'<text x="{x_pos:.2f}" y="{cy:.2f}" font-family="sans-serif" font-size="{font_size}" fill="{self._escape(color)}" text-anchor="{anchor}" dominant-baseline="middle">{self._escape(val)}</text>'

        elif layer.type == LayerType.SHAPE:
            bg = layer.style.get("bg", layer.style.get("fill", layer.style.get("color", "#333333")))
            radius = layer.style.get("radius", 0)
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="{self._escape(bg)}" stroke="{stroke}" stroke-width="{stroke_width}" rx="{radius}" ry="{radius}" />'

        elif layer.type == LayerType.DIVIDER:
            content_svg = f'<line x1="{layer.x:.2f}" y1="{layer.y + layer.height/2:.2f}" x2="{layer.x + layer.width:.2f}" y2="{layer.y + layer.height/2:.2f}" stroke="#666666" stroke-width="2" />'

        elif layer.type == LayerType.ICON:
            r = min(layer.width, layer.height) / 2.0
            content_svg = f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="#888888" />'

        elif layer.type in (LayerType.IMAGE_SLOT, LayerType.VIDEO_SLOT):
            content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#222222" stroke="{stroke}" stroke-width="{stroke_width}" data-layer-id="{self._escape(layer.id)}" />'

        elif layer.type == LayerType.CHART:
            data = layer.content.get("data")
            if data:
                content_svg = f'<rect x="{layer.x:.2f}" y="{layer.y:.2f}" width="{layer.width:.2f}" height="{layer.height:.2f}" fill="#111111" />'
            else:
                content_svg = ""

        else:
            content_svg = ""

        if not content_svg:
            return ""

        group_svg = ""
        if clip_path_def:
            group_svg += f"  <defs>{clip_path_def}</defs>\n"

        group_svg += f'  <g id="layer_{self._escape(layer.id)}" transform="{transform}" opacity="{final_opacity:.2f}" {clip_attr}>\n    {content_svg}\n  </g>'

        return group_svg

    def render_svg(self, composition: SceneComposition, states: list[LayerFrameState] | None = None) -> str:
        state_map = {s.layer_id: s for s in states} if states else {}

        bg_color = composition.background
        if not re.match(r'^#[0-9a-fA-F]{3,8}$|^rgba?\([0-9\s,\.]+\)$|^[a-zA-Z]+$', bg_color):
            bg_color = "#000000"

        is_light = self._is_light_color(bg_color)
        default_fg = "#0f172a" if is_light else "#f8fafc"

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {composition.width} {composition.height}" width="{composition.width}" height="{composition.height}">',
            '  <!-- COMPOSITION FRAME -->',
            f'  <rect width="{composition.width}" height="{composition.height}" fill="{self._escape(bg_color)}" />'
        ]

        # Sort layers by z_index, preserving original order for equal z_index
        sorted_layers = sorted(composition.layers, key=lambda lyr: getattr(lyr, "z_index", 0))

        for layer in sorted_layers:
            svg_parts.append(self._render_layer(layer, state_map.get(layer.id), default_fg))

        svg_parts.append("</svg>")
        return "\n".join(svg_parts)
