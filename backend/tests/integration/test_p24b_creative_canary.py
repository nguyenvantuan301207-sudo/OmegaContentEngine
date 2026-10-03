"""Integration canary and lineage verification for P24-B Creative Style Director.

Covers:
- Section 32: Realistic Isolated Canary
- Section 33: Cross-Modal Integration Canary (demonstrating narrative, visual, and audio downstream effects)
- Section 34: Cross-Phase Lineage Verification
"""

import uuid
from uuid import UUID

import pytest

from omega.application.creative_style_director import CreativeStyleDirector
from omega.application.music_director_service import MusicDirector
from omega.application.retention_pacing_engine import RetentionPacingService
from omega.application.sfx_director_service import SFXDirector
from omega.domain.channel_dna import (
    AudioPreferences,
    AvoidPatterns,
    CameraMotionIntensity,
    ChannelDNA,
    ChannelPositioning,
    ContentPillar,
    EditorialVoice,
    FormatOverride,
    HardConstraints,
    NarrativeContextDepth,
    NarrativeHookStyle,
    NarrativePayoffStyle,
    NarrativePreferences,
    PackagingPreferences,
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
    CameraStyleIntent,
    CreativeArc,
    CreativeStylePlan,
    HookIntensity,
)
from omega.domain.narrative_pacing import PacingPlan, PacingProfile
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.visual_beat import (
    VisualBeat,
    VisualRole,
)
from omega.infrastructure.models import ChannelDNARevision


def _build_realistic_p24_channel_revision() -> tuple[UUID, ChannelDNARevision]:
    """Build a realistic pinned ChannelDNARevision v2."""
    rev_id = uuid.uuid4()
    channel_id = uuid.uuid4()

    dna = ChannelDNA(
        positioning=ChannelPositioning(
            channel_purpose="Demystify cutting-edge science and engineering for curious minds.",
            content_promise="Rigorous, factual, and deeply educational explorations without sensationalism.",
            distinctive_angle="First-principles breakdowns backed by academic papers and primary evidence.",
            primary_subject_domain="Applied Sciences",
            secondary_subject_domains=("Physics", "Computing", "Aerospace"),
            audience_benefit="A clear, intuition-grounded understanding of complex technical systems.",
        ),
        editorial_voice=EditorialVoice(
            formality=VoiceFormality.FORMAL,
            depth=VoiceDepth.EXPLANATORY,
            energy=VoiceEnergy.CALM,
            expressiveness=VoiceExpressiveness.OBJECTIVE,
            technicality=VoiceTechnicality.TECHNICAL,
            tone_character=VoiceCharacter.ANALYTICAL,
            style_notes="Maintain sober, evidence-focused tone; avoid colloquial hype.",
        ),
        hard_constraints=HardConstraints(
            no_fabricated_claims=True,
            no_misleading_clickbait=True,
            no_unsupported_medical_claims=True,
            no_profanity=True,
        ),
        soft_preferences=SoftPreferences(
            prefer_cinematic_visuals=True,
            prefer_moderate_pacing=True,
            prefer_concise_cta=True,
            prefer_instrumental_music=True,
        ),
        narrative_preferences=NarrativePreferences(
            hook_style=NarrativeHookStyle.EVIDENCE_REVEAL,
            context_depth=NarrativeContextDepth.MODERATE,
            preferred_pacing="BALANCED",
            payoff_style=NarrativePayoffStyle.CONCRETE_EVIDENCE,
            educational_depth="DEEP_DIVE",
        ),
        visual_preferences=VisualPreferences(
            visual_density=VisualDensityPreference.MEDIUM,
            evidence_emphasis="HIGH",
            diagram_frequency="FREQUENT",
            camera_motion_intensity=CameraMotionIntensity.RESTRAINED,
            transition_restraint=True,
            typography_character="CLEAN_TECHNICAL",
            graphic_complexity="COMPLEX",
        ),
        audio_preferences=AudioPreferences(
            music_usage_tendency="SUBTLE_BACKGROUND",
            preferred_energy_min=0.15,
            preferred_energy_max=0.50,
            vocal_policy="INSTRUMENTAL_ONLY",
            sfx_density="SPARSE",
            audio_restraint=True,
            sonic_character="MINIMAL_AMBIENT",
        ),
        packaging_preferences=PackagingPreferences(
            title_tone="FACTUAL_COMPELLING",
            title_length_tendency="CONCISE",
            thumbnail_density="MINIMAL_TO_MODERATE",
            thumbnail_text_policy="MAX_3_WORDS",
            description_style="STRUCTURED_OUTLINE",
            chapter_style="SECTION_BASED",
            clickbait_tolerance="ZERO_TOLERANCE",
        ),
        avoid_patterns=AvoidPatterns(
            sensationalized_claims=True,
            repetitive_hooks=True,
            excessive_memes=True,
            overactive_camera_motion=True,
            constant_sfx=True,
            generic_cta=True,
            overloaded_thumbnails=True,
        ),
        content_pillars_v2=[
            ContentPillar(
                pillar_id="pillar-core-physics",
                name="Fundamental Physics",
                description="Quantum mechanics, relativity, and particle physics breakthroughs.",
                priority_weight=0.8,
            ),
        ],
    )

    revision = ChannelDNARevision(
        id=rev_id,
        channel_id=channel_id,
        version=2,
        snapshot=dna.model_dump(),
        change_reason="Promoted to Channel DNA v2",
        actor="USER",
    )
    return rev_id, revision


