import pytest

from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualDirectionError,
    VisualDirector,
    VisualRenderMode,
    VisualTemplateId,
)


@pytest.fixture
def director():
    return VisualDirector()

def create_scene(strategy: VisualStrategy, query_hint: str = None, brief: str = None) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=1,
        section_id="test",
        purpose="test",
        source_statement_references=[1],
        narration_excerpt="test",
        estimated_duration_seconds=5.0,
        visual_strategy=strategy,
        visual_brief=brief or "",
        on_screen_text="test",
        motion_hint="",
        asset_query_hint=query_hint or ""
    )

def test_title_motion_mapping(director):
    scene = create_scene(VisualStrategy.TITLE_MOTION)
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.TEMPLATE
    assert direction.template_id == VisualTemplateId.HERO_TITLE
    assert direction.motion_profile == "title_reveal"
    assert len(direction.asset_requirements) == 0

def test_diagram_mapping(director):
    scene = create_scene(VisualStrategy.DIAGRAM)
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.TEMPLATE
    assert direction.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert direction.motion_profile == "sequential_flow"
    assert len(direction.asset_requirements) == 0

def test_statistic_mapping(director):
    scene = create_scene(VisualStrategy.STATISTIC)
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.TEMPLATE
    assert direction.template_id == VisualTemplateId.STATISTIC_HERO
    assert direction.motion_profile == "metric_emphasis"
    assert len(direction.asset_requirements) == 0

def test_code_demo_mapping(director):
    scene = create_scene(VisualStrategy.CODE_DEMO)
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.TEMPLATE
    assert direction.template_id == VisualTemplateId.CODE_EDITOR
    assert direction.motion_profile == "code_type_on"
    assert len(direction.asset_requirements) == 0

def test_kinetic_text_mapping(director):
    scene = create_scene(VisualStrategy.KINETIC_TEXT)
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.TEMPLATE
    assert direction.template_id == VisualTemplateId.KINETIC_TEXT
    assert direction.motion_profile == "kinetic_phrase"
    assert len(direction.asset_requirements) == 0

def test_image_mapping(director):
    scene = create_scene(VisualStrategy.IMAGE, query_hint="a red car")
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.HYBRID
    assert direction.template_id == VisualTemplateId.IMAGE_EXPLAINER
    assert direction.motion_profile == "image_explainer"
    assert len(direction.asset_requirements) == 1
    req = direction.asset_requirements[0]
    assert req.kind == VisualAssetKind.IMAGE
    assert req.query_hint == "a red car"

def test_broll_mapping(director):
    scene = create_scene(VisualStrategy.BROLL, query_hint="a busy street")
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.BROLL
    assert direction.template_id == VisualTemplateId.BROLL_EXPLAINER
    assert direction.motion_profile == "broll_overlay"
    assert len(direction.asset_requirements) == 1
    req = direction.asset_requirements[0]
    assert req.kind == VisualAssetKind.BROLL
    assert req.query_hint == "a busy street"

def test_screenshot_mapping(director):
    scene = create_scene(VisualStrategy.SCREENSHOT, query_hint="app ui")
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.SCREENSHOT
    assert direction.template_id == VisualTemplateId.SCREENSHOT_FOCUS
    assert direction.motion_profile == "screenshot_focus"
    assert len(direction.asset_requirements) == 1
    req = direction.asset_requirements[0]
    assert req.kind == VisualAssetKind.SCREENSHOT
    assert req.query_hint == "app ui"

def test_cta_mapping(director):
    scene = create_scene(VisualStrategy.CTA)
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.TEMPLATE
    assert direction.template_id == VisualTemplateId.CTA
    assert direction.motion_profile == "cta_reveal"
    assert len(direction.asset_requirements) == 0

def test_deterministic_output(director):
    scene = create_scene(VisualStrategy.DIAGRAM)
    d1 = director.resolve(scene)
    d2 = director.resolve(scene)
    assert d1.model_dump() == d2.model_dump()

def test_generative_modes_exist():
    assert VisualRenderMode.GENERATIVE_IMAGE.value == "GENERATIVE_IMAGE"
    assert VisualRenderMode.GENERATIVE_VIDEO.value == "GENERATIVE_VIDEO"
    # Ensure they are not the default mode for normal strategies
    director = VisualDirector()
    scene = create_scene(VisualStrategy.IMAGE)
    direction = director.resolve(scene)
    assert direction.render_mode != VisualRenderMode.GENERATIVE_IMAGE
    assert direction.render_mode != VisualRenderMode.GENERATIVE_VIDEO

def test_query_hint_preservation(director):
    scene = create_scene(VisualStrategy.IMAGE, query_hint="cloud architecture")
    direction = director.resolve(scene)
    assert direction.asset_requirements[0].query_hint == "cloud architecture"

def test_generic_placeholder_rejection(director):
    scene = create_scene(VisualStrategy.IMAGE, query_hint="abstract technology background")
    direction = director.resolve(scene)
    assert direction.asset_requirements[0].query_hint is None

def test_visual_brief_fallback(director):
    scene = create_scene(VisualStrategy.IMAGE, query_hint="abstract technology background", brief="server rack")
    direction = director.resolve(scene)
    assert direction.asset_requirements[0].query_hint == "server rack"

def test_no_legacy_motion_keywords(director):
    scene = create_scene(VisualStrategy.BROLL)
    scene.motion_hint = "KEN_BURNS, SLOW_ZOOM_IN" # Emulate legacy motion hint
    direction = director.resolve(scene)
    assert "KEN_BURNS" not in (direction.motion_profile or "")
    assert "zoompan" not in (direction.motion_profile or "")

def test_source_not_mutated(director):
    scene = create_scene(VisualStrategy.DIAGRAM)
    original_dump = scene.model_dump()
    director.resolve(scene)
    assert scene.model_dump() == original_dump

def test_all_strategies_mapped(director):
    for strategy in VisualStrategy:
        scene = create_scene(strategy)
        direction = director.resolve(scene)
        assert direction is not None
        assert direction.render_mode is not None

def test_unsupported_strategy(director):
    scene = create_scene(VisualStrategy.DIAGRAM)
    # Force an unsupported strategy
    scene.visual_strategy = "UNSUPPORTED_STRATEGY"
    with pytest.raises(VisualDirectionError) as exc_info:
        director.resolve(scene)
    assert "Unsupported VisualStrategy" in str(exc_info.value)
