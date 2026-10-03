from __future__ import annotations

import uuid

import pytest

from omega.application.music_director_service import (
    MusicContinuityEvaluator,
    MusicDirector,
    MusicSelectionEngine,
    music_selection_to_audio_stem,
)
from omega.domain.audio_mix import AudioStemRole
from omega.domain.channel_dna import BrandVoice, ChannelDNA
from omega.domain.music_direction import (
    MusicContinuityFindingCode,
    MusicDurationPolicy,
    MusicIntentType,
    MusicTrackMetadata,
    MusicTransitionIntent,
    TrackUsageState,
)
from omega.domain.narrative_pacing import (
    PacingPlan,
    PacingProfile,
    RevealStage,
    SectionTimingDetail,
)
from omega.domain.narrative_plan import (
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeSection,
    NarrativeSectionRole,
)

ROLES = (
    NarrativeSectionRole.HOOK,
    NarrativeSectionRole.DEVELOPMENT,
    NarrativeSectionRole.ESCALATION,
    NarrativeSectionRole.PAYOFF,
    NarrativeSectionRole.CLOSING,
)


def authorities(
    pacing: PacingProfile = PacingProfile.BALANCED,
    tone: list[str] | None = None,
) -> tuple[NarrativePlan, PacingPlan, ChannelDNA]:
    sections = [
        NarrativeSection(
            section_order=index,
            role=role,
            objective=f"Narrative objective {index}",
            target_duration_seconds=10,
        )
        for index, role in enumerate(ROLES, 1)
    ]
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=NarrativePlanStatus.APPROVED,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=50,
        estimated_duration_seconds=50,
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
        target_duration_seconds=50,
        initial_duration_sum_seconds=50,
        optimized_duration_sum_seconds=50,
        section_timings=timings,
        open_loop_metrics=[],
        findings=[],
        recommended_adjustments=[],
        escalation_curve=[item.intensity_score for item in timings],
    )
    dna = ChannelDNA(brand_voice=BrandVoice(tone=tone or ["CLEAR", "STRUCTURED"]))
    return plan, pacing_plan, dna


def track(
    asset_id: str,
    *,
    energy: float | None = 0.5,
    moods: tuple[str, ...] = ("focused",),
    duration_ms: int = 60_000,
    bpm: int | None = 100,
    vocals: bool | None = False,
    loop_safe: bool | None = True,
    usage: TrackUsageState = TrackUsageState.GENERATED,
    exists: bool = True,
) -> MusicTrackMetadata:
    return MusicTrackMetadata(
        asset_id=asset_id,
        source_artifact=f"/catalog/{asset_id}.wav",
        exists=exists,
        duration_ms=duration_ms,
        moods=moods,
        energy=energy,
        bpm=bpm,
        has_vocals=vocals,
        loop_safe=loop_safe,
        catalog="synthetic-test",
        usage_state=usage,
        source_sha256=(asset_id[0] * 64),
    )


@pytest.mark.parametrize(
    "role,intent",
    [
        (NarrativeSectionRole.HOOK, MusicIntentType.INTRO),
        (NarrativeSectionRole.DEVELOPMENT, MusicIntentType.DISCOVERY),
        (NarrativeSectionRole.ESCALATION, MusicIntentType.BUILD),
        (NarrativeSectionRole.PAYOFF, MusicIntentType.PAYOFF),
        (NarrativeSectionRole.CLOSING, MusicIntentType.RESOLUTION),
    ],
)
def test_narrative_roles_map_to_music_intent(role, intent):
    assert MusicDirector._role_profile(role)[1] == intent


def test_music_arc_tracks_narrative_and_energy_progression():
    plan, pacing, dna = authorities()
    arc = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna)
    assert len(arc.cues) == 5
    assert arc.cues[0].transition_intent == MusicTransitionIntent.FADE_IN
    assert arc.energy_curve[3] > arc.energy_curve[2] > arc.energy_curve[1]
    assert arc.cues[-1].transition_intent == MusicTransitionIntent.REPLACE_AT_BOUNDARY


def test_pacing_and_channel_dna_change_energy_without_mechanical_bpm_only():
    fast_plan, fast_pacing, fast_dna = authorities(PacingProfile.FAST, ["ENERGETIC"])
    fast = MusicDirector.direct(
        narrative_plan=fast_plan,
        pacing_plan=fast_pacing,
        channel_dna=fast_dna,
    )
    # Build a coherent second authority set (IDs must match).
    plan, pacing, dna = authorities(PacingProfile.DELIBERATE, ["CALM", "ANALYTICAL"])
    slow = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna)
    assert max(fast.energy_curve) > max(slow.energy_curve)
    assert fast.cues[0].intent.tempo_max_bpm != slow.cues[0].intent.tempo_max_bpm


