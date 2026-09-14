"""P17-E4 offline contracts for the canonical scheduled private canary path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.adapters.base import (
    ChunkUploadResult,
    UploadSessionInitResult,
)
from omega.application.publisher.canonical_canary_service import (
    OMEGA_CANARY_CHANNEL_ID,
    OMEGA_CANARY_DISPLAY_NAME,
    OMEGA_CANARY_PLATFORM_ACCOUNT_ID,
    OMEGA_CANARY_YOUTUBE_CHANNEL_ID,
    CanonicalCanaryService,
)
from omega.application.publisher.publish_service import (
    PublishExecutionError,
    PublishExecutionService,
)
from omega.application.scheduler.outbox_relay import OutboxRelayService
from omega.application.scheduler.sweep_service import SchedulerSweepService
from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import PrivacyStatus, PublishAttemptState, PublishIntentState
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
    SchedulerDispatchOutbox,
    ScheduleReservation,
    Task,
    UploadSession,
)
from omega.infrastructure.vault import get_credential_vault
from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def canonical_canary_authorities(db_session: AsyncSession):
    """Create only the fixed target account and prerequisite artifact authorities."""
    now = datetime.now(UTC)
    profile = NetworkProfile(
        id=uuid4(),
        name=f"P17-E4-{uuid4().hex[:8]}",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(profile)
    await db_session.flush()
    db_session.add(
        NetworkRoute(
            id=uuid4(),
            profile_id=profile.id,
            name="Offline mocked provider route",
            route_type="DIRECT",
            allowed_service_categories=["YOUTUBE_API", "GENERAL_HTTP"],
            tls_verify=True,
            config_version=1,
            config_checksum="p17e4-offline-route",
            created_at=now,
            updated_at=now,
        )
    )

    channel = Channel(
        id=OMEGA_CANARY_CHANNEL_ID,
        slug="omega-canonical-canary",
        name="OMEGA",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)
    artifact = await create_artifact_with_ancestry(
        db_session, channel.id, "e4" * 32
    )
    artifact.file_size_bytes = 128_847

    account = PlatformAccount(
        id=OMEGA_CANARY_PLATFORM_ACCOUNT_ID,
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name=OMEGA_CANARY_DISPLAY_NAME,
        external_account_id=OMEGA_CANARY_YOUTUBE_CHANNEL_ID,
        status="ACTIVE",
        scopes=["youtube.upload", "youtube.readonly"],
        created_at=now,
        updated_at=now,
    )
    db_session.add(account)
    await db_session.flush()

    vault = get_credential_vault()
    encrypted_access, key_version = vault.encrypt("offline-e4-access-token")
    encrypted_refresh, _ = vault.encrypt("offline-e4-refresh-token")
    db_session.add(
        CredentialVault(
            id=uuid4(),
            platform_account_id=account.id,
            encrypted_access_token=encrypted_access,
            access_token_expires_at=now + timedelta(hours=2),
            encrypted_refresh_token=encrypted_refresh,
            token_type="Bearer",
            key_version=key_version,
        )
    )
    await db_session.commit()
    return {"channel": channel, "account": account, "artifact": artifact}


async def construct_due_graph(
    db_session: AsyncSession, canonical_canary_authorities
):
    """Build through production services, then simulate completed upstream DAG stages."""
    now = datetime.now(UTC)
    graph = await CanonicalCanaryService.construct(
        db_session,
        media_artifact_id=canonical_canary_authorities["artifact"].id,
        scheduled_start_at=now,
        title="OMEGA P17-E4 canonical private canary",
        description="Offline structural proof only",
    )
    mission = await db_session.get(Mission, graph.mission_id)
    execution = await db_session.get(MissionExecution, graph.execution_id)
    task = await db_session.get(Task, graph.task_id)
    assert mission is not None
    assert execution is not None
    assert task is not None

    # The canary reuses a production-planned task. This test state represents
    # normal completion of its six upstream DAG stages; no orphan task is made.
    mission.state = "RUNNING"
    execution.state = "RUNNING"
    task.state = TaskState.READY.value
    await db_session.commit()
    return graph, now


def install_provider_mocks(monkeypatch):
    init_mock = AsyncMock(
        return_value=UploadSessionInitResult(
            session_uri=(
                "https://www.googleapis.com/upload/youtube/v3/videos"
                "?upload_id=p17e4-offline"
            ),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    chunk_mock = AsyncMock(
        return_value=ChunkUploadResult(
            is_complete=True,
            next_byte_offset=128_847,
            provider_video_id="p17e4-offline-provider-video",
            effective_privacy_status=PrivacyStatus.PRIVATE,
        )
    )
    refresh_mock = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.refresh_access_token",
        refresh_mock,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        init_mock,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        chunk_mock,
    )
    return refresh_mock, init_mock, chunk_mock


@pytest.mark.asyncio
async def test_canonical_scheduled_private_canary_offline_end_to_end_and_duplicate_safe(
    db_session: AsyncSession, canonical_canary_authorities, monkeypatch
):
    """Real services produce one canonical graph and one mocked provider execution."""
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")
    graph, now = await construct_due_graph(db_session, canonical_canary_authorities)

    mission = await db_session.get(Mission, graph.mission_id)
    execution = await db_session.get(MissionExecution, graph.execution_id)
    task = await db_session.get(Task, graph.task_id)
    intent = await db_session.get(PublishIntent, graph.publish_intent_id)
    reservation = await db_session.get(ScheduleReservation, graph.reservation_id)
    decision = await db_session.get(ScheduleDecision, graph.schedule_decision_id)
    assert mission is not None and mission.channel_id == OMEGA_CANARY_CHANNEL_ID
    assert execution is not None and execution.mission_id == mission.id
    assert task is not None and task.execution_id == execution.id
    assert task.mission_id == mission.id and task.task_type == "publish"
    assert intent is not None and intent.task_id == task.id
    assert intent.state == PublishIntentState.APPROVED.value
    assert intent.requested_privacy_status == PrivacyStatus.PRIVATE.value
    assert reservation is not None and reservation.target_id == intent.id
    assert decision is not None and decision.task_id == task.id

    sweep = await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now, reservation_ids=[reservation.id]
    )
    assert sweep == {"claimed": 1, "dispatched": 1, "rejected": 0}
    outboxes = list(
        (
            await db_session.execute(
                select(SchedulerDispatchOutbox).where(
                    SchedulerDispatchOutbox.reservation_id == reservation.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(outboxes) == 1
    outbox = outboxes[0]
    assert outbox.task_id == task.id
    assert outbox.celery_args == {"args": [str(task.id)]}

    broker_send = MagicMock()
    monkeypatch.setattr(
        "omega.application.scheduler.outbox_relay.celery_app.send_task", broker_send
    )
    relay = await OutboxRelayService.process_outbox_batch(
        db_session, now=now, outbox_ids=[outbox.id]
    )
    assert relay == {"claimed": 1, "sent": 1, "retried": 0, "dead_letter": 0}
    broker_send.assert_called_once_with(
        "omega.publisher.execute_publish", args=[str(task.id)], kwargs={}
    )
    await db_session.refresh(outbox)
    assert outbox.status == DispatchOutboxStatus.SENT.value

    refresh_mock, init_mock, chunk_mock = install_provider_mocks(monkeypatch)
    attempt = await PublishExecutionService.execute_publish(
        db_session, task.id, worker_id="p17e4-offline-worker"
    )
    assert attempt.state == PublishAttemptState.SUCCEEDED.value
    assert refresh_mock.await_count == 0
    assert init_mock.await_count == 1
    assert chunk_mock.await_count == 1

    intent = await db_session.get(PublishIntent, graph.publish_intent_id)
    task = await db_session.get(Task, graph.task_id)
    assert intent is not None
    assert task is not None
    assert intent.state == PublishIntentState.PUBLISHED.value
    assert task.state == TaskState.SUCCEEDED.value
    assert (
        await db_session.scalar(
            select(func.count(PublishAttempt.id)).where(
                PublishAttempt.publish_intent_id == intent.id
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(UploadSession.id)).join(
                PublishAttempt, UploadSession.publish_attempt_id == PublishAttempt.id
            )
        )
        == 1
    )
    terminal = list(
        (
            await db_session.execute(
                select(DurableDispatchIntent).where(
                    DurableDispatchIntent.task_name == "omega.orchestrator.evaluate",
                    DurableDispatchIntent.mission_task_id == task.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(terminal) == 1
    assert terminal[0].mission_execution_id == execution.id

    with pytest.raises(PublishExecutionError, match="No approved or reclaimable"):
        await PublishExecutionService.execute_publish(
            db_session, task.id, worker_id="p17e4-duplicate-delivery"
        )
    assert init_mock.await_count == 1
    assert chunk_mock.await_count == 1

    recovery = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session,
        timeout_seconds=300,
        now=now + timedelta(seconds=301),
        reservation_ids=[reservation.id],
    )
    assert recovery["consumed"] == 1
    reservation = await db_session.get(ScheduleReservation, graph.reservation_id)
    assert reservation is not None
    assert reservation.state == ReservationState.CONSUMED.value


@pytest.mark.asyncio
async def test_null_execution_is_rejected_before_attempt_oauth_session_or_provider(
    db_session: AsyncSession, canonical_canary_authorities, monkeypatch
):
    """P17-E3's null-execution gate remains before every external side effect."""
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    graph, now = await construct_due_graph(db_session, canonical_canary_authorities)
    task = await db_session.get(Task, graph.task_id)
    assert task is not None
    task.execution_id = None
    await db_session.commit()
    await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now, reservation_ids=[graph.reservation_id]
    )
    refresh_mock, init_mock, chunk_mock = install_provider_mocks(monkeypatch)

    with pytest.raises(PublishExecutionError, match="requires MissionExecution identity"):
        await PublishExecutionService.execute_publish(db_session, task.id)

    assert refresh_mock.await_count == 0
    assert init_mock.await_count == 0
    assert chunk_mock.await_count == 0
    assert await db_session.scalar(select(func.count(PublishAttempt.id))) == 0
    assert await db_session.scalar(select(func.count(UploadSession.id))) == 0


