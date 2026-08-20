"""Integration tests for Channel DNA update concurrency and revision monotonicity."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.channel_dna import ChannelDNA
from omega.main import app


@pytest.mark.asyncio
async def test_concurrent_channel_dna_updates(db_session: AsyncSession) -> None:
    """Test that concurrent DNA updates generate sequential, monotonic revision numbers without collisions."""
    slug = f"concurrency-{uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Channel (Revision 1)
        res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Concurrency Test Channel",
                "slug": slug,
                "platform": "YOUTUBE",
            },
        )
        assert res.status_code == 201
        channel_id = res.json()["id"]

        # 2. Fire 3 concurrent DNA updates
        async def update_dna(niche_name: str, reason: str) -> int:
            dna = ChannelDNA.create_default(niche=niche_name).model_dump()
            r = await client.patch(
                f"/api/v1/channels/{channel_id}/dna",
                json={"dna": dna, "change_reason": reason, "actor": "USER"},
            )
            return r.status_code

        results = await asyncio.gather(
            update_dna("Niche A", "Concurrent Reason A"),
            update_dna("Niche B", "Concurrent Reason B"),
            update_dna("Niche C", "Concurrent Reason C"),
        )

        assert all(code == 200 for code in results)

        # 3. Verify exactly 4 revisions exist (v1 initial + 3 updates = v1, v2, v3, v4)
        res = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
        assert res.status_code == 200
        revisions = res.json()
        assert len(revisions) == 4

        versions = [r["version"] for r in revisions]
        assert versions == [4, 3, 2, 1]