def test_minimal_channel_can_choose_intentional_silence():
    plan, pacing, dna = authorities(tone=["NO MUSIC"])
    arc = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna)
    assert all(cue.intent.intent == MusicIntentType.NONE for cue in arc.cues)


def test_selection_rejects_missing_blocked_unknown_and_unconfirmed_vocals():
    plan, pacing, dna = authorities()
    cue = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna).cues[1]
    result = MusicSelectionEngine.select(
        cue,
        [
            track("missing", exists=False),
            track("blocked", usage=TrackUsageState.BLOCKED),
            track("unknown", usage=TrackUsageState.UNKNOWN),
            track("vocal", vocals=True),
        ],
    )
    assert result.selected_track is None
    assert all(not candidate.eligible for candidate in result.candidate_scores)


def test_selection_is_deterministic_and_not_first_asset():
    plan, pacing, dna = authorities()
    cue = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna).cues[1]
    poor = track("a-poor", energy=0.0, moods=("ominous",), bpm=200)
    best = track("z-best", energy=cue.target_energy, moods=cue.mood, bpm=100)
    first = MusicSelectionEngine.select(cue, [poor, best])
    second = MusicSelectionEngine.select(cue, [best, poor])
    assert first.selected_track == best
    assert second.selected_track == best
    assert first.candidate_scores[1].components


def test_continuity_beats_repetition_penalty_for_same_motif():
    plan, pacing, dna = authorities()
    cue = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna).cues[2]
    same = track("same", energy=cue.target_energy, moods=cue.mood)
    other = track("other", energy=cue.target_energy, moods=cue.mood)
    result = MusicSelectionEngine.select(
        cue, [other, same], previous_asset_id="same", recent_asset_ids=["same"]
    )
    assert result.selected_track == same


def test_continuity_evaluator_detects_all_required_fault_classes():
    plan, pacing, dna = authorities()
    cues = MusicDirector.direct(
        narrative_plan=plan, pacing_plan=pacing, channel_dna=dna
    ).cues[:4]
    resolved = tuple(
        cue.model_copy(
            update={
                "selected_asset_id": f"track-{index}",
                "target_energy": 0.0 if index % 2 else 0.9,
                "transition_intent": MusicTransitionIntent.CONTINUE,
            }
        )
        for index, cue in enumerate(cues)
    )

    assert set(MusicContinuityEvaluator.evaluate(resolved)) == {
        MusicContinuityFindingCode.EXCESSIVE_MUSIC_SWITCHING,
        MusicContinuityFindingCode.MUSIC_MOTIF_DRIFT,
        MusicContinuityFindingCode.UNJUSTIFIED_TRACK_CHANGE,
        MusicContinuityFindingCode.MUSIC_ARC_DISCONTINUITY,
    }


def test_duration_policy_trim_loop_and_reject_unsafe_short_track():
    plan, pacing, dna = authorities()
    cue = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna).cues[0]
    long_result = MusicSelectionEngine.select(cue, [track("long", duration_ms=20_000)])
    assert long_result.cue.duration_policy == MusicDurationPolicy.TRIM
    loop_result = MusicSelectionEngine.select(cue, [track("loop", duration_ms=2_000)])
    assert loop_result.cue.duration_policy == MusicDurationPolicy.LOOP_SAFE
    unsafe = MusicSelectionEngine.select(
        cue, [track("unsafe", duration_ms=2_000, loop_safe=False)]
    )
    assert unsafe.selected_track is None


def test_empty_catalog_and_none_intent_fall_back_to_no_music():
    plan, pacing, dna = authorities(tone=["NO MUSIC"])
    cue = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna).cues[0]
    assert MusicSelectionEngine.select(cue, []).selected_track is None


def test_p23a_handoff_preserves_timing_fades_identity_and_lineage():
    plan, pacing, dna = authorities()
    cue = MusicDirector.direct(narrative_plan=plan, pacing_plan=pacing, channel_dna=dna).cues[0]
    selected = MusicSelectionEngine.select(cue, [track("calm")])
    stem = music_selection_to_audio_stem(selected)
    assert stem is not None
    assert stem.role == AudioStemRole.MUSIC
    assert (stem.start_ms, stem.end_ms) == (cue.start_ms, cue.end_ms)
    assert stem.fade_in_ms == cue.fade_in_ms
    assert stem.source_identity == "calm"
    assert stem.lineage["music_cue_id"] == cue.cue_id
    assert "duck" not in stem.lineage
