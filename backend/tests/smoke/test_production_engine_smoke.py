"""End-to-end smoke test for OMEGA-007 Production Engine."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_production_engine_e2e_smoke(
    db_session: AsyncSession,
) -> None:
    """Execute complete end-to-end production flow from Channel to rendered MP4 video with QA and revisioning."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Channel Setup
        slug = f"prod-smoke-{uuid.uuid4().hex[:6]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Production Smoke Channel",
                "slug": slug,
                "platform": "YOUTUBE",
                "dna": {
                    "brand_voice": {
                        "tone": ["AUTHORITATIVE", "ENGAGING"],
                        "pace": "BALANCED",
                        "target_audience": "Tech Enthusiasts",
                    }
                },
            },
        )
        assert c_res.status_code == 201
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        # 2. Topic Ingestion
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Quantum Computing Frontiers",
                "summary": "Exploring 100-qubit processors and their implications.",
            },
        )
        assert cand_res.status_code == 201
        cand_id = cand_res.json()["id"]

        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        await client.post(f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/select")

        # 3. Research Synthesis
        r_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": cand_id},
        )
        assert r_res.status_code == 201
        r_req_id = r_res.json()["id"]

        src_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{r_req_id}/sources",
            json={
                "source_type": "MANUAL",
                "url": "https://example.com/quantum-smoke",
                "title": "Quantum Journal",
                "publisher": "Quantum Press",
                "content_excerpt": "Scientists demonstrate fault-tolerant quantum operations.",
                "primary_source_status": "CONFIRMED",
            },
        )
        assert src_res.status_code == 201
        src_id = src_res.json()["id"]

        claim_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{r_req_id}/claims",
            json={
                "claim_text": "Quantum computers can solve specific algorithms exponentially faster.",
            },
        )
        assert claim_res.status_code == 201
        claim_id = claim_res.json()["id"]

        await client.post(
            f"/api/v1/channels/{channel_id}/research/{r_req_id}/claims/{claim_id}/evidence",
            json={
                "source_id": src_id,
                "support_direction": "SUPPORTS",
                "excerpt": "Scientists demonstrate fault-tolerant quantum operations.",
                "strength_score": 95.0,
            },
        )

        synth_res = await client.post(f"/api/v1/channels/{channel_id}/research/{r_req_id}/run")
        assert synth_res.status_code == 200
        brief_id = synth_res.json()["id"]

        # 4. Content Generation (Script Draft v1)
        cnt_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={"topic_candidate_id": cand_id, "research_brief_id": brief_id},
        )
        assert cnt_res.status_code == 201
        content_req_id = cnt_res.json()["id"]

        gen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate",
            json={"idempotency_key": f"gen_{uuid.uuid4().hex}"},
        )
        assert gen_res.status_code == 200
        script_v1 = gen_res.json()
        script_v1_id = script_v1["id"]

        # 5. Production Request Creation (Pinned to Script v1)
        prod_res = await client.post(
            f"/api/v1/channels/{channel_id}/production",
            json={
                "script_version_id": script_v1_id,
                "target_width": 1920,
                "target_height": 1080,
                "fps": 30,
                "video_codec": "h264",
                "audio_codec": "aac",
                "container_format": "mp4",
            },
        )
        assert prod_res.status_code == 201
        prod_req = prod_res.json()
        prod_req_id = prod_req["id"]
        assert prod_req["status"] == "DRAFT"

        # 6. Production Preparation
        prep_res = await client.post(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/prepare"
        )
        assert prep_res.status_code == 200
        assert prep_res.json()["status"] == "READY"

        # Verify scenes, assets, narration, subtitles, and render plan
        scenes_res = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/scenes"
        )
        assert scenes_res.status_code == 200
        assert len(scenes_res.json()) >= 1

        assets_res = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/assets"
        )
        assert assets_res.status_code == 200
        assert len(assets_res.json()) >= 1

        narr_res = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/narration"
        )
        assert narr_res.status_code == 200
        assert len(narr_res.json()) >= 1

        sub_res = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/subtitles"
        )
        assert sub_res.status_code == 200
        assert len(sub_res.json()) >= 1

        plan_res = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/render-plan"
        )
        assert plan_res.status_code == 200
        assert plan_res.json()["version"] == 1

        # 7. Render Execution (v1)
        render_res = await client.post(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/render",
            json={"idempotency_key": f"render_smoke_{uuid.uuid4().hex}"},
        )
        assert render_res.status_code == 200
        assert render_res.json()["state"] == "SUCCEEDED"

        # 8. Check Media Artifacts v1
        art_res = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/artifacts"
        )
        assert art_res.status_code == 200
        artifacts = art_res.json()
        assert len(artifacts) == 1
        art_v1 = artifacts[0]
        assert art_v1["version"] == 1
        assert art_v1["is_current"] is True
        assert art_v1["width"] == 1920
        assert art_v1["height"] == 1080
        assert art_v1["file_size_bytes"] > 0
        assert len(art_v1["content_hash"]) == 64

        # 9. Verify Production QA Result
        qa_res = await client.get(f"/api/v1/channels/{channel_id}/production/{prod_req_id}/qa")
        assert qa_res.status_code == 200
        qa_data = qa_res.json()
        assert qa_data["status"] in ("PASSED", "PASSED_WITH_WARNINGS"), f"QA findings: {qa_data.get('findings')}"

        # 10. Verify Media Streaming
        stream_res = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/artifacts/{art_v1['id']}/media"
        )
        assert stream_res.status_code == 200
        assert stream_res.headers.get("content-type") == "video/mp4"

        # 11. Explicit Rerender ($v1 \to v2$)
        rerender_res = await client.post(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/rerender",
            json={"idempotency_key": f"rerender_smoke_{uuid.uuid4().hex}"},
        )
        assert rerender_res.status_code == 200

        # Check Media Artifacts v2
        art_res2 = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/artifacts"
        )
        assert art_res2.status_code == 200
        artifacts2 = art_res2.json()
        assert len(artifacts2) == 2

        v2 = next(a for a in artifacts2 if a["version"] == 2)
        v1 = next(a for a in artifacts2 if a["version"] == 1)

        assert v2["is_current"] is True
        assert v1["is_current"] is False  # v1 rolled over
        assert v1["content_hash"] == art_v1["content_hash"]  # v1 hash immutable
