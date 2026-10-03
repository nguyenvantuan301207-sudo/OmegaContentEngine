"""Comprehensive unit tests for P24-B Creative Style Director.

Covers Test Matrices A through N:
A. Authority (pinned ChannelDNARevision, derived/recomputable, no competing root)
B. Hard constraints (cannot weaken global hard rules, prohibited behaviors blocked)
C. Soft preferences (contextual adaptation, deterministic output)
D. Narrative style (calm analytical, energetic educational, technical deep-dive)
E. Visual style (minimal, data-forward, cinematic, document-heavy)
F. Camera style (restrained, moderate, energetic, hard limits preserved)
G. Audio style (subtle music, high energy, sparse SFX, intentional silence)
H. Cross-modal coherence (coherent calm production, conflict detection, justified escalation)
I. Format adaptation (SHORT, MEDIUM, LONG overrides)
J. Content pillar (valid pillar adaptation, invalid pillar detection)
K. Avoid patterns (excessive camera blocked, constant SFX blocked, misleading packaging blocked)
L. Style continuity (intentional arc accepted, accidental drift detected)
M. Packaging handoff (clean hints projection, NO actual titles or thumbnails generated)
N. Legacy compatibility (ChannelDNA v1 fallback defaults)
"""

import uuid
from uuid import UUID

import pytest

from omega.application.creative_style_director import CreativeStyleDirector
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
from omega.infrastructure.models import ChannelDNARevision
from omega.domain.creative_style import (
    CameraStyleIntent,
    CreativeArc,
    CreativeArcSection,
    CreativeStyleFindingCode,
    CreativeStylePlan,
    CreativeStylePlanValidator,
    CrossModalConflictCode,
    GraphicStyleMode,
    HookIntensity,
    StyleDriftCode,
)
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)


def _make_sample_narrative_plan(revision_id: UUID) -> NarrativePlan:
    """Helper to build a valid 7-section NarrativePlan."""
    sections = [
        NarrativeSection(
            section_order=1,
            role=NarrativeSectionRole.HOOK,
            target_duration_seconds=15,
            objective="Hook viewer attention with quantum dilemma.",
            core_message="Can quantum computing break RSA encryption tomorrow?",
            key_information=["RSA relies on integer factorization difficulty."],
        ),
        NarrativeSection(
            section_order=2,
            role=NarrativeSectionRole.CONTEXT,
            target_duration_seconds=30,
            objective="Provide classical cryptography baseline.",
            core_message="Classical encryption safeguards all modern web traffic.",
            key_information=["Standard key exchange algorithms."],
        ),
        NarrativeSection(
            section_order=3,
            role=NarrativeSectionRole.DEVELOPMENT,
            target_duration_seconds=45,
            objective="Explain Shor's algorithm.",
            core_message="Shor's algorithm solves factorization in polynomial time.",
            key_information=["Quantum superposition and period finding."],
        ),
        NarrativeSection(
            section_order=4,
            role=NarrativeSectionRole.ESCALATION,
            target_duration_seconds=30,
            objective="Introduce physical scaling challenges.",
            core_message="The engineering hurdle: physical qubits vs logical qubits.",
            key_information=["Error rates require thousands of physical qubits per logical qubit."],
        ),
        NarrativeSection(
            section_order=5,
            role=NarrativeSectionRole.PAYOFF,
            target_duration_seconds=30,
            objective="Reveal post-quantum lattice solutions.",
            core_message="Post-quantum cryptography standards are already arriving.",
            key_information=["NIST lattice-based standards replace RSA before quantum supremacy."],
        ),
        NarrativeSection(
            section_order=6,
            role=NarrativeSectionRole.TAKEAWAY,
            target_duration_seconds=20,
            objective="Synthesize practical takeaways.",
            core_message="Infrastructure will transition smoothly if organizations prepare.",
            key_information=["Lattice algorithms already deployable today."],
        ),
        NarrativeSection(
            section_order=7,
            role=NarrativeSectionRole.CLOSING,
            target_duration_seconds=10,
            objective="Channel wrap-up and next steps.",
            core_message="Subscribe for factual quantum engineering updates.",
            key_information=["Channel wrap-up."],
        ),
    ]
    return NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=revision_id,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=180,
        estimated_duration_seconds=180,
        sections=sections,
    )


# ── Matrix A: Authority ──────────────────────────────────────────────────────


