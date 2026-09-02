import pytest

from omega.application.scene_template_registry import TemplateInputKey
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.template_payload_resolver import (
    TemplateEdge,
    TemplatePayloadError,
    TemplatePayloadResolver,
)
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualAssetRequirement,
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
)


@pytest.fixture
def resolver():
    return TemplatePayloadResolver()


@pytest.fixture
def scene_base():
    return StoryboardScene(
        sequence_index=1,
        section_id="Why Distributed Systems Scale",
        purpose="purpose",
        source_statement_references=[1],
        narration_excerpt="narration",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.TITLE_MOTION,
        visual_brief="",
        on_screen_text="From one request to millions"
    )


def test_hero_title_resolve(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.HERO_TITLE,
        asset_requirements=[],
        motion_profile="title_reveal",
        rationale=""
    )

    payload = resolver.resolve(scene_base, direction)
    assert payload.template_id == VisualTemplateId.HERO_TITLE
    assert payload.inputs[TemplateInputKey.TITLE] == "Why Distributed Systems Scale"
    assert payload.inputs[TemplateInputKey.SUBTITLE] == "From one request to millions"


def test_flow_diagram_resolve(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.DIAGRAM
    scene_base.narration_excerpt = "The Manufacturer ships products to the Distribution Center, which sends them to the Retailer."
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale=""
    )

    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.TITLE] == "Why Distributed Systems Scale"
    nodes = payload.inputs[TemplateInputKey.NODES]
    assert nodes == ["Manufacturer", "Distribution Center", "Retailer"]
    edges = payload.inputs[TemplateInputKey.EDGES]
    assert edges == [
        TemplateEdge(from_node="Manufacturer", to_node="Distribution Center"),
        TemplateEdge(from_node="Distribution Center", to_node="Retailer")
    ]


def test_statistic_hero_resolve(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.STATISTIC
    scene_base.narration_excerpt = "72% of teams reduced average latency after adopting caching."
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.STATISTIC_HERO,
        asset_requirements=[],
        motion_profile="metric_emphasis",
        rationale=""
    )

    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.METRIC] == "72%"


def test_code_editor_resolve(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.CODE_DEMO
    scene_base.narration_excerpt = "def calculate_latency(): return 42"
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.CODE_EDITOR,
        asset_requirements=[],
        motion_profile="code_type_on",
        rationale=""
    )

    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.CODE] == "def calculate_latency(): return 42"
    assert payload.inputs[TemplateInputKey.LANGUAGE] == "python"


def test_image_explainer_resolve(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.IMAGE
    scene_base.narration_excerpt = "Modern data centers coordinate thousands of machines."
    assets = [VisualAssetRequirement(kind=VisualAssetKind.IMAGE, purpose="", query_hint="")]
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.HYBRID,
        template_id=VisualTemplateId.IMAGE_EXPLAINER,
        asset_requirements=assets,
        motion_profile="image_explainer",
        rationale=""
    )

    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.BODY] == "Modern data centers coordinate thousands of machines."
    assert len(payload.asset_requirements) == 1
    assert payload.asset_requirements[0].kind == VisualAssetKind.IMAGE


def test_diagram_without_nodes_fails(resolver, scene_base):
    scene_base.narration_excerpt = "something simple"
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale=""
    )
    with pytest.raises(TemplatePayloadError, match="trustworthy diagram nodes"):
        resolver.resolve(scene_base, direction)


def test_statistic_without_metric_fails(resolver, scene_base):
    scene_base.narration_excerpt = "many teams improved speed"
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.STATISTIC_HERO,
        asset_requirements=[],
        motion_profile="metric_emphasis",
        rationale=""
    )
    with pytest.raises(TemplatePayloadError, match="trustworthy metric"):
        resolver.resolve(scene_base, direction)


def test_code_without_code_fails(resolver, scene_base):
    scene_base.narration_excerpt = "we write some text"
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.CODE_EDITOR,
        asset_requirements=[],
        motion_profile="code_type_on",
        rationale=""
    )
    with pytest.raises(TemplatePayloadError, match="trustworthy code"):
        resolver.resolve(scene_base, direction)


def test_hero_title_without_title_fails(resolver, scene_base):
    scene_base.section_id = ""
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.HERO_TITLE,
        asset_requirements=[],
        motion_profile="title_reveal",
        rationale=""
    )
    with pytest.raises(TemplatePayloadError, match="missing required input: TITLE"):
        resolver.resolve(scene_base, direction)


def test_template_id_none_fails(resolver, scene_base):
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=None,
        asset_requirements=[],
        motion_profile=None,
        rationale=""
    )
    with pytest.raises(TemplatePayloadError, match="missing template_id"):
        resolver.resolve(scene_base, direction)


