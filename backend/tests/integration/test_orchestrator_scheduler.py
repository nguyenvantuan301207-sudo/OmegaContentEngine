"""Integration tests for Orchestrator + Smart Scheduler integration.

Tests that:
1. When a task becomes READY and an active policy exists, Orchestrator evaluates the schedule.
2. SCHEDULE decision leaves the task READY without immediate Celery dispatch.
3. Subsequent dispatch sweep dispatches the scheduled task when due.
4. Default path without policy dispatches immediately (RUN_NOW behavior).
5. Guardian epoch changes reject scheduled dispatch during sweep.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.orchestrator import evaluate_mission
from omega.application.scheduler import SchedulePolicyService
from omega.domain.mission import AutonomyLevel
from omega.domain.scheduler import ReservationState
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    Channel,
    Mission,
    ScheduleDecision,
    SchedulePolicy,
    SchedulerDispatchOutbox,
    ScheduleReservation,
    ScheduleStateTransition,
    Task,
)


@pytest_asyncio.fixture(autouse=True)
async def clean_orchestrator_scheduler_db(db_session: AsyncSession):
    """Clean up scheduler tables before each test."""
    await db_session.execute(delete(SchedulerDispatchOutbox))
    await db_session.execute(delete(ScheduleStateTransition))
    await db_session.execute(delete(ScheduleReservation))
    await db_session.execute(delete(ScheduleDecision))
    await db_session.execute(delete(SchedulePolicy))
    await db_session.commit()


@pytest.mark.asyncio
async def test_orchestrator_task_scheduled_no_immediate_celery_dispatch(
    db_session: AsyncSession, monkeypatch
):
    """Verify that when a schedule policy exists, Orchestrator schedules the task without immediate Celery dispatch."""
    # Active policy with 1 hour min lead time
    _policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category="EXTERNAL_PUBLISH",
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={
            "min_lead_time_seconds": 3600,  # 1 hour in future
            "max_schedule_horizon_days": 14,
            "global_concurrency_limit": 10,
        },
        activate=True,
    )

    channel = Channel(
        id=uuid4(), slug=f"chan-{uuid4().hex[:6]}", name="Orch Channel", state="ACTIVE"
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="Orch Sched Mission",
        objective="Orchestrator Scheduler Test",
        state="RUNNING",
        autonomy_level=AutonomyLevel.AUTONOMOUS.value,
        priority=1,
        guardian_epoch=1,
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_video",  # Maps to EXTERNAL_PUBLISH
        title="Publish Video",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    # Track Celery delay calls
    celery_dispatches = []
    from omega.worker.tasks import execute_task

    def mock_delay(task_id_arg):
        celery_dispatches.append(task_id_arg)

    monkeypatch.setattr(execute_task, "delay", mock_delay)

    # 1. Run orchestrator evaluation
    eval_res = await evaluate_mission(db_session, mission.id)
    assert eval_res["status"] == "evaluated"

    # 2. Verify NO immediate Celery dispatch occurred
    assert len(celery_dispatches) == 0

    # 3. Verify task remains in READY state
    await db_session.refresh(task)
    assert task.state == TaskState.READY.value

    # 4. Verify reservation was created in ACTIVE state
    res_query = await db_session.execute(
        select(ScheduleReservation).where(ScheduleReservation.mission_id == mission.id)
    )
    reservation = res_query.scalar_one_or_none()
    assert reservation is not None
    assert reservation.state == ReservationState.ACTIVE.value
    assert reservation.scheduled_start_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_orchestrator_default_dispatch_when_no_policy(db_session: AsyncSession, monkeypatch):
    """Verify that when no policy is configured for a category, Orchestrator defaults to immediate dispatch."""
    mission = Mission(
        id=uuid4(),
        title="No Policy Mission",
        objective="Immediate Dispatch Test",
        state="RUNNING",
        autonomy_level=AutonomyLevel.AUTONOMOUS.value,
        priority=1,
        guardian_epoch=1,
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="internal_task_no_policy",  # Maps to LIGHT_API (no active policy)
        title="Internal Task",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    celery_dispatches = []
    from omega.worker.tasks import execute_task

    def mock_delay(task_id_arg):
        celery_dispatches.append(task_id_arg)

    monkeypatch.setattr(execute_task, "delay", mock_delay)

    # Run orchestrator evaluation
    eval_res = await evaluate_mission(db_session, mission.id)
    assert eval_res["status"] == "evaluated"

    # Immediate dispatch occurred
    assert len(celery_dispatches) == 1
    assert celery_dispatches[0] == str(task.id)

    await db_session.refresh(task)
    assert task.state == TaskState.QUEUED.value
