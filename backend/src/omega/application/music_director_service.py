"""Deterministic P23-B music direction, eligibility, selection, and P23-A handoff."""

from __future__ import annotations

from collections.abc import Sequence

from omega.domain.audio_mix import AudioStem, AudioStemRole
from omega.domain.channel_dna import ChannelDNA
from omega.domain.music_direction import (
    MUSIC_DIRECTOR_VERSION,
    MusicArc,
    MusicCandidateScore,
    MusicContinuityFindingCode,
    MusicCue,
    MusicDurationPolicy,
    MusicIntent,
    MusicIntentType,
    MusicSelectionResult,
    MusicTrackMetadata,
    MusicTransitionIntent,
    TrackUsageState,
    VocalPolicy,
)
from omega.domain.narrative_pacing import PacingPlan, PacingProfile
from omega.domain.narrative_plan import NarrativePlan, NarrativeSectionRole

_ELIGIBLE_USAGE = {
    TrackUsageState.OWNED,
    TrackUsageState.GENERATED,
    TrackUsageState.LICENSED,
    TrackUsageState.PUBLIC_DOMAIN,
    TrackUsageState.ATTRIBUTION_REQUIRED,
}


class MusicContinuityEvaluator:
    """Detect explainable continuity faults in a resolved cue sequence."""

    @staticmethod
    def evaluate(cues: Sequence[MusicCue]) -> tuple[MusicContinuityFindingCode, ...]:
        ordered = sorted(cues, key=lambda cue: (cue.start_ms, cue.narrative_section_order))
        findings: list[MusicContinuityFindingCode] = []
        resolved = [cue for cue in ordered if cue.selected_asset_id is not None]
        switches = sum(
            previous.selected_asset_id != current.selected_asset_id
            for previous, current in zip(resolved, resolved[1:], strict=False)
        )
        if switches >= 3:
            findings.append(MusicContinuityFindingCode.EXCESSIVE_MUSIC_SWITCHING)

        assets_by_motif: dict[str, set[str]] = {}
        for cue in resolved:
            assets_by_motif.setdefault(cue.continuity_group, set()).add(
                cue.selected_asset_id or ""
            )
        if any(len(assets) > 1 for assets in assets_by_motif.values()):
            findings.append(MusicContinuityFindingCode.MUSIC_MOTIF_DRIFT)

        justified_transitions = {
            MusicTransitionIntent.CROSSFADE,
            MusicTransitionIntent.CUT,
            MusicTransitionIntent.REPLACE_AT_BOUNDARY,
        }
        if any(
            previous.selected_asset_id != current.selected_asset_id
            and current.transition_intent not in justified_transitions
            for previous, current in zip(resolved, resolved[1:], strict=False)
        ):
            findings.append(MusicContinuityFindingCode.UNJUSTIFIED_TRACK_CHANGE)

        if any(
            previous.end_ms != current.start_ms
            or abs(previous.target_energy - current.target_energy) > 0.55
            for previous, current in zip(ordered, ordered[1:], strict=False)
        ):
            findings.append(MusicContinuityFindingCode.MUSIC_ARC_DISCONTINUITY)
        return tuple(findings)


