"""Unit tests for P18-G2B: Beat Asset Policy.

Verifies:
1. REUSE_PARENT chooses nearest prior compatible asset
2. REUSE_PARENT with no compatible asset emits ACQUIRE_IF_NEEDED when query exists
3. REUSE_PARENT fails deterministically if required asset has no meaningful query and no reusable asset
4. LOCAL_EXPLAINER forbids provider acquisition
5. LOCAL_EXPLAINER + external-only strategy rejects inconsistent contract
6. ALLOW_SECONDARY_PROVIDER emits acquisition plan
7. No cross-kind reuse
8. No future-beat reuse
9. No cross-scene reuse
10. Long-hook asset policy test
11. BROLL -> DIAGRAM -> BROLL reuse test
12. No external provider invocation
13. Deterministic identical input -> identical output
"""

import inspect

import pytest

from omega.application.beat_asset_policy import (
    AvailableBeatAsset,
    BeatAssetAction,
    BeatAssetPolicy,
    BeatAssetPolicyError,
)
from omega.application.beat_visual_direction import BeatVisualDirector
from omega.application.editorial_beat import (
    AssetReuseIntent,
    BeatSemanticRole,
    EditorialBeatPlan,
    EditorialBeatSpec,
)
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_direction import VisualAssetKind


def _make_scene(
    sequence_index: int = 1,
    visual_strategy: VisualStrategy = VisualStrategy.BROLL,
    asset_query_hint: str | None = "sunset horizon",
) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=sequence_index,
        section_id="sec_1",
        purpose="Explain scattering",
        source_statement_references=[1],
        narration_excerpt="Atmosphere scatters light.",
        estimated_duration_seconds=6.0,
        visual_strategy=visual_strategy,
        visual_brief="Cinematic sunset",
        asset_query_hint=asset_query_hint,
        importance="NORMAL",
    )


def test_01_reuse_parent_chooses_nearest_prior_compatible_asset():
    """REUSE_PARENT selects the nearest prior beat with a matching asset kind."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=1, narration_span="Beat 0",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.ALLOW_SECONDARY_PROVIDER,
            asset_query_hint="golden hour sky",
        ),
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=1, narration_span="Beat 1",
            semantic_role=BeatSemanticRole.EXPLANATION, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.ALLOW_SECONDARY_PROVIDER,
            asset_query_hint="sunset clouds",
        ),
        EditorialBeatSpec(
            beat_index=2, parent_scene_index=1, narration_span="Beat 2",
            semantic_role=BeatSemanticRole.PAYOFF, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,  # Must choose beat 1, not beat 0
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=15)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene, beat_plan=b_plan, direction_plan=d_plan
    )

    assert len(asset_plan.decisions) == 3
    assert asset_plan.decisions[0].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert asset_plan.decisions[1].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert asset_plan.decisions[2].action == BeatAssetAction.REUSE_COMPATIBLE
    # Must choose nearest prior (beat 1)
    assert asset_plan.decisions[2].reuse_from_beat_index == 1
    assert asset_plan.decisions[2].provider_acquisition_allowed is False


def test_02_reuse_parent_with_no_compatible_asset_acquires_if_query_exists():
    """When no reusable asset exists, REUSE_PARENT falls back to acquisition with query."""
    scene = _make_scene(asset_query_hint="atmospheric light")
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="First beat",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
        preferred_visual_strategy=VisualStrategy.BROLL,
        asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=5)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene, beat_plan=b_plan, direction_plan=d_plan
    )

    assert len(asset_plan.decisions) == 1
    assert asset_plan.decisions[0].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert asset_plan.decisions[0].provider_acquisition_allowed is True
    assert asset_plan.decisions[0].query_hint == "atmospheric light"


def test_03_reuse_parent_fails_when_no_reusable_asset_and_no_meaningful_query():
    """REUSE_PARENT fails deterministically if no prior asset and query is blank/meaningless."""
    scene = _make_scene(asset_query_hint=None)
    scene.visual_brief = "placeholder"  # Meaningless
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="First beat",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
        preferred_visual_strategy=VisualStrategy.BROLL,
        asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
        asset_query_hint=None,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=5)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    with pytest.raises(BeatAssetPolicyError, match="Unresolved required asset"):
        BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)


def test_04_local_explainer_forbids_provider_acquisition():
    """LOCAL_EXPLAINER results in LOCAL_TEMPLATE with provider acquisition strictly forbidden."""
    scene = _make_scene()
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="Diagram text",
        semantic_role=BeatSemanticRole.MECHANISM, word_weight=5,
        preferred_visual_strategy=VisualStrategy.DIAGRAM,
        asset_reuse_intent=AssetReuseIntent.LOCAL_EXPLAINER,
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=5)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene, beat_plan=b_plan, direction_plan=d_plan
    )

    assert len(asset_plan.decisions) == 1
    assert asset_plan.decisions[0].action == BeatAssetAction.LOCAL_TEMPLATE
    assert asset_plan.decisions[0].provider_acquisition_allowed is False


def test_05_local_explainer_with_external_media_strategy_rejects_inconsistency():
    """Specifying LOCAL_EXPLAINER with a strategy requiring external media raises an error."""
    scene = _make_scene()
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=1, narration_span="B-roll text",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
        preferred_visual_strategy=VisualStrategy.BROLL,  # Requires external video
        asset_reuse_intent=AssetReuseIntent.LOCAL_EXPLAINER,  # Contradictory!
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=(beat,), total_word_weight=5)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    with pytest.raises(BeatAssetPolicyError, match="Inconsistent contract"):
        BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)


def test_06_allow_secondary_provider_emits_acquisition_plan():
    """ALLOW_SECONDARY_PROVIDER permits new acquisition even when prior assets exist."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=1, narration_span="B0",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
            asset_query_hint="sunset 1",
        ),
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=1, narration_span="B1",
            semantic_role=BeatSemanticRole.EXPLANATION, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.ALLOW_SECONDARY_PROVIDER,
            asset_query_hint="sunset 2",
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=10)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene, beat_plan=b_plan, direction_plan=d_plan
    )

    assert asset_plan.decisions[1].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert asset_plan.decisions[1].provider_acquisition_allowed is True
    assert asset_plan.decisions[1].query_hint == "sunset 2"


