"""Job service — application layer orchestration.

Handles job creation, dispatch, and retrieval.
Coordinates between domain models, infrastructure (DB), and Celery.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.job import JobCreate, JobCreatedResponse, JobResponse, JobState
from omega.infrastructure.models import Job
from omega.logging import get_logger

logger = get_logger(service="omega-api")


def _sanitize_error(error: Exception) -> str:
    """Return a sanitized error message safe for persistence.

    Never persists raw tracebacks or secret-containing exception details.
    Diagnostic details are logged separately via structured logging.
    """
    error_type = type(error).__name__
    # Only use the first line of the error message, strip potential secrets
    message = str(error).split("\n")[0]
    # Remove common patterns that might contain connection strings / secrets
    for pattern in ["://", "password", "secret", "token", "key=", "apikey"]:
        if pattern.lower() in message.lower():
            return f"{error_type}: Connection or dispatch error (details in logs)"
    # Cap length to prevent oversized error messages
    if len(message) > 500:
        message = message[:500] + "..."
    return f"{error_type}: {message}"


async def create_test_job(session: AsyncSession) -> JobCreatedResponse:
    """Create a test job and dispatch it to the Celery worker.

    If dispatch fails, the job is marked FAILED with a sanitized error.
    Diagnostic details are written to structured logs only.
    """
    job_data = JobCreate(job_type="test")
    job_id = uuid4()
    now = datetime.now(UTC)

    job = Job(
        id=job_id,
        job_type=job_data.job_type,
        state=JobState.QUEUED.value,
        payload=job_data.payload,
        max_retries=job_data.max_retries,
        retry_count=0,
        created_at=now,
        queued_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.commit()

    # Attempt to dispatch to Celery
    try:
        from omega.worker.tasks import run_test_job

        run_test_job.delay(str(job_id))
        logger.info("Job dispatched to worker", job_id=str(job_id), job_type="test")
        return JobCreatedResponse(job_id=job_id, state=JobState.QUEUED)

    except Exception as exc:
        # Dispatch failed — mark FAILED, do NOT leave as QUEUED
        logger.error(
            "Failed to dispatch job to Celery broker",
            job_id=str(job_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )

        # Persist sanitized error only
        job.state = JobState.FAILED.value
        job.error = _sanitize_error(exc)
        job.updated_at = datetime.now(UTC)
        await session.commit()

        return JobCreatedResponse(job_id=job_id, state=JobState.FAILED)


async def get_job(session: AsyncSession, job_id: UUID) -> JobResponse | None:
    """Retrieve a job by ID."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None
    return JobResponse.model_validate(job)
