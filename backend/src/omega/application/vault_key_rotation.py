"""Canonical Application-level Credential Vault Key Rotation Service for OMEGA-011 Publisher.

Provides auditable, idempotent, transactional re-encryption of stored OAuth tokens
when transitioning across versioned encryption keys in the CredentialVault keyring.
Guarantees zero leakage of plaintext tokens or encryption keys in reports and logs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omega.infrastructure.database import AsyncWorkerSessionLocal
from omega.infrastructure.models import CredentialVault
from omega.infrastructure.vault import (
    CredentialVaultService,
    VaultConfigurationError,
    VaultDecryptionError,
    get_credential_vault,
)
from omega.logging import get_logger

logger = get_logger(service="omega-vault-key-rotation")


class VaultEntryRotationStatus(StrEnum):
    """Classification of key rotation result for an individual CredentialVault entry."""

    ROTATED = "ROTATED"
    ELIGIBLE = "ELIGIBLE"
    ALREADY_CURRENT = "ALREADY_CURRENT"
    DECRYPTION_FAILED = "DECRYPTION_FAILED"
    TARGET_KEY_UNAVAILABLE = "TARGET_KEY_UNAVAILABLE"
    ROTATION_VERIFICATION_FAILED = "ROTATION_VERIFICATION_FAILED"
    ERROR = "ERROR"


@dataclass
class VaultEntryResult:
    """Sanitized outcome for a single CredentialVault entry evaluation or rotation."""

    vault_id: UUID
    platform_account_id: UUID
    current_key_version: int
    target_key_version: int
    access_token_decryptable: bool
    refresh_token_decryptable: bool | None
    eligible: bool
    status: VaultEntryRotationStatus
    error_message: str | None = None


@dataclass
class VaultRotationReport:
    """Consolidated report of a vault key rotation dry-run or execution."""

    target_version: int
    active_configured_version: int
    mode: Literal["DRY_RUN", "EXECUTE"]
    filter_platform_account_id: UUID | None = None
    filter_current_version: int | None = None
    total_evaluated: int = 0
    eligible_count: int = 0
    rotated_count: int = 0
    already_current_count: int = 0
    failed_count: int = 0
    entries: list[VaultEntryResult] = field(default_factory=list)


class VaultKeyRotationService:
    """Canonical service for inspecting, planning, and executing vault key rotations."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | Callable[[], AsyncSession] | None = None,
        vault: CredentialVaultService | None = None,
    ) -> None:
        self.session_factory = session_factory or AsyncWorkerSessionLocal
        self.vault = vault or get_credential_vault()

    async def rotate_keys(
        self,
        target_version: int,
        execute: bool = False,
        platform_account_id: UUID | None = None,
        current_version: int | None = None,
    ) -> VaultRotationReport:
        """Evaluate (dry-run) or execute transactional key rotation for candidate vault entries.

        Args:
            target_version: The destination encryption key version (must exist in keyring).
            execute: If True, commits rotated ciphertexts to DB. If False, runs read-only audit.
            platform_account_id: Optional filter targeting a single platform account.
            current_version: Optional filter targeting only entries with a specific key_version.

        Returns:
            A VaultRotationReport summarizing the evaluation and outcomes.
        """
        if target_version <= 0:
            raise ValueError(f"Target key version must be > 0, got {target_version}")

        # In execution mode, target_version must match the currently configured active version
        if execute and target_version != self.vault.active_version:
            raise ValueError(
                f"Execution mode refused: target key version ({target_version}) does not match "
                f"the configured active vault version ({self.vault.active_version})."
            )

        # Verify that the target key exists in the vault keyring
        target_key_available = target_version in self.vault._keyring

        report = VaultRotationReport(
            target_version=target_version,
            active_configured_version=self.vault.active_version,
            mode="EXECUTE" if execute else "DRY_RUN",
            filter_platform_account_id=platform_account_id,
            filter_current_version=current_version,
        )

        async with self.session_factory() as session:
            # Query candidate rows
            stmt = select(CredentialVault).order_by(CredentialVault.updated_at.asc())
            if platform_account_id:
                stmt = stmt.where(CredentialVault.platform_account_id == platform_account_id)
            if current_version:
                stmt = stmt.where(CredentialVault.key_version == current_version)

            res = await session.execute(stmt)
            candidates = res.scalars().all()
            report.total_evaluated = len(candidates)

            for candidate in candidates:
                result = await self._process_entry(
                    session=session,
                    candidate_id=candidate.id,
                    candidate_account_id=candidate.platform_account_id,
                    initial_version=candidate.key_version,
                    initial_access_cipher=candidate.encrypted_access_token,
                    initial_refresh_cipher=candidate.encrypted_refresh_token,
                    target_version=target_version,
                    target_key_available=target_key_available,
                    execute=execute,
                )
                report.entries.append(result)

                if result.status == VaultEntryRotationStatus.ROTATED:
                    report.rotated_count += 1
                elif result.status == VaultEntryRotationStatus.ELIGIBLE:
                    report.eligible_count += 1
                elif result.status == VaultEntryRotationStatus.ALREADY_CURRENT:
                    report.already_current_count += 1
                else:
                    report.failed_count += 1

        logger.info(
            "Vault key rotation completed",
            extra={
                "mode": report.mode,
                "target_version": report.target_version,
                "total_evaluated": report.total_evaluated,
                "rotated": report.rotated_count,
                "eligible": report.eligible_count,
                "already_current": report.already_current_count,
                "failed": report.failed_count,
            },
        )
        return report

    async def _process_entry(
        self,
        session: AsyncSession,
        candidate_id: UUID,
        candidate_account_id: UUID,
        initial_version: int,
        initial_access_cipher: str,
        initial_refresh_cipher: str | None,
        target_version: int,
        target_key_available: bool,
        execute: bool,
    ) -> VaultEntryResult:
        """Process an individual vault entry in dry-run or execute mode."""
        # 1. Check idempotency: already current?
        if initial_version == target_version:
            return VaultEntryResult(
                vault_id=candidate_id,
                platform_account_id=candidate_account_id,
                current_key_version=initial_version,
                target_key_version=target_version,
                access_token_decryptable=True,
                refresh_token_decryptable=True if initial_refresh_cipher else None,
                eligible=False,
                status=VaultEntryRotationStatus.ALREADY_CURRENT,
                error_message=None,
            )

        # 2. Check target key availability
        if not target_key_available:
            return VaultEntryResult(
                vault_id=candidate_id,
                platform_account_id=candidate_account_id,
                current_key_version=initial_version,
                target_key_version=target_version,
                access_token_decryptable=False,
                refresh_token_decryptable=False if initial_refresh_cipher else None,
                eligible=False,
                status=VaultEntryRotationStatus.TARGET_KEY_UNAVAILABLE,
                error_message=f"Target key version {target_version} is not configured in vault keyring",
            )

        # 3. Test decryptability of current ciphertexts
        refresh_decryptable: bool | None = None
        plaintext_access: str = ""
        plaintext_refresh: str = ""

        try:
            plaintext_access = self.vault.decrypt(initial_access_cipher, initial_version)
        except (VaultDecryptionError, VaultConfigurationError, Exception) as exc:
            return VaultEntryResult(
                vault_id=candidate_id,
                platform_account_id=candidate_account_id,
                current_key_version=initial_version,
                target_key_version=target_version,
                access_token_decryptable=False,
                refresh_token_decryptable=None,
                eligible=False,
                status=VaultEntryRotationStatus.DECRYPTION_FAILED,
                error_message=f"Access token decryption failed: {type(exc).__name__}",
            )

        if initial_refresh_cipher:
            try:
                plaintext_refresh = self.vault.decrypt(initial_refresh_cipher, initial_version)
                refresh_decryptable = True
            except (VaultDecryptionError, VaultConfigurationError, Exception) as exc:
                return VaultEntryResult(
                    vault_id=candidate_id,
                    platform_account_id=candidate_account_id,
                    current_key_version=initial_version,
                    target_key_version=target_version,
                    access_token_decryptable=True,
                    refresh_token_decryptable=False,
                    eligible=False,
                    status=VaultEntryRotationStatus.DECRYPTION_FAILED,
                    error_message=f"Refresh token decryption failed: {type(exc).__name__}",
                )

        # 4. Dry-run mode: verify in-memory rotation without DB mutation
        if not execute:
            try:
                # In-memory test rotation
                new_acc_cipher, v_acc = self.vault.rotate_ciphertext(
                    initial_access_cipher, initial_version
                )
                if self.vault.decrypt(new_acc_cipher, target_version) != plaintext_access:
                    raise ValueError("Access token rotated ciphertext roundtrip verification mismatch")

                if initial_refresh_cipher:
                    new_ref_cipher, v_ref = self.vault.rotate_ciphertext(
                        initial_refresh_cipher, initial_version
                    )
                    if self.vault.decrypt(new_ref_cipher, target_version) != plaintext_refresh:
                        raise ValueError("Refresh token rotated ciphertext roundtrip verification mismatch")

                return VaultEntryResult(
                    vault_id=candidate_id,
                    platform_account_id=candidate_account_id,
                    current_key_version=initial_version,
                    target_key_version=target_version,
                    access_token_decryptable=True,
                    refresh_token_decryptable=refresh_decryptable,
                    eligible=True,
                    status=VaultEntryRotationStatus.ELIGIBLE,
                    error_message=None,
                )
            except Exception as exc:
                return VaultEntryResult(
                    vault_id=candidate_id,
                    platform_account_id=candidate_account_id,
                    current_key_version=initial_version,
                    target_key_version=target_version,
                    access_token_decryptable=True,
                    refresh_token_decryptable=refresh_decryptable,
                    eligible=False,
                    status=VaultEntryRotationStatus.ROTATION_VERIFICATION_FAILED,
                    error_message=f"In-memory verification failed: {type(exc).__name__}",
                )

        # 5. Execution mode: transactional, row-locked re-encryption
        try:
            # Re-fetch authoritative row with row-level lock
            lock_stmt = select(CredentialVault).where(CredentialVault.id == candidate_id).with_for_update()
            lock_res = await session.execute(lock_stmt)
            locked_row = lock_res.scalar_one_or_none()

            if not locked_row:
                return VaultEntryResult(
                    vault_id=candidate_id,
                    platform_account_id=candidate_account_id,
                    current_key_version=initial_version,
                    target_key_version=target_version,
                    access_token_decryptable=True,
                    refresh_token_decryptable=refresh_decryptable,
                    eligible=False,
                    status=VaultEntryRotationStatus.ERROR,
                    error_message="Row not found during locked re-read",
                )

            # Idempotency double-check under lock
            if locked_row.key_version == target_version:
                return VaultEntryResult(
                    vault_id=candidate_id,
                    platform_account_id=candidate_account_id,
                    current_key_version=locked_row.key_version,
                    target_key_version=target_version,
                    access_token_decryptable=True,
                    refresh_token_decryptable=refresh_decryptable,
                    eligible=False,
                    status=VaultEntryRotationStatus.ALREADY_CURRENT,
                    error_message=None,
                )

            # Perform rotation via vault service
            new_acc_cipher, v_acc = self.vault.rotate_ciphertext(
                locked_row.encrypted_access_token, locked_row.key_version
            )
            # Verify new access ciphertext roundtrip
            if self.vault.decrypt(new_acc_cipher, target_version) != plaintext_access:
                raise ValueError("Access token ciphertext verification failed before commit")

            new_ref_cipher = None
            if locked_row.encrypted_refresh_token:
                new_ref_cipher, v_ref = self.vault.rotate_ciphertext(
                    locked_row.encrypted_refresh_token, locked_row.key_version
                )
                # Verify new refresh ciphertext roundtrip
                if self.vault.decrypt(new_ref_cipher, target_version) != plaintext_refresh:
                    raise ValueError("Refresh token ciphertext verification failed before commit")

            # Update row values
            locked_row.encrypted_access_token = new_acc_cipher
            if new_ref_cipher is not None:
                locked_row.encrypted_refresh_token = new_ref_cipher
            locked_row.key_version = target_version

            await session.commit()

            return VaultEntryResult(
                vault_id=candidate_id,
                platform_account_id=candidate_account_id,
                current_key_version=initial_version,
                target_key_version=target_version,
                access_token_decryptable=True,
                refresh_token_decryptable=refresh_decryptable,
                eligible=True,
                status=VaultEntryRotationStatus.ROTATED,
                error_message=None,
            )
        except Exception as exc:
            await session.rollback()
            return VaultEntryResult(
                vault_id=candidate_id,
                platform_account_id=candidate_account_id,
                current_key_version=initial_version,
                target_key_version=target_version,
                access_token_decryptable=True,
                refresh_token_decryptable=refresh_decryptable,
                eligible=False,
                status=VaultEntryRotationStatus.ERROR,
                error_message=f"Execution rollback: {type(exc).__name__}",
            )
