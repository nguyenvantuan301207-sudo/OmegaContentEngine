"""Unit tests for PipelineMetricsCalculationEngine formulas and semantic correctness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from omega.application.analytics.metrics_engine import (
    PipelineMetricsCalculationEngine,
    _calculate_percentile,
)
from omega.domain.pipeline_analytics import (
    DimensionType,
    MetricFamily,
    to_utc,
)


def test_percentile_calculation():
    """Verify linear interpolation percentile calculation."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _calculate_percentile(values, 0.50) == 30.0
    assert _calculate_percentile(values, 0.95) == 48.0
    assert _calculate_percentile([], 0.50) is None


class MockResult:
    """Mock database query execution result."""

    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class MockAsyncSession:
    """Lightweight pure-python async session mock for calculation engine unit tests."""

    def __init__(self, execute_fn):
        self.execute_fn = execute_fn

    async def execute(self, stmt):
        return self.execute_fn(stmt)


@pytest.mark.asyncio
async def test_render_reliability_no_synthetic_terminal_timestamp():
    """Historical rows with completed_at IS NULL must NEVER be assigned to daily buckets."""
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    # Simulated returned rows matching:
    # WHERE completed_at IS NOT NULL AND completed_at >= t_start AND completed_at < t_end
    # (state, error_code, dispatch_generation)
    simulated_rows = [
        ("SUCCEEDED", None, 1),
        ("FAILED", "WORKER_LEASE_EXPIRED", 1),
        ("CANCELLED", None, 1),
        # Notice: any row with completed_at IS NULL was filtered out by SQL predicate!
    ]

    session = MockAsyncSession(execute_fn=lambda stmt: MockResult(rows=simulated_rows))

    metrics = await PipelineMetricsCalculationEngine.compute_render_reliability(
        session, t_start, t_end, DimensionType.GLOBAL.value, "ALL"
    )

    assert metrics.total_terminal_jobs == 3
    assert metrics.succeeded_jobs == 1
    assert metrics.failed_jobs == 1
    assert metrics.cancelled_jobs == 1
    # Denominator = succeeded (1) + failed (1) = 2. Cancelled excluded from rate denominator.
    assert metrics.success_rate == 0.5
    assert metrics.failure_rate == 0.5
    assert metrics.lease_expiry_terminal_failures == 1


@pytest.mark.asyncio
async def test_lr3_cumulative_generation_semantics():
    """Verify LR3 redispatch excess generations in terminal cohort."""
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    simulated_rows = [
        ("SUCCEEDED", None, 3),  # 2 excess redispatches
        ("FAILED", "DISPATCH_DELIVERY_EXHAUSTED", 3),  # 2 excess redispatches
    ]

    session = MockAsyncSession(execute_fn=lambda stmt: MockResult(rows=simulated_rows))

    metrics = await PipelineMetricsCalculationEngine.compute_render_reliability(
        session, t_start, t_end, DimensionType.GLOBAL.value, "ALL"
    )

    assert metrics.jobs_with_redispatch_in_terminal_cohort == 2
    assert metrics.redispatch_excess_generations_on_terminal_jobs == 4  # (3-1) + (3-1) = 4
    assert metrics.dispatch_delivery_exhaustions == 1


@pytest.mark.asyncio
async def test_render_dispatch_intent_filter_and_latency():
    """Broker send latency must filter ONLY RenderJob dispatches and detect negative anomalies."""
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    def _execute(stmt):
        sql_str = str(stmt)
        if "dispatch_started_at" in sql_str and "created_at" in sql_str:
            # Dispatch enrollment
            return MockResult(rows=[(t_start, t_start + timedelta(seconds=2))])
        elif "started_at" in sql_str and "dispatch_started_at" in sql_str:
            # Worker pickup
            return MockResult(rows=[(t_start, t_start + timedelta(seconds=3))])
        elif "completed_at" in sql_str and "started_at" in sql_str:
            # Render execution
            return MockResult(rows=[(t_start, t_start + timedelta(seconds=10))])
        elif "durable_dispatch_intents" in sql_str:
            # Broker send: valid 5000ms + negative anomaly -1000ms
            return MockResult(
                rows=[
                    (t_start, t_start + timedelta(seconds=5)),  # 5000ms
                    (t_start + timedelta(seconds=5), t_start + timedelta(seconds=4)),  # -1000ms anomaly!
                ]
            )
        return MockResult()

    session = MockAsyncSession(execute_fn=_execute)

    perf, anomalies = await PipelineMetricsCalculationEngine.compute_render_performance(
        session, t_start, t_end, DimensionType.GLOBAL.value, "ALL"
    )

    assert perf.broker_send_p50_ms == 5000.0
    assert anomalies == 1


