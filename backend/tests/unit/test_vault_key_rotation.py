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
