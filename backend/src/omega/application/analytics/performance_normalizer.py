"""Canonical normalization and anomaly detection for P25-B Performance Analytics.

Translates provider truth into standard units and canonical semantics.
Detects counter resets and anomalies without corrupting cumulative data.
"""

from __future__ import annotations

from typing import Any

from omega.domain.analytics import MetricQuality
from omega.domain.performance_analytics import (
    CanonicalMetrics,
    CounterAnomalyRecord,
    RawProviderMetricsPayload,
)
from omega.logging import get_logger

logger = get_logger(service="omega-performance-normalizer")


class PerformanceNormalizer:
    """Normalizes raw provider payload metrics into authoritative canonical units."""

    @classmethod
    def normalize(cls, raw_payload: RawProviderMetricsPayload) -> CanonicalMetrics:
        """Transform raw provider dict into strongly-typed CanonicalMetrics."""
        data = raw_payload.raw_data
        qualities: dict[str, str] = {}

        # 1. views (monotonic counter)
        views = cls._extract_int(data, ["views", "viewCount", "view_count"])
        if views is not None:
            qualities["views"] = MetricQuality.ZERO_CONFIRMED.value if views == 0 else MetricQuality.AVAILABLE.value
        else:
            qualities["views"] = MetricQuality.UNKNOWN_MISSING.value

        # 2. impressions (monotonic counter)
        impressions = cls._extract_int(data, ["impressions", "impressionCount", "impression_count"])
        if impressions is not None:
            qualities["impressions"] = MetricQuality.ZERO_CONFIRMED.value if impressions == 0 else MetricQuality.AVAILABLE.value
        else:
            qualities["impressions"] = MetricQuality.UNKNOWN_MISSING.value

        # 3. watch_time_seconds (canonical unit = SECONDS)
        # Providers may report estimatedMinutesWatched or watch_time_seconds
        watch_time = cls._extract_float(data, ["watch_time_seconds", "watchTimeSeconds", "watch_time"])
        if watch_time is None:
            minutes = cls._extract_float(data, ["estimatedMinutesWatched", "watch_time_minutes"])
            if minutes is not None:
                watch_time = round(minutes * 60.0, 2)

        if watch_time is not None:
            qualities["watch_time_seconds"] = MetricQuality.ZERO_CONFIRMED.value if watch_time == 0.0 else MetricQuality.AVAILABLE.value
        else:
            qualities["watch_time_seconds"] = MetricQuality.UNKNOWN_MISSING.value

        # 4. average_view_duration_seconds (seconds)
        avg_dur = cls._extract_float(data, ["average_view_duration_seconds", "averageViewDuration", "avg_view_duration"])
        if avg_dur is not None:
            qualities["average_view_duration_seconds"] = MetricQuality.AVAILABLE.value
        else:
            qualities["average_view_duration_seconds"] = MetricQuality.UNKNOWN_MISSING.value

        # 5. average_percentage_viewed (canonical ratio in [0.0, 1.0])
        avg_pct = cls._extract_float(data, ["average_percentage_viewed", "averageViewPercentage", "avg_percentage_viewed"])
        if avg_pct is not None:
            if avg_pct > 1.0:
                avg_pct = round(avg_pct / 100.0, 4)
            avg_pct = max(0.0, min(1.0, avg_pct))
            qualities["average_percentage_viewed"] = MetricQuality.AVAILABLE.value
        else:
            qualities["average_percentage_viewed"] = MetricQuality.UNKNOWN_MISSING.value

        # 6. impressions_ctr (canonical ratio in [0.0, 1.0])
        ctr = cls._extract_float(data, ["impressions_ctr", "impressionClickThroughRate", "ctr", "impression_ctr"])
        if ctr is not None:
            if ctr > 1.0:
                ctr = round(ctr / 100.0, 4)
            ctr = max(0.0, min(1.0, ctr))
            qualities["impressions_ctr"] = MetricQuality.AVAILABLE.value
        else:
            qualities["impressions_ctr"] = MetricQuality.UNKNOWN_MISSING.value

        # 7. likes, comments, shares
        likes = cls._extract_int(data, ["likes", "likeCount", "like_count"])
        qualities["likes"] = MetricQuality.AVAILABLE.value if likes is not None else MetricQuality.UNKNOWN_MISSING.value

        comments = cls._extract_int(data, ["comments", "commentCount", "comment_count"])
        qualities["comments"] = MetricQuality.AVAILABLE.value if comments is not None else MetricQuality.UNKNOWN_MISSING.value

        shares = cls._extract_int(data, ["shares", "shareCount", "share_count"])
        qualities["shares"] = MetricQuality.AVAILABLE.value if shares is not None else MetricQuality.UNKNOWN_MISSING.value

        # 8. subscribers_gained / lost
        sub_gained = cls._extract_int(data, ["subscribers_gained", "subscribersGained", "subs_gained"])
        sub_lost = cls._extract_int(data, ["subscribers_lost", "subscribersLost", "subs_lost"])
        qualities["subscribers_gained"] = MetricQuality.AVAILABLE.value if sub_gained is not None else MetricQuality.UNKNOWN_MISSING.value
        qualities["subscribers_lost"] = MetricQuality.AVAILABLE.value if sub_lost is not None else MetricQuality.UNKNOWN_MISSING.value

        # 9. unique_viewers
        unique_viewers = cls._extract_int(data, ["unique_viewers", "uniqueViewers"])
        qualities["unique_viewers"] = MetricQuality.AVAILABLE.value if unique_viewers is not None else MetricQuality.UNKNOWN_MISSING.value

        return CanonicalMetrics(
            views=views,
            impressions=impressions,
            watch_time_seconds=watch_time,
            average_view_duration_seconds=avg_dur,
            average_percentage_viewed=avg_pct,
            impressions_ctr=ctr,
            likes=likes,
            comments=comments,
            shares=shares,
            subscribers_gained=sub_gained,
            subscribers_lost=sub_lost,
            unique_viewers=unique_viewers,
            metric_qualities=qualities,
        )

    @classmethod
    def detect_counter_anomalies(
        cls,
        current: CanonicalMetrics,
        previous: CanonicalMetrics | None,
    ) -> list[CounterAnomalyRecord]:
        """Detect unexpected decreases in cumulative monotonic counters."""
        if previous is None:
            return []

        anomalies: list[CounterAnomalyRecord] = []
        monotonic_fields = [
            ("views", current.views, previous.views),
            ("watch_time_seconds", current.watch_time_seconds, previous.watch_time_seconds),
            ("likes", current.likes, previous.likes),
            ("comments", current.comments, previous.comments),
            ("shares", current.shares, previous.shares),
            ("subscribers_gained", current.subscribers_gained, previous.subscribers_gained),
        ]

        for name, curr_val, prev_val in monotonic_fields:
            if curr_val is not None and prev_val is not None and curr_val < prev_val:
                diff = prev_val - curr_val
                msg = (
                    f"Suspicious decrease detected in monotonic counter '{name}': "
                    f"previous={prev_val}, current={curr_val} (decrease of {diff}). "
                    f"Flagged for reconciliation."
                )
                logger.warning("counter_anomaly_detected", metric=name, previous=prev_val, current=curr_val)
                anomalies.append(
                    CounterAnomalyRecord(
                        metric_name=name,
                        previous_value=prev_val,
                        current_value=curr_val,
                        difference=diff,
                        status="ANOMALY_DETECTED",
                        message=msg,
                    )
                )

        return anomalies

    @staticmethod
    def _extract_int(data: dict[str, Any], keys: list[str]) -> int | None:
        for k in keys:
            if k in data and data[k] is not None:
                try:
                    return int(data[k])
                except (ValueError, TypeError):
                    continue
        return None

    @staticmethod
    def _extract_float(data: dict[str, Any], keys: list[str]) -> float | None:
        for k in keys:
            if k in data and data[k] is not None:
                try:
                    return float(data[k])
                except (ValueError, TypeError):
                    continue
        return None
