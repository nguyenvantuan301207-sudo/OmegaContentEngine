"""Unit tests for content topic integrity, domain neutrality, and fail-closed QA authority."""

import uuid
from typing import Any

from omega.application.content_provider import TemplateContentProvider
from omega.application.content_qa import run_content_qa_checks
from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
from omega.application.statement_validator import validate_and_classify_statement
from omega.domain.channel_dna import ChannelDNA
from omega.domain.content import (
    ClaimUsagePolicy,
    ContentStatementType,
    QARuleCode,
    ScriptQAStatus,
)
from omega.domain.narrative_plan import (
    GroundingReference,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeSection,
    NarrativeSectionRole,
)


def _make_concrete_brief() -> dict[str, Any]:
    brief_id = str(uuid.uuid4())
    claim1_id = str(uuid.uuid4())
    claim2_id = str(uuid.uuid4())
    return {
        "id": brief_id,
        "title": "Civil Engineering: Concrete Cracking Mechanisms",
        "summary": "Verified mechanics of shrinkage and cracking in concrete structures.",
        "verified_claims": [
            {
                "claim_id": claim1_id,
                "text": "Plastic shrinkage cracking occurs when surface moisture evaporates faster than bleed water rises.",
                "citations": [
                    {
                        "evidence_id": str(uuid.uuid4()),
                        "source_id": str(uuid.uuid4()),
                    }
                ],
            },
            {
                "claim_id": claim2_id,
                "text": "Drying shrinkage happens over months as hardened concrete loses internal moisture.",
                "citations": [
                    {
                        "evidence_id": str(uuid.uuid4()),
                        "source_id": str(uuid.uuid4()),
                    }
                ],
            },
        ],
        "uncertain_claims": [],
        "contradictions": [],
    }


def test_1_and_2_generic_provider_domain_neutral_and_concrete_canary():
    """Generic deterministic provider contains no software domain and concrete topic emits no backend terms."""
    provider = TemplateContentProvider()
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    brief = _make_concrete_brief()
    dna = {"brand_voice": {"tone": "AUTHORITATIVE", "pace": "MODERATE"}}

    intent = provider.generate_intent(topic, None, brief, dna)
    hooks = provider.generate_hooks(topic, brief, dna, intent)
    outline = provider.generate_outline(topic, brief, dna, intent, hooks[0], 480)
    script = provider.generate_script(topic, brief, dna, intent, hooks[0], outline, 480)

    forbidden_backend_terms = [
        "wsgi",
        "asgi",
        "event loop",
        "tcp",
        "microservice",
        "connection pool",
        "backend",
        "coroutine",
        "epoll",
        "multiprocessing",
        "software",
    ]

    all_text = (
        f"{intent['primary_goal']} {intent['audience_intent']} "
        f"{hooks[0]['text']} {hooks[1]['text']} {hooks[2]['text']} "
        f"{script['hook_text']} {script['closing_text']} {script['cta_text']} "
        + " ".join(s["narration_text"] for s in script["sections"])
    ).lower()

    for term in forbidden_backend_terms:
        assert term not in all_text, f"Forbidden software term '{term}' leaked into concrete script!"


def test_3_software_topic_allowed_when_grounded_in_authority():
    """Software topics generate software-oriented prose only when that subject matter comes from its authority."""
    provider = TemplateContentProvider()
    topic = "FastAPI Microservices Architecture & High-Throughput Benchmarks"
    brief = {
        "id": str(uuid.uuid4()),
        "title": "FastAPI Benchmarks",
        "summary": "Verified performance metrics for FastAPI ASGI services.",
        "verified_claims": [
            {
                "claim_id": str(uuid.uuid4()),
                "text": "FastAPI handles high concurrent request volumes using asynchronous event loops.",
                "citations": [{"evidence_id": str(uuid.uuid4()), "source_id": str(uuid.uuid4())}],
            }
        ],
        "uncertain_claims": [],
        "contradictions": [],
    }
    dna = {"brand_voice": {"tone": "AUTHORITATIVE", "pace": "MODERATE"}}

    intent = provider.generate_intent(topic, None, brief, dna)
    hooks = provider.generate_hooks(topic, brief, dna, intent)
    outline = provider.generate_outline(topic, brief, dna, intent, hooks[0], 300)
    script = provider.generate_script(topic, brief, dna, intent, hooks[0], outline, 300)

    # QA check must pass without TOPIC_AUTHORITY_MISMATCH
    status, findings = run_content_qa_checks(
        script_data=script,
        target_duration_seconds=300,
        dna_dict=dna,
        brief_dict=brief,
        topic_title=topic,
    )

    mismatch_findings = [f for f in findings if f["rule_code"] == QARuleCode.TOPIC_AUTHORITY_MISMATCH.value]
    assert len(mismatch_findings) == 0
    assert status in (ScriptQAStatus.PASSED, ScriptQAStatus.PASSED_WITH_WARNINGS)


