"""Tests for P18-G2C1 Beat Render Adapter.

Verifies:
1. One-to-one beat timing produces eligible plan;
2. Merged timing produces deterministic fallback/ineligible result;
3. Missing timing produces fallback/failure;
4. Beat order preserved;
5. Parent scene identity preserved;
6. Exact beat narration_span becomes narration_excerpt;
7. Beat source references preserved;
8. Resolved visual strategy preserved;
9. Parent full narration on_screen_text is not copied into beat BODY;
10. Explicit distinct authored overlay may survive;
11. BeatVisualDirection -> VisualDirection mapping parity;
12. template_motion_profile preserved;
13. camera_motion_intent preserved separately;
14. HOOK_TITLE payload compatibility;
15. BROLL payload compatibility;
16. Trustworthy FLOW_DIAGRAM payload compatibility;
17. Trustworthy STATISTIC_HERO payload compatibility;
18. HARD_CUT eligible;
19. CROSSFADE produces deterministic unsupported/fallback status;
20. Deterministic adapter input -> identical plan.
"""

from __future__ import annotations

from omega.application.beat_asset_policy import (
    BeatAssetAction,
    BeatAssetDecision,
    BeatAssetPlan,
    BeatAssetPolicy,
)
from omega.application.beat_render_adapter import (
    BeatRenderAdapter,
    resolve_beat_on_screen_text,
)
from omega.application.beat_visual_direction import (
    BeatVisualDirectionPlan,
    BeatVisualDirector,
)
from omega.application.editorial_beat import (
    AssetReuseIntent,
    BeatMotionIntent,
    BeatSemanticRole,
    BeatTransitionIntent,
    EditorialBeatPlan,
    EditorialBeatSpec,
    EditorialBeatTiming,
    MaterializedBeatTimingPlan,
)
from omega.application.scene_template_registry import TemplateInputKey
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.template_payload_resolver import TemplatePayloadResolver
from omega.application.visual_direction import VisualTemplateId


def _make_scene(
    sequence_index: int = 1,
    narration: str = "This is a full parent scene narration text.",
    on_screen_text: str | None = None,
    strategy: VisualStrategy = VisualStrategy.BROLL,
    asset_query_hint: str | None = "ocean waves",
    visual_brief: str = "A scenic ocean view",
) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=sequence_index,
        section_id="sec-1",
        purpose="Introductory section",
        source_statement_references=[10, 11],
        narration_excerpt=narration,
        estimated_duration_seconds=5.0,
        visual_strategy=strategy,
        visual_brief=visual_brief,
        on_screen_text=on_screen_text,
        motion_hint="steady",
        asset_query_hint=asset_query_hint,
        importance="NORMAL",
        citations=[{"source": "report", "id": 1}],
    )


def test_01_one_to_one_beat_timing_produces_eligible_plan():
    """One-to-one semantic beats and timings pass the eligibility gate."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=1, narration_span="Beat zero narration.",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=3,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.ALLOW_SECONDARY_PROVIDER,
            asset_query_hint="ocean waves",
        ),
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=1, narration_span="Beat one narration.",
            semantic_role=BeatSemanticRole.PAYOFF, word_weight=3,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=6)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)

    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2500, duration_ms=2500),
        EditorialBeatTiming(materialized_index=1, source_beat_indices=(1,), start_ms=2500, end_ms=5000, duration_ms=2500),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=5000, timings=timings)

    res = BeatRenderAdapter.adapt(
        scene=scene, beat_plan=b_plan, timing_plan=t_plan,
        direction_plan=d_plan, asset_plan=a_plan,
    )

    assert res.eligible is True
    assert res.fallback_reason is None
    assert res.plan is not None
    assert len(res.plan.units) == 2
    assert res.plan.total_duration_ms == 5000


def test_02_merged_timing_produces_deterministic_fallback():
    """When a timing interval represents multiple merged source beats, adapter returns fallback."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=1, narration_span="Beat 0",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=2,
            preferred_visual_strategy=VisualStrategy.BROLL,
        ),
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=1, narration_span="Beat 1",
            semantic_role=BeatSemanticRole.PAYOFF, word_weight=2,
            preferred_visual_strategy=VisualStrategy.BROLL,
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=4)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)

    # Merged into a single timing interval covering (0, 1)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0, 1), start_ms=0, end_ms=4000, duration_ms=4000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=4000, timings=timings)

    res = BeatRenderAdapter.adapt(
        scene=scene, beat_plan=b_plan, timing_plan=t_plan,
        direction_plan=d_plan, asset_plan=a_plan,
    )

    assert res.eligible is False
    assert res.fallback_reason == "MERGED_BEAT_TIMING_UNSUPPORTED"
    assert res.plan is None


