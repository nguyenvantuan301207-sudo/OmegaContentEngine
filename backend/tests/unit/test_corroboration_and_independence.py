"""Unit tests for Corroboration & Source Independence Forensic (P0.3c.5).

Verifies semantic proposition corroboration across independent domains,
contradiction/numeric safety, source independence clustering, and confidence scoring.
"""

from __future__ import annotations

import uuid

from omega.application.claim_reconciliation import are_propositions_corroborating
from omega.application.research_scorer import evaluate_claim_confidence
from omega.application.source_independence import cluster_source_independence
from omega.domain.research import ClaimType, EvidenceDirection, PrimarySourceStatus

# ── 1. Same proposition, different wording, independent domains ──


def test_same_proposition_different_wording_corroborates():
    """Equivalent technical propositions with different surrounding words corroborate."""
    # From real corpus: specchem.com vs bethelcustombrick.com
    text_a = (
        "Drying Shrinkage: Happens after the concrete has hardened and loses moisture "
        "over time, leading to volume reduction and cracking."
    )
    text_b = (
        "Concrete shrinks as it cures because the hydration process consumes water and reduces "
        "the volume of the hardened mass, leading to differential drying shrinkage cracking."
    )
    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is True


# ── 2. Topically similar but different propositions ──


def test_topically_similar_different_propositions_do_not_merge():
    """Distinct mechanisms within the same topic domain must not merge."""
    text_a = "Plastic shrinkage cracking occurs at early ages when surface evaporation exceeds bleeding."
    text_b = "Thermal contraction cracking develops when massive concrete elements cool unevenly from peak hydration."
    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is False


# ── 3. Opposite polarity ──


def test_opposite_polarity_rejected():
    """Negation differences prevent merging contradictory statements."""
    text_a = "Higher water-cement ratio accelerates drying shrinkage cracking in structural slabs."
    text_b = "Higher water-cement ratio does not accelerate drying shrinkage cracking in structural slabs."
    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is False


# ── 4. Different numeric facts ──


def test_different_numeric_facts_rejected():
    """Statements with conflicting numerical assertions must not merge."""
    text_a = "Approximately 80% of concrete structural cracks are caused by non-load volumetric changes."
    text_b = "Approximately 40% of concrete structural cracks are caused by non-load volumetric changes."
    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is False


# ── 5. Same content hash across domains ──


def test_same_content_hash_yields_one_cluster():
    """Documents with identical content hashes share one independence cluster."""
    src1 = {
        "id": uuid.uuid4(),
        "url": "https://mirror1.org/paper.pdf",
        "publisher": "Mirror One",
        "content_excerpt": "Comprehensive technical study on early-age concrete tensile cracking.",
        "content_hash": "hash_abc_123",
    }
    src2 = {
        "id": uuid.uuid4(),
        "url": "https://mirror2.net/paper.pdf",
        "publisher": "Mirror Two",
        "content_excerpt": "Comprehensive technical study on early-age concrete tensile cracking.",
        "content_hash": "hash_abc_123",
    }
    clusters = cluster_source_independence([src1, src2])
    assert clusters[src1["id"]] == clusters[src2["id"]]


# ── 6. Mirror/syndicated near-identical content ──


def test_near_identical_syndicated_content_yields_one_cluster():
    """Near-identical syndicated articles (Dice >= 0.70) share one independence cluster."""
    body = "A comprehensive technical reference examining plastic shrinkage and thermal cracking in concrete. " * 10
    src1 = {
        "id": uuid.uuid4(),
        "url": "https://syndicate-a.com/article",
        "publisher": "Syndicate A",
        "content_excerpt": body,
        "content_hash": "hash_111",
    }
    src2 = {
        "id": uuid.uuid4(),
        "url": "https://syndicate-b.com/article",
        "publisher": "Syndicate B",
        "content_excerpt": body + " Read more at our sister publication.",
        "content_hash": "hash_222",
    }
    clusters = cluster_source_independence([src1, src2])
    assert clusters[src1["id"]] == clusters[src2["id"]]


# ── 7. Independent publishers on same topic ──


def test_independent_publishers_on_same_topic_have_separate_clusters():
    """Independent sources discussing the same topic with different wording have separate clusters."""
    src1 = {
        "id": uuid.uuid4(),
        "url": "https://concrete.org/guide-224r",
        "publisher": "American Concrete Institute",
        "content_excerpt": (
            "ACI 224R provides guidelines on causes, mechanisms, and control of cracking in concrete structures. "
            "Compressive microcracking and flexural tensile stresses are analyzed in detail."
        ),
        "content_hash": "hash_aci",
    }
    src2 = {
        "id": uuid.uuid4(),
        "url": "https://specchem.com/technical-bulletin",
        "publisher": "SpecChem LLC",
        "content_excerpt": (
            "SpecChem technical reference explaining drying shrinkage and chemical admixtures to mitigate "
            "surface map cracking and plastic shrinkage during hot weather placement."
        ),
        "content_hash": "hash_specchem",
    }
    clusters = cluster_source_independence([src1, src2])
    assert clusters[src1["id"]] != clusters[src2["id"]]


# ── 8. Same domain duplicate variants ──


