# P20-C1 recovery and validation

The interrupted implementation was resumed in the existing `p20-c-observability-ops` worktree at base `1ec61e14c01e84becd303119ef7546d0af15e5ed`. No worktree, branch, shared container, or base image was recreated. The root/main/origin refs matched the authoritative base and root tracked files were clean.

## Recovery truth

The prior base build completed (BuildKit `n11rii7z8osikw5lpp2um6i2e`). Its image digest is `sha256:b940c7e02ad9052b737dec24072e93f94655890b4224c3a8748858372d1f3517`. No duplicate build was active. All 549 tracked backend files matched the clean base checkout. The recovered feature image also existed. No P20-C containers/networks/volumes existed at recovery; scratch compose and canary scripts remained.

Git contained 35 changed files: 11 tracked edits and 24 untracked files, with no staged changes. The UI's approximate 51 was not the authoritative filesystem count. All existing edits were reconciled; no unrelated historical or scratch artifact was removed.

## Completed corrections

Outbox retry/dead-letter counters now cover the ordinary transition and stale-claim recovery, independently of RenderJob generation/exhaustion. Render counters follow committed domain changes. CAPACITY_FULL uses exactly one seam; scheduler outcome classification adds no second count. Counter transport is atomic, bounded, time-limited and fail-open, including malformed labels and failure to record/log telemetry.

Readiness checks physical columns, tolerates DB025 with disabled analytics, returns bounded diagnostic reasons, and requires a responsive general worker consuming `celery`. Publisher-only workers cannot satisfy that requirement. Worker/Beat liveness probes inspect local processes without DB or Redis; Beat tick freshness is separately observable. Operational observation uses `clock_timestamp()`, `heartbeat_at`, configured scheduler dispatch timeout, and canonical states. Cache TTL is CONFIGURABLE_UNTUNED.

Production access fails closed without configured tokens. Secret redaction covers structured/standard logs and nested sequences. HTTP/Redis dimensions reject UUIDs, arbitrary keys and free-text label growth. Production context excludes test fixtures/scripts, secrets, dumps, media and scratch. Twelve runbooks and transition/deployment guidance are present; production operations remain separately authorized C3/C4 work.

## Validation

- New P20-C tests: **47 passed**; source lint and git diff checks pass. Physical PostgreSQL authority tests include unmanaged RUNNING exclusion, heartbeat age, stale CLAIMED and missing analytics columns.
- Exact historical regression scope: **tests/unit, 1,964 cases**. BASE and FINAL both return **1,952 passed, 10 failed, 2 errors**. Exact failure/error nodeid sets match; **0 new failures, 0 new errors, 0 unexplained errors**. Both used the same offline Linux image, mounted source/tests and invocation. P20-C tests are separate.
- All nine mandatory isolated runtime canaries pass: healthy stack, PostgreSQL outage, Redis outage, worker loss, Beat loss, analytics schema mismatch, domain telemetry semantics, secret/access, and cardinality. Isolated gate mismatches were restored OFF.
- Semantic canary observed outbox retry +1/render redispatch +0; successful generation advance +1; outbox DEAD_LETTER +1 without render exhaustion; canonical DISPATCH_DELIVERY_EXHAUSTED +1; CAPACITY_FULL +1 after scheduler classification. The strengthened capacity proof uses a valid persisted RUNNING campaign with max_concurrent_missions=1 and one actually admitted reservation; the counter delta is exactly one. No provider or publisher worker ran. Redis outage preserved a committed generation advance.
- Configured secret sentinels, production missing-auth denial, and 100 rejected RenderJob UUID Redis dimensions pass. Existing system/analytics/network endpoints function.
- Base-commit API/worker/Beat start against isolated DB025, all gates OFF, AUTO_MIGRATE=false; endpoints work, DB remains025 and analytics table remains absent. This is a compatibility canary, not a production deployment image approval.
- Effective production Compose uses an immutable application digest, 0 source binds, no reload, one API worker, AUTO_MIGRATE=false and gates OFF. Publisher is absent by default and explicitly profile-gated. This config was **not applied** to shared production.
- EXPLAIN ANALYZE inspected nine actual metrics SELECTs on isolated PostgreSQL. Maximum execution time on the small canary population was **0.075 ms**. This is not load tuning; no index or migration was added.

## Evidence location and validation corrections

