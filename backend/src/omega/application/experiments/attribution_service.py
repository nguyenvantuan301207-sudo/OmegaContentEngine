"""Authoritative Attribution & Experiment Service for P25-C.

Coordinates experiment creation, P24 creative acceptance gating, treatment isolation,
confounding detection, deterministic assignment, data maturity gating, and causal inference.

Strict boundaries:
- P25-C: Answers "what difference can be attributed to the tested variant under controlled comparison?"
- Does NOT mutate ChannelDNA, CreativeStylePlan, NarrativePlan, or PackagingPlan.
- Does NOT trigger automated creative learning (P25-D).
- Does NOT mutate external provider accounts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.experiments.experiment_provider import ExperimentProvider
from omega.application.experiments.statistical_engine import StatisticalEngine
from omega.domain.creative_qa import CreativeQAStatus
from omega.domain.experimentation import (
    AssignmentPolicy,
    AttributionClassification,
    AttributionResult,
    ChangeDimension,
    DataMaturityState,
    ExperimentDefinition,
    ExperimentExposure,
    ExperimentStatus,
    ExperimentType,
    ExperimentUnit,
    ExperimentVariant,
    StatisticalInferenceResult,
    VariantRole,
)
from omega.logging import get_logger

logger = get_logger(service="omega-attribution-service")


class AttributionService:
    """Canonical service for controlled experimentation and causal attribution."""

    # In-memory storage for active experiment state and historical preservation
    _experiments: dict[UUID, ExperimentDefinition] = {}
    _variants: dict[UUID, list[ExperimentVariant]] = {}
    _exposures: dict[UUID, list[ExperimentExposure]] = {}
    _results: dict[UUID, AttributionResult] = {}
    _variant_snapshots: dict[UUID, dict[UUID, dict[str, Any]]] = {}

    @classmethod
    def reset_storage(cls) -> None:
        """Reset in-memory storage (used for test isolation)."""
        cls._experiments.clear()
        cls._variants.clear()
        cls._exposures.clear()
        cls._results.clear()
        cls._variant_snapshots.clear()

    # ── Experiment Creation & Gating ─────────────────────────────────────────

    @classmethod
    def create_experiment(
        cls,
        channel_id: UUID,
        experiment_type: ExperimentType,
        hypothesis: str,
        primary_metric: str,
        control_variant: ExperimentVariant,
        treatment_variants: list[ExperimentVariant],
        secondary_metrics: list[str] | None = None,
        assignment_policy: AssignmentPolicy | None = None,
        experiment_unit: ExperimentUnit = ExperimentUnit.IMPRESSION,
        minimum_sample_size: int = 1000,
        analysis_window_hours: int = 24,
        provenance: dict[str, Any] | None = None,
    ) -> tuple[ExperimentDefinition, list[ExperimentVariant]]:
        """Create and register a new controlled experiment with strict acceptance gating."""
        # 1. Creative Acceptance Gate (Section 6)
        all_variants = [control_variant] + treatment_variants
        for var in all_variants:
            if not var.is_eligible_for_experiment():
                logger.error(
                    "experiment.variant_rejected",
                    variant_id=str(var.variant_id),
                    role=var.role.value,
                    qa_status=var.creative_qa_status.value,
                    is_accepted=var.is_accepted_p24,
                )
                raise ValueError(
                    f"Variant {var.variant_id} ({var.role.value}) does not satisfy P24 creative acceptance "
                    f"(is_accepted={var.is_accepted_p24}, creative_qa_status={var.creative_qa_status.value})."
                )

        # 2. Treatment Isolation / Same-Content Requirement (Section 7)
        is_packaging_experiment = experiment_type in (
            ExperimentType.TITLE,
            ExperimentType.THUMBNAIL,
            ExperimentType.TITLE_AND_THUMBNAIL,
            ExperimentType.DESCRIPTION,
            ExperimentType.PACKAGING_BUNDLE,
        )
        if is_packaging_experiment:
            for trt in treatment_variants:
                if trt.media_artifact_id != control_variant.media_artifact_id:
                    raise ValueError(
                        f"Treatment isolation violated: treatment variant {trt.variant_id} has media_artifact_id "
                        f"{trt.media_artifact_id}, which does not match control artifact {control_variant.media_artifact_id}. "
                        f"Packaging experiments must test identical underlying video content."
                    )

        # 3. Overlapping Experiments Guard (Section 27)
        for existing in cls._experiments.values():
            if (
                existing.channel_id == channel_id
                and existing.status in (ExperimentStatus.READY, ExperimentStatus.RUNNING)
                and existing.experiment_type == experiment_type
            ):
                raise ValueError(
                    f"Overlapping experiment detected: active experiment {existing.experiment_id} on channel {channel_id} "
                    f"is already manipulating dimension {experiment_type.value}."
                )

        # 4. Construct ExperimentDefinition
        exp_id = uuid4()
        exp_def = ExperimentDefinition(
            experiment_id=exp_id,
            channel_id=channel_id,
            experiment_type=experiment_type,
            hypothesis=hypothesis,
            primary_metric=primary_metric,
            secondary_metrics=secondary_metrics or [],
            control_variant_id=control_variant.variant_id,
            treatment_variant_ids=[t.variant_id for t in treatment_variants],
            assignment_policy=assignment_policy or AssignmentPolicy(),
            experiment_unit=experiment_unit,
            minimum_sample_size=minimum_sample_size,
            status=ExperimentStatus.DRAFT,
            analysis_window_hours=analysis_window_hours,
            created_at=datetime.now(UTC),
            provenance=provenance or {},
        )

        # Bind experiment_id to variants
        bound_variants = []
        for v in all_variants:
            bound_v = ExperimentVariant(
                variant_id=v.variant_id,
                experiment_id=exp_id,
                role=v.role,
                change_dimension=v.change_dimension,
                media_artifact_id=v.media_artifact_id,
                packaging_plan_id=v.packaging_plan_id,
                title=v.title,
                thumbnail_concept_id=v.thumbnail_concept_id,
                thumbnail_ref=v.thumbnail_ref,
                is_accepted_p24=v.is_accepted_p24,
                creative_qa_status=v.creative_qa_status,
                provenance=v.provenance,
            )
            bound_variants.append(bound_v)

        cls._experiments[exp_id] = exp_def
        cls._variants[exp_id] = bound_variants

        logger.info(
            "experiment.created",
            experiment_id=str(exp_id),
            channel_id=str(channel_id),
            type=experiment_type.value,
            primary_metric=primary_metric,
        )
        return exp_def, bound_variants

    # ── Experiment Validation & Lifecycle ────────────────────────────────────

    @classmethod
    def validate_experiment(cls, experiment_id: UUID) -> ExperimentDefinition:
        """Validate experiment prerequisites and advance status to READY."""
        exp = cls._get_experiment(experiment_id)
        variants = cls._variants.get(experiment_id, [])

        if not variants:
            raise ValueError(f"Experiment {experiment_id} has no registered variants.")

        ctrl = next((v for v in variants if v.role == VariantRole.CONTROL), None)
        trts = [v for v in variants if v.role == VariantRole.TREATMENT]

        if ctrl is None or not trts:
            raise ValueError(f"Experiment {experiment_id} must have exactly one CONTROL and at least one TREATMENT.")

        # Re-check creative gate
        for v in variants:
            if not v.is_eligible_for_experiment():
                raise ValueError(f"Variant {v.variant_id} failed creative acceptance validation.")

        # Advance status to READY
        updated = ExperimentDefinition(
            experiment_id=exp.experiment_id,
            channel_id=exp.channel_id,
            experiment_type=exp.experiment_type,
            hypothesis=exp.hypothesis,
            primary_metric=exp.primary_metric,
            secondary_metrics=exp.secondary_metrics,
            control_variant_id=exp.control_variant_id,
            treatment_variant_ids=exp.treatment_variant_ids,
            assignment_policy=exp.assignment_policy,
            experiment_unit=exp.experiment_unit,
            minimum_sample_size=exp.minimum_sample_size,
            status=ExperimentStatus.READY,
            analysis_window_hours=exp.analysis_window_hours,
            revision_number=exp.revision_number,
            created_at=exp.created_at,
            provenance=exp.provenance,
        )
        cls._experiments[experiment_id] = updated
        logger.info("experiment.validated", experiment_id=str(experiment_id))
        return updated

    @classmethod
    def start_experiment(cls, experiment_id: UUID) -> ExperimentDefinition:
        """Start the experiment. Primary metric and variants become strictly immutable."""
        exp = cls._get_experiment(experiment_id)
        if exp.status != ExperimentStatus.READY:
            raise ValueError(f"Cannot start experiment {experiment_id} with status {exp.status.value}. Must be READY.")

        now_utc = datetime.now(UTC)
        updated = ExperimentDefinition(
            experiment_id=exp.experiment_id,
            channel_id=exp.channel_id,
            experiment_type=exp.experiment_type,
            hypothesis=exp.hypothesis,
            primary_metric=exp.primary_metric,
            secondary_metrics=exp.secondary_metrics,
            control_variant_id=exp.control_variant_id,
            treatment_variant_ids=exp.treatment_variant_ids,
            assignment_policy=exp.assignment_policy,
            experiment_unit=exp.experiment_unit,
            minimum_sample_size=exp.minimum_sample_size,
            status=ExperimentStatus.RUNNING,
            start_at=now_utc,
            end_at=now_utc + timedelta(hours=exp.analysis_window_hours),
            analysis_window_hours=exp.analysis_window_hours,
            revision_number=exp.revision_number,
            created_at=exp.created_at,
            provenance=exp.provenance,
        )
        cls._experiments[experiment_id] = updated
        cls._variant_snapshots[experiment_id] = {
            v.variant_id: v.model_dump() for v in cls._variants.get(experiment_id, [])
        }
        logger.info("experiment.started", experiment_id=str(experiment_id), start_at=now_utc.isoformat())
        return updated

    @classmethod
    def complete_experiment(cls, experiment_id: UUID) -> ExperimentDefinition:
        """Mark an experiment as COMPLETED."""
        exp = cls._get_experiment(experiment_id)
        updated = ExperimentDefinition(
            experiment_id=exp.experiment_id,
            channel_id=exp.channel_id,
            experiment_type=exp.experiment_type,
            hypothesis=exp.hypothesis,
            primary_metric=exp.primary_metric,
            secondary_metrics=exp.secondary_metrics,
            control_variant_id=exp.control_variant_id,
            treatment_variant_ids=exp.treatment_variant_ids,
            assignment_policy=exp.assignment_policy,
            experiment_unit=exp.experiment_unit,
            minimum_sample_size=exp.minimum_sample_size,
            status=ExperimentStatus.COMPLETED,
            start_at=exp.start_at,
            end_at=exp.end_at or datetime.now(UTC),
            analysis_window_hours=exp.analysis_window_hours,
            revision_number=exp.revision_number,
            created_at=exp.created_at,
            provenance=exp.provenance,
        )
        cls._experiments[experiment_id] = updated
        logger.info("experiment.completed", experiment_id=str(experiment_id))
        return updated

    @classmethod
    def invalidate_experiment(cls, experiment_id: UUID, reasons: str) -> ExperimentDefinition:
        """Mark an experiment as INVALIDATED due to confounding or violation."""
        exp = cls._get_experiment(experiment_id)
        prov = dict(exp.provenance)
        prov["invalidation_reasons"] = reasons
        updated = ExperimentDefinition(
            experiment_id=exp.experiment_id,
            channel_id=exp.channel_id,
            experiment_type=exp.experiment_type,
            hypothesis=exp.hypothesis,
            primary_metric=exp.primary_metric,
            secondary_metrics=exp.secondary_metrics,
            control_variant_id=exp.control_variant_id,
            treatment_variant_ids=exp.treatment_variant_ids,
            assignment_policy=exp.assignment_policy,
            experiment_unit=exp.experiment_unit,
            minimum_sample_size=exp.minimum_sample_size,
            status=ExperimentStatus.INVALIDATED,
            start_at=exp.start_at,
            end_at=datetime.now(UTC),
            analysis_window_hours=exp.analysis_window_hours,
            revision_number=exp.revision_number,
            created_at=exp.created_at,
            provenance=prov,
        )
        cls._experiments[experiment_id] = updated
        logger.warning("experiment.invalidated", experiment_id=str(experiment_id), reasons=reasons)
        return updated

    @classmethod
    def modify_variant_after_start(
        cls,
        experiment_id: UUID,
        variant_id: UUID,
        new_variant: ExperimentVariant,
    ) -> None:
        """Attempt to modify a variant after start, demonstrating variant mutation detection."""
        variants = cls._variants.get(experiment_id, [])
        updated = []
        found = False
        for v in variants:
            if v.variant_id == variant_id:
                updated.append(new_variant)
                found = True
            else:
                updated.append(v)
        if not found:
            raise ValueError(f"Variant {variant_id} not found in experiment {experiment_id}")
        cls._variants[experiment_id] = updated

    # ── Exposure Recording ───────────────────────────────────────────────────

    @classmethod
    def record_exposure(
        cls,
        experiment_id: UUID,
        variant_id: UUID,
        subject_id: str,
        sample_count: int = 1,
        exposed_at: datetime | None = None,
    ) -> ExperimentExposure:
        """Record delivery of a variant to an experimental subject."""
        exp = cls._get_experiment(experiment_id)
        now_utc = exposed_at or datetime.now(UTC)

        exposure = ExperimentExposure(
            experiment_id=experiment_id,
            variant_id=variant_id,
            subject_id=subject_id,
            exposed_at=now_utc,
            aggregate_sample_count=sample_count,
        )
        if experiment_id not in cls._exposures:
            cls._exposures[experiment_id] = []
        cls._exposures[experiment_id].append(exposure)

        logger.info(
            "experiment.exposure_recorded",
            experiment_id=str(experiment_id),
            variant_id=str(variant_id),
            sample_count=sample_count,
        )
        return exposure

    # ── Causal Attribution Analysis ──────────────────────────────────────────

    @classmethod
    async def analyze_experiment(
        cls,
        experiment_id: UUID,
        provider: ExperimentProvider,
        as_of: datetime | None = None,
        force_completion: bool = False,
    ) -> AttributionResult:
        """Execute causal attribution analysis comparing control vs primary treatment.

        Guarantees:
        - Confounding checks (artifact mismatches, variant mutability violations).
        - Data maturity gating (delayed/stale data does not force a winner).
        - Bounded sample sufficiency enforcement.
        - Mathematically sound inferential statistics with confidence intervals.
        - Multiple comparison adjustments.
        - Never forces a winner: INCONCLUSIVE is an expected, supported finding.
        - Strictly read-only towards external providers.
        """
        exp = cls._get_experiment(experiment_id)
        variants = cls._variants.get(experiment_id, [])
        now_utc = as_of or datetime.now(UTC)
        window_str = f"{exp.analysis_window_hours}h"

        # 0. Immutable Historical Preservation & Replay Idempotency (Section 28)
        existing_res = cls._results.get(experiment_id)
        if existing_res is not None and not force_completion:
            if existing_res.data_maturity == DataMaturityState.MATURE or exp.status in (
                ExperimentStatus.COMPLETED,
                ExperimentStatus.INVALIDATED,
            ):
                return existing_res

        ctrl = next((v for v in variants if v.role == VariantRole.CONTROL), None)
        trt = next((v for v in variants if v.role == VariantRole.TREATMENT), None)

        if ctrl is None or trt is None:
            return AttributionResult.build_invalid(
                experiment_id, uuid4(), uuid4(), exp.primary_metric, window_str, ["Missing control or treatment variant"]
            )

        # 1. Confounding Checks (Section 24, 25)
        confounding_reasons: list[str] = []
        is_packaging = exp.experiment_type in (
            ExperimentType.TITLE,
            ExperimentType.THUMBNAIL,
            ExperimentType.TITLE_AND_THUMBNAIL,
            ExperimentType.DESCRIPTION,
            ExperimentType.PACKAGING_BUNDLE,
        )
        if is_packaging and ctrl.media_artifact_id != trt.media_artifact_id:
            confounding_reasons.append(
                f"Underlying video artifact changed between control ({ctrl.media_artifact_id}) and treatment ({trt.media_artifact_id})"
            )

        if not ctrl.is_eligible_for_experiment() or not trt.is_eligible_for_experiment():
            confounding_reasons.append("Variant failed P24 creative acceptance criteria during analysis")

        # Variant Immutability Check: compare against start snapshot
        start_snapshots = cls._variant_snapshots.get(experiment_id, {})
        for v in variants:
            initial = start_snapshots.get(v.variant_id)
            if initial is not None:
                current_dump = v.model_dump()
                core_keys = ["change_dimension", "media_artifact_id", "title", "thumbnail_ref", "packaging_plan_id"]
                if any(initial.get(k) != current_dump.get(k) for k in core_keys):
                    confounding_reasons.append(
                        f"Variant {v.variant_id} was modified after experiment start; variant immutability violated"
                    )

        if confounding_reasons:
            logger.warning("experiment.invalidated", experiment_id=str(experiment_id), reasons=confounding_reasons)
            cls.invalidate_experiment(experiment_id, reasons="; ".join(confounding_reasons))
            res = AttributionResult.build_invalid(
                exp.experiment_id,
                ctrl.variant_id,
                trt.variant_id,
                exp.primary_metric,
                window_str,
                confounding_reasons,
            )
            cls._results[experiment_id] = res
            return res

        # 2. Data Maturity Gate (Section 15)
        prov_freshness = await provider.check_experiment_freshness(experiment_id)
        if prov_freshness == "DELAYED":
            logger.info("experiment.analysis_waiting", experiment_id=str(experiment_id), reason="PROVIDER_LAG")
            res = AttributionResult(
                experiment_id=exp.experiment_id,
                control_variant_id=ctrl.variant_id,
                treatment_variant_id=trt.variant_id,
                primary_metric=exp.primary_metric,
                analysis_window=window_str,
                data_maturity=DataMaturityState.DELAYED,
                classification=AttributionClassification.INSUFFICIENT_DATA,
                findings=["Provider analytics pipeline delayed; waiting for maturity"],
            )
            cls._results[experiment_id] = res
            return res

        if prov_freshness in ("UNAVAILABLE", "INVALID"):
            res = AttributionResult.build_invalid(
                exp.experiment_id,
                ctrl.variant_id,
                trt.variant_id,
                exp.primary_metric,
                window_str,
                [f"Provider data unavailable (freshness={prov_freshness})"],
            )
            cls._results[experiment_id] = res
            return res

        # 3. Fetch Variant Metrics from Provider
        ctrl_perf = await provider.fetch_variant_performance(experiment_id, ctrl.variant_id, exp.analysis_window_hours)
        trt_perf = await provider.fetch_variant_performance(experiment_id, trt.variant_id, exp.analysis_window_hours)

        exposure_map = await provider.fetch_exposure_counts(experiment_id)
        ctrl_n = exposure_map.get(ctrl.variant_id, ctrl_perf.get("impressions", 0))
        trt_n = exposure_map.get(trt.variant_id, trt_perf.get("impressions", 0))

        ctrl_val = ctrl_perf.get(exp.primary_metric)
        trt_val = trt_perf.get(exp.primary_metric)

        sample_basis = {
            "control_sample_size": ctrl_n,
            "treatment_sample_size": trt_n,
        }

        # 4. Sample Sufficiency Check (Section 16)
        if ctrl_n < exp.minimum_sample_size or trt_n < exp.minimum_sample_size:
            logger.info("experiment.sample_insufficient", control_n=ctrl_n, treatment_n=trt_n, min=exp.minimum_sample_size)
            res = AttributionResult(
                experiment_id=exp.experiment_id,
                control_variant_id=ctrl.variant_id,
                treatment_variant_id=trt.variant_id,
                primary_metric=exp.primary_metric,
                analysis_window=window_str,
                control_value=ctrl_val,
                treatment_value=trt_val,
                sample_basis=sample_basis,
                data_maturity=DataMaturityState.INSUFFICIENT_DATA,
                classification=AttributionClassification.INSUFFICIENT_DATA,
                findings=[f"Sample sizes (ctrl={ctrl_n}, trt={trt_n}) below minimum threshold {exp.minimum_sample_size}"],
            )
            cls._results[experiment_id] = res
            return res

        # 5. Compute Effect Size (Section 17)
        abs_diff, rel_lift = StatisticalEngine.compute_effect_size(ctrl_val, trt_val)

        # 6. Statistical Inference & Multiple Comparisons (Sections 18, 19, 20)
        inference_result: StatisticalInferenceResult | None = None
        comparison_count = len(exp.treatment_variant_ids)
        alpha = StatisticalEngine.apply_bonferroni_correction(0.05, comparison_count)

        if (
            exp.primary_metric in ("impressions_ctr", "average_percentage_viewed")
            and ctrl_val is not None
            and trt_val is not None
        ):
            inference_result = StatisticalEngine.evaluate_two_proportion_z_test(
                p_control=ctrl_val,
                n_control=ctrl_n,
                p_treatment=trt_val,
                n_treatment=trt_n,
                alpha=alpha,
            )

        # 7. Classification Decision without Forced Winners (Section 22, 23)
        findings: list[str] = []
        classification = AttributionClassification.INCONCLUSIVE

        if inference_result and inference_result.is_statistically_significant:
            if trt_val is not None and ctrl_val is not None:
                if trt_val > ctrl_val:
                    classification = AttributionClassification.TREATMENT_BETTER
                    findings.append(f"Treatment showed statistically significant lift of {rel_lift * 100:.1f}% (p={inference_result.p_value:.4f})")
                elif ctrl_val > trt_val:
                    classification = AttributionClassification.CONTROL_BETTER
                    findings.append(f"Control outperformed treatment with statistical significance (p={inference_result.p_value:.4f})")
        else:
            if abs_diff is not None and abs(abs_diff) < 0.0001:
                classification = AttributionClassification.NO_MEANINGFUL_DIFFERENCE
                findings.append("No observable difference detected between control and treatment")
            else:
                classification = AttributionClassification.INCONCLUSIVE
                p_str = f"p={inference_result.p_value:.4f}" if inference_result and inference_result.p_value is not None else "n/a"
                findings.append(f"Difference not statistically significant at alpha={alpha} ({p_str}); result is inconclusive")

        # 8. Immutable Historical Result Preservation (Section 28)
        result = AttributionResult(
            result_id=uuid4(),
            experiment_id=exp.experiment_id,
            control_variant_id=ctrl.variant_id,
            treatment_variant_id=trt.variant_id,
            primary_metric=exp.primary_metric,
            analysis_window=window_str,
            control_value=ctrl_val,
            treatment_value=trt_val,
            absolute_difference=abs_diff,
            relative_lift=rel_lift,
            sample_basis=sample_basis,
            statistical_inference=inference_result,
            data_maturity=DataMaturityState.MATURE,
            classification=classification,
            findings=findings,
            evaluated_at=now_utc,
            provenance={"comparison_count": comparison_count, "adjusted_alpha": alpha},
        )
        cls._results[experiment_id] = result
        logger.info(
            "experiment.analysis_completed",
            experiment_id=str(experiment_id),
            classification=classification.value,
            relative_lift=rel_lift,
        )
        return result

    # ── Helpers ──────────────────────────────────────────────────────────────

    @classmethod
    def _get_experiment(cls, experiment_id: UUID) -> ExperimentDefinition:
        exp = cls._experiments.get(experiment_id)
        if exp is None:
            raise ValueError(f"Experiment {experiment_id} not found.")
        return exp

    @classmethod
    def get_experiment(cls, experiment_id: UUID) -> ExperimentDefinition | None:
        return cls._experiments.get(experiment_id)

    @classmethod
    def get_experiment_result(cls, experiment_id: UUID) -> AttributionResult | None:
        return cls._results.get(experiment_id)

    @classmethod
    def list_experiments_for_channel(cls, channel_id: UUID) -> list[ExperimentDefinition]:
        return [e for e in cls._experiments.values() if e.channel_id == channel_id]
