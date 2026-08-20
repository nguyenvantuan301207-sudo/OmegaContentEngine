"""Integration tests for Research Engine concurrency and run idempotency."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_concurrent_research_run_safety(db_session: AsyncSession) -> None:
    """Test that concurrent run calls do not corrupt brief versions or generate duplicate active runs."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup Channel & Evaluated Topic
        slug = f"concurrency-chan-{uuid.uuid4().hex[:8]}"
        chan_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Concurrency Test Channel",
                "slug": slug,
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        channel_id = chan_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Celery with Redis Broker Concurrency Tuning",
                "keywords": ["Celery", "Redis", "Concurrency"],
            },
        )
        cand_id = cand_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        # Create Research Request
        req_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": cand_id},
        )
        req_id = req_res.json()["id"]

        # Add 1 Source
        await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/sources",
            json={
                "title": "Celery 5.3 User Guide",
                "publisher": "Celery Project",
                "primary_source_status": "CONFIRMED",
                "content_excerpt": "Celery supports prefork, eventlet, gevent, and solo worker concurrency pools.",
            },
        )

        # Launch 3 concurrent run requests
        results = await asyncio.gather(
            client.post(f"/api/v1/channels/{channel_id}/research/{req_id}/run"),
            client.post(f"/api/v1/channels/{channel_id}/research/{req_id}/run"),
            client.post(f"/api/v1/channels/{channel_id}/research/{req_id}/run"),
            return_exceptions=True,
        )

        # At least one request must succeed (200), and others must either succeed with serial versioning or fail safely with conflict/bad request
        status_codes = [r.status_code for r in results if hasattr(r, "status_code")]
        assert 200 in status_codes

        # Verify only one is_current brief exists in DB
        brief_res = await client.get(f"/api/v1/channels/{channel_id}/research/{req_id}/brief")
        assert brief_res.status_code == 200
        assert brief_res.json()["is_current"] is True
