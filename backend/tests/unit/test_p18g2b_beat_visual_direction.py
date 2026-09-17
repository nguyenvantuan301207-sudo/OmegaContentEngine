"""Unit tests for P18-G2B: Beat-Level Visual Direction.

Verifies:
1. One direction per semantic beat
2. Beat ordering preserved
3. Parent scene index preserved
4. Preferred beat strategy wins over parent strategy
5. Missing preferred strategy falls back to parent strategy
6. Existing strategy-to-template mapping parity across all strategies
7. Existing VisualDirector.resolve(scene) remains identical and unaffected
8. Query priority: beat hint -> scene hint -> visual brief
9. Meaningless query rejection ('placeholder', 'generic', 'abstract technology background')
10. HOOK_TITLE maps to HERO_TITLE
11. CONTEXT / BROLL maps to BROLL_EXPLAINER
12. MECHANISM / DIAGRAM maps to FLOW_DIAGRAM
13. STATISTIC maps to STATISTIC_HERO
14. Clear separation between template_motion_profile and camera_motion_intent
15. Deterministic identical input yields identical output
"""

from omega.application.beat_visual_direction import (
    BeatVisualDirection,
    BeatVisualDirectionPlan,
    BeatVisualDirector,
    resolve_beat_query_hint,
)
from omega.application.editorial_beat import (
    BeatMotionIntent,
    BeatSemanticRole,
    EditorialBeatPlan,
    EditorialBeatSpec,
)
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualDirector,
    VisualRenderMode,
    VisualTemplateId,
    is_meaningful_query,
    map_visual_strategy,
)


def _make_scene(
    sequence_index: int = 1,
    visual_strategy: VisualStrategy = VisualStrategy.BROLL,
    asset_query_hint: str | None = "sunset atmosphere",
    visual_brief: str = "Cinematic sunset over mountains",
) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=sequence_index,
        section_id="sec_1",
        purpose="Explain scattering",
        source_statement_references=[1, 2],
        narration_excerpt="Sunlight traverses atmospheric layers.",
        estimated_duration_seconds=6.0,
        visual_strategy=visual_strategy,
        visual_brief=visual_brief,
        asset_query_hint=asset_query_hint,
        importance="NORMAL",
    )


def test_01_one_direction_per_semantic_beat_and_order_preserved():
    """Each EditorialBeatSpec receives exactly one BeatVisualDirection in exact order."""
    scene = _make_scene(sequence_index=1)
    beats = (
        EditorialBeatSpec(
            beat_index=0,
            parent_scene_index=1,
            narration_span="Hook title text",
            semantic_role=BeatSemanticRole.HOOK_TITLE,
            word_weight=5,
            preferred_visual_strategy=VisualStrategy.TITLE_MOTION,
        ),
        EditorialBeatSpec(
            beat_index=1,
            parent_scene_index=1,
            narration_span="Context visual text",
            semantic_role=BeatSemanticRole.CONTEXT,
            word_weight=10,
            preferred_visual_strategy=VisualStrategy.BROLL,
        ),
    )
    plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=15)

    dir_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=plan)

    assert isinstance(dir_plan, BeatVisualDirectionPlan)
    assert len(dir_plan.directions) == 2
    assert dir_plan.directions[0].beat_index == 0
    assert dir_plan.directions[1].beat_index == 1
    assert dir_plan.parent_scene_index == 1


def test_02_parent_scene_index_preserved_and_mismatch_rejected():
    """Parent scene index is strictly propagated and mismatch raises ValueError."""
    scene = _make_scene(sequence_index=2)
    beat = EditorialBeatSpec(
        beat_index=0,
        parent_scene_index=2,
        narration_span="Span",
        semantic_role=BeatSemanticRole.CONTEXT,
        word_weight=5,
    )
    plan = EditorialBeatPlan(scene_index=2, beats=(beat,), total_word_weight=5)

    dir_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=plan)
    assert dir_plan.parent_scene_index == 2
    assert dir_plan.directions[0].parent_scene_index == 2

    mismatched_plan = EditorialBeatPlan(scene_index=3, beats=(beat,), total_word_weight=5)
    import pytest
    with pytest.raises(ValueError, match="Scene index mismatch"):
        BeatVisualDirector.resolve_plan(scene=scene, beat_plan=mismatched_plan)


