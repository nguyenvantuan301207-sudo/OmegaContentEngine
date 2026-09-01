import pytest

from omega.application.motion_runtime import MotionRuntime
from omega.application.motion_spec import Easing, MotionSpecEngine
from omega.application.scene_composition import SceneCompositionEngine
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy


@pytest.fixture
def comp_engine():
    return SceneCompositionEngine()

@pytest.fixture
def motion_engine():
    return MotionSpecEngine()

@pytest.fixture
def runtime():
    return MotionRuntime()

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

def test_fade_in_states(comp_engine, motion_engine, runtime):
    scene = create_scene(VisualStrategy.TITLE_MOTION)
    comp = comp_engine.compose(scene)
    plan = motion_engine.plan_motion(comp)

    # subtitle FADE_IN: start 0.5, dur 1.0
    s_before = {s.layer_id: s for s in runtime.evaluate(comp, plan, 0.49)}
    s_mid = {s.layer_id: s for s in runtime.evaluate(comp, plan, 1.0)}
    s_after = {s.layer_id: s for s in runtime.evaluate(comp, plan, 2.0)}

    # Wait, title is SLIDE_IN in spec, subtitle is FADE_IN
    # Let's check subtitle
    assert s_before["subtitle"].opacity == 0.0
    assert 0.0 < s_mid["subtitle"].opacity < 1.0
    assert s_after["subtitle"].opacity == 1.0

def test_slide_in_states(comp_engine, motion_engine, runtime):
    scene = create_scene(VisualStrategy.TITLE_MOTION)
    comp = comp_engine.compose(scene)
    plan = motion_engine.plan_motion(comp)

    # title is SLIDE_IN: start 0.0, dur 1.0
    s_before = {s.layer_id: s for s in runtime.evaluate(comp, plan, 0.0)}
    s_mid = {s.layer_id: s for s in runtime.evaluate(comp, plan, 0.5)}
    s_after = {s.layer_id: s for s in runtime.evaluate(comp, plan, 1.5)}

    assert s_before["title"].translate_y > 0.0
    assert 0.0 < s_mid["title"].translate_y < 50.0
    assert s_after["title"].translate_y == 0.0

def test_reveal_states(comp_engine, motion_engine, runtime):
    scene = create_scene(VisualStrategy.TITLE_MOTION)
    comp = comp_engine.compose(scene)
    plan = motion_engine.plan_motion(comp)

    # accent is REVEAL: start 0.8, dur 0.8
    s_before = {s.layer_id: s for s in runtime.evaluate(comp, plan, 0.5)}
    s_mid = {s.layer_id: s for s in runtime.evaluate(comp, plan, 1.2)}
    s_after = {s.layer_id: s for s in runtime.evaluate(comp, plan, 2.0)}

    assert s_before["accent"].reveal_progress == 0.0
    assert 0.0 < s_mid["accent"].reveal_progress < 1.0
    assert s_after["accent"].reveal_progress == 1.0

def test_zoom_in_states(comp_engine, motion_engine, runtime):
    scene = create_scene(VisualStrategy.IMAGE)
    comp = comp_engine.compose(scene)
    plan = motion_engine.plan_motion(comp)

    # image ZOOM_IN
    s_before = {s.layer_id: s for s in runtime.evaluate(comp, plan, 0.0)}
    s_mid = {s.layer_id: s for s in runtime.evaluate(comp, plan, 2.5)}
    s_after = {s.layer_id: s for s in runtime.evaluate(comp, plan, 5.0)}

    assert s_before["image"].scale == 1.0
    assert 1.0 < s_mid["image"].scale < 1.08
    assert s_after["image"].scale == 1.08

