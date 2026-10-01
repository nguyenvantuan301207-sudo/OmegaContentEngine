"""Pipeline Metrics Calculation Engine for P20-B.

Single authoritative calculation implementation for:
- Live dynamic API calculations
- Historical daily rollup service
- Bounded backfill CLI

Enforces strict [start_time, end_time) half-open time windows in UTC.
Never fabricates terminal timestamps from created_at or started_at.
Filters render dispatches cleanly from DurableDispatchIntent.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.pipeline_analytics import (
    DimensionType,
    MetricFamily,
    PipelineDataQuality,
    QAQualityMetrics,
    RenderPerformanceMetrics,
    RenderReliabilityMetrics,
    SchedulerReliabilityMetrics,
    to_utc,
    validate_family_and_dimension,
)
from omega.domain.production import RenderJobState
from omega.infrastructure.models import (
    DurableDispatchIntent,
    MediaArtifact,
    ProductionQAResult,
    ProductionRenderJob,
    ProductionRequest,
    RecurringScheduleOccurrence,
    RenderPlan,
)


def _calculate_percentile(values: list[float], percentile: float) -> float | None:
    """Calculate the p-th percentile (0.0 to 1.0) using linear interpolation."""
    if not values:
        return None
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * percentile
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return float(round(d0 + d1, 2))


class PipelineMetricsCalculationEngine:
    """Authoritative computation engine for pipeline reliability, latency, and quality."""

    @classmethod
    async def compute_render_reliability(
        cls,
        session: AsyncSession,
        start_time: datetime,
        end_time: datetime,
        dimension_type: str = DimensionType.GLOBAL.value,
        dimension_value: str = "ALL",
    ) -> RenderReliabilityMetrics:
        """Compute render reliability metrics over half-open [start_time, end_time).

        Attribution: Strictly completed_at.
        Rows missing completed_at are NEVER attributed to a daily bucket (NO_SYNTHETIC_TERMINAL_TIMESTAMP).
        Denominator: SUCCEEDED + FAILED. CANCELLED is reported separately and excluded from rate denominator.
        """
        start_utc = to_utc(start_time)
        end_utc = to_utc(end_time)
        validate_family_and_dimension(MetricFamily.RENDER_RELIABILITY.value, dimension_type, dimension_value)

        stmt = (
            select(
                ProductionRenderJob.state,
                ProductionRenderJob.error_code,
                ProductionRenderJob.dispatch_generation,
            )
            .where(
                ProductionRenderJob.state.in_(["SUCCEEDED", "FAILED", "CANCELLED"]),
                ProductionRenderJob.completed_at.is_not(None),
                ProductionRenderJob.completed_at >= start_utc,
                ProductionRenderJob.completed_at < end_utc,
            )
        )

        if dimension_type == DimensionType.CHANNEL.value:
            channel_uuid = UUID(dimension_value)
            stmt = stmt.join(
                ProductionRequest,
                ProductionRequest.id == ProductionRenderJob.production_request_id,
            ).where(ProductionRequest.channel_id == channel_uuid)
        elif dimension_type == DimensionType.VIDEO_CODEC.value:
            codec_val = dimension_value.lower().strip()
            stmt = stmt.join(
                RenderPlan,
                RenderPlan.id == ProductionRenderJob.render_plan_id,
            ).where(func.lower(RenderPlan.video_codec) == codec_val)

        res = await session.execute(stmt)
        rows = res.fetchall()

        total = len(rows)
        succeeded = 0
        failed = 0
        cancelled = 0
        lease_expired = 0
        jobs_redispatched = 0
        excess_generations = 0
        dispatch_exhausted = 0

        for state, error_code, generation in rows:
            if state == "SUCCEEDED":
                succeeded += 1
            elif state == "FAILED":
                failed += 1
            elif state == "CANCELLED":
                cancelled += 1

            if state == "FAILED" and error_code == "WORKER_LEASE_EXPIRED":
                lease_expired += 1
            if state == "FAILED" and error_code == "DISPATCH_DELIVERY_EXHAUSTED":
                dispatch_exhausted += 1

            gen = generation or 1
            if gen > 1:
                jobs_redispatched += 1
                excess_generations += gen - 1

        denom = succeeded + failed
        success_rate = round(succeeded / denom, 4) if denom > 0 else 0.0
        failure_rate = round(failed / denom, 4) if denom > 0 else 0.0

        return RenderReliabilityMetrics(
            total_terminal_jobs=total,
            succeeded_jobs=succeeded,
            failed_jobs=failed,
            cancelled_jobs=cancelled,
            success_rate=success_rate,
            failure_rate=failure_rate,
            lease_expiry_terminal_failures=lease_expired,
            jobs_with_redispatch_in_terminal_cohort=jobs_redispatched,
            redispatch_excess_generations_on_terminal_jobs=excess_generations,
            dispatch_delivery_exhaustions=dispatch_exhausted,
        )

    @classmethod
    async def compute_render_performance(
        cls,
        session: AsyncSession,
        start_time: datetime,
        end_time: datetime,
        dimension_type: str = DimensionType.GLOBAL.value,
        dimension_value: str = "ALL",
    ) -> tuple[RenderPerformanceMetrics, int]:
        """Compute latency percentiles over half-open [start_time, end_time).

        Filters only RenderJob dispatch intents (RENDER_DISPATCH_INTENT_FILTER_VERIFIED = YES).
        Excludes negative latencies from percentiles and returns anomaly count.
        """
        start_utc = to_utc(start_time)
        end_utc = to_utc(end_time)
        validate_family_and_dimension(MetricFamily.RENDER_PERFORMANCE.value, dimension_type, dimension_value)

        negative_anomalies = 0

        # 1. Dispatch Enrollment Latency: dispatch_started_at - created_at
        # Attributed by dispatch_started_at in window
        enrollment_stmt = (
            select(
                ProductionRenderJob.created_at,
                ProductionRenderJob.dispatch_started_at,
            )
            .where(
                ProductionRenderJob.dispatch_started_at.is_not(None),
                ProductionRenderJob.dispatch_started_at >= start_utc,
                ProductionRenderJob.dispatch_started_at < end_utc,
            )
        )
        if dimension_type == DimensionType.CHANNEL.value:
            channel_uuid = UUID(dimension_value)
            enrollment_stmt = enrollment_stmt.join(
                ProductionRequest,
                ProductionRequest.id == ProductionRenderJob.production_request_id,
            ).where(ProductionRequest.channel_id == channel_uuid)
        elif dimension_type == DimensionType.VIDEO_CODEC.value:
            codec_val = dimension_value.lower().strip()
            enrollment_stmt = enrollment_stmt.join(
                RenderPlan,
                RenderPlan.id == ProductionRenderJob.render_plan_id,
            ).where(func.lower(RenderPlan.video_codec) == codec_val)

        res_enroll = await session.execute(enrollment_stmt)
        enroll_latencies: list[float] = []
        for created, disp_started in res_enroll.fetchall():
            ms = (to_utc(disp_started) - to_utc(created)).total_seconds() * 1000.0
            if ms >= 0:
                enroll_latencies.append(ms)
            else:
                negative_anomalies += 1

        # 2. Worker Pickup Latency: started_at - dispatch_started_at
        # Attributed by started_at in window
        pickup_stmt = (
            select(
                ProductionRenderJob.dispatch_started_at,
                ProductionRenderJob.started_at,
            )
            .where(
                ProductionRenderJob.started_at.is_not(None),
                ProductionRenderJob.dispatch_started_at.is_not(None),
                ProductionRenderJob.started_at >= start_utc,
                ProductionRenderJob.started_at < end_utc,
            )
        )
        if dimension_type == DimensionType.CHANNEL.value:
            channel_uuid = UUID(dimension_value)
            pickup_stmt = pickup_stmt.join(
                ProductionRequest,
                ProductionRequest.id == ProductionRenderJob.production_request_id,
            ).where(ProductionRequest.channel_id == channel_uuid)
        elif dimension_type == DimensionType.VIDEO_CODEC.value:
            codec_val = dimension_value.lower().strip()
            pickup_stmt = pickup_stmt.join(
                RenderPlan,
                RenderPlan.id == ProductionRenderJob.render_plan_id,
            ).where(func.lower(RenderPlan.video_codec) == codec_val)

        res_pickup = await session.execute(pickup_stmt)
        pickup_latencies: list[float] = []
        for disp_started, started in res_pickup.fetchall():
            ms = (to_utc(started) - to_utc(disp_started)).total_seconds() * 1000.0
            if ms >= 0:
                pickup_latencies.append(ms)
            else:
                negative_anomalies += 1

        # 3. Render Execution Latency: completed_at - started_at
        # Attributed by completed_at in window
        exec_stmt = (
            select(
                ProductionRenderJob.started_at,
                ProductionRenderJob.completed_at,
            )
            .where(
                ProductionRenderJob.completed_at.is_not(None),
                ProductionRenderJob.started_at.is_not(None),
                ProductionRenderJob.completed_at >= start_utc,
                ProductionRenderJob.completed_at < end_utc,
            )
        )
        if dimension_type == DimensionType.CHANNEL.value:
            channel_uuid = UUID(dimension_value)
            exec_stmt = exec_stmt.join(
                ProductionRequest,
                ProductionRequest.id == ProductionRenderJob.production_request_id,
            ).where(ProductionRequest.channel_id == channel_uuid)
        elif dimension_type == DimensionType.VIDEO_CODEC.value:
            codec_val = dimension_value.lower().strip()
            exec_stmt = exec_stmt.join(
                RenderPlan,
                RenderPlan.id == ProductionRenderJob.render_plan_id,
            ).where(func.lower(RenderPlan.video_codec) == codec_val)

        res_exec = await session.execute(exec_stmt)
        exec_latencies: list[float] = []
        for started, completed in res_exec.fetchall():
            ms = (to_utc(completed) - to_utc(started)).total_seconds() * 1000.0
            if ms >= 0:
                exec_latencies.append(ms)
            else:
                negative_anomalies += 1

        # 4. Broker Send Latency: sent_at - created_at (DurableDispatchIntent)
        # Filtered strictly to RenderJob dispatches (RENDER_DISPATCH_INTENT_FILTER_VERIFIED = YES)
        broker_stmt = (
            select(
                DurableDispatchIntent.created_at,
                DurableDispatchIntent.sent_at,
            )
            .where(
                DurableDispatchIntent.task_name == "omega.production.render",
                DurableDispatchIntent.purpose == "PRODUCTION_RENDER_DISPATCH",
                DurableDispatchIntent.render_job_id.is_not(None),
                DurableDispatchIntent.sent_at.is_not(None),
                DurableDispatchIntent.sent_at >= start_utc,
                DurableDispatchIntent.sent_at < end_utc,
            )
        )
        if dimension_type == DimensionType.CHANNEL.value:
            channel_uuid = UUID(dimension_value)
            broker_stmt = broker_stmt.join(
                ProductionRequest,
                ProductionRequest.id == DurableDispatchIntent.production_request_id,
            ).where(ProductionRequest.channel_id == channel_uuid)
        elif dimension_type == DimensionType.VIDEO_CODEC.value:
            codec_val = dimension_value.lower().strip()
            broker_stmt = broker_stmt.join(
                ProductionRenderJob,
                ProductionRenderJob.id == DurableDispatchIntent.render_job_id,
            ).join(
                RenderPlan,
                RenderPlan.id == ProductionRenderJob.render_plan_id,
            ).where(func.lower(RenderPlan.video_codec) == codec_val)

        res_broker = await session.execute(broker_stmt)
        broker_latencies: list[float] = []
        for created, sent in res_broker.fetchall():
            ms = (to_utc(sent) - to_utc(created)).total_seconds() * 1000.0
            if ms >= 0:
                broker_latencies.append(ms)
            else:
                negative_anomalies += 1

        sample_count = len(enroll_latencies) + len(pickup_latencies) + len(exec_latencies) + len(broker_latencies)

        metrics = RenderPerformanceMetrics(
            sample_count=sample_count,
            dispatch_enrollment_p50_ms=_calculate_percentile(enroll_latencies, 0.50),
            dispatch_enrollment_p95_ms=_calculate_percentile(enroll_latencies, 0.95),
            broker_send_p50_ms=_calculate_percentile(broker_latencies, 0.50),
            broker_send_p95_ms=_calculate_percentile(broker_latencies, 0.95),
            worker_pickup_p50_ms=_calculate_percentile(pickup_latencies, 0.50),
            worker_pickup_p95_ms=_calculate_percentile(pickup_latencies, 0.95),
            render_execution_p50_ms=_calculate_percentile(exec_latencies, 0.50),
            render_execution_p95_ms=_calculate_percentile(exec_latencies, 0.95),
        )

        return metrics, negative_anomalies

    @classmethod
    async def compute_scheduler_reliability(
        cls,
        session: AsyncSession,
        start_time: datetime,
        end_time: datetime,
        dimension_type: str = DimensionType.GLOBAL.value,
        dimension_value: str = "ALL",
    ) -> SchedulerReliabilityMetrics:
        """Compute scheduler reliability and lateness over half-open [start_time, end_time).

        Attribution: Strictly occurrence_at.
        Lateness: created_at - occurrence_at.
        """
        start_utc = to_utc(start_time)
        end_utc = to_utc(end_time)
        validate_family_and_dimension(MetricFamily.SCHEDULER_RELIABILITY.value, dimension_type, dimension_value)

        stmt = (
            select(
                RecurringScheduleOccurrence.status,
                RecurringScheduleOccurrence.occurrence_at,
                RecurringScheduleOccurrence.created_at,
            )
            .where(
                RecurringScheduleOccurrence.occurrence_at >= start_utc,
                RecurringScheduleOccurrence.occurrence_at < end_utc,
            )
        )

        if dimension_type == DimensionType.SCHEDULE_TARGET_TYPE.value:
            stmt = stmt.where(RecurringScheduleOccurrence.downstream_target_type == dimension_value)

        res = await session.execute(stmt)
        rows = res.fetchall()

        total = len(rows)
        dispatched = 0
        skipped = 0
        failed = 0
        lateness_list: list[float] = []

        for status, occ_at, created_at in rows:
            if status == "DISPATCHED":
                dispatched += 1
            elif status == "SKIPPED":
                skipped += 1
            elif status == "FAILED":
                failed += 1

            if created_at is not None and occ_at is not None:
                ms = (to_utc(created_at) - to_utc(occ_at)).total_seconds() * 1000.0
                if ms >= 0:
                    lateness_list.append(ms)

        return SchedulerReliabilityMetrics(
            total_occurrences=total,
            dispatched_occurrences=dispatched,
            skipped_occurrences=skipped,
            failed_occurrences=failed,
            occurrence_materialization_lateness_p50_ms=_calculate_percentile(lateness_list, 0.50),
            occurrence_materialization_lateness_p95_ms=_calculate_percentile(lateness_list, 0.95),
        )

    @classmethod
    async def compute_qa_quality(
        cls,
        session: AsyncSession,
        start_time: datetime,
        end_time: datetime,
        dimension_type: str = DimensionType.GLOBAL.value,
        dimension_value: str = "ALL",
    ) -> QAQualityMetrics:
        """Compute QA quality distribution over half-open [start_time, end_time).

        Attribution: Strictly executed_at.
        No first-pass/regeneration inferences without lineage.
        """
        start_utc = to_utc(start_time)
        end_utc = to_utc(end_time)
        validate_family_and_dimension(MetricFamily.QA_QUALITY.value, dimension_type, dimension_value)

        stmt = (
            select(ProductionQAResult.status)
            .where(
                ProductionQAResult.executed_at >= start_utc,
                ProductionQAResult.executed_at < end_utc,
            )
        )

        if dimension_type == DimensionType.CHANNEL.value:
            channel_uuid = UUID(dimension_value)
            stmt = stmt.join(
                ProductionRequest,
                ProductionRequest.id == ProductionQAResult.production_request_id,
            ).where(ProductionRequest.channel_id == channel_uuid)

        res = await session.execute(stmt)
        statuses = [row[0] for row in res.fetchall()]

        total = len(statuses)
        passed = sum(1 for s in statuses if s == "PASSED")
        passed_with_warnings = sum(1 for s in statuses if s == "PASSED_WITH_WARNINGS")
        blocked = sum(1 for s in statuses if s == "BLOCKED")

        pass_rate = round((passed + passed_with_warnings) / total, 4) if total > 0 else 0.0

        return QAQualityMetrics(
            total_qa_evaluations=total,
            passed_count=passed,
            passed_with_warnings_count=passed_with_warnings,
            blocked_count=blocked,
            pass_rate=pass_rate,
        )

    @classmethod
    async def compute_data_quality_report(cls, session: AsyncSession) -> PipelineDataQuality:
        """Evaluate observational data quality across the database."""
        # Legacy unattributed terminal rows (missing completed_at)
        legacy_stmt = select(func.count(ProductionRenderJob.id)).where(
            ProductionRenderJob.state.in_(["SUCCEEDED", "FAILED", "CANCELLED"]),
            ProductionRenderJob.completed_at.is_(None),
        )
        legacy_unattributed = (await session.execute(legacy_stmt)).scalar_one_or_none() or 0

        # Missing expected timestamp rows: SUCCEEDED but started_at is NULL
        missing_ts_stmt = select(func.count(ProductionRenderJob.id)).where(
            ProductionRenderJob.state == "SUCCEEDED",
            ProductionRenderJob.started_at.is_(None),
        )
        missing_ts = (await session.execute(missing_ts_stmt)).scalar_one_or_none() or 0

        # Negative latencies (completed before started or started before created)
        neg_render_stmt = select(func.count(ProductionRenderJob.id)).where(
            ProductionRenderJob.completed_at.is_not(None),
            ProductionRenderJob.started_at.is_not(None),
            ProductionRenderJob.completed_at < ProductionRenderJob.started_at,
        )
        neg_render = (await session.execute(neg_render_stmt)).scalar_one_or_none() or 0

        neg_pickup_stmt = select(func.count(ProductionRenderJob.id)).where(
            ProductionRenderJob.started_at.is_not(None),
            ProductionRenderJob.dispatch_started_at.is_not(None),
            ProductionRenderJob.started_at < ProductionRenderJob.dispatch_started_at,
        )
        neg_pickup = (await session.execute(neg_pickup_stmt)).scalar_one_or_none() or 0

        return PipelineDataQuality(
            negative_latency_anomalies=neg_render + neg_pickup,
            legacy_unattributed_terminal_jobs=legacy_unattributed,
            missing_expected_timestamp_rows=missing_ts,
            rollup_compute_failures=0,
        )

    @classmethod
    async def compute_summary_counters(cls, session: AsyncSession) -> dict[str, Any]:
        """Compute live snapshot and cumulative counters for GET /api/v1/analytics/summary."""
        # 1. Current WAITING occurrences (live snapshot only)
        wait_stmt = select(func.count(RecurringScheduleOccurrence.id)).where(
            RecurringScheduleOccurrence.status == "WAITING"
        )
        waiting_count = (await session.execute(wait_stmt)).scalar_one_or_none() or 0

        # 2. Legacy unattributed terminal jobs
        legacy_stmt = select(func.count(ProductionRenderJob.id)).where(
            ProductionRenderJob.state.in_(["SUCCEEDED", "FAILED", "CANCELLED"]),
            ProductionRenderJob.completed_at.is_(None),
        )
        legacy_unattributed = (await session.execute(legacy_stmt)).scalar_one_or_none() or 0

        # 3. Cumulative redispatch excess generations across current durable generation state
        redispatch_stmt = select(
            func.coalesce(func.sum(func.greatest(ProductionRenderJob.dispatch_generation - 1, 0)), 0)
        )
        cumulative_redispatch = (await session.execute(redispatch_stmt)).scalar_one_or_none() or 0

        # 4. Total terminal jobs with completed_at populated
        terminal_attributed_stmt = select(func.count(ProductionRenderJob.id)).where(
            ProductionRenderJob.state.in_(["SUCCEEDED", "FAILED", "CANCELLED"]),
            ProductionRenderJob.completed_at.is_not(None),
        )
        terminal_attributed = (await session.execute(terminal_attributed_stmt)).scalar_one_or_none() or 0

        dq = await cls.compute_data_quality_report(session)

        return {
            "current_waiting_occurrences": waiting_count,
            "legacy_unattributed_terminal_jobs": legacy_unattributed,
            "cumulative_redispatch_excess_generations": cumulative_redispatch,
            "total_terminal_jobs_with_completed_at": terminal_attributed,
            "data_quality": dq.model_dump(),
        }