class MusicDirector:
    """Maps accepted narrative/pacing authority to editorial music cues."""

    @classmethod
    def direct(
        cls,
        *,
        narrative_plan: NarrativePlan,
        pacing_plan: PacingPlan,
        channel_dna: ChannelDNA,
    ) -> MusicArc:
        if pacing_plan.narrative_plan_id != narrative_plan.id:
            raise ValueError("PacingPlan does not belong to NarrativePlan")
        tone = " ".join(channel_dna.brand_voice.tone).lower()
        styles = " ".join(channel_dna.audience.preferred_style).lower()
        profile = f"{tone} {styles}"
        no_music = any(term in profile for term in ("no music", "music-free", "silent"))
        calm = any(term in profile for term in ("calm", "analytical", "minimal"))
        energetic = any(term in profile for term in ("energetic", "dynamic", "bold"))
        pacing = pacing_plan.pacing_profile
        cursor_ms = 0
        cues: list[MusicCue] = []
        energy_curve: list[float] = []
        ordered_sections = sorted(
            narrative_plan.sections,
            key=lambda section: section.section_order,
        )
        for index, section in enumerate(ordered_sections):
            timing = next(
                (item for item in pacing_plan.section_timings if item.section_id == section.id),
                None,
            )
            duration_ms = 1_000 * (
                timing.recommended_duration_seconds if timing else section.target_duration_seconds
            )
            base_energy, intent_type, mood = cls._role_profile(section.role)
            if pacing == PacingProfile.FAST:
                base_energy += 0.10
            elif pacing == PacingProfile.DELIBERATE:
                base_energy -= 0.12
            if energetic:
                base_energy += 0.10
            if calm:
                base_energy -= 0.10
            energy = max(0.0, min(0.9, base_energy))
            serious_evidence = (
                section.role == NarrativeSectionRole.CONTEXT
                and bool(section.grounding_references)
            )
            if no_music or serious_evidence:
                intent_type = MusicIntentType.NONE
                energy = 0.0
            start_ms, end_ms = cursor_ms, cursor_ms + duration_ms
            transition = cls._transition(index, section.role, intent_type)
            intent = MusicIntent(
                intent=intent_type,
                mood=mood,
                energy=energy,
                intensity=energy,
                tempo_min_bpm=90 if pacing == PacingProfile.FAST else 60,
                tempo_max_bpm=150 if pacing == PacingProfile.FAST else 125,
                vocal_policy=VocalPolicy.INSTRUMENTAL_ONLY,
                start_ms=start_ms,
                end_ms=end_ms,
                narrative_role=section.role.value,
                continuity_group="main-motif",
                transition_intent=transition,
            )
            cues.append(
                MusicCue(
                    cue_id=f"music-{section.section_order}",
                    intent=intent,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    target_energy=energy,
                    mood=mood,
                    continuity_group="main-motif",
                    narrative_section_id=str(section.id),
                    narrative_section_order=section.section_order,
                    transition_intent=transition,
                    fade_in_ms=500 if transition == MusicTransitionIntent.FADE_IN else 0,
                    fade_out_ms=750 if transition == MusicTransitionIntent.FADE_OUT else 0,
                )
            )
            energy_curve.append(energy)
            cursor_ms = end_ms

        findings: list[MusicContinuityFindingCode] = []
        if energy_curve and max(energy_curve[: max(1, len(energy_curve) // 2)]) >= 0.9:
            findings.append(MusicContinuityFindingCode.EARLY_ENERGY_PEAK)
        oscillations = sum(
            1
            for a, b, c in zip(
                energy_curve,
                energy_curve[1:],
                energy_curve[2:],
                strict=False,
            )
            if (b - a) * (c - b) < 0 and abs(b - a) > 0.35 and abs(c - b) > 0.35
        )
        if oscillations > 1:
            findings.append(MusicContinuityFindingCode.ENERGY_OSCILLATION)
        return MusicArc(
            narrative_plan_id=str(narrative_plan.id),
            pacing_profile=pacing.value,
            channel_profile=profile.strip() or "default",
            cues=tuple(cues),
            energy_curve=tuple(energy_curve),
            findings=tuple(findings),
        )

    @staticmethod
    def _role_profile(
        role: NarrativeSectionRole,
    ) -> tuple[float, MusicIntentType, tuple[str, ...]]:
        return {
            NarrativeSectionRole.HOOK: (0.45, MusicIntentType.INTRO, ("curious",)),
            NarrativeSectionRole.PROMISE: (0.38, MusicIntentType.BACKGROUND, ("restrained",)),
            NarrativeSectionRole.CONTEXT: (0.30, MusicIntentType.BACKGROUND, ("neutral",)),
            NarrativeSectionRole.DEVELOPMENT: (0.48, MusicIntentType.DISCOVERY, ("focused",)),
            NarrativeSectionRole.ESCALATION: (0.68, MusicIntentType.BUILD, ("driving",)),
            NarrativeSectionRole.PAYOFF: (0.82, MusicIntentType.PAYOFF, ("triumphant",)),
            NarrativeSectionRole.TAKEAWAY: (0.42, MusicIntentType.REFLECTION, ("reflective",)),
            NarrativeSectionRole.CLOSING: (0.28, MusicIntentType.RESOLUTION, ("resolved",)),
            NarrativeSectionRole.CTA: (0.35, MusicIntentType.OUTRO, ("optimistic",)),
        }[role]

    @staticmethod
    def _transition(
        index: int,
        role: NarrativeSectionRole,
        intent: MusicIntentType,
    ) -> MusicTransitionIntent:
        if intent == MusicIntentType.NONE:
            return MusicTransitionIntent.FADE_OUT
        if index == 0:
            return MusicTransitionIntent.FADE_IN
        if role in (NarrativeSectionRole.PAYOFF, NarrativeSectionRole.CLOSING):
            return MusicTransitionIntent.REPLACE_AT_BOUNDARY
        return MusicTransitionIntent.CONTINUE


class MusicSelectionEngine:
    """Eligibility is absolute; scoring can rank only eligible supplied tracks."""

    @classmethod
    def select(
        cls,
        cue: MusicCue,
        tracks: Sequence[MusicTrackMetadata],
        *,
        previous_asset_id: str | None = None,
        recent_asset_ids: Sequence[str] = (),
    ) -> MusicSelectionResult:
        if cue.intent.intent == MusicIntentType.NONE:
            return MusicSelectionResult(
                cue=cue,
                selected_track=None,
                candidate_scores=(),
                rationale="Intentional silence requested by MusicDirector.",
            )
        scores = tuple(
            cls._score(cue, track, previous_asset_id, recent_asset_ids) for track in tracks
        )
        eligible = [score for score in scores if score.eligible]
        if not eligible:
            return MusicSelectionResult(
                cue=cue,
                selected_track=None,
                candidate_scores=scores,
                rationale="No eligible supplied track; narration-only fallback.",
            )
        winner = sorted(eligible, key=lambda item: (-item.total_score, item.asset_id))[0]
        selected = next(track for track in tracks if track.asset_id == winner.asset_id)
        cue_duration = cue.end_ms - cue.start_ms
        if selected.duration_ms >= cue_duration:
            policy = MusicDurationPolicy.TRIM
        elif selected.loop_safe is True:
            policy = MusicDurationPolicy.LOOP_SAFE
        else:
            policy = MusicDurationPolicy.CONTINUE
        resolved = cue.model_copy(
            update={"selected_asset_id": selected.asset_id, "duration_policy": policy}
        )
        return MusicSelectionResult(
            cue=resolved,
            selected_track=selected,
            candidate_scores=scores,
            rationale=(
                f"Selected {selected.asset_id} by deterministic intent, energy, mood, "
                "duration, continuity, and repetition scoring."
            ),
        )

    @staticmethod
    def evaluate_continuity(
        selections: Sequence[MusicSelectionResult],
    ) -> tuple[MusicContinuityFindingCode, ...]:
        return MusicContinuityEvaluator.evaluate([selection.cue for selection in selections])

    @classmethod
    def _score(
        cls,
        cue: MusicCue,
        track: MusicTrackMetadata,
        previous_asset_id: str | None,
        recent_asset_ids: Sequence[str],
    ) -> MusicCandidateScore:
        reasons: list[str] = []
        if not track.exists:
            reasons.append("MISSING_ASSET")
        if track.usage_state not in _ELIGIBLE_USAGE:
            reasons.append("USAGE_NOT_ELIGIBLE")
        if (
            cue.intent.vocal_policy == VocalPolicy.INSTRUMENTAL_ONLY
            and track.has_vocals is not False
        ):
            reasons.append("INSTRUMENTAL_STATUS_NOT_CONFIRMED")
        cue_duration = cue.end_ms - cue.start_ms
        if track.duration_ms < cue_duration and track.loop_safe is not True:
            reasons.append("DURATION_TOO_SHORT_AND_NOT_LOOP_SAFE")
        if reasons:
            return MusicCandidateScore(
                asset_id=track.asset_id,
                eligible=False,
                total_score=0.0,
                rejected_reasons=tuple(reasons),
            )
        components: dict[str, float] = {}
        components["energy"] = (
            30.0 * (1.0 - abs(track.energy - cue.target_energy))
            if track.energy is not None
            else 0.0
        )
        wanted_moods = set(cue.mood)
        components["mood"] = 25.0 if wanted_moods.intersection(track.moods) else 0.0
        if track.bpm is not None and cue.intent.tempo_min_bpm is not None:
            components["tempo"] = (
                15.0
                if cue.intent.tempo_min_bpm <= track.bpm <= (cue.intent.tempo_max_bpm or 240)
                else 0.0
            )
        else:
            components["tempo"] = 0.0
        components["duration"] = 15.0
        components["continuity"] = 20.0 if track.asset_id == previous_asset_id else 0.0
        repeated_outside_motif = (
            track.asset_id in recent_asset_ids
            and track.asset_id != previous_asset_id
        )
        components["repetition"] = -10.0 if repeated_outside_motif else 0.0
        return MusicCandidateScore(
            asset_id=track.asset_id,
            eligible=True,
            total_score=round(sum(components.values()), 6),
            components=components,
        )


def music_selection_to_audio_stem(selection: MusicSelectionResult) -> AudioStem | None:
    """Translate a resolved cue to P23-A without importing any renderer behavior."""
    track = selection.selected_track
    cue = selection.cue
    if track is None:
        return None
    return AudioStem(
        stem_id=f"music-{cue.cue_id}-{track.asset_id}",
        role=AudioStemRole.MUSIC,
        source_artifact=track.source_artifact,
        source_identity=track.asset_id,
        source_sha256=track.source_sha256,
        start_ms=cue.start_ms,
        end_ms=cue.end_ms,
        gain_db=-20.0,
        fade_in_ms=cue.fade_in_ms,
        fade_out_ms=cue.fade_out_ms,
        priority=30,
        optional=True,
        lineage={
            "music_cue_id": cue.cue_id,
            "narrative_section_id": cue.narrative_section_id,
            "continuity_group": cue.continuity_group,
            "music_director_version": MUSIC_DIRECTOR_VERSION,
            "catalog": track.catalog or "unknown",
        },
    )