def test_counter_and_type_on(comp_engine, motion_engine, runtime):
    # STATISTIC (COUNTER)
    s_stat = create_scene(VisualStrategy.STATISTIC, "100%")
    c_stat = comp_engine.compose(s_stat)
    p_stat = motion_engine.plan_motion(c_stat)
    st_stat = {s.layer_id: s for s in runtime.evaluate(c_stat, p_stat, 0.75)}

    assert 0.0 < st_stat["metric"].counter_progress < 1.0
    # ensure no content mutation (content not in frame state)
    assert not hasattr(st_stat["metric"], "content")

    # CODE_DEMO (TYPE_ON)
    s_code = create_scene(VisualStrategy.CODE_DEMO, "def test(): return 1")
    c_code = comp_engine.compose(s_code)
    p_code = motion_engine.plan_motion(c_code)
    st_code = {s.layer_id: s for s in runtime.evaluate(c_code, p_code, 1.0)}

    assert 0.0 < st_code["code"].type_on_progress < 1.0
    assert not hasattr(st_code["code"], "content")

def test_diagram_sequential(comp_engine, motion_engine, runtime):
    scene = create_scene(VisualStrategy.DIAGRAM, "The Manufacturer ships to the Distribution Center, which goes to the Retailer.", 2.5)
    comp = comp_engine.compose(scene)
    plan = motion_engine.plan_motion(comp)

    s_0 = {s.layer_id: s for s in runtime.evaluate(comp, plan, 0.0)}
    # node_1 start 0.5
    assert s_0["node_1"].reveal_progress == 0.0
    assert s_0["node_2"].reveal_progress == 0.0

    # evaluate at end
    s_end = {s.layer_id: s for s in runtime.evaluate(comp, plan, 2.5)}
    assert s_end["node_1"].reveal_progress == 1.0
    assert s_end["node_3"].reveal_progress == 1.0
    assert s_end["edge_2"].reveal_progress == 1.0

def test_broll_unchanged(comp_engine, motion_engine, runtime):
    scene = create_scene(VisualStrategy.BROLL)
    comp = comp_engine.compose(scene)
    plan = motion_engine.plan_motion(comp)

    s_before = {s.layer_id: s for s in runtime.evaluate(comp, plan, 0.0)}
    s_mid = {s.layer_id: s for s in runtime.evaluate(comp, plan, 2.5)}

    assert s_before["video"].opacity == 1.0
    assert s_before["video"].translate_y == 0.0
    assert s_before["video"].scale == 1.0

    assert s_mid["video"].opacity == 1.0
    assert s_mid["video"].translate_y == 0.0
    assert s_mid["video"].scale == 1.0

def test_determinism_and_bounds(comp_engine, motion_engine, runtime):
    scene = create_scene(VisualStrategy.TITLE_MOTION)
    comp = comp_engine.compose(scene)
    plan = motion_engine.plan_motion(comp)

    # Determinism
    s1 = runtime.evaluate(comp, plan, 1.2)
    s2 = runtime.evaluate(comp, plan, 1.2)
    for a, b in zip(s1, s2, strict=True):
        assert a.model_dump() == b.model_dump()

    # Bounds: checking clamping
    s_neg = {s.layer_id: s for s in runtime.evaluate(comp, plan, -5.0)}
    s_zero = {s.layer_id: s for s in runtime.evaluate(comp, plan, 0.0)}
    assert s_neg["title"].model_dump() == s_zero["title"].model_dump()

    s_over = {s.layer_id: s for s in runtime.evaluate(comp, plan, 10.0)}
    s_end = {s.layer_id: s for s in runtime.evaluate(comp, plan, 5.0)}
    assert s_over["title"].model_dump() == s_end["title"].model_dump()

def test_easing(runtime):
    # Easing logic tests
    assert runtime._ease(0.5, Easing.LINEAR) == 0.5
    assert runtime._ease(0.5, Easing.EASE_IN_OUT) == 0.5
    assert runtime._ease(0.2, Easing.EASE_IN_OUT) < 0.2
    assert runtime._ease(0.8, Easing.EASE_IN_OUT) > 0.8
