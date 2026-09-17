"""Unit tests for P18-G2A: Editorial Beat Semantic Model + Deterministic Timing Allocator.

Verifies:
1. Deterministic identical input -> identical beat plan
2. Single short statement -> one beat
3. Multi-sentence explanatory statement -> multiple beat candidates
4. Adjacent source statements preserve source references
5. No narration text is rewritten
6. Narration spans concatenate to source narration in original order
7. Long hook TITLE_MOTION produces HOOK_TITLE + CONTEXT
8. Short hook remains one beat
9. Timing allocation first start = 0
10. Timing allocation final end = scene_duration_ms
11. Exact duration conservation (sum(durations) == scene_duration_ms)
12. No gaps
13. No overlaps
14. Deterministic rounding
15. Minimum-duration merge behavior with full source provenance
16. Scene shorter than minimum -> one materialized beat representing all source beats
17. Semantic planner performs zero I/O
18. No external-provider dependency
19. Motion intent deterministic
20. Current StoryboardEngine behavior is unchanged
21. MaterializedBeatTimingPlan contract verification
22. Scene index authority: sequence_index >= 1 accepted, 0 rejected
23. Hook detection: first scene + explicit HOOK => hook policy
24. Hook detection: first scene TITLE_MOTION without statement_type => conservative fallback
25. Hook detection: later TITLE_MOTION without HOOK => NOT hook
26. Hook detection: later scene with explicit HOOK statement => retains hook authority
27. Hook detection: purpose containing 'hook' does not override source semantics
28. Timing merge preserves source_beat_indices provenance in source order
29. Long-hook shared narration semantics vs normal textual segmentation
"""

import inspect
from unittest.mock import patch

import pytest
from pydantic import ValidationError

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
from omega.application.editorial_beat_planner import (
    MIN_BEAT_DURATION_MS,
    EditorialBeatPlanner,
    allocate_beat_timing,
)
from omega.application.storyboard_engine import (
    StoryboardEngine,
    StoryboardScene,
    VisualStrategy,
)


def _make_scene(
    sequence_index: int = 1,
    section_id: str = "sec_1",
    purpose: str = "Explain the mechanics of sunset coloring",
    narration_excerpt: str = "During the day, sunlight crosses a short path. Molecules scatter blue light.",
    estimated_duration_seconds: float = 6.0,
    visual_strategy: VisualStrategy = VisualStrategy.BROLL,
    source_statement_references: list[int] | None = None,
    asset_query_hint: str | None = "sunset sky",
) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=sequence_index,
        section_id=section_id,
        purpose=purpose,
        source_statement_references=source_statement_references or [1, 2],
        narration_excerpt=narration_excerpt,
        estimated_duration_seconds=estimated_duration_seconds,
        visual_strategy=visual_strategy,
        visual_brief="Test visual brief",
        on_screen_text=None,
        motion_hint="Smooth pan",
        asset_query_hint=asset_query_hint,
        importance="NORMAL",
    )


def test_01_deterministic_identical_input_yields_identical_plan():
    """Identical input to EditorialBeatPlanner.plan produces byte-for-byte identical plans."""
    scene = _make_scene(sequence_index=1)
    stmts = [
        {"statement_order": 1, "statement_text": "During the day, sunlight crosses a short path."},
        {"statement_order": 2, "statement_text": "Molecules scatter blue light."},
    ]

    plan1 = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)
    plan2 = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert isinstance(plan1, EditorialBeatPlan)
    assert plan1 == plan2
    assert plan1.model_dump() == plan2.model_dump()
    assert len(plan1.beats) == 2


def test_02_single_short_statement_produces_single_beat():
    """A single short statement yields exactly one visual beat."""
    scene = _make_scene(
        sequence_index=2,
        purpose="Illustrate simple fact",
        narration_excerpt="The sky appears blue.",
        estimated_duration_seconds=2.5,
        visual_strategy=VisualStrategy.IMAGE,
        source_statement_references=[5],
    )
    stmts = [{"statement_order": 5, "statement_text": "The sky appears blue."}]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert len(plan.beats) == 1
    assert plan.beats[0].narration_span == "The sky appears blue."
    assert plan.beats[0].source_statement_references == (5,)
    assert plan.beats[0].beat_index == 0


