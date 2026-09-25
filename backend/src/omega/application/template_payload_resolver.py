import inspect
import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from omega.application.editorial_beat import BeatSemanticRole
from omega.application.mechanism_diagram import resolve_mechanism_diagram_spec
from omega.application.scene_template_registry import (
    SceneTemplateRegistry,
    TemplateInputKey,
)
from omega.application.storyboard_engine import (
    StoryboardScene,
    extract_trustworthy_code,
    extract_trustworthy_metric,
)
from omega.application.visual_direction import (
    VisualAssetRequirement,
    VisualDirection,
    VisualTemplateId,
)

_INTERNAL_STRUCTURAL_EXACT = frozenset({
    "hook",
    "closing",
    "cta",
    "call to action",
    "call-to-action",
})

_INTERNAL_STRUCTURAL_PATTERN = re.compile(
    r"^(?:section|scene)\s*\d+$",
    re.IGNORECASE,
)


def is_internal_structural_label(label: str | None) -> bool:
    """Identify internal orchestration/structural planning labels that must not leak to viewers."""
    if not label or not label.strip():
        return True
    cleaned = label.strip()
    lower = cleaned.lower()
    if lower in _INTERNAL_STRUCTURAL_EXACT:
        return True
    return bool(_INTERNAL_STRUCTURAL_PATTERN.match(cleaned))


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
        sig = inspect.signature(self._resolve_inputs)
        if "direction" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            inputs = self._resolve_inputs(scene, direction.template_id, direction=direction)
        else:
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
            metadata={
                "visual_strategy": scene.visual_strategy.value,
                "importance": scene.importance,
            }
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

    def _resolve_inputs(
        self,
        scene: StoryboardScene,
        template_id: VisualTemplateId,
        direction: VisualDirection | None = None,
    ) -> dict[TemplateInputKey, Any]:
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

        viewer_title = None if is_internal_structural_label(sec_id) else sec_id

        if template_id == VisualTemplateId.HERO_TITLE:
            if viewer_title:
                inputs[TemplateInputKey.TITLE] = viewer_title
            elif sec_id and not is_internal_structural_label(sec_id):
                inputs[TemplateInputKey.TITLE] = sec_id
            if os_text and os_text.strip().lower() != narration.strip().lower():
                inputs[TemplateInputKey.SUBTITLE] = os_text

        elif template_id == VisualTemplateId.FLOW_DIAGRAM:
            if viewer_title:
                inputs[TemplateInputKey.TITLE] = viewer_title

            content = narration or os_text or brief

            # G2C2B0 gate: mechanism shared-authority extraction is strictly gated to G2 beats
            dir_metadata = direction.metadata if direction else {}
            is_g2_mechanism_beat = (
                isinstance(dir_metadata.get("beat_index"), int)
                and dir_metadata.get("beat_index") >= 0
                and dir_metadata.get("semantic_role") == BeatSemanticRole.MECHANISM.value
            )

            mech_spec = resolve_mechanism_diagram_spec(content) if is_g2_mechanism_beat else None
            if mech_spec is not None and len(mech_spec.nodes) >= 2:
                inputs[TemplateInputKey.NODES] = list(mech_spec.nodes)
                inputs[TemplateInputKey.EDGES] = [
                    TemplateEdge(from_node=e.from_node, to_node=e.to_node)
                    for e in mech_spec.edges
                ]
            else:
                nodes, edges = self._extract_diagram(content)
                if len(nodes) >= 2:
                    inputs[TemplateInputKey.NODES] = nodes
                    inputs[TemplateInputKey.EDGES] = edges
                else:
                    raise TemplatePayloadError("Could not extract at least two trustworthy diagram nodes.")

        elif template_id == VisualTemplateId.STATISTIC_HERO:
            if viewer_title:
                inputs[TemplateInputKey.TITLE] = viewer_title

            content = narration or os_text or brief
            metric = self._extract_metric(content)
            if metric:
                inputs[TemplateInputKey.METRIC] = metric
                inputs[TemplateInputKey.METRIC_LABEL] = content
            else:
                raise TemplatePayloadError("Could not extract a trustworthy metric.")

        elif template_id == VisualTemplateId.CODE_EDITOR:
            if viewer_title:
                inputs[TemplateInputKey.TITLE] = viewer_title

            content = narration or os_text or brief
            code, lang = self._extract_code(content)
            if code:
                inputs[TemplateInputKey.CODE] = code
                if lang:
                    inputs[TemplateInputKey.LANGUAGE] = lang
            else:
                raise TemplatePayloadError("Could not extract trustworthy code.")

        elif template_id in (VisualTemplateId.IMAGE_EXPLAINER, VisualTemplateId.BROLL_EXPLAINER, VisualTemplateId.SCREENSHOT_FOCUS):
            if viewer_title:
                inputs[TemplateInputKey.TITLE] = viewer_title
            # G1A: BODY must NOT duplicate full spoken narration paragraph.
            # Only use concise on_screen_text if present and distinct from narration.
            if os_text and os_text.strip().lower() != narration.strip().lower():
                inputs[TemplateInputKey.BODY] = os_text
            elif template_id == VisualTemplateId.SCREENSHOT_FOCUS and narration:
                inputs[TemplateInputKey.BODY] = narration

        elif template_id == VisualTemplateId.KINETIC_TEXT:
            inputs[TemplateInputKey.BODY] = os_text or narration

        elif template_id in (VisualTemplateId.INFOGRAPHIC, VisualTemplateId.COMPARISON, VisualTemplateId.TIMELINE, VisualTemplateId.LIST, VisualTemplateId.RECAP):
            if viewer_title:
                inputs[TemplateInputKey.TITLE] = viewer_title
            items = self._extract_items(narration)
            if items:
                inputs[TemplateInputKey.ITEMS] = items

        elif template_id == VisualTemplateId.CTA:
            if viewer_title:
                inputs[TemplateInputKey.TITLE] = viewer_title
            inputs[TemplateInputKey.CTA_TEXT] = os_text or narration

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

        if len(raw_nodes) < 2:
            conceptual_pattern = re.compile(
                r"\b(?:"
                r"(?:system|software|service|data|network|request|event|ingestion|processing|delivery|deployment|workflow)\s+"
                r"(?:architecture|workflow|pipeline|process|flow|relationship|components?|stages?)"
                r"|architecture|workflow|pipeline|process|flow|relationship|components?|stages?"
                r")\b",
                re.IGNORECASE,
            )
            for match in conceptual_pattern.finditer(text):
                node = match.group(0).strip().lower()
                if node not in raw_nodes:
                    raw_nodes.append(node)

        nodes = raw_nodes[:5]
        edges = []
        for i in range(len(nodes) - 1):
            edges.append(TemplateEdge(from_node=nodes[i], to_node=nodes[i+1]))

        return nodes, edges

    def _extract_metric(self, text: str) -> str | None:
        return extract_trustworthy_metric(text)

    def _extract_code(self, text: str) -> tuple[str | None, str | None]:
        return extract_trustworthy_code(text)

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
