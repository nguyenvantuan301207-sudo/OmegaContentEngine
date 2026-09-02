import enum
from typing import Any

from pydantic import BaseModel, Field

from omega.application.storyboard_engine import StoryboardScene, VisualStrategy


class VisualRenderMode(enum.StrEnum):
    TEMPLATE = "TEMPLATE"
    GENERATIVE_IMAGE = "GENERATIVE_IMAGE"
    GENERATIVE_VIDEO = "GENERATIVE_VIDEO"
    BROLL = "BROLL"
    SCREENSHOT = "SCREENSHOT"
    HYBRID = "HYBRID"


class VisualTemplateId(enum.StrEnum):
    HERO_TITLE = "HERO_TITLE"
    KINETIC_TEXT = "KINETIC_TEXT"
    FLOW_DIAGRAM = "FLOW_DIAGRAM"
    STATISTIC_HERO = "STATISTIC_HERO"
    CODE_EDITOR = "CODE_EDITOR"
    INFOGRAPHIC = "INFOGRAPHIC"
    IMAGE_EXPLAINER = "IMAGE_EXPLAINER"
    BROLL_EXPLAINER = "BROLL_EXPLAINER"
    SCREENSHOT_FOCUS = "SCREENSHOT_FOCUS"
    COMPARISON = "COMPARISON"
    TIMELINE = "TIMELINE"
    LIST = "LIST"
    NEWS_CARD = "NEWS_CARD"
    QUOTE = "QUOTE"
    RECAP = "RECAP"
    CTA = "CTA"


class VisualAssetKind(enum.StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    BROLL = "BROLL"
    SCREENSHOT = "SCREENSHOT"
    ICON = "ICON"


class VisualAssetRequirement(BaseModel):
    kind: VisualAssetKind
    query_hint: str | None
    purpose: str
    required: bool = True


class VisualDirection(BaseModel):
    scene_index: int
    render_mode: VisualRenderMode
    template_id: VisualTemplateId | None
    asset_requirements: list[VisualAssetRequirement]
    motion_profile: str | None
    rationale: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class VisualDirectionError(ValueError):
    pass


_STRATEGY_MAPPING = {
    VisualStrategy.TITLE_MOTION: (VisualRenderMode.TEMPLATE, VisualTemplateId.HERO_TITLE, "title_reveal", "TITLE_MOTION strategy maps to a template-based hero title."),
    VisualStrategy.KINETIC_TEXT: (VisualRenderMode.TEMPLATE, VisualTemplateId.KINETIC_TEXT, "kinetic_phrase", "KINETIC_TEXT strategy maps to a template-based kinetic text."),
    VisualStrategy.DIAGRAM: (VisualRenderMode.TEMPLATE, VisualTemplateId.FLOW_DIAGRAM, "sequential_flow", "DIAGRAM strategy maps to a template-based flow diagram."),
    VisualStrategy.STATISTIC: (VisualRenderMode.TEMPLATE, VisualTemplateId.STATISTIC_HERO, "metric_emphasis", "STATISTIC strategy maps to a template-based statistic hero."),
    VisualStrategy.CODE_DEMO: (VisualRenderMode.TEMPLATE, VisualTemplateId.CODE_EDITOR, "code_type_on", "CODE_DEMO strategy maps to a template-based code editor."),
    VisualStrategy.INFOGRAPHIC: (VisualRenderMode.TEMPLATE, VisualTemplateId.INFOGRAPHIC, "infographic_reveal", "INFOGRAPHIC strategy maps to a template-based infographic."),
    VisualStrategy.CTA: (VisualRenderMode.TEMPLATE, VisualTemplateId.CTA, "cta_reveal", "CTA strategy maps to a template-based CTA."),
    VisualStrategy.IMAGE: (VisualRenderMode.HYBRID, VisualTemplateId.IMAGE_EXPLAINER, "image_explainer", "IMAGE strategy maps to a hybrid image explainer."),
    VisualStrategy.BROLL: (VisualRenderMode.BROLL, VisualTemplateId.BROLL_EXPLAINER, "broll_overlay", "BROLL strategy maps to a broll explainer."),
    VisualStrategy.SCREENSHOT: (VisualRenderMode.SCREENSHOT, VisualTemplateId.SCREENSHOT_FOCUS, "screenshot_focus", "SCREENSHOT strategy maps to a screenshot focus."),
}


class VisualDirector:
    def _is_meaningful_query(self, hint: str | None) -> bool:
        if not hint:
            return False
        clean = hint.strip().lower()
        if not clean:
            return False
        if "abstract technology background" in clean:
            return False
        return clean not in ["placeholder", "generic"]

    def _resolve_query_hint(self, scene: StoryboardScene) -> str | None:
        if self._is_meaningful_query(scene.asset_query_hint):
            return scene.asset_query_hint
        if self._is_meaningful_query(scene.visual_brief):
            return scene.visual_brief
        return None

    def resolve(self, scene: StoryboardScene) -> VisualDirection:
        if scene.visual_strategy not in _STRATEGY_MAPPING:
            raise VisualDirectionError(f"Unsupported VisualStrategy: {scene.visual_strategy}")

        render_mode, template_id, motion_profile, rationale = _STRATEGY_MAPPING[scene.visual_strategy]

        assets = []
        if scene.visual_strategy == VisualStrategy.IMAGE:
            assets.append(VisualAssetRequirement(
                kind=VisualAssetKind.IMAGE,
                query_hint=self._resolve_query_hint(scene),
                purpose="Primary visual for image explainer"
            ))
        elif scene.visual_strategy == VisualStrategy.BROLL:
            assets.append(VisualAssetRequirement(
                kind=VisualAssetKind.BROLL,
                query_hint=self._resolve_query_hint(scene),
                purpose="Primary visual for broll explainer"
            ))
        elif scene.visual_strategy == VisualStrategy.SCREENSHOT:
            assets.append(VisualAssetRequirement(
                kind=VisualAssetKind.SCREENSHOT,
                query_hint=self._resolve_query_hint(scene),
                purpose="Primary visual for screenshot focus"
            ))

        return VisualDirection(
            scene_index=scene.sequence_index,
            render_mode=render_mode,
            template_id=template_id,
            asset_requirements=assets,
            motion_profile=motion_profile,
            rationale=rationale
        )
