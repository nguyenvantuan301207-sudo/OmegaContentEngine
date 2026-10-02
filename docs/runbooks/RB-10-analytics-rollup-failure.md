# RB-10: Pipeline Analytics Rollup Failure / Missing Buckets

---

## 1. Symptoms
- Analytics dashboard shows missing daily summary buckets.
- `GET /api/v1/analytics/historical` queries fall back to raw observations.
- Worker logs report error in `pipeline-analytics-rollup-sweep`.

## 2. Signals
- Telemetry counter `omega_analytics_rollups_total{status="error"}` > 0.
- Gap in daily intervals in `pipeline_analytics_rollups`.
- `check_analytics_schema_capability` returns `table_missing` or `columns_missing`.

## 3. Read-Only Diagnosis
1. Check schema capability for pipeline analytics:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT to_regclass('public.pipeline_analytics_rollups');"
   ```
2. Inspect latest rollup entries:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT metric_family, bucket_start, bucket_end, sample_count FROM pipeline_analytics_rollups ORDER BY bucket_start DESC LIMIT 10;"
   ```
3. Check worker logs for rollup exceptions:
   ```bash
   docker logs --tail 200 omega-worker | grep -i "rollup"
   ```

## 4. Safe Recovery
1. If migration 026 is missing, execute migration before enabling rollup gate:
   ```bash
   docker compose -p omegacontentengine -f docker-compose.prod.yml run --rm --no-deps omega-api alembic upgrade 026
   ```
2. Trigger manual lookback rollup sweep safely:
   ```bash
   docker exec omega-api python -c "from omega.application.analytics.rollup_service import RollupService; from omega.infrastructure.database import async_engine, AsyncSession; import asyncio; asyncio.run(RollupService.run_lookback_rollups(AsyncSession(async_engine), lookback_days=3))"
   ```
3. Verify rollups populate deterministically with sample counts.

## 5. Escalation
If rollups fail due to unique constraint violations, inspect UTC bucket normalization and timezone fold handling.

## 6. What NOT to Mutate Manually
- DO NOT manually insert mock or estimated rollup numbers into `pipeline_analytics_rollups`.
- DO NOT edit `schema_version` column directly.
