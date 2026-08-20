"""Integration test verifying immutable ChannelDNARevision pinning."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_content_request_stays_pinned_to_dna_v1_after_dna_v2(
    db_session: AsyncSession,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup Channel with Initial DNA v1
        slug = f"dna-pin-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "DNA Pinning Channel",
                "slug": slug,
                "platform": "YOUTUBE",
                "dna": {
                    "brand_voice": {"tone": ["PIRATE_METAPHORIC"], "pace": "SLOW"},
                },
            },
        )
        assert c_res.status_code == 201
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        # Check DNA Revision v1
        rev_res = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
        assert rev_res.status_code == 200
        revisions = rev_res.json()
        assert len(revisions) == 1
        dna_v1_id = revisions[0]["id"]
        assert revisions[0]["version"] == 1

        # 2. Topic & Research Brief Setup
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "DNA Versioning Topic", "summary": "DNA pinning test"},
        )
        cand_id = cand_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        r_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": cand_id, "research_question": "DNA question"},
        )
        research_req_id = r_res.json()["id"]

        run_res = await client.post(f"/api/v1/channels/{channel_id}/research/{research_req_id}/run")
        brief_id = run_res.json()["id"]

        # 3. Create Content Request pinned to DNA v1
        content_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={
                "topic_candidate_id": cand_id,
                "research_brief_id": brief_id,
                "content_type": "YOUTUBE_LONGFORM",
            },
        )
        assert content_res.status_code == 201
        content_req_id = content_res.json()["id"]
        assert content_res.json()["channel_dna_revision_id"] == dna_v1_id

        # 4. Update Channel DNA to Revision v2 (Change tone to DRY_ACADEMIC and pace to ENERGETIC)
        dna_patch_res = await client.patch(
            f"/api/v1/channels/{channel_id}/dna",
            json={
                "dna": {"brand_voice": {"tone": ["DRY_ACADEMIC"], "pace": "ENERGETIC"}},
                "change_reason": "Upgrade voice to DRY_ACADEMIC",
                "actor": "TEST_ADMIN",
            },
        )
        assert dna_patch_res.status_code == 200

        rev_res2 = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
        revisions2 = rev_res2.json()
        assert len(revisions2) == 2
        dna_v2_id = revisions2[0]["id"]
        assert dna_v2_id != dna_v1_id

        # 5. Generate Content on Request -> must use pinned DNA v1
        gen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate"
        )
        assert gen_res.status_code == 200
        script = gen_res.json()

        # Check intent and style snapshot
        intent_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/intent"
        )
        assert intent_res.status_code == 200
        intent = intent_res.json()
        assert intent["tone"] == "PIRATE_METAPHORIC"
        assert intent["pace"] == "SLOW"
        assert intent["tone"] != "DRY_ACADEMIC"

        assert script["style_snapshot"]["tone"] == "PIRATE_METAPHORIC"
        assert script["style_snapshot"]["pace"] == "SLOW"

        # 6. Verify Content Request DB Record still has DNA v1 pinned
        get_c_res = await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}")
        assert get_c_res.status_code == 200
        assert get_c_res.json()["channel_dna_revision_id"] == dna_v1_id
