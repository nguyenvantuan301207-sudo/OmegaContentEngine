"""Domain contracts, typed metric payloads, and dimension validators for P20-B Pipeline Analytics.

Single source of truth for metric family schemas, dimension compatibility, and half-open UTC window rules.
Pure domain layer: zero infrastructure/database dependencies.
"""

from __future__ import annotations

import enum
import math
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MetricFamily(enum.StrEnum):
    """Canonical metric families for internal pipeline execution and reliability."""

    RENDER_RELIABILITY = "render_reliability"
    RENDER_PERFORMANCE = "render_performance"
    SCHEDULER_RELIABILITY = "scheduler_reliability"
    QA_QUALITY = "qa_quality"


class DimensionType(enum.StrEnum):
    """Canonical dimension classifications."""

    GLOBAL = "GLOBAL"
    CHANNEL = "CHANNEL"
    VIDEO_CODEC = "VIDEO_CODEC"
    SCHEDULE_TARGET_TYPE = "SCHEDULE_TARGET_TYPE"


# ── Dimension Compatibility Matrix ──────────────────────────────────────────

VALID_FAMILY_DIMENSIONS: dict[str, set[str]] = {
    MetricFamily.RENDER_RELIABILITY.value: {
        DimensionType.GLOBAL.value,
        DimensionType.CHANNEL.value,
        DimensionType.VIDEO_CODEC.value,
    },
    MetricFamily.RENDER_PERFORMANCE.value: {
        DimensionType.GLOBAL.value,
        DimensionType.CHANNEL.value,
        DimensionType.VIDEO_CODEC.value,
    },
    MetricFamily.SCHEDULER_RELIABILITY.value: {
        DimensionType.GLOBAL.value,
        DimensionType.SCHEDULE_TARGET_TYPE.value,
    },
    MetricFamily.QA_QUALITY.value: {
        DimensionType.GLOBAL.value,
        DimensionType.CHANNEL.value,
    },
}

SUPPORTED_VIDEO_CODECS: set[str] = {"h264", "hevc", "vp9", "libx264"}
SUPPORTED_SCHEDULE_TARGET_TYPES: set[str] = {"STANDALONE_MISSION", "CAMPAIGN_ADMISSION"}


def validate_family_and_dimension(
    family: str,
    dimension_type: str,
    dimension_value: str,
) -> None:
    """Validate family x dimension_type compatibility and dimension_value integrity.

    Shared by calculation engine, rollup service, backfill CLI, and API router.
    """
    if family not in VALID_FAMILY_DIMENSIONS:
        raise ValueError(f"Unknown metric family: '{family}'. Allowed: {sorted(VALID_FAMILY_DIMENSIONS.keys())}")

    allowed_dimensions = VALID_FAMILY_DIMENSIONS[family]
    if dimension_type not in allowed_dimensions:
        raise ValueError(
            f"Dimension type '{dimension_type}' is not allowed for family '{family}'. Allowed: {sorted(allowed_dimensions)}"
        )

    # Validate dimension_value syntax/domain
    if dimension_type == DimensionType.GLOBAL.value:
        if dimension_value != "ALL":
            raise ValueError(f"GLOBAL dimension value must be exactly 'ALL', got '{dimension_value}'")
    elif dimension_type == DimensionType.CHANNEL.value:
        try:
            UUID(dimension_value)
        except Exception as exc:
            raise ValueError(f"CHANNEL dimension value must be a valid UUID string, got '{dimension_value}'") from exc
    elif dimension_type == DimensionType.VIDEO_CODEC.value:
        val_lower = dimension_value.lower().strip()
        if val_lower not in SUPPORTED_VIDEO_CODECS:
            raise ValueError(
                f"VIDEO_CODEC dimension value must be one of {sorted(SUPPORTED_VIDEO_CODECS)}, got '{dimension_value}'"
            )
    elif dimension_type == DimensionType.SCHEDULE_TARGET_TYPE.value:
        if dimension_value not in SUPPORTED_SCHEDULE_TARGET_TYPES:
            raise ValueError(
                f"SCHEDULE_TARGET_TYPE must be one of {sorted(SUPPORTED_SCHEDULE_TARGET_TYPES)}, got '{dimension_value}'"
            )
    else:
        raise ValueError(f"Unhandled dimension type: '{dimension_type}'")


# ── Typed Metric Payloads (Pydantic, extra=forbid, finite numerics) ─────────