def test_03_multi_sentence_explanatory_statement_produces_multiple_beats():
    """A statement with multiple complete sentences splits into distinct beat candidates."""
    scene = _make_scene(
        sequence_index=2,
        purpose="Explain scattering mechanism",
        narration_excerpt=(
            "During the day, sunlight crosses a relatively short path through the air. "
            "Tiny molecules scatter shorter blue wavelengths more strongly. "
            "That is why the sky above us appears bright blue."
        ),
        estimated_duration_seconds=8.0,
        visual_strategy=VisualStrategy.BROLL,
        source_statement_references=[10],
    )
    stmts = [
        {
            "statement_order": 10,
            "statement_text": (
                "During the day, sunlight crosses a relatively short path through the air. "
                "Tiny molecules scatter shorter blue wavelengths more strongly. "
                "That is why the sky above us appears bright blue."
            ),
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert len(plan.beats) == 3
    assert plan.beats[0].narration_span == "During the day, sunlight crosses a relatively short path through the air."
    assert plan.beats[1].narration_span == "Tiny molecules scatter shorter blue wavelengths more strongly."
    assert plan.beats[2].narration_span == "That is why the sky above us appears bright blue."
    assert plan.beats[1].semantic_role == BeatSemanticRole.MECHANISM


def test_04_adjacent_source_statements_preserve_source_references():
    """Each generated beat retains its authoritative parent source statement reference."""
    scene = _make_scene(
        sequence_index=3,
        source_statement_references=[21, 22],
    )
    stmts = [
        {"statement_order": 21, "statement_text": "As the sun sets, light travels through more atmosphere."},
        {"statement_order": 22, "statement_text": "Blue light scatters away before reaching our eyes."},
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert len(plan.beats) == 2
    assert plan.beats[0].source_statement_references == (21,)
    assert plan.beats[1].source_statement_references == (22,)


def test_05_no_narration_text_is_rewritten():
    """Narration spans are exact verbatim substrings of source statements without modification."""
    text1 = "Atmospheric scattering redirects light in all directions."
    text2 = "Longer wavelengths continue forward unhindered."
    scene = _make_scene(
        sequence_index=2,
        narration_excerpt=f"{text1} {text2}",
        source_statement_references=[1, 2],
    )
    stmts = [
        {"statement_order": 1, "statement_text": text1},
        {"statement_order": 2, "statement_text": text2},
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert plan.beats[0].narration_span == text1
    assert plan.beats[1].narration_span == text2


def test_06_textual_order_preservation():
    """Narration spans concatenate in original order to reconstruct full source narration."""
    s1 = "First phase of the solar path begins high in the atmosphere."
    s2 = "Second phase experiences dense molecular collision."
    scene = _make_scene(
        sequence_index=2,
        narration_excerpt=f"{s1} {s2}",
        source_statement_references=[10, 11],
    )
    stmts = [
        {"statement_order": 10, "statement_text": s1},
        {"statement_order": 11, "statement_text": s2},
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)
    reconstructed = " ".join(b.narration_span for b in plan.beats)

    assert reconstructed == f"{s1} {s2}"


def test_07_long_hook_title_motion_produces_hook_title_and_context():
    """A long hook scene (>4.5s or >=10 words) produces HOOK_TITLE followed by CONTEXT."""
    scene = _make_scene(
        sequence_index=1,
        purpose="Hook the viewer and introduce the topic.",
        narration_excerpt="Why does the evening sky turn vivid shades of crimson and gold as the sun dips below the horizon?",
        estimated_duration_seconds=7.3,
        visual_strategy=VisualStrategy.TITLE_MOTION,
        source_statement_references=[1],
        asset_query_hint="sunset horizon dusk",
    )
    stmts = [
        {
            "statement_order": 1,
            "statement_text": "Why does the evening sky turn vivid shades of crimson and gold as the sun dips below the horizon?",
            "statement_type": "HOOK",
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert len(plan.beats) == 2
    assert plan.beats[0].semantic_role == BeatSemanticRole.HOOK_TITLE
    assert plan.beats[0].preferred_visual_strategy == VisualStrategy.TITLE_MOTION
    assert plan.beats[0].motion_intent in (BeatMotionIntent.STATIC, BeatMotionIntent.SLOW_PUSH_IN)
    assert plan.beats[0].transition_intent == BeatTransitionIntent.HARD_CUT

    assert plan.beats[1].semantic_role == BeatSemanticRole.CONTEXT
    assert plan.beats[1].preferred_visual_strategy == VisualStrategy.BROLL
    assert plan.beats[1].motion_intent == BeatMotionIntent.SLOW_PUSH_IN
    assert plan.beats[1].asset_reuse_intent == AssetReuseIntent.REUSE_PARENT
    assert plan.beats[1].asset_query_hint == "sunset horizon dusk"


def test_08_short_hook_remains_single_beat():
    """A short hook scene (<4.5s and <10 words) remains a single HOOK_TITLE beat."""
    scene = _make_scene(
        sequence_index=1,
        purpose="Hook the viewer",
        narration_excerpt="Why sunsets glow red.",
        estimated_duration_seconds=2.0,
        visual_strategy=VisualStrategy.TITLE_MOTION,
        source_statement_references=[1],
    )
    stmts = [
        {
            "statement_order": 1,
            "statement_text": "Why sunsets glow red.",
            "statement_type": "HOOK",
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert len(plan.beats) == 1
    assert plan.beats[0].semantic_role == BeatSemanticRole.HOOK_TITLE
    assert plan.beats[0].preferred_visual_strategy == VisualStrategy.TITLE_MOTION


def test_09_timing_allocation_starts_at_zero():
    """First materialized beat timing always starts at 0 ms."""
    specs = [
        EditorialBeatSpec(
            beat_index=0,
            parent_scene_index=1,
            narration_span="Intro span",
            semantic_role=BeatSemanticRole.HOOK_TITLE,
            word_weight=10,
        ),
        EditorialBeatSpec(
            beat_index=1,
            parent_scene_index=1,
            narration_span="Context span",
            semantic_role=BeatSemanticRole.CONTEXT,
            word_weight=15,
        ),
    ]

    timings = allocate_beat_timing(specs, scene_duration_ms=6000)

    assert isinstance(timings[0], EditorialBeatTiming)
    assert timings[0].start_ms == 0


def test_10_timing_allocation_ends_exactly_at_scene_duration():
    """Last materialized beat timing ends exactly at scene_duration_ms."""
    specs = [
        EditorialBeatSpec(
            beat_index=0,
            parent_scene_index=1,
            narration_span="Intro",
            semantic_role=BeatSemanticRole.HOOK_TITLE,
            word_weight=10,
        ),
        EditorialBeatSpec(
            beat_index=1,
            parent_scene_index=1,
            narration_span="Context",
            semantic_role=BeatSemanticRole.CONTEXT,
            word_weight=15,
        ),
    ]

    target_duration = 7342
    timings = allocate_beat_timing(specs, scene_duration_ms=target_duration)

    assert timings[-1].end_ms == target_duration


def test_11_exact_duration_conservation():
    """Sum of all beat durations equals scene_duration_ms exactly."""
    specs = [
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="A", semantic_role=BeatSemanticRole.CONTEXT, word_weight=7),
        EditorialBeatSpec(beat_index=1, parent_scene_index=1, narration_span="B", semantic_role=BeatSemanticRole.MECHANISM, word_weight=13),
        EditorialBeatSpec(beat_index=2, parent_scene_index=1, narration_span="C", semantic_role=BeatSemanticRole.EXPLANATION, word_weight=19),
    ]

    scene_dur = 8517
    timings = allocate_beat_timing(specs, scene_duration_ms=scene_dur)

    total_allocated = sum(t.duration_ms for t in timings)
    assert total_allocated == scene_dur


def test_12_no_gaps_in_timing():
    """Consecutive beats are perfectly contiguous with zero gap."""
    specs = [
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="A", semantic_role=BeatSemanticRole.CONTEXT, word_weight=10),
        EditorialBeatSpec(beat_index=1, parent_scene_index=1, narration_span="B", semantic_role=BeatSemanticRole.EXPLANATION, word_weight=10),
    ]

    timings = allocate_beat_timing(specs, scene_duration_ms=5000)

    for i in range(len(timings) - 1):
        assert timings[i + 1].start_ms == timings[i].end_ms


def test_13_no_overlaps_in_timing():
    """No beat begins before its predecessor ends or exceeds its start+duration."""
    specs = [
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="A", semantic_role=BeatSemanticRole.CONTEXT, word_weight=8),
        EditorialBeatSpec(beat_index=1, parent_scene_index=1, narration_span="B", semantic_role=BeatSemanticRole.EXPLANATION, word_weight=12),
        EditorialBeatSpec(beat_index=2, parent_scene_index=1, narration_span="C", semantic_role=BeatSemanticRole.EVIDENCE, word_weight=10),
    ]

    timings = allocate_beat_timing(specs, scene_duration_ms=7500)

    for t in timings:
        assert t.end_ms == t.start_ms + t.duration_ms
        assert t.duration_ms > 0


def test_14_deterministic_rounding():
    """Rounding is fully deterministic across multiple invocations with odd ms durations."""
    specs = [
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="A", semantic_role=BeatSemanticRole.CONTEXT, word_weight=3),
        EditorialBeatSpec(beat_index=1, parent_scene_index=1, narration_span="B", semantic_role=BeatSemanticRole.EXPLANATION, word_weight=7),
    ]

    res1 = allocate_beat_timing(specs, scene_duration_ms=3333)
    res2 = allocate_beat_timing(specs, scene_duration_ms=3333)

    assert res1 == res2
    assert sum(t.duration_ms for t in res1) == 3333


def test_15_minimum_duration_merge_behavior_with_provenance():
    """A beat whose proportional duration is under 1500ms is merged with full source provenance."""
    specs = [
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="Tiny", semantic_role=BeatSemanticRole.CONTEXT, word_weight=1),
        EditorialBeatSpec(beat_index=1, parent_scene_index=1, narration_span="Long sentence with many words", semantic_role=BeatSemanticRole.EXPLANATION, word_weight=20),
    ]

    timings = allocate_beat_timing(specs, scene_duration_ms=4000, min_beat_duration_ms=MIN_BEAT_DURATION_MS)

    # Undersized beat 0 was merged into beat 1: only 1 materialized timing
    assert len(timings) == 1
    assert timings[0].duration_ms == 4000
    assert timings[0].start_ms == 0
    assert timings[0].end_ms == 4000
    assert timings[0].materialized_index == 0
    # Provenance preserves both source indices in canonical source order
    assert timings[0].source_beat_indices == (0, 1)


def test_16_scene_shorter_than_minimum_materializes_one_beat():
    """If scene_duration_ms < MIN_BEAT_DURATION_MS, exactly one beat is materialized preserving all source indices."""
    specs = [
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="A", semantic_role=BeatSemanticRole.CONTEXT, word_weight=5),
        EditorialBeatSpec(beat_index=1, parent_scene_index=1, narration_span="B", semantic_role=BeatSemanticRole.EXPLANATION, word_weight=5),
    ]

    timings = allocate_beat_timing(specs, scene_duration_ms=1200, min_beat_duration_ms=MIN_BEAT_DURATION_MS)

    assert len(timings) == 1
    assert timings[0].duration_ms == 1200
    assert timings[0].start_ms == 0
    assert timings[0].end_ms == 1200
    assert timings[0].source_beat_indices == (0, 1)


def test_17_semantic_planner_performs_zero_io():
    """EditorialBeatPlanner does not call open(), socket, or database functions."""
    scene = _make_scene(sequence_index=1)
    stmts = [{"statement_order": 1, "statement_text": "Sample text for zero IO test."}]

    with patch("builtins.open") as mock_open:
        plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)
        mock_open.assert_not_called()

    assert len(plan.beats) >= 1


def test_18_no_external_provider_dependency():
    """EditorialBeatPlanner imports zero external provider clients (Pexels, Kokoro, HTTP)."""
    import omega.application.editorial_beat_planner as mod

    source = inspect.getsource(mod)

    forbidden = ["pexels", "kokoro", "httpx", "requests", "aiohttp", "sqlalchemy"]
    for word in forbidden:
        assert word not in source.lower()


def test_19_motion_intent_is_deterministic_and_restrained():
    """Motion intent is statically inferred from role without random jitter."""
    scene = _make_scene(
        sequence_index=2,
        purpose="Explain scattering",
        narration_excerpt="Atmosphere scatters light.",
        visual_strategy=VisualStrategy.BROLL,
    )
    stmts = [{"statement_order": 1, "statement_text": "Atmosphere scatters light."}]

    plan1 = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)
    plan2 = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert plan1.beats[0].motion_intent == plan2.beats[0].motion_intent
    assert plan1.beats[0].motion_intent in (
        BeatMotionIntent.STATIC,
        BeatMotionIntent.SLOW_PUSH_IN,
        BeatMotionIntent.DRIFT,
    )


