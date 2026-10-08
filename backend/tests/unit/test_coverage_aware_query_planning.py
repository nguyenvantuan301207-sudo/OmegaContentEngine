"""Domain-Agnostic Regression Tests for Coverage-Aware Query Planning.

Covers:
1. Exact FHWA citation-prefixed enumeration extraction and nested parentheses preservation.
2. Citation tokens and academic acronyms excluded from query anchors.
3. Unsupported distinct families ranked first.
4. Verified-family duplicate descriptions excluded from corroboration query slots.
5. Plastic shrinkage and differential shrinkage remain distinct.
6. Ambiguous generic shrinkage does not force merging into verified families.
7. Clipped clause / truncated fragment handling.
8. Query history deduplication across rounds.
9. Source independence preserved (same source cannot corroborate itself).
10. One source cannot satisfy all five families.
11. Verified-only numeric authority.
12. Non-causal enumerable topics unaffected (networking, battery, distributed systems).
13. Exhaustion behavior: fallback or clean stop when all causal targets are exhausted.
"""

from __future__ import annotations

from uuid import uuid4

from omega.application.claim_extractor import (
    _is_scholarly_metadata_or_clutter,
    split_explicit_enumeration,
)
from omega.application.claim_reconciliation import (
    are_propositions_corroborating,
    reconcile_source_extractions_into_claims,
)
from omega.application.research_query_planner import (
    build_corroboration_targets,
    classify_candidate_family_coverage,
    extract_query_anchors,
    plan_research_queries,
)
from omega.domain.numeric_promise import (
    extract_numeric_promise,
)
from omega.domain.research import (
    ClaimType,
    CorroborationTarget,
    ResearchQueryIntent,
)
from omega.infrastructure.models import ResearchClaim, ResearchSource


def test_fhwa_citation_prefixed_enumeration_with_nested_parentheses() -> None:
    """Exact FHWA citation-prefixed sentence splits into atomic propositions without splitting nested parens."""
    raw = (
        "Cracks in HCC may have several causes (see ACI 201.1R and ACI 224.1R): "
        "plastic shrinkage, settlement, drying shrinkage, thermal stresses, chemical reactions, "
        "weathering (freezing and thawing, wetting and drying, heating and cooling), "
        "corrosion of reinforcement, poor construction practices (e.g., retempering), "
        "construction overloads, errors in design and detailing, and externally applied loads."
    )
    atoms = split_explicit_enumeration(raw)
    assert len(atoms) >= 10

    # Verify nested parentheses are preserved intact
    weathering = [a for a in atoms if "weathering" in a]
    assert len(weathering) == 1
    assert "freezing and thawing, wetting and drying, heating and cooling" in weathering[0]

    retempering = [a for a in atoms if "poor construction practices" in a]
    assert len(retempering) == 1
    assert "(e.g., retempering)" in retempering[0]

    # Verify citations are NOT treated as causes
    assert not any("ACI 201.1R" in a for a in atoms)
    assert not any("ACI 224.1R" in a for a in atoms)

    # Verify atomic framing
    assert all(a.startswith("Cracks in HCC may be caused by ") for a in atoms)


def test_citation_tokens_excluded_from_query_anchors() -> None:
    """Citation markers, standards acronyms, and discourse fillers are never query anchors."""
    claim = "Cracks in HCC may have several causes (see ACI 201.1R and ASTM C157): drying shrinkage."
    anchors = extract_query_anchors(claim, subject="concrete cracks")

    assert "see" not in anchors
    assert "aci" not in anchors
    assert "astm" not in anchors
    assert "hcc" not in anchors
    assert "may" not in anchors
    assert "drying" in anchors or "shrinkage" in anchors


