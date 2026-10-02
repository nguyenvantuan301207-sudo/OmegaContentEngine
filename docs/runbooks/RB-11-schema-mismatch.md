# RB-11: Database Schema Mismatch / Capability Incompatibility

---

## 1. Symptoms
- `/health/ready` returns HTTP 503 with `"capabilities": {...}` showing `INCOMPATIBLE` or `NOT_DEPLOYED`.
- Database error `relation does not exist` or `column does not exist` in application logs.
- Feature cannot be activated safely.

## 2. Signals
- `evaluate_all_schema_capabilities` reports missing tables or columns.
- Database revision is lower than required by an enabled feature gate (e.g. `ANALYTICS_API_ENABLED=true` while revision is `025`).

## 3. Read-Only Diagnosis
1. Query current deployed Alembic revision:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT version_num FROM alembic_version;"
   ```
2. Inspect schema capabilities via API:
   ```bash
   docker exec omega-api python -c "from omega.application.observability.schema_capability import evaluate_all_schema_capabilities; from omega.infrastructure.database import async_engine, AsyncSession; import asyncio; print(asyncio.run(evaluate_all_schema_capabilities(AsyncSession(async_engine))))"
   ```

## 4. Safe Recovery
1. If an enabled feature requires a migration (e.g. Analytics requires 026):
   - Either: execute the approved migration as an explicit step:
     ```bash
     docker compose -p omegacontentengine -f docker-compose.prod.yml run --rm --no-deps omega-api alembic upgrade <approved_target_rev>
     ```
   - Or: turn OFF the feature gate in configuration until migration is approved:
     ```bash
     ANALYTICS_API_ENABLED=false
     ANALYTICS_ROLLUP_ENABLED=false
     ```
2. Verify `/health/ready` recovers to HTTP 200.

## 5. Escalation
If Alembic reports divergent migration branches or missing revision hashes, escalate to Lead Database Engineer.

## 6. What NOT to Mutate Manually
- DO NOT manually update `version_num` in `alembic_version` table.
- DO NOT manually run DDL statements without an Alembic migration script.
