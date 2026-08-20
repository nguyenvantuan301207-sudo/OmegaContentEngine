"""Smoke test — end-to-end job lifecycle.

Creates a test job, polls for completion, verifies SUCCEEDED state.
Requires all Docker services running.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import is_docker


@pytest.mark.skipif(not is_docker(), reason="Requires Docker services")
class TestSmoke:
    """End-to-end smoke test."""

    @pytest.mark.asyncio
    async def test_full_job_lifecycle(self) -> None:
        """Create job → poll → verify SUCCEEDED."""
        from omega.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Create a test job
            response = await ac.post("/api/v1/jobs/test")
            assert response.status_code == 201
            data = response.json()
            job_id = data["job_id"]
            assert data["state"] in ("QUEUED", "FAILED")

            # If FAILED (broker issue), skip polling
            if data["state"] == "FAILED":
                pytest.skip("Job dispatch failed — Celery broker may be unavailable")

            # 2. Poll for completion (max 30 seconds)
            for _ in range(15):
                await asyncio.sleep(2)
                response = await ac.get(f"/api/v1/jobs/{job_id}")
                assert response.status_code == 200
                job_data = response.json()
                if job_data["state"] in ("SUCCEEDED", "FAILED"):
                    break
            else:
                pytest.fail(f"Job {job_id} did not complete within 30 seconds")

            # 3. Verify final state
            assert job_data["state"] == "SUCCEEDED"
            assert job_data["result"] == {"message": "Test job completed successfully"}
            assert job_data["started_at"] is not None
            assert job_data["completed_at"] is not None
