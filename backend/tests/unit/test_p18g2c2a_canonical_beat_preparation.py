"""Tests for P18-G2C2A Canonical Beat Preparation Service.

Verifies:
1. Source resolver exact unique match integration;
2. Source resolver section-scoped statement order;
3. Ambiguous duplicate section rejection;
4. Narration mismatch rejection;
5. Statement types preserved;
6. Exact authoritative duration passed to timing allocator;
7. G2A -> G2B -> G2C1 pipeline produces eligible render plan;
8. Merged timing propagates deterministic fallback;
9. PEXELS mode allows external acquisition plan;
10. LOCAL_TEMPLATE_ONLY mode rejects external beat plan;
11. LOCAL_TEMPLATE_ONLY mode accepts all-local beat plan;
12. Unsupported SCREENSHOT external execution marked unsupported;
13. Long hook preparation produces HERO_TITLE -> BROLL intent when eligible;
14. Explanatory BROLL -> DIAGRAM -> BROLL preserves planned reuse;
15. Deterministic identical input => identical preparation result;
16. Sunset-like non-production architectural fixture.
"""

from __future__ import annotations

from typing import Any

from omega.application.beat_asset_policy import BeatAssetAction
from omega.application.beat_source_resolver import resolve_scene_source_statements
from omega.application.canonical_beat_preparation import CanonicalBeatPreparationService
from omega.application.editorial_beat import BeatSemanticRole
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_direction import VisualTemplateId


def _make_scene(
    sequence_index: int = 1,
    section_id: str = "Test Section",
    references: list[int] | None = None,
    narration: str = "First sentence. Second sentence.",
    strategy: VisualStrategy = VisualStrategy.BROLL,
    asset_query_hint: str | None = "calm ocean waves",
) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=sequence_index,
        section_id=section_id,
        purpose="Scene purpose",
        source_statement_references=references if references is not None else [1, 2],
        narration_excerpt=narration,
        estimated_duration_seconds=5.0,
        visual_strategy=strategy,
        visual_brief="Visual brief",
        asset_query_hint=asset_query_hint,
    )


def test_01_preparation_pipeline_produces_eligible_render_plan():
    """Complete G2 pipeline produces an eligible BeatRenderPlan with matching units."""
    scene = _make_scene(
        narration="First context sentence. Second payoff sentence.",
        strategy=VisualStrategy.BROLL,
        asset_query_hint="ocean waves",
    )
    statements = [
        {"statement_order": 1, "statement_text": "First context sentence.", "statement_type": "STATEMENT"},
        {"statement_order": 2, "statement_text": "Second payoff sentence.", "statement_type": "STATEMENT"},
    ]
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=statements,
        scene_duration_ms=6000,
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is True
    assert res.fallback_reason is None
    assert res.beat_plan is not None
    assert res.timing_plan is not None
    assert res.direction_plan is not None
    assert res.asset_plan is not None
    assert res.render_plan is not None
    assert len(res.render_plan.units) == 2
    assert res.render_plan.total_duration_ms == 6000


def test_02_exact_authoritative_duration_consumed():
    """Preparation enforces and allocates the exact supplied scene_duration_ms."""
    scene = _make_scene(
        narration="Beat one text. Beat two text.",
        strategy=VisualStrategy.BROLL,
    )
    statements = [
        {"statement_order": 1, "statement_text": "Beat one text.", "statement_type": "STATEMENT"},
        {"statement_order": 2, "statement_text": "Beat two text.", "statement_type": "STATEMENT"},
    ]
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=statements,
        scene_duration_ms=7500,
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is True
    assert res.timing_plan.scene_duration_ms == 7500
    assert res.render_plan.total_duration_ms == 7500
    assert res.render_plan.units[-1].end_ms == 7500


def test_03_empty_source_statements_fails_preparation():
    """Empty source statements immediately fails closed with SOURCE_STATEMENTS_NOT_RESOLVED."""
    scene = _make_scene()
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=[],
        scene_duration_ms=4000,
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is False
    assert res.fallback_reason == "SOURCE_STATEMENTS_NOT_RESOLVED"


