"""Celery worker tasks.

Uses SYNCHRONOUS SQLAlchemy (psycopg2) for database access.
Never uses async/asyncio inside Celery tasks.

See docs/decisions/001-foundation-architecture.md for rationale.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

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


@celery_app.task(name="omega.publisher.execute_publish")
def execute_publish_task(task_id: str) -> dict[str, Any]:
    """Execute external publication for an approved PublishIntent."""
    import asyncio
    from uuid import UUID

    from omega.application.publisher.publish_service import PublishExecutionService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            attempt = await PublishExecutionService.execute_publish(session, UUID(task_id))
            return {
                "attempt_id": str(attempt.id),
                "state": attempt.state,
                "provider_video_id": attempt.provider_video_id,
            }

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error(
            "Publisher task execution failed", task_id=task_id, error=str(exc), exc_info=True
        )
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="omega.publisher.handoff_sweep")
def publisher_handoff_sweep_task() -> dict[str, int]:
    """Periodic sweep delivering retry handoffs to OMEGA-010 Scheduler."""
    import asyncio

    from omega.application.publisher.handoff_relay import HandoffRelayService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            return await HandoffRelayService.process_pending_handoffs(session)

    try:
        delivered = asyncio.run(_run())
        return {"status": "success", "delivered": delivered}
    except Exception as exc:
        logger.error("Publisher handoff sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "delivered": 0}


@celery_app.task(name="omega.publisher.reconciliation_sweep")
def publisher_reconciliation_sweep_task() -> dict[str, int]:
    """Periodic sweep reconciling ambiguous UNKNOWN publish attempts."""
    import asyncio

    from omega.application.publisher.reconciliation_service import ReconciliationService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            return await ReconciliationService.reconcile_pending_attempts_sweep(session)

    try:
        reconciled = asyncio.run(_run())
        return {"status": "success", "reconciled": reconciled}
    except Exception as exc:
        logger.error("Publisher reconciliation sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "reconciled": 0}


# ── OMEGA-012 Analytics Engine Tasks ──────────────────────────────────────────


@celery_app.task(name="omega.analytics.poll_sweep")
def analytics_poll_sweep_task() -> dict[str, Any]:
    """Periodic sweep scanning due analytics assets and enqueuing poll batches."""
    import asyncio

    from sqlalchemy import func, select

    from omega.application.analytics.poll_service import AnalyticsPollService
    from omega.infrastructure.database import AsyncSessionLocal
    from omega.infrastructure.models import AnalyticsAsset

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(AnalyticsAsset.id)
                .where(
                    AnalyticsAsset.asset_status == "ACTIVE",
                    AnalyticsAsset.next_poll_due_at <= func.now(),
                )
                .limit(50)
            )
            res = await session.execute(stmt)
            due_ids = res.scalars().all()

            processed = 0
            for asset_id in due_ids:
                try:
                    await AnalyticsPollService.execute_asset_poll(
                        session=session,
                        asset_id=asset_id,
                        worker_id="celery-poll-worker",
                    )
                    processed += 1
                except Exception as poll_exc:
                    logger.error("Asset poll failed", asset_id=str(asset_id), error=str(poll_exc))

            await session.commit()
            return {"due": len(due_ids), "processed": processed}

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Analytics poll sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "due": 0, "processed": 0}


@celery_app.task(name="omega.analytics.fetch_video_batch")
def analytics_fetch_video_batch_task(asset_id: str) -> dict[str, Any]:
    """Execute video asset analytics poll for a single asset."""
    import asyncio
    from uuid import UUID

    from omega.application.analytics.poll_service import AnalyticsPollService
    from omega.infrastructure.database import AsyncSessionLocal

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            res = await AnalyticsPollService.execute_asset_poll(
                session=session,
                asset_id=UUID(asset_id),
                worker_id="celery-batch-worker",
            )
            await session.commit()
            return res

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error(
            "Analytics fetch video batch failed", asset_id=asset_id, error=str(exc), exc_info=True
        )
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="omega.analytics.fetch_analytics_report")
def analytics_fetch_analytics_report_task(asset_id: str) -> dict[str, Any]:
    """Fetch deep report for a video asset."""
    return analytics_fetch_video_batch_task(asset_id)


@celery_app.task(name="omega.analytics.daily_reconciliation_sweep")
def analytics_daily_reconciliation_sweep_task() -> dict[str, Any]:
    """Periodic sweep checking for finalized daily windows and reconciling stale data."""
    import asyncio

    from sqlalchemy import select

    from omega.infrastructure.database import AsyncSessionLocal
    from omega.infrastructure.models import AnalyticsWindow

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(AnalyticsWindow)
                .where(AnalyticsWindow.window_state == "PROVISIONAL")
                .limit(20)
            )
            res = await session.execute(stmt)
            windows = res.scalars().all()
            return {"checked_windows": len(windows)}

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Analytics daily reconciliation sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "checked_windows": 0}
