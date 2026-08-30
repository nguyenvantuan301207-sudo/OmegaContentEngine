"""Bounded Event-Driven Orchestrator.

Evaluates DAG state, autonomy policies, and manages finite state transitions.
Provides both Async (FastAPI) and Sync (Celery Worker) evaluation entrypoints.
Never runs in an infinite loop. Exits after finite state evaluations and dispatches.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from omega.domain.decision import Actor, DecisionType
from omega.domain.mission import AutonomyLevel, ExecutionState, MissionState
from omega.domain.task import TaskState
from omega.infrastructure.models import DecisionLog, Mission, MissionExecution, Task, TaskDependency
from omega.logging import get_logger

logger = get_logger(service="omega-orchestrator")


def _sanitize_error(error: Exception) -> str:
    """Sanitize error messages to prevent secret or credential leakage."""
    error_type = type(error).__name__
    first_line = str(error).split("\n")[0]
    for pattern in ["://", "password", "secret", "token", "key=", "apikey"]:
        if pattern.lower() in first_line.lower():
            return f"{error_type}: Execution or dispatch error (details redacted)"
    if len(first_line) > 500:
        first_line = first_line[:500] + "..."
    return f"{error_type}: {first_line}"


# ── Async Orchestrator (FastAPI application layer) ──


async def evaluate_mission(
    session: AsyncSession,
    mission_id: uuid.UUID,
    execution_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Async single-pass evaluation of a mission DAG for FastAPI requests."""
    now = datetime.now(UTC)

    # 1. Load mission with row lock
    mission_res = await session.execute(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    mission = mission_res.scalar_one_or_none()
    if not mission:
        logger.error("Mission not found for evaluation", mission_id=str(mission_id))
        return {"status": "error", "message": "Mission not found"}

    if mission.state != MissionState.RUNNING.value:
        logger.info(
            "Skipping orchestrator evaluation — mission not RUNNING",
            mission_id=str(mission_id),
            state=mission.state,
        )
        return {"status": "skipped", "mission_state": mission.state}

    # 2. Load execution
    exec_query = select(MissionExecution).where(MissionExecution.mission_id == mission_id)
    if execution_id:
        exec_query = exec_query.where(MissionExecution.id == execution_id)
    else:
        exec_query = exec_query.where(
            MissionExecution.state.in_([ExecutionState.PLANNED.value, ExecutionState.RUNNING.value])
        ).order_by(MissionExecution.created_at.desc())

    exec_res = await session.execute(exec_query.with_for_update())
    active_execution = exec_res.scalars().first()

    if active_execution and active_execution.state != ExecutionState.RUNNING.value:
        active_execution.state = ExecutionState.RUNNING.value
        active_execution.started_at = active_execution.started_at or now
        active_execution.updated_at = now

    current_execution_id = active_execution.id if active_execution else None

    # 3. Load all tasks and dependencies
    tasks_res = await session.execute(
        select(Task).where(Task.mission_id == mission_id).with_for_update()
    )
    tasks = list(tasks_res.scalars().all())
    task_map: dict[uuid.UUID, Task] = {t.id: t for t in tasks}

    deps_res = await session.execute(
        select(TaskDependency).where(TaskDependency.mission_id == mission_id)
    )
    dependencies = list(deps_res.scalars().all())

    prereqs_map: dict[uuid.UUID, list[uuid.UUID]] = {t.id: [] for t in tasks}
    for dep in dependencies:
        if dep.task_id in prereqs_map:
            prereqs_map[dep.task_id].append(dep.depends_on_task_id)

    # 4. Evaluate task readiness
    for task in tasks:
        prereq_ids = prereqs_map.get(task.id, [])
        prereq_tasks = [task_map[pid] for pid in prereq_ids if pid in task_map]

        all_prereqs_succeeded = (
            all(p.state == TaskState.SUCCEEDED.value for p in prereq_tasks)
            if prereq_tasks
            else True
        )
        any_prereq_failed = any(
            p.state in (TaskState.FAILED.value, TaskState.CANCELLED.value) for p in prereq_tasks
        )

        if task.state in (TaskState.PENDING.value, TaskState.BLOCKED.value):
            if any_prereq_failed:
                task.state = TaskState.CANCELLED.value
                task.updated_at = now
                session.add(
                    DecisionLog(
                        mission_id=mission_id,
                        execution_id=current_execution_id,
                        task_id=task.id,
                        decision_type=DecisionType.TASK_FAILED.value,
                        decision=f"Cancel task {task.title}",
                        reason="Upstream prerequisite task failed or was cancelled",
                        actor=Actor.ORCHESTRATOR.value,
                    )
                )
            elif all_prereqs_succeeded:
                if task.requires_approval:
                    task.state = TaskState.WAITING_APPROVAL.value
                    task.updated_at = now
                    session.add(
                        DecisionLog(
                            mission_id=mission_id,
                            execution_id=current_execution_id,
                            task_id=task.id,
                            decision_type=DecisionType.TASK_APPROVAL_REQUESTED.value,
                            decision=f"Request approval for {task.title}",
                            reason="Prerequisites satisfied; pre-execution gate requires approval",
                            actor=Actor.ORCHESTRATOR.value,
                        )
                    )
                else:
                    task.state = TaskState.READY.value
                    task.updated_at = now

    # Commit initial task readiness and DAG evaluation updates before running decoupled Guardian check
    await session.commit()

    # 5. Determine tasks to dispatch based on Autonomy Level
    tasks_to_dispatch: list[Task] = []
    autonomy = mission.autonomy_level

    ready_tasks = [t for t in tasks if t.state == TaskState.READY.value]
    if ready_tasks:
        # Check Guardian gate at PRE_TASK_DISPATCH
        try:
            from omega.application.guardian.engine import GuardianEngine
            from omega.domain.guardian import (
                CheckTriggerType,
                GuardianAction,
                GuardianCheckCreate,
                GuardianCheckpoint,
            )
            from omega.infrastructure.database import AsyncSessionLocal

            guardian_engine = GuardianEngine(session_factory=AsyncSessionLocal)
            check_res = await guardian_engine.execute_check(
                GuardianCheckCreate(
                    mission_id=mission_id,
                    checkpoint=GuardianCheckpoint.PRE_TASK_DISPATCH,
                    trigger_type=CheckTriggerType.PRE_TASK_DISPATCH,
                    diagnostic_context={"autonomy_level": autonomy},
                )
            )

            if check_res.decision and check_res.decision.action not in (
                GuardianAction.ALLOW,
                GuardianAction.ALLOW_WITH_WARNING,
            ):
                logger.warning(
                    "Guardian gate held at PRE_TASK_DISPATCH",
                    mission_id=str(mission_id),
                    action=check_res.decision.action.value,
                    gate_state=check_res.decision.resulting_gate_state.value,
                    reason=check_res.decision.reason,
                )
                return {
                    "status": "held_by_guardian",
                    "mission_id": str(mission_id),
                    "action": check_res.decision.action.value,
                    "gate_state": check_res.decision.resulting_gate_state.value,
                    "reason": check_res.decision.reason,
                }
        except Exception as exc:
            logger.error(
                "Guardian subsystem unavailable at PRE_TASK_DISPATCH. Holding execution.",
                mission_id=str(mission_id),
                error=str(exc),
                exc_info=True,
            )
            return {
                "status": "waiting_guardian",
                "mission_id": str(mission_id),
                "gate_state": "WAITING_GUARDIAN",
                "reason": "Guardian subsystem unreachable at protected boundary",
            }

    # Re-acquire short lock for task dispatch phase
    mission_res = await session.execute(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    mission = mission_res.scalar_one()
    tasks_res = await session.execute(
        select(Task).where(Task.mission_id == mission_id).with_for_update()
    )
    tasks = list(tasks_res.scalars().all())

    for task in tasks:
        if task.state == TaskState.READY.value:
            should_dispatch = autonomy in (
                AutonomyLevel.SUPERVISED.value,
                AutonomyLevel.ASSISTED.value,
                AutonomyLevel.AUTONOMOUS.value,
                AutonomyLevel.STRATEGIC_AUTONOMOUS.value,
            )

            if should_dispatch:
                task.state = TaskState.QUEUED.value
                task.dispatched_epoch = mission.guardian_epoch
                task.queued_at = now
                task.updated_at = now
                tasks_to_dispatch.append(task)

                session.add(
                    DecisionLog(
                        mission_id=mission_id,
                        execution_id=current_execution_id,
                        task_id=task.id,
                        decision_type=DecisionType.TASK_DISPATCH.value,
                        decision=f"Queue task {task.title}",
                        reason="All dependencies satisfied, eligible for dispatch",
                        actor=Actor.ORCHESTRATOR.value,
                    )
                )

    await session.commit()

    # 6. Dispatch queued tasks to Celery
    from omega.worker.tasks import execute_task

    dispatched_count = 0
    for task in tasks_to_dispatch:
        try:
            execute_task.delay(str(task.id))
            dispatched_count += 1
            logger.info(
                "Dispatched task to worker queue",
                task_id=str(task.id),
                mission_id=str(mission_id),
                task_type=task.task_type,
            )
        except Exception as exc:
            logger.error(
                "Celery broker dispatch failed for task",
                task_id=str(task.id),
                mission_id=str(mission_id),
                exc_info=True,
            )
            failed_task_res = await session.execute(
                select(Task).where(Task.id == task.id).with_for_update()
            )
            failed_task = failed_task_res.scalar_one_or_none()
            if failed_task:
                failed_task.state = TaskState.FAILED.value
                failed_task.error = _sanitize_error(exc)
                failed_task.updated_at = datetime.now(UTC)
                session.add(
                    DecisionLog(
                        mission_id=mission_id,
                        execution_id=current_execution_id,
                        task_id=task.id,
                        decision_type=DecisionType.TASK_FAILED.value,
                        decision=f"Mark task {task.title} FAILED on dispatch failure",
                        reason=f"Broker dispatch exception: {_sanitize_error(exc)}",
                        actor=Actor.ORCHESTRATOR.value,
                    )
                )
                await session.commit()

    # 7. Check overall Mission completion or unrecoverable failure predicates
    fresh_tasks_res = await session.execute(
        select(Task).where(Task.mission_id == mission_id).with_for_update()
    )
    all_current_tasks = list(fresh_tasks_res.scalars().all())

    any_failed = any(
        t.state == TaskState.FAILED.value and t.retry_count >= t.max_retries
        for t in all_current_tasks
    )
    all_succeeded = len(all_current_tasks) > 0 and all(
        t.state == TaskState.SUCCEEDED.value for t in all_current_tasks
    )
    has_active_tasks = any(
        t.state
        in (
            TaskState.RUNNING.value,
            TaskState.QUEUED.value,
            TaskState.READY.value,
            TaskState.WAITING_APPROVAL.value,
            TaskState.PENDING.value,
            TaskState.BLOCKED.value,
        )
        for t in all_current_tasks
    )

    mission_res = await session.execute(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    mission_to_update = mission_res.scalar_one()

    if any_failed:
        mission_to_update.state = MissionState.FAILED.value
        mission_to_update.completed_at = datetime.now(UTC)
        mission_to_update.updated_at = datetime.now(UTC)

        if active_execution:
            active_execution.state = ExecutionState.FAILED.value
            active_execution.completed_at = datetime.now(UTC)
            active_execution.updated_at = datetime.now(UTC)

        session.add(
            DecisionLog(
                mission_id=mission_id,
                execution_id=current_execution_id,
                decision_type=DecisionType.MISSION_FAILED.value,
                decision="Mark mission FAILED",
                reason="One or more required tasks failed permanently without remaining retries",
                actor=Actor.ORCHESTRATOR.value,
            )
        )
        await session.commit()
        logger.info("Mission marked as FAILED", mission_id=str(mission_id))

    elif all_succeeded and not has_active_tasks:
        # Protected boundary: MISSION_TERMINAL Guardian gate
        try:
            from omega.application.guardian.engine import GuardianEngine
            from omega.domain.guardian import (
                CheckTriggerType,
                GuardianAction,
                GuardianCheckCreate,
                GuardianCheckpoint,
            )
            from omega.infrastructure.database import AsyncSessionLocal

            # Commit current transaction to release row locks before Guardian check
            await session.commit()

            guardian_engine = GuardianEngine(session_factory=AsyncSessionLocal)
            terminal_check = await guardian_engine.execute_check(
                GuardianCheckCreate(
                    mission_id=mission_id,
                    checkpoint=GuardianCheckpoint.MISSION_TERMINAL,
                    trigger_type=CheckTriggerType.MISSION_TERMINAL,
                    diagnostic_context={"all_tasks_succeeded": True},
                )
            )

            if terminal_check.decision and terminal_check.decision.action not in (
                GuardianAction.ALLOW,
                GuardianAction.ALLOW_WITH_WARNING,
            ):
                logger.warning(
                    "Guardian gate held at MISSION_TERMINAL",
                    mission_id=str(mission_id),
                    action=terminal_check.decision.action.value,
                    gate_state=terminal_check.decision.resulting_gate_state.value,
                    reason=terminal_check.decision.reason,
                )
                return {
                    "status": "held_by_guardian",
                    "mission_id": str(mission_id),
                    "action": terminal_check.decision.action.value,
                    "gate_state": terminal_check.decision.resulting_gate_state.value,
                    "reason": terminal_check.decision.reason,
                }
        except Exception as exc:
            logger.error(
                "Guardian subsystem unavailable at MISSION_TERMINAL. Holding terminal transition.",
                mission_id=str(mission_id),
                error=str(exc),
                exc_info=True,
            )
            return {
                "status": "waiting_guardian",
                "mission_id": str(mission_id),
                "gate_state": "WAITING_GUARDIAN",
                "reason": "Guardian subsystem unreachable at terminal protected boundary",
            }

        # Re-acquire lock to perform terminal state transition
        m_term_res = await session.execute(
            select(Mission).where(Mission.id == mission_id).with_for_update()
        )
        mission_to_update = m_term_res.scalar_one()

        mission_to_update.state = MissionState.SUCCEEDED.value
        mission_to_update.completed_at = datetime.now(UTC)
        mission_to_update.updated_at = datetime.now(UTC)

        if active_execution:
            exec_res = await session.execute(
                select(MissionExecution)
                .where(MissionExecution.id == active_execution.id)
                .with_for_update()
            )
            active_exec_locked = exec_res.scalar_one_or_none()
            if active_exec_locked:
                active_exec_locked.state = ExecutionState.SUCCEEDED.value
                active_exec_locked.completed_at = datetime.now(UTC)
                active_exec_locked.updated_at = datetime.now(UTC)

        session.add(
            DecisionLog(
                mission_id=mission_id,
                execution_id=current_execution_id,
                decision_type=DecisionType.MISSION_COMPLETE.value,
                decision="Mark mission SUCCEEDED",
                reason="All DAG tasks completed and passed MISSION_TERMINAL Guardian check",
                actor=Actor.ORCHESTRATOR.value,
            )
        )
        await session.commit()
        logger.info("Mission marked as SUCCEEDED", mission_id=str(mission_id))

    return {
        "status": "evaluated",
        "mission_id": str(mission_id),
        "mission_state": mission_to_update.state,
        "dispatched_tasks_count": dispatched_count,
    }


# ── Sync Orchestrator (Celery Worker layer via psycopg2) ──


def evaluate_mission_sync(
    session: Session,
    mission_id: uuid.UUID,
    execution_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Synchronous single-pass evaluation for Celery tasks.

    Eliminates all async event loop hacking inside Celery workers.
    """
    now = datetime.now(UTC)

    # 1. Load mission with row lock
    mission = session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
    if not mission:
        logger.error("Mission not found for sync evaluation", mission_id=str(mission_id))
        return {"status": "error", "message": "Mission not found"}

    if mission.state != MissionState.RUNNING.value:
        return {"status": "skipped", "mission_state": mission.state}

    # 2. Load execution
    exec_q = session.query(MissionExecution).filter(MissionExecution.mission_id == mission_id)
    if execution_id:
        exec_q = exec_q.filter(MissionExecution.id == execution_id)
    else:
        exec_q = exec_q.filter(
            MissionExecution.state.in_([ExecutionState.PLANNED.value, ExecutionState.RUNNING.value])
        ).order_by(MissionExecution.created_at.desc())

    active_execution = exec_q.with_for_update().first()
    if active_execution and active_execution.state != ExecutionState.RUNNING.value:
        active_execution.state = ExecutionState.RUNNING.value
        active_execution.started_at = active_execution.started_at or now
        active_execution.updated_at = now

    current_execution_id = active_execution.id if active_execution else None

    # 3. Load tasks and dependencies
    tasks = session.query(Task).filter(Task.mission_id == mission_id).with_for_update().all()
    task_map: dict[uuid.UUID, Task] = {t.id: t for t in tasks}

    dependencies = (
        session.query(TaskDependency).filter(TaskDependency.mission_id == mission_id).all()
    )
    prereqs_map: dict[uuid.UUID, list[uuid.UUID]] = {t.id: [] for t in tasks}
    for dep in dependencies:
        if dep.task_id in prereqs_map:
            prereqs_map[dep.task_id].append(dep.depends_on_task_id)

    # 4. Evaluate task readiness
    for task in tasks:
        prereq_ids = prereqs_map.get(task.id, [])
        prereq_tasks = [task_map[pid] for pid in prereq_ids if pid in task_map]

        all_prereqs_succeeded = (
            all(p.state == TaskState.SUCCEEDED.value for p in prereq_tasks)
            if prereq_tasks
            else True
        )
        any_prereq_failed = any(
            p.state in (TaskState.FAILED.value, TaskState.CANCELLED.value) for p in prereq_tasks
        )

        if task.state in (TaskState.PENDING.value, TaskState.BLOCKED.value):
            if any_prereq_failed:
                task.state = TaskState.CANCELLED.value
                task.updated_at = now
                session.add(
                    DecisionLog(
                        mission_id=mission_id,
                        execution_id=current_execution_id,
                        task_id=task.id,
                        decision_type=DecisionType.TASK_FAILED.value,
                        decision=f"Cancel task {task.title}",
                        reason="Upstream prerequisite task failed or was cancelled",
                        actor=Actor.ORCHESTRATOR.value,
                    )
                )
            elif all_prereqs_succeeded:
                if task.requires_approval:
                    task.state = TaskState.WAITING_APPROVAL.value
                    task.updated_at = now
                    session.add(
                        DecisionLog(
                            mission_id=mission_id,
                            execution_id=current_execution_id,
                            task_id=task.id,
                            decision_type=DecisionType.TASK_APPROVAL_REQUESTED.value,
                            decision=f"Request approval for {task.title}",
                            reason="Prerequisites satisfied; pre-execution gate requires approval",
                            actor=Actor.ORCHESTRATOR.value,
                        )
                    )
                else:
                    task.state = TaskState.READY.value
                    task.updated_at = now

    # Commit initial task readiness and DAG evaluation updates before running decoupled Guardian check
    session.commit()

    # 5. Determine tasks to dispatch
    tasks_to_dispatch: list[Task] = []
    autonomy = mission.autonomy_level

    ready_tasks = [t for t in tasks if t.state == TaskState.READY.value]
    if ready_tasks:
        try:
            import asyncio

            from omega.application.guardian.engine import GuardianEngine
            from omega.domain.guardian import (
                CheckTriggerType,
                GuardianAction,
                GuardianCheckCreate,
                GuardianCheckpoint,
            )

            async def _run_sync_guardian_check() -> Any:
                from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
                from sqlalchemy.pool import NullPool

                from omega.config import get_settings

                settings = get_settings()
                temp_engine = create_async_engine(settings.database_url, poolclass=NullPool)
                temp_session_factory = async_sessionmaker(temp_engine, expire_on_commit=False)
                try:
                    engine = GuardianEngine(session_factory=temp_session_factory)
                    return await engine.execute_check(
                        GuardianCheckCreate(
                            mission_id=mission_id,
                            checkpoint=GuardianCheckpoint.PRE_TASK_DISPATCH,
                            trigger_type=CheckTriggerType.PRE_TASK_DISPATCH,
                            diagnostic_context={"autonomy_level": autonomy, "sync_worker": True},
                        )
                    )
                finally:
                    await temp_engine.dispose()

            check_res = asyncio.run(_run_sync_guardian_check())
            if check_res.decision and check_res.decision.action not in (
                GuardianAction.ALLOW,
                GuardianAction.ALLOW_WITH_WARNING,
            ):
                logger.warning(
                    "Guardian gate held at PRE_TASK_DISPATCH (sync)",
                    mission_id=str(mission_id),
                    action=check_res.decision.action.value,
                    gate_state=check_res.decision.resulting_gate_state.value,
                    reason=check_res.decision.reason,
                )
                return {
                    "status": "held_by_guardian",
                    "mission_id": str(mission_id),
                    "action": check_res.decision.action.value,
                    "gate_state": check_res.decision.resulting_gate_state.value,
                    "reason": check_res.decision.reason,
                }
        except Exception as exc:
            logger.error(
                "Guardian subsystem unavailable at PRE_TASK_DISPATCH (sync). Holding execution.",
                mission_id=str(mission_id),
                error=str(exc),
                exc_info=True,
            )
            return {
                "status": "waiting_guardian",
                "mission_id": str(mission_id),
                "gate_state": "WAITING_GUARDIAN",
                "reason": "Guardian subsystem unreachable at protected boundary",
            }

    # Re-acquire lock for task dispatch phase
    mission = session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
    tasks = session.query(Task).filter(Task.mission_id == mission_id).with_for_update().all()

    for task in tasks:
        if task.state == TaskState.READY.value:
            should_dispatch = autonomy in (
                AutonomyLevel.SUPERVISED.value,
                AutonomyLevel.ASSISTED.value,
                AutonomyLevel.AUTONOMOUS.value,
                AutonomyLevel.STRATEGIC_AUTONOMOUS.value,
            )

            if should_dispatch:
                task.state = TaskState.QUEUED.value
                task.dispatched_epoch = mission.guardian_epoch
                task.queued_at = now
                task.updated_at = now
                tasks_to_dispatch.append(task)

                session.add(
                    DecisionLog(
                        mission_id=mission_id,
                        execution_id=current_execution_id,
                        task_id=task.id,
                        decision_type=DecisionType.TASK_DISPATCH.value,
                        decision=f"Queue task {task.title}",
                        reason="All dependencies satisfied, eligible for dispatch",
                        actor=Actor.ORCHESTRATOR.value,
                    )
                )

    session.commit()

    # 6. Dispatch queued tasks to Celery
    from omega.worker.tasks import execute_task

    dispatched_count = 0
    for task in tasks_to_dispatch:
        try:
            execute_task.delay(str(task.id))
            dispatched_count += 1
            logger.info("Dispatched task to worker queue (sync)", task_id=str(task.id))
        except Exception as exc:
            logger.error(
                "Celery broker dispatch failed for task (sync)", task_id=str(task.id), exc_info=True
            )
            failed_task = session.query(Task).filter(Task.id == task.id).with_for_update().first()
            if failed_task:
                failed_task.state = TaskState.FAILED.value
                failed_task.error = _sanitize_error(exc)
                failed_task.updated_at = datetime.now(UTC)
                session.add(
                    DecisionLog(
                        mission_id=mission_id,
                        execution_id=current_execution_id,
                        task_id=task.id,
                        decision_type=DecisionType.TASK_FAILED.value,
                        decision=f"Mark task {task.title} FAILED on dispatch failure",
                        reason=f"Broker dispatch exception: {_sanitize_error(exc)}",
                        actor=Actor.ORCHESTRATOR.value,
                    )
                )
                session.commit()

    # 7. Check mission completion or failure predicates
    all_current_tasks = (
        session.query(Task).filter(Task.mission_id == mission_id).with_for_update().all()
    )
    any_failed = any(
        t.state == TaskState.FAILED.value and t.retry_count >= t.max_retries
        for t in all_current_tasks
    )
    all_succeeded = len(all_current_tasks) > 0 and all(
        t.state == TaskState.SUCCEEDED.value for t in all_current_tasks
    )
    has_active_tasks = any(
        t.state
        in (
            TaskState.RUNNING.value,
            TaskState.QUEUED.value,
            TaskState.READY.value,
            TaskState.WAITING_APPROVAL.value,
            TaskState.PENDING.value,
            TaskState.BLOCKED.value,
        )
        for t in all_current_tasks
    )

    mission_to_update = (
        session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
    )

    if any_failed:
        mission_to_update.state = MissionState.FAILED.value
        mission_to_update.completed_at = datetime.now(UTC)
        mission_to_update.updated_at = datetime.now(UTC)

        if active_execution:
            active_execution.state = ExecutionState.FAILED.value
            active_execution.completed_at = datetime.now(UTC)
            active_execution.updated_at = datetime.now(UTC)

        session.add(
            DecisionLog(
                mission_id=mission_id,
                execution_id=current_execution_id,
                decision_type=DecisionType.MISSION_FAILED.value,
                decision="Mark mission FAILED",
                reason="One or more required tasks failed permanently without remaining retries",
                actor=Actor.ORCHESTRATOR.value,
            )
        )
        session.commit()
        logger.info("Mission marked as FAILED (sync)", mission_id=str(mission_id))

    elif all_succeeded and not has_active_tasks:
        try:
            import asyncio

            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
            from sqlalchemy.pool import NullPool

            from omega.application.guardian.engine import GuardianEngine
            from omega.config import get_settings
            from omega.domain.guardian import (
                CheckTriggerType,
                GuardianAction,
                GuardianCheckCreate,
                GuardianCheckpoint,
            )

            session.commit()

            async def _run_sync_terminal_guardian_check() -> Any:
                settings = get_settings()
                temp_engine = create_async_engine(settings.database_url, poolclass=NullPool)
                temp_session_factory = async_sessionmaker(temp_engine, expire_on_commit=False)
                try:
                    engine = GuardianEngine(session_factory=temp_session_factory)
                    return await engine.execute_check(
                        GuardianCheckCreate(
                            mission_id=mission_id,
                            checkpoint=GuardianCheckpoint.MISSION_TERMINAL,
                            trigger_type=CheckTriggerType.MISSION_TERMINAL,
                            diagnostic_context={"all_tasks_succeeded": True, "sync_worker": True},
                        )
                    )
                finally:
                    await temp_engine.dispose()

            terminal_check = asyncio.run(_run_sync_terminal_guardian_check())
            if terminal_check.decision and terminal_check.decision.action not in (
                GuardianAction.ALLOW,
                GuardianAction.ALLOW_WITH_WARNING,
            ):
                logger.warning(
                    "Guardian gate held at MISSION_TERMINAL (sync)",
                    mission_id=str(mission_id),
                    action=terminal_check.decision.action.value,
                    gate_state=terminal_check.decision.resulting_gate_state.value,
                    reason=terminal_check.decision.reason,
                )
                return {
                    "status": "held_by_guardian",
                    "mission_id": str(mission_id),
                    "action": terminal_check.decision.action.value,
                    "gate_state": terminal_check.decision.resulting_gate_state.value,
                    "reason": terminal_check.decision.reason,
                }
        except Exception as exc:
            logger.error(
                "Guardian subsystem unavailable at MISSION_TERMINAL (sync). Holding terminal transition.",
                mission_id=str(mission_id),
                error=str(exc),
                exc_info=True,
            )
            return {
                "status": "waiting_guardian",
                "mission_id": str(mission_id),
                "gate_state": "WAITING_GUARDIAN",
                "reason": "Guardian subsystem unreachable at terminal protected boundary",
            }

        # Re-acquire lock to perform terminal state transition
        mission_to_update = (
            session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
        )

        mission_to_update.state = MissionState.SUCCEEDED.value
        mission_to_update.completed_at = datetime.now(UTC)
        mission_to_update.updated_at = datetime.now(UTC)

        if active_execution:
            active_exec_locked = (
                session.query(MissionExecution)
                .filter(MissionExecution.id == active_execution.id)
                .with_for_update()
                .first()
            )
            if active_exec_locked:
                active_exec_locked.state = ExecutionState.SUCCEEDED.value
                active_exec_locked.completed_at = datetime.now(UTC)
                active_exec_locked.updated_at = datetime.now(UTC)

        session.add(
            DecisionLog(
                mission_id=mission_id,
                execution_id=current_execution_id,
                decision_type=DecisionType.MISSION_COMPLETE.value,
                decision="Mark mission SUCCEEDED",
                reason="All DAG tasks completed and passed MISSION_TERMINAL Guardian check",
                actor=Actor.ORCHESTRATOR.value,
            )
        )
        session.commit()
        logger.info("Mission marked as SUCCEEDED (sync)", mission_id=str(mission_id))

    return {
        "status": "evaluated",
        "mission_id": str(mission_id),
        "mission_state": mission_to_update.state,
        "dispatched_tasks_count": dispatched_count,
    }
