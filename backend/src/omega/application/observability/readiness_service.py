"""Feature-aware readiness evaluation service for OMEGA P20-C.

Computes comprehensive subsystem readiness and overall HTTP status (200 READY vs 503 DEGRADED).
Preserves independence of gated features: disabled features never degrade global readiness.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import text

from omega.application.observability.schema_capability import evaluate_all_schema_capabilities
from omega.config import get_settings
from omega.infrastructure.celery_app import celery_app
from omega.infrastructure.database import async_engine
from omega.infrastructure.redis import redis_client


async def check_database_readiness(timeout_seconds: float = 1.0) -> dict[str, Any]:
    """Check PostgreSQL connectivity and latency within bounded timeout."""
    start = time.monotonic()
    try:
        async with asyncio.timeout(timeout_seconds):
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1;"))
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "READY", "latency_ms": latency_ms}
    except Exception:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status": "UNAVAILABLE",
            "latency_ms": latency_ms,
            "error": "dependency_probe_failed",
        }


async def check_redis_readiness(timeout_seconds: float = 1.0) -> dict[str, Any]:
    """Check Redis connectivity and latency within bounded timeout."""
    start = time.monotonic()
    try:
        if redis_client is None:
            return {"status": "UNAVAILABLE", "error": "redis_client_uninitialized"}
        async with asyncio.timeout(timeout_seconds):
            await redis_client.ping()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "READY", "latency_ms": latency_ms}
    except Exception:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status": "UNAVAILABLE",
            "latency_ms": latency_ms,
            "error": "dependency_probe_failed",
        }


async def check_worker_readiness(timeout_seconds: float = 3.0) -> dict[str, Any]:
    """Check Celery worker responsiveness via inspect ping with bounded timeout."""
    start = time.monotonic()
    try:
        loop = asyncio.get_event_loop()

        def general_workers() -> dict[str, Any]:
            inspector = celery_app.control.inspect(timeout=timeout_seconds)
            responsive = inspector.ping() or {}
            queues = inspector.active_queues() or {}
            return {
                node: pong
                for node, pong in responsive.items()
                if any(queue.get("name") == "celery" for queue in queues.get(node, []))
            }

        res = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                general_workers,
            ),
            timeout=timeout_seconds * 2 + 1.0,
        )
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        if res:
            return {"status": "READY", "latency_ms": latency_ms, "active_workers": list(res.keys())}
        return {"status": "UNAVAILABLE", "latency_ms": latency_ms, "reason": "no_worker_response"}
    except Exception:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "UNAVAILABLE", "latency_ms": latency_ms, "error": "worker_probe_failed"}


async def check_beat_readiness(threshold_seconds: float = 30.0) -> dict[str, Any]:
    """Check Celery Beat progress by reading tick timestamp from Redis."""
    try:
        if redis_client is None:
            return {"status": "UNAVAILABLE", "reason": "redis_client_none"}
        async with asyncio.timeout(1.0):
            raw_tick = await redis_client.get("omega:beat:last_tick")
        if not raw_tick:
            return {"status": "STALE", "reason": "last_tick_missing"}

        tick_float = float(raw_tick.decode("utf-8") if isinstance(raw_tick, bytes) else raw_tick)
        age = time.time() - tick_float
        if 0 <= age <= threshold_seconds:
            return {"status": "READY", "tick_age_seconds": round(age, 1)}
        return {"status": "STALE", "tick_age_seconds": round(age, 1)}
    except Exception:
        return {"status": "UNAVAILABLE", "error": "beat_probe_failed"}


async def evaluate_system_readiness() -> tuple[dict[str, Any], int]:
    """Evaluate overall system readiness according to the frozen P20-C capability model.

    Returns:
        (response_dict, http_status_code) where status code is 200 (READY) or 503 (DEGRADED).
    """
    settings = get_settings()

    # 1. Probe core infrastructure dependencies in parallel
    db_res, redis_res = await asyncio.gather(
        check_database_readiness(settings.db_health_timeout_seconds),
        check_redis_readiness(settings.redis_health_timeout_seconds),
    )

    # 2. Check schema capabilities if database is reachable
    schema_info: dict[str, Any] = {"capabilities": {}}
    if db_res["status"] == "READY":
        from omega.infrastructure.database import AsyncSessionLocal

        try:
            async with asyncio.timeout(settings.db_health_timeout_seconds):
                async with AsyncSessionLocal() as session:
                    schema_info = await evaluate_all_schema_capabilities(session)
        except Exception:
            schema_info = {
                "capabilities": {
                    "core": {
                        "compatible": False,
                        "status": "UNAVAILABLE",
                        "reason": "schema_probe_failed",
                    }
                }
            }

    # 3. Probe worker responsiveness if redis is reachable
    worker_res: dict[str, Any] = {"status": "UNAVAILABLE", "reason": "redis_unavailable"}
    if redis_res["status"] == "READY":
        worker_res = await check_worker_readiness(settings.worker_health_timeout)

    # 4. Probe Beat loop progress
    beat_res: dict[str, Any] = {"status": "UNAVAILABLE", "reason": "redis_unavailable"}
    if redis_res["status"] == "READY":
        beat_res = await check_beat_readiness(settings.beat_health_threshold_seconds)

    # 5. Evaluate feature gates and capability alignment
    core_cap = schema_info.get("capabilities", {}).get("core", {})
    sched_cap = schema_info.get("capabilities", {}).get("recurring_scheduler", {})
    analytics_cap = schema_info.get("capabilities", {}).get("pipeline_analytics", {})

    # Recurring scheduler readiness
    scheduler_ready_status = "DISABLED"
    scheduler_impacts_global = False
    if settings.recurring_scheduler_enabled:
        if not sched_cap.get("compatible", False) or beat_res["status"] != "READY":
            scheduler_ready_status = "DEGRADED"
            scheduler_impacts_global = True
        else:
            scheduler_ready_status = "READY"
    else:
        scheduler_ready_status = "DISABLED"

    # Pipeline analytics readiness
    analytics_ready_status = "DISABLED"
    analytics_impacts_global = False
    analytics_gates_on = settings.analytics_api_enabled or settings.analytics_rollup_enabled
    if analytics_gates_on:
        if not analytics_cap.get("compatible", False):
            analytics_ready_status = "DEGRADED"
            analytics_impacts_global = True
        else:
            analytics_ready_status = "READY"
    else:
        if not analytics_cap.get("compatible", False):
            analytics_ready_status = "NOT_DEPLOYED"
        else:
            analytics_ready_status = "DISABLED"

    # Core failure conditions that cause overall 503
    core_failure = (
        db_res["status"] != "READY"
        or redis_res["status"] != "READY"
        or not core_cap.get("compatible", False)
        or worker_res["status"] != "READY"
    )

    is_overall_degraded = core_failure or scheduler_impacts_global or analytics_impacts_global
    overall_status = "DEGRADED" if is_overall_degraded else "READY"
    status_code = 503 if is_overall_degraded else 200

    readiness_payload = {
        "status": overall_status,
        "environment": settings.environment,
        "version": settings.app_version,
        "dependencies": {
            "database": db_res,
            "redis": redis_res,
            "worker": worker_res,
            "beat": beat_res,
        },
        "capabilities": {
            "core": core_cap,
            "recurring_scheduler": {
                "gate": "ENABLED" if settings.recurring_scheduler_enabled else "DISABLED",
                "status": scheduler_ready_status,
                "schema": sched_cap,
            },
            "pipeline_analytics": {
                "gate": "ENABLED" if analytics_gates_on else "DISABLED",
                "status": analytics_ready_status,
                "schema": analytics_cap,
            },
            "publisher": {
                "status": "DISABLED",
                "gate": "ABSENT",
            },
        },
    }

    return readiness_payload, status_code
