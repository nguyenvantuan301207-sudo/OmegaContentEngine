# OMEGA Publisher Recovery & Network Interruption Runbook

This runbook defines the authoritative operational protocol for managing network loss, timeouts, and state reconciliation in the OMEGA-011 Publisher pipeline.

---

## 1. Fundamental Principles

1. **Provider Truth Outranks Local DB State**:
   - Following network interruptions, socket disconnections, or gateway timeouts, local database progress counters (`bytes_uploaded`) are frequently behind provider reality.
   - Never assume an upload failed, never assume it succeeded, and never assume byte progress remained static.
   - Provider state must be queried authoritatively before any execution decision.

2. **Zero Blind Retries**:
   - Never rerun `execute_publish` or restart an upload from byte 0 without querying the existing session.
   - A blind retry risks duplicate video uploads and quota waste on external platforms.

3. **Existing Session Exclusivity**:
   - While an existing `UploadSession` is unresolved, the initialization of a secondary or replacement upload session is strictly prohibited.
   - Counter rule: `UPLOAD_INIT_CALLS = 0` during all recovery and resume workflows.

---

## 2. Recovery Workflow & State Classification

When an interruption occurs, classify the system into one of five definitive states:

1. **State A (Not Started)**:
   - Attempt state is `UPLOADING` or `CLAIMED` with natural lease expiry; provider confirms 0 or initial checkpoint bytes. Resumable session is clean.
2. **State B (Already Finalized)**:
   - Provider reports upload is already complete (`HTTP 200/201` with `provider_video_id`).
   - Action: Transition attempt to `SUCCEEDED`, transition intent to `PUBLISHED`. **Do NOT upload anything.**
3. **State C (Started but Outcome Ambiguous)**:
   - Provider reports offset greater than local checkpoint, but less than total file size.
   - Action: Adopt existing attempt, seek local file to authoritative provider offset, transmit only remaining bytes using the **same** session URI.
4. **State D (Session Terminal)**:
   - Provider returns `HTTP 404` or `HTTP 410` (session expired or discarded).
   - Action: Mark attempt `MANUAL_HOLD`. Do NOT auto-create a replacement session. Await operator direction.
5. **State E (Unexpected Local Mutation)**:
   - Multiple sessions detected, unapproved attempts exist, or hash mismatch observed.
   - Action: Immediate `SAFE_HOLD` and halt.

---

## 3. Network Call & Budget Limits

For any single controlled recovery execution:

| Operation | Strict Budget Limit | Notes |
|---|---|---|
| **OAuth Token Refresh** | **Maximum 1** | Only when `access_token_expires_at <= now + 5 min`. |
| **Session Progress Query** | **Exactly 1** | Single status query (`bytes */total_bytes`). Double-querying prohibited. |
| **Session Initialization** | **Exactly 0** | Reuse existing persisted session URI. |
| **Chunk Upload** | **Maximum 1** | Upload only remaining bytes (`start_byte = provider_offset`). |
| **Verification Read** | **Maximum 1** | Read-only check after finalization. |

---

## 4. Post-Finalization Failure Policy

Once an upload chunk returns authoritative completion (`HTTP 200/201` with `provider_video_id`):
- The publication result is **authoritative and immutable**.
- If a subsequent read-only verification (e.g. `videos.list`) fails due to transient network errors or authentication mismatches:
  - **DO NOT** retry the upload.
  - **DO NOT** initialize another resumable session.
  - **DO NOT** retransmit media bytes.
  - **DO NOT** execute ad-hoc OAuth refresh calls.
  - **FAIL CLOSED**: Mark verification for human review and **STOP immediately**.

---

## 5. Security & Credential Hygiene

1. **No Plaintext or Prefix Logging**:
   - Never print access tokens, refresh tokens, PKCE verifiers, or session URIs.
   - Never log token prefixes (e.g., `token[:10]`), suffixes, or `repr(token)`.
   - Validate credentials exclusively using boolean assertions (e.g., `assert token_present is True`).

2. **No Query Parameter Token Exposure**:
   - Never transmit access tokens as URL query strings (e.g., `tokeninfo?access_token=...`).
   - Use standard `Authorization: Bearer <token>` headers only.

3. **No Direct Production Credential Mutations**:
   - Never modify `CredentialVault.access_token_expires_at` or ciphertexts via ad-hoc scripts.
   - Credential rotation and persistence must flow exclusively through standard services (`ReconciliationService`, `PublishExecutionService`).

---

## 6. Execution Checklist

- [ ] Check Git baseline (`HEAD`, `git status --short`, `git diff --check`).
- [ ] Confirm `omega-worker` and `omega-beat` are **OFF**.
- [ ] Verify canary file size and SHA256 using bounded reads.
- [ ] Read local DB state (`PublishIntent`, `PublishAttempt`, `UploadSession`).
- [ ] Query provider session status exactly once.
- [ ] Confirm provider offset matches expected remaining range.
- [ ] Seek local stream to exact provider offset and transmit final chunk.
- [ ] On completion, verify `provider_video_id`, commit `SUCCEEDED`, and halt.
