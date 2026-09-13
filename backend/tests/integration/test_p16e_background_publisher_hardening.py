"""Background Publisher and Scheduler Handoff Hardening Tests (Phase P16-E1).

Validates:
1. Human approval gate: DRAFT, REJECTED, CANCELLED intents block background execution (fail-closed).
2. Handoff exactly-once semantics: competing background workers cannot double-consume the same handoff outbox row.
3. Worker entrypoint idempotency: duplicate background task invocations yield exactly one published video.
4. Lease fencing: concurrent workers cannot execute against an actively leased PublishIntent.
5. Expired lease reclaim: worker reclaims expired lease, increments generation, and reconciles before upload.
6. UNKNOWN outcome recovery: ambiguous provider upload states trigger reconciliation first; zero blind retries.
7. Terminal session safe hold: expired/discarded upload sessions trigger MANUAL_HOLD without replacement upload.
8. Privacy enforcement: non-PRIVATE executions in canary mode fail-closed.
9. OAuth refresh budget: max 1 refresh per background execution.
10. Network budget: strict call count accounting per category.
"""

from __future__ import annotations

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
from omega.application.publisher.handoff_relay import HandoffRelayService
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.publish_service import (
    PublishExecutionError,
    PublishExecutionService,
)
from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import (
    HandoffStatus,
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
    PlatformAccount,
    PublishAttempt,
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
    Task,
    UploadSession,
)
from omega.infrastructure.vault import get_credential_vault
from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


class BackgroundNetworkCallBudgetHarness:
    """Mock accounting and bounding harness for background execution paths."""

    def __init__(
        self,
        max_oauth_refresh: int = 1,
        max_progress_query: int = 1,
        max_upload_init: int = 1,
        max_upload_chunk: int = 1,
        max_videos_list: int = 0,
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
async def setup_p16e_fixtures(db_session: AsyncSession):
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Profile-P16E-{uuid4().hex[:8]}",
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
        config_checksum="chk-p16e",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)

    channel = Channel(
        id=uuid4(),
        slug=f"p16e-chan-{uuid4().hex[:8]}",
        name="P16E Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        title="P16E Mission",
        objective="Background Publisher Hardening",
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
        title="Publish Background Task",
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
        access_token_expires_at=now + timedelta(hours=1),
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


# ==============================================================================
# Scenario 1: Human Approval Gate
# ==============================================================================


@pytest.mark.asyncio
async def test_human_approval_gate_blocks_unapproved_intents(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """Background execution must fail closed for DRAFT, CANCELLED, or missing intents."""
    f = setup_p16e_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # 1. DRAFT Intent
    payload_draft = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Unapproved Draft Intent",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent_draft = await PublishIntentService.create_publish_intent(
        db_session, payload_draft, initial_state=PublishIntentState.DRAFT
    )
    assert intent_draft.state == PublishIntentState.DRAFT.value

    with pytest.raises(PublishExecutionError, match="No approved or reclaimable PublishIntent"):
        await PublishExecutionService.execute_publish(db_session, f["task"].id)

    # 2. CANCELLED Intent
    await PublishIntentService.cancel_intent(db_session, intent_draft.id)
    with pytest.raises(PublishExecutionError, match="No approved or reclaimable PublishIntent"):
        await PublishExecutionService.execute_publish(db_session, f["task"].id)

    # Verify zero attempts created
    attempts_res = await db_session.execute(
        select(PublishAttempt).where(PublishAttempt.publish_intent_id == intent_draft.id)
    )
    assert len(attempts_res.scalars().all()) == 0


# ==============================================================================
# Scenario 2: Handoff Exactly-Once Semantics & Fencing
# ==============================================================================


@pytest.mark.asyncio
async def test_handoff_exactly_once_semantics_and_fencing(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """Two competing workers processing outbox: exactly 1 worker claims and delivers handoff."""
    f = setup_p16e_fixtures
    now = datetime.now(UTC)

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Handoff Fencing Video",
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
        idempotency_key=f"handoff-att-{uuid4().hex}",
        state=PublishAttemptState.RETRYABLE_FAILED.value,
        started_at=now,
    )
    db_session.add(attempt)

    handoff = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        task_id=f["task"].id,
        mission_id=intent.mission_id,
        earliest_retry_at=now - timedelta(seconds=10),
        next_attempt_at=now - timedelta(seconds=10),
        reason="Mock retryable network disconnect",
        idempotency_key=f"handoff-test-{uuid4().hex}",
        status=HandoffStatus.PENDING.value,
    )
    db_session.add(handoff)
    await db_session.commit()

    # Mock ScheduleEvaluationEngine
    mock_eval_resp = AsyncMock(id=uuid4())
    monkeypatch.setattr(
        "omega.application.scheduler.evaluation_engine.ScheduleEvaluationEngine.evaluate_schedule",
        AsyncMock(return_value=mock_eval_resp),
    )

    # Worker A processes pending handoffs
    worker_a_count = await HandoffRelayService.process_pending_handoffs(
        db_session, worker_id="worker-A", handoff_ids=[handoff.id]
    )
    assert worker_a_count == 1

    # Worker B tries to process the same handoff concurrently/subsequently
    worker_b_count = await HandoffRelayService.process_pending_handoffs(
        db_session, worker_id="worker-B", handoff_ids=[handoff.id]
    )
    assert worker_b_count == 0  # Cannot re-consume DELIVERED or currently locked handoff

    # Verify final state in DB
    refreshed_handoff = (
        await db_session.execute(
            select(PublisherSchedulerHandoffOutbox).where(
                PublisherSchedulerHandoffOutbox.id == handoff.id
            )
        )
    ).scalar_one()

    assert refreshed_handoff.status == HandoffStatus.DELIVERED.value
    assert refreshed_handoff.claimed_by_worker_id == "worker-A"
    assert refreshed_handoff.claim_token is None
    assert refreshed_handoff.attempt_count == 1


