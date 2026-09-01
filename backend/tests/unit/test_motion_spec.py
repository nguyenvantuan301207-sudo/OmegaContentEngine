import pytest

from omega.application.motion_spec import MotionSpecEngine, MotionType
from omega.application.scene_composition import SceneCompositionEngine
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy


@pytest.fixture
def comp_engine():
    return SceneCompositionEngine()

@pytest.fixture
def motion_engine():
    return MotionSpecEngine()

def create_scene(strategy: VisualStrategy, text: str = "Test text", duration: float = 10.0) -> StoryboardScene:
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

def test_motion_deterministic(comp_engine, motion_engine):
    scene = create_scene(VisualStrategy.TITLE_MOTION)
    comp = comp_engine.compose(scene)
    plan1 = motion_engine.plan_motion(comp)
    plan2 = motion_engine.plan_motion(comp)
    assert plan1.model_dump() == plan2.model_dump()

def test_all_referenced_layers_exist_and_timing(comp_engine, motion_engine):
    strategies = [s for s in VisualStrategy]
    for strategy in strategies:
        # Use python snippet for code demo to avoid fallback
        text = "def test(): pass" if strategy == VisualStrategy.CODE_DEMO else "100%" if strategy == VisualStrategy.STATISTIC else "Some text"
        scene = create_scene(strategy, text, 5.0)
        comp = comp_engine.compose(scene)
        plan = motion_engine.plan_motion(comp)

        layer_ids = {layer.id for layer in comp.layers}
        for motion in plan.motions:
            assert motion.layer_id in layer_ids, f"Motion referenced missing layer {motion.layer_id} in {strategy}"
            assert motion.start_seconds >= 0
            assert motion.duration_seconds >= 0.1
            assert round(motion.start_seconds + motion.duration_seconds, 2) <= comp.duration_seconds

def test_title_motion_staged_entrance(comp_engine, motion_engine):
    comp = comp_engine.compose(create_scene(VisualStrategy.TITLE_MOTION))
    plan = motion_engine.plan_motion(comp)

    title_m = next(m for m in plan.motions if m.layer_id == "title")
    sub_m = next(m for m in plan.motions if m.layer_id == "subtitle")
    acc_m = next(m for m in plan.motions if m.layer_id == "accent")

    assert title_m.start_seconds == 0.0
    assert sub_m.start_seconds == 0.5
    assert acc_m.start_seconds == 0.8

def test_diagram_sequential_reveal(comp_engine, motion_engine):
    # Manufacturer, Distribution Center, Retailer (3 nodes, 2 edges)
    # Using a shorter duration to also test timing compression
    comp = comp_engine.compose(create_scene(VisualStrategy.DIAGRAM, "The Manufacturer ships to the Distribution Center, which goes to the Retailer.", 2.5))
    plan = motion_engine.plan_motion(comp)

    node_1 = next(m for m in plan.motions if m.layer_id == "node_1")
    node_2 = next(m for m in plan.motions if m.layer_id == "node_2")
    node_3 = next(m for m in plan.motions if m.layer_id == "node_3")
    edge_1 = next(m for m in plan.motions if m.layer_id == "edge_1")
    edge_2 = next(m for m in plan.motions if m.layer_id == "edge_2")

    assert node_1.start_seconds < edge_1.start_seconds
    assert edge_1.start_seconds < node_2.start_seconds
    assert node_2.start_seconds < edge_2.start_seconds
    assert edge_2.start_seconds < node_3.start_seconds

    assert round(node_3.start_seconds + node_3.duration_seconds, 2) <= comp.duration_seconds

def test_code_block_type_on(comp_engine, motion_engine):
    comp = comp_engine.compose(create_scene(VisualStrategy.CODE_DEMO, "def test(): return 1"))
    plan = motion_engine.plan_motion(comp)
    code_m = next(m for m in plan.motions if m.layer_id == "code")
    assert code_m.motion_type == MotionType.TYPE_ON

def test_metric_counter_numeric(comp_engine, motion_engine):
    comp = comp_engine.compose(create_scene(VisualStrategy.STATISTIC, "100% latency reduction"))
    plan = motion_engine.plan_motion(comp)
    metric_m = next(m for m in plan.motions if m.layer_id == "metric")
    assert metric_m.motion_type == MotionType.COUNTER

def test_metric_counter_nonnumeric(comp_engine, motion_engine):
    comp = comp_engine.compose(create_scene(VisualStrategy.STATISTIC, "Significant latency reduction"))
    plan = motion_engine.plan_motion(comp)
    # The statistic fallback handles missing metrics by changing strategy to KINETIC_TEXT
    assert comp.visual_strategy == VisualStrategy.KINETIC_TEXT
    kt_m = next((m for m in plan.motions if m.layer_id == "kinetic_text"), None)
    assert kt_m is not None
    assert kt_m.motion_type == MotionType.REVEAL

def test_broll_video_slot(comp_engine, motion_engine):
    comp = comp_engine.compose(create_scene(VisualStrategy.BROLL))
    plan = motion_engine.plan_motion(comp)
    vid_m = next(m for m in plan.motions if m.layer_id == "video")
    assert vid_m.motion_type == MotionType.NONE

def test_screenshot_highlight(comp_engine, motion_engine):
    comp = comp_engine.compose(create_scene(VisualStrategy.SCREENSHOT))
    plan = motion_engine.plan_motion(comp)
    hl_m = next(m for m in plan.motions if m.layer_id == "highlight")
    assert hl_m.motion_type == MotionType.HIGHLIGHT

def test_reasonable_motion_count(comp_engine, motion_engine):
    comp = comp_engine.compose(create_scene(VisualStrategy.TITLE_MOTION))
    plan = motion_engine.plan_motion(comp)
    assert 1 <= len(plan.motions) <= 4
