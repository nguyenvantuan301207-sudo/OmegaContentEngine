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
    from omega.domain.mission import MissionState
    from omega.domain.task import TaskState
    from omega.infrastructure.database_sync import SyncSessionLocal
    from omega.infrastructure.models import DecisionLog, Mission, Task

    logger.info("Executing task", task_id=task_id)
    session = SyncSessionLocal()
    parsed_id = uuid.UUID(str(task_id))

    try:
        # 1. Inspect mission_id for lock hierarchy
        initial_task = session.query(Task.mission_id).filter(Task.id == parsed_id).first()
        if not initial_task:
            logger.error("Task not found in database", task_id=task_id)
            return {"status": "error", "message": "Task not found"}

        mission_id = initial_task.mission_id

        # 2. Lock Mission FIRST, Task SECOND
        mission = session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
        task = session.query(Task).filter(Task.id == parsed_id).with_for_update().first()

        if not mission or not task:
            logger.error("Mission or Task disappeared under lock", task_id=task_id)
            return {"status": "error", "message": "Record missing under lock"}

        if mission.state != MissionState.RUNNING.value:
            logger.warning(
                "Mission not in RUNNING state, refusing execution",
                task_id=task_id,
                mission_state=mission.state,
            )
            return {"status": "skipped", "reason": "mission_not_running"}

        # Validate fencing token
        if task.dispatched_epoch is not None and task.dispatched_epoch != mission.guardian_epoch:
            logger.warning(
                "Stale task epoch detected. Refusing execution without side effects.",
                task_id=task_id,
                dispatched_epoch=task.dispatched_epoch,
                mission_epoch=mission.guardian_epoch,
            )
            session.add(
                DecisionLog(
                    mission_id=mission.id,
                    execution_id=task.execution_id,
                    task_id=task.id,
                    decision_type=DecisionType.TASK_FAILED.value,
                    decision=f"Worker refused stale task '{task.title}'",
                    reason=f"Epoch mismatch: dispatched={task.dispatched_epoch}, current={mission.guardian_epoch}",
                    actor=Actor.WORKER.value,
                )
            )
            session.commit()
            return {
                "status": "stale_epoch_refused",
                "dispatched_epoch": task.dispatched_epoch,
                "current_epoch": mission.guardian_epoch,
            }

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
        mission = session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
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
            mission = (
                session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
            )
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


@celery_app.task(bind=True, name="omega.production.render")
def execute_production_render_task(
    self,
    channel_id: str,
    request_id: str,
    job_id: str,
) -> dict:
    """Asynchronous background rendering task using ProductionRenderService."""
    import asyncio

    from omega.application.render_service import ProductionRenderService
    from omega.infrastructure.database import async_session_factory

    logger.info(
        "Starting background render task",
        channel_id=channel_id,
        request_id=request_id,
        job_id=job_id,
    )

    async def _run():
        async with async_session_factory() as session:
            service = ProductionRenderService()
            c_id = uuid.UUID(str(channel_id))
            r_id = uuid.UUID(str(request_id))
            j_id = uuid.UUID(str(job_id))
            art, qa_status = await service.execute_render_job(session, c_id, r_id, j_id)
            return {
                "status": "success",
                "artifact_id": str(art.id) if art else None,
                "qa_status": str(qa_status.value),
            }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("Background render task failed", job_id=job_id, exc_info=True)
        return {"status": "failed", "error": _sanitize_task_error(exc)}


@celery_app.task(name="omega.guardian.process_alert_outbox")
def process_guardian_alert_outbox() -> dict[str, int]:
    """Process pending alerts from the transactional guardian outbox."""
    import asyncio

    from omega.application.guardian.outbox import AlertOutboxService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            return await AlertOutboxService.process_outbox_items(session)

    try:
        processed = asyncio.run(_run())
        return {"status": "success", "processed": processed}
    except Exception as exc:
        logger.error("Failed to process guardian alert outbox", error=str(exc), exc_info=True)
        return {"status": "error", "processed": 0}


# ── OMEGA-010 Scheduler Background Worker Tasks ──


@celery_app.task(name="omega.scheduler.dispatch_sweep")
def schedule_dispatch_sweep_task() -> dict[str, int]:
    """Execute periodic dispatch sweep for due ACTIVE reservations."""
    import asyncio

    from omega.application.scheduler.sweep_service import SchedulerSweepService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> dict[str, int]:
        async with AsyncSessionLocal() as session:
            return await SchedulerSweepService.run_dispatch_sweep(session)

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Schedule dispatch sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "claimed": 0, "dispatched": 0, "rejected": 0}


@celery_app.task(name="omega.scheduler.outbox_relay")
def schedule_outbox_relay_task() -> dict[str, int]:
    """Execute periodic outbox relay for pending/retry dispatch outbox items."""
    import asyncio

    from omega.application.scheduler.outbox_relay import OutboxRelayService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> dict[str, int]:
        async with AsyncSessionLocal() as session:
            return await OutboxRelayService.process_outbox_batch(session)

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Schedule outbox relay failed", error=str(exc), exc_info=True)
        return {"status": "error", "claimed": 0, "sent": 0, "retried": 0, "dead_letter": 0}


@celery_app.task(name="omega.scheduler.expiration_sweep")
def schedule_expiration_sweep_task() -> dict[str, int]:
    """Execute periodic expiration sweep for expired ACTIVE reservations."""
    import asyncio

    from omega.application.scheduler.sweep_service import SchedulerSweepService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            return await SchedulerSweepService.run_expiration_sweep(session)

    try:
        expired = asyncio.run(_run())
        return {"status": "success", "expired": expired}
    except Exception as exc:
        logger.error("Schedule expiration sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "expired": 0}


@celery_app.task(name="omega.scheduler.stale_dispatching_sweep")
def schedule_stale_dispatching_sweep_task() -> dict[str, int]:
    """Execute periodic stale DISPATCHING recovery sweep."""
    import asyncio

    from omega.application.scheduler.sweep_service import SchedulerSweepService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> dict[str, int]:
        async with AsyncSessionLocal() as session:
            return await SchedulerSweepService.run_stale_dispatching_recovery_sweep(session)

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Schedule stale dispatch recovery failed", error=str(exc), exc_info=True)
        return {"status": "error", "recovered": 0, "consumed": 0, "released": 0, "requeued": 0}
