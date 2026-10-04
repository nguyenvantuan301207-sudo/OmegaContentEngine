"""Deterministic Fake Publishing Provider for P25-A.

Simulates all provider operations completely offline:
- video upload with duplicate replay idempotency
- metadata application
- thumbnail upload
- privacy / visibility settings
- future scheduling
- provider state query
- partial failures (metadata / thumbnail failure after upload)
- retryable and terminal failures
- lost local response simulation (for authoritative reconciliation)

Zero network calls required.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omega.application.publisher.adapters.base import ClassifiedError
from omega.application.publisher.provider_abstraction import (
    ProviderMediaResult,
    ProviderMetadataResult,
    ProviderPublicationState,
    ProviderScheduleResult,
    ProviderThumbnailResult,
    ProviderVisibilityResult,
    VideoPublishingProvider,
)
from omega.domain.publisher import PublisherErrorCategory
from omega.domain.publishing import (
    PublishPayload,
    PublishScheduleSpec,
    PublishVisibility,
)


class FakeProviderError(Exception):
    """Base error for fake provider simulations."""

    pass


class FakeRetryableProviderError(FakeProviderError):
    """Simulated transient 5xx or network timeout."""

    pass


class FakeTerminalProviderError(FakeProviderError):
    """Simulated permanent policy or auth rejection."""

    pass


@dataclass
class FakeMediaRecord:
    """Internal provider store representation of a published video."""

    external_media_id: str
    idempotency_key: str
    payload_checksum: str
    video_artifact_sha256: str
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    chapters: list[dict[str, Any]] = field(default_factory=list)
    thumbnail_sha256: str | None = None
    visibility: PublishVisibility = PublishVisibility.PRIVATE
    metadata_applied: bool = False
    thumbnail_applied: bool = False
    is_scheduled: bool = False
    scheduled_at: datetime | None = None
    status: str = "UPLOADED"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakePublishingProvider(VideoPublishingProvider):
    """Deterministic, in-memory fake provider for regression tests and isolated canaries."""

    def __init__(self, provider_name: str = "FAKE") -> None:
        self._provider_name = provider_name
        self._objects_by_idempotency: dict[str, FakeMediaRecord] = {}
        self._objects_by_external_id: dict[str, FakeMediaRecord] = {}
        self.upload_call_count = 0
        self.metadata_call_count = 0
        self.thumbnail_call_count = 0
        self.visibility_call_count = 0
        self.schedule_call_count = 0
        self.state_query_call_count = 0

        # Failure injection flags
        self.retryable_failures_remaining: int = 0
        self.fail_terminal_message: str | None = None
        self.fail_metadata: bool = False
        self.fail_thumbnail: bool = False
        self.simulate_lost_local_response: bool = False

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def total_media_count(self) -> int:
        return len(self._objects_by_external_id)

    def get_record(self, external_media_id: str) -> FakeMediaRecord | None:
        return self._objects_by_external_id.get(external_media_id)

    async def upload_media(
        self,
        *,
        artifact_path: Path | str,
        payload: PublishPayload,
        idempotency_key: str,
    ) -> ProviderMediaResult:
        self.upload_call_count += 1

        # Check injected terminal failure
        if self.fail_terminal_message:
            raise FakeTerminalProviderError(self.fail_terminal_message)

        # Check injected retryable failure
        if self.retryable_failures_remaining > 0:
            self.retryable_failures_remaining -= 1
            raise FakeRetryableProviderError("Simulated transient 503 Provider Unavailable.")

        # Idempotency check: if already uploaded with this idempotency key, replay existing
        if idempotency_key in self._objects_by_idempotency:
            existing = self._objects_by_idempotency[idempotency_key]
            return ProviderMediaResult(
                external_media_id=existing.external_media_id,
                external_url=f"https://provider.example.com/watch?v={existing.external_media_id}",
                bytes_uploaded=1024 * 1024,
                total_bytes=1024 * 1024,
                is_complete=True,
                provider_status=existing.status,
                raw_response={"status": "replayed", "video_id": existing.external_media_id},
            )

        # Generate deterministic external ID based on idempotency key
        seed_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:11]
        external_id = f"fake-{seed_hash}"

        new_record = FakeMediaRecord(
            external_media_id=external_id,
            idempotency_key=idempotency_key,
            payload_checksum=payload.video_artifact_sha256,
            video_artifact_sha256=payload.video_artifact_sha256,
            title=payload.title,
            description=payload.description,
            tags=list(payload.tags),
            chapters=list(payload.chapters),
            thumbnail_sha256=None,
            visibility=payload.visibility,
            status="UPLOADED",
        )

        # Store in provider state
        self._objects_by_idempotency[idempotency_key] = new_record
        self._objects_by_external_id[external_id] = new_record

        # Check lost local response simulation
        if self.simulate_lost_local_response:
            self.simulate_lost_local_response = False  # fire once
            raise FakeRetryableProviderError(
                "Local connection dropped before receiving provider upload response."
            )

        return ProviderMediaResult(
            external_media_id=external_id,
            external_url=f"https://provider.example.com/watch?v={external_id}",
            bytes_uploaded=1024 * 1024,
            total_bytes=1024 * 1024,
            is_complete=True,
            provider_status="UPLOADED",
            raw_response={"status": "created", "video_id": external_id},
        )

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
        self.metadata_call_count += 1
        record = self._objects_by_external_id.get(external_media_id)
        if not record:
            raise FakeTerminalProviderError(f"Video {external_media_id} not found on provider.")

        if self.fail_metadata:
            self.fail_metadata = False
            raise FakeRetryableProviderError("Simulated metadata update failure.")

        record.title = title
        record.description = description
        record.tags = list(tags)
        record.chapters = list(chapters)
        record.metadata_applied = True
        record.status = "METADATA_APPLIED"
        record.updated_at = datetime.now(UTC)

        return ProviderMetadataResult(
            external_media_id=external_media_id,
            is_applied=True,
            applied_title=title,
            applied_tags_count=len(tags),
            raw_response={"status": "metadata_updated"},
        )

    async def upload_thumbnail(
        self,
        *,
        external_media_id: str,
        thumbnail_path: Path | str,
        thumbnail_sha256: str,
    ) -> ProviderThumbnailResult:
        self.thumbnail_call_count += 1
        record = self._objects_by_external_id.get(external_media_id)
        if not record:
            raise FakeTerminalProviderError(f"Video {external_media_id} not found on provider.")

        if self.fail_thumbnail:
            self.fail_thumbnail = False
            raise FakeRetryableProviderError("Simulated thumbnail upload failure.")

        record.thumbnail_sha256 = thumbnail_sha256
        record.thumbnail_applied = True
        record.status = "THUMBNAIL_APPLIED"
        record.updated_at = datetime.now(UTC)

        return ProviderThumbnailResult(
            external_media_id=external_media_id,
            is_applied=True,
            thumbnail_sha256=thumbnail_sha256,
            raw_response={"status": "thumbnail_updated"},
        )

    async def apply_visibility(
        self,
        *,
        external_media_id: str,
        visibility: PublishVisibility,
    ) -> ProviderVisibilityResult:
        self.visibility_call_count += 1
        record = self._objects_by_external_id.get(external_media_id)
        if not record:
            raise FakeTerminalProviderError(f"Video {external_media_id} not found on provider.")

        record.visibility = visibility
        record.status = "PUBLISHED"
        record.updated_at = datetime.now(UTC)

        return ProviderVisibilityResult(
            external_media_id=external_media_id,
            visibility=visibility,
            is_applied=True,
            raw_response={"status": "visibility_updated", "visibility": visibility.value},
        )

    async def schedule_publication(
        self,
        *,
        external_media_id: str,
        schedule: PublishScheduleSpec,
    ) -> ProviderScheduleResult:
        self.schedule_call_count += 1
        record = self._objects_by_external_id.get(external_media_id)
        if not record:
            raise FakeTerminalProviderError(f"Video {external_media_id} not found on provider.")

        record.is_scheduled = True
        record.scheduled_at = schedule.publish_at
        record.visibility = schedule.visibility
        record.status = "SCHEDULED"
        record.updated_at = datetime.now(UTC)

        return ProviderScheduleResult(
            external_media_id=external_media_id,
            publish_at=schedule.publish_at,
            is_scheduled=True,
            raw_response={"status": "scheduled", "scheduled_at": schedule.publish_at.isoformat()},
        )

    async def fetch_publication_state(
        self,
        *,
        external_media_id: str,
    ) -> ProviderPublicationState:
        self.state_query_call_count += 1
        record = self._objects_by_external_id.get(external_media_id)
        if not record:
            return ProviderPublicationState(
                external_media_id=external_media_id,
                exists_on_provider=False,
                provider_status="NOT_FOUND",
            )

        return ProviderPublicationState(
            external_media_id=record.external_media_id,
            exists_on_provider=True,
            provider_status=record.status,
            visibility=record.visibility,
            metadata_applied=record.metadata_applied,
            thumbnail_applied=record.thumbnail_applied,
            is_scheduled=record.is_scheduled,
            scheduled_at=record.scheduled_at,
            title=record.title,
            external_url=f"https://provider.example.com/watch?v={record.external_media_id}",
            raw_metadata={"status": record.status},
        )

    def classify_error(
        self,
        exc: Exception,
    ) -> ClassifiedError:
        if isinstance(exc, FakeRetryableProviderError):
            return ClassifiedError(
                category=PublisherErrorCategory.PROVIDER_5XX,
                is_retryable=True,
                retry_after_seconds=5,
                error_message=str(exc),
            )
        if isinstance(exc, FakeTerminalProviderError):
            return ClassifiedError(
                category=PublisherErrorCategory.PERMANENT_PROVIDER_ERROR,
                is_retryable=False,
                retry_after_seconds=None,
                error_message=str(exc),
            )
        return ClassifiedError(
            category=PublisherErrorCategory.UNKNOWN_OUTCOME,
            is_retryable=False,
            retry_after_seconds=None,
            error_message=str(exc),
        )
