"""Base interface and error types for analytics provider adapters."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from omega.domain.analytics import AnalyticsErrorCategory


class AnalyticsAdapterError(Exception):
    """Base exception for analytics provider adapters."""

    def __init__(
        self,
        message: str,
        category: AnalyticsErrorCategory,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        raw_error: Any = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.raw_error = raw_error


@dataclass(frozen=True)
class ProviderFetchResult:
    """Standardized response from an analytics provider call."""

    api_endpoint: str
    request_params: dict[str, Any]
    raw_payload: dict[str, Any] | list[Any]
    payload_checksum: str
    http_status: int
    retrieval_timestamp: datetime
    quota_units_consumed: int = 1


class AnalyticsProviderAdapter(abc.ABC):
    """Abstract interface for platform analytics data ingestion."""

    @abc.abstractmethod
    async def fetch_video_batch(
        self,
        access_token: str,
        provider_video_ids: list[str],
    ) -> ProviderFetchResult:
        """Fetch video metadata and public counters in batches."""
        pass

    @abc.abstractmethod
    async def fetch_video_report(
        self,
        access_token: str,
        provider_video_id: str,
        start_date: date,
        end_date: date,
        metrics: list[str],
        dimensions: list[str] | None = None,
    ) -> ProviderFetchResult:
        """Fetch private daily/window performance reports for a video."""
        pass

    @abc.abstractmethod
    async def fetch_channel_summary(
        self,
        access_token: str,
        external_account_id: str | None = None,
    ) -> ProviderFetchResult:
        """Fetch channel-level aggregate metrics."""
        pass
