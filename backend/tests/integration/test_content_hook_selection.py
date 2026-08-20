"""Integration tests verifying single-selected-hook invariant and concurrency safety."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_single_selected_hook_invariant(db_session: AsyncSession) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        slug = f"hook-sel-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Hook Sel Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Hook Topic", "summary": "Hook selection test"},
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

        # Create content request & generate
        c_req_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={"topic_candidate_id": cand_id, "research_brief_id": brief_id},
        )
        content_req_id = c_req_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate")

        # Get hooks
        hooks_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks"
        )
        hooks = hooks_res.json()
        assert len(hooks) >= 3

        hook1 = hooks[1]
        hook2 = hooks[2]

        # Select hook 1
        sel1_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks/{hook1['id']}/select",
            json={"selected": True},
        )
        assert sel1_res.status_code == 200
        assert sel1_res.json()["selected"] is True

        # Verify only hook 1 is selected
        hooks_check1 = (
            await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks")
        ).json()
        selected_hooks = [h for h in hooks_check1 if h["selected"]]
        assert len(selected_hooks) == 1
        assert selected_hooks[0]["id"] == hook1["id"]

        # Now select hook 2
        sel2_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks/{hook2['id']}/select",
            json={"selected": True},
        )
        assert sel2_res.status_code == 200
        assert sel2_res.json()["selected"] is True

        # Verify only hook 2 is selected and hook 1 is unselected
        hooks_check2 = (
            await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks")
        ).json()
        selected_hooks2 = [h for h in hooks_check2 if h["selected"]]
        assert len(selected_hooks2) == 1
        assert selected_hooks2[0]["id"] == hook2["id"]


@pytest.mark.asyncio
async def test_concurrent_hook_selection_safety(db_session: AsyncSession) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        slug = f"hook-conc-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Hook Concurrency Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Hook Concurrency Topic"},
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
        await client.post(f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate")

        hooks = (
            await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks")
        ).json()

        # Fire concurrent selection requests
        async def select_h(hook_id: str):
            return await client.post(
                f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks/{hook_id}/select",
                json={"selected": True},
            )

        _results = await asyncio.gather(
            select_h(hooks[0]["id"]),
            select_h(hooks[1]["id"]),
            select_h(hooks[2]["id"]),
            return_exceptions=True,
        )

        # After all concurrent calls resolve, there must be at most one selected hook
        final_hooks = (
            await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks")
        ).json()
        selected_final = [h for h in final_hooks if h["selected"]]
        assert len(selected_final) <= 1
