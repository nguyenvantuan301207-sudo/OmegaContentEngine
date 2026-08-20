"""Task domain model.

Defines Task states, dependency relations, validation rules, and Pydantic schemas.
This is the domain layer — zero infrastructure dependencies.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from omega.domain.mission import InvalidStateTransitionError


class TaskState(enum.StrEnum):
    """All possible states for a Task."""

    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    READY = "READY"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Valid state transitions for Task
VALID_TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING: {
        TaskState.BLOCKED,
        TaskState.READY,
        TaskState.WAITING_APPROVAL,
        TaskState.CANCELLED,
    },
    TaskState.BLOCKED: {
        TaskState.READY,
        TaskState.WAITING_APPROVAL,
        TaskState.CANCELLED,
    },
    TaskState.WAITING_APPROVAL: {
        TaskState.READY,  # On user approval (pre-execution gate -> READY)
        TaskState.FAILED,  # On user rejection
        TaskState.CANCELLED,
    },
    TaskState.READY: {
        TaskState.QUEUED,
        TaskState.CANCELLED,
    },
    TaskState.QUEUED: {
        TaskState.RUNNING,
        TaskState.FAILED,  # If worker dispatch fails
        TaskState.CANCELLED,
    },
    TaskState.RUNNING: {
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.READY,  # On bounded retry
        TaskState.CANCELLED,
    },
    # Terminal states
    TaskState.SUCCEEDED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


def validate_task_transition(current: TaskState, target: TaskState) -> None:
    """Validate that a task transition from current to target is permissible."""
    if target not in VALID_TASK_TRANSITIONS.get(current, set()):
        raise InvalidStateTransitionError(current=current.value, target=target.value, entity="Task")


# ── Pydantic Schemas ──


class TaskCreate(BaseModel):
    """Schema for task creation (used by planners)."""

    task_type: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    priority: int = Field(default=1, ge=1, le=10)
    requires_approval: bool = False
    input: dict[str, Any] | None = None
    max_retries: int = Field(default=3, ge=0, le=10)


class TaskDependencyCreate(BaseModel):
    """Schema for task dependency creation."""

    task_id: UUID
    depends_on_task_id: UUID


class TaskDependencyResponse(BaseModel):
    """Schema for returning task dependency details."""

    id: UUID
    mission_id: UUID
    task_id: UUID
    depends_on_task_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskResponse(BaseModel):
    """Schema for returning Task details."""

    id: UUID
    mission_id: UUID
    execution_id: UUID | None = None
    task_type: str
    title: str
    description: str | None = None
    state: TaskState
    priority: int
    requires_approval: bool
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime
    updated_at: datetime
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TaskRejectRequest(BaseModel):
    """Schema for rejecting a task waiting approval."""

    reason: str = Field(default="Rejected by user", min_length=1)
