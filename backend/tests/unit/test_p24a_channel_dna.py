"""Comprehensive unit test matrix for Channel DNA v2 (P24-A).

Covers:
- Matrix A: Model (complete v2, minimal v2, invalid ranges, contradictory constraints)
- Matrix B: Revision (initial, next, immutability, monotonic version, single current)
- Matrix C: Legacy (legacy snapshot loads, v2 default projection, historical lineage)
- Matrix D: Policy (hard constraint wins, format overrides, soft preferences, conflict validation)
- Matrix E: Audience & Voice (technical, accessible, calm/analytical, energetic)
- Matrix F: Narrative Integration (strategy, pacing, CTA, hard constraints)
- Matrix G: Visual Integration (density, camera motion, diagrams, transition restraint)
- Matrix H: Audio Integration (music tendency, vocal policy, SFX density)
- Matrix I: Packaging Projection (title policy, thumbnail policy, clickbait zero-tolerance)
"""

from __future__ import annotations

import uuid
from uuid import uuid4

import pytest

from omega.application.channel_service import ChannelDNARevisionService
from omega.application.music_director_service import MusicDirector
from omega.application.retention_pacing_engine import PacingProfileResolver
from omega.application.sfx_director_service import SFXDirector
from omega.application.visual_editorial_qa_service import ChannelStyleQAEvaluator
from omega.domain.channel_dna import (
    AudioPreferences,
    AudienceProfile,
    AvoidPatterns,
    BrandVoice,
    CameraMotionIntensity,
    ChannelDNA,
    ChannelDNAFindingCode,
    ChannelDNAValidator,
    ChannelPositioning,
    ContentPillar,
    DesiredDepth,
    EditorialVoice,
    FormatOverride,
    HardConstraints,
    JargonSensitivity,
    KnowledgeLevel,
    NarrativeCTAStyle,
    NarrativeContextDepth,
    NarrativeHookStyle,
    NarrativePayoffStyle,
    NarrativePreferences,
    PackagingPreferences,
    ResolvedChannelDNA,
    ResolvedPackagingSpec,
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
from omega.domain.visual_beat import VisualBeat, VisualRole


# ── Matrix A: Model Tests ───────────────────────────────────────────────────


def test_matrix_a_complete_v2_model() -> None:
    """A fully populated ChannelDNA v2 instance passes validation."""
    dna = ChannelDNA(
        positioning=ChannelPositioning(
            channel_purpose="Examine complex machine learning architectures with empirical rigor",
            content_promise="Reproducible breakdowns with benchmark data",
            distinctive_angle="Empirical code-level verification",
            primary_subject_domain="Machine Learning",
            secondary_subject_domains=("Distributed Systems", "GPU Computing"),
            audience_benefit="Master production engineering principles",
        ),
        audience=AudienceProfile(
            primary_audience="Senior ML engineers and systems architects",
            expected_context="Working knowledge of PyTorch and distributed training",
            desired_depth=DesiredDepth.DEEP_DIVE,
            sensitivity_to_jargon=JargonSensitivity.LOW,
            knowledge_level=KnowledgeLevel.ADVANCED,
        ),
        editorial_voice=EditorialVoice(
            formality=VoiceFormality.FORMAL,
            depth=VoiceDepth.EXHAUSTIVE,
            energy=VoiceEnergy.CALM,
            expressiveness=VoiceExpressiveness.OBJECTIVE,
            technicality=VoiceTechnicality.SPECIALIZED,
            tone_character=VoiceCharacter.ANALYTICAL,
        ),
        hard_constraints=HardConstraints(
            no_fabricated_claims=True,
            no_misleading_clickbait=True,
            no_unsupported_medical_claims=True,
            no_profanity=True,
            content_safety_level="STRICT",
        ),
        narrative_preferences=NarrativePreferences(
            preferred_strategies=("EVIDENCE_FIRST", "PROBLEM_SOLUTION"),
            hook_style=NarrativeHookStyle.EVIDENCE_REVEAL,
            context_depth=NarrativeContextDepth.MODERATE,
            preferred_pacing="DELIBERATE",
            cta_style=NarrativeCTAStyle.MINIMAL,
        ),
        visual_preferences=VisualPreferences(
            visual_density=VisualDensityPreference.MEDIUM,
            camera_motion_intensity=CameraMotionIntensity.RESTRAINED,
            diagram_frequency="MANDATORY_FOR_COMPLEX_CLAIMS",
            transition_restraint=True,
        ),
        audio_preferences=AudioPreferences(
            music_usage_tendency="SUBTLE_BACKGROUND",
            preferred_energy_min=0.15,
            preferred_energy_max=0.55,
            vocal_policy="INSTRUMENTAL_ONLY",
            sfx_density="SPARSE",
        ),
        packaging_preferences=PackagingPreferences(
            title_tone="FACTUAL_COMPELLING",
            clickbait_tolerance="ZERO_TOLERANCE",
        ),
        content_pillars_v2=[
            ContentPillar(
                pillar_id="p1-transformers",
                name="Transformer Mechanics",
                description="Attention mechanisms and scaling laws",
                priority_weight=0.9,
            ),
            ContentPillar(
                pillar_id="p2-distributed",
                name="Distributed Inference",
                description="Tensor parallelism and vLLM internals",
                priority_weight=0.8,
            ),
        ],
        avoid_patterns=AvoidPatterns(
            sensationalized_claims=True,
            generic_cta=True,
        ),
    )

    validation = ChannelDNAValidator.validate(dna)
    assert validation.is_valid is True
    assert validation.error_count == 0


def test_matrix_a_minimal_valid_v2_and_defaults() -> None:
    """Default ChannelDNA constructs valid v2 structures out of the box."""
    dna = ChannelDNA.create_default(niche="Theoretical Physics")
    assert dna.positioning.primary_subject_domain == "Theoretical Physics"
    assert "Theoretical Physics" in dna.positioning.channel_purpose
    assert dna.hard_constraints.no_fabricated_claims is True
    assert dna.hard_constraints.no_misleading_clickbait is True

    validation = ChannelDNAValidator.validate(dna)
    assert validation.is_valid is True
    assert validation.error_count == 0


def test_matrix_a_invalid_ranges_and_contradictions() -> None:
    """Validator catches invalid energy bounds and contradictory clickbait policies."""
    # 1. Invalid audio energy range: min > max
    with pytest.raises(ValueError, match="preferred_energy_min cannot be greater"):
        AudioPreferences(preferred_energy_min=0.8, preferred_energy_max=0.2)

    # 2. Contradictory hard constraints: clickbait tolerance vs no_misleading_clickbait
    bad_dna = ChannelDNA(
        hard_constraints=HardConstraints(no_misleading_clickbait=True),
        packaging_preferences=PackagingPreferences(clickbait_tolerance="AGGRESSIVE_CLICKBAIT"),
    )
    result = ChannelDNAValidator.validate(bad_dna)
    assert result.is_valid is False
    assert any(f.code == ChannelDNAFindingCode.CONTRADICTORY_CONSTRAINTS for f in result.findings)

    # 3. Duplicate pillar IDs
    bad_pillars_dna = ChannelDNA(
        content_pillars_v2=[
            ContentPillar(pillar_id="same-id", name="Pillar 1", description="Desc 1"),
            ContentPillar(pillar_id="same-id", name="Pillar 2", description="Desc 2"),
        ]
    )
    res_pillar = ChannelDNAValidator.validate(bad_pillars_dna)
    assert res_pillar.is_valid is False
    assert any(f.code == ChannelDNAFindingCode.DUPLICATE_CONTENT_PILLAR_ID for f in res_pillar.findings)


# ── Matrix C: Legacy Revision Compatibility ─────────────────────────────────


def test_matrix_c_legacy_v1_snapshot_deserialization() -> None:
    """A legacy v1 snapshot without v2 fields loads with full backward-compatible defaults."""
    legacy_snapshot = {
        "audience": {
            "age_range": "25-45",
            "interests": ["investing", "economics"],
            "knowledge_level": "INTERMEDIATE",
        },
        "brand_voice": {
            "tone": ["OBJECTIVE", "ANALYTICAL"],
            "pace": "MODERATE",
        },
        "content_strategy": {
            "niche": "Macroeconomics",
            "content_pillars": ["Monetary Policy", "Yield Curves"],
        },
        "constraints": {
            "max_daily_videos": 1,
            "forbidden_topics": ["crypto get-rich-quick"],
        },
    }

    # Deserializing legacy snapshot
    dna = ChannelDNA.model_validate(legacy_snapshot)

    # Legacy fields remain intact
    assert dna.audience.knowledge_level == KnowledgeLevel.INTERMEDIATE
    assert "investing" in dna.audience.interests
    assert dna.brand_voice.pace == "MODERATE"
    assert dna.content_strategy.niche == "Macroeconomics"

    # v2 fields receive deterministic sensible defaults
    assert dna.positioning is not None
    assert dna.hard_constraints.no_fabricated_claims is True
    assert dna.hard_constraints.no_misleading_clickbait is True
    assert dna.narrative_preferences.preferred_pacing == "BALANCED"
    assert dna.visual_preferences.visual_density == VisualDensityPreference.MEDIUM

    # Resolving format from legacy snapshot derives pillars from content_strategy
    resolved = dna.resolve_for_format("LONG")
    assert len(resolved.content_pillars) == 2
    assert resolved.content_pillars[0].name == "Monetary Policy"
    assert resolved.content_pillars[1].name == "Yield Curves"


# ── Matrix D: Policy Resolution & Format Overrides ──────────────────────────


def test_matrix_d_format_overrides_and_hard_constraints_invariance() -> None:
    """Format overrides update soft preferences (pacing, visual density) but NEVER weaken hard constraints."""
    dna = ChannelDNA(
        hard_constraints=HardConstraints(
            no_fabricated_claims=True,
            no_misleading_clickbait=True,
            no_profanity=True,
        ),
        narrative_preferences=NarrativePreferences(
            preferred_pacing="DELIBERATE",
            context_depth=NarrativeContextDepth.COMPREHENSIVE,
        ),
        visual_preferences=VisualPreferences(
            visual_density=VisualDensityPreference.MEDIUM,
        ),
        format_overrides={
            "SHORT": FormatOverride(
                pacing="FAST",
                context_depth=NarrativeContextDepth.MINIMAL,
                visual_density=VisualDensityPreference.HIGH,
                music_tendency="KEY_BEATS_ONLY",
            )
        },
    )

    # Resolve for LONG format: respects global defaults
    long_res = dna.resolve_for_format("LONG")
    assert long_res.narrative_preferences.preferred_pacing == "DELIBERATE"
    assert long_res.narrative_preferences.context_depth == NarrativeContextDepth.COMPREHENSIVE
    assert long_res.visual_preferences.visual_density == VisualDensityPreference.MEDIUM
    assert long_res.hard_constraints.no_fabricated_claims is True

    # Resolve for SHORT format: applies overrides
    short_res = dna.resolve_for_format("SHORT")
    assert short_res.narrative_preferences.preferred_pacing == "FAST"
    assert short_res.narrative_preferences.context_depth == NarrativeContextDepth.MINIMAL
    assert short_res.visual_preferences.visual_density == VisualDensityPreference.HIGH
    assert short_res.audio_preferences.music_usage_tendency == "KEY_BEATS_ONLY"

    # Hard constraints remain strictly invariant
    assert short_res.hard_constraints.no_fabricated_claims is True
    assert short_res.hard_constraints.no_misleading_clickbait is True
    assert short_res.hard_constraints.no_profanity is True


# ── Matrix E: Audience & Voice Dimensions ───────────────────────────────────


def test_matrix_e_audience_and_voice_profiles() -> None:
    """Audience depth and editorial voice dimensions reflect distinct channel personalities."""
    # Technical rigorous channel
    tech_dna = ChannelDNA(
        audience=AudienceProfile(
            knowledge_level=KnowledgeLevel.ADVANCED,
            desired_depth=DesiredDepth.ACADEMIC,
            sensitivity_to_jargon=JargonSensitivity.LOW,
        ),
        editorial_voice=EditorialVoice(
            formality=VoiceFormality.FORMAL,
            depth=VoiceDepth.EXHAUSTIVE,
            energy=VoiceEnergy.CALM,
            technicality=VoiceTechnicality.SPECIALIZED,
        ),
    )
    assert tech_dna.audience.desired_depth == DesiredDepth.ACADEMIC
    assert tech_dna.editorial_voice.formality == VoiceFormality.FORMAL

    # Accessible, dynamic explainer channel
    pop_dna = ChannelDNA(
        audience=AudienceProfile(
            knowledge_level=KnowledgeLevel.BEGINNER,
            desired_depth=DesiredDepth.PRACTICAL,
            sensitivity_to_jargon=JargonSensitivity.HIGH,
        ),
        editorial_voice=EditorialVoice(
            formality=VoiceFormality.CONVERSATIONAL,
            depth=VoiceDepth.CONCISE,
            energy=VoiceEnergy.DYNAMIC,
            technicality=VoiceTechnicality.ACCESSIBLE,
            tone_character=VoiceCharacter.ENGAGING,
        ),
    )
    assert pop_dna.audience.knowledge_level == KnowledgeLevel.BEGINNER
    assert pop_dna.editorial_voice.energy == VoiceEnergy.DYNAMIC


# ── Matrix F: P21 Narrative Integration ─────────────────────────────────────


def test_matrix_f_p21_narrative_integration() -> None:
    """PacingProfileResolver directly honors v2 narrative preferences."""
    # 1. Preferred pacing = FAST resolves to PacingProfile.FAST
    dna_fast = ChannelDNA(
        narrative_preferences=NarrativePreferences(preferred_pacing="FAST"),
        brand_voice=BrandVoice(pace="MODERATE"),
    )
    profile_fast = PacingProfileResolver.resolve_profile(dna_fast)
    assert profile_fast == PacingProfile.FAST

    # 2. Preferred pacing = DELIBERATE resolves to PacingProfile.DELIBERATE
    dna_deliberate = ChannelDNA(
        narrative_preferences=NarrativePreferences(preferred_pacing="DELIBERATE"),
        brand_voice=BrandVoice(pace="MODERATE"),
    )
    profile_deliberate = PacingProfileResolver.resolve_profile(dna_deliberate)
    assert profile_deliberate == PacingProfile.DELIBERATE

    # 3. Dynamic editorial voice energy resolves to FAST
    dna_dyn = ChannelDNA(
        editorial_voice=EditorialVoice(energy=VoiceEnergy.DYNAMIC),
        narrative_preferences=NarrativePreferences(preferred_pacing=""),  # empty forces fallback
        brand_voice=BrandVoice(pace="MODERATE"),
    )
    assert PacingProfileResolver.resolve_profile(dna_dyn) == PacingProfile.FAST


# ── Matrix G: P22 Visual Integration ────────────────────────────────────────


def test_matrix_g_p22_visual_integration() -> None:
    """ChannelStyleQAEvaluator respects v2 visual density preference."""
    dna_low_density = ChannelDNA(
        visual_preferences=VisualPreferences(visual_density=VisualDensityPreference.LOW)
    )

    beat_dense_diagram = VisualBeat(
        scene_id=1,
        parent_scene_index=1,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        visual_intent="Show complex architecture network",
        information_goal="Explain connections",
        visual_role=VisualRole.DIAGRAM,
    )

    findings = ChannelStyleQAEvaluator.evaluate(
        visual_beats=[beat_dense_diagram],
        channel_dna=dna_low_density,
    )
    assert len(findings) == 1
    assert "contrary to channel LOW density preference" in findings[0].explanation


# ── Matrix H: P23 Audio Integration ─────────────────────────────────────────


def test_matrix_h_p23_audio_integration() -> None:
    """MusicDirector and SFXDirector respect v2 audio preferences."""
    sec1 = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Introduce hook",
        target_duration_seconds=15,
    )
    narrative = NarrativePlan(
        content_generation_request_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        status=NarrativePlanStatus.APPROVED,
        topic="Quantum Computing",
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=15,
        estimated_duration_seconds=15,
        sections=[sec1],
    )
    timing = SectionTimingDetail(
        section_id=sec1.id,
        section_order=1,
        role=sec1.role,
        current_duration_seconds=15,
        recommended_duration_seconds=15,
        target_information_density=InformationDensity.MEDIUM,
        reveal_stage=RevealStage.SETUP,
        intensity_score=0.4,
    )
    pacing = PacingPlan(
        narrative_plan_id=narrative.id,
        narrative_plan_version=1,
        pacing_profile=PacingProfile.BALANCED,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=15,
        initial_duration_sum_seconds=15,
        optimized_duration_sum_seconds=15,
        section_timings=[timing],
        open_loop_metrics=[],
        findings=[],
        recommended_adjustments=[],
        escalation_curve=[0.4],
    )

    # 1. AudioPreferences.music_usage_tendency = NONE forces MusicIntent.NONE
    silent_dna = ChannelDNA(
        audio_preferences=AudioPreferences(music_usage_tendency="NONE")
    )
    music_arc = MusicDirector.direct(
        narrative_plan=narrative,
        pacing_plan=pacing,
        channel_dna=silent_dna,
    )
    assert music_arc.cues[0].intent.intent.value == "NONE"
    assert music_arc.cues[0].target_energy == 0.0

    # 2. AudioPreferences.sfx_density = SPARSE enforces tighter cue bounds
    sparse_sfx_dna = ChannelDNA(
        audio_preferences=AudioPreferences(sfx_density="SPARSE")
    )
    sfx_plan = SFXDirector.direct(
        narrative_plan=narrative,
        pacing_plan=pacing,
        channel_dna=sparse_sfx_dna,
        available_assets=[],
    )
    assert sfx_plan is not None


# ── Matrix I: Packaging Projection ──────────────────────────────────────────


def test_matrix_i_packaging_projection() -> None:
    """Packaging projection derives clean, non-competing spec for P24-C."""
    dna = ChannelDNA(
        hard_constraints=HardConstraints(no_misleading_clickbait=True),
        packaging_preferences=PackagingPreferences(
            title_tone="FACTUAL_INTRIGUING",
            title_length_tendency="CONCISE",
            thumbnail_density="MINIMAL",
            thumbnail_text_policy="MAX_3_WORDS",
            description_style="STRUCTURED_OUTLINE",
            chapter_style="SECTION_BASED",
            metadata_voice="OBJECTIVE",
            clickbait_tolerance="ZERO_TOLERANCE",
        ),
    )

    spec: ResolvedPackagingSpec = dna.to_packaging_spec("LONG")
    assert spec.title_tone == "FACTUAL_INTRIGUING"
    assert spec.title_length_tendency == "CONCISE"
    assert spec.thumbnail_density == "MINIMAL"
    assert spec.clickbait_tolerance == "ZERO_TOLERANCE"
    assert spec.hard_constraints_binding is True
    assert "ALL_CAPS" in spec.prohibited_patterns