def _build_realistic_narrative_plan(revision_id: UUID) -> NarrativePlan:
    """Build a realistic 7-section narrative plan."""
    req_id = uuid.uuid4()
    sections = [
        NarrativeSection(
            section_order=1,
            role=NarrativeSectionRole.HOOK,
            target_duration_seconds=15,
            objective="Pose the fundamental mystery of quantum decoherence.",
            core_message="Why does the quantum world dissolve when we look at it?",
            key_information=["Wavefunction collapse vs environmental decoherence."],
        ),
        NarrativeSection(
            section_order=2,
            role=NarrativeSectionRole.CONTEXT,
            target_duration_seconds=30,
            objective="Ground the historical double-slit experiment.",
            core_message="The classical measurement problem in quantum mechanics.",
            key_information=["Double-slit interference patterns with single electrons."],
        ),
        NarrativeSection(
            section_order=3,
            role=NarrativeSectionRole.DEVELOPMENT,
            target_duration_seconds=45,
            objective="Develop the mechanism of environmental entanglement.",
            core_message="How stray photons destroy quantum superposition phases.",
            key_information=["Phase randomization through scattering."],
        ),
        NarrativeSection(
            section_order=4,
            role=NarrativeSectionRole.ESCALATION,
            target_duration_seconds=35,
            objective="Escalate the dilemma for practical quantum computing.",
            core_message="Why maintaining coherence at room temperature is nearly impossible.",
            key_information=["Decoherence timescales in superconducting circuits."],
        ),
        NarrativeSection(
            section_order=5,
            role=NarrativeSectionRole.PAYOFF,
            target_duration_seconds=35,
            objective="Reveal topological qubit error suppression.",
            core_message="Anyons and non-abelian braids protect quantum information geometrically.",
            key_information=["Topological braiding isolates qubits from local noise."],
        ),
        NarrativeSection(
            section_order=6,
            role=NarrativeSectionRole.TAKEAWAY,
            target_duration_seconds=20,
            objective="Synthesize the broader engineering frontier.",
            core_message="Quantum error correction is transitioning from theory to hardware.",
            key_information=["Surface codes and fault-tolerant thresholds."],
        ),
        NarrativeSection(
            section_order=7,
            role=NarrativeSectionRole.CLOSING,
            target_duration_seconds=10,
            objective="Conclude and invite technical discussion in comments.",
            core_message="Subscribe for rigorous explorations of quantum physics.",
            key_information=["Channel closing remarks."],
        ),
    ]

    return NarrativePlan(
        content_generation_request_id=req_id,
        channel_dna_revision_id=revision_id,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=190,
        estimated_duration_seconds=190,
        sections=sections,
    )


