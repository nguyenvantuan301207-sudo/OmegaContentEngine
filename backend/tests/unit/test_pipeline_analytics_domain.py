"""Unit tests for P20-B Pipeline Analytics domain contracts, schemas, and validators."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from omega.application.analytics.hybrid_query_engine import HybridQueryEngine
from omega.domain.pipeline_analytics import (
    DimensionType,
    MetricFamily,
    QAQualityMetrics,
    RenderPerformanceMetrics,
    RenderReliabilityMetrics,
    SchedulerReliabilityMetrics,
    get_daily_utc_bucket,
    get_daily_utc_buckets_for_range,
    validate_family_and_dimension,
    validate_metric_payload,
)


def test_metric_family_and_dimension_matrix_valid():
    """Valid combinations in family-dimension matrix must pass without error."""
    # Render reliability
    validate_family_and_dimension(MetricFamily.RENDER_RELIABILITY.value, DimensionType.GLOBAL.value, "ALL")
    validate_family_and_dimension(MetricFamily.RENDER_RELIABILITY.value, DimensionType.CHANNEL.value, str(uuid4()))
    validate_family_and_dimension(MetricFamily.RENDER_RELIABILITY.value, DimensionType.VIDEO_CODEC.value, "h264")
    validate_family_and_dimension(MetricFamily.RENDER_RELIABILITY.value, DimensionType.VIDEO_CODEC.value, "hevc")

    # Render performance
    validate_family_and_dimension(MetricFamily.RENDER_PERFORMANCE.value, DimensionType.GLOBAL.value, "ALL")
    validate_family_and_dimension(MetricFamily.RENDER_PERFORMANCE.value, DimensionType.CHANNEL.value, str(uuid4()))
    validate_family_and_dimension(MetricFamily.RENDER_PERFORMANCE.value, DimensionType.VIDEO_CODEC.value, "vp9")

    # Scheduler reliability
    validate_family_and_dimension(MetricFamily.SCHEDULER_RELIABILITY.value, DimensionType.GLOBAL.value, "ALL")
    validate_family_and_dimension(
        MetricFamily.SCHEDULER_RELIABILITY.value, DimensionType.SCHEDULE_TARGET_TYPE.value, "STANDALONE_MISSION"
    )
    validate_family_and_dimension(
        MetricFamily.SCHEDULER_RELIABILITY.value, DimensionType.SCHEDULE_TARGET_TYPE.value, "CAMPAIGN_ADMISSION"
    )

    # QA Quality
    validate_family_and_dimension(MetricFamily.QA_QUALITY.value, DimensionType.GLOBAL.value, "ALL")
    validate_family_and_dimension(MetricFamily.QA_QUALITY.value, DimensionType.CHANNEL.value, str(uuid4()))


def test_metric_family_and_dimension_matrix_invalid():
    """Invalid combinations in family-dimension matrix must be rejected."""
    # scheduler_reliability x VIDEO_CODEC must fail
    with pytest.raises(ValueError, match="not allowed for family 'scheduler_reliability'"):
        validate_family_and_dimension(MetricFamily.SCHEDULER_RELIABILITY.value, DimensionType.VIDEO_CODEC.value, "h264")

    # render_reliability x SCHEDULE_TARGET_TYPE must fail
    with pytest.raises(ValueError, match="not allowed for family 'render_reliability'"):
        validate_family_and_dimension(
            MetricFamily.RENDER_RELIABILITY.value, DimensionType.SCHEDULE_TARGET_TYPE.value, "CAMPAIGN_ADMISSION"
        )

    # qa_quality x VIDEO_CODEC must fail
    with pytest.raises(ValueError, match="not allowed for family 'qa_quality'"):
        validate_family_and_dimension(MetricFamily.QA_QUALITY.value, DimensionType.VIDEO_CODEC.value, "h264")


def test_dimension_value_syntax_validation():
    """Dimension values must adhere to strict type and domain rules."""
    # GLOBAL must be exactly 'ALL'
    with pytest.raises(ValueError, match="GLOBAL dimension value must be exactly 'ALL'"):
        validate_family_and_dimension(MetricFamily.RENDER_RELIABILITY.value, DimensionType.GLOBAL.value, "INVALID")

    # CHANNEL must be valid UUID
    with pytest.raises(ValueError, match="CHANNEL dimension value must be a valid UUID string"):
        validate_family_and_dimension(MetricFamily.RENDER_RELIABILITY.value, DimensionType.CHANNEL.value, "not-a-uuid")

    # VIDEO_CODEC must be supported
    with pytest.raises(ValueError, match="VIDEO_CODEC dimension value must be one of"):
        validate_family_and_dimension(MetricFamily.RENDER_RELIABILITY.value, DimensionType.VIDEO_CODEC.value, "wmv3")

    # SCHEDULE_TARGET_TYPE must be supported enum
    with pytest.raises(ValueError, match="SCHEDULE_TARGET_TYPE must be one of"):
        validate_family_and_dimension(
            MetricFamily.SCHEDULER_RELIABILITY.value, DimensionType.SCHEDULE_TARGET_TYPE.value, "RANDOM_TARGET"
        )


def test_payload_validation_extra_fields_forbidden():
    """Rollup payloads must forbid any extra undeclared fields."""
    valid_data = {
        "total_terminal_jobs": 10,
        "succeeded_jobs": 8,
        "failed_jobs": 2,
        "cancelled_jobs": 0,
        "success_rate": 0.8,
        "failure_rate": 0.2,
        "lease_expiry_terminal_failures": 1,
        "jobs_with_redispatch_in_terminal_cohort": 1,
        "redispatch_excess_generations_on_terminal_jobs": 1,
        "dispatch_delivery_exhaustions": 0,
    }
    # Valid data passes
    metrics = validate_metric_payload(MetricFamily.RENDER_RELIABILITY.value, valid_data)
    assert isinstance(metrics, RenderReliabilityMetrics)

    # Extra field must raise ValidationError
    invalid_data = dict(valid_data)
    invalid_data["unauthorized_field"] = "malicious_payload"
    with pytest.raises(ValidationError):
        validate_metric_payload(MetricFamily.RENDER_RELIABILITY.value, invalid_data)


def test_payload_validation_nan_infinity_forbidden():
    """NaN and Infinite floats must be strictly rejected."""
    nan_data = {
        "sample_count": 1,
        "dispatch_enrollment_p50_ms": float("nan"),
    }
    with pytest.raises(ValidationError, match="NaN or Infinite"):
        RenderPerformanceMetrics(**nan_data)

    inf_data = {
        "sample_count": 1,
        "dispatch_enrollment_p50_ms": float("inf"),
    }
    with pytest.raises(ValidationError, match="NaN or Infinite"):
        RenderPerformanceMetrics(**inf_data)


def test_payload_validation_bounds_and_counts():
    """Counts must be >= 0 and rates must be between 0.0 and 1.0."""
    # Negative count fails
    with pytest.raises(ValidationError):
        RenderReliabilityMetrics(
            total_terminal_jobs=-1,
            succeeded_jobs=0,
            failed_jobs=0,
            cancelled_jobs=0,
            success_rate=0.0,
            failure_rate=0.0,
            lease_expiry_terminal_failures=0,
            jobs_with_redispatch_in_terminal_cohort=0,
            redispatch_excess_generations_on_terminal_jobs=0,
            dispatch_delivery_exhaustions=0,
        )

    # Rate > 1.0 fails
    with pytest.raises(ValidationError):
        RenderReliabilityMetrics(
            total_terminal_jobs=1,
            succeeded_jobs=1,
            failed_jobs=0,
            cancelled_jobs=0,
            success_rate=1.5,
            failure_rate=0.0,
            lease_expiry_terminal_failures=0,
            jobs_with_redispatch_in_terminal_cohort=0,
            redispatch_excess_generations_on_terminal_jobs=0,
            dispatch_delivery_exhaustions=0,
        )


def test_daily_utc_bucket_edges_and_leap_day():
    """Verify daily UTC bucket calculations across leap days and year transitions."""
    # Leap day: 2024-02-29
    dt_leap = datetime(2024, 2, 29, 14, 30, tzinfo=UTC)
    b_start, b_end = get_daily_utc_bucket(dt_leap)
    assert b_start == datetime(2024, 2, 29, 0, 0, tzinfo=UTC)
    assert b_end == datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
    assert b_end - b_start == timedelta(days=1)

    # Year transition: 2026-12-31
    dt_nye = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    b_start, b_end = get_daily_utc_bucket(dt_nye)
    assert b_start == datetime(2026, 12, 31, 0, 0, tzinfo=UTC)
    assert b_end == datetime(2027, 1, 1, 0, 0, tzinfo=UTC)

    # get_daily_utc_buckets_for_range
    start = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    end = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    buckets = get_daily_utc_buckets_for_range(start, end)
    assert len(buckets) == 3
    assert buckets[0] == (datetime(2026, 9, 28, 0, 0, tzinfo=UTC), datetime(2026, 9, 29, 0, 0, tzinfo=UTC))
    assert buckets[1] == (datetime(2026, 9, 29, 0, 0, tzinfo=UTC), datetime(2026, 9, 30, 0, 0, tzinfo=UTC))
    assert buckets[2] == (datetime(2026, 9, 30, 0, 0, tzinfo=UTC), datetime(2026, 10, 1, 0, 0, tzinfo=UTC))


def test_hybrid_query_partitioning_exact_half_open():
    """Verify exact half-open mathematical partitioning of arbitrary query ranges."""
    now_ref = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)

    # Case 1: Mid-day start to mid-day end across 3 days
    start = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    end = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

    partitions = HybridQueryEngine.partition_query_intervals(start, end, now_utc=now_ref)

    # Partial first day: [2026-10-02 10:00, 2026-10-03 00:00)
    assert partitions["partial_first"] == (
        datetime(2026, 10, 2, 10, 0, tzinfo=UTC),
        datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
    )

    # Closed complete days: [2026-10-03, 2026-10-04) and [2026-10-04, 2026-10-05)
    assert partitions["closed_complete"] == [
        (datetime(2026, 10, 3, 0, 0, tzinfo=UTC), datetime(2026, 10, 4, 0, 0, tzinfo=UTC)),
        (datetime(2026, 10, 4, 0, 0, tzinfo=UTC), datetime(2026, 10, 5, 0, 0, tzinfo=UTC)),
    ]

    # Current open day: [2026-10-05 00:00, 2026-10-05 12:00)
    assert partitions["current_open"] == (
        datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
    )

    # No partial last closed day in this scenario
    assert partitions["partial_last_closed"] is None

    # Verify zero gap and zero overlap
    t0, t1 = partitions["partial_first"]
    c0_start, c0_end = partitions["closed_complete"][0]
    c1_start, c1_end = partitions["closed_complete"][1]
    o_start, o_end = partitions["current_open"]

    assert t1 == c0_start
    assert c0_end == c1_start
    assert c1_end == o_start
    assert o_end == end


def test_hybrid_query_rejects_inverted_range():
    """Query partitioning must reject end_time <= start_time."""
    start = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    end = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="must be greater than start_time"):
        HybridQueryEngine.partition_query_intervals(start, end)
