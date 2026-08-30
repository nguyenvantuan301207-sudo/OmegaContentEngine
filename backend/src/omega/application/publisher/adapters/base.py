"""Base Platform Adapter interface and registry for OMEGA-011 Publisher.

Defines the abstract contract for external distribution platforms and provides
a thread-safe adapter registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from omega.domain.network import NetworkEgressPermit
from omega.domain.publisher import Platform, PrivacyStatus, PublisherErrorCategory


@dataclass(frozen=True)
class CredentialValidationResult:
    """Outcome of validating account credentials against official API."""

    is_valid: bool
    account_display_name: str | None = None
    external_account_id: str | None = None
    error_category: PublisherErrorCategory | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RefreshedTokenData:
    """Result of refreshing an OAuth access token."""

    access_token: str
    expires_in_seconds: int
    new_refresh_token: str | None = None


@dataclass(frozen=True)
class UploadSessionInitResult:
    """Outcome of initializing a resumable upload session."""

    session_uri: str
    expires_at: Any


@dataclass(frozen=True)
class ChunkUploadResult:
    """Outcome of transmitting a chunk to the provider."""

    is_complete: bool
    next_byte_offset: int
    provider_video_id: str | None = None
    provider_url: str | None = None
    effective_privacy_status: PrivacyStatus | None = None
    raw_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class UploadProgressResult:
    """Outcome of querying bytes saved by provider."""

    bytes_received: int
    is_already_complete: bool
    provider_video_id: str | None = None
    provider_url: str | None = None
    effective_privacy_status: PrivacyStatus | None = None


@dataclass(frozen=True)
class ReconciliationResult:
    """Authoritative outcome of querying an ambiguous upload session."""

    is_confirmed_success: bool
    is_incomplete: bool
    is_held_for_review: bool
    provider_video_id: str | None = None
    provider_url: str | None = None
    bytes_received: int = 0
    diagnostic_reason: str = ""


@dataclass(frozen=True)
class ClassifiedError:
    """Normalized error classification."""

    category: PublisherErrorCategory
    is_retryable: bool
    retry_after_seconds: int | None
    error_message: str


class BasePlatformAdapter(ABC):
    """Abstract interface for external platform distribution."""

    @property
    @abstractmethod
    def platform(self) -> Platform:
        """Target platform enum."""
        ...

    @abstractmethod
    async def validate_credentials(
        self,
        access_token: str,
        permit: NetworkEgressPermit,
    ) -> CredentialValidationResult:
        """Validate account status and permissions against official API."""
        ...

    @abstractmethod
    async def refresh_access_token(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        permit: NetworkEgressPermit,
    ) -> RefreshedTokenData:
        """Exchange refresh token for a fresh short-lived access token."""
        ...

    @abstractmethod
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
        """Initiate official resumable upload session with video metadata."""
        ...

    @abstractmethod
    async def upload_chunk(
        self,
        session_uri: str,
        chunk_data: bytes,
        start_byte: int,
        total_bytes: int,
        permit: NetworkEgressPermit,
    ) -> ChunkUploadResult:
        """Transmit an individual chunk to provider upload session."""
        ...

    @abstractmethod
    async def query_upload_progress(
        self,
        session_uri: str,
        total_bytes: int,
        permit: NetworkEgressPermit,
    ) -> UploadProgressResult:
        """Query provider for received byte offset after interrupted connection."""
        ...

    @abstractmethod
    async def reconcile_upload_session(
        self,
        session_uri: str,
        total_bytes: int,
        permit: NetworkEgressPermit,
    ) -> ReconciliationResult:
        """Reconcile ambiguous submission (UNKNOWN) via Content-Range: bytes */TOTAL."""
        ...

    @abstractmethod
    def classify_error(
        self, exc: Exception, response_status: int | None = None, response_body: Any = None
    ) -> ClassifiedError:
        """Map provider-specific error into normalized error taxonomy."""
        ...


class AdapterRegistry:
    """Registry mapping platforms to their concrete adapter instances."""

    _adapters: dict[Platform, BasePlatformAdapter] = {}

    @classmethod
    def register(cls, adapter: BasePlatformAdapter) -> None:
        cls._adapters[adapter.platform] = adapter

    @classmethod
    def get(cls, platform: Platform | str) -> BasePlatformAdapter:
        plat_enum = Platform(platform) if isinstance(platform, str) else platform
        adapter = cls._adapters.get(plat_enum)
        if not adapter:
            raise ValueError(f"No platform adapter registered for platform: {plat_enum}")
        return adapter
