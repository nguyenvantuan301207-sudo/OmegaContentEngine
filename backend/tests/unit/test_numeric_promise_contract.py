"""Unit tests for Numeric Promise & Research Sufficiency Contract (P0.2).

Verifies title-promise extraction, false-positive protection, entity normalization,
distinct coverage clustering, duplicate claim collapse, research sufficiency gating,
content-generation admission fail-closed enforcement, Content QA blocking,
and the exact 5-vs-2 concrete mechanisms regression.
"""

import uuid
from typing import Any
import pytest

from omega.application.content_qa import run_content_qa_checks
from omega.application.content_service import _require_sufficient_brief
from omega.application.research_scorer import determine_research_outcome
from omega.domain.content import (
    ContentStatementType,
    QARuleCode,
    QASeverity,
    ScriptQAStatus,
)
from omega.domain.numeric_promise import (
    NumericPromiseContract,
    extract_distinct_entities,
    extract_numeric_promise,
    normalize_entity_type,
)
from omega.domain.research import ResearchOutcome


# ── Canonical Fixtures ──

CANONICAL_TOPIC = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"

CLAIM_PLASTIC_SHRINKAGE = {
    "claim_id": "133372f7-18b7-48e1-ad43-fbd5540cb569",
    "text": "Plastic shrinkage cracks are caused by a rapid loss of water from the surface of concrete before it has set",
    "claim_text": "Plastic shrinkage cracks are caused by a rapid loss of water from the surface of concrete before it has set",
    "citations": [
        {
            "evidence_id": "8905359a-14d4-4f24-9b2f-92931a293674",
            "source_id": "76934c9c-b17b-4b11-a877-bb8968ea15a9",
        }
    ],
}

CLAIM_DRYING_SHRINKAGE = {
    "claim_id": "dffabb3a-1a69-41de-b31d-ae0da51ad355",
    "text": "Most hardened concrete shows evidence of drying shrinkage over its service life",
    "claim_text": "Most hardened concrete shows evidence of drying shrinkage over its service life",
    "citations": [
        {
            "evidence_id": "8b5fd3ea-ee82-411a-ae57-a9a2a9a7a922",
            "source_id": "b1ecf87e-d007-4e78-953e-5818987b7db0",
        }
    ],
}


# ── 1. Numeric Promise Extraction ──

def test_numeric_promise_extraction():
    """Verify deterministic extraction of numeric promises from common title structures."""
    matrix = [
        ("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand", 5, "mechanism"),
        ("7 Causes of Structural Failure", 7, "cause"),
        ("3 Mistakes New Engineers Make", 3, "mistake"),
        ("10 Steps in the Review Process", 10, "step"),
        ("4 Reasons Concrete Cracks", 4, "reason"),
        ("6 Methods for Soil Stabilization", 6, "method"),
        ("8 Principles of Modern Architecture", 8, "principle"),
    ]
    for title, expected_count, expected_entity in matrix:
        contract = extract_numeric_promise(title)
        assert contract is not None, f"Failed to extract contract for: {title}"
        assert contract.promised_count == expected_count, f"Count mismatch for {title}: got {contract.promised_count}"
        assert contract.entity_type == expected_entity, f"Entity mismatch for {title}: got {contract.entity_type}"
        assert contract.contract_kind == "ENUMERATION"


# ── 2. Number-Word Extraction ──

def test_number_word_extraction():
    """Verify extraction when numbers are written as English words."""
    matrix = [
        ("Five Mechanisms of Concrete Deterioration", 5, "mechanism"),
        ("Three Mistakes in High-Rise Design", 3, "mistake"),
        ("Seven Causes of Bridge Collapse", 7, "cause"),
        ("Ten Steps to Building Code Compliance", 10, "step"),
        ("Four Reasons for Foundation Settlement", 4, "reason"),
    ]
    for title, expected_count, expected_entity in matrix:
        contract = extract_numeric_promise(title)
        assert contract is not None, f"Failed to extract number word contract for: {title}"
        assert contract.promised_count == expected_count
        assert contract.entity_type == expected_entity


