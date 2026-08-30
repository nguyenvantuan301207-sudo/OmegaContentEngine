"""Metric capability registry for OMEGA-012 Analytics Engine.

Enforces provider metric/dimension/report compatibility and scope gating BEFORE external requests.
"""

from __future__ import annotations

from omega.domain.analytics import AvailabilityClass, MetricQuality, ProviderMetricCapability

# Authoritative versioned registry of provider metric capabilities
DEFAULT_YOUTUBE_CAPABILITIES: list[ProviderMetricCapability] = [
    # Video Metrics (YouTube Data API v3)
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="views",
        provider_metric_name="viewCount",
        api_source="DATA_API_V3",
        supported_report_types=["VIDEOS_LIST"],
        compatible_dimensions=[],
        required_scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="COUNT",
        aggregation_semantics="POINT_IN_TIME_CUMULATIVE",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="likes",
        provider_metric_name="likeCount",
        api_source="DATA_API_V3",
        supported_report_types=["VIDEOS_LIST"],
        compatible_dimensions=[],
        required_scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="COUNT",
        aggregation_semantics="POINT_IN_TIME_CUMULATIVE",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="comments",
        provider_metric_name="commentCount",
        api_source="DATA_API_V3",
        supported_report_types=["VIDEOS_LIST"],
        compatible_dimensions=[],
        required_scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="COUNT",
        aggregation_semantics="POINT_IN_TIME_CUMULATIVE",
        capability_version=1,
    ),
    # Video Metrics (YouTube Analytics API v2)
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="watch_time_seconds",
        provider_metric_name="estimatedMinutesWatched",
        api_source="ANALYTICS_API_V2",
        supported_report_types=["BASIC_VIDEO_REPORT", "TIME_SERIES_REPORT"],
        compatible_dimensions=["day", "video"],
        required_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="SECONDS",
        aggregation_semantics="SUM",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="average_view_duration_seconds",
        provider_metric_name="averageViewDuration",
        api_source="ANALYTICS_API_V2",
        supported_report_types=["BASIC_VIDEO_REPORT", "TIME_SERIES_REPORT"],
        compatible_dimensions=["day", "video"],
        required_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="SECONDS",
        aggregation_semantics="AVG",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="average_percentage_viewed",
        provider_metric_name="averageViewPercentage",
        api_source="ANALYTICS_API_V2",
        supported_report_types=["BASIC_VIDEO_REPORT", "TIME_SERIES_REPORT"],
        compatible_dimensions=["day", "video"],
        required_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="PERCENT",
        aggregation_semantics="AVG",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="subscribers_gained",
        provider_metric_name="subscribersGained",
        api_source="ANALYTICS_API_V2",
        supported_report_types=["BASIC_VIDEO_REPORT", "TIME_SERIES_REPORT"],
        compatible_dimensions=["day", "video"],
        required_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="COUNT",
        aggregation_semantics="SUM",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="subscribers_lost",
        provider_metric_name="subscribersLost",
        api_source="ANALYTICS_API_V2",
        supported_report_types=["BASIC_VIDEO_REPORT", "TIME_SERIES_REPORT"],
        compatible_dimensions=["day", "video"],
        required_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="COUNT",
        aggregation_semantics="SUM",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="shares",
        provider_metric_name="shares",
        api_source="ANALYTICS_API_V2",
        supported_report_types=["BASIC_VIDEO_REPORT", "TIME_SERIES_REPORT"],
        compatible_dimensions=["day", "video"],
        required_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        availability_class=AvailabilityClass.UNIVERSAL,
        unit="COUNT",
        aggregation_semantics="SUM",
        capability_version=1,
    ),
    # Dimension-restricted metrics
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="impressions",
        provider_metric_name="impressions",
        api_source="ANALYTICS_API_V2",
        supported_report_types=["TIME_SERIES_REPORT", "TRAFFIC_SOURCE_REPORT"],
        compatible_dimensions=["day", "insightTrafficSourceType"],
        required_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        availability_class=AvailabilityClass.DIMENSION_RESTRICTED,
        unit="COUNT",
        aggregation_semantics="SUM",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="impression_ctr_percent",
        provider_metric_name="impressionClickThroughRate",
        api_source="ANALYTICS_API_V2",
        supported_report_types=["TIME_SERIES_REPORT", "TRAFFIC_SOURCE_REPORT"],
        compatible_dimensions=["day", "insightTrafficSourceType"],
        required_scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        availability_class=AvailabilityClass.DIMENSION_RESTRICTED,
        unit="PERCENT",
        aggregation_semantics="AVG",
        capability_version=1,
    ),
    # Channel Aggregate Metrics (YouTube Data API v3)
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="channel_total_views",
        provider_metric_name="viewCount",
        api_source="DATA_API_V3",
        supported_report_types=["CHANNELS_LIST"],
        compatible_dimensions=[],
        required_scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        availability_class=AvailabilityClass.CHANNEL_ONLY,
        unit="COUNT",
        aggregation_semantics="POINT_IN_TIME_CUMULATIVE",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="channel_subscribers",
        provider_metric_name="subscriberCount",
        api_source="DATA_API_V3",
        supported_report_types=["CHANNELS_LIST"],
        compatible_dimensions=[],
        required_scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        availability_class=AvailabilityClass.CHANNEL_ONLY,
        unit="COUNT",
        aggregation_semantics="POINT_IN_TIME_CUMULATIVE",
        capability_version=1,
    ),
    ProviderMetricCapability(
        provider="YOUTUBE",
        canonical_metric_name="channel_video_count",
        provider_metric_name="videoCount",
        api_source="DATA_API_V3",
        supported_report_types=["CHANNELS_LIST"],
        compatible_dimensions=[],
        required_scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        availability_class=AvailabilityClass.CHANNEL_ONLY,
        unit="COUNT",
        aggregation_semantics="POINT_IN_TIME_CUMULATIVE",
        capability_version=1,
    ),
]