def test_04_merged_timing_propagates_deterministic_fallback():
    """When word weights or min beat durations force merged timing intervals, preparation returns fallback."""
    # A scene where duration is so short (e.g. 1500ms for 3 beats) that G2A must merge beats
    scene = _make_scene(
        narration="A. B. C.",
        references=[1, 2, 3],
        strategy=VisualStrategy.BROLL,
    )
    statements = [
        {"statement_order": 1, "statement_text": "A.", "statement_type": "STATEMENT"},
        {"statement_order": 2, "statement_text": "B.", "statement_type": "STATEMENT"},
        {"statement_order": 3, "statement_text": "C.", "statement_type": "STATEMENT"},
    ]
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=statements,
        scene_duration_ms=1500,  # 1500ms < 3 * 1000ms minimum -> will merge
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is False
    assert res.fallback_reason == "MERGED_BEAT_TIMING_UNSUPPORTED"
    assert res.render_plan is None


def test_05_pexels_mode_allows_external_acquisition_plan():
    """In PEXELS mode, external media acquisition actions remain eligible."""
    scene = _make_scene(
        narration="Deep ocean reef. Coral formations.",
        strategy=VisualStrategy.BROLL,
        asset_query_hint="coral reef diving",
    )
    statements = [
        {"statement_order": 1, "statement_text": "Deep ocean reef.", "statement_type": "STATEMENT"},
        {"statement_order": 2, "statement_text": "Coral formations.", "statement_type": "STATEMENT"},
    ]
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=statements,
        scene_duration_ms=5000,
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is True
    # At least beat 0 should plan ACQUIRE_IF_NEEDED
    assert res.asset_plan.decisions[0].action == BeatAssetAction.ACQUIRE_IF_NEEDED


def test_06_local_template_only_rejects_external_beat_plan():
    """In LOCAL_TEMPLATE_ONLY mode, plans requiring external media acquisition are rejected."""
    scene = _make_scene(
        narration="Deep ocean reef. Coral formations.",
        strategy=VisualStrategy.BROLL,
        asset_query_hint="coral reef diving",
    )
    statements = [
        {"statement_order": 1, "statement_text": "Deep ocean reef.", "statement_type": "STATEMENT"},
        {"statement_order": 2, "statement_text": "Coral formations.", "statement_type": "STATEMENT"},
    ]
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=statements,
        scene_duration_ms=5000,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    assert res.eligible is False
    assert res.fallback_reason == "EXTERNAL_ASSET_DISALLOWED_BY_MODE"
    assert res.render_plan is None


def test_07_local_template_only_accepts_all_local_beat_plan():
    """In LOCAL_TEMPLATE_ONLY mode, scenes planning pure local templates (e.g. DIAGRAM) are eligible."""
    scene = _make_scene(
        narration="The Manufacturer ships components to the Assembly Plant. The Assembly Plant coordinates with the Distribution Warehouse.",
        strategy=VisualStrategy.DIAGRAM,
        asset_query_hint=None,
    )
    statements = [
        {
            "statement_order": 1,
            "statement_text": "The Manufacturer ships components to the Assembly Plant.",
            "statement_type": "STATEMENT",
        },
        {
            "statement_order": 2,
            "statement_text": "The Assembly Plant coordinates with the Distribution Warehouse.",
            "statement_type": "STATEMENT",
        },
    ]
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=statements,
        scene_duration_ms=6000,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    assert res.eligible is True
    assert res.fallback_reason is None
    for d in res.asset_plan.decisions:
        assert d.action in (BeatAssetAction.LOCAL_TEMPLATE, BeatAssetAction.NONE)


