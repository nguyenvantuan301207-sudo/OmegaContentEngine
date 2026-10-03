"""Deterministic P23-C SFX Director, eligibility, selection, and P23-A handoff."""

from __future__ import annotations

import math
from collections.abc import Sequence
from uuid import UUID, uuid4

from omega.domain.audio_mix import AudioStem, AudioStemRole
from omega.domain.camera_transition import CameraIntent, CameraTransitionPlan, TransitionIntent
from omega.domain.channel_dna import ChannelDNA
from omega.domain.music_direction import MusicArc, MusicCue, MusicIntentType, TrackUsageState
from omega.domain.narrative_pacing import PacingPlan, PacingProfile
from omega.domain.narrative_plan import NarrativePlan, NarrativeSectionRole
from omega.domain.sfx_direction import (
    SFX_DIRECTOR_VERSION,
    SFXAssetMetadata,
    SFXCandidateScore,
    SFXCue,
    SFXDurationPolicy,
    SFXFindingCode,
    SFXIntent,
    SFXIntentType,
    SFXPlan,
    SFXProminence,
    SFXSelectionResult,
)
from omega.domain.visual_beat import VisualBeat, VisualRole

_ELIGIBLE_SFX_USAGE = {
    TrackUsageState.OWNED,
    TrackUsageState.GENERATED,
    TrackUsageState.LICENSED,
    TrackUsageState.PUBLIC_DOMAIN,
    TrackUsageState.ATTRIBUTION_REQUIRED,
}

# Compatibility mapping between requested intent and candidate asset intent
_COMPATIBLE_INTENTS: dict[SFXIntentType, set[SFXIntentType]] = {
    SFXIntentType.UI_CLICK: {SFXIntentType.UI_CLICK, SFXIntentType.MECHANICAL, SFXIntentType.ACCENT},
    SFXIntentType.WHOOSH: {SFXIntentType.WHOOSH, SFXIntentType.TRANSITION, SFXIntentType.RISER},
    SFXIntentType.IMPACT: {SFXIntentType.IMPACT, SFXIntentType.ACCENT, SFXIntentType.STINGER},
    SFXIntentType.RISER: {SFXIntentType.RISER, SFXIntentType.WHOOSH, SFXIntentType.TRANSITION},
    SFXIntentType.REVEAL: {SFXIntentType.REVEAL, SFXIntentType.ACCENT, SFXIntentType.IMPACT, SFXIntentType.STINGER},
    SFXIntentType.TRANSITION: {SFXIntentType.TRANSITION, SFXIntentType.WHOOSH, SFXIntentType.ACCENT},
    SFXIntentType.ACCENT: {SFXIntentType.ACCENT, SFXIntentType.UI_CLICK, SFXIntentType.REVEAL, SFXIntentType.NOTIFICATION},
    SFXIntentType.NOTIFICATION: {SFXIntentType.NOTIFICATION, SFXIntentType.UI_CLICK, SFXIntentType.ACCENT},
    SFXIntentType.MECHANICAL: {SFXIntentType.MECHANICAL, SFXIntentType.UI_CLICK},
    SFXIntentType.ENVIRONMENTAL: {SFXIntentType.ENVIRONMENTAL, SFXIntentType.AMBIENCE},
    SFXIntentType.AMBIENCE: {SFXIntentType.AMBIENCE, SFXIntentType.ENVIRONMENTAL},
    SFXIntentType.STINGER: {SFXIntentType.STINGER, SFXIntentType.IMPACT, SFXIntentType.REVEAL},
    SFXIntentType.NONE: set(),
}