@pytest.mark.asyncio
async def test_inconsistent_scheduler_graph_fails_before_provider_work(
    db_session: AsyncSession, canonical_canary_authorities, monkeypatch
):
    """A canary outbox that does not belong to its task fails closed pre-provider."""
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    graph, now = await construct_due_graph(db_session, canonical_canary_authorities)
    await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now, reservation_ids=[graph.reservation_id]
    )
    outbox = (
        await db_session.execute(
            select(SchedulerDispatchOutbox).where(
                SchedulerDispatchOutbox.reservation_id == graph.reservation_id
            )
        )
    ).scalar_one()
    other_task = (
        await db_session.execute(
            select(Task).where(
                Task.mission_id == graph.mission_id,
                Task.id != graph.task_id,
            )
        )
    ).scalars().first()
    assert other_task is not None
    outbox.task_id = other_task.id
    await db_session.commit()
    refresh_mock, init_mock, chunk_mock = install_provider_mocks(monkeypatch)

    with pytest.raises(PublishExecutionError, match="scheduler outbox identity is invalid"):
        await PublishExecutionService.execute_publish(db_session, graph.task_id)

    assert refresh_mock.await_count == 0
    assert init_mock.await_count == 0
    assert chunk_mock.await_count == 0
    assert await db_session.scalar(select(func.count(PublishAttempt.id))) == 0
    assert await db_session.scalar(select(func.count(UploadSession.id))) == 0
