"""Focused unit tests for P20-D1 Production Hardening Implementation."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from omega.application.analytics.rollup_service import RollupService
from omega.application.observability.operator_status_service import collect_operator_status
from omega.config import (
    SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP,
    SCHEMA_MINIMUM_INTERVAL_SECONDS,
    Settings,
    get_settings,
)
from omega.infrastructure.celery_app import celery_app
from scripts.p20_release_tool import (
    check_publisher_absence,
    verify_backup_readability,
    verify_deployment_safety,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# ── B1 & B6: Production Compose Contract Tests ──

def test_compose_prod_contract_no_publisher():
    """Assert publisher worker is excluded from primary production compose topology (B6)."""
    compose_path = _repo_root() / "docker-compose.prod.yml"
    content = compose_path.read_text(encoding="utf-8")
    assert "omega-publisher-worker:" not in content
    assert "omega-publisher" not in content


def test_compose_prod_network_isolation():
    """Assert PostgreSQL and Redis ports are not published to host interfaces (B3)."""
    compose_path = _repo_root() / "docker-compose.prod.yml"
    content = compose_path.read_text(encoding="utf-8")

    # DB and Redis must not have published ports
    assert 'ports:\n      - "5432:5432"' not in content
    assert 'ports:\n      - "6379:6379"' not in content

    # API must bind to loopback/protected interface
    assert "${API_BIND_HOST:-127.0.0.1}:8000:8000" in content


def test_compose_prod_feature_gates_configurable_and_default_off():
    """Assert all six production gates use ${NAME:-false} format (B1)."""
    compose_path = _repo_root() / "docker-compose.prod.yml"
    content = compose_path.read_text(encoding="utf-8")

    gates = [
        "RECURRING_SCHEDULER_ENABLED",
        "ANALYTICS_API_ENABLED",
        "ANALYTICS_ROLLUP_ENABLED",
        "CAMPAIGN_ORCHESTRATION_ENABLED",
        "PRODUCTION_DISPATCH_RECOVERY_ENABLED",
        "PRODUCTION_LEASE_SWEEP_ENABLED",
    ]
    for gate in gates:
        assert f"{gate}: ${{{gate}:-false}}" in content, f"Missing {gate} default-off config"


def test_compose_prod_timing_defaults_pinned():
    """Assert timing and batch parameters are pinned as configurable defaults (H7)."""
    compose_path = _repo_root() / "docker-compose.prod.yml"
    content = compose_path.read_text(encoding="utf-8")

    assert "PRODUCTION_DISPATCH_TIMEOUT_SECONDS: ${PRODUCTION_DISPATCH_TIMEOUT_SECONDS:-30}" in content
    assert "CAMPAIGN_RECONCILIATION_INTERVAL_SECONDS: ${CAMPAIGN_RECONCILIATION_INTERVAL_SECONDS:-5}" in content
    assert "PRODUCTION_LEASE_SWEEP_INTERVAL_SECONDS: ${PRODUCTION_LEASE_SWEEP_INTERVAL_SECONDS:-60}" in content
    assert "PRODUCTION_LEASE_TIMEOUT_SECONDS: ${PRODUCTION_LEASE_TIMEOUT_SECONDS:-300}" in content
    assert "RECURRING_SCHEDULER_INTERVAL_SECONDS: ${RECURRING_SCHEDULER_INTERVAL_SECONDS:-60}" in content
    assert "RECURRING_SCHEDULER_CATCH_UP_CAP: ${RECURRING_SCHEDULER_CATCH_UP_CAP:-5}" in content
    assert "ANALYTICS_ROLLUP_INTERVAL_MINUTES: ${ANALYTICS_ROLLUP_INTERVAL_MINUTES:-60}" in content
    assert "ANALYTICS_ROLLUP_LOOKBACK_HOURS: ${ANALYTICS_ROLLUP_LOOKBACK_HOURS:-168}" in content


def test_compose_prod_graceful_shutdown_and_logging():
    """Assert graceful shutdown periods and Docker log rotation are configured (H1)."""
    compose_path = _repo_root() / "docker-compose.prod.yml"
    content = compose_path.read_text(encoding="utf-8")

    assert "stop_grace_period: 60s" in content  # omega-worker
    assert "stop_grace_period: 15s" in content  # omega-api / beat / redis
    assert "stop_grace_period: 30s" in content  # omega-postgres

    assert 'max-size: "50m"' in content
    assert 'max-file: "5"' in content


# ── B4 & B6: Release Preflight Tool Tests ──

def test_publisher_absence_preflight_unit():
    """Test publisher absence check behavior on pass and failure."""
    with patch("subprocess.run") as mock_subproc, patch("redis.Redis.from_url") as mock_redis_cls:
        # Case 1: Clean - no container, 0 queue depth
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 0
        mock_redis_cls.return_value = mock_redis

        res = check_publisher_absence(redis_url="redis://localhost:6379/0")
        assert res["passed"] is True
        assert len(res["violations"]) == 0

        # Case 2: Violation - publisher container exists
        mock_subproc.return_value = MagicMock(
            returncode=0,
            stdout="bab5b79e098e|omega-publisher-worker|Exited (0)|omegacontentengine-omega-publisher-worker\n",
        )
        res = check_publisher_absence(redis_url="redis://localhost:6379/0")
        assert res["passed"] is False
        assert any("Found existing publisher containers" in v for v in res["violations"])

        # Case 3: Violation - queue depth > 0
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        mock_redis.llen.return_value = 5
        res = check_publisher_absence(redis_url="redis://localhost:6379/0")
        assert res["passed"] is False
        assert any("queue depth is non-zero (5)" in v for v in res["violations"])


def test_deployment_safety_verifier_unit(tmp_path: Path):
    """Test deployment safety verification passes on valid compose and catches violations."""
    prod_compose = _repo_root() / "docker-compose.prod.yml"
    res = verify_deployment_safety(prod_compose)
    assert res["passed"] is True, f"Prod compose failed verification: {res['violations']}"

    # Verify catching an invalid compose with bind mount and reload
    bad_compose = tmp_path / "bad.yml"
    bad_compose.write_text(
        """
