"""Unit tests for VaultKeyRotationService and rotate_vault_keys maintenance CLI.

Validates in-memory rotation logic, deterministic keyrings, idempotency,
error handling, report generation, and CLI argument parsing.
Uses deterministic test keys; never uses real secrets.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from omega.application.vault_key_rotation import (
    VaultEntryResult,
    VaultEntryRotationStatus,
    VaultKeyRotationService,
    VaultRotationReport,
)
from omega.infrastructure.models import CredentialVault
from omega.infrastructure.vault import CredentialVaultService
from omega.maintenance.rotate_vault_keys import format_report, parse_args


@pytest.fixture
def deterministic_keyring() -> tuple[dict[int, str], CredentialVaultService]:
    """Provide a deterministic 2-key test keyring."""
    key1 = Fernet.generate_key().decode("utf-8")
    key2 = Fernet.generate_key().decode("utf-8")
    keyring = {1: key1, 2: key2}
    vault = CredentialVaultService(keyring=keyring, active_version=2)
    return keyring, vault


def test_cli_argument_parsing():
    """Verify CLI argument parsing for dry-run and execution flags."""
    test_uuid = str(uuid4())
    # Dry run
    args = parse_args(["--target-version", "2", "--platform-account-id", test_uuid, "--dry-run"])
    assert args.target_version == 2
    assert args.platform_account_id == test_uuid
    assert args.dry_run is True
    assert args.execute is False

    # Execute
    args_exec = parse_args(["--target-version", "2", "--execute"])
    assert args_exec.target_version == 2
    assert args_exec.execute is True


def test_report_formatting_sanitization():
    """Verify ASCII report contains no secret material or plaintext tokens."""
    vault_id = uuid4()
    account_id = uuid4()
    entry = VaultEntryResult(
        vault_id=vault_id,
        platform_account_id=account_id,
        current_key_version=1,
        target_key_version=2,
        access_token_decryptable=True,
        refresh_token_decryptable=True,
        eligible=True,
        status=VaultEntryRotationStatus.ELIGIBLE,
    )
    report = VaultRotationReport(
        target_version=2,
        active_configured_version=2,
        mode="DRY_RUN",
        total_evaluated=1,
        eligible_count=1,
        entries=[entry],
    )
    output = format_report(report)

    assert "OMEGA CREDENTIAL VAULT KEY ROTATION — DRY_RUN" in output
    assert str(vault_id) in output
    assert str(account_id) in output
    assert "v1 -> v2" in output
    assert "Access: OK, Refresh: OK" in output
    # Ensure no token values or keys leaked
    assert "ya29." not in output
    assert "1//" not in output


@pytest.mark.asyncio
async def test_vault_key_rotation_refuses_mismatched_active_version(deterministic_keyring):
    """Execution mode must refuse if target_version does not match configured active version."""
    _, vault = deterministic_keyring
    service = VaultKeyRotationService(vault=vault)

    # Active version is 2, but target is 3
    with pytest.raises(ValueError, match="target key version .* does not match"):
        await service.rotate_keys(target_version=3, execute=True)


@pytest.mark.asyncio
async def test_vault_key_rotation_dry_run_in_memory(deterministic_keyring):
    """Verify in-memory dry-run evaluation identifies eligible rows without DB writes."""
    _, vault = deterministic_keyring

    plain_access = "test_access_token_alpha"
    plain_refresh = "test_refresh_token_beta"

    cipher_acc, v1 = vault.encrypt(plain_access, key_version=1)
    cipher_ref, _ = vault.encrypt(plain_refresh, key_version=1)

    mock_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc,
        encrypted_refresh_token=cipher_ref,
        key_version=1,
    )

    # Mock async session
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entry]
    mock_session.execute.return_value = mock_result

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.rotate_keys(target_version=2, execute=False)

    assert report.total_evaluated == 1
    assert report.eligible_count == 1
    assert report.rotated_count == 0
    assert len(report.entries) == 1

    res = report.entries[0]
    assert res.status == VaultEntryRotationStatus.ELIGIBLE
    assert res.access_token_decryptable is True
    assert res.refresh_token_decryptable is True
    # Zero DB commits
    mock_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_vault_key_rotation_idempotency_already_current(deterministic_keyring):
    """Verify rows already having target key_version are marked ALREADY_CURRENT and skipped."""
    _, vault = deterministic_keyring

    cipher_acc, v2 = vault.encrypt("already_v2_secret", key_version=2)
    mock_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc,
        encrypted_refresh_token=None,
        key_version=2,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entry]
    mock_session.execute.return_value = mock_result

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.rotate_keys(target_version=2, execute=True)

    assert report.total_evaluated == 1
    assert report.already_current_count == 1
    assert report.rotated_count == 0
    assert report.entries[0].status == VaultEntryRotationStatus.ALREADY_CURRENT
    # Zero DB commits
    mock_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_vault_key_rotation_target_key_unavailable():
    """Verify target key missing from keyring fails safely with TARGET_KEY_UNAVAILABLE."""
    key1 = Fernet.generate_key().decode("utf-8")
    vault = CredentialVaultService(keyring={1: key1}, active_version=1)

    cipher_acc, _ = vault.encrypt("secret_data", key_version=1)
    mock_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc,
        encrypted_refresh_token=None,
        key_version=1,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entry]
    mock_session.execute.return_value = mock_result

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    # Target version 2 is not in keyring
    report = await service.rotate_keys(target_version=2, execute=False)

    assert report.failed_count == 1
    assert report.entries[0].status == VaultEntryRotationStatus.TARGET_KEY_UNAVAILABLE


@pytest.mark.asyncio
async def test_vault_key_rotation_corrupted_ciphertext_fails_safely(deterministic_keyring):
    """Verify corrupted or un-decryptable ciphertext marks DECRYPTION_FAILED."""
    _, vault = deterministic_keyring

    mock_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token="corrupted_ciphertext_not_fernet",
        encrypted_refresh_token=None,
        key_version=1,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entry]
    mock_session.execute.return_value = mock_result

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.rotate_keys(target_version=2, execute=False)

    assert report.failed_count == 1
    assert report.entries[0].status == VaultEntryRotationStatus.DECRYPTION_FAILED


@pytest.mark.asyncio
async def test_vault_key_rotation_execute_success(deterministic_keyring):
    """Verify execution mode rotates candidate row and commits new ciphertext with target version."""
    _, vault = deterministic_keyring
    cipher_acc_v1, _ = vault.encrypt("my_access_secret", key_version=1)
    cipher_ref_v1, _ = vault.encrypt("my_refresh_secret", key_version=1)

    mock_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc_v1,
        encrypted_refresh_token=cipher_ref_v1,
        key_version=1,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entry]
    mock_result.scalar_one_or_none.return_value = mock_entry
    mock_session.execute.return_value = mock_result

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.rotate_keys(target_version=2, execute=True)

    assert report.total_evaluated == 1
    assert report.rotated_count == 1
    assert report.failed_count == 0
    assert mock_entry.key_version == 2
    # Verify commit called
    mock_session.commit.assert_called_once()
    # Verify new ciphertext decrypts with target version 2
    assert vault.decrypt(mock_entry.encrypted_access_token, 2) == "my_access_secret"
    assert vault.decrypt(mock_entry.encrypted_refresh_token, 2) == "my_refresh_secret"


@pytest.mark.asyncio
async def test_vault_key_rotation_crash_rollback(deterministic_keyring):
    """Verify rollback is called if a database exception occurs during execution."""
    _, vault = deterministic_keyring
    cipher_acc_v1, _ = vault.encrypt("my_access_secret", key_version=1)

    mock_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc_v1,
        encrypted_refresh_token=None,
        key_version=1,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entry]
    mock_result.scalar_one_or_none.return_value = mock_entry
    mock_session.execute.return_value = mock_result
    # Force commit failure
    mock_session.commit.side_effect = RuntimeError("Simulated DB connection drop")

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.rotate_keys(target_version=2, execute=True)

    assert report.failed_count == 1
    assert report.entries[0].status == VaultEntryRotationStatus.ERROR
    mock_session.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_vault_key_rotation_resumability(deterministic_keyring):
    """Verify resumability: already-rotated rows are skipped idempotently, unrotated rows are converted."""
    _, vault = deterministic_keyring
    cipher_acc_v2, _ = vault.encrypt("secret_already_v2", key_version=2)
    cipher_acc_v1, _ = vault.encrypt("secret_still_v1", key_version=1)

    row1_already_current = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc_v2,
        encrypted_refresh_token=None,
        key_version=2,
    )
    row2_needs_rotation = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc_v1,
        encrypted_refresh_token=None,
        key_version=1,
    )

    mock_session = AsyncMock()
    mock_res_all = MagicMock()
    mock_res_all.scalars.return_value.all.return_value = [row1_already_current, row2_needs_rotation]

    mock_res_row2 = MagicMock()
    mock_res_row2.scalar_one_or_none.return_value = row2_needs_rotation

    mock_session.execute.side_effect = [mock_res_all, mock_res_row2]

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.rotate_keys(target_version=2, execute=True)

    assert report.total_evaluated == 2
    assert report.already_current_count == 1
    assert report.rotated_count == 1
    assert report.failed_count == 0
    assert row2_needs_rotation.key_version == 2
    # Only one commit for the unrotated row
    assert mock_session.commit.call_count == 1


@pytest.mark.asyncio
async def test_vault_key_verify_mode_pass(deterministic_keyring):
    """Verify strict read-only verify_keys passes when all rows are at target version and decrypt cleanly."""
    _, vault = deterministic_keyring
    cipher_acc_v2, _ = vault.encrypt("verified_secret", key_version=2)

    row = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc_v2,
        encrypted_refresh_token=None,
        key_version=2,
    )

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [row]
    mock_session.execute.return_value = mock_res

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.verify_keys(target_version=2)

    assert report.mode == "VERIFY"
    assert report.total_evaluated == 1
    assert report.failed_count == 0
    assert report.already_current_count == 1
    assert report.entries[0].status == VaultEntryRotationStatus.VERIFIED


@pytest.mark.asyncio
async def test_vault_key_verify_mode_fails_on_version_mismatch(deterministic_keyring):
    """Verify verify_keys detects version mismatch without DB mutation."""
    _, vault = deterministic_keyring
    cipher_acc_v1, _ = vault.encrypt("v1_secret", key_version=1)

    row = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token=cipher_acc_v1,
        encrypted_refresh_token=None,
        key_version=1,
    )

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [row]
    mock_session.execute.return_value = mock_res

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.verify_keys(target_version=2)

    assert report.failed_count == 1
    assert report.entries[0].status == VaultEntryRotationStatus.VERSION_MISMATCH


@pytest.mark.asyncio
async def test_vault_key_verify_mode_fails_on_corrupt_ciphertext(deterministic_keyring):
    """Verify verify_keys detects corrupted non-Fernet ciphertext."""
    _, vault = deterministic_keyring

    row = CredentialVault(
        id=uuid4(),
        platform_account_id=uuid4(),
        encrypted_access_token="invalid_corrupt_ciphertext_bytes",
        encrypted_refresh_token=None,
        key_version=2,
    )

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [row]
    mock_session.execute.return_value = mock_res

    class MockSessionFactory:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    service = VaultKeyRotationService(session_factory=MockSessionFactory, vault=vault)
    report = await service.verify_keys(target_version=2)

    assert report.failed_count == 1
    assert report.entries[0].status == VaultEntryRotationStatus.CORRUPT_CIPHERTEXT


def test_vault_multifernet_read_support():
    """Verify CredentialVaultService decrypts across multi-key keyring with or without key_version."""
    key1 = Fernet.generate_key().decode("utf-8")
    key2 = Fernet.generate_key().decode("utf-8")

    # Vault configured with both key 1 and key 2, active=2
    vault = CredentialVaultService(keyring={1: key1, 2: key2}, active_version=2)

    # Secret encrypted with key 1
    f1 = Fernet(key1.encode("utf-8"))
    c1 = f1.encrypt(b"token_encrypted_under_key1").decode("utf-8")

    # Secret encrypted with key 2
    f2 = Fernet(key2.encode("utf-8"))
    c2 = f2.encrypt(b"token_encrypted_under_key2").decode("utf-8")

    # Decrypt with exact key_version
    assert vault.decrypt(c1, 1) == "token_encrypted_under_key1"
    assert vault.decrypt(c2, 2) == "token_encrypted_under_key2"

    # MultiFernet fallback: decrypt without key_version (e.g. OAuth PKCE verifier)
    assert vault.decrypt(c1) == "token_encrypted_under_key1"
    assert vault.decrypt(c2) == "token_encrypted_under_key2"


def test_secret_redaction_guarantee():
    """Verify that report output and exception formatting never expose raw encryption keys or tokens."""
    vault_id = uuid4()
    account_id = uuid4()
    entry = VaultEntryResult(
        vault_id=vault_id,
        platform_account_id=account_id,
        current_key_version=1,
        target_key_version=2,
        access_token_decryptable=False,
        refresh_token_decryptable=None,
        eligible=False,
        status=VaultEntryRotationStatus.DECRYPTION_FAILED,
        error_message="InvalidToken during authentication",
    )
    report = VaultRotationReport(
        target_version=2,
        active_configured_version=2,
        mode="DRY_RUN",
        total_evaluated=1,
        failed_count=1,
        entries=[entry],
    )
    report_text = format_report(report)

    # Check for absence of secrets and key patterns
    import re
    assert not re.search(r"[A-Za-z0-9_-]{43}=", report_text)
    assert "ya29." not in report_text
    assert "1//" not in report_text