class _StrictMetricPayload(BaseModel):
    """Base class for all rollup metric payloads."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _validate_finite_numbers(cls, v: Any) -> Any:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise ValueError("NaN or Infinite floating point values are strictly forbidden in metric payloads")
        return v


class RenderReliabilityMetrics(_StrictMetricPayload):
    """Payload for render_reliability family."""

    total_terminal_jobs: int = Field(ge=0, description="Total completed jobs (SUCCEEDED + FAILED + CANCELLED)")
    succeeded_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    cancelled_jobs: int = Field(ge=0)
    success_rate: float = Field(ge=0.0, le=1.0, description="succeeded / (succeeded + failed); 0.0 if empty")
    failure_rate: float = Field(ge=0.0, le=1.0, description="failed / (succeeded + failed); 0.0 if empty")
    lease_expiry_terminal_failures: int = Field(ge=0, description="Jobs failed with WORKER_LEASE_EXPIRED")
    jobs_with_redispatch_in_terminal_cohort: int = Field(ge=0, description="Terminal jobs with generation > 1")
    redispatch_excess_generations_on_terminal_jobs: int = Field(ge=0, description="Sum of (generation - 1) on terminal jobs")
    dispatch_delivery_exhaustions: int = Field(ge=0, description="Jobs failed with DISPATCH_DELIVERY_EXHAUSTED")


class RenderPerformanceMetrics(_StrictMetricPayload):
    """Payload for render_performance family."""

    sample_count: int = Field(ge=0)
    dispatch_enrollment_p50_ms: float | None = Field(default=None, ge=0.0)
    dispatch_enrollment_p95_ms: float | None = Field(default=None, ge=0.0)
    broker_send_p50_ms: float | None = Field(default=None, ge=0.0)
    broker_send_p95_ms: float | None = Field(default=None, ge=0.0)
    worker_pickup_p50_ms: float | None = Field(default=None, ge=0.0)
    worker_pickup_p95_ms: float | None = Field(default=None, ge=0.0)
    render_execution_p50_ms: float | None = Field(default=None, ge=0.0)
    render_execution_p95_ms: float | None = Field(default=None, ge=0.0)


class SchedulerReliabilityMetrics(_StrictMetricPayload):
    """Payload for scheduler_reliability family."""

    total_occurrences: int = Field(ge=0)
    dispatched_occurrences: int = Field(ge=0)
    skipped_occurrences: int = Field(ge=0)
    failed_occurrences: int = Field(ge=0)
    occurrence_materialization_lateness_p50_ms: float | None = Field(default=None, ge=0.0)
    occurrence_materialization_lateness_p95_ms: float | None = Field(default=None, ge=0.0)


class QAQualityMetrics(_StrictMetricPayload):
    """Payload for qa_quality family."""

    total_qa_evaluations: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    passed_with_warnings_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0, description="(passed + passed_with_warnings) / total; 0.0 if empty")


METRIC_FAMILY_PAYLOAD_MAP: dict[str, type[_StrictMetricPayload]] = {
    MetricFamily.RENDER_RELIABILITY.value: RenderReliabilityMetrics,
    MetricFamily.RENDER_PERFORMANCE.value: RenderPerformanceMetrics,
    MetricFamily.SCHEDULER_RELIABILITY.value: SchedulerReliabilityMetrics,
    MetricFamily.QA_QUALITY.value: QAQualityMetrics,
}


def validate_metric_payload(family: str, payload_dict: dict[str, Any]) -> _StrictMetricPayload:
    """Validate a raw dictionary against the strict Pydantic model for the metric family."""
    if family not in METRIC_FAMILY_PAYLOAD_MAP:
        raise ValueError(f"No payload model registered for family: '{family}'")
    model_cls = METRIC_FAMILY_PAYLOAD_MAP[family]
    return model_cls.model_validate(payload_dict)


# ── Data Quality & Operational Counters ─────────────────────────────────────


class PipelineDataQuality(BaseModel):
    """Data quality and anomaly tracking without mutating authority rows."""

    model_config = ConfigDict(extra="forbid")

    negative_latency_anomalies: int = Field(default=0, ge=0)
    legacy_unattributed_terminal_jobs: int = Field(default=0, ge=0)
    missing_expected_timestamp_rows: int = Field(default=0, ge=0)
    rollup_compute_failures: int = Field(default=0, ge=0)


class AnalyticsSummaryResponse(BaseModel):
    """Read-only operational overview of current and lifetime system metrics."""

    current_waiting_occurrences: int = Field(ge=0)
    legacy_unattributed_terminal_jobs: int = Field(ge=0)
    cumulative_redispatch_excess_generations: int = Field(ge=0)
    total_terminal_jobs_with_completed_at: int = Field(ge=0)
    data_quality: PipelineDataQuality


# ── UTC Bucket Helpers ──────────────────────────────────────────────────────


def to_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and converted to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def get_daily_utc_bucket(dt: datetime) -> tuple[datetime, datetime]:
    """Return the half-open [bucket_start, bucket_end) for the UTC day containing dt."""
    utc_dt = to_utc(dt)
    start = datetime.combine(utc_dt.date(), time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    return start, end


def get_daily_utc_buckets_for_range(start_time: datetime, end_time: datetime) -> list[tuple[datetime, datetime]]:
    """Generate all complete daily UTC buckets strictly contained within [start_time, end_time)."""
    start_utc = to_utc(start_time)
    end_utc = to_utc(end_time)
    if start_utc >= end_utc:
        return []

    # First midnight at or after start_utc
    cur = datetime.combine(start_utc.date(), time.min, tzinfo=UTC)
    if cur < start_utc:
        cur += timedelta(days=1)

    buckets: list[tuple[datetime, datetime]] = []
    while cur + timedelta(days=1) <= end_utc:
        b_end = cur + timedelta(days=1)
        buckets.append((cur, b_end))
        cur = b_end

    return buckets