def test_20_current_storyboard_engine_behavior_unchanged():
    """Current StoryboardEngine generates plans identically without regression."""
    engine = StoryboardEngine()
    script = {
        "title": "Sunset Rayleigh Scattering",
        "sections": [
            {
                "heading": "Introduction",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "Have you ever wondered why sunsets display intense crimson colors?",
                        "statement_type": "CREATIVE",
                    }
                ],
            }
        ],
    }

    storyboard = engine.generate_storyboard(script)

    assert len(storyboard.scenes) == 1
    assert storyboard.scenes[0].sequence_index == 1
    assert storyboard.scenes[0].visual_strategy == VisualStrategy.TITLE_MOTION
    assert storyboard.scenes[0].source_statement_references == [1]


def test_21_materialize_timing_plan_contract():
    """EditorialBeatPlanner.allocate_timing returns a well-formed MaterializedBeatTimingPlan."""
    scene = _make_scene(sequence_index=1)
    plan = EditorialBeatPlanner.plan(scene=scene)
    timing_plan = EditorialBeatPlanner.allocate_timing(plan, scene_duration_ms=6000)

    assert isinstance(timing_plan, MaterializedBeatTimingPlan)
    assert timing_plan.scene_index == scene.sequence_index
    assert timing_plan.scene_duration_ms == 6000
    assert len(timing_plan.timings) == len(plan.beats)


