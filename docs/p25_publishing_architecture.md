# P25-A Controlled Publishing Rollout Architecture

## 1. Publishing Authority and Scope
P25-A establishes the canonical, fail-closed publishing rollout boundary for OMEGA Content Engine.
It bridges accepted creative packages from P24 (P24-A ChannelDNA, P24-B CreativeStyle, P24-C Packaging, P24-D Creative QA) and core production artifacts (P20-C, P21 Narrative, P22 Visual, P23 Audio) into immutable publish intents, isolated provider execution, and non-secret durable publish receipts.

P25-A enforces strict provider boundary separation:
- Domain and application services interact solely via the `VideoPublishingProvider` abstraction.
- Zero external mutations are permitted without explicit user authorization (`P25A_REAL_PROVIDER_MUTATED = NO`).
- All normal execution, testing, and canaries run deterministically against `FakePublishingProvider`.
- No database schema migrations were added; existing schema (revision 027 isolated / 026 production) fully persists intents, attempts, upload sessions, and state transitions (`P25A_SCHEMA_CHANGE_REQUIRED = NO`, `MIGRATION_028_CREATED = NO`).

---

## 2. Publish Eligibility Gate & Fail-Closed Taxonomy
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

## 3. PublishIntent & Canonical Payload Checksum
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

## 4. Publish State Machine
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

## 5. Provider Abstraction & Fake Implementation
Orchestration never invokes external vendor SDKs directly.
The `VideoPublishingProvider` interface abstracts:
- `upload_media(intent, attempt_id)`
- `apply_metadata(intent, attempt_id, external_media_id)`
- `apply_thumbnail(intent, attempt_id, external_media_id)`
- `apply_visibility(intent, attempt_id, external_media_id)`
- `schedule_publication(intent, attempt_id, external_media_id)`
- `fetch_publication_state(platform_account_id, external_media_id)`

The offline `FakePublishingProvider` supports deterministic simulation of:
- Clean end-to-end publish flows
- Idempotent duplicates (verifying provider media count remains 1)
- Retryable failures (transient 503s)
- Terminal policy rejections
- Lost local responses (client drop after provider upload succeeds)
- Authoritative remote state queries

---

## 6. PublishReceipt & Secret Boundary
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

## 7. Partial Failure Recovery & Reconciliation
When execution fails mid-stream (e.g., video uploaded but thumbnail failed, or process restarted after network timeout):
- `reconcile_publish(intent_id)` queries the provider via `fetch_publication_state`.
- If the media object exists on the provider, it recovers the `external_media_id` and resumes only the missing steps (metadata, thumbnail, visibility) without re-uploading the video binary.
- Worker concurrency is fenced via transactional lease claims (`submitting_worker_id`, lease timestamps), preventing parallel executions of the same intent.

---

## 8. Lineage & Hand-off Contracts
Full traceability is maintained:
`PublishReceipt` → `PublishIntent` → `PackagingPlan` (P24-C) → `CreativeQAResult` (P24-D) → `MediaArtifact` → `ProductionRuntimeTruth` → `ContentGenerationRequest` → `ChannelDNARevision` (P24-A).

### Handoff to P25-B Analytics:
`PublishReceipt.external_media_id` and `PublishReceipt.provider` provide the canonical foreign key for subsequent telemetry, performance tracking, and analytics ingestion in P25-B.
