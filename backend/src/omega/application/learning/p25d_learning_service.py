"""P25-D Canonical Evidence-Based Learning Loop Application Service.

Core Responsibilities:
1. Ingests causal evidence (P25-C) and descriptive evidence (P25-B) into typed LearningEvidence.
2. Manages bounded, falsifiable LearningHypotheses with deterministic deduplication.
3. Evaluates evidence safely, weighting by quality and recency, detecting tradeoffs.
4. Generates immutable LearningInsights with explicit limitations and tradeoffs.
5. Produces ranked, explainable LearningRecommendations.
6. Proposes CandidateAdaptations (status=PROPOSED strictly) respecting the approval boundary.
7. Strictly prevents automatic mutation of ChannelDNA, CreativeStylePlan, or NarrativePlan.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.learning.learning_repository import LearningRepository
from omega.domain.experimentation import AttributionClassification, AttributionResult
from omega.domain.learning_loop import (
    AdaptationStatus,
    CandidateAdaptation,
    CausalityStatus,
    EvidenceTier,
    LearningConfidence,
    LearningEvidence,
    LearningHypothesis,
    LearningHypothesisStatus,
    LearningInsight,
    LearningPolicy,
    LearningRecommendation,
    RecommendationAction,
)
from omega.domain.performance_analytics import PerformanceSnapshot
from omega.infrastructure.models import (
    ExperimentAttributionResultModel,
    ExperimentRevision,
    ExperimentRoot,
    LearningEvaluationModel,
    LearningInsightModel,
)
from omega.logging import get_logger

logger = get_logger(service="p25d-learning-service")


class LearningRules:
    """Pure transformations; dict/list values are transient inputs and outputs."""

    @classmethod
    def _causal_evidence_core(
        cls,
        attribution_result: AttributionResult,
        channel_id: UUID,
        creative_dimensions: list[str],
        content_format: str = "TUTORIAL",
        content_pillar: str = "SCIENCE",
        target_scope_type: str = "MEDIA_ARTIFACT",
        target_scope_id: str = "",
        policy: LearningPolicy | None = None,
    ) -> LearningEvidence | None:
        """Transform a valid completed P25-C AttributionResult into Tier 1 Causal Evidence."""
        # Gate: Reject invalid or immature experiments
        if attribution_result.classification == AttributionClassification.INVALID_EXPERIMENT:
            logger.warning(
                "learning.evidence_rejected_invalid_experiment",
                result_id=str(attribution_result.result_id),
                findings=attribution_result.findings,
            )
            return None

        if attribution_result.data_maturity.value != "MATURE":
            logger.warning(
                "learning.evidence_rejected_immature_data",
                result_id=str(attribution_result.result_id),
                maturity=attribution_result.data_maturity.value,
            )
            return None

        # Determine effect direction
        rel_lift = attribution_result.relative_lift or 0.0
        if attribution_result.classification == AttributionClassification.TREATMENT_BETTER:
            direction = "POSITIVE"
        elif attribution_result.classification == AttributionClassification.CONTROL_BETTER:
            direction = "NEGATIVE"
        else:
            direction = "NEUTRAL"

        pol = policy or LearningPolicy()
        if attribution_result.classification not in (
            AttributionClassification.TREATMENT_BETTER,
            AttributionClassification.CONTROL_BETTER,
            AttributionClassification.NO_MEANINGFUL_DIFFERENCE,
        ):
            return None
        if (
            not attribution_result.statistical_inference
            or not attribution_result.statistical_inference.is_statistically_significant
        ):
            return None
        if (
            not attribution_result.sample_basis
            or min(attribution_result.sample_basis.values()) < pol.min_sample_size_per_evidence
        ):
            return None
        total_sample = (
            sum(attribution_result.sample_basis.values())
            if attribution_result.sample_basis
            else 1000
        )
        quality = 1.0
        if total_sample < 2000:
            quality *= 0.8
        if (
            attribution_result.statistical_inference
            and not attribution_result.statistical_inference.is_statistically_significant
        ):
            quality *= 0.6

        evidence = LearningEvidence(
            evidence_id=uuid4(),
            evidence_tier=EvidenceTier.TIER_1_CAUSAL_EXPERIMENT,
            causal_status=CausalityStatus.CAUSAL,
            source_phase="P25-C",
            source_ids=[str(attribution_result.result_id)],
            channel_id=channel_id,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
            content_format=content_format,
            content_pillar=content_pillar,
            creative_dimensions=creative_dimensions,
            metric=attribution_result.primary_metric,
            effect_direction=direction,
            effect_magnitude=rel_lift,
            sample_basis=attribution_result.sample_basis,
            quality_score=quality,
            observed_at=attribution_result.evaluated_at,
            provenance={
                "experiment_id": str(attribution_result.experiment_id),
                "classification": attribution_result.classification.value,
                "input_fingerprint": attribution_result.input_lineage_fingerprint,
            },
        )
        logger.info("learning.evidence_ingested_causal", evidence_id=str(evidence.evidence_id))
        return evidence

    @classmethod
    def _descriptive_evidence_core(
        cls,
        snapshot: PerformanceSnapshot,
        metric_name: str,
        delta_value: float,
        channel_id: UUID,
        creative_dimensions: list[str] | None = None,
        content_format: str = "TUTORIAL",
        content_pillar: str = "SCIENCE",
    ) -> LearningEvidence:
        """Ingest P25-B performance metrics as Tier 4 Descriptive Association (NEVER marked CAUSAL)."""
        direction = (
            "POSITIVE" if delta_value > 0.05 else ("NEGATIVE" if delta_value < -0.05 else "NEUTRAL")
        )

        snap_id = str(getattr(snapshot, "snapshot_id", getattr(snapshot, "id", uuid4())))
        target_id = str(
            getattr(snapshot, "external_media_id", getattr(snapshot, "asset_id", snap_id))
        )
        evidence = LearningEvidence(
            evidence_id=uuid4(),
            evidence_tier=EvidenceTier.TIER_4_DESCRIPTIVE_ASSOCIATION,
            causal_status=CausalityStatus.DESCRIPTIVE,  # P25-B alone is NEVER CAUSAL
            source_phase="P25-B",
            source_ids=[snap_id],
            channel_id=channel_id,
            target_scope_type="PERFORMANCE_SNAPSHOT",
            target_scope_id=target_id,
            content_format=content_format,
            content_pillar=content_pillar,
            creative_dimensions=creative_dimensions or [],
            metric=metric_name,
            effect_direction=direction,
            effect_magnitude=delta_value,
            sample_basis={"observed_at": snapshot.observed_at.isoformat()},
            quality_score=0.7,
            observed_at=snapshot.observed_at,
            provenance={"target_id": target_id},
        )
        logger.info("learning.evidence_ingested_descriptive", evidence_id=str(evidence.evidence_id))
        return evidence

    @classmethod
    def _evaluate_core(
        cls,
        hyp: LearningHypothesis,
        matching_evidence: list[LearningEvidence],
        pol: LearningPolicy,
        now_utc: datetime,
    ) -> tuple[LearningHypothesis, LearningInsight | None]:
        """Core deterministic evaluation logic shared between sync and async implementations."""
        # 1. Separate primary metric evidence and cross-metric tradeoff evidence
        primary_evidence = [e for e in matching_evidence if e.metric == hyp.predicted_metric]
        other_evidence = [e for e in matching_evidence if e.metric != hyp.predicted_metric]

        if not primary_evidence:
            return hyp, None

        # 2. Aggregate primary evidence with quality and recency weighting
        supporting_ids: list[UUID] = []
        contradicting_ids: list[UUID] = []
        weighted_support = 0.0
        weighted_contradict = 0.0
        causal_replications: set[str] = set()

        for ev in primary_evidence:
            recency_weight = pol.compute_recency_weight(ev.observed_at, as_of=now_utc)
            combined_weight = ev.quality_score * recency_weight

            if ev.causal_status == CausalityStatus.CAUSAL:
                if (
                    ev.sample_basis
                    and min(ev.sample_basis.values()) >= pol.min_sample_size_per_evidence
                ):
                    causal_replications.add(
                        ev.provenance.get("experiment_root_id")
                        or ev.provenance.get("experiment_id")
                        or ev.source_ids[0]
                    )
                combined_weight *= pol.causal_weight

            if ev.effect_direction == hyp.predicted_direction and ev.effect_magnitude != 0:
                supporting_ids.append(ev.evidence_id)
                weighted_support += combined_weight
            elif ev.effect_direction != "NEUTRAL":
                contradicting_ids.append(ev.evidence_id)
                weighted_contradict += combined_weight

        causal_count = len(causal_replications)

        # 3. Check cross-metric tradeoffs (Section 30)
        tradeoffs: list[str] = []
        for other in other_evidence:
            if other.effect_direction == "NEGATIVE" and other.effect_magnitude < (
                pol.tradeoff_negative_threshold_pct / 100.0
            ):
                tradeoffs.append(
                    f"Tradeoff detected: while {hyp.predicted_metric} improves, "
                    f"{other.metric} drops by {other.effect_magnitude * 100:.1f}%."
                )

        # 4. Determine resulting hypothesis status and confidence
        if contradicting_ids and weighted_contradict > weighted_support:
            new_status = LearningHypothesisStatus.CONTRADICTED
            confidence = LearningConfidence.LOW
        elif tradeoffs:
            # Tradeoffs force cautious classification
            new_status = LearningHypothesisStatus.WEAKENED
            confidence = LearningConfidence.LOW
        elif weighted_support > (weighted_contradict * pol.support_ratio) and supporting_ids:
            new_status = LearningHypothesisStatus.SUPPORTED
            if causal_count >= pol.min_causal_replications_for_high:
                confidence = LearningConfidence.HIGH
            elif causal_count >= 1:
                confidence = LearningConfidence.MODERATE
            else:
                # Observational only -> Bounded at LOW (Section 16, 28)
                confidence = LearningConfidence.LOW
        else:
            new_status = LearningHypothesisStatus.INCONCLUSIVE
            confidence = LearningConfidence.VERY_LOW

        # Determine overall causal status for the insight
        if causal_count >= 1 and not tradeoffs:
            causal_status = CausalityStatus.CAUSAL
        elif any(e.causal_status == CausalityStatus.DESCRIPTIVE for e in primary_evidence):
            causal_status = CausalityStatus.DESCRIPTIVE
        else:
            causal_status = CausalityStatus.ASSOCIATIONAL

        # Update hypothesis state
        updated_hyp = LearningHypothesis(
            hypothesis_id=hyp.hypothesis_id,
            channel_id=hyp.channel_id,
            target_scope_type=hyp.target_scope_type,
            target_scope_id=hyp.target_scope_id,
            creative_dimension=hyp.creative_dimension,
            content_pillar=hyp.content_pillar,
            content_format=hyp.content_format,
            predicted_metric=hyp.predicted_metric,
            predicted_direction=hyp.predicted_direction,
            statement=hyp.statement,
            status=new_status,
            evidence_requirements=hyp.evidence_requirements,
            revision_number=hyp.revision_number + 1,
            supersedes_hypothesis_id=hyp.hypothesis_id,
            created_at=hyp.created_at,
            provenance={**hyp.provenance, "evaluated_at": now_utc.isoformat()},
        )

        # Build LearningInsight
        metric_impacts = {
            hyp.predicted_metric: sum(e.effect_magnitude for e in primary_evidence)
            / len(primary_evidence)
        }
        for o in other_evidence:
            metric_impacts[o.metric] = o.effect_magnitude

        summary = (
            f"Hypothesis '{hyp.statement}' is {new_status.value} with {confidence.value} confidence "
            f"across {len(supporting_ids)} supporting and {len(contradicting_ids)} contradicting items."
        )

        limitations = [
            f"Context bounded to {hyp.content_pillar} / {hyp.content_format}.",
        ]
        if causal_count == 0:
            limitations.append(
                "Based purely on observational performance; no causal claim established."
            )
        if tradeoffs:
            limitations.extend(tradeoffs)

        insight = LearningInsight(
            insight_id=uuid4(),
            hypothesis_id=hyp.hypothesis_id,
            scope=f"{hyp.content_pillar}:{hyp.content_format}",
            summary=summary,
            causal_status=causal_status,
            confidence=confidence,
            supporting_evidence_ids=supporting_ids,
            contradicting_evidence_ids=contradicting_ids,
            metric_impacts=metric_impacts,
            tradeoffs=tradeoffs,
            limitations=limitations,
            policy_version=pol.policy_version,
            generated_at=now_utc,
            provenance={"causal_replications": causal_count},
        )
        return updated_hyp, insight

    @classmethod
    def _recommendations_core(
        cls,
        insight: LearningInsight,
        hyp: LearningHypothesis,
        policy: LearningPolicy | None = None,
    ) -> list[LearningRecommendation]:
        """Generate ranked, explainable recommendations with candidate adaptations (Sections 18-34)."""
        pol = policy or LearningPolicy()

        recommendations: list[LearningRecommendation] = []

        # Case 1: Inconclusive, Tradeoffs, or Low Observational Evidence -> RUN_FOLLOWUP_EXPERIMENT
        if insight.tradeoffs or insight.confidence in (
            LearningConfidence.VERY_LOW,
            LearningConfidence.LOW,
        ):
            rec = LearningRecommendation(
                recommendation_id=uuid4(),
                action=RecommendationAction.RUN_FOLLOWUP_EXPERIMENT,
                target_authority="AttributionService",
                proposed_change=(
                    f"Design follow-up experiment isolating {hyp.creative_dimension} to clarify "
                    f"conflicting findings / tradeoffs on {hyp.predicted_metric}."
                ),
                scope=insight.scope,
                supporting_insight_id=insight.insight_id,
                confidence=insight.confidence,
                risk_assessment="LOW: controlled experiment avoids unvalidated production changes.",
                expected_metrics={hyp.predicted_metric: "Resolved causal attribution"},
                tradeoffs=insight.tradeoffs,
                ranking_score=pol.followup_rank,  # High ranking because resolving uncertainty is paramount
                explanation={
                    "what_supports_this": f"{len(insight.supporting_evidence_ids)} initial evidence items.",
                    "what_contradicts_it": f"{len(insight.contradicting_evidence_ids)} contradictions and tradeoffs.",
                    "how_strong_is_evidence": f"Bounded at {insight.confidence.value}.",
                    "where_does_it_apply": insight.scope,
                    "what_are_the_risks": "Minimal risk under controlled assignment.",
                    "what_should_happen_next": "Queue experiment candidate in AttributionService.",
                },
            )
            recommendations.append(rec)
            return recommendations

        # Case 2: Supported Causal Evidence -> CONSIDER_TITLE_STYLE / CONSIDER_THUMBNAIL_STYLE
        if (
            hyp.status == LearningHypothesisStatus.SUPPORTED
            and insight.causal_status == CausalityStatus.CAUSAL
        ):
            action = (
                RecommendationAction.CONSIDER_TITLE_STYLE
                if hyp.creative_dimension == "TITLE"
                else RecommendationAction.CONSIDER_THUMBNAIL_STYLE
            )

            # Create CandidateAdaptation (Strictly PROPOSED status, Section 20)
            adaptation = CandidateAdaptation(
                adaptation_id=uuid4(),
                target_authority="ChannelDNA",
                target_dimension=hyp.creative_dimension,
                current_value="Generic / Declarative style",
                proposed_value=f"Adopt {hyp.statement} pattern",
                supporting_evidence_ids=insight.supporting_evidence_ids,
                confidence=insight.confidence,
                scope=insight.scope,
                validation_requirement="Requires producer sign-off before drafting next ChannelDNARevision.",
                status=AdaptationStatus.PROPOSED,
                provenance={"insight_id": str(insight.insight_id)},
            )

            # Ranking score: weighted by confidence and lift
            lift = insight.metric_impacts.get(hyp.predicted_metric, 0.0)
            base_rank = (
                pol.high_rank
                if insight.confidence == LearningConfidence.HIGH
                else pol.moderate_rank
            )
            ranking_score = min(1.0, base_rank + (lift * pol.lift_rank_weight))

            rec = LearningRecommendation(
                recommendation_id=uuid4(),
                action=action,
                target_authority="ChannelDNA",
                proposed_change=f"Consider adopting {hyp.statement} for future {insight.scope} productions.",
                scope=insight.scope,
                supporting_insight_id=insight.insight_id,
                confidence=insight.confidence,
                risk_assessment="MODERATE: Requires editorial review to ensure adherence to brand guidelines.",
                expected_metrics={
                    hyp.predicted_metric: f"+{lift * 100:.1f}% estimated relative lift"
                },
                tradeoffs=insight.tradeoffs,
                candidate_adaptation=adaptation,
                ranking_score=ranking_score,
                explanation={
                    "what_supports_this": f"{len(insight.supporting_evidence_ids)} causal experiment replications.",
                    "what_contradicts_it": "Zero conflicting tradeoffs detected in observation window.",
                    "how_strong_is_evidence": f"{insight.confidence.value} causal confidence.",
                    "where_does_it_apply": f"Scoped strictly to {insight.scope}.",
                    "what_are_the_risks": "Brand consistency must be maintained; avoid sensational clickbait.",
                    "what_should_happen_next": "Producer reviews proposal in CandidateAdaptation dashboard.",
                },
            )
            recommendations.append(rec)

        return recommendations


class TestOnlyLearningService(LearningRules):
    """TEST_ONLY synchronous compatibility harness for the original logic canaries.

    Application orchestration uses P25DLearningService, which never reads these stores.
    """

    # TEST_ONLY stores for the original synchronous canaries. Canonical services
    # never access them; deleting them leaves every durable answer unchanged.
    _evidence_store: dict[UUID, LearningEvidence] = {}
    _hypotheses_store: dict[UUID, LearningHypothesis] = {}
    _insights_store: dict[UUID, LearningInsight] = {}
    _recommendations_store: dict[UUID, LearningRecommendation] = {}
    _adaptations_store: dict[UUID, CandidateAdaptation] = {}

    @classmethod
    def reset_storage(cls) -> None:
        """Reset in-memory storage (used strictly for test isolation)."""
        cls._evidence_store.clear()
        cls._hypotheses_store.clear()
        cls._insights_store.clear()
        cls._recommendations_store.clear()
        cls._adaptations_store.clear()

    # ── Evidence Ingestion ───────────────────────────────────────────────────

    # ── Hypothesis Management ────────────────────────────────────────────────

    @classmethod
    def register_hypothesis(
        cls,
        channel_id: UUID,
        creative_dimension: str,
        predicted_metric: str,
        predicted_direction: str,
        statement: str,
        content_pillar: str = "SCIENCE",
        content_format: str = "TUTORIAL",
        target_scope_type: str = "MEDIA_ARTIFACT",
        target_scope_id: str = "",
        evidence_requirements: dict[str, Any] | None = None,
    ) -> tuple[LearningHypothesis, bool]:
        """Register a bounded hypothesis with deterministic deduplication (Section 9).

        Returns (hypothesis, is_created_new).
        """
        candidate = LearningHypothesis(
            channel_id=channel_id,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
            creative_dimension=creative_dimension,
            content_pillar=content_pillar,
            content_format=content_format,
            predicted_metric=predicted_metric,
            predicted_direction=predicted_direction,
            statement=statement,
            evidence_requirements=evidence_requirements or {},
        )
        dedupe_key = candidate.compute_dedupe_key()

        # Check existing deduplicated hypothesis
        for existing in cls._hypotheses_store.values():
            if existing.compute_dedupe_key() == dedupe_key and existing.channel_id == channel_id:
                logger.info(
                    "learning.hypothesis_deduplicated",
                    existing_id=str(existing.hypothesis_id),
                    dedupe_key=dedupe_key,
                )
                return existing, False

        cls._hypotheses_store[candidate.hypothesis_id] = candidate
        logger.info("learning.hypothesis_created", hypothesis_id=str(candidate.hypothesis_id))
        return candidate, True

    # ── Evidence Evaluation & Tradeoff Analysis ──────────────────────────────

    @classmethod
    def evaluate_hypothesis(
        cls,
        hypothesis_id: UUID,
        policy: LearningPolicy | None = None,
        as_of: datetime | None = None,
    ) -> tuple[LearningHypothesis, LearningInsight | None]:
        """Evaluate evidence for a hypothesis, producing status updates and an insight (Sections 10-17)."""
        hyp = cls._hypotheses_store.get(hypothesis_id)
        if hyp is None:
            raise ValueError(f"Hypothesis {hypothesis_id} not found.")

        pol = policy or LearningPolicy()
        now_utc = as_of or datetime.now(UTC)

        # 1. Gather matching evidence for the channel and creative dimension
        matching_evidence = [
            e
            for e in cls._evidence_store.values()
            if e.channel_id == hyp.channel_id
            and hyp.creative_dimension in e.creative_dimensions
            and (e.content_pillar == hyp.content_pillar or hyp.content_pillar == "GENERAL")
        ]

        if not matching_evidence:
            logger.info("learning.no_evidence_for_hypothesis", hypothesis_id=str(hypothesis_id))
            return hyp, None

        updated_hyp, insight = cls._evaluate_core(hyp, matching_evidence, pol, now_utc)
        if updated_hyp:
            cls._hypotheses_store[hyp.hypothesis_id] = updated_hyp
        if insight:
            cls._insights_store[insight.insight_id] = insight
        return updated_hyp, insight

    # ── Recommendation Generation & Candidate Adaptations ────────────────────

    @classmethod
    def ingest_causal_experiment_result(cls, *args, **kwargs):
        evidence = cls._causal_evidence_core(*args, **kwargs)
        if evidence is not None:
            cls._evidence_store[evidence.evidence_id] = evidence
        return evidence

    @classmethod
    def ingest_descriptive_snapshot(cls, *args, **kwargs):
        evidence = cls._descriptive_evidence_core(*args, **kwargs)
        cls._evidence_store[evidence.evidence_id] = evidence
        return evidence

    @classmethod
    def generate_recommendations(cls, insight_id, channel_id, policy=None):
        insight = cls._insights_store.get(insight_id)
        if insight is None:
            raise ValueError("Insight not found")
        hyp = cls._hypotheses_store.get(insight.hypothesis_id)
        if hyp is None:
            raise ValueError("Associated hypothesis not found")
        recs = cls._recommendations_core(insight, hyp, policy)
        for rec in recs:
            cls._recommendations_store[rec.recommendation_id] = rec
            if rec.candidate_adaptation:
                cls._adaptations_store[rec.candidate_adaptation.adaptation_id] = (
                    rec.candidate_adaptation
                )
        return recs


class P25DLearningService:
    """Canonical DB-backed orchestration. No process-local learning authority.

    Transactions belong to the caller. Durable reads always use pinned revision IDs.
    The compatibility harness's pure functions have no access to its TEST_ONLY stores.
    """

    def __init__(self, repository: LearningRepository | None = None):
        self.repository = repository or LearningRepository()

    async def async_ingest_causal_experiment_result(
        self,
        session,
        attribution_result,
        channel_id,
        creative_dimensions,
        content_format="TUTORIAL",
        content_pillar="SCIENCE",
        target_scope_type="MEDIA_ARTIFACT",
        target_scope_id="",
        policy=None,
    ):
        # Validate the supplied object against exact persisted P25-C truth before deriving evidence.
        row = await session.get(ExperimentAttributionResultModel, attribution_result.result_id)
        if row is None:
            raise ValueError("Exact durable P25-C AttributionResult required")
        revision = await session.get(ExperimentRevision, row.experiment_revision_id)
        root = await session.get(ExperimentRoot, revision.experiment_root_id)
        if root.channel_id != channel_id or root.id != attribution_result.experiment_id:
            raise ValueError("Attribution experiment/channel lineage mismatch")
        for field, actual in {
            "control_variant_id": row.control_variant_id,
            "treatment_variant_id": row.treatment_variant_id,
            "primary_metric": row.metric,
            "relative_lift": row.relative_lift,
            "classification": row.classification,
            "data_maturity": row.data_maturity,
            "input_lineage_fingerprint": row.input_lineage_fingerprint,
            "sample_basis": row.sample_basis,
            "evaluated_at": row.evaluated_at,
        }.items():
            if getattr(attribution_result, field) != actual:
                raise ValueError(f"Attribution source mismatch: {field}")
        supplied_inference = (
            attribution_result.statistical_inference.model_dump(mode="json")
            if attribution_result.statistical_inference
            else None
        )
        if supplied_inference != row.statistical_inference:
            raise ValueError("Statistical inference source mismatch")
        evidence = LearningRules._causal_evidence_core(
            attribution_result,
            channel_id,
            creative_dimensions,
            content_format,
            content_pillar,
            target_scope_type,
            target_scope_id,
            policy,
        )
        if evidence is None:
            return None
        model = await self.repository.persist_evidence(session, evidence)
        return await self.repository.load_evidence(session, model.id)

    async def async_ingest_descriptive_snapshot(
        self,
        session,
        snapshot,
        metric_name,
        delta_value,
        channel_id,
        creative_dimensions=None,
        content_format="TUTORIAL",
        content_pillar="SCIENCE",
    ):
        evidence = LearningRules._descriptive_evidence_core(
            snapshot,
            metric_name,
            delta_value,
            channel_id,
            creative_dimensions,
            content_format,
            content_pillar,
        )
        model = await self.repository.persist_evidence(session, evidence)
        return await self.repository.load_evidence(session, model.id)

    async def async_register_hypothesis(self, session, hypothesis):
        existing = await self.repository.find_hypothesis_by_id(session, hypothesis.hypothesis_id)
        root, revision = await self.repository.get_or_create_hypothesis(session, hypothesis)
        return await self.repository.load_hypothesis_revision(
            session, revision.id
        ), existing is None and root.id == hypothesis.hypothesis_id

    async def evaluate_revision(
        self,
        session: AsyncSession,
        hypothesis_revision_id: UUID,
        evidence_ids: list[UUID],
        policy_revision_id: UUID,
        as_of: datetime | None = None,
    ) -> LearningInsight:
        hyp = await self.repository.load_hypothesis_revision(session, hypothesis_revision_id)
        pol = await self.repository.load_policy_revision(session, policy_revision_id)
        evidence = [
            await self.repository.load_evidence(session, e)
            for e in sorted(set(evidence_ids), key=str)
        ]
        # Context bounds are checked before identity or inference is formed.
        for e in evidence:
            if (
                e.channel_id != hyp.channel_id
                or hyp.creative_dimension not in e.creative_dimensions
                or (hyp.content_pillar != "GENERAL" and e.content_pillar != hyp.content_pillar)
                or (hyp.content_format != "DEFAULT" and e.content_format != hyp.content_format)
                or (
                    hyp.target_scope_id
                    and (
                        e.target_scope_id != hyp.target_scope_id
                        or e.target_scope_type != hyp.target_scope_type
                    )
                )
            ):
                raise ValueError("Evidence falls outside the hypothesis context")
        fp = self.repository.fingerprint({"evidence_ids": [str(e.evidence_id) for e in evidence]})
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"p25d-evaluation:{hypothesis_revision_id}:{policy_revision_id}:{fp}"},
        )
        existing = (
            await session.execute(
                select(LearningEvaluationModel).where(
                    LearningEvaluationModel.hypothesis_revision_id == hypothesis_revision_id,
                    LearningEvaluationModel.policy_revision_id == policy_revision_id,
                    LearningEvaluationModel.evidence_set_fingerprint == fp,
                )
            )
        ).scalar_one_or_none()
        if existing:
            insight = (
                await session.execute(
                    select(LearningInsightModel).where(
                        LearningInsightModel.evaluation_id == existing.id
                    )
                )
            ).scalar_one()
            return await self.repository.load_insight(session, insight.id)
        now = as_of or datetime.now(UTC)
        updated, insight = LearningRules._evaluate_core(hyp, evidence, pol, now)
        if insight is None:
            raise ValueError("No matching primary metric evidence")
        memberships = []
        for e in evidence:
            if e.evidence_id in insight.supporting_evidence_ids:
                role = "SUPPORTING"
            elif e.evidence_id in insight.contradicting_evidence_ids:
                role = "CONTRADICTING"
            elif (
                e.metric != hyp.predicted_metric
                and e.effect_direction == "NEGATIVE"
                and e.effect_magnitude < pol.tradeoff_negative_threshold_pct / 100
            ):
                role = "TRADEOFF"
            else:
                role = "CONTEXT"
            memberships.append(
                (e.evidence_id, role, pol.compute_recency_weight(e.observed_at, now))
            )
        evaluation, _ = await self.repository.persist_evaluation(
            session,
            hypothesis_revision_id,
            policy_revision_id,
            fp,
            insight.causal_status,
            updated.status,
            insight.confidence,
            insight.metric_impacts.get(hyp.predicted_metric),
            insight.tradeoffs,
            insight.limitations,
            {**insight.provenance, "as_of": now.isoformat()},
            now,
            memberships,
        )
        insight = insight.model_copy(
            update={
                "evaluation_id": evaluation.id,
                "hypothesis_revision_id": hypothesis_revision_id,
                "policy_revision_id": policy_revision_id,
            }
        )
        row = await self.repository.persist_insight(
            session, insight, evaluation.id, hypothesis_revision_id, policy_revision_id
        )
        return await self.repository.load_insight(session, row.id)

    async def async_evaluate_hypothesis(
        self, session, hypothesis, evidence_items, policy=None, as_of=None
    ):
        _, revision = await self.repository.get_or_create_hypothesis(session, hypothesis)
        _, policy_revision = await self.repository.get_or_create_policy(
            session, policy=policy or LearningPolicy()
        )
        return await self.evaluate_revision(
            session, revision.id, [e.evidence_id for e in evidence_items], policy_revision.id, as_of
        )

    async def async_generate_recommendations(self, session, insight, hypothesis=None, policy=None):
        insight_id = insight if isinstance(insight, UUID) else insight.insight_id
        pinned = await self.repository.load_insight(session, insight_id)
        evaluation = await session.get(LearningEvaluationModel, pinned.evaluation_id)
        if evaluation is None:
            raise ValueError("Persisted insight must pin an immutable evaluation")
        hyp = await self.repository.load_hypothesis_revision(
            session, evaluation.hypothesis_revision_id
        )
        pol = await self.repository.load_policy_revision(session, evaluation.policy_revision_id)
        # Status comes from this exact evaluation, never the mutable hypothesis root.
        hyp = hyp.model_copy(
            update={"status": LearningHypothesisStatus(evaluation.resulting_status)}
        )
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "p25d-recommendation:" + str(insight_id)},
        )
        recs = LearningRules._recommendations_core(pinned, hyp, pol)
        result = []
        for rec in recs:
            row, _ = await self.repository.persist_recommendation(
                session, rec, pinned.insight_id, evaluation.id, evaluation.policy_revision_id
            )
            result.append(await self.repository.load_recommendation(session, row.id))
        return result

    async def async_transition_candidate_adaptation(
        self, session, candidate_id, to_status, actor, reason
    ):
        await self.repository.transition_candidate_adaptation(
            session, candidate_id, to_status, actor, reason
        )
        return await self.repository.load_candidate(session, candidate_id)


# Original imports remain a TEST_ONLY compatibility seam for the 17 logic tests.
# Production callers use P25DLearningService and P25DQueryService explicitly.
LearningService = TestOnlyLearningService
