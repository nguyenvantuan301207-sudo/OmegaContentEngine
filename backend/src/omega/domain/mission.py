"""Mission domain model.

Defines Mission states, autonomy levels, trigger types, and Pydantic schemas.
This is the domain layer — zero infrastructure dependencies.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MissionState(enum.StrEnum):
    """All possible states for a Mission."""

    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AutonomyLevel(enum.StrEnum):
    """Autonomy levels governing task dispatch policy."""

    MANUAL = "MANUAL"
    ASSISTED = "ASSISTED"
    SUPERVISED = "SUPERVISED"
    # Reserved for future phases
    AUTONOMOUS = "AUTONOMOUS"
    STRATEGIC_AUTONOMOUS = "STRATEGIC_AUTONOMOUS"


class MissionTriggerType(enum.StrEnum):
    """How a mission execution was initiated."""

    MANUAL = "MANUAL"
    API = "API"
    SCHEDULED = "SCHEDULED"  # Reserved


class ExecutionState(enum.StrEnum):
    """States for an individual MissionExecution instance."""

    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Valid state transitions for Mission
VALID_MISSION_TRANSITIONS: dict[MissionState, set[MissionState]] = {
    MissionState.DRAFT: {MissionState.READY},
    MissionState.READY: {MissionState.RUNNING, MissionState.CANCELLED},
    MissionState.RUNNING: {
        MissionState.PAUSED,
        MissionState.SUCCEEDED,
        MissionState.FAILED,
        MissionState.CANCELLED,
    },
    MissionState.PAUSED: {
        MissionState.RUNNING,
        MissionState.FAILED,
        MissionState.CANCELLED,
    },
    # Terminal states have no valid outgoing transitions
    MissionState.SUCCEEDED: set(),
    MissionState.FAILED: set(),
    MissionState.CANCELLED: set(),
}


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: str, target: str, entity: str = "Mission") -> None:
        self.current = current
        self.target = target
        self.entity = entity
        super().__init__(f"Invalid {entity} state transition: {current} -> {target}")


def validate_mission_transition(current: MissionState, target: MissionState) -> None:
    """Validate that a transition from current to target state is permissible."""
    if target not in VALID_MISSION_TRANSITIONS.get(current, set()):
        raise InvalidStateTransitionError(current=current.value, target=target.value, entity="Mission")


# ── Pydantic Schemas ──


class MissionCreate(BaseModel):
    """Schema for creating a new mission."""

    title: str = Field(..., min_length=1, max_length=255)
    objective: str = Field(..., min_length=1)
    channel_id: UUID | None = None
    description: str | None = None
    autonomy_level: AutonomyLevel = AutonomyLevel.SUPERVISED
    priority: int = Field(default=1, ge=1, le=10)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionUpdate(BaseModel):
    """Schema for updating an existing mission in DRAFT state."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    objective: str | None = Field(default=None, min_length=1)
    channel_id: UUID | None = None
    description: str | None = None
    autonomy_level: AutonomyLevel | None = None
    priority: int | None = Field(default=None, ge=1, le=10)
    metadata: dict[str, Any] | None = None


class MissionExecutionResponse(BaseModel):
    """Schema for returning MissionExecution details."""

    id: UUID
    mission_id: UUID
    channel_dna_revision_id: UUID | None = None
    state: ExecutionState
    trigger_type: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class MissionResponse(BaseModel):
    """Schema for returning Mission details."""

    id: UUID
    title: str
    objective: str
    channel_id: UUID | None = None
    description: str | None = None
    state: MissionState
    autonomy_level: AutonomyLevel
    priority: int
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    paused_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
