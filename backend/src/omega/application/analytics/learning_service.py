"""Learning export contract service for OMEGA-012 Analytics Engine.

Exports immutable, factual features to OMEGA-013 Learning while strictly excluding heuristics.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.analytics import (
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
    async def export_observation_contract(
        session: AsyncSession,
        asset_id: UUID,
        window_type: WindowType,
        include_heuristics: bool = False,
    ) -> LearningObservation | None:
        """Export standardized learning observation facts and deterministic features.

        HEURISTIC_FEATURE metrics are strictly excluded by default.
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

        # Fetch latest provider fact observations
        stmt_facts = (
            select(AnalyticsMetricObservation)
            .join(
                AnalyticsMetricLatestPointer,
                AnalyticsMetricLatestPointer.current_observation_id
                == AnalyticsMetricObservation.id,
            )
            .where(
                AnalyticsMetricObservation.asset_id == asset.id,
                AnalyticsMetricObservation.window_id == window.id,
                AnalyticsMetricObservation.classification
                == MetricClassification.PROVIDER_FACT.value,
            )
        )
        facts = (await session.execute(stmt_facts)).scalars().all()

        # Fetch latest computed metrics
        allowed_classes = [MetricClassification.DETERMINISTIC_DERIVED.value]
        if include_heuristics:
            allowed_classes.append(MetricClassification.HEURISTIC_FEATURE.value)

        stmt_comp = select(AnalyticsComputedMetric).where(
            AnalyticsComputedMetric.asset_id == asset.id,
            AnalyticsComputedMetric.window_id == window.id,
            AnalyticsComputedMetric.classification.in_(allowed_classes),
        )
        computed = (await session.execute(stmt_comp)).scalars().all()

        metrics_map: dict[str, float | int | None] = {}
        qualities_map: dict[str, MetricQuality] = {}
        classes_map: dict[str, MetricClassification] = {}

        for fact in facts:
            metrics_map[fact.metric_name] = (
                fact.integer_value if fact.integer_value is not None else fact.numeric_value
            )
            qualities_map[fact.metric_name] = MetricQuality(fact.metric_quality)
            classes_map[fact.metric_name] = MetricClassification.PROVIDER_FACT

        for comp in computed:
            metrics_map[comp.metric_name] = comp.numeric_value
            qualities_map[comp.metric_name] = MetricQuality(comp.metric_quality)
            classes_map[comp.metric_name] = MetricClassification(comp.classification)

        is_finalized = window.window_state == WindowState.FINALIZED.value
        quality_flags = [q.value for q in qualities_map.values()]

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
