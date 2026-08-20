"""End-to-End Smoke Test for OMEGA-005 Research Engine."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_research_engine_e2e_smoke(db_session: AsyncSession) -> None:
    """Smoke test: Channel -> Selected Topic -> Sources -> Evidence -> Conflict -> Brief v1 -> Rerun v2 -> Citation Provenance."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Create & Activate Channel
        slug = f"smoke-research-{uuid.uuid4().hex[:8]}"
        chan_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Cloud Native Architecture",
                "slug": slug,
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        assert chan_res.status_code == 201
        channel_id = chan_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        # Step 2: Ingest, Evaluate, and Select Topic Candidate
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "FastAPI Performance Tuning with Async PostgreSQL in 2026",
                "keywords": ["FastAPI", "PostgreSQL", "AsyncIO", "Performance"],
            },
        )
        assert cand_res.status_code == 201
        cand_id = cand_res.json()["id"]

        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        await client.post(f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/select")

        # Step 3: Create Research Request
        req_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={
                "topic_candidate_id": cand_id,
                "research_question": "What architectural patterns maximize throughput in Async FastAPI services?",
                "scope": "ASGI, asyncpg, PgBouncer connection pooling",
            },
        )
        assert req_res.status_code == 201
        req_id = req_res.json()["id"]

        # Step 4: Add 3 Sources (1 Confirmed Primary, 1 Corroborating, 1 Contradicting)
        src1_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/sources",
            json={
                "title": "FastAPI 2026 Official Architectural Whitepaper",
                "publisher": "Tiangolo Engineering",
                "url": "https://fastapi.tiangolo.com/whitepaper-2026",
                "primary_source_status": "CONFIRMED",
                "content_excerpt": "FastAPI utilizes AnyIO and Starlette to achieve high async throughput with non-blocking coroutines.",
            },
        )
        assert src1_res.status_code == 201
        src1_id = src1_res.json()["id"]

        src2_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/sources",
            json={
                "title": "AsyncPG High Performance Benchmarks",
                "publisher": "MagicStack",
                "url": "https://magic.io/blog/asyncpg-benchmarks",
                "primary_source_status": "CONFIRMED",
                "content_excerpt": "AsyncPG implements binary PostgreSQL protocol directly over asyncio streams.",
            },
        )
        assert src2_res.status_code == 201
        src2_id = src2_res.json()["id"]

        src3_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/sources",
            json={
                "title": "Synchronous WSGI vs ASGI Comparison",
                "publisher": "Legacy Systems Digest",
                "url": "https://legacysystems.org/wsgi-vs-asgi",
                "primary_source_status": "CLAIMED",
                "content_excerpt": "ASGI provides no throughput advantages over standard gunicorn WSGI synchronous workers.",
            },
        )
        assert src3_res.status_code == 201
        src3_id = src3_res.json()["id"]

        # Step 5: Add Claims and First-Class Evidence
        # Claim A: Supported by Source 1 and Source 2
        claim_a_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/claims",
            json={
                "claim_text": "FastAPI achieves high throughput via non-blocking asynchronous event loop coroutines.",
                "claim_type": "FACT",
            },
        )
        assert claim_a_res.status_code == 201
        claim_a_id = claim_a_res.json()["id"]

        await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/claims/{claim_a_id}/evidence",
            json={
                "source_id": src1_id,
                "support_direction": "SUPPORTS",
                "excerpt": "FastAPI utilizes AnyIO and Starlette to achieve high async throughput with non-blocking coroutines.",
                "strength_score": 95.0,
            },
        )

        await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/claims/{claim_a_id}/evidence",
            json={
                "source_id": src2_id,
                "support_direction": "SUPPORTS",
                "excerpt": "AsyncPG implements binary PostgreSQL protocol directly over asyncio streams.",
                "strength_score": 90.0,
            },
        )

        # Claim B: Has conflicting evidence from Source 3
        claim_b_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/claims",
            json={
                "claim_text": "ASGI architecture offers superior throughput over traditional WSGI synchronous servers.",
                "claim_type": "FACT",
            },
        )
        assert claim_b_res.status_code == 201
        claim_b_id = claim_b_res.json()["id"]

        await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/claims/{claim_b_id}/evidence",
            json={
                "source_id": src1_id,
                "support_direction": "SUPPORTS",
                "excerpt": "FastAPI utilizes AnyIO and Starlette to achieve high async throughput with non-blocking coroutines.",
                "strength_score": 90.0,
            },
        )

        await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/claims/{claim_b_id}/evidence",
            json={
                "source_id": src3_id,
                "support_direction": "CONTRADICTS",
                "excerpt": "ASGI provides no throughput advantages over standard gunicorn WSGI synchronous workers.",
                "strength_score": 80.0,
            },
        )

        # Step 6: Run Pipeline -> Brief v1
        run1_res = await client.post(f"/api/v1/channels/{channel_id}/research/{req_id}/run")
        assert run1_res.status_code == 200
        brief_v1 = run1_res.json()
        assert brief_v1["version"] == 1
        assert brief_v1["is_current"] is True
        assert brief_v1["outcome"] in ("SUFFICIENT", "PARTIAL")

        # Verify conflict recorded
        conflicts_res = await client.get(
            f"/api/v1/channels/{channel_id}/research/{req_id}/conflicts"
        )
        assert conflicts_res.status_code == 200
        assert len(conflicts_res.json()) >= 1

        # Step 7: Rerun Pipeline -> Brief v2
        run2_res = await client.post(f"/api/v1/channels/{channel_id}/research/{req_id}/run")
        assert run2_res.status_code == 200
        brief_v2 = run2_res.json()
        assert brief_v2["version"] == 2
        assert brief_v2["is_current"] is True
        assert brief_v2["supersedes_brief_id"] == brief_v1["id"]

        # Step 8: Assert 100% Provenance Traceability on all verified claims in Brief v2
        assert len(brief_v2["verified_claims"]) >= 1
        for vc in brief_v2["verified_claims"]:
            assert "claim_id" in vc
            assert len(vc["citations"]) >= 1
            for cit in vc["citations"]:
                assert "source_id" in cit
                assert "evidence_id" in cit
                assert "publisher" in cit
                assert "excerpt" in cit
                assert len(cit["excerpt"]) > 0
