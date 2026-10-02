# RB-04: PostgreSQL Outage / Pool Saturation

---

## 1. Symptoms
- `/health/ready` returns HTTP 503 with `"database": {"status": "UNAVAILABLE"}`.
- API endpoints return 500 Internal Server Error.
- Database connection pool exhaustion errors appear in logs.

## 2. Signals
- `SELECT 1` ping fails or exceeds 1.0s timeout.
- Prometheus metric `omega_observability_source_up{source="database"} == 0`.
- Pool stats in `/ops/status` report `checked_out == size` and `overflow == max_overflow`.

## 3. Read-Only Diagnosis
1. Check PostgreSQL container health:
   ```bash
   docker ps -f name=omega-postgres
   docker exec omega-postgres pg_isready -U omega
   ```
2. Inspect active queries and locks:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT pid, state, now() - query_start AS duration, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY duration DESC LIMIT 10;"
   ```
3. Check deadlocks count:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT datname, deadlocks FROM pg_stat_database WHERE datname='omega';"
   ```

## 4. Safe Recovery
1. If PostgreSQL container stopped, start it:
   ```bash
   docker start omega-postgres
   ```
2. If hung on connection saturation, restart API container to drain leaked connections:
   ```bash
   docker restart omega-api
   ```
3. Verify database recovers: `/health/ready` returns HTTP 200.

## 5. Escalation
If PostgreSQL reports corruption or failed recovery, prepare to restore from pre-deployment snapshot.

## 6. What NOT to Mutate Manually
- DO NOT terminate backend connections blindly without checking process IDs.
- DO NOT manually drop or truncate tables.
