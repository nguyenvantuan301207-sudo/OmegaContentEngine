"""Integration tests for Research Engine REST API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_research_api_lifecycle(db_session: AsyncSession) -> None:
    """Test full Research API lifecycle: Request -> Sources -> Claims -> Evidence -> Run -> Brief."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create and Activate Channel
        slug = f"research-chan-{uuid.uuid4().hex[:8]}"
        chan_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Async Architecture Channel",
                "slug": slug,
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        assert chan_res.status_code == 201
        channel_id = chan_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        # 2. Ingest & Evaluate Topic Candidate (to make it eligible)
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "PostgreSQL 16 Async Connection Pooling Deep Dive",
                "summary": "Benchmarking pgvector and connection poolers in 2026",
                "keywords": ["PostgreSQL", "Async", "Database"],
            },
        )
        assert cand_res.status_code == 201
        cand_id = cand_res.json()["id"]

        # Evaluate candidate so status is EVALUATED/RECOMMENDED
        eval_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        assert eval_res.status_code == 200

        # 3. Create Research Request
        req_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={
                "topic_candidate_id": cand_id,
                "research_question": "What is the optimal connection pooling configuration for high-concurrency FastAPI apps?",
                "scope": "PostgreSQL 16, PgBouncer, asyncpg",
            },
        )
        assert req_res.status_code == 201
        req_data = req_res.json()
        req_id = req_data["id"]
        assert req_data["status"] == "PENDING"

        # 4. Add Research Sources
        src1_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/sources",
            json={
                "title": "Official PostgreSQL 16 Documentation",
                "publisher": "PostgreSQL Global Development Group",
                "url": "https://www.postgresql.org/docs/16/runtime-config-connection.html",
                "primary_source_status": "CONFIRMED",
                "content_excerpt": "PostgreSQL uses a process-based model where each client connection consumes dedicated server memory.",
            },
        )
        assert src1_res.status_code == 201
        src1_id = src1_res.json()["id"]

        src2_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/sources",
            json={
                "title": "Benchmarking PgBouncer with AsyncPG",
                "publisher": "Tech Engineering Lab",
                "url": "https://techenglab.org/benchmarks/pgbouncer",
                "primary_source_status": "CLAIMED",
                "content_excerpt": "Deploying PgBouncer transaction pooling yielded a 40% reduction in peak connection latency.",
            },
        )
        assert src2_res.status_code == 201
        assert "id" in src2_res.json()

        # 5. Add Claim & Link First-Class Evidence
        claim_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/claims",
            json={
                "claim_text": "PostgreSQL processes client connections via dedicated server processes.",
                "claim_type": "FACT",
            },
        )
        assert claim_res.status_code == 201
        claim_id = claim_res.json()["id"]

        ev_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/claims/{claim_id}/evidence",
            json={
                "source_id": src1_id,
                "support_direction": "SUPPORTS",
                "excerpt": "PostgreSQL uses a process-based model where each client connection consumes dedicated server memory.",
                "strength_score": 95.0,
            },
        )
        assert ev_res.status_code == 201

        # 6. Run Research Pipeline
        run_res = await client.post(f"/api/v1/channels/{channel_id}/research/{req_id}/run")
        assert run_res.status_code == 200
        brief_data = run_res.json()
        assert brief_data["version"] == 1
        assert brief_data["is_current"] is True
        assert len(brief_data["verified_claims"]) >= 1

        # Assert full citation provenance
        first_verified = brief_data["verified_claims"][0]
        assert len(first_verified["citations"]) >= 1
        cit = first_verified["citations"][0]
        assert cit["source_id"] == src1_id

        # 7. Retrieve Brief via GET endpoint
        get_brief_res = await client.get(f"/api/v1/channels/{channel_id}/research/{req_id}/brief")
        assert get_brief_res.status_code == 200
        assert get_brief_res.json()["id"] == brief_data["id"]
