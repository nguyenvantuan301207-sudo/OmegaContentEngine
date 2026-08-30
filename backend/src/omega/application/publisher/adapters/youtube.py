"""Official YouTube Data API v3 Adapter for OMEGA-011 Publisher.

Implements the official Google YouTube Data API v3 Resumable Upload protocol,
OAuth 2.0 credential validation & refresh, chunked streaming, and authoritative
reconciliation.
"""

from __future__ import annotations

import re
import zoneinfo
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from omega.application.publisher.adapters.base import (
    AdapterRegistry,
    BasePlatformAdapter,
    ChunkUploadResult,
    ClassifiedError,
    CredentialValidationResult,
    ReconciliationResult,
    RefreshedTokenData,
    UploadProgressResult,
    UploadSessionInitResult,
)
from omega.domain.network import NetworkEgressPermit
from omega.domain.publisher import Platform, PrivacyStatus, PublisherErrorCategory
from omega.logging import get_logger

logger = get_logger(service="omega-publisher-youtube-adapter")


class YouTubeDataApiAdapter(BasePlatformAdapter):
    """Platform adapter for YouTube Data API v3 and Google Identity OAuth."""

    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    async def validate_credentials(
        self,
        access_token: str,
        permit: NetworkEgressPermit,
    ) -> CredentialValidationResult:
        """Validate account and fetch channel profile from YouTube API."""
        url = "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if items:
                        channel_item = items[0]
                        ext_id = channel_item.get("id")
                        snippet = channel_item.get("snippet", {})
                        title = snippet.get("title", "YouTube Channel")
                        return CredentialValidationResult(
                            is_valid=True,
                            account_display_name=title,
                            external_account_id=ext_id,
                        )
                    return CredentialValidationResult(
                        is_valid=False,
                        error_category=PublisherErrorCategory.PERMISSION_DENIED,
                        error_message="No YouTube channel found for authenticated Google account.",
                    )
                if resp.status_code == 401:
                    return CredentialValidationResult(
                        is_valid=False,
                        error_category=PublisherErrorCategory.AUTH_EXPIRED,
                        error_message="Access token expired.",
                    )
                return CredentialValidationResult(
                    is_valid=False,
                    error_category=PublisherErrorCategory.PERMISSION_DENIED,
                    error_message=f"YouTube API validation failed with HTTP {resp.status_code}: {resp.text}",
                )
        except Exception as exc:
            return CredentialValidationResult(
                is_valid=False,
                error_category=PublisherErrorCategory.NETWORK_TRANSIENT,
                error_message=f"Network error validating YouTube credentials: {exc}",
            )

    async def refresh_access_token(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        permit: NetworkEgressPermit,
    ) -> RefreshedTokenData:
        """Exchange refresh token for a fresh short-lived access token."""
        url = "https://oauth2.googleapis.com/token"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data=data)
            if resp.status_code == 200:
                payload = resp.json()
                access_token = payload["access_token"]
                expires_in = payload.get("expires_in", 3600)
                new_refresh_token = payload.get("refresh_token")
                return RefreshedTokenData(
                    access_token=access_token,
                    expires_in_seconds=expires_in,
                    new_refresh_token=new_refresh_token,
                )
            if resp.status_code == 400:
                body = (
                    resp.json()
                    if resp.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
                if body.get("error") == "invalid_grant":
                    raise ValueError("OAuth refresh token is invalid or revoked (invalid_grant).")
            raise RuntimeError(f"Token refresh failed with HTTP {resp.status_code}: {resp.text}")

    async def initialize_resumable_upload(
        self,
        title: str,
        description: str,
        tags: list[str],
        category_id: str,
        requested_privacy: PrivacyStatus,
        made_for_kids: bool,
        total_bytes: int,
        access_token: str,
        permit: NetworkEgressPermit,
        custom_options: dict[str, Any] | None = None,
    ) -> UploadSessionInitResult:
        """Initiate official resumable upload session with video metadata resource."""
        url = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
        metadata_body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": requested_privacy.value.lower(),
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(total_bytes),
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=metadata_body, headers=headers)
            if resp.status_code == 200:
                session_uri = resp.headers.get("Location")
                if not session_uri:
                    raise RuntimeError(
                        "YouTube API 200 OK did not return Location header for resumable session."
                    )
                # Resumable session URIs are valid for ~24 hours
                expires_at = datetime.now(UTC) + timedelta(hours=24)
                return UploadSessionInitResult(session_uri=session_uri, expires_at=expires_at)

            # Raise error for classification
            resp.raise_for_status()
            raise RuntimeError(f"Unexpected response initializing upload: HTTP {resp.status_code}")

    async def upload_chunk(
        self,
        session_uri: str,
        chunk_data: bytes,
        start_byte: int,
        total_bytes: int,
        permit: NetworkEgressPermit,
    ) -> ChunkUploadResult:
        """Transmit an individual chunk to provider upload session."""
        chunk_len = len(chunk_data)
        end_byte = start_byte + chunk_len - 1
        headers = {
            "Content-Length": str(chunk_len),
            "Content-Range": f"bytes {start_byte}-{end_byte}/{total_bytes}",
            "Content-Type": "video/mp4",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(session_uri, content=chunk_data, headers=headers)

            if resp.status_code == 308:
                # Intermediate chunk accepted
                range_hdr = resp.headers.get("Range", "")
                next_offset = end_byte + 1
                if range_hdr:
                    match = re.search(r"bytes=0-(\d+)", range_hdr)
                    if match:
                        next_offset = int(match.group(1)) + 1
                return ChunkUploadResult(
                    is_complete=False,
                    next_byte_offset=next_offset,
                )

            if resp.status_code in (200, 201):
                # Upload complete!
                body = resp.json()
                video_id = body.get("id")
                status_obj = body.get("status", {})
                eff_privacy_str = status_obj.get("privacyStatus", "private").upper()
                eff_privacy = (
                    PrivacyStatus(eff_privacy_str)
                    if eff_privacy_str in PrivacyStatus._value2member_map_
                    else PrivacyStatus.PRIVATE
                )
                provider_url = f"https://youtu.be/{video_id}" if video_id else None

                return ChunkUploadResult(
                    is_complete=True,
                    next_byte_offset=total_bytes,
                    provider_video_id=video_id,
                    provider_url=provider_url,
                    effective_privacy_status=eff_privacy,
                    raw_response=body,
                )

            resp.raise_for_status()
            raise RuntimeError(f"Unexpected chunk upload response: HTTP {resp.status_code}")

    async def query_upload_progress(
        self,
        session_uri: str,
        total_bytes: int,
        permit: NetworkEgressPermit,
    ) -> UploadProgressResult:
        """Query provider for received byte offset."""
        headers = {
            "Content-Length": "0",
            "Content-Range": f"bytes */{total_bytes}",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(session_uri, headers=headers)
            if resp.status_code == 308:
                range_hdr = resp.headers.get("Range", "")
                bytes_received = 0
                if range_hdr:
                    match = re.search(r"bytes=0-(\d+)", range_hdr)
                    if match:
                        bytes_received = int(match.group(1)) + 1
                return UploadProgressResult(
                    bytes_received=bytes_received,
                    is_already_complete=False,
                )
            if resp.status_code in (200, 201):
                body = resp.json()
                video_id = body.get("id")
                status_obj = body.get("status", {})
                eff_privacy_str = status_obj.get("privacyStatus", "private").upper()
                eff_privacy = (
                    PrivacyStatus(eff_privacy_str)
                    if eff_privacy_str in PrivacyStatus._value2member_map_
                    else PrivacyStatus.PRIVATE
                )
                provider_url = f"https://youtu.be/{video_id}" if video_id else None
                return UploadProgressResult(
                    bytes_received=total_bytes,
                    is_already_complete=True,
                    provider_video_id=video_id,
                    provider_url=provider_url,
                    effective_privacy_status=eff_privacy,
                )

            resp.raise_for_status()
            raise RuntimeError(f"Unexpected status query response: HTTP {resp.status_code}")

    async def reconcile_upload_session(
        self,
        session_uri: str,
        total_bytes: int,
        permit: NetworkEgressPermit,
    ) -> ReconciliationResult:
        """Authoritatively query session URI to reconcile UNKNOWN outcome."""
        headers = {
            "Content-Length": "0",
            "Content-Range": f"bytes */{total_bytes}",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.put(session_uri, headers=headers)
                if resp.status_code in (200, 201):
                    body = resp.json()
                    video_id = body.get("id")
                    provider_url = f"https://youtu.be/{video_id}" if video_id else None
                    return ReconciliationResult(
                        is_confirmed_success=True,
                        is_incomplete=False,
                        is_held_for_review=False,
                        provider_video_id=video_id,
                        provider_url=provider_url,
                        bytes_received=total_bytes,
                        diagnostic_reason="Provider confirmed upload completed successfully.",
                    )
                if resp.status_code == 308:
                    range_hdr = resp.headers.get("Range", "")
                    bytes_received = 0
                    if range_hdr:
                        match = re.search(r"bytes=0-(\d+)", range_hdr)
                        if match:
                            bytes_received = int(match.group(1)) + 1
                    return ReconciliationResult(
                        is_confirmed_success=False,
                        is_incomplete=True,
                        is_held_for_review=False,
                        bytes_received=bytes_received,
                        diagnostic_reason=f"Upload incomplete on provider; resumes from byte {bytes_received}.",
                    )
                if resp.status_code in (404, 410):
                    return ReconciliationResult(
                        is_confirmed_success=False,
                        is_incomplete=False,
                        is_held_for_review=True,
                        diagnostic_reason="Session URI expired or not found (404/410) after ambiguous completion; manual review required.",
                    )
                return ReconciliationResult(
                    is_confirmed_success=False,
                    is_incomplete=False,
                    is_held_for_review=True,
                    diagnostic_reason=f"Provider returned HTTP {resp.status_code}; reconciliation remaining pending.",
                )
        except Exception as exc:
            return ReconciliationResult(
                is_confirmed_success=False,
                is_incomplete=False,
                is_held_for_review=True,
                diagnostic_reason=f"Network error during reconciliation: {exc}",
            )

    def classify_error(
        self,
        exc: Exception,
        response_status: int | None = None,
        response_body: Any = None,
    ) -> ClassifiedError:
        """Map exception or HTTP status code into normalized error taxonomy."""
        status = response_status
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code

        body_str = str(response_body or getattr(exc, "response", None) or "")
        err_msg = str(exc)

        # 1. Auth errors
        if status == 401:
            return ClassifiedError(
                category=PublisherErrorCategory.AUTH_EXPIRED,
                is_retryable=True,
                retry_after_seconds=0,
                error_message="Access token expired (401).",
            )
        if status == 400 and ("invalid_grant" in body_str or "invalid_grant" in err_msg):
            return ClassifiedError(
                category=PublisherErrorCategory.AUTH_REVOKED,
                is_retryable=False,
                retry_after_seconds=None,
                error_message="OAuth authorization revoked or invalid (invalid_grant).",
            )

        # 2. Rate limits & Quotas
        if status == 429:
            return ClassifiedError(
                category=PublisherErrorCategory.RATE_LIMITED,
                is_retryable=True,
                retry_after_seconds=60,
                error_message="YouTube rate limit exceeded (429).",
            )
        if status == 403:
            if "quotaExceeded" in body_str or "quotaExceeded" in err_msg:
                # Timezone-aware Pacific midnight calculation
                tz_pacific = zoneinfo.ZoneInfo("America/Los_Angeles")
                now_pacific = datetime.now(tz_pacific)
                next_midnight = (now_pacific + timedelta(days=1)).replace(
                    hour=0, minute=0, second=5, microsecond=0
                )
                retry_after = max(60, int((next_midnight - now_pacific).total_seconds()))
                return ClassifiedError(
                    category=PublisherErrorCategory.QUOTA_EXCEEDED,
                    is_retryable=True,
                    retry_after_seconds=retry_after,
                    error_message=f"YouTube daily API quota exceeded. Retry after Pacific midnight ({retry_after}s).",
                )
            if "insufficientPermissions" in body_str or "forbidden" in body_str:
                return ClassifiedError(
                    category=PublisherErrorCategory.PERMISSION_DENIED,
                    is_retryable=False,
                    retry_after_seconds=None,
                    error_message="Insufficient YouTube API permissions.",
                )

        # 3. Server errors
        if status and status >= 500:
            return ClassifiedError(
                category=PublisherErrorCategory.PROVIDER_5XX,
                is_retryable=True,
                retry_after_seconds=30,
                error_message=f"YouTube provider 5xx server error (HTTP {status}).",
            )

        # 4. Network errors
        if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError)):
            return ClassifiedError(
                category=PublisherErrorCategory.NETWORK_TRANSIENT,
                is_retryable=True,
                retry_after_seconds=15,
                error_message=f"Transient network error: {exc}",
            )

        return ClassifiedError(
            category=PublisherErrorCategory.PERMANENT_PROVIDER_ERROR,
            is_retryable=False,
            retry_after_seconds=None,
            error_message=f"Unclassified publisher error: {exc}",
        )


# Register YouTube adapter automatically
AdapterRegistry.register(YouTubeDataApiAdapter())