def test_unexpected_input_fails(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.HERO_TITLE,
        asset_requirements=[],
        motion_profile="title_reveal",
        rationale=""
    )

    original = resolver._resolve_inputs
    def bad_resolve(scene, tid):
        return {TemplateInputKey.TITLE: "title", TemplateInputKey.NODES: ["bad"]}

    resolver._resolve_inputs = bad_resolve
    with pytest.raises(TemplatePayloadError, match="Unexpected input key NODES"):
        resolver.resolve(scene_base, direction)

    resolver._resolve_inputs = original


def test_no_mutation(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.HERO_TITLE,
        asset_requirements=[],
        motion_profile="title_reveal",
        rationale=""
    )

    scene_dump = scene_base.model_dump()
    dir_dump = direction.model_dump()

    payload = resolver.resolve(scene_base, direction)
    payload2 = resolver.resolve(scene_base, direction)

    assert scene_base.model_dump() == scene_dump
    assert direction.model_dump() == dir_dump
    assert payload.model_dump() == payload2.model_dump()


def test_placeholder_hero_fails(resolver, scene_base):
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.HERO_TITLE,
        asset_requirements=[],
        motion_profile="title_reveal",
        rationale=""
    )

    scene_base.section_id = "test"
    with pytest.raises(TemplatePayloadError, match="missing required input: TITLE"):
        resolver.resolve(scene_base, direction)

    scene_base.section_id = "placeholder"
    with pytest.raises(TemplatePayloadError, match="missing required input: TITLE"):
        resolver.resolve(scene_base, direction)


def test_quote_no_attribution_from_hyphen(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    scene_base.narration_excerpt = '"A great quote" - Some Guy'
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.QUOTE,
        asset_requirements=[],
        motion_profile="quote_reveal",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.QUOTE] == "A great quote"
    assert TemplateInputKey.ATTRIBUTION not in payload.inputs


def test_quote_attribution_from_citation(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    scene_base.narration_excerpt = '"A great quote"'
    scene_base.citations = [{"attribution": "Einstein"}]
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.QUOTE,
        asset_requirements=[],
        motion_profile="quote_reveal",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.QUOTE] == "A great quote"
    assert payload.inputs[TemplateInputKey.ATTRIBUTION] == "Einstein"


def test_kinetic_text_resolves_body(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    scene_base.on_screen_text = "Fast moving words"
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.KINETIC_TEXT,
        asset_requirements=[],
        motion_profile="kinetic_phrase",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.BODY] == "Fast moving words"


def test_broll_explainer_preserves_asset(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.BROLL
    assets = [VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")]
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=assets,
        motion_profile="broll_overlay",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert len(payload.asset_requirements) == 1
    assert payload.asset_requirements[0].kind == VisualAssetKind.BROLL
    assert payload.inputs[TemplateInputKey.BODY] == scene_base.narration_excerpt


def test_screenshot_focus_preserves_asset(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    assets = [VisualAssetRequirement(kind=VisualAssetKind.SCREENSHOT, purpose="", query_hint="")]
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.SCREENSHOT,
        template_id=VisualTemplateId.SCREENSHOT_FOCUS,
        asset_requirements=assets,
        motion_profile="screenshot_focus",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert len(payload.asset_requirements) == 1
    assert payload.asset_requirements[0].kind == VisualAssetKind.SCREENSHOT
    assert payload.inputs[TemplateInputKey.BODY] == scene_base.narration_excerpt


def test_cta_resolves_text(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    scene_base.on_screen_text = "Subscribe now!"
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.CTA,
        asset_requirements=[],
        motion_profile="cta_reveal",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.CTA_TEXT] == "Subscribe now!"


def test_news_card_source(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    scene_base.citations = [{"source": "TechCrunch"}]
    scene_base.on_screen_text = "Big News"
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.NEWS_CARD,
        asset_requirements=[],
        motion_profile="news_card_reveal",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.HEADLINE] == "Big News"
    assert payload.inputs[TemplateInputKey.SOURCE] == "TechCrunch"


def test_news_card_no_source(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    scene_base.citations = []
    scene_base.on_screen_text = "Big News"
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.NEWS_CARD,
        asset_requirements=[],
        motion_profile="news_card_reveal",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.HEADLINE] == "Big News"
    assert TemplateInputKey.SOURCE not in payload.inputs


def test_infographic_items(resolver, scene_base):
    scene_base.visual_strategy = VisualStrategy.TITLE_MOTION
    scene_base.narration_excerpt = "First thing. Second thing. Third thing."
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.INFOGRAPHIC,
        asset_requirements=[],
        motion_profile="infographic_reveal",
        rationale=""
    )
    payload = resolver.resolve(scene_base, direction)
    assert payload.inputs[TemplateInputKey.ITEMS] == ["First thing", "Second thing", "Third thing"]
