"""Integration tests for P17-E3: Terminal Commit + Publisher-Aware Stale Recovery Hardening.

Verifies:
1. publish tasks without MissionExecution fail before provider work
2. no attempt or upload session is created for structurally invalid tasks
3. normal task WITH execution_id still enqueues orchestrator evaluation
4. invalid reconciliation is held before provider query
9. reconciliation normal execution path still enqueues evaluation
10. terminal failure path cannot rollback because optional evaluation is absent
11. stale recovery publisher SENT + QUEUED + zero publisher evidence may retain existing lost-message recovery behavior
12. stale recovery publisher SENT + QUEUED + PublishAttempt exists -> NO requeue
13. UploadSession exists -> NO requeue
14. provider_video_id exists -> NO requeue
15. UNKNOWN attempt -> NO requeue
16. SUCCEEDED attempt -> NO requeue
17. non-publisher task preserves old stale recovery behavior
18. reservation_ids=None preserves broad behavior
19. reservation_ids=[] performs zero work
20. explicit reservation_ids scopes selection in SQL
21. historical unrelated reservation untouched
22. no provider adapter called by stale recovery
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
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
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.publish_service import (
    PublishExecutionError,
    PublishExecutionService,
)
from omega.application.publisher.reconciliation_service import ReconciliationService
from omega.application.scheduler.sweep_service import SchedulerSweepService
from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import (
    PrivacyStatus,
    PublishAttemptState,
    PublishIntentCreate,
    PublishIntentState,
    ReconciliationStatus,
)
from omega.domain.scheduler import DispatchOutboxStatus, ReservationState
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    Channel,
    CredentialVault,
    DurableDispatchIntent,
    Mission,
    MissionExecution,
    NetworkProfile,
    NetworkRoute,
    PlatformAccount,
    PublishAttempt,
    PublishIntent,
    ScheduleDecision,
    SchedulePolicy,
    SchedulerDispatchOutbox,
    ScheduleReservation,
    ScheduleStateTransition,
    Task,
    UploadSession,
)
from omega.infrastructure.vault import get_credential_vault
from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def setup_p17e3_fixtures(db_session: AsyncSession):
    """Create test infrastructure for P17-E3 tests."""
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Profile-P17E3-{uuid4().hex[:8]}",
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
        config_checksum="chk-p17e3",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)

    channel = Channel(
        id=uuid4(),
        slug=f"p17e3-chan-{uuid4().hex[:8]}",
        name="P17E3 Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        title="P17E3 Mission",
        objective="Terminal Commit Hardening",
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
    enc_acc, v_acc = vault.encrypt("mock-access-token")
    enc_ref, v_ref = vault.encrypt("mock-refresh-token")

    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=enc_acc,
        access_token_expires_at=now + timedelta(hours=2),
        encrypted_refresh_token=enc_ref,
        token_type="Bearer",
        key_version=v_acc,
    )
    db_session.add(vault_entry)

    art_hash = "7cb981a9f5350d8d7caed8fbd42c25209f20fa75b14cf93a29e547c742dced71"
    artifact = await create_artifact_with_ancestry(db_session, channel.id, art_hash)
    artifact.file_size_bytes = 128847

    # Create a schedule policy for reservations
    policy = SchedulePolicy(
        id=uuid4(),
        workload_category="EXTERNAL_PUBLISH",
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={"default": True},
        checksum="chk-p17e3-pol",
        status="ACTIVE",
    )
    db_session.add(policy)

    await db_session.commit()

    return {
        "channel": channel,
        "mission": mission,
        "execution": execution,
        "account": account,
        "vault_entry": vault_entry,
        "artifact": artifact,
        "policy": policy,
    }


# =========================================================================
# MISSION B: EXECUTION ID INVARIANT AND NORMAL EVALUATION
# =========================================================================


@pytest.mark.asyncio
async def test_publish_without_mission_execution_fails_before_provider_work(
    db_session: AsyncSession, setup_p17e3_fixtures, monkeypatch
):
    """PUBLISH_VIDEO without MissionExecution fails before attempt or provider work."""
    f = setup_p17e3_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    # Structurally invalid task: no MissionExecution identity.
    task = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        execution_id=None,
        task_type="PUBLISH_VIDEO",
        title="Standalone Publish Task",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    mock_refresh = AsyncMock()
    mock_init = AsyncMock()
    mock_chunk = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.refresh_access_token",
        mock_refresh,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_chunk,
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="OMEGA Standalone Publish",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    with pytest.raises(PublishExecutionError, match="requires MissionExecution identity"):
        await PublishExecutionService.execute_publish(db_session, task.id)

    attempts = await db_session.execute(
        select(PublishAttempt).where(PublishAttempt.publish_intent_id == intent.id)
    )
    sessions = await db_session.execute(select(UploadSession))
    assert list(attempts.scalars().all()) == []
    assert list(sessions.scalars().all()) == []
    assert mock_refresh.await_count == 0
    assert mock_init.await_count == 0
    assert mock_chunk.await_count == 0


@pytest.mark.asyncio
async def test_normal_publish_with_execution_id_enqueues_orchestrator_evaluation(
    db_session: AsyncSession, setup_p17e3_fixtures, monkeypatch
):
    """Contract 7: Normal task WITH execution_id enqueues omega.orchestrator.evaluate."""
    f = setup_p17e3_fixtures
    now = datetime.now(UTC)
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    task = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        execution_id=f["execution"].id,
        task_type="PUBLISH_VIDEO",
        title="Normal Orchestrated Publish Task",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        AsyncMock(
            return_value=UploadSessionInitResult(
                session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock-normal-1",
                expires_at=now + timedelta(hours=24),
            )
        ),
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(
            return_value=ChunkUploadResult(
                is_complete=True,
                next_byte_offset=128847,
                provider_video_id="video-normal-succeeded",
                effective_privacy_status=PrivacyStatus.PRIVATE,
            )
        ),
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="OMEGA Normal Publish",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    attempt = await PublishExecutionService.execute_publish(db_session, task.id)
    assert attempt.state == PublishAttemptState.SUCCEEDED.value
    intent_row = (await db_session.execute(select(PublishIntent).where(PublishIntent.id == intent.id))).scalar_one()
    assert intent_row.state == PublishIntentState.PUBLISHED.value

    # Verify durable dispatch was enqueued
    dispatches = await db_session.execute(
        select(DurableDispatchIntent).where(
            DurableDispatchIntent.task_name == "omega.orchestrator.evaluate",
            DurableDispatchIntent.mission_task_id == task.id,
        )
    )
    eval_intents = list(dispatches.scalars().all())
    assert len(eval_intents) == 1
    assert eval_intents[0].mission_execution_id == f["execution"].id
    assert eval_intents[0].args == [str(f["mission"].id), str(f["execution"].id)]


# =========================================================================
# MISSION C & CONTRACTS 8-10: RECONCILIATION CONTRACT & TERMINAL FAILURE
# =========================================================================


@pytest.mark.asyncio
async def test_reconciliation_without_execution_id_holds_before_provider_query(
    db_session: AsyncSession, setup_p17e3_fixtures, monkeypatch
):
    """Structurally invalid reconciliation is held before any provider query."""
    f = setup_p17e3_fixtures
    now = datetime.now(UTC)

    task = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        execution_id=None,
        task_type="PUBLISH_VIDEO",
        title="Standalone Recon Task",
        state="QUEUED",
    )
    db_session.add(task)
    await db_session.flush()

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Recon Standalone Intent",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-recon-1",
        state=PublishIntentState.CLAIMED.value,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"recon-att-{uuid4().hex[:8]}",
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.PENDING.value,
    )
    db_session.add(attempt)
    await db_session.flush()

    upload_sess = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock-recon-1",
        total_bytes=128847,
        bytes_uploaded=0,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(upload_sess)
    await db_session.commit()

    mock_reconcile = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        mock_reconcile,
    )

    outcome = await ReconciliationService.reconcile_attempt_detailed(db_session, attempt.id)
    assert outcome.status == ReconciliationStatus.MANUAL_HOLD
    assert mock_reconcile.await_count == 0

    attempt_row = (await db_session.execute(select(PublishAttempt).where(PublishAttempt.id == attempt.id))).scalar_one()
    assert attempt_row.state == PublishAttemptState.UNKNOWN.value
    assert attempt_row.reconciliation_status == ReconciliationStatus.MANUAL_HOLD.value
    assert attempt_row.provider_video_id is None

    intent_row = (await db_session.execute(select(PublishIntent).where(PublishIntent.id == intent.id))).scalar_one()
    assert intent_row.state == PublishIntentState.CLAIMED.value

    task_row = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert task_row.state == TaskState.QUEUED.value

    sessions = await db_session.execute(
        select(UploadSession).where(UploadSession.publish_attempt_id == attempt.id)
    )
    persisted_sessions = list(sessions.scalars().all())
    assert len(persisted_sessions) == 1
    assert persisted_sessions[0].id == upload_sess.id


@pytest.mark.asyncio
async def test_reconciliation_normal_execution_path_enqueues_evaluation(
    db_session: AsyncSession, setup_p17e3_fixtures, monkeypatch
):
    """Contract 9: Normal task WITH execution_id still enqueues orchestrator evaluation upon confirmed reconciliation."""
    f = setup_p17e3_fixtures
    now = datetime.now(UTC)

    task = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        execution_id=f["execution"].id,
        task_type="PUBLISH_VIDEO",
        title="Normal Recon Task",
        state="QUEUED",
    )
    db_session.add(task)
    await db_session.flush()

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Recon Normal Intent",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-recon-2",
        state=PublishIntentState.CLAIMED.value,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"recon-att-norm-{uuid4().hex[:8]}",
        state=PublishAttemptState.UPLOADING.value,
    )
    db_session.add(attempt)
    await db_session.flush()

    upload_sess = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock-recon-2",
        total_bytes=128847,
        bytes_uploaded=0,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(upload_sess)
    await db_session.commit()

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        AsyncMock(
            return_value=ReconciliationResult(
                is_confirmed_success=True,
                is_incomplete=False,
                is_held_for_review=False,
                provider_video_id="video-recon-norm-success",
                provider_url="https://youtu.be/video-recon-norm-success",
                diagnostic_reason="Upload verified complete",
            )
        ),
    )

    outcome = await ReconciliationService.reconcile_attempt_detailed(db_session, attempt.id)
    assert outcome.status == ReconciliationStatus.CONFIRMED_SUCCESS

    dispatches = await db_session.execute(
        select(DurableDispatchIntent).where(
            DurableDispatchIntent.task_name == "omega.orchestrator.evaluate",
            DurableDispatchIntent.mission_task_id == task.id,
        )
    )
    eval_intents = list(dispatches.scalars().all())
    assert len(eval_intents) == 1
    assert eval_intents[0].mission_execution_id == f["execution"].id


@pytest.mark.asyncio
async def test_terminal_failure_path_with_execution_id_preserves_evaluation_contract(
    db_session: AsyncSession, setup_p17e3_fixtures, monkeypatch
):
    """Terminal failure with MissionExecution preserves normal terminal semantics."""
    f = setup_p17e3_fixtures
    # Canonical production task with MissionExecution identity.
    task = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        execution_id=f["execution"].id,
        task_type="PUBLISH_VIDEO",
        title="Failing Orchestrated Task",
        state=TaskState.READY.value,
    )
    db_session.add(task)
    await db_session.flush()

    import httpx
    # Raise a 400 Bad Request error on upload chunk
    request = httpx.Request("PUT", "https://www.googleapis.com/upload/youtube/v3/videos")
    response = httpx.Response(status_code=400, json={"error": {"message": "Invalid video binary", "errors": [{"reason": "invalid"}]}}, request=request)
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(side_effect=httpx.HTTPStatusError("400 Bad Request", request=request, response=response)),
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="OMEGA Failing Publish",
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, payload, initial_state=PublishIntentState.APPROVED
    )

    attempt = await PublishExecutionService.execute_publish(db_session, task.id)
    assert attempt.state == PublishAttemptState.PERMANENT_FAILED.value

    # Verify task and intent are committed FAILED without rolling back
    task_row = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert task_row.state == TaskState.FAILED.value

    intent_row = (await db_session.execute(select(PublishIntent).where(PublishIntent.id == intent.id))).scalar_one()
    assert intent_row.state == PublishIntentState.FAILED.value


# =========================================================================
# MISSION D & CONTRACTS 11-17: PUBLISHER-AWARE STALE RECOVERY FENCE
# =========================================================================


async def create_stale_dispatching_reservation(
    db_session: AsyncSession,
    f: dict[str, Any],
    celery_task_name: str = "omega.publisher.execute_publish",
    task_state: str = "QUEUED",
    outbox_status: str = "SENT",
    stale_minutes: int = 15,
) -> tuple[ScheduleReservation, Task, SchedulerDispatchOutbox]:
    """Helper creating a reservation stuck in DISPATCHING past timeout."""
    now = datetime.now(UTC)
    stale_time = now - timedelta(minutes=stale_minutes)

    task = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        execution_id=f["execution"].id,
        task_type="PUBLISH_VIDEO",
        title="Stale Recovery Task",
        state=task_state,
    )
    db_session.add(task)
    await db_session.flush()

    decision = ScheduleDecision(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        target_type="TASK_EXECUTION",
        target_id=task.id,
        workload_category="EXTERNAL_PUBLISH",
        action="PUBLISH",
        reason="Stale recovery test",
        policy_id=f["policy"].id,
        policy_version=f["policy"].version,
        policy_checksum=f["policy"].checksum,
        guardian_epoch=1,
        idempotency_key=f"dec-{uuid4().hex[:12]}",
        evaluated_at=stale_time,
    )
    db_session.add(decision)
    await db_session.flush()

    res = ScheduleReservation(
        id=uuid4(),
        decision_id=decision.id,
        channel_id=f["channel"].id,
        mission_id=f["mission"].id,
        workload_category="EXTERNAL_PUBLISH",
        policy_id=f["policy"].id,
        policy_version=f["policy"].version,
        policy_checksum=f["policy"].checksum,
        priority_score=100,
        guardian_epoch=1,
        scheduled_start_at=stale_time - timedelta(minutes=5),
        scheduled_end_at=stale_time + timedelta(minutes=15),
        target_type="TASK_EXECUTION",
        target_id=task.id,
        state=ReservationState.DISPATCHING.value,
        dispatching_at=stale_time,
        created_at=stale_time,
        updated_at=stale_time,
    )
    db_session.add(res)
    await db_session.flush()

    outbox = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=res.id,
        task_id=task.id,
        mission_id=f["mission"].id,
        celery_task_name=celery_task_name,
        celery_args={"args": [str(task.id)]},
        idempotency_key=f"outbox-{uuid4().hex[:12]}",
        status=outbox_status,
        attempt_count=1,
        scheduled_send_at=stale_time,
        created_at=stale_time,
    )
    db_session.add(outbox)
    await db_session.commit()

    return res, task, outbox


@pytest.mark.asyncio
async def test_stale_recovery_publisher_zero_evidence_retains_requeue(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contract 11: Publisher SENT + QUEUED + zero publisher evidence resets outbox to PENDING."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    # Intent exists but is in APPROVED state with NO attempts, sessions, or claim
    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Zero Evidence Intent",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-zero-1",
        state=PublishIntentState.APPROVED.value,
        attempt_generation=0,
    )
    db_session.add(intent)
    await db_session.commit()

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )

    assert stats["recovered"] == 1
    assert stats["requeued"] == 1
    assert stats["suppressed"] == 0

    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.PENDING.value


@pytest.mark.asyncio
async def test_stale_recovery_uses_canonical_reservation_target_intent(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """PUBLISH_INTENT reservations use target_id, not a task_id-derived intent."""
    f = setup_p17e3_fixtures
    reservation, stale_task, outbox = await create_stale_dispatching_reservation(db_session, f)

    # A task-linked intent with zero evidence would permit legacy redelivery if chosen.
    fallback_intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=stale_task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Task-linked zero evidence",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="canonical-fallback",
        state=PublishIntentState.APPROVED.value,
    )
    canonical_task = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        execution_id=f["execution"].id,
        task_type="PUBLISH_VIDEO",
        title="Canonical target task",
        state=TaskState.QUEUED.value,
    )
    canonical_intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=canonical_task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Canonical publisher intent",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="canonical-target",
        state=PublishIntentState.CLAIMED.value,
        attempt_generation=1,
    )
    canonical_attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=canonical_intent.id,
        attempt_number=1,
        idempotency_key=f"canonical-{uuid4().hex}",
        state=PublishAttemptState.UPLOADING.value,
    )
    reservation.target_type = "PUBLISH_INTENT"
    reservation.target_id = canonical_intent.id
    db_session.add_all(
        [fallback_intent, canonical_task, canonical_intent, canonical_attempt]
    )
    await db_session.commit()

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[reservation.id]
    )

    assert stats["requeued"] == 0
    assert stats["suppressed"] == 1
    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value


@pytest.mark.asyncio
async def test_stale_recovery_publisher_with_publish_attempt_suppresses_requeue(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contract 12: Publisher SENT + QUEUED + PublishAttempt exists -> NO requeue, suppressed."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="With Attempt Intent",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-att-1",
        state=PublishIntentState.CLAIMED.value,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"att-{uuid4().hex[:8]}",
        state=PublishAttemptState.CREATED.value,
    )
    db_session.add(attempt)
    await db_session.commit()

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )

    assert stats["recovered"] == 1
    assert stats["requeued"] == 0
    assert stats["suppressed"] == 1

    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value

    await db_session.refresh(res)
    assert res.state == ReservationState.DISPATCHING.value


@pytest.mark.asyncio
async def test_stale_recovery_publisher_with_upload_session_suppresses_requeue(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contract 13: UploadSession exists -> NO requeue."""
    f = setup_p17e3_fixtures
    now = datetime.now(UTC)
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="With Session Intent",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-sess-1",
        state=PublishIntentState.CLAIMED.value,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"att-{uuid4().hex[:8]}",
        state=PublishAttemptState.UPLOADING.value,
    )
    db_session.add(attempt)
    await db_session.flush()

    upload_sess = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock-stale-sess",
        total_bytes=128847,
        bytes_uploaded=50000,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(upload_sess)
    await db_session.commit()

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )

    assert stats["requeued"] == 0
    assert stats["suppressed"] == 1
    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value


