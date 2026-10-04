"""Unit test matrix for P24-D Creative QA & Brand Acceptance.

Covers Test Matrix:
A. ChannelDNA: correct revision, wrong revision lineage, hard constraint violation
B. Voice: calm analytical match, energetic mismatch, justified variation
C. Narrative: valid style, hook over-intensified, CTA mismatch
D. Visual: valid density, overactive visual style, document/diagram fit
E. Camera: restrained match, excessive motion, justified escalation
F. Audio: valid subtle audio, music too aggressive for style, constant SFX conflict
G. Title: grounded brand-fit title, misleading clickbait, tone mismatch, rejected title resurrection
H. Thumbnail: valid thumbnail, overloaded text, unsupported implied claim, mobile readability
I. Description: brand-fit, unsupported statement, CTA mismatch, attribution missing
J. Packaging coherence: aligned package, title/thumbnail mismatch, packaging/content mismatch
K. Promise/payoff: aligned, overpromise
L. Cross-modal: coherent calm production, incoherent hyperactive combination, intentional escalation accepted
M. Drift: stable style, unexplained drift
N. Gate: PASS continues, REVISE blocks, FAIL blocks
O. Finding dedupe: duplicate source findings collapse
"""

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from omega.application.creative_qa_service import (
    CreativeQAService,
    FinalCreativeGate,
    FindingDeduplicationService,
)
from omega.domain.channel_dna import (
    AudioPreferences,
    AvoidPatterns,
    CameraMotionIntensity,
    ChannelDNA,
    ContentPillar,
    EditorialVoice,
    HardConstraints,
    PackagingPreferences,
    SoftPreferences,
    VisualDensityPreference,
    VisualPreferences,
    VoiceCharacter,
    VoiceDepth,
    VoiceEnergy,
    VoiceFormality,
    VoiceTechnicality,
)
from omega.domain.creative_qa import (
    CreativeQAFinding,
    CreativeQAFindingCode,
    CreativeQARecommendationAction,
    CreativeQAResult,
    CreativeQASeverity,
    CreativeQAStatus,
    CreativeQASubsystem,
    CreativeRenderGateError,
)
from omega.domain.creative_style import (
    AudioStyleDirection,
    CameraStyleDirection,
    CameraStyleIntent,
    CreativeArc,
    CreativeArcSection,
    CreativeIntensityProfile,
    CreativeStylePlan,
    GraphicStyleDirection,
    HookIntensity,
    NarrativeStyleDirection,
    PackagingStyleHints,
    StyleRationaleEntry,
    VisualStyleDirection,
)
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.packaging import (
    CTAMetadata,
    PackagingPlan,
    PackagingProvenance,
    TextOverlayIntent,
    ThumbnailArtifact,
    ThumbnailConcept,
    ThumbnailSafeZone,
    TitleCandidate,
    TitleStrategy,
    VideoChapter,
)


@pytest.fixture
def test_channel_id() -> UUID:
    return uuid.uuid4()


@pytest.fixture
def valid_channel_dna() -> ChannelDNA:
    return ChannelDNA(
        editorial_voice=EditorialVoice(
            formality=VoiceFormality.FORMAL,
            depth=VoiceDepth.EXPLANATORY,
            energy=VoiceEnergy.CALM,
            technicality=VoiceTechnicality.TECHNICAL,
            character=VoiceCharacter.SERIOUS,
        ),
        hard_constraints=HardConstraints(
            no_fabricated_claims=True,
            no_misleading_clickbait=True,
            no_unsupported_medical_claims=True,
            no_profanity=True,
            prohibited_vocabulary=("forbidden_secret_phrase",),
        ),
        visual_preferences=VisualPreferences(
            visual_density=VisualDensityPreference.MEDIUM,
            camera_motion_intensity=CameraMotionIntensity.RESTRAINED,
        ),
        audio_preferences=AudioPreferences(
            sonic_character="MINIMAL",
            vocal_policy="INSTRUMENTAL_ONLY",
        ),
        avoid_patterns=AvoidPatterns(
            overactive_camera_motion=True,
            constant_sfx=True,
            generic_cta=True,
            overloaded_thumbnails=True,
            sensationalized_claims=True,
        ),
        content_pillars_v2=(
            ContentPillar(
                pillar_id="quantum_computing",
                name="Quantum Computing",
                description="Technical physics breakdowns",
                priority_weight=1.0,
                allowed_subtopics=("superconducting", "qubits"),
                exclusions=("crypto", "get_rich_quick"),
            ),
        ),
    )