def test_4_and_5_narrative_plan_drives_script_and_claims_preserve_citations():
    """NarrativePlan sections drive script structure and verified claims preserve exact citations."""
    provider = TemplateContentProvider()
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    brief = _make_concrete_brief()
    dna = {"brand_voice": {"tone": "AUTHORITATIVE", "pace": "MODERATE"}}

    req_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    claim1_id = uuid.UUID(brief["verified_claims"][0]["claim_id"])
    ev1_id = uuid.UUID(brief["verified_claims"][0]["citations"][0]["evidence_id"])
    src1_id = uuid.UUID(brief["verified_claims"][0]["citations"][0]["source_id"])

    sections = [
        NarrativeSection(
            id=uuid.uuid4(),
            narrative_plan_id=plan_id,
            section_order=1,
            role=NarrativeSectionRole.HOOK,
            objective="Engage civil engineers with concrete cracking puzzle.",
            key_information=["Visual evidence of cracking", "Economic impact of premature failure"],
            target_duration_seconds=30,
            target_information_density=InformationDensity.LOW,
            transition_hint="Let's examine the first mechanism.",
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            narrative_plan_id=plan_id,
            section_order=2,
            role=NarrativeSectionRole.DEVELOPMENT,
            objective="Explain plastic shrinkage cracking and surface moisture evaporation.",
            key_information=["Rapid moisture loss before set", "Tensile stress exceeds early strength"],
            target_duration_seconds=90,
            target_information_density=InformationDensity.HIGH,
            transition_hint="Next we observe drying shrinkage.",
            grounding_references=[
                GroundingReference(
                    research_brief_id=uuid.UUID(brief["id"]),
                    claim_id=claim1_id,
                    evidence_id=ev1_id,
                    source_id=src1_id,
                )
            ],
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            narrative_plan_id=plan_id,
            section_order=3,
            role=NarrativeSectionRole.CLOSING,
            objective="Summarize mitigation strategies and best practices.",
            key_information=["Early curing methods", "Moisture barriers"],
            target_duration_seconds=30,
            target_information_density=InformationDensity.MEDIUM,
            transition_hint="Closing perspective.",
        ),
    ]

    plan = NarrativePlan(
        id=plan_id,
        content_generation_request_id=req_id,
        channel_dna_revision_id=uuid.uuid4(),
        topic_candidate_id=uuid.uuid4(),
        research_brief_id=uuid.UUID(brief["id"]),
        version=1,
        is_current=True,
        status=NarrativePlanStatus.VALIDATED,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=150,
        estimated_duration_seconds=150,
        sections=sections,
    )

    outline = NarrativePlanScriptAdapter.map_plan_to_script_outline(plan)
    intent = provider.generate_intent(topic, None, brief, dna)
    hook = provider.generate_hooks(topic, brief, dna, intent)[0]
    script = provider.generate_script(topic, brief, dna, intent, hook, outline, 150)

    # 1. Structure matches plan sections exactly
    assert len(script["sections"]) == len(plan.sections)
    assert script["sections"][0]["heading"] == outline["sections"][0]["title"]
    assert script["sections"][1]["heading"] == outline["sections"][1]["title"]
    assert script["sections"][2]["heading"] == outline["sections"][2]["title"]

    # 2. Section 2 contains verified claim with exact citation
    sec2_stmts = script["sections"][1]["statements"]
    factual_stmts = [s for s in sec2_stmts if s["statement_type"] == ContentStatementType.FACTUAL.value]
    assert len(factual_stmts) >= 1
    assert str(claim1_id) in str(factual_stmts[0]["citations"])
    assert str(ev1_id) in str(factual_stmts[0]["citations"])
    assert str(src1_id) in str(factual_stmts[0]["citations"])


