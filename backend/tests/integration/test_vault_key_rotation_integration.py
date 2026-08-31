"""Integration tests for VaultKeyRotationService with real PostgreSQL sessions.

Verifies transactional row-locking, zero mutation on dry-run, atomic commit on execute,
safe rollback on corrupted ciphertexts, idempotency, and targeted filtering by platform_account_id.
Never uses real secrets; generates deterministic test Fernet keys.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from omega.application.vault_key_rotation import (
    VaultEntryRotationStatus,
    VaultKeyRotationService,
)
from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import PlatformAccountStatus
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import Channel, CredentialVault, PlatformAccount
from omega.infrastructure.vault import CredentialVaultService


@pytest.fixture
def dual_test_keyring() -> tuple[dict[int, str], CredentialVaultService]:
    """Provide a 2-key test keyring for v1 -> v2 migration."""
    key1 = Fernet.generate_key().decode("utf-8")
    key2 = Fernet.generate_key().decode("utf-8")
    keyring = {1: key1, 2: key2}
    vault = CredentialVaultService(keyring=keyring, active_version=2)
    return keyring, vault


async def create_test_channel_and_account(db_session) -> PlatformAccount:
    """Helper to create a test channel and platform account for vault entries."""
    chan_id = uuid4()
    channel = Channel(
        id=chan_id,
        name=f"Test Channel {chan_id.hex[:6]}",
        slug=f"test-channel-{chan_id.hex[:6]}",
        platform=Platform.YOUTUBE.value,
        primary_language="en",
        target_region="US",
        timezone="UTC",
        state=ChannelState.ACTIVE.value,
        dna={},
    )
    db_session.add(channel)

    account_id = uuid4()
    account = PlatformAccount(
        id=account_id,
        channel_id=chan_id,
        platform=Platform.YOUTUBE.value,
        external_account_id=f"UC_{account_id.hex[:8]}",
        account_display_name=f"Account {account_id.hex[:6]}",
        status=PlatformAccountStatus.ACTIVE.value,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    db_session.add(account)
    await db_session.commit()
    return account


@pytest.mark.asyncio
async def test_dry_run_causes_zero_db_mutation(db_session, dual_test_keyring):
    """Verify dry-run mode identifies eligible candidate but leaves DB rows completely unchanged."""
    _, vault = dual_test_keyring
    account = await create_test_channel_and_account(db_session)

    plain_access = "secret_access_token_123"
    plain_refresh = "secret_refresh_token_456"
    cipher_acc_v1, _ = vault.encrypt(plain_access, key_version=1)
    cipher_ref_v1, _ = vault.encrypt(plain_refresh, key_version=1)

    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=cipher_acc_v1,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        encrypted_refresh_token=cipher_ref_v1,
        token_type="Bearer",
        key_version=1,
    )
    db_session.add(vault_entry)
    await db_session.commit()

    service = VaultKeyRotationService(session_factory=AsyncSessionLocal, vault=vault)
    report = await service.rotate_keys(
        target_version=2,
        execute=False,
        platform_account_id=account.id,
    )

    assert report.total_evaluated == 1
    assert report.eligible_count == 1
    assert report.rotated_count == 0
    assert report.entries[0].status == VaultEntryRotationStatus.ELIGIBLE

    # Query DB to verify zero mutation
    async with AsyncSessionLocal() as session:
        refetched = await session.get(CredentialVault, vault_entry.id)
        assert refetched is not None
        assert refetched.key_version == 1
        assert refetched.encrypted_access_token == cipher_acc_v1
        assert refetched.encrypted_refresh_token == cipher_ref_v1


@pytest.mark.asyncio
async def test_execution_mode_successful_rotation(db_session, dual_test_keyring):
    """Verify execution mode rotates ciphertext from v1 to v2, preserves plaintext, and updates DB."""
    keyring, vault = dual_test_keyring
    account = await create_test_channel_and_account(db_session)

    plain_access = "live_oauth_access_token_abc"
    plain_refresh = "live_oauth_refresh_token_xyz"
    cipher_acc_v1, _ = vault.encrypt(plain_access, key_version=1)
    cipher_ref_v1, _ = vault.encrypt(plain_refresh, key_version=1)

    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=cipher_acc_v1,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        encrypted_refresh_token=cipher_ref_v1,
        token_type="Bearer",
        key_version=1,
    )
    db_session.add(vault_entry)
    await db_session.commit()

    service = VaultKeyRotationService(session_factory=AsyncSessionLocal, vault=vault)
    report = await service.rotate_keys(
        target_version=2,
        execute=True,
        platform_account_id=account.id,
    )

    assert report.total_evaluated == 1
    assert report.rotated_count == 1
    assert report.failed_count == 0
    assert report.entries[0].status == VaultEntryRotationStatus.ROTATED

    # Verify authoritative row in DB
    async with AsyncSessionLocal() as session:
        updated = await session.get(CredentialVault, vault_entry.id)
        assert updated is not None
        assert updated.key_version == 2
        assert updated.encrypted_access_token != cipher_acc_v1
        assert updated.encrypted_refresh_token != cipher_ref_v1

        # Decrypt with key 2 and verify identical plaintext
        decrypted_acc = vault.decrypt(updated.encrypted_access_token, 2)
        decrypted_ref = vault.decrypt(updated.encrypted_refresh_token, 2)
        assert decrypted_acc == plain_access
        assert decrypted_ref == plain_refresh


@pytest.mark.asyncio
async def test_idempotent_reexecution_is_noop(db_session, dual_test_keyring):
    """Repeated execution on an already-rotated row must classify as ALREADY_CURRENT and do no writes."""
    _, vault = dual_test_keyring
    account = await create_test_channel_and_account(db_session)

    plain_access = "idempotent_token_123"
    cipher_acc_v2, _ = vault.encrypt(plain_access, key_version=2)

    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=cipher_acc_v2,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        encrypted_refresh_token=cipher_acc_v2,
        token_type="Bearer",
        key_version=2,
    )
    db_session.add(vault_entry)
    await db_session.commit()

    service = VaultKeyRotationService(session_factory=AsyncSessionLocal, vault=vault)
    report = await service.rotate_keys(
        target_version=2,
        execute=True,
        platform_account_id=account.id,
    )

    assert report.total_evaluated == 1
    assert report.already_current_count == 1
    assert report.rotated_count == 0
    assert report.entries[0].status == VaultEntryRotationStatus.ALREADY_CURRENT


@pytest.mark.asyncio
async def test_corrupted_access_token_rolls_back_safely(db_session, dual_test_keyring):
    """If access token is corrupted, execution fails safely and transaction rolls back."""
    _, vault = dual_test_keyring
    account = await create_test_channel_and_account(db_session)

    corrupted_cipher = "gAAAAABcorrupted_not_valid_fernet"
    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=corrupted_cipher,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        encrypted_refresh_token=corrupted_cipher,
        token_type="Bearer",
        key_version=1,
    )
    db_session.add(vault_entry)
    await db_session.commit()

    service = VaultKeyRotationService(session_factory=AsyncSessionLocal, vault=vault)
    report = await service.rotate_keys(
        target_version=2,
        execute=True,
        platform_account_id=account.id,
    )

    assert report.failed_count == 1
    assert report.rotated_count == 0
    assert report.entries[0].status == VaultEntryRotationStatus.DECRYPTION_FAILED

    # Row in DB remains intact
    async with AsyncSessionLocal() as session:
        refetched = await session.get(CredentialVault, vault_entry.id)
        assert refetched is not None
        assert refetched.key_version == 1
        assert refetched.encrypted_access_token == corrupted_cipher


@pytest.mark.asyncio
async def test_corrupted_refresh_token_rolls_back_safely(db_session, dual_test_keyring):
    """If access token is valid but refresh token is corrupted, rollback occurs without partial update."""
    _, vault = dual_test_keyring
    account = await create_test_channel_and_account(db_session)

    plain_access = "valid_access_token"
    cipher_acc, _ = vault.encrypt(plain_access, key_version=1)
    corrupted_refresh = "gAAAAABcorrupted_refresh"

    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=cipher_acc,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        encrypted_refresh_token=corrupted_refresh,
        token_type="Bearer",
        key_version=1,
    )
    db_session.add(vault_entry)
    await db_session.commit()

    service = VaultKeyRotationService(session_factory=AsyncSessionLocal, vault=vault)
    report = await service.rotate_keys(
        target_version=2,
        execute=True,
        platform_account_id=account.id,
    )

    assert report.failed_count == 1
    assert report.rotated_count == 0
    assert report.entries[0].status == VaultEntryRotationStatus.DECRYPTION_FAILED

    # Verify DB was NOT partially updated
    async with AsyncSessionLocal() as session:
        refetched = await session.get(CredentialVault, vault_entry.id)
        assert refetched is not None
        assert refetched.key_version == 1
        assert refetched.encrypted_access_token == cipher_acc


@pytest.mark.asyncio
async def test_platform_account_id_filtering_rotates_only_targeted_row(db_session, dual_test_keyring):
    """Verify targeting a specific platform_account_id rotates ONLY that row and leaves other rows untouched."""
    _, vault = dual_test_keyring
    account_target = await create_test_channel_and_account(db_session)
    account_other = await create_test_channel_and_account(db_session)

    cipher_target, _ = vault.encrypt("target_token", key_version=1)
    cipher_other, _ = vault.encrypt("other_token", key_version=1)

    entry_target = CredentialVault(
        id=uuid4(),
        platform_account_id=account_target.id,
        encrypted_access_token=cipher_target,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        encrypted_refresh_token=cipher_target,
        token_type="Bearer",
        key_version=1,
    )
    entry_other = CredentialVault(
        id=uuid4(),
        platform_account_id=account_other.id,
        encrypted_access_token=cipher_other,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        encrypted_refresh_token=cipher_other,
        token_type="Bearer",
        key_version=1,
    )
    db_session.add(entry_target)
    db_session.add(entry_other)
    await db_session.commit()

    service = VaultKeyRotationService(session_factory=AsyncSessionLocal, vault=vault)
    report = await service.rotate_keys(
        target_version=2,
        execute=True,
        platform_account_id=account_target.id,
    )

    assert report.total_evaluated == 1
    assert report.rotated_count == 1
    assert report.entries[0].vault_id == entry_target.id

    # Verify DB: target is updated to v2, other remains v1
    async with AsyncSessionLocal() as session:
        check_target = await session.get(CredentialVault, entry_target.id)
        check_other = await session.get(CredentialVault, entry_other.id)

        assert check_target is not None and check_target.key_version == 2
        assert check_other is not None and check_other.key_version == 1