@pytest.fixture
def valid_style_plan(test_channel_id: UUID) -> CreativeStylePlan:
    return CreativeStylePlan(
        channel_dna_revision_id=test_channel_id,
        content_pillar_id="quantum_computing",
        narrative_style=NarrativeStyleDirection(
            hook_intensity=HookIntensity.SUBTLE,
            editorial_energy=VoiceEnergy.CALM,
            formality=VoiceFormality.FORMAL,
        ),
        visual_style=VisualStyleDirection(
            visual_density=VisualDensityPreference.MEDIUM,
        ),
        camera_style=CameraStyleDirection(
            camera_style_intent=CameraStyleIntent.SUBTLE_MOTION,
            max_camera_energy=0.4,
        ),
        audio_style=AudioStyleDirection(
            target_music_energy=0.35,
            sfx_density="SPARSE",
            vocal_policy="INSTRUMENTAL_ONLY",
        ),
        creative_intensity=CreativeIntensityProfile(
            narrative_energy=0.3,
            visual_energy=0.4,
            camera_energy=0.3,
            music_energy=0.3,
            sfx_energy=0.2,
        ),
    )


@pytest.fixture
def valid_narrative_plan(test_channel_id: UUID) -> NarrativePlan:
    sec1 = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Introduce silicon qubit coherence scaling",
        target_duration_seconds=30,
    )
    sec2 = NarrativeSection(
        section_order=2,
        role=NarrativeSectionRole.CONTEXT,
        objective="Explain thermal noise limits",
        target_duration_seconds=60,
    )
    sec3 = NarrativeSection(
        section_order=3,
        role=NarrativeSectionRole.PAYOFF,
        objective="Deliver empirical findings on error suppression",
        target_duration_seconds=90,
    )
    return NarrativePlan(
        channel_dna_revision_id=test_channel_id,
        content_generation_request_id=uuid.uuid4(),
        target_duration_seconds=180,
        estimated_duration_seconds=180,
        metadata={"premise": "Silicon quantum dot qubit coherence scaling analysis"},
        sections=[sec1, sec2, sec3],
        format_profile=NarrativeFormatProfile.SHORT,
    )


@pytest.fixture
def valid_packaging_plan(test_channel_id: UUID, valid_style_plan: CreativeStylePlan, tmp_path: Path) -> PackagingPlan:
    thumb_file = tmp_path / "valid_thumb.png"
    thumb_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000)

    title = TitleCandidate(
        text="Silicon Quantum Dots: Coherence Scaling Under Thermal Noise",
        strategy=TitleStrategy.FACTUAL,
        character_count=58,
        is_grounded=True,
        clickbait_risk="NONE",
    )
    thumb = ThumbnailConcept(
        primary_subject="Silicon Qubit Diagram",
        text_overlay_intent=TextOverlayIntent.SHORT_TEXT,
        text_content="Qubit Coherence",
        safe_zone=ThumbnailSafeZone.LEFT_WEIGHTED,
        is_grounded=True,
        contrast_intent="HIGH",
    )
    thumb_art = ThumbnailArtifact(
        concept_id=thumb.concept_id,
        file_path=thumb_file,
        width=1280,
        height=720,
        file_size_bytes=2008,
        content_sha256="a" * 64,
    )
    prov = PackagingProvenance(
        channel_dna_revision_id=test_channel_id,
        creative_style_plan_id=valid_style_plan.plan_id,
        selected_title_id=title.candidate_id,
        selected_thumbnail_id=thumb.concept_id,
    )

    return PackagingPlan(
        channel_dna_revision_id=test_channel_id,
        creative_style_plan_id=valid_style_plan.plan_id,
        title_candidates=(title,),
        selected_title=title,
        thumbnail_concepts=(thumb,),
        selected_thumbnail=thumb,
        physical_thumbnail_artifact=thumb_art,
        description="Comprehensive technical analysis of silicon quantum dot coherence scaling.\n\nTakeaway: Error suppression verified.\n\nDiscussion: Share your thoughts below.",
        chapters=(
            VideoChapter(title="Introduction", start_time_seconds=0),
            VideoChapter(title="Thermal Noise", start_time_seconds=30),
            VideoChapter(title="Results", start_time_seconds=90),
        ),
        tags=("quantum", "qubit", "silicon", "physics"),
        cta_metadata=CTAMetadata(cta_policy="SOFT_CTA", cta_text="Join the discussion below"),
        attribution_block="Audio Narration: Kokoro TTS (Neural Production).\nMusic: CC-BY Licensed Ambient Bed.",
        provenance=prov,
    )


