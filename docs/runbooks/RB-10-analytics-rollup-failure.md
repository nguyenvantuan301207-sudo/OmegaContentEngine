# RB-10: Pipeline Analytics Rollup Failure / Missing Buckets

---

## 1. Symptoms
- Analytics dashboard shows missing daily summary buckets.
- `GET /api/v1/analytics/historical` queries fall back to raw observations.
- Worker logs report error in `pipeline-analytics-rollup-sweep`.

## 2. Signals
- Telemetry counter `omega_analytics_rollups_total{status="ERROR"}` > 0.
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
1. If migration 026 is missing, keep analytics gates off and stop. Migration 026 requires a
   separately reviewed deployment authorization and must not be run as incident recovery.
2. If schema 026 and the rollup gate were already deployed under an approved rollout, trigger
   the documented bounded lookback sweep through the normal task interface under incident
   commander authorization. Do not call the service directly from an API shell.
3. Verify rollups populate deterministically with sample counts.

## 5. Escalation
If rollups fail due to unique constraint violations, inspect UTC bucket normalization and timezone fold handling.

## 6. What NOT to Mutate Manually
- DO NOT manually insert mock or estimated rollup numbers into `pipeline_analytics_rollups`.
- DO NOT edit `schema_version` column directly.