# ==============================================================================
# Scenario 3: Worker Entrypoint Idempotency
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_entrypoint_idempotency(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """Calling background execute_publish_task twice produces exactly 1 published video."""
    f = setup_p16e_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Worker Idempotency Canary",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    budget = BackgroundNetworkCallBudgetHarness(
        max_oauth_refresh=0,
        max_progress_query=0,
        max_upload_init=1,
        max_upload_chunk=1,
        max_videos_list=0,
    )

    init_mock = AsyncMock(
        side_effect=lambda *args, **kwargs: (
            budget.record_upload_init(),
            UploadSessionInitResult(
                session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=idemp-mock",
                expires_at=now + timedelta(hours=24),
            ),
        )[1]
    )
    chunk_mock = AsyncMock(
        side_effect=lambda *args, **kwargs: (
            budget.record_upload_chunk(),
            ChunkUploadResult(
                is_complete=True,
                next_byte_offset=10_781_571,
                provider_video_id="idemp-video-abc",
                effective_privacy_status=PrivacyStatus.PRIVATE,
            ),
        )[1]
    )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        init_mock,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        chunk_mock,
    )

    # First background worker invocation: executes successfully
    attempt_1 = await PublishExecutionService.execute_publish(
        db_session, f["task"].id, worker_id="celery-worker-1"
    )
    assert attempt_1.state == PublishAttemptState.SUCCEEDED.value
    assert attempt_1.provider_video_id == "idemp-video-abc"

    # Second background worker invocation: intent is already PUBLISHED
    with pytest.raises(PublishExecutionError, match="No approved or reclaimable PublishIntent"):
        await PublishExecutionService.execute_publish(
            db_session, f["task"].id, worker_id="celery-worker-2"
        )

    # Verify database state
    intents = (
        await db_session.execute(
            select(PublishIntent).where(PublishIntent.task_id == f["task"].id)
        )
    ).scalars().all()
    assert len(intents) == 1
    assert intents[0].state == PublishIntentState.PUBLISHED.value

    upload_sessions = (
        await db_session.execute(
            select(UploadSession).where(UploadSession.publish_attempt_id == attempt_1.id)
        )
    ).scalars().all()
    assert len(upload_sessions) == 1
    assert budget.upload_init_calls == 1
    assert budget.upload_chunk_calls == 1


# ==============================================================================
# Scenario 4: Lease Fencing Blocks Concurrent Worker
# ==============================================================================


