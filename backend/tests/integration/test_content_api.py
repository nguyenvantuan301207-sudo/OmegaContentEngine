"""Integration tests for Content Engine REST API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_content_api_lifecycle(db_session: AsyncSession) -> None:
    """Test full Content API lifecycle: Request -> Generate -> Intent -> Hooks -> Outline -> Script -> Citations -> QA."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create and Activate Channel
        slug = f"content-api-{uuid.uuid4().hex[:8]}"
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

        # 2. Ingest and Evaluate Topic Candidate
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "PostgreSQL 16 Async Connection Pooling Deep Dive",
                "summary": "Benchmarking asyncpg and PgBouncer under high concurrency.",
                "keywords": ["PostgreSQL", "AsyncIO", "pgvector"],
            },
        )
        assert cand_res.status_code == 201
        cand_id = cand_res.json()["id"]

        eval_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        assert eval_res.status_code == 200

        # 3. Create Research Request, Add Sources & Claims, Run Research
        r_req_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={
                "topic_candidate_id": cand_id,
                "research_question": "What is the optimal connection pooling configuration?",
                "scope": "PostgreSQL 16, asyncpg",
            },
        )
        assert r_req_res.status_code == 201
        research_req_id = r_req_res.json()["id"]

        src1_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/sources",
            json={
                "source_type": "MANUAL",
                "url": "https://www.postgresql.org/docs/16/async.html",
                "title": "PostgreSQL 16 Official Manual",
                "publisher": "PostgreSQL Global Development Group",
                "content_excerpt": "Official PostgreSQL documentation covering async query throughput and connection pool management.",
            },
        )
        assert src1_res.status_code == 201
        source1_id = src1_res.json()["id"]

        src2_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/sources",
            json={
                "source_type": "MANUAL",
                "url": "https://benchmarks.io/async-bench",
                "title": "Async Architecture Benchmarks",
                "publisher": "Engineering Performance Institute",
                "content_excerpt": "Connection pooling doubles query throughput under concurrent workloads in benchmark suites.",
            },
        )
        assert src2_res.status_code == 201
        source2_id = src2_res.json()["id"]

        claim_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{research_req_id}/claims",
            json={
                "claim_text": "PostgreSQL 16 connection pooling doubles query throughput under concurrent workloads.",
                "claim_type": "FACT",
                "evidence": [
                    {
                        "source_id": source1_id,
                        "support_direction": "SUPPORTS",
                        "excerpt": "Connection pooling doubles query throughput under concurrent workloads.",
                        "strength_score": 95.0,
                    },
                    {
                        "source_id": source2_id,
                        "support_direction": "SUPPORTS",
                        "excerpt": "Connection pooling doubles query throughput under concurrent workloads.",
                        "strength_score": 90.0,
                    },
                ],
            },
        )
        assert claim_res.status_code == 201

        # Run Research to produce ResearchBrief v1
        run_res = await client.post(f"/api/v1/channels/{channel_id}/research/{research_req_id}/run")
        assert run_res.status_code == 200
        brief_id = run_res.json()["id"]

        # 4. Create Content Generation Request
        content_req_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={
                "topic_candidate_id": cand_id,
                "research_brief_id": brief_id,
                "content_type": "YOUTUBE_LONGFORM",
                "target_duration_seconds": 480,
            },
        )
        assert content_req_res.status_code == 201
        content_req = content_req_res.json()
        assert content_req["status"] == "DRAFT"
        assert content_req["research_brief_id"] == brief_id
        content_req_id = content_req["id"]

        # 5. List Content Requests
        list_res = await client.get(f"/api/v1/channels/{channel_id}/content")
        assert list_res.status_code == 200
        assert len(list_res.json()) == 1

        # 6. Generate Content Pipeline (Intent -> Hooks -> Outline -> Script v1 -> QA)
        gen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/generate"
        )
        assert gen_res.status_code == 200
        script_v1 = gen_res.json()
        assert script_v1["version"] == 1
        assert script_v1["is_current"] is True
        assert len(script_v1["sections"]) == 4

        # 7. Check Intent
        intent_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/intent"
        )
        assert intent_res.status_code == 200
        assert intent_res.json()["tone"] is not None

        # 8. Check Hooks and Select Hook
        hooks_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks"
        )
        assert hooks_res.status_code == 200
        hooks = hooks_res.json()
        assert len(hooks) >= 3

        sel_hook_id = hooks[1]["id"]
        sel_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/hooks/{sel_hook_id}/select",
            json={"selected": True},
        )
        assert sel_res.status_code == 200
        assert sel_res.json()["selected"] is True

        # 9. Check Outline
        outline_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/outline"
        )
        assert outline_res.status_code == 200
        assert len(outline_res.json()["sections"]) == 4

        # 10. Check Script Version & QA
        qa_res = await client.get(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/scripts/1/qa"
        )
        assert qa_res.status_code == 200
        assert qa_res.json()["status"] in ("PASSED", "PASSED_WITH_WARNINGS")

        # 11. Re-run QA
        rerun_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{content_req_id}/scripts/1/qa"
        )
        assert rerun_res.status_code == 200
