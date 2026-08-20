"""Unit tests for Claim Confidence and Verification Policy."""

from __future__ import annotations

import uuid

import pytest

from omega.application.research_scorer import evaluate_claim_confidence
from omega.domain.research import (
    ClaimType,
    ConfidenceBand,
    EvidenceDirection,
    PrimarySourceStatus,
)
from omega.domain.research_scoring import ClaimConfidenceProfile


def test_confidence_profile_weight_validation() -> None:
    """Test that ClaimConfidenceProfile weights must sum to 1.0."""
    with pytest.raises(ValueError, match="Confidence weights must sum to 1.0"):
        ClaimConfidenceProfile(
            independent_support_weight=0.50,
            source_quality_weight=0.50,
            evidence_strength_weight=0.50,
        )


def test_zero_evidence_invariant() -> None:
    """Test that a claim with zero supporting evidence is capped at 0.0 confidence."""
    claim = {"claim_type": ClaimType.FACT}
    metrics = evaluate_claim_confidence(
        claim=claim,
        evidence_items=[],  # No supporting evidence
        sources_map={},
        conflicts=[],
    )

    assert metrics["confidence_score"] == 0.0
    assert metrics["confidence_band"] == ConfidenceBand.LOW
    assert metrics["is_verified"] is False
    assert "ZERO_SUPPORTING_EVIDENCE" in metrics["reasons"]


def test_single_source_cannot_reach_high_band() -> None:
    """Test that a single source, even with 100 quality, cannot reach HIGH or VERY_HIGH band."""
    s1_id = uuid.uuid4()
    sources_map = {
        s1_id: {
            "quality_score": 100.0,
            "primary_source_status": PrimarySourceStatus.CONFIRMED,
            "independence_cluster_id": "cluster_1",
        }
    }

    evidence = [
        {
            "id": uuid.uuid4(),
            "source_id": s1_id,
            "support_direction": EvidenceDirection.SUPPORTS,
            "excerpt": "Async IO handles non-blocking I/O operations.",
            "strength_score": 100.0,
        }
    ]

    metrics = evaluate_claim_confidence(
        claim={"claim_type": ClaimType.FACT},
        evidence_items=evidence,
        sources_map=sources_map,
        conflicts=[],
    )

    # With only 1 independent source, confidence band must be MEDIUM, not HIGH or VERY_HIGH
    assert metrics["confidence_band"] == ConfidenceBand.MEDIUM


def test_claim_type_aware_verification_policy() -> None:
    """Test verification policies across different ClaimTypes."""
    s1_id = uuid.uuid4()
    s2_id = uuid.uuid4()

    sources_map = {
        s1_id: {
            "quality_score": 85.0,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
            "independence_cluster_id": "cluster_1",
        },
        s2_id: {
            "quality_score": 90.0,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
            "independence_cluster_id": "cluster_2",
        },
    }

    evidence_multi = [
        {
            "id": uuid.uuid4(),
            "source_id": s1_id,
            "support_direction": EvidenceDirection.SUPPORTS,
            "excerpt": "40% performance gain",
            "strength_score": 85.0,
        },
        {
            "id": uuid.uuid4(),
            "source_id": s2_id,
            "support_direction": EvidenceDirection.SUPPORTS,
            "excerpt": "40% performance gain confirmed",
            "strength_score": 90.0,
        },
    ]

    # 1. Multi-source STATISTIC is verified
    stat_metrics = evaluate_claim_confidence(
        claim={"claim_type": ClaimType.STATISTIC},
        evidence_items=evidence_multi,
        sources_map=sources_map,
        conflicts=[],
    )
    assert stat_metrics["is_verified"] is True

    # 2. CAUSAL / INTERPRETATION is never verified as undisputed factual certainty
    causal_metrics = evaluate_claim_confidence(
        claim={"claim_type": ClaimType.CAUSAL},
        evidence_items=evidence_multi,
        sources_map=sources_map,
        conflicts=[],
    )
    assert causal_metrics["is_verified"] is False
    assert "QUALIFIED_INTERPRETATION" in causal_metrics["reasons"]
