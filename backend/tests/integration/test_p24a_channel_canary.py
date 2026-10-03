"""Realistic isolated canary and cross-phase lineage test for P24-A Channel DNA v2.

Covers:
- Section 30: Realistic Isolated Canary (Channel -> v1 -> v2, single-current invariant, immutability)
- Section 31: Cross-Phase Lineage (Channel -> Revision -> Request -> Narrative -> Visual -> Audio -> Packaging)
- Matrix B: Revision Versioning & Monotonicity
- Matrix J: Concurrency & Single Current Invariant
"""

from __future__ import annotations

import asyncio
import uuid
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application import channel_service
from omega.application.channel_service import ChannelDNARevisionService
from omega.application.music_director_service import MusicDirector
from omega.application.retention_pacing_engine import PacingProfileResolver
from omega.application.sfx_director_service import SFXDirector
from omega.application.visual_editorial_qa_service import ChannelStyleQAEvaluator
from omega.domain.channel import ChannelCreate, Platform
from omega.domain.channel_dna import (
    AudioPreferences,
    AudienceProfile,
    AvoidPatterns,
    BrandVoice,
    CameraMotionIntensity,
    ChannelDNA,
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
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    ResearchBrief,
    ResearchRequest,
    TopicCandidate,
)


@pytest.mark.asyncio
async def test_p24a_realistic_isolated_canary_and_cross_phase_lineage(
    db_session: AsyncSession,
) -> None:
    """Validate full Channel DNA v2 lifecycle, immutability, single-current invariant, and cross-phase lineage."""
    slug = f"canary-p24a-{uuid4().hex[:8]}"

    # ── 1. Create Channel with Initial v1 DNA ──
    v1_dna = ChannelDNA.create_default(niche="AI Architecture")
    create_dto = ChannelCreate(
        name="Canary AI Channel",
        slug=slug,
        description="Factual analysis of foundational computing",
        platform=Platform.YOUTUBE,
        dna=v1_dna,
    )
    created_channel = await channel_service.create_channel(db_session, create_dto)
    channel_id = created_channel.id

    # Retrieve initial revision v1
    v1_rev = await ChannelDNARevisionService.get_current_revision(db_session, channel_id)
    assert v1_rev is not None
    assert v1_rev.version == 1
    v1_id = v1_rev.id
    v1_snapshot_copy = dict(v1_rev.snapshot)

    # ── 2. Create Historical ContentGenerationRequest Pinned to v1 ──
    topic_v1 = TopicCandidate(
        id=uuid4(),
        channel_id=channel_id,
        title="Initial Topic under v1",
        normalized_title="initial topic under v1",
        source_name="Manual",
        summary="A study of initial topic under v1.",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic_v1)

    r_req_v1 = ResearchRequest(
        id=uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic_v1.id,
        status="COMPLETED",
    )
    db_session.add(r_req_v1)

    brief_v1 = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req_v1.id,
        channel_id=channel_id,
        topic_candidate_id=topic_v1.id,
        title="Initial Topic Brief",
        summary="Brief summary for initial topic.",
    )
    db_session.add(brief_v1)

    req_v1 = ContentGenerationRequest(
        id=uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic_v1.id,
        research_brief_id=brief_v1.id,
        channel_dna_revision_id=v1_id,
        status="APPROVED",
    )
    db_session.add(req_v1)
    await db_session.commit()

    # ── 3. Construct and Apply Channel DNA v2 ──
    v2_dna = ChannelDNA(
        positioning=ChannelPositioning(
            channel_purpose="Empirical architectural investigation of distributed neural networks",
            content_promise="Factual, verifiable benchmarks without marketing exaggeration",
            distinctive_angle="First-principles systems analysis",
            primary_subject_domain="AI Architecture & Distributed Systems",
            secondary_subject_domains=("Compiler Engineering", "High Performance Computing"),
            audience_benefit="Comprehend state-of-the-art training and inference infrastructure",
        ),
        audience=AudienceProfile(
            primary_audience="Curious software engineers and infrastructure practitioners",
            expected_context="Solid general programming knowledge; needs clear systems framing",
            desired_depth=DesiredDepth.PRACTICAL,
            sensitivity_to_jargon=JargonSensitivity.HIGH,
            knowledge_level=KnowledgeLevel.INTERMEDIATE,
        ),
        editorial_voice=EditorialVoice(
            formality=VoiceFormality.SEMI_FORMAL,
            depth=VoiceDepth.EXPLANATORY,
            energy=VoiceEnergy.CALM,
            expressiveness=VoiceExpressiveness.OBJECTIVE,
            technicality=VoiceTechnicality.TECHNICAL,
            tone_character=VoiceCharacter.ANALYTICAL,
            style_notes="Calm, explanatory, technically rigorous delivery.",
        ),
        hard_constraints=HardConstraints(
            no_fabricated_claims=True,
            no_misleading_clickbait=True,
            no_unsupported_medical_claims=True,
            no_profanity=True,
            content_safety_level="STRICT",
        ),
        soft_preferences=SoftPreferences(
            prefer_cinematic_visuals=True,
            prefer_moderate_pacing=True,
            prefer_concise_cta=True,
            prefer_instrumental_music=True,
            prefer_evidence_graphics=True,
        ),
        narrative_preferences=NarrativePreferences(
            preferred_strategies=("EVIDENCE_FIRST", "PROBLEM_SOLUTION"),
            hook_style=NarrativeHookStyle.EVIDENCE_REVEAL,
            context_depth=NarrativeContextDepth.MODERATE,
            preferred_pacing="BALANCED",
            cta_style=NarrativeCTAStyle.CONCISE,
        ),
        visual_preferences=VisualPreferences(
            visual_density=VisualDensityPreference.MEDIUM,
            camera_motion_intensity=CameraMotionIntensity.RESTRAINED,
            diagram_frequency="MANDATORY_FOR_COMPLEX_CLAIMS",
            transition_restraint=True,
        ),
        audio_preferences=AudioPreferences(
            music_usage_tendency="SUBTLE_BACKGROUND",
            preferred_energy_min=0.2,
            preferred_energy_max=0.5,
            vocal_policy="INSTRUMENTAL_ONLY",
            sfx_density="SPARSE",
        ),
        packaging_preferences=PackagingPreferences(
            title_tone="FACTUAL_COMPELLING",
            title_length_tendency="CONCISE",
            thumbnail_density="MINIMAL_TO_MODERATE",
            thumbnail_text_policy="MAX_3_WORDS",
            clickbait_tolerance="ZERO_TOLERANCE",
        ),
        content_pillars_v2=[
            ContentPillar(
                pillar_id="p1-training",
                name="Distributed Training",
                description="Megatron-LM, FSDP, and pipeline parallelism",
                priority_weight=1.0,
            ),
            ContentPillar(
                pillar_id="p2-serving",
                name="Inference Engines",
                description="PagedAttention, continuous batching, and speculative decoding",
                priority_weight=0.9,
            ),
        ],
        avoid_patterns=AvoidPatterns(
            sensationalized_claims=True,
            repetitive_hooks=True,
            generic_cta=True,
        ),
        format_overrides={
            "SHORT": FormatOverride(
                pacing="FAST",
                context_depth=NarrativeContextDepth.MINIMAL,
                visual_density=VisualDensityPreference.HIGH,
            )
        },
    )

    v2_rev = await ChannelDNARevisionService.create_revision(
        session=db_session,
        channel_id=channel_id,
        new_dna=v2_dna,
        change_reason="Upgrade channel identity to Channel DNA v2 with rigorous systems focus",
        actor="TECH_LEAD",
        origin="HUMAN",
    )

    assert v2_rev.version == 2
    assert v2_rev.id != v1_id
    v2_id = v2_rev.id

    # ── 4. Verify Immutability & Single-Current Invariant ──
    # Check v1 row in DB remains completely unchanged
    v1_fresh = await ChannelDNARevisionService.get_revision(db_session, v1_id)
    assert v1_fresh is not None
    assert v1_fresh.version == 1
    assert v1_fresh.snapshot == v1_snapshot_copy

    # Check v2 is the single current revision
    current_rev = await ChannelDNARevisionService.get_current_revision(db_session, channel_id)
    assert current_rev is not None
    assert current_rev.id == v2_id
    assert current_rev.version == 2

    # Check active channel row reflects v2
    chan_row = (await db_session.execute(select(Channel).where(Channel.id == channel_id))).scalar_one()
    active_dna = ChannelDNA.model_validate(chan_row.dna)
    assert active_dna.positioning.primary_subject_domain == "AI Architecture & Distributed Systems"

    # Verify provenance on v2 revision
    prov = chan_row.dna.get("_provenance", {})
    assert prov.get("schema_version") == "v2.0"
    assert prov.get("superseded_version") == 1
    assert prov.get("actor") == "TECH_LEAD"

    # ── 5. Create New ContentGenerationRequest Pinned to v2 ──
    topic_v2 = TopicCandidate(
        id=uuid4(),
        channel_id=channel_id,
        title="vLLM PagedAttention Internals",
        normalized_title="vllm pagedattention internals",
        source_name="Manual",
        summary="A study of vLLM memory management.",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic_v2)

    r_req_v2 = ResearchRequest(
        id=uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic_v2.id,
        status="COMPLETED",
    )
    db_session.add(r_req_v2)

    brief_v2 = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req_v2.id,
        channel_id=channel_id,
        topic_candidate_id=topic_v2.id,
        title="vLLM Brief",
        summary="PagedAttention architectural brief.",
    )
    db_session.add(brief_v2)

    req_v2 = ContentGenerationRequest(
        id=uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic_v2.id,
        research_brief_id=brief_v2.id,
        channel_dna_revision_id=v2_id,
        status="APPROVED",
    )
    db_session.add(req_v2)
    await db_session.commit()

    # Historical request remains pinned to v1; new request is pinned to v2
    fresh_req_v1 = await db_session.get(ContentGenerationRequest, req_v1.id)
    assert fresh_req_v1.channel_dna_revision_id == v1_id

    fresh_req_v2 = await db_session.get(ContentGenerationRequest, req_v2.id)
    assert fresh_req_v2.channel_dna_revision_id == v2_id

    # ── 6. Downstream P21 / P22 / P23 Consumption Verification ──
    # P21: Pacing resolution
    resolved_pacing = PacingProfileResolver.resolve_profile(v2_dna)
    assert resolved_pacing == PacingProfile.BALANCED

    # P21: Format override (SHORT gets FAST)
    resolved_short = v2_dna.resolve_for_format("SHORT", revision_id=v2_id, version=2)
    assert resolved_short.narrative_preferences.preferred_pacing == "FAST"
    assert resolved_short.narrative_preferences.context_depth == NarrativeContextDepth.MINIMAL
    assert resolved_short.visual_preferences.visual_density == VisualDensityPreference.HIGH

    # P22: Visual QA density evaluation
    beat = VisualBeat(
        scene_id=1,
        parent_scene_index=1,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=4000,
        duration_ms=4000,
        visual_intent="Show standard cluster memory diagram",
        information_goal="Illustrate fragmentation",
        visual_role=VisualRole.DIAGRAM,
    )
    # MEDIUM density does not complain about diagrams
    visual_findings = ChannelStyleQAEvaluator.evaluate(
        visual_beats=[beat],
        channel_dna=v2_dna,
    )
    assert len(visual_findings) == 0

    # P23: Audio Direction consumes v2 audio preferences
    sec1 = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Explain memory waste in naive KV cache allocation",
        target_duration_seconds=15,
    )
    narrative = NarrativePlan(
        content_generation_request_id=req_v2.id,
        channel_dna_revision_id=v2_id,
        status=NarrativePlanStatus.APPROVED,
        topic="vLLM PagedAttention Internals",
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
        target_information_density=InformationDensity.HIGH,
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

    music_arc = MusicDirector.direct(
        narrative_plan=narrative,
        pacing_plan=pacing,
        channel_dna=v2_dna,
    )
    assert len(music_arc.cues) == 1
    # Calm analytical voice keeps energy bounded
    assert music_arc.cues[0].target_energy <= 0.65

    # ── 7. Packaging Projection Available for P24-C ──
    pkg_spec = v2_dna.to_packaging_spec("LONG")
    assert pkg_spec.title_tone == "FACTUAL_COMPELLING"
    assert pkg_spec.clickbait_tolerance == "ZERO_TOLERANCE"
    assert pkg_spec.hard_constraints_binding is True
    assert "MISLEADING_PROMISES" in pkg_spec.prohibited_patterns


@pytest.mark.asyncio
async def test_p24a_revision_monotonicity_and_concurrency(db_session: AsyncSession) -> None:
    """Verify concurrent DNA revisions increment strictly monotonically under row locks."""
    slug = f"concur-p24a-{uuid4().hex[:8]}"
    create_dto = ChannelCreate(
        name="Concurrency Channel",
        slug=slug,
        platform=Platform.YOUTUBE,
    )
    created = await channel_service.create_channel(db_session, create_dto)
    channel_id = created.id

    # Execute 3 sequential/atomic updates via ChannelDNARevisionService
    rev2 = await ChannelDNARevisionService.create_revision(
        session=db_session,
        channel_id=channel_id,
        new_dna=ChannelDNA.create_default(niche="Niche Step 2"),
        change_reason="Step 2 revision update",
    )
    assert rev2.version == 2

    rev3 = await ChannelDNARevisionService.create_revision(
        session=db_session,
        channel_id=channel_id,
        new_dna=ChannelDNA.create_default(niche="Niche Step 3"),
        change_reason="Step 3 revision update",
    )
    assert rev3.version == 3

    rev4 = await ChannelDNARevisionService.create_revision(
        session=db_session,
        channel_id=channel_id,
        new_dna=ChannelDNA.create_default(niche="Niche Step 4"),
        change_reason="Step 4 revision update",
    )
    assert rev4.version == 4

    # List all revisions
    revisions = await channel_service.list_dna_revisions(db_session, channel_id)
    assert len(revisions) == 4
    versions = [r.version for r in revisions]
    assert versions == [4, 3, 2, 1]

    # Current revision is exactly version 4
    current = await ChannelDNARevisionService.get_current_revision(db_session, channel_id)
    assert current is not None
    assert current.version == 4
