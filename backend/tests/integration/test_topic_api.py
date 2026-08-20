"""Integration tests for Topic Intelligence REST API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_topic_candidate_crud_and_lifecycle(db_session: AsyncSession) -> None:
    """Test full TopicCandidate ingestion, evaluation, selection, rejection, and soft-archival lifecycle."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Channel
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Topic Test Channel",
                "slug": f"topic-test-{uuid.uuid4().hex[:8]}",
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        assert c_res.status_code == 201
        channel_id = c_res.json()["id"]

        # 2. Ingest Single Candidate
        ingest_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Understanding Python AsyncIO Internals",
                "summary": "Deep dive into event loop and task scheduling",
                "source_type": "MANUAL",
                "keywords": ["Python", "AsyncIO", "Concurrency"],
                "angles": [
                    {
                        "angle": "Event loop under the hood",
                        "hook": "How AsyncIO actually works in Python 3.12",
                    }
                ],
            },
        )
        assert ingest_res.status_code == 201
        cand = ingest_res.json()
        cand_id = cand["id"]
        assert cand["status"] == "DISCOVERED"
        assert cand["title"] == "Understanding Python AsyncIO Internals"
        assert len(cand["angles"]) == 1

        # 3. Batch Ingest Candidates
        batch_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/import",
            json={
                "candidates": [
                    {
                        "title": "Building Custom Pytest Plugins",
                        "keywords": ["Python", "Testing", "Pytest"],
                    },
                    {
                        "title": "Docker Multi-Stage Builds Guide",
                        "keywords": ["Docker", "DevOps"],
                    },
                ]
            },
        )
        assert batch_res.status_code == 201
        assert len(batch_res.json()) == 2

        # 4. List Candidates
        list_res = await client.get(f"/api/v1/channels/{channel_id}/topics/candidates")
        assert list_res.status_code == 200
        assert len(list_res.json()) >= 3

        # 5. Evaluate Single Candidate
        eval_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        assert eval_res.status_code == 200
        eval_cand = eval_res.json()
        assert eval_cand["status"] in ("RECOMMENDED", "EVALUATED")
        assert eval_cand["final_score"] is not None
        assert "audience_fit" in eval_cand["score_breakdown"]
        assert len(eval_cand["reasons"]) > 0

        # 6. Evaluate Batch
        batch_eval_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/evaluate-batch",
            json={"mode": "INTERACTIVE"},
        )
        assert batch_eval_res.status_code == 200

        # 7. Get Recommendations
        rec_res = await client.get(
            f"/api/v1/channels/{channel_id}/topics/recommendations?min_score=0.0"
        )
        assert rec_res.status_code == 200
        recs = rec_res.json()
        assert len(recs) >= 3
        # Assert descending sort by final_score
        scores = [r["final_score"] for r in recs]
        assert scores == sorted(scores, reverse=True)

        # 8. Select Candidate for Production
        sel_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/select"
        )
        assert sel_res.status_code == 200
        assert sel_res.json()["status"] == "SELECTED"

        # 9. Verify TopicMemory updated with times_selected = 1
        mem_res = await client.get(f"/api/v1/channels/{channel_id}/topics/memory")
        assert mem_res.status_code == 200
        mems = mem_res.json()
        assert len(mems) >= 1
        selected_mem = next((m for m in mems if m["canonical_topic"] == cand["title"]), None)
        assert selected_mem is not None
        assert selected_mem["times_selected"] == 1
        assert selected_mem["last_selected_at"] is not None

        # 10. Reject another candidate
        cand_2_id = batch_res.json()[0]["id"]
        rej_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_2_id}/reject",
            json={"reason": "Topic outside current sprint focus"},
        )
        assert rej_res.status_code == 200
        assert rej_res.json()["status"] == "REJECTED"

        # 11. Soft-Archive third candidate (No hard delete)
        cand_3_id = batch_res.json()[1]["id"]
        arch_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_3_id}/archive"
        )
        assert arch_res.status_code == 200
        assert arch_res.json()["status"] == "ARCHIVED"
        assert arch_res.json()["archived_at"] is not None
