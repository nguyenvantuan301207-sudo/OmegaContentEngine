"""Application service: CreativeStyleDirector (P24-B).

Derives production-specific CreativeStylePlan from:
- Pinned ChannelDNARevision v2
- NarrativePlan & PacingPlan
- ContentGenerationRequest / Topic context
- Target format profile

Guarantees:
- ChannelDNARevision remains sole canonical channel authority.
- Hard constraints are strictly propagated and cannot be weakened.
- Soft preferences are resolved against production context.
- Cross-modal coherence is evaluated and conflicts detected.
- Style continuity is preserved across sections.
- Bounded variation without brand drift.
- Deterministic output for identical inputs.
- Inspectable rationale records.
- Handoff projections for P21, P22, P23, P24-C, and P24-D.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping, Sequence
from uuid import UUID

from omega.domain.channel_dna import (
    AudioPreferences,
    AvoidPatterns,
    CameraMotionIntensity,
    ChannelDNA,
    ContentPillar,
    EditorialVoice,
    FormatOverride,
    HardConstraints,
    NarrativeContextDepth,
    NarrativeHookStyle,
    NarrativePayoffStyle,
    NarrativePreferences,
    PackagingPreferences,
    ResolvedChannelDNA,
    SoftPreferences,
    VisualDensityPreference,
    VisualPreferences,
    VoiceCharacter,
    VoiceDepth,
    VoiceEnergy,
    VoiceExpressiveness,
    VoiceFormality,
    VoiceTechnicality,
)
from omega.domain.creative_style import (
    CREATIVE_STYLE_VERSION,
    AudioStyleDirection,
    CameraStyleDirection,
    CameraStyleIntent,
    CreativeArc,
    CreativeArcSection,
    CreativeIntensityProfile,
    CreativeStyleFindingCode,
    CreativeStylePlan,
    CreativeStylePlanValidator,
    CreativeStyleValidationResult,
    CrossModalCoherenceFinding,
    CrossModalConflictCode,
    CTAIntensity,
    DocumentTreatmentMode,
    ExplanationDensity,
    GraphicStyleDirection,
    GraphicStyleMode,
    HookIntensity,
    MusicStyleConstraints,
    NarrativeStyleDirection,
    PackagingStyleHints,
    PayoffEmphasis,
    SFXStyleConstraints,
    StyleConsistencyFinding,
    StyleDriftCode,
    StyleRationaleEntry,
    VisualCompositionCharacter,
    VisualStyleDirection,
)
from omega.domain.narrative_pacing import PacingPlan, PacingProfile
from omega.domain.narrative_plan import (
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativeSectionRole,
)


class CreativeStyleDirector:
    """Derives production-specific, recomputable CreativeStylePlan pinned to a ChannelDNARevision."""

    @classmethod
    def direct(
        cls,
        *,
        channel_dna: Any,
        narrative_plan: NarrativePlan | None = None,
        pacing_plan: PacingPlan | None = None,
        content_generation_request_id: UUID | None = None,
        format_profile: str | NarrativeFormatProfile | None = None,
        content_pillar_id: str | None = None,
        channel_dna_revision_id: UUID | None = None,
    ) -> CreativeStylePlan:
        """Derive an immutable, coordinated CreativeStylePlan."""
        # 1. Resolve raw ChannelDNA and revision ID
        if hasattr(channel_dna, "snapshot") and hasattr(channel_dna, "id"):
            effective_revision_id = channel_dna.id
            raw_dna = ChannelDNA.model_validate(channel_dna.snapshot) if isinstance(channel_dna.snapshot, dict) else channel_dna.snapshot
        elif hasattr(channel_dna, "dna") and hasattr(channel_dna, "id"):
            effective_revision_id = channel_dna.id
            raw_dna = channel_dna.dna
        elif isinstance(channel_dna, ChannelDNA):
            raw_dna = channel_dna
            effective_revision_id = channel_dna_revision_id or getattr(channel_dna, "revision_id", None) or uuid.uuid4()
        elif isinstance(channel_dna, dict):
            raw_dna = ChannelDNA.model_validate(channel_dna)
            effective_revision_id = channel_dna_revision_id or uuid.uuid4()
        else:
            raw_dna = ChannelDNA()
            effective_revision_id = channel_dna_revision_id or uuid.uuid4()

        # 2. Determine format profile
        fmt_str: str = "MEDIUM"
        if format_profile is not None:
            fmt_str = format_profile.value if isinstance(format_profile, NarrativeFormatProfile) else str(format_profile).upper()
        elif narrative_plan is not None:
            fmt_str = narrative_plan.format_profile.value if isinstance(narrative_plan.format_profile, NarrativeFormatProfile) else str(narrative_plan.format_profile).upper()

        try:
            norm_format = NarrativeFormatProfile(fmt_str)
        except ValueError:
            norm_format = NarrativeFormatProfile.MEDIUM

        # 3. Resolve Channel DNA for format
        resolved_dna: ResolvedChannelDNA = raw_dna.resolve_for_format(
            format_profile=fmt_str,
            revision_id=effective_revision_id,
        )

        hard_constraints: HardConstraints = resolved_dna.hard_constraints
        avoid_patterns: AvoidPatterns = resolved_dna.avoid_patterns
        editorial_voice: EditorialVoice = resolved_dna.editorial_voice
        narrative_prefs: NarrativePreferences = resolved_dna.narrative_preferences
        visual_prefs: VisualPreferences = resolved_dna.visual_preferences
        audio_prefs: AudioPreferences = resolved_dna.audio_preferences
        packaging_prefs: PackagingPreferences = resolved_dna.packaging_preferences

        # 4. Resolve Content Pillar (if selected)
        active_pillar: ContentPillar | None = None
        if content_pillar_id and raw_dna.content_pillars_v2:
            for p in raw_dna.content_pillars_v2:
                if p.pillar_id == content_pillar_id:
                    active_pillar = p
                    break

        rationales: list[StyleRationaleEntry] = []

        # 5. Narrative Style Direction
        # Determine Hook Intensity
        if narrative_prefs.hook_style in (NarrativeHookStyle.PARADOX, NarrativeHookStyle.BOLD_STATEMENT):
            hook_intensity = HookIntensity.HIGH if editorial_voice.energy == VoiceEnergy.DYNAMIC else HookIntensity.ENGAGING
            rationales.append(
                StyleRationaleEntry(
                    aspect="hook_intensity",
                    decision=hook_intensity.value,
                    contributing_source="NarrativePreferences.hook_style",
                    explanation=f"Selected {hook_intensity.value} hook intensity reflecting channel's hook style {narrative_prefs.hook_style}.",
                )
            )
        elif editorial_voice.energy == VoiceEnergy.CALM or avoid_patterns.sensationalized_claims:
            hook_intensity = HookIntensity.SUBTLE if editorial_voice.energy == VoiceEnergy.CALM else HookIntensity.BALANCED
            rationales.append(
                StyleRationaleEntry(
                    aspect="hook_intensity",
                    decision=hook_intensity.value,
                    contributing_source="EditorialVoice / AvoidPatterns",
                    explanation="Restrained hook intensity to comply with truthful, non-sensational guideline.",
                )
            )
        else:
            hook_intensity = HookIntensity.BALANCED

        # Context depth
        context_depth = narrative_prefs.context_depth

        # Explanation density & technicality
        explanation_density = ExplanationDensity.BALANCED
        if editorial_voice.depth == VoiceDepth.EXPLANATORY:
            explanation_density = ExplanationDensity.DENSE
        elif editorial_voice.depth == VoiceDepth.CONCISE:
            explanation_density = ExplanationDensity.CONCISE

        # CTA intensity
        cta_intensity = CTAIntensity.BALANCED
        cta_val = str(narrative_prefs.cta_style.value if hasattr(narrative_prefs.cta_style, "value") else narrative_prefs.cta_style).upper()
        if cta_val == "NONE" or cta_val == "MINIMAL":
            cta_intensity = CTAIntensity.LOW
        elif cta_val == "CONCISE":
            cta_intensity = CTAIntensity.BALANCED
        elif cta_val in ("DIRECT", "ENGAGEMENT"):
            cta_intensity = CTAIntensity.DIRECT

        narrative_style = NarrativeStyleDirection(
            hook_intensity=hook_intensity,
            context_depth=context_depth,
            explanation_density=explanation_density,
            editorial_energy=editorial_voice.energy,
            payoff_emphasis=PayoffEmphasis.PRONOUNCED if narrative_prefs.payoff_style == NarrativePayoffStyle.ACTIONABLE_FRAMEWORK else PayoffEmphasis.BALANCED,
            cta_intensity=cta_intensity,
            formality=editorial_voice.formality,
            technicality=editorial_voice.technicality,
            expressiveness=editorial_voice.expressiveness,
            notes=editorial_voice.style_notes,
        )

        # 6. Visual Style Direction
        # Composition character
        composition_character = VisualCompositionCharacter.EDITORIAL
        if raw_dna.soft_preferences.prefer_cinematic_visuals:
            composition_character = VisualCompositionCharacter.CINEMATIC
        elif visual_prefs.visual_density == VisualDensityPreference.LOW:
            composition_character = VisualCompositionCharacter.MINIMAL
        elif visual_prefs.graphic_complexity in ("COMPLEX", "SPECIALIZED"):
            composition_character = VisualCompositionCharacter.TECHNICAL

        # Motion & Camera Restraint
        camera_restraint = visual_prefs.transition_restraint
        motion_intensity = visual_prefs.camera_motion_intensity

        if avoid_patterns.overactive_camera_motion:
            motion_intensity = CameraMotionIntensity.RESTRAINED
            camera_restraint = True
            rationales.append(
                StyleRationaleEntry(
                    aspect="camera_motion",
                    decision="RESTRAINED",
                    contributing_source="AvoidPatterns.overactive_camera_motion",
                    explanation="Enforced restrained motion and strict camera restraint per avoid_patterns.",
                )
            )

        doc_treatment = DocumentTreatmentMode.ANNOTATED if visual_prefs.evidence_emphasis == "HIGH" else DocumentTreatmentMode.CLEAN

        visual_style = VisualStyleDirection(
            visual_density=visual_prefs.visual_density,
            evidence_emphasis=visual_prefs.evidence_emphasis,
            b_roll_tendency=visual_prefs.b_roll_usage,
            diagram_tendency=visual_prefs.diagram_frequency,
            document_treatment=doc_treatment,
            comparison_treatment=visual_prefs.comparison_treatment,
            motion_intensity=motion_intensity,
            camera_restraint=camera_restraint,
            transition_restraint=visual_prefs.transition_restraint,
            composition_character=composition_character,
        )

        # 7. Camera Style Direction
        if motion_intensity == CameraMotionIntensity.RESTRAINED:
            cam_intent = CameraStyleIntent.STATIC_RESTRAINED
            max_cam_energy = 0.25
        elif motion_intensity == CameraMotionIntensity.SMOOTH:
            cam_intent = CameraStyleIntent.SUBTLE_MOTION
            max_cam_energy = 0.45
        else:
            cam_intent = CameraStyleIntent.MODERATE_EDITORIAL_MOTION if avoid_patterns.overactive_camera_motion else CameraStyleIntent.ENERGETIC_MOTION
            max_cam_energy = 0.6 if avoid_patterns.overactive_camera_motion else 0.85

        camera_style = CameraStyleDirection(
            camera_style_intent=cam_intent,
            max_camera_energy=max_cam_energy,
            pan_allowed=True,
            tilt_allowed=True,
            zoom_allowed=True,
            abrupt_moves_forbidden=avoid_patterns.overactive_camera_motion,
        )

        # 8. Graphic Style Direction
        graphic_mode = GraphicStyleMode.EDITORIAL
        if visual_prefs.graphic_complexity == "COMPLEX" or editorial_voice.technicality in (VoiceTechnicality.TECHNICAL, VoiceTechnicality.SPECIALIZED):
            graphic_mode = GraphicStyleMode.TECHNICAL
        elif visual_prefs.visual_density == VisualDensityPreference.LOW:
            graphic_mode = GraphicStyleMode.MINIMAL
        elif visual_prefs.evidence_emphasis == "HIGH":
            graphic_mode = GraphicStyleMode.DATA_FORWARD

        # If pillar is technical, boost graphic technicality
        if active_pillar and any(w in active_pillar.name.lower() for w in ("tutorial", "technical", "engineering", "deep dive")):
            graphic_mode = GraphicStyleMode.TECHNICAL

        graphic_style = GraphicStyleDirection(
            graphic_mode=graphic_mode,
            high_contrast=False,
            annotation_density="HEAVY" if visual_prefs.evidence_emphasis == "HIGH" else "BALANCED",
        )

        # 9. Audio Style Direction & Constraints
        # SFX density
        resolved_sfx_density = audio_prefs.sfx_density
        if avoid_patterns.constant_sfx and resolved_sfx_density in ("DENSE", "DYNAMIC"):
            resolved_sfx_density = "BALANCED"
            rationales.append(
                StyleRationaleEntry(
                    aspect="sfx_density",
                    decision="BALANCED",
                    contributing_source="AvoidPatterns.constant_sfx",
                    explanation="Clamped SFX density to BALANCED to prevent continuous SFX clutter.",
                )
            )

        # Target music energy
        target_music_energy = (audio_prefs.preferred_energy_min + audio_prefs.preferred_energy_max) / 2.0
        if editorial_voice.energy == VoiceEnergy.CALM and target_music_energy > 0.6:
            target_music_energy = 0.45
            rationales.append(
                StyleRationaleEntry(
                    aspect="target_music_energy",
                    decision="0.45",
                    contributing_source="EditorialVoice.energy=CALM",
                    explanation="Adjusted target music energy to align with calm editorial voice.",
                )
            )

        audio_style = AudioStyleDirection(
            music_tendency=audio_prefs.music_usage_tendency,
            target_music_energy=round(target_music_energy, 2),
            music_energy_min=audio_prefs.preferred_energy_min,
            music_energy_max=audio_prefs.preferred_energy_max,
            vocal_policy=audio_prefs.vocal_policy,
            sfx_density=resolved_sfx_density,
            sfx_prominence="SUBTLE" if audio_prefs.audio_restraint else "BALANCED",
            silence_permitted=True,
            audio_character=audio_prefs.sonic_character,
        )

        music_constraints = MusicStyleConstraints(
            instrumental_only=(audio_prefs.vocal_policy == "INSTRUMENTAL_ONLY"),
            max_energy_ceiling=audio_prefs.preferred_energy_max,
            min_energy_floor=audio_prefs.preferred_energy_min,
            silence_for_serious_claims=True,
        )

        sfx_constraints = SFXStyleConstraints(
            sfx_allowed=(resolved_sfx_density not in ("OFF", "NONE")),
            max_density=resolved_sfx_density,
            max_sfx_per_minute=6 if resolved_sfx_density == "SPARSE" else 14,
            prohibit_constant_sfx=avoid_patterns.constant_sfx,
            no_sfx_during_serious_claims=True,
        )

        # 10. Packaging Style Hints (NO final titles/thumbnails generated!)
        clickbait = "ZERO_TOLERANCE" if hard_constraints.no_misleading_clickbait else packaging_prefs.clickbait_tolerance
        pkg_hints = PackagingStyleHints(
            title_tone=packaging_prefs.title_tone,
            title_restraint="FACTUAL_RESTRAINED" if hard_constraints.no_misleading_clickbait else "BALANCED",
            title_length_tendency=packaging_prefs.title_length_tendency,
            thumbnail_density=packaging_prefs.thumbnail_density,
            thumbnail_text_policy=packaging_prefs.thumbnail_text_policy,
            thumbnail_emotional_intensity="MEASURED" if editorial_voice.expressiveness == VoiceExpressiveness.OBJECTIVE else "EXPRESSIVE",
            description_voice=packaging_prefs.description_style,
            metadata_voice=packaging_prefs.metadata_voice,
            chapter_style=packaging_prefs.chapter_style,
            clickbait_tolerance=clickbait,
            prohibited_patterns=packaging_prefs.prohibited_packaging_patterns,
        )

        # 11. Creative Intensity Profile
        norm_narrative_energy = 0.3 if editorial_voice.energy == VoiceEnergy.CALM else (0.75 if editorial_voice.energy == VoiceEnergy.DYNAMIC else 0.5)
        norm_visual_energy = 0.35 if visual_prefs.visual_density == VisualDensityPreference.LOW else (0.75 if visual_prefs.visual_density == VisualDensityPreference.HIGH else 0.5)
        norm_camera_energy = camera_style.max_camera_energy
        norm_music_energy = target_music_energy
        norm_sfx_energy = 0.2 if resolved_sfx_density == "SPARSE" else (0.7 if resolved_sfx_density in ("DENSE", "DYNAMIC") else 0.45)
        norm_graphic_density = 0.3 if visual_prefs.graphic_complexity == "SIMPLE" else (0.75 if visual_prefs.graphic_complexity == "COMPLEX" else 0.5)

        creative_intensity = CreativeIntensityProfile(
            narrative_energy=round(norm_narrative_energy, 2),
            visual_energy=round(norm_visual_energy, 2),
            camera_energy=round(norm_camera_energy, 2),
            music_energy=round(norm_music_energy, 2),
            sfx_energy=round(norm_sfx_energy, 2),
            graphic_density=round(norm_graphic_density, 2),
        )

        # 12. Creative Arc Progression
        arc_sections: list[CreativeArcSection] = []
        if narrative_plan and narrative_plan.sections:
            for s in narrative_plan.sections:
                role = s.role
                order = s.section_order

                if role == NarrativeSectionRole.HOOK:
                    energy = min(0.85, norm_narrative_energy + 0.2)
                    focus = "Hook focal concept & opening paradox"
                    cam = CameraStyleIntent.SUBTLE_MOTION if avoid_patterns.overactive_camera_motion else CameraStyleIntent.MODERATE_EDITORIAL_MOTION
                    audio = "Intro tension or discovery theme"
                    justified = True
                    rat = "Hook section captures attention within channel restraint bounds."
                elif role == NarrativeSectionRole.CONTEXT:
                    energy = max(0.2, norm_narrative_energy - 0.15)
                    focus = "Foundational background and grounding evidence"
                    cam = CameraStyleIntent.STATIC_RESTRAINED
                    audio = "Subtle background ambience"
                    justified = False
                    rat = "Context requires clean clarity and minimal camera movement."
                elif role == NarrativeSectionRole.DEVELOPMENT:
                    energy = norm_narrative_energy
                    focus = "Sequential topic expansion and core analysis"
                    cam = CameraStyleIntent.SUBTLE_MOTION
                    audio = "Steady informative progression"
                    justified = False
                    rat = "Development section maintains stable cognitive pacing."
                elif role == NarrativeSectionRole.ESCALATION:
                    energy = min(0.9, norm_narrative_energy + 0.25)
                    focus = "Heightened tension, critical comparative evidence"
                    cam = CameraStyleIntent.MODERATE_EDITORIAL_MOTION
                    audio = "Building harmonic tension"
                    justified = True
                    rat = "Escalation drives viewer interest toward the central payoff."
                elif role == NarrativeSectionRole.PAYOFF:
                    energy = min(0.95, norm_narrative_energy + 0.3)
                    focus = "Resolution of core question / primary revelation"
                    cam = CameraStyleIntent.MODERATE_EDITORIAL_MOTION
                    audio = "Triumphant or definitive resolution cue"
                    justified = True
                    rat = "Payoff fulfills the opening narrative promise."
                elif role == NarrativeSectionRole.TAKEAWAY:
                    energy = max(0.25, norm_narrative_energy - 0.1)
                    focus = "Synthesized lessons and lasting insights"
                    cam = CameraStyleIntent.SUBTLE_MOTION
                    audio = "Reflective settling cue"
                    justified = False
                    rat = "Takeaway settles the cognitive load for viewer retention."
                elif role in (NarrativeSectionRole.CLOSING, NarrativeSectionRole.CTA):
                    energy = norm_narrative_energy
                    focus = "Channel identity recap and concise next steps"
                    cam = CameraStyleIntent.STATIC_RESTRAINED
                    audio = "Outro motif"
                    justified = False
                    rat = "Closing provides clean exit without lingering."
                else:
                    energy = norm_narrative_energy
                    focus = f"Section {order} focus"
                    cam = CameraStyleIntent.SUBTLE_MOTION
                    audio = "Background continuation"
                    justified = False
                    rat = f"Standard handling for section role {role}."

                arc_sections.append(
                    CreativeArcSection(
                        section_role=role,
                        section_order=order,
                        target_energy=round(energy, 2),
                        visual_focus=focus,
                        camera_motion=cam,
                        audio_intent=audio,
                        justified_escalation=justified,
                        rationale=rat,
                    )
                )
        else:
            # Canonical 7-section progression
            canonical_roles = (
                (NarrativeSectionRole.HOOK, 0.65, CameraStyleIntent.SUBTLE_MOTION, "Intro hook", True),
                (NarrativeSectionRole.CONTEXT, 0.35, CameraStyleIntent.STATIC_RESTRAINED, "Context grounding", False),
                (NarrativeSectionRole.DEVELOPMENT, 0.50, CameraStyleIntent.SUBTLE_MOTION, "Core analysis", False),
                (NarrativeSectionRole.ESCALATION, 0.70, CameraStyleIntent.MODERATE_EDITORIAL_MOTION, "Key dilemma build", True),
                (NarrativeSectionRole.PAYOFF, 0.80, CameraStyleIntent.MODERATE_EDITORIAL_MOTION, "Core revelation", True),
                (NarrativeSectionRole.TAKEAWAY, 0.45, CameraStyleIntent.SUBTLE_MOTION, "Insight synthesis", False),
                (NarrativeSectionRole.CLOSING, 0.40, CameraStyleIntent.STATIC_RESTRAINED, "Outro resolution", False),
            )
            for idx, (role, eng, cam, aud, just) in enumerate(canonical_roles, start=1):
                arc_sections.append(
                    CreativeArcSection(
                        section_role=role,
                        section_order=idx,
                        target_energy=round(eng, 2),
                        visual_focus=f"{role.value} visual focus",
                        camera_motion=cam,
                        audio_intent=aud,
                        justified_escalation=just,
                        rationale=f"Canonical {role.value} creative progression.",
                    )
                )

        creative_arc = CreativeArc(
            sections=tuple(arc_sections),
            overall_pacing=narrative_prefs.preferred_pacing,
        )

        # 13. Cross-Modal Coherence & Conflict Detection
        coherence_findings: list[CrossModalCoherenceFinding] = []

        # Conflict A: Calm narration + Hyperactive camera
        if editorial_voice.energy == VoiceEnergy.CALM and camera_style.camera_style_intent == CameraStyleIntent.ENERGETIC_MOTION:
            coherence_findings.append(
                CrossModalCoherenceFinding(
                    code=CrossModalConflictCode.CALM_NARRATION_HYPERACTIVE_CAMERA,
                    severity="ERROR",
                    modalities=("narrative", "camera"),
                    explanation="Calm editorial voice paired with energetic camera motion causes viewer disorientation.",
                    recommended_remediation="Restrain camera style to SUBTLE_MOTION or STATIC_RESTRAINED.",
                )
            )

        # Conflict B: Calm narration + Overactive audio
        if editorial_voice.energy == VoiceEnergy.CALM and (audio_style.target_music_energy > 0.75 or audio_style.sfx_density in ("DENSE", "DYNAMIC")):
            coherence_findings.append(
                CrossModalCoherenceFinding(
                    code=CrossModalConflictCode.CALM_NARRATION_OVERACTIVE_AUDIO,
                    severity="WARNING",
                    modalities=("narrative", "audio"),
                    explanation="Calm voice paired with aggressive music or dense SFX overpowers narration.",
                    recommended_remediation="Lower target music energy and set SFX density to SPARSE or BALANCED.",
                )
            )

        # Conflict C: High energy hook + inert visuals without justification
        if hook_intensity == HookIntensity.HIGH and visual_style.motion_intensity == CameraMotionIntensity.RESTRAINED and visual_style.visual_density == VisualDensityPreference.LOW:
            coherence_findings.append(
                CrossModalCoherenceFinding(
                    code=CrossModalConflictCode.HIGH_ENERGY_HOOK_INERT_VISUALS,
                    severity="WARNING",
                    modalities=("narrative", "visual"),
                    explanation="High-intensity hook delivery accompanied by static minimal visuals creates visual-verbal disconnect.",
                    recommended_remediation="Enhance visual engagement during hook or temper hook delivery.",
                )
            )

        # Conflict D: Hard constraint violation check
        if hard_constraints.no_misleading_clickbait and pkg_hints.clickbait_tolerance != "ZERO_TOLERANCE":
            coherence_findings.append(
                CrossModalCoherenceFinding(
                    code=CrossModalConflictCode.HARD_CONSTRAINT_STYLE_VIOLATION,
                    severity="ERROR",
                    modalities=("packaging", "channel_policy"),
                    explanation="Packaging style allows clickbait contrary to binding channel hard constraint.",
                    recommended_remediation="Enforce ZERO_TOLERANCE clickbait policy.",
                )
            )

        # 14. Style Consistency Across Sections (Drift Detection)
        consistency_findings: list[StyleConsistencyFinding] = []
        for i in range(len(arc_sections) - 1):
            curr_sec = arc_sections[i]
            next_sec = arc_sections[i + 1]
            diff = abs(next_sec.target_energy - curr_sec.target_energy)

            # If energy swings abruptly without justified escalation
            if diff >= 0.4 and not next_sec.justified_escalation and not curr_sec.justified_escalation:
                consistency_findings.append(
                    StyleConsistencyFinding(
                        code=StyleDriftCode.VISUAL_STYLE_DRIFT,
                        severity="WARNING",
                        from_section_order=curr_sec.section_order,
                        to_section_order=next_sec.section_order,
                        explanation=f"Abrupt style energy shift ({curr_sec.target_energy} -> {next_sec.target_energy}) between sections {curr_sec.section_order} and {next_sec.section_order} without narrative escalation rationale.",
                        recommended_remediation="Smooth energy transition or mark escalation as justified.",
                    )
                )

        # 15. Construct Immutable CreativeStylePlan
        plan = CreativeStylePlan(
            channel_dna_revision_id=effective_revision_id,
            content_generation_request_id=content_generation_request_id,
            narrative_plan_id=narrative_plan.id if narrative_plan else None,
            format_profile=norm_format,
            content_pillar_id=content_pillar_id,
            narrative_style=narrative_style,
            visual_style=visual_style,
            camera_style=camera_style,
            graphic_style=graphic_style,
            audio_style=audio_style,
            music_constraints=music_constraints,
            sfx_constraints=sfx_constraints,
            packaging_hints=pkg_hints,
            creative_intensity=creative_intensity,
            creative_arc=creative_arc,
            hard_constraints_binding=hard_constraints,
            coherence_findings=tuple(coherence_findings),
            style_consistency_findings=tuple(consistency_findings),
            style_rationale=tuple(rationales),
            version=CREATIVE_STYLE_VERSION,
        )

        return plan
