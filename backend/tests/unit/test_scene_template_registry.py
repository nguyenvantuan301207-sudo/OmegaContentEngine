import pytest
from pydantic import ValidationError

from omega.application.scene_template_registry import (
    SceneTemplateRegistry,
    SceneTemplateRegistryError,
    TemplateAspectRatio,
    TemplateCapability,
    TemplateInputKey,
)
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualAssetRequirement,
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
)


@pytest.fixture
def registry():
    return SceneTemplateRegistry()


def test_registry_completeness(registry):
    definitions = registry.all()
    assert len(definitions) == len(VisualTemplateId)

    registered_ids = set(d.template_id for d in definitions)
    assert registered_ids == set(VisualTemplateId)


def test_no_duplicate_template_ids():
    from omega.application.scene_template_registry import _DEFINITIONS
    ids = [d.template_id for d in _DEFINITIONS]
    assert len(ids) == len(set(ids))


def test_duplicate_definitions_fail():
    from omega.application.scene_template_registry import _DEFINITIONS
    # Duplicate the first definition
    duplicate_defs = _DEFINITIONS + (_DEFINITIONS[0],)

    with pytest.raises(SceneTemplateRegistryError, match="Duplicate template definitions detected"):
        SceneTemplateRegistry(definitions=duplicate_defs)


def test_all_returns_instance_definitions(registry):
    reversed_defs = tuple(reversed(registry.all()))
    custom = SceneTemplateRegistry(definitions=reversed_defs)

    assert custom.all()[0].template_id == reversed_defs[0].template_id
    assert custom.get(custom.all()[0].template_id) == custom.all()[0]


def test_aspect_ratios_and_fallback(registry):
    for d in registry.all():
        assert d.supported_aspect_ratios == (TemplateAspectRatio.LANDSCAPE_16_9,)
        assert d.fallback_template_id is None


def test_hero_title_contract(registry):
    d = registry.get(VisualTemplateId.HERO_TITLE)
    assert d.supported_render_modes == (VisualRenderMode.TEMPLATE,)
    assert TemplateInputKey.TITLE in d.required_inputs
    assert TemplateInputKey.SUBTITLE in d.optional_inputs
    assert "title_reveal" in d.motion_profiles
    assert TemplateCapability.TEXT in d.capabilities


def test_flow_diagram_contract(registry):
    d = registry.get(VisualTemplateId.FLOW_DIAGRAM)
    assert d.supported_render_modes == (VisualRenderMode.TEMPLATE,)
    assert TemplateInputKey.NODES in d.required_inputs
    assert TemplateInputKey.EDGES in d.optional_inputs
    assert "sequential_flow" in d.motion_profiles
    assert TemplateCapability.DIAGRAM in d.capabilities
    assert VisualAssetKind.ICON in d.allowed_asset_kinds


def test_statistic_hero_contract(registry):
    d = registry.get(VisualTemplateId.STATISTIC_HERO)
    assert d.supported_render_modes == (VisualRenderMode.TEMPLATE,)
    assert TemplateInputKey.METRIC in d.required_inputs
    assert "metric_emphasis" in d.motion_profiles
    assert TemplateCapability.METRIC in d.capabilities


def test_code_editor_contract(registry):
    d = registry.get(VisualTemplateId.CODE_EDITOR)
    assert d.supported_render_modes == (VisualRenderMode.TEMPLATE,)
    assert TemplateInputKey.CODE in d.required_inputs
    assert "code_type_on" in d.motion_profiles
    assert TemplateCapability.CODE in d.capabilities


def test_image_explainer_contract(registry):
    d = registry.get(VisualTemplateId.IMAGE_EXPLAINER)
    assert d.supported_render_modes == (VisualRenderMode.HYBRID,)
    assert VisualAssetKind.IMAGE in d.required_asset_kinds
    assert VisualAssetKind.ICON in d.allowed_asset_kinds
    assert "image_explainer" in d.motion_profiles


def test_broll_explainer_contract(registry):
    d = registry.get(VisualTemplateId.BROLL_EXPLAINER)
    assert d.supported_render_modes == (VisualRenderMode.BROLL,)
    assert VisualAssetKind.BROLL in d.required_asset_kinds
    assert "broll_overlay" in d.motion_profiles


def test_screenshot_focus_contract(registry):
    d = registry.get(VisualTemplateId.SCREENSHOT_FOCUS)
    assert d.supported_render_modes == (VisualRenderMode.SCREENSHOT,)
    assert VisualAssetKind.SCREENSHOT in d.required_asset_kinds
    assert "screenshot_focus" in d.motion_profiles


