"""Real PostgreSQL CLI rehearsal using generated keys in a read-only secret file."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import Channel, CredentialVault, PlatformAccount
from omega.infrastructure.vault import CredentialVaultService
from omega.maintenance.rotate_vault_keys import main_async, parse_args


@pytest.mark.asyncio
async def test_file_only_cli_rotation_and_retirement(db_session, tmp_path, monkeypatch, capsys):
    keys = {str(version): Fernet.generate_key().decode("ascii") for version in (1, 2, 3)}
    path = tmp_path / "omega_keyring.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    path.chmod(0o444)
    original = path.read_bytes()
    monkeypatch.delenv("OMEGA_KEYRING", raising=False)
    monkeypatch.delenv("OMEGA_SECRET_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("OMEGA_KEYRING_FILE", str(path))
    monkeypatch.setenv("OMEGA_CURRENT_KEY_VERSION", "3")
    vault = CredentialVaultService()
    assert vault.active_version == 3
    row_ids = []
    original_ciphertexts = []
    for version in (1, 2):
        channel_id, account_id, row_id = uuid4(), uuid4(), uuid4()
        db_session.add(
            Channel(
                id=channel_id,
                name="PRE1 synthetic",
                slug=f"pre1-{channel_id}",
                platform="YOUTUBE",
                primary_language="en",
                target_region="US",
                timezone="UTC",
                state="ACTIVE",
                dna={},
            )
        )
        db_session.add(
            PlatformAccount(
                id=account_id,
                channel_id=channel_id,
                platform="YOUTUBE",
                external_account_id=f"pre1-{account_id}",
                account_display_name="PRE1 synthetic",
                status="ACTIVE",
                scopes=[],
            )
        )
        access, _ = vault.encrypt(f"synthetic-access-v{version}", version)
        refresh, _ = vault.encrypt(f"synthetic-refresh-v{version}", version)
        db_session.add(
            CredentialVault(
                id=row_id,
                platform_account_id=account_id,
                encrypted_access_token=access,
                access_token_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                encrypted_refresh_token=refresh,
                key_version=version,
            )
        )
        row_ids.append(row_id)
        original_ciphertexts.append(access)
    await db_session.commit()

    assert await main_async(parse_args(["--target-version", "3", "--dry-run"])) == 0
    async with AsyncSessionLocal() as session:
        rows = (
            (await session.execute(select(CredentialVault).where(CredentialVault.id.in_(row_ids))))
            .scalars()
            .all()
        )
        assert sorted(row.key_version for row in rows) == [1, 2]
        assert sorted(row.encrypted_access_token for row in rows) == sorted(original_ciphertexts)
    assert await main_async(parse_args(["--target-version", "3", "--execute"])) == 0
    assert await main_async(parse_args(["--target-version", "3", "--verify"])) == 0
    assert path.read_bytes() == original

    only_v3 = tmp_path / "v3-only.json"
    only_v3.write_text(json.dumps({"3": keys["3"]}), encoding="utf-8")
    only_v3.chmod(0o444)
    monkeypatch.setenv("OMEGA_KEYRING_FILE", str(only_v3))
    assert await main_async(parse_args(["--target-version", "3", "--verify"])) == 0
    v3 = CredentialVaultService()
    async with AsyncSessionLocal() as session:
        rows = (
            (await session.execute(select(CredentialVault).where(CredentialVault.id.in_(row_ids))))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.key_version == 3
            assert v3.decrypt(row.encrypted_access_token, 3).startswith("synthetic-access-v")
            assert v3.decrypt(row.encrypted_refresh_token, 3).startswith("synthetic-refresh-v")
    cipher, version = v3.encrypt("synthetic-v3-write")
    assert version == 3 and v3.decrypt(cipher, 3) == "synthetic-v3-write"
    output = capsys.readouterr()
    for forbidden in [*keys.values(), "synthetic-access-v", "synthetic-refresh-v"]:
        assert forbidden not in output.out + output.err
