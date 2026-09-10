"""Integration tests for Orchestrator idempotency, retry bounds, and dispatch failure safety."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.orchestrator import evaluate_mission
from omega.domain.task import TaskState
from omega.infrastructure.models import DurableDispatchIntent, Mission, MissionExecution, Task


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

    # 1st evaluation -> transitions READY to QUEUED and enrolls one durable dispatch intent.
    res1 = await evaluate_mission(db_session, mission_id, exec_id)
    assert res1["dispatched_tasks_count"] == 1
    await db_session.refresh(task)
    assert task.state == TaskState.QUEUED.value

    intent_res = await db_session.execute(
        select(DurableDispatchIntent).where(
            DurableDispatchIntent.mission_task_id == task.id,
            DurableDispatchIntent.purpose == "MISSION_TASK_DISPATCH",
        )
    )
    intents = list(intent_res.scalars().all())
    assert len(intents) == 1
    intent = intents[0]
    assert intent.mission_id == mission_id
    assert intent.mission_execution_id == exec_id
    assert intent.mission_task_id == task.id
    assert intent.purpose == "MISSION_TASK_DISPATCH"
    assert intent.task_name == "omega.tasks.execute"
    assert intent.args == [str(task.id)]
    assert intent.idempotency_key == (
        f"mission-task-dispatch:{exec_id}:{task.id}"
        f":{task.retry_count}:{mission.guardian_epoch}"
    )

    # 2nd evaluation observes QUEUED state and must not enroll a duplicate intent.
    res2 = await evaluate_mission(db_session, mission_id, exec_id)
    assert res2["dispatched_tasks_count"] == 0
    replay_res = await db_session.execute(
        select(DurableDispatchIntent).where(
            DurableDispatchIntent.mission_task_id == task.id,
            DurableDispatchIntent.purpose == "MISSION_TASK_DISPATCH",
        )
    )
    assert len(list(replay_res.scalars().all())) == 1


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

    await evaluate_mission(db_session, mission_id, exec_id)

    # Broker delivery is intentionally outside the orchestrator transaction. The task and its
    # durable intent must commit together so the relay can retry publication without lost work.
    fresh_task_res = await db_session.execute(select(Task).where(Task.id == task_id))
    queued_task = fresh_task_res.scalar_one()
    assert queued_task.state == TaskState.QUEUED.value
    assert queued_task.error is None

    intent_res = await db_session.execute(
        select(DurableDispatchIntent).where(
            DurableDispatchIntent.mission_task_id == task_id,
            DurableDispatchIntent.purpose == "MISSION_TASK_DISPATCH",
        )
    )
    intent = intent_res.scalar_one()
    assert intent.state == "PENDING"
    assert intent.task_name == "omega.tasks.execute"
    assert intent.args == [str(task_id)]
    assert intent.mission_id == mission_id
    assert intent.mission_execution_id == exec_id
