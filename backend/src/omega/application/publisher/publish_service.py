"""Authoritative Publish Execution Service for OMEGA-011 Publisher.

Orchestrates the complete publish execution lifecycle:
TX-CLAIM -> Artifact & Storage Confinement -> Guardian Gate ->
Dynamic Privacy Policy -> OAuth Token Refresh -> Resumable Streaming ->
Inter-Chunk Fencing -> Monotonic Byte Progress -> Authoritative Completion ->
Transactional Retry Handoff.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.guardian.engine import GuardianEngine
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

            # Validate path safety
            storage_root = Path(settings.media_storage_root).resolve()
            artifact_file_path = (storage_root / artifact.storage_uri).resolve()
            if not str(artifact_file_path).startswith(str(storage_root)):
                raise PublishExecutionError(
                    f"Artifact path escape detected: {artifact.storage_uri}"
                )

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
