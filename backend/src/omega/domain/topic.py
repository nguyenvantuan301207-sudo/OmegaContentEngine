"""Topic domain models, enums, validators, and Pydantic schemas.

Defines TopicCandidate, TopicAngle, TopicMemory, and associated schemas.
Zero infrastructure dependencies in domain layer.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TopicStatus(enum.StrEnum):
    """Lifecycle states for a TopicCandidate."""

    DISCOVERED = "DISCOVERED"
    EVALUATED = "EVALUATED"
    RECOMMENDED = "RECOMMENDED"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class TopicSourceType(enum.StrEnum):
    """Supported and reserved topic candidate sources.

    Only MANUAL, SEED, HISTORICAL, IMPORT, and SYSTEM_GENERATED are active in OMEGA-004.
    Other sources are reserved for future phases and cannot be used at runtime.
    """

    # Active in OMEGA-004
    MANUAL = "MANUAL"
    SEED = "SEED"
    HISTORICAL = "HISTORICAL"
    IMPORT = "IMPORT"
    SYSTEM_GENERATED = "SYSTEM_GENERATED"

    # Reserved for future phases
    GOOGLE_TRENDS = "GOOGLE_TRENDS"
    YOUTUBE = "YOUTUBE"
    REDDIT = "REDDIT"
    NEWS = "NEWS"
    RSS = "RSS"
    SEARCH = "SEARCH"


SUPPORTED_TOPIC_SOURCES: set[TopicSourceType] = {
    TopicSourceType.MANUAL,
    TopicSourceType.SEED,
    TopicSourceType.HISTORICAL,
    TopicSourceType.IMPORT,
    TopicSourceType.SYSTEM_GENERATED,
}


class DuplicateStatus(enum.StrEnum):
    """Classification of topic similarity against TopicMemory."""

    FRESH_TOPIC = "FRESH_TOPIC"
    RELATED_TOPIC = "RELATED_TOPIC"
    SAME_TOPIC_NEW_ANGLE = "SAME_TOPIC_NEW_ANGLE"
    SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"


class EvaluationContextMode(enum.StrEnum):
    """Evaluation context resolution mode."""

    INTERACTIVE = "INTERACTIVE"
    MISSION_EXECUTION = "MISSION_EXECUTION"


# ── Pydantic Schemas for Angles ──


class TopicAngleBase(BaseModel):
    """Base schema for a specific thematic angle or framing of a topic."""

    angle: str = Field(..., min_length=2, max_length=300)
    hook: str | None = Field(default=None, max_length=500)
    audience_intent: str | None = Field(default=None, max_length=100)
    format_hint: str | None = Field(default=None, max_length=100)


class TopicAngleCreate(TopicAngleBase):
    """Schema for creating a topic angle."""

    pass


class TopicAngleResponse(TopicAngleBase):
    """Schema for returning a topic angle."""

    id: UUID
    candidate_id: UUID | None = None
    memory_id: UUID | None = None
    normalized_angle: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Pydantic Schemas for Topic Candidate ──


class TopicCandidateCreate(BaseModel):
    """Schema for ingesting a single topic candidate."""

    title: str = Field(..., min_length=2, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    source_type: TopicSourceType = TopicSourceType.MANUAL
    source_name: str = Field(default="user_input", max_length=100)
    source_ref: str | None = Field(default=None, max_length=500)
    language: str = Field(default="en", min_length=2, max_length=20)
    region: str = Field(default="US", min_length=2, max_length=10)
    entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    angles: list[TopicAngleCreate] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=100)
    manual_trend_score: float | None = Field(default=None, ge=0.0, le=100.0)
    manual_cost_efficiency_score: float | None = Field(default=None, ge=0.0, le=100.0)
    manual_revenue_score: float | None = Field(default=None, ge=0.0, le=100.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type")
    @classmethod
    def check_source_type(cls, v: TopicSourceType) -> TopicSourceType:
        if v not in SUPPORTED_TOPIC_SOURCES:
            raise ValueError(
                f"Source '{v.value}' is reserved and not supported in OMEGA-004. Supported sources: {', '.join(s.value for s in SUPPORTED_TOPIC_SOURCES)}"
            )
        return v


class TopicCandidateBatchImport(BaseModel):
    """Schema for batch ingesting multiple candidates."""

    candidates: list[TopicCandidateCreate] = Field(..., min_length=1, max_length=100)


class TopicCandidateUpdate(BaseModel):
    """Schema for updating mutable attributes of a candidate."""

    title: str | None = Field(default=None, min_length=2, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    entities: list[str] | None = None
    keywords: list[str] | None = None
    tags: list[str] | None = None
    manual_trend_score: float | None = Field(default=None, ge=0.0, le=100.0)
    manual_cost_efficiency_score: float | None = Field(default=None, ge=0.0, le=100.0)
    manual_revenue_score: float | None = Field(default=None, ge=0.0, le=100.0)
    metadata: dict[str, Any] | None = None


class TopicEvaluateRequest(BaseModel):
    """Request payload for evaluating a topic candidate."""

    mode: EvaluationContextMode = EvaluationContextMode.INTERACTIVE
    mission_execution_id: UUID | None = None


class TopicRejectRequest(BaseModel):
    """Request payload for rejecting a candidate."""

    reason: str = Field(..., min_length=3, max_length=500)


class TopicCandidateResponse(BaseModel):
    """Detailed response model for a topic candidate."""

    id: UUID
    channel_id: UUID
    title: str
    normalized_title: str
    summary: str | None = None
    source_type: TopicSourceType
    source_name: str
    source_ref: str | None = None
    language: str
    region: str
    entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    topic_fingerprint: str
    audience_fit_score: float | None = None
    strategic_fit_score: float | None = None
    trend_score: float | None = None
    novelty_score: float | None = None
    content_gap_score: float | None = None
    historical_performance_score: float | None = None
    cost_efficiency_score: float | None = None
    revenue_potential_score: float | None = None
    final_score: float | None = None
    duplicate_status: DuplicateStatus = DuplicateStatus.FRESH_TOPIC
    similar_memory_id: UUID | None = None
    similarity_score: float | None = None
    status: TopicStatus
    reasons: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    angles: list[TopicAngleResponse] = Field(default_factory=list)
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    created_at: datetime
    updated_at: datetime
    evaluated_at: datetime | None = None
    archived_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── Pydantic Schemas for Topic Memory ──


class TopicMemoryResponse(BaseModel):
    """Response model for a Channel Topic Memory record."""

    id: UUID
    channel_id: UUID
    canonical_topic: str
    normalized_topic: str
    topic_fingerprint: str
    entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    semantic_tags: list[str] = Field(default_factory=list)
    first_seen_at: datetime
    last_seen_at: datetime
    times_discovered: int
    times_selected: int
    times_produced: int
    times_rejected: int
    last_selected_at: datetime | None = None
    last_produced_at: datetime | None = None
    last_rejected_at: datetime | None = None
    last_evaluation_score: float | None = None
    angles: list[TopicAngleResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
