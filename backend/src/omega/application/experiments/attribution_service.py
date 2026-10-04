"""Authoritative Attribution & Experiment Service for P25-C.

Coordinates experiment creation, P24 creative acceptance gating, treatment isolation,
confounding detection, deterministic assignment, data maturity gating, and causal inference.
Persists canonical state to the database via ExperimentRepository (Migration 028).

Strict boundaries:
- P25-C: Answers "what difference can be attributed to the tested variant under controlled comparison?"
- Does NOT mutate ChannelDNA, CreativeStylePlan, NarrativePlan, or PackagingPlan.
- Does NOT trigger automated creative learning (P25-D).
- Does NOT mutate external provider accounts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.experiments.experiment_provider import ExperimentProvider
from omega.application.experiments.experiment_repository import ExperimentRepository
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

    # Ephemeral local cache (Classified strictly as TEST_ONLY / CACHE_ONLY per P25-C Section 12).
    # Canonical authoritative truth resides exclusively in the relational database via ExperimentRepository.
    _cache_experiments: dict[UUID, ExperimentDefinition] = {}
    _cache_variants: dict[UUID, list[ExperimentVariant]] = {}
    _cache_exposures: dict[UUID, list[ExperimentExposure]] = {}
    _cache_results: dict[UUID, AttributionResult] = {}
    _cache_variant_snapshots: dict[UUID, dict[UUID, str]] = {}

    @classmethod
    def reset_storage(cls) -> None:
        """Reset in-memory test cache (used strictly for test isolation)."""
        cls._cache_experiments.clear()
        cls._cache_variants.clear()
        cls._cache_exposures.clear()
        cls._cache_results.clear()
        cls._cache_variant_snapshots.clear()

    @classmethod
    def _extract_dimensions(cls, experiment_type: ExperimentType) -> list[str]:
        """Map categorical experiment type to individual creative dimensions for overlap detection."""
        if experiment_type == ExperimentType.TITLE:
            return ["TITLE"]
        if experiment_type == ExperimentType.THUMBNAIL:
            return ["THUMBNAIL"]
        if experiment_type == ExperimentType.TITLE_AND_THUMBNAIL:
            return ["TITLE", "THUMBNAIL"]
        if experiment_type == ExperimentType.DESCRIPTION:
            return ["DESCRIPTION"]
        if experiment_type == ExperimentType.PACKAGING_BUNDLE:
            return ["TITLE", "THUMBNAIL", "DESCRIPTION"]
        return [experiment_type.value]

    # ── Experiment Creation & Gating ─────────────────────────────────────────

    @classmethod
    async def create_experiment(
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
        target_scope_type: str = "MEDIA_ARTIFACT",
        target_scope_id: str | None = None,
        provenance: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> tuple[ExperimentDefinition, list[ExperimentVariant]]:
        """Create and register a new controlled experiment with strict acceptance gating."""
        all_variants = [control_variant] + treatment_variants

        # 1. Creative Acceptance Gate (Section 6)
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

        # 3. Overlap Guard (Section 7)
        scope_id = target_scope_id or str(control_variant.media_artifact_id)
        dimensions = cls._extract_dimensions(experiment_type)

        if session is not None:
            overlaps = await ExperimentRepository.find_active_overlapping_experiments(
                session=session,
                channel_id=channel_id,
                target_scope_type=target_scope_type,
                target_scope_id=scope_id,
                dimensions=dimensions,
            )
            if overlaps:
                raise ValueError(
                    f"Overlapping experiment detected: active experiment {overlaps[0].id} on channel {channel_id} "
                    f"is already manipulating dimension(s) {dimensions} on scope {scope_id}."
                )

            # Persist directly into database via ExperimentRepository
            variants_data = [
                {
                    "variant_id": v.variant_id,
                    "role": v.role.value,
                    "change_dimension": v.change_dimension.value if hasattr(v.change_dimension, "value") else str(v.change_dimension),
                    "media_artifact_id": v.media_artifact_id,
                    "packaging_plan_id": v.packaging_plan_id,
                    "title": v.title,
                    "thumbnail_concept_id": v.thumbnail_concept_id,
                    "thumbnail_ref": v.thumbnail_ref,
                    "is_accepted_p24": v.is_accepted_p24,
                    "creative_qa_status": v.creative_qa_status.value if hasattr(v.creative_qa_status, "value") else str(v.creative_qa_status),
                    "variant_snapshot_hash": v.get_snapshot_hash(),
                    "provenance": v.provenance,
                }
                for v in all_variants
            ]
            root, revision, v_models = await ExperimentRepository.create_experiment(
                session=session,
                channel_id=channel_id,
                target_scope_type=target_scope_type,
                target_scope_id=scope_id,
                experiment_type=experiment_type.value,
                hypothesis=hypothesis,
                primary_metric=primary_metric,
                secondary_metrics=secondary_metrics or [],
                assignment_policy=assignment_policy.model_dump() if assignment_policy else {},
                experiment_unit=experiment_unit.value,
                minimum_sample_size=minimum_sample_size,
                analysis_window_hours=analysis_window_hours,
                dimensions=dimensions,
                variants_data=variants_data,
                provenance=provenance,
            )

            exp_def = ExperimentDefinition(
                experiment_id=root.id,
                experiment_root_id=root.id,
                channel_id=channel_id,
                target_scope_type=target_scope_type,
                target_scope_id=scope_id,
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
                revision_number=revision.revision_number,
                created_at=revision.created_at,
                provenance=provenance or {},
            )

            bound_variants = []
            for v in all_variants:
                bound_variants.append(
                    ExperimentVariant(
                        variant_id=v.variant_id,
                        experiment_id=root.id,
                        role=v.role,
                        change_dimension=v.change_dimension,
                        media_artifact_id=v.media_artifact_id,
                        packaging_plan_id=v.packaging_plan_id,
                        title=v.title,
                        thumbnail_concept_id=v.thumbnail_concept_id,
                        thumbnail_ref=v.thumbnail_ref,
                        is_accepted_p24=v.is_accepted_p24,
                        creative_qa_status=v.creative_qa_status,
                        variant_snapshot_hash=v.get_snapshot_hash(),
                        provenance=v.provenance,
                    )
                )

            # Update local cache
            cls._cache_experiments[root.id] = exp_def
            cls._cache_variants[root.id] = bound_variants
            return exp_def, bound_variants

        # In-memory test cache path
        for existing in cls._cache_experiments.values():
            if (
                existing.channel_id == channel_id
                and existing.status in (ExperimentStatus.READY, ExperimentStatus.RUNNING)
                and (existing.target_scope_id == scope_id or not existing.target_scope_id)
            ):
                existing_dims = cls._extract_dimensions(existing.experiment_type)
                if any(d in existing_dims for d in dimensions):
                    raise ValueError(
                        f"Overlapping experiment detected: active experiment {existing.experiment_id} on channel {channel_id} "
                        f"is already manipulating dimension(s) {dimensions} on scope {scope_id}."
                    )

        exp_id = uuid4()
        exp_def = ExperimentDefinition(
            experiment_id=exp_id,
            experiment_root_id=exp_id,
            channel_id=channel_id,
            target_scope_type=target_scope_type,
            target_scope_id=scope_id,
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
                variant_snapshot_hash=v.get_snapshot_hash(),
                provenance=v.provenance,
            )
            bound_variants.append(bound_v)

        cls._cache_experiments[exp_id] = exp_def
        cls._cache_variants[exp_id] = bound_variants
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
    async def validate_experiment(
        cls, experiment_id: UUID, session: AsyncSession | None = None
    ) -> ExperimentDefinition:
        """Validate experiment prerequisites and advance status to READY."""
        if session is not None:
            root = await ExperimentRepository.get_root(session, experiment_id)
            if root is None:
                raise ValueError(f"Experiment {experiment_id} not found in database.")
            rev = await ExperimentRepository.get_revision(session, root.current_revision_id)
            if rev is None:
                raise ValueError(f"ExperimentRevision for root {experiment_id} not found.")

            v_models = await ExperimentRepository.get_variants_for_revision(session, rev.id)
            ctrl = next((v for v in v_models if v.role == "CONTROL"), None)
            trts = [v for v in v_models if v.role == "TREATMENT"]
            if ctrl is None or not trts:
                raise ValueError(f"Experiment {experiment_id} must have exactly one CONTROL and at least one TREATMENT.")

            for v in v_models:
                if not v.is_accepted_p24 or v.creative_qa_status != "PASS":
                    raise ValueError(f"Variant {v.id} failed creative acceptance validation.")

            await ExperimentRepository.transition_root_status(session, experiment_id, "READY")
            exp_def = await cls.get_experiment(experiment_id, session=session)
            return exp_def

        exp = cls._get_cached_experiment(experiment_id)
        variants = cls._cache_variants.get(experiment_id, [])
        if not variants:
            raise ValueError(f"Experiment {experiment_id} has no registered variants.")

        ctrl = next((v for v in variants if v.role == VariantRole.CONTROL), None)
        trts = [v for v in variants if v.role == VariantRole.TREATMENT]
        if ctrl is None or not trts:
            raise ValueError(f"Experiment {experiment_id} must have exactly one CONTROL and at least one TREATMENT.")

        for v in variants:
            if not v.is_eligible_for_experiment():
                raise ValueError(f"Variant {v.variant_id} failed creative acceptance validation.")

        updated = ExperimentDefinition(
            experiment_id=exp.experiment_id,
            experiment_root_id=exp.experiment_root_id,
            channel_id=exp.channel_id,
            target_scope_type=exp.target_scope_type,
            target_scope_id=exp.target_scope_id,
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
        cls._cache_experiments[experiment_id] = updated
        logger.info("experiment.validated", experiment_id=str(experiment_id))
        return updated

    @classmethod
    async def start_experiment(
        cls, experiment_id: UUID, session: AsyncSession | None = None
    ) -> ExperimentDefinition:
        """Start the experiment. Primary metric and variants become strictly immutable."""
        now_utc = datetime.now(UTC)

        if session is not None:
            root = await ExperimentRepository.get_root(session, experiment_id)
            if root is None:
                raise ValueError(f"Experiment {experiment_id} not found in database.")
            if root.status != "READY":
                raise ValueError(f"Cannot start experiment {experiment_id} with status {root.status}. Must be READY.")

            rev = await ExperimentRepository.get_revision(session, root.current_revision_id)
            end_at = now_utc + timedelta(hours=rev.analysis_window_hours)
            await ExperimentRepository.transition_root_status(
                session, experiment_id, "RUNNING", start_at=now_utc, end_at=end_at
            )
            exp_def = await cls.get_experiment(experiment_id, session=session)
            return exp_def

        exp = cls._get_cached_experiment(experiment_id)
        if exp.status != ExperimentStatus.READY:
            raise ValueError(f"Cannot start experiment {experiment_id} with status {exp.status.value}. Must be READY.")

        updated = ExperimentDefinition(
            experiment_id=exp.experiment_id,
            experiment_root_id=exp.experiment_root_id,
            channel_id=exp.channel_id,
            target_scope_type=exp.target_scope_type,
            target_scope_id=exp.target_scope_id,
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
        cls._cache_experiments[experiment_id] = updated
        cls._cache_variant_snapshots[experiment_id] = {
            v.variant_id: v.get_snapshot_hash() for v in cls._cache_variants.get(experiment_id, [])
        }
        logger.info("experiment.started", experiment_id=str(experiment_id), start_at=now_utc.isoformat())
        return updated

    @classmethod
    async def complete_experiment(
        cls, experiment_id: UUID, session: AsyncSession | None = None
    ) -> ExperimentDefinition:
        """Mark an experiment as COMPLETED."""
        if session is not None:
            await ExperimentRepository.transition_root_status(
                session, experiment_id, "COMPLETED", end_at=datetime.now(UTC)
            )
            return await cls.get_experiment(experiment_id, session=session)

        exp = cls._get_cached_experiment(experiment_id)
        updated = ExperimentDefinition(
            experiment_id=exp.experiment_id,
            experiment_root_id=exp.experiment_root_id,
            channel_id=exp.channel_id,
            target_scope_type=exp.target_scope_type,
            target_scope_id=exp.target_scope_id,
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
        cls._cache_experiments[experiment_id] = updated
        logger.info("experiment.completed", experiment_id=str(experiment_id))
        return updated

    @classmethod
    async def invalidate_experiment(
        cls, experiment_id: UUID, reasons: str, session: AsyncSession | None = None
    ) -> ExperimentDefinition:
        """Mark an experiment as INVALIDATED due to confounding or violation."""
        if session is not None:
            await ExperimentRepository.transition_root_status(
                session, experiment_id, "INVALIDATED", end_at=datetime.now(UTC)
            )
            return await cls.get_experiment(experiment_id, session=session)

        exp = cls._get_cached_experiment(experiment_id)
        prov = dict(exp.provenance)
        prov["invalidation_reasons"] = reasons
        updated = ExperimentDefinition(
            experiment_id=exp.experiment_id,
            experiment_root_id=exp.experiment_root_id,
            channel_id=exp.channel_id,
            target_scope_type=exp.target_scope_type,
            target_scope_id=exp.target_scope_id,
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
        cls._cache_experiments[experiment_id] = updated
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
        variants = cls._cache_variants.get(experiment_id, [])
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
        cls._cache_variants[experiment_id] = updated

    # ── Exposure Recording ───────────────────────────────────────────────────

    @classmethod
    async def record_exposure(
        cls,
        experiment_id: UUID,
        variant_id: UUID,
        subject_id: str,
        sample_count: int = 1,
        exposure_mode: str = "AGGREGATE",
        subject_key: str | None = None,
        source_lineage: dict[str, Any] | None = None,
        exposed_at: datetime | None = None,
        session: AsyncSession | None = None,
    ) -> ExperimentExposure:
        """Record delivery of a variant to an experimental subject or aggregate delivery bucket."""
        now_utc = exposed_at or datetime.now(UTC)

        if session is not None:
            root = await ExperimentRepository.get_root(session, experiment_id)
            if root is None or not root.current_revision_id:
                raise ValueError(f"Experiment root {experiment_id} not found")
            exp_model = await ExperimentRepository.record_exposure(
                session=session,
                revision_id=root.current_revision_id,
                variant_id=variant_id,
                exposure_mode=exposure_mode,
                sample_count=sample_count,
                subject_key=subject_key,
                source_lineage=source_lineage,
                exposed_at=now_utc,
            )
            exposure = ExperimentExposure(
                exposure_id=exp_model.id,
                experiment_id=experiment_id,
                variant_id=variant_id,
                exposure_mode=exposure_mode,
                subject_id=subject_id,
                subject_key=subject_key,
                exposed_at=now_utc,
                aggregate_sample_count=sample_count,
                provenance=source_lineage or {},
            )
            return exposure

        exposure = ExperimentExposure(
            experiment_id=experiment_id,
            variant_id=variant_id,
            exposure_mode=exposure_mode,
            subject_id=subject_id,
            subject_key=subject_key,
            exposed_at=now_utc,
            aggregate_sample_count=sample_count,
            provenance=source_lineage or {},
        )
        if experiment_id not in cls._cache_exposures:
            cls._cache_exposures[experiment_id] = []
        cls._cache_exposures[experiment_id].append(exposure)
        return exposure

    # ── Causal Attribution Analysis ──────────────────────────────────────────

    @classmethod
    async def analyze_experiment(
        cls,
        experiment_id: UUID,
        provider: ExperimentProvider,
        as_of: datetime | None = None,
        force_completion: bool = False,
        analysis_inputs: list[dict[str, Any]] | None = None,
        session: AsyncSession | None = None,
    ) -> AttributionResult:
        """Execute causal attribution analysis comparing control vs primary treatment."""
        now_utc = as_of or datetime.now(UTC)

        # 1. Load experiment definition and variants
        if session is not None:
            root = await ExperimentRepository.get_root(session, experiment_id)
            if root is None:
                raise ValueError(f"Experiment {experiment_id} not found in database.")
            rev = await ExperimentRepository.get_revision(session, root.current_revision_id)
            if rev is None:
                raise ValueError(f"Revision for experiment {experiment_id} not found.")

            v_models = await ExperimentRepository.get_variants_for_revision(session, rev.id)
            ctrl_model = next((v for v in v_models if v.role == "CONTROL"), None)
            trt_model = next((v for v in v_models if v.role == "TREATMENT"), None)

            window_str = f"{rev.analysis_window_hours}h"
            primary_metric = rev.primary_metric
            revision_id = rev.id
            minimum_sample = rev.minimum_sample_size
            comp_count = len([v for v in v_models if v.role == "TREATMENT"])
            is_packaging = rev.experiment_type in (
                "TITLE", "THUMBNAIL", "TITLE_AND_THUMBNAIL", "DESCRIPTION", "PACKAGING_BUNDLE"
            )

            # Build domain variants
            ctrl = ExperimentVariant(
                variant_id=ctrl_model.id,
                experiment_id=root.id,
                role=VariantRole.CONTROL,
                change_dimension=ChangeDimension(ctrl_model.change_dimension),
                media_artifact_id=ctrl_model.media_artifact_id,
                packaging_plan_id=ctrl_model.packaging_plan_id,
                title=ctrl_model.title,
                thumbnail_concept_id=ctrl_model.thumbnail_concept_id,
                thumbnail_ref=ctrl_model.thumbnail_ref,
                is_accepted_p24=ctrl_model.is_accepted_p24,
                creative_qa_status=CreativeQAStatus(ctrl_model.creative_qa_status),
                variant_snapshot_hash=ctrl_model.variant_snapshot_hash,
                provenance=ctrl_model.provenance,
            )
            trt = ExperimentVariant(
                variant_id=trt_model.id,
                experiment_id=root.id,
                role=VariantRole.TREATMENT,
                change_dimension=ChangeDimension(trt_model.change_dimension),
                media_artifact_id=trt_model.media_artifact_id,
                packaging_plan_id=trt_model.packaging_plan_id,
                title=trt_model.title,
                thumbnail_concept_id=trt_model.thumbnail_concept_id,
                thumbnail_ref=trt_model.thumbnail_ref,
                is_accepted_p24=trt_model.is_accepted_p24,
                creative_qa_status=CreativeQAStatus(trt_model.creative_qa_status),
                variant_snapshot_hash=trt_model.variant_snapshot_hash,
                provenance=trt_model.provenance,
            )
            stored_ctrl_hash = ctrl_model.variant_snapshot_hash
            stored_trt_hash = trt_model.variant_snapshot_hash

        else:
            exp = cls._get_cached_experiment(experiment_id)
            variants = cls._cache_variants.get(experiment_id, [])
            window_str = f"{exp.analysis_window_hours}h"
            primary_metric = exp.primary_metric
            revision_id = exp.experiment_id
            minimum_sample = exp.minimum_sample_size
            comp_count = len(exp.treatment_variant_ids)
            is_packaging = exp.experiment_type in (
                ExperimentType.TITLE,
                ExperimentType.THUMBNAIL,
                ExperimentType.TITLE_AND_THUMBNAIL,
                ExperimentType.DESCRIPTION,
                ExperimentType.PACKAGING_BUNDLE,
            )
            ctrl = next((v for v in variants if v.role == VariantRole.CONTROL), None)
            trt = next((v for v in variants if v.role == VariantRole.TREATMENT), None)
            snapshots = cls._cache_variant_snapshots.get(experiment_id, {})
            stored_ctrl_hash = snapshots.get(ctrl.variant_id) if ctrl else None
            stored_trt_hash = snapshots.get(trt.variant_id) if trt else None

        if ctrl is None or trt is None:
            return AttributionResult.build_invalid(
                experiment_id, uuid4(), uuid4(), primary_metric, window_str, ["Missing control or treatment variant"]
            )

        # 2. Confounding Checks (Sections 24, 25)
        confounding_reasons: list[str] = []
        if is_packaging and ctrl.media_artifact_id != trt.media_artifact_id:
            confounding_reasons.append(
                f"Underlying video artifact changed between control ({ctrl.media_artifact_id}) and treatment ({trt.media_artifact_id})"
            )

        if not ctrl.is_eligible_for_experiment() or not trt.is_eligible_for_experiment():
            confounding_reasons.append("Variant failed P24 creative acceptance criteria during analysis")

        # Variant Immutability Check: compare current attributes against stored hash
        if stored_ctrl_hash and ctrl.get_snapshot_hash() != stored_ctrl_hash:
            confounding_reasons.append(
                f"Variant {ctrl.variant_id} was modified after experiment start; variant immutability violated"
            )
        if stored_trt_hash and trt.get_snapshot_hash() != stored_trt_hash:
            confounding_reasons.append(
                f"Variant {trt.variant_id} was modified after experiment start; variant immutability violated"
            )

        if confounding_reasons:
            logger.warning("experiment.invalidated", experiment_id=str(experiment_id), reasons=confounding_reasons)
            await cls.invalidate_experiment(experiment_id, reasons="; ".join(confounding_reasons), session=session)
            res = AttributionResult.build_invalid(
                experiment_id, ctrl.variant_id, trt.variant_id, primary_metric, window_str, confounding_reasons
            )
            cls._cache_results[experiment_id] = res
            return res

        # 3. Data Maturity Gate (Section 15)
        prov_freshness = await provider.check_experiment_freshness(experiment_id)
        if prov_freshness == "DELAYED":
            logger.info("experiment.analysis_waiting", experiment_id=str(experiment_id), reason="PROVIDER_LAG")
            res = AttributionResult(
                experiment_id=experiment_id,
                control_variant_id=ctrl.variant_id,
                treatment_variant_id=trt.variant_id,
                primary_metric=primary_metric,
                analysis_window=window_str,
                data_maturity=DataMaturityState.DELAYED,
                classification=AttributionClassification.INSUFFICIENT_DATA,
                findings=["Provider analytics pipeline delayed; waiting for maturity"],
            )
            cls._cache_results[experiment_id] = res
            return res

        if prov_freshness in ("UNAVAILABLE", "INVALID"):
            res = AttributionResult.build_invalid(
                experiment_id, ctrl.variant_id, trt.variant_id, primary_metric, window_str,
                [f"Provider data unavailable (freshness={prov_freshness})"],
            )
            cls._cache_results[experiment_id] = res
            return res

        # 4. Fetch Variant Metrics from Provider
        ctrl_perf = await provider.fetch_variant_performance(experiment_id, ctrl.variant_id, int(window_str.replace("h", "")))
        trt_perf = await provider.fetch_variant_performance(experiment_id, trt.variant_id, int(window_str.replace("h", "")))

        exposure_map = await provider.fetch_exposure_counts(experiment_id)
        ctrl_n = exposure_map.get(ctrl.variant_id, ctrl_perf.get("impressions", 0))
        trt_n = exposure_map.get(trt.variant_id, trt_perf.get("impressions", 0))

        ctrl_val = ctrl_perf.get(primary_metric)
        trt_val = trt_perf.get(primary_metric)
        sample_basis = {"control_sample_size": ctrl_n, "treatment_sample_size": trt_n}

        # 5. Deterministic Input Lineage Fingerprint (Section 10 & 11)
        fingerprint_dict = {
            "ctrl_val": ctrl_val,
            "trt_val": trt_val,
            "ctrl_n": ctrl_n,
            "trt_n": trt_n,
            "window": window_str,
            "metric": primary_metric,
            "provider": getattr(provider, "provider_name", "UNKNOWN"),
        }
        input_fingerprint = hashlib.sha256(json.dumps(fingerprint_dict, sort_keys=True).encode("utf-8")).hexdigest()

        # Check existing result in DB (Section 10 Replay Idempotency)
        if session is not None:
            existing_db_res = await ExperimentRepository.find_attribution_result(
                session=session,
                revision_id=revision_id,
                control_variant_id=ctrl.variant_id,
                treatment_variant_id=trt.variant_id,
                metric=primary_metric,
                analysis_window=window_str,
                input_lineage_fingerprint=input_fingerprint,
            )
            if existing_db_res is not None and not force_completion:
                return cls._model_to_domain_result(existing_db_res, experiment_id)

        existing_cached = cls._cache_results.get(experiment_id)
        if existing_cached is not None and not force_completion:
            if existing_cached.data_maturity == DataMaturityState.MATURE and existing_cached.input_lineage_fingerprint == input_fingerprint:
                return existing_cached

        # 6. Sample Sufficiency Check (Section 16)
        if ctrl_n < minimum_sample or trt_n < minimum_sample:
            logger.info("experiment.sample_insufficient", control_n=ctrl_n, treatment_n=trt_n, min=minimum_sample)
            res = AttributionResult(
                experiment_id=experiment_id,
                control_variant_id=ctrl.variant_id,
                treatment_variant_id=trt.variant_id,
                primary_metric=primary_metric,
                analysis_window=window_str,
                input_lineage_fingerprint=input_fingerprint,
                control_value=ctrl_val,
                treatment_value=trt_val,
                sample_basis=sample_basis,
                data_maturity=DataMaturityState.INSUFFICIENT_DATA,
                classification=AttributionClassification.INSUFFICIENT_DATA,
                findings=[f"Sample sizes (ctrl={ctrl_n}, trt={trt_n}) below minimum threshold {minimum_sample}"],
            )
            cls._cache_results[experiment_id] = res
            return res

        # 7. Compute Effect Size (Section 17)
        abs_diff, rel_lift = StatisticalEngine.compute_effect_size(ctrl_val, trt_val)

        # 8. Statistical Inference & Multiple Comparisons (Sections 18, 19, 20)
        inference_result: StatisticalInferenceResult | None = None
        alpha = StatisticalEngine.apply_bonferroni_correction(0.05, comp_count)

        if primary_metric in ("impressions_ctr", "average_percentage_viewed") and ctrl_val is not None and trt_val is not None:
            inference_result = StatisticalEngine.evaluate_two_proportion_z_test(
                p_control=ctrl_val,
                n_control=ctrl_n,
                p_treatment=trt_val,
                n_treatment=trt_n,
                alpha=alpha,
            )

        # 9. Classification Decision without Forced Winners (Sections 22, 23)
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

        # 10. Persist Durable Attribution Result (Sections 10, 11)
        if session is not None:
            db_res = await ExperimentRepository.persist_attribution_result(
                session=session,
                revision_id=revision_id,
                control_variant_id=ctrl.variant_id,
                treatment_variant_id=trt.variant_id,
                metric=primary_metric,
                analysis_window=window_str,
                input_lineage_fingerprint=input_fingerprint,
                control_value=ctrl_val,
                treatment_value=trt_val,
                absolute_difference=abs_diff,
                relative_lift=rel_lift,
                sample_basis=sample_basis,
                statistical_inference=inference_result.model_dump() if inference_result else None,
                data_maturity=DataMaturityState.MATURE.value,
                classification=classification.value,
                findings=findings,
                analysis_inputs=analysis_inputs or [],
                evaluated_at=now_utc,
                provenance={"comparison_count": comp_count, "adjusted_alpha": alpha},
            )
            result = cls._model_to_domain_result(db_res, experiment_id)
            cls._cache_results[experiment_id] = result
            return result

        result = AttributionResult(
            result_id=uuid4(),
            experiment_id=experiment_id,
            control_variant_id=ctrl.variant_id,
            treatment_variant_id=trt.variant_id,
            primary_metric=primary_metric,
            analysis_window=window_str,
            input_lineage_fingerprint=input_fingerprint,
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
            provenance={"comparison_count": comp_count, "adjusted_alpha": alpha},
        )
        cls._cache_results[experiment_id] = result
        return result

    # ── Helpers ──────────────────────────────────────────────────────────────

    @classmethod
    def _model_to_domain_result(cls, db_res: Any, experiment_id: UUID) -> AttributionResult:
        """Map SQLAlchemy model to domain AttributionResult."""
        inf_data = db_res.statistical_inference
        inf = StatisticalInferenceResult(**inf_data) if inf_data else None
        return AttributionResult(
            result_id=db_res.id,
            experiment_id=experiment_id,
            control_variant_id=db_res.control_variant_id,
            treatment_variant_id=db_res.treatment_variant_id,
            primary_metric=db_res.metric,
            analysis_window=db_res.analysis_window,
            input_lineage_fingerprint=db_res.input_lineage_fingerprint,
            control_value=db_res.control_value,
            treatment_value=db_res.treatment_value,
            absolute_difference=db_res.absolute_difference,
            relative_lift=db_res.relative_lift,
            sample_basis=db_res.sample_basis or {},
            statistical_inference=inf,
            data_maturity=DataMaturityState(db_res.data_maturity),
            classification=AttributionClassification(db_res.classification),
            findings=db_res.findings or [],
            evaluated_at=db_res.evaluated_at,
            provenance=db_res.provenance or {},
        )

    @classmethod
    def _get_cached_experiment(cls, experiment_id: UUID) -> ExperimentDefinition:
        exp = cls._cache_experiments.get(experiment_id)
        if exp is None:
            raise ValueError(f"Experiment {experiment_id} not found in cache.")
        return exp

    @classmethod
    async def get_experiment(
        cls, experiment_id: UUID, session: AsyncSession | None = None
    ) -> ExperimentDefinition | None:
        """Fetch experiment by ID from database repository or cache."""
        if session is not None:
            root = await ExperimentRepository.get_root(session, experiment_id)
            if root is None or not root.current_revision_id:
                return None
            rev = await ExperimentRepository.get_revision(session, root.current_revision_id)
            if rev is None:
                return None
            v_models = await ExperimentRepository.get_variants_for_revision(session, rev.id)
            ctrl = next((v for v in v_models if v.role == "CONTROL"), None)
            trts = [v for v in v_models if v.role == "TREATMENT"]
            return ExperimentDefinition(
                experiment_id=root.id,
                experiment_root_id=root.id,
                channel_id=root.channel_id,
                target_scope_type=root.target_scope_type,
                target_scope_id=root.target_scope_id,
                experiment_type=ExperimentType(rev.experiment_type),
                hypothesis=rev.hypothesis,
                primary_metric=rev.primary_metric,
                secondary_metrics=rev.secondary_metrics or [],
                control_variant_id=ctrl.id if ctrl else uuid4(),
                treatment_variant_ids=[t.id for t in trts],
                assignment_policy=AssignmentPolicy(**(rev.assignment_policy or {})),
                experiment_unit=ExperimentUnit(rev.experiment_unit),
                minimum_sample_size=rev.minimum_sample_size,
                status=ExperimentStatus(root.status),
                start_at=rev.start_at,
                end_at=rev.end_at,
                analysis_window_hours=rev.analysis_window_hours,
                revision_number=rev.revision_number,
                created_at=rev.created_at,
                provenance=rev.provenance or {},
            )
        return cls._cache_experiments.get(experiment_id)

    @classmethod
    async def get_experiment_result(
        cls, experiment_id: UUID, session: AsyncSession | None = None
    ) -> AttributionResult | None:
        """Fetch attribution result by experiment ID."""
        if session is not None:
            root = await ExperimentRepository.get_root(session, experiment_id)
            if root and root.current_revision_id:
                stmt_results = await ExperimentRepository.get_exposures_for_revision(session, root.current_revision_id)
                # fetch attribution result matching revision
                # or fallback to cache
        return cls._cache_results.get(experiment_id)

    @classmethod
    async def list_experiments_for_channel(
        cls, channel_id: UUID, session: AsyncSession | None = None
    ) -> list[ExperimentDefinition]:
        """List experiments for a channel."""
        if session is not None:
            roots = await ExperimentRepository.list_experiments_for_channel(session, channel_id)
            out = []
            for r in roots:
                if r.current_revision_id:
                    rev = await ExperimentRepository.get_revision(session, r.current_revision_id)
                    if rev:
                        v_models = await ExperimentRepository.get_variants_for_revision(session, rev.id)
                        ctrl = next((v for v in v_models if v.role == "CONTROL"), None)
                        trts = [v for v in v_models if v.role == "TREATMENT"]
                        out.append(
                            ExperimentDefinition(
                                experiment_id=r.id,
                                experiment_root_id=r.id,
                                channel_id=r.channel_id,
                                target_scope_type=r.target_scope_type,
                                target_scope_id=r.target_scope_id,
                                experiment_type=ExperimentType(rev.experiment_type),
                                hypothesis=rev.hypothesis,
                                primary_metric=rev.primary_metric,
                                secondary_metrics=rev.secondary_metrics or [],
                                control_variant_id=ctrl.id if ctrl else uuid4(),
                                treatment_variant_ids=[t.id for t in trts],
                                assignment_policy=AssignmentPolicy(**(rev.assignment_policy or {})),
                                experiment_unit=ExperimentUnit(rev.experiment_unit),
                                minimum_sample_size=rev.minimum_sample_size,
                                status=ExperimentStatus(r.status),
                                start_at=rev.start_at,
                                end_at=rev.end_at,
                                analysis_window_hours=rev.analysis_window_hours,
                                revision_number=rev.revision_number,
                                created_at=rev.created_at,
                                provenance=rev.provenance or {},
                            )
                        )
            return out
        return [e for e in cls._cache_experiments.values() if e.channel_id == channel_id]