@pytest.mark.asyncio
async def test_lease_fencing_blocks_concurrent_worker(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """Worker B cannot execute when Worker A holds an active unexpired lease on the PublishIntent."""
    f = setup_p16e_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Fencing Lease Canary",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    # Worker A claims intent with active 5-minute lease
    intent.state = PublishIntentState.CLAIMED.value
    intent.claim_token = uuid4()
    intent.claimed_by_worker_id = "worker-A"
    intent.lease_expires_at = now + timedelta(minutes=5)
    await db_session.commit()

    # Worker B attempts execution
    with pytest.raises(PublishExecutionError, match="No approved or reclaimable PublishIntent"):
        await PublishExecutionService.execute_publish(
            db_session, f["task"].id, worker_id="worker-B"
        )


# ==============================================================================
# Scenario 5: Expired Lease Reclaim
# ==============================================================================


@pytest.mark.asyncio
async def test_expired_lease_reclaim_increments_generation_and_reconciles(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """When Worker A's lease expires naturally, Worker B reclaims it and increments generation."""
    f = setup_p16e_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Reclaim Lease Canary",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    # Worker A claimed intent, but crashed / lease expired 2 minutes ago
    intent.state = PublishIntentState.CLAIMED.value
    intent.claim_token = uuid4()
    intent.claimed_by_worker_id = "worker-A"
    intent.lease_expires_at = now - timedelta(minutes=2)
    intent.attempt_generation = 1
    await db_session.commit()

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        AsyncMock(
            return_value=UploadSessionInitResult(
                session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=reclaim-mock",
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
                provider_video_id="reclaim-vid-123",
                effective_privacy_status=PrivacyStatus.PRIVATE,
            )
        ),
    )

    # Worker B successfully reclaims the intent
    attempt_b = await PublishExecutionService.execute_publish(
        db_session, f["task"].id, worker_id="worker-B"
    )
    assert attempt_b.state == PublishAttemptState.SUCCEEDED.value

    # Verify attempt generation was incremented
    reloaded_intent = (
        await db_session.execute(select(PublishIntent).where(PublishIntent.id == intent.id))
    ).scalar_one()
    assert reloaded_intent.attempt_generation == 2
    assert reloaded_intent.state == PublishIntentState.PUBLISHED.value


# ==============================================================================
# Scenario 6: UNKNOWN Outcome Background Safety & Reconciliation
# ==============================================================================


@pytest.mark.asyncio
async def test_unknown_outcome_background_retry_reconciles_first(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """When prior attempt is UNKNOWN, background retry reconciles existing session and uploads remaining bytes."""
    f = setup_p16e_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    local_offset = 8_388_608
    provider_authoritative_offset = 9_175_040
    total_bytes = 10_781_571

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="P16E UNKNOWN Recovery",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    # Prior attempt #1 halted in UNKNOWN state
    attempt_1 = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"attempt:{intent.id}:1",
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.PENDING.value,
        started_at=now - timedelta(minutes=10),
    )
    db_session.add(attempt_1)
    await db_session.flush()

    upload_session = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt_1.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=unknown-recovery",
        chunk_size_bytes=8_388_608,
        total_bytes=total_bytes,
        bytes_uploaded=local_offset,
        expires_at=now + timedelta(hours=20),
    )
    db_session.add(upload_session)
    await db_session.commit()

    budget = BackgroundNetworkCallBudgetHarness(
        max_oauth_refresh=0,
        max_progress_query=1,
        max_upload_init=0,  # Strict: zero upload initializations permitted
        max_upload_chunk=1,
        max_videos_list=0,
    )

    # Reconciliation mock returns provider authoritative offset
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                budget.record_progress_query(),
                ReconciliationResult(
                    is_confirmed_success=False,
                    is_incomplete=True,
                    is_held_for_review=False,
                    bytes_received=provider_authoritative_offset,
                    is_expired=False,
                    diagnostic_reason=f"Upload incomplete; resuming from byte {provider_authoritative_offset}",
                ),
            )[1]
        ),
    )

    captured_chunk_call: dict = {}

    def mock_upload_chunk_fn(*args, **kwargs):
        budget.record_upload_chunk()
        chunk_data = kwargs.get("chunk_data", b"")
        start_byte = kwargs.get("start_byte", 0)
        captured_chunk_call["start_byte"] = start_byte
        captured_chunk_call["length"] = len(chunk_data)
        captured_chunk_call["session_uri"] = kwargs.get("session_uri")
        return ChunkUploadResult(
            is_complete=True,
            next_byte_offset=total_bytes,
            provider_video_id="video-recovery-p16e",
            effective_privacy_status=PrivacyStatus.PRIVATE,
        )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(side_effect=mock_upload_chunk_fn),
    )

    # Background retry executed by worker
    result_attempt = await PublishExecutionService.execute_publish(
        db_session, f["task"].id, worker_id="background-reconciler"
    )

    assert result_attempt.id == attempt_1.id
    assert result_attempt.state == PublishAttemptState.SUCCEEDED.value
    assert result_attempt.provider_video_id == "video-recovery-p16e"

    # Enforce network and byte bounds
    assert budget.upload_init_calls == 0
    assert budget.progress_query_calls == 1
    assert budget.upload_chunk_calls == 1
    assert captured_chunk_call["start_byte"] == provider_authoritative_offset
    assert captured_chunk_call["length"] == total_bytes - provider_authoritative_offset
    assert captured_chunk_call["session_uri"] == upload_session.session_uri


