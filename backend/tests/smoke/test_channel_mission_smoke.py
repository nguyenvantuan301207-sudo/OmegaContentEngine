"""End-to-End smoke test for OMEGA-003 Channel Manager + Mission Engine flow.

Verifies:
1. Channel creation (DRAFT)
2. Channel activation (ACTIVE)
3. Channel DNA update (creating Revision 2)
4. Mission creation linked to Channel
5. Mission planning (pinning Revision 2)
6. Mission execution via Celery worker
7. Pre-execution approval gate & completion (SUCCEEDED)
8. Audit trail persistence in Channel revisions and Mission decision logs.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from omega.main import app


@pytest.mark.asyncio
async def test_full_channel_mission_smoke() -> None:
    """End-to-end smoke test validating Channel -> DNA Revision -> Mission Execution -> Success."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Create Channel
        chan_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Smoke Operating Channel",
                "slug": f"smoke-channel-{uuid.uuid4().hex[:6]}",
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
                "timezone": "America/Los_Angeles",
            },
        )
        assert chan_res.status_code == 201
        channel_id = chan_res.json()["id"]

        # 2. Activate Channel
        act_res = await client.post(f"/api/v1/channels/{channel_id}/activate")
        assert act_res.status_code == 200
        assert act_res.json()["state"] == "ACTIVE"

        # 3. Update Channel DNA (Revision 2)
        dna_res = await client.get(f"/api/v1/channels/{channel_id}/dna")
        dna_data = dna_res.json()
        dna_data["content_strategy"]["niche"] = "Autonomous Agent Frameworks"
        patch_res = await client.patch(
            f"/api/v1/channels/{channel_id}/dna",
            json={
                "dna": dna_data,
                "change_reason": "Specialize niche for smoke verification",
                "actor": "SMOKE_RUNNER",
            },
        )
        assert patch_res.status_code == 200

        # Verify revision 2 is active
        revs_res = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
        revs = revs_res.json()
        assert len(revs) == 2
        assert revs[0]["version"] == 2

        # 4. Create Mission linked to Channel
        m_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Agent Framework Deep Dive",
                "objective": "Produce benchmark report on agent frameworks",
                "channel_id": channel_id,
                "autonomy_level": "SUPERVISED",
                "priority": 1,
            },
        )
        assert m_res.status_code == 201
        mission_id = m_res.json()["id"]

        # 5. Plan Mission
        plan_res = await client.post(f"/api/v1/missions/{mission_id}/plan")
        assert plan_res.status_code == 200
        assert plan_res.json()["state"] == "READY"

        # 6. Start Mission
        start_res = await client.post(f"/api/v1/missions/{mission_id}/start")
        assert start_res.status_code == 200
        assert start_res.json()["state"] == "RUNNING"

        # 7. Poll for QA task WAITING_APPROVAL
        qa_task_id = None
        for _ in range(30):
            tasks_res = await client.get(f"/api/v1/missions/{mission_id}/tasks")
            assert tasks_res.status_code == 200
            tasks = tasks_res.json()
            for t in tasks:
                if t["task_type"] == "qa" and t["state"] == "WAITING_APPROVAL":
                    qa_task_id = t["id"]
                    break
            if qa_task_id:
                break
            await asyncio.sleep(1)

        assert qa_task_id is not None

        # 8. Approve QA task
        appr_res = await client.post(f"/api/v1/tasks/{qa_task_id}/approve")
        assert appr_res.status_code == 200

        # 9. Poll for Mission completion (SUCCEEDED)
        final_state = None
        for _ in range(30):
            m_check = await client.get(f"/api/v1/missions/{mission_id}")
            final_state = m_check.json()["state"]
            if final_state == "SUCCEEDED":
                break
            await asyncio.sleep(1)

        assert final_state == "SUCCEEDED"

        # 10. Verify all 7 tasks succeeded
        all_tasks_res = await client.get(f"/api/v1/missions/{mission_id}/tasks")
        all_tasks = all_tasks_res.json()
        assert len(all_tasks) == 7
        assert all(t["state"] == "SUCCEEDED" for t in all_tasks)

        # 11. Verify decision logs
        decisions_res = await client.get(f"/api/v1/missions/{mission_id}/decisions")
        decisions = decisions_res.json()
        assert len(decisions) >= 4
