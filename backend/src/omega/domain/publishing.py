"""Domain models, enums, schemas, and deterministic helpers for P25-A Publishing Rollout.

Defines:
- PublishEligibilityDenialReason: Canonical fail-closed taxonomy
- PublishEligibilityResult: Typed structured outcome of publish preflight gate
- PublishingState: Authoritative 11-state publishing state machine
- PublishVisibility: Typed visibility options (PRIVATE, UNLISTED, PUBLIC, SCHEDULED)
- PublishScheduleSpec: Future scheduling parameters
- PublishPayload: Structured provider handoff payload
- compute_canonical_payload_checksum: Deterministic fingerprint of publish state
- PublishReceipt: Non-secret provider execution receipt with full lineage
"""

from __future__ import annotations

import enum
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Canonical Enums ─────────────────────────────────────────────────────────


class PublishEligibilityDenialReason(enum.StrEnum):
    """Authoritative fail-closed denial taxonomy for publish eligibility."""

    NO_CURRENT_ARTIFACT = "NO_CURRENT_ARTIFACT"
    RUNTIME_TRUTH_MISSING = "RUNTIME_TRUTH_MISSING"
    PRODUCTION_QA_NOT_PASS = "PRODUCTION_QA_NOT_PASS"
    GUARDIAN_NOT_ACCEPTED = "GUARDIAN_NOT_ACCEPTED"
    CREATIVE_QA_NOT_PASS = "CREATIVE_QA_NOT_PASS"
    PACKAGING_NOT_ACCEPTED = "PACKAGING_NOT_ACCEPTED"
    THUMBNAIL_MISSING = "THUMBNAIL_MISSING"
    ATTRIBUTION_REQUIRED = "ATTRIBUTION_REQUIRED"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    STALE_ARTIFACT = "STALE_ARTIFACT"
    PAYLOAD_LINEAGE_MISMATCH = "PAYLOAD_LINEAGE_MISMATCH"


class PublishingState(enum.StrEnum):
    """Authoritative 11-state lifecycle machine for P25-A publishing."""

    PREPARED = "PREPARED"
    ELIGIBLE = "ELIGIBLE"
    SUBMITTING = "SUBMITTING"
    UPLOADED = "UPLOADED"
    METADATA_APPLIED = "METADATA_APPLIED"
    THUMBNAIL_APPLIED = "THUMBNAIL_APPLIED"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class PublishVisibility(enum.StrEnum):
    """Target and effective visibility options."""

    PRIVATE = "PRIVATE"
    UNLISTED = "UNLISTED"
    PUBLIC = "PUBLIC"
    SCHEDULED = "SCHEDULED"


# ── Eligibility Gate Domain Schemas ──────────────────────────────────────────


class PublishEligibilityResult(BaseModel):
    """Deterministic, fail-closed result of publish eligibility evaluation."""

    is_eligible: bool
    denial_reasons: tuple[PublishEligibilityDenialReason, ...] = ()
    production_request_id: UUID | None = None
    artifact_id: UUID | None = None
    channel_id: UUID | None = None
    platform_account_id: UUID | None = None
    packaging_plan_id: UUID | None = None
    creative_qa_result_id: UUID | None = None
    runtime_truth_id: UUID | None = None
    channel_dna_revision_id: UUID | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


# ── Schedule & Payload Specification ─────────────────────────────────────────


class PublishScheduleSpec(BaseModel):
    """Validated scheduling configuration for scheduled publication."""

    publish_at: datetime
    timezone: str = "UTC"
    visibility: PublishVisibility = PublishVisibility.SCHEDULED

    model_config = ConfigDict(frozen=True)

    @field_validator("publish_at")
    @classmethod
    def validate_future_timestamp(cls, v: datetime) -> datetime:
        now = datetime.now(UTC)
        ts = v if v.tzinfo else v.replace(tzinfo=UTC)
        if ts <= now:
            raise ValueError(f"publish_at must be in the future (got {ts.isoformat()}, now is {now.isoformat()})")
        return ts


class PublishPayload(BaseModel):
    """Normalized provider payload containing validated media and packaging metadata."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    tags: tuple[str, ...] = ()
    thumbnail_path: str
    thumbnail_sha256: str = Field(..., min_length=64, max_length=64)
    video_artifact_id: UUID
    video_artifact_sha256: str = Field(..., min_length=64, max_length=64)
    channel_dna_revision_id: UUID
    chapters: tuple[dict[str, Any], ...] = ()
    attribution_block: str | None = None
    visibility: PublishVisibility = PublishVisibility.PRIVATE
    schedule: PublishScheduleSpec | None = None
    category_id: str = "28"
    made_for_kids: bool = False
    custom_options: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


# ── Canonical Checksum ────────────────────────────────────────────────────────


def compute_canonical_payload_checksum(
    *,
    video_artifact_sha256: str,
    title: str,
    description: str,
    tags: tuple[str, ...] | list[str],
    thumbnail_sha256: str,
    platform: str,
    visibility: str,
    channel_dna_revision_id: UUID | None,
    schedule_at: str | None = None,
    attribution_block: str | None = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint for a publish payload.

    Deterministically binds:
    - video artifact hash
    - title
    - description
    - sorted tags
    - thumbnail hash
    - platform target
    - visibility
    - scheduled timestamp (if applicable)
    - channel DNA revision
    - mandatory attribution block
    """
    canon_tags = sorted(list(tags))
    parts = [
        video_artifact_sha256.lower().strip(),
        title.strip(),
        description.strip(),
        json.dumps(canon_tags),
        thumbnail_sha256.lower().strip(),
        platform.upper().strip(),
        visibility.upper().strip(),
        str(channel_dna_revision_id).lower() if channel_dna_revision_id else "NONE",
        schedule_at.strip() if schedule_at else "NONE",
        attribution_block.strip() if attribution_block else "NONE",
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Publish Receipt (Non-Secret Provider Truth) ───────────────────────────────


class PublishReceipt(BaseModel):
    """Authoritative, non-secret provider outcome record for a published artifact.

    Strictly excludes access tokens, refresh tokens, client secrets, and sensitive headers.
    """

    receipt_id: UUID = Field(default_factory=uuid.uuid4)
    publish_intent_id: UUID
    publish_attempt_id: UUID
    provider: str
    external_media_id: str
    external_url: str | None = None
    provider_status: str
    published_at: datetime | None = None
    payload_checksum: str
    completion_state: PublishingState
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    lineage: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(frozen=True)

    @field_validator("response_metadata")
    @classmethod
    def sanitize_metadata_secrets(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Fail-closed redaction of any sensitive token keys in response metadata."""
        prohibited_keys = {
            "access_token",
            "refresh_token",
            "token",
            "secret",
            "client_secret",
            "authorization",
            "bearer",
            "api_key",
            "password",
        }
        sanitized: dict[str, Any] = {}
        for k, val in v.items():
            if any(forbidden in k.lower() for forbidden in prohibited_keys):
                sanitized[k] = "[REDACTED_SECRET]"
            elif isinstance(val, dict):
                sanitized[k] = cls.sanitize_metadata_secrets(val)
            else:
                sanitized[k] = val
        return sanitized
