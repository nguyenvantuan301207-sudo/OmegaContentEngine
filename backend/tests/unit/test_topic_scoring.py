"""Unit tests for topic scoring engine, profile weights, and explainability."""

from __future__ import annotations

import uuid

import pytest

from omega.application.topic_scorer import calculate_topic_scores
from omega.domain.channel import ChannelState, Platform
from omega.domain.channel_context import ChannelContext
from omega.domain.channel_dna import ChannelDNA
from omega.domain.topic import DuplicateStatus, TopicCandidateCreate
from omega.domain.topic_scoring import (
    REASON_CONTENT_GAP_FILL,
    REASON_FRESH_TOPIC,
    REASON_MATCHES_PRIMARY_PILLAR,
    TopicScoringProfile,
)


def test_scoring_profile_weight_sum_validation() -> None:
    """Test that TopicScoringProfile strictly requires weights to sum to 1.0."""
    # Valid profile
    valid = TopicScoringProfile(
        weights={
            "audience_fit": 0.25,
            "strategic_fit": 0.25,
            "trend": 0.10,
            "novelty": 0.10,
            "content_gap": 0.10,
            "historical_performance": 0.10,
            "cost_efficiency": 0.05,
            "revenue_potential": 0.05,
        }
    )
    assert sum(valid.weights.values()) == 1.0

    # Invalid profile (sum != 1.0)
    with pytest.raises(ValueError, match="Scoring weights must sum to 1.0"):
        TopicScoringProfile(
            weights={
                "audience_fit": 0.50,
                "strategic_fit": 0.20,
                "trend": 0.15,
                "novelty": 0.15,
                "content_gap": 0.10,
                "historical_performance": 0.10,
                "cost_efficiency": 0.05,
                "revenue_potential": 0.05,
            }
        )


def test_positive_cost_efficiency_score_semantics() -> None:
    """Test that cost_efficiency_score has positive polarity (100 = easy/feasible, increases FinalScore)."""
    dna = ChannelDNA.create_default(niche="AI Engineering", language="en", region="US")
    ctx = ChannelContext(
        channel_id=uuid.uuid4(),
        name="AI Channel",
        slug="ai-channel",
        platform=Platform.YOUTUBE,
        state=ChannelState.ACTIVE,
        primary_language="en",
        target_region="US",
        timezone="UTC",
        dna=dna,
        active_dna_version=1,
    )

    cand_easy = TopicCandidateCreate(
        title="Top 5 AI Tools in 2026",
        keywords=["AI", "tools"],
        manual_cost_efficiency_score=95.0,  # Highly feasible
    )

    cand_hard = TopicCandidateCreate(
        title="Top 5 AI Tools in 2026",
        keywords=["AI", "tools"],
        manual_cost_efficiency_score=10.0,  # Low feasibility
    )

    res_easy = calculate_topic_scores(cand_easy, ctx, DuplicateStatus.FRESH_TOPIC)
    res_hard = calculate_topic_scores(cand_hard, ctx, DuplicateStatus.FRESH_TOPIC)

    assert res_easy["cost_efficiency_score"] == 95.0
    assert res_hard["cost_efficiency_score"] == 10.0
    # Easy candidate receives higher final score
    assert res_easy["final_score"] > res_hard["final_score"]


def test_explainable_reason_codes_generation() -> None:
    """Test that scoring generates concise operational reason codes."""
    dna = ChannelDNA.create_default(niche="Cybersecurity", language="en", region="US")
    dna.content_strategy.content_pillars = ["Malware Analysis", "Zero Trust"]
    ctx = ChannelContext(
        channel_id=uuid.uuid4(),
        name="Cyber Channel",
        slug="cyber-channel",
        platform=Platform.YOUTUBE,
        state=ChannelState.ACTIVE,
        primary_language="en",
        target_region="US",
        timezone="UTC",
        dna=dna,
        active_dna_version=1,
    )

    cand = TopicCandidateCreate(
        title="Zero Trust Architecture Deep Dive",
        keywords=["Zero Trust", "security"],
    )

    res = calculate_topic_scores(cand, ctx, DuplicateStatus.FRESH_TOPIC)

    assert REASON_MATCHES_PRIMARY_PILLAR in res["reasons"]
    assert REASON_FRESH_TOPIC in res["reasons"]
    assert REASON_CONTENT_GAP_FILL in res["reasons"]
    assert res["final_score"] >= 70.0
