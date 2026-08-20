"""Integration tests for Task API and pre-execution approval/rejection gates."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.task import TaskState
from omega.infrastructure.models import Mission, Task
from omega.main import app


@pytest.mark.asyncio
async def test_task_approval_gate_flow(db_session: AsyncSession) -> None:
    """Test pre-execution approval gate: WAITING_APPROVAL -> READY on approve."""
    # Seed mission and a waiting approval task
    mission = Mission(
        id=uuid.uuid4(),
        title="Approval Test Mission",
        objective="Verify approval gating",
        state="RUNNING",
    )
    db_session.add(mission)
    await db_session.flush()

    task = Task(
        id=uuid.uuid4(),
        mission_id=mission.id,
        task_type="qa",
        title="QA Sign-off",
        state=TaskState.WAITING_APPROVAL.value,
        requires_approval=True,
    )
    db_session.add(task)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Get task details
        res = await client.get(f"/api/v1/tasks/{task.id}")
        assert res.status_code == 200
        assert res.json()["state"] == "WAITING_APPROVAL"

        # Approve task: WAITING_APPROVAL -> READY
        res = await client.post(f"/api/v1/tasks/{task.id}/approve")
        assert res.status_code == 200
        approved_task = res.json()
        # Pre-execution gate unblocks to READY (or queued by orchestrator)
        assert approved_task["state"] in ("READY", "QUEUED")


@pytest.mark.asyncio
async def test_task_rejection_flow(db_session: AsyncSession) -> None:
    """Test pre-execution rejection: WAITING_APPROVAL -> FAILED on reject."""
    mission = Mission(
        id=uuid.uuid4(),
        title="Rejection Test Mission",
        objective="Verify rejection gating",
        state="RUNNING",
    )
    db_session.add(mission)
    await db_session.flush()

    task = Task(
        id=uuid.uuid4(),
        mission_id=mission.id,
        task_type="qa",
        title="QA Editorial Check",
        state=TaskState.WAITING_APPROVAL.value,
        requires_approval=True,
    )
    db_session.add(task)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Reject task: WAITING_APPROVAL -> FAILED
        res = await client.post(
            f"/api/v1/tasks/{task.id}/reject",
            json={"reason": "Brand tone guidelines violated"},
        )
        assert res.status_code == 200
        rejected_task = res.json()
        assert rejected_task["state"] == "FAILED"
        assert "Brand tone guidelines violated" in rejected_task["error"]


@pytest.mark.asyncio
async def test_invalid_approval_on_non_waiting_task(db_session: AsyncSession) -> None:
    """Approving a task that is not in WAITING_APPROVAL must fail with 400 Bad Request."""
    mission = Mission(
        id=uuid.uuid4(),
        title="Invalid Approval Test",
        objective="Verify state guards",
        state="RUNNING",
    )
    db_session.add(mission)
    await db_session.flush()

    task = Task(
        id=uuid.uuid4(),
        mission_id=mission.id,
        task_type="strategy",
        title="Strategy Setup",
        state=TaskState.PENDING.value,
        requires_approval=False,
    )
    db_session.add(task)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.post(f"/api/v1/tasks/{task.id}/approve")
        assert res.status_code == 400
        assert "WAITING_APPROVAL" in res.json()["detail"]