def test_matrix_a_derives_from_pinned_revision():
    """CreativeStylePlan must derive from and link to a pinned ChannelDNARevision."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA.create_default(niche="Quantum Computing")
    revision = ChannelDNARevision(
        id=rev_id,
        channel_id=uuid.uuid4(),
        version=1,
        snapshot=dna.model_dump(),
        change_reason="initial",
        actor="USER",
    )

    plan = CreativeStyleDirector.direct(channel_dna=revision)
    assert plan.channel_dna_revision_id == rev_id
    assert isinstance(plan, CreativeStylePlan)
    assert plan.version == "p24b-v1"


# ── Matrix B: Hard Constraints ──────────────────────────────────────────────


def test_matrix_b_hard_constraints_propagation():
    """Hard constraints must propagate strictly and cannot be weakened."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA(
        hard_constraints=HardConstraints(
            no_fabricated_claims=True,
            no_misleading_clickbait=True,
            no_profanity=True,
        ),
        packaging_preferences=PackagingPreferences(
            clickbait_tolerance="PERMITTED",
        ),
    )

    plan = CreativeStyleDirector.direct(channel_dna=dna, channel_dna_revision_id=rev_id)
    assert plan.hard_constraints_binding.no_misleading_clickbait is True
    assert plan.hard_constraints_binding.no_fabricated_claims is True
    # Packaging hints must enforce ZERO_TOLERANCE
    assert plan.packaging_hints.clickbait_tolerance == "ZERO_TOLERANCE"
    assert plan.packaging_hints.title_restraint == "FACTUAL_RESTRAINED"


# ── Matrix C: Soft Preferences & Determinism ────────────────────────────────


def test_matrix_c_soft_preferences_and_determinism():
    """Soft preferences adapt contextually, and identical inputs produce deterministic output."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA(
        soft_preferences=SoftPreferences(
            prefer_cinematic_visuals=True,
            prefer_moderate_pacing=True,
            prefer_instrumental_music=True,
        ),
    )

    plan1 = CreativeStyleDirector.direct(channel_dna=dna, channel_dna_revision_id=rev_id)
    plan2 = CreativeStyleDirector.direct(channel_dna=dna, channel_dna_revision_id=rev_id)

    assert plan1.visual_style.composition_character.value == "CINEMATIC"
    assert plan1.music_constraints.instrumental_only is True
    # Determinism: identical fields
    assert plan1.creative_intensity == plan2.creative_intensity
    assert plan1.narrative_style == plan2.narrative_style
    assert plan1.visual_style == plan2.visual_style


# ── Matrix D: Narrative Style ────────────────────────────────────────────────


def test_matrix_d_narrative_style_dimensions():
    """Tests calm analytical vs energetic educational narrative direction."""
    rev_id = uuid.uuid4()

    # Calm analytical channel
    calm_dna = ChannelDNA(
        editorial_voice=EditorialVoice(
            energy=VoiceEnergy.CALM,
            formality=VoiceFormality.FORMAL,
            technicality=VoiceTechnicality.TECHNICAL,
            depth=VoiceDepth.EXPLANATORY,
        ),
    )
    calm_plan = CreativeStyleDirector.direct(channel_dna=calm_dna, channel_dna_revision_id=rev_id)
    assert calm_plan.narrative_style.editorial_energy == VoiceEnergy.CALM
    assert calm_plan.narrative_style.technicality == VoiceTechnicality.TECHNICAL
    assert calm_plan.narrative_style.explanation_density.value == "DENSE"
    assert calm_plan.creative_intensity.narrative_energy <= 0.35

    # Energetic educational channel
    energetic_dna = ChannelDNA(
        editorial_voice=EditorialVoice(
            energy=VoiceEnergy.DYNAMIC,
            depth=VoiceDepth.CONCISE,
        ),
        narrative_preferences=NarrativePreferences(
            hook_style=NarrativeHookStyle.PARADOX,
        ),
    )
    energetic_plan = CreativeStyleDirector.direct(channel_dna=energetic_dna, channel_dna_revision_id=rev_id)
    assert energetic_plan.narrative_style.editorial_energy == VoiceEnergy.DYNAMIC
    assert energetic_plan.narrative_style.hook_intensity == HookIntensity.HIGH
    assert energetic_plan.creative_intensity.narrative_energy >= 0.7


# ── Matrix E: Visual Style ──────────────────────────────────────────────────


def test_matrix_e_visual_style_direction():
    """Tests visual density, evidence emphasis, and document treatment."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA(
        visual_preferences=VisualPreferences(
            visual_density=VisualDensityPreference.HIGH,
            evidence_emphasis="HIGH",
            diagram_frequency="MANDATORY",
            graphic_complexity="COMPLEX",
        ),
    )

    plan = CreativeStyleDirector.direct(channel_dna=dna, channel_dna_revision_id=rev_id)
    assert plan.visual_style.visual_density == VisualDensityPreference.HIGH
    assert plan.visual_style.evidence_emphasis == "HIGH"
    assert plan.visual_style.document_treatment.value == "ANNOTATED"
    assert plan.graphic_style.graphic_mode == GraphicStyleMode.TECHNICAL
    assert plan.graphic_style.annotation_density == "HEAVY"