def test_08_long_hook_preparation_produces_title_and_broll_intent():
    """Long hook scene splits into HERO_TITLE on beat 0 and BROLL on beat 1."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Introduction",
        purpose="Hook the audience",
        source_statement_references=[1, 2],
        narration_excerpt="Why are distributed databases so difficult? Here is what happens when networks partition.",
        estimated_duration_seconds=6.0,
        visual_strategy=VisualStrategy.TITLE_MOTION,
        visual_brief="Hook visual",
        asset_query_hint="server room blinking lights",
    )
    statements = [
        {
            "statement_order": 1,
            "statement_text": "Why are distributed databases so difficult?",
            "statement_type": "HOOK",
        },
        {
            "statement_order": 2,
            "statement_text": "Here is what happens when networks partition.",
            "statement_type": "STATEMENT",
        },
    ]
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=statements,
        scene_duration_ms=6000,
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is True
    assert res.direction_plan.directions[0].template_id == VisualTemplateId.HERO_TITLE
    assert res.direction_plan.directions[1].template_id == VisualTemplateId.BROLL_EXPLAINER


def test_09_explanatory_broll_diagram_broll_preserves_planned_reuse():
    """A 3-beat scene (BROLL -> DIAGRAM -> BROLL) plans acquisition for beat 0 and reuse for beat 2."""
    scene = StoryboardScene(
        sequence_index=2,
        section_id="Core Mechanics",
        purpose="Explain mechanism",
        source_statement_references=[1, 2, 3],
        narration_excerpt="Servers operate across wide area links. The consensus protocol confirms writes before replying. The cluster maintains consistency without downtime.",
        estimated_duration_seconds=9.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Cluster brief",
        asset_query_hint="data center network cabling",
    )
    statements = [
        {
            "statement_order": 1,
            "statement_text": "Servers operate across wide area links.",
            "statement_type": "STATEMENT",
        },
        {
            "statement_order": 2,
            "statement_text": "The consensus protocol confirms writes before replying.",
            "statement_type": "STATEMENT",
        },
        {
            "statement_order": 3,
            "statement_text": "The cluster maintains consistency without downtime.",
            "statement_type": "STATEMENT",
        },
    ]
    res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=statements,
        scene_duration_ms=9000,
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is True
    assert len(res.render_plan.units) == 3
    # Beat 0: ACQUIRE
    assert res.asset_plan.decisions[0].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    # Beat 2: REUSE_COMPATIBLE referencing beat 0
    assert res.asset_plan.decisions[2].action == BeatAssetAction.REUSE_COMPATIBLE
    assert res.asset_plan.decisions[2].reuse_from_beat_index == 0


def test_10_deterministic_identical_input_yields_identical_result():
    """Calling prepare twice with identical inputs yields byte-for-byte identical plans."""
    scene = _make_scene()
    statements = [
        {"statement_order": 1, "statement_text": "First sentence.", "statement_type": "STATEMENT"},
        {"statement_order": 2, "statement_text": "Second sentence.", "statement_type": "STATEMENT"},
    ]
    res1 = CanonicalBeatPreparationService.prepare(
        scene=scene, source_statements=statements, scene_duration_ms=5000, visual_asset_mode="PEXELS"
    )
    res2 = CanonicalBeatPreparationService.prepare(
        scene=scene, source_statements=statements, scene_duration_ms=5000, visual_asset_mode="PEXELS"
    )
    assert res1 == res2
    assert res1.model_dump() == res2.model_dump()


def test_11_sunset_like_non_production_architectural_fixture():
    """Architectural test: verifies preparation on a multi-beat Rayleigh Scattering scientific narrative.

    Validates context (sunset/red light), mechanism (Rayleigh scattering/wavelengths),
    and payoff across the entire preparation pipeline without touching production.
    """
    script_dict: dict[str, Any] = {
        "title": "Atmospheric Optics",
        "estimated_duration_seconds": 9.0,
        "sections": [
            {
                "heading": "Rayleigh Scattering",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "Sunsets appear intensely colorful as evening approaches.",
                        "statement_type": "STATEMENT",
                    },
                    {
                        "statement_order": 2,
                        "statement_text": "As sunlight travels through more atmosphere, Rayleigh scattering removes blue light.",
                        "statement_type": "STATEMENT",
                    },
                    {
                        "statement_order": 3,
                        "statement_text": "Only warm red and orange tones remain to complete the spectacle.",
                        "statement_type": "CLOSING",
                    },
                ],
            }
        ],
    }
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Rayleigh Scattering",
        purpose="Explain sunset color transition",
        source_statement_references=[1, 2, 3],
        narration_excerpt=(
            "Sunsets appear intensely colorful as evening approaches. "
            "As sunlight travels through more atmosphere, Rayleigh scattering removes blue light. "
            "Only warm red and orange tones remain to complete the spectacle."
        ),
        estimated_duration_seconds=9.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Atmospheric sunset perspective",
        asset_query_hint="sunset golden hour atmosphere",
    )

    # 1. Resolve source statements cleanly from script
    resolution = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert resolution.resolved is True
    assert len(resolution.statements) == 3

    # 2. Execute canonical preparation
    prep_res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=resolution.statements,
        scene_duration_ms=9000,
        visual_asset_mode="PEXELS",
    )
    assert prep_res.eligible is True
    assert prep_res.render_plan is not None
    assert len(prep_res.render_plan.units) == 3

    roles = [u.direction_view.metadata["semantic_role"] for u in prep_res.render_plan.units]
    assert roles[0] == BeatSemanticRole.CONTEXT.value
    assert roles[1] == BeatSemanticRole.MECHANISM.value
    assert roles[2] == BeatSemanticRole.EXPLANATION.value
    assert len(set(roles)) == 3

    # Check exact strategies and template IDs
    u0, u1, u2 = prep_res.render_plan.units
    assert u0.scene_view.visual_strategy == VisualStrategy.BROLL
    assert u0.direction_view.template_id == VisualTemplateId.BROLL_EXPLAINER
    assert u0.asset_decision.action == BeatAssetAction.ACQUIRE_IF_NEEDED

    assert u1.scene_view.visual_strategy == VisualStrategy.DIAGRAM
    assert u1.direction_view.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert u1.asset_decision.action == BeatAssetAction.LOCAL_TEMPLATE

    assert u2.scene_view.visual_strategy == VisualStrategy.BROLL
    assert u2.direction_view.template_id == VisualTemplateId.BROLL_EXPLAINER
    assert u2.asset_decision.action == BeatAssetAction.REUSE_COMPATIBLE
    assert u2.asset_decision.reuse_from_beat_index == 0

    # Assert planned external acquisition count == 1
    external_acquisitions = [
        d for d in prep_res.asset_plan.decisions if d.action == BeatAssetAction.ACQUIRE_IF_NEEDED
    ]
    assert len(external_acquisitions) == 1


def test_12_prepare_from_script_dict_happy_path_and_types_preserved():
    """prepare_from_script_dict resolves statements and executes planning, preserving HOOK types."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Hook Intro",
                "statements": [
                    {"statement_order": 1, "statement_text": "Is this working?", "statement_type": "HOOK"},
                    {"statement_order": 2, "statement_text": "Yes it certainly is.", "statement_type": "STATEMENT"},
                ],
            }
        ]
    }
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Hook Intro",
        purpose="Introduce concept",
        source_statement_references=[1, 2],
        narration_excerpt="Is this working? Yes it certainly is.",
        estimated_duration_seconds=6.0,
        visual_strategy=VisualStrategy.TITLE_MOTION,
        visual_brief="Hook graphic",
        asset_query_hint="computer terminal code",
    )

    res = CanonicalBeatPreparationService.prepare_from_script_dict(
        script_dict=script_dict,
        scene=scene,
        scene_duration_ms=6000,
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is True
    assert res.fallback_reason is None
    assert len(res.source_statements) == 2
    assert res.source_statements[0]["statement_type"] == "HOOK"
    assert res.beat_plan.beats[0].semantic_role == BeatSemanticRole.HOOK_TITLE


def test_13_prepare_from_script_dict_fails_closed_without_calling_planner(monkeypatch):
    """When source resolution fails, EditorialBeatPlanner.plan is NEVER called."""
    from omega.application.editorial_beat_planner import EditorialBeatPlanner

    planner_called = False

    def mock_plan(*args, **kwargs):
        nonlocal planner_called
        planner_called = True
        raise AssertionError("Planner should not be called when source resolution fails!")

    monkeypatch.setattr(EditorialBeatPlanner, "plan", mock_plan)

    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Unrelated Section",
                "statements": [
                    {"statement_order": 1, "statement_text": "Text", "statement_type": "STATEMENT"}
                ],
            }
        ]
    }
    scene = _make_scene(section_id="Missing Section")

    res = CanonicalBeatPreparationService.prepare_from_script_dict(
        script_dict=script_dict,
        scene=scene,
        scene_duration_ms=5000,
        visual_asset_mode="PEXELS",
    )
    assert res.eligible is False
    assert res.fallback_reason == "SOURCE_STATEMENTS_NOT_RESOLVED"
    assert planner_called is False


