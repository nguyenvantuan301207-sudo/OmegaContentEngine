"""Learning export contract service for OMEGA-012 Analytics Engine.

Exports immutable, factual features to OMEGA-013 Learning while strictly excluding heuristics.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from omega.domain.analytics import (
    CANONICAL_COMPUTED_FORMULAS,
    EMPTY_DIMENSIONS_HASH,
    AnalyticsExportContractError,
    ExportCandidate,
    LearningObservation,
    MetricClassification,
    MetricQuality,
    WindowState,
    WindowType,
)
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsComputedMetric,
    AnalyticsMetricLatestPointer,
    AnalyticsMetricObservation,
    AnalyticsWindow,
)


class LearningExportService:
    """Extracts factual features for consumption by downstream Learning Engine (OMEGA-013)."""

    @staticmethod
    async def enumerate_export_candidates(
        session: AsyncSession,
        after_updated_at: datetime,
        after_window_id: UUID,
        limit: int = 100,
    ) -> list[ExportCandidate]:
        """Enumerate finalized and revised analytics windows ordered deterministically.

        Uses keyset pagination on (updated_at ASC, id ASC).
        """
        allowed_states = [WindowState.FINALIZED.value, WindowState.REVISED.value]
        stmt = (
            select(
                AnalyticsWindow.id,
                AnalyticsWindow.asset_id,
                AnalyticsWindow.window_type,
                AnalyticsWindow.window_state,
                AnalyticsWindow.updated_at,
            )
            .where(
                AnalyticsWindow.window_state.in_(allowed_states),
                or_(
                    AnalyticsWindow.updated_at > after_updated_at,
                    and_(
                        AnalyticsWindow.updated_at == after_updated_at,
                        AnalyticsWindow.id > after_window_id,
                    ),
                ),
            )
            .order_by(AnalyticsWindow.updated_at.asc(), AnalyticsWindow.id.asc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        return [
            ExportCandidate(
                window_id=row.id,
                asset_id=row.asset_id,
                window_type=WindowType(row.window_type),
                window_state=WindowState(row.window_state),
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    @staticmethod
    async def export_observation_contract(
        session: AsyncSession,
        asset_id: UUID,
        window_type: WindowType,
        include_heuristics: bool = False,
    ) -> LearningObservation | None:
        """Export standardized learning observation facts and deterministic features.

        HEURISTIC_FEATURE metrics are strictly excluded by default.
        Exports ONLY canonical non-dimensional aggregate provider facts.
        """
        stmt_asset = select(AnalyticsAsset).where(AnalyticsAsset.id == asset_id)
        res_asset = await session.execute(stmt_asset)
        asset = res_asset.scalar_one_or_none()
        if asset is None:
            return None

        stmt_win = select(AnalyticsWindow).where(
            AnalyticsWindow.asset_id == asset.id,
            AnalyticsWindow.window_type == window_type.value,
        )
        res_win = await session.execute(stmt_win)
        window = res_win.scalar_one_or_none()
        if window is None:
            return None

        # Fetch latest aggregate provider fact observations (strictly EMPTY_DIMENSIONS_HASH == "")
        stmt_facts = (
            select(AnalyticsMetricObservation)
            .join(
                AnalyticsMetricLatestPointer,
                and_(
                    AnalyticsMetricLatestPointer.current_observation_id
                    == AnalyticsMetricObservation.id,
                    AnalyticsMetricLatestPointer.dimensions_hash == EMPTY_DIMENSIONS_HASH,
                ),
            )
            .where(
                AnalyticsMetricObservation.asset_id == asset.id,
                AnalyticsMetricObservation.window_id == window.id,
                AnalyticsMetricObservation.classification
                == MetricClassification.PROVIDER_FACT.value,
                AnalyticsMetricObservation.dimensions_hash == EMPTY_DIMENSIONS_HASH,
            )
            .order_by(
                AnalyticsMetricObservation.metric_name.asc(),
                AnalyticsMetricObservation.dimensions_hash.asc(),
                AnalyticsMetricObservation.id.asc(),
            )
        )
        facts = (await session.execute(stmt_facts)).scalars().all()

        # Fetch latest computed metrics restricted to canonical formula versions
        canonical_filters = [
            and_(
                AnalyticsComputedMetric.metric_name == name,
                AnalyticsComputedMetric.formula_identifier == formula_id,
                AnalyticsComputedMetric.formula_version == version,
            )
            for name, (formula_id, version) in CANONICAL_COMPUTED_FORMULAS.items()
        ]
        canonical_condition = or_(*canonical_filters)

        if include_heuristics:
            computed_filter = or_(
                and_(
                    AnalyticsComputedMetric.classification
                    == MetricClassification.DETERMINISTIC_DERIVED.value,
                    canonical_condition,
                ),
                AnalyticsComputedMetric.classification
                == MetricClassification.HEURISTIC_FEATURE.value,
            )
        else:
            computed_filter = and_(
                AnalyticsComputedMetric.classification
                == MetricClassification.DETERMINISTIC_DERIVED.value,
                canonical_condition,
            )

        subq = (
            select(
                AnalyticsComputedMetric,
                func.row_number()
                .over(
                    partition_by=AnalyticsComputedMetric.metric_name,
                    order_by=(
                        AnalyticsComputedMetric.computation_revision_sequence.desc(),
                        AnalyticsComputedMetric.computed_at.desc(),
                        AnalyticsComputedMetric.id.desc(),
                    ),
                )
                .label("rn"),
            )
            .where(
                AnalyticsComputedMetric.asset_id == asset.id,
                AnalyticsComputedMetric.window_id == window.id,
                computed_filter,
            )
            .subquery()
        )
        aliased_comp = aliased(AnalyticsComputedMetric, subq)
        stmt_comp = (
            select(aliased_comp).where(subq.c.rn == 1).order_by(aliased_comp.metric_name.asc())
        )
        computed = (await session.execute(stmt_comp)).scalars().all()

        metrics_map: dict[str, float | int | None] = {}
        qualities_map: dict[str, MetricQuality] = {}
        classes_map: dict[str, MetricClassification] = {}
        seen_metrics: set[str] = set()

        for fact in facts:
            if fact.metric_name in seen_metrics:
                raise AnalyticsExportContractError(
                    f"Duplicate aggregate provider fact detected for metric '{fact.metric_name}' "
                    f"in asset {asset.id}, window {window.id}. Schema v1 requires exactly one value."
                )
            seen_metrics.add(fact.metric_name)
            metrics_map[fact.metric_name] = (
                fact.integer_value if fact.integer_value is not None else fact.numeric_value
            )
            qualities_map[fact.metric_name] = MetricQuality(fact.metric_quality)
            classes_map[fact.metric_name] = MetricClassification.PROVIDER_FACT

        for comp in computed:
            if comp.metric_name in seen_metrics:
                raise AnalyticsExportContractError(
                    f"Duplicate computed metric detected for metric '{comp.metric_name}' "
                    f"in asset {asset.id}, window {window.id}."
                )
            seen_metrics.add(comp.metric_name)
            metrics_map[comp.metric_name] = comp.numeric_value
            qualities_map[comp.metric_name] = MetricQuality(comp.metric_quality)
            classes_map[comp.metric_name] = MetricClassification(comp.classification)

        is_finalized = window.window_state in (
            WindowState.FINALIZED.value,
            WindowState.REVISED.value,
        )
        quality_flags = sorted(list(set(q.value for q in qualities_map.values())))

        return LearningObservation(
            observation_schema_version=1,
            observation_id=window.id,
            channel_id=asset.channel_id,
            publish_intent_id=asset.publish_intent_id,
            provider_video_id=asset.provider_video_id,
            media_artifact_id=asset.media_artifact_id,
            channel_dna_revision_id=asset.channel_dna_revision_id,
            published_at_utc=asset.published_at,
            window_type=WindowType(window.window_type),
            window_state=WindowState(window.window_state),
            window_start_utc=window.window_start_utc,
            window_end_utc=window.window_end_utc,
            metrics=metrics_map,
            metric_qualities=qualities_map,
            classifications=classes_map,
            is_fully_finalized=is_finalized,
            quality_flags=quality_flags,
        )
