import pytest

from omega.application.composition_frame_renderer import CompositionFrameRenderer
from omega.application.motion_runtime import LayerFrameState
from omega.application.scene_composition import LayerType, SceneCompositionEngine
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy


@pytest.fixture
def comp_engine():
    return SceneCompositionEngine()

@pytest.fixture
def renderer():
    return CompositionFrameRenderer()

def create_scene(strategy: VisualStrategy, text: str = "Test text", duration: float = 5.0) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=1,
        section_id="test",
        purpose="test",
        source_statement_references=[1],
        narration_excerpt=text,
        estimated_duration_seconds=duration,
        visual_strategy=strategy,
        visual_brief="",
        on_screen_text=text,
        motion_hint="",
        asset_query_hint=""
    )

def test_valid_svg_root(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.TITLE_MOTION))
    svg = renderer.render_svg(comp)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"')
    assert svg.endswith("</svg>")

def test_deterministic_output(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.DIAGRAM, "Client to Server"))
    svg1 = renderer.render_svg(comp)
    svg2 = renderer.render_svg(comp)
    assert svg1 == svg2

def test_actual_diagram_labels_appear(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.DIAGRAM, "The Client sends to Server"))
    svg = renderer.render_svg(comp)
    assert "Client" in svg
    assert "Server" in svg
    assert "Database" not in svg  # Ensure fake label does not appear

def test_code_block_renders(comp_engine, renderer):
    code = 'def test():\n    return 1'
    comp = comp_engine.compose(create_scene(VisualStrategy.CODE_DEMO, code))
    svg = renderer.render_svg(comp)
    assert "def test" in svg
    assert "return 1" in svg

def test_xml_safely_escaped(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.TITLE_MOTION, "A < B & C > D"))
    svg = renderer.render_svg(comp)
    assert "A &lt; B &amp; C &gt; D" in svg
    assert "< B" not in svg

def test_opacity_translation_zoom_states(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.TITLE_MOTION))
    state = LayerFrameState(
        layer_id="title",
        opacity=0.5,
        translate_x=10.0,
        translate_y=20.0,
        scale=1.5
    )
    svg = renderer.render_svg(comp, [state])
    assert 'opacity="0.50"' in svg
    assert 'translate(10.00 20.00)' in svg
    assert 'scale(1.50)' in svg

def test_reveal_clipping(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.DIAGRAM, "Client to Server"))

    # reveal = 0
    state0 = LayerFrameState(layer_id="node_1", reveal_progress=0.0)
    svg0 = renderer.render_svg(comp, [state0])
    assert 'clipPath id="clip_node_1"' in svg0
    assert 'width="0.00"' in svg0

    # reveal = 0.5
    state_half = LayerFrameState(layer_id="node_1", reveal_progress=0.5)
    svg_half = renderer.render_svg(comp, [state_half])
    assert 'clipPath id="clip_node_1"' in svg_half
    # Half width, assume node_1 has some positive width
    assert 'width="0.00"' not in svg_half

    # reveal = 1.0
    state1 = LayerFrameState(layer_id="node_1", reveal_progress=1.0)
    svg1 = renderer.render_svg(comp, [state1])
    assert 'clipPath id="clip_node_1"' not in svg1

def test_type_on_prefixes(comp_engine, renderer):
    from omega.application.scene_composition import Layer

    comp = comp_engine.compose(create_scene(VisualStrategy.CODE_DEMO, "ignored"))

    code = "def test():\n    print('Hello World')"
    layer = Layer(id="code", type=LayerType.CODE_BLOCK, x=0, y=0, width=500, height=500, z_index=1, content={"code": code})
    comp.layers = [layer]

    s0 = LayerFrameState(layer_id="code", type_on_progress=0.0)
    svg0 = renderer.render_svg(comp, [s0])
    assert "Hello" not in svg0

    s_half = LayerFrameState(layer_id="code", type_on_progress=0.5)
    svg_half = renderer.render_svg(comp, [s_half])
    assert "def test" in svg_half
    assert "World" not in svg_half

    s1 = LayerFrameState(layer_id="code", type_on_progress=1.0)
    svg1 = renderer.render_svg(comp, [s1])
    assert "Hello World" in svg1

def test_metric_counter(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.STATISTIC, "100% improvement"))

    # Remove the label layer so it doesn't contain the '100% improvement' text
    comp.layers = [layer for layer in comp.layers if layer.type != LayerType.TEXT]

    s_half = LayerFrameState(layer_id="metric", counter_progress=0.5)
    svg_half = renderer.render_svg(comp, [s_half])
    # 100 * 0.5 = 50. The % might be stripped by the scene composition metric extractor, so we just check "50"
    assert ">50<" in svg_half
    assert "100" not in svg_half

    s1 = LayerFrameState(layer_id="metric", counter_progress=1.0)
    svg1 = renderer.render_svg(comp, [s1])
    assert "100" in svg1

def test_slots_no_fabrication(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.BROLL))
    svg = renderer.render_svg(comp)
    assert 'data-layer-id="video"' in svg
    assert "<image" not in svg  # No image assets fabricated
    assert "<video" not in svg

def test_scene_composition_immutable(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.TITLE_MOTION))
    original_dump = comp.model_dump()

    state = LayerFrameState(layer_id="title", opacity=0.5, translate_x=10.0, reveal_progress=0.5)
    renderer.render_svg(comp, [state])

    assert comp.model_dump() == original_dump


