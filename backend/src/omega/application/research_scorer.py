"""Pure domain scoring and evaluation logic for the Research Engine.

Calculates deterministic source quality, detects contradictions, computes explainable
claim confidence with zero-evidence invariants, and determines research outcomes.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from omega.application.source_provider import NullAuthorityProvider, SourceAuthorityProvider
from omega.domain.research import (
    ClaimType,
    ConfidenceBand,
    ConflictSeverity,
    EvidenceDirection,
    PrimarySourceStatus,
    ResearchOutcome,
)
from omega.domain.research_scoring import (
    DEFAULT_CLAIM_CONFIDENCE_PROFILE,
    DEFAULT_OUTCOME_PROFILE,
    REASON_CLAIMED_PRIMARY_SOURCE,
    REASON_CONFIRMED_PRIMARY_SOURCE,
    REASON_DIRECT_EXCERPT_EVIDENCE,
    REASON_HIGH_AUTHORITY_PUBLISHER,
    REASON_MULTI_SOURCE_INDEPENDENT_CONSENSUS,
    REASON_OPEN_HIGH_CONTRADICTION,
    REASON_OPEN_MEDIUM_CONTRADICTION,
    REASON_QUALIFIED_INTERPRETATION,
    REASON_UNVERIFIED_STATISTIC,
    REASON_ZERO_SUPPORTING_EVIDENCE,
    ClaimConfidenceProfile,
    ResearchOutcomeProfile,
)


def calculate_source_quality(
    publisher: str,
    url: str | None,
    primary_source_status: PrimarySourceStatus,
    published_at: datetime | None,
    topic_keywords: list[str],
    content_excerpt: str,
    authority_provider: SourceAuthorityProvider | None = None,
) -> tuple[float, float, float, list[str]]:
    """Compute (quality_score, relevance_score, freshness_score, reasons)."""
    provider = authority_provider or NullAuthorityProvider()
    auth_score = provider.get_authority_score(publisher, url)

    reasons: list[str] = []
    if auth_score >= 80.0:
        reasons.append(REASON_HIGH_AUTHORITY_PUBLISHER)

    # 1. Primary Source Bonus
    primary_bonus = 0.0
    if primary_source_status == PrimarySourceStatus.CONFIRMED:
        primary_bonus = 100.0
        reasons.append(REASON_CONFIRMED_PRIMARY_SOURCE)
    elif primary_source_status == PrimarySourceStatus.CLAIMED:
        primary_bonus = 20.0
        reasons.append(REASON_CLAIMED_PRIMARY_SOURCE)

    # 2. Relevance Score
    excerpt_lower = content_excerpt.lower()
    matched_kws = sum(1 for kw in topic_keywords if kw.lower() in excerpt_lower)
    relevance_score = 50.0
    if topic_keywords:
        relevance_score = min(100.0, (matched_kws / len(topic_keywords)) * 100.0 + 30.0)

    # 3. Freshness Score
    freshness_score = 70.0
    if published_at:
        age_years = max(0, datetime.now().year - published_at.year)
        freshness_score = max(20.0, 100.0 - (age_years * 15.0))

    # 4. Traceability
    traceability_score = 40.0
    if url:
        traceability_score += 40.0
    if len(content_excerpt) >= 50:
        traceability_score += 20.0
    traceability_score = min(100.0, traceability_score)

    # Weighted Quality Score
    quality_score = (
        (0.35 * auth_score)
        + (0.25 * primary_bonus)
        + (0.20 * relevance_score)
        + (0.10 * freshness_score)
        + (0.10 * traceability_score)
    )
    quality_score = min(max(quality_score, 0.0), 100.0)

    return round(quality_score, 2), round(relevance_score, 2), round(freshness_score, 2), reasons


def detect_claim_conflicts(
    claim_id: UUID,
    claim_text: str,
    evidence_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect contradictions and numerical/chronological conflicts for a claim."""
    conflicts: list[dict[str, Any]] = []

    # 1. Check direct CONTRADICTS evidence direction
    contradicting_ev = [
        e for e in evidence_items if e.get("support_direction") == EvidenceDirection.CONTRADICTS
    ]
    if contradicting_ev:
        involved_ev_ids = [str(e["id"]) for e in contradicting_ev]
        involved_src_ids = [str(e["source_id"]) for e in contradicting_ev]
        conflicts.append(
            {
                "claim_id": claim_id,
                "conflict_type": "DIRECT_CONTRADICTION",
                "severity": ConflictSeverity.HIGH,
                "description": f"Direct conflicting evidence provided against claim: '{claim_text[:100]}...'",
                "involved_evidence_ids": involved_ev_ids,
                "involved_source_ids": involved_src_ids,
            }
        )

    # 2. Check numerical discrepancies across supporting excerpts
    num_patterns = re.findall(r"\b\d+(?:\.\d+)?%|\$\d+(?:\.\d+)?\b|\b\d{4}\b", claim_text)
    if num_patterns:
        # Look for conflicting numbers in evidence excerpts
        for ev in evidence_items:
            ev_nums = re.findall(
                r"\b\d+(?:\.\d+)?%|\$\d+(?:\.\d+)?\b|\b\d{4}\b", ev.get("excerpt", "")
            )
            # If evidence has numbers but none match claim numbers
            if ev_nums and not any(n in ev_nums for n in num_patterns):
                conflicts.append(
                    {
                        "claim_id": claim_id,
                        "conflict_type": "NUMERICAL_DISCREPANCY",
                        "severity": ConflictSeverity.MEDIUM,
                        "description": f"Numerical variance detected between claim '{num_patterns}' and source excerpt '{ev_nums}'",
                        "involved_evidence_ids": [str(ev["id"])],
                        "involved_source_ids": [str(ev["source_id"])],
                    }
                )
                break

    return conflicts


