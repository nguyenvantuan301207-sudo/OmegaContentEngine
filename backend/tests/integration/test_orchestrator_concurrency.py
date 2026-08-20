"""Integration tests for Orchestrator idempotency, retry bounds, and dispatch failure safety."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.orchestrator import evaluate_mission
from omega.domain.task import TaskState
from omega.infrastructure.models import Mission, MissionExecution, Task


@pytest.mark.asyncio
async def test_orchestrator_idempotent_dispatch(db_session: AsyncSession) -> None:
    """Duplicate orchestrator invocations must NOT double-dispatch tasks."""
    mission_id = uuid.uuid4()
    exec_id = uuid.uuid4()

    mission = Mission(
        id=mission_id,
        title="Idempotency Mission",
        objective="Verify single dispatch",
        state="RUNNING",
        autonomy_level="SUPERVISED",
    )
    execution = MissionExecution(
        id=exec_id,
        mission_id=mission_id,
        state="RUNNING",
    )
    task = Task(
        id=uuid.uuid4(),
        mission_id=mission_id,
        execution_id=exec_id,
        task_type="system.noop",
        title="Single Dispatch Task",
        state=TaskState.READY.value,
        requires_approval=False,
    )
    db_session.add_all([mission, execution, task])
    await db_session.commit()

    dispatched_calls: list[str] = []

    def mock_delay(task_id_str: str) -> None:
        dispatched_calls.append(task_id_str)

    with patch("omega.worker.tasks.execute_task.delay", side_effect=mock_delay):
        # 1st evaluation -> transitions READY to QUEUED, dispatches 1 task
        res1 = await evaluate_mission(db_session, mission_id, exec_id)
        assert res1["dispatched_tasks_count"] == 1
        assert len(dispatched_calls) == 1

        # 2nd evaluation (duplicate trigger) -> task is already QUEUED, does not dispatch again!
        res2 = await evaluate_mission(db_session, mission_id, exec_id)
        assert res2["dispatched_tasks_count"] == 0
        assert len(dispatched_calls) == 1


@pytest.mark.asyncio
async def test_dispatch_failure_safety(db_session: AsyncSession) -> None:
    """If Celery broker dispatch fails, task must NOT stay QUEUED; must transition to FAILED with sanitized error."""
    mission_id = uuid.uuid4()
    exec_id = uuid.uuid4()
    task_id = uuid.uuid4()

    mission = Mission(
        id=mission_id,
        title="Broker Failure Mission",
        objective="Verify broker failure handling",
        state="RUNNING",
        autonomy_level="SUPERVISED",
    )
    execution = MissionExecution(
        id=exec_id,
        mission_id=mission_id,
        state="RUNNING",
    )
    task = Task(
        id=task_id,
        mission_id=mission_id,
        execution_id=exec_id,
        task_type="system.noop",
        title="Broker Fail Task",
        state=TaskState.READY.value,
        requires_approval=False,
    )
    db_session.add_all([mission, execution, task])
    await db_session.commit()

    # Simulate broker connection failure with potential secret in exception
    def mock_broken_delay(tid: str) -> None:
        raise ConnectionError("Cannot connect to redis://default:secret_password@redis:6379/0")

    with patch("omega.worker.tasks.execute_task.delay", side_effect=mock_broken_delay):
        await evaluate_mission(db_session, mission_id, exec_id)

    # Task should be marked FAILED, error must be sanitized (no secret password)
    fresh_task_res = await db_session.execute(select(Task).where(Task.id == task_id))
    failed_task = fresh_task_res.scalar_one()

    assert failed_task.state == TaskState.FAILED.value
    assert "secret_password" not in failed_task.error
    assert "ConnectionError" in failed_task.error