Detailed evidence remains in `C:\Users\User\AppData\Local\Temp\p20c-isolated-runtime`: `recovered-base-canary-evidence.json`, `recovered-runtime-evidence.json`, `regression-comparison.json`, BASE/FINAL JUnit/logs, `p20c-tests-final.xml`, `metrics-query-plans.json`, effective Compose proof, secret/cardinality proofs, shared snapshots, build logs and scratch harnesses. Scratch is not committed.

A scheduling error initially allowed an outage canary to interrupt tests sharing its physical PostgreSQL container. Those attempts are invalid evidence and were rerun on a physically separate regression PostgreSQL/Redis stack. Only disposable regression PostgreSQL had fsync disabled to reduce fixture I/O; no disk-crash durability claim is made. The saved harness also had response/key/import/cache-call errors, corrected before accepting its gates.

Supplemental all-repository runs were not the supplied 1,964-case regression scope. They exposed identical pre-existing collection errors importing missing `tests.conftest.is_docker` and additional old fixture failures in BASE/FINAL. They were stopped after matching initial coverage and are **not reported PASS**. The exact required regression comparison completed independently.

## Shared production safety

Read-only checks confirm DB025, analytics rollups absent, five recurring tables present, all feature gates OFF and publisher stopped. The old stopped publisher container remains; it is not a consumer. Shared API AUTO_MIGRATE=true and root source binds remain unchanged. Container IDs, start times, restart counts, sorted mount mappings and hashed environments match recovery snapshots. Root/main/origin remain on the authoritative base. No shared DB data/schema/topology was changed; no shared container restarted during recovery. Pre-recovery start timestamps were recorded rather than asserting unverifiable historical absence of restarts.

No migration027, production migration026, feature activation, P20-C3 decoupling, publishing, Retry-7, main merge or main push occurred. The canary runtime remains isolated with all gates OFF and literal PublishIntent/PublishAttempt counts zero.

## Recovered-file reconciliation

| File | Recovered classification | Final classification |
| --- | --- | --- |
| backend/.dockerignore | COMPLETE | COMPLETE |
| backend/pyproject.toml | COMPLETE | COMPLETE |
| backend/src/omega/config.py | COMPLETE | COMPLETE |
| backend/src/omega/main.py | COMPLETE | COMPLETE |
| backend/src/omega/application/observability/logging_context.py | COMPLETE | COMPLETE |
| backend/src/omega/api/health.py | PARTIAL | COMPLETE |
| backend/src/omega/api/middleware.py | PARTIAL | COMPLETE |
| backend/src/omega/application/campaign_admission_service.py | PARTIAL | COMPLETE |
| backend/src/omega/application/durable_dispatch.py | PARTIAL | COMPLETE |
| backend/src/omega/application/production_dispatch_service.py | PARTIAL | COMPLETE |
| backend/src/omega/infrastructure/celery_app.py | PARTIAL | COMPLETE |
| backend/src/omega/logging.py | PARTIAL | COMPLETE |
| backend/src/omega/application/observability/metrics.py | PARTIAL | COMPLETE |
| backend/src/omega/application/observability/operator_status_service.py | PARTIAL | COMPLETE |
| backend/src/omega/application/observability/readiness_service.py | PARTIAL | COMPLETE |
| backend/src/omega/application/observability/schema_capability.py | PARTIAL | COMPLETE |
| backend/src/omega/application/observability/secret_sanitizer.py | PARTIAL | COMPLETE |
| backend/src/omega/application/observability/telemetry.py | PARTIAL | COMPLETE |
| docker-compose.prod.yml | PARTIAL | COMPLETE |
| backend/src/omega/application/observability/__init__.py | INCORRECT | COMPLETE |
| backend/tests/test_p20c_observability.py | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/deployment.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-01-api-unhealthy.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-02-worker-offline.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-03-beat-stalled.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-04-postgres-outage.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-05-redis-outage.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-06-lease-expiry.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-07-dispatch-stall.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-08-campaign-admission-stalled.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-09-scheduler-backlog.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-10-analytics-rollup-failure.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-11-schema-mismatch.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/RB-12-deployment-rollback.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |
| docs/runbooks/README.md | GENERATED_TEST/RUNBOOK/DOC | COMPLETE |

New recovery implementation file: `backend/src/omega/application/observability/process_health.py`. This report is an additional documentation artifact. No unrelated file was deleted.
