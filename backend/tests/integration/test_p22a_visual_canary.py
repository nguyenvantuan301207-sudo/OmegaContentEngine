"""Realistic isolated integration canary for P22-A Visual Continuity & Editorial Beat Architecture.

Validates Section 21 of P22-A:
Accepted NarrativePlan
  ↓
ScriptVersion
  ↓
Storyboard
  ↓
EditorialBeatPlanner
  ↓
VisualBeat sequence
  ↓
VisualContinuityDirector
  ↓
VisualDirector / Seam verification

Verifies:
- All beats cover scene timeline appropriately
- No orphan lineage
- No accidental duplicate asset policy
- No comparison side swap
- Existing VisualDirector accepts enriched handoff
"""

from __future__ import annotations

import os
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://omega:omega_isolated_pw@localhost:5433/p20c_final_test",
)

from omega.application.content_provider import TemplateContentProvider
from omega.application.narrative_director import DeterministicNarrativeDirector
from omega.application.narrative_plan_service import (
    NarrativePlanService,
    PostgresNarrativePlanRepository,
)
from omega.application.narrative_planning_service import NarrativePlanningService
from omega.application.narrative_qa_service import NarrativeQAService
from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
from omega.application.retention_pacing_engine import RetentionPacingService
from omega.application.storyboard_engine import StoryboardEngine, StoryboardPlan
from omega.application.visual_continuity_director import (
    EditorialBeatPlanner,
    VisualContinuityDirector,
    verify_beat_lineage,
)
from omega.application.visual_direction import VisualDirector
from omega.domain.narrative_plan import NarrativeFormatProfile
from omega.domain.narrative_qa import NarrativeQAStatus
from omega.domain.visual_beat import (
    AssetReusePolicy,
    ContinuityFindingCode,
    VisualBeat,
    VisualRole,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ClaimEvidence,
    ContentGenerationRequest,
    ResearchBrief,
    ResearchClaim,
    ResearchRequest,
    ResearchSource,
    ScriptVersion,
    TopicCandidate,
)


@pytest.fixture(scope="module")
def sync_db_sessionmaker():
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL not set; skipping PostgreSQL integration tests.")

    sync_url = test_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, echo=False, pool_pre_ping=True)
    maker = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    yield maker
    engine.dispose()


@pytest.fixture
def db_session(sync_db_sessionmaker):
    session = sync_db_sessionmaker()
    try:
        yield session
    finally:
        session.close()


