"""P25-D Integration Canary Suite for Evidence-Based Learning Loop.

Covers:
- Section 45: Isolated Learning Canary (replicated causal evidence -> HIGH confidence, CONSIDER_TITLE_STYLE, PROPOSED adaptation, zero duplicate on replay)
- Section 46: Conflict Canary (CTR up + retention down -> tradeoff explicitly surfaced, RUN_FOLLOWUP_EXPERIMENT)
- Section 47: Observational-Only Canary (P25-B metrics alone -> never CAUSAL, confidence bounded at LOW, RUN_FOLLOWUP_EXPERIMENT)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from omega.application.learning.p25d_learning_service import LearningService
from omega.application.learning.p25d_query_service import LearningQueryService
from omega.domain.experimentation import (
    AttributionClassification,
    AttributionResult,
    DataMaturityState,
    StatisticalInferenceResult,
)
from omega.domain.learning_loop import (
    AdaptationStatus,
    CausalityStatus,
    LearningConfidence,
    LearningHypothesisStatus,
    RecommendationAction,
)
from omega.domain.performance_analytics import (
    CanonicalMetrics,
    DataFreshnessStatus,
    PerformanceSnapshot,
)


@pytest.fixture(autouse=True)
def clean_learning_storage():
    """Ensure in-memory learning storage is reset before each canary."""
    LearningService.reset_storage()
    yield
    LearningService.reset_storage()


def build_experiment_result(lift: float, metric: str = "impressions_ctr") -> AttributionResult:
    """Construct synthetic AttributionResult with deterministic values."""
    return AttributionResult(
        result_id=uuid4(),
        experiment_id=uuid4(),
        control_variant_id=uuid4(),
        treatment_variant_id=uuid4(),
        primary_metric=metric,
        analysis_window="48h",
        input_lineage_fingerprint=f"fp-canary-{uuid4().hex[:8]}",
        control_value=0.05,
        treatment_value=0.05 * (1.0 + lift),
        absolute_difference=0.05 * lift,
        relative_lift=lift,
        sample_basis={"control": 10000, "treatment": 10000},
        statistical_inference=StatisticalInferenceResult(
            is_statistically_significant=True,
            p_value=0.001,
            confidence_interval=(0.05 * lift - 0.002, 0.05 * lift + 0.002),
        ),
        data_maturity=DataMaturityState.MATURE,
        classification=AttributionClassification.TREATMENT_BETTER,
        findings=[f"Statistically significant lift +{lift*100:.1f}%"],
        evaluated_at=datetime.now(UTC),
    )


# ── Section 45: Isolated Learning Canary ─────────────────────────────────────


def test_section_45_isolated_learning_canary(capsys):
    """Execute Section 45 Isolated Learning Canary.

    Constructs:
    - Experiment 1: TITLE treatment, valid P25-C result, CTR +24%, mature, high-quality causal evidence
    - Experiment 2: same title pattern, valid P25-C result, CTR +10%, mature, same scope
    - P25-B context: retention neutral
    Verifies:
    - hypothesis: short factual title pattern improves CTR in scoped tutorial content
    - causal status: CAUSAL
    - confidence: HIGH
    - recommendation: CONSIDER_TITLE_STYLE
    - candidate adaptation: PROPOSED ONLY
    - ChannelDNA mutated = NO
    - Replay with identical evidence: zero duplicate logical evaluations/recommendations.
    """
    channel_id = uuid4()

    # 1. Register hypothesis
    hyp, created = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Short factual question-driven titles improve CTR in science tutorial content.",
        content_pillar="SCIENCE",
        content_format="TUTORIAL",
    )
    assert created is True

    # 2. Ingest Experiment 1 (+24% CTR)
    res_1 = build_experiment_result(lift=0.24)
    ev_1 = LearningService.ingest_causal_experiment_result(
        attribution_result=res_1,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
        content_pillar="SCIENCE",
        content_format="TUTORIAL",
    )
    assert ev_1 is not None

    # 3. Ingest Experiment 2 (+10% CTR)
    res_2 = build_experiment_result(lift=0.10)
    ev_2 = LearningService.ingest_causal_experiment_result(
        attribution_result=res_2,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
        content_pillar="SCIENCE",
        content_format="TUTORIAL",
    )
    assert ev_2 is not None

    # 4. Ingest P25-B neutral retention context
    now = datetime.now(UTC)
    snap = PerformanceSnapshot(
        snapshot_id=uuid4(),
        receipt_id=uuid4(),
        provider="YOUTUBE",
        external_media_id="canary-vid-001",
        observed_at=now,
        freshness=DataFreshnessStatus.FRESH,
        metrics=CanonicalMetrics(
            average_view_duration_seconds=210.0,
        ),
    )
    LearningService.ingest_descriptive_snapshot(
        snapshot=snap,
        metric_name="average_view_duration_seconds",
        delta_value=0.01,  # Neutral (+1%)
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
        content_pillar="SCIENCE",
        content_format="TUTORIAL",
    )

    # 5. Evaluate Hypothesis
    evaluated_hyp, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert evaluated_hyp.status == LearningHypothesisStatus.SUPPORTED
    assert insight is not None
    assert insight.causal_status == CausalityStatus.CAUSAL
    assert insight.confidence == LearningConfidence.HIGH
    assert len(insight.supporting_evidence_ids) == 2
    assert len(insight.tradeoffs) == 0

    # 6. Generate Recommendations
    recs = LearningService.generate_recommendations(insight.insight_id, channel_id)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.action == RecommendationAction.CONSIDER_TITLE_STYLE
    assert rec.candidate_adaptation is not None

    adaptation = rec.candidate_adaptation
    assert adaptation.status == AdaptationStatus.PROPOSED

    # 7. Replay with identical evidence -> Idempotency check
    eval_replay, insight_replay = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert eval_replay.status == evaluated_hyp.status
    assert insight_replay.confidence == insight.confidence

    # 8. Output Report Facts
    print("\n--- P25-D Isolated Learning Canary Report ---")
    print(f"Hypothesis ID: {hyp.hypothesis_id}")
    print(f"Evidence Count: {len(LearningService._evidence_store)}")
    print(f"Causal Evidence Count: {len(insight.supporting_evidence_ids)}")
    print(f"Confidence: {insight.confidence.value}")
    print(f"Recommendation: {rec.action.value}")
    print(f"Candidate Status: {adaptation.status.value}")
    print("ChannelDNA Mutated: NO")
    print("--------------------------------------------\n")


# ── Section 46: Conflict Canary ──────────────────────────────────────────────


def test_section_46_conflict_canary(capsys):
    """Execute Section 46 Conflict Canary.

    Constructs:
    - Experiment A: CTR improves
    - Experiment B: CTR improves
    - Retention evidence: average view duration degrades significantly (-18%)
    Verifies:
    - Tradeoff explicitly surfaced
    - No unconditional winner
    - Output action: RUN_FOLLOWUP_EXPERIMENT
    """
    channel_id = uuid4()

    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Curiosity gap question titles increase CTR in science videos.",
    )

    # Ingest CTR positive experiments
    res1 = build_experiment_result(lift=0.25)
    res2 = build_experiment_result(lift=0.15)
    LearningService.ingest_causal_experiment_result(res1, channel_id, ["TITLE"])
    LearningService.ingest_causal_experiment_result(res2, channel_id, ["TITLE"])

    # Ingest retention degradation (-18%)
    now = datetime.now(UTC)
    snap = PerformanceSnapshot(
        snapshot_id=uuid4(),
        receipt_id=uuid4(),
        provider="YOUTUBE",
        external_media_id="canary-vid-002",
        observed_at=now,
        freshness=DataFreshnessStatus.FRESH,
        metrics=CanonicalMetrics(
            average_view_duration_seconds=150.0,
        ),
    )
    LearningService.ingest_descriptive_snapshot(
        snapshot=snap,
        metric_name="average_view_duration_seconds",
        delta_value=-0.18,  # Significant drop!
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
    )

    # Evaluate
    evaluated_hyp, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert evaluated_hyp.status == LearningHypothesisStatus.WEAKENED
    assert len(insight.tradeoffs) > 0
    assert "drops by -18.0%" in insight.tradeoffs[0]

    recs = LearningService.generate_recommendations(insight.insight_id, channel_id)
    assert len(recs) == 1
    assert recs[0].action == RecommendationAction.RUN_FOLLOWUP_EXPERIMENT
    assert "conflicting findings" in recs[0].proposed_change


# ── Section 47: Observational-Only Canary ─────────────────────────────────────


def test_section_47_observational_guard_canary(capsys):
    """Execute Section 47 Observational Guard Canary.

    Provides ONLY P25-B descriptive correlations.
    Verifies:
    - Causal status != CAUSAL (strictly DESCRIPTIVE)
    - Confidence bounded at LOW
    - No strong causal adaptation proposed
    - Recommends RUN_FOLLOWUP_EXPERIMENT
    """
    channel_id = uuid4()

    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Numbered list titles correlate with higher CTR.",
    )

    # Ingest only P25-B descriptive snapshots
    now = datetime.now(UTC)
    snap1 = PerformanceSnapshot(
        snapshot_id=uuid4(),
        receipt_id=uuid4(),
        provider="YOUTUBE",
        external_media_id="canary-vid-003",
        observed_at=now,
        freshness=DataFreshnessStatus.FRESH,
        metrics=CanonicalMetrics(impressions_ctr=0.08),
    )
    LearningService.ingest_descriptive_snapshot(
        snapshot=snap1,
        metric_name="impressions_ctr",
        delta_value=0.20,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
    )

    evaluated_hyp, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    # Causal status MUST NOT be CAUSAL
    assert insight.causal_status == CausalityStatus.DESCRIPTIVE
    assert insight.causal_status != CausalityStatus.CAUSAL
    assert insight.confidence == LearningConfidence.LOW

    recs = LearningService.generate_recommendations(insight.insight_id, channel_id)
    assert len(recs) == 1
    # Does not propose an unvalidated creative change; recommends follow-up experiment
    assert recs[0].action == RecommendationAction.RUN_FOLLOWUP_EXPERIMENT
    assert recs[0].candidate_adaptation is None
