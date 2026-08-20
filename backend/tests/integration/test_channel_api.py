"""Integration tests for Channels REST API and DNA revision tracking."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_channel_crud_and_lifecycle(db_session: AsyncSession) -> None:
    """Test full Channel CRUD, activation, pause, and archive lifecycle."""
    slug = f"ai-daily-{uuid.uuid4().hex[:8]}"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Create Channel (DRAFT)
        res = await client.post(
            "/api/v1/channels",
            json={
                "name": "AI Daily Insights",
                "slug": slug,
                "description": "Daily breakdown of breakthroughs in AI",
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
                "timezone": "America/New_York",
            },
        )
        assert res.status_code == 201
        chan = res.json()
        channel_id = chan["id"]
        assert chan["state"] == "DRAFT"
        assert chan["slug"] == slug
        assert chan["platform"] == "YOUTUBE"

        # 2. Get Channel
        res = await client.get(f"/api/v1/channels/{channel_id}")
        assert res.status_code == 200
        assert res.json()["name"] == "AI Daily Insights"

        # 3. Update Channel metadata (slug is immutable)
        res = await client.patch(
            f"/api/v1/channels/{channel_id}",
            json={"description": "Updated channel description"},
        )
        assert res.status_code == 200
        assert res.json()["description"] == "Updated channel description"

        # 4. Activate Channel: DRAFT -> ACTIVE
        res = await client.post(f"/api/v1/channels/{channel_id}/activate")
        assert res.status_code == 200
        assert res.json()["state"] == "ACTIVE"

        # 5. Pause Channel: ACTIVE -> PAUSED
        res = await client.post(f"/api/v1/channels/{channel_id}/pause")
        assert res.status_code == 200
        assert res.json()["state"] == "PAUSED"

        # 6. Reactivate Channel: PAUSED -> ACTIVE
        res = await client.post(f"/api/v1/channels/{channel_id}/activate")
        assert res.status_code == 200
        assert res.json()["state"] == "ACTIVE"

        # 7. Get Channel DNA
        res = await client.get(f"/api/v1/channels/{channel_id}/dna")
        assert res.status_code == 200
        dna = res.json()
        assert "audience" in dna
        assert "content_strategy" in dna

        # 8. Update Channel DNA (Creates Revision 2)
        dna["content_strategy"]["niche"] = "Advanced Robotics"
        res = await client.patch(
            f"/api/v1/channels/{channel_id}/dna",
            json={
                "dna": dna,
                "change_reason": "Pivoting content strategy toward Robotics and Embodied AI",
                "actor": "USER",
            },
        )
        assert res.status_code == 200
        assert res.json()["content_strategy"]["niche"] == "Advanced Robotics"

        # 9. List DNA Revisions (must contain v2 and v1)
        res = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
        assert res.status_code == 200
        revisions = res.json()
        assert len(revisions) == 2
        assert revisions[0]["version"] == 2
        assert revisions[0]["change_reason"] == "Pivoting content strategy toward Robotics and Embodied AI"
        assert revisions[1]["version"] == 1

        # 10. Get Channel Context
        res = await client.get(f"/api/v1/channels/{channel_id}/context")
        assert res.status_code == 200
        ctx = res.json()
        assert ctx["active_dna_version"] == 2
        assert ctx["dna"]["content_strategy"]["niche"] == "Advanced Robotics"

        # 11. Archive Channel: ACTIVE -> ARCHIVED
        res = await client.post(f"/api/v1/channels/{channel_id}/archive")
        assert res.status_code == 200
        assert res.json()["state"] == "ARCHIVED"

        # 12. Archived Channel Restrictions: cannot activate or update DNA
        res = await client.post(f"/api/v1/channels/{channel_id}/activate")
        assert res.status_code == 400

        res = await client.patch(
            f"/api/v1/channels/{channel_id}/dna",
            json={"dna": dna, "change_reason": "Illegal update on archived channel"},
        )
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_channel_platform_restriction() -> None:
    """Test that non-YOUTUBE platforms are rejected in OMEGA-003."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.post(
            "/api/v1/channels",
            json={
                "name": "TikTok Tech",
                "slug": f"tiktok-tech-{uuid.uuid4().hex[:6]}",
                "platform": "TIKTOK",
            },
        )
        assert res.status_code == 422 or res.status_code == 400
