"""Read-only physical schema capability checks, independent of Alembic head equality."""

from __future__ import annotations

from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

CORE_COLUMNS = {
    "missions": {"id", "state"},
    "tasks": {"id", "state"},
    "production_requests": {"id", "status"},
    "production_render_jobs": {
        "id",
        "state",
        "lease_token",
        "lease_expires_at",
        "heartbeat_at",
        "dispatch_generation",
        "dispatch_started_at",
    },
    "durable_dispatch_intents": {"id", "state", "claimed_at", "attempt", "max_attempts"},
    "content_campaigns": {"id", "status"},
}
SCHEDULER_COLUMNS = {
    "recurring_schedules": {"id", "status", "next_run_at", "current_version_id"},
    "recurring_schedule_occurrences": {"id", "status", "updated_at", "wait_deadline_at"},
    "recurring_schedule_versions": {"id", "schedule_id"},
    "recurring_schedule_mission_bindings": {"id", "occurrence_id", "mission_id"},
    "recurring_schedule_campaign_bindings": {"id", "occurrence_id", "campaign_id"},
}
ANALYTICS_COLUMNS = {
    "pipeline_analytics_rollups": {
        "id",
        "metric_family",
        "dimension_type",
        "dimension_value",
        "bucket_start",
        "bucket_end",
        "metrics",
        "sample_count",
        "schema_version",
    }
}


async def get_deployed_alembic_version(session: AsyncSession) -> str | None:
    try:
        async with session.begin_nested():
            row = (
                await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            ).first()
        return str(row[0]) if row else None
    except Exception:
        return None


async def _check(session: AsyncSession, required: dict[str, set[str]]) -> tuple[bool, str]:
    try:
        found: dict[str, set[str]] = {}
        if session.get_bind().dialect.name == "sqlite":
            for table in required:
                rows = (await session.execute(text(f"PRAGMA table_info({table})"))).all()
                found[table] = {row[1] for row in rows}
        else:
            statement = text(
                "SELECT table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name IN :table_names"
            ).bindparams(bindparam("table_names", expanding=True))
            rows = (
                await session.execute(statement, {"table_names": sorted(required)})
            ).all()
            for table, column in rows:
                if table in required:
                    found.setdefault(table, set()).add(column)
        missing = [
            table for table, columns in required.items() if not columns <= found.get(table, set())
        ]
        return (
            (False, "missing_capability: " + ", ".join(sorted(missing)))
            if missing
            else (True, "ready")
        )
    except Exception:
        return False, "schema_probe_failed"


async def check_core_schema_capability(session: AsyncSession) -> tuple[bool, str]:
    return await _check(session, CORE_COLUMNS)


async def check_recurring_scheduler_schema_capability(session: AsyncSession) -> tuple[bool, str]:
    return await _check(session, SCHEDULER_COLUMNS)


async def check_pipeline_analytics_schema_capability(session: AsyncSession) -> tuple[bool, str]:
    return await _check(session, ANALYTICS_COLUMNS)


async def evaluate_all_schema_capabilities(session: AsyncSession) -> dict[str, Any]:
    version = await get_deployed_alembic_version(session)
    capabilities = {}
    for name, check in [
        ("core", check_core_schema_capability),
        ("recurring_scheduler", check_recurring_scheduler_schema_capability),
        ("pipeline_analytics", check_pipeline_analytics_schema_capability),
    ]:
        ok, reason = await check(session)
        capabilities[name] = {
            "compatible": ok,
            "status": "READY" if ok else ("INCOMPATIBLE" if name == "core" else "NOT_DEPLOYED"),
            "reason": reason,
        }
    return {"deployed_revision": version, "capabilities": capabilities}
