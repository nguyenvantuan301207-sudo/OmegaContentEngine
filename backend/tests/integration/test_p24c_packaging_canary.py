"""Realistic Packaging Canary and Cross-Phase Lineage Acceptance Test for P24-C.

Covers:
- Section 38: Realistic Packaging Canary (4+ titles, 1 selected, 2+ thumbnails, physical rendering, description, chapters, keywords, no publishing)
- Section 39: Physical Thumbnail Acceptance (1280x720 RGB PNG, non-blank, valid header, safe zones)
- Section 40: Cross-Phase Lineage (PackagingPlan -> CreativeStylePlan -> ChannelDNARevision -> NarrativePlan -> ScriptVersion)
- Section 41: P24-D Handoff Package (Inspection of QA bundle)
"""

import tempfile
import uuid
from pathlib import Path

import pytest

from omega.application.packaging_engine import PackagingEngine
from omega.domain.channel_dna import (
    ChannelDNA,
    HardConstraints,
    PackagingPreferences,
    SoftPreferences,
)
from omega.domain.creative_style import (
    CreativeStylePlan,
    PackagingStyleHints,
)
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.packaging import (
    PackagingPlan,
    TextOverlayIntent,
    ThumbnailSafeZone,
    TitleStrategy,
)


@pytest.fixture
def realistic_production_bundle() -> dict:
    """Fixture providing a realistic, isolated, end-to-end production context."""
    channel_id = uuid.uuid4()
    content_req_id = uuid.uuid4()

    # 1. Pinned ChannelDNARevision v2
    raw_dna = ChannelDNA(
        hard_constraints=HardConstraints(
            no_fabricated_claims=True,
            no_misleading_clickbait=True,
            no_profanity=True,
            prohibited_vocabulary=("miracle", "shocking_truth"),
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
        soft_preferences=SoftPreferences(
            prefer_concise_cta=True,
            prefer_evidence_graphics=True,
        ),
    )
    resolved_dna = raw_dna.resolve_for_format("LONG", revision_id=channel_id)

    # 2. CreativeStylePlan consuming PackagingStyleHints
    creative_style_plan = CreativeStylePlan(
        channel_dna_revision_id=channel_id,
        content_generation_request_id=content_req_id,
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

    # 3. Accepted NarrativePlan
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
        role=NarrativeSectionRole.DEVELOPMENT,
        objective="Detail extreme ultraviolet high-NA lithography challenges",
        key_information=["0.55 NA EUV systems deployed in Fab 20"],
        target_duration_seconds=150,
    )
    sec4 = NarrativeSection(
        section_order=4,
        role=NarrativeSectionRole.PAYOFF,
        objective="Demonstrate power efficiency and performance benchmark verification",
        key_information=["15% performance uplift at identical power budget"],
        target_duration_seconds=120,
    )
    sec5 = NarrativeSection(
        section_order=5,
        role=NarrativeSectionRole.TAKEAWAY,
        objective="Synthesize competitive timeline for mobile and datacenter silicon",
        key_information=["First commercial volume tape-outs expected Q3 2026"],
        target_duration_seconds=45,
    )

    narrative_plan = NarrativePlan(
        channel_dna_revision_id=channel_id,
        content_generation_request_id=content_req_id,
        metadata={"premise": "2nm Semiconductor Architecture: Nanosheet Breakthrough and Yield Validation"},
        sections=[sec1, sec2, sec3, sec4, sec5],
        format_profile=NarrativeFormatProfile.LONG,
        target_duration_seconds=480,
        estimated_duration_seconds=480,
    )

    # 4. Audio attribution metadata from P23-D
    attribution_manifest = {
        "voice_narration": "Generated by OMEGA Kokoro Local TTS Engine",
        "music_bed": "Atmospheric Ambient by CC-BY Free Music Archive",
    }

    return {
        "channel_id": channel_id,
        "content_req_id": content_req_id,
        "channel_dna": resolved_dna,
        "creative_style_plan": creative_style_plan,
        "narrative_plan": narrative_plan,
        "attribution_manifest": attribution_manifest,
        "final_duration_seconds": 480,
    }


def test_section_38_realistic_packaging_canary(realistic_production_bundle: dict) -> None:
    """Execute realistic isolated packaging canary fulfilling Section 38 requirements."""
    bundle = realistic_production_bundle
    engine = PackagingEngine()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        plan: PackagingPlan = engine.generate_packaging_plan(
            channel_dna=bundle["channel_dna"],
            creative_style_plan=bundle["creative_style_plan"],
            narrative_plan=bundle["narrative_plan"],
            final_duration_seconds=bundle["final_duration_seconds"],
            attribution_manifest=bundle["attribution_manifest"],
            output_dir=out_dir,
        )

        # 1. Title candidates count >= 4
        assert len(plan.title_candidates) >= 4, f"Expected at least 4 title candidates, got {len(plan.title_candidates)}"

        # 2. Selected title is provisionally selected and grounded
        assert plan.selected_title is not None
        assert plan.selected_title.is_grounded is True
        assert len(plan.selected_title.constraint_findings) == 0
        assert plan.selected_title.strategy in (
            TitleStrategy.FACTUAL,
            TitleStrategy.EXPLAINER,
            TitleStrategy.OUTCOME,
        )

        # 3. Thumbnail concepts count >= 2
        assert len(plan.thumbnail_concepts) >= 2, f"Expected at least 2 thumbnail concepts, got {len(plan.thumbnail_concepts)}"
        assert plan.selected_thumbnail is not None
        assert plan.selected_thumbnail.safe_zone == ThumbnailSafeZone.LEFT_WEIGHTED

        # 4. Physical thumbnail artifact exists, non-zero, 1280x720 PNG
        artifact = plan.physical_thumbnail_artifact
        assert artifact is not None
        assert artifact.file_path.is_file()
        assert artifact.width == 1280
        assert artifact.height == 720
        assert artifact.format == "PNG"
        assert artifact.file_size_bytes > 5000

        # 5. Description matches actual content
        assert "2nm Semiconductor Architecture" in plan.description
        assert "Key Takeaways & Findings:" in plan.description
        assert "Attribution & Sources:" in plan.description
        assert "OMEGA Kokoro Local TTS Engine" in plan.description

        # 6. Chapters match timeline and duration
        assert len(plan.chapters) == 5
        assert plan.chapters[0].start_time_seconds == 0
        assert plan.chapters[-1].end_time_seconds <= 480
        for i in range(1, len(plan.chapters)):
            assert plan.chapters[i].start_time_seconds > plan.chapters[i - 1].start_time_seconds

        # 7. Bounded keywords/tags derived from content
        assert 5 <= len(plan.tags) <= 15
        assert "2nm" in [t.lower() for t in plan.tags] or "semiconductor" in [t.lower() for t in plan.tags]

        # 8. Strict non-publishing boundary
        # Confirm no publisher fields mutated or live endpoints called
        assert not hasattr(plan, "publish_status")
        assert not hasattr(plan, "published_at")
        publish_payload = plan.to_p25_publish_payload()
        assert "title" in publish_payload
        assert "description" in publish_payload
        assert "thumbnail_path" in publish_payload
        assert publish_payload["channel_dna_revision_id"] == str(bundle["channel_id"])

        # 9. Coherence check: 0 blocking findings
        assert len([f for f in plan.validation_findings if f.severity in ("ERROR", "BLOCKER")]) == 0


def test_section_39_physical_thumbnail_acceptance(realistic_production_bundle: dict) -> None:
    """Verify Section 39 physical thumbnail standards."""
    bundle = realistic_production_bundle
    engine = PackagingEngine()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        plan = engine.generate_packaging_plan(
            channel_dna=bundle["channel_dna"],
            creative_style_plan=bundle["creative_style_plan"],
            narrative_plan=bundle["narrative_plan"],
            output_dir=out_dir,
        )

        artifact = plan.physical_thumbnail_artifact
        assert artifact is not None
        assert artifact.width == 1280
        assert artifact.height == 720
        assert artifact.format == "PNG"
        assert artifact.file_size_bytes > 0

        # Physical validation passing
        validated = engine.thumbnail_renderer.validate_physical(
            artifact.file_path,
            concept=plan.selected_thumbnail,
        )
        assert validated.content_sha256 == artifact.content_sha256


def test_section_40_cross_phase_lineage(realistic_production_bundle: dict) -> None:
    """Verify Section 40 lineage chain."""
    bundle = realistic_production_bundle
    engine = PackagingEngine()

    plan = engine.generate_packaging_plan(
        channel_dna=bundle["channel_dna"],
        creative_style_plan=bundle["creative_style_plan"],
        narrative_plan=bundle["narrative_plan"],
    )

    # PackagingPlan -> CreativeStylePlan -> ChannelDNARevision -> ContentGenerationRequest -> NarrativePlan
    prov = plan.provenance
    assert prov.channel_dna_revision_id == bundle["channel_id"]
    assert prov.creative_style_plan_id == bundle["creative_style_plan"].plan_id
    assert prov.narrative_plan_id == bundle["narrative_plan"].id
    assert plan.content_generation_request_id == bundle["content_req_id"]
    assert prov.selected_title_id == plan.selected_title.candidate_id
    assert prov.selected_thumbnail_id == plan.selected_thumbnail.concept_id


def test_section_41_p24d_handoff(realistic_production_bundle: dict) -> None:
    """Verify Section 41 complete QA handoff context for P24-D."""
    bundle = realistic_production_bundle
    engine = PackagingEngine()

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)
        plan = engine.generate_packaging_plan(
            channel_dna=bundle["channel_dna"],
            creative_style_plan=bundle["creative_style_plan"],
            narrative_plan=bundle["narrative_plan"],
            output_dir=out_dir,
        )

        qa_pkg = plan.to_p24d_qa_package()
        required_keys = {
            "packaging_plan_id",
            "channel_dna_revision_id",
            "creative_style_plan_id",
            "selected_title",
            "title_candidates",
            "selected_thumbnail",
            "thumbnail_concepts",
            "physical_thumbnail_artifact",
            "description",
            "chapters",
            "tags",
            "attribution_block",
            "validation_findings",
        }
        assert required_keys.issubset(qa_pkg.keys())
        assert qa_pkg["selected_title"]["text"] == plan.selected_title.text
        assert len(qa_pkg["title_candidates"]) >= 4
        assert qa_pkg["physical_thumbnail_artifact"] is not None