class SFXEligibilityEngine:
    """Enforces strict, deterministic eligibility before creative scoring."""

    @classmethod
    def evaluate_candidate(
        cls,
        *,
        asset: SFXAssetMetadata,
        cue: SFXCue,
    ) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []

        if not asset.exists:
            reasons.append("ASSET_FILE_MISSING")

        if asset.usage_state not in _ELIGIBLE_SFX_USAGE:
            reasons.append(f"INELIGIBLE_USAGE_STATE_{asset.usage_state.value}")

        allowed_intents = _COMPATIBLE_INTENTS.get(cue.intent.intent, {cue.intent.intent})
        if asset.intent not in allowed_intents and asset.intent != cue.intent.intent:
            reasons.append(f"INCOMPATIBLE_INTENT_{asset.intent.value}_FOR_{cue.intent.intent.value}")

        # Duration suitability: asset should not grossly exceed cue window unless loop/trim safe
        if cue.duration_ms > 0 and asset.duration_ms > cue.duration_ms + 3000 and not asset.loop_safe:
            reasons.append(f"EXCESSIVE_DURATION_{asset.duration_ms}MS_FOR_CUE_{cue.duration_ms}MS")

        return (len(reasons) == 0, tuple(reasons))


class SFXSelectionEngine:
    """Deterministic, inspectable SFX candidate ranking with stable tie-breaking."""

    @classmethod
    def select(
        cls,
        *,
        cue: SFXCue,
        available_assets: Sequence[SFXAssetMetadata],
        channel_dna: ChannelDNA,
        recent_selected_asset_ids: Sequence[str] = (),
    ) -> SFXSelectionResult:
        if cue.intent.intent == SFXIntentType.NONE or cue.suppressed:
            return SFXSelectionResult(
                cue=cue,
                selected_asset=None,
                candidates=(),
                rationale="Cue is explicitly NONE or suppressed by policy.",
            )

        tone = " ".join(channel_dna.brand_voice.tone).lower()
        calm = any(t in tone for t in ("calm", "analytical", "minimal", "factual"))
        energetic = any(t in tone for t in ("energetic", "dynamic", "bold"))

        candidate_scores: list[SFXCandidateScore] = []
        for asset in available_assets:
            eligible, ineligibility_reasons = SFXEligibilityEngine.evaluate_candidate(
                asset=asset, cue=cue
            )
            if not eligible:
                candidate_scores.append(
                    SFXCandidateScore(
                        asset_id=asset.asset_id,
                        eligible=False,
                        ineligibility_reasons=ineligibility_reasons,
                        total_score=-1.0,
                        rationale=f"Ineligible: {', '.join(ineligibility_reasons)}",
                    )
                )
                continue

            # 1. Intent score (0.40)
            if asset.intent == cue.intent.intent:
                intent_score = 0.40
            else:
                intent_score = 0.20

            # 2. Energy match (0.20)
            energy_diff = abs(asset.energy - cue.energy)
            energy_score = 0.20 * max(0.0, 1.0 - energy_diff)

            # 3. Duration fit (0.15)
            if cue.duration_ms > 0:
                duration_diff = abs(asset.duration_ms - cue.duration_ms) / max(1, cue.duration_ms)
                duration_score = 0.15 * max(0.0, 1.0 - min(1.0, duration_diff))
            else:
                duration_score = 0.15

            # 4. Channel DNA fit (0.15)
            dna_score = 0.10
            if calm:
                if asset.prominence in (SFXProminence.SUBTLE, SFXProminence.BALANCED):
                    dna_score = 0.15
                else:
                    dna_score = 0.05
            elif energetic:
                if asset.energy >= 0.6 or asset.prominence in (SFXProminence.PROMINENT, SFXProminence.ACCENT):
                    dna_score = 0.15

            # 5. Continuity / motif match (0.10)
            continuity_score = 0.0
            if cue.continuity_motif_id and asset.tags and cue.continuity_motif_id in asset.tags:
                continuity_score = 0.10
            elif cue.continuity_motif_id and cue.continuity_motif_id == asset.asset_id:
                continuity_score = 0.10

            # 6. Repetition penalty (-0.25)
            # If asset was recently selected and is NOT an intentional motif, penalize
            repetition_penalty = 0.0
            if asset.asset_id in recent_selected_asset_ids[-2:]:
                if not cue.continuity_motif_id:
                    repetition_penalty = 0.25

            total_score = max(
                0.0,
                intent_score
                + energy_score
                + duration_score
                + dna_score
                + continuity_score
                - repetition_penalty,
            )

            rationale = (
                f"intent={intent_score:.2f}, energy={energy_score:.2f}, "
                f"duration={duration_score:.2f}, dna={dna_score:.2f}, "
                f"continuity={continuity_score:.2f}, penalty={repetition_penalty:.2f}"
            )
            candidate_scores.append(
                SFXCandidateScore(
                    asset_id=asset.asset_id,
                    eligible=True,
                    ineligibility_reasons=(),
                    total_score=round(total_score, 4),
                    intent_score=round(intent_score, 4),
                    energy_score=round(energy_score, 4),
                    duration_score=round(duration_score, 4),
                    context_score=0.0,
                    dna_score=round(dna_score, 4),
                    continuity_score=round(continuity_score, 4),
                    repetition_penalty=round(repetition_penalty, 4),
                    rationale=rationale,
                )
            )

        eligible_scores = [cs for cs in candidate_scores if cs.eligible]
        if not eligible_scores:
            return SFXSelectionResult(
                cue=cue,
                selected_asset=None,
                candidates=tuple(candidate_scores),
                rationale="No eligible SFX asset found; falling back to silence.",
            )

        # Stable tie-breaking: (-total_score, asset_id)
        eligible_scores.sort(key=lambda cs: (-cs.total_score, cs.asset_id))
        winning_score = eligible_scores[0]
        winning_asset = next(a for a in available_assets if a.asset_id == winning_score.asset_id)

        return SFXSelectionResult(
            cue=cue,
            selected_asset=winning_asset,
            candidates=tuple(candidate_scores),
            rationale=f"Selected {winning_asset.asset_id} with score {winning_score.total_score:.4f} ({winning_score.rationale})",
        )


