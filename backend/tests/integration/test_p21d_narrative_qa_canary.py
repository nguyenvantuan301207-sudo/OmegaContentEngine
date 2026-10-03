"""Isolated end-to-end integration canary and P21 full-lineage acceptance for P21-D.

Validates:
Section 20 (Realistic Isolated Canary):
TopicCandidate + ResearchBrief + ContentGenerationRequest + ContentIntent + ChannelDNARevision
→ NarrativeDirector (P21-B)
→ NarrativePlan v1 (PostgreSQL schema027)
→ Retention & Pacing Intelligence (P21-C)
→ NarrativePlan v2 revision (PostgreSQL schema027)
→ Narrative QA & Acceptance (P21-D)
→ PASS
→ NarrativePlanScriptAdapter
→ ScriptVersion

Also validates that an intentionally flawed plan results in REVISE or FAIL and
is blocked from script generation by the script gate.

Section 21 (P21 End-to-End Acceptance):
Full lineage verification:
Storyboard → ScriptVersion → accepted NarrativePlan revision (v2) →
original NarrativePlan lineage (v1) → ContentGenerationRequest → ResearchBrief → ChannelDNARevision.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from omega.application.content_provider import TemplateContentProvider
from omega.application.narrative_director import (
    CandidateSelectionEngine,
    DeterministicNarrativeDirector,
)
from omega.application.narrative_plan_service import (
    NarrativePlanService,
    PostgresNarrativePlanRepository,
)
from omega.application.narrative_planning_service import NarrativePlanningService
from omega.application.narrative_qa_service import NarrativeQAService
from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
from omega.application.retention_pacing_engine import RetentionPacingService
from omega.application.storyboard_engine import StoryboardEngine, StoryboardPlan
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlanStatus,
    NarrativeSectionRole,
)
from omega.domain.narrative_qa import NarrativeQAGateError, NarrativeQAStatus
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ClaimEvidence,
    ContentGenerationRequest,
    NarrativePlan as NarrativePlanModel,
    ResearchBrief,
    ResearchClaim,
    ResearchRequest,
    ResearchSource,
    ScriptVersion,
    TopicCandidate,
)


@pytest.fixture(scope="module")
def sync_db_sessionmaker():
    """Create a synchronous session factory bound to the isolated test database."""
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
    """Provide a clean transactional session for the canary test."""
    session = sync_db_sessionmaker()
    try:
        yield session
    finally:
        session.close()


def test_p21d_isolated_narrative_qa_canary_and_full_lineage(db_session: Session):
    """Execute end-to-end realistic isolated canary and full lineage acceptance for P21-D."""
    # 1. Seed realistic database entities in isolated schema027
    channel_id = uuid.uuid4()
    channel = Channel(
        id=channel_id,
        slug=f"p21d-channel-{uuid.uuid4().hex[:6]}",
        name="P21-D Editorial Quality & QA",
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
        change_reason="P21-D QA baseline DNA",
    )
    db_session.add(dna_rev)

    topic_id = uuid.uuid4()
    topic = TopicCandidate(
        id=topic_id,
        channel_id=channel_id,
        title="Consensus Protocols Under Partitions",
        normalized_title="consensus protocols under partitions",
        summary="How modern consensus architectures resolve distributed network partitions.",
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
        claim_text="Two-phase commit requires synchronous lock acquisition across distributed participants.",
        normalized_claim="two-phase commit requires synchronous lock acquisition across distributed participants",
        confidence_score=0.95,
    )
    db_session.add(claim_1)

    claim_2_id = uuid.uuid4()
    claim_2 = ResearchClaim(
        id=claim_2_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        claim_text="Throughput drops precipitously when coordinator failure forces participants to hold locks.",
        normalized_claim="throughput drops precipitously when coordinator failure forces participants to hold locks",
        confidence_score=0.92,
    )
    db_session.add(claim_2)

    claim_3_id = uuid.uuid4()
    claim_3 = ResearchClaim(
        id=claim_3_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        claim_text="Raft consensus uses leader election and quorum logging to guarantee forward progress.",
        normalized_claim="raft consensus uses leader election and quorum logging to guarantee forward progress",
        confidence_score=0.96,
    )
    db_session.add(claim_3)

    evidence_id = uuid.uuid4()
    ev = ClaimEvidence(
        id=evidence_id,
        claim_id=claim_1_id,
        source_id=source_id,
        excerpt="Two-phase commit blocks if coordinator crashes during commit phase.",
        strength_score=95.0,
    )
    db_session.add(ev)

    brief_id = uuid.uuid4()
    brief = ResearchBrief(
        id=brief_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        version=1,
        title="Consensus Protocols Research Brief",
        summary="Empirical evidence on 2PC vulnerabilities versus Raft quorum consensus.",
        verified_claims=[
            {
                "claim_id": str(claim_1_id),
                "text": claim_1.claim_text,
                "confidence": 0.95,
            },
            {
                "claim_id": str(claim_2_id),
                "text": claim_2.claim_text,
                "confidence": 0.92,
            },
            {
                "claim_id": str(claim_3_id),
                "text": claim_3.claim_text,
                "confidence": 0.96,
            },
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

    # 2. P21-B Narrative Director: generate and persist NarrativePlan v1
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
        "viewer_promise": "You will understand how Raft prevents coordinator blocking.",
        "central_question": "How do distributed systems survive coordinator failures?",
        "core_takeaway": "Quorum logging ensures forward progress without two-phase locks.",
    }
    brief_dict = {
        "id": str(brief_id),
        "title": brief.title,
        "summary": brief.summary,
        "verified_claims": brief.verified_claims,
        "uncertain_claims": [],
        "contradictions": [],
    }

    v1_domain, val_result_v1, _ = planning_service.plan_narrative_for_request(
        content_generation_request_id=content_req_id,
        channel_dna_revision_id=dna_rev_id,
        channel_dna=channel_dna_snapshot,
        research_brief=brief_dict,
        content_intent=content_intent,
        topic_title=topic.title,
        topic_summary=topic.summary,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
        topic_candidate_id=topic_id,
        research_brief_id=brief_id,
        candidate_count=1,
    )
    assert v1_domain.version == 1
    assert v1_domain.is_current is True

    # 3. P21-C Retention & Pacing Intelligence: PacingPlan & optimization
    pacing_service = RetentionPacingService()
    pacing_plan_v1 = pacing_service.audit_plan(v1_domain, channel_dna=channel_dna_snapshot)
    assert pacing_plan_v1.target_duration_seconds == 300

    v2_domain, pacing_plan_v2 = pacing_service.optimize_plan(
        plan=v1_domain,
        plan_service=plan_service,
        channel_dna=channel_dna_snapshot,
    )
    assert v2_domain.version == 2
    assert v2_domain.supersedes_plan_id == v1_domain.id
    assert v2_domain.is_current is True

    # 4. P21-D Narrative QA & Acceptance Layer
    qa_service = NarrativeQAService()
    qa_result = qa_service.evaluate_plan(
        plan=v2_domain,
        research_brief=brief_dict,
        channel_dna=channel_dna_snapshot,
        pacing_plan=pacing_plan_v2,
    )

    # Valid plan must achieve PASS status
    assert qa_result.status == NarrativeQAStatus.PASS
    assert qa_result.is_accepted is True
    assert qa_result.blocker_count == 0
    assert qa_result.error_count == 0
    assert "p21d_qa" in v2_domain.metadata
    assert v2_domain.metadata["p21d_qa"]["status"] == "PASS"

    # Enforce script generation gate
    gate_result = NarrativePlanScriptAdapter.enforce_script_gate(
        plan=v2_domain,
        qa_result=qa_result,
        research_brief=brief_dict,
        channel_dna=channel_dna_snapshot,
    )
    assert gate_result.status == NarrativeQAStatus.PASS

    # 5. Downstream Script Generation Handoff
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

    script_ver = ScriptVersion(
        id=uuid.uuid4(),
        content_request_id=content_req_id,
        version=1,
        is_current=True,
        title=topic.title,
        hook_id=None,
        hook_text="Discover how distributed transactions achieve consensus under network partitions.",
        closing_text="Thank you for watching.",
        cta_text="Subscribe for more distributed systems insights.",
        estimated_word_count=raw_script_data.get("estimated_word_count", 600),
        estimated_duration_seconds=v2_domain.target_duration_seconds,
        qa_status="PASSED",
        narrative_plan_id=v2_domain.id,
    )
    db_session.add(script_ver)
    db_session.commit()

    # 6. P21 End-to-End Lineage Acceptance (Section 21)
    storyboard_engine = StoryboardEngine()
    script_dict = {
        "title": script_ver.title,
        "narrative_plan_id": str(v2_domain.id),
        "narrative_plan_version": v2_domain.version,
        "estimated_duration_seconds": script_ver.estimated_duration_seconds,
        "sections": [
            {
                "heading": "Consensus Under Partitions",
                "statements": [
                    {"statement_text": "Distributed systems must survive unexpected coordinator failures."},
                    {"statement_text": "Raft uses leader election to establish high-throughput consensus."},
                ],
            }
        ],
    }
    storyboard = storyboard_engine.generate_storyboard(script_dict, pacing="BALANCED")
    assert isinstance(storyboard, StoryboardPlan)
    assert storyboard.narrative_plan_id == str(v2_domain.id)
    assert storyboard.narrative_plan_version == 2

    # Verify unbroken lineage through PostgreSQL
    reloaded_script = db_session.scalars(
        select(ScriptVersion).where(ScriptVersion.id == script_ver.id)
    ).first()
    assert reloaded_script is not None
    assert reloaded_script.narrative_plan_id == v2_domain.id
    assert reloaded_script.narrative_plan.version == 2

    v2_reloaded = repo.get(v2_domain.id)
    assert v2_reloaded is not None
    assert v2_reloaded.is_current is True
    assert v2_reloaded.supersedes_plan_id == v1_domain.id
    assert v2_reloaded.content_generation_request_id == content_req_id
    assert v2_reloaded.research_brief_id == brief_id
    assert v2_reloaded.channel_dna_revision_id == dna_rev_id

    v1_reloaded = repo.get(v1_domain.id)
    assert v1_reloaded is not None
    assert v1_reloaded.is_current is False
    assert v1_reloaded.status == NarrativePlanStatus.SUPERSEDED

    # 7. Intentionally flawed plan blocked by Script Gate
    flawed_plan = v2_domain.model_copy(deep=True)
    # Inject orphan payoff and broken ordering
    for s in flawed_plan.sections:
        if s.role == NarrativeSectionRole.PAYOFF:
            s.payoff_reference = "nonexistent_orphan_promise"

    flawed_qa_result = qa_service.evaluate_plan(
        plan=flawed_plan,
        research_brief=brief_dict,
        channel_dna=channel_dna_snapshot,
    )
    assert flawed_qa_result.status == NarrativeQAStatus.FAIL
    assert flawed_qa_result.blocker_count >= 1

    with pytest.raises(NarrativeQAGateError) as exc_info:
        qa_service.enforce_script_gate(flawed_plan, qa_result=flawed_qa_result)

    assert "blocked by script generation gate" in str(exc_info.value)
    assert exc_info.value.qa_result.status == NarrativeQAStatus.FAIL
