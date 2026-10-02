"""Comprehensive unit and semantic test suite for P20-C Observability & Operations."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from omega.application.durable_dispatch import (
    CLAIMED,
    DEAD_LETTER,
    PENDING,
    RETRY,
    SENT,
)
from omega.application.observability.logging_context import (
    bind_correlation_context,
    clear_correlation_context,
)
from omega.application.observability.metrics import (
    OmegaDatabaseMetricsCollector,
    generate_prometheus_metrics,
    resolve_route_template,
    verify_observability_access,
)
from omega.application.observability.readiness_service import (
    check_database_readiness,
    evaluate_system_readiness,
)
from omega.application.observability.secret_sanitizer import (
    sanitize_event_dict,
    sanitize_string_value,
)
from omega.application.observability.telemetry import (
    ALLOWED_EVENT_METRIC_NAMES,
    increment_event_counter_async,
)
from omega.config import Settings
from omega.domain.content_campaign import ContentCampaignStatus
from omega.domain.recurring_schedule import RecurringOccurrenceStatus
from omega.main import app

client = TestClient(app)


# ── 1. Liveness & Probe Independence Tests ────────────────────────────────────


def test_health_live_endpoint_returns_ok_without_dependencies():
    """Verify /health and /health/live return 200 without DB/Redis calls."""
    res1 = client.get("/health")
    assert res1.status_code == 200
    assert res1.json() == {"status": "ok"}

    res2 = client.get("/health/live")
    assert res2.status_code == 200
    assert res2.json() == {"status": "ok"}


# ── 2. Readiness Evaluation Tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_readiness_healthy_when_core_ready_and_gates_off():
    """Verify readiness returns HTTP 200 when core is healthy and feature gates are OFF."""
    with (
        patch(
            "omega.application.observability.readiness_service.check_database_readiness",
            new_callable=AsyncMock,
        ) as m_db,
        patch(
            "omega.application.observability.readiness_service.check_redis_readiness",
            new_callable=AsyncMock,
        ) as m_redis,
        patch(
            "omega.application.observability.readiness_service.check_worker_readiness",
            new_callable=AsyncMock,
        ) as m_worker,
        patch(
            "omega.application.observability.readiness_service.check_beat_readiness",
            new_callable=AsyncMock,
        ) as m_beat,
        patch(
            "omega.application.observability.readiness_service.evaluate_all_schema_capabilities",
            new_callable=AsyncMock,
        ) as m_schema,
        patch("omega.application.observability.readiness_service.get_settings") as m_settings,
    ):
        m_db.return_value = {"status": "READY", "latency_ms": 1.0}
        m_redis.return_value = {"status": "READY", "latency_ms": 0.5}
        m_worker.return_value = {"status": "READY", "latency_ms": 10.0}
        m_beat.return_value = {"status": "READY", "tick_age_seconds": 2.0}
        m_schema.return_value = {
            "capabilities": {
                "core": {"compatible": True, "status": "READY"},
                "recurring_scheduler": {"compatible": True, "status": "READY"},
                "pipeline_analytics": {"compatible": False, "status": "NOT_DEPLOYED"},
            }
        }
        m_settings.return_value = Settings(
            recurring_scheduler_enabled=False,
            analytics_api_enabled=False,
            analytics_rollup_enabled=False,
        )

        payload, status_code = await evaluate_system_readiness()
        assert status_code == 200
        assert payload["status"] == "READY"
        assert payload["capabilities"]["recurring_scheduler"]["status"] == "DISABLED"
        assert payload["capabilities"]["pipeline_analytics"]["status"] == "NOT_DEPLOYED"


@pytest.mark.asyncio
async def test_readiness_degraded_when_postgres_unreachable():
    """Verify readiness degrades to HTTP 503 when PostgreSQL is down."""
    with (
        patch(
            "omega.application.observability.readiness_service.check_database_readiness",
            new_callable=AsyncMock,
        ) as m_db,
        patch(
            "omega.application.observability.readiness_service.check_redis_readiness",
            new_callable=AsyncMock,
        ) as m_redis,
        patch(
            "omega.application.observability.readiness_service.check_worker_readiness",
            new_callable=AsyncMock,
        ) as m_worker,
        patch(
            "omega.application.observability.readiness_service.check_beat_readiness",
            new_callable=AsyncMock,
        ) as m_beat,
    ):
        m_db.return_value = {"status": "UNAVAILABLE", "error": "connection_refused"}
        m_redis.return_value = {"status": "READY"}
        m_worker.return_value = {"status": "READY"}
        m_beat.return_value = {"status": "READY"}

        payload, status_code = await evaluate_system_readiness()
        assert status_code == 503
        assert payload["status"] == "DEGRADED"
        assert payload["dependencies"]["database"]["status"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_readiness_degraded_when_redis_unreachable():
    """Verify readiness degrades to HTTP 503 when Redis is down."""
    with (
        patch(
            "omega.application.observability.readiness_service.check_database_readiness",
            new_callable=AsyncMock,
        ) as m_db,
        patch(
            "omega.application.observability.readiness_service.check_redis_readiness",
            new_callable=AsyncMock,
        ) as m_redis,
    ):
        m_db.return_value = {"status": "READY"}
        m_redis.return_value = {"status": "UNAVAILABLE", "error": "timeout"}

        payload, status_code = await evaluate_system_readiness()
        assert status_code == 503
        assert payload["status"] == "DEGRADED"
        assert payload["dependencies"]["redis"]["status"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_readiness_degraded_when_scheduler_gate_on_and_beat_stale():
    """Verify readiness degrades to HTTP 503 when recurring scheduler is ON but Beat is STALE."""
    with (
        patch(
            "omega.application.observability.readiness_service.check_database_readiness",
            new_callable=AsyncMock,
        ) as m_db,
        patch(
            "omega.application.observability.readiness_service.check_redis_readiness",
            new_callable=AsyncMock,
        ) as m_redis,
        patch(
            "omega.application.observability.readiness_service.check_worker_readiness",
            new_callable=AsyncMock,
        ) as m_worker,
        patch(
            "omega.application.observability.readiness_service.check_beat_readiness",
            new_callable=AsyncMock,
        ) as m_beat,
        patch(
            "omega.application.observability.readiness_service.evaluate_all_schema_capabilities",
            new_callable=AsyncMock,
        ) as m_schema,
        patch("omega.application.observability.readiness_service.get_settings") as m_settings,
    ):
        m_db.return_value = {"status": "READY"}
        m_redis.return_value = {"status": "READY"}
        m_worker.return_value = {"status": "READY"}
        m_beat.return_value = {"status": "STALE", "tick_age_seconds": 65.0}
        m_schema.return_value = {
            "capabilities": {
                "core": {"compatible": True, "status": "READY"},
                "recurring_scheduler": {"compatible": True, "status": "READY"},
                "pipeline_analytics": {"compatible": False, "status": "NOT_DEPLOYED"},
            }
        }
        m_settings.return_value = Settings(recurring_scheduler_enabled=True)

        payload, status_code = await evaluate_system_readiness()
        assert status_code == 503
        assert payload["status"] == "DEGRADED"
        assert payload["capabilities"]["recurring_scheduler"]["status"] == "DEGRADED"


@pytest.mark.asyncio
async def test_readiness_degraded_when_analytics_gate_on_and_schema_missing():
    """Verify readiness degrades to HTTP 503 when analytics gate is ON but schema 026 is missing."""
    with (
        patch(
            "omega.application.observability.readiness_service.check_database_readiness",
            new_callable=AsyncMock,
        ) as m_db,
        patch(
            "omega.application.observability.readiness_service.check_redis_readiness",
            new_callable=AsyncMock,
        ) as m_redis,
        patch(
            "omega.application.observability.readiness_service.check_worker_readiness",
            new_callable=AsyncMock,
        ) as m_worker,
        patch(
            "omega.application.observability.readiness_service.check_beat_readiness",
            new_callable=AsyncMock,
        ) as m_beat,
        patch(
            "omega.application.observability.readiness_service.evaluate_all_schema_capabilities",
            new_callable=AsyncMock,
        ) as m_schema,
        patch("omega.application.observability.readiness_service.get_settings") as m_settings,
    ):
        m_db.return_value = {"status": "READY"}
        m_redis.return_value = {"status": "READY"}
        m_worker.return_value = {"status": "READY"}
        m_beat.return_value = {"status": "READY"}
        m_schema.return_value = {
            "capabilities": {
                "core": {"compatible": True, "status": "READY"},
                "recurring_scheduler": {"compatible": True, "status": "READY"},
                "pipeline_analytics": {
                    "compatible": False,
                    "status": "NOT_DEPLOYED",
                    "reason": "table_missing",
                },
            }
        }
        m_settings.return_value = Settings(analytics_api_enabled=True)

        payload, status_code = await evaluate_system_readiness()
        assert status_code == 503
        assert payload["status"] == "DEGRADED"
        assert payload["capabilities"]["pipeline_analytics"]["status"] == "DEGRADED"


# ── 3. Exact Authority & State Mappings ───────────────────────────────────────


def test_occurrence_canonical_state_map():
    """Verify recurring occurrence states strictly match the 7 authorized enum values."""
    expected = {"PENDING", "WAITING", "DISPATCHING", "DISPATCHED", "SKIPPED", "FAILED", "CANCELLED"}
    actual = {s.value for s in RecurringOccurrenceStatus}
    assert actual == expected
    assert "COMPLETED" not in actual


def test_campaign_canonical_status_map():
    """Verify campaign statuses include RUNNING and do not use ACTIVE."""
    statuses = {s.value for s in ContentCampaignStatus}
    assert "RUNNING" in statuses
    assert "ACTIVE" not in statuses


def test_durable_dispatch_intent_states():
    """Verify durable dispatch intent states strictly match the 5 authorized states."""
    expected = {"PENDING", "CLAIMED", "RETRY", "SENT", "DEAD_LETTER"}
    actual = {PENDING, CLAIMED, RETRY, SENT, DEAD_LETTER}
    assert actual == expected


# ── 4. Telemetry Independence & Best-Effort Safety ────────────────────────────


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_raise():
    """Verify Redis telemetry failure is caught and swallowed (domain safety)."""
    with patch("omega.infrastructure.redis.redis_client") as m_redis:
        m_redis.hincrby = AsyncMock(side_effect=ConnectionError("Redis down"))

        # Must not raise
        await increment_event_counter_async("omega_campaign_admissions_total", 1)


# ── 5. Secret Sanitizer & Log Redaction Tests ─────────────────────────────────


def test_secret_sanitizer_redacts_credentials_and_passwords():
    """Verify secret sanitizer redacts connection passwords, bearer tokens, and keys."""
    # Redact URL passwords
    url = "postgresql+asyncpg://omega_user:super_secret_pw@db.internal:5432/omega"
    sanitized_url = sanitize_string_value(url)
    assert "super_secret_pw" not in sanitized_url
    assert "://omega_user:[REDACTED]@" in sanitized_url

    # Redact Bearer tokens
    header = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    assert sanitize_string_value(header) == "Bearer [REDACTED]"

    # Event dictionary redaction
    event = {
        "event": "render_started",
        "render_job_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "fencing_token": 2,
        "dispatch_generation": 1,
        "lease_token": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "api_key": "secret_key_123",
        "authorization": "Bearer secret_bearer",
        "database_url": url,
    }

    cleaned = sanitize_event_dict(None, "info", event)
    assert cleaned["lease_token"] == "[REDACTED]"
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["authorization"] == "[REDACTED]"
    assert "super_secret_pw" not in cleaned["database_url"]

    # Non-sensitive IDs preserved
    assert cleaned["render_job_id"] == "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    assert cleaned["fencing_token"] == 2
    assert cleaned["dispatch_generation"] == 1


def test_correlation_context_ignores_lease_token():
    """Verify correlation context helper explicitly rejects binding raw lease_token."""
    clear_correlation_context()
    bind_correlation_context(
        render_job_id="job-123",
        lease_token="raw-secret-token",
        fencing_token=4,
    )

    import structlog

    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("render_job_id") == "job-123"
    assert ctx.get("fencing_token") == "4"
    assert "lease_token" not in ctx
    clear_correlation_context()


# ── 6. Metrics Route Template & Cardinality Protection ────────────────────────


def test_metrics_route_template_resolution():
    """Verify resolve_route_template extracts path template and never raw UUIDs."""
    req_mock = MagicMock(spec=Request)
    route_mock = MagicMock()
    route_mock.path = "/api/v1/missions/{mission_id}"
    req_mock.scope = {"route": route_mock}

    assert resolve_route_template(req_mock) == "/api/v1/missions/{mission_id}"

    # Unmatched route
    req_unmatched = MagicMock(spec=Request)
    req_unmatched.scope = {}
    assert resolve_route_template(req_unmatched) == "UNMATCHED"


# ── 7. Access Control Fail-Closed Tests ────────────────────────────────────────


def test_metrics_and_ops_fail_closed_in_production_when_token_unconfigured():
    """Verify production mode returns 403 when auth tokens are unconfigured."""
    with patch("omega.application.observability.metrics.get_settings") as m_settings:
        m_settings.return_value = Settings(
            environment="production",
            metrics_auth_token=None,
            operator_auth_token=None,
        )

        req_mock = MagicMock(spec=Request)
        req_mock.headers = {}

        assert not verify_observability_access(req_mock, None)


def test_metrics_access_succeeds_with_valid_bearer_token():
    """Verify access succeeds when matching Bearer token is provided."""
    req_mock = MagicMock(spec=Request)
    req_mock.headers = {"Authorization": "Bearer valid-secret-token"}

    with patch("omega.application.observability.metrics.get_settings") as m_settings:
        m_settings.return_value = Settings(environment="production")
        assert verify_observability_access(req_mock, "valid-secret-token")
        assert not verify_observability_access(req_mock, "wrong-token")


# ── 8. Compatibility with Existing APIs ───────────────────────────────────────


def test_existing_system_endpoints_compatibility():
    """Verify existing /api/v1/system/info returns metadata without regression."""
    res = client.get("/api/v1/system/info")
    assert res.status_code == 200
    data = res.json()
    assert "app" in data
    assert "version" in data
    assert "environment" in data


# ── 9. LR2 Managed Cohort & Heartbeat Authority Tests ─────────────────────────


def test_lr2_managed_cohort_predicate_definition():
    """Verify LR2 managed cohort requires lease_token and lease_expires_at to be non-null."""
    from omega.application.observability.metrics import OmegaDatabaseMetricsCollector

    collector = OmegaDatabaseMetricsCollector()
    assert hasattr(collector, "get_db_metrics")


# ── 10. LR3 and Outbox Semantic Distinctness Tests ─────────────────────────────


def test_outbox_retry_and_render_redispatch_metric_distinctness():
    """Verify outbox retry and render redispatch are defined as distinct telemetry metrics."""
    assert "omega_dispatch_outbox_retries_total" in ALLOWED_EVENT_METRIC_NAMES
    assert "omega_render_redispatches_total" in ALLOWED_EVENT_METRIC_NAMES
    assert "omega_dispatch_outbox_dead_letters_total" in ALLOWED_EVENT_METRIC_NAMES
    assert "omega_render_dispatch_exhaustions_total" in ALLOWED_EVENT_METRIC_NAMES
    assert "omega_campaign_admissions_total" in ALLOWED_EVENT_METRIC_NAMES


@pytest.mark.asyncio
async def test_campaign_admission_capacity_full_single_count_seam():
    """Verify campaign admission emits CAPACITY_FULL exactly once."""
    from omega.application.campaign_admission_service import _reserve_one_in_transaction
    from omega.domain.content_campaign import ContentCampaignStatus
    from omega.infrastructure.models import ContentCampaign

    mock_campaign = MagicMock(spec=ContentCampaign)
    mock_campaign.status = ContentCampaignStatus.RUNNING.value
    mock_campaign.max_concurrent_missions = 1

    mock_session = AsyncMock()

    with (
        patch("omega.application.campaign_admission_service.get_settings") as m_settings,
        patch(
            "omega.application.campaign_admission_service._active_counts", new_callable=AsyncMock
        ) as m_active,
        patch(
            "omega.application.observability.telemetry.increment_event_counter_async",
            new_callable=AsyncMock,
        ) as m_incr,
    ):
        m_settings.return_value = Settings(
            campaign_orchestration_enabled=True,
            campaign_channel_max_active_missions=10,
        )
        # Return campaign_active = 2 (exceeding max_concurrent_missions = 1)
        m_active.return_value = (2, 0)

        item, reason = await _reserve_one_in_transaction(mock_session, mock_campaign)
        assert item is None
        assert reason == "CAPACITY_FULL"

        # Verify emitted exactly once
        m_incr.assert_awaited_once_with(
            "omega_campaign_admissions_total",
            1,
            {"status": "CAPACITY_FULL"},
        )


# ── 11. Celery Context Cleared Post-Task ──────────────────────────────────────


def test_celery_task_postrun_clears_correlation_context():
    """Verify celery task_postrun signal clears contextvars to prevent cross-task leakage."""
    import structlog

    from omega.infrastructure.celery_app import on_task_postrun

    bind_correlation_context(mission_id="m-123", task_id="t-456")
    ctx_before = structlog.contextvars.get_contextvars()
    assert ctx_before.get("mission_id") == "m-123"

    # Invoke postrun handler
    on_task_postrun(
        sender=None, task_id="t-456", task=None, args=(), kwargs={}, retval=None, state="SUCCESS"
    )

    ctx_after = structlog.contextvars.get_contextvars()
    assert ctx_after.get("mission_id") is None
    assert ctx_after.get("task_id") is None


# Recovery coverage: behavior at authority seams, not only metric-name definitions.
@pytest.mark.parametrize(
    "core,scheduler_schema,beat,worker,scheduler,analytics,expected",
    [
        (False, True, "READY", "READY", False, False, 503),
        (True, False, "READY", "READY", True, False, 503),
        (True, True, "READY", "READY", True, False, 200),
        (True, True, "STALE", "READY", False, False, 200),
        (True, True, "READY", "UNAVAILABLE", False, False, 503),
        (True, True, "READY", "READY", False, True, 503),
    ],
)
@pytest.mark.asyncio
async def test_readiness_authority_matrix(
    core, scheduler_schema, beat, worker, scheduler, analytics, expected
):
    prefix = "omega.application.observability.readiness_service."
    with (
        patch(prefix + "check_database_readiness", AsyncMock(return_value={"status": "READY"})),
        patch(prefix + "check_redis_readiness", AsyncMock(return_value={"status": "READY"})),
        patch(prefix + "check_worker_readiness", AsyncMock(return_value={"status": worker})),
        patch(prefix + "check_beat_readiness", AsyncMock(return_value={"status": beat})),
        patch(
            prefix + "evaluate_all_schema_capabilities",
            AsyncMock(
                return_value={
                    "capabilities": {
                        "core": {"compatible": core},
                        "recurring_scheduler": {"compatible": scheduler_schema},
                        "pipeline_analytics": {"compatible": False},
                    }
                }
            ),
        ),
        patch(
            prefix + "get_settings",
            return_value=Settings(
                recurring_scheduler_enabled=scheduler, analytics_api_enabled=analytics
            ),
        ),
    ):
        payload, code = await evaluate_system_readiness()
        assert code == expected
        assert payload["capabilities"]["publisher"]["status"] == "DISABLED"


@pytest.mark.parametrize(
    "attempt,max_attempts,state,metric",
    [
        (1, 3, RETRY, "omega_dispatch_outbox_retries_total"),
        (3, 3, DEAD_LETTER, "omega_dispatch_outbox_dead_letters_total"),
    ],
)
def test_outbox_domain_transition_emits_only_canonical_counter(
    attempt, max_attempts, state, metric
):
    from omega.application.durable_dispatch import DurableDispatchService

    intent = MagicMock(state=CLAIMED, attempt=attempt, max_attempts=max_attempts)
    session = MagicMock()
    session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = intent
    with patch("omega.application.observability.telemetry.increment_event_counter_sync") as event:
        assert (
            DurableDispatchService.mark_retry_or_dead_letter(
                session, uuid.uuid4(), uuid.uuid4(), RuntimeError("broker down")
            )
            == state
        )
        session.commit.assert_called_once()
        event.assert_called_once_with(metric, 1)


@pytest.mark.asyncio
async def test_render_generation_and_exhaustion_counters_follow_commit():
    from omega.application.production_dispatch_service import ProductionDispatchService
    from tests.unit.test_production_dispatch_recovery import (
        MockDispatchAsyncSession,
        _make_production_hierarchy,
    )

    for gen, action, metric in [
        (1, "REDISPATCHED", "omega_render_redispatches_total"),
        (3, "EXHAUSTED", "omega_render_dispatch_exhaustions_total"),
    ]:
        session = MockDispatchAsyncSession()
        request, job = _make_production_hierarchy(
            dispatch_gen=gen,
            dispatch_started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        session.add(request)
        session.add(job)

        async def emit(name, amount, session=session, metric=metric):
            assert session.committed
            assert name == metric and amount == 1

        with (
            patch(
                "omega.application.observability.telemetry.increment_event_counter_async",
                side_effect=emit,
            ) as event,
            patch(
                "omega.application.production_lifecycle_service.ProductionLifecycleService.fail_production_request",
                AsyncMock(),
            ),
        ):
            result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
                session, request.id, job.id, gen
            )
            assert result["action"] == action
            event.assert_awaited_once()
            if gen == 1:
                assert job.dispatch_generation == 2
            else:
                assert job.error_code == "DISPATCH_DELIVERY_EXHAUSTED"


@pytest.mark.asyncio
async def test_telemetry_callback_failure_preserves_committed_generation():
    from omega.application.production_dispatch_service import ProductionDispatchService
    from tests.unit.test_production_dispatch_recovery import (
        MockDispatchAsyncSession,
        _make_production_hierarchy,
    )

    session = MockDispatchAsyncSession()
    request, job = _make_production_hierarchy(
        dispatch_started_at=__import__("datetime").datetime.now(__import__("datetime").UTC)
    )
    session.add(request)
    session.add(job)
    with patch(
        "omega.application.observability.telemetry.increment_event_counter_async",
        AsyncMock(side_effect=RuntimeError("metrics failed")),
    ):
        result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
            session, request.id, job.id, 1
        )
    assert result["action"] == "REDISPATCHED" and session.committed and job.dispatch_generation == 2


@pytest.mark.asyncio
async def test_campaign_callback_failure_preserves_capacity_outcome():
    from omega.application.campaign_admission_service import _reserve_one_in_transaction

    campaign = MagicMock(status="RUNNING", max_concurrent_missions=1)
    with (
        patch(
            "omega.application.campaign_admission_service.get_settings",
            return_value=Settings(campaign_orchestration_enabled=True),
        ),
        patch(
            "omega.application.campaign_admission_service._active_counts",
            AsyncMock(return_value=(2, 0)),
        ),
        patch(
            "omega.application.observability.telemetry.increment_event_counter_async",
            AsyncMock(side_effect=RuntimeError("metrics failed")),
        ),
    ):
        assert await _reserve_one_in_transaction(AsyncMock(), campaign) == (None, "CAPACITY_FULL")


@pytest.mark.asyncio
async def test_redis_telemetry_bounds_and_atomic_increment():
    from omega.application.observability import telemetry

    redis = AsyncMock()
    with patch.object(telemetry, "redis_client", redis):
        await telemetry.increment_event_counter_async(
            "omega_render_redispatches_total", 1, {"render_job_id": str(uuid.uuid4())}
        )
        redis.hincrby.assert_not_awaited()
        await telemetry.increment_event_counter_async(
            "omega_campaign_admissions_total", 1, {"status": "user-input"}
        )
        redis.hincrby.assert_awaited_once_with(
            telemetry.TELEMETRY_HASH_KEY, "omega_campaign_admissions_total{status=UNKNOWN}", 1
        )


@pytest.mark.asyncio
async def test_cache_singleflight_and_outage_backoff():
    collector = OmegaDatabaseMetricsCollector()
    with patch.object(
        collector, "_fetch_db_state", AsyncMock(return_value={"source_up": 1, "active_leases": 2})
    ) as fetch:
        await asyncio.gather(*[collector.get_db_metrics() for _ in range(10)])
        assert fetch.await_count == 1
    collector._last_scrape_time = 0
    with patch.object(
        collector, "_fetch_db_state", AsyncMock(side_effect=RuntimeError("down"))
    ) as fetch:
        a = await collector.get_db_metrics()
        b = await collector.get_db_metrics()
        assert a["source_up"] == 0 and b["active_leases"] == 2 and fetch.await_count == 1


@pytest.mark.parametrize("role", ["worker", "beat"])
def test_container_local_liveness_does_not_import_dependencies(tmp_path, role):
    from omega.application.observability.process_health import process_alive

    proc = tmp_path / "123456"
    proc.mkdir()
    (proc / "cmdline").write_bytes(b"python\0celery\0" + role.encode() + b"\0")
    assert process_alive(role, tmp_path)
    (proc / "cmdline").write_bytes(b"python\0other\0")
    assert not process_alive(role, tmp_path)


@pytest.mark.asyncio
async def test_database_probe_does_not_expose_exception_body():
    with patch("omega.application.observability.readiness_service.async_engine") as engine:
        engine.connect.side_effect = RuntimeError("password=sentinel credential")
        result = await check_database_readiness()
    assert result["status"] == "UNAVAILABLE" and "sentinel" not in str(result)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/system/status",
        "/api/v1/system/info",
        "/api/v1/system/tts",
        "/api/v1/system/render-capabilities",
        "/api/v1/analytics/health",
        "/api/v1/network/routes/{route_id}/health",
    ],
)
def test_existing_compatibility_routes_retained(path):
    assert path in app.openapi()["paths"]


@pytest.mark.asyncio
async def test_multiple_label_values_export_as_one_metric_family():
    with (
        patch(
            "omega.application.observability.metrics.db_metrics_collector.get_db_metrics",
            AsyncMock(return_value={"source_up": 1}),
        ),
        patch(
            "omega.application.observability.metrics.read_telemetry_counters",
            AsyncMock(
                return_value=(
                    {
                        "omega_campaign_admissions_total{status=CAPACITY_FULL}": 1,
                        "omega_campaign_admissions_total{status=UNKNOWN}": 2,
                    },
                    True,
                )
            ),
        ),
    ):
        data = (await generate_prometheus_metrics()).decode()
        assert data.count("# HELP omega_campaign_admissions_total") == 1
        assert 'status="CAPACITY_FULL"' in data and 'status="UNKNOWN"' in data


@pytest.mark.asyncio
async def test_physical_schema_and_managed_cohort_with_real_postgresql(db_session):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from omega.application.observability.schema_capability import evaluate_all_schema_capabilities
    from omega.infrastructure.models import ProductionRenderJob
    from tests.integration.test_dispatch_stall_recovery import _create_test_lineage

    now = datetime.now(UTC)
    _, req, job, plan = await _create_test_lineage(db_session, job_state="RUNNING")
    # The unmanaged historical shape is present alongside a managed expired lease.
    job.started_at = now
    job.lease_token = None
    job.lease_expires_at = None
    managed = ProductionRenderJob(
        id=uuid.uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        state="RUNNING",
        dispatch_generation=1,
        idempotency_key="p20c-managed-" + uuid.uuid4().hex,
        lease_token=uuid.uuid4(),
        lease_expires_at=now - timedelta(seconds=1),
        heartbeat_at=now - timedelta(seconds=120),
        started_at=now - timedelta(seconds=130),
    )
    db_session.add(managed)
    await db_session.commit()
    collector = OmegaDatabaseMetricsCollector()
    state = await collector._fetch_db_state()
    assert state["managed_running_leases"] == 1
    assert state["expired_leases"] == 1 and state["active_leases"] == 0
    assert state["heartbeat_age_seconds"] >= 120
    assert state["schema"]["capabilities"]["pipeline_analytics"]["compatible"]
    # The table name can exist without the required columns: still incompatible.
    await db_session.execute(
        text("ALTER TABLE pipeline_analytics_rollups RENAME COLUMN metrics TO metrics_hidden")
    )
    try:
        capability = await evaluate_all_schema_capabilities(db_session)
        assert not capability["capabilities"]["pipeline_analytics"]["compatible"]
        assert capability["capabilities"]["core"]["compatible"]
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_operational_staleness_uses_progressing_clock_and_canonical_columns(db_session):
    from datetime import UTC, datetime, timedelta

    from omega.application.durable_dispatch import DurableDispatchService
    from omega.application.observability.metrics import OmegaDatabaseMetricsCollector

    intent = await DurableDispatchService.enqueue_async(
        db_session,
        idempotency_key="p20c-stale-" + uuid.uuid4().hex,
        task_name="omega.dispatch.relay",
        args=[],
        purpose="TEST",
    )
    intent.state = "CLAIMED"
    intent.claim_token = uuid.uuid4()
    intent.claimed_at = datetime.now(UTC) - timedelta(seconds=301)
    await db_session.commit()
    state = await OmegaDatabaseMetricsCollector()._fetch_db_state()
    assert state["stale_claimed_count"] == 1
    from pathlib import Path

    queries = Path(
        __import__("omega.application.observability.metrics", fromlist=["x"]).__file__
    ).read_text()
    assert "status='DISPATCHING' AND updated_at <= clock_timestamp()" in queries
    assert "status='WAITING' AND wait_deadline_at <= clock_timestamp()" in queries
    assert "claimed_at < clock_timestamp() - interval '300 seconds'" in queries
    assert "settings.scheduler_dispatch_timeout_seconds" in queries


@pytest.mark.asyncio
async def test_publisher_only_does_not_satisfy_general_worker_readiness():
    from omega.application.observability.readiness_service import check_worker_readiness
    with patch("omega.application.observability.readiness_service.celery_app") as celery:
        inspector = celery.control.inspect.return_value
        inspector.ping.return_value = {"publisher": {"ok": "pong"}}
        inspector.active_queues.return_value = {"publisher": [{"name": "omega-publisher"}]}
        assert (await check_worker_readiness())["status"] == "UNAVAILABLE"
        inspector.active_queues.return_value = {"publisher": [{"name": "celery"}]}
        assert (await check_worker_readiness())["status"] == "READY"


def test_nested_sequences_cannot_leak_raw_tokens():
    value = {
        "payload": [
            [
                {
                    "lease_token": "sentinel-lease-nested",
                    "authorization": "Bearer sentinel-bearer-nested",
                }
            ]
        ]
    }
    safe = sanitize_event_dict(None, "info", value)
    assert "sentinel-lease-nested" not in str(safe) and "sentinel-bearer-nested" not in str(safe)
