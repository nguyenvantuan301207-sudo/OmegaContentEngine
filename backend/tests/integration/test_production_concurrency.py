"""Integration tests for render idempotency and version allocation under concurrency."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app
from tests.integration.test_production_lineage_pinning import _create_test_script


@pytest.mark.asyncio
async def test_render_idempotency_same_key_returns_existing_job(
    db_session: AsyncSession,
) -> None:
    """Verify that submitting the same idempotency key returns the existing job."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup
        slug = f"idemp-{uuid.uuid4().hex[:6]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Idempotency Channel", "slug": slug, "platform": "YOUTUBE"},
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

        # First Render dispatch
        key = f"key_{uuid.uuid4().hex}"
        render1 = await client.post(
            f"/api/v1/channels/{ch_id}/production/{req_id}/render",
            json={"idempotency_key": key},
        )
        assert render1.status_code == 200
        job1 = render1.json()

        # Retry with same idempotency key
        render2 = await client.post(
            f"/api/v1/channels/{ch_id}/production/{req_id}/render",
            json={"idempotency_key": key},
        )
        assert render2.status_code == 200
        job2 = render2.json()

        # Must return the exact same job
        assert job1["id"] == job2["id"]
        assert job1["idempotency_key"] == job2["idempotency_key"]


@pytest.mark.asyncio
async def test_concurrent_render_same_key_single_job(
    db_session: AsyncSession,
) -> None:
    """Verify concurrent requests with the same idempotency key produce exactly one job."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        slug = f"conc-{uuid.uuid4().hex[:6]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Concurrency Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        ch_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{ch_id}/activate")
        _, script_id, _ = await _create_test_script(client, ch_id)

        p_res = await client.post(
            f"/api/v1/channels/{ch_id}/production", json={"script_version_id": script_id}
        )
        req_id = p_res.json()["id"]
        await client.post(f"/api/v1/channels/{ch_id}/production/{req_id}/prepare")

        key = f"concurrent_key_{uuid.uuid4().hex}"

        async def _call_render():
            async with AsyncClient(transport=transport, base_url="http://test") as cl:
                return await cl.post(
                    f"/api/v1/channels/{ch_id}/production/{req_id}/render",
                    json={"idempotency_key": key},
                )

        results = await asyncio.gather(_call_render(), _call_render(), _call_render())

        job_ids = {r.json()["id"] for r in results if r.status_code == 200}
        assert len(job_ids) == 1
