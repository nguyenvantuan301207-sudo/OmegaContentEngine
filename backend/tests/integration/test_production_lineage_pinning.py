"""Integration test verifying ScriptVersion and ChannelDNARevision pinning in Production Engine."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


async def _create_test_script(client: AsyncClient, channel_id: str) -> tuple[str, str, str]:
    """Helper to create TopicCandidate -> ResearchBrief -> ContentRequest -> ScriptVersion."""
    # Topic
    cand_res = await client.post(
        f"/api/v1/channels/{channel_id}/topics/candidates",
        json={"title": "Quantum Pinning Test", "summary": "Lineage test"},
    )
    assert cand_res.status_code == 201
    cand_id = cand_res.json()["id"]

    await client.post(
        f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
        json={"mode": "INTERACTIVE"},
    )
    await client.post(f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/select")

    # Research Brief
    req_res = await client.post(
        f"/api/v1/channels/{channel_id}/research",
        json={"topic_candidate_id": cand_id},
    )
    assert req_res.status_code == 201
    r_req_id = req_res.json()["id"]

    src_res = await client.post(
        f"/api/v1/channels/{channel_id}/research/{r_req_id}/sources",
        json={
            "source_type": "MANUAL",
            "url": "https://example.com/quantum",
            "title": "Quantum Research",
            "publisher": "Quantum Labs",
            "content_excerpt": "Quantum mechanics works.",
            "primary_source_status": "CONFIRMED",
        },
    )
    assert src_res.status_code == 201
    src_id = src_res.json()["id"]

    claim_res = await client.post(
        f"/api/v1/channels/{channel_id}/research/{r_req_id}/claims",
        json={"claim_text": "Quantum mechanics works."},
    )
    assert claim_res.status_code == 201
    claim_id = claim_res.json()["id"]

    await client.post(
        f"/api/v1/channels/{channel_id}/research/{r_req_id}/claims/{claim_id}/evidence",
        json={
            "source_id": src_id,
            "support_direction": "SUPPORTS",
            "excerpt": "Quantum mechanics works.",
            "strength_score": 90.0,
        },
    )

    synth_res = await client.post(f"/api/v1/channels/{channel_id}/research/{r_req_id}/run")
    assert synth_res.status_code == 200
    brief_id = synth_res.json()["id"]

    # Content Request & Script
    c_res = await client.post(
        f"/api/v1/channels/{channel_id}/content",
        json={"topic_candidate_id": cand_id, "research_brief_id": brief_id},
    )
    assert c_res.status_code == 201
    content_req_id = c_res.json()["id"]

    gen_res = await client.post(
        f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate",
        json={"idempotency_key": f"gen_{uuid.uuid4().hex}"},
    )
    assert gen_res.status_code == 200
    script_v1_id = gen_res.json()["id"]

    return content_req_id, script_v1_id, brief_id


@pytest.mark.asyncio
async def test_production_pins_script_and_dna_revisions(
    db_session: AsyncSession,
) -> None:
    """Verify ProductionRequest pins exact ScriptVersion and ChannelDNARevision."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Channel with DNA v1
        slug = f"prod-pin-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Production Pinning Channel",
                "slug": slug,
                "platform": "YOUTUBE",
                "dna": {"brand_voice": {"tone": ["TECHNICAL"]}},
            },
        )
        assert c_res.status_code == 201
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        # Create Script v1
        content_req_id, script_v1_id, _ = await _create_test_script(client, channel_id)

        # 2. Create ProductionRequest pinned to Script v1
        p_res = await client.post(
            f"/api/v1/channels/{channel_id}/production",
            json={"script_version_id": script_v1_id},
        )
        assert p_res.status_code == 201
        prod_req = p_res.json()
        assert prod_req["script_version_id"] == script_v1_id
        dna_v1_id = prod_req["channel_dna_revision_id"]

        # 3. Update Channel DNA to v2
        dna2_res = await client.patch(
            f"/api/v1/channels/{channel_id}/dna",
            json={
                "dna": {"brand_voice": {"tone": ["CASUAL"]}},
                "change_reason": "DNA Update to v2",
            },
        )
        assert dna2_res.status_code == 200

        rev_res = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
        assert rev_res.status_code == 200
        revisions = rev_res.json()
        assert len(revisions) == 2
        assert revisions[0]["version"] == 2

        # 4. Generate Script v2 on Content Request
        regen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/regenerate",
            json={"idempotency_key": f"regen_{uuid.uuid4().hex}"},
        )
        assert regen_res.status_code == 200
        script_v2_id = regen_res.json()["id"]
        assert script_v2_id != script_v1_id

        # 5. Verify existing ProductionRequest remains pinned to Script v1 and DNA v1
        check_res = await client.get(f"/api/v1/channels/{channel_id}/production/{prod_req['id']}")
        assert check_res.status_code == 200
        reloaded_prod = check_res.json()
        assert reloaded_prod["script_version_id"] == script_v1_id
        assert reloaded_prod["channel_dna_revision_id"] == dna_v1_id
