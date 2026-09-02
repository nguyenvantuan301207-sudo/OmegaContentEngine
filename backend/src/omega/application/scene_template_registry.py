import enum

from pydantic import BaseModel, ConfigDict

from omega.application.visual_direction import (
    VisualAssetKind,
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
)


class TemplateAspectRatio(enum.StrEnum):
    LANDSCAPE_16_9 = "LANDSCAPE_16_9"
    PORTRAIT_9_16 = "PORTRAIT_9_16"
    SQUARE_1_1 = "SQUARE_1_1"


class TemplateInputKey(enum.StrEnum):
    TITLE = "TITLE"
    SUBTITLE = "SUBTITLE"
    BODY = "BODY"
    ITEMS = "ITEMS"
    NODES = "NODES"
    EDGES = "EDGES"
    METRIC = "METRIC"
    METRIC_LABEL = "METRIC_LABEL"
    CODE = "CODE"
    LANGUAGE = "LANGUAGE"
    CAPTION = "CAPTION"
    HEADLINE = "HEADLINE"
    SOURCE = "SOURCE"
    QUOTE = "QUOTE"
    ATTRIBUTION = "ATTRIBUTION"
    CTA_TEXT = "CTA_TEXT"


class TemplateCapability(enum.StrEnum):
    TEXT = "TEXT"
    MOTION = "MOTION"
    DIAGRAM = "DIAGRAM"
    METRIC = "METRIC"
    CODE = "CODE"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    SCREENSHOT = "SCREENSHOT"
    LIST = "LIST"
    ICON = "ICON"


class SceneTemplateDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    template_id: VisualTemplateId
    version: str = "1.0"
    supported_render_modes: tuple[VisualRenderMode, ...]
    supported_aspect_ratios: tuple[TemplateAspectRatio, ...]
    required_inputs: tuple[TemplateInputKey, ...]
    optional_inputs: tuple[TemplateInputKey, ...]
    required_asset_kinds: tuple[VisualAssetKind, ...]
    allowed_asset_kinds: tuple[VisualAssetKind, ...]
    motion_profiles: tuple[str, ...]
    capabilities: tuple[TemplateCapability, ...]
    fallback_template_id: VisualTemplateId | None = None


class SceneTemplateRegistryError(ValueError):
    pass


# Central registry definition
_DEFINITIONS = (
    SceneTemplateDefinition(
        template_id=VisualTemplateId.HERO_TITLE,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.TITLE,),
        optional_inputs=(TemplateInputKey.SUBTITLE,),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("title_reveal",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.KINETIC_TEXT,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.BODY,),
        optional_inputs=(),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("kinetic_phrase",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.NODES,),
        optional_inputs=(TemplateInputKey.TITLE, TemplateInputKey.EDGES),
        required_asset_kinds=(),
        allowed_asset_kinds=(VisualAssetKind.ICON,),
        motion_profiles=("sequential_flow",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.DIAGRAM, TemplateCapability.ICON),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.STATISTIC_HERO,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.METRIC,),
        optional_inputs=(TemplateInputKey.TITLE, TemplateInputKey.METRIC_LABEL),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("metric_emphasis",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.METRIC),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.CODE_EDITOR,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.CODE,),
        optional_inputs=(TemplateInputKey.TITLE, TemplateInputKey.LANGUAGE),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("code_type_on",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.CODE),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.INFOGRAPHIC,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.ITEMS,),
        optional_inputs=(TemplateInputKey.TITLE,),
        required_asset_kinds=(),
        allowed_asset_kinds=(VisualAssetKind.ICON,),
        motion_profiles=("infographic_reveal",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.LIST, TemplateCapability.ICON),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.IMAGE_EXPLAINER,
        supported_render_modes=(VisualRenderMode.HYBRID,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.BODY,),
        optional_inputs=(TemplateInputKey.TITLE, TemplateInputKey.CAPTION),
        required_asset_kinds=(VisualAssetKind.IMAGE,),
        allowed_asset_kinds=(VisualAssetKind.IMAGE, VisualAssetKind.ICON),
        motion_profiles=("image_explainer",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.IMAGE, TemplateCapability.ICON),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        supported_render_modes=(VisualRenderMode.BROLL,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.BODY,),
        optional_inputs=(TemplateInputKey.TITLE, TemplateInputKey.CAPTION),
        required_asset_kinds=(VisualAssetKind.BROLL,),
        allowed_asset_kinds=(VisualAssetKind.BROLL, VisualAssetKind.ICON),
        motion_profiles=("broll_overlay",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.VIDEO, TemplateCapability.ICON),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.SCREENSHOT_FOCUS,
        supported_render_modes=(VisualRenderMode.SCREENSHOT,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.BODY,),
        optional_inputs=(TemplateInputKey.TITLE, TemplateInputKey.CAPTION),
        required_asset_kinds=(VisualAssetKind.SCREENSHOT,),
        allowed_asset_kinds=(VisualAssetKind.SCREENSHOT, VisualAssetKind.ICON),
        motion_profiles=("screenshot_focus",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.SCREENSHOT, TemplateCapability.ICON),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.COMPARISON,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.ITEMS,),
        optional_inputs=(TemplateInputKey.TITLE,),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("comparison_reveal",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.LIST, TemplateCapability.ICON),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.TIMELINE,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.ITEMS,),
        optional_inputs=(TemplateInputKey.TITLE,),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("timeline_progress",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.LIST, TemplateCapability.ICON),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.LIST,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.ITEMS,),
        optional_inputs=(TemplateInputKey.TITLE,),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("list_reveal",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.LIST, TemplateCapability.ICON),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.NEWS_CARD,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.HEADLINE,),
        optional_inputs=(TemplateInputKey.SOURCE, TemplateInputKey.BODY),
        required_asset_kinds=(),
        allowed_asset_kinds=(VisualAssetKind.IMAGE,),
        motion_profiles=("news_card_reveal",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.IMAGE),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.QUOTE,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.QUOTE,),
        optional_inputs=(TemplateInputKey.ATTRIBUTION,),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("quote_reveal",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.RECAP,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.ITEMS,),
        optional_inputs=(TemplateInputKey.TITLE,),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("recap_reveal",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION, TemplateCapability.LIST),
    ),
    SceneTemplateDefinition(
        template_id=VisualTemplateId.CTA,
        supported_render_modes=(VisualRenderMode.TEMPLATE,),
        supported_aspect_ratios=(TemplateAspectRatio.LANDSCAPE_16_9,),
        required_inputs=(TemplateInputKey.CTA_TEXT,),
        optional_inputs=(TemplateInputKey.TITLE,),
        required_asset_kinds=(),
        allowed_asset_kinds=(),
        motion_profiles=("cta_reveal",),
        capabilities=(TemplateCapability.TEXT, TemplateCapability.MOTION),
    ),
)