# ── 3. False Positive Protection ──

def test_false_positive_protection():
    """Ensure non-enumerable quantities (dates, years, units, standards, versions) are rejected."""
    negatives = [
        "Concrete Strength at 28 Days",
        "Construction Outlook 2027",
        "40 MPa Concrete Mix Design",
        "ISO 9001 Quality Management",
        "Version 5 Rendering Architecture",
        "Concrete at 28 Days",
        "2026 Construction Outlook",
        "Top Trends for 2027",
        "Strength at 40 MPa",
        "Bridge Construction in 2025",
        "Concrete Slump Test at 100 mm",
        "Civil Engineering in the 21st Century",
    ]
    for title in negatives:
        contract = extract_numeric_promise(title)
        assert contract is None, f"False positive detected for: '{title}', got {contract}"


# ── 4. Entity Normalization ──

def test_entity_normalization():
    """Verify deterministic normalization of singular and plural entity nouns."""
    assert normalize_entity_type("mechanisms") == "mechanism"
    assert normalize_entity_type("mechanism") == "mechanism"
    assert normalize_entity_type("causes") == "cause"
    assert normalize_entity_type("cause") == "cause"
    assert normalize_entity_type("reasons") == "reason"
    assert normalize_entity_type("reason") == "reason"
    assert normalize_entity_type("mistakes") == "mistake"
    assert normalize_entity_type("steps") == "step"
    assert normalize_entity_type("methods") == "method"
    assert normalize_entity_type("principles") == "principle"


# ── 5. Contract Propagation and Serialization ──

def test_contract_propagation_and_serialization():
    """Verify round-trip serialization of NumericPromiseContract."""
    contract = NumericPromiseContract(
        promised_count=5,
        entity_type="mechanism",
        source_text="5 Mechanisms",
        confidence=1.0,
        contract_kind="ENUMERATION",
    )
    data = contract.to_dict()
    assert data["promised_count"] == 5
    assert data["entity_type"] == "mechanism"

    deserialized = NumericPromiseContract.from_dict(data)
    assert deserialized.promised_count == contract.promised_count
    assert deserialized.entity_type == contract.entity_type
    assert deserialized.contract_kind == contract.contract_kind


# ── 6. Distinct Coverage Model ──

def test_distinct_coverage_model():
    """Verify calculation of distinct supported entities from verified claims."""
    claims = [
        "Plastic shrinkage cracking occurs when surface evaporation exceeds bleed rate.",
        "Drying shrinkage develops over months as tensile stresses build up.",
        "Thermal contraction causes cracking in mass concrete as heat dissipates.",
    ]
    distinct = extract_distinct_entities(claims, topic_title=CANONICAL_TOPIC, entity_type="mechanism")
    assert len(distinct) == 3
    assert any("plastic" in d for d in distinct)
    assert any("dry" in d for d in distinct)
    assert any("thermal" in d for d in distinct)


# ── 7. Duplicate Claim Collapse Regression (Mandatory) ──

def test_duplicate_claims_collapse_to_single_family():
    """Verify that multiple rephrased claims about the same mechanism collapse to 1 family."""
    duplicate_claims = [
        "Plastic shrinkage cracking occurs when surface evaporation is rapid.",
        "Plastic-shrinkage cracking develops prior to initial set.",
        "Rapid evaporation causes plastic shrinkage cracks on flatwork.",
        "Plastic shrinkage crack formation during early curing.",
        "Occurrence of plastic shrinkage cracking under high wind conditions.",
    ]
    # 5 verified claims total
    assert len(duplicate_claims) == 5

    # But only 1 distinct semantic mechanism family!
    distinct = extract_distinct_entities(
        duplicate_claims, topic_title=CANONICAL_TOPIC, entity_type="mechanism"
    )
    assert len(distinct) == 1, f"Expected 1 collapsed family, got {len(distinct)}: {distinct}"

    # Under 5-mechanism contract, this MUST fail sufficiency
    outcome = determine_research_outcome(
        sources_count=3,
        independent_sources_count=3,
        verified_claims_count=5,
        open_high_conflicts_count=0,
        promised_count=5,
        distinct_entities_count=len(distinct),
    )
    assert outcome != ResearchOutcome.SUFFICIENT
    assert outcome == ResearchOutcome.PARTIAL