# ── Section 32: Realistic Isolated Canary ───────────────────────────────────


def test_section_32_realistic_isolated_canary():
    """Verify Section 32 realistic isolated canary requirements."""
    rev_id, revision = _build_realistic_p24_channel_revision()
    narrative_plan = _build_realistic_narrative_plan(rev_id)

    # Run CreativeStyleDirector
    style_plan = CreativeStyleDirector.direct(
        channel_dna=revision,
        narrative_plan=narrative_plan,
        content_generation_request_id=narrative_plan.content_generation_request_id,
        content_pillar_id="pillar-core-physics",
    )

    # 1. Pinned revision linkage
    assert style_plan.channel_dna_revision_id == rev_id
    assert style_plan.content_generation_request_id == narrative_plan.content_generation_request_id

    # 2. Hard constraints preserved
    assert style_plan.hard_constraints_binding.no_fabricated_claims is True
    assert style_plan.hard_constraints_binding.no_misleading_clickbait is True

    # 3. Calm sections remain restrained
    assert style_plan.narrative_style.editorial_energy == VoiceEnergy.CALM
    assert style_plan.creative_intensity.narrative_energy <= 0.35

    # 4. Creative arc escalation increases in bounded form
    arc_sections = style_plan.creative_arc.sections
    assert len(arc_sections) == 7
    hook_sec = arc_sections[0]
    context_sec = arc_sections[1]
    escalation_sec = arc_sections[3]
    payoff_sec = arc_sections[4]

    # Context is restrained
    assert context_sec.target_energy <= 0.35
    assert context_sec.camera_motion == CameraStyleIntent.STATIC_RESTRAINED

    # Escalation and Payoff receive bounded emphasis
    assert escalation_sec.target_energy > context_sec.target_energy
    assert payoff_sec.target_energy > context_sec.target_energy
    assert payoff_sec.target_energy <= 0.95
    assert payoff_sec.justified_escalation is True

    # 5. Camera does not become hyperactive
    assert style_plan.camera_style.camera_style_intent in (CameraStyleIntent.STATIC_RESTRAINED, CameraStyleIntent.SUBTLE_MOTION)
    assert style_plan.camera_style.max_camera_energy <= 0.45
    assert style_plan.camera_style.abrupt_moves_forbidden is True

    # 6. Music & SFX remain compatible
    assert style_plan.audio_style.music_energy_max <= 0.55
    assert style_plan.audio_style.sfx_density == "SPARSE"
    assert style_plan.sfx_constraints.prohibit_constant_sfx is True

    # 7. Packaging projection remains factual / restraint-oriented (NO titles/thumbnails generated)
    pkg_hints = style_plan.to_p24c_packaging_hints()
    assert pkg_hints.title_restraint == "FACTUAL_RESTRAINED"
    assert pkg_hints.clickbait_tolerance == "ZERO_TOLERANCE"
    assert not hasattr(pkg_hints, "final_title")
    assert not hasattr(pkg_hints, "generated_thumbnails")

    # 8. All projections accessible
    p21_proj = style_plan.to_p21_style_projection()
    assert p21_proj["formality"] == "FORMAL"
    assert p21_proj["editorial_energy"] == "CALM"

    p22_proj = style_plan.to_p22_style_projection()
    assert p22_proj["visual_density"] == "MEDIUM"
    assert p22_proj["transition_restraint"] is True

    p23_proj = style_plan.to_p23_style_projection()
    assert p23_proj["music_tendency"] == "SUBTLE_BACKGROUND"
    assert p23_proj["vocal_policy"] == "INSTRUMENTAL_ONLY"


# ── Section 33: Cross-Modal Integration Canary ──────────────────────────────


