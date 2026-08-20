"""Integration tests for Research context modes, topic eligibility, and channel isolation."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.models import MissionExecution
from omega.main import app


@pytest.mark.asyncio
async def test_topic_eligibility_and_channel_isolation(db_session: AsyncSession) -> None:
    """Test topic candidate state eligibility rules and cross-channel isolation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Channel A
        chan_a_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Channel A",
                "slug": f"chan-a-{uuid.uuid4().hex[:8]}",
                "platform": "YOUTUBE",
            },
        )
        chan_a_id = chan_a_res.json()["id"]
        await client.post(f"/api/v1/channels/{chan_a_id}/activate")

        # 2. Ingest Discovered Topic Candidate (NOT evaluated yet)
        cand_disc_res = await client.post(
            f"/api/v1/channels/{chan_a_id}/topics/candidates",
            json={"title": "Discovered Topic Alpha"},
        )
        cand_disc_id = cand_disc_res.json()["id"]

        # Attempt to create research request on DISCOVERED candidate -> must fail 400
        fail_req_res = await client.post(
            f"/api/v1/channels/{chan_a_id}/research",
            json={"topic_candidate_id": cand_disc_id},
        )
        assert fail_req_res.status_code == 400
        assert "must be EVALUATED, RECOMMENDED, or SELECTED" in fail_req_res.json()["detail"]

        # 3. Evaluate candidate -> state becomes EVALUATED/RECOMMENDED
        await client.post(
            f"/api/v1/channels/{chan_a_id}/topics/candidates/{cand_disc_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        # Now creating research request on EVALUATED candidate succeeds
        succ_req_res = await client.post(
            f"/api/v1/channels/{chan_a_id}/research",
            json={"topic_candidate_id": cand_disc_id},
        )
        assert succ_req_res.status_code == 201
        req_a_id = succ_req_res.json()["id"]

        # 4. Create Channel B
        chan_b_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Channel B",
                "slug": f"chan-b-{uuid.uuid4().hex[:8]}",
                "platform": "YOUTUBE",
            },
        )
        chan_b_id = chan_b_res.json()["id"]

        # Channel B cannot access Channel A's research request -> 404
        b_access_res = await client.get(f"/api/v1/channels/{chan_b_id}/research/{req_a_id}")
        assert b_access_res.status_code == 404


@pytest.mark.asyncio
async def test_mission_execution_pinned_dna_research(db_session: AsyncSession) -> None:
    """Test that MISSION_EXECUTION mode requires SELECTED topic candidate and pinned DNA."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create Channel and Mission
        slug = f"exec-res-chan-{uuid.uuid4().hex[:8]}"
        chan_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Exec Research Channel",
                "slug": slug,
                "platform": "YOUTUBE",
            },
        )
        channel_id = chan_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        # Create Candidate and Select it
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Mission Planned Production Topic"},
        )
        cand_id = cand_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        await client.post(f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/select")

        # Create & Plan Mission linked to Channel
        mis_res = await client.post(
            "/api/v1/missions",
            json={
                "channel_id": channel_id,
                "title": "Autonomous Video Research Mission",
                "objective": "Produce research briefing for autonomous video",
                "autonomy_level": "SUPERVISED",
                "priority": 1,
            },
        )
        mission_id = mis_res.json()["id"]
        await client.post(f"/api/v1/missions/{mission_id}/plan")

        # Query Mission Execution ID
        db_session.expire_all()
        exec_res = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == uuid.UUID(mission_id))
        )
        execution = exec_res.scalars().one()
        assert execution.channel_dna_revision_id is not None

        # Initiate Research with mission_execution_id
        req_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={
                "topic_candidate_id": cand_id,
                "mission_execution_id": str(execution.id),
            },
        )
        assert req_res.status_code == 201
        assert req_res.json()["mode"] == "MISSION_EXECUTION"
