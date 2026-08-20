"""Smoke test for the full end-to-end Mission Lifecycle.

Flow:
1. Create Mission (DRAFT)
2. Plan Mission DAG (READY)
3. Start Mission (RUNNING)
4. Worker executes upstream stages (strategy -> topic_discovery -> research -> content_generation -> production)
5. QA stage reaches WAITING_APPROVAL (pre-execution approval gate)
6. User approves QA stage via API
7. Worker executes QA and Publish stages
8. All tasks SUCCEEDED -> Mission SUCCEEDED.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from omega.main import app


@pytest.mark.asyncio
async def test_full_mission_lifecycle_smoke() -> None:
    """Execute complete end-to-end Mission DAG workflow with human-in-the-loop approval."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Create Mission
        create_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Autonomous Tech Deep Dive",
                "objective": "Produce automated multi-platform research briefing",
                "autonomy_level": "SUPERVISED",
                "priority": 1,
            },
        )
        assert create_res.status_code == 201
        mission_id = create_res.json()["id"]

        # 2. Plan Mission DAG
        plan_res = await client.post(f"/api/v1/missions/{mission_id}/plan")
        assert plan_res.status_code == 200
        assert plan_res.json()["state"] == "READY"

        # 3. Start Mission
        start_res = await client.post(f"/api/v1/missions/{mission_id}/start")
        assert start_res.status_code == 200
        assert start_res.json()["state"] == "RUNNING"

        # 4. Wait / Poll for QA stage to reach WAITING_APPROVAL
        qa_task_id: str | None = None
        for _ in range(30):
            tasks_res = await client.get(f"/api/v1/missions/{mission_id}/tasks")
            tasks = tasks_res.json()

            qa_task = next((t for t in tasks if t["task_type"] == "qa"), None)
            if qa_task and qa_task["state"] == "WAITING_APPROVAL":
                qa_task_id = qa_task["id"]
                break

            await asyncio.sleep(1)

        assert qa_task_id is not None, "QA stage failed to reach WAITING_APPROVAL within timeout"

        # 5. Approve QA stage
        approve_res = await client.post(f"/api/v1/tasks/{qa_task_id}/approve")
        assert approve_res.status_code == 200
        assert approve_res.json()["state"] in ("READY", "QUEUED", "RUNNING", "SUCCEEDED")

        # 6. Poll for overall mission completion
        mission_succeeded = False
        for _ in range(30):
            m_res = await client.get(f"/api/v1/missions/{mission_id}")
            m_state = m_res.json()["state"]
            if m_state == "SUCCEEDED":
                mission_succeeded = True
                break
            await asyncio.sleep(1)

        assert mission_succeeded, "Mission failed to reach SUCCEEDED state within timeout"

        # 7. Final assertions
        final_tasks_res = await client.get(f"/api/v1/missions/{mission_id}/tasks")
        final_tasks = final_tasks_res.json()
        assert len(final_tasks) == 7
        for t in final_tasks:
            assert t["state"] == "SUCCEEDED", f"Task {t['title']} ({t['task_type']}) was {t['state']}, expected SUCCEEDED"

        final_decisions_res = await client.get(f"/api/v1/missions/{mission_id}/decisions")
        final_decisions = final_decisions_res.json()
        dec_types = [d["decision_type"] for d in final_decisions]
        assert "MISSION_PLAN" in dec_types
        assert "MISSION_START" in dec_types
        assert "TASK_APPROVAL_REQUESTED" in dec_types
        assert "TASK_APPROVED" in dec_types
        assert "MISSION_COMPLETE" in dec_types
