"""Celery worker tasks.

Uses SYNCHRONOUS SQLAlchemy (psycopg2) for database access.
Never uses async/asyncio inside Celery tasks.

See docs/decisions/001-foundation-architecture.md for rationale.
"""

from __future__ import annotations

import time
import uuid
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


# ── OMEGA-001 Foundation Task (Preserved) ──


@celery_app.task(bind=True, name="omega.worker.tasks.run_test_job")
def run_test_job(self, job_id: str) -> dict:
    """Execute a test job (OMEGA-001 Foundation).

    Updates job state: RUNNING → SUCCEEDED (or FAILED on error).
    Uses synchronous DB access via psycopg2.
    """
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
        logger.error(
            "Test job failed",
            job_id=job_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )
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


# ── OMEGA-002 Mission Engine Worker Tasks ──


@celery_app.task(bind=True, name="omega.tasks.execute")
def execute_task(self, task_id: str) -> dict:
    """Execute a mission task using registered TaskExecutor.

    Atomic transitions:
    QUEUED → RUNNING → SUCCEEDED (or FAILED / READY on retry).
    Uses synchronous psycopg2 session.
    """
    from omega.application.executor import default_executor_registry
    from omega.domain.decision import Actor, DecisionType
    from omega.domain.task import TaskState
    from omega.infrastructure.database_sync import SyncSessionLocal
    from omega.infrastructure.models import DecisionLog, Task

    logger.info("Executing task", task_id=task_id)
    session = SyncSessionLocal()
    parsed_id = uuid.UUID(str(task_id))

    try:
        # Atomic lock & transition QUEUED -> RUNNING
        task = session.query(Task).filter(Task.id == parsed_id).with_for_update().first()
        if not task:
            logger.error("Task not found in database", task_id=task_id)
            return {"status": "error", "message": "Task not found"}

        if task.state != TaskState.QUEUED.value:
            logger.warning(
                "Task not in QUEUED state, skipping execution",
                task_id=task_id,
                current_state=task.state,
            )
            return {"status": "skipped", "task_state": task.state}

        now = datetime.now(UTC)
        task.state = TaskState.RUNNING.value
        task.started_at = now
        task.updated_at = now
        session.commit()

        # Resolve executor
        executor = default_executor_registry.get(task.task_type)
        context = {
            "mission_id": str(task.mission_id),
            "execution_id": str(task.execution_id) if task.execution_id else None,
        }

        # Execute
        result_output = executor.execute(
            task_id=task.id,
            task_type=task.task_type,
            task_input=task.input,
            context=context,
        )

        # Mark SUCCEEDED under lock
        now = datetime.now(UTC)
        task = session.query(Task).filter(Task.id == parsed_id).with_for_update().first()
        if task:
            task.state = TaskState.SUCCEEDED.value
            task.output = result_output
            task.completed_at = now
            task.updated_at = now

            session.add(
                DecisionLog(
                    mission_id=task.mission_id,
                    execution_id=task.execution_id,
                    task_id=task.id,
                    decision_type=DecisionType.TASK_SUCCEEDED.value,
                    decision=f"Task '{task.title}' succeeded",
                    reason="Executor returned successfully",
                    actor=Actor.WORKER.value,
                )
            )
            session.commit()

            logger.info("Task completed successfully", task_id=task_id)

            # Trigger sync orchestrator evaluation in background Celery queue
            evaluate_mission_task.delay(
                str(task.mission_id),
                str(task.execution_id) if task.execution_id else "",
            )

        return {"status": "success", "task_id": task_id}

    except Exception as exc:
        session.rollback()
        sanitized_err = _sanitize_task_error(exc)
        logger.error(
            "Task execution failed",
            task_id=task_id,
            error=sanitized_err,
            exc_info=True,
        )

        try:
            now = datetime.now(UTC)
            task = session.query(Task).filter(Task.id == parsed_id).with_for_update().first()
            if task:
                if task.retry_count < task.max_retries:
                    # Increment retry atomically and transition back to READY
                    task.retry_count += 1
                    task.state = TaskState.READY.value
                    task.error = f"Retry {task.retry_count}/{task.max_retries}: {sanitized_err}"
                    task.updated_at = now

                    session.add(
                        DecisionLog(
                            mission_id=task.mission_id,
                            execution_id=task.execution_id,
                            task_id=task.id,
                            decision_type=DecisionType.TASK_RETRY.value,
                            decision=f"Schedule retry {task.retry_count}/{task.max_retries} for task '{task.title}'",
                            reason=sanitized_err,
                            actor=Actor.WORKER.value,
                        )
                    )
                else:
                    # Retries exhausted -> FAILED
                    task.state = TaskState.FAILED.value
                    task.error = sanitized_err
                    task.completed_at = now
                    task.updated_at = now

                    session.add(
                        DecisionLog(
                            mission_id=task.mission_id,
                            execution_id=task.execution_id,
                            task_id=task.id,
                            decision_type=DecisionType.TASK_FAILED.value,
                            decision=f"Task '{task.title}' permanently failed",
                            reason=f"Max retries exhausted ({task.max_retries}). Error: {sanitized_err}",
                            actor=Actor.WORKER.value,
                        )
                    )

                session.commit()

                # Trigger orchestrator evaluation to handle retry or failure propagation
                evaluate_mission_task.delay(
                    str(task.mission_id),
                    str(task.execution_id) if task.execution_id else "",
                )

        except Exception:
            logger.error("Failed to persist task error state", task_id=task_id, exc_info=True)

        return {"status": "failed", "task_id": task_id, "error": sanitized_err}
    finally:
        session.close()


@celery_app.task(bind=True, name="omega.orchestrator.evaluate")
def evaluate_mission_task(self, mission_id: str, execution_id: str = "") -> dict:
    """Trigger synchronous Orchestrator evaluation from Celery worker queue."""
    from omega.application.orchestrator import evaluate_mission_sync
    from omega.infrastructure.database_sync import SyncSessionLocal

    session = SyncSessionLocal()
    try:
        m_id = uuid.UUID(str(mission_id))
        e_id = uuid.UUID(str(execution_id)) if execution_id else None
        res = evaluate_mission_sync(session, m_id, e_id)
        return res
    except Exception as exc:
        logger.error(
            "Orchestrator sync evaluation task failed", mission_id=mission_id, exc_info=True
        )
        return {"status": "error", "message": _sanitize_task_error(exc)}
    finally:
        session.close()
