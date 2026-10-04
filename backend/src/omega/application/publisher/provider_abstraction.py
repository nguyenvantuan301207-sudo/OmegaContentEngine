"""Video Publishing Provider Abstraction for P25-A.

Decouples publishing domain orchestration from third-party vendor SDKs.
Defines narrow provider interfaces for media upload, metadata application,
thumbnail upload, visibility, scheduling, and publication state verification.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega.application.publisher.adapters.base import ClassifiedError
from omega.domain.publisher import PublisherErrorCategory
from omega.domain.publishing import (
    PublishPayload,
    PublishScheduleSpec,
    PublishVisibility,
)


@dataclass(frozen=True)
class ProviderMediaResult:
    """Outcome of uploading media to the provider."""

    external_media_id: str
    external_url: str | None = None
    bytes_uploaded: int = 0
    total_bytes: int = 0
    is_complete: bool = True
    provider_status: str = "UPLOADED"
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderMetadataResult:
    """Outcome of applying title, description, tags, and chapters."""

    external_media_id: str
    is_applied: bool = True
    applied_title: str = ""
    applied_tags_count: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderThumbnailResult:
    """Outcome of uploading a physical thumbnail."""

    external_media_id: str
    is_applied: bool = True
    thumbnail_sha256: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderVisibilityResult:
    """Outcome of configuring video privacy / visibility."""

    external_media_id: str
    visibility: PublishVisibility
    is_applied: bool = True
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderScheduleResult:
    """Outcome of scheduling future release."""

    external_media_id: str
    publish_at: datetime
    is_scheduled: bool = True
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderPublicationState:
    """Authoritative state of the publication on the provider."""

    external_media_id: str
    exists_on_provider: bool
    provider_status: str  # e.g., "UPLOADED", "PROCESSED", "PUBLISHED", "FAILED"
    visibility: PublishVisibility = PublishVisibility.PRIVATE
    metadata_applied: bool = False
    thumbnail_applied: bool = False
    is_scheduled: bool = False
    scheduled_at: datetime | None = None
    title: str | None = None
    external_url: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class VideoPublishingProvider(ABC):
    """Abstract interface defining provider boundaries for video publication."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Normalized provider name (e.g. YOUTUBE, FAKE)."""
        ...

    @abstractmethod
    async def upload_media(
        self,
        *,
        artifact_path: Path | str,
        payload: PublishPayload,
        idempotency_key: str,
    ) -> ProviderMediaResult:
        """Upload physical video media with idempotency protection."""
        ...

    @abstractmethod
    async def apply_metadata(
        self,
        *,
        external_media_id: str,
        title: str,
        description: str,
        tags: tuple[str, ...] | list[str],
        chapters: tuple[dict[str, Any], ...] = (),
        custom_options: dict[str, Any] | None = None,
    ) -> ProviderMetadataResult:
        """Apply video title, description, tags, and chapters."""
        ...

    @abstractmethod
    async def upload_thumbnail(
        self,
        *,
        external_media_id: str,
        thumbnail_path: Path | str,
        thumbnail_sha256: str,
    ) -> ProviderThumbnailResult:
        """Upload custom thumbnail for the media."""
        ...

    @abstractmethod
    async def apply_visibility(
        self,
        *,
        external_media_id: str,
        visibility: PublishVisibility,
    ) -> ProviderVisibilityResult:
        """Set privacy visibility on the provider."""
        ...

    @abstractmethod
    async def schedule_publication(
        self,
        *,
        external_media_id: str,
        schedule: PublishScheduleSpec,
    ) -> ProviderScheduleResult:
        """Configure future release schedule on the provider."""
        ...

    @abstractmethod
    async def fetch_publication_state(
        self,
        *,
        external_media_id: str,
    ) -> ProviderPublicationState:
        """Query authoritative current provider publication state."""
        ...

    @abstractmethod
    def classify_error(
        self,
        exc: Exception,
    ) -> ClassifiedError:
        """Classify exceptions into normalized retryable or terminal error taxonomy."""
        ...
