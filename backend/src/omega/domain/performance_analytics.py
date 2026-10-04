"""Domain models and contracts for P25-B Published Performance Analytics.

Strictly answers: "How did a published production perform?"
Does NOT answer:
- Which variant caused improvement? (P25-C attribution)
- What should Omega change next? (P25-D learning loop)

Pure domain layer: zero vendor SDK dependencies, zero mutation of external providers.
"""

from __future__ import annotations

import enum
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Canonical Enums & Constants ──────────────────────────────────────────────


class DataFreshnessStatus(enum.StrEnum):
    """Categorical freshness status of provider performance analytics."""

    FRESH = "FRESH"
    DELAYED = "DELAYED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class MetricSemanticPolicy:
    """Documented semantic definitions and units for canonical metrics.

    Semantics:
    - CTR: Canonically represented as a decimal ratio in range [0.0, 1.0] (e.g., 0.05 = 5%).
    - Watch Time: Canonical unit is seconds (float).
    - Average View Duration: Canonical unit is seconds (float).
    - Average Percentage Viewed: Canonically represented as a decimal ratio in range [0.0, 1.0] (e.g., 0.65 = 65%).
    - Monotonic Counters: views, watch_time_seconds, likes, comments, shares, subscribers_gained, subscribers_lost.
      Must never be summed across temporal snapshots; deltas are computed between consecutive snapshots.
    - Rates: CTR, average percentage viewed. Must never be summed across time windows.
    - Timestamps: Always UTC.
    """

    CTR_UNIT = "RATIO_0_TO_1"
    WATCH_TIME_UNIT = "SECONDS"
    DURATION_UNIT = "SECONDS"
    PERCENTAGE_VIEWED_UNIT = "RATIO_0_TO_1"
    TIMEZONE = "UTC"


# ── Retention Curve Domain Models ────────────────────────────────────────────


class RetentionPoint(BaseModel):
    """A single normalized point on an audience retention curve."""

    relative_position: float = Field(..., ge=0.0, le=1.0, description="Normalized video position from 0.0 to 1.0")
    retention_ratio: float = Field(..., ge=0.0, le=1.0, description="Audience retention ratio from 0.0 to 1.0")

    model_config = ConfigDict(frozen=True)


class RetentionCurve(BaseModel):
    """Audience retention curve consisting of monotonic normalized position points."""

    points: list[RetentionPoint] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)

    @field_validator("points")
    @classmethod
    def validate_points_ordering(cls, points: list[RetentionPoint]) -> list[RetentionPoint]:
        if not points:
            return points
        for i in range(len(points) - 1):
            if points[i].relative_position > points[i + 1].relative_position:
                raise ValueError(
                    f"Retention curve points must have non-decreasing relative_position: "
                    f"pos[{i}]={points[i].relative_position} > pos[{i+1}]={points[i+1].relative_position}"
                )
        return points

    def interpolate_at_position(self, target_pos: float) -> float | None:
        """Deterministically interpolate retention ratio at target relative position [0.0, 1.0]."""
        if not self.points:
            return None
        target_pos = max(0.0, min(1.0, float(target_pos)))

        if target_pos <= self.points[0].relative_position:
            return self.points[0].retention_ratio
        if target_pos >= self.points[-1].relative_position:
            return self.points[-1].retention_ratio

        for i in range(len(self.points) - 1):
            p0 = self.points[i]
            p1 = self.points[i + 1]
            if p0.relative_position <= target_pos <= p1.relative_position:
                segment_width = p1.relative_position - p0.relative_position
                if segment_width == 0:
                    return p0.retention_ratio
                t = (target_pos - p0.relative_position) / segment_width
                ratio = p0.retention_ratio + t * (p1.retention_ratio - p0.retention_ratio)
                return max(0.0, min(1.0, round(ratio, 4)))
        return self.points[-1].retention_ratio


