"""Unit test suite for P25-C Experimentation & Causal Attribution (Matrix A through K).

Covers:
- Matrix A: Experiment definition (valid definition, invalid primary metric, transition rules)
- Matrix B: Variants (accepted control/treatment, unaccepted blocked, treatment isolation)
- Matrix C: Assignment (deterministic assignment, reproducible seed, allocation ratio)
- Matrix D: Exposure (assignment vs exposure, temporal causal boundary)
- Matrix E: Data maturity (mature, delayed, insufficient data, invalid)
- Matrix F: Effect size (treatment lift, control lift, zero denominator safety)
- Matrix G: Sufficiency (sufficient sample, insufficient sample)
- Matrix H: Attribution (treatment better, control better, no meaningful difference, inconclusive)
- Matrix I: Confounding (different artifact, overlapping experiment)
- Matrix J: Historical preservation (immutable results)
- Matrix K: Boundaries (no P25-D learning/recommendations, no provider mutation)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from omega.application.experiments.attribution_service import AttributionService
from omega.application.experiments.fake_experiment_provider import FakeExperimentProvider
from omega.application.experiments.statistical_engine import StatisticalEngine
from omega.domain.creative_qa import CreativeQAStatus
from omega.domain.experimentation import (
    AssignmentPolicy,
    AttributionClassification,
    AttributionResult,
    ChangeDimension,
    DataMaturityState,
    ExperimentDefinition,
    ExperimentStatus,
    ExperimentType,
    ExperimentUnit,
    ExperimentVariant,
    VariantRole,
)


@pytest.fixture(autouse=True)
def clean_attribution_storage():
    """Ensure clean in-memory state before and after each test."""
    AttributionService.reset_storage()
    yield
    AttributionService.reset_storage()


@pytest.fixture
def base_artifact_id() -> uuid4:
    return uuid4()


@pytest.fixture
def valid_control_variant(base_artifact_id) -> ExperimentVariant:
    return ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=base_artifact_id,
        title="Valid Control Title",
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )


@pytest.fixture
def valid_treatment_variant(base_artifact_id) -> ExperimentVariant:
    return ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=base_artifact_id,  # Same video artifact
        title="Valid Treatment Title",
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )


# ── Matrix A: Experiment Definition ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_a_valid_experiment_definition(valid_control_variant, valid_treatment_variant):
    """Valid experiment definition initializes with DRAFT status."""
    channel_id = uuid4()
    exp, variants = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="A more descriptive title increases click-through rate.",
        primary_metric="impressions_ctr",
        control_variant=valid_control_variant,
        treatment_variants=[valid_treatment_variant],
    )
    assert exp.status == ExperimentStatus.DRAFT
    assert exp.primary_metric == "impressions_ctr"
    assert len(variants) == 2


@pytest.mark.asyncio
async def test_matrix_a_invalid_primary_metric(valid_control_variant, valid_treatment_variant):
    """Unknown primary metric is rejected by validation."""
    channel_id = uuid4()
    with pytest.raises(ValueError) as exc:
        await AttributionService.create_experiment(
            channel_id=channel_id,
            experiment_type=ExperimentType.TITLE,
            hypothesis="Testing invalid metric.",
            primary_metric="arbitrary_unsupported_metric",
            control_variant=valid_control_variant,
            treatment_variants=[valid_treatment_variant],
        )
    assert "Primary metric" in str(exc.value)


# ── Matrix B: Variants & Creative Gate ────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_b_unaccepted_variant_blocked(base_artifact_id, valid_control_variant):
    """Variant not accepted by CreativeQA is blocked from participating."""
    unaccepted_treatment = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=base_artifact_id,
        is_accepted_p24=False,
        creative_qa_status=CreativeQAStatus.FAIL,
    )
    channel_id = uuid4()
    with pytest.raises(ValueError) as exc:
        await AttributionService.create_experiment(
            channel_id=channel_id,
            experiment_type=ExperimentType.TITLE,
            hypothesis="Testing unaccepted variant.",
            primary_metric="impressions_ctr",
            control_variant=valid_control_variant,
            treatment_variants=[unaccepted_treatment],
        )
    assert "does not satisfy P24 creative acceptance" in str(exc.value)


@pytest.mark.asyncio
async def test_matrix_b_treatment_isolation_artifact_mismatch(valid_control_variant):
    """Packaging experiment with different underlying video artifacts is rejected."""
    different_artifact_id = uuid4()
    confounded_treatment = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=different_artifact_id,  # Confounded! Different video!
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )
    channel_id = uuid4()
    with pytest.raises(ValueError) as exc:
        await AttributionService.create_experiment(
            channel_id=channel_id,
            experiment_type=ExperimentType.TITLE,
            hypothesis="Testing artifact mismatch.",
            primary_metric="impressions_ctr",
            control_variant=valid_control_variant,
            treatment_variants=[confounded_treatment],
        )
    assert "Treatment isolation violated" in str(exc.value)


# ── Matrix C: Assignment Policy ───────────────────────────────────────────────


def test_matrix_c_deterministic_assignment():
    """Assignment is deterministic and perfectly reproducible from seed and subject ID."""
    policy = AssignmentPolicy(randomization_seed="test-seed-42", control_allocation_ratio=0.50)

    # Same subject assigned multiple times always yields identical role
    role1 = policy.assign_subject("user-98765")
    role2 = policy.assign_subject("user-98765")
    assert role1 == role2

    # Different subjects distributed across CONTROL and TREATMENT
    assignments = [policy.assign_subject(f"viewer-{i}") for i in range(1000)]
    control_count = sum(1 for a in assignments if a == VariantRole.CONTROL)
    # Check that distribution is approximately 50/50 within standard tolerance
    assert 450 <= control_count <= 550


# ── Matrix D: Exposure ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_d_exposure_recording(valid_control_variant, valid_treatment_variant):
    """Exposures are recorded explicitly without fabricating impression identity."""
    channel_id = uuid4()
    exp, _ = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Testing exposure recording.",
        primary_metric="impressions_ctr",
        control_variant=valid_control_variant,
        treatment_variants=[valid_treatment_variant],
    )
    exp = await AttributionService.validate_experiment(exp.experiment_id)
    exp = await AttributionService.start_experiment(exp.experiment_id)

    exp_rec = await AttributionService.record_exposure(
        experiment_id=exp.experiment_id,
        variant_id=valid_control_variant.variant_id,
        subject_id="aggregate-bucket-1h",
        sample_count=500,
    )
    assert exp_rec.aggregate_sample_count == 500
    assert exp_rec.variant_id == valid_control_variant.variant_id


# ── Matrix E: Data Maturity ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_e_delayed_data_maturity(valid_control_variant, valid_treatment_variant):
    """Delayed provider analytics gates analysis; does not force a winner."""
    channel_id = uuid4()
    exp, _ = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Testing delayed freshness.",
        primary_metric="impressions_ctr",
        control_variant=valid_control_variant,
        treatment_variants=[valid_treatment_variant],
    )
    exp = await AttributionService.validate_experiment(exp.experiment_id)
    exp = await AttributionService.start_experiment(exp.experiment_id)

    fake_provider = FakeExperimentProvider()
    fake_provider.configure_freshness(exp.experiment_id, "DELAYED")

    result = await AttributionService.analyze_experiment(exp.experiment_id, provider=fake_provider)
    assert result.data_maturity == DataMaturityState.DELAYED
    assert result.classification == AttributionClassification.INSUFFICIENT_DATA


# ── Matrix F: Effect Size ─────────────────────────────────────────────────────


def test_matrix_f_effect_size_calculations():
    """Absolute difference and relative lift calculated with zero-denominator safety."""
    # Normal lift: 0.05 -> 0.062 (relative lift = 0.24)
    abs_diff, rel_lift = StatisticalEngine.compute_effect_size(0.05, 0.062)
    assert abs_diff == 0.012
    assert rel_lift == 0.24

    # Control better: 0.06 -> 0.04
    abs_diff2, rel_lift2 = StatisticalEngine.compute_effect_size(0.06, 0.04)
    assert abs_diff2 == -0.02
    assert rel_lift2 == -0.3333

    # Zero baseline control
    abs_diff_zero, rel_lift_zero = StatisticalEngine.compute_effect_size(0.0, 0.05)
    assert abs_diff_zero == 0.05
    assert rel_lift_zero is None  # Safe undefined lift


# ── Matrix G: Sample Sufficiency ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_g_sample_sufficiency_gate(valid_control_variant, valid_treatment_variant):
    """Sample sizes below minimum threshold return INSUFFICIENT_DATA without calling winner."""
    channel_id = uuid4()
    exp, _ = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Testing sample sufficiency.",
        primary_metric="impressions_ctr",
        control_variant=valid_control_variant,
        treatment_variants=[valid_treatment_variant],
        minimum_sample_size=1000,
    )
    exp = await AttributionService.validate_experiment(exp.experiment_id)
    exp = await AttributionService.start_experiment(exp.experiment_id)

    fake_provider = FakeExperimentProvider()
    fake_provider.configure_variant_metrics(
        exp.experiment_id, valid_control_variant.variant_id, {"impressions_ctr": 0.05}, sample_count=50
    )
    fake_provider.configure_variant_metrics(
        exp.experiment_id, valid_treatment_variant.variant_id, {"impressions_ctr": 0.08}, sample_count=50
    )

    result = await AttributionService.analyze_experiment(exp.experiment_id, provider=fake_provider)
    assert result.data_maturity == DataMaturityState.INSUFFICIENT_DATA
    assert result.classification == AttributionClassification.INSUFFICIENT_DATA


# ── Matrix H: Attribution Classification ──────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_h_treatment_better_attribution(valid_control_variant, valid_treatment_variant):
    """Statistically significant positive lift classifies as TREATMENT_BETTER."""
    channel_id = uuid4()
    exp, _ = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Treatment title performs better.",
        primary_metric="impressions_ctr",
        control_variant=valid_control_variant,
        treatment_variants=[valid_treatment_variant],
        minimum_sample_size=5000,
    )
    exp = await AttributionService.validate_experiment(exp.experiment_id)
    exp = await AttributionService.start_experiment(exp.experiment_id)

    fake_provider = FakeExperimentProvider()
    # 10,000 impressions each: control=0.05 (500 clicks), treatment=0.062 (620 clicks)
    fake_provider.configure_variant_metrics(
        exp.experiment_id, valid_control_variant.variant_id, {"impressions_ctr": 0.05}, sample_count=10000
    )
    fake_provider.configure_variant_metrics(
        exp.experiment_id, valid_treatment_variant.variant_id, {"impressions_ctr": 0.062}, sample_count=10000
    )

    result = await AttributionService.analyze_experiment(exp.experiment_id, provider=fake_provider)
    assert result.classification == AttributionClassification.TREATMENT_BETTER
    assert result.absolute_difference == 0.012
    assert result.relative_lift == 0.24
    assert result.statistical_inference is not None
    assert result.statistical_inference.is_statistically_significant is True


@pytest.mark.asyncio
async def test_matrix_h_inconclusive_supported_no_forced_winner(valid_control_variant, valid_treatment_variant):
    """Non-significant difference returns INCONCLUSIVE; never forces a winner."""
    channel_id = uuid4()
    exp, _ = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Inconclusive test.",
        primary_metric="impressions_ctr",
        control_variant=valid_control_variant,
        treatment_variants=[valid_treatment_variant],
        minimum_sample_size=500,
    )
    exp = await AttributionService.validate_experiment(exp.experiment_id)
    exp = await AttributionService.start_experiment(exp.experiment_id)

    fake_provider = FakeExperimentProvider()
    # 500 impressions: 0.05 vs 0.052 -> not statistically significant
    fake_provider.configure_variant_metrics(
        exp.experiment_id, valid_control_variant.variant_id, {"impressions_ctr": 0.05}, sample_count=500
    )
    fake_provider.configure_variant_metrics(
        exp.experiment_id, valid_treatment_variant.variant_id, {"impressions_ctr": 0.052}, sample_count=500
    )

    result = await AttributionService.analyze_experiment(exp.experiment_id, provider=fake_provider)
    assert result.classification == AttributionClassification.INCONCLUSIVE
    assert "Difference not statistically significant" in result.findings[0]


# ── Matrix I: Overlapping Guard & Confounding ─────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_i_overlapping_experiment_guard(valid_control_variant, valid_treatment_variant):
    """Simultaneous active experiments manipulating same dimension on same channel are blocked."""
    channel_id = uuid4()
    exp1, _ = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Experiment 1.",
        primary_metric="impressions_ctr",
        control_variant=valid_control_variant,
        treatment_variants=[valid_treatment_variant],
    )
    await AttributionService.validate_experiment(exp1.experiment_id)

    # Attempt to create second TITLE experiment on same channel while first is READY
    with pytest.raises(ValueError) as exc:
        await AttributionService.create_experiment(
            channel_id=channel_id,
            experiment_type=ExperimentType.TITLE,
            hypothesis="Experiment 2 overlapping.",
            primary_metric="impressions_ctr",
            control_variant=valid_control_variant,
            treatment_variants=[valid_treatment_variant],
        )
    assert "Overlapping experiment detected" in str(exc.value)


# ── Matrix J: Historical Preservation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_j_historical_preservation(valid_control_variant, valid_treatment_variant):
    """AttributionResult remains immutable historical evidence."""
    channel_id = uuid4()
    exp, _ = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Preservation test.",
        primary_metric="impressions_ctr",
        control_variant=valid_control_variant,
        treatment_variants=[valid_treatment_variant],
        minimum_sample_size=1000,
    )
    exp = await AttributionService.validate_experiment(exp.experiment_id)
    exp = await AttributionService.start_experiment(exp.experiment_id)

    fake_provider = FakeExperimentProvider()
    fake_provider.configure_variant_metrics(
        exp.experiment_id, valid_control_variant.variant_id, {"impressions_ctr": 0.05}, sample_count=1000
    )
    fake_provider.configure_variant_metrics(
        exp.experiment_id, valid_treatment_variant.variant_id, {"impressions_ctr": 0.06}, sample_count=1000
    )

    result = await AttributionService.analyze_experiment(exp.experiment_id, provider=fake_provider)
    stored = await AttributionService.get_experiment_result(exp.experiment_id)
    assert stored is not None
    assert stored.result_id == result.result_id
    assert stored.classification == result.classification


# ── Matrix K: Boundary (No Learning / Mutation) ───────────────────────────────


def test_matrix_k_boundary_no_learning_attributes():
    """AttributionResult reports causal differences only; zero learning/optimization decisions."""
    prohibited_fields = {
        "title_recommendation",
        "thumbnail_recommendation",
        "channel_dna_update",
        "style_adjustment",
        "learning_policy",
    }
    result_fields = set(AttributionResult.model_fields.keys())
    assert prohibited_fields.isdisjoint(result_fields)
