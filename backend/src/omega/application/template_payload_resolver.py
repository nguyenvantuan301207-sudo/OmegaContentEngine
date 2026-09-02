import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from omega.application.scene_template_registry import (
    SceneTemplateRegistry,
    TemplateInputKey,
)
from omega.application.storyboard_engine import StoryboardScene
from omega.application.visual_direction import (
    VisualAssetRequirement,
    VisualDirection,
    VisualTemplateId,
)


class TemplateEdge(BaseModel):
    model_config = ConfigDict(frozen=True)
    from_node: str
    to_node: str


class TemplatePayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    scene_index: int
    template_id: VisualTemplateId
    inputs: dict[TemplateInputKey, Any]
    asset_requirements: tuple[VisualAssetRequirement, ...]
    motion_profile: str | None
    metadata: dict[str, Any]


class TemplatePayloadError(ValueError):
    pass


class TemplatePayloadResolver:
    def __init__(self, registry: SceneTemplateRegistry | None = None):
        self.registry = registry or SceneTemplateRegistry()

    def resolve(
        self,
        scene: StoryboardScene,
        direction: VisualDirection,
        registry: SceneTemplateRegistry | None = None,
    ) -> TemplatePayload:
        reg = registry or self.registry

        # 1. Require direction.template_id FIRST
        if direction.template_id is None:
            raise TemplatePayloadError("Direction missing template_id")

        # 2. Validate VisualDirection using SceneTemplateRegistry
        definition = reg.validate_direction(direction)

        # 3. Resolve semantic inputs
        inputs = self._resolve_inputs(scene, direction.template_id)

        # 4. Validate required inputs exist and are meaningful
        for req_key in definition.required_inputs:
            val = inputs.get(req_key)
            if not self._is_meaningful(val):
                raise TemplatePayloadError(f"Template {definition.template_id} missing required input: {req_key}")

        # 5. Reject unsupported/unexpected input keys
        allowed_keys = set(definition.required_inputs) | set(definition.optional_inputs)
        for key in inputs:
            if key not in allowed_keys:
                raise TemplatePayloadError(f"Unexpected input key {key} for template {definition.template_id}")

        # Clean empty optional inputs
        final_inputs = {k: v for k, v in inputs.items() if self._is_meaningful(v)}

        return TemplatePayload(
            scene_index=scene.sequence_index,
            template_id=direction.template_id,
            inputs=final_inputs,
            asset_requirements=tuple(direction.asset_requirements),
            motion_profile=direction.motion_profile,
            metadata={}
        )

    def _is_meaningful(self, val: Any) -> bool:
        if val is None:
            return False
        if isinstance(val, str):
            if not val.strip():
                return False
            if val.strip().lower() in ("test", "placeholder"):
                return False
        return not (isinstance(val, (list, tuple, dict, set)) and len(val) == 0)

    def _resolve_inputs(self, scene: StoryboardScene, template_id: VisualTemplateId) -> dict[TemplateInputKey, Any]:
        inputs: dict[TemplateInputKey, Any] = {}

        def meaningful_str(val: str | None) -> str | None:
            if val and val.strip():
                if val.strip().lower() in ("test", "placeholder"):
                    return None
                return val.strip()
            return None

        sec_id = meaningful_str(scene.section_id)
        narration = meaningful_str(scene.narration_excerpt) or ""
        os_text = meaningful_str(scene.on_screen_text)
        brief = meaningful_str(scene.visual_brief) or ""

        if template_id == VisualTemplateId.HERO_TITLE:
            if sec_id:
                inputs[TemplateInputKey.TITLE] = sec_id
            if os_text:
                inputs[TemplateInputKey.SUBTITLE] = os_text

        elif template_id == VisualTemplateId.FLOW_DIAGRAM:
            if sec_id:
                inputs[TemplateInputKey.TITLE] = sec_id

            content = narration or os_text or brief
            nodes, edges = self._extract_diagram(content)
            if len(nodes) >= 2:
                inputs[TemplateInputKey.NODES] = nodes
                inputs[TemplateInputKey.EDGES] = edges
            else:
                raise TemplatePayloadError("Could not extract at least two trustworthy diagram nodes.")

        elif template_id == VisualTemplateId.STATISTIC_HERO:
            if sec_id:
                inputs[TemplateInputKey.TITLE] = sec_id

            content = narration or os_text or brief
            metric = self._extract_metric(content)
            if metric:
                inputs[TemplateInputKey.METRIC] = metric
                inputs[TemplateInputKey.METRIC_LABEL] = content
            else:
                raise TemplatePayloadError("Could not extract a trustworthy metric.")

        elif template_id == VisualTemplateId.CODE_EDITOR:
            if sec_id:
                inputs[TemplateInputKey.TITLE] = sec_id

            content = narration or os_text or brief
            code, lang = self._extract_code(content)
            if code:
                inputs[TemplateInputKey.CODE] = code
                if lang:
                    inputs[TemplateInputKey.LANGUAGE] = lang
            else:
                raise TemplatePayloadError("Could not extract trustworthy code.")

        elif template_id in (VisualTemplateId.IMAGE_EXPLAINER, VisualTemplateId.BROLL_EXPLAINER, VisualTemplateId.SCREENSHOT_FOCUS):
            inputs[TemplateInputKey.BODY] = narration
            if sec_id:
                inputs[TemplateInputKey.TITLE] = sec_id
            if os_text and os_text != narration:
                inputs[TemplateInputKey.CAPTION] = os_text

        elif template_id == VisualTemplateId.KINETIC_TEXT:
            inputs[TemplateInputKey.BODY] = os_text or narration

        elif template_id in (VisualTemplateId.INFOGRAPHIC, VisualTemplateId.COMPARISON, VisualTemplateId.TIMELINE, VisualTemplateId.LIST, VisualTemplateId.RECAP):
            if sec_id:
                inputs[TemplateInputKey.TITLE] = sec_id
            items = self._extract_items(narration)
            if items:
                inputs[TemplateInputKey.ITEMS] = items

        elif template_id == VisualTemplateId.CTA:
            inputs[TemplateInputKey.CTA_TEXT] = os_text or narration
            if sec_id:
                inputs[TemplateInputKey.TITLE] = sec_id

        elif template_id == VisualTemplateId.NEWS_CARD:
            inputs[TemplateInputKey.HEADLINE] = os_text or self._first_sentence(narration)
            if scene.citations and len(scene.citations) > 0:
                cit = scene.citations[0]
                if isinstance(cit, dict) and "source" in cit:
                    inputs[TemplateInputKey.SOURCE] = cit["source"]
            inputs[TemplateInputKey.BODY] = narration

        elif template_id == VisualTemplateId.QUOTE:
            quote = self._extract_quote(narration)
            if quote:
                inputs[TemplateInputKey.QUOTE] = quote

            if scene.citations and len(scene.citations) > 0:
                cit = scene.citations[0]
                if isinstance(cit, dict) and "attribution" in cit:
                    inputs[TemplateInputKey.ATTRIBUTION] = cit["attribution"]

        return inputs

    def _extract_diagram(self, text: str) -> tuple[list[str], list[TemplateEdge]]:
        matches = re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", text)
        raw_nodes = []
        for m in matches:
            node = m.group(1).strip()
            node = re.sub(r"^(The|A|An)\s+", "", node, flags=re.IGNORECASE)
            if node and node not in ("The", "A", "An") and node not in raw_nodes:
                raw_nodes.append(node)

        nodes = raw_nodes[:5]
        edges = []
        for i in range(len(nodes) - 1):
            edges.append(TemplateEdge(from_node=nodes[i], to_node=nodes[i+1]))

        return nodes, edges

    def _extract_metric(self, text: str) -> str | None:
        m = re.search(r'\b(\d+(?:\.\d+)?(?:%|ms|x|k|M|m|s))\b', text)
        if m:
            return m.group(1)
        m2 = re.search(r'(\d+(?:\.\d+)?(?:%|ms|x|k|M|m|s))(?:\s|$|\.|\,)', text)
        if m2:
            return m2.group(1)
        return None

    def _extract_code(self, text: str) -> tuple[str | None, str | None]:
        m = re.search(r"```(?P<lang>\w+)?\n(?P<code>.*?)```", text, re.DOTALL)
        if m:
            return m.group("code").strip(), m.group("lang")

        if "def " in text:
            m2 = re.search(r"(def\s+.*?:.*)", text)
            if m2:
                return m2.group(1).strip(), "python"

        if "function " in text:
            m3 = re.search(r"(function\s+.*?\s*\{.*?\})", text)
            if m3:
                return m3.group(1).strip(), "javascript"

        if "SELECT " in text.upper():
            m4 = re.search(r"(SELECT\s+.*)", text, re.IGNORECASE)
            if m4:
                return m4.group(1).strip(), "sql"

        if "def " in text:
            return text.strip(), "python"

        return None, None

    def _extract_items(self, text: str) -> list[str]:
        if not text:
            return []

        if ";" in text:
            raw = text.split(";")
        elif "." in text:
            raw = text.split(".")
        else:
            raw = [text]

        items = []
        for r in raw:
            s = r.strip()
            if s and s not in items:
                items.append(s)
        return items[:6]

    def _first_sentence(self, text: str) -> str:
        if not text:
            return ""
        return text.split(".")[0].strip() + ("." if "." in text else "")

    def _extract_quote(self, text: str) -> str | None:
        m = re.search(r'"([^"]+)"', text)
        if m:
            return m.group(1)
        return None