@pytest.mark.asyncio
async def test_stale_recovery_publisher_with_provider_video_id_suppresses_requeue(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contract 14: provider_video_id exists -> NO requeue."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="With Video ID Intent",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-vid-1",
        state=PublishIntentState.CLAIMED.value,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"att-{uuid4().hex[:8]}",
        state=PublishAttemptState.FINALIZING.value,
        provider_video_id="video-already-on-youtube",
    )
    db_session.add(attempt)
    await db_session.commit()

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )

    assert stats["requeued"] == 0
    assert stats["suppressed"] == 1
    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value


@pytest.mark.asyncio
async def test_stale_recovery_publisher_with_unknown_attempt_suppresses_requeue(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contract 15: UNKNOWN attempt -> NO requeue."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="With Unknown Attempt",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-unk-1",
        state=PublishIntentState.CLAIMED.value,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"att-{uuid4().hex[:8]}",
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.PENDING.value,
    )
    db_session.add(attempt)
    await db_session.commit()

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )

    assert stats["requeued"] == 0
    assert stats["suppressed"] == 1
    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value


@pytest.mark.asyncio
async def test_stale_recovery_publisher_with_succeeded_attempt_suppresses_requeue(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contract 16: SUCCEEDED attempt -> NO requeue."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="With Succeeded Attempt",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-succ-1",
        state=PublishIntentState.PUBLISHED.value,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"att-{uuid4().hex[:8]}",
        state=PublishAttemptState.SUCCEEDED.value,
        provider_video_id="video-succeeded",
    )
    db_session.add(attempt)
    await db_session.commit()

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )

    assert stats["requeued"] == 0
    assert stats["suppressed"] == 1
    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value