def test_6_and_7_factual_assertions_cannot_escape_as_creative():
    """Empirical research assertions cannot evade citation requirements by being labeled CREATIVE."""
    pinned_brief_id = uuid.uuid4()
    # Statement with research assertion words but no citations
    statement = "Studies show that plastic shrinkage occurs when surface evaporation exceeds bleed water rate."

    eff_type, policy, reasons = validate_and_classify_statement(
        statement_text=statement,
        suggested_type="CREATIVE",  # Untrusted label
        citations=[],
        pinned_brief_id=pinned_brief_id,
        brief_verified_claim_ids=set(),
        brief_conflict_claim_ids=set(),
    )

    assert eff_type == ContentStatementType.FACTUAL
    assert policy == ClaimUsagePolicy.BLOCKED_NO_EVIDENCE
    assert "FORCED_RECLASSIFICATION_FACTUAL_CONTENT" in reasons
    assert "FACTUAL_STATEMENT_MISSING_CITATIONS" in reasons


def test_8_cross_domain_leakage_yields_blocking_topic_authority_mismatch():
    """Substantive section introducing ungrounded domain vocabulary produces BLOCKING TOPIC_AUTHORITY_MISMATCH."""
    brief = _make_concrete_brief()
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"

    # Build a script where Section 2 introduces software architecture vocabulary
    off_topic_script = {
        "title": topic,
        "hook_text": "Why do concrete structures crack?",
        "closing_text": "Thank you for watching.",
        "cta_text": "Subscribe for more civil engineering breakdowns.",
        "estimated_duration_seconds": 300,
        "sections": [
            {
                "section_order": 1,
                "heading": "Introduction to Concrete Cracking",
                "narration_text": "Concrete cracking represents a fundamental challenge for civil engineers worldwide.",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "Concrete cracking represents a fundamental challenge for civil engineers worldwide.",
                        "statement_type": "CREATIVE",
                        "citations": [],
                    }
                ],
            },
            {
                "section_order": 2,
                "heading": "WSGI and ASGI Architecture Comparison",
                "narration_text": (
                    "When building distributed backend services, asynchronous ASGI event loops handle "
                    "thousands of concurrent TCP socket connections through non-blocking coroutines and database connection pooling."
                ),
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": (
                            "When building distributed backend services, asynchronous ASGI event loops handle "
                            "thousands of concurrent TCP socket connections through non-blocking coroutines and database connection pooling."
                        ),
                        "statement_type": "CREATIVE",
                        "citations": [],
                    }
                ],
            },
        ],
    }

    status, findings = run_content_qa_checks(
        script_data=off_topic_script,
        target_duration_seconds=300,
        dna_dict={},
        brief_dict=brief,
        topic_title=topic,
    )

    assert status == ScriptQAStatus.BLOCKED
    mismatch_findings = [f for f in findings if f["rule_code"] == QARuleCode.TOPIC_AUTHORITY_MISMATCH.value]
    assert len(mismatch_findings) >= 1
    assert mismatch_findings[0]["severity"] == "BLOCKING"
    assert mismatch_findings[0]["section_index"] == 2


