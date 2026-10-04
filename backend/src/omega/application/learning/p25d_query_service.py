"""P25-D Canonical Learning Query Service (Read-Only).

Provides bounded, safe inspection of learning artifacts:
- Hypotheses
- Insights
- Recommendations
- Candidate Adaptations
- Full Evidence Traceability
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.learning.learning_repository import LearningRepository
from omega.application.learning.p25d_learning_service import LearningService
from omega.domain.learning_loop import (
    CandidateAdaptation,
    LearningEvidence,
    LearningHypothesis,
    LearningInsight,
    LearningRecommendation,
)


class TestOnlyLearningQueryService:
    """TEST_ONLY compatibility queries for the original synchronous canaries."""

    @classmethod
    def list_hypotheses(cls, channel_id: UUID | None = None) -> list[LearningHypothesis]:
        """List active and historical hypotheses, optionally filtered by channel."""
        hyps = list(LearningService._hypotheses_store.values())
        if channel_id:
            hyps = [h for h in hyps if h.channel_id == channel_id]
        return hyps

    @classmethod
    async def async_list_hypotheses(
        cls,
        session: AsyncSession,
        channel_id: UUID,
    ) -> list[dict[str, Any]]:
        """List active and historical hypotheses from durable DB."""
        roots = await LearningRepository.list_hypotheses(session, channel_id)
        return [
            {
                "hypothesis_id": str(r.id),
                "channel_id": str(r.channel_id),
                "target_dimension": r.target_dimension,
                "target_metric": r.target_metric,
                "status": r.status,
                "revisions_count": len(r.revisions),
                "created_at": r.created_at.isoformat(),
            }
            for r in roots
        ]

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
    async def async_list_recommendations(
        cls,
        session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """List all generated recommendations from durable DB."""
        recs = await LearningRepository.list_recommendations(session)
        return [
            {
                "recommendation_id": str(r.id),
                "insight_id": str(r.insight_id),
                "action": r.action,
                "target_authority": r.target_authority,
                "target_dimension": r.target_dimension,
                "ranking_score": r.ranking_score,
                "risk_level": r.risk_level,
                "confidence": r.confidence,
                "has_candidate_adaptation": len(r.adaptations) > 0,
            }
            for r in recs
        ]

    @classmethod
    def get_candidate_adaptation(cls, adaptation_id: UUID) -> CandidateAdaptation | None:
        """Fetch candidate adaptation by ID."""
        return LearningService._adaptations_store.get(adaptation_id)

    @classmethod
    async def async_get_candidate_adaptation(
        cls,
        session: AsyncSession,
        adaptation_id: UUID,
    ) -> dict[str, Any] | None:
        """Fetch candidate adaptation with approval history from durable DB."""
        cand = await LearningRepository.get_candidate_adaptation(session, adaptation_id)
        if not cand:
            return None
        return {
            "adaptation_id": str(cand.id),
            "recommendation_id": str(cand.recommendation_id),
            "target_authority": cand.target_authority,
            "target_field": cand.target_field,
            "status": cand.status,
            "confidence": cand.confidence,
            "validation_requirement": cand.validation_requirement,
            "history": [
                {
                    "from_status": h.from_status,
                    "to_status": h.to_status,
                    "actor": h.actor,
                    "transition_reason": h.transition_reason,
                    "transitioned_at": h.transitioned_at.isoformat(),
                }
                for h in cand.approval_history
            ],
        }

    @classmethod
    def get_evidence_trace(cls, evidence_ids: list[UUID]) -> list[LearningEvidence]:
        """Fetch complete durable evidence trace for auditing and explainability."""
        return [
            LearningService._evidence_store[e_id]
            for e_id in evidence_ids
            if e_id in LearningService._evidence_store
        ]

    @classmethod
    async def async_get_evidence_trace(
        cls,
        session: AsyncSession,
        hypothesis_id: UUID,
    ) -> dict[str, Any]:
        """Fetch complete reproducible evidence trace from durable DB."""
        return await LearningRepository.get_evidence_trace(session, hypothesis_id)


class P25DQueryService:
    """Canonical read-only DB queries. No dependency on TEST_ONLY stores."""

    def __init__(self, repository: LearningRepository | None = None):
        self.repository = repository or LearningRepository()

    async def async_get_evidence_trace(self, session, hypothesis_id):
        return await self.repository.get_evidence_trace(session, hypothesis_id)

    async def list_hypotheses(self, session, channel_id):
        return await self.repository.list_hypotheses(session, channel_id)

    async def list_recommendations(self, session):
        return await self.repository.list_recommendations(session)

    async def get_evidence(self, session, evidence_id):
        return await self.repository.load_evidence(session, evidence_id)

    async def get_hypothesis_revision(self, session, revision_id):
        return await self.repository.load_hypothesis_revision(session, revision_id)

    async def get_insight(self, session, insight_id):
        return await self.repository.load_insight(session, insight_id)

    async def get_recommendation(self, session, recommendation_id):
        return await self.repository.load_recommendation(session, recommendation_id)

    async def async_get_candidate_adaptation(self, session, candidate_id):
        return await self.repository.load_candidate(session, candidate_id)


LearningQueryService = TestOnlyLearningQueryService