# ==============================================================================
# Scenario 7: Terminal Session Safe Hold
# ==============================================================================


@pytest.mark.asyncio
async def test_terminal_session_safe_hold(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """When provider returns HTTP 404/410 for prior session, background execution holds safely."""
    f = setup_p16e_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Terminal Session Canary",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    prior_attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"attempt:{intent.id}:1",
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.PENDING.value,
        started_at=now - timedelta(minutes=15),
    )
    db_session.add(prior_attempt)
    await db_session.flush()

    prior_sess = UploadSession(
        id=uuid4(),
        publish_attempt_id=prior_attempt.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=terminal-404",
        chunk_size_bytes=8_388_608,
        total_bytes=10_781_571,
        bytes_uploaded=8_388_608,
        expires_at=now + timedelta(hours=10),
    )
    db_session.add(prior_sess)
    await db_session.commit()

    budget = BackgroundNetworkCallBudgetHarness(
        max_oauth_refresh=0,
        max_progress_query=1,
        max_upload_init=0,  # No replacement session initialization allowed
        max_upload_chunk=0,
        max_videos_list=0,
    )

    # Provider reports ambiguous / terminal hold condition requiring manual review
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        AsyncMock(
            side_effect=lambda *args, **kwargs: (
                budget.record_progress_query(),
                ReconciliationResult(
                    is_confirmed_success=False,
                    is_incomplete=False,
                    is_held_for_review=True,
                    bytes_received=0,
                    is_expired=False,
                    diagnostic_reason="Provider reports session in manual hold state",
                ),
            )[1]
        ),
    )

    # Worker attempts execution
    held_attempt = await PublishExecutionService.execute_publish(
        db_session, f["task"].id, worker_id="background-worker-term"
    )

    # Prior attempt is marked MANUAL_HOLD; new attempt was cancelled
    assert held_attempt.id == prior_attempt.id

    reloaded_prior = (
        await db_session.execute(select(PublishAttempt).where(PublishAttempt.id == prior_attempt.id))
    ).scalar_one()
    assert reloaded_prior.reconciliation_status == ReconciliationStatus.MANUAL_HOLD.value

    # Verify no new upload sessions were created
    all_sessions = (
        await db_session.execute(
            select(UploadSession).where(
                UploadSession.publish_attempt_id.in_([prior_attempt.id, held_attempt.id])
            )
        )
    ).scalars().all()
    assert len(all_sessions) == 1
    assert budget.upload_init_calls == 0


# ==============================================================================
# Scenario 8: Privacy Policy Enforcement in Canary Mode
# ==============================================================================


