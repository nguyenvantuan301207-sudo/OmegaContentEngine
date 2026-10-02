"""Focused regression tests for P20-C2 review corrections."""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_schema_capability_inventory_is_limited_to_requested_tables():
    from omega.application.observability.schema_capability import _check

    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    result = MagicMock()
    result.all.return_value = [("missions", "id"), ("missions", "state")]
    session.execute = AsyncMock(return_value=result)

    assert await _check(session, {"missions": {"id", "state"}}) == (True, "ready")
    statement, parameters = session.execute.await_args.args
    assert "table_name IN" in str(statement)
    assert parameters == {"table_names": ["missions"]}


@pytest.mark.asyncio
async def test_fence_rejection_counter_uses_the_rejection_seam():
    from omega.application.production_render_lease_service import (
        ProductionRenderLeaseService,
    )

    active_request = MagicMock(status="RUNNING")
    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = active_request
    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[request_result, job_result])

    with patch(
        "omega.application.observability.telemetry.increment_event_counter_async",
        new_callable=AsyncMock,
    ) as event:
        result = await ProductionRenderLeaseService.validate_lease_fenced_async(
            session, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 7
        )

    assert result == (False, "LEASE_FENCE_LOST_OR_EXPIRED")
    event.assert_awaited_once_with("omega_render_fence_rejections_total", 1)


@pytest.mark.asyncio
async def test_lease_expiry_counter_runs_after_committed_authority():
    from omega.application.production_lifecycle_service import ProductionLifecycleService

    candidate_result = MagicMock()
    candidate_result.all.return_value = [(uuid.uuid4(), uuid.uuid4())]
    session = MagicMock()
    session.execute = AsyncMock(return_value=candidate_result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    async def assert_committed(name: str, amount: int) -> None:
        assert session.commit.await_count == 1
        assert (name, amount) == ("omega_render_leases_expired_total", 1)

    with (
        patch(
            "omega.application.production_render_lease_service.ProductionRenderLeaseService.expire_lease",
            AsyncMock(return_value=(True, "EXPIRED")),
        ),
        patch(
            "omega.application.observability.telemetry.increment_event_counter_async",
            AsyncMock(side_effect=assert_committed),
        ) as event,
    ):
        result = await ProductionLifecycleService.reconcile_expired_leases(session)

    assert result == {"scanned": 1, "expired": 1}
    event.assert_awaited_once()


def test_every_declared_operational_event_has_one_instrumentation_owner():
    import omega
    from omega.application.observability.telemetry import ALLOWED_EVENT_METRIC_NAMES

    source_root = Path(omega.__file__).parent
    telemetry_path = source_root / "application" / "observability" / "telemetry.py"
    expected_owners = {
        "omega_dispatch_outbox_retries_total": "application/durable_dispatch.py",
        "omega_dispatch_outbox_dead_letters_total": "application/durable_dispatch.py",
        "omega_render_redispatches_total": "application/production_dispatch_service.py",
        "omega_render_dispatch_exhaustions_total": "application/production_dispatch_service.py",
        "omega_render_leases_expired_total": "application/production_lifecycle_service.py",
        "omega_render_fence_rejections_total": "application/production_render_lease_service.py",
        "omega_campaign_admissions_total": "application/campaign_admission_service.py",
        "omega_scheduler_sweeps_total": "worker/tasks.py",
        "omega_analytics_rollups_total": "worker/tasks.py",
    }
    owners = {name: set() for name in ALLOWED_EVENT_METRIC_NAMES}
    for path in source_root.rglob("*.py"):
        if path == telemetry_path:
            continue
        source = path.read_text(encoding="utf-8")
        for name in owners:
            if f'"{name}"' in source or f"'{name}'" in source:
                owners[name].add(path.relative_to(source_root).as_posix())

    assert owners == {name: {owner} for name, owner in expected_owners.items()}


def test_c3_compose_and_instructions_are_approved_base_runtime_compatible():
    repository = Path(__file__).resolve().parents[2]
    compose = (repository / "docker-compose.prod.yml").read_text(encoding="utf-8")
    guide = (repository / "docs" / "deployment.md").read_text(encoding="utf-8")
    base = "1ec61e14c01e84becd303119ef7546d0af15e5ed"

    assert "http://localhost:8000/health'" in compose
    assert "/health/live" not in compose
    assert "omega.application.observability.process_health" not in compose
    assert compose.count('test: ["CMD-SHELL", "kill -0 1"]') == 2
    assert guide.count(base) >= 3
    assert "must be the verified base image" in guide
    assert "must not be run as incident recovery" in (
        repository / "docs" / "runbooks" / "RB-10-analytics-rollup-failure.md"
    ).read_text(encoding="utf-8")
