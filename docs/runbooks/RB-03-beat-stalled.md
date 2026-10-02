# RB-03: Celery Beat Offline / Stalled Tick Loop

---

## 1. Symptoms
- Periodic background tasks (sweeps, reconciliation, handoffs) cease executing.
- `/ops/status` reports `"beat": {"status": "STALE"}` or tick age > 30s.
- If recurring scheduler is enabled, `/health/ready` returns HTTP 503.

## 2. Signals
- Redis key `omega:beat:last_tick` is missing or older than 30 seconds.
- Process-local file `/tmp/beat_heartbeat` is not updating.
- No new task dispatches logged by Beat.

## 3. Read-Only Diagnosis
1. Inspect Beat container logs:
   ```bash
   docker logs --tail 200 omega-beat
   ```
2. Verify Redis `omega:beat:last_tick` value:
   ```bash
   docker exec omega-redis redis-cli get omega:beat:last_tick
   ```
3. Check if Beat process is alive:
   ```bash
   docker top omega-beat
   ```

## 4. Safe Recovery
1. Restart the Beat container:
   ```bash
   docker restart omega-beat
   ```
2. Verify Beat logs show schedule loaded:
   `beat: Starting...` and periodic task registrations.
3. Check `/ops/status` to verify `beat.status == "READY"` and tick age < 10s.

## 5. Escalation
If Beat fails to acquire schedule or crashes with syntax error, inspect recent commits on `celery_app.py:beat_schedule`.

## 6. What NOT to Mutate Manually
- DO NOT manually run background sweeps via ad-hoc CLI commands in production.
- DO NOT manually advance recurring schedules in PostgreSQL.
