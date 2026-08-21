"""Integration tests for cross-channel isolation and path traversal rejection in Production Engine."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app
from tests.integration.test_production_lineage_pinning import _create_test_script


@pytest.mark.asyncio
async def test_cross_channel_production_request_rejected(
    db_session: AsyncSession,
) -> None:
    """Verify that a script from Channel A cannot be used to create a ProductionRequest on Channel B."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create Channel A
        res_a = await client.post(
            "/api/v1/channels",
            json={
                "name": "Channel A",
                "slug": f"ch-a-{uuid.uuid4().hex[:6]}",
                "platform": "YOUTUBE",
            },
        )
        ch_a_id = res_a.json()["id"]
        await client.post(f"/api/v1/channels/{ch_a_id}/activate")

        # Create Channel B
        res_b = await client.post(
            "/api/v1/channels",
            json={
                "name": "Channel B",
                "slug": f"ch-b-{uuid.uuid4().hex[:6]}",
                "platform": "YOUTUBE",
            },
        )
        ch_b_id = res_b.json()["id"]
        await client.post(f"/api/v1/channels/{ch_b_id}/activate")

        # Create Script on Channel A
        _, script_a_id, _ = await _create_test_script(client, ch_a_id)

        # Attempt to create ProductionRequest on Channel B using Channel A's script
        cross_res = await client.post(
            f"/api/v1/channels/{ch_b_id}/production",
            json={"script_version_id": script_a_id},
        )
        assert cross_res.status_code == 400
        assert "Channel mismatch" in cross_res.json()["detail"]
