"""Domain models, enums, schemas, and QA rules for OMEGA-007 Production Engine."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Enums ──


class ProductionMode(enum.StrEnum):
    """Execution context mode for production."""

    INTERACTIVE = "INTERACTIVE"
    MISSION_EXECUTION = "MISSION_EXECUTION"


class ProductionRequestStatus(enum.StrEnum):
    """Lifecycle status of a ProductionRequest."""

    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProductionOutcome(enum.StrEnum):
    """Usability outcome of a production request."""

    RENDERED = "RENDERED"
    BLOCKED = "BLOCKED"


class ProductionFormat(enum.StrEnum):
    """Supported output video targets."""

    YOUTUBE_VIDEO_MP4 = "YOUTUBE_VIDEO_MP4"


class SceneType(enum.StrEnum):
    """Semantic type of a production scene."""

    NARRATION = "NARRATION"
    TITLE = "TITLE"
    QUOTE = "QUOTE"
    STATISTIC = "STATISTIC"
    B_ROLL = "B_ROLL"
    IMAGE = "IMAGE"
    TEXT_CARD = "TEXT_CARD"
    CTA = "CTA"


class AssetType(enum.StrEnum):
    """Type of visual or auditory asset."""

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    BACKGROUND = "BACKGROUND"
    ICON = "ICON"
    AUDIO = "AUDIO"
    MUSIC = "MUSIC"
    SUBTITLE = "SUBTITLE"
    DOCUMENT = "DOCUMENT"
    NONE = "NONE"


class AssetRequirementStatus(enum.StrEnum):
    """Status of resolving an asset requirement."""

    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    UNAVAILABLE = "UNAVAILABLE"


class LicenseRequirement(enum.StrEnum):
    """Required rights level for an asset."""

    COMMERCIAL_ALLOWED = "COMMERCIAL_ALLOWED"
    EDITORIAL_ONLY = "EDITORIAL_ONLY"
    PUBLIC_DOMAIN_ONLY = "PUBLIC_DOMAIN_ONLY"
    ANY = "ANY"


class AssetProviderType(enum.StrEnum):
    """Provider source of an asset."""

    LOCAL = "LOCAL"
    PLACEHOLDER = "PLACEHOLDER"
    SYSTEM = "SYSTEM"


class LicenseStatus(enum.StrEnum):
    """Legal rights status of an asset."""

    OWNED = "OWNED"
    GENERATED = "GENERATED"
    LICENSED = "LICENSED"
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    ATTRIBUTION_REQUIRED = "ATTRIBUTION_REQUIRED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class RenderJobState(enum.StrEnum):
    """State of a production render execution job."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY = "RETRY"
    CANCELLED = "CANCELLED"


class RenderErrorCode(enum.StrEnum):
    """Classified error codes for render failures."""

    INPUT_INVALID = "INPUT_INVALID"
    ASSET_MISSING = "ASSET_MISSING"
    RIGHTS_BLOCKED = "RIGHTS_BLOCKED"
    STORAGE_FAILED = "STORAGE_FAILED"
    FFMPEG_FAILED = "FFMPEG_FAILED"
    PROBE_FAILED = "PROBE_FAILED"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class MediaArtifactType(enum.StrEnum):
    """Type of produced media artifact."""

    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    SUBTITLE = "SUBTITLE"
    SCENE_RENDER = "SCENE_RENDER"
    THUMBNAIL_PLACEHOLDER = "THUMBNAIL_PLACEHOLDER"