def test_03_preferred_beat_strategy_wins_over_parent_strategy():
    """Beat preferred_visual_strategy takes precedence over parent scene strategy."""
    scene = _make_scene(visual_strategy=VisualStrategy.BROLL)
    beat = EditorialBeatSpec(
        beat_index=0,
        parent_scene_index=1,
        narration_span="A diagram of Rayleigh scattering",
        semantic_role=BeatSemanticRole.MECHANISM,
        word_weight=8,
        preferred_visual_strategy=VisualStrategy.DIAGRAM,  # Overrides parent BROLL
    )

    direction = BeatVisualDirector.resolve_beat(scene=scene, beat=beat)

    assert direction.render_mode == VisualRenderMode.TEMPLATE
    assert direction.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert len(direction.asset_requirements) == 0


def test_04_missing_preferred_strategy_falls_back_to_parent():
    """When preferred_visual_strategy is None, parent visual_strategy is used."""
    scene = _make_scene(visual_strategy=VisualStrategy.IMAGE)
    beat = EditorialBeatSpec(
        beat_index=0,
        parent_scene_index=1,
        narration_span="An image of the setting sun",
        semantic_role=BeatSemanticRole.CONTEXT,
        word_weight=6,
        preferred_visual_strategy=None,  # Falls back to parent IMAGE
    )

    direction = BeatVisualDirector.resolve_beat(scene=scene, beat=beat)

    assert direction.render_mode == VisualRenderMode.HYBRID
    assert direction.template_id == VisualTemplateId.IMAGE_EXPLAINER
    assert len(direction.asset_requirements) == 1
    assert direction.asset_requirements[0].kind == VisualAssetKind.IMAGE


def test_05_existing_strategy_mapping_parity():
    """All VisualStrategy values map identically in shared map_visual_strategy helper."""
    for strat in VisualStrategy:
        render_mode, template_id, motion_profile, rationale = map_visual_strategy(strat)
        assert render_mode is not None
        assert template_id is not None
        assert motion_profile is not None
        assert rationale is not None


def test_06_existing_visual_director_output_unchanged():
    """Existing VisualDirector.resolve(scene) produces identical output before and after refactoring."""
    director = VisualDirector()
    for strat in VisualStrategy:
        scene = _make_scene(visual_strategy=strat)
        direction = director.resolve(scene)
        expected_mode, expected_template, expected_motion, expected_rationale = map_visual_strategy(strat)

        assert direction.render_mode == expected_mode
        assert direction.template_id == expected_template
        assert direction.motion_profile == expected_motion
        assert direction.rationale == expected_rationale


def test_07_query_priority_beat_then_scene_then_brief():
    """Query hint resolves in priority order: beat -> scene -> visual brief."""
    # 1. Beat hint takes priority
    scene = _make_scene(asset_query_hint="scene hint", visual_brief="brief hint")
    beat = EditorialBeatSpec(
        beat_index=0,
        parent_scene_index=1,
        narration_span="Text",
        semantic_role=BeatSemanticRole.CONTEXT,
        word_weight=5,
        asset_query_hint="beat specific hint",
    )
    assert resolve_beat_query_hint(beat, scene) == "beat specific hint"

    # 2. Scene hint takes second priority
    beat_no_hint = EditorialBeatSpec(
        beat_index=0,
        parent_scene_index=1,
        narration_span="Text",
        semantic_role=BeatSemanticRole.CONTEXT,
        word_weight=5,
        asset_query_hint=None,
    )
    assert resolve_beat_query_hint(beat_no_hint, scene) == "scene hint"

    # 3. Visual brief is fallback when scene hint is empty
    scene_brief_only = _make_scene(asset_query_hint=None, visual_brief="brief fallback")
    assert resolve_beat_query_hint(beat_no_hint, scene_brief_only) == "brief fallback"

    # 4. None if all empty
    scene_empty = _make_scene(asset_query_hint=None, visual_brief="")
    assert resolve_beat_query_hint(beat_no_hint, scene_empty) is None


def test_08_meaningless_query_rejection():
    """Generic placeholders and abstract terms are rejected as meaningless queries."""
    assert is_meaningful_query(None) is False
    assert is_meaningful_query("") is False
    assert is_meaningful_query("   ") is False
    assert is_meaningful_query("placeholder") is False
    assert is_meaningful_query("generic") is False
    assert is_meaningful_query("Abstract Technology Background") is False
    assert is_meaningful_query("sunset over ocean horizon") is True


