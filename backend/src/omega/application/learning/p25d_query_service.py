"""P25-D Canonical Learning Query Service (Read-Only).

Provides bounded, safe inspection of learning artifacts:
- Hypotheses
- Insights
- Recommendations
- Candidate Adaptations
- Full Evidence Traceability
"""

from __future__ import annotations

from uuid import UUID

from omega.application.learning.p25d_learning_service import LearningService
from omega.domain.learning_loop import (
    CandidateAdaptation,
    LearningEvidence,
    LearningHypothesis,
    LearningInsight,
    LearningRecommendation,
)


class LearningQueryService:
    """Read-only interface for inspecting learning states, insights, and proposed adaptations."""

    @classmethod
    def list_hypotheses(cls, channel_id: UUID | None = None) -> list[LearningHypothesis]:
        """List active and historical hypotheses, optionally filtered by channel."""
        hyps = list(LearningService._hypotheses_store.values())
        if channel_id:
            hyps = [h for h in hyps if h.channel_id == channel_id]
        return hyps

    @classmethod
    def get_hypothesis(cls, hypothesis_id: UUID) -> LearningHypothesis | None:
        """Fetch a single hypothesis by ID."""
        return LearningService._hypotheses_store.get(hypothesis_id)

    @classmethod
    def get_insight(cls, insight_id: UUID) -> LearningInsight | None:
        """Fetch a single insight by ID."""
        return LearningService._insights_store.get(insight_id)

    @classmethod
    def list_recommendations(cls, channel_id: UUID | None = None) -> list[LearningRecommendation]:
        """List all generated recommendations, sorted by ranking score descending."""
        recs = list(LearningService._recommendations_store.values())
        recs.sort(key=lambda r: r.ranking_score, reverse=True)
        return recs

    @classmethod
    def get_candidate_adaptation(cls, adaptation_id: UUID) -> CandidateAdaptation | None:
        """Fetch candidate adaptation by ID."""
        return LearningService._adaptations_store.get(adaptation_id)

    @classmethod
    def get_evidence_trace(cls, evidence_ids: list[UUID]) -> list[LearningEvidence]:
        """Fetch complete durable evidence trace for auditing and explainability."""
        return [LearningService._evidence_store[e_id] for e_id in evidence_ids if e_id in LearningService._evidence_store]