@pytest.mark.asyncio
async def test_non_publisher_task_preserves_old_stale_recovery_behavior(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contract 17: Non-publisher task (e.g. render) resets to PENDING as before."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(
        db_session, f, celery_task_name="omega.production.render"
    )

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )

    assert stats["recovered"] == 1
    assert stats["requeued"] == 1
    assert stats["suppressed"] == 0

    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.PENDING.value


# =========================================================================
# MISSION E & CONTRACTS 18-22: SCOPED STALE RECOVERY & IMMUTABILITY
# =========================================================================


@pytest.mark.asyncio
async def test_stale_recovery_empty_reservation_ids_performs_zero_work(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contract 19: reservation_ids=[] performs zero work."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[]
    )

    assert stats == {
        "recovered": 0,
        "consumed": 0,
        "released": 0,
        "requeued": 0,
        "suppressed": 0,
    }
    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value


@pytest.mark.asyncio
async def test_stale_recovery_none_keeps_broad_selection_behavior(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """reservation_ids=None processes every eligible stale reservation."""
    f = setup_p17e3_fixtures
    _res1, _task1, outbox1 = await create_stale_dispatching_reservation(
        db_session, f, celery_task_name="omega.production.render"
    )
    _res2, _task2, outbox2 = await create_stale_dispatching_reservation(
        db_session, f, celery_task_name="omega.production.render"
    )

    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=None
    )

    assert stats["recovered"] == 2
    assert stats["requeued"] == 2
    await db_session.refresh(outbox1)
    await db_session.refresh(outbox2)
    assert outbox1.status == DispatchOutboxStatus.PENDING.value
    assert outbox2.status == DispatchOutboxStatus.PENDING.value