class ProductionQASeverity(enum.StrEnum):
    """Severity of a Production QA finding."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKING = "BLOCKING"


class ProductionQAStatus(enum.StrEnum):
    """QA verification status for a media artifact / production request."""

    PENDING = "PENDING"
    PASSED = "PASSED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
    BLOCKED = "BLOCKED"


class ProductionQARuleCode(enum.StrEnum):
    """Authoritative 17 rule codes for local Production Engine QA."""

    SCRIPT_PIN_MISMATCH = "SCRIPT_PIN_MISMATCH"
    DNA_LINEAGE_MISMATCH = "DNA_LINEAGE_MISMATCH"
    MISSING_REQUIRED_ASSET = "MISSING_REQUIRED_ASSET"
    BLOCKED_ASSET_RIGHTS = "BLOCKED_ASSET_RIGHTS"
    UNKNOWN_REQUIRED_ASSET_RIGHTS = "UNKNOWN_REQUIRED_ASSET_RIGHTS"
    MISSING_NARRATION = "MISSING_NARRATION"
    TIMELINE_GAP = "TIMELINE_GAP"
    TIMELINE_OVERLAP = "TIMELINE_OVERLAP"
    SUBTITLE_OUT_OF_RANGE = "SUBTITLE_OUT_OF_RANGE"
    SUBTITLE_EMPTY = "SUBTITLE_EMPTY"
    RENDER_FILE_MISSING = "RENDER_FILE_MISSING"
    RENDER_HASH_MISMATCH = "RENDER_HASH_MISMATCH"
    ZERO_DURATION_ARTIFACT = "ZERO_DURATION_ARTIFACT"
    VIDEO_DIMENSION_MISMATCH = "VIDEO_DIMENSION_MISMATCH"
    VIDEO_CODEC_MISMATCH = "VIDEO_CODEC_MISMATCH"
    AUDIO_STREAM_MISSING = "AUDIO_STREAM_MISSING"
    FFPROBE_VALIDATION_FAILED = "FFPROBE_VALIDATION_FAILED"


# ── Schemas ──


class VoiceProfileSnapshot(BaseModel):
    """Voice synthesis configuration profile."""

    language: str = Field(default="en", max_length=20)
    provider: str = Field(default="PLACEHOLDER", max_length=50)
    voice_ref: str = Field(default="neutral_default", max_length=100)
    speaking_rate: float = Field(default=1.0, ge=0.5, le=2.5)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)
    style: str = Field(default="conversational", max_length=50)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)

    model_config = ConfigDict(extra="ignore")


class ProductionRequestCreate(BaseModel):
    """Schema for initiating a new ProductionRequest."""

    script_version_id: UUID
    target_width: int = Field(default=1920, ge=240, le=3840)
    target_height: int = Field(default=1080, ge=240, le=3840)
    fps: int = Field(default=30, ge=15, le=60)
    video_codec: str = Field(default="h264", max_length=30)
    audio_codec: str = Field(default="aac", max_length=30)
    container_format: str = Field(default="mp4", max_length=20)
    mission_execution_id: UUID | None = None
    voice_profile: VoiceProfileSnapshot = Field(default_factory=VoiceProfileSnapshot)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("container_format")
    @classmethod
    def validate_container(cls, v: str) -> str:
        if v.lower() not in ("mp4", "mov"):
            raise ValueError("Only 'mp4' (and 'mov') container formats are supported.")
        return v.lower()


class ProductionRenderPayload(BaseModel):
    """Payload for triggering render execution."""

    idempotency_key: str = Field(..., min_length=1, max_length=128)


class ProductionRerenderPayload(BaseModel):
    """Payload for explicit rerender execution ($vN \to vN+1$)."""

    idempotency_key: str = Field(..., min_length=1, max_length=128)
    change_reason: str = Field(default="Explicit rerender", max_length=300)


class ProductionRequestResponse(BaseModel):
    """Schema for ProductionRequest representation."""

    id: UUID
    channel_id: UUID
    script_version_id: UUID
    content_request_id: UUID
    channel_dna_revision_id: UUID
    mission_execution_id: UUID | None = None
    mode: ProductionMode
    status: ProductionRequestStatus
    outcome: ProductionOutcome | None = None
    target_width: int
    target_height: int
    fps: int
    video_codec: str
    audio_codec: str
    container_format: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ProductionSceneResponse(BaseModel):
    """Schema for a production scene."""

    id: UUID
    production_request_id: UUID
    scene_order: int
    script_section_id: UUID | None = None
    start_statement_id: UUID | None = None
    end_statement_id: UUID | None = None
    scene_type: SceneType
    narration_text: str
    estimated_duration_ms: int
    visual_intent: str | None = None
    transition_in: str | None = None
    transition_out: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssetRequirementResponse(BaseModel):
    """Schema for an asset requirement."""

    id: UUID
    scene_id: UUID
    asset_type: AssetType
    purpose: str
    query_hint: str | None = None
    required: bool
    status: AssetRequirementStatus
    license_requirement: LicenseRequirement
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductionAssetResponse(BaseModel):
    """Schema for a resolved production asset."""

    id: UUID
    channel_id: UUID
    production_request_id: UUID
    asset_requirement_id: UUID | None = None
    asset_type: AssetType
    provider_type: AssetProviderType
    storage_uri: str
    content_hash: str
    mime_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    license_status: LicenseStatus
    source_ref: str | None = None
    attribution: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NarrationSegmentResponse(BaseModel):
    """Schema for a narration audio segment."""

    id: UUID
    production_request_id: UUID
    scene_id: UUID
    audio_asset_id: UUID | None = None
    text: str
    start_ms: int
    end_ms: int
    duration_ms: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SubtitleCueResponse(BaseModel):
    """Schema for a subtitle cue."""

    id: UUID
    production_request_id: UUID
    scene_id: UUID
    cue_order: int
    start_ms: int
    end_ms: int
    text: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RenderPlanResponse(BaseModel):
    """Schema for a RenderPlan manifest."""

    id: UUID
    production_request_id: UUID
    version: int
    width: int
    height: int
    fps: int
    video_codec: str
    audio_codec: str
    container: str
    total_duration_ms: int
    scene_manifest: list[dict[str, Any]]
    audio_manifest: list[dict[str, Any]]
    subtitle_manifest: list[dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductionRenderJobResponse(BaseModel):
    """Schema for a render job."""

    id: UUID
    production_request_id: UUID
    render_plan_id: UUID
    idempotency_key: str
    state: RenderJobState
    attempt: int
    max_attempts: int
    error_code: RenderErrorCode | None = None
    sanitized_error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class MediaArtifactResponse(BaseModel):
    """Schema for an immutable MediaArtifact."""

    id: UUID
    production_request_id: UUID
    render_job_id: UUID | None = None
    artifact_type: MediaArtifactType
    version: int
    is_current: bool
    storage_uri: str
    content_hash: str
    file_size_bytes: int
    mime_type: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductionQAFinding(BaseModel):
    """Structured QA finding for Production QA."""

    rule_code: ProductionQARuleCode
    severity: ProductionQASeverity
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ProductionQAResultResponse(BaseModel):
    """Schema for Production QA evaluation result."""

    id: UUID
    production_request_id: UUID
    artifact_id: UUID
    status: ProductionQAStatus
    findings: list[ProductionQAFinding] = Field(default_factory=list)
    executed_at: datetime

    model_config = ConfigDict(from_attributes=True)