def test_03_missing_or_mismatched_timing_produces_fallback():
    """Timing plan count mismatch returns deterministic fallback result."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="Beat 0", semantic_role=BeatSemanticRole.CONTEXT, word_weight=2),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=2)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)

    # Empty timings
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=2000, timings=())

    res = BeatRenderAdapter.adapt(
        scene=scene, beat_plan=b_plan, timing_plan=t_plan,
        direction_plan=d_plan, asset_plan=a_plan,
    )

    assert res.eligible is False
    assert res.fallback_reason == "MERGED_BEAT_TIMING_UNSUPPORTED"
    assert res.plan is None


def test_04_beat_order_and_parent_scene_identity_preserved():
    """Units preserve exact beat order, materialized indices, and parent scene index."""
    scene = _make_scene(sequence_index=3)
    beats = (
        EditorialBeatSpec(beat_index=0, parent_scene_index=3, narration_span="First", semantic_role=BeatSemanticRole.CONTEXT, word_weight=1),
        EditorialBeatSpec(beat_index=1, parent_scene_index=3, narration_span="Second", semantic_role=BeatSemanticRole.PAYOFF, word_weight=1),
    )
    b_plan = EditorialBeatPlan(scene_index=3, beats=beats, total_word_weight=2)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=1000, duration_ms=1000),
        EditorialBeatTiming(materialized_index=1, source_beat_indices=(1,), start_ms=1000, end_ms=2000, duration_ms=1000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=3, scene_duration_ms=2000, timings=timings)

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    assert res.eligible is True
    assert res.plan.parent_scene_index == 3
    assert [u.materialized_index for u in res.plan.units] == [0, 1]
    assert [u.source_beat_index for u in res.plan.units] == [0, 1]
    assert [u.parent_scene_index for u in res.plan.units] == [3, 3]


def test_05_beat_narration_span_becomes_narration_excerpt():
    """The beat's specific narration span becomes the scene view's narration_excerpt."""
    scene = _make_scene(narration="Long scene narration covering everything.")
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1,
        narration_span="Exact beat span text.",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=4,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=4)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=2000, timings=timings)

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    unit = res.plan.units[0]
    assert unit.scene_view.narration_excerpt == "Exact beat span text."


def test_06_beat_source_references_and_visual_strategy_preserved():
    """Beat source references and preferred strategy take authority in scene view."""
    scene = _make_scene(strategy=VisualStrategy.BROLL)
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1,
        source_statement_references=(42, 43),
        narration_span="Evidence text.",
        semantic_role=BeatSemanticRole.EVIDENCE,
        word_weight=2,
        preferred_visual_strategy=VisualStrategy.STATISTIC,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=2)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=2000, timings=timings)

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    unit = res.plan.units[0]
    assert unit.scene_view.source_statement_references == [42, 43]
    assert unit.scene_view.visual_strategy == VisualStrategy.STATISTIC


def test_07_parent_full_narration_on_screen_text_suppression():
    """Verbatim narration copies are suppressed from on_screen_text to prevent duplication."""
    narration = "The sun sets slowly in the west."
    # Case 1: on_screen_text is identical to narration
    res_suppressed = resolve_beat_on_screen_text(
        parent_on_screen_text="The sun sets slowly in the west.",
        parent_narration=narration,
    )
    assert res_suppressed is None

    # Case 2: on_screen_text is identical ignoring whitespace/case
    res_suppressed_case = resolve_beat_on_screen_text(
        parent_on_screen_text="  the sun sets slowly in the west. ",
        parent_narration=narration,
    )
    assert res_suppressed_case is None

    # Case 3: on_screen_text is blank
    assert resolve_beat_on_screen_text(parent_on_screen_text="  ", parent_narration=narration) is None
    assert resolve_beat_on_screen_text(parent_on_screen_text=None, parent_narration=narration) is None


def test_08_distinct_authored_overlay_survives():
    """Distinct authored on_screen_text is safely preserved for beat view."""
    narration = "The sun sets slowly in the west."
    distinct_title = "Golden Hour Phenomena"
    res = resolve_beat_on_screen_text(
        parent_on_screen_text=distinct_title,
        parent_narration=narration,
    )
    assert res == "Golden Hour Phenomena"


def test_09_beat_visual_direction_to_visual_direction_mapping():
    """Adapts BeatVisualDirection into standard VisualDirection without putting camera motion into profile."""
    scene = _make_scene()
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1,
        narration_span="Diagram flow",
        semantic_role=BeatSemanticRole.MECHANISM,
        word_weight=2,
        preferred_visual_strategy=VisualStrategy.DIAGRAM,
        motion_intent=BeatMotionIntent.SLOW_PUSH_IN,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=2)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=2000, timings=timings)

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    unit = res.plan.units[0]

    # direction_view must be a standard VisualDirection
    assert unit.direction_view.scene_index == 1
    assert unit.direction_view.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert unit.direction_view.motion_profile == "sequential_flow"
    assert "SLOW_PUSH_IN" not in str(unit.direction_view.motion_profile)

    # camera_motion_intent is preserved on BeatRenderUnit separately
    assert unit.camera_motion_intent == BeatMotionIntent.SLOW_PUSH_IN


def test_10_template_resolver_hook_title_compatibility():
    """Adapted HOOK_TITLE unit cleanly resolves with TemplatePayloadResolver."""
    scene = _make_scene(
        strategy=VisualStrategy.TITLE_MOTION,
        on_screen_text="From one request to millions",
    )
    # Give the parent scene an authored viewer title
    scene.section_id = "Why Distributed Systems Scale"

    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1,
        narration_span="Why leaves change color in autumn.",
        semantic_role=BeatSemanticRole.HOOK_TITLE,
        word_weight=6,
        preferred_visual_strategy=VisualStrategy.TITLE_MOTION,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=6)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=3000, duration_ms=3000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=3000, timings=timings)

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    unit = res.plan.units[0]

    resolver = TemplatePayloadResolver()
    payload = resolver.resolve(unit.scene_view, unit.direction_view)

    assert payload.template_id == VisualTemplateId.HERO_TITLE
    assert payload.inputs[TemplateInputKey.TITLE] == "Why Distributed Systems Scale"
    assert payload.inputs[TemplateInputKey.SUBTITLE] == "From one request to millions"


def test_11_template_resolver_broll_compatibility():
    """Adapted BROLL unit cleanly resolves with TemplatePayloadResolver."""
    scene = _make_scene(strategy=VisualStrategy.BROLL, asset_query_hint="golden leaves")
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1,
        narration_span="Leaves turn bright orange.",
        semantic_role=BeatSemanticRole.CONTEXT,
        word_weight=4,
        preferred_visual_strategy=VisualStrategy.BROLL,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=4)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=3000, duration_ms=3000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=3000, timings=timings)

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    unit = res.plan.units[0]

    resolver = TemplatePayloadResolver()
    payload = resolver.resolve(unit.scene_view, unit.direction_view)

    assert payload.template_id == VisualTemplateId.BROLL_EXPLAINER


def test_12_template_resolver_flow_diagram_compatibility():
    """Adapted FLOW_DIAGRAM unit with trustworthy nodes resolves with TemplatePayloadResolver."""
    flow_text = "The Manufacturer ships products to the Distribution Center, which sends them to the Retailer."
    scene = _make_scene(
        strategy=VisualStrategy.DIAGRAM,
        narration=flow_text,
        visual_brief="Supply chain diagram",
    )
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1,
        narration_span=flow_text,
        semantic_role=BeatSemanticRole.MECHANISM,
        word_weight=13,
        preferred_visual_strategy=VisualStrategy.DIAGRAM,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=13)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=3500, duration_ms=3500),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=3500, timings=timings)

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    unit = res.plan.units[0]

    resolver = TemplatePayloadResolver()
    payload = resolver.resolve(unit.scene_view, unit.direction_view)

    assert payload.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert TemplateInputKey.NODES in payload.inputs
    assert len(payload.inputs[TemplateInputKey.NODES]) >= 2


def test_13_template_resolver_statistic_hero_compatibility():
    """Adapted STATISTIC_HERO unit with trustworthy metric resolves with TemplatePayloadResolver."""
    stat_text = "72% of teams reduced average latency after adopting caching."
    scene = _make_scene(
        strategy=VisualStrategy.STATISTIC,
        narration=stat_text,
        on_screen_text="72% latency reduction",
    )
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1,
        narration_span=stat_text,
        semantic_role=BeatSemanticRole.EVIDENCE,
        word_weight=9,
        preferred_visual_strategy=VisualStrategy.STATISTIC,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=9)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=3000, duration_ms=3000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=3000, timings=timings)

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    unit = res.plan.units[0]

    resolver = TemplatePayloadResolver()
    payload = resolver.resolve(unit.scene_view, unit.direction_view)

    assert payload.template_id == VisualTemplateId.STATISTIC_HERO
    assert payload.inputs[TemplateInputKey.METRIC] == "72%"


def test_14_transition_intent_hard_cut_vs_crossfade():
    """HARD_CUT is eligible in G2C1; CROSSFADE returns deterministic ineligibility."""
    scene = _make_scene()

    # Case 1: HARD_CUT -> eligible
    b_hard = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="Beat 1",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=2,
        transition_intent=BeatTransitionIntent.HARD_CUT,
    )
    bp_hard = EditorialBeatPlan(scene_index=1, beats=(b_hard,), total_word_weight=2)
    dp_hard = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=bp_hard)
    ap_hard = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=bp_hard, direction_plan=dp_hard)
    t_plan = MaterializedBeatTimingPlan(
        scene_index=1, scene_duration_ms=2000,
        timings=(EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),),
    )

    res_hard = BeatRenderAdapter.adapt(scene=scene, beat_plan=bp_hard, timing_plan=t_plan, direction_plan=dp_hard, asset_plan=ap_hard)
    assert res_hard.eligible is True

    # Case 2: CROSSFADE -> unsupported in G2C1
    b_fade = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="Beat 1",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=2,
        transition_intent=BeatTransitionIntent.CROSSFADE,
    )
    bp_fade = EditorialBeatPlan(scene_index=1, beats=(b_fade,), total_word_weight=2)
    dp_fade = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=bp_fade)
    ap_fade = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=bp_fade, direction_plan=dp_fade)

    res_fade = BeatRenderAdapter.adapt(scene=scene, beat_plan=bp_fade, timing_plan=t_plan, direction_plan=dp_fade, asset_plan=ap_fade)
    assert res_fade.eligible is False
    assert res_fade.fallback_reason == "UNSUPPORTED_TRANSITION_INTENT"
    assert res_fade.plan is None


def test_15_deterministic_adapter_input_yields_identical_plan():
    """Identical scene, beats, timing, direction, and asset inputs yield byte-for-byte identical plans."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="Beat 0", semantic_role=BeatSemanticRole.CONTEXT, word_weight=2),
        EditorialBeatSpec(beat_index=1, parent_scene_index=1, narration_span="Beat 1", semantic_role=BeatSemanticRole.PAYOFF, word_weight=2),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=4)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    timings = (
        EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),
        EditorialBeatTiming(materialized_index=1, source_beat_indices=(1,), start_ms=2000, end_ms=4000, duration_ms=2000),
    )
    t_plan = MaterializedBeatTimingPlan(scene_index=1, scene_duration_ms=4000, timings=timings)

    plan1 = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)
    plan2 = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan, asset_plan=a_plan)

    assert plan1 == plan2
    assert plan1.model_dump() == plan2.model_dump()


