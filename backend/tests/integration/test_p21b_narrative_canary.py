"""Isolated end-to-end integration canary for P21-B Narrative Director.

Validates Section 19:
realistic TopicCandidate
+
ResearchBrief
+
ContentGenerationRequest
+
ContentIntent
+
ChannelDNARevision
↓
NarrativeDirector
↓
candidate plans
↓
selected valid NarrativePlan
↓
PostgreSQL persistence at schema027
↓
NarrativePlanScriptAdapter
↓
ScriptVersion lineage
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
from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
from omega.domain.narrative_plan import (
    GroundingType,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeSectionRole,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ClaimEvidence,
    ContentGenerationRequest,
    NarrativeGroundingCitation as NarrativeGroundingCitationModel,
    NarrativePlan as NarrativePlanModel,
    NarrativeSection as NarrativeSectionModel,
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


def test_p21b_isolated_narrative_director_canary(db_session: Session):
    """Execute end-to-end realistic isolated canary for P21-B Narrative Director."""
    # 1. Seed realistic hierarchy
    channel_id = uuid.uuid4()
    channel = Channel(
        id=channel_id,
        slug=f"quantum-channel-{uuid.uuid4().hex[:6]}",
        name="Quantum Computing Insights",
    )
    db_session.add(channel)

    dna_rev_id = uuid.uuid4()
    channel_dna_snapshot = {
        "channel_id": str(channel_id),
        "brand_voice": {
            "tone": "AUTHORITATIVE",
            "pace": "MEASURED",
            "complexity": "ADVANCED",
        },
        "narrative_preferences": {
            "default_strategy": "HOW_IT_WORKS",
            "preferred_format": "MEDIUM",
        },
    }
    dna_rev = ChannelDNARevision(
        id=dna_rev_id,
        channel_id=channel_id,
        version=1,
        snapshot=channel_dna_snapshot,
        change_reason="Initial Quantum DNA",
    )
    db_session.add(dna_rev)

    topic_id = uuid.uuid4()
    topic = TopicCandidate(
        id=topic_id,
        channel_id=channel_id,
        title="Superconducting Transmon Qubits Architecture",
        normalized_title="superconducting transmon qubits architecture",
        source_name="ArXiv",
        summary="A comprehensive technical breakdown of Josephson junction non-linear inductance and decoherence mitigation in transmon qubits.",
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

    brief_id = uuid.uuid4()
    brief = ResearchBrief(
        id=brief_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        title="Transmon Qubits Physical Mechanics",
        summary="Empirical evidence on transmon anharmonicity, microwave drive control, and dilution refrigerator thermal shields.",
    )
    db_session.add(brief)

    source_id = uuid.uuid4()
    source = ResearchSource(
        id=source_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        title="Physical Review Letters 2007",
        publisher="APS",
        url="https://doi.org/10.1103/PhysRevA.76.042319",
        content_excerpt="Charge noise insensitivity in the transmon regime.",
        content_hash=uuid.uuid4().hex,
        source_type="JOURNAL",
    )
    db_session.add(source)

    claim1_id = uuid.uuid4()
    claim1 = ResearchClaim(
        id=claim1_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        claim_text="Shunting the Josephson junction with a large capacitor suppresses charge noise exponentially.",
        normalized_claim="shunting the josephson junction with a large capacitor suppresses charge noise exponentially",
        confidence_score=0.99,
    )
    db_session.add(claim1)

    ev1_id = uuid.uuid4()
    ev1 = ClaimEvidence(
        id=ev1_id,
        claim_id=claim1_id,
        source_id=source_id,
        excerpt="The ratio of Josephson energy EJ to charging energy EC suppresses charge dispersion.",
        strength_score=98.5,
    )
    db_session.add(ev1)

    content_req_id = uuid.uuid4()
    content_req = ContentGenerationRequest(
        id=content_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        research_brief_id=brief_id,
        channel_dna_revision_id=dna_rev_id,
        status="APPROVED",
    )
    db_session.add(content_req)
    db_session.commit()

    # Formulate domain objects for planning
    brief_dict = {
        "id": str(brief_id),
        "title": brief.title,
        "summary": brief.summary,
        "verified_claims": [
            {
                "claim_id": str(claim1_id),
                "claim_text": claim1.claim_text,
                "evidence": [
                    {
                        "evidence_id": str(ev1_id),
                        "source_id": str(source_id),
                        "excerpt": ev1.excerpt,
                    }
                ],
            }
        ],
        "uncertain_claims": [],
        "contradictions": [],
    }

    content_intent = {
        "primary_goal": "Explain how transmon qubits achieve noise suppression through anharmonic oscillator mechanics.",
        "audience_intent": "Understand transmon superconducting circuits.",
        "viewer_promise": "You will understand exactly how transmon qubits isolate quantum states from thermal noise.",
        "central_question": "How do transmon qubits overcome destructive charge noise?",
        "core_takeaway": "Large capacitive shunts exponentially flatten charge dispersion bands.",
    }

    # 2. Run Narrative Planning Service with PostgreSQL Persistence at revision 027
    postgres_repo = PostgresNarrativePlanRepository(session=db_session)
    plan_service = NarrativePlanService(repository=postgres_repo)
    planning_service = NarrativePlanningService(
        plan_service=plan_service,
        director=DeterministicNarrativeDirector(),
    )

    plan, val_result, selection_rationale = planning_service.plan_narrative_for_request(
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
        candidate_count=3,
    )

    assert plan is not None
    assert val_result.is_valid
    assert plan.status == NarrativePlanStatus.VALIDATED
    assert plan.is_current is True

    # 3. Verify PostgreSQL Persistence at schema027
    persisted_model = db_session.execute(
        select(NarrativePlanModel).where(NarrativePlanModel.id == plan.id)
    ).scalar_one()

    assert persisted_model is not None
    assert persisted_model.content_generation_request_id == content_req_id
    assert persisted_model.channel_dna_revision_id == dna_rev_id
    assert persisted_model.topic_candidate_id == topic_id
    assert persisted_model.research_brief_id == brief_id
    assert persisted_model.version == 1
    assert persisted_model.is_current is True
    assert len(persisted_model.sections) == len(plan.sections)

    # 4. NarrativePlanScriptAdapter Handoff
    adapter = NarrativePlanScriptAdapter()
    script_context = adapter.prepare_generation_context(
        content_generation_request_id=content_req_id,
        target_duration_seconds=300,
        channel_dna=channel_dna_snapshot,
        research_brief=brief_dict,
        content_intent=content_intent,
        narrative_plan=plan,
    )
    assert script_context.narrative_plan.id == plan.id
    assert len(script_context.narrative_plan.sections) == len(plan.sections)

    outline_dict = adapter.map_plan_to_script_outline(plan)
    assert outline_dict["narrative_plan_id"] == str(plan.id)

    # 5. Generate Script and Pin ScriptVersion Lineage
    provider = TemplateContentProvider()
    selected_hook = {
        "id": str(uuid.uuid4()),
        "hook_variant_index": 0,
        "hook_text": "How do transmon circuits survive destructive thermal noise in quantum computing?",
        "hook_type": "PROVOCATIVE_QUESTION",
        "selected": True,
        "citations": [],
    }

    script_data = provider.generate_script(
        topic_title=topic.title,
        brief_dict=brief_dict,
        dna_dict=channel_dna_snapshot,
        intent_dict=content_intent,
        selected_hook=selected_hook,
        outline_dict=outline_dict,
        target_duration_seconds=300,
    )

    script_version_id = uuid.uuid4()
    script_model = ScriptVersion(
        id=script_version_id,
        content_request_id=content_req_id,
        version=1,
        is_current=True,
        title=script_data["title"],
        hook_id=None,
        hook_text=selected_hook["hook_text"],
        closing_text=script_data.get("closing_text", "Thank you for watching."),
        cta_text=script_data.get("cta_text", "Subscribe for more."),
        estimated_word_count=script_data.get("estimated_word_count", 600),
        estimated_duration_seconds=script_data.get("estimated_duration_seconds", 300),
        qa_status="PASSED",
        narrative_plan_id=plan.id,
    )
    db_session.add(script_model)
    db_session.commit()

    # 6. Verify Lineage in PostgreSQL
    verified_script = db_session.execute(
        select(ScriptVersion).where(ScriptVersion.id == script_version_id)
    ).scalar_one()

    assert verified_script.narrative_plan_id == plan.id
    assert verified_script.narrative_plan.id == plan.id
    assert verified_script.narrative_plan.content_generation_request_id == content_req_id

    # Gather Canary Metrics
    selected_strategy = plan.metadata.get("selection_candidate_strategy") or plan.metadata.get("strategy")
    section_roles = [s.role.value for s in plan.sections]
    grounding_coverage_count = sum(len(s.grounding_references) for s in plan.sections)
    promise_count = sum(1 for s in plan.sections if s.promise_id)
    payoff_count = sum(1 for s in plan.sections if s.payoff_reference)

    assert grounding_coverage_count >= 1
    assert promise_count == 1
    assert payoff_count == 1

    # Report structure for Canary
    print("\n" + "=" * 70)
    print("P21-B ISOLATED CANARY EXECUTION REPORT")
    print("=" * 70)
    print(f"selected strategy:             {selected_strategy}")
    print(f"section roles:                 {' -> '.join(section_roles)}")
    print(f"grounding coverage count:      {grounding_coverage_count}")
    print(f"promise/payoff link count:     {promise_count} promise, {payoff_count} payoff")
    print(f"NarrativePlan ID:              {plan.id}")
    print(f"ScriptVersion lineage result:  ScriptVersion {script_version_id} -> NarrativePlan {plan.id} -> Request {content_req_id}")
    print("=" * 70 + "\n")
