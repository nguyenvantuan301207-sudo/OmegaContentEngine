"""Hybrid Query Engine for P20-B Deterministic Pipeline Analytics.

Executes arbitrary-range queries over half-open [start_time, end_time) UTC windows.
Correctly partitions:
- Partial first day -> dynamic authoritative SQL
- Complete closed UTC days -> daily rollups (with authoritative SQL fallback on cache miss)
- Current open day -> dynamic authoritative SQL
- Partial last closed day -> dynamic authoritative SQL

Guarantees 0 overlap, 0 gap, 0 double-counting.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.metrics_engine import PipelineMetricsCalculationEngine
from omega.domain.pipeline_analytics import (
    DimensionType,
    MetricFamily,
    to_utc,
    validate_family_and_dimension,
)
from omega.infrastructure.models import PipelineAnalyticsRollup


class HybridQueryEngine:
    """Engine for executing hybrid queries combining daily rollups and dynamic authoritative SQL."""

    @classmethod
    def partition_query_intervals(
        cls,
        start_time: datetime,
        end_time: datetime,
        now_utc: datetime | None = None,
    ) -> dict[str, Any]:
        """Mathematically partition [start_time, end_time) into disjoint segments.

        Returns:
            {
                "partial_first": tuple[datetime, datetime] | None,
                "closed_complete": list[tuple[datetime, datetime]],
                "partial_last_closed": tuple[datetime, datetime] | None,
                "current_open": tuple[datetime, datetime] | None,
            }
        """
        start = to_utc(start_time)
        end = to_utc(end_time)
        if end <= start:
            raise ValueError(f"Invalid time range: end_time ({end}) must be greater than start_time ({start})")

        now = to_utc(now_utc or datetime.now(UTC))
        today_start = datetime.combine(now.date(), time.min, tzinfo=UTC)

        # Segment 1: Any portion strictly within current open day (today_start onwards)
        current_open: tuple[datetime, datetime] | None = None
        historical_end = end
        if end > today_start:
            open_start = max(start, today_start)
            current_open = (open_start, end)
            historical_end = open_start

        # If entire interval was in current open day
        if historical_end <= start:
            return {
                "partial_first": None,
                "closed_complete": [],
                "partial_last_closed": None,
                "current_open": current_open,
            }

        # Partition closed historical interval [start, historical_end)
        # Determine first midnight at or after start
        start_midnight = datetime.combine(start.date(), time.min, tzinfo=UTC)
        next_midnight_after_start = start_midnight + timedelta(days=1)

        partial_first: tuple[datetime, datetime] | None = None
        complete_start = start_midnight

        if start > start_midnight:
            # Partial first day exists
            p_end = min(next_midnight_after_start, historical_end)
            partial_first = (start, p_end)
            complete_start = p_end

        # Determine last midnight before or at historical_end
        end_midnight = datetime.combine(historical_end.date(), time.min, tzinfo=UTC)
        partial_last: tuple[datetime, datetime] | None = None
        complete_end = historical_end

        if historical_end > end_midnight:
            if complete_start < end_midnight:
                partial_last = (end_midnight, historical_end)
                complete_end = end_midnight
            elif partial_first is None and start < historical_end:
                # Range falls entirely within one single closed day
                partial_first = (start, historical_end)
                complete_start = historical_end
                complete_end = historical_end

        # Closed complete days strictly between complete_start and complete_end
        closed_complete: list[tuple[datetime, datetime]] = []
        cur = complete_start
        while cur + timedelta(days=1) <= complete_end:
            nxt = cur + timedelta(days=1)
            closed_complete.append((cur, nxt))
            cur = nxt

        return {
            "partial_first": partial_first,
            "closed_complete": closed_complete,
            "partial_last_closed": partial_last,
            "current_open": current_open,
        }

    @classmethod
    async def _compute_dynamic_interval(
        cls,
        session: AsyncSession,
        family: str,
        start_time: datetime,
        end_time: datetime,
        dimension_type: str,
        dimension_value: str,
        is_live: bool,
    ) -> dict[str, Any]:
        """Compute metrics dynamically via calculation engine for an ad-hoc interval."""
        sample_count = 0
        payload: Any

        if family == MetricFamily.RENDER_RELIABILITY.value:
            payload = await PipelineMetricsCalculationEngine.compute_render_reliability(
                session, start_time, end_time, dimension_type, dimension_value
            )
            sample_count = payload.total_terminal_jobs
        elif family == MetricFamily.RENDER_PERFORMANCE.value:
            payload, _ = await PipelineMetricsCalculationEngine.compute_render_performance(
                session, start_time, end_time, dimension_type, dimension_value
            )
            sample_count = payload.sample_count
        elif family == MetricFamily.SCHEDULER_RELIABILITY.value:
            payload = await PipelineMetricsCalculationEngine.compute_scheduler_reliability(
                session, start_time, end_time, dimension_type, dimension_value
            )
            sample_count = payload.total_occurrences
        elif family == MetricFamily.QA_QUALITY.value:
            payload = await PipelineMetricsCalculationEngine.compute_qa_quality(
                session, start_time, end_time, dimension_type, dimension_value
            )
            sample_count = payload.total_qa_evaluations
        else:
            raise ValueError(f"Unknown family: {family}")

        return {
            "metric_family": family,
            "dimension_type": dimension_type,
            "dimension_value": dimension_value,
            "bucket_start": start_time,
            "bucket_end": end_time,
            "metrics": payload.model_dump(),
            "sample_count": sample_count,
            "is_live": is_live,
            "is_rollup": False,
        }

    @classmethod
    async def query_hybrid_series(
        cls,
        session: AsyncSession,
        family: str,
        start_time: datetime,
        end_time: datetime,
        dimension_type: str = DimensionType.GLOBAL.value,
        dimension_value: str = "ALL",
        now_utc: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Query a continuous time series over [start_time, end_time) with hybrid rollups and live fallback."""
        validate_family_and_dimension(family, dimension_type, dimension_value)
        partitions = cls.partition_query_intervals(start_time, end_time, now_utc=now_utc)

        results: list[dict[str, Any]] = []

        # 1. Partial first day (dynamic authoritative SQL)
        if partitions["partial_first"]:
            p_start, p_end = partitions["partial_first"]
            res = await cls._compute_dynamic_interval(
                session, family, p_start, p_end, dimension_type, dimension_value, is_live=False
            )
            results.append(res)

        # 2. Closed complete UTC days (read from rollup table, or fallback to dynamic SQL)
        for c_start, c_end in partitions["closed_complete"]:
            stmt = select(PipelineAnalyticsRollup).where(
                PipelineAnalyticsRollup.metric_family == family,
                PipelineAnalyticsRollup.dimension_type == dimension_type,
                PipelineAnalyticsRollup.dimension_value == dimension_value,
                PipelineAnalyticsRollup.bucket_start == c_start,
            )
            res = (await session.execute(stmt)).scalar_one_or_none()
            if res is not None:
                results.append(
                    {
                        "metric_family": family,
                        "dimension_type": dimension_type,
                        "dimension_value": dimension_value,
                        "bucket_start": res.bucket_start,
                        "bucket_end": res.bucket_end,
                        "metrics": res.metrics,
                        "sample_count": res.sample_count,
                        "is_live": False,
                        "is_rollup": True,
                    }
                )
            else:
                # Rollup cache miss: fallback cleanly to dynamic SQL
                fallback = await cls._compute_dynamic_interval(
                    session, family, c_start, c_end, dimension_type, dimension_value, is_live=False
                )
                results.append(fallback)

        # 3. Partial last closed day (dynamic authoritative SQL)
        if partitions["partial_last_closed"]:
            l_start, l_end = partitions["partial_last_closed"]
            res = await cls._compute_dynamic_interval(
                session, family, l_start, l_end, dimension_type, dimension_value, is_live=False
            )
            results.append(res)

        # 4. Current open day (dynamic authoritative SQL)
        if partitions["current_open"]:
            o_start, o_end = partitions["current_open"]
            res = await cls._compute_dynamic_interval(
                session, family, o_start, o_end, dimension_type, dimension_value, is_live=True
            )
            results.append(res)

        return results