def evaluate_claim_confidence(
    claim: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    sources_map: dict[UUID, dict[str, Any]],
    conflicts: list[dict[str, Any]],
    profile: ClaimConfidenceProfile = DEFAULT_CLAIM_CONFIDENCE_PROFILE,
) -> dict[str, Any]:
    """Calculate confidence score, band, verification status, and reason codes for a claim."""
    claim_type = ClaimType(claim.get("claim_type", ClaimType.FACT))
    supporting_ev = [
        e for e in evidence_items if e.get("support_direction") == EvidenceDirection.SUPPORTS
    ]
    contradicting_ev = [
        e for e in evidence_items if e.get("support_direction") == EvidenceDirection.CONTRADICTS
    ]

    reasons: list[str] = []

    # ── ZERO-EVIDENCE INVARIANT ──
    if not supporting_ev:
        reasons.append(REASON_ZERO_SUPPORTING_EVIDENCE)
        return {
            "confidence_score": 0.0,
            "confidence_band": ConfidenceBand.LOW,
            "supporting_sources_count": 0,
            "contradicting_sources_count": len(contradicting_ev),
            "independent_sources_count": 0,
            "is_verified": False,
            "reasons": reasons,
        }

    # Resolve independent source clusters
    supporting_source_ids = {e["source_id"] for e in supporting_ev}
    cluster_ids: set[str] = set()
    source_qualities: list[float] = []
    has_confirmed_primary = False
    has_claimed_primary = False

    for s_id in supporting_source_ids:
        src = sources_map.get(s_id)
        if src:
            source_qualities.append(src.get("quality_score", 50.0))
            c_id = src.get("independence_cluster_id") or str(s_id)
            cluster_ids.add(c_id)
            p_status = src.get("primary_source_status")
            if p_status == PrimarySourceStatus.CONFIRMED:
                has_confirmed_primary = True
            elif p_status == PrimarySourceStatus.CLAIMED:
                has_claimed_primary = True

    n_independent = len(cluster_ids)
    avg_quality = sum(source_qualities) / len(source_qualities) if source_qualities else 50.0
    evidence_strengths = [float(e.get("strength_score", 80.0)) for e in supporting_ev]
    avg_strength = sum(evidence_strengths) / len(evidence_strengths) if evidence_strengths else 80.0

    # 1. Independent support component (capped at 100.0)
    indep_component = min(100.0, n_independent * 35.0)

    # 2. Base weighted score
    raw_score = (
        (profile.independent_support_weight * indep_component)
        + (profile.source_quality_weight * avg_quality)
        + (profile.evidence_strength_weight * avg_strength)
    )

    # 3. Primary source bonus
    if has_confirmed_primary:
        raw_score += profile.confirmed_primary_bonus
        reasons.append(REASON_CONFIRMED_PRIMARY_SOURCE)
    elif has_claimed_primary:
        raw_score += profile.claimed_primary_bonus
        reasons.append(REASON_CLAIMED_PRIMARY_SOURCE)

    if n_independent >= 2:
        reasons.append(REASON_MULTI_SOURCE_INDEPENDENT_CONSENSUS)
    if any(len(e.get("excerpt", "")) >= 10 for e in supporting_ev):
        reasons.append(REASON_DIRECT_EXCERPT_EVIDENCE)

    # 4. Contradiction penalties
    has_high_conflict = any(c.get("severity") == ConflictSeverity.HIGH for c in conflicts)
    has_med_conflict = any(c.get("severity") == ConflictSeverity.MEDIUM for c in conflicts)

    if has_high_conflict:
        raw_score -= profile.contradiction_penalty_high
        reasons.append(REASON_OPEN_HIGH_CONTRADICTION)
    elif has_med_conflict:
        raw_score -= profile.contradiction_penalty_medium
        reasons.append(REASON_OPEN_MEDIUM_CONTRADICTION)

    final_score = min(max(raw_score, 0.0), 100.0)

    # 5. Discrete Confidence Bands (with multi-source requirements)
    if (
        final_score >= 90.0
        and n_independent >= profile.min_sources_for_very_high_band
        and not has_high_conflict
    ):
        band = ConfidenceBand.VERY_HIGH
    elif (
        final_score >= 70.0
        and n_independent >= profile.min_sources_for_high_band
        and not has_high_conflict
    ):
        band = ConfidenceBand.HIGH
    elif final_score >= 40.0:
        band = ConfidenceBand.MEDIUM
    else:
        band = ConfidenceBand.LOW

    # 6. Claim-Type-Aware Verification Policy
    is_verified = False
    if not has_high_conflict and final_score >= profile.minimum_verified_confidence:
        if claim_type in (ClaimType.FACT, ClaimType.DATE, ClaimType.DEFINITION):
            is_verified = True
        elif claim_type == ClaimType.STATISTIC:
            # Statistic requires confirmed primary OR at least 2 independent corroborating sources
            if has_confirmed_primary or n_independent >= 2:
                is_verified = True
            else:
                reasons.append(REASON_UNVERIFIED_STATISTIC)
        elif claim_type == ClaimType.QUOTE:
            is_verified = True
        elif claim_type in (ClaimType.CAUSAL, ClaimType.INTERPRETATION):
            # Causal/interpretations remain qualified and are not verified as absolute factual certainties
            is_verified = False
            reasons.append(REASON_QUALIFIED_INTERPRETATION)

    return {
        "confidence_score": round(final_score, 2),
        "confidence_band": band,
        "supporting_sources_count": len(supporting_ev),
        "contradicting_sources_count": len(contradicting_ev),
        "independent_sources_count": n_independent,
        "is_verified": is_verified,
        "reasons": reasons,
    }


def determine_research_outcome(
    sources_count: int,
    independent_sources_count: int,
    verified_claims_count: int,
    open_high_conflicts_count: int,
    profile: ResearchOutcomeProfile = DEFAULT_OUTCOME_PROFILE,
) -> ResearchOutcome:
    """Determine SUFFICIENT / PARTIAL / INSUFFICIENT research outcome."""
    if (
        sources_count >= profile.min_sources_sufficient
        and independent_sources_count >= profile.min_independent_sources_sufficient
        and verified_claims_count >= profile.min_verified_claims_sufficient
        and open_high_conflicts_count <= profile.max_open_high_conflicts_sufficient
    ):
        return ResearchOutcome.SUFFICIENT

    if (
        sources_count >= profile.min_sources_partial
        and verified_claims_count >= profile.min_claims_partial
    ):
        return ResearchOutcome.PARTIAL

    return ResearchOutcome.INSUFFICIENT
