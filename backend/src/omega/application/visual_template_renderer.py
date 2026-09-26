import hashlib
import html
from typing import Any

from pydantic import BaseModel, ConfigDict

from omega.application.scene_template_registry import SceneTemplateRegistry, TemplateInputKey
from omega.application.subtitle_engine import CANONICAL_SUBTITLE_SAFE_BOTTOM_PX
from omega.application.template_payload_resolver import TemplatePayload
from omega.application.text_fitting import TextFittingDecision, fit_text
from omega.application.visual_asset_binding import (
    BoundBrollAsset,
    BoundVisualAsset,
    RenderBoundAsset,
)
from omega.application.visual_direction import VisualAssetKind, VisualTemplateId


class RenderedTemplateDocument(BaseModel):

    model_config = ConfigDict(frozen=True)

    scene_index: int
    template_id: VisualTemplateId
    width: int
    height: int
    html: str
    semantic_element_ids: tuple[str, ...]
    content_sha256: str
    text_fitting: tuple[TextFittingDecision, ...] = ()


class VisualTemplateRenderError(ValueError):
    pass


class VisualTemplateRenderer:
    def __init__(self, registry: SceneTemplateRegistry | None = None):
        self.registry = registry or SceneTemplateRegistry()

    def render(
        self,
        payload: TemplatePayload,
        assets: tuple[RenderBoundAsset, ...] = (),
        accent_color: str | None = None,
        bg_color: str | None = None,
    ) -> RenderedTemplateDocument:
        if payload.template_id not in (
            VisualTemplateId.HERO_TITLE,
            VisualTemplateId.FLOW_DIAGRAM,
            VisualTemplateId.STATISTIC_HERO,
            VisualTemplateId.CODE_EDITOR,
            VisualTemplateId.IMAGE_EXPLAINER,
            VisualTemplateId.BROLL_EXPLAINER,
            VisualTemplateId.KINETIC_TEXT,
            VisualTemplateId.INFOGRAPHIC,
            VisualTemplateId.CTA,
        ):
            raise VisualTemplateRenderError(f"Unsupported template_id {payload.template_id}")

        # Media templates check
        if payload.template_id not in (
            VisualTemplateId.IMAGE_EXPLAINER,
            VisualTemplateId.BROLL_EXPLAINER,
        ) and assets:
            raise VisualTemplateRenderError("Unexpected bound assets for template")

        definition = self.registry.get(payload.template_id)

        # Verify required input keys
        for req_key in definition.required_inputs:
            val = payload.inputs.get(req_key)
            if not self._is_meaningful(val):
                raise VisualTemplateRenderError(f"Missing required input key {req_key}")

        # Verify no unexpected keys
        allowed_keys = set(definition.required_inputs) | set(definition.optional_inputs)
        for key in payload.inputs:
            if key not in allowed_keys:
                raise VisualTemplateRenderError(f"Unexpected input key {key}")

        payload, text_fitting, fit_css = self._fit_payload_text(payload)
        semantic_ids: list[str] = []
        html_content = ""
        transparent_bg = False

        if payload.template_id == VisualTemplateId.HERO_TITLE:
            html_content, semantic_ids = self._render_hero_title(payload)
        elif payload.template_id == VisualTemplateId.FLOW_DIAGRAM:
            html_content, semantic_ids = self._render_flow_diagram(payload)
        elif payload.template_id == VisualTemplateId.STATISTIC_HERO:
            html_content, semantic_ids = self._render_statistic_hero(payload)
        elif payload.template_id == VisualTemplateId.CODE_EDITOR:
            html_content, semantic_ids = self._render_code_editor(payload)
        elif payload.template_id == VisualTemplateId.IMAGE_EXPLAINER:
            html_content, semantic_ids = self._render_image_explainer(payload, assets)
        elif payload.template_id == VisualTemplateId.BROLL_EXPLAINER:
            html_content, semantic_ids = self._render_broll_explainer(payload, assets)
            transparent_bg = True
        elif payload.template_id == VisualTemplateId.KINETIC_TEXT:
            html_content, semantic_ids = self._render_kinetic_text(payload)
        elif payload.template_id == VisualTemplateId.INFOGRAPHIC:
            html_content, semantic_ids = self._render_infographic(payload)
        elif payload.template_id == VisualTemplateId.CTA:
            html_content, semantic_ids = self._render_cta(payload)

        # Wrap in full HTML document
        final_html = self._wrap_in_document(
            html_content,
            transparent_background=transparent_bg,
            fitted_text_css=fit_css,
            accent_color=accent_color,
            bg_color=bg_color,
        )

        sha256 = hashlib.sha256(final_html.encode("utf-8")).hexdigest()
        return RenderedTemplateDocument(
            scene_index=payload.scene_index,
            template_id=payload.template_id,
            width=1920,
            height=1080,
            html=final_html,
            semantic_element_ids=tuple(semantic_ids),
            content_sha256=sha256,
            text_fitting=tuple(text_fitting),
        )

    def _fit_payload_text(
        self, payload: TemplatePayload
    ) -> tuple[TemplatePayload, list[TextFittingDecision], str]:
        rules = {
            VisualTemplateId.HERO_TITLE: {
                TemplateInputKey.TITLE: ("hero-title", 110, 56, 3, 22),
                TemplateInputKey.SUBTITLE: ("hero-subtitle", 40, 28, 3, 55),
            },
            VisualTemplateId.IMAGE_EXPLAINER: {
                TemplateInputKey.TITLE: ("image-title", 64, 40, 3, 30),
                TemplateInputKey.BODY: ("image-body", 34, 24, 8, 48),
                TemplateInputKey.CAPTION: ("image-caption", 22, 18, 3, 58),
            },
            VisualTemplateId.BROLL_EXPLAINER: {
                TemplateInputKey.TITLE: ("broll-title", 68, 42, 3, 28),
                TemplateInputKey.BODY: ("broll-body", 36, 24, 7, 46),
                TemplateInputKey.CAPTION: ("broll-caption", 24, 18, 3, 56),
            },
            VisualTemplateId.KINETIC_TEXT: {
                TemplateInputKey.BODY: ("kinetic-body", 84, 44, 5, 24),
            },
            VisualTemplateId.CTA: {
                TemplateInputKey.TITLE: ("cta-title", 36, 26, 3, 48),
                TemplateInputKey.CTA_TEXT: ("cta-text", 92, 48, 4, 23),
            },
        }.get(payload.template_id, {})
        inputs = dict(payload.inputs)
        decisions: list[TextFittingDecision] = []
        css: list[str] = []
        for key, (element_id, initial, minimum, max_lines, chars) in rules.items():
            value = inputs.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            decision = fit_text(
                value,
                role=key.value,
                initial_font_size=initial,
                min_font_size=minimum,
                max_lines=max_lines,
                chars_per_line_at_initial_size=chars,
            )
            inputs[key] = decision.rendered_text
            decisions.append(decision)
            css.append(
                f"#{element_id} {{ font-size: {decision.font_size}px !important; "
                "white-space: pre-line !important; overflow-wrap: anywhere; }}"
            )
        return payload.model_copy(update={"inputs": inputs}), decisions, "\n".join(css)

    def _is_meaningful(self, val: Any) -> bool:
        if val is None:
            return False
        if isinstance(val, str) and not val.strip():
            return False
        return not (isinstance(val, (list, tuple, dict, set)) and len(val) == 0)

    def _wrap_in_document(
        self,
        body_content: str,
        transparent_background: bool = False,
        fitted_text_css: str = "",
        accent_color: str | None = None,
        bg_color: str | None = None,
    ) -> str:
        base_css = self._get_base_css(
            transparent_background=transparent_background,
            accent_color=accent_color,
            bg_color=bg_color,
        )
        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
{base_css}
{fitted_text_css}
</style>
</head>
<body>
<div id="scene-root" class="scene-root">
{body_content}
</div>
</body>
</html>"""

    def _get_base_css(
        self,
        transparent_background: bool = False,
        accent_color: str | None = None,
        bg_color: str | None = None,
    ) -> str:
        accent = accent_color or "#3B82F6"
        bg = bg_color or "#0B0F19"
        if transparent_background:
            return f"""
        :root {{
            --bg: transparent;
            --text-primary: #F1F5F9;
            --text-secondary: #94A3B8;
            --accent: {accent};
            --surface: rgba(30, 41, 59, 0.7);
            --surface-border: rgba(255, 255, 255, 0.1);
        }}
        body, html {{
            margin: 0;
            padding: 0;
            width: 1920px;
            height: 1080px;
            background-color: transparent;
            color: var(--text-primary);
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .scene-root {{
            width: 1920px;
            height: 1080px;
            position: relative;
            padding: 90px 120px;
            box-sizing: border-box;
            background: transparent;
            display: flex;
        }}
        """

        return f"""
        :root {{
            --bg: {bg};
            --text-primary: #F1F5F9;
            --text-secondary: #94A3B8;
            --accent: {accent};
            --surface: rgba(30, 41, 59, 0.7);
            --surface-border: rgba(255, 255, 255, 0.1);
        }}
        body, html {{
            margin: 0;
            padding: 0;
            width: 1920px;
            height: 1080px;
            background-color: var(--bg);
            color: var(--text-primary);
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .scene-root {{
            width: 1920px;
            height: 1080px;
            position: relative;
            padding: 90px 120px;
            box-sizing: border-box;
            background: radial-gradient(circle at top left, rgba(59, 130, 246, 0.1), transparent 50%),
                        radial-gradient(circle at bottom right, rgba(99, 102, 241, 0.05), transparent 50%);
            display: flex;
        }}
        """

    def _render_hero_title(self, payload: TemplatePayload) -> tuple[str, list[str]]:
        title = html.escape(payload.inputs[TemplateInputKey.TITLE])
        subtitle = payload.inputs.get(TemplateInputKey.SUBTITLE)

        title_length = len(title)
        if title_length < 20:
            title_size = "110px"
        elif title_length < 50:
            title_size = "90px"
        else:
            title_size = "72px"

        semantic_ids = ["scene-root", "hero-title"]

        subtitle_html = ""
        if subtitle:
            esc_sub = html.escape(subtitle)
            subtitle_html = f'<div id="hero-subtitle" data-motion-role="subtitle" class="hero-subtitle">{esc_sub}</div>'
            semantic_ids.append("hero-subtitle")

        semantic_ids.append("hero-accent")

        content = f"""
        <style>
        .hero-container {{ display: flex; flex-direction: column; justify-content: center; height: 100%; max-width: 1400px; z-index: 2; position: relative; }}
        .hero-title {{ font-size: {title_size}; font-weight: 800; line-height: 1.1; margin-bottom: 24px; letter-spacing: -0.02em; word-wrap: break-word; }}
        .hero-subtitle {{ font-size: 40px; color: var(--text-secondary); line-height: 1.4; max-width: 1200px; word-wrap: break-word; }}
        .hero-accent {{ width: 80px; height: 6px; background-color: var(--accent); margin-bottom: 40px; border-radius: 3px; }}
        .hero-bg {{ position: absolute; right: 0; top: 0; width: 50%; height: 100%; opacity: 0.1; z-index: 1; }}
        </style>
        <div class="hero-bg" data-motion-role="background-decoration">
            <svg viewBox="0 0 100 100" fill="none" preserveAspectRatio="none">
                <circle cx="50" cy="50" r="40" stroke="var(--accent)" stroke-width="2"/>
                <path d="M0 100 L100 0" stroke="var(--accent)" stroke-width="1"/>
            </svg>
        </div>
        <div class="hero-container">
            <div id="hero-accent" data-motion-role="accent" class="hero-accent"></div>
            <div id="hero-title" data-motion-role="title" class="hero-title">{title}</div>
            {subtitle_html}
        </div>
        """
        return content, semantic_ids

    def _render_flow_diagram(self, payload: TemplatePayload) -> tuple[str, list[str]]:

        nodes = payload.inputs[TemplateInputKey.NODES]
        title = payload.inputs.get(TemplateInputKey.TITLE)

        semantic_ids = ["scene-root"]

        title_html = ""
        if title:
            esc_title = html.escape(title)
            title_html = f'<div id="flow-title" class="section-title">{esc_title}</div>'
            semantic_ids.append("flow-title")

        edges = payload.inputs.get(TemplateInputKey.EDGES) or []
        edge_map = {e.from_node: e for e in edges if hasattr(e, "from_node")}
        from omega.application.mechanism_diagram import derive_relation_label

        num_nodes = len(nodes)
        if num_nodes <= 2:
            node_min_w = 420
            node_max_w = 540
            node_min_h = 180
            node_pad = "48px 56px"
            node_fs = 40
            node_radius = 24
            edge_gap = 40
            svg_w, svg_h = 72, 36
            label_fs = 20
            label_pad = "6px 18px"
        elif num_nodes == 3:
            node_min_w = 320
            node_max_w = 420
            node_min_h = 160
            node_pad = "40px 48px"
            node_fs = 34
            node_radius = 20
            edge_gap = 32
            svg_w, svg_h = 60, 30
            label_fs = 18
            label_pad = "5px 14px"
        else:
            node_min_w = 260
            node_max_w = 340
            node_min_h = 140
            node_pad = "32px 36px"
            node_fs = 28
            node_radius = 18
            edge_gap = 24
            svg_w, svg_h = 48, 24
            label_fs = 15
            label_pad = "4px 10px"

        nodes_html = ""
        for i, node in enumerate(nodes):
            esc_node = html.escape(node)
            node_id = f"flow-node-{i}"
            nodes_html += f'<div id="{node_id}" data-motion-role="flow-node" data-motion-index="{i}" class="flow-node">{esc_node}</div>\n'
            semantic_ids.append(node_id)

            if i < num_nodes - 1:
                edge_id = f"flow-edge-{i}"
                next_node = nodes[i + 1]
                edge_obj = edge_map.get(node) or (edges[i] if i < len(edges) else None)
                rel_label = getattr(edge_obj, "label", None) if edge_obj else None
                if not rel_label:
                    rel_label = derive_relation_label(node, next_node)

                esc_rel = html.escape(rel_label)
                label_html = f'<div class="flow-edge-label">{esc_rel}</div>'

                nodes_html += f"""
                <div id="{edge_id}" data-motion-role="flow-edge" data-motion-index="{i}" class="flow-edge">
                    <svg width="{svg_w}" height="{svg_h}" viewBox="0 0 60 30" fill="none">
                        <path d="M0 15 H52 M40 5 L52 15 L40 25" stroke="var(--accent)" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    {label_html}
                </div>
                """
                semantic_ids.append(edge_id)

        content = f"""
        <style>
        .flow-container {{ display: flex; flex-direction: column; height: 100%; width: 100%; justify-content: center; align-items: center; }}
        .section-title {{ font-size: 32px; font-weight: 600; color: var(--accent); margin-bottom: 50px; letter-spacing: 0.05em; text-transform: uppercase; width: 100%; text-align: left; }}
        .flow-diagram-area {{ display: flex; flex-direction: row; align-items: center; justify-content: center; flex-grow: 1; flex-wrap: nowrap; gap: {edge_gap}px; width: 100%; max-width: 1720px; }}
        .flow-node {{
            background: linear-gradient(145deg, rgba(30, 41, 59, 0.95), rgba(15, 23, 42, 0.98));
            border: 2px solid rgba(96, 165, 250, 0.45);
            border-radius: {node_radius}px;
            font-size: {node_fs}px;
            font-weight: 700;
            text-align: center;
            color: #F8FAFC;
            box-shadow: 0 20px 45px rgba(0,0,0,0.6), 0 0 30px rgba(59, 130, 246, 0.18);
            min-width: {node_min_w}px;
            max-width: {node_max_w}px;
            min-height: {node_min_h}px;
            padding: {node_pad};
            display: flex;
            align-items: center;
            justify-content: center;
            word-wrap: break-word;
            line-height: 1.35;
        }}
        .flow-edge {{ display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 0 12px; flex-shrink: 0; }}
        .flow-edge-label {{
            font-size: {label_fs}px;
            font-weight: 800;
            color: #93C5FD;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            background: rgba(15, 23, 42, 0.92);
            border: 1.5px solid rgba(96, 165, 250, 0.45);
            padding: {label_pad};
            border-radius: 8px;
            white-space: nowrap;
            box-shadow: 0 4px 16px rgba(0,0,0,0.5);
        }}
        </style>
        <div class="flow-container">
            {title_html}
            <div class="flow-diagram-area">
                {nodes_html}
            </div>
        </div>
        """

        return content, semantic_ids

    def _render_statistic_hero(self, payload: TemplatePayload) -> tuple[str, list[str]]:
        metric = html.escape(payload.inputs[TemplateInputKey.METRIC])
        label = payload.inputs.get(TemplateInputKey.METRIC_LABEL)
        title = payload.inputs.get(TemplateInputKey.TITLE)

        semantic_ids = ["scene-root", "metric-value"]

        title_html = ""
        if title:
            esc_title = html.escape(title)
            title_html = f'<div id="metric-title" class="section-title">{esc_title}</div>'
            semantic_ids.append("metric-title")

        label_html = ""
        if label:
            esc_label = html.escape(label)
            label_html = f'<div id="metric-label" data-motion-role="metric-label" class="metric-label">{esc_label}</div>'
            semantic_ids.append("metric-label")

        progress_svg = ""
        try:
            if metric.endswith("%"):
                val_str = metric[:-1]
                val_float = float(val_str)
                if 0 <= val_float <= 100:
                    circumference = 2 * 3.14159 * 180
                    dasharray = f"{circumference * (val_float / 100)} {circumference}"
                    progress_svg = f"""
                    <svg width="400" height="400" viewBox="0 0 400 400" class="stat-ring">
                        <circle cx="200" cy="200" r="180" fill="none" stroke="var(--surface)" stroke-width="20"/>
                        <circle cx="200" cy="200" r="180" fill="none" stroke="var(--accent)" stroke-width="20" stroke-dasharray="{dasharray}" stroke-dashoffset="0" transform="rotate(-90 200 200)"/>
                    </svg>
                    """
        except ValueError:
            pass

        if not progress_svg:
            progress_svg = """
            <svg width="400" height="400" viewBox="0 0 400 400" class="stat-ring">
                <rect x="50" y="50" width="300" height="300" fill="none" stroke="var(--surface)" stroke-width="4" transform="rotate(45 200 200)"/>
                <rect x="100" y="100" width="200" height="200" fill="none" stroke="var(--accent)" stroke-width="2" transform="rotate(45 200 200)"/>
            </svg>
            """

        content = f"""
        <style>
        .stat-container {{ display: flex; flex-direction: column; height: 100%; width: 100%; }}
        .section-title {{ font-size: 32px; font-weight: 600; color: var(--accent); margin-bottom: 80px; letter-spacing: 0.05em; text-transform: uppercase; }}
        .stat-content {{ display: flex; align-items: center; justify-content: center; flex-grow: 1; gap: 80px; }}
        .stat-text {{ display: flex; flex-direction: column; max-width: 800px; }}
        .metric-value {{ font-size: 180px; font-weight: 900; line-height: 1; margin-bottom: 24px; background: linear-gradient(135deg, #F1F5F9, #94A3B8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .metric-label {{ font-size: 48px; color: var(--text-secondary); line-height: 1.4; word-wrap: break-word; }}
        .stat-ring {{ display: block; }}
        </style>
        <div class="stat-container">
            {title_html}
            <div class="stat-content">
                <div class="stat-visual" data-motion-role="stat-decoration">
                    {progress_svg}
                </div>
                <div class="stat-text">
                    <div id="metric-value" data-motion-role="metric" class="metric-value">{metric}</div>
                    {label_html}
                </div>
            </div>
        </div>
        """
        return content, semantic_ids

    def _render_code_editor(self, payload: TemplatePayload) -> tuple[str, list[str]]:
        raw_code = payload.inputs[TemplateInputKey.CODE]
        lang = payload.inputs.get(TemplateInputKey.LANGUAGE)
        title = payload.inputs.get(TemplateInputKey.TITLE)

        semantic_ids = ["scene-root", "code-panel", "code-content"]

        title_html = ""
        if title:
            esc_title = html.escape(title)
            title_html = f'<div id="code-title" class="section-title">{esc_title}</div>'
            semantic_ids.append("code-title")

        lines = raw_code.split("\n")
        line_numbers_html = "\n".join(f'<div class="code-line-num">{i+1}</div>' for i in range(len(lines)))

        # Do NOT syntax highlight in D1, only strictly preserve the entire escaped string.
        esc_code = html.escape(raw_code)

        lang_html = ""

        if lang:
            esc_lang = html.escape(lang)
            lang_html = f'<div class="code-badge">{esc_lang}</div>'

        content = f"""
        <style>
        .code-container {{ display: flex; flex-direction: column; height: 100%; width: 100%; }}
        .section-title {{ font-size: 32px; font-weight: 600; color: var(--accent); margin-bottom: 80px; letter-spacing: 0.05em; text-transform: uppercase; }}
        .code-panel {{ background: #0f1423; border: 1px solid var(--surface-border); border-radius: 12px; display: flex; flex-direction: column; flex-grow: 1; max-height: 800px; box-shadow: 0 20px 40px rgba(0,0,0,0.5); overflow: hidden; }}
        .code-header {{ height: 48px; background: #1a2035; border-bottom: 1px solid var(--surface-border); display: flex; align-items: center; padding: 0 20px; position: relative; }}
        .code-dots {{ display: flex; gap: 8px; }}
        .code-dot {{ width: 12px; height: 12px; border-radius: 50%; background: #475569; }}
        .code-dot:nth-child(1) {{ background: #ef4444; }}
        .code-dot:nth-child(2) {{ background: #eab308; }}
        .code-dot:nth-child(3) {{ background: #22c55e; }}
        .code-badge {{ position: absolute; left: 50%; transform: translateX(-50%); font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; color: var(--text-secondary); font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }}
        .code-body {{ display: flex; flex-grow: 1; padding: 24px 0; overflow: hidden; }}
        .code-lines {{ padding: 0 24px; color: #475569; font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 28px; line-height: 1.6; text-align: right; user-select: none; border-right: 1px solid var(--surface-border); }}
        .code-content {{ flex-grow: 1; padding: 0 32px; font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 28px; line-height: 1.6; color: #e2e8f0; white-space: pre-wrap; word-wrap: break-word; overflow: hidden; }}
        </style>
        <div class="code-container">
            {title_html}
            <div id="code-panel" data-motion-role="code-panel" class="code-panel">
                <div class="code-header">
                    <div class="code-dots"><div class="code-dot"></div><div class="code-dot"></div><div class="code-dot"></div></div>
                    {lang_html}
                </div>
                <div class="code-body">
                    <div class="code-lines">{line_numbers_html}</div>
                    <div id="code-content" data-motion-role="code-content" class="code-content">{esc_code}</div>
                </div>
            </div>
        </div>
        """
        return content, semantic_ids

    def _render_image_explainer(
        self,
        payload: TemplatePayload,
        assets: tuple[RenderBoundAsset, ...],
    ) -> tuple[str, list[str]]:
        if len(assets) == 0:
            raise VisualTemplateRenderError("Missing required IMAGE asset for IMAGE_EXPLAINER")
        if len(assets) > 1:
            raise VisualTemplateRenderError("Multiple IMAGE assets provided for IMAGE_EXPLAINER")

        bound_asset = assets[0]
        if not isinstance(bound_asset, BoundVisualAsset) or bound_asset.kind != VisualAssetKind.IMAGE:
            raise VisualTemplateRenderError("Invalid asset kind for IMAGE_EXPLAINER")

        allowed_mimes = ("image/jpeg", "image/png", "image/webp")
        if bound_asset.mime_type not in allowed_mimes:
            raise VisualTemplateRenderError("Invalid image MIME for IMAGE_EXPLAINER")

        raw_body = payload.inputs.get(TemplateInputKey.BODY)
        title = payload.inputs.get(TemplateInputKey.TITLE)
        caption = payload.inputs.get(TemplateInputKey.CAPTION)

        semantic_ids = ["scene-root", "image-frame"]

        body_html = ""
        if raw_body:
            esc_body = html.escape(raw_body)
            body_html = f'<div id="image-body" class="image-body">{esc_body}</div>'
            semantic_ids.append("image-body")

        title_html = ""
        if title:
            esc_title = html.escape(title)
            title_html = f'<div id="image-title" class="image-title">{esc_title}</div>'
            semantic_ids.append("image-title")

        caption_html = ""
        if caption:
            esc_caption = html.escape(caption)
            caption_html = f'<div id="image-caption" class="image-caption">{esc_caption}</div>'
            semantic_ids.append("image-caption")

        alt_raw = caption or title or raw_body or ""
        esc_alt = html.escape(alt_raw, quote=True)
        esc_src = html.escape(bound_asset.data_uri, quote=True)

        img_html = f'<img id="image-element" class="image-asset" src="{esc_src}" alt="{esc_alt}" />'

        layout = payload.metadata.get("layout_variant") or "SPLIT_LEFT_VISUAL"
        container_style_override = ""
        if layout == "SPLIT_RIGHT_VISUAL":
            container_style_override = ".split-container { flex-direction: row-reverse; }"
        elif layout == "FULL_BLEED_VISUAL":
            container_style_override = """
            .split-container { position: relative; gap: 0; }
            .split-media { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
            .image-frame { width: 100%; height: 100%; border: none; border-radius: 0; }
            .split-text { position: absolute; bottom: 80px; left: 80px; z-index: 2; max-width: 55%; background: rgba(11, 15, 25, 0.85); backdrop-filter: blur(16px); padding: 36px 44px; border-radius: 20px; border: 1px solid var(--surface-border); }
            """
        elif layout == "TEXT_OVER_VISUAL":
            container_style_override = """
            .split-container { position: relative; justify-content: center; align-items: center; }
            .split-media { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; opacity: 0.35; }
            .image-frame { width: 100%; height: 100%; border: none; border-radius: 0; }
            .split-text { position: relative; z-index: 2; max-width: 75%; text-align: center; align-items: center; background: rgba(11, 15, 25, 0.7); backdrop-filter: blur(20px); padding: 48px 64px; border-radius: 24px; border: 1px solid var(--accent); }
            """
        elif layout == "IMAGE_WITH_CALLOUTS":
            container_style_override = """
            .image-frame { border: 2px solid var(--accent); box-shadow: 0 0 40px rgba(59, 130, 246, 0.3); }
            .split-text { border-left: 4px solid var(--accent); padding-left: 32px; }
            """

        content = f"""
        <style>
        .split-container {{ display: flex; height: 100%; width: 100%; gap: 60px; padding-bottom: {CANONICAL_SUBTITLE_SAFE_BOTTOM_PX}px; box-sizing: border-box; }}
        .split-text {{ flex: 1; display: flex; flex-direction: column; justify-content: center; max-width: 50%; }}
        .image-title {{ font-size: 32px; font-weight: 600; color: var(--accent); margin-bottom: 40px; letter-spacing: 0.05em; text-transform: uppercase; }}
        .image-body {{ font-size: 48px; line-height: 1.4; color: var(--text-primary); margin-bottom: 30px; word-wrap: break-word; }}
        .image-caption {{ font-size: 24px; color: var(--text-secondary); border-left: 4px solid var(--surface-border); padding-left: 16px; font-style: italic; word-wrap: break-word; }}
        .split-media {{ flex: 1; display: flex; align-items: center; justify-content: center; }}
        .image-frame {{ width: 100%; height: 800px; background: linear-gradient(135deg, rgba(59, 130, 246, 0.2), rgba(99, 102, 241, 0.1)); border: 1px solid var(--accent); border-radius: 20px; box-shadow: inset 0 0 100px rgba(0,0,0,0.5), 0 20px 40px rgba(0,0,0,0.3); overflow: hidden; position: relative; }}
        .image-asset {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
        {container_style_override}
        </style>
        <div class="split-container">
            <div class="split-text">
                {title_html}
                {body_html}
                {caption_html}
            </div>
            <div class="split-media">
                <div id="image-frame" data-asset-kind="IMAGE" data-motion-role="image-frame" class="image-frame">
                    {img_html}
                </div>
            </div>
        </div>
        """
        return content, semantic_ids

    def _render_broll_explainer(
        self,
        payload: TemplatePayload,
        assets: tuple[RenderBoundAsset, ...],
    ) -> tuple[str, list[str]]:
        if len(assets) == 0:
            raise VisualTemplateRenderError("Missing required BROLL asset for BROLL_EXPLAINER")
        if len(assets) > 1:
            raise VisualTemplateRenderError("Multiple BROLL assets provided for BROLL_EXPLAINER")

        bound_asset = assets[0]
        if not isinstance(bound_asset, BoundBrollAsset) or bound_asset.kind != VisualAssetKind.BROLL:
            raise VisualTemplateRenderError("Invalid BROLL asset for BROLL_EXPLAINER")

        if bound_asset.mime_type != "video/mp4":
            raise VisualTemplateRenderError("Invalid BROLL asset for BROLL_EXPLAINER")

        raw_body = payload.inputs.get(TemplateInputKey.BODY)
        title = payload.inputs.get(TemplateInputKey.TITLE)
        caption = payload.inputs.get(TemplateInputKey.CAPTION)

        semantic_ids = ["scene-root", "broll-scrim"]

        body_html = ""
        if raw_body:
            esc_body = html.escape(raw_body)
            body_html = f'<div id="broll-body" data-motion-role="broll-body" class="broll-body">{esc_body}</div>'
            semantic_ids.append("broll-body")

        title_html = ""
        if title:
            esc_title = html.escape(title)
            title_html = f'<div id="broll-title" data-motion-role="broll-title" class="broll-title">{esc_title}</div>'
            semantic_ids.append("broll-title")

        caption_html = ""
        if caption:
            esc_caption = html.escape(caption)
            caption_html = f'<div id="broll-caption" data-motion-role="broll-caption" class="broll-caption">{esc_caption}</div>'
            semantic_ids.append("broll-caption")

        layout = payload.metadata.get("layout_variant") or "TEXT_OVER_VISUAL"
        broll_layout_override = ""
        if layout == "BROLL_WITH_MINIMAL_LABEL":
            broll_layout_override = """
            .broll-title { font-size: 20px; margin-bottom: 8px; opacity: 0.85; }
            .broll-body { font-size: 32px; max-width: 700px; background: rgba(11, 15, 25, 0.85); backdrop-filter: blur(14px); padding: 16px 24px; border-radius: 16px; border: 1px solid var(--surface-border); display: inline-block; }
            .broll-caption { display: none; }
            """
        elif layout == "FULL_BLEED_VISUAL":
            broll_layout_override = """
            .broll-scrim { background: linear-gradient(0deg, rgba(11, 15, 25, 0.85) 0%, transparent 50%); }
            .broll-content { max-width: 850px; margin-left: 60px; }
            """

        content = f"""
        <style>
        .broll-container {{
            position: relative;
            width: 100%;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            padding: 0;
            box-sizing: border-box;
        }}
        .broll-scrim {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(135deg, rgba(11, 15, 25, 0.85) 0%, rgba(11, 15, 25, 0.4) 50%, transparent 100%);
            pointer-events: none;
            z-index: 1;
        }}
        .broll-content {{
            position: relative;
            z-index: 2;
            max-width: 1100px;
            margin-bottom: {CANONICAL_SUBTITLE_SAFE_BOTTOM_PX}px;
            margin-left: 80px;
        }}
        .broll-title {{
            font-size: 36px;
            font-weight: 700;
            color: var(--accent);
            margin-bottom: 24px;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }}
        .broll-body {{
            font-size: 52px;
            font-weight: 600;
            line-height: 1.3;
            color: var(--text-primary);
            margin-bottom: 24px;
            word-wrap: break-word;
            text-shadow: 0 4px 12px rgba(0,0,0,0.6);
        }}
        .broll-caption {{
            font-size: 26px;
            color: var(--text-secondary);
            border-left: 4px solid var(--accent);
            padding-left: 16px;
            font-style: italic;
            word-wrap: break-word;
            text-shadow: 0 2px 8px rgba(0,0,0,0.6);
        }}
        {broll_layout_override}
        </style>

        <div class="broll-container">
            <div id="broll-scrim" data-motion-role="broll-scrim" class="broll-scrim"></div>
            <div class="broll-content">
                {title_html}
                {body_html}
                {caption_html}
            </div>
        </div>
        """
        return content, semantic_ids

    def _render_kinetic_text(self, payload: TemplatePayload) -> tuple[str, list[str]]:
        raw_body = payload.inputs[TemplateInputKey.BODY]

        semantic_ids = ["scene-root", "kinetic-body"]

        words = raw_body.split()
        words_html = ""
        for i, word in enumerate(words):
            esc_word = html.escape(word)
            word_id = f"kinetic-word-{i}"
            words_html += f'<span id="{word_id}" data-motion-role="kinetic-word" data-motion-index="{i}" class="kinetic-word">{esc_word}</span> '
            semantic_ids.append(word_id)

        content = f"""
        <style>
        .kinetic-container {{ display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100%; width: 100%; padding: 0 100px; box-sizing: border-box; }}
        .kinetic-body {{ font-size: 80px; font-weight: 800; line-height: 1.3; text-align: center; color: var(--text-primary); max-width: 1600px; word-wrap: break-word; }}
        .kinetic-word {{ display: inline-block; opacity: 1; }}
        </style>
        <div class="kinetic-container">
            <div id="kinetic-body" class="kinetic-body">
                {words_html.strip()}
            </div>
        </div>
        """
        return content, semantic_ids

    def _render_infographic(self, payload: TemplatePayload) -> tuple[str, list[str]]:
        items = payload.inputs[TemplateInputKey.ITEMS]
        title = payload.inputs.get(TemplateInputKey.TITLE)

        semantic_ids = ["scene-root"]

        title_html = ""
        if title:
            esc_title = html.escape(title)
            title_html = f'<div id="infographic-title" data-motion-role="infographic-title" class="infographic-title">{esc_title}</div>'
            semantic_ids.append("infographic-title")

        items_html = ""
        for i, item in enumerate(items):
            esc_item = html.escape(item)
            item_id = f"infographic-item-{i}"
            items_html += f'<div id="{item_id}" data-motion-role="infographic-item" data-motion-index="{i}" class="infographic-item">{esc_item}</div>\n'
            semantic_ids.append(item_id)

        content = f"""
        <style>
        .infographic-container {{ display: flex; flex-direction: column; height: 100%; width: 100%; padding: 60px 80px; box-sizing: border-box; }}
        .infographic-title {{ font-size: 48px; font-weight: 700; color: var(--accent); margin-bottom: 60px; text-align: center; word-wrap: break-word; }}
        .infographic-grid {{ display: flex; flex-direction: row; flex-wrap: wrap; justify-content: center; gap: 40px; align-items: stretch; }}
        .infographic-item {{ background: var(--surface); border: 1px solid var(--surface-border); border-radius: 16px; padding: 40px; font-size: 32px; color: var(--text-primary); max-width: 500px; flex: 1; min-width: 300px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); word-wrap: break-word; }}
        </style>
        <div class="infographic-container">
            {title_html}
            <div class="infographic-grid">
                {items_html}
            </div>
        </div>
        """
        return content, semantic_ids

    def _render_cta(self, payload: TemplatePayload) -> tuple[str, list[str]]:
        cta_text = html.escape(payload.inputs[TemplateInputKey.CTA_TEXT])
        title = payload.inputs.get(TemplateInputKey.TITLE)

        semantic_ids = ["scene-root", "cta-text", "cta-accent"]

        title_html = ""
        if title:
            esc_title = html.escape(title)
            title_html = f'<div id="cta-title" data-motion-role="cta-title" class="cta-title">{esc_title}</div>'
            semantic_ids.append("cta-title")

        content = f"""
        <style>
        .cta-container {{ display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100%; width: 100%; padding: 0 100px {CANONICAL_SUBTITLE_SAFE_BOTTOM_PX}px 100px; box-sizing: border-box; }}
        .cta-accent {{ width: 120px; height: 8px; background-color: var(--accent); border-radius: 4px; margin-bottom: 40px; }}
        .cta-title {{ font-size: 64px; font-weight: 700; color: var(--text-primary); margin-bottom: 40px; text-align: center; word-wrap: break-word; }}
        .cta-text {{ font-size: 48px; font-weight: 600; color: var(--bg); background-color: var(--accent); padding: 24px 64px; border-radius: 40px; text-align: center; display: inline-block; box-shadow: 0 20px 40px rgba(59, 130, 246, 0.4); word-wrap: break-word; }}
        </style>
        <div class="cta-container">
            <div id="cta-accent" data-motion-role="cta-accent" class="cta-accent"></div>
            {title_html}
            <div id="cta-text" data-motion-role="cta-text" class="cta-text">{cta_text}</div>
        </div>
        """
        return content, semantic_ids
