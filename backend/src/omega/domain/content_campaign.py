"""Canonical content campaign planning domain models and contracts."""

from __future__ import annotations

import datetime
import enum
import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omega.domain.content import ContentType
from omega.domain.content_selection import ContentSelectionMode


class ContentCampaignStatus(enum.StrEnum):
    READY = "READY"


def normalize_planned_release_at(value: datetime.datetime | str | None) -> str | None:
    """Normalize a timezone-aware release timestamp to canonical UTC ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError("planned_release_at must be timezone-aware")
    utc_dt = value.astimezone(datetime.UTC)
    return utc_dt.isoformat()


def compute_campaign_plan_checksum(
    *,
    channel_id: UUID,
    channel_dna_revision_id: UUID,
    title: str,
    objective: str | None,
    priority: int,
    items: list[dict[str, Any]],
) -> str:
    """Deterministic SHA-256 plan checksum using sorted-key compact JSON.

    Item order is semantic and strictly preserved without sorting items.
    """
    canonical_items: list[dict[str, Any]] = []
    for item in items:
        rel_at = item.get("planned_release_at")
        norm_rel = (
            normalize_planned_release_at(rel_at)
            if rel_at is not None
            else None
        )
        canonical_items.append(
            {
                "position": int(item["position"]),
                "selection_run_id": str(item["selection_run_id"]),
                "selection_decision_id": str(item["selection_decision_id"]),
                "topic_candidate_id": str(item["topic_candidate_id"]),
                "target_content_type": str(item["target_content_type"]),
                "planned_release_at": norm_rel,
            }
        )

    payload = {
        "channel_id": str(channel_id),
        "channel_dna_revision_id": str(channel_dna_revision_id),
        "title": title.strip(),
        "objective": objective.strip() if objective and objective.strip() else None,
        "priority": int(priority),
        "items": canonical_items,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recompute_persisted_campaign_plan_checksum(campaign: Any) -> str:
    """Deterministically recompute plan checksum from a persisted ContentCampaign authority."""
    sorted_items = sorted(campaign.items, key=lambda it: it.position)
    raw_items = [
        {
            "position": it.position,
            "selection_run_id": it.selection_run_id,
            "selection_decision_id": it.selection_decision_id,
            "topic_candidate_id": it.topic_candidate_id,
            "target_content_type": it.target_content_type,
            "planned_release_at": it.planned_release_at,
        }
        for it in sorted_items
    ]
    return compute_campaign_plan_checksum(
        channel_id=campaign.channel_id,
        channel_dna_revision_id=campaign.channel_dna_revision_id,
        title=campaign.title,
        objective=campaign.objective,
        priority=campaign.priority,
        items=raw_items,
    )


class ContentCampaignItemInput(BaseModel):
    selection_run_id: UUID
    target_content_type: ContentType
    planned_release_at: datetime.datetime | None = None

    @field_validator("planned_release_at")
    @classmethod
    def validate_timezone(
        cls, value: datetime.datetime | None
    ) -> datetime.datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("planned_release_at must be timezone-aware")
        return value


class ContentCampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    objective: str | None = Field(default=None, max_length=2000)
    priority: int = Field(default=1, ge=1, le=100)
    idempotency_key: str = Field(min_length=1, max_length=128)
    created_by: str = Field(min_length=1, max_length=100)
    items: list[ContentCampaignItemInput] = Field(min_length=1, max_length=50)

    @field_validator("title", "created_by", "idempotency_key")
    @classmethod
    def strip_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Field must not be blank")
        return clean

    @model_validator(mode="after")
    def validate_campaign(self) -> ContentCampaignCreate:
        if self.objective is not None:
            self.objective = self.objective.strip() or None

        # Check unique selection_run_ids in request
        run_ids = [item.selection_run_id for item in self.items]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("selection_run_id cannot appear multiple times in a campaign")

        # Validate release order: scheduled items must have strictly increasing release times
        scheduled_items: list[datetime.datetime] = [
            item.planned_release_at
            for item in self.items
            if item.planned_release_at is not None
        ]
        for i in range(len(scheduled_items) - 1):
            t1 = scheduled_items[i].astimezone(datetime.UTC)
            t2 = scheduled_items[i + 1].astimezone(datetime.UTC)
            if t2 <= t1:
                raise ValueError(
                    "planned_release_at must be strictly increasing according to campaign item order"
                )

        return self


class ContentCampaignItemResponse(BaseModel):
    id: UUID
    campaign_id: UUID
    position: int
    selection_run_id: UUID
    selection_decision_id: UUID
    topic_candidate_id: UUID
    candidate_title_snapshot: str
    selection_mode: ContentSelectionMode
    target_content_type: ContentType
    planned_release_at: datetime.datetime | None
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)


class ContentCampaignResponse(BaseModel):
    id: UUID
    channel_id: UUID
    channel_dna_revision_id: UUID
    title: str
    objective: str | None
    priority: int
    status: ContentCampaignStatus
    idempotency_key: str
    plan_checksum: str
    item_count: int
    created_by: str
    created_at: datetime.datetime
    items: list[ContentCampaignItemResponse]

    model_config = ConfigDict(from_attributes=True)
