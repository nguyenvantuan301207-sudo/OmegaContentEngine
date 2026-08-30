"""Unit tests for OMEGA-012 Analytics domain logic, timezone conversions, and quota math."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from omega.application.analytics.capabilities import MetricCapabilityRegistry
from omega.application.analytics.quota_service import QuotaService
from omega.domain.analytics import (
    MetricQuality,
    WindowType,
    compute_pacific_calendar_day_utc_range,
    compute_quota_reset_instants,
    compute_relative_window_utc_range,
)
from omega.infrastructure.models import AnalyticsQuotaBucket

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


# ── Timezone & DST Tests ───────────────────────────────────────────────────────


def test_pacific_standard_day_boundary_maps_to_utc_interval():
    """Verify standard Pacific winter day (PST, UTC-8) maps to 08:00 UTC next day."""
    winter_date = date(2026, 1, 15)
    start_utc, end_utc = compute_pacific_calendar_day_utc_range(winter_date)

    # 2026-01-15 00:00:00 PST == 2026-01-15 08:00:00 UTC
    assert start_utc == datetime(2026, 1, 15, 8, 0, 0, tzinfo=UTC)
    # 2026-01-16 00:00:00 PST == 2026-01-16 08:00:00 UTC
    assert end_utc == datetime(2026, 1, 16, 8, 0, 0, tzinfo=UTC)
    # Exact 24-hour duration
    assert (end_utc - start_utc).total_seconds() == 86400.0


def test_pacific_day_spring_forward_dst_duration_is_23_hours():
    """Verify second Sunday of March (Spring-Forward) produces an exact 23-hour Pacific day."""
    # In 2026, US Spring Forward is Sunday, March 8
    spring_date = date(2026, 3, 8)
    start_utc, end_utc = compute_pacific_calendar_day_utc_range(spring_date)

    # Starts at 00:00 PST (UTC-8) -> 08:00 UTC
    assert start_utc == datetime(2026, 3, 8, 8, 0, 0, tzinfo=UTC)
    # Next day starts at 00:00 PDT (UTC-7) -> 07:00 UTC
    assert end_utc == datetime(2026, 3, 9, 7, 0, 0, tzinfo=UTC)
    # Exactly 23 hours == 82,800 seconds
    duration_seconds = (end_utc - start_utc).total_seconds()
    assert duration_seconds == 23 * 3600
    assert duration_seconds == 82800.0


def test_pacific_day_fall_back_dst_duration_is_25_hours():
    """Verify first Sunday of November (Fall-Back) produces an exact 25-hour Pacific day."""
    # In 2026, US Fall Back is Sunday, November 1
    fall_date = date(2026, 11, 1)
    start_utc, end_utc = compute_pacific_calendar_day_utc_range(fall_date)

    # Starts at 00:00 PDT (UTC-7) -> 07:00 UTC
    assert start_utc == datetime(2026, 11, 1, 7, 0, 0, tzinfo=UTC)
    # Next day starts at 00:00 PST (UTC-8) -> 08:00 UTC
    assert end_utc == datetime(2026, 11, 2, 8, 0, 0, tzinfo=UTC)
    # Exactly 25 hours == 90,000 seconds
    duration_seconds = (end_utc - start_utc).total_seconds()
    assert duration_seconds == 25 * 3600
    assert duration_seconds == 90000.0


def test_relative_first_24h_window_independent_of_pacific_day():
    """Relative publication windows must be pure elapsed UTC intervals, independent of DST shifts."""
    pub_time = datetime(2026, 3, 8, 1, 30, 0, tzinfo=UTC)
    start_utc, end_utc = compute_relative_window_utc_range(pub_time, WindowType.FIRST_24H)

    assert start_utc == pub_time
    assert end_utc == pub_time + timedelta(hours=24)
    assert (end_utc - start_utc).total_seconds() == 86400.0


# ── Quota Math & Boundary Tests ────────────────────────────────────────────────


def test_unknown_quota_limit_never_fabricates_utilization():
    """If configured_daily_limit is None, utilization_percent must strictly be None."""
    bucket = AnalyticsQuotaBucket(
        id=uuid4(),
        provider="YOUTUBE",
        quota_bucket="DATA_API_V3",
        method="VIDEOS_LIST",
        configured_daily_limit=None,
        internal_budget_limit=1000,
        provider_authoritative_usage=None,
        estimated_internal_units=500,
        provider_timezone="America/Los_Angeles",
        last_reset_at_utc=datetime.now(UTC) - timedelta(hours=5),
        next_reset_at_utc=datetime.now(UTC) + timedelta(hours=19),
        evidence_source="DEFAULT_ESTIMATE",
    )

    util = QuotaService.get_utilization_percent(bucket)
    assert util is None


def test_dst_aware_quota_next_reset_at():
    """Next reset must align with Pacific midnight across both PST and PDT."""
    # Summer instant (PDT, UTC-7)
    summer_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    last_reset, next_reset = compute_quota_reset_instants(summer_now)
    # In PDT, midnight is 07:00 UTC
    assert next_reset.hour == 7
    assert next_reset.tzinfo == UTC
    assert next_reset > summer_now

    # Winter instant (PST, UTC-8)
    winter_now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    last_reset, next_reset = compute_quota_reset_instants(winter_now)
    # In PST, midnight is 08:00 UTC
    assert next_reset.hour == 8
    assert next_reset.tzinfo == UTC
    assert next_reset > winter_now


def test_provider_usage_and_internal_estimate_are_distinct():
    """Distinguish internal estimated units from provider-reported usage."""
    bucket = AnalyticsQuotaBucket(
        id=uuid4(),
        provider="YOUTUBE",
        quota_bucket="DATA_API_V3",
        method="VIDEOS_LIST",
        configured_daily_limit=10000,
        internal_budget_limit=8000,
        provider_authoritative_usage=4500,
        estimated_internal_units=4480,
        provider_timezone="America/Los_Angeles",
        last_reset_at_utc=datetime.now(UTC),
        next_reset_at_utc=datetime.now(UTC) + timedelta(hours=12),
        evidence_source="PROVIDER_API_HEADER",
    )

    assert bucket.provider_authoritative_usage == 4500
    assert bucket.estimated_internal_units == 4480
    # Authoritative usage prioritized when calculating utilization
    util = QuotaService.get_utilization_percent(bucket)
    assert util == 45.0


# ── Capabilities & Metric Quality Tests ────────────────────────────────────────


def test_explicit_zero_is_zero_confirmed():
    """Provider returning literal 0 must be tagged ZERO_CONFIRMED."""
    from omega.application.analytics.normalizer import AnalyticsNormalizer

    raw_item = {
        "id": "vid123",
        "statistics": {"viewCount": "0", "likeCount": "0", "commentCount": "0"},
    }
    facts = AnalyticsNormalizer.normalize_data_api_video_item(
        raw_item, uuid4(), uuid4(), datetime.now(UTC)
    )

    facts_dict = {f.metric_name: f for f in facts}
    assert facts_dict["views"].integer_value == 0
    assert facts_dict["views"].metric_quality == MetricQuality.ZERO_CONFIRMED
    assert facts_dict["likes"].integer_value == 0
    assert facts_dict["likes"].metric_quality == MetricQuality.ZERO_CONFIRMED


def test_unknown_omission_is_unknown_missing():
    """Omitted field with no provider reason must become UNKNOWN_MISSING."""
    from omega.application.analytics.normalizer import AnalyticsNormalizer

    raw_item = {
        "id": "vid123",
        "statistics": {},  # Fields omitted
    }
    facts = AnalyticsNormalizer.normalize_data_api_video_item(
        raw_item, uuid4(), uuid4(), datetime.now(UTC)
    )

    facts_dict = {f.metric_name: f for f in facts}
    assert facts_dict["views"].integer_value is None
    assert facts_dict["views"].metric_quality == MetricQuality.UNKNOWN_MISSING
    assert facts_dict["likes"].integer_value is None
    assert facts_dict["likes"].metric_quality == MetricQuality.UNKNOWN_MISSING


def test_unsupported_metric_dimension_is_not_available():
    """Unsupported dimension combination must return NOT_AVAILABLE before HTTP request."""
    MetricCapabilityRegistry.initialize()
    is_valid, quality = MetricCapabilityRegistry.validate_metric_request(
        provider="YOUTUBE",
        canonical_metric_name="impressions",
        api_source="ANALYTICS_API_V2",
        report_type="TIME_SERIES_REPORT",
        dimensions=["country"],  # impressions not supported by country dimension in v1 capability
        account_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
    )
    assert not is_valid
    assert quality == MetricQuality.NOT_AVAILABLE


def test_no_hardcoded_traffic_threshold_suppression():
    """Low view counts must NOT automatically be labeled SUPPRESSED."""
    from omega.application.analytics.normalizer import AnalyticsNormalizer

    raw_item = {
        "id": "vid123",
        "statistics": {"viewCount": "5", "likeCount": "1", "commentCount": "0"},
    }
    facts = AnalyticsNormalizer.normalize_data_api_video_item(
        raw_item, uuid4(), uuid4(), datetime.now(UTC)
    )

    facts_dict = {f.metric_name: f for f in facts}
    # 5 views is AVAILABLE, NOT SUPPRESSED!
    assert facts_dict["views"].integer_value == 5
    assert facts_dict["views"].metric_quality == MetricQuality.AVAILABLE