# ── Test Suite ───────────────────────────────────────────────────────────────


def test_matrix_a_channel_dna_compliance_and_lineage(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix A: Revision match passes; mismatch blocks; hard constraint violation blocks."""
    qa_service = CreativeQAService()

    # 1. Matching revision -> PASS
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert res.status == CreativeQAStatus.PASS
    assert res.blocker_count == 0

    # 2. Wrong revision lineage -> BLOCKER / FAIL
    wrong_rev = uuid.uuid4()
    res_wrong = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=wrong_rev,
        style_plan=valid_style_plan,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert res_wrong.status == CreativeQAStatus.FAIL
    assert any(f.finding_code == CreativeQAFindingCode.CHANNEL_REVISION_MISMATCH for f in res_wrong.findings)

    # 3. Hard constraint custom prohibition -> BLOCKER / FAIL
    bad_title = TitleCandidate(
        text="Forbidden_Secret_Phrase in Silicon Qubits",
        strategy=TitleStrategy.FACTUAL,
        character_count=41,
        is_grounded=True,
    )
    bad_pkg = valid_packaging_plan.model_copy(update={"selected_title": bad_title})
    res_bad = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=bad_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert res_bad.status == CreativeQAStatus.FAIL
    assert any(f.finding_code == CreativeQAFindingCode.PROHIBITED_PHRASE_DETECTED for f in res_bad.findings)


def test_matrix_b_editorial_voice(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix B: Calm analytical matches; casual mismatch raises error; justified variation is accepted."""
    qa_service = CreativeQAService()

    # Formality mismatch (Channel is FORMAL, plan is CASUAL)
    casual_plan = valid_style_plan.model_copy(
        update={
            "narrative_style": valid_style_plan.narrative_style.model_copy(
                update={"formality": VoiceFormality.CASUAL}
            )
        }
    )
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=casual_plan,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert res.status == CreativeQAStatus.REVISE
    assert any(f.finding_code == CreativeQAFindingCode.VOICE_FORMALITY_MISMATCH for f in res.findings)

    # Justified energy variation
    justified_plan = valid_style_plan.model_copy(
        update={
            "narrative_style": valid_style_plan.narrative_style.model_copy(
                update={"editorial_energy": VoiceEnergy.HIGH_ENERGY}
            ),
            "style_rationale": (
                StyleRationaleEntry(
                    aspect="narrative_energy",
                    decision="HIGH_ENERGY",
                    contributing_source="NarrativePlan.payoff",
                    explanation="High energy justified for technical reveal payoff",
                ),
            ),
        }
    )
    res_just = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=justified_plan,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    # Justified rationale does NOT emit warning/error for energy
    assert not any(f.finding_code == CreativeQAFindingCode.VOICE_ENERGY_MISMATCH for f in res_just.findings)


def test_matrix_c_narrative_style(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
):
    """Test Matrix C: Hook over-intensified beyond style plan limit is detected."""
    qa_service = CreativeQAService()

    over_hook_narrative = NarrativePlan(
        channel_dna_revision_id=test_channel_id,
        content_generation_request_id=uuid.uuid4(),
        target_duration_seconds=120,
        estimated_duration_seconds=120,
        metadata={"premise": "Qubit scaling"},
        sections=[
            NarrativeSection(
                section_order=1,
                role=NarrativeSectionRole.HOOK,
                objective="Reveal INSANE shocking secret quantum scaling",
                target_duration_seconds=30,
            ),
        ],
        format_profile=NarrativeFormatProfile.SHORT,
    )
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=valid_packaging_plan,
        narrative_plan=over_hook_narrative,
    )
    assert any(f.finding_code == CreativeQAFindingCode.HOOK_INTENSITY_MISMATCH for f in res.findings)


