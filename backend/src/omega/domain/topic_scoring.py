"""Topic scoring profile, similarity thresholds, and reason code models.

Defines canonical weights summing to 1.0, authoritative duplicate thresholds,
and operational explainability constants.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SimilarityProfile(BaseModel):
    """Authoritative thresholds for topic duplicate and similarity detection."""

    fresh_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    related_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    new_angle_min_similarity: float = Field(default=0.50, ge=0.0, le=1.0)
    semantic_duplicate_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    exact_duplicate_threshold: float = Field(default=0.98, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_ordering(self) -> SimilarityProfile:
        if not (
            self.fresh_threshold
            < self.related_threshold
            <= self.new_angle_min_similarity
            < self.semantic_duplicate_threshold
            < self.exact_duplicate_threshold
        ):
            raise ValueError(
                "Similarity thresholds must satisfy: "
                "fresh_threshold < related_threshold <= new_angle_min_similarity "
                "< semantic_duplicate_threshold < exact_duplicate_threshold"
            )
        return self


# Default singleton instance for reuse across modules
DEFAULT_SIMILARITY_PROFILE = SimilarityProfile()


class TopicScoringProfile(BaseModel):
    """Canonical scoring weights and threshold configuration for topic evaluation.

    Weights represent positive contributors to the final score and MUST sum to 1.0.
    """

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "audience_fit": 0.20,
            "strategic_fit": 0.20,
            "trend": 0.15,
            "novelty": 0.15,
            "content_gap": 0.10,
            "historical_performance": 0.10,
            "cost_efficiency": 0.05,
            "revenue_potential": 0.05,
        }
    )
    minimum_recommendation_score: float = Field(default=60.0, ge=0.0, le=100.0)
    recent_duplicate_days: int = Field(default=30, ge=1)
    cooldown_days: int = Field(default=90, ge=1)
    max_recommendations: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validate_weights(self) -> TopicScoringProfile:
        required_keys = {
            "audience_fit",
            "strategic_fit",
            "trend",
            "novelty",
            "content_gap",
            "historical_performance",
            "cost_efficiency",
            "revenue_potential",
        }
        actual_keys = set(self.weights.keys())
        if actual_keys != required_keys:
            missing = required_keys - actual_keys
            extra = actual_keys - required_keys
            raise ValueError(f"Invalid scoring weight dimensions. Missing: {missing}, Extra: {extra}")

        for k, v in self.weights.items():
            if v < 0.0 or v > 1.0:
                raise ValueError(f"Weight '{k}' must be between 0.0 and 1.0 (got {v})")

        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-5:
            raise ValueError(f"Scoring weights must sum to 1.0 (got {total:.5f})")

        return self

    model_config = ConfigDict(extra="forbid")


# Default singleton scoring profile
DEFAULT_SCORING_PROFILE = TopicScoringProfile()


# ── Operational Reason Codes ──

REASON_MATCHES_PRIMARY_PILLAR = "MATCHES_PRIMARY_PILLAR"
REASON_MATCHES_SUBNICHE = "MATCHES_SUBNICHE"
REASON_HIGH_AUDIENCE_RELEVANCE = "HIGH_AUDIENCE_RELEVANCE"
REASON_CONTENT_GAP_FILL = "CONTENT_GAP_FILL"
REASON_FRESH_TOPIC = "FRESH_TOPIC"
REASON_SAME_TOPIC_NEW_ANGLE = "SAME_TOPIC_NEW_ANGLE"
REASON_PENALIZED_RECENT_DUPLICATE = "PENALIZED_RECENT_DUPLICATE"
REASON_PENALIZED_SEMANTIC_DUPLICATE = "PENALIZED_SEMANTIC_DUPLICATE"
REASON_EXACT_DUPLICATE = "EXACT_DUPLICATE"
REASON_STRONG_TREND_SIGNAL = "STRONG_TREND_SIGNAL"
REASON_HIGH_FEASIBILITY = "HIGH_FEASIBILITY"
REASON_LOW_AUDIENCE_FIT = "LOW_AUDIENCE_FIT"
REASON_LOW_STRATEGIC_FIT = "LOW_STRATEGIC_FIT"
