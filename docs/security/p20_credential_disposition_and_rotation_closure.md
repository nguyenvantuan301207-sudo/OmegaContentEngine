# P20 Credential Data Disposition Policy and Rotation Closure Specification

**Document Version:** 1.0.0  
**Phase:** P20-D1 Security Closure Addendum  
**Scope:** Policy, Architecture, Runbook, and Manifest for Closing Blocker B6 (Exposed Credential Security)

---

## 1. Secret Material Handling Contract

### 1.1 Zero-Secret-Exposure Invariants
The production target key (e.g., `key_version = 3`) and all historical encryption keys must never appear in:
- Interactive command-line arguments (e.g., `--key <value>`)
- Plaintext shell command histories (`.bash_history`, PowerShell PSReadLine)
- LLM / Antigravity interaction prompts and agent transcripts
- Git tracked files, commit messages, or diffs
- Tracked or shared `.env` files
- Test fixtures or unit test code
- Operator status outputs, reports, logs, or stdout/stderr streams
- Docker build arguments (`--build-arg`) or Docker image layers
- Application logs or exception tracebacks

### 1.2 Target Key Generation and Storage Destination
- **Generation:** Target key material must be generated offline or in an ephemeral operator container using cryptographic randomness:
  ```bash
  python -c "from cryptography.fernet import Fernet; import sys; sys.stdout.write(Fernet.generate_key().decode('utf-8'))"
  ```
- **Storage:** Keys must be stored exclusively in a secure, mode-0600 file on the host (e.g., `/etc/omega/secrets/keyring.json` or Docker Swarm/Kubernetes secret) accessible only by the root/operator service account.

### 1.3 Injection and Runtime Consumption
- **Multi-Key Keyring Structure:**
  ```json
  {
    "1": "<v1-historical-key>",
    "2": "<v2-current-key>",
    "3": "<v3-target-key>"
  }
  ```
- **Application Injection:**
  The container runtime receives the keyring via a read-only secret file mount or environment variable `OMEGA_KEYRING_FILE=/run/secrets/omega_keyring.json`.
- **Active Key Version Configuration:**
  `OMEGA_ACTIVE_KEY_VERSION=3`
- **Maintenance CLI Consumption:**
  The maintenance tool `python -m omega.maintenance.rotate_vault_keys` reads directly from the injected `CredentialVaultService` keyring without requiring command-line key arguments.
- **Preflight Verification Without Echo:**
  A read-only preflight check validates that:
  1. The secret file exists and is non-empty.
  2. The JSON payload parses into a dictionary mapping integer versions to valid 32-byte URL-safe base64 strings.
  3. The active target version (e.g., 3) is present in the keyring.
  4. Only summary metadata (e.g., `versions_present=[1, 2, 3]`, `active_version=3`, `valid_fernet=True`) is logged. No secret bytes are printed.

---

## 2. Formal Dirty-Data Disposition Policy

Due to historical integration tests executing against shared production without database isolation (commit `6ce5ca3`), 125 non-clean rows exist in `credential_vault`.

### 2.1 Guiding Invariants
1. **Audit Lineage Preservation:** Hard SQL deletion (`DELETE FROM`) of production-domain rows is prohibited. Foreign keys to `platform_accounts` enforce `ON DELETE RESTRICT`. Historical records and transition logs must remain immutable.
2. **Fail-Closed Dispatch Fencing:** Any non-production or corrupt credential must be rendered formally unreachable by publishing, scheduling, or analytics background workers.
3. **No Phantom Rotations:** Test residue and corrupt rows must never be silently rotated or re-encrypted into the target production key version.

### 2.2 Category Classification and Legal Disposition

