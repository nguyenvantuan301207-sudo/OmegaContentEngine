"""Integration tests for Channel DNA context modes and Channel isolation."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_channel_memory_isolation(db_session: AsyncSession) -> None:
    """Verify that Topic Memory for Channel A does not impact or leak into Channel B."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create Channel A & Candidate
        ca_res = await client.post(
            "/api/v1/channels",
            json={"name": "Channel A", "slug": f"chan-a-{uuid.uuid4().hex[:8]}"},
        )
        chan_a_id = ca_res.json()["id"]

        # Create Channel B & Candidate
        cb_res = await client.post(
            "/api/v1/channels",
            json={"name": "Channel B", "slug": f"chan-b-{uuid.uuid4().hex[:8]}"},
        )
        chan_b_id = cb_res.json()["id"]

        # Ingest same topic on Channel A and Channel B
        topic_title = "Global Macroeconomics in 2026"
        await client.post(
            f"/api/v1/channels/{chan_a_id}/topics/candidates", json={"title": topic_title}
        )
        cand_b_res = await client.post(
            f"/api/v1/channels/{chan_b_id}/topics/candidates", json={"title": topic_title}
        )
        cand_b_id = cand_b_res.json()["id"]

        # Select candidate on Channel A
        cand_a_list = await client.get(f"/api/v1/channels/{chan_a_id}/topics/candidates")
        cand_a_id = cand_a_list.json()[0]["id"]
        await client.post(f"/api/v1/channels/{chan_a_id}/topics/candidates/{cand_a_id}/select")

        # Verify Channel A memory has times_selected = 1
        mem_a = (await client.get(f"/api/v1/channels/{chan_a_id}/topics/memory")).json()
        assert mem_a[0]["times_selected"] == 1

        # Verify Channel B memory has times_selected = 0 (Strict isolation!)
        mem_b = (await client.get(f"/api/v1/channels/{chan_b_id}/topics/memory")).json()
        assert mem_b[0]["times_selected"] == 0

        # Evaluate Candidate B in Channel B -> Novelty should still be 100% FRESH_TOPIC
        eval_b = await client.post(
            f"/api/v1/channels/{chan_b_id}/topics/candidates/{cand_b_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        assert eval_b.status_code == 200
        assert eval_b.json()["novelty_score"] == 100.0


@pytest.mark.asyncio
async def test_mission_execution_pinned_dna_context_evaluation(db_session: AsyncSession) -> None:
    """Verify that MISSION_EXECUTION evaluation mode deterministically uses the pinned ChannelDNARevision."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Channel (Revision 1 with Niche: "General Technology")
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Pinned Context Channel", "slug": f"pinned-ctx-{uuid.uuid4().hex[:8]}"},
        )
        chan_id = c_res.json()["id"]

        # 2. Create and Plan Mission A (Pins Revision 1)
        m_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Planned Mission A",
                "objective": "Execute strategy",
                "channel_id": chan_id,
            },
        )
        mission_id = m_res.json()["id"]
        await client.post(f"/api/v1/missions/{mission_id}/plan")

        # Query Mission Execution ID
        from sqlalchemy import select

        from omega.infrastructure.models import MissionExecution

        db_session.expire_all()
        exec_res = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == mission_id)
        )
        execution_id = str(exec_res.scalars().one().id)

        # 3. Update Channel DNA to Revision 2 (Change Niche to: "Quantum Physics Only")
        dna = c_res.json()["dna"]
        dna["content_strategy"]["niche"] = "Quantum Physics Only"
        await client.patch(
            f"/api/v1/channels/{chan_id}/dna",
            json={"dna": dna, "change_reason": "Specialize to Quantum Physics"},
        )

        # 4. Ingest candidate about General Technology
        cand_res = await client.post(
            f"/api/v1/channels/{chan_id}/topics/candidates",
            json={"title": "General Technology Overview", "keywords": ["Technology"]},
        )
        cand_id = cand_res.json()["id"]

        # 5. Evaluate in MISSION_EXECUTION mode (Uses Revision 1 with General Technology niche)
        eval_mission = await client.post(
            f"/api/v1/channels/{chan_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "MISSION_EXECUTION", "mission_execution_id": execution_id},
        )
        assert eval_mission.status_code == 200
        # Strategic fit should be high because Revision 1 had General Technology
        assert eval_mission.json()["strategic_fit_score"] >= 60.0

        # 6. Evaluate in INTERACTIVE mode (Uses active Revision 2 with Quantum Physics Only)
        eval_inter = await client.post(
            f"/api/v1/channels/{chan_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        assert eval_inter.status_code == 200
        # Strategic fit should be lower under Revision 2
        assert eval_inter.json()["strategic_fit_score"] < eval_mission.json()["strategic_fit_score"]
