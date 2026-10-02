# RB-07: Durable Dispatch Stall / Dead-Letter Triage

---

## 1. Symptoms
- Render jobs remain in `QUEUED` state without transitioning to `RUNNING`.
- Durable dispatch intents accumulate in `CLAIMED` or `DEAD_LETTER`.
- Requests fail with `DISPATCH_DELIVERY_EXHAUSTED`.

## 2. Signals
- Stale CLAIMED intent count > 0 (`claimed_at < clock_timestamp() - interval '300 seconds'`).
- Gauge `omega_dispatch_intents_state{state="DEAD_LETTER"}` > 0.
- Telemetry counter `omega_render_redispatches_total` or `omega_render_dispatch_exhaustions_total` increments.

## 3. Read-Only Diagnosis
1. Query durable dispatch intents by state:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT state, count(*) FROM durable_dispatch_intents GROUP BY state;"
   ```
2. Inspect stale claimed intents:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT id, task_name, state, attempt, max_attempts, claimed_at, last_error FROM durable_dispatch_intents WHERE state = 'CLAIMED' AND claimed_at < clock_timestamp() - interval '300 seconds';"
   ```
3. Inspect dead-lettered intents:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT id, task_name, purpose, attempt, last_error FROM durable_dispatch_intents WHERE state = 'DEAD_LETTER' LIMIT 10;"
   ```

## 4. Safe Recovery
1. The periodic sweep `production-dispatch-stall-sweep` automatically executes `ProductionDispatchService.reconcile_all_dispatch_stalls`.
2. Stale claims are automatically recovered by `DurableDispatchService.recover_stale_claims` during relay runs.
3. If an intent is in `DEAD_LETTER`, diagnose the `last_error` field (e.g. Celery broker connection error, invalid payload).
4. Outbox DEAD_LETTER ends delivery for that intent only. Render reconciliation separately advances dispatch_generation or terminalizes with DISPATCH_DELIVERY_EXHAUSTED; dead-letter alone is not parent failure.

## 5. Escalation
If dead-letter count continues to grow, verify Celery worker subscription to `celery` queue and check Redis broker availability.

## 6. What NOT to Mutate Manually
- DO NOT delete rows from `durable_dispatch_intents`.
- DO NOT reset `attempt` counter directly in SQL.
