"""Integration tests for Topic Intelligence concurrency, idempotency, and times_discovered semantics."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_times_discovered_lifecycle_and_retry_idempotency(db_session: AsyncSession) -> None:
    """Test that TopicMemory.times_discovered increments per distinct discovery event, but not on retries."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Channel
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Discovery Test Channel",
                "slug": f"discovery-test-{uuid.uuid4().hex[:8]}",
            },
        )
        assert c_res.status_code == 201
        channel_id = c_res.json()["id"]

        # 2. Ingest candidate with idempotency_key
        idem_key = f"key-{uuid.uuid4().hex}"
        cand_payload = {
            "title": "Machine Learning Model Quantization",
            "keywords": ["ML", "Quantization"],
            "idempotency_key": idem_key,
        }
        res1 = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates", json=cand_payload
        )
        assert res1.status_code == 201

        # Check TopicMemory: times_discovered should be 1
        mem_res1 = await client.get(f"/api/v1/channels/{channel_id}/topics/memory")
        assert mem_res1.status_code == 200
        mem1 = mem_res1.json()[0]
        assert mem1["times_discovered"] == 1

        # 3. Retry identical request with same idempotency key (e.g. network retry)
        res2 = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates", json=cand_payload
        )
        assert res2.status_code == 201  # Idempotent response

        # Check TopicMemory: times_discovered must STILL be 1 (No inflation on retry)
        mem_res2 = await client.get(f"/api/v1/channels/{channel_id}/topics/memory")
        mem2 = mem_res2.json()[0]
        assert mem2["times_discovered"] == 1

        # 4. Ingest a genuinely distinct candidate mapped to the same canonical topic/fingerprint
        distinct_cand_payload = {
            "title": "Machine Learning Model Quantization",
            "keywords": ["ML", "Quantization"],
            "source_type": "IMPORT",
            "source_name": "conference_papers_2026",
            "idempotency_key": f"key-{uuid.uuid4().hex}",  # Different idempotency key
        }
        res3 = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates", json=distinct_cand_payload
        )
        assert res3.status_code == 201

        # Check TopicMemory: times_discovered must now be 2
        mem_res3 = await client.get(f"/api/v1/channels/{channel_id}/topics/memory")
        mem3 = mem_res3.json()[0]
        assert mem3["times_discovered"] == 2


@pytest.mark.asyncio
async def test_concurrent_candidate_selection_idempotency(db_session: AsyncSession) -> None:
    """Test that concurrent selection requests on the same candidate increment times_selected exactly once."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Channel & Candidate
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Selection Concurrency Channel",
                "slug": f"select-conc-{uuid.uuid4().hex[:8]}",
            },
        )
        channel_id = c_res.json()["id"]

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "High Throughput Data Pipelines"},
        )
        candidate_id = cand_res.json()["id"]

        # 2. Fire 3 concurrent selection requests
        async def select_cand() -> int:
            r = await client.post(
                f"/api/v1/channels/{channel_id}/topics/candidates/{candidate_id}/select"
            )
            return r.status_code

        results = await asyncio.gather(
            select_cand(),
            select_cand(),
            select_cand(),
        )

        assert all(code == 200 for code in results)

        # 3. Verify TopicMemory.times_selected is EXACTLY 1 (no double increment)
        mem_res = await client.get(f"/api/v1/channels/{channel_id}/topics/memory")
        assert mem_res.status_code == 200
        mem = mem_res.json()[0]
        assert mem["times_selected"] == 1