# ============================================================
# NEW P18-G2A HARDENING TESTS
# ============================================================


def test_22_scene_index_authority_ge_1_enforced():
    """Scene index must be >= 1; index 0 is rejected."""
    # 1 is valid
    spec = EditorialBeatSpec(
        beat_index=0,
        parent_scene_index=1,
        narration_span="Valid scene index span",
        semantic_role=BeatSemanticRole.CONTEXT,
        word_weight=4,
    )
    assert spec.parent_scene_index == 1

    plan = EditorialBeatPlan(
        scene_index=1,
        beats=(spec,),
        total_word_weight=4,
    )
    assert plan.scene_index == 1

    timing_plan = MaterializedBeatTimingPlan(
        scene_index=1,
        scene_duration_ms=3000,
        timings=(
            EditorialBeatTiming(
                materialized_index=0,
                source_beat_indices=(0,),
                start_ms=0,
                end_ms=3000,
                duration_ms=3000,
            ),
        ),
    )
    assert timing_plan.scene_index == 1

    # 0 is rejected by validation
    with pytest.raises(ValidationError):
        EditorialBeatSpec(
            beat_index=0,
            parent_scene_index=0,
            narration_span="Invalid zero parent",
            semantic_role=BeatSemanticRole.CONTEXT,
            word_weight=4,
        )

    with pytest.raises(ValidationError):
        EditorialBeatPlan(
            scene_index=0,
            beats=(spec,),
            total_word_weight=4,
        )

    with pytest.raises(ValidationError):
        MaterializedBeatTimingPlan(
            scene_index=0,
            scene_duration_ms=3000,
            timings=(),
        )


