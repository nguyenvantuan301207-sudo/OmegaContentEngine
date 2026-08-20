"""Pure domain scoring algorithm and explainability generator for Topic Intelligence.

Evaluates 8 positive-polarity dimensions, computes weighted final score,
and emits operational reason codes without hidden chain-of-thought.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from omega.application.content_gap_analyzer import analyze_content_gap
from omega.application.duplicate_detector import normalize_text
from omega.application.signal_providers import (
    DefaultCostProvider,
    DefaultRevenueProvider,
    NullPerformanceProvider,
    NullTrendSignalProvider,
)
from omega.domain.channel_context import ChannelContext
from omega.domain.topic import DuplicateStatus, TopicCandidateCreate
from omega.domain.topic_scoring import (
    DEFAULT_SCORING_PROFILE,
    REASON_CONTENT_GAP_FILL,
    REASON_EXACT_DUPLICATE,
    REASON_FRESH_TOPIC,
    REASON_HIGH_AUDIENCE_RELEVANCE,
    REASON_HIGH_FEASIBILITY,
    REASON_LOW_AUDIENCE_FIT,
    REASON_LOW_STRATEGIC_FIT,
    REASON_MATCHES_PRIMARY_PILLAR,
    REASON_MATCHES_SUBNICHE,
    REASON_PENALIZED_RECENT_DUPLICATE,
    REASON_PENALIZED_SEMANTIC_DUPLICATE,
    REASON_SAME_TOPIC_NEW_ANGLE,
    REASON_STRONG_TREND_SIGNAL,
    TopicScoringProfile,
)


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to safely extract an attribute from either an object or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def evaluate_audience_fit(
    candidate_lang: str,
    candidate_region: str,
    candidate_text: str,
    candidate_keywords: list[str],
    channel_context: ChannelContext,
) -> tuple[float, list[str]]:
    """Compute audience fit score (0.0 to 100.0) against Channel DNA audience profile."""
    reasons: list[str] = []
    base_score = 50.0

    # 1. Language & Region compatibility
    if candidate_lang.lower() == channel_context.primary_language.lower():
        base_score += 15.0
    else:
        base_score -= 25.0

    if candidate_region.upper() == channel_context.target_region.upper():
        base_score += 10.0

    # 2. Audience Interests match
    dna_audience = channel_context.dna.audience
    dna_interests = [normalize_text(i) for i in dna_audience.interests if normalize_text(i)]
    cand_norm_keywords = [normalize_text(k) for k in candidate_keywords if normalize_text(k)]
    all_cand_text = candidate_text + " " + " ".join(cand_norm_keywords)

    interest_matches = [i for i in dna_interests if i in all_cand_text or any(token in all_cand_text for token in i.split())]
    if interest_matches:
        base_score += min(len(interest_matches) * 12.0, 25.0)
        reasons.append(REASON_HIGH_AUDIENCE_RELEVANCE)

    final_aud_score = max(0.0, min(100.0, base_score))
    if final_aud_score < 40.0:
        reasons.append(REASON_LOW_AUDIENCE_FIT)

    return final_aud_score, reasons


def evaluate_strategic_fit(
    candidate_text: str,
    candidate_keywords: list[str],
    channel_context: ChannelContext,
) -> tuple[float, list[str]]:
    """Compute strategic fit score (0.0 to 100.0) against Channel DNA niche and pillars."""
    reasons: list[str] = []
    base_score = 40.0

    dna_strat = channel_context.dna.content_strategy
    niche_norm = normalize_text(dna_strat.niche)
    subniches_norm = [normalize_text(s) for s in dna_strat.subniches if normalize_text(s)]
    pillars_norm = [normalize_text(p) for p in dna_strat.content_pillars if normalize_text(p)]

    cand_norm_keywords = [normalize_text(k) for k in candidate_keywords if normalize_text(k)]
    all_text = candidate_text + " " + " ".join(cand_norm_keywords)

    # Niche match
    if niche_norm and (niche_norm in all_text or any(token in all_text for token in niche_norm.split())):
        base_score += 25.0

    # Subniche match
    subniche_matches = [s for s in subniches_norm if s in all_text or any(token in all_text for token in s.split())]
    if subniche_matches:
        base_score += 15.0
        reasons.append(REASON_MATCHES_SUBNICHE)

    # Pillar match
    pillar_matches = [p for p in pillars_norm if p in all_text or any(token in all_text for token in p.split())]
    if pillar_matches:
        base_score += 20.0
        reasons.append(REASON_MATCHES_PRIMARY_PILLAR)

    final_strat_score = max(0.0, min(100.0, base_score))
    if final_strat_score < 40.0:
        reasons.append(REASON_LOW_STRATEGIC_FIT)

    return final_strat_score, reasons


def evaluate_novelty(
    duplicate_status: DuplicateStatus,
    similar_memory: Any | None,
    profile: TopicScoringProfile,
) -> tuple[float, list[str]]:
    """Compute novelty score (0.0 to 100.0) taking into account duplicate status and memory recency."""
    reasons: list[str] = []

    if duplicate_status == DuplicateStatus.EXACT_DUPLICATE:
        reasons.append(REASON_EXACT_DUPLICATE)
        return 0.0, reasons

    if duplicate_status == DuplicateStatus.SEMANTIC_DUPLICATE:
        reasons.append(REASON_PENALIZED_SEMANTIC_DUPLICATE)
        return 20.0, reasons

    if duplicate_status == DuplicateStatus.SAME_TOPIC_NEW_ANGLE:
        reasons.append(REASON_SAME_TOPIC_NEW_ANGLE)
        # Check recency if memory is provided
        if similar_memory and hasattr(similar_memory, "last_selected_at") and similar_memory.last_selected_at:
            delta_days = (datetime.now(UTC) - similar_memory.last_selected_at).days
            if delta_days < profile.recent_duplicate_days:
                reasons.append(REASON_PENALIZED_RECENT_DUPLICATE)
                return 50.0, reasons
            elif delta_days >= profile.cooldown_days:
                return 90.0, reasons
        return 75.0, reasons

    if duplicate_status == DuplicateStatus.RELATED_TOPIC:
        return 80.0, reasons

    # FRESH_TOPIC
    reasons.append(REASON_FRESH_TOPIC)
    return 100.0, reasons


def calculate_topic_scores(
    candidate: TopicCandidateCreate | dict[str, Any] | Any,
    channel_context: ChannelContext,
    duplicate_status: DuplicateStatus,
    similar_memory: Any | None = None,
    memory_records: list[Any] | None = None,
    profile: TopicScoringProfile = DEFAULT_SCORING_PROFILE,
    trend_provider: Any = None,
    perf_provider: Any = None,
    cost_provider: Any = None,
    revenue_provider: Any = None,
) -> dict[str, Any]:
    """Execute complete explainable scoring across all 8 dimensions.

    Returns a dictionary with all numerical subscores, final_score, score_breakdown, and reason codes.
    """
    trend_prov = trend_provider or NullTrendSignalProvider()
    perf_prov = perf_provider or NullPerformanceProvider()
    cost_prov = cost_provider or DefaultCostProvider()
    rev_prov = revenue_provider or DefaultRevenueProvider()

    title = _get_val(candidate, "title", "")
    summary = _get_val(candidate, "summary", "")
    keywords = _get_val(candidate, "keywords", []) or []
    language = _get_val(candidate, "language", "en")
    region = _get_val(candidate, "region", "US")

    manual_trend = _get_val(candidate, "manual_trend_score")
    manual_cost = _get_val(candidate, "manual_cost_efficiency_score")
    manual_rev = _get_val(candidate, "manual_revenue_score")

    norm_text = normalize_text(title) + " " + normalize_text(summary or "")

    reasons: list[str] = []

    # 1. Audience Fit
    aud_score, aud_reasons = evaluate_audience_fit(
        candidate_lang=language,
        candidate_region=region,
        candidate_text=norm_text,
        candidate_keywords=keywords,
        channel_context=channel_context,
    )
    reasons.extend(aud_reasons)

    # 2. Strategic Fit
    strat_score, strat_reasons = evaluate_strategic_fit(
        candidate_text=norm_text,
        candidate_keywords=keywords,
        channel_context=channel_context,
    )
    reasons.extend(strat_reasons)

    # 3. Trend Signal
    trend_score = trend_prov.get_trend_score(title=title, keywords=keywords, manual_score=manual_trend)
    if trend_score >= 75.0:
        reasons.append(REASON_STRONG_TREND_SIGNAL)

    # 4. Novelty Score
    novelty_score, nov_reasons = evaluate_novelty(
        duplicate_status=duplicate_status,
        similar_memory=similar_memory,
        profile=profile,
    )
    reasons.extend(nov_reasons)

    # 5. Content Gap Score
    content_gap_score, gap_reasons = analyze_content_gap(
        candidate_title=title,
        candidate_keywords=keywords,
        content_pillars=channel_context.dna.content_strategy.content_pillars,
        memory_records=memory_records or [],
    )
    if content_gap_score >= 80.0:
        reasons.append(REASON_CONTENT_GAP_FILL)
    reasons.extend(gap_reasons)

    # 6. Historical Performance Score (independent from candidate evaluation score)
    hist_perf_score = perf_prov.get_performance_score(topic_title=title, keywords=keywords)

    # 7. Cost Efficiency / Feasibility Score (100 = easy/high feasibility, 0 = hard/low feasibility)
    cost_eff_score = cost_prov.get_cost_efficiency_score(title=title, manual_score=manual_cost)
    if cost_eff_score >= 80.0:
        reasons.append(REASON_HIGH_FEASIBILITY)

    # 8. Revenue Potential Score
    revenue_score = rev_prov.get_revenue_score(
        niche=channel_context.dna.content_strategy.niche,
        keywords=keywords,
        manual_score=manual_rev,
    )

    # Weighted combination
    w = profile.weights
    final_score = (
        (w["audience_fit"] * aud_score)
        + (w["strategic_fit"] * strat_score)
        + (w["trend"] * trend_score)
        + (w["novelty"] * novelty_score)
        + (w["content_gap"] * content_gap_score)
        + (w["historical_performance"] * hist_perf_score)
        + (w["cost_efficiency"] * cost_eff_score)
        + (w["revenue_potential"] * revenue_score)
    )
    final_score = round(max(0.0, min(100.0, final_score)), 2)

    score_breakdown = {
        "audience_fit": round(aud_score, 1),
        "strategic_fit": round(strat_score, 1),
        "trend": round(trend_score, 1),
        "novelty": round(novelty_score, 1),
        "content_gap": round(content_gap_score, 1),
        "historical_performance": round(hist_perf_score, 1),
        "cost_efficiency": round(cost_eff_score, 1),
        "revenue_potential": round(revenue_score, 1),
    }

    # Deduplicate reason codes preserving order
    dedup_reasons = list(dict.fromkeys(reasons))

    return {
        "audience_fit_score": aud_score,
        "strategic_fit_score": strat_score,
        "trend_score": trend_score,
        "novelty_score": novelty_score,
        "content_gap_score": content_gap_score,
        "historical_performance_score": hist_perf_score,
        "cost_efficiency_score": cost_eff_score,
        "revenue_potential_score": revenue_score,
        "final_score": final_score,
        "score_breakdown": score_breakdown,
        "reasons": dedup_reasons,
    }
