import hashlib
import html
from typing import Any

from pydantic import BaseModel, ConfigDict

from omega.application.scene_template_registry import SceneTemplateRegistry, TemplateInputKey
from omega.application.template_payload_resolver import TemplatePayload
from omega.application.visual_direction import VisualTemplateId


class RenderedTemplateDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_index: int
    template_id: VisualTemplateId
    width: int
    height: int
    html: str
    semantic_element_ids: tuple[str, ...]
    content_sha256: str


class VisualTemplateRenderError(ValueError):
    pass


class VisualTemplateRenderer:
    def __init__(self, registry: SceneTemplateRegistry | None = None):
        self.registry = registry or SceneTemplateRegistry()

    def render(self, payload: TemplatePayload) -> RenderedTemplateDocument:
        if payload.template_id not in (
            VisualTemplateId.HERO_TITLE,
            VisualTemplateId.FLOW_DIAGRAM,
            VisualTemplateId.STATISTIC_HERO,
            VisualTemplateId.CODE_EDITOR,
            VisualTemplateId.IMAGE_EXPLAINER,
        ):
            raise VisualTemplateRenderError(f"Unsupported template_id {payload.template_id}")

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

        semantic_ids: list[str] = []
        html_content = ""

        if payload.template_id == VisualTemplateId.HERO_TITLE:
            html_content, semantic_ids = self._render_hero_title(payload)
        elif payload.template_id == VisualTemplateId.FLOW_DIAGRAM:
            html_content, semantic_ids = self._render_flow_diagram(payload)
        elif payload.template_id == VisualTemplateId.STATISTIC_HERO:
            html_content, semantic_ids = self._render_statistic_hero(payload)
        elif payload.template_id == VisualTemplateId.CODE_EDITOR:
            html_content, semantic_ids = self._render_code_editor(payload)
        elif payload.template_id == VisualTemplateId.IMAGE_EXPLAINER:
            html_content, semantic_ids = self._render_image_explainer(payload)

        # Wrap in full HTML document
        final_html = self._wrap_in_document(html_content)

        sha256 = hashlib.sha256(final_html.encode("utf-8")).hexdigest()
        return RenderedTemplateDocument(
            scene_index=payload.scene_index,
            template_id=payload.template_id,
            width=1920,
            height=1080,
            html=final_html,
            semantic_element_ids=tuple(semantic_ids),
            content_sha256=sha256,
        )

    def _is_meaningful(self, val: Any) -> bool:
        if val is None:
            return False
        if isinstance(val, str) and not val.strip():
            return False
        return not (isinstance(val, (list, tuple, dict, set)) and len(val) == 0)

    def _wrap_in_document(self, body_content: str) -> str:
        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
{self._get_base_css()}
</style>
</head>
<body>
<div id="scene-root" class="scene-root">
{body_content}
</div>
</body>
</html>"""

    def _get_base_css(self) -> str:
        return """
        :root {
            --bg: #0B0F19;
            --text-primary: #F1F5F9;
            --text-secondary: #94A3B8;
            --accent: #3B82F6;
            --surface: rgba(30, 41, 59, 0.7);
            --surface-border: rgba(255, 255, 255, 0.1);
        }
        body, html {
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
        }
        .scene-root {
            width: 1920px;
            height: 1080px;
            position: relative;
            padding: 90px 120px;
            box-sizing: border-box;
            background: radial-gradient(circle at top left, rgba(59, 130, 246, 0.1), transparent 50%),
                        radial-gradient(circle at bottom right, rgba(99, 102, 241, 0.05), transparent 50%);
            display: flex;
        }
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

        num_nodes = len(nodes)

        nodes_html = ""
        for i, node in enumerate(nodes):
            esc_node = html.escape(node)
            node_id = f"flow-node-{i}"
            nodes_html += f'<div id="{node_id}" data-motion-role="flow-node" data-motion-index="{i}" class="flow-node">{esc_node}</div>\n'
            semantic_ids.append(node_id)

            if i < num_nodes - 1:
                edge_id = f"flow-edge-{i}"
                nodes_html += f"""
                <div id="{edge_id}" data-motion-role="flow-edge" data-motion-index="{i}" class="flow-edge">
                    <svg width="40" height="24" viewBox="0 0 40 24" fill="none">
                        <path d="M0 12 H36 M28 4 L38 12 L28 20" stroke="var(--accent)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                """
                semantic_ids.append(edge_id)

        content = f"""
        <style>
        .flow-container {{ display: flex; flex-direction: column; height: 100%; width: 100%; }}
        .section-title {{ font-size: 32px; font-weight: 600; color: var(--accent); margin-bottom: 80px; letter-spacing: 0.05em; text-transform: uppercase; }}
        .flow-diagram-area {{ display: flex; flex-direction: row; align-items: center; justify-content: center; flex-grow: 1; flex-wrap: wrap; gap: 20px; }}
        .flow-node {{ background: var(--surface); border: 1px solid var(--surface-border); padding: 40px; border-radius: 16px; font-size: 36px; font-weight: 600; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.3); min-width: 200px; max-width: 300px; word-wrap: break-word; }}
        .flow-edge {{ display: flex; align-items: center; justify-content: center; padding: 0 10px; }}
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

    def _render_image_explainer(self, payload: TemplatePayload) -> tuple[str, list[str]]:
        body = html.escape(payload.inputs[TemplateInputKey.BODY])
        title = payload.inputs.get(TemplateInputKey.TITLE)
        caption = payload.inputs.get(TemplateInputKey.CAPTION)

        semantic_ids = ["scene-root", "image-body", "image-frame"]

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

        content = f"""
        <style>
        .split-container {{ display: flex; height: 100%; width: 100%; gap: 60px; }}
        .split-text {{ flex: 1; display: flex; flex-direction: column; justify-content: center; max-width: 50%; }}
        .image-title {{ font-size: 32px; font-weight: 600; color: var(--accent); margin-bottom: 40px; letter-spacing: 0.05em; text-transform: uppercase; }}
        .image-body {{ font-size: 48px; line-height: 1.4; color: var(--text-primary); margin-bottom: 30px; word-wrap: break-word; }}
        .image-caption {{ font-size: 24px; color: var(--text-secondary); border-left: 4px solid var(--surface-border); padding-left: 16px; font-style: italic; word-wrap: break-word; }}
        .split-media {{ flex: 1; display: flex; align-items: center; justify-content: center; }}
        .image-frame {{ width: 100%; height: 800px; background: linear-gradient(135deg, rgba(59, 130, 246, 0.2), rgba(99, 102, 241, 0.1)); border: 1px solid var(--accent); border-radius: 20px; box-shadow: inset 0 0 100px rgba(0,0,0,0.5), 0 20px 40px rgba(0,0,0,0.3); overflow: hidden; position: relative; }}
        .image-frame::after {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background-image: repeating-linear-gradient(45deg, transparent, transparent 10px, rgba(255,255,255,0.02) 10px, rgba(255,255,255,0.02) 20px); }}
        </style>
        <div class="split-container">
            <div class="split-text">
                {title_html}
                <div id="image-body" class="image-body">{body}</div>
                {caption_html}
            </div>
            <div class="split-media">
                <div id="image-frame" data-asset-kind="IMAGE" data-motion-role="image-frame" class="image-frame"></div>
            </div>
        </div>
        """
        return content, semantic_ids