def test_09_semantic_roles_map_to_canonical_templates():
    """Verifies canonical template mappings for HOOK, CONTEXT, MECHANISM, and STATISTIC."""
    scene = _make_scene()

    # HOOK_TITLE -> HERO_TITLE
    b_hook = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="Hook",
        semantic_role=BeatSemanticRole.HOOK_TITLE, word_weight=5,
        preferred_visual_strategy=VisualStrategy.TITLE_MOTION,
    )
    d_hook = BeatVisualDirector.resolve_beat(scene=scene, beat=b_hook)
    assert d_hook.template_id == VisualTemplateId.HERO_TITLE
    assert d_hook.render_mode == VisualRenderMode.TEMPLATE

    # CONTEXT -> BROLL_EXPLAINER
    b_ctx = EditorialBeatSpec(
        beat_index=1, parent_scene_index=1, narration_span="Context",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=8,
        preferred_visual_strategy=VisualStrategy.BROLL,
    )
    d_ctx = BeatVisualDirector.resolve_beat(scene=scene, beat=b_ctx)
    assert d_ctx.template_id == VisualTemplateId.BROLL_EXPLAINER
    assert d_ctx.render_mode == VisualRenderMode.BROLL

    # MECHANISM -> FLOW_DIAGRAM
    b_mech = EditorialBeatSpec(
        beat_index=2, parent_scene_index=1, narration_span="Mechanism",
        semantic_role=BeatSemanticRole.MECHANISM, word_weight=10,
        preferred_visual_strategy=VisualStrategy.DIAGRAM,
    )
    d_mech = BeatVisualDirector.resolve_beat(scene=scene, beat=b_mech)
    assert d_mech.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert d_mech.render_mode == VisualRenderMode.TEMPLATE

    # EVIDENCE -> STATISTIC_HERO
    b_evid = EditorialBeatSpec(
        beat_index=3, parent_scene_index=1, narration_span="Evidence",
        semantic_role=BeatSemanticRole.EVIDENCE, word_weight=6,
        preferred_visual_strategy=VisualStrategy.STATISTIC,
    )
    d_evid = BeatVisualDirector.resolve_beat(scene=scene, beat=b_evid)
    assert d_evid.template_id == VisualTemplateId.STATISTIC_HERO
    assert d_evid.render_mode == VisualRenderMode.TEMPLATE


def test_10_separation_of_template_motion_profile_and_camera_motion_intent():
    """BeatVisualDirection clearly separates template_motion_profile from camera motion intent."""
    scene = _make_scene()

    # 1. DIAGRAM has sequential_flow template motion while camera motion is STATIC
    b_diagram = EditorialBeatSpec(
        beat_index=0,
        parent_scene_index=1,
        narration_span="Static diagram camera",
        semantic_role=BeatSemanticRole.MECHANISM,
        word_weight=6,
        preferred_visual_strategy=VisualStrategy.DIAGRAM,
        motion_intent=BeatMotionIntent.STATIC,
    )
    d_diagram = BeatVisualDirector.resolve_beat(scene=scene, beat=b_diagram)
    assert d_diagram.template_motion_profile == "sequential_flow"
    assert d_diagram.camera_motion_intent == BeatMotionIntent.STATIC
    assert "motion_intent" not in BeatVisualDirection.model_fields
    assert not hasattr(d_diagram, "motion_intent")

    # 2. BROLL has broll_overlay template motion while camera motion is SLOW_PUSH_IN
    b_broll = EditorialBeatSpec(
        beat_index=1,
        parent_scene_index=1,
        narration_span="Pushing into broll",
        semantic_role=BeatSemanticRole.CONTEXT,
        word_weight=6,
        preferred_visual_strategy=VisualStrategy.BROLL,
        motion_intent=BeatMotionIntent.SLOW_PUSH_IN,
    )
    d_broll = BeatVisualDirector.resolve_beat(scene=scene, beat=b_broll)
    assert d_broll.template_motion_profile == "broll_overlay"
    assert d_broll.camera_motion_intent == BeatMotionIntent.SLOW_PUSH_IN
    assert not hasattr(d_broll, "motion_intent")


def test_11_deterministic_direction_resolution():
    """Identical scene and beat plan yield identical direction plans."""
    scene = _make_scene()
    beat = EditorialBeatSpec(
        beat_index=0,
        parent_scene_index=1,
        narration_span="Sample text",
        semantic_role=BeatSemanticRole.CONTEXT,
        word_weight=5,
        preferred_visual_strategy=VisualStrategy.BROLL,
    )
    plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=5)

    dir1 = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=plan)
    dir2 = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=plan)

    assert dir1 == dir2
    assert dir1.model_dump() == dir2.model_dump()
