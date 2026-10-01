"""Forward-compatible database schema capability detection for P20-B Pipeline Analytics.

Does NOT hard-code alembic_version == 026.
Safely inspects the public schema to verify required tables and columns exist.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_analytics_schema_capability(session: AsyncSession) -> tuple[bool, str]:
    """Verify that pipeline_analytics_rollups exists and has the required schema columns.

    Returns:
        (True, "ready") if fully capable.
        (False, reason_str) if capability is incomplete.
    """
    try:
        # Check column inventory for public.pipeline_analytics_rollups
        stmt = text(
            "SELECT column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "  AND table_name = 'pipeline_analytics_rollups';"
        )
        res = await session.execute(stmt)
        columns = {row[0] for row in res.fetchall()}

        if not columns:
            # Fallback check for SQLite (in-memory unit tests)
            sqlite_stmt = text("PRAGMA table_info(pipeline_analytics_rollups);")
            try:
                sqlite_res = await session.execute(sqlite_stmt)
                columns = {row[1] for row in sqlite_res.fetchall()}
            except Exception:
                pass

        if not columns:
            return False, "table_missing: pipeline_analytics_rollups"

        required_columns = {
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
        missing = required_columns - columns
        if missing:
            return False, f"columns_missing: {sorted(missing)}"

        return True, "ready"
    except Exception as exc:
        return False, f"inspection_error: {str(exc)}"
