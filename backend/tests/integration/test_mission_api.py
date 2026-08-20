"""Integration tests for Missions REST API and execution ownership."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.models import MissionExecution, Task
from omega.main import app


@pytest.mark.asyncio
async def test_mission_crud_and_lifecycle_flow(db_session: AsyncSession) -> None:
    """Test full mission CRUD and lifecycle state machine transitions via API."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Create Mission (DRAFT)
        res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Autonomous Tech Briefing",
                "objective": "Produce daily analysis on AI developments",
                "autonomy_level": "SUPERVISED",
                "priority": 1,
            },
        )
        assert res.status_code == 201
        m_data = res.json()
        mission_id = m_data["id"]
        assert m_data["state"] == "DRAFT"
        assert m_data["title"] == "Autonomous Tech Briefing"

        # 2. Get Mission
        res = await client.get(f"/api/v1/missions/{mission_id}")
        assert res.status_code == 200
        assert res.json()["id"] == mission_id

        # 3. Update Draft Mission
        res = await client.patch(
            f"/api/v1/missions/{mission_id}",
            json={"title": "Updated Tech Briefing Title"},
        )
        assert res.status_code == 200
        assert res.json()["title"] == "Updated Tech Briefing Title"

        # 4. Plan Mission: DRAFT -> READY
        # Must create planned MissionExecution and 7 tasks with execution_id
        res = await client.post(f"/api/v1/missions/{mission_id}/plan")
        assert res.status_code == 200
        assert res.json()["state"] == "READY"

        # Verify MissionExecution ownership in database
        db_session.expire_all()
        exec_res = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == mission_id)
        )
        executions = list(exec_res.scalars().all())
        assert len(executions) == 1
        planned_exec = executions[0]
        assert planned_exec.state == "PLANNED"

        # Verify all tasks belong to planned execution_id
        tasks_res = await db_session.execute(
            select(Task).where(Task.mission_id == mission_id)
        )
        tasks = list(tasks_res.scalars().all())
        assert len(tasks) == 7
        for t in tasks:
            assert t.execution_id == planned_exec.id

        # 5. Start Mission: READY -> RUNNING
        # Must REUSE existing execution, not create a second one!
        res = await client.post(f"/api/v1/missions/{mission_id}/start")
        assert res.status_code == 200
        assert res.json()["state"] == "RUNNING"

        # Verify no duplicate execution was created
        db_session.expire_all()
        exec_res_after = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == mission_id)
        )
        executions_after = list(exec_res_after.scalars().all())
        assert len(executions_after) == 1
        assert executions_after[0].id == planned_exec.id
        assert executions_after[0].state == "RUNNING"

        # 6. Pause Mission: RUNNING -> PAUSED
        res = await client.post(f"/api/v1/missions/{mission_id}/pause")
        assert res.status_code == 200
        assert res.json()["state"] == "PAUSED"

        # 7. Resume Mission: PAUSED -> RUNNING
        res = await client.post(f"/api/v1/missions/{mission_id}/resume")
        assert res.status_code == 200
        assert res.json()["state"] == "RUNNING"

        # 8. Cancel Mission: RUNNING -> CANCELLED
        res = await client.post(f"/api/v1/missions/{mission_id}/cancel")
        assert res.status_code == 200
        assert res.json()["state"] == "CANCELLED"

        # 9. Get Mission Tasks
        res = await client.get(f"/api/v1/missions/{mission_id}/tasks")
        assert res.status_code == 200
        assert len(res.json()) == 7

        # 10. Get Mission Decisions
        res = await client.get(f"/api/v1/missions/{mission_id}/decisions")
        assert res.status_code == 200
        decisions = res.json()
        assert len(decisions) >= 4
        # Verify decision log types recorded
        dec_types = [d["decision_type"] for d in decisions]
        assert "MISSION_PLAN" in dec_types
        assert "MISSION_START" in dec_types
        assert "MISSION_PAUSE" in dec_types
        assert "MISSION_CANCEL" in dec_types
