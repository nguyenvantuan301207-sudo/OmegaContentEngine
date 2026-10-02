# RB-06: Worker Lease Expiry / Stale Fencing Remediation

---

## 1. Symptoms
- Render jobs remain stuck in `RUNNING` past execution duration.
- Prometheus metric `omega_render_leases_expired` > 0.
- Worker logs report `render_lease_lost` or `stale_worker_fence_rejected`.

## 2. Signals
- Managed RUNNING render jobs with `lease_expires_at <= clock_timestamp()`.
- Log event `stale_worker_fence_rejected` observed when a slow worker attempts completion.
- Telemetry counter `omega_render_leases_expired_total` increments.

## 3. Read-Only Diagnosis
1. Query expired managed render jobs in PostgreSQL:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT id, production_request_id, fencing_token, heartbeat_at, lease_expires_at FROM production_render_jobs WHERE state = 'RUNNING' AND lease_token IS NOT NULL AND lease_expires_at <= clock_timestamp();"
   ```
2. Verify heartbeat age:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT id, clock_timestamp() - heartbeat_at AS heartbeat_age FROM production_render_jobs WHERE state = 'RUNNING' AND lease_token IS NOT NULL;"
   ```

## 4. Safe Recovery
1. The authoritative reconciliation is automatically handled by `ProductionLifecycleService.reconcile_expired_leases` running in periodic sweep `production-orphan-reconciliation-sweep`.
2. If the production lease sweep gate is already enabled under an approved rollout, confirm
   the scheduled reconciliation is progressing. If it is disabled, stop and request a separate
   gate-activation authorization; do not enable it or invoke the task ad hoc from this runbook.
3. Verify expired jobs transition monotonically to FAILED with `RenderErrorCode.WORKER_LEASE_EXPIRED`.

## 5. Escalation
If worker continuously loses leases, check worker host CPU/network contention causing heartbeat renewal delays.

## 6. What NOT to Mutate Manually
- DO NOT manually update `state = 'FAILED'` or reset `lease_token` in raw SQL.
- DO NOT delete rows from `production_render_jobs`.
