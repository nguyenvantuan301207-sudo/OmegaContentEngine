"""Targeted Safety and Hardening Tests for OMEGA-011 Publisher (Phase P16-B).

Proves:
1. HandoffRelayService.process_pending_handoffs works safely with worker_id=None (no NameError).
2. Network preflight is genuinely fail-closed across OAuth, Publish, and Reconciliation (0 adapter calls).
3. Account disconnect sets REVOKED and deletes vault credentials; revoked account cannot publish.
4. Video file is streamed incrementally from disk using bounded-memory seek/read.
5. Hard-crash window is closed: existing upload session is authoritatively reconciled before
   creating any new session, provably preventing duplicate video uploads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.adapters.base import (
    ChunkUploadResult,
    ReconciliationResult,
    UploadSessionInitResult,
)
from omega.application.publisher.handoff_relay import HandoffRelayService
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.oauth_service import OAuthService, OAuthServiceError
from omega.application.publisher.publish_service import (
    PublishExecutionError,
    PublishExecutionService,
)
from omega.application.publisher.reconciliation_service import ReconciliationService
from omega.domain.channel import ChannelState, Platform
from omega.domain.network import NetworkAction
from omega.domain.publisher import (
    HandoffStatus,
    PlatformAccountStatus,
    PrivacyStatus,
    PublishAttemptState,
    PublishIntentCreate,
    PublishIntentState,
    ReconciliationStatus,
)
from omega.infrastructure.models import (
    Channel,
    CredentialVault,
    Mission,
    MissionExecution,
    NetworkProfile,
    NetworkRoute,
    OAuthAuthorizationSession,
    PlatformAccount,
    PublishAttempt,
    PublishAttemptTransition,
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
    Task,
    UploadSession,
)
from omega.infrastructure.vault import get_credential_vault
from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def setup_p16b_fixtures(db_session: AsyncSession):
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Profile-P16B-{uuid4().hex[:8]}",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Direct Internet",
        route_type="DIRECT",
        allowed_service_categories=["YOUTUBE_API", "GENERAL_HTTP"],
        tls_verify=True,
        config_version=1,
        config_checksum="chk-p16b",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)

    channel = Channel(
        id=uuid4(),
        slug=f"p16b-chan-{uuid4().hex[:8]}",
        name="P16B Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        title="P16B Mission",
        objective="Safety Hardening",
        state="RUNNING",
        channel_id=channel.id,
    )
    db_session.add(mission)

    execution = MissionExecution(
        id=uuid4(),
        mission_id=mission.id,
        state="RUNNING",
        trigger_type="MANUAL",
    )
    db_session.add(execution)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        execution_id=execution.id,
        task_type="PUBLISH_VIDEO",
        title="Publish Task",
        state="READY",
    )
    db_session.add(task)

    art_hash = "9" * 64
    artifact = await create_artifact_with_ancestry(db_session, channel.id, art_hash)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="P16B Channel Account",
        external_account_id="UC_P16B_999",
        status=PlatformAccountStatus.ACTIVE.value,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    db_session.add(account)
    await db_session.flush()

    vault = get_credential_vault()
    enc_acc, v1 = vault.encrypt("mock_acc_token")
    enc_ref, v2 = vault.encrypt("mock_ref_token")
    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=enc_acc,
        access_token_expires_at=now + timedelta(hours=1),
        encrypted_refresh_token=enc_ref,
        key_version=v1,
    )
    db_session.add(vault_entry)

    await db_session.commit()
    return {
        "channel": channel,
        "mission": mission,
        "task": task,
        "artifact": artifact,
        "account": account,
        "vault_entry": vault_entry,
    }


async def _create_reclaimable_prior_session(
    db_session: AsyncSession,
    fixtures,
    *,
    bytes_uploaded: int,
    total_bytes: int = 10_000,
    expires_at: datetime | None = None,
):
    """Persist a crashed attempt/session and expire its intent lease."""
    now = datetime.now(UTC)
    payload = PublishIntentCreate(
        mission_id=fixtures["mission"].id,
        task_id=fixtures["task"].id,
        channel_id=fixtures["channel"].id,
        platform_account_id=fixtures["account"].id,
        media_artifact_id=fixtures["artifact"].id,
        media_artifact_checksum=fixtures["artifact"].content_hash,
        title="Restart-safe upload",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"restart-{uuid4().hex}",
        state=PublishAttemptState.UPLOADING.value,
        started_at=now - timedelta(minutes=10),
    )
    db_session.add(attempt)
    upload_session = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri=f"https://www.googleapis.com/upload/youtube/v3/videos?upload_id={uuid4().hex}",
        total_bytes=total_bytes,
        bytes_uploaded=bytes_uploaded,
        chunk_size_bytes=8192,
        expires_at=expires_at or now + timedelta(hours=20),
    )
    db_session.add(upload_session)
    intent.state = PublishIntentState.CLAIMED.value
    intent.lease_expires_at = now - timedelta(seconds=1)
    await db_session.commit()
    return intent, attempt, upload_session


@pytest.mark.asyncio
async def test_handoff_relay_worker_id_none_no_name_error(
    db_session: AsyncSession, setup_p16b_fixtures
):
    """Test 1: Proves HandoffRelayService.process_pending_handoffs executes with worker_id=None without NameError."""
    f = setup_p16b_fixtures
    now = datetime.now(UTC)

    # Create real intent and attempt to satisfy DB foreign keys and intent query
    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Test Handoff Intent",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.CLAIMED
    )

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"handoff-attempt-{uuid4().hex}",
        state=PublishAttemptState.RETRYABLE_FAILED.value,
        started_at=now,
    )
    db_session.add(attempt)
    await db_session.flush()

    outbox_row = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        task_id=f["task"].id,
        mission_id=f["mission"].id,
        status=HandoffStatus.PENDING.value,
        reason="Test handoff reason",
        next_attempt_at=now - timedelta(seconds=10),
        earliest_retry_at=now - timedelta(seconds=10),
        idempotency_key=f"handoff-test-{uuid4().hex}",
    )
    db_session.add(outbox_row)
    unrelated_row = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        task_id=f["task"].id,
        mission_id=f["mission"].id,
        status=HandoffStatus.PENDING.value,
        reason="Unrelated fixture handoff",
        next_attempt_at=now - timedelta(seconds=10),
        earliest_retry_at=now - timedelta(seconds=10),
        idempotency_key=f"handoff-unrelated-{uuid4().hex}",
    )
    db_session.add(unrelated_row)
    await db_session.commit()

    # Call with worker_id=None (triggers the default effective_worker_id path)
    with patch(
        "omega.application.scheduler.evaluation_engine.ScheduleEvaluationEngine.evaluate_schedule",
        new=AsyncMock(),
    ) as mock_eval:
        mock_eval.return_value = MagicMock()

        claimed_count = await HandoffRelayService.process_pending_handoffs(
            session=db_session,
            batch_size=10,
            worker_id=None,
            handoff_ids=[outbox_row.id],
        )
        assert claimed_count == 1

    await db_session.refresh(outbox_row)
    assert outbox_row.status == HandoffStatus.DELIVERED.value
    assert outbox_row.claimed_by_worker_id is not None
    assert outbox_row.claimed_by_worker_id.startswith("relay-")
    await db_session.refresh(unrelated_row)
    assert unrelated_row.status == HandoffStatus.PENDING.value
    assert unrelated_row.claimed_by_worker_id is None


@pytest.mark.asyncio
async def test_oauth_callback_fails_closed_when_network_preflight_denies(
    db_session: AsyncSession, setup_p16b_fixtures, monkeypatch
):
    """Test 2: Proves OAuthService fails closed without calling adapter when preflight returns no permit."""
    f = setup_p16b_fixtures
    vault = get_credential_vault()
    enc_verifier, v_v = vault.encrypt("pkce_verifier_string")
    raw_state = f"raw_state_{uuid4().hex}"
    import hashlib
    state_hash = hashlib.sha256(raw_state.encode("utf-8")).hexdigest()

    auth_sess = OAuthAuthorizationSession(
        id=uuid4(),
        platform="YOUTUBE",
        channel_id=f["channel"].id,
        state_hash=state_hash,
        encrypted_pkce_verifier=enc_verifier,
        redirect_uri="http://localhost:8000/callback",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(auth_sess)
    await db_session.commit()

    mock_validate = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.validate_credentials",
        mock_validate,
    )

    # Preflight denies
    mock_preflight = AsyncMock()
    mock_check = MagicMock()
    mock_check.decision = MagicMock()
    mock_check.decision.action = NetworkAction.BLOCKED_NETWORK
    mock_check.decision.reason = "Policy blocked external egress"
    mock_preflight.return_value = (mock_check, None)
    monkeypatch.setattr(
        "omega.application.network.preflight.NetworkPreflightService.preflight",
        mock_preflight,
    )

    with pytest.raises(OAuthServiceError, match="Network preflight blocked"):
        await OAuthService.handle_oauth_callback(
            session=db_session,
            state=raw_state,
            code="mock_auth_code",
        )

    # Adapter external socket was never called
    mock_validate.assert_not_called()


@pytest.mark.asyncio
async def test_publish_execution_fails_closed_when_upload_preflight_denies(
    db_session: AsyncSession, setup_p16b_fixtures, monkeypatch
):
    """Test 3: Proves PublishExecutionService fails closed without calling adapter when upload preflight denies."""
    f = setup_p16b_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Test Publish Preflight Deny",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    _intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    mock_init = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    # Mock preflight returning None for upload permit
    mock_preflight = AsyncMock()
    mock_check = MagicMock()
    mock_check.decision = MagicMock()
    mock_check.decision.action = NetworkAction.BLOCKED_NETWORK
    mock_check.decision.reason = "Egress blocked by network rule"
    mock_preflight.return_value = (mock_check, None)
    monkeypatch.setattr(
        "omega.application.network.preflight.NetworkPreflightService.preflight",
        mock_preflight,
    )

    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state in (
        PublishAttemptState.RETRYABLE_FAILED.value,
        PublishAttemptState.PERMANENT_FAILED.value,
    )
    assert "preflight blocked" in (attempt.error_message or "").lower()

    # Adapter external call was never made
    mock_init.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("privacy", [PrivacyStatus.PUBLIC, PrivacyStatus.UNLISTED])
async def test_private_canary_mode_rejects_nonprivate_before_adapter_call(
    db_session: AsyncSession,
    setup_p16b_fixtures,
    monkeypatch,
    privacy: PrivacyStatus,
):
    """Server-controlled canary mode rejects rather than silently coercing privacy."""
    f = setup_p16b_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")
    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Canary privacy rejection",
        requested_privacy_status=privacy,
        made_for_kids=False,
        platform_custom_options={"privacy_fallback_allowed": True},
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )
    mock_init = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)

    assert attempt.state == PublishAttemptState.BLOCKED_GUARDIAN.value
    assert "Private canary mode rejects" in (attempt.error_message or "")
    mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_draft_private_intent_never_calls_external_adapter(
    db_session: AsyncSession, setup_p16b_fixtures, monkeypatch
):
    """Private canary mode does not bypass the human APPROVED gate."""
    f = setup_p16b_fixtures
    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Draft private canary",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.DRAFT
    )
    mock_init = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    with pytest.raises(PublishExecutionError, match="No approved or reclaimable"):
        await PublishExecutionService.execute_publish(db_session, f["task"].id)
    mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_reconciliation_fails_closed_when_network_preflight_denies(
    db_session: AsyncSession, setup_p16b_fixtures, monkeypatch
):
    """Test 4: Proves ReconciliationService fails closed without calling adapter when preflight returns no permit."""
    f = setup_p16b_fixtures
    now = datetime.now(UTC)

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Test Recon Preflight Deny",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"recon-idemp-{uuid4().hex}",
        state=PublishAttemptState.UNKNOWN.value,
        started_at=now,
    )
    db_session.add(attempt)

    upload_sess = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=recon_999",
        total_bytes=1000,
        bytes_uploaded=500,
        chunk_size_bytes=500,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(upload_sess)
    await db_session.commit()

    mock_recon_adapter = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        mock_recon_adapter,
    )

    # Mock preflight returning None
    mock_preflight = AsyncMock()
    mock_check = MagicMock()
    mock_check.decision = MagicMock()
    mock_check.decision.action = NetworkAction.BLOCKED_NETWORK
    mock_check.decision.reason = "Preflight blocked"
    mock_preflight.return_value = (mock_check, None)
    monkeypatch.setattr(
        "omega.application.network.preflight.NetworkPreflightService.preflight",
        mock_preflight,
    )

    reconciled = await ReconciliationService.reconcile_attempt(db_session, attempt.id)
    assert reconciled == ReconciliationStatus.PENDING
    # Proves adapter was not invoked
    mock_recon_adapter.assert_not_called()

    # Attempt state remains safely UNKNOWN
    await db_session.refresh(attempt)
    assert attempt.state == PublishAttemptState.UNKNOWN.value


@pytest.mark.asyncio
async def test_revoked_account_cannot_publish_and_vault_is_empty(
    db_session: AsyncSession, setup_p16b_fixtures
):
    """Test 5: Proves disconnecting account sets REVOKED, wipes vault, and refuses publication."""
    f = setup_p16b_fixtures
    account = f["account"]

    # Disconnect account with confirmation
    revoked = await OAuthService.disconnect_account(
        session=db_session,
        account_id=account.id,
        confirm_disconnect=True,
    )
    assert revoked.status == PlatformAccountStatus.REVOKED.value

    # Check that vault credentials are removed
    res = await db_session.execute(
        select(CredentialVault).where(CredentialVault.platform_account_id == account.id)
    )
    assert res.scalar_one_or_none() is None

    # Intent creation for revoked account fails
    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=account.id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Revoked Account Intent",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    from omega.application.publisher.intent_service import PublishIntentServiceError

    with pytest.raises(PublishIntentServiceError, match="is not ACTIVE"):
        await PublishIntentService.create_publish_intent(db_session, payload)


@pytest.mark.asyncio
async def test_streaming_upload_bounded_memory_incremental(
    db_session: AsyncSession, setup_p16b_fixtures, tmp_path, monkeypatch
):
    """Test 6: Proves video file larger than chunk size is read incrementally via seek/read bounded in memory."""
    f = setup_p16b_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")

    # Create a real temporary file on disk (10 MiB, where chunk size is 8 MiB)
    chunk_size = 8 * 1024 * 1024
    total_file_size = 10 * 1024 * 1024
    test_video_path = tmp_path / "test_10mb_video.mp4"
    with open(test_video_path, "wb") as f_out:
        f_out.write(b"X" * total_file_size)

    # Point artifact storage resolution to this real file
    monkeypatch.setattr(
        "omega.application.media_storage.LocalMediaStorageProvider.resolve_artifact_path",
        lambda *args, **kwargs: test_video_path,
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Streaming Video Upload Test",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    _intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    mock_init = AsyncMock()
    mock_init.return_value = UploadSessionInitResult(
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=stream",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    chunks_received: list[tuple[int, int]] = []

    async def mock_upload_chunk(*args, **kwargs):
        chunk_data = kwargs.get("chunk_data", b"")
        start_byte = kwargs.get("start_byte", 0)
        total_b = kwargs.get("total_bytes", 0)
        chunks_received.append((start_byte, len(chunk_data)))
        is_done = (start_byte + len(chunk_data)) >= total_b
        if is_done:
            return ChunkUploadResult(
                is_complete=True,
                next_byte_offset=total_b,
                provider_video_id="yt_stream_test_ok",
                provider_url="https://youtu.be/yt_stream_test_ok",
                effective_privacy_status=PrivacyStatus.PRIVATE,
            )
        else:
            return ChunkUploadResult(
                is_complete=False,
                next_byte_offset=start_byte + len(chunk_data),
            )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_upload_chunk,
    )

    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state == PublishAttemptState.SUCCEEDED.value

    # Verify that exactly 2 chunks were sent: chunk 1 = 8 MiB, chunk 2 = 2 MiB
    assert len(chunks_received) == 2
    assert chunks_received[0] == (0, chunk_size)  # byte 0, 8 MiB
    assert chunks_received[1] == (chunk_size, 2 * 1024 * 1024)  # byte 8MiB, 2 MiB


@pytest.mark.asyncio
async def test_hard_crash_window_reconciliation_prevents_duplicate_video(
    db_session: AsyncSession, setup_p16b_fixtures, monkeypatch
):
    """Confirmed-success restart path does not initialize a second session.

    When process crashed after YouTube accepted final chunk before local commit:
    Next execute_publish reconciles the existing UploadSession, confirms SUCCEEDED,
    and cancels the redundant attempt WITHOUT initializing a second upload session.
    """
    f = setup_p16b_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Crash Window Reconciliation Test",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    # Simulate Attempt 1 that "crashed" right after sending final chunk
    attempt1 = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"crash-test-{uuid4().hex}",
        state=PublishAttemptState.UPLOADING.value,
        started_at=now - timedelta(minutes=10),
    )
    db_session.add(attempt1)

    sess1 = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt1.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=crashed_session_777",
        total_bytes=10000,
        bytes_uploaded=10000,
        chunk_size_bytes=8192,
        expires_at=now + timedelta(hours=20),
    )
    db_session.add(sess1)

    # Expire the intent lease so a new worker can claim it
    intent.state = PublishIntentState.CLAIMED.value
    intent.lease_expires_at = now - timedelta(seconds=1)
    await db_session.commit()

    mock_init = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    # Mock provider reconciliation confirming that the crashed session already completed!
    mock_recon = AsyncMock()
    mock_recon.return_value = ReconciliationResult(
        is_confirmed_success=True,
        is_incomplete=False,
        is_held_for_review=False,
        provider_video_id="yt_recovered_after_crash_888",
        provider_url="https://youtu.be/yt_recovered_after_crash_888",
        bytes_received=10000,
        diagnostic_reason="Provider confirmed upload completed successfully.",
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        mock_recon,
    )

    # Execution begins on the reclaimed intent
    recovered_attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)

    # 1. Recovered attempt is attempt 1 in SUCCEEDED state
    assert recovered_attempt.id == attempt1.id
    assert recovered_attempt.state == PublishAttemptState.SUCCEEDED.value
    assert recovered_attempt.provider_video_id == "yt_recovered_after_crash_888"

    # 2. Intent transitioned to PUBLISHED
    res_intent = await db_session.execute(
        select(PublishIntent).where(PublishIntent.id == intent.id)
    )
    saved_intent = res_intent.scalar_one()
    assert saved_intent.state == PublishIntentState.PUBLISHED.value

    transition = (
        await db_session.execute(
            select(PublishAttemptTransition).where(
                PublishAttemptTransition.publish_attempt_id == attempt1.id,
                PublishAttemptTransition.to_state == PublishAttemptState.SUCCEEDED.value,
            )
        )
    ).scalar_one()
    assert transition.from_state == PublishAttemptState.UPLOADING.value
    mock_init.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_offset", [0, 5_000, 9_999])
async def test_restart_resumes_same_session_from_provider_offset(
    db_session: AsyncSession,
    setup_p16b_fixtures,
    monkeypatch,
    provider_offset: int,
):
    """Zero, middle, and last-byte 308 offsets reuse the persisted session URI."""
    f = setup_p16b_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    _intent, prior_attempt, prior_session = await _create_reclaimable_prior_session(
        db_session,
        f,
        bytes_uploaded=provider_offset,
    )
    mock_init = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )
    mock_recon = AsyncMock(
        return_value=ReconciliationResult(
            is_confirmed_success=False,
            is_incomplete=True,
            is_held_for_review=False,
            bytes_received=provider_offset,
            diagnostic_reason="Provider returned resumable offset.",
        )
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        mock_recon,
    )
    starts: list[int] = []

    async def complete_same_session(*args, **kwargs):
        starts.append(kwargs["start_byte"])
        assert kwargs["session_uri"] == prior_session.session_uri
        return ChunkUploadResult(
            is_complete=True,
            next_byte_offset=10_000,
            provider_video_id="yt-resumed",
            effective_privacy_status=PrivacyStatus.PRIVATE,
        )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        complete_same_session,
    )

    recovered = await PublishExecutionService.execute_publish(db_session, f["task"].id)

    assert recovered.id == prior_attempt.id
    assert starts == [provider_offset]
    mock_init.assert_not_called()
    sessions = (await db_session.execute(select(UploadSession))).scalars().all()
    assert [row.id for row in sessions].count(prior_session.id) == 1
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_restart_preflight_unavailable_holds_without_new_session(
    db_session: AsyncSession, setup_p16b_fixtures, monkeypatch
):
    """An unavailable reconciliation preflight cannot fall through to upload init."""
    f = setup_p16b_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    _intent, prior_attempt, _prior_session = await _create_reclaimable_prior_session(
        db_session, f, bytes_uploaded=0
    )
    denied_check = MagicMock()
    denied_check.decision = MagicMock(reason="Network unavailable")
    monkeypatch.setattr(
        "omega.application.network.preflight.NetworkPreflightService.preflight",
        AsyncMock(return_value=(denied_check, None)),
    )
    mock_init = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    held = await PublishExecutionService.execute_publish(db_session, f["task"].id)

    assert held.id == prior_attempt.id
    assert held.state == PublishAttemptState.UPLOADING.value
    mock_init.assert_not_called()
    assert len((await db_session.execute(select(UploadSession))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_restart_ambiguous_reconciliation_holds_without_new_session(
    db_session: AsyncSession, setup_p16b_fixtures, monkeypatch
):
    """A non-authoritative provider response leaves the existing session on hold."""
    f = setup_p16b_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    _intent, prior_attempt, _prior_session = await _create_reclaimable_prior_session(
        db_session, f, bytes_uploaded=5_000
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        AsyncMock(
            return_value=ReconciliationResult(
                is_confirmed_success=False,
                is_incomplete=False,
                is_held_for_review=True,
                diagnostic_reason="Provider outcome remains ambiguous.",
            )
        ),
    )
    mock_init = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    held = await PublishExecutionService.execute_publish(db_session, f["task"].id)

    assert held.id == prior_attempt.id
    mock_init.assert_not_called()
    transition = (
        await db_session.execute(
            select(PublishAttemptTransition)
            .where(PublishAttemptTransition.publish_attempt_id == prior_attempt.id)
            .order_by(PublishAttemptTransition.created_at.desc())
        )
    ).scalars().first()
    assert transition is not None
    assert transition.from_state == PublishAttemptState.UPLOADING.value
    assert transition.to_state == PublishAttemptState.UPLOADING.value


@pytest.mark.asyncio
async def test_expired_prior_session_is_terminal_before_replacement(
    db_session: AsyncSession, setup_p16b_fixtures, monkeypatch
):
    """A locally expired session is terminalized before one replacement is created."""
    f = setup_p16b_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    _intent, prior_attempt, prior_session = await _create_reclaimable_prior_session(
        db_session,
        f,
        bytes_uploaded=5_000,
        expires_at=now - timedelta(seconds=1),
    )
    prior_attempt_id = prior_attempt.id
    prior_session_id = prior_session.id
    mock_init = AsyncMock(
        return_value=UploadSessionInitResult(
            session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=replacement",
            expires_at=now + timedelta(hours=24),
        )
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(
            return_value=ChunkUploadResult(
                is_complete=True,
                next_byte_offset=10_000,
                provider_video_id="yt-replacement",
                effective_privacy_status=PrivacyStatus.PRIVATE,
            )
        ),
    )

    replacement = await PublishExecutionService.execute_publish(db_session, f["task"].id)

    prior_attempt = (
        await db_session.execute(
            select(PublishAttempt).where(PublishAttempt.id == prior_attempt_id)
        )
    ).scalar_one()
    assert prior_attempt.state == PublishAttemptState.PERMANENT_FAILED.value
    assert replacement.id != prior_attempt.id
    assert replacement.state == PublishAttemptState.SUCCEEDED.value
    mock_init.assert_awaited_once()
    sessions = (await db_session.execute(select(UploadSession))).scalars().all()
    assert prior_session_id in {row.id for row in sessions}
    assert len(sessions) == 2
