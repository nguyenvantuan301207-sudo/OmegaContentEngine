"""Integration tests for safe media streaming endpoint and HTTP Range (206) support."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app
from tests.integration.test_production_lineage_pinning import _create_test_script


@pytest.mark.asyncio
async def test_media_streaming_and_http_range_support(
    db_session: AsyncSession,
) -> None:
    """Verify media streaming endpoint delivers correct headers and supports Range requests (206)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup Channel & Script
        slug = f"media-{uuid.uuid4().hex[:6]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Media Delivery Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        ch_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{ch_id}/activate")
        _, script_id, _ = await _create_test_script(client, ch_id)

        # Create Production Request & Prepare
        p_res = await client.post(
            f"/api/v1/channels/{ch_id}/production", json={"script_version_id": script_id}
        )
        req_id = p_res.json()["id"]
        await client.post(f"/api/v1/channels/{ch_id}/production/{req_id}/prepare")

        # Render artifact
        r_res = await client.post(
            f"/api/v1/channels/{ch_id}/production/{req_id}/render",
            json={"idempotency_key": f"render_{uuid.uuid4().hex}"},
        )
        assert r_res.status_code == 200

        # List artifacts
        arts_res = await client.get(f"/api/v1/channels/{ch_id}/production/{req_id}/artifacts")
        assert arts_res.status_code == 200
        artifacts = arts_res.json()
        assert len(artifacts) >= 1
        art = artifacts[0]
        art_id = art["id"]

        # 1. Full Stream (200 OK)
        stream_res = await client.get(
            f"/api/v1/channels/{ch_id}/production/{req_id}/artifacts/{art_id}/media"
        )
        assert stream_res.status_code == 200
        assert stream_res.headers.get("content-type") == "video/mp4"
        assert stream_res.headers.get("accept-ranges") == "bytes"
        assert int(stream_res.headers.get("content-length", 0)) > 0
        total_len = len(stream_res.content)

        # 2. HTTP Range Request (206 Partial Content)
        range_res = await client.get(
            f"/api/v1/channels/{ch_id}/production/{req_id}/artifacts/{art_id}/media",
            headers={"Range": "bytes=0-49"},
        )
        assert range_res.status_code == 206
        assert len(range_res.content) == 50
        assert range_res.headers.get("content-range") == f"bytes 0-49/{total_len}"
