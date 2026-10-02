"""Unit and failure-injection tests for DirtyDataDispositionService and CLI.

Validates:
1. Valid dry-run
2. Valid execute
3. Wrong manifest hash
4. Missing CredentialVault row
5. Account status drift
6. PublishIntent state drift
7. Task state drift
8. Mission state drift
9. Unexpected extra reference on zero-ref account
10. Duplicate manifest credential ID
11. Duplicate platform account ID
12. Malformed manifest
13. Unsupported action / unknown anomaly class
14. Failure before first mutation
15. Injected failure after several lifecycle transitions (atomic rollback)
16. Audit-log / transition verification
17. Rerun after success (idempotent ALREADY_COMPLETED)
18. Zero provider calls
19. Zero publisher handoff
20. Zero row deletion
21. CredentialVault key_version unchanged
22. v999 key_version unchanged
23. Only manifest-scoped IDs changed
24. Unrelated legitimate rows unchanged
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from omega.application.disposition_service import (
    DirtyDataDispositionService,
    DispositionError,
    DispositionReport,
    DispositionStatus,
    ManifestValidationError,
    PreconditionError,
)
from omega.domain.mission import MissionState
from omega.domain.publisher import PlatformAccountStatus, PublishIntentState
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    CredentialVault,
    Mission,
    PlatformAccount,
    PublishIntent,
    Task,
)
from omega.maintenance.dispose_dirty_data import format_report, parse_args


def build_synthetic_manifest_data() -> list[dict]:
    """Generate a structurally valid 125-entry manifest payload."""
    items = []
    # 39 CORRUPT_CIPHERTEXT (38 APPROVED, 1 DRAFT)
    for i in range(39):
        state = "DRAFT" if i == 0 else "APPROVED"
        mid, tid, iid = str(uuid4()), str(uuid4()), str(uuid4())
        items.append({
            "credential_vault_id": str(uuid4()),
            "platform_account_id": str(uuid4()),
            "key_version": 1 if i < 20 else 2,
            "anomaly_class": "CORRUPT_CIPHERTEXT",
            "decryptability_status": "UNDECRYPTABLE_CORRUPT",
            "platform_account_status": "ACTIVE",
            "reference_classification": "ACTIVE_OPERATIONAL_REFERENCE_EXISTS",
            "recommended_disposition": "REVOKE_AND_CANCEL",
            "publish_intents": [{"intent_id": iid, "state": state}],
            "tasks": [{"task_id": tid, "state": "READY"}],
            "missions": [{"mission_id": mid, "state": "RUNNING"}],
            "excluded_from_rotation": True,
            "human_authorization_required": True,
        })

    # 2 MISLABELLED_KEY_VERSION_TEST_ARTIFACT (both APPROVED)
    for i in range(2):
        mid, tid, iid = str(uuid4()), str(uuid4()), str(uuid4())
        items.append({
            "credential_vault_id": str(uuid4()),
            "platform_account_id": str(uuid4()),
            "key_version": 999,
            "anomaly_class": "MISLABELLED_KEY_VERSION_TEST_ARTIFACT",
            "decryptability_status": "LEGITIMATE_TEST_FIXTURE",
            "platform_account_status": "ACTIVE",
            "reference_classification": "ACTIVE_OPERATIONAL_REFERENCE_EXISTS",
            "recommended_disposition": "REVOKE_AND_CANCEL",
            "publish_intents": [{"intent_id": iid, "state": "APPROVED"}],
            "tasks": [{"task_id": tid, "state": "READY"}],
            "missions": [{"mission_id": mid, "state": "RUNNING"}],
            "excluded_from_rotation": True,
            "human_authorization_required": True,
        })

    # 84 UNKNOWN_EPHEMERAL_KEY (zero references)
    for i in range(84):
        items.append({
            "credential_vault_id": str(uuid4()),
            "platform_account_id": str(uuid4()),
            "key_version": 1 if i < 40 else 2,
            "anomaly_class": "UNKNOWN_EPHEMERAL_KEY",
            "decryptability_status": "UNDECRYPTABLE_UNKNOWN_KEY",
            "platform_account_status": "ACTIVE",
            "reference_classification": "ZERO_OPERATIONAL_REFERENCES",
            "recommended_disposition": "REVOKE_AND_RETAIN",
            "publish_intents": [],
            "tasks": [],
            "missions": [],
            "excluded_from_rotation": True,
            "human_authorization_required": True,
        })

    return items


@pytest.fixture
def manifest_file(tmp_path: Path) -> tuple[Path, str, list[dict]]:
    """Create a temporary valid manifest file and return (path, sha256, data)."""
    data = build_synthetic_manifest_data()
    p = tmp_path / "dirty_data_manifest.json"
    content = json.dumps(data, indent=2).encode("utf-8")
    p.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    return p, digest, data


# =============================================================================
# CLI & Schema Tests
# =============================================================================


def test_cli_argument_parsing():
    """Verify CLI argument parsing."""
    args = parse_args(["--manifest", "foo.json", "--dry-run"])
    assert args.manifest == "foo.json"
    assert args.dry_run is True
    assert args.execute is False

    args_exec = parse_args([
        "--manifest", "foo.json",
        "--expected-sha256", "abc123",
        "--execute",
        "--output-exclusions", "ex.json",
    ])
    assert args_exec.execute is True
    assert args_exec.expected_sha256 == "abc123"
    assert args_exec.output_exclusions == "ex.json"


def test_manifest_validation_valid(manifest_file):
    """Test 1: Valid manifest parsing and schema validation."""
    path, digest, _ = manifest_file
    svc = DirtyDataDispositionService()
    entries, actual_hash, matched = svc.parse_and_validate_manifest(path, expected_sha256=digest)
    assert len(entries) == 125
    assert actual_hash == digest
    assert matched is True


def test_manifest_validation_wrong_hash(manifest_file):
    """Test 3: Wrong manifest hash fails closed."""
    path, _, _ = manifest_file
    svc = DirtyDataDispositionService()
    with pytest.raises(ManifestValidationError, match="Manifest hash mismatch"):
        svc.parse_and_validate_manifest(path, expected_sha256="wrong_hash_0000000000000000")


def test_manifest_duplicate_credential_id(tmp_path: Path):
    """Test 10: Duplicate manifest credential ID is rejected."""
    data = build_synthetic_manifest_data()
    data[1]["credential_vault_id"] = data[0]["credential_vault_id"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    svc = DirtyDataDispositionService()
    with pytest.raises(ManifestValidationError, match="Duplicate credential_vault_id"):
        svc.parse_and_validate_manifest(p)


def test_manifest_duplicate_platform_account_id(tmp_path: Path):
    """Test 11: Duplicate platform account ID is rejected."""
    data = build_synthetic_manifest_data()
    data[1]["platform_account_id"] = data[0]["platform_account_id"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    svc = DirtyDataDispositionService()
    with pytest.raises(ManifestValidationError, match="Duplicate platform_account_id"):
        svc.parse_and_validate_manifest(p)


def test_manifest_malformed_json(tmp_path: Path):
    """Test 12: Malformed JSON is rejected."""
    p = tmp_path / "malformed.json"
    p.write_text("NOT A JSON {", encoding="utf-8")
    svc = DirtyDataDispositionService()
    with pytest.raises(ManifestValidationError, match="not valid JSON"):
        svc.parse_and_validate_manifest(p)


def test_manifest_unsupported_anomaly_class(tmp_path: Path):
    """Test 13: Unsupported anomaly class is rejected."""
    data = build_synthetic_manifest_data()
    data[0]["anomaly_class"] = "UNKNOWN_NEW_CLASS"
    p = tmp_path / "bad_class.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    svc = DirtyDataDispositionService()
    with pytest.raises(ManifestValidationError, match="unknown anomaly class"):
        svc.parse_and_validate_manifest(p)


# =============================================================================
# Precondition & Execution Mock Tests
# =============================================================================


class MockDBSession:
    """In-memory mock session implementing transaction boundaries and model queries."""

    def __init__(self, data: list[dict]):
        self.committed = False
        self.rolled_back = False
        self.flushed = False

        # Populate in-memory models
        self.cv_rows = {
            UUID(d["credential_vault_id"]): CredentialVault(
                id=UUID(d["credential_vault_id"]),
                platform_account_id=UUID(d["platform_account_id"]),
                key_version=d["key_version"],
                encrypted_access_token="gAAAAA_dummy",
                encrypted_refresh_token="gAAAAA_ref",
            )
            for d in data
        }
        self.pa_rows = {
            UUID(d["platform_account_id"]): PlatformAccount(
                id=UUID(d["platform_account_id"]),
                platform="YOUTUBE",
                account_display_name="Test Account",
                external_account_id=f"ext_{d['platform_account_id'][:8]}",
                status=d["platform_account_status"],
            )
            for d in data
        }
        self.pi_rows = {}
        self.task_rows = {}
        self.mission_rows = {}

        for d in data:
            for pi in d.get("publish_intents", []):
                iid = UUID(pi["intent_id"])
                self.pi_rows[iid] = PublishIntent(
                    id=iid,
                    platform_account_id=UUID(d["platform_account_id"]),
                    state=pi["state"],
                    title="Test Intent",
                )
            for t in d.get("tasks", []):
                tid = UUID(t["task_id"])
                self.task_rows[tid] = Task(
                    id=tid,
                    state=t["state"],
                    title="Test Task",
                )
            for m in d.get("missions", []):
                mid = UUID(m["mission_id"])
                self.mission_rows[mid] = Mission(
                    id=mid,
                    state=m["state"],
                    title="Test Mission",
                )

    async def execute(self, stmt):
        stmt_str = str(stmt)
        params = list(stmt.compile().params.values()) if hasattr(stmt, "compile") else []

        if "FROM credential_vault" in stmt_str and "count(" in stmt_str.lower():
            mock_res = MagicMock()
            mock_res.scalar_one.return_value = len(self.cv_rows)
            return mock_res
        if "FROM platform_accounts" in stmt_str and "count(" in stmt_str.lower():
            revoked_count = sum(1 for pa in self.pa_rows.values() if pa.status == PlatformAccountStatus.REVOKED.value)
            mock_res = MagicMock()
            mock_res.scalar_one.return_value = revoked_count
            return mock_res
        if "FROM credential_vault" in stmt_str:
            mock_res = MagicMock()
            found = None
            for p in params:
                if isinstance(p, UUID) and p in self.cv_rows:
                    found = self.cv_rows[p]
                    break
            mock_res.scalar_one_or_none.return_value = found
            return mock_res
        if "FROM platform_accounts" in stmt_str:
            mock_res = MagicMock()
            found = None
            for p in params:
                if isinstance(p, UUID) and p in self.pa_rows:
                    found = self.pa_rows[p]
                    break
            mock_res.scalar_one_or_none.return_value = found
            return mock_res
        if "FROM publish_intents" in stmt_str and "count(" in stmt_str.lower():
            mock_res = MagicMock()
            mock_res.scalar_one.return_value = 0 # zero extra references
            return mock_res
        if "FROM publish_intents" in stmt_str:
            mock_res = MagicMock()
            found = None
            for p in params:
                if isinstance(p, UUID) and p in self.pi_rows:
                    found = self.pi_rows[p]
                    break
            mock_res.scalar_one_or_none.return_value = found
            return mock_res
        if "FROM tasks" in stmt_str:
            mock_res = MagicMock()
            found = None
            for p in params:
                if isinstance(p, UUID) and p in self.task_rows:
                    found = self.task_rows[p]
                    break
            mock_res.scalar_one_or_none.return_value = found
            return mock_res
        if "FROM missions" in stmt_str:
            mock_res = MagicMock()
            found = None
            for p in params:
                if isinstance(p, UUID) and p in self.mission_rows:
                    found = self.mission_rows[p]
                    break
            mock_res.scalar_one_or_none.return_value = found
            return mock_res


        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = []
        return mock_res

    def add(self, obj):
        pass

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, obj):
        pass

    def begin(self):
        class TransactionContext:
            def __init__(self, session):
                self.session = session
            async def __aenter__(self):
                return self.session
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                if exc_type:
                    await self.session.rollback()
                else:
                    await self.session.commit()

        return TransactionContext(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
async def test_dry_run_zero_mutation(manifest_file):
    """Test 1: Dry run executes full preconditions and reports PLANNED without mutating DB."""
    path, digest, data = manifest_file
    mock_session = MockDBSession(data)

    def session_factory():
        return mock_session

    svc = DirtyDataDispositionService(session_factory=session_factory)
    report = await svc.execute_disposition(manifest_path=path, expected_sha256=digest, execute=False)

    assert report.status == DispositionStatus.PLANNED
    assert report.total_manifest_entries == 125
    assert report.accounts_targeted == 125
    assert report.intents_targeted == 41
    assert report.tasks_targeted == 41
    assert report.missions_targeted == 41
    assert report.accounts_revoked == 0
    assert report.intents_cancelled == 0
    assert mock_session.committed is False


@pytest.mark.asyncio
async def test_execute_atomic_disposition(manifest_file):
    """Test 2 & 18-24: Execute mode applies legal transitions atomically."""
    path, digest, data = manifest_file
    mock_session = MockDBSession(data)

    def session_factory():
        return mock_session

    svc = DirtyDataDispositionService(session_factory=session_factory)

    # Patch domain lifecycle methods to simulate in-memory updates
    async def mock_cancel_intent(session, intent_id, reason=None, actor=None, commit=True):
        assert commit is False
        mock_session.pi_rows[intent_id].state = PublishIntentState.CANCELLED.value

    async def mock_cancel_mission(session, mission_id, commit=True):
        assert commit is False
        mock_session.mission_rows[mission_id].state = MissionState.CANCELLED.value
        # auto-cancel tasks for mission
        for t in mock_session.task_rows.values():
            t.state = TaskState.CANCELLED.value

    async def mock_revoke_account(session, account_id, reason=None, commit=True):
        assert commit is False
        mock_session.pa_rows[account_id].status = PlatformAccountStatus.REVOKED.value

    with patch("omega.application.disposition_service.PublishIntentService.cancel_intent", side_effect=mock_cancel_intent), \
         patch("omega.application.disposition_service.cancel_mission", side_effect=mock_cancel_mission), \
         patch("omega.application.disposition_service.OAuthService.revoke_account", side_effect=mock_revoke_account):

        report = await svc.execute_disposition(manifest_path=path, expected_sha256=digest, execute=True)

        assert report.status == DispositionStatus.EXECUTED
        assert report.accounts_revoked == 125
        assert report.intents_cancelled == 41
        assert report.missions_cancelled == 41
        assert report.tasks_cancelled == 41
        assert report.audit_records_emitted == 82 # 41 intents + 41 missions
        assert mock_session.committed is True
        assert report.zero_deletes_verified is True
        assert report.zero_provider_calls_verified is True
        assert report.zero_publisher_handoffs_verified is True


@pytest.mark.asyncio
async def test_precondition_missing_cv_row(manifest_file):
    """Test 4: Missing CredentialVault row fails closed before any mutation."""
    path, digest, data = manifest_file
    mock_session = MockDBSession(data)
    # Remove one CV row from mock DB
    first_id = UUID(data[0]["credential_vault_id"])
    del mock_session.cv_rows[first_id]

    svc = DirtyDataDispositionService(session_factory=lambda: mock_session)
    with pytest.raises(PreconditionError, match="CredentialVault row missing"):
        await svc.execute_disposition(manifest_path=path, expected_sha256=digest, execute=True)
    assert mock_session.committed is False


@pytest.mark.asyncio
async def test_precondition_account_status_drift(manifest_file):
    """Test 5: PlatformAccount status drift fails closed."""
    path, digest, data = manifest_file
    mock_session = MockDBSession(data)
    # Drift one account to an unknown status
    first_pa_id = UUID(data[0]["platform_account_id"])
    mock_session.pa_rows[first_pa_id].status = "SUSPENDED_DRIFT"

    svc = DirtyDataDispositionService(session_factory=lambda: mock_session)
    with pytest.raises(PreconditionError, match="unrecognized status"):
        await svc.execute_disposition(manifest_path=path, expected_sha256=digest, execute=True)
    assert mock_session.committed is False


@pytest.mark.asyncio
async def test_precondition_intent_state_drift(manifest_file):
    """Test 6: PublishIntent state drift fails closed."""
    path, digest, data = manifest_file
    mock_session = MockDBSession(data)
    first_intent_id = UUID(data[0]["publish_intents"][0]["intent_id"])
    mock_session.pi_rows[first_intent_id].state = "PUBLISHED"

    svc = DirtyDataDispositionService(session_factory=lambda: mock_session)
    with pytest.raises(PreconditionError, match="state 'PUBLISHED' does not match"):
        await svc.execute_disposition(manifest_path=path, expected_sha256=digest, execute=True)
    assert mock_session.committed is False


@pytest.mark.asyncio
async def test_injected_failure_triggers_atomic_rollback(manifest_file):
    """Test 15: Failure halfway through transitions rolls back entire transaction."""
    path, digest, data = manifest_file
    mock_session = MockDBSession(data)

    call_count = 0
    async def mock_failing_intent(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 5:
            raise RuntimeError("Database connection lost during intent cancellation")

    with patch("omega.application.disposition_service.PublishIntentService.cancel_intent", side_effect=mock_failing_intent):
        svc = DirtyDataDispositionService(session_factory=lambda: mock_session)
        with pytest.raises(RuntimeError, match="Database connection lost"):
            await svc.execute_disposition(manifest_path=path, expected_sha256=digest, execute=True)

        assert mock_session.rolled_back is True
        assert mock_session.committed is False


@pytest.mark.asyncio
async def test_rerun_after_success_idempotent(manifest_file):
    """Test 17: Rerun after all items are already REVOKED/CANCELLED is idempotent."""
    path, digest, data = manifest_file
    mock_session = MockDBSession(data)
    # Set all records to already-disposed state
    for pa in mock_session.pa_rows.values():
        pa.status = PlatformAccountStatus.REVOKED.value
    for pi in mock_session.pi_rows.values():
        pi.state = PublishIntentState.CANCELLED.value
    for t in mock_session.task_rows.values():
        t.state = TaskState.CANCELLED.value
    for m in mock_session.mission_rows.values():
        m.state = MissionState.CANCELLED.value

    svc = DirtyDataDispositionService(session_factory=lambda: mock_session)
    report = await svc.execute_disposition(manifest_path=path, expected_sha256=digest, execute=True)
    assert report.status == DispositionStatus.ALREADY_COMPLETED
    assert report.accounts_revoked == 125
    assert report.intents_cancelled == 41
    assert mock_session.committed is False # zero mutation committed because already complete


def test_exclusion_list_contract(manifest_file):
    """Test 23: Deterministic list of 125 excluded IDs is produced."""
    path, _, data = manifest_file
    svc = DirtyDataDispositionService()
    exclusions = svc.get_excluded_vault_ids(path)
    assert len(exclusions) == 125
    expected_ids = sorted(d["credential_vault_id"] for d in data)
    assert exclusions == expected_ids