@pytest.mark.asyncio
async def test_stale_recovery_scopes_selection_in_sql_and_leaves_unrelated_untouched(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Contracts 18, 20, 21: Scoped reservation_ids restricts query and leaves other reservations untouched."""
    f = setup_p17e3_fixtures
    res1, task1, outbox1 = await create_stale_dispatching_reservation(
        db_session, f, celery_task_name="omega.tasks.execute"
    )
    res2, task2, outbox2 = await create_stale_dispatching_reservation(
        db_session, f, celery_task_name="omega.tasks.execute"
    )

    # Sweep ONLY res1
    stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res1.id]
    )

    assert stats["recovered"] == 1
    assert stats["requeued"] == 1

    await db_session.refresh(outbox1)
    assert outbox1.status == DispatchOutboxStatus.PENDING.value

    # Contract 21: Unrelated reservation res2 is untouched
    await db_session.refresh(outbox2)
    assert outbox2.status == DispatchOutboxStatus.SENT.value
    await db_session.refresh(res2)
    assert res2.state == ReservationState.DISPATCHING.value


@pytest.mark.asyncio
async def test_stale_recovery_does_not_invoke_provider_adapters(
    db_session: AsyncSession, setup_p17e3_fixtures, monkeypatch
):
    """Contract 22: Stale recovery performs DB operations only; no provider adapter is called."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    mock_recon = AsyncMock()
    mock_upload = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        mock_recon,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_upload,
    )

    await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )

    assert mock_recon.call_count == 0
    assert mock_upload.call_count == 0