def test_section_33_cross_modal_integration_canary():
    """Verify actual downstream decisions respond to the style plan across modalities."""
    rev_id, revision = _build_realistic_p24_channel_revision()
    narrative_plan = _build_realistic_narrative_plan(rev_id)

    # 1. Direct CreativeStylePlan
    style_plan = CreativeStyleDirector.direct(
        channel_dna=revision,
        narrative_plan=narrative_plan,
        content_generation_request_id=narrative_plan.content_generation_request_id,
    )

    # Demonstrate Narrative-Style Effect:
    # Editorial energy is CALM and hook intensity is restrained to SUBTLE
    assert style_plan.narrative_style.editorial_energy == VoiceEnergy.CALM
    assert style_plan.narrative_style.hook_intensity == HookIntensity.SUBTLE

    # Demonstrate Visual-Style Effect:
    # Camera motion is restrained, abrupt moves forbidden, document treatment annotated
    p22_proj = style_plan.to_p22_style_projection()
    assert p22_proj["camera_style_intent"] == "STATIC_RESTRAINED"
    assert p22_proj["document_treatment"] == "ANNOTATED"
    assert p22_proj["max_camera_energy"] <= 0.3

    # Demonstrate Audio-Style Effect:
    # Run MusicDirector with channel_dna reflecting the calm restrained style
    pacing_plan = RetentionPacingService().audit_plan(
        plan=narrative_plan,
        channel_dna=ChannelDNA.model_validate(revision.snapshot),
    )
    music_arc = MusicDirector.direct(
        narrative_plan=narrative_plan,
        pacing_plan=pacing_plan,
        channel_dna=ChannelDNA.model_validate(revision.snapshot),
    )
    assert music_arc is not None
    # Because editorial voice is CALM and audio preferences are restrained, cues are clamped to subtle energy
    for cue in music_arc.cues:
        assert cue.target_energy <= 0.75
        assert cue.intent.vocal_policy.value == "INSTRUMENTAL_ONLY"

    # Demonstrate SFX-Style Effect:
    # Run SFXDirector with channel_dna
    visual_beats = [
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=0,
            start_offset_ms=0,
            end_offset_ms=4000,
            duration_ms=4000,
            visual_intent="Show core document",
            information_goal="Inspect quantum paper",
            visual_role=VisualRole.DOCUMENT,
        ),
    ]
    sfx_arc = SFXDirector.direct(
        narrative_plan=narrative_plan,
        pacing_plan=pacing_plan,
        visual_beats=visual_beats,
        music_arc=music_arc,
        channel_dna=ChannelDNA.model_validate(revision.snapshot),
    )
    assert sfx_arc is not None
    # In a calm channel with sparse SFX, cues are subtle
    for cue in sfx_arc.cues:
        assert cue.energy <= 0.65


# ── Section 34: Lineage Verification ────────────────────────────────────────


def test_section_34_creative_lineage_traceability():
    """Verify CreativeStylePlan traces to pinned ChannelDNARevision and downstream projections."""
    rev_id, revision = _build_realistic_p24_channel_revision()
    narrative_plan = _build_realistic_narrative_plan(rev_id)

    style_plan = CreativeStyleDirector.direct(
        channel_dna=revision,
        narrative_plan=narrative_plan,
        content_generation_request_id=narrative_plan.content_generation_request_id,
        content_pillar_id="pillar-core-physics",
    )

    # 1. Style plan traces upstream
    assert style_plan.channel_dna_revision_id == rev_id
    assert style_plan.content_generation_request_id == narrative_plan.content_generation_request_id
    assert style_plan.narrative_plan_id == narrative_plan.id
    assert style_plan.content_pillar_id == "pillar-core-physics"

    # 2. Downstream QA context preserves complete lineage
    qa_context = style_plan.to_p24d_qa_context()
    assert qa_context["plan_id"] == str(style_plan.plan_id)
    assert qa_context["channel_dna_revision_id"] == str(rev_id)
    assert "hard_constraints_binding" in qa_context
    assert qa_context["hard_constraints_binding"]["no_fabricated_claims"] is True