services:
  omega-api:
    command: uvicorn omega.main:app --reload
    volumes:
      - ./backend:/app
    environment:
      AUTO_MIGRATE: "true"
      RECURRING_SCHEDULER_ENABLED: "true"
""",
        encoding="utf-8",
    )
    bad_res = verify_deployment_safety(bad_compose)
    assert bad_res["passed"] is False
    assert any("bind mount" in v for v in bad_res["violations"])
    assert any("--reload" in v for v in bad_res["violations"])
    assert any("AUTO_MIGRATE" in v for v in bad_res["violations"])


def test_backup_verifier_unit(tmp_path: Path):
    """Test backup verification catches missing or empty files."""
    missing = tmp_path / "nonexistent.dump"
    res = verify_backup_readability(missing)
    assert res["passed"] is False

    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")
    res_empty = verify_backup_readability(empty)
    assert res_empty["passed"] is False

    valid = tmp_path / "test.dump"
    valid.write_bytes(b"PGDMP_DUMMY_HEADER_BYTES")
    res_valid = verify_backup_readability(valid)
    assert res_valid["passed"] is True
    assert "sha256" in res_valid["details"]


# ── H4: PostgreSQL Advisory Lock Fail-Closed ──

@pytest.mark.asyncio
async def test_analytics_rollup_fails_closed_on_postgres_advisory_lock_error():
    """Assert RollupService raises RuntimeError on PostgreSQL when advisory lock fails (H4)."""
    mock_session = AsyncMock()
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind = MagicMock(return_value=mock_bind)

    # Simulate advisory lock execution failure
    mock_session.execute.side_effect = RuntimeError("connection reset by peer")

    service = RollupService()
    from datetime import datetime, UTC
    now = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    with pytest.raises(RuntimeError, match="Failed to acquire PostgreSQL transaction advisory lock"):
        await RollupService.replace_family_bucket_snapshot(
            session=mock_session,
            family="renders",
            bucket_start=now,
            bucket_end=now,
            snapshot_rows=[],
        )


# ── H5: Runtime Provenance in Operator Diagnostics ──

@pytest.mark.asyncio
async def test_ops_status_includes_runtime_provenance():
    """Assert /ops/status response includes source_commit, image_tag, and build_timestamp (H5)."""
    status = await collect_operator_status()
    assert "provenance" in status
    prov = status["provenance"]
    assert "source_commit" in prov
    assert "image_tag" in prov
    assert "build_timestamp" in prov


# ── H2: Database Connection Pool & Query Bounds ──

def test_database_connection_pool_and_query_timeout_settings():
    """Assert database connection pool and query timeout bounds are present in Settings (H2)."""
    settings = Settings()
    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 10
    assert settings.db_pool_timeout == 30.0
    assert settings.db_pool_recycle == 1800
    assert settings.db_statement_timeout_ms == 30000
    assert settings.db_lock_timeout_ms == 10000


# ── H3: Celery Transport Semantics ──

def test_celery_transport_semantics_explicit():
    """Assert Celery transport settings match explicit production parameters (H3)."""
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_transport_options.get("visibility_timeout") == 3600


# ── S2 & S3: Safe Cleanup Tests ──

def test_recurring_schedule_hard_bounds_single_authority():
    """Assert SCHEMA_MINIMUM_INTERVAL_SECONDS and SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP are single authority (S2)."""
    settings = get_settings()
    assert settings.SCHEMA_MINIMUM_INTERVAL_SECONDS == SCHEMA_MINIMUM_INTERVAL_SECONDS == 60
    assert settings.SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP == SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP == 10


def test_unreferenced_telemetry_wrapper_removed():
    """Assert get_all_telemetry_counters wrapper is completely removed (S3)."""
    import omega.application.observability.telemetry as tel
    assert not hasattr(tel, "get_all_telemetry_counters")


def test_no_inline_secrets_in_tracked_files():
    """Assert no inline production credentials exist in tracked compose or config files."""
    repo = _repo_root()
    files_to_check = [
        repo / "docker-compose.prod.yml",
        repo / "docker-compose.yml",
        repo / "docker-compose.publisher.yml",
        repo / "backend" / "Dockerfile",
    ]
    for path in files_to_check:
        if path.exists():
            text_content = path.read_text(encoding="utf-8")
            assert "OMEGA_KEYRING:" not in text_content
            assert "POSTGRES_PASSWORD: omega_prod" not in text_content
            assert "METRICS_AUTH_TOKEN: omega_" not in text_content
