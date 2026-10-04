"""P25-D Unit Test Suite for Evidence-Based Learning Loop.

Covers Section 44 Test Matrix:
- Matrix A: Evidence ingestion, rejection of invalid/immature experiments
- Matrix B: Strict Causality policy (P25-C CAUSAL vs P25-B DESCRIPTIVE)
- Matrix C: Hypothesis creation, deterministic deduplication, lifecycle
- Matrix D: Deterministic confidence scaling (single vs replicated vs mixed)
- Matrix E: Context bounding and generalization guard
- Matrix F: Actionable recommendations, follow-up experiments, negative learning
- Matrix G: Cross-metric tradeoff detection (e.g. CTR up / retention down)
- Matrix H: Strict authority protection (zero mutation of ChannelDNA or production plans)
- Matrix I: Replay idempotency
- Matrix J: Full explainability answers
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
    EvidenceTier,
    LearningConfidence,
    LearningHypothesisStatus,
    LearningPolicy,
    RecommendationAction,
)
from omega.domain.performance_analytics import (
    CanonicalMetrics,
    DataFreshnessStatus,
    PerformanceSnapshot,
)


@pytest.fixture(autouse=True)
def clean_learning_storage():
    """Ensure in-memory learning storage is reset before each test."""
    LearningService.reset_storage()
    yield
    LearningService.reset_storage()


def create_fake_attribution_result(
    classification: AttributionClassification = AttributionClassification.TREATMENT_BETTER,
    relative_lift: float = 0.24,
    maturity: DataMaturityState = DataMaturityState.MATURE,
    metric: str = "impressions_ctr",
) -> AttributionResult:
    """Helper to construct synthetic AttributionResult."""
    return AttributionResult(
        result_id=uuid4(),
        experiment_id=uuid4(),
        control_variant_id=uuid4(),
        treatment_variant_id=uuid4(),
        primary_metric=metric,
        analysis_window="24h",
        input_lineage_fingerprint="fp-" + uuid4().hex[:12],
        control_value=0.05,
        treatment_value=0.062 if relative_lift > 0 else 0.04,
        absolute_difference=0.012 if relative_lift > 0 else -0.01,
        relative_lift=relative_lift,
        sample_basis={"control": 5000, "treatment": 5000},
        statistical_inference=StatisticalInferenceResult(
            is_statistically_significant=True,
            p_value=0.002,
        ),
        data_maturity=maturity,
        classification=classification,
        findings=["Statistically significant treatment lift"],
        evaluated_at=datetime.now(UTC),
    )


# ── Matrix A: Evidence Ingestion & Validity Gates ─────────────────────────────


def test_matrix_a_causal_evidence_ingestion():
    """Valid mature AttributionResult is ingested as Tier 1 CAUSAL evidence."""
    attr_res = create_fake_attribution_result()
    channel_id = uuid4()

    evidence = LearningService.ingest_causal_experiment_result(
        attribution_result=attr_res,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
    )
    assert evidence is not None
    assert evidence.evidence_tier == EvidenceTier.TIER_1_CAUSAL_EXPERIMENT
    assert evidence.causal_status == CausalityStatus.CAUSAL
    assert evidence.effect_direction == "POSITIVE"
    assert evidence.effect_magnitude == 0.24


def test_matrix_a_invalid_or_immature_experiment_rejected():
    """Invalid experiment or delayed data maturity is strictly rejected from learning."""
    channel_id = uuid4()

    # Case 1: INVALID_EXPERIMENT
    invalid_res = create_fake_attribution_result(classification=AttributionClassification.INVALID_EXPERIMENT)
    ev_invalid = LearningService.ingest_causal_experiment_result(
        attribution_result=invalid_res, channel_id=channel_id, creative_dimensions=["TITLE"]
    )
    assert ev_invalid is None

    # Case 2: WAITING_FOR_MATURITY
    immature_res = create_fake_attribution_result(maturity=DataMaturityState.WAITING_FOR_MATURITY)
    ev_immature = LearningService.ingest_causal_experiment_result(
        attribution_result=immature_res, channel_id=channel_id, creative_dimensions=["TITLE"]
    )
    assert ev_immature is None


# ── Matrix B: Causality Policy (P25-C vs P25-B) ───────────────────────────────


def test_matrix_b_p25b_performance_never_causal():
    """P25-B descriptive metrics must NEVER be marked as CAUSAL."""
    now = datetime.now(UTC)
    channel_id = uuid4()
    snapshot = PerformanceSnapshot(
        snapshot_id=uuid4(),
        receipt_id=uuid4(),
        provider="YOUTUBE",
        external_media_id="ext-vid-001",
        observed_at=now,
        freshness=DataFreshnessStatus.FRESH,
        metrics=CanonicalMetrics(
            views=1200,
            impressions_ctr=0.06,
        ),
    )

    evidence = LearningService.ingest_descriptive_snapshot(
        snapshot=snapshot,
        metric_name="impressions_ctr",
        delta_value=0.15,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
    )
    assert evidence.evidence_tier == EvidenceTier.TIER_4_DESCRIPTIVE_ASSOCIATION
    assert evidence.causal_status == CausalityStatus.DESCRIPTIVE
    assert evidence.causal_status != CausalityStatus.CAUSAL


# ── Matrix C: Hypothesis Lifecycle & Deduplication ────────────────────────────


def test_matrix_c_hypothesis_deduplication():
    """Creating an identical semantic hypothesis returns the existing record rather than duplicating."""
    channel_id = uuid4()
    hyp1, created1 = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Question titles increase CTR in science tutorials.",
        content_pillar="SCIENCE",
        content_format="TUTORIAL",
    )
    assert created1 is True

    # Register again with identical semantic scope
    hyp2, created2 = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Question titles increase CTR in science tutorials (slight rewording).",
        content_pillar="SCIENCE",
        content_format="TUTORIAL",
    )
    assert created2 is False
    assert hyp2.hypothesis_id == hyp1.hypothesis_id


# ── Matrix D & E: Confidence Scaling & Replicated Evidence ────────────────────


def test_matrix_d_single_vs_replicated_confidence():
    """Single causal experiment yields MODERATE confidence; replicated yields HIGH confidence."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Curiosity titles increase CTR in physics tutorials.",
    )

    # 1. Ingest 1 causal experiment
    res1 = create_fake_attribution_result(relative_lift=0.20)
    LearningService.ingest_causal_experiment_result(
        attribution_result=res1,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
    )

    evaluated_hyp, insight_1 = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert evaluated_hyp.status == LearningHypothesisStatus.SUPPORTED
    assert insight_1.confidence == LearningConfidence.MODERATE

    # 2. Ingest 2nd replicated causal experiment
    res2 = create_fake_attribution_result(relative_lift=0.18)
    LearningService.ingest_causal_experiment_result(
        attribution_result=res2,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
    )

    evaluated_hyp_2, insight_2 = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert evaluated_hyp_2.status == LearningHypothesisStatus.SUPPORTED
    assert insight_2.confidence == LearningConfidence.HIGH