def test_16_cross_plan_identity_mismatch_produces_fallback():
    """Beat and direction having conflicting parent_scene_index or beat_index triggers PLAN_IDENTITY_MISMATCH."""
    scene = _make_scene(sequence_index=1)
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="Test beat",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=2,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=2)
    d_plan_normal = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan_normal)
    t_plan = MaterializedBeatTimingPlan(
        scene_index=1, scene_duration_ms=2000,
        timings=(EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),),
    )

    # Mismatch beat_index in direction
    d_mismatch = d_plan_normal.directions[0].model_copy(update={"beat_index": 5})
    d_plan_bad = BeatVisualDirectionPlan(parent_scene_index=1, directions=(d_mismatch,))

    res = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan_bad, asset_plan=a_plan)
    assert res.eligible is False
    assert res.fallback_reason == "PLAN_IDENTITY_MISMATCH"


def test_17_cross_plan_semantic_mismatch_produces_fallback():
    """Semantic role or source statement reference disagreement triggers PLAN_SEMANTIC_MISMATCH."""
    scene = _make_scene(sequence_index=1)
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="Test beat",
        source_statement_references=(10,),
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=2,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=2)
    d_plan_normal = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan_normal)
    t_plan = MaterializedBeatTimingPlan(
        scene_index=1, scene_duration_ms=2000,
        timings=(EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),),
    )

    # Case A: statement references mismatch
    d_stmt_bad = d_plan_normal.directions[0].model_copy(update={"source_statement_references": [999]})
    d_plan_stmt = BeatVisualDirectionPlan(parent_scene_index=1, directions=(d_stmt_bad,))
    res_stmt = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan_stmt, asset_plan=a_plan)
    assert res_stmt.eligible is False
    assert res_stmt.fallback_reason == "PLAN_SEMANTIC_MISMATCH"

    # Case B: semantic role mismatch
    d_role_bad = d_plan_normal.directions[0].model_copy(update={"semantic_role": BeatSemanticRole.PAYOFF})
    d_plan_role = BeatVisualDirectionPlan(parent_scene_index=1, directions=(d_role_bad,))
    res_role = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan_role, asset_plan=a_plan)
    assert res_role.eligible is False
    assert res_role.fallback_reason == "PLAN_SEMANTIC_MISMATCH"