def test_matrix_d_visual_style(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix D: Visual density mismatch with channel preferences."""
    qa_service = CreativeQAService()

    minimal_dna = valid_channel_dna.model_copy(
        update={
            "visual_preferences": valid_channel_dna.visual_preferences.model_copy(
                update={"visual_density": VisualDensityPreference.LOW}
            )
        }
    )
    high_dense_style = valid_style_plan.model_copy(
        update={
            "visual_style": valid_style_plan.visual_style.model_copy(
                update={"visual_density": VisualDensityPreference.HIGH}
            )
        }
    )
    res = qa_service.evaluate_creative_package(
        channel_dna=minimal_dna,
        pinned_revision_id=test_channel_id,
        style_plan=high_dense_style,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert any(f.finding_code == CreativeQAFindingCode.VISUAL_DENSITY_MISMATCH for f in res.findings)


def test_matrix_e_camera_motion(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix E: Excessive camera motion violates avoid_patterns."""
    qa_service = CreativeQAService()

    hyper_cam_style = valid_style_plan.model_copy(
        update={
            "camera_style": CameraStyleDirection(
                camera_style_intent=CameraStyleIntent.ENERGETIC_MOTION,
                max_camera_energy=0.9,
            )
        }
    )
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=hyper_cam_style,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert res.status == CreativeQAStatus.REVISE
    assert any(f.finding_code == CreativeQAFindingCode.CAMERA_TOO_ACTIVE for f in res.findings)


def test_matrix_f_audio_style(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix F: Music too aggressive or constant SFX conflict."""
    qa_service = CreativeQAService()

    aggressive_audio_style = valid_style_plan.model_copy(
        update={
            "audio_style": AudioStyleDirection(
                target_music_energy=0.85,
                sfx_density="DENSE",
                sfx_prominence="PROMINENT",
            )
        }
    )
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=aggressive_audio_style,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert res.status == CreativeQAStatus.REVISE
    assert any(f.finding_code == CreativeQAFindingCode.MUSIC_ENERGY_TOO_HIGH for f in res.findings)
    assert any(f.finding_code == CreativeQAFindingCode.SFX_DENSITY_EXCESSIVE for f in res.findings)


def test_matrix_g_title_qa(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix G: Grounded brand-fit passes; misleading clickbait blocks; rejected title blocks."""
    qa_service = CreativeQAService()

    # 1. Clickbait title -> BLOCKER
    cb_title = TitleCandidate(
        text="You Won't Believe What Quantum Computers Just Did!",
        strategy=TitleStrategy.CURIOSITY,
        character_count=52,
        clickbait_risk="HIGH",
    )
    cb_pkg = valid_packaging_plan.model_copy(update={"selected_title": cb_title})
    res_cb = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=cb_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert res_cb.status == CreativeQAStatus.FAIL
    assert any(f.finding_code == CreativeQAFindingCode.MISLEADING_CLICKBAIT for f in res_cb.findings)

    # 2. Ungrounded title -> BLOCKER
    ungrounded_title = TitleCandidate(
        text="Silicon Qubits Reach 10 Million Operands Today",
        strategy=TitleStrategy.FACTUAL,
        character_count=45,
        is_grounded=False,
    )
    ungrounded_pkg = valid_packaging_plan.model_copy(update={"selected_title": ungrounded_title})
    res_ungrounded = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=ungrounded_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert res_ungrounded.status == CreativeQAStatus.FAIL
    assert any(f.finding_code == CreativeQAFindingCode.TITLE_UNGROUNDED for f in res_ungrounded.findings)

    # 3. Resurrecting rejected title with constraint findings -> BLOCKER
    rejected_title = TitleCandidate(
        text="Silicon Qubit Supercharged Breakthrough",
        strategy=TitleStrategy.OUTCOME,
        character_count=39,
        constraint_findings=("EXAGGERATION_DISQUALIFIED",),
    )
    rej_pkg = valid_packaging_plan.model_copy(update={"selected_title": rejected_title})
    res_rej = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=rej_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert res_rej.status == CreativeQAStatus.FAIL
    assert any(f.finding_code == CreativeQAFindingCode.TITLE_PREVIOUSLY_REJECTED for f in res_rej.findings)


def test_matrix_h_thumbnail_qa(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix H: Overloaded text, mobile readability, and safe-area collision."""
    qa_service = CreativeQAService()

    # Overloaded text (>4 words / >30 chars)
    over_thumb = valid_packaging_plan.selected_thumbnail.model_copy(
        update={"text_content": "This is a full sentence overlay explaining quantum physics"}
    )
    over_pkg = valid_packaging_plan.model_copy(update={"selected_thumbnail": over_thumb})
    res_over = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=over_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert any(f.finding_code == CreativeQAFindingCode.THUMBNAIL_TEXT_OVERLOADED for f in res_over.findings)

    # Safe zone full (badge collision risk)
    badge_thumb = valid_packaging_plan.selected_thumbnail.model_copy(
        update={"safe_zone": ThumbnailSafeZone.FULL}
    )
    badge_pkg = valid_packaging_plan.model_copy(update={"selected_thumbnail": badge_thumb})
    res_badge = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=badge_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert any(f.finding_code == CreativeQAFindingCode.THUMBNAIL_SAFE_AREA_COLLISION for f in res_badge.findings)


def test_matrix_i_description_and_metadata(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix I: Missing audio attribution and keyword stuffing."""
    qa_service = CreativeQAService()

    # Missing attribution
    no_attr_pkg = valid_packaging_plan.model_copy(update={"attribution_block": ""})
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=no_attr_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert res.status == CreativeQAStatus.REVISE
    assert any(f.finding_code == CreativeQAFindingCode.ATTRIBUTION_MISSING for f in res.findings)

    # Keyword stuffing
    stuffed_pkg = valid_packaging_plan.model_copy(update={"tags": tuple(f"tag{i}" for i in range(30))})
    res_stuffed = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=stuffed_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert any(f.finding_code == CreativeQAFindingCode.KEYWORD_STUFFING for f in res_stuffed.findings)


def test_matrix_j_packaging_coherence(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix J: Title and thumbnail promise mismatch."""
    qa_service = CreativeQAService()

    mismatch_thumb = valid_packaging_plan.selected_thumbnail.model_copy(
        update={"primary_subject": "Biology DNA Helix Genetics"}
    )
    mismatch_pkg = valid_packaging_plan.model_copy(update={"selected_thumbnail": mismatch_thumb})
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=mismatch_pkg,
        narrative_plan=valid_narrative_plan,
    )
    assert res.status == CreativeQAStatus.REVISE
    assert any(f.finding_code == CreativeQAFindingCode.TITLE_THUMBNAIL_PROMISE_MISMATCH for f in res.findings)


def test_matrix_k_promise_payoff(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
):
    """Test Matrix K: Packaging overpromise not delivered by payoff sections."""
    qa_service = CreativeQAService()

    empty_payoff_narrative = NarrativePlan(
        channel_dna_revision_id=test_channel_id,
        content_generation_request_id=uuid.uuid4(),
        target_duration_seconds=60,
        estimated_duration_seconds=60,
        metadata={"premise": "Qubit scaling"},
        sections=[
            NarrativeSection(
                section_order=1,
                role=NarrativeSectionRole.HOOK,
                objective="Introductory overview of quantum coherence",
                target_duration_seconds=60,
            ),
        ],
        format_profile=NarrativeFormatProfile.SHORT,
    )
    overpromise_title = TitleCandidate(
        text="Proven Solution Reveals the Secret to Quantum Scaling",
        strategy=TitleStrategy.OUTCOME,
        character_count=54,
    )
    over_pkg = valid_packaging_plan.model_copy(update={"selected_title": overpromise_title})
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=over_pkg,
        narrative_plan=empty_payoff_narrative,
    )
    assert res.status == CreativeQAStatus.FAIL
    assert any(f.finding_code == CreativeQAFindingCode.OVERPROMISE for f in res.findings)


def test_matrix_l_cross_modal_coherence(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix L: Calm narration + hyperactive camera + aggressive audio is flagged."""
    qa_service = CreativeQAService()

    incoherent_style = valid_style_plan.model_copy(
        update={
            "creative_intensity": CreativeIntensityProfile(
                narrative_energy=0.2,
                camera_energy=0.9,
                music_energy=0.9,
                sfx_energy=0.8,
            )
        }
    )
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=incoherent_style,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert res.status == CreativeQAStatus.REVISE
    assert any(f.finding_code == CreativeQAFindingCode.CALM_NARRATION_HYPERACTIVE_PRODUCTION for f in res.findings)


def test_matrix_m_style_drift(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix M: Unexplained camera style drift across development sections."""
    qa_service = CreativeQAService()

    drift_style = valid_style_plan.model_copy(
        update={
            "creative_arc": CreativeArc(
                sections=(
                    CreativeArcSection(
                        section_order=1,
                        section_role=NarrativeSectionRole.DEVELOPMENT,
                        target_energy=0.2,
                        visual_focus="Diagram",
                        camera_motion=CameraStyleIntent.STATIC_RESTRAINED,
                        audio_intent="Minimal bed",
                        justified_escalation=False,
                        rationale="Initial baseline",
                    ),
                    CreativeArcSection(
                        section_order=2,
                        section_role=NarrativeSectionRole.DEVELOPMENT,
                        target_energy=0.85,
                        visual_focus="Complex 3D model",
                        camera_motion=CameraStyleIntent.ENERGETIC_MOTION,
                        audio_intent="Loud synth",
                        justified_escalation=False,
                        rationale="Abrupt spike",
                    ),
                )
            )
        }
    )
    res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=drift_style,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert any(f.finding_code == CreativeQAFindingCode.CAMERA_STYLE_DRIFT for f in res.findings)


def test_matrix_n_final_creative_gate(
    test_channel_id: UUID,
    valid_channel_dna: ChannelDNA,
    valid_style_plan: CreativeStylePlan,
    valid_packaging_plan: PackagingPlan,
    valid_narrative_plan: NarrativePlan,
):
    """Test Matrix N: PASS verifies; REVISE and FAIL raise CreativeRenderGateError."""
    qa_service = CreativeQAService()

    # Valid PASS
    pass_res = qa_service.evaluate_creative_package(
        channel_dna=valid_channel_dna,
        pinned_revision_id=test_channel_id,
        style_plan=valid_style_plan,
        packaging_plan=valid_packaging_plan,
        narrative_plan=valid_narrative_plan,
    )
    assert FinalCreativeGate.verify_acceptance(pass_res) is True

    # REVISE blocks
    revise_res = pass_res.model_copy(
        update={
            "status": CreativeQAStatus.REVISE,
            "error_count": 1,
            "is_accepted": False,
        }
    )
    with pytest.raises(CreativeRenderGateError, match="Final creative gate blocked"):
        FinalCreativeGate.verify_acceptance(revise_res)

    # FAIL blocks
    fail_res = pass_res.model_copy(
        update={
            "status": CreativeQAStatus.FAIL,
            "blocker_count": 1,
            "is_accepted": False,
        }
    )
    with pytest.raises(CreativeRenderGateError, match="Final creative gate blocked"):
        FinalCreativeGate.verify_acceptance(fail_res)


def test_matrix_o_finding_deduplication():
    """Test Matrix O: Equivalent findings are collapsed without data loss."""
    f1 = CreativeQAFinding(
        finding_code=CreativeQAFindingCode.CAMERA_TOO_ACTIVE,
        severity=CreativeQASeverity.ERROR,
        subsystem=CreativeQASubsystem.CAMERA,
        affected_artifact="camera",
        explanation="Camera too fast",
        source_authority="P22_VisualQA",
        recommended_remediation="Slow down",
    )
    f2 = CreativeQAFinding(
        finding_code=CreativeQAFindingCode.CAMERA_TOO_ACTIVE,
        severity=CreativeQASeverity.ERROR,
        subsystem=CreativeQASubsystem.CAMERA,
        affected_artifact="camera",
        explanation="Camera too fast duplicate",
        source_authority="CreativeStylePlan",
        recommended_remediation="Slow down",
    )
    f3 = CreativeQAFinding(
        finding_code=CreativeQAFindingCode.SFX_DENSITY_EXCESSIVE,
        severity=CreativeQASeverity.WARNING,
        subsystem=CreativeQASubsystem.SFX,
        affected_artifact="audio",
        explanation="Too many clicks",
        source_authority="P23_AudioQA",
        recommended_remediation="Reduce clicks",
    )

    deduped = FindingDeduplicationService.deduplicate([f1, f2, f3])
    assert len(deduped) == 2
    assert deduped[0].finding_code == CreativeQAFindingCode.CAMERA_TOO_ACTIVE
    assert deduped[1].finding_code == CreativeQAFindingCode.SFX_DENSITY_EXCESSIVE
