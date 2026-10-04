# P25-A Controlled Publishing Rollout & Authority Architecture

## 1. Publishing Authority and Scope
P25-A establishes the canonical, fail-closed publishing rollout boundary for OMEGA Content Engine.
It bridges accepted creative packages from P24 (P24-A ChannelDNA, P24-B CreativeStyle, P24-C Packaging, P24-D Creative QA) and core production artifacts (P20-C, P21 Narrative, P22 Visual, P23 Audio) into immutable publish intents, isolated provider execution, and non-secret durable publish receipts.

### Canonical Orchestration Authority:
**`omega.application.publisher.publishing_service.PublishingService`** is the **sole canonical publishing authority**.
All publishing execution converges on:
- `PublishingService.prepare_publish(...)`
- `PublishingService.execute_publish(...)`
- `PublishingService.reconcile_publish(...)`

---

## 2. Canonical Call Graph & Entry Points

```
             ┌─────────────────────────────────────────────────────────┐
             │       Publishing Entry Points                           │
             │  • API: POST /api/v1/publisher/execute-p25              │
             │  • API: POST /api/v1/publisher/execute (Legacy Seam)    │
             │  • Worker: execute_publish_task(task_id)                │
             │  • Scheduler: PublishCalendarService Dispatch           │
             └────────────────────────────┬────────────────────────────┘
                                          │
                                          ▼
                      ┌───────────────────────────────────────┐
                      │  omega.application.publisher          │
                      │  .publishing_service                  │
                      │  .PublishingService                   │
                      └───────────────────┬───────────────────┘
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
     ┌─────────────────────────┐                     ┌─────────────────────────┐
     │  PublishEligibilityGate │                     │  ReconciliationEngine   │
     │  (Fail-Closed Upstream  │                     │  (Authoritative Query)  │
     │   Validation)           │                     └────────────┬────────────┘
     └────────────┬────────────┘                                  │
                  │                                               │
                  ▼                                               │
     ┌─────────────────────────┐                                  │
     │  PublishIntent          │                                  │
     │  (Canonical Checksum,   │                                  │
     │   Monotonic Revision)   │                                  │
     └────────────┬────────────┘                                  │
                  │                                               │
                  ▼                                               │
     ┌─────────────────────────┐                                  │
     │  PublishingStateMachine │                                  │
     │  (11 Typed States)      │                                  │
     └────────────┬────────────┘                                  │
                  │                                               │
                  ▼                                               │
     ┌─────────────────────────┐                                  │
     │ VideoPublishingProvider │◄─────────────────────────────────┘
     │ (Narrow Abstraction)    │
     └────────────┬────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
┌──────────────┐     ┌────────────────────────────────┐
│ FakeProvider │     │ LegacyAdapterBridgeProvider    │
│ (Sandbox/CI) │     │ (Wraps BasePlatformAdapter/YT) │
└───────┬──────┘     └───────────────┬────────────────┘
        │                            │
        └──────────────┬─────────────┘
                       ▼
         ┌───────────────────────────┐
         │ External State Verified   │
         └─────────────┬─────────────┘
                       ▼
         ┌───────────────────────────┐
         │ PublishReceipt            │
         │ (Non-Secret Durable Truth)│
         └───────────────────────────┘
```

---

## 3. Legacy Seams & Reconciliation

### A. `PublishExecutionService` (`publish_service.py`)
- **Status:** Converted into a thin compatibility delegate into canonical `PublishingService`.
- **Fail-Closed Gate:** Before execution, `PublishExecutionService.execute_publish` enforces the canonical eligibility gate fail-closed:
  - If `MediaArtifact` is missing or non-current (`is_current is False`, `status in ("IS_SUPERSEDED", "PURGED", "FAILED")`) -> `BLOCKED (STALE_ARTIFACT / NO_CURRENT_ARTIFACT)`.
  - If `ProductionRuntimeTruth` is missing -> `BLOCKED (RUNTIME_TRUTH_MISSING)`.
  - If `CreativeQAResult` is not `PASS` or not accepted -> `BLOCKED (CREATIVE_QA_NOT_PASS)`.
- **Delegation:** Upon passing gate validation, it delegates execution exclusively to `PublishingService.execute_publish`. It does **not** independently mutate external providers.

### B. `PublishIntentService` (`intent_service.py`)
- **Status:** Intent persistence and revisioning helpers are unified with canonical intent creation.
- Monotonic revisions (`revision_number = max_rev + 1`, `supersedes_intent_id`) and immutable transitions in `PublishIntentTransition` obey canonical publishing rules.

### C. Provider Abstraction Unification (`provider_abstraction.py`)
- **Single Canonical Abstraction:** `VideoPublishingProvider` is the sole interface consumed by domain orchestration.
- **Legacy Adapter Bridge:** `LegacyAdapterBridgeProvider` adapts legacy `BasePlatformAdapter` instances (including `YouTubeAdapter`) behind `VideoPublishingProvider`.
- Real provider accounts remain unmutated (`P25A_REAL_PROVIDER_MUTATED = NO`).

### D. Multi-Entrypoint Convergence
All entry points (direct API, legacy API, background Celery worker, and reconciliation) converge on `PublishingService`.
Replaying or concurrently executing the same intent across different entry points yields exactly **one** provider object (`provider.total_media_count == 1`).

