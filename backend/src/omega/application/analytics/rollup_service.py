"""RollupService for P20-B Deterministic Pipeline Analytics.

Executes daily UTC rollups and enforces atomic full-family/bucket snapshot replacement.
Ensures stale dimension values are completely purged upon recomputation.
Guarantees idempotent execution and fail-closed transaction boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.metrics_engine import PipelineMetricsCalculationEngine
from omega.domain.pipeline_analytics import (
    DimensionType,
    MetricFamily,
    get_daily_utc_bucket,
    get_daily_utc_buckets_for_range,
    to_utc,
    validate_family_and_dimension,
    validate_metric_payload,
)
from omega.infrastructure.models import (
    Channel,
    PipelineAnalyticsRollup,
    RenderPlan,
)
from omega.logging import get_logger

logger = get_logger(service="omega-analytics-rollup")


class RollupService:
    """Service for managing daily UTC analytics rollups and atomic snapshot replacements."""

    @classmethod
    async def get_applicable_dimensions(
        cls,
        session: AsyncSession,
        family: str,
    ) -> list[tuple[str, str]]:
        """Return all (dimension_type, dimension_value) tuples applicable for this family."""
        dims: list[tuple[str, str]] = [(DimensionType.GLOBAL.value, "ALL")]

        if family in (MetricFamily.RENDER_RELIABILITY.value, MetricFamily.RENDER_PERFORMANCE.value, MetricFamily.QA_QUALITY.value):
            # Fetch active channel UUIDs
            channel_stmt = select(Channel.id)
            res_channels = await session.execute(channel_stmt)
            for (ch_id,) in res_channels.fetchall():
                dims.append((DimensionType.CHANNEL.value, str(ch_id)))

        if family in (MetricFamily.RENDER_RELIABILITY.value, MetricFamily.RENDER_PERFORMANCE.value):
            # Fetch distinct video codecs
            codec_stmt = select(RenderPlan.video_codec).distinct()
            res_codecs = await session.execute(codec_stmt)
            for (codec,) in res_codecs.fetchall():
                if codec:
                    codec_norm = codec.lower().strip()
                    dims.append((DimensionType.VIDEO_CODEC.value, codec_norm))

        if family == MetricFamily.SCHEDULER_RELIABILITY.value:
            dims.append((DimensionType.SCHEDULE_TARGET_TYPE.value, "STANDALONE_MISSION"))
            dims.append((DimensionType.SCHEDULE_TARGET_TYPE.value, "CAMPAIGN_ADMISSION"))

        return dims

    @classmethod
    async def compute_family_bucket_snapshot(
        cls,
        session: AsyncSession,
        family: str,
        bucket_start: datetime,
        bucket_end: datetime,
    ) -> list[dict[str, Any]]:
        """Compute the complete set of valid dimension rows for (family, bucket_start)."""
        start_utc = to_utc(bucket_start)
        end_utc = to_utc(bucket_end)
        dimensions = await cls.get_applicable_dimensions(session, family)

        rows: list[dict[str, Any]] = []
        for dim_type, dim_val in dimensions:
            validate_family_and_dimension(family, dim_type, dim_val)

            payload: Any
            sample_count = 0
            if family == MetricFamily.RENDER_RELIABILITY.value:
                payload = await PipelineMetricsCalculationEngine.compute_render_reliability(
                    session, start_utc, end_utc, dim_type, dim_val
                )
                sample_count = payload.total_terminal_jobs
            elif family == MetricFamily.RENDER_PERFORMANCE.value:
                payload, _anomalies = await PipelineMetricsCalculationEngine.compute_render_performance(
                    session, start_utc, end_utc, dim_type, dim_val
                )
                sample_count = payload.sample_count
            elif family == MetricFamily.SCHEDULER_RELIABILITY.value:
                payload = await PipelineMetricsCalculationEngine.compute_scheduler_reliability(
                    session, start_utc, end_utc, dim_type, dim_val
                )
                sample_count = payload.total_occurrences
            elif family == MetricFamily.QA_QUALITY.value:
                payload = await PipelineMetricsCalculationEngine.compute_qa_quality(
                    session, start_utc, end_utc, dim_type, dim_val
                )
                sample_count = payload.total_qa_evaluations
            else:
                raise ValueError(f"Unsupported family for rollup: '{family}'")

            # Validate typed payload
            payload_dict = payload.model_dump()
            validate_metric_payload(family, payload_dict)

            rows.append(
                {
                    "metric_family": family,
                    "dimension_type": dim_type,
                    "dimension_value": dim_val,
                    "bucket_start": start_utc,
                    "bucket_end": end_utc,
                    "metrics": payload_dict,
                    "sample_count": sample_count,
                    "schema_version": 1,
                }
            )

        return rows

    @classmethod
    async def replace_family_bucket_snapshot(
        cls,
        session: AsyncSession,
        family: str,
        bucket_start: datetime,
        bucket_end: datetime,
        snapshot_rows: list[dict[str, Any]],
    ) -> int:
        """Atomically replace the full dimension snapshot for (family, bucket_start).

        Deletes existing rows for this exact family + bucket_start, then inserts all newly
        calculated rows. On failure, transaction rolls back preserving prior state.
        Ensures stale dimension values are cleanly removed.
        """
        start_utc = to_utc(bucket_start)
        end_utc = to_utc(bucket_end)

        # 1. Acquire transaction-level advisory lock to serialize concurrent replacements of (family, bucket_start)
        try:
            bind = session.get_bind()
            if hasattr(bind, "dialect") and bind.dialect.name == "postgresql":
                lock_key = f"{family}:{start_utc.isoformat()}"
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                    {"lock_key": lock_key},
                )
        except Exception as exc:
            logger.warning("advisory_lock_skipped", family=family, error=str(exc))

        # 2. Delete prior rows for exact family + bucket_start
        try:
            delete_stmt = delete(PipelineAnalyticsRollup).where(
                PipelineAnalyticsRollup.metric_family == family,
                PipelineAnalyticsRollup.bucket_start == start_utc,
            )
            await session.execute(delete_stmt)

            # 3. Insert complete replacement snapshot
            new_objects = [
                PipelineAnalyticsRollup(
                    metric_family=row["metric_family"],
                    dimension_type=row["dimension_type"],
                    dimension_value=row["dimension_value"],
                    bucket_start=row.get("bucket_start", start_utc),
                    bucket_end=row.get("bucket_end", end_utc),
                    metrics=row["metrics"],
                    sample_count=row["sample_count"],
                    schema_version=row["schema_version"],
                )
                for row in snapshot_rows
            ]
            session.add_all(new_objects)
            await session.commit()

            logger.info(
                "rollup_snapshot_replaced",
                family=family,
                bucket_start=start_utc.isoformat(),
                inserted_rows=len(new_objects),
            )
            return len(new_objects)
        except Exception:
            await session.rollback()
            raise

    @classmethod
    async def recompute_and_replace_bucket(
        cls,
        session: AsyncSession,
        family: str,
        bucket_start: datetime,
        bucket_end: datetime,
    ) -> int:
        """Calculate and atomically replace one family bucket snapshot."""
        try:
            snapshot_rows = await cls.compute_family_bucket_snapshot(
                session, family, bucket_start, bucket_end
            )
            return await cls.replace_family_bucket_snapshot(
                session, family, bucket_start, bucket_end, snapshot_rows
            )
        except Exception as exc:
            await session.rollback()
            logger.error(
                "rollup_snapshot_replacement_failed",
                family=family,
                bucket_start=bucket_start.isoformat(),
                error=str(exc),
            )
            raise

    @classmethod
    async def run_lookback_rollups(
        cls,
        session: AsyncSession,
        lookback_days: int = 2,
    ) -> dict[str, Any]:
        """Recompute complete closed UTC days within the sliding lookback window."""
        now_utc = datetime.now(UTC)
        today_start = datetime.combine(now_utc.date(), time.min, tzinfo=UTC)
        window_start = today_start - timedelta(days=lookback_days)

        # Closed days only: strictly < today_start
        buckets = get_daily_utc_buckets_for_range(window_start, today_start)
        families = [
            MetricFamily.RENDER_RELIABILITY.value,
            MetricFamily.RENDER_PERFORMANCE.value,
            MetricFamily.SCHEDULER_RELIABILITY.value,
            MetricFamily.QA_QUALITY.value,
        ]

        results: dict[str, Any] = {"buckets_processed": len(buckets), "rows_written": 0}
        for b_start, b_end in buckets:
            for family in families:
                count = await cls.recompute_and_replace_bucket(session, family, b_start, b_end)
                results["rows_written"] += count

        return results