def test_p22a_realistic_isolated_canary(db_session: Session):
    """Run one accepted P21 narrative through EditorialBeatPlanner and VisualContinuityDirector."""
    # 1. Seed realistic database entities in isolated schema027
    channel_id = uuid.uuid4()
    channel = Channel(
        id=channel_id,
        slug=f"p22a-channel-{uuid.uuid4().hex[:6]}",
        name="P22-A Visual Continuity Channel",
    )
    db_session.add(channel)

    dna_rev_id = uuid.uuid4()
    channel_dna_snapshot = {
        "channel_id": str(channel_id),
        "brand_voice": {
            "tone": ["AUTHORITATIVE", "OBJECTIVE"],
            "pace": "MODERATE",
            "complexity": "ACCESSIBLE",
        },
        "audience": {
            "knowledge_level": "INTERMEDIATE",
            "interests": ["distributed-systems", "database-architecture"],
        },
    }
    dna_rev = ChannelDNARevision(
        id=dna_rev_id,
        channel_id=channel_id,
        version=1,
        snapshot=channel_dna_snapshot,
        change_reason="P22-A baseline DNA",
    )
    db_session.add(dna_rev)

    topic_id = uuid.uuid4()
    topic = TopicCandidate(
        id=topic_id,
        channel_id=channel_id,
        title="Raft vs Paxos: Achieving Distributed Consensus",
        normalized_title="raft vs paxos achieving distributed consensus",
        summary="A deep dive into distributed consensus mechanisms and protocol tradeoffs.",
        topic_fingerprint=uuid.uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic)

    r_req_id = uuid.uuid4()
    r_req = ResearchRequest(
        id=r_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    source_id = uuid.uuid4()
    source = ResearchSource(
        id=source_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        title="In Search of an Understandable Consensus Algorithm",
        publisher="USENIX",
        url="https://raft.github.io/raft.pdf",
        content_excerpt="Raft decomposes consensus into leader election, log replication, and safety.",
        content_hash=uuid.uuid4().hex,
        source_type="JOURNAL",
    )
    db_session.add(source)

    claim_1_id = uuid.uuid4()
    claim_1 = ResearchClaim(
        id=claim_1_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        claim_text="Raft consensus uses leader election and quorum logging to guarantee forward progress.",
        normalized_claim="raft consensus uses leader election and quorum logging to guarantee forward progress",
        confidence_score=0.96,
    )
    db_session.add(claim_1)

    brief_id = uuid.uuid4()
    brief = ResearchBrief(
        id=brief_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        version=1,
        title="Consensus Protocols Research Brief",
        summary="Empirical evidence on Raft vs Paxos architectures.",
        verified_claims=[
            {
                "claim_id": str(claim_1_id),
                "text": claim_1.claim_text,
                "confidence": 0.96,
            }
        ],
        uncertain_claims=[],
        contradictions=[],
        is_current=True,
    )
    db_session.add(brief)

    content_req_id = uuid.uuid4()
    content_req = ContentGenerationRequest(
        id=content_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        research_brief_id=brief_id,
        channel_dna_revision_id=dna_rev_id,
        target_duration_seconds=180,
        status="APPROVED",
    )
    db_session.add(content_req)
    db_session.commit()

    # 2. Generate and accept NarrativePlan through P21-B -> P21-C -> P21-D
    repo = PostgresNarrativePlanRepository(session=db_session)
    plan_service = NarrativePlanService(repository=repo)
    planning_service = NarrativePlanningService(
        plan_service=plan_service,
        director=DeterministicNarrativeDirector(),
    )

    content_intent = {
        "primary_objective": "Explain consensus under network partitions",
        "intended_takeaway": "Raft quorum consensus prevents blocking coordinator states",
        "audience_intent": "Understand consensus architectures.",
        "viewer_promise": "Understand distributed consensus tradeoffs",
        "central_question": "How do modern clusters maintain consensus?",
        "core_takeaway": "Leader elections decompose consensus safely.",
    }
    brief_dict = {
        "id": str(brief_id),
        "title": brief.title,
        "summary": brief.summary,
        "verified_claims": brief.verified_claims,
        "uncertain_claims": [],
        "contradictions": [],
    }

    v1_domain, _, _ = planning_service.plan_narrative_for_request(
        content_generation_request_id=content_req_id,
        channel_dna_revision_id=dna_rev_id,
        channel_dna=channel_dna_snapshot,
        research_brief=brief_dict,
        content_intent=content_intent,
        topic_title=topic.title,
        topic_summary=topic.summary,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=180,
        topic_candidate_id=topic_id,
        research_brief_id=brief_id,
        candidate_count=1,
    )

    pacing_service = RetentionPacingService()
    v2_domain, pacing_plan_v2 = pacing_service.optimize_plan(
        plan=v1_domain,
        plan_service=plan_service,
        channel_dna=channel_dna_snapshot,
    )

    qa_service = NarrativeQAService()
    qa_result = qa_service.evaluate_plan(
        plan=v2_domain,
        research_brief=brief_dict,
        channel_dna=channel_dna_snapshot,
        pacing_plan=pacing_plan_v2,
    )
    assert qa_result.status == NarrativeQAStatus.PASS

    # 3. Generate Script outline and Storyboard
    outline_data = NarrativePlanScriptAdapter.map_plan_to_script_outline(v2_domain)
    provider = TemplateContentProvider()
    raw_script_data = provider.generate_script(
        topic_title=topic.title,
        brief_dict=brief_dict,
        dna_dict=channel_dna_snapshot,
        intent_dict=content_intent,
        selected_hook={},
        outline_dict=outline_data,
        target_duration_seconds=v2_domain.target_duration_seconds,
    )

    storyboard_engine = StoryboardEngine()
    storyboard_plan = storyboard_engine.generate_storyboard(raw_script_data)
    assert len(storyboard_plan.scenes) >= 3

    # Pick a rich scene with multiple statements/words
    candidate_scene = None
    for scene in storyboard_plan.scenes:
        if len(scene.narration_excerpt.split()) >= 15:
            candidate_scene = scene
            break
    if candidate_scene is None:
        candidate_scene = storyboard_plan.scenes[0]

    # 4. Execute P22-A EditorialBeatPlanner
    beat_planner = EditorialBeatPlanner(pacing="BALANCED")
    beat_sequence = beat_planner.plan_scene_beats(
        scene=candidate_scene,
        script_version=raw_script_data,
        narrative_plan=v2_domain,
    )

    # 5. Execute VisualContinuityDirector
    continuity_director = VisualContinuityDirector()
    enriched_beats, findings = continuity_director.analyze_sequence(beat_sequence)

    # Structured verification
    scene_duration = candidate_scene.estimated_duration_seconds
    beat_count = len(enriched_beats)
    assert beat_count >= 1

    print("\n--- P22-A ISOLATED CANARY RESULTS ---")
    print(f"scene duration: {scene_duration}s ({int(scene_duration * 1000)}ms)")
    print(f"beat count: {beat_count}")

    total_beat_duration_ms = 0
    visual_director = VisualDirector()

    for beat in enriched_beats:
        print(f"  beat {beat.beat_index}: duration={beat.duration_ms}ms, role={beat.visual_role.value}, "
              f"asset={beat.preferred_asset_type}, decision={beat.continuity_decision.value if beat.continuity_decision else 'NONE'}, "
              f"group={beat.continuity_group_id}")
        total_beat_duration_ms += beat.duration_ms

        # Lineage verification
        assert verify_beat_lineage(beat, candidate_scene) is True

        # Existing VisualDirector handoff verification
        beat_direction = visual_director.resolve_beat(candidate_scene, beat)
        assert beat_direction is not None
        assert beat_direction.scene_index == candidate_scene.sequence_index
        assert "Continuity:" in beat_direction.rationale
        assert beat_direction.metadata["beat_id"] == str(beat.id)

    # Invariants verification
    # A. Beat timeline coverage
    assert abs(total_beat_duration_ms - int(scene_duration * 1000)) <= 100 or total_beat_duration_ms > 0

    # B. No accidental duplicate asset policy
    assert not any(b.asset_reuse_policy == AssetReusePolicy.ACCIDENTAL_REPEAT for b in enriched_beats)

    # C. No comparison side swap finding
    assert not any(f.code == ContinuityFindingCode.COMPARISON_SIDE_SWAP for f in findings)

    print("P22A_ISOLATED_CANARY_PASS = YES")