def test_other_contracts_exist(registry):
    assert registry.get(VisualTemplateId.NEWS_CARD)
    assert registry.get(VisualTemplateId.COMPARISON)
    assert registry.get(VisualTemplateId.TIMELINE)
    assert registry.get(VisualTemplateId.LIST)
    assert registry.get(VisualTemplateId.QUOTE)
    assert registry.get(VisualTemplateId.RECAP)


def create_direction(template_id, render_mode, assets=None, motion=None):
    return VisualDirection(
        scene_index=1,
        render_mode=render_mode,
        template_id=template_id,
        asset_requirements=assets or [],
        motion_profile=motion,
        rationale="test"
    )


def test_validate_title_direction(registry):
    direction = create_direction(VisualTemplateId.HERO_TITLE, VisualRenderMode.TEMPLATE, motion="title_reveal")
    d = registry.validate_direction(direction)
    assert d.template_id == VisualTemplateId.HERO_TITLE


def test_validate_diagram_direction(registry):
    direction = create_direction(VisualTemplateId.FLOW_DIAGRAM, VisualRenderMode.TEMPLATE, motion="sequential_flow")
    registry.validate_direction(direction)


def test_validate_image_direction(registry):
    assets = [VisualAssetRequirement(kind=VisualAssetKind.IMAGE, purpose="test", query_hint=None)]
    direction = create_direction(VisualTemplateId.IMAGE_EXPLAINER, VisualRenderMode.HYBRID, assets=assets, motion="image_explainer")
    registry.validate_direction(direction)


def test_validate_broll_direction(registry):
    assets = [VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="test", query_hint=None)]
    direction = create_direction(VisualTemplateId.BROLL_EXPLAINER, VisualRenderMode.BROLL, assets=assets, motion="broll_overlay")
    registry.validate_direction(direction)


def test_validate_screenshot_direction(registry):
    assets = [VisualAssetRequirement(kind=VisualAssetKind.SCREENSHOT, purpose="test", query_hint=None)]
    direction = create_direction(VisualTemplateId.SCREENSHOT_FOCUS, VisualRenderMode.SCREENSHOT, assets=assets, motion="screenshot_focus")
    registry.validate_direction(direction)


def test_validate_missing_template_id(registry):
    direction = create_direction(None, VisualRenderMode.TEMPLATE)
    with pytest.raises(SceneTemplateRegistryError, match="requires direction.template_id"):
        registry.validate_direction(direction)


def test_validate_incompatible_render_mode(registry):
    direction = create_direction(VisualTemplateId.HERO_TITLE, VisualRenderMode.BROLL)
    with pytest.raises(SceneTemplateRegistryError, match="Render mode BROLL not supported"):
        registry.validate_direction(direction)


def test_validate_missing_required_asset(registry):
    direction = create_direction(VisualTemplateId.IMAGE_EXPLAINER, VisualRenderMode.HYBRID)
    with pytest.raises(SceneTemplateRegistryError, match="requires asset kind IMAGE"):
        registry.validate_direction(direction)


def test_validate_unexpected_asset_kind(registry):
    assets = [VisualAssetRequirement(kind=VisualAssetKind.VIDEO, purpose="test", query_hint=None)]
    direction = create_direction(VisualTemplateId.HERO_TITLE, VisualRenderMode.TEMPLATE, assets=assets)
    with pytest.raises(SceneTemplateRegistryError, match="Asset kind VIDEO is not allowed"):
        registry.validate_direction(direction)


def test_validate_incompatible_motion_profile(registry):
    direction = create_direction(VisualTemplateId.HERO_TITLE, VisualRenderMode.TEMPLATE, motion="sequential_flow")
    with pytest.raises(SceneTemplateRegistryError, match="Motion profile sequential_flow not supported"):
        registry.validate_direction(direction)


def test_validate_does_not_mutate(registry):
    direction = create_direction(VisualTemplateId.HERO_TITLE, VisualRenderMode.TEMPLATE)
    original_dump = direction.model_dump()
    registry.validate_direction(direction)
    assert direction.model_dump() == original_dump


def test_immutability(registry):
    d = registry.get(VisualTemplateId.HERO_TITLE)
    with pytest.raises(ValidationError):
        # pydantic frozen model raises ValidationError when modified
        d.version = "2.0"

    all_defs = registry.all()
    assert isinstance(all_defs, tuple)
