"""Integration tests for context modes, channel isolation, and archived channel behavior."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.models import MissionExecution
from omega.main import app


@pytest.mark.asyncio
async def test_archived_channel_rejects_content_creation(db_session: AsyncSession) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create and Archive Channel
        slug = f"archived-c-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Archived Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Topic before archive"},
        )
        cand_id = cand_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        r_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": cand_id, "research_question": "Q1"},
        )
        r_id = r_res.json()["id"]
        b_res = await client.post(f"/api/v1/channels/{channel_id}/research/{r_id}/run")
        brief_id = b_res.json()["id"]

        # Now archive channel
        await client.post(f"/api/v1/channels/{channel_id}/archive")

        # Attempt to create content request
        bad_req_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={"topic_candidate_id": cand_id, "research_brief_id": brief_id},
        )
        assert bad_req_res.status_code == 400
        assert "archived channel" in bad_req_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_mission_execution_mode_enforces_selected_topic(db_session: AsyncSession) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create Channel & Mission
        slug = f"mission-ctx-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Mission Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        m_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Autonomous Video Pipeline",
                "objective": "Plan and generate full content package",
                "channel_id": channel_id,
            },
        )
        mission_id = m_res.json()["id"]

        plan_res = await client.post(f"/api/v1/missions/{mission_id}/plan")
        assert plan_res.status_code == 200

        # Query MissionExecution from db_session
        exec_res = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == uuid.UUID(mission_id))
        )
        mission_exec_id = str(exec_res.scalars().first().id)

        # Topic Candidate in EVALUATED state (NOT SELECTED)
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Evaluated Topic"},
        )
        cand_id = cand_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        r_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": cand_id, "research_question": "Q1"},
        )
        r_id = r_res.json()["id"]
        b_res = await client.post(f"/api/v1/channels/{channel_id}/research/{r_id}/run")
        brief_id = b_res.json()["id"]

        # Creating request in MISSION_EXECUTION mode with EVALUATED topic must fail
        fail_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={
                "topic_candidate_id": cand_id,
                "research_brief_id": brief_id,
                "mission_execution_id": mission_exec_id,
            },
        )
        assert fail_res.status_code == 400
        assert "SELECTED state" in fail_res.json()["detail"]

        # Select candidate
        sel_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/select"
        )
        assert sel_res.status_code == 200

        # Now creating request in MISSION_EXECUTION mode succeeds
        ok_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={
                "topic_candidate_id": cand_id,
                "research_brief_id": brief_id,
                "mission_execution_id": mission_exec_id,
            },
        )
        assert ok_res.status_code == 201
        assert ok_res.json()["mode"] == "MISSION_EXECUTION"
