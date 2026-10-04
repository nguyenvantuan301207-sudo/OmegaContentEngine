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
from omega.logging import get_logger

logger = get_logger(service="p25d-learning-service")


class LearningService:
    """Canonical application service for P25-D evidence-based learning loop."""

    # In-memory store for isolated execution and tests
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

    @classmethod
    def ingest_causal_experiment_result(
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

        total_sample = sum(attribution_result.sample_basis.values()) if attribution_result.sample_basis else 1000
        quality = 1.0
        if total_sample < 2000:
            quality *= 0.8
        if attribution_result.statistical_inference and not attribution_result.statistical_inference.is_statistically_significant:
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
        cls._evidence_store[evidence.evidence_id] = evidence
        logger.info("learning.evidence_ingested_causal", evidence_id=str(evidence.evidence_id))
        return evidence

    @classmethod
    def ingest_descriptive_snapshot(
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
        direction = "POSITIVE" if delta_value > 0.05 else ("NEGATIVE" if delta_value < -0.05 else "NEUTRAL")

        snap_id = str(getattr(snapshot, "snapshot_id", getattr(snapshot, "id", uuid4())))
        target_id = str(getattr(snapshot, "external_media_id", getattr(snapshot, "asset_id", snap_id)))
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
        cls._evidence_store[evidence.evidence_id] = evidence
        logger.info("learning.evidence_ingested_descriptive", evidence_id=str(evidence.evidence_id))
        return evidence

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

        # 2. Separate primary metric evidence and cross-metric tradeoff evidence
        primary_evidence = [e for e in matching_evidence if e.metric == hyp.predicted_metric]
        other_evidence = [e for e in matching_evidence if e.metric != hyp.predicted_metric]

        if not primary_evidence:
            return hyp, None

        # 3. Aggregate primary evidence with quality and recency weighting
        supporting_ids: list[UUID] = []
        contradicting_ids: list[UUID] = []
        weighted_support = 0.0
        weighted_contradict = 0.0
        causal_count = 0

        for ev in primary_evidence:
            recency_weight = pol.compute_recency_weight(ev.observed_at, as_of=now_utc)
            combined_weight = ev.quality_score * recency_weight

            if ev.causal_status == CausalityStatus.CAUSAL:
                causal_count += 1
                combined_weight *= 1.5

            if ev.effect_direction == hyp.predicted_direction and ev.effect_magnitude > 0:
                supporting_ids.append(ev.evidence_id)
                weighted_support += combined_weight
            elif ev.effect_direction != "NEUTRAL":
                contradicting_ids.append(ev.evidence_id)
                weighted_contradict += combined_weight

        # 4. Check cross-metric tradeoffs (Section 30)
        tradeoffs: list[str] = []
        for other in other_evidence:
            if other.effect_direction == "NEGATIVE" and other.effect_magnitude < (pol.tradeoff_negative_threshold_pct / 100.0):
                tradeoffs.append(
                    f"Tradeoff detected: while {hyp.predicted_metric} improves, "
                    f"{other.metric} drops by {other.effect_magnitude * 100:.1f}%."
                )

        # 5. Determine resulting hypothesis status and confidence
        if contradicting_ids and weighted_contradict > weighted_support:
            new_status = LearningHypothesisStatus.CONTRADICTED
            confidence = LearningConfidence.LOW
        elif tradeoffs:
            # Tradeoffs force cautious classification
            new_status = LearningHypothesisStatus.WEAKENED
            confidence = LearningConfidence.LOW
        elif weighted_support > (weighted_contradict * 2.0) and supporting_ids:
            new_status = LearningHypothesisStatus.SUPPORTED
            if causal_count >= pol.min_causal_replications_for_high:
                confidence = LearningConfidence.HIGH
            elif causal_count == 1:
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
        cls._hypotheses_store[hyp.hypothesis_id] = updated_hyp

        # Build LearningInsight
        metric_impacts = {
            hyp.predicted_metric: sum(e.effect_magnitude for e in primary_evidence) / len(primary_evidence)
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
            limitations.append("Based purely on observational performance; no causal claim established.")
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
        cls._insights_store[insight.insight_id] = insight
        return updated_hyp, insight

    # ── Recommendation Generation & Candidate Adaptations ────────────────────

    @classmethod
    def generate_recommendations(
        cls,
        insight_id: UUID,
        channel_id: UUID,
        policy: LearningPolicy | None = None,
    ) -> list[LearningRecommendation]:
        """Generate ranked, explainable recommendations with candidate adaptations (Sections 18-34)."""
        insight = cls._insights_store.get(insight_id)
        if insight is None:
            raise ValueError(f"Insight {insight_id} not found.")

        hyp = cls._hypotheses_store.get(insight.hypothesis_id)
        if hyp is None:
            raise ValueError(f"Associated hypothesis for insight {insight_id} not found.")

        recommendations: list[LearningRecommendation] = []

        # Case 1: Inconclusive, Tradeoffs, or Low Observational Evidence -> RUN_FOLLOWUP_EXPERIMENT
        if insight.tradeoffs or insight.confidence in (LearningConfidence.VERY_LOW, LearningConfidence.LOW):
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
                ranking_score=0.85,  # High ranking because resolving uncertainty is paramount
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
            cls._recommendations_store[rec.recommendation_id] = rec
            return recommendations

        # Case 2: Supported Causal Evidence -> CONSIDER_TITLE_STYLE / CONSIDER_THUMBNAIL_STYLE
        if hyp.status == LearningHypothesisStatus.SUPPORTED and insight.causal_status == CausalityStatus.CAUSAL:
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
            cls._adaptations_store[adaptation.adaptation_id] = adaptation

            # Ranking score: weighted by confidence and lift
            lift = insight.metric_impacts.get(hyp.predicted_metric, 0.0)
            base_rank = 0.9 if insight.confidence == LearningConfidence.HIGH else 0.75
            ranking_score = min(1.0, base_rank + (lift * 0.2))

            rec = LearningRecommendation(
                recommendation_id=uuid4(),
                action=action,
                target_authority="ChannelDNA",
                proposed_change=f"Consider adopting {hyp.statement} for future {insight.scope} productions.",
                scope=insight.scope,
                supporting_insight_id=insight.insight_id,
                confidence=insight.confidence,
                risk_assessment="MODERATE: Requires editorial review to ensure adherence to brand guidelines.",
                expected_metrics={hyp.predicted_metric: f"+{lift * 100:.1f}% estimated relative lift"},
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
            cls._recommendations_store[rec.recommendation_id] = rec

        return recommendations
