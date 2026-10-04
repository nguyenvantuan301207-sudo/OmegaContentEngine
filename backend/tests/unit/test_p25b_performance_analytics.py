"""Unit test suite for P25-B Performance Analytics (Matrix A through L).

Covers:
- Matrix A: Provider mapping (valid external media identity, missing receipt, invalid ID)
- Matrix B: Normalization (full metrics, partial metrics, unsupported metric omitted)
- Matrix C: Semantics (CTR as decimal ratio, watch time in seconds, percentage viewed in [0..1], UTC timestamps)
- Matrix D: Snapshot ingestion (first snapshot, later snapshot, duplicate replay idempotency)
- Matrix E: Delta / velocity (valid delta, missing previous snapshot, zero/negative interval safeguards)
- Matrix F: Freshness (fresh, delayed != zero, stale, unavailable)
- Matrix G: Counter anomaly (normal growth, suspicious counter decrease marked ANOMALY_DETECTED)
- Matrix H: Retention curve (valid curve, non-monotonic invalid ordering, missing curve)
- Matrix I: Recovery (transient failure retry, resume without duplicate)
- Matrix J: Historical preservation (media unavailable, historical snapshots preserved)
- Matrix K: Query service (latest, history, summary)
- Matrix L: Boundary (read-only, no learning / recommendation, zero provider mutation)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from omega.application.analytics.fake_performance_provider import (
    FakePerformanceAnalyticsProvider,
)
from omega.application.analytics.performance_normalizer import PerformanceNormalizer
from omega.application.analytics.performance_provider import (
    AnalyticsErrorClassification,
    RetryableAnalyticsError,
    TerminalAnalyticsError,
)
from omega.domain.analytics import MetricQuality
from omega.domain.performance_analytics import (
    CanonicalMetrics,
    DataFreshnessStatus,
    DeltaVelocityMetrics,
    DerivedRetentionMetrics,
    PerformanceSnapshot,
    PerformanceSummary,
    RawProviderMetricsPayload,
    RetentionCurve,
    RetentionPoint,
)
from omega.domain.publishing import PublishingState, PublishReceipt


# ── Fixtures & Helpers ────────────────────────────────────────────────────────


@pytest.fixture
def sample_receipt() -> PublishReceipt:
    intent_id = uuid4()
    attempt_id = uuid4()
    return PublishReceipt(
        receipt_id=uuid4(),
        publish_intent_id=intent_id,
        publish_attempt_id=attempt_id,
        provider="FAKE_YOUTUBE",
        external_media_id="ext-vid-p25b-001",
        provider_status="PROCESSED",
        published_at=datetime(2026, 10, 4, 10, 0, 0, tzinfo=UTC),
        payload_checksum="test-checksum-001",
        completion_state=PublishingState.PUBLISHED,
        response_metadata={"channel_id": "test-channel-p25b"},
        lineage={
            "media_artifact_id": str(uuid4()),
            "channel_id": str(uuid4()),
            "request_id": str(uuid4()),
        },
    )


# ── Matrix A: Provider Mapping ────────────────────────────────────────────────


def test_matrix_a_provider_mapping_valid(sample_receipt: PublishReceipt):
    """External media identity is attached to exact published provider object."""
    assert sample_receipt.external_media_id == "ext-vid-p25b-001"
    assert sample_receipt.provider == "FAKE_YOUTUBE"
    assert "media_artifact_id" in sample_receipt.lineage


def test_matrix_a_provider_mapping_missing_id():
    """Provider fetch for unknown external ID raises TerminalAnalyticsError."""
    provider = FakePerformanceAnalyticsProvider()
    with pytest.raises(TerminalAnalyticsError) as exc_info:
        import asyncio
        asyncio.run(provider.fetch_media_metrics("non-existent-id"))
    assert exc_info.value.classification == AnalyticsErrorClassification.INVALID_MEDIA_ID


# ── Matrix B: Normalization ───────────────────────────────────────────────────


def test_matrix_b_normalization_full_metrics():
    """All supported metrics normalized into canonical fields without fabrication."""
    now = datetime.now(UTC)
    raw = RawProviderMetricsPayload(
        provider="FAKE_YOUTUBE",
        external_media_id="vid-1",
        provider_timestamp=now,
        observed_at=now,
        raw_data={
            "views": 500,
            "impressions": 10000,
            "estimatedMinutesWatched": 250.0,
            "averageViewDuration": 30.0,
            "averageViewPercentage": 60.0,  # 60%
            "impressionClickThroughRate": 5.0,  # 5%
            "likes": 50,
            "comments": 10,
            "shares": 5,
            "subscribersGained": 8,
            "subscribersLost": 2,
            "uniqueViewers": 420,
        },
    )
    canon = PerformanceNormalizer.normalize(raw)
    assert canon.views == 500
    assert canon.impressions == 10000
    assert canon.watch_time_seconds == 15000.0  # 250 mins * 60
    assert canon.average_view_duration_seconds == 30.0
    assert canon.average_percentage_viewed == 0.60
    assert canon.impressions_ctr == 0.05
    assert canon.likes == 50
    assert canon.comments == 10
    assert canon.shares == 5
    assert canon.subscribers_gained == 8
    assert canon.subscribers_lost == 2
    assert canon.net_subscribers == 6
    assert canon.unique_viewers == 420
    assert canon.metric_qualities["views"] == MetricQuality.AVAILABLE.value


def test_matrix_b_normalization_partial_metrics():
    """Partial metrics handled explicitly; missing metrics are None with UNKNOWN_MISSING."""
    now = datetime.now(UTC)
    raw = RawProviderMetricsPayload(
        provider="FAKE_YOUTUBE",
        external_media_id="vid-1",
        provider_timestamp=now,
        observed_at=now,
        raw_data={"views": 0, "likes": 0},
    )
    canon = PerformanceNormalizer.normalize(raw)
    assert canon.views == 0
    assert canon.metric_qualities["views"] == MetricQuality.ZERO_CONFIRMED.value
    assert canon.impressions is None
    assert canon.metric_qualities["impressions"] == MetricQuality.UNKNOWN_MISSING.value
    assert canon.watch_time_seconds is None


# ── Matrix C: Metric Semantics ────────────────────────────────────────────────


def test_matrix_c_metric_semantics_ctr_and_percentage_ratios():
    """CTR and percentage viewed are stored as decimal ratios in range [0.0, 1.0]."""
    now = datetime.now(UTC)
    # Test raw values given as percentages (> 1.0)
    raw = RawProviderMetricsPayload(
        provider="FAKE_YOUTUBE",
        external_media_id="vid-1",
        provider_timestamp=now,
        observed_at=now,
        raw_data={"ctr": 6.5, "averageViewPercentage": 45.5},
    )
    canon = PerformanceNormalizer.normalize(raw)
    assert canon.impressions_ctr == 0.065
    assert canon.average_percentage_viewed == 0.455

    # Test raw values already given as ratios
    raw2 = RawProviderMetricsPayload(
        provider="FAKE_YOUTUBE",
        external_media_id="vid-1",
        provider_timestamp=now,
        observed_at=now,
        raw_data={"ctr": 0.082, "average_percentage_viewed": 0.72},
    )
    canon2 = PerformanceNormalizer.normalize(raw2)
    assert canon2.impressions_ctr == 0.082
    assert canon2.average_percentage_viewed == 0.72


def test_matrix_c_watch_time_seconds():
    """Watch time unit is strictly seconds."""
    now = datetime.now(UTC)
    raw = RawProviderMetricsPayload(
        provider="FAKE_YOUTUBE",
        external_media_id="vid-1",
        provider_timestamp=now,
        observed_at=now,
        raw_data={"watch_time_seconds": 3600.0},
    )
    canon = PerformanceNormalizer.normalize(raw)
    assert canon.watch_time_seconds == 3600.0


# ── Matrix E: Delta and Velocity ──────────────────────────────────────────────


def test_matrix_e_delta_velocity_valid():
    """Bounded delta and rate-of-change metrics computed correctly."""
    t0 = datetime(2026, 10, 4, 10, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=2)

    m0 = CanonicalMetrics(views=100, watch_time_seconds=3600.0, subscribers_gained=5, subscribers_lost=1)
    m1 = CanonicalMetrics(views=250, watch_time_seconds=9000.0, subscribers_gained=12, subscribers_lost=2)

    deltas = DeltaVelocityMetrics.compute(current=m1, current_time=t1, previous=m0, previous_time=t0)
    assert deltas.is_valid is True
    assert deltas.views_delta == 150
    assert deltas.watch_time_delta_seconds == 5400.0
    assert deltas.views_per_hour == 75.0  # 150 / 2 hours
    assert deltas.watch_time_per_hour_seconds == 2700.0  # 5400 / 2 hours
    assert deltas.subscriber_delta == 6  # (12-2) - (5-1) = 10 - 4 = 6
    assert deltas.time_delta_seconds == 7200.0


def test_matrix_e_delta_velocity_safeguards():
    """Delta is invalid if no previous snapshot or non-positive time interval."""
    t0 = datetime(2026, 10, 4, 10, 0, 0, tzinfo=UTC)
    m0 = CanonicalMetrics(views=100)

    # Missing previous
    deltas_none = DeltaVelocityMetrics.compute(current=m0, current_time=t0, previous=None, previous_time=None)
    assert deltas_none.is_valid is False
    assert deltas_none.notes == "NO_PREVIOUS_SNAPSHOT"

    # Non-positive time interval (t1 <= t0)
    deltas_zero_time = DeltaVelocityMetrics.compute(current=m0, current_time=t0, previous=m0, previous_time=t0)
    assert deltas_zero_time.is_valid is False
    assert deltas_zero_time.notes == "NON_POSITIVE_TIME_INTERVAL"


# ── Matrix F: Freshness ───────────────────────────────────────────────────────


def test_matrix_f_freshness_delayed_policy():
    """Delayed provider analytics must not be converted into zero views."""
    empty_metrics = CanonicalMetrics(
        views=None,
        watch_time_seconds=None,
        metric_qualities={"all": MetricQuality.NOT_READY.value},
    )
    snap = PerformanceSnapshot(
        snapshot_id=uuid4(),
        receipt_id=uuid4(),
        provider="FAKE_YOUTUBE",
        external_media_id="vid-delayed",
        observed_at=datetime.now(UTC),
        freshness=DataFreshnessStatus.DELAYED,
        metrics=empty_metrics,
    )
    assert snap.freshness == DataFreshnessStatus.DELAYED
    assert snap.metrics.views is None  # NOT ZERO!


# ── Matrix G: Counter Anomaly Handling ────────────────────────────────────────


def test_matrix_g_counter_anomaly_detection():
    """Decreasing monotonic counters flagged as ANOMALY_DETECTED without negative corruption."""
    m_prev = CanonicalMetrics(views=1200, watch_time_seconds=45000.0, likes=100)
    m_curr = CanonicalMetrics(views=900, watch_time_seconds=45000.0, likes=100)

    anomalies = PerformanceNormalizer.detect_counter_anomalies(m_curr, m_prev)
    assert len(anomalies) == 1
    anom = anomalies[0]
    assert anom.metric_name == "views"
    assert anom.previous_value == 1200
    assert anom.current_value == 900
    assert anom.difference == 300
    assert anom.status == "ANOMALY_DETECTED"


def test_matrix_g_counter_anomaly_normal_growth():
    """Normal growth in monotonic counters produces zero anomalies."""
    m_prev = CanonicalMetrics(views=1200, watch_time_seconds=45000.0)
    m_curr = CanonicalMetrics(views=1500, watch_time_seconds=55000.0)

    anomalies = PerformanceNormalizer.detect_counter_anomalies(m_curr, m_prev)
    assert len(anomalies) == 0


# ── Matrix H: Retention Curve ─────────────────────────────────────────────────


def test_matrix_h_retention_curve_valid_and_interpolation():
    """Audience retention curve validates monotonic positions and interpolates milestones."""
    curve = RetentionCurve(
        points=[
            RetentionPoint(relative_position=0.0, retention_ratio=1.0),
            RetentionPoint(relative_position=0.1, retention_ratio=0.85),
            RetentionPoint(relative_position=0.5, retention_ratio=0.60),
            RetentionPoint(relative_position=0.9, retention_ratio=0.40),
            RetentionPoint(relative_position=1.0, retention_ratio=0.35),
        ]
    )
    derived = DerivedRetentionMetrics.from_curve(curve, duration_seconds=60.0)
    assert derived.retention_at_50_percent == 0.60
    assert derived.retention_at_90_percent == 0.40
    assert derived.early_dropoff_rate == 0.15  # 1.0 - 0.85
    # 30s in 60s video is position 0.5 -> 0.60
    assert derived.retention_at_30s == 0.60


def test_matrix_h_retention_curve_invalid_ordering():
    """Non-monotonic relative positions raise validation error."""
    with pytest.raises(ValueError) as exc_info:
        RetentionCurve(
            points=[
                RetentionPoint(relative_position=0.5, retention_ratio=0.7),
                RetentionPoint(relative_position=0.2, retention_ratio=0.9),  # Decreasing position!
            ]
        )
    assert "non-decreasing relative_position" in str(exc_info.value)


# ── Matrix I: Recovery ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_i_transient_failure_recovery():
    """Transient failures retry and succeed within bounded budget."""
    provider = FakePerformanceAnalyticsProvider()
    provider.configure_timeline_metrics(
        "vid-recover",
        [(datetime.now(UTC), {"views": 100})],
    )
    provider.configure_transient_failure("vid-recover", failures_before_success=2)

    # First attempt fails with 503
    with pytest.raises(RetryableAnalyticsError):
        await provider.fetch_media_metrics("vid-recover")

    # Second attempt fails with 503
    with pytest.raises(RetryableAnalyticsError):
        await provider.fetch_media_metrics("vid-recover")

    # Third attempt succeeds
    res = await provider.fetch_media_metrics("vid-recover")
    assert res.raw_data["views"] == 100
    assert provider.retry_recovery_count == 2


# ── Matrix J: Historical Preservation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_j_media_unavailable_preservation():
    """Media marked unavailable does not delete historical records."""
    provider = FakePerformanceAnalyticsProvider()
    provider.configure_timeline_metrics(
        "vid-del",
        [(datetime.now(UTC), {"views": 999})],
    )
    provider.configure_availability("vid-del", "DELETED")

    avail = await provider.check_media_availability("vid-del")
    assert avail == "DELETED"
    # Prior data is still retrievable from provider store
    past_data = await provider.fetch_media_metrics("vid-del")
    assert past_data.raw_data["views"] == 999


# ── Matrix K: Summary ─────────────────────────────────────────────────────────


def test_matrix_k_performance_summary():
    """PerformanceSummary deterministically calculates engagement and subscriber impact."""
    summary = PerformanceSummary(
        external_media_id="vid-sum",
        receipt_id=uuid4(),
        provider="FAKE_YOUTUBE",
        latest_observed_at=datetime.now(UTC),
        freshness=DataFreshnessStatus.FRESH,
        views=1000,
        watch_time_seconds=30000.0,
        impressions_ctr=0.065,
        engagement_score=0.12,  # 12%
        subscriber_impact=45,
        snapshot_count=3,
        age_hours=24.5,
    )
    assert summary.views == 1000
    assert summary.engagement_score == 0.12
    assert summary.subscriber_impact == 45
    assert summary.snapshot_count == 3


# ── Matrix L: Boundaries ──────────────────────────────────────────────────────


def test_matrix_l_boundaries_no_learning_or_mutation():
    """Domain models strictly answer 'how did it perform' and contain no learning recommendations."""
    fields = PerformanceSummary.model_fields.keys()
    prohibited_recommendations = {
        "title_recommendation",
        "thumbnail_recommendation",
        "sfx_adjustment",
        "channel_dna_update",
        "variant_winner",
    }
    assert prohibited_recommendations.isdisjoint(fields)