class DerivedRetentionMetrics(BaseModel):
    """Deterministic summary metrics derived from a retention curve without model judgments."""

    retention_at_30s: float | None = None
    retention_at_50_percent: float | None = None
    retention_at_90_percent: float | None = None
    early_dropoff_rate: float | None = None
    late_retention: float | None = None

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_curve(
        cls,
        curve: RetentionCurve | None,
        duration_seconds: float | None = None,
    ) -> DerivedRetentionMetrics:
        """Derive standard retention milestone metrics from a validated RetentionCurve."""
        if curve is None or not curve.points:
            return cls()

        ret_50 = curve.interpolate_at_position(0.50)
        ret_90 = curve.interpolate_at_position(0.90)

        # Early dropoff: drop between start (0.0) and early milestone (0.10)
        ret_0 = curve.interpolate_at_position(0.0)
        ret_10 = curve.interpolate_at_position(0.10)
        early_dropoff = None
        if ret_0 is not None and ret_10 is not None:
            early_dropoff = max(0.0, round(ret_0 - ret_10, 4))

        # Retention at 30 seconds if video duration is provided and valid
        ret_30s = None
        if duration_seconds is not None and duration_seconds > 0:
            target_pos = min(1.0, 30.0 / duration_seconds)
            ret_30s = curve.interpolate_at_position(target_pos)

        return cls(
            retention_at_30s=ret_30s,
            retention_at_50_percent=ret_50,
            retention_at_90_percent=ret_90,
            early_dropoff_rate=early_dropoff,
            late_retention=ret_90,
        )


# ── Canonical Metrics Model ──────────────────────────────────────────────────


