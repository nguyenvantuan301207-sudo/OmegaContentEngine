"""Official YouTube Adapters for OMEGA-012 Analytics Engine.

Integrates with:
- YouTube Data API v3 (videos.list, channels.list)
- YouTube Analytics API v2 (reports.query)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from omega.application.analytics.adapters.base import (
    AnalyticsAdapterError,
    AnalyticsProviderAdapter,
    ProviderFetchResult,
)
from omega.domain.analytics import AnalyticsErrorCategory, compute_payload_checksum
from omega.domain.network import NetworkEgressPermit
from omega.logging import get_logger

logger = get_logger(service="omega-analytics-youtube-adapter")

YOUTUBE_DATA_API_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_DATA_API_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_ANALYTICS_REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"


class YouTubeAnalyticsAdapter(AnalyticsProviderAdapter):
    """Adapter executing authenticated read-only queries against official YouTube APIs."""

    def _classify_error(self, resp: httpx.Response) -> AnalyticsAdapterError:
        """Classify Google API error responses into structured categories."""
        status = resp.status_code
        retry_after: int | None = None
        if "Retry-After" in resp.headers:
            try:
                retry_after = int(resp.headers["Retry-After"])
            except ValueError:
                retry_after = 60

        try:
            body = resp.json()
            err = body.get("error", {})
            errors = err.get("errors", [])
            reason = errors[0].get("reason", "") if errors else ""
            message = err.get("message", resp.text)
        except Exception:
            reason = ""
            message = resp.text

        if status == 401:
            return AnalyticsAdapterError(
                message=f"YouTube authentication failed: {message}",
                category=AnalyticsErrorCategory.AUTH_REVOKED,
                retryable=False,
            )
        elif status == 403:
            if "insufficient" in reason.lower() or "scope" in message.lower():
                return AnalyticsAdapterError(
                    message=f"YouTube scope missing: {message}",
                    category=AnalyticsErrorCategory.AUTH_SCOPE_MISSING,
                    retryable=False,
                )
            elif reason == "quotaExceeded" or "quota" in message.lower():
                return AnalyticsAdapterError(
                    message=f"YouTube quota exceeded: {message}",
                    category=AnalyticsErrorCategory.QUOTA_EXHAUSTED,
                    retryable=True,
                    retry_after_seconds=retry_after,
                )
            elif reason == "rateLimitExceeded" or "userRateLimitExceeded" in reason:
                return AnalyticsAdapterError(
                    message=f"YouTube rate limited: {message}",
                    category=AnalyticsErrorCategory.RATE_LIMITED,
                    retryable=True,
                    retry_after_seconds=retry_after or 60,
                )
            else:
                return AnalyticsAdapterError(
                    message=f"YouTube permission forbidden: {message}",
                    category=AnalyticsErrorCategory.PERMISSION_DENIED,
                    retryable=False,
                )
        elif status == 429:
            return AnalyticsAdapterError(
                message=f"YouTube rate limited: {message}",
                category=AnalyticsErrorCategory.RATE_LIMITED,
                retryable=True,
                retry_after_seconds=retry_after or 60,
            )
        elif status == 404:
            return AnalyticsAdapterError(
                message=f"YouTube resource not found: {message}",
                category=AnalyticsErrorCategory.VIDEO_NOT_FOUND,
                retryable=False,
            )
        elif status >= 500:
            return AnalyticsAdapterError(
                message=f"YouTube transient provider error: {message}",
                category=AnalyticsErrorCategory.PROVIDER_TRANSIENT,
                retryable=True,
                retry_after_seconds=retry_after or 15,
            )
        else:
            return AnalyticsAdapterError(
                message=f"YouTube permanent provider error ({status}): {message}",
                category=AnalyticsErrorCategory.PROVIDER_PERMANENT,
                retryable=False,
            )

    async def fetch_video_batch(
        self,
        access_token: str,
        provider_video_ids: list[str],
        permit: NetworkEgressPermit | None = None,
    ) -> ProviderFetchResult:
        """Fetch video metadata and statistics from Data API v3 in batches of up to 50."""
        if not provider_video_ids:
            return ProviderFetchResult(
                api_endpoint="YOUTUBE_DATA_API_VIDEOS",
                request_params={},
                raw_payload={"items": []},
                payload_checksum=compute_payload_checksum(b'{"items": []}'),
                http_status=200,
                retrieval_timestamp=datetime.now(UTC),
                quota_units_consumed=0,
            )

        ids_chunk = provider_video_ids[:50]
        params = {
            "part": "snippet,statistics,status,contentDetails",
            "id": ",".join(ids_chunk),
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(YOUTUBE_DATA_API_VIDEOS_URL, params=params, headers=headers)
                if resp.status_code != 200:
                    raise self._classify_error(resp)

                raw_bytes = resp.content
                payload = resp.json()
                retrieval_time = datetime.now(UTC)
                return ProviderFetchResult(
                    api_endpoint="YOUTUBE_DATA_API_VIDEOS",
                    request_params={"ids": ids_chunk, "part": params["part"]},
                    raw_payload=payload,
                    payload_checksum=compute_payload_checksum(raw_bytes),
                    http_status=200,
                    retrieval_timestamp=retrieval_time,
                    quota_units_consumed=1,
                )
        except httpx.RequestError as exc:
            raise AnalyticsAdapterError(
                message=f"Network request to YouTube Data API failed: {exc}",
                category=AnalyticsErrorCategory.PROVIDER_TRANSIENT,
                retryable=True,
                retry_after_seconds=10,
            ) from exc

    async def fetch_video_report(
        self,
        access_token: str,
        provider_video_id: str,
        start_date: date,
        end_date: date,
        metrics: list[str],
        dimensions: list[str] | None = None,
        permit: NetworkEgressPermit | None = None,
    ) -> ProviderFetchResult:
        """Query official YouTube Analytics API v2 for date-bounded video report."""
        params: dict[str, Any] = {
            "ids": "channel==MINE",
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "metrics": ",".join(metrics),
            "filters": f"video=={provider_video_id}",
        }
        if dimensions:
            params["dimensions"] = ",".join(dimensions)

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    YOUTUBE_ANALYTICS_REPORTS_URL, params=params, headers=headers
                )
                if resp.status_code != 200:
                    raise self._classify_error(resp)

                raw_bytes = resp.content
                payload = resp.json()
                retrieval_time = datetime.now(UTC)
                return ProviderFetchResult(
                    api_endpoint="YOUTUBE_ANALYTICS_REPORTS",
                    request_params=params,
                    raw_payload=payload,
                    payload_checksum=compute_payload_checksum(raw_bytes),
                    http_status=200,
                    retrieval_timestamp=retrieval_time,
                    quota_units_consumed=1,
                )
        except httpx.RequestError as exc:
            raise AnalyticsAdapterError(
                message=f"Network request to YouTube Analytics API failed: {exc}",
                category=AnalyticsErrorCategory.PROVIDER_TRANSIENT,
                retryable=True,
                retry_after_seconds=10,
            ) from exc

    async def fetch_channel_summary(
        self,
        access_token: str,
        external_account_id: str | None = None,
        permit: NetworkEgressPermit | None = None,
    ) -> ProviderFetchResult:
        """Fetch channel statistics from Data API v3."""
        params: dict[str, str] = {"part": "snippet,statistics"}
        if external_account_id:
            params["id"] = external_account_id
        else:
            params["mine"] = "true"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    YOUTUBE_DATA_API_CHANNELS_URL, params=params, headers=headers
                )
                if resp.status_code != 200:
                    raise self._classify_error(resp)

                raw_bytes = resp.content
                payload = resp.json()
                retrieval_time = datetime.now(UTC)
                return ProviderFetchResult(
                    api_endpoint="YOUTUBE_DATA_API_CHANNELS",
                    request_params=params,
                    raw_payload=payload,
                    payload_checksum=compute_payload_checksum(raw_bytes),
                    http_status=200,
                    retrieval_timestamp=retrieval_time,
                    quota_units_consumed=1,
                )
        except httpx.RequestError as exc:
            raise AnalyticsAdapterError(
                message=f"Network request to YouTube Channels API failed: {exc}",
                category=AnalyticsErrorCategory.PROVIDER_TRANSIENT,
                retryable=True,
                retry_after_seconds=10,
            ) from exc