class SFXDirector:
    """Editorial authority for SFX intent derivation, collision planning, density enforcement, and selection."""

    @classmethod
    def direct(
        cls,
        *,
        narrative_plan: NarrativePlan,
        pacing_plan: PacingPlan,
        channel_dna: ChannelDNA,
        visual_beats: Sequence[VisualBeat] | None = None,
        camera_transition_plan: CameraTransitionPlan | None = None,
        music_arc: MusicArc | None = None,
        available_assets: Sequence[SFXAssetMetadata] = (),
    ) -> SFXPlan:
        tone = " ".join(channel_dna.brand_voice.tone).lower()
        styles = " ".join(channel_dna.audience.preferred_style).lower()
        profile = f"{tone} {styles}"

        calm = any(term in profile for term in ("calm", "analytical", "minimal", "factual", "serious"))
        energetic = any(term in profile for term in ("energetic", "dynamic", "bold"))
        pacing = pacing_plan.pacing_profile

        # Density limits based on pacing
        if pacing == PacingProfile.FAST:
            max_cues_per_minute = 10.0
            min_cue_gap_ms = 400
        elif pacing == PacingProfile.DELIBERATE:
            max_cues_per_minute = 4.0
            min_cue_gap_ms = 1200
        else:  # BALANCED
            max_cues_per_minute = 6.0
            min_cue_gap_ms = 800

        if calm:
            max_cues_per_minute = min(max_cues_per_minute, 4.0)
            min_cue_gap_ms = max(min_cue_gap_ms, 1000)

        # Build raw candidate editorial cues
        raw_cues: list[SFXCue] = []
        ordered_sections = sorted(narrative_plan.sections, key=lambda s: s.section_order)

        # 1. Narrative Section Level Intent
        cursor_ms = 0
        section_offsets: dict[UUID, int] = {}
        for section in ordered_sections:
            section_offsets[section.id] = cursor_ms
            timing = next(
                (item for item in pacing_plan.section_timings if item.section_id == section.id),
                None,
            )
            duration_ms = 1_000 * (
                timing.recommended_duration_seconds if timing else section.target_duration_seconds
            )

            # Narrative cues:
            # HOOK: accent/whoosh if energetic or structured hook
            if section.role == NarrativeSectionRole.HOOK and not calm:
                intent_type = SFXIntentType.WHOOSH if energetic else SFXIntentType.ACCENT
                raw_cues.append(
                    SFXCue(
                        cue_id=f"cue-narrative-hook-{uuid4().hex[:6]}",
                        intent=SFXIntent(
                            intent=intent_type,
                            editorial_purpose="Hook open curiosity accent",
                            energy=0.7 if energetic else 0.5,
                            prominence=SFXProminence.BALANCED,
                            start_ms=cursor_ms,
                            duration_ms=min(800, duration_ms),
                            narrative_role=section.role.value,
                            collision_priority=75,
                        ),
                        start_ms=cursor_ms,
                        duration_ms=min(800, duration_ms),
                        energy=0.7 if energetic else 0.5,
                        prominence=SFXProminence.BALANCED,
                        narrative_section_id=section.id,
                        collision_priority=75,
                    )
                )
            elif section.role == NarrativeSectionRole.PAYOFF:
                raw_cues.append(
                    SFXCue(
                        cue_id=f"cue-narrative-payoff-{uuid4().hex[:6]}",
                        intent=SFXIntent(
                            intent=SFXIntentType.IMPACT if energetic else SFXIntentType.REVEAL,
                            editorial_purpose="Payoff resolution delivery accent",
                            energy=0.8 if energetic else 0.6,
                            prominence=SFXProminence.PROMINENT if energetic else SFXProminence.BALANCED,
                            start_ms=cursor_ms + 100,
                            duration_ms=min(1200, duration_ms),
                            narrative_role=section.role.value,
                            collision_priority=85,
                        ),
                        start_ms=cursor_ms + 100,
                        duration_ms=min(1200, duration_ms),
                        energy=0.8 if energetic else 0.6,
                        prominence=SFXProminence.PROMINENT if energetic else SFXProminence.BALANCED,
                        narrative_section_id=section.id,
                        collision_priority=85,
                    )
                )

            cursor_ms += duration_ms

        total_timeline_ms = max(cursor_ms, 1000 * narrative_plan.target_duration_seconds)

        # 2. Visual Beat Level Intent
        if visual_beats:
            for beat in visual_beats:
                beat_start_ms = beat.start_offset_ms
                beat_duration_ms = beat.duration_ms
                if beat.narrative_section_role:
                    # Map section role start offset if available
                    pass

                # Visual Role mappings:
                if beat.visual_role == VisualRole.REVEAL:
                    raw_cues.append(
                        SFXCue(
                            cue_id=f"cue-visual-reveal-{uuid4().hex[:6]}",
                            intent=SFXIntent(
                                intent=SFXIntentType.REVEAL,
                                editorial_purpose="Visual reveal sound accent",
                                energy=0.65,
                                prominence=SFXProminence.BALANCED,
                                start_ms=beat_start_ms,
                                duration_ms=min(600, beat_duration_ms),
                                visual_role=beat.visual_role.value,
                                collision_priority=70,
                                motif_id=beat.continuity_group_id,
                            ),
                            start_ms=beat_start_ms,
                            duration_ms=min(600, beat_duration_ms),
                            energy=0.65,
                            prominence=SFXProminence.BALANCED,
                            visual_beat_id=beat.id,
                            continuity_motif_id=beat.continuity_group_id,
                            collision_priority=70,
                        )
                    )
                elif beat.visual_role == VisualRole.EMPHASIZE:
                    raw_cues.append(
                        SFXCue(
                            cue_id=f"cue-visual-emphasize-{uuid4().hex[:6]}",
                            intent=SFXIntent(
                                intent=SFXIntentType.ACCENT,
                                editorial_purpose="Visual emphasis accent",
                                energy=0.55,
                                prominence=SFXProminence.SUBTLE if calm else SFXProminence.BALANCED,
                                start_ms=beat_start_ms,
                                duration_ms=min(500, beat_duration_ms),
                                visual_role=beat.visual_role.value,
                                collision_priority=65,
                            ),
                            start_ms=beat_start_ms,
                            duration_ms=min(500, beat_duration_ms),
                            energy=0.55,
                            prominence=SFXProminence.SUBTLE if calm else SFXProminence.BALANCED,
                            visual_beat_id=beat.id,
                            collision_priority=65,
                        )
                    )
                elif beat.visual_role in (VisualRole.DOCUMENT, VisualRole.DIAGRAM, VisualRole.DATA):
                    raw_cues.append(
                        SFXCue(
                            cue_id=f"cue-visual-doc-{uuid4().hex[:6]}",
                            intent=SFXIntent(
                                intent=SFXIntentType.UI_CLICK,
                                editorial_purpose="Document/diagram inspection subtle click",
                                energy=0.35,
                                prominence=SFXProminence.SUBTLE,
                                start_ms=beat_start_ms,
                                duration_ms=min(300, beat_duration_ms),
                                visual_role=beat.visual_role.value,
                                collision_priority=55,
                                motif_id=beat.continuity_group_id,
                            ),
                            start_ms=beat_start_ms,
                            duration_ms=min(300, beat_duration_ms),
                            energy=0.35,
                            prominence=SFXProminence.SUBTLE,
                            visual_beat_id=beat.id,
                            continuity_motif_id=beat.continuity_group_id,
                            collision_priority=55,
                        )
                    )
                elif beat.visual_role == VisualRole.COMPARE:
                    raw_cues.append(
                        SFXCue(
                            cue_id=f"cue-visual-compare-{uuid4().hex[:6]}",
                            intent=SFXIntent(
                                intent=SFXIntentType.UI_CLICK,
                                editorial_purpose="Comparison side switch subtle accent",
                                energy=0.4,
                                prominence=SFXProminence.SUBTLE,
                                start_ms=beat_start_ms,
                                duration_ms=min(300, beat_duration_ms),
                                visual_role=beat.visual_role.value,
                                collision_priority=50,
                            ),
                            start_ms=beat_start_ms,
                            duration_ms=min(300, beat_duration_ms),
                            energy=0.4,
                            prominence=SFXProminence.SUBTLE,
                            visual_beat_id=beat.id,
                            collision_priority=50,
                        )
                    )

        # 3. Camera / Transition Plan Level Intent
        if camera_transition_plan:
            for t_plan in camera_transition_plan.transition_plans:
                if t_plan.applied_intent == TransitionIntent.HARD_CONTEXT_SWITCH:
                    t_start_ms = 0
                    if visual_beats:
                        matching_beat = next(
                            (vb for vb in visual_beats if vb.id == t_plan.visual_beat_id),
                            None,
                        )
                        if matching_beat:
                            t_start_ms = matching_beat.start_offset_ms
                    else:
                        t_start_ms = (t_plan.parent_scene_index - 1) * 10000 + t_plan.beat_index * 2000

                    raw_cues.append(
                        SFXCue(
                            cue_id=f"cue-camera-transition-{uuid4().hex[:6]}",
                            intent=SFXIntent(
                                intent=SFXIntentType.TRANSITION,
                                editorial_purpose="Hard context switch transition whoosh/accent",
                                energy=0.6,
                                prominence=SFXProminence.BALANCED,
                                start_ms=t_start_ms,
                                duration_ms=min(500, t_plan.duration_ms or 400),
                                transition_intent=t_plan.applied_intent.value,
                                collision_priority=60,
                            ),
                            start_ms=t_start_ms,
                            duration_ms=min(500, t_plan.duration_ms or 400),
                            energy=0.6,
                            prominence=SFXProminence.BALANCED,
                            camera_transition_lineage={
                                "transition_intent": t_plan.applied_intent.value,
                                "parent_scene_index": str(t_plan.parent_scene_index),
                            },
                            collision_priority=60,
                        )
                    )

            for c_plan in camera_transition_plan.camera_plans:
                if c_plan.intent == CameraIntent.PUSH_IN and c_plan.strength == "EMPHATIC":
                    c_start_ms = 0
                    if visual_beats:
                        matching_beat = next(
                            (vb for vb in visual_beats if vb.id == c_plan.visual_beat_id),
                            None,
                        )
                        if matching_beat:
                            c_start_ms = matching_beat.start_offset_ms
                    else:
                        c_start_ms = (c_plan.parent_scene_index - 1) * 10000 + c_plan.beat_index * 2000

                    raw_cues.append(
                        SFXCue(
                            cue_id=f"cue-camera-pushin-{uuid4().hex[:6]}",
                            intent=SFXIntent(
                                intent=SFXIntentType.ACCENT,
                                editorial_purpose="Emphatic camera push-in subtle accent",
                                energy=0.5,
                                prominence=SFXProminence.SUBTLE,
                                start_ms=c_start_ms,
                                duration_ms=min(400, c_plan.duration_ms),
                                camera_intent=c_plan.intent.value,
                                collision_priority=45,
                            ),
                            start_ms=c_start_ms,
                            duration_ms=min(400, c_plan.duration_ms),
                            energy=0.5,
                            prominence=SFXProminence.SUBTLE,
                            camera_transition_lineage={
                                "camera_intent": c_plan.intent.value,
                                "strength": c_plan.strength.value,
                            },
                            collision_priority=45,
                        )
                    )

        # 4. Music Coexistence Adjustments
        findings: list[SFXFindingCode] = []
        if music_arc:
            for cue in raw_cues:
                matching_music = next(
                    (mc for mc in music_arc.cues if mc.start_ms <= cue.start_ms < mc.end_ms),
                    None,
                )
                if matching_music:
                    # If intentional silence from MusicDirector, keep SFX restrained or suppress high energy impacts
                    if matching_music.intent == MusicIntentType.NONE:
                        if cue.energy > 0.65:
                            cue = cue.model_copy(
                                update={
                                    "suppressed": True,
                                    "suppression_reason": "MUSIC_DIRECTOR_INTENTIONAL_SILENCE",
                                }
                            )
                    # If music peak (energy >= 0.85), avoid clashing impacts
                    elif matching_music.target_energy >= 0.85:
                        if cue.intent.intent in (SFXIntentType.IMPACT, SFXIntentType.STINGER):
                            findings.append(SFXFindingCode.MUSIC_PEAK_COLLISION)
                            # De-escalate prominence to subtle or suppress optional cue
                            if cue.collision_priority < 80:
                                cue = cue.model_copy(
                                    update={
                                        "suppressed": True,
                                        "suppression_reason": "SUPPRESSED_FOR_MUSIC_PEAK",
                                    }
                                )

        # Sort all cues by start_ms, then -collision_priority
        raw_cues.sort(key=lambda c: (c.start_ms, -c.collision_priority))

        # 5. Collision Planning & Gap Enforcement
        adjusted_cues: list[SFXCue] = []
        last_accepted_end_ms = -1_000_000
        for cue in raw_cues:
            if cue.suppressed:
                adjusted_cues.append(cue)
                continue

            # Check collision window (< min_cue_gap_ms)
            if cue.start_ms - last_accepted_end_ms < min_cue_gap_ms:
                findings.append(SFXFindingCode.SEMANTIC_COLLISION)
                # Suppress lower priority overlapping cue
                cue = cue.model_copy(
                    update={
                        "suppressed": True,
                        "suppression_reason": f"COLLISION_WITHIN_{min_cue_gap_ms}MS",
                    }
                )
                adjusted_cues.append(cue)
            else:
                adjusted_cues.append(cue)
                last_accepted_end_ms = cue.start_ms + cue.duration_ms

        # 6. Density Policy Enforcement
        active_cues = [c for c in adjusted_cues if not c.suppressed]
        video_duration_minutes = max(0.1, total_timeline_ms / 60_000)
        density = len(active_cues) / video_duration_minutes

        if density > max_cues_per_minute:
            findings.append(SFXFindingCode.SFX_OVERUSE)
            # Suppress lowest priority cues until within allowed density
            max_allowed_cues = max(1, math.ceil(max_cues_per_minute * video_duration_minutes))
            cues_by_priority = sorted(
                enumerate(adjusted_cues),
                key=lambda item: (item[1].suppressed, item[1].collision_priority),
            )
            to_suppress_count = len(active_cues) - max_allowed_cues
            suppressed_indices = set()
            for idx, c in cues_by_priority:
                if not c.suppressed and len(suppressed_indices) < to_suppress_count:
                    suppressed_indices.add(idx)

            final_cues: list[SFXCue] = []
            for idx, c in enumerate(adjusted_cues):
                if idx in suppressed_indices:
                    final_cues.append(
                        c.model_copy(
                            update={
                                "suppressed": True,
                                "suppression_reason": "DENSITY_POLICY_LIMIT_EXCEEDED",
                            }
                        )
                    )
                else:
                    final_cues.append(c)
            adjusted_cues = final_cues

        # Quality findings: detect clustering or repetitive accents
        active_cues = [c for c in adjusted_cues if not c.suppressed]
        density = len(active_cues) / video_duration_minutes

        # Check repetitive consecutive accents
        consecutive_same = 0
        prev_intent = None
        for c in active_cues:
            if c.intent.intent == prev_intent and c.intent.intent != SFXIntentType.NONE:
                consecutive_same += 1
                if consecutive_same >= 3:
                    findings.append(SFXFindingCode.REPETITIVE_ACCENTS)
            else:
                consecutive_same = 1
                prev_intent = c.intent.intent

        # 7. Selection Engine
        selections: list[SFXSelectionResult] = []
        recent_assets: list[str] = []
        for cue in adjusted_cues:
            res = SFXSelectionEngine.select(
                cue=cue,
                available_assets=available_assets,
                channel_dna=channel_dna,
                recent_selected_asset_ids=recent_assets,
            )
            selections.append(res)
            if res.selected_asset:
                recent_assets.append(res.selected_asset.asset_id)

        # Distinct deduplicated findings
        unique_findings = tuple(dict.fromkeys(findings))

        provenance = {
            "sfx_director_version": SFX_DIRECTOR_VERSION,
            "total_cues_considered": len(raw_cues),
            "total_cues_selected": len([s for s in selections if s.selected_asset is not None]),
            "density_events_per_minute": round(density, 2),
            "pacing_profile": pacing.value,
            "channel_profile": profile,
            "timeline_duration_ms": total_timeline_ms,
        }

        return SFXPlan(
            version=SFX_DIRECTOR_VERSION,
            cues=tuple(adjusted_cues),
            selections=tuple(selections),
            findings=unique_findings,
            density_events_per_minute=round(density, 2),
            provenance=provenance,
        )


