"""Integration tests for Mission-Channel linkage, immutability, and DNA revision pinning."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.models import MissionExecution
from omega.main import app


@pytest.mark.asyncio
async def test_mission_channel_linkage_and_immutability(db_session: AsyncSession) -> None:
    """Test mission channel association, validation guards, and channel_id immutability."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Create Channel
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Mission Linkage Channel",
                "slug": f"mission-linkage-{uuid.uuid4().hex[:6]}",
            },
        )
        assert c_res.status_code == 201
        channel_id = c_res.json()["id"]

        # 2. Create Mission with non-existent channel -> 400 Bad Request
        fake_id = str(uuid.uuid4())
        bad_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Invalid Channel Mission",
                "objective": "Should fail",
                "channel_id": fake_id,
            },
        )
        assert bad_res.status_code == 400

        # 3. Create Mission linked to valid Channel -> 201 Created
        m_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Channel Linked Mission",
                "objective": "Verify channel association",
                "channel_id": channel_id,
            },
        )
        assert m_res.status_code == 201
        mission_id = m_res.json()["id"]
        assert m_res.json()["channel_id"] == channel_id

        # 4. Plan Mission
        plan_res = await client.post(f"/api/v1/missions/{mission_id}/plan")
        assert plan_res.status_code == 200

        # 5. Attempt to modify channel_id after planning -> 400 Bad Request (Immutable!)
        patch_res = await client.patch(
            f"/api/v1/missions/{mission_id}",
            json={"channel_id": str(uuid.uuid4())},
        )
        assert patch_res.status_code == 400


@pytest.mark.asyncio
async def test_dna_revision_pinning_and_reproducibility(db_session: AsyncSession) -> None:
    """Verify deterministic MissionExecution -> ChannelDNARevision pinning across DNA changes.

    Flow:
    Channel DNA v1
    -> Mission A is planned (pins v1)
    -> Channel DNA updated to v2
    -> MissionExecution A still points to v1
    -> Mission B is planned (pins v2)
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Create Channel (Revision 1, version=1)
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Pinning Test Channel",
                "slug": f"pinning-channel-{uuid.uuid4().hex[:6]}",
            },
        )
        assert c_res.status_code == 201
        channel_id = c_res.json()["id"]

        # Get initial revision 1 ID
        revs_res = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
        revs = revs_res.json()
        assert len(revs) == 1
        rev1_id = revs[0]["id"]
        assert revs[0]["version"] == 1

        # 2. Create and Plan Mission A
        m_a_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Mission A (Pins v1)",
                "objective": "Verify pinning to v1",
                "channel_id": channel_id,
            },
        )
        mission_a_id = m_a_res.json()["id"]
        await client.post(f"/api/v1/missions/{mission_a_id}/plan")

        # Query MissionExecution A from DB
        db_session.expire_all()
        exec_a_res = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == mission_a_id)
        )
        exec_a = exec_a_res.scalars().one()
        assert str(exec_a.channel_dna_revision_id) == rev1_id

        # 3. Update Channel DNA to Version 2
        dna_res = await client.get(f"/api/v1/channels/{channel_id}/dna")
        dna_v2 = dna_res.json()
        dna_v2["content_strategy"]["niche"] = "Quantum Computing"
        patch_dna_res = await client.patch(
            f"/api/v1/channels/{channel_id}/dna",
            json={
                "dna": dna_v2,
                "change_reason": "Upgraded strategic niche to Quantum Computing",
                "actor": "USER",
            },
        )
        assert patch_dna_res.status_code == 200

        # Get new revision 2 ID
        revs_res2 = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
        revs2 = revs_res2.json()
        assert len(revs2) == 2
        rev2_id = revs2[0]["id"]
        assert revs2[0]["version"] == 2

        # 4. Verify MissionExecution A STILL points to Revision 1 (Frozen / Reproducible!)
        db_session.expire_all()
        exec_a_check = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == mission_a_id)
        )
        assert str(exec_a_check.scalars().one().channel_dna_revision_id) == rev1_id

        # 5. Create and Plan Mission B (Must pin Revision 2)
        m_b_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Mission B (Pins v2)",
                "objective": "Verify pinning to v2",
                "channel_id": channel_id,
            },
        )
        mission_b_id = m_b_res.json()["id"]
        await client.post(f"/api/v1/missions/{mission_b_id}/plan")

        db_session.expire_all()
        exec_b_res = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == mission_b_id)
        )
        exec_b = exec_b_res.scalars().one()
        assert str(exec_b.channel_dna_revision_id) == rev2_id


@pytest.mark.asyncio
async def test_backward_compatibility_mission_without_channel(db_session: AsyncSession) -> None:
    """Verify that missions without a channel continue to plan and execute normally with channel_dna_revision_id=None."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Create Mission with channel_id = None
        m_res = await client.post(
            "/api/v1/missions",
            json={
                "title": "Standalone Legacy Mission",
                "objective": "Ensure zero breakage for unlinked missions",
            },
        )
        assert m_res.status_code == 201
        m_id = m_res.json()["id"]
        assert m_res.json()["channel_id"] is None

        # Plan mission
        plan_res = await client.post(f"/api/v1/missions/{m_id}/plan")
        assert plan_res.status_code == 200

        db_session.expire_all()
        exec_res = await db_session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == m_id)
        )
        execution = exec_res.scalars().one()
        assert execution.channel_dna_revision_id is None