def test_same_domain_variants_yield_same_cluster():
    """Multiple pages from the same publisher with moderate overlap share one cluster."""
    src1 = {
        "id": uuid.uuid4(),
        "url": "https://manufacturer.com/blog/part1",
        "publisher": "Manufacturer Blog",
        "content_excerpt": "Understanding concrete cracking mechanisms part one: moisture movement and shrinkage.",
        "content_hash": "hash_p1",
    }
    src2 = {
        "id": uuid.uuid4(),
        "url": "https://manufacturer.com/blog/part2",
        "publisher": "Manufacturer Blog",
        "content_excerpt": "Understanding concrete cracking mechanisms part two: moisture movement and curing.",
        "content_hash": "hash_p2",
    }
    clusters = cluster_source_independence([src1, src2])
    assert clusters[src1["id"]] == clusters[src2["id"]]


# ── 9. Two real-quality UNKNOWN web sources: confidence < 70 ──


def test_two_unknown_web_sources_do_not_reach_verified_threshold():
    """Two neutral UNKNOWN web sources (quality ~53) reach ~68 confidence and remain unverified (< 70)."""
    s1_id = uuid.uuid4()
    s2_id = uuid.uuid4()
    sources_map = {
        s1_id: {
            "id": s1_id,
            "quality_score": 53.5,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
            "independence_cluster_id": "cluster_1",
        },
        s2_id: {
            "id": s2_id,
            "quality_score": 53.5,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
            "independence_cluster_id": "cluster_2",
        },
    }
    evidence = [
        {"id": "ev1", "source_id": s1_id, "strength_score": 80.0, "support_direction": EvidenceDirection.SUPPORTS},
        {"id": "ev2", "source_id": s2_id, "strength_score": 80.0, "support_direction": EvidenceDirection.SUPPORTS},
    ]
    res = evaluate_claim_confidence(
        claim={"id": uuid.uuid4(), "claim_text": "Drying shrinkage causes concrete cracking.", "claim_type": ClaimType.FACT},
        evidence_items=evidence,
        sources_map=sources_map,
        conflicts=[],
    )
    assert res["independent_sources_count"] == 2
    assert res["confidence_score"] < 70.0  # exactly 68.05
    assert res["is_verified"] is False


# ── 10. Three truly independent real-quality sources: confidence >= 70 (verified) ──


def test_three_independent_unknown_web_sources_reach_verified_threshold():
    """Three independent neutral UNKNOWN web sources (quality ~53) reach ~80 confidence and verify."""
    s1_id = uuid.uuid4()
    s2_id = uuid.uuid4()
    s3_id = uuid.uuid4()
    sources_map = {
        s1_id: {
            "id": s1_id,
            "quality_score": 53.5,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
            "independence_cluster_id": "cluster_1",
        },
        s2_id: {
            "id": s2_id,
            "quality_score": 53.5,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
            "independence_cluster_id": "cluster_2",
        },
        s3_id: {
            "id": s3_id,
            "quality_score": 53.5,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
            "independence_cluster_id": "cluster_3",
        },
    }
    evidence = [
        {"id": "ev1", "source_id": s1_id, "strength_score": 80.0, "support_direction": EvidenceDirection.SUPPORTS},
        {"id": "ev2", "source_id": s2_id, "strength_score": 80.0, "support_direction": EvidenceDirection.SUPPORTS},
        {"id": "ev3", "source_id": s3_id, "strength_score": 80.0, "support_direction": EvidenceDirection.SUPPORTS},
    ]
    res = evaluate_claim_confidence(
        claim={"id": uuid.uuid4(), "claim_text": "Drying shrinkage causes concrete cracking.", "claim_type": ClaimType.FACT},
        evidence_items=evidence,
        sources_map=sources_map,
        conflicts=[],
    )
    assert res["independent_sources_count"] == 3
    assert res["confidence_score"] >= 70.0  # exactly 80.05
    assert res["is_verified"] is True


# ── 11. Single source never gains fake multi-source confidence ──


def test_single_source_multiple_evidence_remains_one_independent_source():
    """Multiple evidence items from the same source or cluster do not inflate independent count."""
    s1_id = uuid.uuid4()
    sources_map = {
        s1_id: {
            "id": s1_id,
            "quality_score": 53.5,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
            "independence_cluster_id": "cluster_single",
        },
    }
    evidence = [
        {"id": "ev1", "source_id": s1_id, "strength_score": 80.0, "support_direction": EvidenceDirection.SUPPORTS},
        {"id": "ev2", "source_id": s1_id, "strength_score": 80.0, "support_direction": EvidenceDirection.SUPPORTS},
    ]
    res = evaluate_claim_confidence(
        claim={"id": uuid.uuid4(), "claim_text": "Drying shrinkage causes concrete cracking.", "claim_type": ClaimType.FACT},
        evidence_items=evidence,
        sources_map=sources_map,
        conflicts=[],
    )
    assert res["independent_sources_count"] == 1
    assert res["confidence_score"] < 60.0
    assert res["is_verified"] is False


# ── 12. Existing P0.3a corroboration invariants preserved ──


def test_p0_3a_exact_and_near_match_invariants():
    """Exact and canonical near-match propositions continue to corroborate."""
    t1 = "Plastic shrinkage cracking occurs when evaporation outpaces bleeding rate."
    t2 = "Plastic shrinkage cracking occurs when evaporation outpaces bleeding rate."
    assert are_propositions_corroborating(t1, ClaimType.FACT, t2, ClaimType.FACT) is True