| Category | Population | Cryptographic Status | Domain References | Required Legal Disposition |
| :--- | :--- | :--- | :--- | :--- |
| **A. ZERO_REFERENCE_TEST_RESIDUE** | 84 rows | Structurally valid Fernet, unknown ephemeral test key | 0 intents, 0 tasks, 0 analytics | Mark `PlatformAccount.status = REVOKED`. Exclude credential from rotation. Retain row for audit lineage. |
| **B. NONTERMINAL_TEST_RESIDUE** | 1 row | Corrupt ciphertext (`invalid_corrupt_ciphertext_bytes`) | 1 `PublishIntent` in `DRAFT` | Transition intent: `DRAFT -> CANCELLED`. Mark `PlatformAccount.status = REVOKED`. Exclude credential from rotation. |
| **C. ACTIVE_OPERATIONAL_REFERENCE_TEST_RESIDUE** | 38 rows | Corrupt ciphertext (`invalid_corrupt_ciphertext_bytes`) | 38 `PublishIntent` in `APPROVED` | Transition intent: `APPROVED -> CANCELLED`. Transition Task: `READY -> CANCELLED`. Transition Mission: `RUNNING -> CANCELLED`. Mark `PlatformAccount.status = REVOKED`. Exclude credential from rotation. |
| **D. CORRUPT_CREDENTIAL_DATA** | 39 rows total (20 v1, 19 v2) | Irrecoverable non-Fernet literal string | Covered in categories B and C | No re-encryption possible. Exclude from rotation. |
| **E. UNKNOWN_KEY_CREDENTIAL_DATA** | 84 rows total (48 v1, 36 v2) | Irrecoverable lost test key | Covered in category A | No re-encryption possible. Exclude from rotation. |
| **F. MISLABELLED_KEY_VERSION_DATA** | 2 rows (key_version=999) | Decryptable with historical v1 key | 2 `PublishIntent` in `APPROVED` | Provenance proves origin as test fixtures (`setup_shadow_fixtures`). Do not repair metadata. Transition: intent `APPROVED -> CANCELLED`, task `READY -> CANCELLED`, mission `RUNNING -> CANCELLED`, account `ACTIVE -> REVOKED`. Exclude from production rotation. |

---

## 3. Evaluation of Existing Lifecycle Transitions

All proposed dispositions use exclusively **existing, authorized lifecycle state transitions**:

1. **`PlatformAccountStatus`:**
   - Legal enum values: `ACTIVE`, `EXPIRED`, `REVOKED`.
   - Legal transition: `ACTIVE -> REVOKED`.
   - Effect: Platform accounts with status `REVOKED` fail preflight checks in `PublishExecutionService.validate_publish_readiness` (`credentials_ready = False`), blocking all dispatch paths.
2. **`PublishIntentState`:**
   - Legal enum values: `DRAFT`, `APPROVED`, `CLAIMED`, `PUBLISHED`, `FAILED`, `SUPERSEDED`, `CANCELLED`.
   - Legal transitions: `APPROVED -> CANCELLED`, `DRAFT -> CANCELLED`.
   - Authority: Handled via `PublishIntentService.cancel_intent()`, which writes an immutable transition log row to `publish_intent_transitions`.
   - Effect: Once cancelled, intents cannot be claimed by publisher workers (which only query `state = 'APPROVED'`).
3. **`TaskState`:**
   - Legal transitions: `READY -> CANCELLED` is explicitly authorized in `VALID_TASK_TRANSITIONS`.
4. **`MissionState`:**
   - Legal transitions: `RUNNING -> CANCELLED` is explicitly authorized in `VALID_MISSION_TRANSITIONS`.

Zero new schema fields or migrations are required.

---

## 4. Exact 125-Row Dirty-Data Disposition Manifest

The complete machine-readable manifest is persisted in `dirty_data_manifest.json`.

### Aggregate Summary Check
- **Total Corrupt Rows:** 39 (20 v1, 19 v2)
  - Zero Reference: 0
  - Terminal Reference: 0
  - Nonterminal Reference (`DRAFT` intent): 1
  - Active Operational Reference (`APPROVED` intent): 38
- **Total Unknown Ephemeral Key Rows:** 84 (48 v1, 36 v2)
  - Zero Reference: 84
  - Active Reference: 0
- **Total Version-999 Rows:** 2
  - Active Operational Reference (`APPROVED` intent): 2
- **Sum Total:** $39 + 84 + 2 = 125$ rows.
- **Uniqueness:** Exactly 125 unique `credential_vault.id` entries; zero omissions, zero duplicates.

---

## 5. Security Status of Historical and Current Keys

1. **Historical Version-1 Key:**
   - **Status:** EXPOSED_IN_GIT_HISTORY = YES (present in `backend/tests/conftest.py` from commit `f2e89e32`).
   - **Requirement:** Must be retired immediately after rotating the 974 legitimate version-1 rows.
2. **Current Version-2 Key:**
   - **Status:** EXPOSED_IN_LOCAL_ARTIFACTS = YES (discovered in plaintext local cutover configuration).
   - **Requirement:** Must be retired immediately after rotating the 656 legitimate version-2 rows.

