# RB-09: Recurring Scheduler Backlog / Overdue Occurrences

---

## 1. Symptoms
- Scheduled occurrences fail to fire on time.
- Gauge `omega_scheduler_backlog_count` > 0 and increasing.
- Occurrences accumulate in `DISPATCHING` or `WAITING` status.

## 2. Signals
- Active schedules with `next_run_at <= clock_timestamp()`.
- Occurrences with `status = 'DISPATCHING'` and `updated_at <= clock_timestamp() - make_interval(secs => <SCHEDULER_DISPATCH_TIMEOUT_SECONDS>)`.
- Occurrences with `status = 'WAITING'` and `wait_deadline_at <= clock_timestamp()`.

## 3. Read-Only Diagnosis
1. Query active schedules that are overdue:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT id, status, next_run_at, clock_timestamp() - next_run_at AS overdue_age FROM recurring_schedules WHERE status = 'ACTIVE' AND next_run_at <= clock_timestamp();"
   ```
2. Query occurrence status breakdown:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT status, count(*) FROM recurring_schedule_occurrences GROUP BY status;"
   ```
3. Query stale dispatching occurrences:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT id, schedule_id, status, attempt_count, updated_at FROM recurring_schedule_occurrences WHERE status = 'DISPATCHING' AND updated_at <= clock_timestamp() - make_interval(secs => <SCHEDULER_DISPATCH_TIMEOUT_SECONDS>);"
   ```

## 4. Safe Recovery
1. Verify Celery Beat is healthy and advancing (`RB-03`).
2. Verify `RECURRING_SCHEDULER_ENABLED=true` in environment.
3. The periodic sweep `recurring-stale-reconciliation-sweep` automatically executes `RecurringSweepService.reconcile_stale_occurrences`.
4. Stale `DISPATCHING` occurrences check downstream bindings and converge safely to `DISPATCHED` (if binding was committed) or reset to `PENDING` (if attempts remain).
5. Expired `WAITING` occurrences are reconciled through scheduler policy; inspect the resulting canonical state.

## 5. Escalation
If recurring scheduler sweep fails repeatedly with database deadlocks, inspect lock contention on `recurring_schedules`.

## 6. What NOT to Mutate Manually
- DO NOT manually update occurrence `status` to `DISPATCHED` without downstream bindings.
- DO NOT delete rows from `recurring_schedule_occurrences`.