def test_18_cross_plan_motion_and_transition_mismatch_produces_fallback():
    """Motion intent or transition intent disagreement triggers PLAN_MOTION_MISMATCH or PLAN_TRANSITION_MISMATCH."""
    scene = _make_scene(sequence_index=1)
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="Test beat",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=2,
        motion_intent=BeatMotionIntent.STATIC,
        transition_intent=BeatTransitionIntent.HARD_CUT,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=2)
    d_plan_normal = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    a_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan_normal)
    t_plan = MaterializedBeatTimingPlan(
        scene_index=1, scene_duration_ms=2000,
        timings=(EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),),
    )

    # Motion mismatch
    d_motion_bad = d_plan_normal.directions[0].model_copy(update={"camera_motion_intent": BeatMotionIntent.SLOW_PUSH_IN})
    d_plan_motion = BeatVisualDirectionPlan(parent_scene_index=1, directions=(d_motion_bad,))
    res_motion = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan_motion, asset_plan=a_plan)
    assert res_motion.eligible is False
    assert res_motion.fallback_reason == "PLAN_MOTION_MISMATCH"

    # Transition mismatch
    d_trans_bad = d_plan_normal.directions[0].model_copy(update={"transition_intent": BeatTransitionIntent.CROSSFADE})
    d_plan_trans = BeatVisualDirectionPlan(parent_scene_index=1, directions=(d_trans_bad,))
    res_trans = BeatRenderAdapter.adapt(scene=scene, beat_plan=b_plan, timing_plan=t_plan, direction_plan=d_plan_trans, asset_plan=a_plan)
    assert res_trans.eligible is False
    assert res_trans.fallback_reason == "PLAN_TRANSITION_MISMATCH"