# ── 8. Research Outcome Blocking ──

def test_research_outcome_blocking():
    """Research outcome cannot be SUFFICIENT when distinct entities < promised count."""
    outcome = determine_research_outcome(
        sources_count=2,
        independent_sources_count=2,
        verified_claims_count=2,
        open_high_conflicts_count=0,
        promised_count=5,
        distinct_entities_count=2,
    )
    assert outcome != ResearchOutcome.SUFFICIENT
    assert outcome == ResearchOutcome.PARTIAL


# ── 9. Legacy No-Contract Behavior Preserved ──

def test_legacy_no_contract_behavior_preserved():
    """Non-numeric topics preserve standard sufficiency behavior with static minimums."""
    # 2 sources, 2 independent, 2 verified claims -> SUFFICIENT when no contract
    outcome = determine_research_outcome(
        sources_count=2,
        independent_sources_count=2,
        verified_claims_count=2,
        open_high_conflicts_count=0,
        promised_count=None,
        distinct_entities_count=None,
    )
    assert outcome == ResearchOutcome.SUFFICIENT


# ── 10. Content Generation Admission Gate ──

def test_content_generation_admission_gate_blocks_insufficient_contract():
    """Verify content service fail-closed admission when brief fails numeric contract."""
    class DummyBrief:
        def __init__(self, outcome: str, claims: list[dict[str, Any]], title: str):
            self.id = uuid.uuid4()
            self.outcome = outcome
            self.verified_claims = claims
            self.title = title
            self.metadata_ = {}

    # Case A: outcome is PARTIAL (normal research gate stopped it)
    partial_brief = DummyBrief(
        outcome=ResearchOutcome.PARTIAL.value,
        claims=[CLAIM_PLASTIC_SHRINKAGE, CLAIM_DRYING_SHRINKAGE],
        title=f"Research Brief: {CANONICAL_TOPIC} (v1)",
    )
    with pytest.raises(ValueError, match="PARTIAL or INSUFFICIENT research cannot become authoritative"):
        _require_sufficient_brief(partial_brief, CANONICAL_TOPIC)

    # Case B: legacy brief with outcome=SUFFICIENT but only 2 mechanisms for title promising 5
    legacy_sufficient_brief = DummyBrief(
        outcome=ResearchOutcome.SUFFICIENT.value,
        claims=[CLAIM_PLASTIC_SHRINKAGE, CLAIM_DRYING_SHRINKAGE],
        title=f"Research Brief: {CANONICAL_TOPIC} (v1)",
    )
    with pytest.raises(ValueError, match="does not satisfy numeric title promise"):
        _require_sufficient_brief(legacy_sufficient_brief, CANONICAL_TOPIC)


# ── 11. Content QA Blocking Mirror ──

