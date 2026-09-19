"""Domain models, policy checksum, and contracts for content campaign execution."""

from __future__ import annotations

import datetime
import enum
import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Authoritative compatibility version with StaticMissionPlanner
STATIC_MISSION_PLANNER_COMPAT_VERSION = "1.0.0"

FANOUT_POLICY_NAME = "default_campaign_fanout"
FANOUT_POLICY_VERSION = 1
FANOUT_AUTONOMY_LEVEL = "SUPERVISED"
FANOUT_MISSION_TRIGGER_TYPE = "API"
FANOUT_MIN_ITEMS = 1
FANOUT_MAX_ITEMS = 50
FANOUT_PRIORITY_MIN = 1
FANOUT_PRIORITY_MAX = 10
FANOUT_STAGE_COUNT = 7
FANOUT_DEPENDENCY_COUNT = 6


class ContentCampaignExecutionStatus(enum.StrEnum):
    MATERIALIZED = "MATERIALIZED"


def get_fanout_policy_payload() -> dict[str, Any]:
    """Return canonical policy payload for checksum generation."""
    return {
        "autonomy_level": FANOUT_AUTONOMY_LEVEL,
        "dependency_count": FANOUT_DEPENDENCY_COUNT,
        "fanout_policy_name": FANOUT_POLICY_NAME,
        "fanout_policy_version": FANOUT_POLICY_VERSION,
        "max_items": FANOUT_MAX_ITEMS,
        "min_items": FANOUT_MIN_ITEMS,
        "mission_trigger_type": FANOUT_MISSION_TRIGGER_TYPE,
        "planner_name": "StaticMissionPlanner",
        "planner_plan_version": STATIC_MISSION_PLANNER_COMPAT_VERSION,
        "priority_max": FANOUT_PRIORITY_MAX,
        "priority_min": FANOUT_PRIORITY_MIN,
        "stage_count": FANOUT_STAGE_COUNT,
    }


def compute_fanout_policy_checksum() -> str:
    """Compute deterministic SHA-256 checksum for the fan-out policy."""
    payload = get_fanout_policy_payload()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ContentCampaignExecutionCreate(BaseModel):
    actor: str = Field(..., max_length=100)

    @field_validator("actor")
    @classmethod
    def validate_actor(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("actor must not be empty or blank")
        if len(trimmed) > 100:
            raise ValueError("actor must not exceed 100 characters")
        return trimmed


class ContentCampaignItemExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_execution_id: UUID
    campaign_item_id: UUID
    position: int
    mission_id: UUID
    mission_execution_id: UUID
    mission_state: str
    mission_execution_state: str
    created_at: datetime.datetime


class ContentCampaignExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    channel_id: UUID
    channel_dna_revision_id: UUID
    status: str
    fanout_policy_name: str
    fanout_policy_version: int
    fanout_policy_checksum: str
    item_count: int
    materialized_by: str
    created_at: datetime.datetime
    items: list[ContentCampaignItemExecutionResponse] = Field(default_factory=list)
