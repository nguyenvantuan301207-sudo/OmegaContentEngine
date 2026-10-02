# RB-02: Celery Worker Offline / Pool Starvation

---

## 1. Symptoms
- `/health/ready` returns HTTP 503 with `"worker": {"status": "UNAVAILABLE"}`.
- Async render tasks or sweep tasks do not execute.
- Queue backlog increases in Redis.

## 2. Signals
- Celery inspect ping fails: `celery_app.control.inspect().ping()` returns None.
- Container-local worker process probe fails (`python -m omega.application.observability.process_health worker`).
- Redis key queue depth `LLEN celery` grows continuously.

## 3. Read-Only Diagnosis
1. Inspect worker logs:
   ```bash
   docker logs --tail 200 omega-worker
   ```
2. Check worker concurrency and active processes:
   ```bash
   docker top omega-worker
   ```
3. Run inspect ping from inside API container:
   ```bash
   docker exec omega-api python -c "from omega.infrastructure.celery_app import celery_app; print(celery_app.control.inspect(timeout=3.0).ping())"
   ```

## 4. Safe Recovery
1. If worker process crashed or deadlocked, restart container:
   ```bash
   docker restart omega-worker
   ```
2. Verify worker registers queues `['celery']` in startup logs.
3. Check `/health/ready` returns HTTP 200.

## 5. Escalation
If worker crashes immediately on task import, check database credentials or Redis connection timeout.

## 6. What NOT to Mutate Manually
- DO NOT flush Redis or delete task messages directly from the `celery` list.
- DO NOT manually update `production_render_jobs` state in SQL.
