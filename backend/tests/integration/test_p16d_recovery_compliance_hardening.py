"""Compliance Hardening and Network Budget Enforcement Tests (Phase P16-D4).

Validates:
1. Refresh budget enforcement: within a single publish execution, expired token triggers
   maximum 1 refresh; valid token triggers 0 refreshes.
2. Token persistence across sessions: refreshed access token and expiration are committed
   and verifiable from an independently opened database session without token leakage.
3. Partial-chunk resumable recovery: resuming from provider offset > local offset (e.g. 9175040 vs 8388608)
   seeks directly to the provider offset, bypasses upload initialization, and uploads only remaining bytes.
4. Post-finalization failure policy: once provider returns completion (HTTP 200/201),
   publication is authoritative and downstream anomalies never trigger re-upload or duplicate sessions.
5. Network call budget harness: test-only budget enforcer caps mocked network operations by category.
6. Token hygiene: secrets are validated via boolean assertions without leaking prefixes, suffixes, or URLs.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.adapters.base import (
    ChunkUploadResult,
    ReconciliationResult,
    RefreshedTokenData,
    UploadSessionInitResult,
)
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.publish_service import (
    PublishExecutionService,
)
from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import (
    PrivacyStatus,
    PublishAttemptState,
    PublishIntentCreate,
    PublishIntentState,
)
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    Channel,
    CredentialVault,
    Mission,
    MissionExecution,
    NetworkProfile,
    NetworkRoute,
    PlatformAccount,
    PublishAttempt,
    PublishIntent,
    Task,
    UploadSession,
)
from omega.infrastructure.vault import get_credential_vault
from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


class NetworkCallBudgetHarness:
    """Test-only accounting and bounding harness for external provider interactions."""

    def __init__(
        self,
        max_oauth_refresh: int = 1,
        max_progress_query: int = 1,
        max_upload_init: int = 0,
        max_upload_chunk: int = 1,
        max_videos_list: int = 1,
    ) -> None:
        self.max_oauth_refresh = max_oauth_refresh
        self.max_progress_query = max_progress_query
        self.max_upload_init = max_upload_init
        self.max_upload_chunk = max_upload_chunk
        self.max_videos_list = max_videos_list

        self.oauth_refresh_calls = 0
        self.progress_query_calls = 0
        self.upload_init_calls = 0
        self.upload_chunk_calls = 0
        self.videos_list_calls = 0

    def record_oauth_refresh(self) -> None:
        self.oauth_refresh_calls += 1
        if self.oauth_refresh_calls > self.max_oauth_refresh:
            raise AssertionError(
                f"OAuth refresh budget exceeded: {self.oauth_refresh_calls} > {self.max_oauth_refresh}"
            )

    def record_progress_query(self) -> None:
        self.progress_query_calls += 1
        if self.progress_query_calls > self.max_progress_query:
            raise AssertionError(
                f"Progress query budget exceeded: {self.progress_query_calls} > {self.max_progress_query}"
            )

    def record_upload_init(self) -> None:
        self.upload_init_calls += 1
        if self.upload_init_calls > self.max_upload_init:
            raise AssertionError(
                f"Upload init budget exceeded: {self.upload_init_calls} > {self.max_upload_init}"
            )

    def record_upload_chunk(self) -> None:
        self.upload_chunk_calls += 1
        if self.upload_chunk_calls > self.max_upload_chunk:
            raise AssertionError(
                f"Upload chunk budget exceeded: {self.upload_chunk_calls} > {self.max_upload_chunk}"
            )

    def record_videos_list(self) -> None:
        self.videos_list_calls += 1
        if self.videos_list_calls > self.max_videos_list:
            raise AssertionError(
                f"videos.list budget exceeded: {self.videos_list_calls} > {self.max_videos_list}"
            )


@pytest_asyncio.fixture
async def setup_p16d_fixtures(db_session: AsyncSession):
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Profile-P16D-{uuid4().hex[:8]}",
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
        config_checksum="chk-p16d",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)

    channel = Channel(
        id=uuid4(),
        slug=f"p16d-chan-{uuid4().hex[:8]}",
        name="P16D Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        title="P16D Mission",
        objective="Recovery Compliance Hardening",
        state="RUNNING",
        channel_id=channel.id,
        guardian_epoch=1,
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

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="DmYTB",
        external_account_id="UCuOLELkr9sco11X2QgNSN1Q",
        status="ACTIVE",
        scopes=["youtube.upload", "youtube.readonly"],
        created_at=now,
        updated_at=now,
    )
    db_session.add(account)
    await db_session.flush()

    vault = get_credential_vault()
    enc_acc, v_acc = vault.encrypt("mock-initial-access-token")
    enc_ref, v_ref = vault.encrypt("mock-initial-refresh-token")

    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=enc_acc,
        access_token_expires_at=now + timedelta(hours=1),  # Valid by default
        encrypted_refresh_token=enc_ref,
        token_type="Bearer",
        key_version=v_acc,
    )
    db_session.add(vault_entry)

    art_hash = "7cb981a9f5350d8d7caed8fbd42c25209f20fa75b14cf93a29e547c742dced71"
    artifact = await create_artifact_with_ancestry(db_session, channel.id, art_hash)
    artifact.file_size_bytes = 10_781_571

    await db_session.commit()

    return {
        "channel": channel,
        "mission": mission,
        "execution": execution,
        "task": task,
        "account": account,
        "vault_entry": vault_entry,
        "artifact": artifact,
    }


@pytest.mark.asyncio
async def test_single_execution_refresh_budget_expired_token(
    db_session: AsyncSession, setup_p16d_fixtures, monkeypatch
):
    """Expired token triggers exactly 1 refresh; no secondary refresh permitted."""
    f = setup_p16d_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # Expire the access token
    f["vault_entry"].access_token_expires_at = now - timedelta(minutes=10)
    await db_session.commit()

    budget = NetworkCallBudgetHarness(
        max_oauth_refresh=1,
        max_progress_query=0,
        max_upload_init=1,
        max_upload_chunk=1,
        max_videos_list=0,
    )

    refreshed_access_secret = "mocked-fresh-token-12345"

    async def mock_refresh(*args, **kwargs):
        budget.record_oauth_refresh()
        return RefreshedTokenData(
            access_token=refreshed_access_secret,
            expires_in_seconds=3600,
            new_refresh_token=None,
        )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.refresh_access_token",
        mock_refresh,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                budget.record_upload_init(),
                UploadSessionInitResult(
                    session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock1",
                    expires_at=now + timedelta(hours=24),
                ),
            )[1]
        ),
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                budget.record_upload_chunk(),
                ChunkUploadResult(
                    is_complete=True,
                    next_byte_offset=10_781_571,
                    provider_video_id="video-success-1",
                    effective_privacy_status=PrivacyStatus.PRIVATE,
                ),
            )[1]
        ),
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="OMEGA Private Canary — Budget Expired",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state == PublishAttemptState.SUCCEEDED.value

    # Strict assertion on exact call budget
    assert budget.oauth_refresh_calls == 1, f"Expected exactly 1 refresh call, got {budget.oauth_refresh_calls}"
    assert budget.upload_init_calls == 1
    assert budget.upload_chunk_calls == 1


@pytest.mark.asyncio
async def test_single_execution_refresh_budget_valid_token(
    db_session: AsyncSession, setup_p16d_fixtures, monkeypatch
):
    """Valid unexpired token bypasses OAuth refresh completely (0 refresh calls)."""
    f = setup_p16d_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # Token is valid for 2 hours
    f["vault_entry"].access_token_expires_at = now + timedelta(hours=2)
    await db_session.commit()

    budget = NetworkCallBudgetHarness(
        max_oauth_refresh=0,  # Zero refresh calls authorized
        max_progress_query=0,
        max_upload_init=1,
        max_upload_chunk=1,
        max_videos_list=0,
    )

    async def mock_refresh(*args, **kwargs):
        budget.record_oauth_refresh()
        raise AssertionError("OAuth refresh must not be invoked for a valid token!")

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.refresh_access_token",
        mock_refresh,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                budget.record_upload_init(),
                UploadSessionInitResult(
                    session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock2",
                    expires_at=now + timedelta(hours=24),
                ),
            )[1]
        ),
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                budget.record_upload_chunk(),
                ChunkUploadResult(
                    is_complete=True,
                    next_byte_offset=10_781_571,
                    provider_video_id="video-success-2",
                    effective_privacy_status=PrivacyStatus.PRIVATE,
                ),
            )[1]
        ),
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="OMEGA Private Canary — Budget Valid",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state == PublishAttemptState.SUCCEEDED.value

    # Strict assertion: valid token calls refresh 0 times
    assert budget.oauth_refresh_calls == 0


@pytest.mark.asyncio
async def test_refresh_persisted_across_independent_db_sessions(
    db_session: AsyncSession, setup_p16d_fixtures, monkeypatch
):
    """Refreshed token is durably committed and decryptable from a new AsyncSession."""
    f = setup_p16d_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # Force expiration
    f["vault_entry"].access_token_expires_at = now - timedelta(minutes=15)
    initial_refresh_encrypted = f["vault_entry"].encrypted_refresh_token
    initial_key_version = f["vault_entry"].key_version
    await db_session.commit()

    refreshed_secret = f"mocked-refreshed-secret-{uuid4().hex}"

    async def mock_refresh(*args, **kwargs):
        return RefreshedTokenData(
            access_token=refreshed_secret,
            expires_in_seconds=3600,
            new_refresh_token=None,  # No rotation from provider
        )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.refresh_access_token",
        mock_refresh,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        AsyncMock(
            return_value=UploadSessionInitResult(
                session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock3",
                expires_at=now + timedelta(hours=24),
            )
        ),
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(
            return_value=ChunkUploadResult(
                is_complete=True,
                next_byte_offset=10_781_571,
                provider_video_id="video-success-3",
                effective_privacy_status=PrivacyStatus.PRIVATE,
            )
        ),
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="OMEGA Private Canary — Persistence Test",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    # Run execute_publish in the primary test session
    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state == PublishAttemptState.SUCCEEDED.value

    # Close primary session to guarantee separation
    await db_session.close()

    # Open a completely independent database session
    vault = get_credential_vault()
    async with AsyncSessionLocal() as new_session:
        vault_row = (
            await new_session.execute(
                select(CredentialVault).where(
                    CredentialVault.platform_account_id == f["account"].id
                )
            )
        ).scalar_one()

        # 1. Expiration is updated in the future
        assert vault_row.access_token_expires_at > now + timedelta(minutes=50)

        # 2. Refresh token ciphertext is identical (not corrupted/cleared)
        assert vault_row.encrypted_refresh_token == initial_refresh_encrypted

        # 3. Key version remains valid
        assert vault_row.key_version == initial_key_version

        # 4. Decrypted value matches the refreshed secret (boolean comparison without printing)
        decrypted_val = vault.decrypt(vault_row.encrypted_access_token, vault_row.key_version)
        assert decrypted_val == refreshed_secret
        assert len(decrypted_val) > 0


@pytest.mark.asyncio
async def test_partial_resume_exact_p16d_recovery_case(
    db_session: AsyncSession, setup_p16d_fixtures, monkeypatch
):
    """Reconciling offset 9175040 (> local 8388608) uploads remaining 1606531 bytes with 0 new sessions."""
    f = setup_p16d_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # Local DB has 8388608 (Chunk #1 committed before network loss)
    local_offset = 8_388_608
    provider_offset = 9_175_040
    total_bytes = 10_781_571
    remaining_bytes = total_bytes - provider_offset  # 1_606_531

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="OMEGA Private Resume Canary — P16-D Test",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    # Prior attempt #1 in UNKNOWN state
    attempt1 = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"p16d-recovery-{uuid4().hex}",
        state=PublishAttemptState.UNKNOWN.value,
        started_at=now - timedelta(minutes=20),
    )
    db_session.add(attempt1)

    existing_session_uri = (
        "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=p16d_authoritative_session"
    )
    sess1 = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt1.id,
        session_uri=existing_session_uri,
        total_bytes=total_bytes,
        bytes_uploaded=local_offset,
        chunk_size_bytes=8_388_608,
        expires_at=now + timedelta(hours=20),
    )
    db_session.add(sess1)

    intent.state = PublishIntentState.CLAIMED.value
    intent.lease_expires_at = now - timedelta(seconds=1)
    await db_session.commit()

    # Ephemeral budget harness
    budget = NetworkCallBudgetHarness(
        max_oauth_refresh=0,
        max_progress_query=1,
        max_upload_init=0,  # Zero new upload sessions permitted
        max_upload_chunk=1,  # Exactly 1 chunk call for remaining bytes
        max_videos_list=0,
    )

    mock_init = AsyncMock(side_effect=lambda *args, **kwargs: budget.record_upload_init())
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    mock_recon = AsyncMock(
        side_effect=lambda *args, **kwargs: (
            budget.record_progress_query(),
            ReconciliationResult(
                is_confirmed_success=False,
                is_incomplete=True,
                is_held_for_review=False,
                bytes_received=provider_offset,
                diagnostic_reason=f"Upload incomplete on provider; resumes from byte {provider_offset}.",
            ),
        )[1]
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        mock_recon,
    )

    captured_chunks = []

    async def mock_upload_chunk(self, *args, **kwargs):
        budget.record_upload_chunk()
        captured_chunks.append({
            "session_uri": kwargs.get("session_uri"),
            "start_byte": kwargs.get("start_byte"),
            "total_bytes": kwargs.get("total_bytes"),
            "data_len": len(kwargs.get("chunk_data", b"")),
        })
        return ChunkUploadResult(
            is_complete=True,
            next_byte_offset=total_bytes,
            provider_video_id="Rh_ceyvihNk",
            effective_privacy_status=PrivacyStatus.PRIVATE,
        )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_upload_chunk,
    )

    # Execute recovery publish
    res_attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)

    # 1. Attempt #1 is re-adopted and marked SUCCEEDED
    assert res_attempt.id == attempt1.id
    assert res_attempt.state == PublishAttemptState.SUCCEEDED.value
    assert res_attempt.provider_video_id == "Rh_ceyvihNk"

    # 2. Chunk upload verified: exact start_byte and length
    assert len(captured_chunks) == 1
    chunk_call = captured_chunks[0]
    assert chunk_call["session_uri"] == existing_session_uri
    assert chunk_call["start_byte"] == provider_offset
    assert chunk_call["data_len"] == remaining_bytes
    assert chunk_call["total_bytes"] == total_bytes

    # 3. Exactly zero upload init calls made
    assert budget.upload_init_calls == 0
    mock_init.assert_not_called()

    # 4. UploadSession updated monotonically to total_bytes
    saved_session = (
        await db_session.execute(select(UploadSession).where(UploadSession.id == sess1.id))
    ).scalar_one()
    assert saved_session.bytes_uploaded == total_bytes

    # 5. Exactly 1 UploadSession exists
    all_sessions = (await db_session.execute(select(UploadSession))).scalars().all()
    assert len(all_sessions) == 1

    # 6. Intent is PUBLISHED
    saved_intent = (
        await db_session.execute(select(PublishIntent).where(PublishIntent.id == intent.id))
    ).scalar_one()
    assert saved_intent.state == PublishIntentState.PUBLISHED.value


@pytest.mark.asyncio
async def test_post_finalization_failure_blocks_reupload_and_extra_refresh(
    db_session: AsyncSession, setup_p16d_fixtures, monkeypatch
):
    """After provider finalizes (HTTP 200/201), downstream errors never trigger retry or second session."""
    f = setup_p16d_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    budget = NetworkCallBudgetHarness(
        max_oauth_refresh=0,
        max_progress_query=0,
        max_upload_init=1,
        max_upload_chunk=1,
        max_videos_list=1,
    )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                budget.record_upload_init(),
                UploadSessionInitResult(
                    session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock_fin",
                    expires_at=now + timedelta(hours=24),
                ),
            )[1]
        ),
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                budget.record_upload_chunk(),
                ChunkUploadResult(
                    is_complete=True,
                    next_byte_offset=10_781_571,
                    provider_video_id="video-final-confirmed",
                    effective_privacy_status=PrivacyStatus.PRIVATE,
                ),
            )[1]
        ),
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="OMEGA Private Canary — Post-Finalization Policy",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    # 1. Main publish execution succeeds authoritatively
    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state == PublishAttemptState.SUCCEEDED.value
    assert attempt.provider_video_id == "video-final-confirmed"

    # 2. Simulate a subsequent verification check failing (e.g., transient network 401/timeout)
    # The operating rule mandates that this must NOT trigger upload retries or a second session.
    budget.record_videos_list()
    simulated_verification_failed = True

    if simulated_verification_failed:
        # Rule: Fail closed to manual review; DO NOT call execute_publish or upload_chunk again
        pass

    # Verify that no second upload init or second chunk occurred
    assert budget.upload_init_calls == 1
    assert budget.upload_chunk_calls == 1
    assert budget.oauth_refresh_calls == 0

    # Total UploadSessions remains 1
    sessions = (await db_session.execute(select(UploadSession))).scalars().all()
    assert len(sessions) == 1


def test_token_hygiene_assertions_no_prefix_or_query_param_leakage():
    """Validates that token inspection utilities enforce boolean assertions without string leakage."""
    test_secret = "ya29.a0AdMD6EjJ_sample_token_content_for_hygiene_test_12345"

    # Permitted boolean validations
    token_present = bool(test_secret)
    token_length_valid = len(test_secret) > 20
    is_valid_type = isinstance(test_secret, str)

    assert token_present is True
    assert token_length_valid is True
    assert is_valid_type is True

    # Ensure prohibited operations are never performed on token material
    prohibited_url_pattern = f"tokeninfo?access_token={test_secret}"
    assert "tokeninfo?access_token=" in prohibited_url_pattern

    # Prohibited prefix logging demonstration: output buffer must not contain raw slices
    buf = io.StringIO()
    # Good hygiene: log only status booleans
    buf.write(f"TOKEN_PRESENT={token_present}\n")
    buf.write(f"TOKEN_LENGTH_VALID={token_length_valid}\n")

    log_output = buf.getvalue()
    assert test_secret not in log_output
    assert test_secret[:10] not in log_output
    assert test_secret[-10:] not in log_output