def test_fidelity_highlight_default_off(comp_engine, renderer):
    from omega.application.scene_composition import Layer, LayerType, SceneComposition
    layer = Layer(id="test", type=LayerType.DIAGRAM_NODE, x=0, y=0, width=100, height=100, z_index=1, content={"label": "A"})
    comp = SceneComposition(width=1920, height=1080, layout_type="TEST", duration_seconds=5.0, scene_index=1, source_storyboard_scene_index=1, visual_strategy=VisualStrategy.DIAGRAM, layers=[layer])

    # Render with no state -> highlight should be 0.0, no #FFD700
    svg = renderer.render_svg(comp)
    assert "#FFD700" not in svg

def test_fidelity_highlight_motion(comp_engine, renderer):
    from omega.application.scene_composition import Layer, LayerType, SceneComposition
    layer = Layer(id="test", type=LayerType.DIAGRAM_NODE, x=0, y=0, width=100, height=100, z_index=1, content={"label": "A"})
    comp = SceneComposition(width=1920, height=1080, layout_type="TEST", duration_seconds=5.0, scene_index=1, source_storyboard_scene_index=1, visual_strategy=VisualStrategy.DIAGRAM, layers=[layer])

    state = LayerFrameState(layer_id="test", highlight_progress=1.0)
    svg = renderer.render_svg(comp, [state])
    assert "#FFD700" in svg

def test_fidelity_title_fonts_and_align(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.TITLE_MOTION, "My Subtitle"))
    comp.layers[0].content["text"] = "My Title" # Header

    svg = renderer.render_svg(comp)
    assert 'font-size="96"' in svg
    assert 'font-size="48"' in svg
    assert 'text-anchor="middle"' in svg

def test_fidelity_composition_background(comp_engine, renderer):
    comp = comp_engine.compose(create_scene(VisualStrategy.TITLE_MOTION))
    comp.background = "#123456"
    svg = renderer.render_svg(comp)
    assert 'fill="#123456"' in svg

def test_fidelity_layer_opacity_combined(comp_engine, renderer):
    from omega.application.scene_composition import Layer, LayerType, SceneComposition
    layer = Layer(id="test", type=LayerType.SHAPE, x=0, y=0, width=100, height=100, z_index=1, opacity=0.5, style={"opacity": 0.5})
    comp = SceneComposition(width=1920, height=1080, layout_type="TEST", duration_seconds=5.0, scene_index=1, source_storyboard_scene_index=1, visual_strategy=VisualStrategy.INFOGRAPHIC, layers=[layer])

    state = LayerFrameState(layer_id="test", opacity=0.5)
    svg = renderer.render_svg(comp, [state])
    # 0.5 * 0.5 * 0.5 = 0.125
    assert 'opacity="0.12"' in svg or 'opacity="0.13"' in svg

def test_fidelity_shape_styles(comp_engine, renderer):
    from omega.application.scene_composition import Layer, LayerType, SceneComposition
    layer1 = Layer(id="red", type=LayerType.SHAPE, x=0, y=0, width=100, height=100, z_index=1, style={"bg": "#ff0000"})
    layer2 = Layer(id="border", type=LayerType.SHAPE, x=0, y=0, width=100, height=100, z_index=1, style={"border": "4px solid red", "fill": "transparent"})
    comp = SceneComposition(width=1920, height=1080, layout_type="TEST", duration_seconds=5.0, scene_index=1, source_storyboard_scene_index=1, visual_strategy=VisualStrategy.INFOGRAPHIC, layers=[layer1, layer2])

    svg = renderer.render_svg(comp)
    assert 'fill="#ff0000"' in svg
    assert 'stroke="red"' in svg
    assert 'stroke-width="4"' in svg
    assert 'fill="transparent"' in svg

def test_fidelity_chart_and_unknown(comp_engine, renderer):
    from omega.application.scene_composition import Layer, LayerType, SceneComposition
    layer_chart = Layer(id="c", type=LayerType.CHART, x=0, y=0, width=100, height=100, z_index=1, content={})

    comp = SceneComposition(width=1920, height=1080, layout_type="TEST", duration_seconds=5.0, scene_index=1, source_storyboard_scene_index=1, visual_strategy=VisualStrategy.INFOGRAPHIC, layers=[layer_chart])

    svg = renderer.render_svg(comp)
    # Chart with no data should render empty, so layer string should be empty
    assert 'layer_c' not in svg
    assert '#FF00FF' not in svg


def test_fidelity_screenshot_highlight(comp_engine, renderer):
    from omega.application.scene_composition import Layer, LayerType, SceneComposition
    layer = Layer(id="test", type=LayerType.SHAPE, x=0, y=0, width=100, height=100, z_index=1, style={"border": "4px solid red", "fill": "transparent"})
    comp = SceneComposition(width=1920, height=1080, layout_type="TEST", duration_seconds=5.0, scene_index=1, source_storyboard_scene_index=1, visual_strategy=VisualStrategy.SCREENSHOT, layers=[layer])

    # Progress 0
    state0 = LayerFrameState(layer_id="test", highlight_progress=0.0)
    svg0 = renderer.render_svg(comp, [state0])
    assert 'stroke="red"' in svg0
    assert 'stroke-width="4"' in svg0
    assert 'fill="transparent"' in svg0

    # Progress 0.5
    state_half = LayerFrameState(layer_id="test", highlight_progress=0.5)
    svg_half = renderer.render_svg(comp, [state_half])
    assert 'stroke="red"' in svg_half
    assert 'stroke-width="5"' in svg_half

    # Progress 1.0
    state1 = LayerFrameState(layer_id="test", highlight_progress=1.0)
    svg1 = renderer.render_svg(comp, [state1])
    assert 'stroke="red"' in svg1
    assert 'stroke-width="6"' in svg1
    assert 'fill="transparent"' in svg1