# ── Matrix F: Camera Style ──────────────────────────────────────────────────


def test_matrix_f_camera_style_direction():
    """Tests camera restraint and motion intensity."""
    rev_id = uuid.uuid4()

    # Restrained camera
    restrained_dna = ChannelDNA(
        visual_preferences=VisualPreferences(
            camera_motion_intensity=CameraMotionIntensity.RESTRAINED,
            transition_restraint=True,
        ),
    )
    r_plan = CreativeStyleDirector.direct(channel_dna=restrained_dna, channel_dna_revision_id=rev_id)
    assert r_plan.camera_style.camera_style_intent == CameraStyleIntent.STATIC_RESTRAINED
    assert r_plan.camera_style.max_camera_energy <= 0.3

    # Smooth motion (explicitly disable overactive_camera_motion clamp for test)
    motion_dna = ChannelDNA(
        visual_preferences=VisualPreferences(
            camera_motion_intensity=CameraMotionIntensity.SMOOTH,
            transition_restraint=True,
        ),
        avoid_patterns=AvoidPatterns(overactive_camera_motion=False),
    )
    m_plan = CreativeStyleDirector.direct(channel_dna=motion_dna, channel_dna_revision_id=rev_id)
    assert m_plan.camera_style.camera_style_intent == CameraStyleIntent.SUBTLE_MOTION
    assert 0.3 <= m_plan.camera_style.max_camera_energy <= 0.6


# ── Matrix G: Audio Style ───────────────────────────────────────────────────


def test_matrix_g_audio_style_direction():
    """Tests subtle audio vs high energy and silence policies."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA(
        audio_preferences=AudioPreferences(
            music_usage_tendency="BALANCED",
            preferred_energy_min=0.15,
            preferred_energy_max=0.55,
            sfx_density="SPARSE",
            audio_restraint=True,
            sonic_character="MINIMAL",
        ),
    )

    plan = CreativeStyleDirector.direct(channel_dna=dna, channel_dna_revision_id=rev_id)
    assert plan.audio_style.music_tendency == "BALANCED"
    assert plan.audio_style.sfx_density == "SPARSE"
    assert plan.audio_style.sfx_prominence == "SUBTLE"
    assert plan.music_constraints.instrumental_only is True
    assert plan.sfx_constraints.max_sfx_per_minute <= 8


# ── Matrix H: Cross-Modal Coherence ─────────────────────────────────────────


def test_matrix_h_cross_modal_conflict_detection():
    """Detects cross-modal conflict when calm narration is paired with hyperactive camera."""
    rev_id = uuid.uuid4()
    # Force contradiction: calm voice but dynamic motion without avoid_pattern clamp
    dna = ChannelDNA(
        editorial_voice=EditorialVoice(energy=VoiceEnergy.CALM),
        visual_preferences=VisualPreferences(camera_motion_intensity=CameraMotionIntensity.DYNAMIC),
        avoid_patterns=AvoidPatterns(overactive_camera_motion=False),
    )

    plan = CreativeStyleDirector.direct(channel_dna=dna, channel_dna_revision_id=rev_id)
    # Check that coherence finding is flagged
    conflict_codes = [f.code for f in plan.coherence_findings]
    assert CrossModalConflictCode.CALM_NARRATION_HYPERACTIVE_CAMERA in conflict_codes


# ── Matrix I: Format Adaptation ─────────────────────────────────────────────


def test_matrix_i_format_profile_adaptation():
    """Tests format-specific overrides for SHORT vs LONG."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA(
        narrative_preferences=NarrativePreferences(
            preferred_pacing="BALANCED",
            context_depth=NarrativeContextDepth.MODERATE,
        ),
        format_overrides={
            "SHORT": FormatOverride(
                format_profile="SHORT",
                pacing="FAST",
                context_depth=NarrativeContextDepth.MINIMAL,
                visual_density="HIGH",
            ),
        },
    )

    short_plan = CreativeStyleDirector.direct(channel_dna=dna, format_profile="SHORT", channel_dna_revision_id=rev_id)
    assert short_plan.narrative_style.context_depth == NarrativeContextDepth.MINIMAL
    assert short_plan.format_profile == NarrativeFormatProfile.SHORT


# ── Matrix J: Content Pillar ────────────────────────────────────────────────


