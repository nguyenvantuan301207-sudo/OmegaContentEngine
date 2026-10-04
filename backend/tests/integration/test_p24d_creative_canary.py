"""Integration canary and lineage verification for P24-D Creative QA & Brand Acceptance.

Covers:
- Section 33: Realistic Creative Acceptance Canary (Valid case -> PASS; Invalid case A: overpromise/misleading title -> BLOCKER/FAIL; Invalid case B: hyperactive camera + aggressive audio against calm ChannelDNA -> REVISE/FAIL).
- Section 34: Full P24 Lineage Verification (ChannelDNARevision -> CreativeStylePlan -> PackagingPlan -> CreativeQAResult -> downstream artifact references).
- Section 35: Physical Acceptance Package (CreativeAcceptancePackage with title, physical thumbnail, description, chapters, metadata, video identity, P24-D QA PASS, no publishing).
- Section 30: Runtime Truth / Acceptance Boundary (failed P24-D blocks acceptance gate and cannot produce accepted artifact).
"""

import tempfile
import uuid
from pathlib import Path

import pytest

from omega.application.creative_qa_service import CreativeQAService, FinalCreativeGate
from omega.application.packaging_engine import PackagingEngine
from omega.domain.channel_dna import (
    AudioPreferences,
    AvoidPatterns,
    CameraMotionIntensity,
    ChannelDNA,
    ChannelPositioning,
    ContentPillar,
    EditorialVoice,
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
from omega.domain.creative_qa import (
    CreativeAcceptancePackage,
    CreativeQAFindingCode,
    CreativeQAResult,
    CreativeQASeverity,
    CreativeQAStatus,
    CreativeRenderGateError,
)
from omega.domain.creative_style import (
    AudioStyleDirection,
    CameraStyleDirection,
    CameraStyleIntent,
    CreativeArc,
    CreativeArcSection,
    CreativeStylePlan,
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
from omega.domain.packaging import PackagingPlan


@pytest.fixture
def realistic_p24_environment() -> dict:
    """Fixture providing a complete, realistic, isolated P24 environment."""
    channel_id = uuid.uuid4()
    content_req_id = uuid.uuid4()
    video_artifact_id = uuid.uuid4()

    pillar = ContentPillar(
        pillar_id="applied_science",
        name="Applied Science & Engineering",
        description="Rigorous engineering breakdowns and empirical verifications",
    )

    channel_dna = ChannelDNA(
        content_pillars_v2=(pillar,),
        positioning=ChannelPositioning(
            channel_purpose="Demystify cutting-edge engineering for technical professionals.",
            content_promise="Rigorous, factual, and deeply educational explorations without sensationalism.",
            distinctive_angle="First-principles breakdowns backed by academic papers and primary evidence.",
            primary_subject_domain="Applied Sciences",
            secondary_subject_domains=("Computing", "Physics"),
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
            prohibited_vocabulary=("miracle", "shocking_truth"),
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
            payoff_style=NarrativePayoffStyle.CONCRETE_EVIDENCE,
        ),
        visual_preferences=VisualPreferences(
            visual_density=VisualDensityPreference.MEDIUM,
            camera_motion_intensity=CameraMotionIntensity.RESTRAINED,
        ),
        audio_preferences=AudioPreferences(
            music_usage_tendency="SUBTLE_BACKGROUND",
            preferred_energy_min=0.15,
            preferred_energy_max=0.50,
            vocal_policy="INSTRUMENTAL_ONLY",
            sfx_density="SPARSE",
            audio_restraint=True,
            sonic_character="Instrumental ambient",
        ),
        packaging_preferences=PackagingPreferences(
            title_tone="FACTUAL_COMPELLING",
            title_length_tendency="CONCISE",
            thumbnail_density="MINIMAL_TO_MODERATE",
            thumbnail_text_policy="MAX_3_WORDS",
            description_style="STRUCTURED_OUTLINE",
            chapter_style="SECTION_BASED",
            metadata_voice="OBJECTIVE",
            clickbait_tolerance="ZERO_TOLERANCE",
        ),
        avoid_patterns=AvoidPatterns(
            overactive_camera_motion=True,
            hyperbolic_hooks=True,
        ),
    )
    resolved_dna = channel_dna.resolve_for_format("LONG", revision_id=channel_id)

    # CreativeStylePlan
    arc_sec1 = CreativeArcSection(
        section_role="HOOK",
        section_order=1,
        target_energy=0.4,
        visual_focus="Wafer cross-section diagram",
        camera_motion="STATIC_RESTRAINED",
        audio_intent="Sparse ambient pads",
        justified_escalation=False,
        rationale="Sober evidence hook",
    )
    arc_sec2 = CreativeArcSection(
        section_role="CONTEXT",
        section_order=2,
        target_energy=0.3,
        visual_focus="Transistor physics schematic",
        camera_motion="STATIC_RESTRAINED",
        audio_intent="Minimal backdrop",
        justified_escalation=False,
        rationale="Explanatory context",
    )
    arc_sec3 = CreativeArcSection(
        section_role="PAYOFF",
        section_order=3,
        target_energy=0.4,
        visual_focus="Benchmark yield graph",
        camera_motion="STATIC_RESTRAINED",
        audio_intent="Measured resolution",
        justified_escalation=False,
        rationale="Conclusion and verification",
    )

    style_plan = CreativeStylePlan(
        channel_dna_revision_id=channel_id,
        content_generation_request_id=content_req_id,
        content_pillar_id="applied_science",
        camera_style=CameraStyleDirection(
            camera_style_intent=CameraStyleIntent.STATIC_RESTRAINED,
            max_camera_energy=0.4,
        ),
        audio_style=AudioStyleDirection(
            music_tendency="SUBTLE_BACKGROUND",
            target_music_energy=0.35,
            sfx_density="SPARSE",
            sfx_prominence="SUBTLE",
        ),
        visual_style=VisualStyleDirection(
            visual_density="MEDIUM",
            b_roll_tendency="MINIMAL",
        ),
        narrative_style=NarrativeStyleDirection(hook_intensity=HookIntensity.BALANCED),
        creative_arc=CreativeArc(arc_id=uuid.uuid4(), sections=[arc_sec1, arc_sec2, arc_sec3]),
        style_rationale=(
            StyleRationaleEntry(
                aspect="visual_density",
                decision="MEDIUM",
                contributing_source="ChannelDNA",
                explanation="Aligns with explanatory depth",
            ),
        ),
        packaging_hints=PackagingStyleHints(
            title_tone="FACTUAL_COMPELLING",
            title_restraint="FACTUAL_RESTRAINED",
            title_length_tendency="CONCISE",
            thumbnail_density="MINIMAL_TO_MODERATE",
            thumbnail_text_policy="MAX_3_WORDS",
            thumbnail_emotional_intensity="MEASURED",
            description_voice="STRUCTURED_OUTLINE",
            metadata_voice="OBJECTIVE",
            chapter_style="SECTION_BASED",
            clickbait_tolerance="ZERO_TOLERANCE",
        ),
    )

    # NarrativePlan
    sec1 = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Introduce 2nm semiconductor mass production milestone",
        key_information=["TSMC achieves commercial 2nm wafer yields exceeding 65%"],
        target_duration_seconds=45,
    )
    sec2 = NarrativeSection(
        section_order=2,
        role=NarrativeSectionRole.CONTEXT,
        objective="Explain Gate-All-Around nanosheet transistor architecture",
        key_information=["Replaces FinFET with 4-layer stacked nanosheets", "Leakage reduced by 30%"],
        target_duration_seconds=120,
    )
    sec3 = NarrativeSection(
        section_order=3,
        role=NarrativeSectionRole.PAYOFF,
        objective="Demonstrate power efficiency and performance benchmark verification",
        key_information=["15% performance uplift at identical power budget"],
        target_duration_seconds=120,
    )

    narrative_plan = NarrativePlan(
        channel_dna_revision_id=channel_id,
        content_generation_request_id=content_req_id,
        metadata={"premise": "2nm Semiconductor Architecture: Nanosheet Breakthrough and Yield Validation"},
        sections=[sec1, sec2, sec3],
        format_profile=NarrativeFormatProfile.LONG,
        target_duration_seconds=285,
        estimated_duration_seconds=285,
    )

    return {
        "channel_id": channel_id,
        "content_req_id": content_req_id,
        "video_artifact_id": video_artifact_id,
        "channel_dna": resolved_dna,
        "creative_style_plan": style_plan,
        "narrative_plan": narrative_plan,
    }


def test_section_33_realistic_canary_valid_case_passes(realistic_p24_environment: dict) -> None:
    """Verify Section 33 valid case: calm, technically rigorous channel with coherent packaging PASSES."""
    env = realistic_p24_environment
    packaging_engine = PackagingEngine()
    qa_service = CreativeQAService()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        packaging_plan: PackagingPlan = packaging_engine.generate_packaging_plan(
            channel_dna=env["channel_dna"],
            creative_style_plan=env["creative_style_plan"],
            narrative_plan=env["narrative_plan"],
            final_duration_seconds=285,
            attribution_manifest={"voice": "Kokoro TTS", "music": "Ambient CC-BY"},
            output_dir=out_dir,
        )

        qa_result: CreativeQAResult = qa_service.evaluate_creative_package(
            channel_dna=env["channel_dna"],
            pinned_revision_id=env["channel_id"],
            style_plan=env["creative_style_plan"],
            packaging_plan=packaging_plan,
            narrative_plan=env["narrative_plan"],
            video_artifact_id=env["video_artifact_id"],
        )

        # Result status must be PASS
        assert qa_result.status == CreativeQAStatus.PASS
        assert qa_result.blocker_count == 0
        assert qa_result.error_count == 0
        assert qa_result.is_accepted is True

        # Gate verification
        assert FinalCreativeGate.is_eligible_for_downstream_qa(qa_result) is True
        FinalCreativeGate.verify_acceptance(qa_result)  # Must not raise


def test_section_33_realistic_canary_invalid_case_a_misleading_overpromise(realistic_p24_environment: dict) -> None:
    """Verify Section 33 invalid case A: misleading title/overpromise triggers BLOCKER/FAIL and blocks gate."""
    env = realistic_p24_environment
    packaging_engine = PackagingEngine()
    qa_service = CreativeQAService()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        packaging_plan = packaging_engine.generate_packaging_plan(
            channel_dna=env["channel_dna"],
            creative_style_plan=env["creative_style_plan"],
            narrative_plan=env["narrative_plan"],
            output_dir=out_dir,
        )

        # Mutate selected title to an overpromising / misleading claim containing prohibited vocabulary
        mutated_title = packaging_plan.selected_title.model_copy(
            update={
                "text": "This Miracle Discovery Will Shock You And Change Everything Today",
                "is_grounded": False,
            }
        )
        mutated_packaging_plan = packaging_plan.model_copy(update={"selected_title": mutated_title})

        qa_result = qa_service.evaluate_creative_package(
            channel_dna=env["channel_dna"],
            pinned_revision_id=env["channel_id"],
            style_plan=env["creative_style_plan"],
            packaging_plan=mutated_packaging_plan,
            narrative_plan=env["narrative_plan"],
            video_artifact_id=env["video_artifact_id"],
        )

        assert qa_result.status == CreativeQAStatus.FAIL
        assert qa_result.blocker_count >= 1

        finding_codes = [f.finding_code for f in qa_result.findings]
        assert (
            CreativeQAFindingCode.OVERPROMISE in finding_codes
            or CreativeQAFindingCode.PROHIBITED_PHRASE_DETECTED in finding_codes
            or CreativeQAFindingCode.TITLE_UNGROUNDED in finding_codes
        )

        # Gate must block
        assert FinalCreativeGate.is_eligible_for_downstream_qa(qa_result) is False
        with pytest.raises(CreativeRenderGateError) as exc_info:
            FinalCreativeGate.verify_acceptance(qa_result)
        assert exc_info.value.qa_result.status == CreativeQAStatus.FAIL


def test_section_33_realistic_canary_invalid_case_b_hyperactive_cross_modal(realistic_p24_environment: dict) -> None:
    """Verify Section 33 invalid case B: hyperactive camera + aggressive audio against calm DNA triggers REVISE/FAIL and blocks gate."""
    env = realistic_p24_environment
    packaging_engine = PackagingEngine()
    qa_service = CreativeQAService()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        packaging_plan = packaging_engine.generate_packaging_plan(
            channel_dna=env["channel_dna"],
            creative_style_plan=env["creative_style_plan"],
            narrative_plan=env["narrative_plan"],
            output_dir=out_dir,
        )

        # Conflict: calm analytical narration with hyperactive camera and aggressive audio
        hyperactive_style_plan = env["creative_style_plan"].model_copy(
            update={
                "camera_style": CameraStyleDirection(
                    camera_style_intent=CameraStyleIntent.ENERGETIC_MOTION,
                    max_camera_energy=0.95,
                ),
                "audio_style": AudioStyleDirection(
                    music_tendency="PROMINENT_LEAD",
                    target_music_energy=0.9,
                    sfx_density="CONSTANT",
                    sfx_prominence="PROMINENT",
                ),
            }
        )

        qa_result = qa_service.evaluate_creative_package(
            channel_dna=env["channel_dna"],
            pinned_revision_id=env["channel_id"],
            style_plan=hyperactive_style_plan,
            packaging_plan=packaging_plan,
            narrative_plan=env["narrative_plan"],
            video_artifact_id=env["video_artifact_id"],
        )

        # Must be REVISE or FAIL
        assert qa_result.status in (CreativeQAStatus.REVISE, CreativeQAStatus.FAIL)
        assert qa_result.error_count >= 1 or qa_result.blocker_count >= 1

        finding_codes = [f.finding_code for f in qa_result.findings]
        assert (
            CreativeQAFindingCode.CALM_NARRATION_HYPERACTIVE_PRODUCTION in finding_codes
            or CreativeQAFindingCode.CAMERA_TOO_ACTIVE in finding_codes
            or CreativeQAFindingCode.AUDIO_ENERGY_TOO_HIGH in finding_codes
        )

        # Gate must block
        assert FinalCreativeGate.is_eligible_for_downstream_qa(qa_result) is False
        with pytest.raises(CreativeRenderGateError):
            FinalCreativeGate.verify_acceptance(qa_result)


def test_section_34_full_p24_lineage(realistic_p24_environment: dict) -> None:
    """Verify Section 34: full lineage traceable across ChannelDNA -> CreativeStylePlan -> PackagingPlan -> CreativeQAResult."""
    env = realistic_p24_environment
    packaging_engine = PackagingEngine()
    qa_service = CreativeQAService()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        packaging_plan = packaging_engine.generate_packaging_plan(
            channel_dna=env["channel_dna"],
            creative_style_plan=env["creative_style_plan"],
            narrative_plan=env["narrative_plan"],
            output_dir=out_dir,
        )

        qa_result = qa_service.evaluate_creative_package(
            channel_dna=env["channel_dna"],
            pinned_revision_id=env["channel_id"],
            style_plan=env["creative_style_plan"],
            packaging_plan=packaging_plan,
            narrative_plan=env["narrative_plan"],
            video_artifact_id=env["video_artifact_id"],
        )

        prov = qa_result.provenance
        assert prov.channel_dna_revision_id == env["channel_id"]
        assert prov.creative_style_plan_id == env["creative_style_plan"].plan_id
        assert prov.packaging_plan_id == packaging_plan.packaging_plan_id
        assert prov.narrative_plan_id == env["narrative_plan"].id
        assert prov.video_artifact_id == env["video_artifact_id"]


def test_section_35_physical_acceptance_package(realistic_p24_environment: dict) -> None:
    """Verify Section 35: build a bounded physical acceptance package and verify non-publishing boundary."""
    env = realistic_p24_environment
    packaging_engine = PackagingEngine()
    qa_service = CreativeQAService()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        packaging_plan = packaging_engine.generate_packaging_plan(
            channel_dna=env["channel_dna"],
            creative_style_plan=env["creative_style_plan"],
            narrative_plan=env["narrative_plan"],
            attribution_manifest={"voice": "Kokoro TTS", "music": "Ambient CC-BY"},
            output_dir=out_dir,
        )

        qa_result = qa_service.evaluate_creative_package(
            channel_dna=env["channel_dna"],
            pinned_revision_id=env["channel_id"],
            style_plan=env["creative_style_plan"],
            packaging_plan=packaging_plan,
            narrative_plan=env["narrative_plan"],
            video_artifact_id=env["video_artifact_id"],
        )

        assert qa_result.status == CreativeQAStatus.PASS

        # Assemble physical acceptance package
        acceptance_pkg = qa_service.assemble_acceptance_package(
            packaging_plan=packaging_plan,
            qa_result=qa_result,
            video_artifact_id=str(env["video_artifact_id"]),
        )

        # Verify physical integrity
        assert acceptance_pkg.title == packaging_plan.selected_title.text
        assert Path(acceptance_pkg.thumbnail_path).is_file()
        assert Path(acceptance_pkg.thumbnail_path).suffix.lower() == ".png"
        assert len(acceptance_pkg.chapters) > 0
        assert "tags" in acceptance_pkg.metadata
        assert acceptance_pkg.video_artifact_id == str(env["video_artifact_id"])
        assert acceptance_pkg.qa_result.status == CreativeQAStatus.PASS

        # Strict non-publishing boundary: verify no publication fields or endpoints
        assert not hasattr(acceptance_pkg, "publish_status")
        assert not hasattr(acceptance_pkg, "published_at")


def test_section_30_runtime_truth_acceptance_boundary(realistic_p24_environment: dict) -> None:
    """Verify Section 30: failed P24-D QA strictly blocks downstream acceptance and cannot produce accepted truth."""
    env = realistic_p24_environment
    packaging_engine = PackagingEngine()
    qa_service = CreativeQAService()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        packaging_plan = packaging_engine.generate_packaging_plan(
            channel_dna=env["channel_dna"],
            creative_style_plan=env["creative_style_plan"],
            narrative_plan=env["narrative_plan"],
            output_dir=out_dir,
        )

        # Introduce a hard constraint violation: prohibited vocabulary
        mutated_title = packaging_plan.selected_title.model_copy(
            update={"text": "The Shocking Truth About 2nm Transistors"}
        )
        mutated_packaging_plan = packaging_plan.model_copy(update={"selected_title": mutated_title})

        qa_result = qa_service.evaluate_creative_package(
            channel_dna=env["channel_dna"],
            pinned_revision_id=env["channel_id"],
            style_plan=env["creative_style_plan"],
            packaging_plan=mutated_packaging_plan,
            narrative_plan=env["narrative_plan"],
            video_artifact_id=env["video_artifact_id"],
        )

        # Gate check: strictly blocks
        assert FinalCreativeGate.is_eligible_for_downstream_qa(qa_result) is False
        with pytest.raises(CreativeRenderGateError) as exc_info:
            FinalCreativeGate.verify_acceptance(qa_result)

        assert exc_info.value.qa_result.status == CreativeQAStatus.FAIL
        assert exc_info.value.qa_result.blocker_count >= 1