**Target Cryptographic End State:**
- Active production key: `key_version = 3`.
- Keyring during maintenance: `{1: <v1>, 2: <v2>, 3: <v3>}`.
- Keyring after stabilization cutover: `{3: <v3>}`.
- Legitimate production rows operational under v3: **1,630 rows**.
- Dirty rows excluded and formally retired under `REVOKED` / `CANCELLED`: **125 rows**.

---

## 6. Minimal Phase Model Architecture Correction: Subphase P20-D1S

To resolve the omission of maintenance placement without redesigning the frozen P20-D phase model, a single bounded corrective subphase is designated:

```
[ P20-D0 ] Architecture & Containment (Closed)
    │
[ P20-D1 ] Production Hardening Implementation & Forensics (Closing)
    │
[ P20-D1S ] Security Closure Maintenance (Bounded Maintenance Window)
    │       ├── Apply 125-row dirty-data disposition (REVOKED / CANCELLED)
    │       ├── Inject temporary 3-key keyring
    │       ├── Rotate 1,630 legitimate rows (v1/v2 -> v3) via maintenance CLI
    │       ├── Verify 1,630 rows under v3; verify 125 dirty rows excluded
    │       └── Close Security Blocker B6
    │
[ P20-D2 ] Application & Schema Cutover (Gates remain OFF, Migration 026)
    │
[ P20-D3 ] Controlled Rollout, Retry-7, and Final Acceptance
```

---

## 7. Bounded Security-Maintenance Runbook (P20-D1S)

All steps require an offline maintenance window with the application stopped and all gates verified OFF.

- **Phase M0 — Authorization & Freeze:** Verify explicit human operator authorization for P20-D1S. Freeze code and database state.
- **Phase M1 — Fail-Closed Gate Verification:** Assert all 6 production gates are OFF. Assert publisher container is ABSENT and Celery queue is 0.
- **Phase M2 — External Backup:** Execute `pg_dump` of shared production database to verified external storage before any mutation.
- **Phase M3 — Stop Writers/Readers:** Stop `omega-api`, `omega-worker`, `omega-beat`. Confirm Postgres remains up.
- **Phase M4 — Apply Dirty-Data Disposition:**
  - Transition 40 test intents (38 corrupt + 2 v999) from `APPROVED` to `CANCELLED` via `PublishIntentService.cancel_intent`.
  - Transition 1 test intent (corrupt) from `DRAFT` to `CANCELLED`.
  - Transition 40 corresponding Tasks from `READY` to `CANCELLED`.
  - Transition 40 corresponding Missions from `RUNNING` to `CANCELLED`.
  - Transition 125 PlatformAccounts from `ACTIVE` to `REVOKED`.
  - Assert zero rows in `credential_vault` are modified or deleted during this phase.
- **Phase M5 — Inject Multi-Key Keyring Securely:**
  - Mount secure keyring containing versions `{1: v1, 2: v2, 3: v3}` with `active_version = 3`.
- **Phase M6 — Rotate Legitimate Credential Population:**
  - Execute rotation CLI in targeted execution mode:
    ```bash
    python -m omega.maintenance.rotate_vault_keys --target-version 3 --current-version 1 --execute
    python -m omega.maintenance.rotate_vault_keys --target-version 3 --current-version 2 --execute
    ```
  - Exactly 974 v1 rows rotate to v3.
  - Exactly 656 v2 rows rotate to v3.
  - Total rotated rows: 1,630.
- **Phase M7 — Strict Verification:**
  - Execute verification CLI:
    ```bash
    python -m omega.maintenance.rotate_vault_keys --target-version 3 --verify
    ```
  - Assert exactly 1,630 rows verified; assert 125 dirty rows reported failed/excluded.
- **Phase M8 — Promote Target Active Key:**
  - Configure `OMEGA_ACTIVE_KEY_VERSION=3` in production configuration.
- **Phase M9 — Restart Application Services:**
  - Start `omega-api`, `omega-worker`, `omega-beat` with gates still OFF.
- **Phase M10 — Application Canaries:**
  - Assert `/health` and `/ops/status` return HTTP 200.
  - Verify token decryption for canary channel accounts.
- **Phase M11 — Stabilization Observation:**
  - Monitor logs for 60 minutes for decryption errors.
- **Phase M12 — Retire Compromised Key Material:**
  - Remove keys 1 and 2 from keyring configuration once all services stabilize.
- **Phase M13 — Final Blocker B6 Closure Evidence:**
  - Persist signed disposition log and rotation verification manifest. Mark B6 CLOSED.