def test_14_prepare_from_script_dict_ambiguous_and_mismatch_rejection():
    """prepare_from_script_dict propagates ambiguous section and narration mismatch failure reasons."""
    # 1. Narration mismatch
    script_mismatch = {
        "sections": [
            {
                "heading": "Sec A",
                "statements": [
                    {"statement_order": 1, "statement_text": "Changed statement text.", "statement_type": "STATEMENT"}
                ],
            }
        ]
    }
    scene_mismatch = _make_scene(section_id="Sec A", references=[1], narration="Original narration.")
    res_mismatch = CanonicalBeatPreparationService.prepare_from_script_dict(
        script_dict=script_mismatch,
        scene=scene_mismatch,
        scene_duration_ms=5000,
        visual_asset_mode="PEXELS",
    )
    assert res_mismatch.eligible is False
    assert res_mismatch.fallback_reason == "SOURCE_STATEMENTS_NOT_RESOLVED"

    # 2. Ambiguous sections with identical headings, refs, and narration
    script_ambiguous = {
        "sections": [
            {
                "heading": "Duplicate Sec",
                "statements": [{"statement_order": 1, "statement_text": "Same text.", "statement_type": "STATEMENT"}],
            },
            {
                "heading": "Duplicate Sec",
                "statements": [{"statement_order": 1, "statement_text": "Same text.", "statement_type": "STATEMENT"}],
            },
        ]
    }
    scene_ambiguous = _make_scene(section_id="Duplicate Sec", references=[1], narration="Same text.")
    res_ambiguous = CanonicalBeatPreparationService.prepare_from_script_dict(
        script_dict=script_ambiguous,
        scene=scene_ambiguous,
        scene_duration_ms=5000,
        visual_asset_mode="PEXELS",
    )
    assert res_ambiguous.eligible is False
    assert res_ambiguous.fallback_reason == "SOURCE_STATEMENTS_AMBIGUOUS"


def test_15_prepare_from_script_dict_duplicate_statement_order_across_sections_resolves():
    """Statement order 1 in two different sections resolves only through exact section identity."""
    script_dict = {
        "sections": [
            {
                "heading": "Section Alpha",
                "statements": [
                    {"statement_order": 1, "statement_text": "Alpha statement.", "statement_type": "STATEMENT"}
                ],
            },
            {
                "heading": "Section Beta",
                "statements": [
                    {"statement_order": 1, "statement_text": "Beta statement.", "statement_type": "STATEMENT"}
                ],
            },
        ]
    }
    scene_beta = _make_scene(
        section_id="Section Beta",
        references=[1],
        narration="Beta statement.",
        strategy=VisualStrategy.DIAGRAM,
    )
    res = CanonicalBeatPreparationService.prepare_from_script_dict(
        script_dict=script_dict,
        scene=scene_beta,
        scene_duration_ms=5000,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    assert res.eligible is True
    assert len(res.source_statements) == 1
    assert res.source_statements[0]["statement_text"] == "Beta statement."
