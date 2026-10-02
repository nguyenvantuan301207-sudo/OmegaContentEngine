"""Prometheus metrics registration, custom collectors, and route-template tracking.

Provides bounded-cardinality Prometheus metrics using prometheus_client.
Includes cached database state gauges, Redis telemetry counter export,
and route-template-only HTTP request latency/count tracking.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

from fastapi import Request
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import REGISTRY
from sqlalchemy import text

from omega.application.observability.telemetry import read_telemetry_counters
from omega.config import get_settings

# ── Static Standard Metrics ──────────────────────────────────────────────────

HTTP_REQUESTS_TOTAL = Counter(
    "omega_http_requests_total",
    "Total HTTP requests handled by OMEGA API.",
    ["method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "omega_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def resolve_route_template(request: Request) -> str:
    """Extract bounded route template from request scope.

    Never returns raw URLs or paths containing UUIDs.
    Returns 'UNMATCHED' if path did not match a declared route.
    """
    route = request.scope.get("route")
    if route and hasattr(route, "path"):
        return str(route.path)
    return "UNMATCHED"


# ── Cached Database Metrics Collector ─────────────────────────────────────────


class OmegaDatabaseMetricsCollector:
    """Prometheus custom collector for database-backed operational gauges.

    Uses a bounded TTL cache to prevent database query saturation on frequent scrapes.
    Gracefully handles database outages by emitting source_up=0 without crashing the scrape.
    """

    def __init__(self) -> None:
        self._last_scrape_time: float = 0.0
        self._cached_metrics: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def _fetch_db_state(self) -> dict[str, Any]:
        from omega.application.observability.schema_capability import (
            evaluate_all_schema_capabilities,
        )
        from omega.infrastructure.database import AsyncSessionLocal

        settings = get_settings()
        state: dict[str, Any] = {"source_up": 1}
        async with AsyncSessionLocal() as session:
            schema = await evaluate_all_schema_capabilities(session)
            state["schema"] = schema
            if not schema["capabilities"]["core"]["compatible"]:
                raise RuntimeError("core_schema_unavailable")
            # One bounded aggregate query per domain; no identifiers are returned.
            leases = (
                (
                    await session.execute(
                        text("""
                SELECT count(*) AS managed_running_leases,
                  count(*) FILTER (WHERE lease_expires_at > clock_timestamp()) AS active_leases,
                  count(*) FILTER (WHERE lease_expires_at <= clock_timestamp()) AS expired_leases,
                  max(EXTRACT(EPOCH FROM clock_timestamp() - heartbeat_at)) AS heartbeat_age_seconds
                FROM production_render_jobs WHERE state='RUNNING'
                  AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL
            """)
                    )
                )
                .mappings()
                .one()
            )
            state.update(dict(leases))
            rows = (
                await session.execute(
                    text("SELECT state, count(*) FROM durable_dispatch_intents GROUP BY state")
                )
            ).all()
            state["intents_by_state"] = {
                r[0]: r[1]
                for r in rows
                if r[0] in {"PENDING", "CLAIMED", "RETRY", "SENT", "DEAD_LETTER"}
            }
            state["stale_claimed_count"] = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM durable_dispatch_intents WHERE state='CLAIMED' AND claimed_at < clock_timestamp() - interval '300 seconds'"
                    )
                )
            ).scalar_one()
            state["campaigns_running"] = (
                await session.execute(
                    text("SELECT count(*) FROM content_campaigns WHERE status='RUNNING'")
                )
            ).scalar_one()
            if schema["capabilities"]["recurring_scheduler"]["compatible"]:
                state["scheduler_backlog"] = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM recurring_schedules WHERE status='ACTIVE' AND next_run_at <= clock_timestamp()"
                        )
                    )
                ).scalar_one()
                rows = (
                    await session.execute(
                        text(
                            "SELECT status, count(*) FROM recurring_schedule_occurrences GROUP BY status"
                        )
                    )
                ).all()
                state["occurrences_by_state"] = {
                    r[0]: r[1]
                    for r in rows
                    if r[0]
                    in {
                        "PENDING",
                        "WAITING",
                        "DISPATCHING",
                        "DISPATCHED",
                        "SKIPPED",
                        "FAILED",
                        "CANCELLED",
                    }
                }
                state["stale_dispatching_count"] = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM recurring_schedule_occurrences WHERE status='DISPATCHING' AND updated_at <= clock_timestamp() - make_interval(secs => :timeout)"
                        ),
                        {"timeout": settings.scheduler_dispatch_timeout_seconds},
                    )
                ).scalar_one()
                state["waiting_expired_count"] = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM recurring_schedule_occurrences WHERE status='WAITING' AND wait_deadline_at <= clock_timestamp()"
                        )
                    )
                ).scalar_one()
            if schema["capabilities"]["pipeline_analytics"]["compatible"]:
                state["analytics_rollup_count"] = (
                    await session.execute(text("SELECT count(*) FROM pipeline_analytics_rollups"))
                ).scalar_one()
        return state

    async def get_db_metrics(self) -> dict[str, Any]:
        ttl = get_settings().observability_db_collector_cache_ttl_seconds
        async with self._lock:
            now = time.monotonic()
            if self._cached_metrics and now - self._last_scrape_time < ttl:
                return dict(self._cached_metrics)
            try:
                async with asyncio.timeout(2.0):
                    data = await self._fetch_db_state()
            except Exception:
                data = dict(self._cached_metrics)
                data["source_up"] = 0
            self._cached_metrics = data
            self._last_scrape_time = time.monotonic()
            return dict(data)


db_metrics_collector = OmegaDatabaseMetricsCollector()


async def generate_prometheus_metrics() -> bytes:
    """Generate complete Prometheus text representation combining standard metrics, DB gauges, and Redis event counters."""
    # 1. Fetch DB state gauges
    db_state = await db_metrics_collector.get_db_metrics()

    # 2. Fetch Redis telemetry counters
    redis_counters, redis_up = await read_telemetry_counters()

    # 3. Build dynamic metrics registry for collection
    custom_registry = CollectorRegistry()

    # Telemetry source status
    source_metric = GaugeMetricFamily(
        "omega_observability_source_up",
        "Operational telemetry source availability (1=up, 0=down)",
        labels=["source"],
    )
    source_metric.add_metric(["database"], db_state.get("source_up", 0))
    source_metric.add_metric(["redis"], int(redis_up))
    custom_registry.register(type("DynColl", (), {"collect": lambda s: [source_metric]})())

    # LR2 Gauges
    lr2_metric = GaugeMetricFamily(
        "omega_render_leases_active",
        "Active managed worker leases on RUNNING render jobs",
    )
    lr2_metric.add_metric([], db_state.get("active_leases", 0))

    lr2_expired_metric = GaugeMetricFamily(
        "omega_render_leases_expired",
        "Expired worker leases on RUNNING render jobs awaiting sweep",
    )
    lr2_expired_metric.add_metric([], db_state.get("expired_leases", 0))

    custom_registry.register(
        type("DynColl", (), {"collect": lambda s: [lr2_metric, lr2_expired_metric]})()
    )

    # LR3 Intents by State
    lr3_metric = GaugeMetricFamily(
        "omega_dispatch_intents_state",
        "Durable dispatch intents count by state",
        labels=["state"],
    )
    for state_name, count in db_state.get("intents_by_state", {}).items():
        lr3_metric.add_metric([str(state_name)], count)
    custom_registry.register(type("DynColl", (), {"collect": lambda s: [lr3_metric]})())

    # Scheduler Backlog & Occurrences
    sched_backlog_metric = GaugeMetricFamily(
        "omega_scheduler_backlog_count",
        "Active recurring schedules due for execution",
    )
    sched_backlog_metric.add_metric([], db_state.get("scheduler_backlog", 0))

    occ_metric = GaugeMetricFamily(
        "omega_scheduler_occurrences_state",
        "Recurring schedule occurrences count by state",
        labels=["state"],
    )
    for occ_state, count in db_state.get("occurrences_by_state", {}).items():
        occ_metric.add_metric([str(occ_state)], count)
    custom_registry.register(
        type("DynColl", (), {"collect": lambda s: [sched_backlog_metric, occ_metric]})()
    )

    # Campaigns Running
    camp_metric = GaugeMetricFamily(
        "omega_campaigns_running_count",
        "Content campaigns currently in RUNNING state",
    )
    camp_metric.add_metric([], db_state.get("campaigns_running", 0))
    custom_registry.register(type("DynColl", (), {"collect": lambda s: [camp_metric]})())

    # Group all label combinations into one family to avoid duplicate registration.
    families = {}
    for field, count in redis_counters.items():
        name = field.split("{", 1)[0]
        labels = (
            dict(pair.split("=", 1) for pair in field.split("{", 1)[1][:-1].split(","))
            if "{" in field
            else {}
        )
        names = sorted(labels)
        if name not in families:
            families[name] = CounterMetricFamily(
                name, "Best-effort operational event count", labels=names
            )
        families[name].add_metric([labels[k] for k in names], count)
    for family in families.values():
        custom_registry.register(type("DynColl", (), {"collect": lambda s, m=family: [m]})())

    # Combine default registry output and custom registry output
    default_output = generate_latest(REGISTRY)
    custom_output = generate_latest(custom_registry)
    return default_output + custom_output


# ── Production Access Policy Helpers ──────────────────────────────────────────


def verify_observability_access(request: Request, required_token: str | None) -> bool:
    """Verify bearer token for metrics/ops access in production.

    In production environment:
    - If required_token is not configured: FAILS CLOSED (returns False).
    - If Authorization header is missing or does not match: returns False.
    In development/test environment:
    - If required_token is None: permits access (returns True).
    - If required_token is set: validates token.
    Uses constant-time comparison to prevent timing attacks.
    """
    settings = get_settings()
    is_production = settings.environment.lower() in ("production", "prod")

    if is_production and not required_token:
        return False

    if not required_token:
        # Permitted in dev/test when no token is configured
        return True

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False

    token = auth_header[7:].strip()
    return secrets.compare_digest(token, required_token)
