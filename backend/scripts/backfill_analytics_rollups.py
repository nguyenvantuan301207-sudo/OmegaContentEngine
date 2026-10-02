"""Bounded Idempotent Backfill CLI for P20-B Pipeline Analytics Rollups.

Usage:
    python backend/scripts/backfill_analytics_rollups.py --from 2026-09-01 --to 2026-09-10
    python backend/scripts/backfill_analytics_rollups.py --from 2026-09-01 --to 2026-09-02 --family render_reliability

Guarantees atomic full-bucket replacement per day/family unit.
Safe to restart or rerun anytime without producing duplicate rows.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, time, timedelta

from omega.application.analytics.rollup_service import RollupService
from omega.application.analytics.schema_capability import check_analytics_schema_capability
from omega.domain.pipeline_analytics import (
    MetricFamily,
    get_daily_utc_buckets_for_range,
    to_utc,
)
from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.database import AsyncWorkerSessionLocal
from omega.logging import get_logger

logger = get_logger(service="omega-analytics-backfill")


def parse_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD or ISO timestamp into timezone-aware UTC datetime."""
    try:
        d = date.fromisoformat(date_str)
        return datetime.combine(d, time.min, tzinfo=UTC)
    except ValueError:
        return to_utc(datetime.fromisoformat(date_str))


async def run_backfill(
    from_dt: datetime,
    to_dt: datetime,
    family: str | None = None,
    session: AsyncSession | None = None,
) -> int:
    """Execute bounded backfill across specified daily buckets."""
    buckets = get_daily_utc_buckets_for_range(from_dt, to_dt)
    if not buckets:
        print(f"No complete daily UTC buckets found between {from_dt} and {to_dt}.")
        return 0

    families = [family] if family else [
        MetricFamily.RENDER_RELIABILITY.value,
        MetricFamily.RENDER_PERFORMANCE.value,
        MetricFamily.SCHEDULER_RELIABILITY.value,
        MetricFamily.QA_QUALITY.value,
    ]

    async def _execute_with_session(sess: AsyncSession) -> int:
        # 1. Verify schema capability
        capable, reason = await check_analytics_schema_capability(sess)
        if not capable:
            print(f"ERROR: Cannot run backfill: {reason}")
            sys.exit(1)

        print(f"Starting analytics backfill: {len(buckets)} days x {len(families)} families...")
        rows_total = 0
        for b_start, b_end in buckets:
            day_str = b_start.strftime("%Y-%m-%d")
            for f in families:
                rows_written = await RollupService.recompute_and_replace_bucket(
                    sess, f, b_start, b_end
                )
                rows_total += rows_written
                print(f"  [{day_str}] {f:25} -> {rows_written} dimension rows written")

        print(f"Backfill complete: {rows_total} total rows replaced across {len(buckets)} days.")
        return rows_total

    if session is not None:
        return await _execute_with_session(session)

    async with AsyncWorkerSessionLocal() as sess:
        return await _execute_with_session(sess)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill daily pipeline analytics rollups.")
    parser.add_argument("--from", dest="from_date", required=True, help="Start date (YYYY-MM-DD, inclusive)")
    parser.add_argument("--to", dest="to_date", required=True, help="End date (YYYY-MM-DD, exclusive)")
    parser.add_argument("--family", dest="family", required=False, default=None, help="Optional metric family")

    args = parser.parse_args()

    from_dt = parse_date(args.from_date)
    to_dt = parse_date(args.to_date)

    if to_dt <= from_dt:
        print(f"ERROR: --to ({to_dt}) must be strictly after --from ({from_dt})")
        sys.exit(1)

    asyncio.run(run_backfill(from_dt, to_dt, args.family))


if __name__ == "__main__":
    main()