class CanonicalMetrics(BaseModel):
    """Authoritative normalized performance metrics for a published video.

    Values are strictly non-fabricated. Missing or unsupported metrics are None.
    """

    views: int | None = Field(default=None, ge=0)
    impressions: int | None = Field(default=None, ge=0)
    watch_time_seconds: float | None = Field(default=None, ge=0.0)
    average_view_duration_seconds: float | None = Field(default=None, ge=0.0)
    average_percentage_viewed: float | None = Field(default=None, ge=0.0, le=1.0)
    impressions_ctr: float | None = Field(default=None, ge=0.0, le=1.0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    subscribers_gained: int | None = Field(default=None, ge=0)
    subscribers_lost: int | None = Field(default=None, ge=0)
    net_subscribers: int | None = None
    unique_viewers: int | None = Field(default=None, ge=0)
    metric_qualities: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def compute_net_subscribers(self) -> CanonicalMetrics:
        if self.subscribers_gained is not None and self.subscribers_lost is not None:
            computed_net = self.subscribers_gained - self.subscribers_lost
            if self.net_subscribers != computed_net:
                object.__setattr__(self, "net_subscribers", computed_net)
        return self


# ── Raw Provider Snapshot ────────────────────────────────────────────────────


class RawProviderMetricsPayload(BaseModel):
    """Immutable raw provider truth captured directly from provider response before normalization.

    Excludes secrets, OAuth tokens, and authorization headers.
    """

    provider: str
    external_media_id: str
    provider_timestamp: datetime
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_data: dict[str, Any]
    schema_version: str = "1.0"
    ingestion_provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    def compute_checksum(self) -> str:
        """Compute deterministic payload checksum."""
        canonical_str = json.dumps(
            {
                "provider": self.provider,
                "external_media_id": self.external_media_id,
                "raw_data": self.raw_data,
                "schema_version": self.schema_version,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


# ── Anomaly and Delta Models ────────────────────────────────────────────────


class CounterAnomalyRecord(BaseModel):
    """Detection record for suspicious decreases in monotonic cumulative counters."""

    metric_name: str
    previous_value: float | int
    current_value: float | int
    difference: float | int
    status: str = "ANOMALY_DETECTED"
    message: str

    model_config = ConfigDict(frozen=True)


class DeltaVelocityMetrics(BaseModel):
    """Bounded delta and rate-of-change metrics between two comparable snapshots."""

    views_delta: int | None = None
    watch_time_delta_seconds: float | None = None
    views_per_hour: float | None = None
    watch_time_per_hour_seconds: float | None = None
    subscriber_delta: int | None = None
    time_delta_seconds: float | None = None
    is_valid: bool = True
    notes: str | None = None

    model_config = ConfigDict(frozen=True)

    @classmethod
    def compute(
        cls,
        current: CanonicalMetrics,
        current_time: datetime,
        previous: CanonicalMetrics | None,
        previous_time: datetime | None,
    ) -> DeltaVelocityMetrics:
        """Derive bounded delta and velocity metrics between two observation points."""
        if previous is None or previous_time is None:
            return cls(is_valid=False, notes="NO_PREVIOUS_SNAPSHOT")

        delta_seconds = (current_time - previous_time).total_seconds()
        if delta_seconds <= 0:
            return cls(is_valid=False, time_delta_seconds=delta_seconds, notes="NON_POSITIVE_TIME_INTERVAL")

        hours = delta_seconds / 3600.0

        # Views delta & velocity
        v_delta = None
        v_per_hour = None
        if current.views is not None and previous.views is not None:
            v_delta = current.views - previous.views
            v_per_hour = round(v_delta / hours, 2)

        # Watch time delta & velocity
        wt_delta = None
        wt_per_hour = None
        if current.watch_time_seconds is not None and previous.watch_time_seconds is not None:
            wt_delta = round(current.watch_time_seconds - previous.watch_time_seconds, 2)
            wt_per_hour = round(wt_delta / hours, 2)

        # Net subscriber delta
        sub_delta = None
        curr_sub = current.net_subscribers if current.net_subscribers is not None else current.subscribers_gained
        prev_sub = previous.net_subscribers if previous.net_subscribers is not None else previous.subscribers_gained
        if curr_sub is not None and prev_sub is not None:
            sub_delta = curr_sub - prev_sub

        return cls(
            views_delta=v_delta,
            watch_time_delta_seconds=wt_delta,
            views_per_hour=v_per_hour,
            watch_time_per_hour_seconds=wt_per_hour,
            subscriber_delta=sub_delta,
            time_delta_seconds=round(delta_seconds, 2),
            is_valid=True,
        )


# ── Canonical Performance Snapshot ──────────────────────────────────────────


class PerformanceSnapshot(BaseModel):
    """Canonical, inspectable performance snapshot attached to a PublishReceipt / external media ID."""

    snapshot_id: UUID = Field(default_factory=uuid4)
    receipt_id: UUID
    provider: str
    external_media_id: str
    observed_at: datetime
    freshness: DataFreshnessStatus
    metrics: CanonicalMetrics
    retention_curve: RetentionCurve | None = None
    derived_retention: DerivedRetentionMetrics | None = None
    deltas: DeltaVelocityMetrics | None = None
    anomalies: list[CounterAnomalyRecord] = Field(default_factory=list)
    raw_snapshot_id: UUID | None = None
    raw_payload_checksum: str | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)
    sync_checkpoint: str | None = None

    model_config = ConfigDict(frozen=True)


class PerformanceSummary(BaseModel):
    """Deterministic summary representation of published performance."""

    external_media_id: str
    receipt_id: UUID
    provider: str
    latest_observed_at: datetime
    freshness: DataFreshnessStatus
    views: int | None = None
    watch_time_seconds: float | None = None
    impressions_ctr: float | None = None
    average_view_duration_seconds: float | None = None
    average_percentage_viewed: float | None = None
    engagement_score: float | None = None
    subscriber_impact: int | None = None
    retention_at_50_percent: float | None = None
    snapshot_count: int = 0
    age_hours: float = 0.0
    has_anomalies: bool = False

    model_config = ConfigDict(frozen=True)
