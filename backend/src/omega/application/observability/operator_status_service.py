"""Authenticated, read-only operator diagnostics with bounded failure output."""

from __future__ import annotations

import time

from omega.application.observability.metrics import db_metrics_collector
from omega.application.observability.readiness_service import evaluate_system_readiness
from omega.application.observability.telemetry import read_telemetry_counters
from omega.config import get_settings


async def collect_operator_status() -> dict:
    settings = get_settings()
    readiness, _ = await evaluate_system_readiness()
    state = await db_metrics_collector.get_db_metrics()
    counters, redis_up = await read_telemetry_counters()
    return {
        "status": readiness["status"],
        "timestamp_epoch": time.time(),
        "environment": settings.environment,
        "version": settings.app_version,
        "gates": {
            k: getattr(settings, k)
            for k in [
                "recurring_scheduler_enabled",
                "analytics_api_enabled",
                "analytics_rollup_enabled",
                "campaign_orchestration_enabled",
                "production_dispatch_recovery_enabled",
                "production_lease_sweep_enabled",
            ]
        },
        "dependencies": readiness["dependencies"],
        "capabilities": readiness["capabilities"],
        "schema": state.get("schema", {}),
        "domain_state": state,
        "source_health": {"database": state.get("source_up", 0), "redis": int(redis_up)},
        "telemetry_counters": counters,
    }
