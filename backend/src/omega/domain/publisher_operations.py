"""Pydantic schemas for Publisher Operations and Observability API.

Strictly defines all response models. NEVER exposes raw ORM models, credentials,
OAuth tokens, or upload session URIs.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PublisherOperationsOverviewResponse(BaseModel):
    """Aggregate metrics for operational overview cards."""

    model_config = ConfigDict(extra="forbid")

    scheduled_upcoming: int
    scheduled_due: int
    publish_claimed: int
    publish_uploading: int
    publish_unknown: int
    publish_succeeded_recent: int
    publish_failed_recent: int
    recent_window_hours: int
    retry_pending: int
    retry_claimed: int
    dead_letter_count: int
    recovery_manual_hold_count: int
    schedule_manual_hold_count: int
    oldest_pending_retry_age_seconds: float | None = None
    oldest_recovery_hold_age_seconds: float | None = None
    publisher_queue_name: str
    publisher_worker_role: str
    configured_concurrency: int
    prefetch_multiplier: int
    max_handoff_attempts: int


class CalendarPublicationItem(BaseModel):
    """Publication slot from canonical OMEGA-010 schedule reservation."""

    model_config = ConfigDict(extra="forbid")

    reservation_id: UUID
    publish_intent_id: UUID
    title: str
    channel_id: UUID
    channel_name: str | None = None
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    reservation_state: str
    dispatching_at: datetime | None = None
    intent_state: str
    requested_privacy_status: str
    revision_number: int


class CalendarPublicationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CalendarPublicationItem]
    total_count: int
    limit: int
    offset: int


class ActivePublicationItem(BaseModel):
    """Active in-flight publication progress."""

    model_config = ConfigDict(extra="forbid")

    publish_intent_id: UUID
    task_id: UUID
    channel_id: UUID
    channel_name: str | None = None
    title: str
    intent_state: str
    requested_privacy_status: str
    attempt_id: UUID
    attempt_number: int
    attempt_state: str
    error_category: str | None = None
    reconciliation_status: str | None = None
    started_at: datetime
    claimed_by_worker_id: str | None = None
    lease_expires_at: datetime | None = None
    bytes_uploaded: int
    total_bytes: int
    progress_percentage: float
    upload_expires_at: datetime | None = None


class ActivePublicationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ActivePublicationItem]
    total_count: int
    limit: int
    offset: int


class RetryQueueItem(BaseModel):
    """Pending or claimed retry handoff."""

    model_config = ConfigDict(extra="forbid")

    handoff_id: UUID
    publish_intent_id: UUID
    publish_attempt_id: UUID
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    last_sanitized_error: str | None = None
    title: str
    channel_id: UUID
    channel_name: str | None = None
    created_at: datetime


class RetryQueueListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RetryQueueItem]
    total_count: int
    limit: int
    offset: int


class ManualHoldItem(BaseModel):
    """Publish attempt in provider recovery MANUAL_HOLD status."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    publish_intent_id: UUID
    attempt_number: int
    attempt_state: str
    error_category: str | None = None
    reconciliation_status: str | None = None
    last_sanitized_error: str | None = None
    started_at: datetime
    age_seconds: float | None = None
    title: str
    channel_id: UUID
    channel_name: str | None = None
    hold_source: str = "RECOVERY"


class ManualHoldListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ManualHoldItem]
    total_count: int
    limit: int
    offset: int


class DeadLetterItem(BaseModel):
    """Exhausted handoff in DEAD_LETTER status."""

    model_config = ConfigDict(extra="forbid")

    handoff_id: UUID
    publish_intent_id: UUID
    publish_attempt_id: UUID
    status: str
    attempt_count: int
    max_attempts: int
    next_retry_time: datetime | None = None
    last_sanitized_error: str | None = None
    title: str
    channel_id: UUID
    channel_name: str | None = None
    created_at: datetime
    can_requeue: bool
    requeue_blocked_reason: str | None = None


class DeadLetterListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeadLetterItem]
    total_count: int
    limit: int
    offset: int


class PublicationHistoryItem(BaseModel):
    """Historical publication attempt record."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    publish_intent_id: UUID
    attempt_number: int
    state: str
    error_category: str | None = None
    reconciliation_status: str | None = None
    provider_video_id: str | None = None
    last_sanitized_error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    title: str
    channel_id: UUID
    channel_name: str | None = None
    requested_privacy_status: str


class PublicationHistoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PublicationHistoryItem]
    total_count: int
    limit: int
    offset: int


class CalendarDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_id: UUID
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    reservation_state: str
    dispatching_at: datetime | None = None


class AttemptHistoryDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    attempt_number: int
    state: str
    error_category: str | None = None
    reconciliation_status: str | None = None
    provider_video_id: str | None = None
    last_sanitized_error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float | None = None


class UploadSessionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    bytes_uploaded: int
    total_bytes: int
    progress_percentage: float
    expires_at: datetime


class RetryHandoffDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handoff_id: UUID
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    last_sanitized_error: str | None = None


class ProviderDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str | None = None
    url: str | None = None


class RecoveryEligibilityDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_operations: list[str]
    blocked_operations: dict[str, str]


class PublicationDetailResponse(BaseModel):
    """Complete detail view model for one PublishIntent."""

    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    task_id: UUID
    channel_id: UUID
    channel_name: str | None = None
    title: str
    requested_privacy_status: str
    intent_state: str
    revision_number: int
    attempt_generation: int
    created_at: datetime
    calendar: CalendarDetail | None = None
    attempts: list[AttemptHistoryDetail]
    upload_session: UploadSessionDetail | None = None
    retry_handoff: RetryHandoffDetail | None = None
    provider: ProviderDetail
    recovery_eligibility: RecoveryEligibilityDetail


# ── Controlled Operator Action Request / Response Schemas ──


class OperatorActionRequest(BaseModel):
    """Mandatory operator context for recovery actions."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(..., min_length=1, max_length=64, description="Operator name or ID")
    reason: str = Field(..., min_length=1, max_length=500, description="Audit justification")


class ReconcileManualHoldResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    reconciliation_status: str
    operation: str = "EXPLICIT_EXTERNAL_RECONCILIATION"
    actor: str
    reason: str


class RequeueDeadLetterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handoff_id: UUID
    status: str
    next_attempt_at: datetime
    attempt_count: int
    actor: str
    reason: str


class AuthorizeSessionResumeResponse(BaseModel):
    """Reports authorized existing session/offset WITHOUT session_uri."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    upload_session_id: UUID
    provider_offset: int
    total_bytes: int
    actor: str
    reason: str
