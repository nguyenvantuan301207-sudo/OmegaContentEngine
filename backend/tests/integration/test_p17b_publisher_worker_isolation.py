"""P17-B publisher queue isolation and redelivery safety evidence.

All provider interactions are mocked by the reused P16 safety scenarios. No broker or
provider network access is required.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.durable_dispatch import DurableDispatchService
from omega.application.guardian.engine import GuardianEngine
from omega.application.network.circuit_breaker import RouteCircuitBreaker
from omega.application.network.preflight import DEFAULT_POLICY_CONFIG
from omega.application.publisher.adapters.base import ChunkUploadResult, UploadSessionInitResult
from omega.application.publisher.calendar_service import PublishCalendarService
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.publish_service import PublishExecutionService
from omega.application.scheduler.sweep_service import SchedulerSweepService
from omega.domain.network import ServiceCategory, compute_config_checksum
from omega.domain.publisher import PrivacyStatus, PublishIntentCreate, PublishIntentState
from omega.infrastructure.celery_app import (
    DEFAULT_QUEUE,
    GENERAL_WORKER_QUEUES,
    PUBLISHER_QUEUE,
    PUBLISHER_TASK_ACKS_LATE,
    PUBLISHER_TASK_NAME,
    PUBLISHER_TASK_REJECT_ON_WORKER_LOST,
    PUBLISHER_WORKER_CONCURRENCY_DEFAULT,
    PUBLISHER_WORKER_PREFETCH_MULTIPLIER,
    PUBLISHER_WORKER_QUEUES,
    publisher_worker_metadata,
    resolve_task_queue,
)
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    MediaArtifact,
    Mission,
    MissionExecution,
    NetworkPolicy,
    NetworkRoute,
    SchedulerDispatchOutbox,
    Task,
)
from omega.worker.tasks import execute_publish_task
from tests.integration.test_p16e_background_publisher_hardening import (
    test_expired_lease_reclaim_increments_generation_and_reconciles as verify_expired_reclaim,
)
from tests.integration.test_p16e_background_publisher_hardening import (
    test_lease_fencing_blocks_concurrent_worker as verify_active_lease_fence,
)
from tests.integration.test_p16e_background_publisher_hardening import (
    test_terminal_session_safe_hold as verify_terminal_session_redelivery,
)
from tests.integration.test_p16e_background_publisher_hardening import (
    test_unknown_outcome_background_retry_reconciles_first as verify_unknown_reconciliation,
)
from tests.integration.test_p16e_background_publisher_hardening import (
    test_worker_entrypoint_idempotency as verify_duplicate_delivery,
)
from tests.integration.test_p17a_publish_scheduler import create_fixture_bundle

pytest_plugins = ("tests.integration.test_p16e_background_publisher_hardening",)
pytestmark = pytest.mark.usefixtures("publisher_test_env")


def test_publisher_task_routes_to_dedicated_queue() -> None:
    assert resolve_task_queue(PUBLISHER_TASK_NAME) == PUBLISHER_QUEUE


def test_normal_task_does_not_route_to_publisher_queue() -> None:
    assert resolve_task_queue("omega.scheduler.dispatch_sweep") == DEFAULT_QUEUE


def test_general_worker_queue_set_excludes_publisher() -> None:
    assert GENERAL_WORKER_QUEUES == (DEFAULT_QUEUE,)
    assert PUBLISHER_QUEUE not in GENERAL_WORKER_QUEUES


def test_publisher_worker_queue_set_and_delivery_policy() -> None:
    assert PUBLISHER_WORKER_QUEUES == (PUBLISHER_QUEUE,)
    assert PUBLISHER_WORKER_CONCURRENCY_DEFAULT == 1
    assert PUBLISHER_WORKER_PREFETCH_MULTIPLIER == 1
    assert PUBLISHER_TASK_ACKS_LATE is True
    assert PUBLISHER_TASK_REJECT_ON_WORKER_LOST is True
    assert execute_publish_task.acks_late is True
    assert execute_publish_task.reject_on_worker_lost is True
    assert publisher_worker_metadata()["max_in_flight_per_worker"] == 1


@pytest.mark.asyncio
async def test_scheduler_outbox_publisher_task_resolves_to_publisher_queue(
    db_session: AsyncSession,
) -> None:
    bundle = await create_fixture_bundle(
        db_session, intent_state=PublishIntentState.APPROVED.value
    )
    now = datetime.now(UTC)
    _, reservation = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=now - timedelta(seconds=5),
        actor="p17b-test",
        now=now,
    )
    await db_session.commit()

    result = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert result["dispatched"] == 1
    outbox = (
        await db_session.execute(
            select(SchedulerDispatchOutbox).where(
                SchedulerDispatchOutbox.reservation_id == reservation.id
            )
        )
    ).scalar_one()
    assert outbox.celery_task_name == PUBLISHER_TASK_NAME
    assert resolve_task_queue(outbox.celery_task_name) == PUBLISHER_QUEUE


@pytest.mark.asyncio
async def test_durable_dispatch_publisher_retry_resolves_to_publisher_queue(
    db_session: AsyncSession,
) -> None:
    dispatch = await DurableDispatchService.enqueue_async(
        db_session,
        idempotency_key=f"p17b:{uuid4()}",
        task_name=PUBLISHER_TASK_NAME,
        args=[str(uuid4())],
        purpose="PUBLISH_RETRY",
    )
    await db_session.commit()
    assert resolve_task_queue(dispatch.task_name) == PUBLISHER_QUEUE


@pytest.mark.asyncio
async def test_duplicate_task_delivery_produces_one_logical_claim(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch: pytest.MonkeyPatch
) -> None:
    await verify_duplicate_delivery(db_session, setup_p16e_fixtures, monkeypatch)


@pytest.mark.asyncio
async def test_active_lease_blocks_second_worker(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch: pytest.MonkeyPatch
) -> None:
    await verify_active_lease_fence(db_session, setup_p16e_fixtures, monkeypatch)


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_safely(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch: pytest.MonkeyPatch
) -> None:
    await verify_expired_reclaim(db_session, setup_p16e_fixtures, monkeypatch)


@pytest.mark.asyncio
async def test_unknown_outcome_reconciles_before_provider_mutation(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch: pytest.MonkeyPatch
) -> None:
    await verify_unknown_reconciliation(db_session, setup_p16e_fixtures, monkeypatch)


@pytest.mark.asyncio
async def test_worker_loss_redelivery_does_not_create_second_upload_session(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch: pytest.MonkeyPatch
) -> None:
    await verify_terminal_session_redelivery(db_session, setup_p16e_fixtures, monkeypatch)


@pytest.mark.asyncio
async def test_different_publish_tasks_can_execute_independently(
    db_session: AsyncSession, setup_p16e_fixtures, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = setup_p16e_fixtures
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "true")

    second_mission = Mission(
        id=uuid4(),
        title="Independent Publisher Mission",
        objective="Prove independent publisher claims",
        state="RUNNING",
        channel_id=fixture["channel"].id,
        guardian_epoch=1,
    )
    second_execution = MissionExecution(
        id=uuid4(),
        mission_id=second_mission.id,
        state="RUNNING",
        trigger_type="MANUAL",
    )
    second_task = Task(
        id=uuid4(),
        mission_id=second_mission.id,
        execution_id=second_execution.id,
        task_type="PUBLISH_VIDEO",
        title="Independent Publisher Task",
        state="READY",
    )
    db_session.add_all([second_mission, second_execution, second_task])
    second_artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=fixture["artifact"].production_request_id,
        artifact_type=fixture["artifact"].artifact_type,
        version=2,
        is_current=False,
        storage_uri="videos/p17b-independent.mp4",
        file_size_bytes=10_781_571,
        content_hash="8cb981a9f5350d8d7caed8fbd42c25209f20fa75b14cf93a29e547c742dced72",
        mime_type="video/mp4",
    )
    db_session.add(second_artifact)
    await PublishIntentService.create_publish_intent(
        db_session,
        PublishIntentCreate(
            mission_id=fixture["mission"].id,
            task_id=fixture["task"].id,
            channel_id=fixture["channel"].id,
            platform_account_id=fixture["account"].id,
            media_artifact_id=second_artifact.id,
            media_artifact_checksum=second_artifact.content_hash,
            title="Independent Publisher Task One",
            requested_privacy_status=PrivacyStatus.PRIVATE,
            made_for_kids=False,
        ),
        initial_state=PublishIntentState.APPROVED,
    )
    await PublishIntentService.create_publish_intent(
        db_session,
        PublishIntentCreate(
            mission_id=second_mission.id,
            task_id=second_task.id,
            channel_id=fixture["channel"].id,
            platform_account_id=fixture["account"].id,
            media_artifact_id=fixture["artifact"].id,
            media_artifact_checksum=fixture["artifact"].content_hash,
            title="Independent Publisher Task Two",
            requested_privacy_status=PrivacyStatus.PRIVATE,
            made_for_kids=False,
        ),
        initial_state=PublishIntentState.APPROVED,
    )
    await db_session.commit()
    await GuardianEngine(session_factory=lambda: db_session).get_or_create_default_ruleset(
        db_session
    )
    now = datetime.now(UTC)
    db_session.add(
        NetworkPolicy(
            id=uuid4(),
            service_category=ServiceCategory.YOUTUBE_API.value,
            version="1.0.0",
            status="ACTIVE",
            effective_at=now,
            policy_config=DEFAULT_POLICY_CONFIG,
            checksum=compute_config_checksum(DEFAULT_POLICY_CONFIG),
            created_at=now,
        )
    )
    route = (await db_session.execute(select(NetworkRoute))).scalars().one()
    await RouteCircuitBreaker.get_or_create_circuit_state(
        db_session,
        route.id,
        ServiceCategory.YOUTUBE_API,
    )
    await db_session.commit()

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        AsyncMock(
            side_effect=[
                UploadSessionInitResult(
                    session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=p17b-one",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
                UploadSessionInitResult(
                    session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=p17b-two",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        AsyncMock(
            side_effect=[
                ChunkUploadResult(
                    is_complete=True,
                    next_byte_offset=10_781_571,
                    provider_video_id="independent-video-one",
                    effective_privacy_status=PrivacyStatus.PRIVATE,
                ),
                ChunkUploadResult(
                    is_complete=True,
                    next_byte_offset=10_781_571,
                    provider_video_id="independent-video-two",
                    effective_privacy_status=PrivacyStatus.PRIVATE,
                ),
            ]
        ),
    )

    async def execute(task_id, worker_id):
        async with AsyncSessionLocal() as session:
            return await PublishExecutionService.execute_publish(
                session, task_id, worker_id=worker_id
            )

    first, second = await asyncio.gather(
        execute(fixture["task"].id, "publisher-worker-one"),
        execute(second_task.id, "publisher-worker-two"),
    )
    assert first.state == "SUCCEEDED"
    assert second.state == "SUCCEEDED"


def test_publisher_task_cannot_land_on_general_queue_in_canonical_config() -> None:
    assert resolve_task_queue(PUBLISHER_TASK_NAME) == PUBLISHER_QUEUE
    assert PUBLISHER_QUEUE not in GENERAL_WORKER_QUEUES
    assert PUBLISHER_TASK_NAME not in {
        "omega.scheduler.dispatch_sweep",
        "omega.tasks.execute",
    }
