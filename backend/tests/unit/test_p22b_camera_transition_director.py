"""P22-B camera and transition direction acceptance matrix."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from omega.application.beat_render_adapter import adapt_camera_plan, adapt_transition_plan
from omega.application.camera_transition_director import CameraTransitionDirector
from omega.application.canonical_beat_preparation import CanonicalBeatPreparationService
from omega.application.editorial_beat import BeatMotionIntent, BeatTransitionIntent
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_camera_motion import (
    build_broll_zoompan_filter,
    resolve_camera_motion_profile,
)
from omega.application.visual_direction import VisualTemplateId
from omega.domain.camera_transition import (
    CameraIntent,
    FocusRegion,
    MotionFindingCode,
    TransitionIntent,
)
from omega.domain.narrative_pacing import PacingProfile
from omega.domain.narrative_plan import InformationDensity
from omega.domain.visual_beat import (
    ComparisonSide,
    ContinuityDecisionType,
    DocumentProgressStage,
    VisualBeat,
    VisualRole,
)


def beat(
    index: int,
    role: VisualRole,
    *,
    duration: int = 3000,
    decision: ContinuityDecisionType | None = None,
    asset: str = "IMAGE",
    narration: str = "Explain the evidence.",
    importance: str = "NORMAL",
) -> VisualBeat:
    return VisualBeat(
        id=UUID(int=index + 1),
        scene_id="scene-1",
        parent_scene_index=1,
        beat_index=index,
        source_editorial_beat_indices=(index,),
        start_offset_ms=index * duration,
        end_offset_ms=(index + 1) * duration,
        duration_ms=duration,
        narration_text=narration,
        visual_intent=f"visual {index}",
        information_goal=f"goal {index}",
        visual_role=role,
        preferred_asset_type=asset,
        importance=importance,
        continuity_decision=decision,
    )


def test_camera_selection_establish_reveal_broll_and_stable_data():
    result = CameraTransitionDirector.direct(
        [
            beat(0, VisualRole.ESTABLISH, duration=5000),
            beat(1, VisualRole.REVEAL, importance="HIGH"),
            beat(2, VisualRole.BROLL),
            beat(3, VisualRole.DATA),
            beat(4, VisualRole.DIAGRAM),
        ]
    )
    assert [plan.intent for plan in result.camera_plans] == [
        CameraIntent.PULL_OUT,
        CameraIntent.PUSH_IN,
        CameraIntent.PAN_LEFT,
        CameraIntent.STATIC,
        CameraIntent.STATIC,
    ]
    assert result.camera_plans[1].strength.value == "EMPHATIC"


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (DocumentProgressStage.OVERVIEW, CameraIntent.STATIC),
        (DocumentProgressStage.SECTION, CameraIntent.REFRAME),
        (DocumentProgressStage.DETAIL, CameraIntent.DETAIL_FOCUS),
        (DocumentProgressStage.RETURN_TO_CONTEXT, CameraIntent.RETURN_TO_CONTEXT),
    ],
)
def test_document_progression(stage, expected):
    result = CameraTransitionDirector.direct(
        [beat(0, VisualRole.DOCUMENT)], document_stages={0: stage}
    )
    plan = result.camera_plans[0]
    assert plan.intent == expected
    if stage in (DocumentProgressStage.SECTION, DocumentProgressStage.DETAIL):
        assert plan.focus_region is not None


def test_comparison_focus_preserves_stable_side_coordinates():
    left = CameraTransitionDirector.direct(
        [beat(0, VisualRole.COMPARE)], comparison_focus={0: ComparisonSide.LEFT}
    ).camera_plans[0]
    right = CameraTransitionDirector.direct(
        [beat(0, VisualRole.COMPARE)], comparison_focus={0: ComparisonSide.RIGHT}
    ).camera_plans[0]
    assert left.focus_region is not None and left.focus_region.x < 0.5
    assert right.focus_region is not None and right.focus_region.x >= 0.5
    assert "LEFT" in (left.focus_region.label or "")
    assert "RIGHT" in (right.focus_region.label or "")


def test_focus_region_rejects_invalid_and_clamps_untrusted_bounds():
    with pytest.raises(ValidationError):
        FocusRegion(x=0.8, y=0.1, width=0.4, height=0.5)
    clamped = FocusRegion.clamp(x=-2, y=0.95, width=3, height=0.4)
    assert clamped.x == 0.0
    assert clamped.width == 1.0
    assert clamped.y + clamped.height <= 1.0


def test_short_and_dense_beats_remain_static():
    short = CameraTransitionDirector.direct(
        [beat(0, VisualRole.REVEAL, duration=1000)], pacing=PacingProfile.FAST
    )
    dense = CameraTransitionDirector.direct(
        [beat(0, VisualRole.BROLL)], information_density=InformationDensity.HIGH
    )
    assert short.camera_plans[0].intent == CameraIntent.STATIC
    assert dense.camera_plans[0].intent == CameraIntent.STATIC


def test_unknown_asset_type_uses_static_selection_fallback():
    result = CameraTransitionDirector.direct(
        [beat(0, VisualRole.REVEAL, asset="UNSUPPORTED_FUTURE_ASSET")]
    )
    assert result.camera_plans[0].intent == CameraIntent.STATIC
    assert "unsupported asset type" in result.camera_plans[0].editorial_purpose


def test_motion_restraint_detects_excess_and_rapid_reversal():
    plans = CameraTransitionDirector.direct(
        [
            beat(0, VisualRole.BROLL, duration=2800),
            beat(1, VisualRole.BROLL, duration=2800),
            beat(2, VisualRole.REVEAL, duration=2000),
            beat(3, VisualRole.EMPHASIZE, duration=2000),
        ],
        pacing=PacingProfile.DELIBERATE,
    )
    codes = {finding.code for finding in plans.findings}
    assert MotionFindingCode.EXCESSIVE_CAMERA_MOTION in codes
    assert MotionFindingCode.RAPID_DIRECTION_REVERSAL in codes
    assert MotionFindingCode.CAMERA_CHANGE_TOO_FREQUENT in codes


def test_continuity_and_context_switch_transition_policy():
    result = CameraTransitionDirector.direct(
        [
            beat(0, VisualRole.ESTABLISH),
            beat(1, VisualRole.EXPLAIN, decision=ContinuityDecisionType.KEEP),
            beat(2, VisualRole.REVEAL, decision=ContinuityDecisionType.SWITCH_CONTEXT),
        ]
    )
    assert result.transition_plans[0].requested_intent == TransitionIntent.CUT
    assert result.transition_plans[1].requested_intent == TransitionIntent.MATCH_CONTINUITY
    assert result.transition_plans[1].applied_intent == TransitionIntent.CUT
    assert result.transition_plans[1].fallback_reason == "RENDERER_SUPPORTS_CUT_ONLY"
    assert result.transition_plans[2].requested_intent == TransitionIntent.HARD_CONTEXT_SWITCH
    assert result.transition_plans[2].duration_ms == 0


@pytest.mark.parametrize("pacing", list(PacingProfile))
def test_canonical_pacing_values_are_consumed(pacing):
    plan = CameraTransitionDirector.direct([beat(0, VisualRole.REVEAL)], pacing=pacing)
    assert plan.camera_plans[0].duration_ms == 3000


def test_adapter_static_and_cut_fallbacks_are_explicit():
    directed = CameraTransitionDirector.direct(
        [beat(0, VisualRole.REVEAL, decision=ContinuityDecisionType.KEEP)]
    )
    camera_plan = directed.camera_plans[0]
    transition_plan = directed.transition_plans[0]
    motion, focus, _strength, reason = adapt_camera_plan(camera_plan, VisualTemplateId.COMPARISON)
    assert (motion, focus, reason) == (
        BeatMotionIntent.STATIC,
        None,
        "UNSUPPORTED_TEMPLATE_STATIC_FALLBACK",
    )
    transition, _ = adapt_transition_plan(transition_plan)
    assert transition == BeatTransitionIntent.HARD_CUT


def test_focus_aware_reframe_maps_to_real_bounded_zoompan():
    directed = CameraTransitionDirector.direct(
        [beat(0, VisualRole.DOCUMENT)],
        document_stages={0: DocumentProgressStage.DETAIL},
    )
    plan = directed.camera_plans[0]
    motion, focus, strength, reason = adapt_camera_plan(plan, VisualTemplateId.IMAGE_EXPLAINER)
    assert motion == BeatMotionIntent.FOCAL_ZOOM
    assert reason is None and focus is not None
    profile = resolve_camera_motion_profile(motion, focus_region=focus, strength=strength)
    assert 1.0 <= profile.end_scale <= 1.2
    assert -0.04 <= profile.end_x_offset <= 0.04
    expression = build_broll_zoompan_filter(profile, total_frames=72, fps=24)
    assert "clip(" in expression and "1920x1080" in expression


def test_lineage_is_preserved_through_both_plans():
    source = beat(3, VisualRole.EXPLAIN)
    result = CameraTransitionDirector.direct([source])
    assert result.camera_plans[0].visual_beat_id == source.id
    assert result.transition_plans[0].visual_beat_id == source.id
    assert result.camera_plans[0].source_editorial_beat_indices == (3,)
    assert result.transition_plans[0].source_editorial_beat_indices == (3,)


def test_canonical_pipeline_carries_p22b_plans_to_render_units():
    scene = StoryboardScene(
        sequence_index=1,
        section_id="evidence",
        purpose="Show one stable metric",
        source_statement_references=[1],
        narration_excerpt="The measured result is 72 percent.",
        estimated_duration_seconds=3.0,
        visual_strategy=VisualStrategy.STATISTIC,
        visual_brief="A stable evidence card",
    )
    result = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=(
            {
                "statement_order": 1,
                "statement_text": scene.narration_excerpt,
                "statement_type": "EVIDENCE",
            },
        ),
        scene_duration_ms=3000,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    assert result.eligible is True
    assert result.camera_transition_plan is not None
    assert result.render_plan is not None
    unit = result.render_plan.units[0]
    assert unit.camera_plan == result.camera_transition_plan.camera_plans[0]
    assert unit.transition_plan == result.camera_transition_plan.transition_plans[0]