def test_19_asset_decision_direction_inconsistency_produces_fallback():
    """Contradiction between asset decision action and direction asset requirements triggers ASSET_DIRECTION_INCONSISTENT."""
    scene = _make_scene(sequence_index=1, strategy=VisualStrategy.BROLL)
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="Ocean waves",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=2,
        preferred_visual_strategy=VisualStrategy.BROLL,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=2)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    t_plan = MaterializedBeatTimingPlan(
        scene_index=1, scene_duration_ms=2000,
        timings=(EditorialBeatTiming(materialized_index=0, source_beat_indices=(0,), start_ms=0, end_ms=2000, duration_ms=2000),),
    )

    # direction has 1 asset requirement (VIDEO), but asset decision says LOCAL_TEMPLATE
    bad_decision = BeatAssetDecision(
        parent_scene_index=1,
        beat_index=0,
        action=BeatAssetAction.LOCAL_TEMPLATE,
        rationale="Contradiction test",
    )
    bad_a_plan = BeatAssetPlan(parent_scene_index=1, decisions=(bad_decision,))

    res = BeatRenderAdapter.adapt(
        scene=scene, beat_plan=b_plan, timing_plan=t_plan,
        direction_plan=d_plan, asset_plan=bad_a_plan,
    )
    assert res.eligible is False
    assert res.fallback_reason == "ASSET_DIRECTION_INCONSISTENT"