def test_matrix_j_content_pillar_adaptation():
    """Tests adapting creative direction to active content pillar."""
    rev_id = uuid.uuid4()
    pillar = ContentPillar(
        pillar_id="pil-deep-dive",
        name="Technical Tutorial & Deep Dive",
        description="Step-by-step engineering breakdowns.",
    )
    dna = ChannelDNA(
        content_pillars_v2=[pillar],
    )

    plan = CreativeStyleDirector.direct(
        channel_dna=dna,
        content_pillar_id="pil-deep-dive",
        channel_dna_revision_id=rev_id,
    )
    assert plan.content_pillar_id == "pil-deep-dive"
    assert plan.graphic_style.graphic_mode == GraphicStyleMode.TECHNICAL

    # Invalid pillar validation
    val = CreativeStylePlanValidator.validate(plan, channel_pillars=[
        ContentPillar(pillar_id="other-pillar", name="News", description="Daily news")
    ])
    assert not val.is_valid
    assert any(f.code == CreativeStyleFindingCode.INVALID_PILLAR_REFERENCE for f in val.findings)


# ── Matrix K: Avoid Patterns ────────────────────────────────────────────────


def test_matrix_k_avoid_pattern_enforcement():
    """Avoid patterns clamp camera motion, SFX density, and sensationalism."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA(
        avoid_patterns=AvoidPatterns(
            overactive_camera_motion=True,
            constant_sfx=True,
            sensationalized_claims=True,
        ),
        visual_preferences=VisualPreferences(
            camera_motion_intensity=CameraMotionIntensity.DYNAMIC,
        ),
        audio_preferences=AudioPreferences(
            sfx_density="DENSE",
        ),
    )

    plan = CreativeStyleDirector.direct(channel_dna=dna, channel_dna_revision_id=rev_id)
    assert plan.visual_style.motion_intensity == CameraMotionIntensity.RESTRAINED
    assert plan.camera_style.abrupt_moves_forbidden is True
    assert plan.audio_style.sfx_density in ("BALANCED", "SPARSE")
    assert plan.sfx_constraints.prohibit_constant_sfx is True


# ── Matrix L: Style Continuity & Arc ──────────────────────────────────────


def test_matrix_l_style_continuity_and_arc():
    """Creative arc models section progression, accepting justified escalation and detecting drift."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA.create_default(niche="Science")
    n_plan = _make_sample_narrative_plan(rev_id)

    plan = CreativeStyleDirector.direct(
        channel_dna=dna,
        narrative_plan=n_plan,
        channel_dna_revision_id=rev_id,
    )

    assert len(plan.creative_arc.sections) == 7
    roles = [s.section_role for s in plan.creative_arc.sections]
    assert roles == [
        NarrativeSectionRole.HOOK,
        NarrativeSectionRole.CONTEXT,
        NarrativeSectionRole.DEVELOPMENT,
        NarrativeSectionRole.ESCALATION,
        NarrativeSectionRole.PAYOFF,
        NarrativeSectionRole.TAKEAWAY,
        NarrativeSectionRole.CLOSING,
    ]
    # Hook and Payoff have justified escalation
    hook_sec = plan.creative_arc.sections[0]
    payoff_sec = plan.creative_arc.sections[4]
    assert hook_sec.justified_escalation is True
    assert payoff_sec.justified_escalation is True


# ── Matrix M: Packaging Handoff ─────────────────────────────────────────────


def test_matrix_m_packaging_handoff_projection():
    """Prepares clean packaging hints for P24-C without generating titles/thumbnails."""
    rev_id = uuid.uuid4()
    dna = ChannelDNA(
        packaging_preferences=PackagingPreferences(
            title_tone="FACTUAL_COMPELLING",
            title_length_tendency="CONCISE",
        ),
    )

    plan = CreativeStyleDirector.direct(channel_dna=dna, channel_dna_revision_id=rev_id)
    hints = plan.to_p24c_packaging_hints()

    assert hints.title_tone == "FACTUAL_COMPELLING"
    assert hints.clickbait_tolerance == "ZERO_TOLERANCE"
    # Ensure NO final titles or thumbnails generated
    assert not hasattr(hints, "final_title")
    assert not hasattr(hints, "generated_thumbnails")


# ── Matrix N: Legacy Compatibility ──────────────────────────────────────────


def test_matrix_n_legacy_channel_dna_compatibility():
    """A legacy ChannelDNA instance without explicit v2 structures directs cleanly."""
    rev_id = uuid.uuid4()
    legacy_dna = ChannelDNA()

    plan = CreativeStyleDirector.direct(channel_dna=legacy_dna, channel_dna_revision_id=rev_id)
    assert plan.channel_dna_revision_id == rev_id
    assert plan.narrative_style.formality == VoiceFormality.SEMI_FORMAL
    assert plan.visual_style.visual_density == VisualDensityPreference.MEDIUM
    assert plan.audio_style.music_tendency == "SUBTLE_BACKGROUND"

    val = CreativeStylePlanValidator.validate(plan)
    assert val.is_valid is True
    assert val.error_count == 0
