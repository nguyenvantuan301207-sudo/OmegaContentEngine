"""Domain models, enums, schemas, and deterministic helpers for OMEGA-011 Publisher.

Defines platform types, account states, publish intent and attempt state machines,
error taxonomies, privacy statuses, and canonical checksum functions.
This is the domain layer — zero infrastructure/database dependencies.
"""

from __future__ import annotations

import enum
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Authoritative Enums ──


class Platform(enum.StrEnum):
    """Supported and reserved publishing platforms."""

    YOUTUBE = "YOUTUBE"
    TIKTOK = "TIKTOK"
    INSTAGRAM = "INSTAGRAM"
    X_TWITTER = "X_TWITTER"


SUPPORTED_PUBLISH_PLATFORMS: set[Platform] = {Platform.YOUTUBE}


class PlatformAccountStatus(enum.StrEnum):
    """Lifecycle status of a connected platform account."""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class PublishIntentState(enum.StrEnum):
    """Authoritative 7-state lifecycle machine for PublishIntent."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class PublishAttemptState(enum.StrEnum):
    """Authoritative 9-state lifecycle machine for PublishAttempt."""

    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    FINALIZING = "FINALIZING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"
    UNKNOWN = "UNKNOWN"
    BLOCKED_GUARDIAN = "BLOCKED_GUARDIAN"
    CANCELLED = "CANCELLED"


class ReconciliationStatus(enum.StrEnum):
    """Reconciliation state for ambiguous (UNKNOWN) publish attempts."""

    PENDING = "PENDING"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILED = "CONFIRMED_FAILED"
    MANUAL_HOLD = "MANUAL_HOLD"


class PublisherErrorCategory(enum.StrEnum):
    """Provider-neutral error taxonomy for external publishing side effects."""

    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTH_REVOKED = "AUTH_REVOKED"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    NETWORK_TRANSIENT = "NETWORK_TRANSIENT"
    PROVIDER_5XX = "PROVIDER_5XX"
    INVALID_MEDIA = "INVALID_MEDIA"
    INVALID_METADATA = "INVALID_METADATA"
    CONTENT_POLICY_REJECTED = "CONTENT_POLICY_REJECTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DUPLICATE_OR_CONFLICT = "DUPLICATE_OR_CONFLICT"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    PRIVACY_RESTRICTION_BLOCKED = "PRIVACY_RESTRICTION_BLOCKED"
    PERMANENT_PROVIDER_ERROR = "PERMANENT_PROVIDER_ERROR"


class HandoffStatus(enum.StrEnum):
    """State of a durable cross-service retry handoff row to OMEGA-010."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DELIVERED = "DELIVERED"
    DEAD_LETTER = "DEAD_LETTER"


class PrivacyStatus(enum.StrEnum):
    """Target and effective video privacy visibility."""

    PRIVATE = "PRIVATE"
    UNLISTED = "UNLISTED"
    PUBLIC = "PUBLIC"


# ── Canonical Checksums & Identity Functions ──


def compute_publish_intent_checksum(
    *,
    task_id: UUID,
    media_artifact_checksum: str,
    channel_dna_revision_id: UUID | None,
    platform: str,
    title: str,
    description: str,
    tags: list[str],
    requested_privacy_status: str,
    category_id: str,
    made_for_kids: bool,
    platform_custom_options: dict[str, Any] | None = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint for a PublishIntent contract.

    Any change to metadata, media artifact, audience flags, or DNA invalidates this checksum.
    """
    canon_tags = sorted(list(tags))
    canon_options = json.dumps(platform_custom_options or {}, sort_keys=True)
    parts = [
        str(task_id).lower(),
        media_artifact_checksum.lower(),
        str(channel_dna_revision_id).lower() if channel_dna_revision_id else "NONE",
        platform.upper(),
        title.strip(),
        description.strip(),
        json.dumps(canon_tags),
        requested_privacy_status.upper(),
        category_id.strip(),
        "TRUE" if made_for_kids else "FALSE",
        canon_options,
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_publish_attempt_idempotency_key(
    *,
    publish_intent_id: UUID,
    attempt_number: int,
    intent_checksum: str,
    guardian_epoch: int,
) -> str:
    """Compute deterministic SHA-256 idempotency key for an individual PublishAttempt."""
    parts = [
        str(publish_intent_id).lower(),
        str(attempt_number),
        intent_checksum.lower(),
        str(guardian_epoch),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_handoff_idempotency_key(
    *,
    publish_intent_id: UUID,
    publish_attempt_id: UUID,
    retry_generation: int,
    earliest_retry_at: datetime,
) -> str:
    """Compute deterministic SHA-256 idempotency key for a Scheduler retry handoff outbox row."""
    canon_ts = earliest_retry_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [
        str(publish_intent_id).lower(),
        str(publish_attempt_id).lower(),
        str(retry_generation),
        canon_ts,
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Domain Schemas ──


class PlatformAccountCreate(BaseModel):
    """Payload to register or connect a platform account."""

    channel_id: UUID
    platform: Platform = Platform.YOUTUBE
    account_display_name: str = Field(..., min_length=1, max_length=255)
    external_account_id: str = Field(..., min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=list)


class PlatformAccountResponse(BaseModel):
    """Safe response representation of a connected platform account (no secrets)."""

    id: UUID
    channel_id: UUID
    platform: Platform
    account_display_name: str
    external_account_id: str
    status: PlatformAccountStatus
    scopes: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PublishIntentCreate(BaseModel):
    """Payload to construct an approved, versioned PublishIntent before scheduling."""

    mission_id: UUID
    task_id: UUID
    channel_id: UUID
    platform_account_id: UUID
    media_artifact_id: UUID
    media_artifact_checksum: str = Field(..., min_length=64, max_length=64)
    channel_dna_revision_id: UUID | None = None
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list)
    requested_privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    category_id: str = Field(default="28", min_length=1, max_length=32)
    made_for_kids: bool
    platform_custom_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("media_artifact_checksum")
    @classmethod
    def validate_sha256(cls, v: str) -> str:
        if len(v) != 64 or not all(c in "0123456789abcdefABCDEF" for c in v):
            raise ValueError(
                "media_artifact_checksum must be a valid 64-character SHA-256 hex string"
            )
        return v.lower()


class PublishIntentResponse(BaseModel):
    """Public schema for an immutable PublishIntent."""

    id: UUID
    mission_id: UUID
    task_id: UUID
    channel_id: UUID
    platform_account_id: UUID
    media_artifact_id: UUID
    media_artifact_checksum: str
    channel_dna_revision_id: UUID | None
    revision_number: int
    supersedes_intent_id: UUID | None
    title: str
    description: str
    tags: list[str]
    requested_privacy_status: PrivacyStatus
    category_id: str
    made_for_kids: bool
    platform_custom_options: dict[str, Any]
    intent_checksum: str
    state: PublishIntentState
    lease_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    superseded_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PublishAttemptResponse(BaseModel):
    """Schema representing an individual publish execution attempt."""

    id: UUID
    publish_intent_id: UUID
    attempt_number: int
    idempotency_key: str
    state: PublishAttemptState
    provider_video_id: str | None
    provider_url: str | None
    effective_privacy_status: PrivacyStatus | None
    error_category: PublisherErrorCategory | None
    error_message: str | None
    retry_after_seconds: int | None
    reconciliation_status: ReconciliationStatus | None
    started_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class UploadProgressResponse(BaseModel):
    """Safe upload progression schema (hides sensitive session URI)."""

    publish_attempt_id: UUID
    total_bytes: int
    bytes_uploaded: int
    progress_percentage: float
    is_complete: bool
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)