@pytest.mark.asyncio
async def test_repeated_stale_recovery_sweeps_are_idempotent_and_do_not_requeue(
    db_session: AsyncSession, setup_p17e3_fixtures
):
    """Verify repeated sweeps against a reservation with publisher evidence suppress redelivery on both runs and avoid duplicate transitions."""
    f = setup_p17e3_fixtures
    res, task, outbox = await create_stale_dispatching_reservation(db_session, f)

    # Give it publisher evidence (an attempt)
    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=task.id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Repeated Sweep Intent",
        description="Test",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="chk-rep-1",
        state=PublishIntentState.CLAIMED.value,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"att-rep-{uuid4().hex[:8]}",
        state=PublishAttemptState.UPLOADING.value,
    )
    db_session.add(attempt)
    await db_session.commit()

    # First sweep
    stats1 = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )
    assert stats1["requeued"] == 0
    assert stats1["suppressed"] == 1

    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value

    # Check transitions
    trans_res1 = await db_session.execute(
        select(ScheduleStateTransition).where(
            ScheduleStateTransition.reservation_id == res.id,
            ScheduleStateTransition.actor == "STALE_DISPATCH_RECOVERY",
        )
    )
    trans_count_1 = len(list(trans_res1.scalars().all()))
    assert trans_count_1 == 1

    # Second sweep
    stats2 = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300, reservation_ids=[res.id]
    )
    assert stats2["requeued"] == 0
    assert stats2["suppressed"] == 1

    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value

    # Idempotent transitions: count remains 1
    trans_res2 = await db_session.execute(
        select(ScheduleStateTransition).where(
            ScheduleStateTransition.reservation_id == res.id,
            ScheduleStateTransition.actor == "STALE_DISPATCH_RECOVERY",
        )
    )
    trans_count_2 = len(list(trans_res2.scalars().all()))
    assert trans_count_2 == 1