class MetricCapabilityRegistry:
    """In-memory and cached lookup for provider metric capabilities."""

    _registry: dict[tuple[str, str, str], ProviderMetricCapability] = {}

    @classmethod
    def initialize(cls, capabilities: list[ProviderMetricCapability] | None = None) -> None:
        """Initialize the capability registry."""
        items = capabilities or DEFAULT_YOUTUBE_CAPABILITIES
        cls._registry = {
            (cap.provider, cap.canonical_metric_name, cap.api_source): cap for cap in items
        }

    @classmethod
    def get_capability(
        cls, provider: str, canonical_metric_name: str, api_source: str
    ) -> ProviderMetricCapability | None:
        """Lookup capability specification."""
        if not cls._registry:
            cls.initialize()
        return cls._registry.get((provider, canonical_metric_name, api_source))

    @classmethod
    def validate_metric_request(
        cls,
        provider: str,
        canonical_metric_name: str,
        api_source: str,
        report_type: str,
        dimensions: list[str],
        account_scopes: list[str],
    ) -> tuple[bool, MetricQuality]:
        """Validate if a requested metric query is supported before calling external API.

        Returns:
            (is_supported, quality_reason)
        """
        cap = cls.get_capability(provider, canonical_metric_name, api_source)
        if cap is None:
            return False, MetricQuality.NOT_AVAILABLE

        # Check required scopes
        for req_scope in cap.required_scopes:
            if req_scope not in account_scopes:
                return False, MetricQuality.PERMISSION_DENIED

        # Check report type compatibility
        if report_type not in cap.supported_report_types:
            return False, MetricQuality.NOT_AVAILABLE

        # Check dimensions compatibility
        if cap.availability_class == AvailabilityClass.DIMENSION_RESTRICTED:
            for dim in dimensions:
                if dim not in cap.compatible_dimensions:
                    return False, MetricQuality.NOT_AVAILABLE

        return True, MetricQuality.AVAILABLE
