"""End-to-end smoke test for OMEGA-006 Content Engine."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_content_engine_full_smoke(db_session: AsyncSession) -> None:
    """Comprehensive smoke test verifying complete Content Engine lifecycle and pinning guarantees."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Channel with DNA v1
        slug = f"content-smoke-{uuid.uuid4().hex[:8]}"
        chan_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Cloud Engineering Deep Dives",
                "slug": slug,
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
                "dna": {
                    "brand_voice": {
                        "tone": ["AUTHORITATIVE"],
                        "pace": "MODERATE",
                        "complexity": "ADVANCED",
                    },
                    "constraints": {
                        "avoided_vocabulary": ["synergy", "paradigm shift"],
                        "forbidden_topics": ["crypto gambling"],
                    },
                },
            },
        )
        assert chan_res.status_code == 201
        channel_id = chan_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        # Get DNA Revision v1 ID
        dna_revs = (await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")).json()
        assert len(dna_revs) == 1
        dna_v1_id = dna_revs[0]["id"]

        # 2. Ingest and Select Topic Candidate
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "PostgreSQL 16 High-Throughput Connection Pooling",
                "summary": "Deep dive into asyncpg, PgBouncer, and transaction modes.",
                "keywords": ["PostgreSQL", "AsyncIO", "PgBouncer", "Database"],
            },
        )
        assert cand_res.status_code == 201
        cand_id = cand_res.json()["id"]

        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        await client.post(f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/select")

        # 3. Create Research Request, Add Official Source & Claims
        r_req_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={
                "topic_candidate_id": cand_id,
                "research_question": "What is the benchmarked impact of transaction-level connection pooling?",
                "scope": "PostgreSQL 16, PgBouncer, asyncpg",
            },
        )
        assert r_req_res.status_code == 201
        research_req_id = r_req_res.json()["id"]

        src1_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/sources",
            json={
                "source_type": "MANUAL",
                "url": "https://www.postgresql.org/docs/16/pool.html",
                "title": "PostgreSQL 16 Connection Management",
                "publisher": "PostgreSQL Global Development Group",
                "primary_source_status": "CONFIRMED",
                "content_excerpt": "Transaction pooling enables up to 10,000 concurrent client connections with minimal backend memory overhead.",
            },
        )
        source1_id = src1_res.json()["id"]

        src2_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/sources",
            json={
                "source_type": "MANUAL",
                "url": "https://benchmarks.io/postgres16-pooling",
                "title": "Empirical Benchmarks for Database Pooling",
                "publisher": "Cloud Database Engineering Labs",
                "primary_source_status": "CLAIMED",
                "content_excerpt": "Empirical testing proves transaction pooling maintains high throughput across 10,000 active connections.",
            },
        )
        source2_id = src2_res.json()["id"]

        claim_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/claims",
            json={
                "claim_text": "Transaction pooling enables 10,000 concurrent client connections with low backend overhead.",
                "claim_type": "FACT",
            },
        )
        assert claim_res.status_code == 201
        claim_id = claim_res.json()["id"]

        ev1_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/claims/{claim_id}/evidence",
            json={
                "source_id": source1_id,
                "support_direction": "SUPPORTS",
                "excerpt": "Transaction pooling enables up to 10,000 concurrent client connections.",
                "strength_score": 95.0,
            },
        )
        assert ev1_res.status_code == 201

        ev2_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/claims/{claim_id}/evidence",
            json={
                "source_id": source2_id,
                "support_direction": "SUPPORTS",
                "excerpt": "Empirical testing proves transaction pooling maintains high throughput.",
                "strength_score": 90.0,
            },
        )
        assert ev2_res.status_code == 201

        # Run Research to produce ResearchBrief v1
        brief_run_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/run"
        )
        assert brief_run_res.status_code == 200
        brief_v1 = brief_run_res.json()
        brief_v1_id = brief_v1["id"]
        assert brief_v1["version"] == 1
        assert len(brief_v1["verified_claims"]) >= 1

        # 4. Create Content Generation Request pinned to DNA v1 & Brief v1
        content_req_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={
                "topic_candidate_id": cand_id,
                "research_brief_id": brief_v1_id,
                "content_type": "YOUTUBE_LONGFORM",
                "target_duration_seconds": 480,
            },
        )
        assert content_req_res.status_code == 201
        content_req = content_req_res.json()
        content_req_id = content_req["id"]
        assert content_req["channel_dna_revision_id"] == dna_v1_id
        assert content_req["research_brief_id"] == brief_v1_id

        # 5. Generate Full Content Pipeline
        gen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate"
        )
        assert gen_res.status_code == 200
        script_v1 = gen_res.json()
        assert script_v1["version"] == 1
        assert script_v1["is_current"] is True
        assert script_v1["supersedes_script_id"] is None
        assert script_v1["qa_status"] in ("PASSED", "PASSED_WITH_WARNINGS")

        # 6. Verify Full 100% Provenance Chain
        factual_stmts = [
            st
            for sec in script_v1["sections"]
            for st in sec["statements"]
            if st["statement_type"] == "FACTUAL"
        ]
        assert len(factual_stmts) > 0
        for st in factual_stmts:
            assert len(st["citations"]) > 0
            for cit in st["citations"]:
                assert cit["research_brief_id"] == brief_v1_id
                assert cit["claim_id"] is not None
                assert cit["evidence_id"] is not None
                assert cit["source_id"] is not None

        # 7. Select Hook Variant
        hooks = (
            await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks")
        ).json()
        assert len(hooks) >= 3
        sel_hook_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks/{hooks[1]['id']}/select",
            json={"selected": True},
        )
        assert sel_hook_res.status_code == 200

        # 8. Check Outline
        outline = (
            await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}/outline")
        ).json()
        assert len(outline["sections"]) == 4

        # 9. Update Channel DNA to v2
        await client.patch(
            f"/api/v1/channels/{channel_id}/dna",
            json={
                "dna": {"brand_voice": {"tone": ["ENTHUSIASTIC_NARRATOR"], "pace": "FAST"}},
                "change_reason": "DNA v2 update",
                "actor": "TEST_SMOKE",
            },
        )

        # 10. Run Research again to produce ResearchBrief v2
        brief_v2_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/run"
        )
        brief_v2_id = brief_v2_res.json()["id"]
        assert brief_v2_res.json()["version"] == 2
        assert brief_v2_id != brief_v1_id

        # 11. Regenerate Script v2 on Content Request
        regen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/regenerate"
        )
        assert regen_res.status_code == 200
        script_v2 = regen_res.json()
        assert script_v2["version"] == 2
        assert script_v2["is_current"] is True
        assert script_v2["supersedes_script_id"] == script_v1["id"]

        # Verify Script v2 still uses DNA v1 and Brief v1
        assert script_v2["style_snapshot"]["tone"] == "AUTHORITATIVE"
        assert script_v2["style_snapshot"]["pace"] == "MODERATE"

        for sec in script_v2["sections"]:
            for st in sec["statements"]:
                for cit in st["citations"]:
                    assert cit["research_brief_id"] == brief_v1_id
                    assert cit["research_brief_id"] != brief_v2_id

        # 12. Verify Script v1 is preserved unchanged and marked is_current = False
        v1_check = (
            await client.get(f"/api/v1/channels/{channel_id}/content/{content_req_id}/scripts/1")
        ).json()
        assert v1_check["id"] == script_v1["id"]
        assert v1_check["is_current"] is False
        assert v1_check["hook_text"] == script_v1["hook_text"]
