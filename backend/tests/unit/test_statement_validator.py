"""Unit tests for statement classification and untrusted provider validation."""

import uuid

from omega.application.statement_validator import validate_and_classify_statement
from omega.domain.content import ClaimUsagePolicy, ContentStatementType


def test_provider_creative_with_numbers_forced_to_factual():
    pinned_brief_id = uuid.uuid4()
    statement = "Our database queries experienced a 40% reduction in response latency."

    eff_type, policy, reasons = validate_and_classify_statement(
        statement_text=statement,
        suggested_type="CREATIVE",  # Provider falsely claimed creative
        citations=[],
        pinned_brief_id=pinned_brief_id,
        brief_verified_claim_ids=set(),
        brief_conflict_claim_ids=set(),
    )

    # Must be forced to FACTUAL and flagged for missing citations
    assert eff_type == ContentStatementType.FACTUAL
    assert policy == ClaimUsagePolicy.BLOCKED_NO_EVIDENCE
    assert "FORCED_RECLASSIFICATION_STATISTICAL_CONTENT" in reasons
    assert "FACTUAL_STATEMENT_MISSING_CITATIONS" in reasons


def test_provider_creative_with_quote_forced_to_factual():
    pinned_brief_id = uuid.uuid4()
    statement = 'The report explicitly stated "architecture is about trade-offs not perfection".'

    eff_type, policy, reasons = validate_and_classify_statement(
        statement_text=statement,
        suggested_type="CREATIVE",
        citations=[],
        pinned_brief_id=pinned_brief_id,
        brief_verified_claim_ids=set(),
        brief_conflict_claim_ids=set(),
    )

    assert eff_type == ContentStatementType.FACTUAL
    assert policy == ClaimUsagePolicy.BLOCKED_NO_EVIDENCE
    assert "FORCED_RECLASSIFICATION_STATISTICAL_CONTENT" in reasons


def test_valid_factual_statement_with_citation():
    pinned_brief_id = uuid.uuid4()
    claim_id = str(uuid.uuid4())
    statement = "Connection pooling reduces latency under high concurrent load."

    citations = [
        {
            "research_brief_id": str(pinned_brief_id),
            "claim_id": claim_id,
            "evidence_id": str(uuid.uuid4()),
            "source_id": str(uuid.uuid4()),
        }
    ]

    eff_type, policy, reasons = validate_and_classify_statement(
        statement_text=statement,
        suggested_type="FACTUAL",
        citations=citations,
        pinned_brief_id=pinned_brief_id,
        brief_verified_claim_ids={claim_id},
        brief_conflict_claim_ids=set(),
    )

    assert eff_type == ContentStatementType.FACTUAL
    assert policy == ClaimUsagePolicy.VERIFIED_FACT
    assert len(reasons) == 0


def test_citation_from_different_brief_rejected():
    pinned_brief_id = uuid.uuid4()
    foreign_brief_id = uuid.uuid4()
    claim_id = str(uuid.uuid4())

    citations = [
        {
            "research_brief_id": str(foreign_brief_id),
            "claim_id": claim_id,
            "evidence_id": str(uuid.uuid4()),
            "source_id": str(uuid.uuid4()),
        }
    ]

    eff_type, policy, reasons = validate_and_classify_statement(
        statement_text="Some factual claim.",
        suggested_type="FACTUAL",
        citations=citations,
        pinned_brief_id=pinned_brief_id,
        brief_verified_claim_ids={claim_id},
        brief_conflict_claim_ids=set(),
    )

    assert policy == ClaimUsagePolicy.BLOCKED_UNVERIFIED
    assert "CITATION_OUTSIDE_PINNED_BRIEF" in reasons


def test_citation_referencing_conflicted_claim_blocked():
    pinned_brief_id = uuid.uuid4()
    claim_id = str(uuid.uuid4())

    citations = [
        {
            "research_brief_id": str(pinned_brief_id),
            "claim_id": claim_id,
            "evidence_id": str(uuid.uuid4()),
            "source_id": str(uuid.uuid4()),
        }
    ]

    eff_type, policy, reasons = validate_and_classify_statement(
        statement_text="Controverted assertion.",
        suggested_type="FACTUAL",
        citations=citations,
        pinned_brief_id=pinned_brief_id,
        brief_verified_claim_ids={claim_id},
        brief_conflict_claim_ids={claim_id},  # Open high conflict!
    )

    assert policy == ClaimUsagePolicy.BLOCKED_CONFLICT
    assert "CITATION_REFERENCES_CONFLICTED_CLAIM" in reasons
