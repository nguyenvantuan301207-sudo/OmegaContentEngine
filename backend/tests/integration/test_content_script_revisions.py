"""Integration tests verifying immutable script revisions and single-current-script invariant."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_script_revisions_v1_to_v2_and_immutability(db_session: AsyncSession) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup Channel, Topic, Brief
        slug = f"revisions-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Revisions Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Revisioning Mechanics", "summary": "Testing v1 to v2 immutability"},
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
        research_req_id = r_res.json()["id"]
        b_res = await client.post(f"/api/v1/channels/{channel_id}/research/{research_req_id}/run")
        brief_id = b_res.json()["id"]

        # 2. Create Content Request
        c_req_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={"topic_candidate_id": cand_id, "research_brief_id": brief_id},
        )
        content_req_id = c_req_res.json()["id"]

        # 3. Generate Script v1
        gen1_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate"
        )
        assert gen1_res.status_code == 200
        script_v1 = gen1_res.json()
        assert script_v1["version"] == 1
        assert script_v1["is_current"] is True
        assert script_v1["supersedes_script_id"] is None
        v1_id = script_v1["id"]
        v1_title = script_v1["title"]
        v1_hook = script_v1["hook_text"]

        # 4. Regenerate Script v2
        regen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/regenerate"
        )
        assert regen_res.status_code == 200
        script_v2 = regen_res.json()
        assert script_v2["version"] == 2
        assert script_v2["is_current"] is True
        assert script_v2["supersedes_script_id"] == v1_id
        v2_id = script_v2["id"]

        # 5. Verify Script v1 is preserved and marked is_current = False
        v1_check_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/scripts/1"
        )
        assert v1_check_res.status_code == 200
        v1_persisted = v1_check_res.json()
        assert v1_persisted["id"] == v1_id
        assert v1_persisted["version"] == 1
        assert v1_persisted["is_current"] is False
        assert v1_persisted["title"] == v1_title
        assert v1_persisted["hook_text"] == v1_hook

        # 6. Verify Script List returns both versions, exactly one current
        list_s_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/scripts"
        )
        assert list_s_res.status_code == 200
        summaries = list_s_res.json()
        assert len(summaries) == 2
        current_scripts = [s for s in summaries if s["is_current"]]
        assert len(current_scripts) == 1
        assert current_scripts[0]["version"] == 2
        assert current_scripts[0]["id"] == v2_id
