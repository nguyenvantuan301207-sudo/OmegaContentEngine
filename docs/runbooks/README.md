# OMEGA Production Runbooks (P20-C)

---

## Runbook Inventory

| ID | Title | Scope | Manual Mutation Prohibited |
| :--- | :--- | :--- | :--- |
| **[RB-01](RB-01-api-unhealthy.md)** | API Unhealthy / Liveness Failure | Fast-fail process loop recovery | DO NOT bypass health checks or disable liveness probe |
| **[RB-02](RB-02-worker-offline.md)** | Celery Worker Offline / Pool Starvation | Worker queue & IPC recovery | DO NOT delete in-flight jobs directly in Redis |
| **[RB-03](RB-03-beat-stalled.md)** | Celery Beat Offline / Stalled Tick Loop | Scheduler heartbeat recovery | DO NOT manually dispatch recurring schedules |
| **[RB-04](RB-04-postgres-outage.md)** | PostgreSQL Outage / Pool Saturation | Database connectivity recovery | DO NOT restart PG without graceful connection draining |
| **[RB-05](RB-05-redis-outage.md)** | Redis Outage / Broker Reconnect Loop | Broker & state cache recovery | DO NOT flush Redis database 0 blindly |
| **[RB-06](RB-06-lease-expiry.md)** | Worker Lease Expiry / Stale Fencing | LR2 render lease reconciliation | DO NOT manually update lease_token or state in SQL |
| **[RB-07](RB-07-dispatch-stall.md)** | Durable Dispatch Stall / Dead-Letter Triage| LR3 durable outbox recovery | DO NOT delete rows from durable_dispatch_intents |
| **[RB-08](RB-08-campaign-admission-stalled.md)** | Campaign Admission Stalled | Concurrency & capacity recovery | DO NOT force campaign status to SUCCEEDED manually |
| **[RB-09](RB-09-scheduler-backlog.md)** | Recurring Scheduler Backlog / Overdue | Occurrence sweep backlog recovery | DO NOT manually insert or update occurrences in DB |
| **[RB-10](RB-10-analytics-rollup-failure.md)** | Pipeline Analytics Rollup Failure | Rollup aggregation recovery | DO NOT insert mock rows into pipeline_analytics_rollups |
| **[RB-11](RB-11-schema-mismatch.md)** | Database Schema Mismatch | Migration & capability recovery | DO NOT manually edit alembic_version table |
| **[RB-12](RB-12-deployment-rollback.md)** | Emergency Deployment Rollback | Rollback to previous immutable tag | DO NOT downgrade database without restoring snapshot |

---

## Universal Operational Safety Rule: What NOT to Mutate Manually
Under NO circumstances should an on-call operator execute direct manual SQL mutations against:
1. `alembic_version`: Corrupts migration state tracking.
2. `production_render_jobs.state` / `lease_token`: Violates LR2 fencing authority and creates split-brain execution.
3. `durable_dispatch_intents`: Bypasses LR3 retry and delivery guarantees.
4. `recurring_schedules` / `recurring_schedule_occurrences`: Bypasses P20-A catch-up and deduplication constraints.
5. `content_campaigns` / `content_campaign_items`: Causes desynchronization with downstream missions.
All reconciliations must occur through authoritative services (`ProductionLifecycleService`, `RecurringSweepService`, `ProductionDispatchService`).
