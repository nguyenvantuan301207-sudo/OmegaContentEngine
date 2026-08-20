"""Integration test verifying immutable ResearchBrief pinning."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_content_request_stays_pinned_to_brief_v1_after_brief_v2(
    db_session: AsyncSession,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup Channel & Topic
        slug = f"brief-pin-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Brief Pinning Test Channel",
                "slug": slug,
                "platform": "YOUTUBE",
            },
        )
        assert c_res.status_code == 201
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "PostgreSQL Pinning Test",
                "summary": "Brief pinning invariant verification",
            },
        )
        assert cand_res.status_code == 201
        cand_id = cand_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        # 2. Create Research Request & Brief v1
        r_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": cand_id, "research_question": "Question 1"},
        )
        research_req_id = r_res.json()["id"]

        src_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/sources",
            json={
                "source_type": "MANUAL",
                "title": "Manual Source v1",
                "publisher": "Internal Engineering Benchmark",
                "content_excerpt": "Source v1 initial facts and statistics.",
            },
        )
        source_v1_id = src_res.json()["id"]

        await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/claims",
            json={
                "claim_text": "Initial Claim v1 verified fact.",
                "claim_type": "FACT",
                "evidence": [
                    {
                        "source_id": source_v1_id,
                        "support_direction": "SUPPORTS",
                        "excerpt": "Source v1 initial facts.",
                        "strength_score": 0.9,
                    }
                ],
            },
        )

        run_v1_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/run"
        )
        assert run_v1_res.status_code == 200
        brief_v1_id = run_v1_res.json()["id"]
        assert run_v1_res.json()["version"] == 1

        # 3. Create Content Request pinned to Brief v1
        content_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={
                "topic_candidate_id": cand_id,
                "research_brief_id": brief_v1_id,
                "content_type": "YOUTUBE_LONGFORM",
                "target_duration_seconds": 480,
            },
        )
        assert content_res.status_code == 201
        content_req_id = content_res.json()["id"]
        assert content_res.json()["research_brief_id"] == brief_v1_id

        # Generate Script v1
        gen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate"
        )
        assert gen_res.status_code == 200
        assert gen_res.json()["version"] == 1

        # 4. Run Research again to generate ResearchBrief v2
        run_v2_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/run"
        )
        assert run_v2_res.status_code == 200
        brief_v2_id = run_v2_res.json()["id"]
        assert run_v2_res.json()["version"] == 2
        assert brief_v2_id != brief_v1_id

        # 5. Verify Content Request remains pinned to Brief v1
        get_c_res = await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}")
        assert get_c_res.status_code == 200
        assert get_c_res.json()["research_brief_id"] == brief_v1_id

        # 6. Regenerate Script v2 on Content Request -> must still use Brief v1
        regen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/regenerate"
        )
        assert regen_res.status_code == 200
        script_v2 = regen_res.json()
        assert script_v2["version"] == 2

        # Verify all citations in script v2 link exclusively to brief_v1_id
        for sec in script_v2["sections"]:
            for stmt in sec["statements"]:
                for cit in stmt["citations"]:
                    assert cit["research_brief_id"] == brief_v1_id
                    assert cit["research_brief_id"] != brief_v2_id
