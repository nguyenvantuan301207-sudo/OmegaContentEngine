"""Integration tests for API endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import is_docker


@pytest.mark.skipif(not is_docker(), reason="Requires Docker services")
class TestAPI:
    """Test API endpoints with live services."""

    @pytest.mark.asyncio
    async def test_health(self) -> None:
        """GET /health should return ok."""
        from omega.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_health_has_request_id(self) -> None:
        """Response should include X-Request-ID header."""
        from omega.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")
        assert "x-request-id" in response.headers

    @pytest.mark.asyncio
    async def test_system_info(self) -> None:
        """GET /api/v1/system/info should return app metadata."""
        from omega.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/system/info")
        assert response.status_code == 200
        data = response.json()
        assert data["app"] == "omega"
        assert "version" in data
        assert "environment" in data

    @pytest.mark.asyncio
    async def test_system_status(self) -> None:
        """GET /api/v1/system/status should return dependency checks."""
        from omega.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/system/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "checks" in data
        assert "postgres" in data["checks"]
        assert "redis" in data["checks"]
        assert "worker" in data["checks"]

    @pytest.mark.asyncio
    async def test_create_test_job(self) -> None:
        """POST /api/v1/jobs/test should create a job."""
        from omega.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/v1/jobs/test")
        assert response.status_code == 201
        data = response.json()
        assert "job_id" in data
        assert "state" in data

    @pytest.mark.asyncio
    async def test_get_job(self) -> None:
        """GET /api/v1/jobs/{job_id} should return job details."""
        from omega.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Create a job first
            create_response = await ac.post("/api/v1/jobs/test")
            job_id = create_response.json()["job_id"]

            # Fetch it
            response = await ac.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["job_type"] == "test"

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self) -> None:
        """GET /api/v1/jobs/{bad_id} should return 404."""
        from omega.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