def sfx_selections_to_audio_stems(
    selections: Sequence[SFXSelectionResult],
) -> tuple[AudioStem, ...]:
    """Translate resolved SFX selections into renderer-neutral AudioStem(role=SFX) for P23-A.

    P23-A remains the sole owner of physical mixing, gain adjustments,
    collision attenuation (6 dB under narration), ducking, loudnorm, limiting, and muxing.
    """
    stems: list[AudioStem] = []
    for sel in selections:
        if sel.selected_asset is None:
            continue
        cue = sel.cue
        asset = sel.selected_asset
        duration_ms = cue.duration_ms if cue.duration_ms > 0 else asset.duration_ms
        end_ms = cue.start_ms + duration_ms

        lineage = {
            "cue_id": cue.cue_id,
            "intent": cue.intent.intent.value,
            "editorial_purpose": cue.intent.editorial_purpose,
            "prominence": cue.prominence.value,
            "sfx_director_version": SFX_DIRECTOR_VERSION,
        }
        if cue.continuity_motif_id:
            lineage["continuity_motif_id"] = cue.continuity_motif_id
        if cue.narrative_section_id:
            lineage["narrative_section_id"] = str(cue.narrative_section_id)
        if cue.visual_beat_id:
            lineage["visual_beat_id"] = str(cue.visual_beat_id)
        if cue.camera_transition_lineage:
            lineage.update(cue.camera_transition_lineage)

        stems.append(
            AudioStem(
                stem_id=f"sfx-{cue.cue_id}",
                role=AudioStemRole.SFX,
                source_artifact=asset.source_artifact,
                source_identity=asset.asset_id,
                source_sha256=asset.source_sha256,
                start_ms=cue.start_ms,
                end_ms=end_ms,
                trim_start_ms=0,
                gain_db=cue.gain_db,
                fade_in_ms=cue.fade_in_ms,
                fade_out_ms=cue.fade_out_ms,
                priority=cue.collision_priority,
                optional=True,
                lineage=lineage,
            )
        )

    return tuple(stems)
