"""P25-C Integration Canary Suite for Controlled Experimentation & Causal Attribution.

Covers:
- Section 39: Isolated Experiment Canary
  - Real accepted video artifact
  - Two independently accepted packaging variants (Control: Title A / Thumb A, Treatment: Title B / Thumb B)
  - Comparable mature outcome window (Control: 10,000 imps, 500 clicks, CTR 0.05; Treatment: 10,000 imps, 620 clicks, CTR 0.062)
  - Verifies: absolute difference = 0.012, relative lift = 0.24, z-test significance, 95% Wald CI
  - Replay idempotency: exact same immutable attribution identity / result_id, no duplicate object
  - Inspectable report: provider = FAKE, real provider mutation = NO
- Section 40: Invalid Experiment Canary
  - Case A: control and treatment use different underlying video artifacts for packaging test -> INVALID_EXPERIMENT
  - Case B: treatment data DELAYED -> WAITING_FOR_MATURITY / INSUFFICIENT_DATA, not a winner
  - Case C: variant changed after experiment start -> INVALID_EXPERIMENT, immutability violation
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from omega.application.experiments.attribution_service import AttributionService
from omega.application.experiments.fake_experiment_provider import FakeExperimentProvider
from omega.domain.creative_qa import CreativeQAStatus
from omega.domain.experimentation import (
    AttributionClassification,
    ChangeDimension,
    DataMaturityState,
    ExperimentStatus,
    ExperimentType,
    ExperimentUnit,
    ExperimentVariant,
    VariantRole,
)


@pytest.fixture(autouse=True)
def clean_attribution_storage():
    """Ensure in-memory attribution state is wiped clean before each test."""
    AttributionService.reset_storage()
    yield
    AttributionService.reset_storage()


# ── Section 39: Isolated Experiment Canary ───────────────────────────────────


@pytest.mark.asyncio
async def test_section_39_isolated_experiment_canary(capsys):
    """Execute Section 39 Isolated Experiment Canary with deterministic fake provider data."""
    # 1. Setup accepted video artifact and packaging variants
    accepted_video_artifact_id = uuid4()
    channel_id = uuid4()

    control_variant = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE_AND_THUMBNAIL,
        media_artifact_id=accepted_video_artifact_id,
        packaging_plan_id=uuid4(),
        title="The Physics of Black Holes Explained [Control]",
        thumbnail_concept_id=uuid4(),
        thumbnail_ref="s3://media/thumbnails/control_a.png",
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
        provenance={"p24_gate": "PASSED", "reviewer": "CreativeQA_v2"},
    )

    treatment_variant = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE_AND_THUMBNAIL,
        media_artifact_id=accepted_video_artifact_id,  # IDENTICAL underlying video artifact
        packaging_plan_id=uuid4(),
        title="Inside Black Holes: What Happens at the Event Horizon? [Treatment]",
        thumbnail_concept_id=uuid4(),
        thumbnail_ref="s3://media/thumbnails/treatment_b.png",
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
        provenance={"p24_gate": "PASSED", "reviewer": "CreativeQA_v2"},
    )

    # 2. Define Experiment with locked primary metric
    exp_def, bound_variants = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE_AND_THUMBNAIL,
        hypothesis="A question-driven title and event horizon thumbnail increases CTR by >15%.",
        primary_metric="impressions_ctr",
        secondary_metrics=["average_view_duration", "views"],
        control_variant=control_variant,
        treatment_variants=[treatment_variant],
        experiment_unit=ExperimentUnit.IMPRESSION,
        minimum_sample_size=5000,
        analysis_window_hours=48,
    )
    bound_ctrl = bound_variants[0]
    bound_trt = bound_variants[1]

    # 3. Validate and Start Experiment
    validated_exp = await AttributionService.validate_experiment(exp_def.experiment_id)
    assert validated_exp.status == ExperimentStatus.READY

    running_exp = await AttributionService.start_experiment(exp_def.experiment_id)
    assert running_exp.status == ExperimentStatus.RUNNING

    # 4. Configure Deterministic Fake Experiment Provider
    fake_provider = FakeExperimentProvider(provider_name="FAKE")
    fake_provider.configure_freshness(exp_def.experiment_id, "FRESH")

    # Control: 10,000 impressions, 500 clicks, CTR = 0.05
    fake_provider.configure_variant_metrics(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_ctrl.variant_id,
        metrics={
            "impressions": 10000,
            "clicks": 500,
            "impressions_ctr": 0.05,
            "views": 490,
            "average_view_duration": 210.0,
        },
        sample_count=10000,
    )

    # Treatment: 10,000 impressions, 620 clicks, CTR = 0.062
    fake_provider.configure_variant_metrics(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_trt.variant_id,
        metrics={
            "impressions": 10000,
            "clicks": 620,
            "impressions_ctr": 0.062,
            "views": 610,
            "average_view_duration": 215.0,
        },
        sample_count=10000,
    )

    # 5. Execute Causal Attribution Analysis
    as_of_time = datetime.now(UTC) + timedelta(hours=48)
    attribution_result = await AttributionService.analyze_experiment(
        experiment_id=exp_def.experiment_id,
        provider=fake_provider,
        as_of=as_of_time,
    )

    # 6. Verify Effect Size & Mathematical Soundness
    assert attribution_result.control_value == 0.05
    assert attribution_result.treatment_value == 0.062
    assert attribution_result.absolute_difference == 0.012
    assert attribution_result.relative_lift == 0.24  # +24.0% relative lift
    assert attribution_result.data_maturity == DataMaturityState.MATURE
    assert attribution_result.classification == AttributionClassification.TREATMENT_BETTER

    # Verify inferential statistics
    inf = attribution_result.statistical_inference
    assert inf is not None
    assert inf.is_statistically_significant is True
    assert inf.method == "TWO_PROPORTION_POOLED_Z_TEST"
    assert inf.p_value is not None and inf.p_value < 0.001
    assert inf.confidence_interval is not None
    ci_lower, ci_upper = inf.confidence_interval
    assert ci_lower > 0.0  # Entire 95% CI is strictly positive
    assert ci_upper > ci_lower

    # 7. Replay Analysis: Verify Idempotency & Historical Immutability
    replay_result = await AttributionService.analyze_experiment(
        experiment_id=exp_def.experiment_id,
        provider=fake_provider,
        as_of=as_of_time + timedelta(hours=1),
    )
    assert replay_result.result_id == attribution_result.result_id
    assert replay_result.evaluated_at == attribution_result.evaluated_at
    assert replay_result.relative_lift == attribution_result.relative_lift
    assert replay_result.classification == attribution_result.classification

    # 8. Report Canary Facts
    print("\n" + "=" * 70)
    print("P25-C ISOLATED EXPERIMENT CANARY REPORT")
    print("=" * 70)
    print(f"experiment ID: {exp_def.experiment_id}")
    print(f"control variant: {bound_ctrl.variant_id}")
    print(f"treatment variant: {bound_trt.variant_id}")
    print(f"primary metric: {exp_def.primary_metric}")
    print(f"control value: {attribution_result.control_value}")
    print(f"treatment value: {attribution_result.treatment_value}")
    print(f"absolute difference: {attribution_result.absolute_difference}")
    print(f"relative lift: {attribution_result.relative_lift}")
    print(f"sample sizes: {attribution_result.sample_basis}")
    print(f"confidence interval: {inf.confidence_interval} (method={inf.method})")
    print(f"analysis maturity: {attribution_result.data_maturity.value}")
    print(f"classification: {attribution_result.classification.value}")
    print("provider: FAKE")
    print("real provider mutation: NO")
    print("=" * 70)


# ── Section 40: Invalid Experiment Canary ────────────────────────────────────


@pytest.mark.asyncio
async def test_section_40_case_a_different_underlying_video_artifacts():
    """Case A: Control and treatment use different underlying video artifacts for packaging test -> INVALID."""
    channel_id = uuid4()
    artifact_a = uuid4()
    artifact_b = uuid4()

    control_variant = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a,
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )
    mismatched_treatment = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_b,  # DIFFERENT underlying video artifact!
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )

    # Creation gate strictly blocks this confounding design
    with pytest.raises(ValueError) as exc:
        await AttributionService.create_experiment(
            channel_id=channel_id,
            experiment_type=ExperimentType.TITLE,
            hypothesis="Testing title with mismatched video renders",
            primary_metric="impressions_ctr",
            control_variant=control_variant,
            treatment_variants=[mismatched_treatment],
        )
    assert "Treatment isolation violated" in str(exc.value)


@pytest.mark.asyncio
async def test_section_40_case_b_treatment_data_delayed():
    """Case B: Treatment telemetry delayed -> WAITING_FOR_MATURITY / INSUFFICIENT_DATA, not a winner."""
    channel_id = uuid4()
    artifact_id = uuid4()

    control_variant = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_id,
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )
    treatment_variant = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_id,
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )

    exp_def, _ = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Title test under delayed provider ingestion",
        primary_metric="impressions_ctr",
        control_variant=control_variant,
        treatment_variants=[treatment_variant],
    )
    await AttributionService.validate_experiment(exp_def.experiment_id)
    await AttributionService.start_experiment(exp_def.experiment_id)

    fake_provider = FakeExperimentProvider(provider_name="FAKE")
    # Simulate provider ingestion lag
    fake_provider.configure_freshness(exp_def.experiment_id, "DELAYED")

    result = await AttributionService.analyze_experiment(exp_def.experiment_id, fake_provider)
    assert result.data_maturity == DataMaturityState.DELAYED
    assert result.classification == AttributionClassification.INSUFFICIENT_DATA
    assert result.classification != AttributionClassification.TREATMENT_BETTER
    assert "waiting for maturity" in result.findings[0]


@pytest.mark.asyncio
async def test_section_40_case_c_variant_changed_after_start():
    """Case C: Variant modified after experiment start -> INVALID_EXPERIMENT, immutability violated."""
    channel_id = uuid4()
    artifact_id = uuid4()

    control_variant = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_id,
        title="Initial Valid Control Title",
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )
    treatment_variant = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_id,
        title="Initial Valid Treatment Title",
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )

    exp_def, bound_variants = await AttributionService.create_experiment(
        channel_id=channel_id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Title test where treatment mutates after start",
        primary_metric="impressions_ctr",
        control_variant=control_variant,
        treatment_variants=[treatment_variant],
    )
    bound_trt = bound_variants[1]
    await AttributionService.validate_experiment(exp_def.experiment_id)
    await AttributionService.start_experiment(exp_def.experiment_id)

    # Tamper with variant after start
    tampered_variant = ExperimentVariant(
        variant_id=bound_trt.variant_id,
        experiment_id=exp_def.experiment_id,
        role=bound_trt.role,
        change_dimension=bound_trt.change_dimension,
        media_artifact_id=bound_trt.media_artifact_id,
        title="Tampered Sneaky Title Post Start!",
        is_accepted_p24=True,
        creative_qa_status=CreativeQAStatus.PASS,
    )
    AttributionService.modify_variant_after_start(exp_def.experiment_id, bound_trt.variant_id, tampered_variant)

    fake_provider = FakeExperimentProvider(provider_name="FAKE")
    fake_provider.configure_freshness(exp_def.experiment_id, "FRESH")

    result = await AttributionService.analyze_experiment(exp_def.experiment_id, fake_provider)
    assert result.classification == AttributionClassification.INVALID_EXPERIMENT
    assert any("variant immutability violated" in f for f in result.findings)

    # Experiment definition status marked INVALIDATED
    updated_exp = await AttributionService.get_experiment(exp_def.experiment_id)
    assert updated_exp.status == ExperimentStatus.INVALIDATED
