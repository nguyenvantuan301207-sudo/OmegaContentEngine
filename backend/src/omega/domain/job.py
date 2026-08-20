"""Job domain model.

Defines job states and Pydantic schemas for API serialization.
This is the domain layer — no infrastructure dependencies.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class JobState(enum.StrEnum):
    """All possible states for a job."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY = "RETRY"
    DEAD = "DEAD"


class JobCreate(BaseModel):
    """Schema for creating a test job (no user input needed)."""

    job_type: str = "test"
    payload: dict | None = None
    max_retries: int = 3


class JobResponse(BaseModel):
    """Schema for returning a job to clients."""

    id: UUID
    job_type: str
    state: JobState
    payload: dict | None = None
    result: dict | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobCreatedResponse(BaseModel):
    """Schema returned when a job is created."""

    job_id: UUID
    state: JobState