def test_unsupported_distinct_families_ranked_first() -> None:
    """Unsupported candidate families are prioritized over already-verified or redundant descriptions."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    verified_families = ["differential shrinkage", "plastic shrinkage"]

    c1 = ResearchClaim(
        id=uuid4(),
        claim_text="Cracks are caused by subgrade settlement under foundational loads.",
        claim_type=ClaimType.FACT,
        confidence_score=75.0,
        independent_sources_count=2,
        is_verified=False,
    )
    c2 = ResearchClaim(
        id=uuid4(),
        claim_text="Plastic shrinkage occurs when concrete loses moisture rapidly before hardening.",
        claim_type=ClaimType.FACT,
        confidence_score=80.0,
        independent_sources_count=2,
        is_verified=False,
    )

    targets = build_corroboration_targets(
        claims=[c1, c2],
        contract=contract,
        already_supported_families=verified_families,
    )

    t1 = next(t for t in targets if "subgrade settlement" in t.representative_claim_text)
    t2 = next(t for t in targets if "Plastic shrinkage" in t.representative_claim_text)

    # Subgrade settlement is an unverified family -> priority 1
    assert t1.priority == 1
    # Plastic shrinkage is redundant of verified family -> priority 5
    assert t2.priority == 5


def test_plastic_and_differential_shrinkage_remain_distinct() -> None:
    """Plastic shrinkage and differential shrinkage must not merge or be considered redundant."""
    t1 = "Plastic shrinkage causes cracking in fresh concrete."
    t2 = "Differential shrinkage causes surface cracking in curing concrete."

    assert not are_propositions_corroborating(t1, ClaimType.FACT, t2, ClaimType.FACT)

    verified = ["differential shrinkage"]
    cov_class, fam = classify_candidate_family_coverage(t1, already_supported_families=verified, entity_type="mechanism")
    assert cov_class == "UNVERIFIED_ENTITY_FAMILY"
    assert fam == "plastic shrinkage"


def test_ambiguous_generic_shrinkage_does_not_force_merging() -> None:
    """Ambiguous generic shrinkage statements are not merged into either verified family."""
    generic = "Concrete cracking is caused by shrinkage."
    verified = ["differential shrinkage", "plastic shrinkage"]

    cov_class, fam = classify_candidate_family_coverage(generic, already_supported_families=verified, entity_type="mechanism")
    assert cov_class == "AMBIGUOUS_OR_GENERIC_CONTEXT"
    assert fam == "shrinkage"


def test_clipped_clause_handling() -> None:
    """Truncated fragments with dangling clauses or conjunctions are rejected."""
    fragment = "This total loss of water leads to a contraction of the concrete, termed drying shrinkage, and i"
    assert _is_scholarly_metadata_or_clutter(fragment)

    cov_class, _ = classify_candidate_family_coverage(fragment, already_supported_families=[])
    assert cov_class == "AMBIGUOUS_OR_GENERIC_CONTEXT"


def test_query_history_deduplication() -> None:
    """Queries issued in earlier rounds are never repeated in later rounds."""
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    prior_queries = [
        "concrete cracks settlement technical reference",
        "concrete cracks freeze thaw cycling technical reference",
    ]

    target = CorroborationTarget(
        representative_claim_text="Concrete cracks are caused by settlement.",
        claim_type=ClaimType.FACT,
        independent_support_count=2,
        supporting_domains=["example.com"],
        confidence_score=75.0,
        topic_relevance=90.0,
        priority=1,
        semantic_role="CAUSE_OR_PROCESS",
        candidate_family="settlement",
    )

    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=2,
        already_supported_families=["differential shrinkage"],
        corroboration_targets=[target],
        issued_query_texts=prior_queries,
    )

    assert not any(q.query_text == "concrete cracks settlement technical reference" for q in queries)


def test_source_independence_preserved() -> None:
    """Multiple extractions from the same source do not artificially inflate independent support."""
    from unittest.mock import MagicMock
    src = ResearchSource(
        id=uuid4(),
        research_request_id=uuid4(),
        url="https://example.com/article",
        quality_score=80.0,
        content_excerpt="Test excerpt",
    )
    items = [
        {
            "claim_text": "Thermal expansion and contraction cycles cause severe surface cracking in concrete structures.",
            "claim_type": ClaimType.FACT,
            "excerpt": "Thermal expansion and contraction cycles cause severe surface cracking in concrete structures.",
            "strength_score": 80.0,
        },
        {
            "claim_text": "Thermal expansion and contraction cycles cause severe surface cracking in concrete structures.",
            "claim_type": ClaimType.FACT,
            "excerpt": "Thermal expansion and contraction cycles cause severe surface cracking in concrete structures.",
            "strength_score": 80.0,
        },
    ]

    reconciled = reconcile_source_extractions_into_claims(
        session=MagicMock(),
        existing_claims=[],
        extracted_items=items,
        source=src,
        channel_id=None,
        request_id=src.research_request_id,
    )

    assert len(reconciled) == 1
    # Only 1 unique source attached
    assert len(reconciled[0].evidence) == 1
    assert len({ev.source_id for ev in reconciled[0].evidence}) == 1


def test_one_source_cannot_satisfy_all_five_families() -> None:
    """Five verified families require independent source backing, not one source asserting all five."""
    from omega.application.research_service import evaluate_canonical_claims

    src = ResearchSource(
        id=uuid4(),
        research_request_id=uuid4(),
        url="https://single-source.com/cracks",
        quality_score=90.0,
        content_excerpt="Single source with all five claims",
    )

    mechanisms = [
        "Cracks in concrete are caused by plastic shrinkage.",
        "Cracks in concrete are caused by drying shrinkage.",
        "Cracks in concrete are caused by thermal stresses.",
        "Cracks in concrete are caused by chemical attack.",
        "Cracks in concrete are caused by subgrade settlement.",
    ]
    claims = [
        ResearchClaim(
            id=uuid4(),
            claim_text=m,
            claim_type=ClaimType.FACT,
            confidence_score=60.0,
            independent_sources_count=1,
            is_verified=False,
        )
        for m in mechanisms
    ]

    verified, _, _ = evaluate_canonical_claims(eligible_sources=[src], claims=claims)
    # A single source cannot reach verification threshold (requires independent support and >= 70 score)
    assert len(verified) == 0


def test_non_causal_enumerable_topics_unaffected() -> None:
    """Non-causal topics (e.g. 5 tips, 4 steps) plan queries correctly without causal restriction."""
    topic = "5 Strategies for Optimizing Kubernetes Resource Allocation"
    contract = extract_numeric_promise(topic)

    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=1,
    )
    assert len(queries) == 3
    assert any("strategies" in q.query_text for q in queries)


def test_domain_agnostic_topics() -> None:
    """Battery degradation and distributed systems topics work seamlessly without hardcoded terms."""
    topic_battery = "4 Mechanisms of Solid-State Battery Degradation"
    contract_battery = extract_numeric_promise(topic_battery)
    assert contract_battery is not None
    assert contract_battery.entity_type == "mechanism"

    claim_battery = "Dendrite penetration causes internal short circuit in solid-state electrolytes."
    cov_class, fam = classify_candidate_family_coverage(claim_battery, already_supported_families=[], entity_type=contract_battery.entity_type)
    assert cov_class == "UNVERIFIED_ENTITY_FAMILY"
    assert fam != "unknown"

    topic_ds = "3 Techniques for Distributed Database Replication"
    contract_ds = extract_numeric_promise(topic_ds)
    assert contract_ds is not None
    assert contract_ds.entity_type == "technique"
    queries_ds = plan_research_queries(topic_title=topic_ds, contract=contract_ds, round_number=1)
    assert len(queries_ds) == 3


def test_exhaustion_clean_fallback() -> None:
    """When all candidate targets are exhausted or already verified, planner cleanly issues generic fallback queries."""
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    # All targets are already verified
    verified = ["differential shrinkage", "plastic shrinkage", "thermal contraction", "settlement", "corrosion"]
    targets = [
        CorroborationTarget(
            representative_claim_text="Plastic shrinkage causes cracking.",
            claim_type=ClaimType.FACT,
            independent_support_count=2,
            supporting_domains=["example.com"],
            confidence_score=85.0,
            topic_relevance=90.0,
            priority=5,  # redundant
            semantic_role="CAUSE_OR_PROCESS",
            candidate_family="plastic shrinkage",
        )
    ]

    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=2,
        already_supported_families=verified,
        corroboration_targets=targets,
        max_queries=3,
    )

    # Must fall back to additional coverage queries, never fabricate pseudo-families
    assert len(queries) == 3
    assert all(q.intent in (ResearchQueryIntent.ADDITIONAL_COVERAGE, ResearchQueryIntent.CAUSES, ResearchQueryIntent.TYPES) for q in queries)
    assert not any("plastic shrinkage" in q.query_text for q in queries)
