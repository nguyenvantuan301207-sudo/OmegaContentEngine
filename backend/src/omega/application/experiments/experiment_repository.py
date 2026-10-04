"""Durable database repository for controlled experiments, variants, exposures, and attribution results.

Provides the single canonical database repository for P25-C experimentation persistence.
Ensures zero process-local authoritative state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.infrastructure.models import (
    ExperimentAnalysisInputModel,
    ExperimentAttributionResultModel,
    ExperimentExposureModel,
    ExperimentRevision,
    ExperimentRevisionDimension,
    ExperimentRoot,
    ExperimentVariantModel,
)


class ExperimentRepository:
    """Canonical repository managing durable persistence for P25-C experimentation."""

    @staticmethod
    def compute_variant_hash(
        media_artifact_id: UUID,
        title: str | None,
        thumbnail_ref: str | None,
        change_dimension: str,
        packaging_plan_id: UUID | None,
    ) -> str:
        """Compute deterministic SHA-256 fingerprint for a variant snapshot."""
        payload = {
            "artifact": str(media_artifact_id),
            "title": title or "",
            "thumbnail": thumbnail_ref or "",
            "dimension": change_dimension,
            "packaging_plan": str(packaging_plan_id) if packaging_plan_id else "",
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    async def create_experiment(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        target_scope_type: str,
        target_scope_id: str,
        experiment_type: str,
        hypothesis: str,
        primary_metric: str,
        secondary_metrics: list[str],
        assignment_policy: dict[str, Any],
        experiment_unit: str,
        minimum_sample_size: int,
        analysis_window_hours: int,
        dimensions: list[str],
        variants_data: list[dict[str, Any]],
        provenance: dict[str, Any] | None = None,
    ) -> tuple[ExperimentRoot, ExperimentRevision, list[ExperimentVariantModel]]:
        """Create a new experiment root, initial immutable revision, normalized dimensions, and variants."""
        root_id = uuid4()
        revision_id = uuid4()

        root = ExperimentRoot(
            id=root_id,
            channel_id=channel_id,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
            status="DRAFT",
            current_revision_id=None,
        )
        session.add(root)
        await session.flush()

        revision = ExperimentRevision(
            id=revision_id,
            experiment_root_id=root_id,
            revision_number=1,
            experiment_type=experiment_type,
            hypothesis=hypothesis,
            primary_metric=primary_metric,
            secondary_metrics=secondary_metrics,
            assignment_policy=assignment_policy,
            experiment_unit=experiment_unit,
            minimum_sample_size=minimum_sample_size,
            analysis_window_hours=analysis_window_hours,
            provenance=provenance or {},
        )
        session.add(revision)

        # Normalized creative dimensions for overlap guard
        dim_models = []
        for dim in dimensions:
            dim_row = ExperimentRevisionDimension(
                id=uuid4(),
                experiment_revision_id=revision_id,
                dimension=dim,
            )
            session.add(dim_row)
            dim_models.append(dim_row)

        # Normalized variants
        variant_models = []
        for v in variants_data:
            v_id = v.get("variant_id") or uuid4()
            v_hash = v.get("variant_snapshot_hash") or cls.compute_variant_hash(
                media_artifact_id=v["media_artifact_id"],
                title=v.get("title"),
                thumbnail_ref=v.get("thumbnail_ref"),
                change_dimension=v["change_dimension"],
                packaging_plan_id=v.get("packaging_plan_id"),
            )
            v_model = ExperimentVariantModel(
                id=v_id,
                experiment_revision_id=revision_id,
                role=v["role"],
                change_dimension=v["change_dimension"],
                media_artifact_id=v["media_artifact_id"],
                packaging_plan_id=v.get("packaging_plan_id"),
                title=v.get("title"),
                thumbnail_concept_id=v.get("thumbnail_concept_id"),
                thumbnail_ref=v.get("thumbnail_ref"),
                is_accepted_p24=v.get("is_accepted_p24", False),
                creative_qa_status=v.get("creative_qa_status", "FAIL"),
                variant_snapshot_hash=v_hash,
                provenance=v.get("provenance", {}),
            )
            session.add(v_model)
            variant_models.append(v_model)

        await session.flush()
        root.current_revision_id = revision_id
        await session.flush()
        return root, revision, variant_models

    @classmethod
    async def create_revision(
        cls,
        session: AsyncSession,
        root_id: UUID,
        experiment_type: str,
        hypothesis: str,
        primary_metric: str,
        secondary_metrics: list[str],
        assignment_policy: dict[str, Any],
        experiment_unit: str,
        minimum_sample_size: int,
        analysis_window_hours: int,
        dimensions: list[str],
        variants_data: list[dict[str, Any]],
        provenance: dict[str, Any] | None = None,
    ) -> tuple[ExperimentRevision, list[ExperimentVariantModel]]:
        """Create a new versioned revision for an existing experiment root."""
        root = await cls.get_root(session, root_id)
        if root is None:
            raise ValueError(f"ExperimentRoot {root_id} not found")

        # Determine next revision number
        stmt = (
            select(ExperimentRevision.revision_number)
            .where(ExperimentRevision.experiment_root_id == root_id)
            .order_by(ExperimentRevision.revision_number.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        latest_rev = res.scalar_one_or_none() or 0
        new_rev_number = latest_rev + 1
        new_rev_id = uuid4()

        revision = ExperimentRevision(
            id=new_rev_id,
            experiment_root_id=root_id,
            revision_number=new_rev_number,
            supersedes_revision_id=root.current_revision_id,
            experiment_type=experiment_type,
            hypothesis=hypothesis,
            primary_metric=primary_metric,
            secondary_metrics=secondary_metrics,
            assignment_policy=assignment_policy,
            experiment_unit=experiment_unit,
            minimum_sample_size=minimum_sample_size,
            analysis_window_hours=analysis_window_hours,
            provenance=provenance or {},
        )
        session.add(revision)

        for dim in dimensions:
            session.add(
                ExperimentRevisionDimension(
                    id=uuid4(),
                    experiment_revision_id=new_rev_id,
                    dimension=dim,
                )
            )

        variant_models = []
        for v in variants_data:
            v_id = v.get("variant_id") or uuid4()
            v_hash = v.get("variant_snapshot_hash") or cls.compute_variant_hash(
                media_artifact_id=v["media_artifact_id"],
                title=v.get("title"),
                thumbnail_ref=v.get("thumbnail_ref"),
                change_dimension=v["change_dimension"],
                packaging_plan_id=v.get("packaging_plan_id"),
            )
            v_model = ExperimentVariantModel(
                id=v_id,
                experiment_revision_id=new_rev_id,
                role=v["role"],
                change_dimension=v["change_dimension"],
                media_artifact_id=v["media_artifact_id"],
                packaging_plan_id=v.get("packaging_plan_id"),
                title=v.get("title"),
                thumbnail_concept_id=v.get("thumbnail_concept_id"),
                thumbnail_ref=v.get("thumbnail_ref"),
                is_accepted_p24=v.get("is_accepted_p24", False),
                creative_qa_status=v.get("creative_qa_status", "FAIL"),
                variant_snapshot_hash=v_hash,
                provenance=v.get("provenance", {}),
            )
            session.add(v_model)
            variant_models.append(v_model)

        await session.flush()
        root.current_revision_id = new_rev_id
        await session.flush()
        return revision, variant_models

    @classmethod
    async def get_root(cls, session: AsyncSession, root_id: UUID) -> ExperimentRoot | None:
        """Fetch experiment root by ID."""
        stmt = (
            select(ExperimentRoot)
            .options(selectinload(ExperimentRoot.revisions))
            .where(ExperimentRoot.id == root_id)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def get_revision(cls, session: AsyncSession, revision_id: UUID) -> ExperimentRevision | None:
        """Fetch revision by ID with loaded variants and dimensions."""
        stmt = (
            select(ExperimentRevision)
            .options(
                selectinload(ExperimentRevision.dimensions),
                selectinload(ExperimentRevision.variants),
                selectinload(ExperimentRevision.root),
            )
            .where(ExperimentRevision.id == revision_id)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def get_variants_for_revision(
        cls, session: AsyncSession, revision_id: UUID
    ) -> list[ExperimentVariantModel]:
        """Fetch all variants belonging to an experiment revision."""
        stmt = select(ExperimentVariantModel).where(
            ExperimentVariantModel.experiment_revision_id == revision_id
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def transition_root_status(
        cls,
        session: AsyncSession,
        root_id: UUID,
        new_status: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> ExperimentRoot:
        """Atomically update operational status and timestamps of an experiment root and current revision."""
        root = await cls.get_root(session, root_id)
        if root is None:
            raise ValueError(f"ExperimentRoot {root_id} not found")

        root.status = new_status
        root.updated_at = datetime.now(UTC)

        if root.current_revision_id:
            rev_stmt = select(ExperimentRevision).where(ExperimentRevision.id == root.current_revision_id)
            rev_res = await session.execute(rev_stmt)
            rev = rev_res.scalar_one_or_none()
            if rev:
                if start_at:
                    rev.start_at = start_at
                if end_at:
                    rev.end_at = end_at

        await session.flush()
        return root

    @classmethod
    async def find_active_overlapping_experiments(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        target_scope_type: str,
        target_scope_id: str,
        dimensions: list[str],
        exclude_root_id: UUID | None = None,
    ) -> list[ExperimentRoot]:
        """Find active experiments on the same target scope that manipulate any overlapping creative dimensions."""
        stmt = (
            select(ExperimentRoot)
            .join(ExperimentRevision, ExperimentRoot.current_revision_id == ExperimentRevision.id)
            .join(
                ExperimentRevisionDimension,
                ExperimentRevision.id == ExperimentRevisionDimension.experiment_revision_id,
            )
            .where(
                and_(
                    ExperimentRoot.channel_id == channel_id,
                    ExperimentRoot.target_scope_type == target_scope_type,
                    ExperimentRoot.target_scope_id == target_scope_id,
                    ExperimentRoot.status.in_(["READY", "RUNNING"]),
                    ExperimentRevisionDimension.dimension.in_(dimensions),
                )
            )
            .distinct()
        )
        if exclude_root_id:
            stmt = stmt.where(ExperimentRoot.id != exclude_root_id)

        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def record_exposure(
        cls,
        session: AsyncSession,
        revision_id: UUID,
        variant_id: UUID,
        exposure_mode: str,
        sample_count: int = 1,
        subject_key: str | None = None,
        source_lineage: dict[str, Any] | None = None,
        exposed_at: datetime | None = None,
    ) -> ExperimentExposureModel:
        """Persist durable exposure record."""
        exposure = ExperimentExposureModel(
            id=uuid4(),
            experiment_revision_id=revision_id,
            variant_id=variant_id,
            exposure_mode=exposure_mode,
            subject_key=subject_key,
            sample_count=sample_count,
            exposed_at=exposed_at or datetime.now(UTC),
            source_lineage=source_lineage or {},
        )
        session.add(exposure)
        await session.flush()
        return exposure

    @classmethod
    async def get_exposures_for_revision(
        cls, session: AsyncSession, revision_id: UUID
    ) -> list[ExperimentExposureModel]:
        """Fetch all exposure records for an experiment revision."""
        stmt = select(ExperimentExposureModel).where(
            ExperimentExposureModel.experiment_revision_id == revision_id
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def find_attribution_result(
        cls,
        session: AsyncSession,
        revision_id: UUID,
        control_variant_id: UUID,
        treatment_variant_id: UUID,
        metric: str,
        analysis_window: str,
        input_lineage_fingerprint: str,
    ) -> ExperimentAttributionResultModel | None:
        """Find existing immutable attribution result matching exact deterministic identity."""
        stmt = (
            select(ExperimentAttributionResultModel)
            .options(selectinload(ExperimentAttributionResultModel.analysis_inputs))
            .where(
                and_(
                    ExperimentAttributionResultModel.experiment_revision_id == revision_id,
                    ExperimentAttributionResultModel.control_variant_id == control_variant_id,
                    ExperimentAttributionResultModel.treatment_variant_id == treatment_variant_id,
                    ExperimentAttributionResultModel.metric == metric,
                    ExperimentAttributionResultModel.analysis_window == analysis_window,
                    ExperimentAttributionResultModel.input_lineage_fingerprint == input_lineage_fingerprint,
                )
            )
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def persist_attribution_result(
        cls,
        session: AsyncSession,
        revision_id: UUID,
        control_variant_id: UUID,
        treatment_variant_id: UUID,
        metric: str,
        analysis_window: str,
        input_lineage_fingerprint: str,
        control_value: float | None,
        treatment_value: float | None,
        absolute_difference: float | None,
        relative_lift: float | None,
        sample_basis: dict[str, Any],
        statistical_inference: dict[str, Any] | None,
        data_maturity: str,
        classification: str,
        findings: list[str],
        analysis_inputs: list[dict[str, Any]],
        evaluated_at: datetime | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> ExperimentAttributionResultModel:
        """Append immutable attribution result and normalized analysis input lineage."""
        # Recheck for idempotency
        existing = await cls.find_attribution_result(
            session=session,
            revision_id=revision_id,
            control_variant_id=control_variant_id,
            treatment_variant_id=treatment_variant_id,
            metric=metric,
            analysis_window=analysis_window,
            input_lineage_fingerprint=input_lineage_fingerprint,
        )
        if existing is not None:
            return existing

        res_id = uuid4()
        now_utc = evaluated_at or datetime.now(UTC)

        result_model = ExperimentAttributionResultModel(
            id=res_id,
            experiment_revision_id=revision_id,
            control_variant_id=control_variant_id,
            treatment_variant_id=treatment_variant_id,
            metric=metric,
            analysis_window=analysis_window,
            input_lineage_fingerprint=input_lineage_fingerprint,
            control_value=control_value,
            treatment_value=treatment_value,
            absolute_difference=absolute_difference,
            relative_lift=relative_lift,
            sample_basis=sample_basis,
            statistical_inference=statistical_inference,
            data_maturity=data_maturity,
            classification=classification,
            findings=findings,
            evaluated_at=now_utc,
            provenance=provenance or {},
        )
        session.add(result_model)

        for inp in analysis_inputs:
            input_row = ExperimentAnalysisInputModel(
                id=uuid4(),
                attribution_result_id=res_id,
                snapshot_id=inp.get("snapshot_id"),
                snapshot_type=inp.get("snapshot_type", "ANALYTICS_PROVIDER_SNAPSHOT"),
                snapshot_checksum=inp.get("snapshot_checksum", ""),
                retrieval_timestamp=inp.get("retrieval_timestamp", now_utc),
            )
            session.add(input_row)

        await session.flush()
        return result_model

    @classmethod
    async def get_attribution_result(
        cls, session: AsyncSession, result_id: UUID
    ) -> ExperimentAttributionResultModel | None:
        """Fetch attribution result by ID with lineage."""
        stmt = (
            select(ExperimentAttributionResultModel)
            .options(selectinload(ExperimentAttributionResultModel.analysis_inputs))
            .where(ExperimentAttributionResultModel.id == result_id)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def list_experiments_for_channel(
        cls, session: AsyncSession, channel_id: UUID
    ) -> list[ExperimentRoot]:
        """List all experiment roots for a channel."""
        stmt = (
            select(ExperimentRoot)
            .options(selectinload(ExperimentRoot.revisions))
            .where(ExperimentRoot.channel_id == channel_id)
            .order_by(ExperimentRoot.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())
