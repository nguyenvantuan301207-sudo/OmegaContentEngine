import pytest

from omega.application.scene_composition import LayerType, SceneCompositionEngine
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy


@pytest.fixture
def engine():
    return SceneCompositionEngine()

def create_mock_scene(strategy: VisualStrategy, text: str = "Test narration"):
    return StoryboardScene(
        sequence_index=1,
        section_id="Test Section",
        purpose="Testing",
        source_statement_references=[1],
        narration_excerpt=text,
        estimated_duration_seconds=10.0,
        visual_strategy=strategy,
        visual_brief="Test brief",
        on_screen_text="Test on screen text",
        motion_hint="Test hint",
        asset_query_hint="Test query"
    )

def test_scene_composition_valid_geometry(engine):
    for strategy in list(VisualStrategy):
        scene = create_mock_scene(strategy, "We saw a 50% increase.")
        comp = engine.compose(scene)

        assert comp.width == 1920
        assert comp.height == 1080

        for layer in comp.layers:
            assert layer.x >= 0
            assert layer.y >= 0
            assert layer.width > 0
            assert layer.height > 0
            # Allow some layers to touch or go slightly off if intended, but let's just check they don't wildly exceed bounds
            # For simplicity, they should be within width/height mostly
            assert layer.x < comp.width
            assert layer.y < comp.height
            assert layer.type not in ["PRESENTER", "AVATAR", "3D"]

def test_scene_composition_strategies(engine):
    # TITLE_MOTION
    comp = engine.compose(create_mock_scene(VisualStrategy.TITLE_MOTION))
    assert any(layer.type == LayerType.TEXT for layer in comp.layers)

    # DIAGRAM
    comp = engine.compose(create_mock_scene(VisualStrategy.DIAGRAM))
    assert any(layer.type == LayerType.DIAGRAM_NODE for layer in comp.layers)
    assert any(layer.type == LayerType.DIAGRAM_EDGE for layer in comp.layers)

    # CODE_DEMO
    comp = engine.compose(create_mock_scene(VisualStrategy.CODE_DEMO))
    assert any(layer.type == LayerType.CODE_BLOCK for layer in comp.layers)

    # IMAGE
    comp = engine.compose(create_mock_scene(VisualStrategy.IMAGE))
    assert any(layer.type == LayerType.IMAGE_SLOT for layer in comp.layers)

    # BROLL
    comp = engine.compose(create_mock_scene(VisualStrategy.BROLL))
    assert any(layer.type == LayerType.VIDEO_SLOT for layer in comp.layers)

    # SCREENSHOT
    comp = engine.compose(create_mock_scene(VisualStrategy.SCREENSHOT))
    assert any(layer.type == LayerType.IMAGE_SLOT for layer in comp.layers)

    # STATISTIC (with metric)
    comp = engine.compose(create_mock_scene(VisualStrategy.STATISTIC, "Reduced latency by 45ms and improved 99% uptime."))
    metric_layer = next((layer for layer in comp.layers if layer.type == LayerType.METRIC), None)
    assert metric_layer is not None
    assert metric_layer.content.get("value") in ["45ms", "99%"]

    # STATISTIC (fallback to KINETIC_TEXT without metric)
    comp2 = engine.compose(create_mock_scene(VisualStrategy.STATISTIC, "This is just a generic statement with no numbers."))
    assert comp2.visual_strategy == VisualStrategy.KINETIC_TEXT
    assert any(layer.type == LayerType.TEXT for layer in comp2.layers)
    assert not any(layer.type == LayerType.METRIC for layer in comp2.layers)

def test_determinism(engine):
    scene = create_mock_scene(VisualStrategy.DIAGRAM)
    comp1 = engine.compose(scene)
    comp2 = engine.compose(scene)

    # Ensure they are identical
    assert comp1.model_dump() == comp2.model_dump()