def test_23_hook_detection_first_scene_explicit_hook():
    """First scene with explicit statement_type=='HOOK' receives hook policy."""
    scene = _make_scene(
        sequence_index=1,
        visual_strategy=VisualStrategy.BROLL,
        narration_excerpt="Why does twilight glow in breathtaking crimson and gold across the evening sky?",
        estimated_duration_seconds=6.5,
    )
    stmts = [
        {
            "statement_order": 1,
            "statement_text": "Why does twilight glow in breathtaking crimson and gold across the evening sky?",
            "statement_type": "HOOK",
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert len(plan.beats) == 2
    assert plan.beats[0].semantic_role == BeatSemanticRole.HOOK_TITLE
    assert plan.beats[1].semantic_role == BeatSemanticRole.CONTEXT


def test_24_hook_detection_first_scene_title_motion_without_statement_type():
    """First scene TITLE_MOTION with untyped statements receives conservative fallback hook policy."""
    scene = _make_scene(
        sequence_index=1,
        visual_strategy=VisualStrategy.TITLE_MOTION,
        narration_excerpt="Why does twilight glow in breathtaking crimson and gold across the evening sky?",
        estimated_duration_seconds=7.0,
    )
    stmts = [
        {
            "statement_order": 1,
            "statement_text": "Why does twilight glow in breathtaking crimson and gold across the evening sky?",
            "statement_type": "",  # Untyped legacy
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert len(plan.beats) == 2
    assert plan.beats[0].semantic_role == BeatSemanticRole.HOOK_TITLE
    assert plan.beats[1].semantic_role == BeatSemanticRole.CONTEXT


def test_25_hook_detection_later_title_motion_without_hook_is_not_hook():
    """A scene with sequence_index > 1 and TITLE_MOTION without HOOK statement is NOT a hook."""
    scene = _make_scene(
        sequence_index=3,
        purpose="Mid-video section title",
        visual_strategy=VisualStrategy.TITLE_MOTION,
        narration_excerpt="Now we explore the mathematics of scattering across denser air.",
        estimated_duration_seconds=5.0,
    )
    stmts = [
        {
            "statement_order": 12,
            "statement_text": "Now we explore the mathematics of scattering across denser air.",
            "statement_type": "TRANSITION",
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    # Must NOT produce HOOK_TITLE + CONTEXT
    for b in plan.beats:
        assert b.semantic_role != BeatSemanticRole.HOOK_TITLE


def test_26_hook_detection_later_scene_with_explicit_hook_retains_authority():
    """A scene with sequence_index > 1 with canonical HOOK statement retains HOOK authority."""
    scene = _make_scene(
        sequence_index=2,
        purpose="Secondary hook introducing new section",
        visual_strategy=VisualStrategy.TITLE_MOTION,
        narration_excerpt="Could an atmosphere without nitrogen produce the same vivid sunset colors?",
        estimated_duration_seconds=6.0,
        source_statement_references=[8],
    )
    stmts = [
        {
            "statement_order": 8,
            "statement_text": "Could an atmosphere without nitrogen produce the same vivid sunset colors?",
            "statement_type": "hook",  # Case-insensitive
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    assert len(plan.beats) == 2
    assert plan.beats[0].semantic_role == BeatSemanticRole.HOOK_TITLE
    assert plan.beats[1].semantic_role == BeatSemanticRole.CONTEXT


def test_27_hook_detection_purpose_containing_hook_does_not_override_semantics():
    """Arbitrary purpose text containing 'hook' does not trigger hook policy on a non-hook scene."""
    scene = _make_scene(
        sequence_index=4,
        purpose="This sentence hooks the viewer back into the explanation of wavelengths.",
        visual_strategy=VisualStrategy.BROLL,
        narration_excerpt="Wavelengths determine how photons interact with atmospheric gases.",
        estimated_duration_seconds=5.0,
    )
    stmts = [
        {
            "statement_order": 18,
            "statement_text": "Wavelengths determine how photons interact with atmospheric gases.",
            "statement_type": "FACTUAL",
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=stmts)

    for b in plan.beats:
        assert b.semantic_role != BeatSemanticRole.HOOK_TITLE


def test_28_timing_merge_provenance_preserves_canonical_source_order():
    """Merging multiple beats maintains complete, non-overlapping, canonically ordered provenance."""
    # 3 beats with weights 1, 1, 18 in a 4000ms scene:
    # Beat 0 and 1 raw durations ~200ms (<1500ms) will merge
    specs = [
        EditorialBeatSpec(beat_index=0, parent_scene_index=1, narration_span="A", semantic_role=BeatSemanticRole.CONTEXT, word_weight=1),
        EditorialBeatSpec(beat_index=1, parent_scene_index=1, narration_span="B", semantic_role=BeatSemanticRole.MECHANISM, word_weight=1),
        EditorialBeatSpec(beat_index=2, parent_scene_index=1, narration_span="C", semantic_role=BeatSemanticRole.EXPLANATION, word_weight=18),
    ]

    timings = allocate_beat_timing(specs, scene_duration_ms=4000, min_beat_duration_ms=1500)

    # Verify provenance invariants
    all_represented_sources: list[int] = []
    for t in timings:
        assert len(t.source_beat_indices) >= 1
        # Canonically ordered
        assert list(t.source_beat_indices) == sorted(t.source_beat_indices)
        all_represented_sources.extend(t.source_beat_indices)

    # Every source beat index appears exactly once (no loss, no duplication)
    assert all_represented_sources == [0, 1, 2]


def test_29_long_hook_shared_narration_vs_normal_textual_segmentation():
    """Distinguishes visual subdivision over shared narration from normal textual segmentation."""
    # 1. Normal textual segmentation: spans are disjoint and concatenate to source
    normal_scene = _make_scene(
        sequence_index=2,
        purpose="Explain scattering",
        narration_excerpt="Sunlight enters atmosphere. Molecules scatter blue light.",
        visual_strategy=VisualStrategy.BROLL,
    )
    normal_stmts = [
        {"statement_order": 1, "statement_text": "Sunlight enters atmosphere."},
        {"statement_order": 2, "statement_text": "Molecules scatter blue light."},
    ]
    normal_plan = EditorialBeatPlanner.plan(scene=normal_scene, source_statements=normal_stmts)

    assert len(normal_plan.beats) == 2
    assert normal_plan.beats[0].narration_span == "Sunlight enters atmosphere."
    assert normal_plan.beats[1].narration_span == "Molecules scatter blue light."
    # Disjoint text spans
    assert normal_plan.beats[0].narration_span != normal_plan.beats[1].narration_span

    # 2. Long-hook single-sentence visual subdivision: shared narration authority
    single_sentence_hook = "Why does twilight glow in breathtaking crimson and gold across the evening sky?"
    hook_scene = _make_scene(
        sequence_index=1,
        purpose="Hook the viewer",
        narration_excerpt=single_sentence_hook,
        estimated_duration_seconds=7.0,
        visual_strategy=VisualStrategy.TITLE_MOTION,
    )
    hook_stmts = [
        {"statement_order": 1, "statement_text": single_sentence_hook, "statement_type": "HOOK"}
    ]
    hook_plan = EditorialBeatPlanner.plan(scene=hook_scene, source_statements=hook_stmts)

    assert len(hook_plan.beats) == 2
    # Both beats reference the exact same single-sentence statement without duplicating narration audio
    assert hook_plan.beats[0].narration_span == single_sentence_hook
    assert hook_plan.beats[1].narration_span == single_sentence_hook
    assert hook_plan.beats[0].source_statement_references == (1,)
    assert hook_plan.beats[1].source_statement_references == (1,)
    assert hook_plan.beats[0].semantic_role == BeatSemanticRole.HOOK_TITLE
    assert hook_plan.beats[1].semantic_role == BeatSemanticRole.CONTEXT
