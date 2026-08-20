"""Decision log domain model.

Defines operational actors, decision types, and Pydantic schemas.
No chain-of-thought is persisted — only concise operational rationales.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Actor(enum.StrEnum):
    """Operational actor recording a decision."""

    USER = "USER"
    SYSTEM = "SYSTEM"
    ORCHESTRATOR = "ORCHESTRATOR"
    PLANNER = "PLANNER"
    WORKER = "WORKER"


class DecisionType(enum.StrEnum):
    """Categories of operational decisions."""

    MISSION_PLAN = "MISSION_PLAN"
    MISSION_START = "MISSION_START"
    MISSION_PAUSE = "MISSION_PAUSE"
    MISSION_RESUME = "MISSION_RESUME"
    MISSION_CANCEL = "MISSION_CANCEL"
    MISSION_COMPLETE = "MISSION_COMPLETE"
    MISSION_FAILED = "MISSION_FAILED"

    TASK_DISPATCH = "TASK_DISPATCH"
    TASK_APPROVAL_REQUESTED = "TASK_APPROVAL_REQUESTED"
    TASK_APPROVED = "TASK_APPROVED"
    TASK_REJECTED = "TASK_REJECTED"
    TASK_RETRY = "TASK_RETRY"
    TASK_SUCCEEDED = "TASK_SUCCEEDED"
    TASK_FAILED = "TASK_FAILED"


class DecisionLogCreate(BaseModel):
    """Schema for recording a new decision log."""

    mission_id: UUID
    execution_id: UUID | None = None
    task_id: UUID | None = None
    decision_type: str = Field(..., max_length=50)
    decision: str = Field(..., min_length=1, max_length=500)
    reason: str = Field(..., min_length=1, max_length=1000)
    actor: str = Field(..., max_length=50)
    context: dict[str, Any] | None = None


class DecisionLogResponse(BaseModel):
    """Schema for returning DecisionLog details."""

    id: UUID
    mission_id: UUID
    execution_id: UUID | None = None
    task_id: UUID | None = None
    decision_type: str
    decision: str
    reason: str
    actor: str
    context: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
