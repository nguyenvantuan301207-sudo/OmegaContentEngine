"""Provider Analytics Abstraction for P25-B Performance Analytics.

Read-only interface defining the boundary between vendor APIs and internal analytics.
No domain service calls vendor SDK directly.
"""

from __future__ import annotations

import abc
import enum
from datetime import datetime
from typing import Any

from omega.domain.performance_analytics import (
    RawProviderMetricsPayload,
    RetentionCurve,
)


class AnalyticsErrorClassification(enum.StrEnum):
    """Classification of provider analytics errors into retryable vs terminal."""

    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    PROVIDER_5XX = "PROVIDER_5XX"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARY_LAG = "TEMPORARY_LAG"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    MISSING_PERMISSIONS = "MISSING_PERMISSIONS"
    MEDIA_DELETED = "MEDIA_DELETED"
    INVALID_MEDIA_ID = "INVALID_MEDIA_ID"


class PerformanceAnalyticsError(Exception):
    """Base exception for provider performance analytics operations."""

    def __init__(
        self,
        message: str,
        classification: AnalyticsErrorClassification,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.classification = classification
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class RetryableAnalyticsError(PerformanceAnalyticsError):
    """Transient provider error eligible for bounded backoff and retry."""

    def __init__(
        self,
        message: str,
        classification: AnalyticsErrorClassification = AnalyticsErrorClassification.PROVIDER_5XX,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message, classification, retryable=True, retry_after_seconds=retry_after_seconds)


class TerminalAnalyticsError(PerformanceAnalyticsError):
    """Terminal provider error that fails closed without automatic retry."""

    def __init__(
        self,
        message: str,
        classification: AnalyticsErrorClassification = AnalyticsErrorClassification.INVALID_CREDENTIALS,
    ) -> None:
        super().__init__(message, classification, retryable=False)


class PerformanceAnalyticsProvider(abc.ABC):
    """Narrow, read-only interface for provider performance data fetching."""

    @abc.abstractmethod
    async def fetch_media_metrics(
        self,
        external_media_id: str,
        as_of: datetime | None = None,
    ) -> RawProviderMetricsPayload:
        """Fetch raw point-in-time performance metrics for an external media object."""
        pass

    @abc.abstractmethod
    async def fetch_retention_curve(
        self,
        external_media_id: str,
    ) -> RetentionCurve | None:
        """Fetch normalized audience retention curve if supported by the provider."""
        pass

    @abc.abstractmethod
    async def fetch_channel_context(
        self,
        channel_account_id: str,
    ) -> dict[str, Any] | None:
        """Fetch isolated channel-level aggregate context (never to be mixed with single-video metrics)."""
        pass

    @abc.abstractmethod
    async def check_media_availability(
        self,
        external_media_id: str,
    ) -> str:
        """Check provider media availability status: AVAILABLE, DELAYED, PRIVATE, DELETED, or UNAVAILABLE."""
        pass