def test_07_no_cross_kind_reuse():
    """An asset of kind IMAGE is never reused for a BROLL requirement."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=1, narration_span="B0",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
            preferred_visual_strategy=VisualStrategy.IMAGE,  # Yields IMAGE asset
            asset_reuse_intent=AssetReuseIntent.ALLOW_SECONDARY_PROVIDER,
            asset_query_hint="still photo of sky",
        ),
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=1, narration_span="B1",
            semantic_role=BeatSemanticRole.EXPLANATION, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,  # Requires BROLL
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
            asset_query_hint="video of moving clouds",
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=10)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene, beat_plan=b_plan, direction_plan=d_plan
    )

    # Beat 1 requires BROLL. Beat 0 only provided IMAGE. So Beat 1 CANNOT reuse Beat 0!
    assert asset_plan.decisions[1].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert asset_plan.decisions[1].reuse_from_beat_index is None
    assert asset_plan.decisions[1].required_kind == VisualAssetKind.BROLL


def test_08_no_future_beat_reuse():
    """A beat cannot reuse an asset from a subsequent future beat."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=1, narration_span="B0",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
            asset_query_hint="dusk horizon",
        ),
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=1, narration_span="B1",
            semantic_role=BeatSemanticRole.PAYOFF, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.ALLOW_SECONDARY_PROVIDER,
            asset_query_hint="deep sunset",
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=10)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene, beat_plan=b_plan, direction_plan=d_plan
    )

    # Beat 0 cannot reuse Beat 1
    assert asset_plan.decisions[0].reuse_from_beat_index is None
    assert asset_plan.decisions[0].action == BeatAssetAction.ACQUIRE_IF_NEEDED


def test_09_no_cross_scene_reuse():
    """Assets from a different parent scene index are strictly ignored."""
    scene = _make_scene(sequence_index=2)
    beat = EditorialBeatSpec(
        beat_index=0, parent_scene_index=2, narration_span="Text",
        semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
        preferred_visual_strategy=VisualStrategy.BROLL,
        asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
        asset_query_hint="scene 2 sky",
    )
    b_plan = EditorialBeatPlan(scene_index=2, beats=(beat,), total_word_weight=5)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    # Pre-existing asset from Scene 1
    prior_assets = [
        AvailableBeatAsset(parent_scene_index=1, beat_index=0, kind=VisualAssetKind.BROLL)
    ]

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene,
        beat_plan=b_plan,
        direction_plan=d_plan,
        initial_available_assets=prior_assets,
    )

    # Must NOT reuse asset from scene 1
    assert asset_plan.decisions[0].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert asset_plan.decisions[0].reuse_from_beat_index is None


def test_10_long_hook_contract_test():
    """Verifies long-hook policy: Beat 0 template, Beat 1 acquires B-roll if needed."""
    scene = _make_scene(
        sequence_index=1,
        visual_strategy=VisualStrategy.TITLE_MOTION,
        asset_query_hint="crimson sunset",
    )
    beats = (
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=1, narration_span="Hook title",
            semantic_role=BeatSemanticRole.HOOK_TITLE, word_weight=5,
            preferred_visual_strategy=VisualStrategy.TITLE_MOTION,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
        ),
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=1, narration_span="Hook context",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=8,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
            asset_query_hint="crimson sunset",
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=13)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene, beat_plan=b_plan, direction_plan=d_plan
    )

    # Beat 0 is a local template
    assert asset_plan.decisions[0].action == BeatAssetAction.LOCAL_TEMPLATE
    assert asset_plan.decisions[0].provider_acquisition_allowed is False

    # Beat 1 needs BROLL; no prior BROLL exists in scene, so it acquires
    assert asset_plan.decisions[1].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert asset_plan.decisions[1].provider_acquisition_allowed is True
    assert asset_plan.decisions[1].required_kind == VisualAssetKind.BROLL
    assert asset_plan.decisions[1].query_hint == "crimson sunset"