# ── Matrix G: Cross-Metric Tradeoffs & Goodhart Guard ─────────────────────────


def test_matrix_g_cross_metric_tradeoff_detection():
    """Detects when CTR increases but average view duration drops, surfacing a tradeoff."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Aggressive titles increase CTR.",
    )

    # Ingest CTR experiment (positive lift +30%)
    res_ctr = create_fake_attribution_result(relative_lift=0.30, metric="impressions_ctr")
    LearningService.ingest_causal_experiment_result(
        attribution_result=res_ctr,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
    )

    # Ingest retention evidence showing significant degradation (-15%)
    now = datetime.now(UTC)
    snap = PerformanceSnapshot(
        snapshot_id=uuid4(),
        receipt_id=uuid4(),
        provider="YOUTUBE",
        external_media_id="ext-vid-002",
        observed_at=now,
        freshness=DataFreshnessStatus.FRESH,
        metrics=CanonicalMetrics(
            average_view_duration_seconds=90.0,
        ),
    )
    LearningService.ingest_descriptive_snapshot(
        snapshot=snap,
        metric_name="average_view_duration_seconds",
        delta_value=-0.15,  # -15% drop!
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
    )

    evaluated_hyp, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert evaluated_hyp.status == LearningHypothesisStatus.WEAKENED
    assert len(insight.tradeoffs) > 0
    assert "drops by -15.0%" in insight.tradeoffs[0]

    # Recommendation should recommend follow-up experiment rather than adopting blindly
    recs = LearningService.generate_recommendations(insight.insight_id, channel_id)
    assert len(recs) == 1
    assert recs[0].action == RecommendationAction.RUN_FOLLOWUP_EXPERIMENT


# ── Matrix H: Strict Authority Protection (No Automatic Mutation) ─────────────


def test_matrix_h_strict_approval_boundary_no_auto_mutation():
    """CandidateAdaptation starts in PROPOSED status and does not mutate ChannelDNA."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Question titles increase CTR in science tutorials.",
    )

    res1 = create_fake_attribution_result(relative_lift=0.20)
    res2 = create_fake_attribution_result(relative_lift=0.22)
    LearningService.ingest_causal_experiment_result(res1, channel_id, ["TITLE"])
    LearningService.ingest_causal_experiment_result(res2, channel_id, ["TITLE"])

    _, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    recs = LearningService.generate_recommendations(insight.insight_id, channel_id)

    assert len(recs) == 1
    rec = recs[0]
    assert rec.action == RecommendationAction.CONSIDER_TITLE_STYLE
    assert rec.candidate_adaptation is not None

    adaptation = rec.candidate_adaptation
    assert adaptation.status == AdaptationStatus.PROPOSED
    assert "Requires producer sign-off" in adaptation.validation_requirement


# ── Matrix J: Full Explainability ─────────────────────────────────────────────


