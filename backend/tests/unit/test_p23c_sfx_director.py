"""Unit tests for P23-C SFX Director, eligibility, selection, collision, and P23-A handoff."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from omega.application.audio_mix_v2_service import AudioMixPlanner
from omega.application.sfx_director_service import (
    SFXDirector,
    SFXEligibilityEngine,
    SFXSelectionEngine,
    sfx_selections_to_audio_stems,
)
from omega.domain.audio_mix import AudioStem, AudioStemRole
from omega.domain.camera_transition import (
    CameraFrameState,
    CameraIntent,
    CameraPlan,
    CameraTransitionPlan,
    MotionStrength,
    TransitionIntent,
    TransitionPlan,
)
from omega.domain.channel_dna import BrandVoice, ChannelDNA
from omega.domain.music_direction import (
    MusicArc,
    MusicCue,
    MusicDurationPolicy,
    MusicIntent,
    MusicIntentType,
    MusicTransitionIntent,
    TrackUsageState,
    VocalPolicy,
)
from omega.domain.narrative_pacing import (
    InformationDensity,
    PacingPlan,
    PacingProfile,
    RevealStage,
    SectionTimingDetail,
)
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.application.music_director_service import MusicDirector
from omega.domain.sfx_direction import (
    SFX_DIRECTOR_VERSION,
    SFXAssetMetadata,
    SFXCue,
    SFXFindingCode,
    SFXIntent,
    SFXIntentType,
    SFXProminence,
    SFXSelectionResult,
)
from omega.domain.visual_beat import VisualBeat, VisualRole


def make_authorities(
    pacing: PacingProfile = PacingProfile.BALANCED,
    tone: list[str] | None = None,
    roles: tuple[NarrativeSectionRole, ...] = (
        NarrativeSectionRole.HOOK,
        NarrativeSectionRole.DEVELOPMENT,
        NarrativeSectionRole.ESCALATION,
        NarrativeSectionRole.PAYOFF,
        NarrativeSectionRole.CLOSING,
    ),
) -> tuple[NarrativePlan, PacingPlan, ChannelDNA]:
    sections = [
        NarrativeSection(
            section_order=index,
            role=role,
            objective=f"Narrative objective {index}",
            target_duration_seconds=10,
        )
        for index, role in enumerate(roles, 1)
    ]
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=NarrativePlanStatus.APPROVED,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=10 * len(sections),
        estimated_duration_seconds=10 * len(sections),
        sections=sections,
    )
    timings = [
        SectionTimingDetail(
            section_id=section.id,
            section_order=section.section_order,
            role=section.role,
            current_duration_seconds=10,
            recommended_duration_seconds=10,
            target_information_density=InformationDensity.MEDIUM,
            reveal_stage=RevealStage.SETUP,
            intensity_score=index / len(sections),
        )
        for index, section in enumerate(sections, 1)
    ]
    pacing_plan = PacingPlan(
        narrative_plan_id=plan.id,
        narrative_plan_version=plan.version,
        pacing_profile=pacing,
        format_profile=plan.format_profile,
        target_duration_seconds=10 * len(sections),
        initial_duration_sum_seconds=10 * len(sections),
        optimized_duration_sum_seconds=10 * len(sections),
        section_timings=timings,
        open_loop_metrics=[],
        findings=[],
        recommended_adjustments=[],
        escalation_curve=[item.intensity_score for item in timings],
    )
    dna = ChannelDNA(brand_voice=BrandVoice(tone=tone or ["CLEAR", "STRUCTURED"]))
    return plan, pacing_plan, dna


def make_sfx_asset(
    asset_id: str,
    *,
    intent: SFXIntentType = SFXIntentType.UI_CLICK,
    duration_ms: int = 500,
    energy: float = 0.5,
    prominence: SFXProminence = SFXProminence.BALANCED,
    usage: TrackUsageState = TrackUsageState.GENERATED,
    exists: bool = True,
    loop_safe: bool = False,
    tags: tuple[str, ...] = (),
) -> SFXAssetMetadata:
    return SFXAssetMetadata(
        asset_id=asset_id,
        source_artifact=f"/catalog/sfx/{asset_id}.wav",
        intent=intent,
        duration_ms=duration_ms,
        energy=energy,
        prominence=prominence,
        loop_safe=loop_safe,
        source_catalog="test-sfx-catalog",
        usage_state=usage,
        source_sha256=(asset_id[0] * 64),
        tags=tags,
        format="wav",
        exists=exists,
    )


# -------------------------------------------------------------------------
# A. Narrative Integration Tests
# -------------------------------------------------------------------------

def test_narrative_hook_reveal_payoff_and_routine():
    plan, pacing, dna = make_authorities(tone=["BOLD", "ENERGETIC"])
    assets = [
        make_sfx_asset("whoosh-1", intent=SFXIntentType.WHOOSH, energy=0.7),
        make_sfx_asset("impact-1", intent=SFXIntentType.IMPACT, energy=0.8),
    ]
    sfx_plan = SFXDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
        available_assets=assets,
    )
    # Hook receives whoosh, payoff receives impact
    active_cues = [c for c in sfx_plan.cues if not c.suppressed]
    narrative_roles = [c.intent.narrative_role for c in active_cues if c.intent.narrative_role]
    assert NarrativeSectionRole.HOOK.value in narrative_roles
    assert NarrativeSectionRole.PAYOFF.value in narrative_roles

    # Routine developmental section generates no unbacked narrative SFX
    dev_cues = [c for c in active_cues if c.intent.narrative_role == NarrativeSectionRole.DEVELOPMENT.value]
    assert len(dev_cues) == 0


def test_serious_factual_section_defaults_to_none():
    plan, pacing, dna = make_authorities(tone=["FACTUAL", "SERIOUS", "CALM"])
    assets = [
        make_sfx_asset("whoosh-1", intent=SFXIntentType.WHOOSH, energy=0.8),
    ]
    sfx_plan = SFXDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
        available_assets=assets,
    )
    # Hook should NOT receive aggressive whoosh when channel is calm/factual/serious
    active_cues = [c for c in sfx_plan.cues if not c.suppressed]
    assert not any(c.intent.intent == SFXIntentType.WHOOSH for c in active_cues)


# -------------------------------------------------------------------------
# B. Visual-Beat Integration Tests
# -------------------------------------------------------------------------

def test_visual_reveal_comparison_and_document():
    plan, pacing, dna = make_authorities()
    beats = [
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=0,
            start_offset_ms=2000,
            end_offset_ms=4000,
            duration_ms=2000,
            visual_intent="Show core evidentiary chart",
            information_goal="Reveal data point",
            visual_role=VisualRole.REVEAL,
        ),
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=1,
            start_offset_ms=5000,
            end_offset_ms=7000,
            duration_ms=2000,
            visual_intent="Switch comparison to side B",
            information_goal="Contrast models",
            visual_role=VisualRole.COMPARE,
        ),
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=2,
            start_offset_ms=8000,
            end_offset_ms=10000,
            duration_ms=2000,
            visual_intent="Inspect primary source document",
            information_goal="Examine clause",
            visual_role=VisualRole.DOCUMENT,
        ),
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=3,
            start_offset_ms=11000,
            end_offset_ms=13000,
            duration_ms=2000,
            visual_intent="General ambient scene background",
            information_goal="Ordinary context",
            visual_role=VisualRole.BROLL,
        ),
    ]
    assets = [
        make_sfx_asset("reveal-1", intent=SFXIntentType.REVEAL),
        make_sfx_asset("click-1", intent=SFXIntentType.UI_CLICK),
    ]
    sfx_plan = SFXDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
        visual_beats=beats,
        available_assets=assets,
    )
    active_cues = [c for c in sfx_plan.cues if not c.suppressed]
    visual_roles = [c.intent.visual_role for c in active_cues if c.intent.visual_role]
    assert VisualRole.REVEAL.value in visual_roles
    assert VisualRole.COMPARE.value in visual_roles
    assert VisualRole.DOCUMENT.value in visual_roles
    assert VisualRole.BROLL.value not in visual_roles  # Ordinary BROLL does not produce sound


# -------------------------------------------------------------------------
# C. Camera / Transition Integration Tests
# -------------------------------------------------------------------------

def test_camera_transition_integration():
    plan, pacing, dna = make_authorities()
    c_plan = CameraPlan(
        visual_beat_id=uuid.uuid4(),
        parent_scene_index=1,
        beat_index=0,
        source_editorial_beat_indices=(0,),
        duration_ms=2000,
        preferred_asset_type="IMAGE",
        intent=CameraIntent.PUSH_IN,
        strength=MotionStrength.EMPHATIC,
        start_state=CameraFrameState(scale=1.0, center_x=0.5, center_y=0.5),
        end_state=CameraFrameState(scale=1.15, center_x=0.5, center_y=0.5),
        editorial_purpose="Draw focus to crucial finding",
    )
    t_plan_switch = TransitionPlan(
        visual_beat_id=uuid.uuid4(),
        parent_scene_index=1,
        beat_index=1,
        source_editorial_beat_indices=(1,),
        requested_intent=TransitionIntent.HARD_CONTEXT_SWITCH,
        applied_intent=TransitionIntent.HARD_CONTEXT_SWITCH,
        duration_ms=400,
    )
    t_plan_cut = TransitionPlan(
        visual_beat_id=uuid.uuid4(),
        parent_scene_index=1,
        beat_index=2,
        source_editorial_beat_indices=(2,),
        requested_intent=TransitionIntent.CUT,
        applied_intent=TransitionIntent.CUT,
        duration_ms=0,
    )
    cam_trans = CameraTransitionPlan(
        parent_scene_index=1,
        camera_plans=(c_plan,),
        transition_plans=(t_plan_switch, t_plan_cut),
    )
    assets = [
        make_sfx_asset("trans-1", intent=SFXIntentType.TRANSITION),
        make_sfx_asset("accent-1", intent=SFXIntentType.ACCENT),
    ]
    sfx_plan = SFXDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
        camera_transition_plan=cam_trans,
        available_assets=assets,
    )
    active_cues = [c for c in sfx_plan.cues if not c.suppressed]
    # Hard context switch receives transition intent
    has_transition = any(c.intent.transition_intent == TransitionIntent.HARD_CONTEXT_SWITCH.value for c in active_cues)
    assert has_transition is True
    # Ordinary CUT does NOT receive sound
    has_cut = any(c.intent.transition_intent == TransitionIntent.CUT.value for c in active_cues)
    assert has_cut is False


# -------------------------------------------------------------------------
# D. Density Policy Tests
# -------------------------------------------------------------------------

def test_density_policy_enforcement_and_overuse():
    plan, pacing, dna = make_authorities(pacing=PacingProfile.DELIBERATE)
    # Generate 10 visual beats in close succession
    beats = [
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=i,
            start_offset_ms=i * 1500,
            end_offset_ms=(i + 1) * 1500,
            duration_ms=1500,
            visual_intent=f"Beat {i}",
            information_goal="Evidence",
            visual_role=VisualRole.REVEAL,
        )
        for i in range(10)
    ]
    assets = [make_sfx_asset("reveal-1", intent=SFXIntentType.REVEAL)]
    sfx_plan = SFXDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
        visual_beats=beats,
        available_assets=assets,
    )
    # DELIBERATE max is 4 cues / min. 50s video -> ~3-4 active cues max
    active_cues = [c for c in sfx_plan.cues if not c.suppressed]
    assert len(active_cues) <= 4
    # Overuse or collision finding recorded
    assert any(
        f in (SFXFindingCode.SFX_OVERUSE, SFXFindingCode.SEMANTIC_COLLISION)
        for f in sfx_plan.findings
    )


# -------------------------------------------------------------------------
# E. Pacing Integration Tests
# -------------------------------------------------------------------------

def test_pacing_profiles_fast_vs_deliberate():
    plan_fast, pacing_fast, dna = make_authorities(pacing=PacingProfile.FAST)
    plan_delib, pacing_delib, _ = make_authorities(pacing=PacingProfile.DELIBERATE)
    beats = [
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=i,
            start_offset_ms=i * 2000,
            end_offset_ms=(i + 1) * 2000,
            duration_ms=2000,
            visual_intent=f"Beat {i}",
            information_goal="Point",
            visual_role=VisualRole.EMPHASIZE,
        )
        for i in range(8)
    ]
    assets = [make_sfx_asset("accent-1", intent=SFXIntentType.ACCENT)]

    sfx_fast = SFXDirector.direct(
        narrative_plan=plan_fast,
        pacing_plan=pacing_fast,
        channel_dna=dna,
        visual_beats=beats,
        available_assets=assets,
    )
    sfx_delib = SFXDirector.direct(
        narrative_plan=plan_delib,
        pacing_plan=pacing_delib,
        channel_dna=dna,
        visual_beats=beats,
        available_assets=assets,
    )
    active_fast = [c for c in sfx_fast.cues if not c.suppressed]
    active_delib = [c for c in sfx_delib.cues if not c.suppressed]
    assert len(active_fast) >= len(active_delib)


# -------------------------------------------------------------------------
# F. Music Coexistence Tests
# -------------------------------------------------------------------------

def test_music_coexistence_peak_and_silence():
    plan, pacing, dna = make_authorities(tone=["BOLD", "ENERGETIC"])
    arc = MusicDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
    )
    beats = [
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=0,
            start_offset_ms=30100,  # Coincides with payoff
            end_offset_ms=32000,
            duration_ms=1900,
            visual_intent="Payoff impact",
            information_goal="Goal",
            visual_role=VisualRole.REVEAL,
        ),
    ]
    assets = [make_sfx_asset("impact-1", intent=SFXIntentType.IMPACT, energy=0.9)]
    sfx_plan = SFXDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
        visual_beats=beats,
        music_arc=arc,
        available_assets=assets,
    )
    assert any(
        f == SFXFindingCode.MUSIC_PEAK_COLLISION or any(c.suppressed for c in sfx_plan.cues)
        for f in sfx_plan.findings
    ) or any(c.suppressed for c in sfx_plan.cues)


# -------------------------------------------------------------------------
# G. Eligibility Tests
# -------------------------------------------------------------------------

def test_eligibility_checks():
    cue = SFXCue(
        cue_id="test-cue",
        intent=SFXIntent(
            intent=SFXIntentType.UI_CLICK,
            editorial_purpose="Inspection click",
            start_ms=1000,
            duration_ms=500,
        ),
        start_ms=1000,
        duration_ms=500,
    )
    # 1. Eligible asset
    a_valid = make_sfx_asset("valid-1", intent=SFXIntentType.UI_CLICK, usage=TrackUsageState.OWNED)
    assert SFXEligibilityEngine.evaluate_candidate(asset=a_valid, cue=cue)[0] is True

    # 2. Missing asset
    a_missing = make_sfx_asset("missing-1", intent=SFXIntentType.UI_CLICK, exists=False)
    assert SFXEligibilityEngine.evaluate_candidate(asset=a_missing, cue=cue)[0] is False

    # 3. Invalid usage state
    a_blocked = make_sfx_asset("blocked-1", intent=SFXIntentType.UI_CLICK, usage=TrackUsageState.BLOCKED)
    assert SFXEligibilityEngine.evaluate_candidate(asset=a_blocked, cue=cue)[0] is False

    # 4. Unknown usage state
    a_unknown = make_sfx_asset("unknown-1", intent=SFXIntentType.UI_CLICK, usage=TrackUsageState.UNKNOWN)
    assert SFXEligibilityEngine.evaluate_candidate(asset=a_unknown, cue=cue)[0] is False

    # 5. Incompatible intent
    a_ambience = make_sfx_asset("ambience-1", intent=SFXIntentType.AMBIENCE, usage=TrackUsageState.OWNED)
    assert SFXEligibilityEngine.evaluate_candidate(asset=a_ambience, cue=cue)[0] is False


# -------------------------------------------------------------------------
# H. Selection Engine Tests
# -------------------------------------------------------------------------

def test_selection_ranking_and_tie_breaking():
    cue = SFXCue(
        cue_id="cue-1",
        intent=SFXIntent(
            intent=SFXIntentType.UI_CLICK,
            editorial_purpose="Click",
            start_ms=1000,
            duration_ms=400,
            energy=0.3,
        ),
        start_ms=1000,
        duration_ms=400,
        energy=0.3,
    )
    dna = ChannelDNA(brand_voice=BrandVoice(tone=["CALM", "ANALYTICAL"]))

    # Two exact intent matches with identical score parameters -> tie broken by asset_id alphabetically
    a_b = make_sfx_asset("click-b", intent=SFXIntentType.UI_CLICK, duration_ms=400, energy=0.3)
    a_a = make_sfx_asset("click-a", intent=SFXIntentType.UI_CLICK, duration_ms=400, energy=0.3)

    res = SFXSelectionEngine.select(
        cue=cue,
        available_assets=[a_b, a_a],
        channel_dna=dna,
    )
    assert res.selected_asset is not None
    assert res.selected_asset.asset_id == "click-a"  # Sorted deterministically


def test_selection_repetition_penalty():
    cue = SFXCue(
        cue_id="cue-1",
        intent=SFXIntent(
            intent=SFXIntentType.UI_CLICK,
            editorial_purpose="Click",
            start_ms=1000,
            duration_ms=400,
        ),
        start_ms=1000,
        duration_ms=400,
    )
    dna = ChannelDNA()
    a_recent = make_sfx_asset("click-1", intent=SFXIntentType.UI_CLICK)
    a_fresh = make_sfx_asset("click-2", intent=SFXIntentType.UI_CLICK)

    # If click-1 was recently used, click-2 should win
    res = SFXSelectionEngine.select(
        cue=cue,
        available_assets=[a_recent, a_fresh],
        channel_dna=dna,
        recent_selected_asset_ids=["click-1"],
    )
    assert res.selected_asset is not None
    assert res.selected_asset.asset_id == "click-2"


# -------------------------------------------------------------------------
# I. Collision Planning Tests
# -------------------------------------------------------------------------

def test_semantic_collision_suppresses_lower_priority():
    plan, pacing, dna = make_authorities()
    beats = [
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=0,
            start_offset_ms=2000,
            end_offset_ms=2200,  # 200ms apart from next beat!
            duration_ms=200,
            visual_intent="First beat",
            information_goal="Goal",
            visual_role=VisualRole.EMPHASIZE,  # Priority 65
        ),
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=1,
            start_offset_ms=2100,  # Colliding!
            end_offset_ms=3000,
            duration_ms=900,
            visual_intent="Second beat reveal",
            information_goal="Goal",
            visual_role=VisualRole.REVEAL,  # Priority 70
        ),
    ]
    assets = [
        make_sfx_asset("accent-1", intent=SFXIntentType.ACCENT),
        make_sfx_asset("reveal-1", intent=SFXIntentType.REVEAL),
    ]
    sfx_plan = SFXDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
        visual_beats=beats,
        available_assets=assets,
    )
    assert SFXFindingCode.SEMANTIC_COLLISION in sfx_plan.findings
    suppressed_cues = [c for c in sfx_plan.cues if c.suppressed]
    assert len(suppressed_cues) >= 1


# -------------------------------------------------------------------------
# J. Motif & Repetition Tests
# -------------------------------------------------------------------------

def test_intentional_motif_retained():
    cue = SFXCue(
        cue_id="cue-motif",
        intent=SFXIntent(
            intent=SFXIntentType.UI_CLICK,
            editorial_purpose="Recurring UI motif",
            start_ms=1000,
            duration_ms=400,
            motif_id="system-marker",
        ),
        start_ms=1000,
        duration_ms=400,
        continuity_motif_id="system-marker",
    )
    dna = ChannelDNA()
    a_motif = make_sfx_asset("click-system", intent=SFXIntentType.UI_CLICK, tags=("system-marker",))
    a_other = make_sfx_asset("click-other", intent=SFXIntentType.UI_CLICK)

    # Even if click-system was recently selected, motif bonus protects it
    res = SFXSelectionEngine.select(
        cue=cue,
        available_assets=[a_motif, a_other],
        channel_dna=dna,
        recent_selected_asset_ids=["click-system"],
    )
    assert res.selected_asset is not None
    assert res.selected_asset.asset_id == "click-system"


# -------------------------------------------------------------------------
# K. Duration Policy Tests
# -------------------------------------------------------------------------

def test_duration_suitability_rejection():
    cue = SFXCue(
        cue_id="cue-short",
        intent=SFXIntent(
            intent=SFXIntentType.UI_CLICK,
            editorial_purpose="Quick click",
            start_ms=1000,
            duration_ms=200,
        ),
        start_ms=1000,
        duration_ms=200,
    )
    # Asset with 15000ms duration is excessively long for a 200ms click window
    a_long = make_sfx_asset("click-huge", intent=SFXIntentType.UI_CLICK, duration_ms=15_000, loop_safe=False)
    eligible, reasons = SFXEligibilityEngine.evaluate_candidate(asset=a_long, cue=cue)
    assert eligible is False
    assert any("EXCESSIVE_DURATION" in r for r in reasons)


# -------------------------------------------------------------------------
# L. P23-A Handoff Tests
# -------------------------------------------------------------------------

def test_p23a_handoff_and_mix_authority():
    cue = SFXCue(
        cue_id="test-cue-1",
        intent=SFXIntent(
            intent=SFXIntentType.UI_CLICK,
            editorial_purpose="Inspection click",
            start_ms=2000,
            duration_ms=300,
        ),
        start_ms=2000,
        duration_ms=300,
        collision_priority=60,
    )
    asset = make_sfx_asset("click-1", intent=SFXIntentType.UI_CLICK, duration_ms=300)
    selection = SFXSelectionResult(cue=cue, selected_asset=asset)

    stems = sfx_selections_to_audio_stems([selection])
    assert len(stems) == 1
    stem = stems[0]
    assert stem.role == AudioStemRole.SFX
    assert stem.stem_id == "sfx-test-cue-1"
    assert stem.start_ms == 2000
    assert stem.end_ms == 2300
    assert stem.source_identity == "click-1"
    assert stem.source_sha256 == ("c" * 64)
    assert stem.priority == 60
    assert stem.lineage["sfx_director_version"] == SFX_DIRECTOR_VERSION

    # Now verify P23-A preserves mix authority:
    # When fed into AudioMixPlanner with overlapping speech (narration at 1800ms - 2500ms),
    # P23-A attenuates SFX by 6 dB!
    narration_stem = AudioStem(
        stem_id="narration-1",
        role=AudioStemRole.NARRATION,
        source_artifact="/catalog/narration.wav",
        source_identity="narr-1",
        start_ms=1800,
        end_ms=2500,
    )
    plan = AudioMixPlanner.plan(
        timeline_duration_ms=5000,
        stems=list(stems) + [narration_stem],
        narration_intervals_ms=((1800, 2500),),
    )
    planned_sfx = [s for s in plan.stems if s.role == AudioStemRole.SFX][0]
    assert planned_sfx.gain_db == -6.0  # P23-A applied 6 dB collision attenuation!


# -------------------------------------------------------------------------
# M. Fallback & Intentional Silence Tests
# -------------------------------------------------------------------------

def test_fallback_empty_catalog():
    plan, pacing, dna = make_authorities()
    sfx_plan = SFXDirector.direct(
        narrative_plan=plan,
        pacing_plan=pacing,
        channel_dna=dna,
        available_assets=[],  # Empty catalog
    )
    assert len(sfx_plan.selections) > 0
    # Every selection has None selected asset (clean fallback to silence)
    for sel in sfx_plan.selections:
        assert sel.selected_asset is None
        assert "No eligible" in sel.rationale or "NONE" in sel.rationale or "suppressed" in sel.rationale

    # Hand off to P23-A stems results in empty SFX stem tuple without errors
    stems = sfx_selections_to_audio_stems(sfx_plan.selections)
    assert len(stems) == 0