def test_content_qa_blocks_unfulfilled_numeric_promise():
    """Verify Content QA detects title contract violation as BLOCKING finding."""
    # Script explaining only 2 mechanisms
    script_data = {
        "title": CANONICAL_TOPIC,
        "hook_text": "Why do massive concrete structures develop dangerous cracks?",
        "estimated_duration_seconds": 300,
        "sections": [
            {
                "heading": "Introduction: The Hidden Cost of Concrete Cracking",
                "narration_text": "Concrete is the foundation of civil infrastructure.",
                "statements": [],
            },
            {
                "heading": "Mechanism 1: Plastic Shrinkage Cracking",
                "narration_text": "Plastic shrinkage occurs when surface evaporation exceeds bleed rate.",
                "statements": [
                    {
                        "statement_text": "Plastic shrinkage occurs when surface evaporation exceeds bleed rate.",
                        "statement_type": ContentStatementType.FACTUAL.value,
                        "citations": [{"claim_id": CLAIM_PLASTIC_SHRINKAGE["claim_id"]}],
                    }
                ],
            },
            {
                "heading": "Mechanism 2: Drying Shrinkage Contraction",
                "narration_text": "Drying shrinkage develops over months as tensile stresses build.",
                "statements": [
                    {
                        "statement_text": "Drying shrinkage develops over months as tensile stresses build.",
                        "statement_type": ContentStatementType.FACTUAL.value,
                        "citations": [{"claim_id": CLAIM_DRYING_SHRINKAGE["claim_id"]}],
                    }
                ],
            },
            {
                "heading": "Conclusion: Engineering Crack Control",
                "narration_text": "Careful design and curing prevent catastrophic failure.",
                "statements": [],
            },
        ],
    }
    brief_dict = {
        "title": CANONICAL_TOPIC,
        "verified_claims": [CLAIM_PLASTIC_SHRINKAGE, CLAIM_DRYING_SHRINKAGE],
    }

    qa_status, findings = run_content_qa_checks(
        script_data=script_data,
        target_duration_seconds=300,
        dna_dict={},
        brief_dict=brief_dict,
        topic_title=CANONICAL_TOPIC,
    )

    promise_findings = [f for f in findings if f["rule_code"] == QARuleCode.NUMERIC_PROMISE_UNFULFILLED.value]
    assert len(promise_findings) == 1
    assert promise_findings[0]["severity"] == QASeverity.BLOCKING.value
    assert promise_findings[0]["details"]["promised_count"] == 5
    assert promise_findings[0]["details"]["grounded_count"] == 2
    assert qa_status == ScriptQAStatus.BLOCKED


# ── 12. Exact 5-vs-2 Historical Concrete Regression ──

def test_exact_5_vs_2_historical_concrete_regression():
    """Exact historical reproduction:
    Title: Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand
    Verified authority: Plastic Shrinkage, Drying Shrinkage (2 mechanisms)
    Promised: 5, Distinct Supported: 2 -> Outcome PARTIAL, Admission BLOCKED, QA BLOCKED.
    """
    contract = extract_numeric_promise(CANONICAL_TOPIC)
    assert contract is not None
    assert contract.promised_count == 5
    assert contract.entity_type == "mechanism"

    # Authority has 2 claims
    claims = [CLAIM_PLASTIC_SHRINKAGE["text"], CLAIM_DRYING_SHRINKAGE["text"]]
    distinct = extract_distinct_entities(claims, topic_title=CANONICAL_TOPIC, entity_type=contract.entity_type)
    assert len(distinct) == 2

    # Scorer gate: outcome must NOT be SUFFICIENT
    outcome = determine_research_outcome(
        sources_count=2,
        independent_sources_count=2,
        verified_claims_count=2,
        open_high_conflicts_count=0,
        promised_count=contract.promised_count,
        distinct_entities_count=len(distinct),
    )
    assert outcome == ResearchOutcome.PARTIAL
    assert outcome != ResearchOutcome.SUFFICIENT


# ── 13. Positive Regression: 5 Distinct Mechanisms ──

def test_positive_five_distinct_mechanisms():
    """Synthetic positive: 5 genuinely distinct mechanism entities satisfy the contract."""
    claims = [
        "Plastic shrinkage cracking occurs when surface evaporation exceeds bleed rate.",
        "Drying shrinkage develops over months as tensile stresses build up.",
        "Thermal contraction causes cracking in mass concrete as hydration heat dissipates.",
        "Chemical attack via alkali-silica reaction expands and fractures the aggregate matrix.",
        "Subgrade settlement causes differential movement and structural shear cracking.",
    ]
    distinct = extract_distinct_entities(claims, topic_title=CANONICAL_TOPIC, entity_type="mechanism")
    assert len(distinct) == 5

    outcome = determine_research_outcome(
        sources_count=3,
        independent_sources_count=3,
        verified_claims_count=5,
        open_high_conflicts_count=0,
        promised_count=5,
        distinct_entities_count=len(distinct),
    )
    assert outcome == ResearchOutcome.SUFFICIENT
