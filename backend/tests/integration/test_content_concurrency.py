"""Integration tests for Content Engine concurrency safety."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_concurrent_generate_safety(db_session: AsyncSession) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        slug = f"gen-conc-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Concurrency Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Concurrency Topic"},
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

        c_req_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={"topic_candidate_id": cand_id, "research_brief_id": brief_id},
        )
        content_req_id = c_req_res.json()["id"]

        # Run generate
        gen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate"
        )
        assert gen_res.status_code == 200

        # Now fire 3 concurrent regenerate calls
        async def do_regen():
            return await client.post(
                f"/api/v1/channels/{channel_id}/content/{content_req_id}/regenerate"
            )

        _results = await asyncio.gather(
            do_regen(),
            do_regen(),
            do_regen(),
            return_exceptions=True,
        )

        # Verify that all responses that succeeded maintain exactly one is_current script
        scripts_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/scripts"
        )
        assert scripts_res.status_code == 200
        scripts = scripts_res.json()
        current_scripts = [s for s in scripts if s["is_current"]]
        assert len(current_scripts) == 1
