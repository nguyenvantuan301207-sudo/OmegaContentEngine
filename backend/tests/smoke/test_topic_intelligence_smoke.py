"""End-to-End Smoke Test for OMEGA-004 Topic Intelligence Engine."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_topic_intelligence_e2e_smoke(db_session: AsyncSession) -> None:
    """Full E2E smoke test covering Channel creation, topic ingestion, duplicate classification,

    scoring, recommendation ranking, selection, TopicMemory synchronization, and candidate archival.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Create and Activate Channel
        slug = f"smoke-topic-{uuid.uuid4().hex[:8]}"
        chan_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "FastAPI Masterclass",
                "slug": slug,
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        assert chan_res.status_code == 201
        channel_id = chan_res.json()["id"]

        act_res = await client.post(f"/api/v1/channels/{channel_id}/activate")
        assert act_res.status_code == 200

        # Step 2: Ingest 5 Candidates
        # Candidate 1: Fresh Base Topic
        c1_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Complete Guide to FastAPI in 2026",
                "summary": "Building modern REST APIs with Python",
                "keywords": ["FastAPI", "Python", "API"],
            },
        )
        assert c1_res.status_code == 201
        c1_id = c1_res.json()["id"]

        # Candidate 2: Exact Duplicate Title & Keywords
        c2_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Complete Guide to FastAPI in 2026",
                "keywords": ["FastAPI", "Python", "API"],
            },
        )
        assert c2_res.status_code == 201
        c2_id = c2_res.json()["id"]

        # Candidate 3: Paraphrase Wording without angle evidence (Semantic Duplicate)
        c3_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "FastAPI Complete Tutorial in 2026",
                "keywords": ["FastAPI", "Python", "API"],
            },
        )
        assert c3_res.status_code == 201
        c3_id = c3_res.json()["id"]

        # Candidate 4: Same base topic + Distinct Supplied Angle (Same Topic New Angle)
        c4_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Complete Guide to FastAPI in 2026",
                "keywords": ["FastAPI", "Python", "API"],
                "angles": [
                    {
                        "angle": "Async SQLAlchemy 2.0 with PostgreSQL",
                        "hook": "Database performance optimization in FastAPI",
                    }
                ],
            },
        )
        assert c4_res.status_code == 201
        c4_id = c4_res.json()["id"]

        # Candidate 5: Completely Fresh Topic
        c5_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Docker and Kubernetes Fundamentals for Developers",
                "keywords": ["Docker", "Kubernetes", "DevOps"],
            },
        )
        assert c5_res.status_code == 201
        c5_id = c5_res.json()["id"]

        # Step 3: Run Batch Evaluation
        eval_batch_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/evaluate-batch",
            json={"mode": "INTERACTIVE"},
        )
        assert eval_batch_res.status_code == 200
        evaluated_list = eval_batch_res.json()
        assert len(evaluated_list) == 5

        # Step 4: Verify Duplicate Classifications
        c1_eval = next(c for c in evaluated_list if c["id"] == c1_id)
        c2_eval = next(c for c in evaluated_list if c["id"] == c2_id)
        c3_eval = next(c for c in evaluated_list if c["id"] == c3_id)
        c4_eval = next(c for c in evaluated_list if c["id"] == c4_id)
        c5_eval = next(c for c in evaluated_list if c["id"] == c5_id)

        assert c1_eval["duplicate_status"] in ("FRESH_TOPIC", "EXACT_DUPLICATE")
        assert c2_eval["duplicate_status"] == "EXACT_DUPLICATE"
        assert c3_eval["duplicate_status"] == "SEMANTIC_DUPLICATE"
        assert c4_eval["duplicate_status"] == "SAME_TOPIC_NEW_ANGLE"
        assert c5_eval["duplicate_status"] == "FRESH_TOPIC"

        # Step 5: Query Recommendations
        recs_res = await client.get(
            f"/api/v1/channels/{channel_id}/topics/recommendations?min_score=0.0&limit=10"
        )
        assert recs_res.status_code == 200
        recs = recs_res.json()
        assert len(recs) == 5
        # The top recommendation should not be an exact duplicate
        assert recs[0]["duplicate_status"] != "EXACT_DUPLICATE"

        # Step 6: Select Top Recommendation
        top_cand_id = recs[0]["id"]
        sel_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{top_cand_id}/select"
        )
        assert sel_res.status_code == 200
        assert sel_res.json()["status"] == "SELECTED"

        # Step 7: Verify Topic Memory update
        mem_res = await client.get(f"/api/v1/channels/{channel_id}/topics/memory")
        assert mem_res.status_code == 200
        mems = mem_res.json()
        assert len(mems) >= 2
        selected_mem = next(m for m in mems if m["times_selected"] > 0)
        assert selected_mem["times_selected"] == 1
        assert selected_mem["last_selected_at"] is not None

        # Step 8: Soft-Archive exact duplicate candidate
        arch_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{c2_id}/archive"
        )
        assert arch_res.status_code == 200
        assert arch_res.json()["status"] == "ARCHIVED"