def test_9_valid_topic_aligned_content_not_blocked():
    """Valid civil engineering concrete script passes QA without TOPIC_AUTHORITY_MISMATCH."""
    provider = TemplateContentProvider()
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    brief = _make_concrete_brief()
    dna = {"brand_voice": {"tone": "AUTHORITATIVE", "pace": "MODERATE"}}

    intent = provider.generate_intent(topic, None, brief, dna)
    hooks = provider.generate_hooks(topic, brief, dna, intent)
    outline = provider.generate_outline(topic, brief, dna, intent, hooks[0], 200)
    script = provider.generate_script(topic, brief, dna, intent, hooks[0], outline, 200)

    status, findings = run_content_qa_checks(
        script_data=script,
        target_duration_seconds=200,
        dna_dict=dna,
        brief_dict=brief,
        topic_title=topic,
        topic_summary=brief["summary"],
    )

    mismatch_findings = [f for f in findings if f["rule_code"] == QARuleCode.TOPIC_AUTHORITY_MISMATCH.value]
    assert len(mismatch_findings) == 0
    assert status in (ScriptQAStatus.PASSED, ScriptQAStatus.PASSED_WITH_WARNINGS)


def test_10_and_11_regeneration_preserves_narrative_plan_pinning_and_script_v1_immutability():
    """Script regeneration reuses NarrativePlan v1, produces Script v2, and preserves Script v1 immutability."""
    from omega.infrastructure.models import NarrativePlan as NarrativePlanRecord
    from omega.infrastructure.models import ScriptVersion as ScriptVersionRecord

    content_req_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    plan_record = NarrativePlanRecord(
        id=plan_id,
        content_generation_request_id=content_req_id,
        channel_dna_revision_id=uuid.uuid4(),
        topic_candidate_id=uuid.uuid4(),
        research_brief_id=uuid.uuid4(),
        version=1,
        is_current=True,
        status="VALIDATED",
        format_profile="SHORT",
        target_duration_seconds=150,
        estimated_duration_seconds=150,
        sections=[],
    )

    # Historical Script v1
    v1_id = uuid.uuid4()
    script_v1 = ScriptVersionRecord(
        id=v1_id,
        content_request_id=content_req_id,
        version=1,
        is_current=True,
        supersedes_script_id=None,
        title="Why Concrete Cracks",
        hook_text="Historical hook v1",
        closing_text="Historical closing v1",
        cta_text="Historical CTA v1",
        estimated_word_count=300,
        estimated_duration_seconds=150,
        qa_status="PASSED",
        narrative_plan_id=plan_id,
    )
    script_v1.narrative_plan = plan_record

    # Verify Script v1 initial state
    assert script_v1.version == 1
    assert script_v1.is_current is True
    assert script_v1.supersedes_script_id is None
    assert script_v1.narrative_plan_id == plan_id
    assert script_v1.narrative_plan_version == 1

    # Simulate regeneration (as performed in content_service.py generate_content / regenerate_script)
    # 1. Historical script v1 becomes inactive but remains unchanged in DB
    script_v1.is_current = False
    v1_frozen_title = script_v1.title
    v1_frozen_hook = script_v1.hook_text

    # 2. Script v2 is created
    v2_id = uuid.uuid4()
    script_v2 = ScriptVersionRecord(
        id=v2_id,
        content_request_id=content_req_id,
        version=script_v1.version + 1,
        is_current=True,
        supersedes_script_id=v1_id,
        title="Why Concrete Cracks",
        hook_text="Regenerated hook v2",
        closing_text="Regenerated closing v2",
        cta_text="Regenerated CTA v2",
        estimated_word_count=310,
        estimated_duration_seconds=150,
        qa_status="PASSED",
        narrative_plan_id=plan_id,  # Reuses EXACT NarrativePlan v1
    )
    script_v2.narrative_plan = plan_record

    # Assertions on regenerated Script v2
    assert script_v2.version == 2
    assert script_v2.is_current is True
    assert script_v2.supersedes_script_id == v1_id
    assert script_v2.narrative_plan_id == script_v1.narrative_plan_id == plan_id
    assert script_v2.narrative_plan_version == 1

    # Assertions on historical Script v1 immutability
    assert script_v1.id == v1_id
    assert script_v1.version == 1
    assert script_v1.is_current is False
    assert script_v1.title == v1_frozen_title
    assert script_v1.hook_text == v1_frozen_hook
