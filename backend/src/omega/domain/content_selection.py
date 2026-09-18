"""Canonical content-selection execution contracts."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContentSelectionStatus(enum.StrEnum):
    READY = "READY"
    SELECTED = "SELECTED"


class ContentSelectionMode(enum.StrEnum):
    POLICY = "POLICY"
    OVERRIDE = "OVERRIDE"


class ContentSelectionRunCreate(BaseModel):
    candidate_ids: list[UUID] = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=1, max_length=128)
    mission_execution_id: UUID | None = None

    @field_validator("idempotency_key")
    @classmethod
    def strip_idempotency_key(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("idempotency_key must not be blank")
        return clean

    @field_validator("candidate_ids")
    @classmethod
    def require_unique_candidates(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("candidate_ids must be unique")
        return value


class ContentSelectionFinalize(BaseModel):
    selected_candidate_id: UUID | None = None
    actor: str = Field(min_length=1, max_length=100)
    override_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("actor")
    @classmethod
    def strip_actor(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("actor must not be blank")
        return clean

    @model_validator(mode="after")
    def strip_optional_reason(self) -> ContentSelectionFinalize:
        if self.override_reason is not None:
            self.override_reason = self.override_reason.strip() or None
        return self


class ContentSelectionDecisionResponse(BaseModel):
    id: UUID
    candidate_id: UUID
    rank: int
    final_score: float
    score_breakdown: dict[str, float]
    reasons: list[str]
    duplicate_status: str
    similar_memory_id: UUID | None
    similarity_score: float | None
    candidate_title_snapshot: str
    topic_fingerprint_snapshot: str
    candidate_snapshot: dict[str, Any]
    evidence_snapshot: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContentSelectionRunResponse(BaseModel):
    id: UUID
    channel_id: UUID
    channel_dna_revision_id: UUID
    mission_execution_id: UUID | None
    status: ContentSelectionStatus
    policy_name: str
    policy_version: int
    policy_checksum: str
    candidate_set_checksum: str
    idempotency_key: str
    recommended_candidate_id: UUID
    selected_candidate_id: UUID | None
    selection_mode: ContentSelectionMode | None
    selected_by: str | None
    selection_reason: str | None
    considered_count: int
    created_at: datetime
    completed_at: datetime
    selected_at: datetime | None
    decisions: list[ContentSelectionDecisionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