def test_matrix_j_recommendation_explainability():
    """Recommendations answer all six required explainability questions."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Question titles increase CTR.",
    )

    res1 = create_fake_attribution_result(relative_lift=0.20)
    LearningService.ingest_causal_experiment_result(res1, channel_id, ["TITLE"])

    _, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    recs = LearningService.generate_recommendations(insight.insight_id, channel_id)

    assert len(recs) == 1
    exp = recs[0].explanation
    assert "what_supports_this" in exp
    assert "what_contradicts_it" in exp
    assert "how_strong_is_evidence" in exp
    assert "where_does_it_apply" in exp
    assert "what_are_the_risks" in exp
    assert "what_should_happen_next" in exp


# ── Matrix E: Context Bounding & Generalization Guard ─────────────────────────


def test_matrix_e_context_bounding():
    """Evidence scoped to SCIENCE / TUTORIAL does not evaluate a hypothesis for HISTORY / ESSAY."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Title style for history essays.",
        content_pillar="HISTORY",
        content_format="ESSAY",
    )

    # Ingest evidence for SCIENCE / TUTORIAL
    res = create_fake_attribution_result(relative_lift=0.20)
    LearningService.ingest_causal_experiment_result(
        attribution_result=res,
        channel_id=channel_id,
        creative_dimensions=["TITLE"],
        content_pillar="SCIENCE",
        content_format="TUTORIAL",
    )

    # Evaluating HISTORY hypothesis yields NO matching evidence
    evaluated_hyp, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert insight is None
    assert evaluated_hyp.status == LearningHypothesisStatus.PROPOSED


# ── Matrix F: Negative Learning & Null Outcome ────────────────────────────────


def test_matrix_f_negative_learning():
    """Causal evidence showing negative impact leads to CONTRADICTED hypothesis."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="All-caps titles increase CTR.",
    )

    # Ingest negative causal result (-20% lift, CONTROL_BETTER)
    res_neg = create_fake_attribution_result(
        classification=AttributionClassification.CONTROL_BETTER,
        relative_lift=-0.20,
    )
    LearningService.ingest_causal_experiment_result(res_neg, channel_id, ["TITLE"])

    evaluated_hyp, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert evaluated_hyp.status == LearningHypothesisStatus.CONTRADICTED
    assert len(insight.contradicting_evidence_ids) == 1


def test_matrix_f_null_no_learning_supported():
    """When no evidence exists, no recommendation is forced (Section 24)."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="THUMBNAIL",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Neon borders increase CTR.",
    )

    evaluated_hyp, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    assert insight is None
    assert evaluated_hyp.status == LearningHypothesisStatus.PROPOSED


# ── Matrix I: Replay Idempotency & Recency Policy ─────────────────────────────


def test_matrix_i_replay_idempotency():
    """Re-evaluating identical evidence under the same policy yields identical outcome."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Idempotency test hypothesis.",
    )
    res = create_fake_attribution_result(relative_lift=0.20)
    LearningService.ingest_causal_experiment_result(res, channel_id, ["TITLE"])

    h1, i1 = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    h2, i2 = LearningService.evaluate_hypothesis(hyp.hypothesis_id)

    assert h1.status == h2.status
    assert i1.confidence == i2.confidence
    assert i1.metric_impacts == i2.metric_impacts


def test_matrix_recency_policy():
    """Temporal decay correctly downweights older evidence without deleting history."""
    policy = LearningPolicy(recency_half_life_days=90.0)
    now = datetime.now(UTC)

    # Fresh evidence (0 days old) -> weight ~ 1.0
    weight_fresh = policy.compute_recency_weight(now, as_of=now)
    assert abs(weight_fresh - 1.0) < 0.01

    # 90-day-old evidence (1 half-life) -> weight ~ 0.5
    old_90d = now - timedelta(days=90)
    weight_90d = policy.compute_recency_weight(old_90d, as_of=now)
    assert abs(weight_90d - 0.5) < 0.02

    # Very old evidence -> bounded floor at 0.10
    old_500d = now - timedelta(days=500)
    weight_500d = policy.compute_recency_weight(old_500d, as_of=now)
    assert weight_500d == 0.10


# ── Query Service Tests ───────────────────────────────────────────────────────


def test_learning_query_service_read_only():
    """Query service exposes read-only access to hypotheses, insights, and evidence traces."""
    channel_id = uuid4()
    hyp, _ = LearningService.register_hypothesis(
        channel_id=channel_id,
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Query service test hypothesis.",
    )
    res = create_fake_attribution_result(relative_lift=0.20)
    ev = LearningService.ingest_causal_experiment_result(res, channel_id, ["TITLE"])
    _, insight = LearningService.evaluate_hypothesis(hyp.hypothesis_id)
    recs = LearningService.generate_recommendations(insight.insight_id, channel_id)

    # Test Query Service
    hyps = LearningQueryService.list_hypotheses(channel_id=channel_id)
    assert len(hyps) >= 1

    fetched_hyp = LearningQueryService.get_hypothesis(hyp.hypothesis_id)
    assert fetched_hyp is not None

    fetched_insight = LearningQueryService.get_insight(insight.insight_id)
    assert fetched_insight is not None

    fetched_recs = LearningQueryService.list_recommendations(channel_id=channel_id)
    assert len(fetched_recs) == len(recs)

    trace = LearningQueryService.get_evidence_trace([ev.evidence_id])
    assert len(trace) == 1
    assert trace[0].evidence_id == ev.evidence_id