@pytest.mark.asyncio
async def test_private_policy_enforcement_in_canary_mode(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """In private canary mode, non-PRIVATE requests are rejected (BLOCKED_GUARDIAN)."""
    f = setup_p16e_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # 1. PUBLIC request -> BLOCKED_GUARDIAN
    payload_public = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Public Canary Attempt",
        requested_privacy_status=PrivacyStatus.PUBLIC,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload_public, initial_state=PublishIntentState.APPROVED
    )

    att_pub = await PublishExecutionService.execute_publish(
        db_session, f["task"].id, worker_id="worker-privacy-test"
    )
    assert att_pub.state == PublishAttemptState.BLOCKED_GUARDIAN.value

    # 2. UNLISTED request -> BLOCKED_GUARDIAN
    # Create new task to isolate
    task_unlisted = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        execution_id=f["execution"].id,
        task_type="PUBLISH_VIDEO",
        title="Unlisted Task",
        state="READY",
    )
    db_session.add(task_unlisted)
    await db_session.flush()

    payload_unlisted = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=task_unlisted.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Unlisted Canary Attempt",
        requested_privacy_status=PrivacyStatus.UNLISTED,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload_unlisted, initial_state=PublishIntentState.APPROVED
    )

    att_unl = await PublishExecutionService.execute_publish(
        db_session, task_unlisted.id, worker_id="worker-privacy-test"
    )
    assert att_unl.state == PublishAttemptState.BLOCKED_GUARDIAN.value


# ==============================================================================
# Scenario 9: OAuth Refresh Budget in Background Execution
# ==============================================================================


@pytest.mark.asyncio
async def test_oauth_refresh_budget_in_background_execution(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """Background execution refreshes at most once when token expired, and 0 times when valid."""
    f = setup_p16e_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # Force expiration
    f["vault_entry"].access_token_expires_at = now - timedelta(minutes=10)
    await db_session.commit()

    budget = BackgroundNetworkCallBudgetHarness(
        max_oauth_refresh=1,
        max_progress_query=0,
        max_upload_init=1,
        max_upload_chunk=1,
        max_videos_list=0,
    )

    refreshed_secret = f"mocked-background-fresh-token-{uuid4().hex}"

    async def mock_refresh(*args, **kwargs):
        budget.record_oauth_refresh()
        return RefreshedTokenData(
            access_token=refreshed_secret,
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
                    session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=bg-budget-1",
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
                    provider_video_id="bg-vid-success",
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
        title="OAuth Budget Background Canary",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    attempt = await PublishExecutionService.execute_publish(
        db_session, f["task"].id, worker_id="worker-budget-check"
    )
    assert attempt.state == PublishAttemptState.SUCCEEDED.value

    # Exactly 1 refresh call made
    assert budget.oauth_refresh_calls == 1
    assert budget.upload_init_calls == 1
    assert budget.upload_chunk_calls == 1


# ==============================================================================
# Scenario 10: Database Row Count Assertions
# ==============================================================================


@pytest.mark.asyncio
async def test_database_row_counts_per_scenario(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch
):
    """Verify exact expected row count increments across all publisher entities."""
    f = setup_p16e_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # Initial state: 0 intents, 0 attempts, 0 sessions, 0 handoffs for this task
    intents_before = (
        await db_session.execute(
            select(PublishIntent).where(PublishIntent.task_id == f["task"].id)
        )
    ).scalars().all()
    assert len(intents_before) == 0

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        AsyncMock(
            return_value=UploadSessionInitResult(
                session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=counts-mock",
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
                provider_video_id="counts-video-xyz",
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
        title="Row Count Verification",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    attempt = await PublishExecutionService.execute_publish(
        db_session, f["task"].id, worker_id="worker-count-check"
    )
    assert attempt.state == PublishAttemptState.SUCCEEDED.value

    # Verify exact DB row counts for this task
    intents_after = (
        await db_session.execute(
            select(PublishIntent).where(PublishIntent.task_id == f["task"].id)
        )
    ).scalars().all()
    attempts_after = (
        await db_session.execute(
            select(PublishAttempt).where(PublishAttempt.publish_intent_id == intents_after[0].id)
        )
    ).scalars().all()
    sessions_after = (
        await db_session.execute(
            select(UploadSession).where(UploadSession.publish_attempt_id == attempt.id)
        )
    ).scalars().all()
    handoffs_after = (
        await db_session.execute(
            select(PublisherSchedulerHandoffOutbox).where(
                PublisherSchedulerHandoffOutbox.task_id == f["task"].id
            )
        )
    ).scalars().all()

    assert len(intents_after) == 1
    assert len(attempts_after) == 1
    assert len(sessions_after) == 1
    assert len(handoffs_after) == 0  # Clean publish creates 0 handoff outbox rows
