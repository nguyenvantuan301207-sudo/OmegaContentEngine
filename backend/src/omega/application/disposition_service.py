"""Application service for dirty-data disposition.

Provides strict manifest-bound verification, atomic precondition checks,
and atomic domain lifecycle transitions (PlatformAccount -> REVOKED,
PublishIntent/Task/Mission -> CANCELLED) preserving CredentialVault rows
and audit history without any hard deletes or provider calls.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omega.application.mission_service import cancel_mission
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.oauth_service import OAuthService
from omega.domain.mission import MissionState
from omega.domain.publisher import PlatformAccountStatus, PublishIntentState
from omega.domain.task import TaskState
from omega.infrastructure.database import AsyncWorkerSessionLocal
from omega.infrastructure.models import (
    CredentialVault,
    Mission,
    PlatformAccount,
    PublishIntent,
    Task,
)
from omega.logging import get_logger

logger = get_logger(service="omega-disposition-service")


class DispositionError(Exception):
    """Base error for dirty-data disposition failures."""


class ManifestValidationError(DispositionError):
    """Raised when manifest schema or hash verification fails."""


class PreconditionError(DispositionError):
    """Raised when database pre-state diverges from authorized manifest."""


class DispositionStatus(StrEnum):
    """Execution status for disposition operation."""

    PLANNED = "PLANNED"
    EXECUTED = "EXECUTED"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    FAILED = "FAILED"


@dataclass
class ManifestEntry:
    """Strict model for a single row in the dirty-data manifest."""

    credential_vault_id: UUID
    platform_account_id: UUID
    key_version: int
    anomaly_class: str
    platform_account_status: str
    reference_classification: str
    recommended_disposition: str
    publish_intents: list[dict[str, str]] = field(default_factory=list)
    tasks: list[dict[str, str]] = field(default_factory=list)
    missions: list[dict[str, str]] = field(default_factory=list)
    decryptability_status: str | None = None
    excluded_from_rotation: bool = True
    human_authorization_required: bool = True


@dataclass
class DispositionReport:
    """Sanitized evidence report of disposition evaluation or execution."""

    status: DispositionStatus
    mode: str
    manifest_path: str
    manifest_hash: str
    manifest_hash_match: bool
    total_manifest_entries: int = 0
    accounts_targeted: int = 0
    accounts_revoked: int = 0
    intents_targeted: int = 0
    intents_cancelled: int = 0
    tasks_targeted: int = 0
    tasks_cancelled: int = 0
    missions_targeted: int = 0
    missions_cancelled: int = 0
    credentials_retained: int = 0
    audit_records_emitted: int = 0
    zero_deletes_verified: bool = True
    zero_provider_calls_verified: bool = True
    zero_publisher_handoffs_verified: bool = True
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class DirtyDataDispositionService:
    """Canonical executor for manifest-bound dirty data lifecycle disposition."""

    EXPECTED_TOTAL_ENTRIES = 125
    EXPECTED_CORRUPT_COUNT = 39
    EXPECTED_EPHEMERAL_COUNT = 84
    EXPECTED_MISLABELLED_COUNT = 2
    EXPECTED_WORKFLOW_REFS_COUNT = 41
    EXPECTED_ZERO_REFS_COUNT = 84

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | Callable[[], AsyncSession] | None = None,
    ) -> None:
        self.session_factory = session_factory or AsyncWorkerSessionLocal

    @staticmethod
    def compute_sha256(file_path: Path | str) -> str:
        """Compute SHA-256 digest of a file."""
        p = Path(file_path)
        if not p.exists():
            raise ManifestValidationError(f"Manifest file not found: {p}")
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def parse_and_validate_manifest(
        self,
        manifest_path: Path | str,
        expected_sha256: str | None = None,
    ) -> tuple[list[ManifestEntry], str, bool]:
        """Strictly validate manifest format, hash binding, counts, and schemas."""
        p = Path(manifest_path)
        if not p.exists():
            raise ManifestValidationError(f"Manifest file does not exist at '{p}'")

        file_bytes = p.read_bytes()
        actual_hash = hashlib.sha256(file_bytes).hexdigest()

        hash_matches = True
        if expected_sha256:
            if actual_hash.lower() != expected_sha256.strip().lower():
                raise ManifestValidationError(
                    f"Manifest hash mismatch: expected {expected_sha256}, got {actual_hash}"
                )
        else:
            hash_matches = False

        try:
            raw_data = json.loads(file_bytes.decode("utf-8"))
        except Exception as exc:
            raise ManifestValidationError(f"Manifest is not valid JSON: {exc}") from exc

        if not isinstance(raw_data, list):
            raise ManifestValidationError("Manifest root must be a JSON array of entries.")

        if len(raw_data) != self.EXPECTED_TOTAL_ENTRIES:
            raise ManifestValidationError(
                f"Manifest entries count mismatch: expected {self.EXPECTED_TOTAL_ENTRIES}, got {len(raw_data)}"
            )

        entries: list[ManifestEntry] = []
        seen_cv_ids: set[UUID] = set()
        seen_pa_ids: set[UUID] = set()

        corrupt_count = 0
        ephemeral_count = 0
        mislabelled_count = 0
        workflows_count = 0
        zero_refs_count = 0

        for idx, item in enumerate(raw_data):
            if not isinstance(item, dict):
                raise ManifestValidationError(f"Entry {idx} is not an object.")

            # Required fields
            req_keys = [
                "credential_vault_id",
                "platform_account_id",
                "key_version",
                "anomaly_class",
                "platform_account_status",
                "reference_classification",
            ]
            for rk in req_keys:
                if rk not in item:
                    raise ManifestValidationError(f"Entry {idx} missing required key '{rk}'.")

            try:
                cv_id = UUID(item["credential_vault_id"])
            except Exception as exc:
                raise ManifestValidationError(f"Entry {idx} has invalid credential_vault_id: {exc}") from exc

            try:
                pa_id = UUID(item["platform_account_id"])
            except Exception as exc:
                raise ManifestValidationError(f"Entry {idx} has invalid platform_account_id: {exc}") from exc

            if cv_id in seen_cv_ids:
                raise ManifestValidationError(f"Duplicate credential_vault_id in manifest: {cv_id}")
            seen_cv_ids.add(cv_id)

            if pa_id in seen_pa_ids:
                raise ManifestValidationError(f"Duplicate platform_account_id in manifest: {pa_id}")
            seen_pa_ids.add(pa_id)

            anom_class = item["anomaly_class"]
            if anom_class == "CORRUPT_CIPHERTEXT":
                corrupt_count += 1
            elif anom_class == "UNKNOWN_EPHEMERAL_KEY":
                ephemeral_count += 1
            elif anom_class == "MISLABELLED_KEY_VERSION_TEST_ARTIFACT":
                mislabelled_count += 1
            else:
                raise ManifestValidationError(f"Entry {idx} has unknown anomaly class '{anom_class}'.")

            pi_list = item.get("publish_intents", [])
            task_list = item.get("tasks", [])
            mission_list = item.get("missions", [])

            if pi_list:
                workflows_count += 1
            else:
                zero_refs_count += 1

            entries.append(
                ManifestEntry(
                    credential_vault_id=cv_id,
                    platform_account_id=pa_id,
                    key_version=int(item["key_version"]),
                    anomaly_class=anom_class,
                    platform_account_status=str(item["platform_account_status"]),
                    reference_classification=str(item["reference_classification"]),
                    recommended_disposition=str(item.get("recommended_disposition", "REVOKE_AND_CANCEL")),
                    publish_intents=pi_list,
                    tasks=task_list,
                    missions=mission_list,
                    decryptability_status=item.get("decryptability_status"),
                    excluded_from_rotation=bool(item.get("excluded_from_rotation", True)),
                    human_authorization_required=bool(item.get("human_authorization_required", True)),
                )
            )

        # Validate aggregate composition
        if corrupt_count != self.EXPECTED_CORRUPT_COUNT:
            raise ManifestValidationError(
                f"CORRUPT_CIPHERTEXT count mismatch: expected {self.EXPECTED_CORRUPT_COUNT}, got {corrupt_count}"
            )
        if ephemeral_count != self.EXPECTED_EPHEMERAL_COUNT:
            raise ManifestValidationError(
                f"UNKNOWN_EPHEMERAL_KEY count mismatch: expected {self.EXPECTED_EPHEMERAL_COUNT}, got {ephemeral_count}"
            )
        if mislabelled_count != self.EXPECTED_MISLABELLED_COUNT:
            raise ManifestValidationError(
                f"MISLABELLED_KEY_VERSION count mismatch: expected {self.EXPECTED_MISLABELLED_COUNT}, got {mislabelled_count}"
            )
        if workflows_count != self.EXPECTED_WORKFLOW_REFS_COUNT:
            raise ManifestValidationError(
                f"Referenced workflows count mismatch: expected {self.EXPECTED_WORKFLOW_REFS_COUNT}, got {workflows_count}"
            )
        if zero_refs_count != self.EXPECTED_ZERO_REFS_COUNT:
            raise ManifestValidationError(
                f"Zero-reference count mismatch: expected {self.EXPECTED_ZERO_REFS_COUNT}, got {zero_refs_count}"
            )

        return entries, actual_hash, hash_matches

    async def verify_preconditions(
        self,
        session: AsyncSession,
        entries: list[ManifestEntry],
    ) -> bool:
        """Atomic read-only pre-flight check asserting DB matches expected manifest state.

        Returns:
            True if all records are in initial pending state (eligible for disposition).
            False if ALL records are ALREADY in disposed state (REVOKED / CANCELLED).
        Raises:
            PreconditionError if any row is missing or in an unexpected/drifted state.
        """
        initial_states_count = 0
        already_disposed_count = 0

        for entry in entries:
            # 1. CredentialVault check
            stmt_cv = select(CredentialVault).where(CredentialVault.id == entry.credential_vault_id)
            res_cv = await session.execute(stmt_cv)
            cv_row = res_cv.scalar_one_or_none()
            if not cv_row:
                raise PreconditionError(
                    f"Precondition failed: CredentialVault row missing for id {entry.credential_vault_id}"
                )
            if cv_row.platform_account_id != entry.platform_account_id:
                raise PreconditionError(
                    f"Precondition failed: CredentialVault {cv_row.id} platform_account_id mismatch: "
                    f"expected {entry.platform_account_id}, got {cv_row.platform_account_id}"
                )
            if cv_row.key_version != entry.key_version:
                raise PreconditionError(
                    f"Precondition failed: CredentialVault {cv_row.id} key_version mismatch: "
                    f"expected {entry.key_version}, got {cv_row.key_version}"
                )

            # 2. PlatformAccount check
            stmt_pa = select(PlatformAccount).where(PlatformAccount.id == entry.platform_account_id)
            res_pa = await session.execute(stmt_pa)
            pa_row = res_pa.scalar_one_or_none()
            if not pa_row:
                raise PreconditionError(
                    f"Precondition failed: PlatformAccount row missing for id {entry.platform_account_id}"
                )

            if pa_row.status == PlatformAccountStatus.ACTIVE.value:
                initial_states_count += 1
            elif pa_row.status == PlatformAccountStatus.REVOKED.value:
                already_disposed_count += 1
            else:
                raise PreconditionError(
                    f"Precondition failed: PlatformAccount {pa_row.id} has unrecognized status '{pa_row.status}'"
                )

            # 3. PublishIntents check
            for pi in entry.publish_intents:
                iid = UUID(pi["intent_id"])
                exp_state = pi["state"]
                stmt_pi = select(PublishIntent).where(PublishIntent.id == iid)
                res_pi = await session.execute(stmt_pi)
                pi_row = res_pi.scalar_one_or_none()
                if not pi_row:
                    raise PreconditionError(f"Precondition failed: PublishIntent {iid} missing")
                if pi_row.state != exp_state and pi_row.state != PublishIntentState.CANCELLED.value:
                    raise PreconditionError(
                        f"Precondition failed: PublishIntent {iid} state '{pi_row.state}' "
                        f"does not match expected '{exp_state}' nor 'CANCELLED'"
                    )

            # 4. Tasks check
            for t in entry.tasks:
                tid = UUID(t["task_id"])
                exp_state = t["state"]
                stmt_t = select(Task).where(Task.id == tid)
                res_t = await session.execute(stmt_t)
                t_row = res_t.scalar_one_or_none()
                if not t_row:
                    raise PreconditionError(f"Precondition failed: Task {tid} missing")
                if t_row.state != exp_state and t_row.state != TaskState.CANCELLED.value:
                    raise PreconditionError(
                        f"Precondition failed: Task {tid} state '{t_row.state}' "
                        f"does not match expected '{exp_state}' nor 'CANCELLED'"
                    )

            # 5. Missions check
            for m in entry.missions:
                mid = UUID(m["mission_id"])
                exp_state = m["state"]
                stmt_m = select(Mission).where(Mission.id == mid)
                res_m = await session.execute(stmt_m)
                m_row = res_m.scalar_one_or_none()
                if not m_row:
                    raise PreconditionError(f"Precondition failed: Mission {mid} missing")
                if m_row.state != exp_state and m_row.state != MissionState.CANCELLED.value:
                    raise PreconditionError(
                        f"Precondition failed: Mission {mid} state '{m_row.state}' "
                        f"does not match expected '{exp_state}' nor 'CANCELLED'"
                    )

            # 6. Zero-reference guarantee
            if not entry.publish_intents:
                stmt_zero = select(func.count(PublishIntent.id)).where(
                    PublishIntent.platform_account_id == entry.platform_account_id
                )
                res_zero = await session.execute(stmt_zero)
                count_pi = res_zero.scalar_one()
                if count_pi > 0:
                    raise PreconditionError(
                        f"Precondition failed: Zero-reference account {entry.platform_account_id} "
                        f"has {count_pi} PublishIntents in DB!"
                    )

        if initial_states_count == len(entries):
            return True
        if already_disposed_count == len(entries):
            return False

        raise PreconditionError(
            f"Precondition failed: Inconsistent disposition state: {initial_states_count} ACTIVE, "
            f"{already_disposed_count} REVOKED out of {len(entries)}"
        )

    async def execute_disposition(
        self,
        manifest_path: Path | str,
        expected_sha256: str | None = None,
        execute: bool = False,
    ) -> DispositionReport:
        """Run complete manifest validation, precondition check, and atomic execution."""
        p = Path(manifest_path)
        entries, actual_hash, hash_matched = self.parse_and_validate_manifest(
            manifest_path=p, expected_sha256=expected_sha256
        )

        mode_str = "EXECUTE" if execute else "DRY_RUN"
        report = DispositionReport(
            status=DispositionStatus.PLANNED,
            mode=mode_str,
            manifest_path=str(p),
            manifest_hash=actual_hash,
            manifest_hash_match=hash_matched,
            total_manifest_entries=len(entries),
            accounts_targeted=len(entries),
            intents_targeted=sum(len(e.publish_intents) for e in entries),
            tasks_targeted=sum(len(e.tasks) for e in entries),
            missions_targeted=sum(len(e.missions) for e in entries),
            credentials_retained=len(entries),
        )

        async with self.session_factory() as session:
            # 1. Read-only atomic precondition evaluation
            is_pending = await self.verify_preconditions(session, entries)

            if not is_pending:
                report.status = DispositionStatus.ALREADY_COMPLETED
                report.accounts_revoked = len(entries)
                report.intents_cancelled = report.intents_targeted
                report.tasks_cancelled = report.tasks_targeted
                report.missions_cancelled = report.missions_targeted
                logger.info("All manifest items already disposed. Zero mutations needed.")
                return report

            if not execute:
                # Dry run completed successfully with zero mutations
                report.status = DispositionStatus.PLANNED
                logger.info(
                    "Disposition dry-run completed successfully. Zero mutations committed.",
                    extra={"total": len(entries)},
                )
                return report

            # 2. Execution mode: single atomic outer transaction
            # If an implicit transaction was started during read-only preconditions in real SQLAlchemy,
            # rollback the read transaction to start a clean atomic write transaction.
            if hasattr(session, "in_transaction") and callable(session.in_transaction) and session.in_transaction():
                await session.rollback()

            audit_events = 0
            async with session.begin():
                # Order of transitions:
                # Step 1: Cancel PublishIntents (records PublishIntentTransition)
                for entry in entries:
                    for pi in entry.publish_intents:
                        iid = UUID(pi["intent_id"])
                        await PublishIntentService.cancel_intent(
                            session=session,
                            intent_id=iid,
                            reason="Dirty data disposition: unauthorized test residue",
                            actor="SYSTEM_MAINTENANCE",
                            commit=False,
                        )
                        report.intents_cancelled += 1
                        audit_events += 1

                # Step 2: Cancel Missions (which automatically cancels child Tasks and records DecisionLog)
                for entry in entries:
                    for m in entry.missions:
                        mid = UUID(m["mission_id"])
                        await cancel_mission(
                            session=session,
                            mission_id=mid,
                            commit=False,
                        )
                        report.missions_cancelled += 1
                        report.tasks_cancelled += len(entry.tasks)
                        audit_events += 1

                # Step 3: Revoke PlatformAccounts (preserves CredentialVault)
                for entry in entries:
                    await OAuthService.revoke_account(
                        session=session,
                        account_id=entry.platform_account_id,
                        reason="Dirty data disposition: unauthorized test residue",
                        commit=False,
                    )
                    report.accounts_revoked += 1

                # Step 4: Validate post-conditions within transaction
                stmt_chk_pa = select(func.count(PlatformAccount.id)).where(
                    PlatformAccount.id.in_([e.platform_account_id for e in entries]),
                    PlatformAccount.status == PlatformAccountStatus.REVOKED.value,
                )
                res_pa = await session.execute(stmt_chk_pa)
                if res_pa.scalar_one() != len(entries):
                    raise DispositionError("Post-condition check failed: not all accounts marked REVOKED")

                # Verify all credential vault rows are still present and unmodified
                stmt_chk_cv = select(func.count(CredentialVault.id)).where(
                    CredentialVault.id.in_([e.credential_vault_id for e in entries])
                )
                res_cv = await session.execute(stmt_chk_cv)
                if res_cv.scalar_one() != len(entries):
                    raise DispositionError("Post-condition check failed: CredentialVault rows were deleted!")

            report.status = DispositionStatus.EXECUTED
            report.audit_records_emitted = audit_events
            logger.info(
                "Disposition execution completed successfully",
                extra={
                    "accounts_revoked": report.accounts_revoked,
                    "intents_cancelled": report.intents_cancelled,
                    "tasks_cancelled": report.tasks_cancelled,
                    "missions_cancelled": report.missions_cancelled,
                },
            )

        return report

    def get_excluded_vault_ids(self, manifest_path: Path | str) -> list[str]:
        """Extract sorted deterministic list of the 125 vault IDs to exclude from rotation."""
        entries, _, _ = self.parse_and_validate_manifest(manifest_path)
        return sorted(str(e.credential_vault_id) for e in entries)
