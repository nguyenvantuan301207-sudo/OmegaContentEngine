"""Unit tests for P20-B Read-Only Pipeline Analytics API endpoints and feature gates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from omega.api.dependencies import get_db
from omega.main import create_app


@pytest.fixture
def app():
    """Create a FastAPI application instance."""
    return create_app()


@pytest.mark.asyncio
async def test_analytics_api_gate_off_returns_503(app, monkeypatch: pytest.MonkeyPatch):
    """When ANALYTICS_API_ENABLED=false, endpoints must return 503 Service Unavailable."""
    monkeypatch.setenv("ANALYTICS_API_ENABLED", "false")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # GET /api/v1/analytics/summary
        r_sum = await client.get("/api/v1/analytics/summary")
        assert r_sum.status_code == 503
        assert "disabled" in r_sum.json()["detail"].lower()

        # GET /api/v1/analytics/live
        r_live = await client.get("/api/v1/analytics/live")
        assert r_live.status_code == 503

        # GET /api/v1/analytics/historical
        r_hist = await client.get(
            "/api/v1/analytics/historical",
            params={"start_time": "2026-10-01T00:00:00Z", "end_time": "2026-10-02T00:00:00Z"},
        )
        assert r_hist.status_code == 503


@pytest.mark.asyncio
async def test_analytics_api_schema_not_ready_returns_503(app, monkeypatch: pytest.MonkeyPatch):
    """When gate is enabled but migration 026 table is missing, endpoints must return 503."""
    monkeypatch.setenv("ANALYTICS_API_ENABLED", "true")

    with patch(
        "omega.api.analytics.check_analytics_schema_capability",
        new=AsyncMock(return_value=(False, "table_missing: pipeline_analytics_rollups")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/analytics/summary")
            assert r.status_code == 503
            assert "schema not ready" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_analytics_api_historical_rejects_inverted_range(app, monkeypatch: pytest.MonkeyPatch):
    """Historical API must reject end_time <= start_time with 422."""
    monkeypatch.setenv("ANALYTICS_API_ENABLED", "true")

    with patch(
        "omega.api.analytics.check_analytics_schema_capability",
        new=AsyncMock(return_value=(True, "ready")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/analytics/historical",
                params={
                    "start_time": "2026-10-02T00:00:00Z",
                    "end_time": "2026-10-01T00:00:00Z",  # Inverted!
                },
            )
            assert r.status_code == 422


@pytest.mark.asyncio
async def test_analytics_api_summary_success(app, monkeypatch: pytest.MonkeyPatch):
    """Summary endpoint returns expected payload when gate and schema are ready."""
    monkeypatch.setenv("ANALYTICS_API_ENABLED", "true")

    mock_counters = {
        "current_waiting_occurrences": 3,
        "legacy_unattributed_terminal_jobs": 12,
        "cumulative_redispatch_excess_generations": 5,
        "total_terminal_jobs_with_completed_at": 150,
        "data_quality": {
            "negative_latency_anomalies": 0,
            "legacy_unattributed_terminal_jobs": 12,
            "missing_expected_timestamp_rows": 0,
            "rollup_compute_failures": 0,
        },
    }

    with (
        patch(
            "omega.api.analytics.check_analytics_schema_capability",
            new=AsyncMock(return_value=(True, "ready")),
        ),
        patch(
            "omega.application.analytics.metrics_engine.PipelineMetricsCalculationEngine.compute_summary_counters",
            new=AsyncMock(return_value=mock_counters),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/analytics/summary")
            assert r.status_code == 200
            data = r.json()
            assert data["current_waiting_occurrences"] == 3
            assert data["legacy_unattributed_terminal_jobs"] == 12
            assert data["data_quality"]["negative_latency_anomalies"] == 0


@pytest.mark.asyncio
async def test_preexisting_analytics_routes_remain_ungated(app, monkeypatch: pytest.MonkeyPatch):
    """Pre-existing analytics endpoints must NOT be gated behind ANALYTICS_API_ENABLED=false."""
    monkeypatch.setenv("ANALYTICS_API_ENABLED", "false")

    mock_session = AsyncMock()
    mock_result_scalar = MagicMock()
    mock_result_scalar.scalar.return_value = 0
    mock_result_scalars = MagicMock()
    mock_result_scalars.scalars.return_value.all.return_value = []
    mock_session.execute.side_effect = [mock_result_scalar, mock_result_scalars]

    async def _override_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_health = await client.get("/api/v1/analytics/health")
            # Pre-existing health route must succeed with 200, NOT 503!
            assert r_health.status_code == 200
            assert r_health.json()["status"] == "healthy"
    finally:
        app.dependency_overrides.clear()