---

## 4. Publish Eligibility Gate & Fail-Closed Taxonomy
The `PublishEligibilityGate` strictly enforces fail-closed validation before any publish intent can be prepared or submitted.
A production is ineligible if ANY upstream evidence is missing, stale, or contradictory.

### Denial Reason Taxonomy (`PublishEligibilityDenialReason`):
- `NO_CURRENT_ARTIFACT`: Artifact does not exist or has non-current status (`IS_SUPERSEDED`, `PURGED`, `FAILED`).
- `RUNTIME_TRUTH_MISSING`: No `ProductionRuntimeTruth` record exists, schema version is outdated, or SHA-256 hash does not match the physical artifact.
- `PRODUCTION_QA_NOT_PASS`: `ProductionQAEngine` status is not `PASSED` / `"PASS"`.
- `GUARDIAN_NOT_ACCEPTED`: Guardian governance record is missing or not marked accepted.
- `CREATIVE_QA_NOT_PASS`: P24-D `CreativeQAResult` status is not `PASS` or `is_accepted` is false.
- `PACKAGING_NOT_ACCEPTED`: P24-C `PackagingPlan` missing or missing selected title/description.
- `THUMBNAIL_MISSING`: Physical thumbnail artifact missing, path does not exist, or validation failed.
- `ATTRIBUTION_REQUIRED`: Required attribution block from P23-D / P24-C is missing or modified in the metadata payload.
- `PROVIDER_NOT_CONFIGURED`: Target platform account is inactive, disconnected, or missing required scopes.
- `STALE_ARTIFACT`: Artifact has been superseded by a newer accepted artifact in the same production lineage.
- `PAYLOAD_LINEAGE_MISMATCH`: Packaging plan, creative QA result, or artifact belong to discordant production requests or channel identities.

---

## 5. PublishIntent & Canonical Payload Checksum
A `PublishIntent` is effectively immutable once prepared.
It deterministically captures:
- `production_request_id`
- `artifact_id`
- `platform_account_id` / provider channel identity
- `platform` (e.g. `YOUTUBE`)
- `visibility` (`PRIVATE`, `UNLISTED`, `PUBLIC`, `SCHEDULED`)
- `schedule_spec` (validated UTC future timestamp, timezone)
- `payload`: packaging metadata (accepted title, description with preserved attribution, tags, thumbnail path)
- `intent_checksum`: deterministic SHA-256 digest covering artifact hash, title, description, tags, thumbnail hash, platform, visibility, schedule, and ChannelDNA revision.

### Replay & Idempotency:
- Replay with identical payload and artifact produces the same intent checksum and reuses or reconciles the existing publication attempt.
- Material modifications to packaging or target yield a distinct checksum, incrementing the intent revision number without duplicate video creation.

---

## 6. Publish State Machine
Transitions adhere to a strict 11-state deterministic lifecycle (`PublishingState`):
1. `PREPARED`: Initial eligibility passed, intent prepared.
2. `ELIGIBLE`: Pre-execution validation verified.
3. `SUBMITTING`: Claimed by publisher worker under concurrency fencing.
4. `UPLOADED`: Video binary successfully transferred to provider.
5. `METADATA_APPLIED`: Title, description, tags, and category applied.
6. `THUMBNAIL_APPLIED`: Custom physical thumbnail uploaded and linked.
7. `SCHEDULED`: Future publication timestamp registered with provider.
8. `PUBLISHED`: Visibility set to target state (default `PRIVATE`).
9. `FAILED_RETRYABLE`: Transient failure (network drop, provider 5xx, rate limit); eligible for bounded backoff.
10. `FAILED_TERMINAL`: Permanent failure (invalid grant, policy rejection, missing file); stops execution without storm.
11. `RECONCILIATION_REQUIRED`: Interrupted execution, partial failure, or lost local response requiring provider state sync.

Illegal transitions fail closed by raising `IllegalPublishStateTransitionError`.

---

## 7. PublishReceipt & Secret Boundary
`PublishReceipt` represents the immutable record of provider truth:
- `publish_receipt_id`
- `publish_intent_id`
- `attempt_id`
- `provider`
- `external_media_id`
- `external_url`
- `provider_status`
- `payload_checksum`
- `completion_state`
- `completed_at`
- `response_metadata`

### Secret Boundary:
- `PublishReceipt` automatically redacts sensitive tokens (`access_token`, `refresh_token`, `client_secret`, `session_token`, `bearer`) in post-initialization hooks.
- No tokens are logged in structured JSON logs or stored in receipt records.

---

## 8. Lineage & Hand-off Contracts
Full traceability is maintained:
`PublishReceipt` → `PublishIntent` → `PackagingPlan` (P24-C) → `CreativeQAResult` (P24-D) → `MediaArtifact` → `ProductionRuntimeTruth` → `ContentGenerationRequest` → `ChannelDNARevision` (P24-A).

### Handoff to P25-B Analytics:
`PublishReceipt.external_media_id` and `PublishReceipt.provider` provide the canonical foreign key for subsequent telemetry, performance tracking, and analytics ingestion in P25-B.
