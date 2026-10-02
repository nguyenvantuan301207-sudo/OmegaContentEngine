# P20 Acceptance Manifest & Retry-7 Contract

## 1. Acceptance Scope & Invariants

This document establishes the authoritative acceptance contract for OMEGA P20 production hardening and closure.

### Invariants:
1. **Schema Neutrality in D1**: No new migration 027. Production schema remains revision 025 until explicit authorized migration 026 rollout in D2.
2. **Feature Gates Default Off**: All six production gates (`RECURRING_SCHEDULER_ENABLED`, `ANALYTICS_API_ENABLED`, `ANALYTICS_ROLLUP_ENABLED`, `CAMPAIGN_ORCHESTRATION_ENABLED`, `PRODUCTION_DISPATCH_RECOVERY_ENABLED`, `PRODUCTION_LEASE_SWEEP_ENABLED`) default to `false`.
3. **Publisher Absence**: Publishing is strictly OUTSIDE P20 scope. No publisher container, no worker queue consumer on `omega-publisher`, and queue depth must remain 0.
4. **Historical Lifecycle Integrity**: No historical lifecycle rows (`RenderJob`, `PublishIntent`, `PublishAttempt`, `MissionExecution`) may be manually rewritten, repaired, or deleted. 293 historical render jobs missing `completed_at` remain legacy-unattributed.

---

## 2. Test Suites & Verification Inventory

| Suite | Category | Invariant Checked | Expected Result |
|---|---|---|---|
| Existing P20-C Suite | Unit / Integration | Observability, health, metrics, readiness, leak prevention | 52 passed |
| P20-D1 Suite | Unit / Contract | Hardening, publisher absence, network boundary, H1-H7 | All passed |
| Full Regression Suite | Unit / Domain / Architecture | Baseline parity vs `ca44a599` | 0 new failures, 0 new errors |

---

## 3. Final-Image Isolated Canaries

Prior to production activation in D3, the immutable production image must execute the following bounded canaries in an isolated container environment:

1. **DB025 Compatibility Canary**: Assert application boots cleanly, `/health` returns 200, and readiness reports degraded gracefully without crashing when analytics schema (026) is absent.
2. **Analytics Missing Canary**: Assert `/api/v1/analytics/*` endpoints return structured feature-disabled or schema-missing errors without unhandled exceptions.
3. **Dependency Outage Canary**: Assert API fails closed gracefully when Redis or PostgreSQL is temporarily unreachable and recovers without restarting.
4. **Worker Loss Canary**: Assert Celery `task_reject_on_worker_lost=True` redelivers unacknowledged tasks upon SIGKILL of worker child process.
5. **Beat Loss Canary**: Assert `omega_beat_last_tick` staleness is detected and reported by readiness probes when Beat process terminates.

---

## 4. Authoritative Retry-7 Contract

### Placement in Rollout Order:
**Retry-7 belongs strictly in P20-D3**, after:
- Immutable application image deployment (D2);
- Explicit database migration 026 deployment (D2);
- Bounded standalone schedule canary (P20-A);
- Bounded analytics API & rollup canary (P20-B);
- **BEFORE** final P20 closure.

### Contract Boundaries:
1. **No Publishing**: Retry-7 must never start `omega-publisher-worker`, never attach consumers to `omega-publisher`, and never issue provider dispatch calls.
2. **Bounded Scope**: Executes one deterministic end-to-end rendering pipeline test using canned, local assets (visual template mode `LOCAL_TEMPLATE_ONLY` or deterministic canned data).
3. **Stop-on-Failure**: Any exception, unexpected queue publication, or invariant violation immediately halts the test and triggers rollback.
4. **Artifacts & Evidence**:
   - Master MP4 video output generated and validated (duration, resolution, audio/video synchronization).
   - Manifest checksums and runtime truth verified.
   - Zero mutation of historical non-canary rows.
5. **Retention**: Retry-7 artifacts are preserved in `artifacts/retry-7/` alongside execution logs.
