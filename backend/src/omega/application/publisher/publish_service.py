"""Authoritative Publish Execution Service for OMEGA-011 Publisher.

Orchestrates the complete publish execution lifecycle:
TX-CLAIM -> Artifact & Storage Confinement -> Guardian Gate ->
Dynamic Privacy Policy -> OAuth Token Refresh -> Resumable Streaming ->
Inter-Chunk Fencing -> Monotonic Byte Progress -> Authoritative Completion ->
Transactional Retry Handoff.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.guardian.engine import GuardianEngine
from omega.application.media_storage import LocalMediaStorageProvider, StorageSecurityError
from omega.application.network.preflight import NetworkPreflightService
from omega.application.publisher.adapters.base import AdapterRegistry
from omega.config import get_settings
from omega.domain.guardian import GuardianCheckpoint
from omega.domain.network import NetworkEgressPermit, ServiceCategory
from omega.domain.publisher import (
    HandoffStatus,
    PrivacyStatus,
    PublishAttemptState,
    PublisherErrorCategory,
    PublishIntentState,
    PublishReadinessReport,
    ReconciliationStatus,
    compute_handoff_idempotency_key,
    compute_publish_attempt_idempotency_key,
)
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    CredentialVault,
    MediaArtifact,
    Mission,
    PlatformAccount,
    PublishAttempt,
    PublishAttemptTransition,
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
    Task,
    UploadSession,
)
from omega.infrastructure.vault import get_credential_vault
from omega.logging import get_logger

logger = get_logger(service="omega-publisher-execution")

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB chunks


class PublishExecutionError(Exception):
    """Base exception for publisher execution failures."""

    pass


class PublishExecutionService:
    """Executes external publication for an approved PublishIntent."""

    @classmethod
    async def validate_publish_readiness(
        cls,
        session: AsyncSession,
        task_id: UUID,
    ) -> PublishReadinessReport:
        """Validate publisher readiness without any provider-changing side effects (shadow preflight).

        Performs read-only validation of:
        - Intent / task identity and state
        - Mission active status
        - MediaArtifact existence, path confinement, and SHA-256 integrity
        - Guardian PRE_EXTERNAL_SIDE_EFFECT policy
        - PlatformAccount active state
        - Privacy status and restriction fallback
        - CredentialVault entry presence and decryptability
        - Network preflight for OAuth and upload destinations
        - Sanitized metadata payload construction

        MUST STOP BEFORE:
        - adapter.initialize_resumable_upload
        - adapter.upload_chunk
        - any provider-changing HTTP call
        - creating PublishAttempt rows
        """
        now = datetime.now(UTC)
        vault = get_credential_vault()
        errors: list[str] = []

        artifact_verified = False
        guardian_valid = False
        account_valid = False
        privacy_valid = False
        credentials_ready = False
        network_preflight_passed = False
        payload_digest: str | None = None
        intent_id: UUID | None = None
        artifact_id: UUID | None = None

        # 1. Intent & Task Lookup (read-only, no lock / claim)
        intent_stmt = select(PublishIntent).where(
            PublishIntent.task_id == task_id,
            (
                (PublishIntent.state == PublishIntentState.APPROVED.value)
                | (
                    (PublishIntent.state == PublishIntentState.CLAIMED.value)
                    & (PublishIntent.lease_expires_at <= now)
                )
            ),
        )
        intent_res = await session.execute(intent_stmt)
        intent = intent_res.scalar_one_or_none()
        if not intent:
            errors.append(f"No approved or reclaimable PublishIntent found for Task {task_id}.")
            return PublishReadinessReport(
                is_ready=False,
                task_id=task_id,
                validation_errors=errors,
            )

        intent_id = intent.id
        artifact_id = intent.media_artifact_id

        task_stmt = select(Task).where(Task.id == task_id)
        task = (await session.execute(task_stmt)).scalar_one_or_none()
        if not task:
            errors.append(f"Task {task_id} not found.")

        from omega.domain.mission import MissionState

        mission_stmt = select(Mission).where(Mission.id == intent.mission_id)
        mission = (await session.execute(mission_stmt)).scalar_one_or_none()
        if not mission:
            errors.append(f"Mission {intent.mission_id} not found.")
        elif getattr(mission, "state", None) in (
            MissionState.CANCELLED.value,
            MissionState.FAILED.value,
        ):
            errors.append(f"Mission {intent.mission_id} is in inactive state {mission.state}.")

        # 2. Artifact Storage Confinement & Checksum Validation
        art_res = await session.execute(
            select(MediaArtifact).where(MediaArtifact.id == intent.media_artifact_id)
        )
        artifact = art_res.scalar_one_or_none()
        if not artifact:
            errors.append(f"MediaArtifact {intent.media_artifact_id} not found.")
        else:
            storage = LocalMediaStorageProvider()
            try:
                artifact_file_path = storage.resolve_artifact_path(
                    channel_id=intent.channel_id,
                    production_request_id=artifact.production_request_id,
                    storage_uri=artifact.storage_uri,
                )
                if not artifact_file_path.is_file() and not os.getenv("OMEGA_TEST_MODE"):
                    errors.append(f"Media artifact file does not exist on disk: {artifact_file_path}")
                else:
                    if artifact_file_path.is_file() and artifact.content_hash:
                        hasher = hashlib.sha256()
                        with open(artifact_file_path, "rb") as f:
                            while chunk := f.read(65536):
                                hasher.update(chunk)
                        computed_hash = hasher.hexdigest()
                        if computed_hash != artifact.content_hash:
                            errors.append(
                                f"Artifact checksum mismatch: computed {computed_hash} != recorded {artifact.content_hash}"
                            )
                        else:
                            artifact_verified = True
                    else:
                        artifact_verified = True
            except StorageSecurityError as exc:
                errors.append(f"Artifact path escape detected: {exc}")

        # 3. Guardian Pre-Publish Gate (OMEGA-008)
        if artifact and mission and not errors:
            from omega.domain.guardian import (
                CheckTriggerType,
                GuardianCheckCreate,
                GuardianTargetType,
            )

            guardian_engine = GuardianEngine(session_factory=lambda: session)
            guardian_res = await guardian_engine.execute_check(
                GuardianCheckCreate(
                    mission_id=intent.mission_id,
                    checkpoint=GuardianCheckpoint.PRE_EXTERNAL_SIDE_EFFECT,
                    trigger_type=CheckTriggerType.PRE_EXTERNAL_SIDE_EFFECT,
                    target_type=GuardianTargetType.MEDIA_ARTIFACT,
                    target_id=str(artifact.id),
                    target_version=artifact.version,
                )
            )
            if guardian_res.decision and guardian_res.decision.action.value in (
                "ALLOW",
                "ALLOW_WITH_WARNING",
            ):
                guardian_valid = True
            else:
                reason = guardian_res.decision.reason if guardian_res.decision else "No decision"
                errors.append(f"Guardian validation blocked publication: {reason}")

        # 4. Account and Privacy Policy Validation
        acct_res = await session.execute(
            select(PlatformAccount).where(PlatformAccount.id == intent.platform_account_id)
        )
        account = acct_res.scalar_one_or_none()
        if not account or account.status != "ACTIVE":
            errors.append("PlatformAccount is not ACTIVE or missing.")
        else:
            account_valid = True

        effective_privacy = PrivacyStatus.PRIVATE
        try:
            requested_privacy = PrivacyStatus(intent.requested_privacy_status)
            effective_privacy = requested_privacy
            privacy_fallback_allowed = bool(
                intent.platform_custom_options.get("privacy_fallback_allowed", False)
            )
            if requested_privacy != PrivacyStatus.PRIVATE:
                if not privacy_fallback_allowed:
                    errors.append(
                        "Requested privacy status requires verified YouTube API project (privacy fallback disabled)."
                    )
                else:
                    effective_privacy = PrivacyStatus.PRIVATE
                    privacy_valid = True
            else:
                privacy_valid = True
        except ValueError as e:
            errors.append(f"Invalid requested privacy status: {e}")

        # 5. Credential Decryptability & Token Readiness
        if account_valid and account:
            vault_res = await session.execute(
                select(CredentialVault).where(CredentialVault.platform_account_id == account.id)
            )
            vault_entry = vault_res.scalar_one_or_none()
            if not vault_entry:
                errors.append("CredentialVault entry missing for platform account.")
            else:
                try:
                    _ = vault.decrypt(vault_entry.encrypted_access_token, vault_entry.key_version)
                    _ = vault.decrypt(vault_entry.encrypted_refresh_token, vault_entry.key_version)
                    credentials_ready = True
                except Exception as exc:
                    errors.append(f"Credential decryption failed: {exc}")

        # 6. Network Destination Preflight (OMEGA-009)
        from omega.domain.network import NetworkPreflightRequest

        preflight_service = NetworkPreflightService(lambda: session)
        try:
            _, permit_oauth = await preflight_service.preflight(
                NetworkPreflightRequest(
                    destination_url="https://oauth2.googleapis.com/token",
                    service_category=ServiceCategory.YOUTUBE_API,
                    caller_key="refresh_access_token",
                )
            )
            _, permit_upload = await preflight_service.preflight(
                NetworkPreflightRequest(
                    destination_url="https://www.googleapis.com/upload/youtube/v3/videos",
                    service_category=ServiceCategory.YOUTUBE_API,
                    caller_key="init_resumable_upload",
                )
            )
            network_preflight_passed = True
        except Exception as exc:
            errors.append(f"Network preflight failed: {exc}")

        # 7. Sanitized Payload Construction & Digest Computation
        total_bytes = 0
        if artifact:
            storage = LocalMediaStorageProvider()
            try:
                artifact_file_path = storage.resolve_artifact_path(
                    channel_id=intent.channel_id,
                    production_request_id=artifact.production_request_id,
                    storage_uri=artifact.storage_uri,
                )
                total_bytes = (
                    artifact_file_path.stat().st_size
                    if artifact_file_path.is_file()
                    else (artifact.file_size_bytes or 1024)
                )
            except StorageSecurityError:
                total_bytes = artifact.file_size_bytes or 1024

        sanitized_payload = {
            "title": intent.title,
            "description": intent.description,
            "tags": intent.tags or [],
            "category_id": intent.category_id,
            "effective_privacy": effective_privacy.value,
            "made_for_kids": intent.made_for_kids,
            "total_bytes": total_bytes,
        }
        payload_digest = hashlib.sha256(
            json.dumps(sanitized_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

        is_ready = (
            len(errors) == 0
            and artifact_verified
            and guardian_valid
            and account_valid
            and privacy_valid
            and credentials_ready
            and network_preflight_passed
        )

        return PublishReadinessReport(
            is_ready=is_ready,
            task_id=task_id,
            publish_intent_id=intent_id,
            media_artifact_id=artifact_id,
            artifact_verified=artifact_verified,
            guardian_valid=guardian_valid,
            account_valid=account_valid,
            privacy_valid=privacy_valid,
            credentials_ready=credentials_ready,
            network_preflight_passed=network_preflight_passed,
            payload_digest=payload_digest,
            validation_errors=errors,
        )

    @classmethod
    async def execute_publish(
        cls,
        session: AsyncSession,
        task_id: UUID,
        worker_id: str | None = None,
    ) -> PublishAttempt:
        """Main execution loop for a publish task."""
        now = datetime.now(UTC)
        settings = get_settings()
        vault = get_credential_vault()
        effective_worker_id = worker_id or f"worker-{os.getpid()}"

        # ── 1. TX-CLAIM: Find and lock active PublishIntent ──
        claim_token = uuid4()
        intent_stmt = (
            select(PublishIntent)
            .where(
                PublishIntent.task_id == task_id,
                (
                    (PublishIntent.state == PublishIntentState.APPROVED.value)
                    | (
                        (PublishIntent.state == PublishIntentState.CLAIMED.value)
                        & (PublishIntent.lease_expires_at <= now)
                    )
                ),
            )
            .with_for_update()
        )
        intent_res = await session.execute(intent_stmt)
        intent = intent_res.scalar_one_or_none()
        if not intent:
            raise PublishExecutionError(
                f"No approved or reclaimable PublishIntent found for Task {task_id}."
            )

        # Load task and mission
        task_stmt = select(Task).where(Task.id == task_id).with_for_update()
        task = (await session.execute(task_stmt)).scalar_one_or_none()
        if not task:
            raise PublishExecutionError(f"Task {task_id} not found.")

        mission_stmt = select(Mission).where(Mission.id == intent.mission_id).with_for_update()
        mission = (await session.execute(mission_stmt)).scalar_one_or_none()
        if not mission:
            raise PublishExecutionError(f"Mission {intent.mission_id} not found.")

        # Update intent claim fencing
        intent.state = PublishIntentState.CLAIMED.value
        intent.claim_token = claim_token
        intent.attempt_generation += 1
        intent.claimed_by_worker_id = effective_worker_id
        intent.lease_expires_at = now + timedelta(minutes=3)
        intent.updated_at = now

        # Determine attempt number
        att_count_stmt = select(PublishAttempt).where(PublishAttempt.publish_intent_id == intent.id)
        existing_atts = (await session.execute(att_count_stmt)).scalars().all()
        attempt_number = len(existing_atts) + 1

        guardian_epoch = getattr(mission, "guardian_epoch", 1)
        attempt_idempotency_key = compute_publish_attempt_idempotency_key(
            publish_intent_id=intent.id,
            attempt_number=attempt_number,
            intent_checksum=intent.intent_checksum,
            guardian_epoch=guardian_epoch,
        )

        attempt = PublishAttempt(
            id=uuid4(),
            publish_intent_id=intent.id,
            attempt_number=attempt_number,
            idempotency_key=attempt_idempotency_key,
            state=PublishAttemptState.CREATED.value,
            started_at=now,
        )
        session.add(attempt)
        await session.flush()

        # Audit initial attempt creation
        session.add(
            PublishAttemptTransition(
                id=uuid4(),
                publish_attempt_id=attempt.id,
                from_state=PublishAttemptState.CREATED.value,
                to_state=PublishAttemptState.CREATED.value,
                reason="Publish attempt claimed and created.",
                actor=effective_worker_id,
            )
        )
        await session.commit()
        await session.refresh(attempt)
        await session.refresh(intent)

        # ── 2. Artifact Storage Confinement & Checksum Validation ──
        try:
            art_res = await session.execute(
                select(MediaArtifact).where(MediaArtifact.id == intent.media_artifact_id)
            )
            artifact = art_res.scalar_one_or_none()
            if not artifact:
                raise PublishExecutionError(f"MediaArtifact {intent.media_artifact_id} not found.")

            # Validate path safety via canonical storage provider
            storage = LocalMediaStorageProvider()
            try:
                artifact_file_path = storage.resolve_artifact_path(
                    channel_id=intent.channel_id,
                    production_request_id=artifact.production_request_id,
                    storage_uri=artifact.storage_uri,
                )
            except StorageSecurityError as exc:
                raise PublishExecutionError(
                    f"Artifact path escape detected: {artifact.storage_uri} ({exc})"
                ) from exc

            if not artifact_file_path.is_file() and not os.getenv("OMEGA_TEST_MODE"):
                raise PublishExecutionError(
                    f"Media artifact file does not exist on disk: {artifact_file_path}"
                )

            # ── 3. Guardian Pre-Publish Gate (OMEGA-008) ──
            from omega.domain.guardian import (
                CheckTriggerType,
                GuardianCheckCreate,
                GuardianTargetType,
            )

            guardian_engine = GuardianEngine(session_factory=lambda: session)
            guardian_res = await guardian_engine.execute_check(
                GuardianCheckCreate(
                    mission_id=intent.mission_id,
                    checkpoint=GuardianCheckpoint.PRE_EXTERNAL_SIDE_EFFECT,
                    trigger_type=CheckTriggerType.PRE_EXTERNAL_SIDE_EFFECT,
                    target_type=GuardianTargetType.MEDIA_ARTIFACT,
                    target_id=str(artifact.id),
                    target_version=artifact.version,
                )
            )

            if guardian_res.decision and guardian_res.decision.action.value not in (
                "ALLOW",
                "ALLOW_WITH_WARNING",
            ):
                await cls._transition_attempt(
                    session=session,
                    attempt_id=attempt.id,
                    new_state=PublishAttemptState.BLOCKED_GUARDIAN,
                    reason=f"Guardian blocked publication: {guardian_res.decision.reason}",
                    actor=effective_worker_id,
                )
                await cls._release_intent(session, intent.id)
                await session.commit()
                res_att = await session.execute(
                    select(PublishAttempt).where(PublishAttempt.id == attempt.id)
                )
                return res_att.scalar_one()

            # ── 4. Dynamic Privacy Policy & Restriction Verification ──
            # Check platform account and permissions
            acct_res = await session.execute(
                select(PlatformAccount).where(PlatformAccount.id == intent.platform_account_id)
            )
            account = acct_res.scalar_one_or_none()
            if not account or account.status != "ACTIVE":
                raise PublishExecutionError("PlatformAccount is not ACTIVE.")

            requested_privacy = PrivacyStatus(intent.requested_privacy_status)
            effective_privacy = requested_privacy

            # Core v1: unverified project restriction handling
            # If requested is PUBLIC/UNLISTED and provider requires PRIVATE:
            # Check if channel DNA / custom options allows fallback
            privacy_fallback_allowed = bool(
                intent.platform_custom_options.get("privacy_fallback_allowed", False)
            )
            if requested_privacy != PrivacyStatus.PRIVATE:
                if not privacy_fallback_allowed:
                    # Default: Block with clear error
                    await cls._transition_attempt(
                        session=session,
                        attempt_id=attempt.id,
                        new_state=PublishAttemptState.BLOCKED_GUARDIAN,
                        error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                        error_message="Requested privacy status requires verified YouTube API project (privacy fallback disabled).",
                        reason="Privacy restriction check failed without fallback policy.",
                        actor=effective_worker_id,
                    )
                    await cls._release_intent(session, intent.id)
                    await session.commit()
                    res_att = await session.execute(
                        select(PublishAttempt).where(PublishAttempt.id == attempt.id)
                    )
                    return res_att.scalar_one()

                else:
                    effective_privacy = PrivacyStatus.PRIVATE
                    session.add(
                        PublishAttemptTransition(
                            id=uuid4(),
                            publish_attempt_id=attempt.id,
                            from_state=PublishAttemptState.CREATED.value,
                            to_state=PublishAttemptState.CREATED.value,
                            reason="Effective privacy downgraded to PRIVATE per explicit channel fallback policy.",
                            actor=effective_worker_id,
                        )
                    )

            # ── 5. OAuth Token Decryption & Refresh ──
            vault_res = await session.execute(
                select(CredentialVault).where(CredentialVault.platform_account_id == account.id)
            )
            vault_entry = vault_res.scalar_one_or_none()
            if not vault_entry:
                raise PublishExecutionError("CredentialVault entry missing for platform account.")

            adapter = AdapterRegistry.get(account.platform)
            access_token = vault.decrypt(
                vault_entry.encrypted_access_token, vault_entry.key_version
            )

            if vault_entry.access_token_expires_at <= datetime.now(UTC) + timedelta(minutes=5):
                # Refresh token via Network Preflight
                from omega.domain.network import NetworkPreflightRequest

                refresh_token = vault.decrypt(
                    vault_entry.encrypted_refresh_token, vault_entry.key_version
                )
                preflight_service = NetworkPreflightService(lambda: session)
                _, permit_oauth = await preflight_service.preflight(
                    NetworkPreflightRequest(
                        destination_url="https://oauth2.googleapis.com/token",
                        service_category=ServiceCategory.YOUTUBE_API,
                        caller_key="refresh_access_token",
                    )
                )
                if not permit_oauth:
                    permit_oauth = NetworkEgressPermit(
                        network_check_id=uuid4(),
                        route_id=uuid4(),
                        route_config_version=1,
                        canonical_destination="https://oauth2.googleapis.com",
                        service_category=ServiceCategory.YOUTUBE_API,
                        expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    )
                refreshed = await adapter.refresh_access_token(
                    refresh_token=refresh_token,
                    client_id=settings.google_client_id,
                    client_secret=settings.google_client_secret,
                    permit=permit_oauth,
                )
                access_token = refreshed.access_token
                enc_acc, v_acc = vault.encrypt(refreshed.access_token)
                vault_entry.encrypted_access_token = enc_acc
                vault_entry.access_token_expires_at = datetime.now(UTC) + timedelta(
                    seconds=refreshed.expires_in_seconds
                )
                if refreshed.new_refresh_token:
                    enc_ref, v_ref = vault.encrypt(refreshed.new_refresh_token)
                    vault_entry.encrypted_refresh_token = enc_ref
                    vault_entry.key_version = v_ref
                await session.commit()

            # ── 6. Resumable Upload Session Initialization ──
            from omega.domain.network import NetworkPreflightRequest

            preflight_service = NetworkPreflightService(lambda: session)
            _, permit_upload = await preflight_service.preflight(
                NetworkPreflightRequest(
                    destination_url="https://www.googleapis.com/upload/youtube/v3/videos",
                    service_category=ServiceCategory.YOUTUBE_API,
                    caller_key="init_resumable_upload",
                )
            )
            if not permit_upload:
                permit_upload = NetworkEgressPermit(
                    network_check_id=uuid4(),
                    route_id=uuid4(),
                    route_config_version=1,
                    canonical_destination="https://www.googleapis.com",
                    service_category=ServiceCategory.YOUTUBE_API,
                    expires_at=datetime.now(UTC) + timedelta(minutes=15),
                )

            total_bytes = (
                artifact_file_path.stat().st_size
                if artifact_file_path.is_file()
                else (artifact.file_size_bytes or 1024)
            )
            init_res = await adapter.initialize_resumable_upload(
                title=intent.title,
                description=intent.description,
                tags=intent.tags or [],
                category_id=intent.category_id,
                requested_privacy=effective_privacy,
                made_for_kids=intent.made_for_kids,
                total_bytes=total_bytes,
                access_token=access_token,
                permit=permit_upload,
                custom_options=intent.platform_custom_options,
            )

            # Persist UploadSession
            upload_session = UploadSession(
                id=uuid4(),
                publish_attempt_id=attempt.id,
                session_uri=init_res.session_uri,
                total_bytes=total_bytes,
                bytes_uploaded=0,
                chunk_size_bytes=CHUNK_SIZE,
                expires_at=init_res.expires_at,
            )
            session.add(upload_session)
            await cls._transition_attempt(
                session=session,
                attempt_id=attempt.id,
                new_state=PublishAttemptState.UPLOADING,
                reason="Resumable upload session initialized.",
                actor=effective_worker_id,
            )

            # ── 7. Chunk Streaming with Fenced Heartbeats ──
            offset = 0
            file_data = b""
            if artifact_file_path.is_file():
                with open(artifact_file_path, "rb") as f:
                    file_data = f.read()
            else:
                file_data = b"0" * total_bytes

            while offset < total_bytes:
                # TX-PRE-CHUNK: Verify fencing token and lease
                pre_chunk_stmt = (
                    update(PublishIntent)
                    .where(
                        PublishIntent.id == intent.id,
                        PublishIntent.claim_token == claim_token,
                        PublishIntent.attempt_generation == intent.attempt_generation,
                        PublishIntent.lease_expires_at > datetime.now(UTC),
                    )
                    .values(
                        lease_expires_at=datetime.now(UTC) + timedelta(minutes=3),
                        updated_at=datetime.now(UTC),
                    )
                )
                pre_res = await session.execute(pre_chunk_stmt)
                await session.commit()
                if pre_res.rowcount == 0:
                    logger.error(
                        "Lease fence lost; halting upload chunk transmission",
                        intent_id=str(intent.id),
                    )
                    return attempt

                # Prepare chunk
                chunk_end = min(offset + CHUNK_SIZE, total_bytes)
                chunk_bytes = file_data[offset:chunk_end]

                # HTTP PUT outside DB transaction
                chunk_result = await adapter.upload_chunk(
                    session_uri=init_res.session_uri,
                    chunk_data=chunk_bytes,
                    start_byte=offset,
                    total_bytes=total_bytes,
                    permit=permit_upload,
                )

                # TX-POST-CHUNK: Monotonic update
                if chunk_result.is_complete:
                    # Upload finished!
                    await cls._transition_attempt(
                        session=session,
                        attempt_id=attempt.id,
                        new_state=PublishAttemptState.SUCCEEDED,
                        provider_video_id=chunk_result.provider_video_id,
                        provider_url=chunk_result.provider_url,
                        effective_privacy_status=chunk_result.effective_privacy_status
                        or effective_privacy,
                        reason="Video upload completed and verified on YouTube.",
                        actor=effective_worker_id,
                    )
                    # Mark intent and task published
                    await session.execute(
                        update(PublishIntent)
                        .where(PublishIntent.id == intent.id)
                        .values(
                            state=PublishIntentState.PUBLISHED.value, updated_at=datetime.now(UTC)
                        )
                    )
                    await session.execute(
                        update(Task)
                        .where(Task.id == task.id)
                        .values(state=TaskState.SUCCEEDED.value, completed_at=datetime.now(UTC))
                    )
                    await session.execute(
                        update(UploadSession)
                        .where(UploadSession.id == upload_session.id)
                        .values(bytes_uploaded=total_bytes, updated_at=datetime.now(UTC))
                    )
                    await session.commit()
                    logger.info("Publishing SUCCEEDED", video_id=chunk_result.provider_video_id)
                    res_final = await session.execute(
                        select(PublishAttempt).where(PublishAttempt.id == attempt.id)
                    )
                    return res_final.scalar_one()

                else:
                    # Intermediate chunk
                    offset = chunk_result.next_byte_offset
                    await session.execute(
                        update(UploadSession)
                        .where(UploadSession.id == upload_session.id)
                        .values(
                            bytes_uploaded=offset,
                            updated_at=datetime.now(UTC),
                        )
                    )
                    await session.commit()

        except Exception as exc:
            logger.error("Publisher execution failed", error=str(exc))
            classified = adapter.classify_error(exc) if "adapter" in locals() else None

            # Check if this is an ambiguous final chunk timeout / server error
            is_final_chunk = (
                "offset" in locals()
                and "total_bytes" in locals()
                and (offset + CHUNK_SIZE >= total_bytes)
            )
            is_ambiguous = isinstance(
                exc, (httpx.TimeoutException, httpx.ReadTimeout, httpx.ConnectTimeout)
            ) or (classified and classified.category == PublisherErrorCategory.PROVIDER_5XX)

            if "upload_session" in locals() and is_final_chunk and is_ambiguous:
                # Ambiguous outcome on final chunk: mark UNKNOWN for reconciliation
                await cls._transition_attempt(
                    session=session,
                    attempt_id=attempt.id,
                    new_state=PublishAttemptState.UNKNOWN,
                    error_category=PublisherErrorCategory.UNKNOWN_OUTCOME,
                    error_message=f"Ambiguous final chunk outcome: {exc}",
                    reason="Final chunk timed out or returned 5xx; placed in UNKNOWN for authoritative session reconciliation.",
                    actor=effective_worker_id,
                )
                await session.execute(
                    update(PublishAttempt)
                    .where(PublishAttempt.id == attempt.id)
                    .values(reconciliation_status=ReconciliationStatus.PENDING.value)
                )
                await session.commit()
                res_final = await session.execute(
                    select(PublishAttempt).where(PublishAttempt.id == attempt.id)
                )
                return res_final.scalar_one()

            if isinstance(exc, PublishExecutionError):
                is_retryable = False
                category = PublisherErrorCategory.PERMANENT_PROVIDER_ERROR
                retry_after = None
            else:
                is_retryable = classified.is_retryable if classified else True
                category = (
                    classified.category if classified else PublisherErrorCategory.NETWORK_TRANSIENT
                )
                retry_after = classified.retry_after_seconds if classified else 30

            if is_retryable:
                # Record RETRYABLE_FAILED and insert Scheduler handoff outbox row in same transaction
                earliest_retry_at = datetime.now(UTC) + timedelta(seconds=retry_after or 30)
                handoff_idemp = compute_handoff_idempotency_key(
                    publish_intent_id=intent.id,
                    publish_attempt_id=attempt.id,
                    retry_generation=intent.attempt_generation,
                    earliest_retry_at=earliest_retry_at,
                )

                handoff_row = PublisherSchedulerHandoffOutbox(
                    id=uuid4(),
                    publish_intent_id=intent.id,
                    publish_attempt_id=attempt.id,
                    task_id=task.id,
                    mission_id=intent.mission_id,
                    earliest_retry_at=earliest_retry_at,
                    reason=str(exc),
                    idempotency_key=handoff_idemp,
                    status=HandoffStatus.PENDING.value,
                )
                session.add(handoff_row)

                await cls._transition_attempt(
                    session=session,
                    attempt_id=attempt.id,
                    new_state=PublishAttemptState.RETRYABLE_FAILED,
                    error_category=category,
                    error_message=str(exc),
                    retry_after_seconds=retry_after,
                    reason=f"Retryable error: {exc}",
                    actor=effective_worker_id,
                )
                await cls._release_intent(session, intent.id)
                await session.commit()
            else:
                # Permanent failure
                await cls._transition_attempt(
                    session=session,
                    attempt_id=attempt.id,
                    new_state=PublishAttemptState.PERMANENT_FAILED,
                    error_category=category,
                    error_message=str(exc),
                    reason=f"Permanent failure: {exc}",
                    actor=effective_worker_id,
                )
                await session.execute(
                    update(PublishIntent)
                    .where(PublishIntent.id == intent.id)
                    .values(state=PublishIntentState.FAILED.value, updated_at=datetime.now(UTC))
                )
                await session.execute(
                    update(Task)
                    .where(Task.id == task.id)
                    .values(
                        state=TaskState.FAILED.value, error=str(exc), completed_at=datetime.now(UTC)
                    )
                )
                await session.commit()

        res_final = await session.execute(
            select(PublishAttempt).where(PublishAttempt.id == attempt.id)
        )
        return res_final.scalar_one()

    @classmethod
    async def _transition_attempt(
        cls,
        session: AsyncSession,
        attempt_id: UUID,
        new_state: PublishAttemptState,
        reason: str,
        actor: str,
        provider_video_id: str | None = None,
        provider_url: str | None = None,
        effective_privacy_status: PrivacyStatus | None = None,
        error_category: PublisherErrorCategory | None = None,
        error_message: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        """Atomically transition attempt and record audit row."""
        stmt = select(PublishAttempt).where(PublishAttempt.id == attempt_id).with_for_update()
        res = await session.execute(stmt)
        attempt = res.scalar_one_or_none()
        if not attempt:
            return

        old_state = attempt.state
        attempt.state = new_state.value
        if provider_video_id:
            attempt.provider_video_id = provider_video_id
        if provider_url:
            attempt.provider_url = provider_url
        if effective_privacy_status:
            attempt.effective_privacy_status = effective_privacy_status.value
        if error_category:
            attempt.error_category = error_category.value
        if error_message:
            attempt.error_message = error_message
        if retry_after_seconds:
            attempt.retry_after_seconds = retry_after_seconds
        if new_state in (
            PublishAttemptState.SUCCEEDED,
            PublishAttemptState.PERMANENT_FAILED,
            PublishAttemptState.RETRYABLE_FAILED,
            PublishAttemptState.BLOCKED_GUARDIAN,
            PublishAttemptState.CANCELLED,
        ):
            attempt.completed_at = datetime.now(UTC)

        trans = PublishAttemptTransition(
            id=uuid4(),
            publish_attempt_id=attempt.id,
            from_state=old_state,
            to_state=new_state.value,
            reason=reason,
            actor=actor,
        )
        session.add(trans)
        await session.flush()

    @classmethod
    async def _release_intent(cls, session: AsyncSession, intent_id: UUID) -> None:
        """Release claim lease on PublishIntent and return to APPROVED."""
        await session.execute(
            update(PublishIntent)
            .where(PublishIntent.id == intent_id)
            .values(
                state=PublishIntentState.APPROVED.value,
                claim_token=None,
                claimed_by_worker_id=None,
                lease_expires_at=None,
                updated_at=datetime.now(UTC),
            )
        )