def test_11_explanatory_scene_broll_diagram_broll_reuse():
    """Core G2 storytelling: BROLL context -> local DIAGRAM -> reuse BROLL payoff."""
    scene = _make_scene(
        sequence_index=2,
        visual_strategy=VisualStrategy.BROLL,
        asset_query_hint="atmospheric haze",
    )
    beats = (
        # Beat 0: Establishing context with B-roll
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=2, narration_span="Sunlight enters atmosphere.",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=4,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
            asset_query_hint="atmospheric haze",
        ),
        # Beat 1: Local diagram explaining mechanism
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=2, narration_span="Molecules scatter short blue wavelengths.",
            semantic_role=BeatSemanticRole.MECHANISM, word_weight=6,
            preferred_visual_strategy=VisualStrategy.DIAGRAM,
            asset_reuse_intent=AssetReuseIntent.LOCAL_EXPLAINER,
        ),
        # Beat 2: Payoff reuses the establishing B-roll
        EditorialBeatSpec(
            beat_index=2, parent_scene_index=2, narration_span="Leaving red light to reach our eyes.",
            semantic_role=BeatSemanticRole.PAYOFF, word_weight=7,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=2, beats=beats, total_word_weight=17)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    asset_plan = BeatAssetPolicy.plan_assets(
        scene=scene, beat_plan=b_plan, direction_plan=d_plan
    )

    # Beat 0: Acquires BROLL
    assert asset_plan.decisions[0].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert asset_plan.decisions[0].provider_acquisition_allowed is True

    # Beat 1: Local explainer template with zero provider acquisition
    assert asset_plan.decisions[1].action == BeatAssetAction.LOCAL_TEMPLATE
    assert asset_plan.decisions[1].provider_acquisition_allowed is False

    # Beat 2: Reuses BROLL from Beat 0 with zero provider calls!
    assert asset_plan.decisions[2].action == BeatAssetAction.REUSE_COMPATIBLE
    assert asset_plan.decisions[2].reuse_from_beat_index == 0
    assert asset_plan.decisions[2].provider_acquisition_allowed is False


def test_12_no_external_provider_dependencies():
    """BeatAssetPolicy imports zero external media provider modules."""
    import omega.application.beat_asset_policy as mod

    source = inspect.getsource(mod)
    forbidden = ["pexels", "kokoro", "httpx", "requests", "aiohttp", "sqlalchemy"]
    for word in forbidden:
        assert word not in source.lower()


def test_13_deterministic_identical_input_yields_identical_asset_plan():
    """BeatAssetPolicy produces byte-for-byte identical plans on repeated evaluation."""
    scene = _make_scene()
    beats = (
        EditorialBeatSpec(
            beat_index=0, parent_scene_index=1, narration_span="Text 1",
            semantic_role=BeatSemanticRole.CONTEXT, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
            asset_query_hint="sunset",
        ),
        EditorialBeatSpec(
            beat_index=1, parent_scene_index=1, narration_span="Text 2",
            semantic_role=BeatSemanticRole.PAYOFF, word_weight=5,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=10)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)

    plan1 = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)
    plan2 = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)

    assert plan1 == plan2
    assert plan1.model_dump() == plan2.model_dump()


def test_14_planned_dependency_reuse_contains_no_physical_provider_fields():
    """Planned reuse expresses conditional dependency and contains zero physical asset metadata."""
    scene = _make_scene(
        sequence_index=1,
        visual_strategy=VisualStrategy.BROLL,
        asset_query_hint="ocean waves",
    )
    beats = (
        EditorialBeatSpec(
            beat_index=0,
            parent_scene_index=1,
            narration_span="Ocean waves crash against the rocks.",
            semantic_role=BeatSemanticRole.CONTEXT,
            word_weight=6,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.ALLOW_SECONDARY_PROVIDER,
            asset_query_hint="ocean waves",
        ),
        EditorialBeatSpec(
            beat_index=1,
            parent_scene_index=1,
            narration_span="They erode the cliffside over millennia.",
            semantic_role=BeatSemanticRole.PAYOFF,
            word_weight=6,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
        ),
    )
    b_plan = EditorialBeatPlan(scene_index=1, beats=beats, total_word_weight=12)
    d_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=b_plan)
    asset_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=b_plan, direction_plan=d_plan)

    decision_0 = asset_plan.decisions[0]
    decision_1 = asset_plan.decisions[1]

    # Beat 0 is planned acquisition
    assert decision_0.action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert decision_0.provider_acquisition_allowed is True

    # Beat 1 is planned dependency reuse from beat 0
    assert decision_1.action == BeatAssetAction.REUSE_COMPATIBLE
    assert decision_1.reuse_from_beat_index == 0
    assert decision_1.provider_acquisition_allowed is False

    # Verify absence of physical asset execution artifacts in decisions
    for d in [decision_0, decision_1]:
        dump = d.model_dump()
        for forbidden in [
            "asset_id",
            "file_path",
            "local_path",
            "content_sha256",
            "sha256",
            "source_url",
            "license_name",
            "license_url",
        ]:
            assert forbidden not in dump
            assert not hasattr(d, forbidden)