class SceneTemplateRegistry:
    def __init__(self, definitions: tuple[SceneTemplateDefinition, ...] | None = None):
        if definitions is None:
            definitions = _DEFINITIONS

        ids = [d.template_id for d in definitions]
        if len(ids) != len(set(ids)):
            from collections import Counter
            duplicates = [k for k, v in Counter(ids).items() if v > 1]
            raise SceneTemplateRegistryError(f"Duplicate template definitions detected for IDs: {duplicates}")

        self._definitions = {d.template_id: d for d in definitions}

        # Verify completeness statically
        all_ids = set(VisualTemplateId)
        registered_ids = set(self._definitions.keys())
        if all_ids != registered_ids:
            missing = all_ids - registered_ids
            raise SceneTemplateRegistryError(f"Registry incomplete. Missing: {missing}")

    def get(self, template_id: VisualTemplateId) -> SceneTemplateDefinition:
        if template_id not in self._definitions:
            raise SceneTemplateRegistryError(f"Unknown template ID: {template_id}")
        return self._definitions[template_id]

    def all(self) -> tuple[SceneTemplateDefinition, ...]:
        return _DEFINITIONS

    def validate_direction(self, direction: VisualDirection) -> SceneTemplateDefinition:
        if direction.template_id is None:
            raise SceneTemplateRegistryError("Validation requires direction.template_id to be set.")

        template = self.get(direction.template_id)

        if direction.render_mode not in template.supported_render_modes:
            raise SceneTemplateRegistryError(
                f"Render mode {direction.render_mode} not supported by {template.template_id}."
            )

        direction_asset_kinds = [req.kind for req in direction.asset_requirements]

        for req_kind in template.required_asset_kinds:
            if req_kind not in direction_asset_kinds:
                raise SceneTemplateRegistryError(
                    f"Template {template.template_id} requires asset kind {req_kind}."
                )

        for dir_kind in direction_asset_kinds:
            if dir_kind not in template.allowed_asset_kinds and dir_kind not in template.required_asset_kinds:
                raise SceneTemplateRegistryError(
                    f"Asset kind {dir_kind} is not allowed in template {template.template_id}."
                )

        if direction.motion_profile is not None and direction.motion_profile not in template.motion_profiles:
            raise SceneTemplateRegistryError(
                f"Motion profile {direction.motion_profile} not supported by {template.template_id}."
            )

        return template