@pytest.mark.asyncio
async def test_scheduler_reliability_and_lateness():
    """Scheduler occurrences attributed by occurrence_at with materialization lateness."""
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    simulated_rows = [
        ("DISPATCHED", t_start + timedelta(hours=1), t_start + timedelta(hours=1, seconds=10)),  # 10s
        ("SKIPPED", t_start + timedelta(hours=2), t_start + timedelta(hours=2, seconds=20)),  # 20s
    ]

    session = MockAsyncSession(execute_fn=lambda stmt: MockResult(rows=simulated_rows))

    metrics = await PipelineMetricsCalculationEngine.compute_scheduler_reliability(
        session, t_start, t_end, DimensionType.GLOBAL.value, "ALL"
    )

    assert metrics.total_occurrences == 2
    assert metrics.dispatched_occurrences == 1
    assert metrics.skipped_occurrences == 1
    assert metrics.occurrence_materialization_lateness_p50_ms == 15000.0


@pytest.mark.asyncio
async def test_qa_quality_distribution():
    """QA metrics report distribution of verdicts without claiming first-pass rate."""
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    simulated_statuses = [
        ("PASSED",),
        ("PASSED_WITH_WARNINGS",),
        ("BLOCKED",),
    ]

    session = MockAsyncSession(execute_fn=lambda stmt: MockResult(rows=simulated_statuses))

    metrics = await PipelineMetricsCalculationEngine.compute_qa_quality(
        session, t_start, t_end, DimensionType.GLOBAL.value, "ALL"
    )

    assert metrics.total_qa_evaluations == 3
    assert metrics.passed_count == 1
    assert metrics.passed_with_warnings_count == 1
    assert metrics.blocked_count == 1
    assert metrics.pass_rate == round(2 / 3, 4)


@pytest.mark.asyncio
async def test_dispatch_delivery_exhaustion_canonical_semantics():
    """Verify dispatch_delivery_exhaustions counts strictly FAILED + DISPATCH_DELIVERY_EXHAUSTED.

    - FAILED + DISPATCH_DELIVERY_EXHAUSTED -> counted
    - FAILED with another error (e.g. WORKER_LEASE_EXPIRED) at max attempts -> NOT counted
    - SUCCEEDED at high attempt count / generation -> NOT counted
    """
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    simulated_rows = [
        ("FAILED", "DISPATCH_DELIVERY_EXHAUSTED", 3),  # Counted
        ("FAILED", "WORKER_LEASE_EXPIRED", 3),          # NOT counted as dispatch exhaustion
        ("FAILED", "RENDER_CRASH_SEGFAULT", 5),        # NOT counted as dispatch exhaustion
        ("SUCCEEDED", None, 4),                        # High attempt/generation, NOT counted
        ("CANCELLED", None, 3),                        # Cancelled, NOT counted
    ]

    session = MockAsyncSession(execute_fn=lambda stmt: MockResult(rows=simulated_rows))

    metrics = await PipelineMetricsCalculationEngine.compute_render_reliability(
        session, t_start, t_end, DimensionType.GLOBAL.value, "ALL"
    )

    assert metrics.total_terminal_jobs == 5
    assert metrics.succeeded_jobs == 1
    assert metrics.failed_jobs == 3
    assert metrics.cancelled_jobs == 1
    assert metrics.dispatch_delivery_exhaustions == 1
    assert metrics.lease_expiry_terminal_failures == 1
