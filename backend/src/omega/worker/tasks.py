"""Celery worker tasks.

Uses SYNCHRONOUS SQLAlchemy (psycopg2) for database access.
Never uses async/asyncio inside Celery tasks.

See docs/decisions/001-foundation-architecture.md for rationale.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from omega.infrastructure.celery_app import celery_app
from omega.logging import get_logger

logger = get_logger(service="omega-worker")


def _sanitize_task_error(error: Exception) -> str:
    """Return a sanitized error message safe for database persistence.

    Never persists raw tracebacks or secret-containing exception details.
    """
    error_type = type(error).__name__
    message = str(error).split("\n")[0]
    for pattern in ["://", "password", "secret", "token", "key=", "apikey"]:
        if pattern.lower() in message.lower():
            return f"{error_type}: Task execution error (details in logs)"
    if len(message) > 500:
        message = message[:500] + "..."
    return f"{error_type}: {message}"


@celery_app.task(bind=True, name="omega.worker.tasks.run_test_job")
def run_test_job(self, job_id: str) -> dict:
    """Execute a test job.

    Updates job state: RUNNING → SUCCEEDED (or FAILED on error).
    Uses synchronous DB access via psycopg2.
    """
    import uuid

    from omega.infrastructure.database_sync import SyncSessionLocal
    from omega.infrastructure.models import Job

    logger.info("Test job started", job_id=job_id)

    session = SyncSessionLocal()
    try:
        parsed_id = uuid.UUID(str(job_id))
        job = session.query(Job).filter(Job.id == parsed_id).first()
        if not job:
            logger.error("Job not found in database", job_id=job_id)
            return {"status": "error", "message": "Job not found"}

        # Mark as RUNNING
        job.state = "RUNNING"
        job.started_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        session.commit()

        # Simulate work
        time.sleep(2)

        # Mark as SUCCEEDED
        job.state = "SUCCEEDED"
        job.result = {"message": "Test job completed successfully"}
        job.completed_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        session.commit()

        logger.info("Test job completed", job_id=job_id)
        return {"status": "success", "job_id": job_id}

    except Exception as exc:
        session.rollback()
        # Log full diagnostic error
        logger.error(
            "Test job failed",
            job_id=job_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        # Persist sanitized error
        try:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job:
                job.state = "FAILED"
                job.error = _sanitize_task_error(exc)
                job.completed_at = datetime.now(UTC)
                job.updated_at = datetime.now(UTC)
                session.commit()
        except Exception:
            logger.error("Failed to update job state after error", job_id=job_id, exc_info=True)
        return {"status": "error", "job_id": job_id}
    finally:
        session.close()
