"""Bounded, atomic, non-authoritative Redis counters with bounded I/O."""

from __future__ import annotations

import asyncio

from omega.infrastructure.redis import redis_client

TELEMETRY_HASH_KEY = "omega:telemetry:counters"
ALLOWED_EVENT_METRIC_NAMES = frozenset(
    {
        "omega_dispatch_outbox_retries_total",
        "omega_dispatch_outbox_dead_letters_total",
        "omega_render_redispatches_total",
        "omega_render_dispatch_exhaustions_total",
        "omega_render_leases_expired_total",
        "omega_render_fence_rejections_total",
        "omega_campaign_admissions_total",
        "omega_scheduler_sweeps_total",
        "omega_analytics_rollups_total",
    }
)
LABEL_VALUES = {
    "omega_campaign_admissions_total": {
        "status": frozenset({"CAPACITY_FULL", "ADMITTED", "UNKNOWN"})
    },
    "omega_scheduler_sweeps_total": {"status": frozenset({"OK", "DISABLED", "ERROR", "UNKNOWN"})},
    "omega_analytics_rollups_total": {"status": frozenset({"OK", "DISABLED", "ERROR", "UNKNOWN"})},
}


def _format_field_key(metric_name: str, labels: dict[str, str] | None = None) -> str:
    allowed = LABEL_VALUES.get(metric_name, {})
    if labels and set(labels) - set(allowed):
        raise ValueError("unrecognized telemetry dimension")
    values = {key: (labels or {}).get(key, "UNKNOWN") for key in allowed}
    values = {k: v if v in allowed[k] else "UNKNOWN" for k, v in values.items()}
    return metric_name + (
        "{" + ",".join(f"{k}={v}" for k, v in sorted(values.items())) + "}" if values else ""
    )


async def increment_event_counter_async(
    metric_name: str, amount: int = 1, labels: dict[str, str] | None = None
) -> None:
    try:
        if (
            metric_name not in ALLOWED_EVENT_METRIC_NAMES
            or not isinstance(amount, int)
            or amount <= 0
        ):
            return
        field = _format_field_key(metric_name, labels)
        async with asyncio.timeout(0.2):
            await redis_client.hincrby(TELEMETRY_HASH_KEY, field, amount)
    except Exception:
        pass


def increment_event_counter_sync(
    metric_name: str, amount: int = 1, labels: dict[str, str] | None = None
) -> None:
    try:
        if (
            metric_name not in ALLOWED_EVENT_METRIC_NAMES
            or not isinstance(amount, int)
            or amount <= 0
        ):
            return
        field = _format_field_key(metric_name, labels)
        import redis

        from omega.config import get_settings

        with redis.Redis.from_url(
            get_settings().redis_url, socket_timeout=0.2, socket_connect_timeout=0.2
        ) as client:
            client.hincrby(TELEMETRY_HASH_KEY, field, amount)
    except Exception:
        pass


async def read_telemetry_counters() -> tuple[dict[str, int], bool]:
    try:
        async with asyncio.timeout(0.2):
            raw = await redis_client.hgetall(TELEMETRY_HASH_KEY)
        counters = {}
        for k, v in raw.items():
            field = k.decode() if isinstance(k, bytes) else str(k)
            name = field.split("{", 1)[0]
            if name not in ALLOWED_EVENT_METRIC_NAMES:
                continue
            labels = {}
            if "{" in field:
                if not field.endswith("}"):
                    continue
                labels = dict(pair.split("=", 1) for pair in field.split("{", 1)[1][:-1].split(","))
            if _format_field_key(name, labels) != field:
                continue
            count = int(v)
            if count >= 0:
                counters[field] = count
        return counters, True
    except Exception:
        return {}, False
