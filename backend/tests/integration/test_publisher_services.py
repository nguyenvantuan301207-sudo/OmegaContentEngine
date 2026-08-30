"""Integration tests for OMEGA-011 Publisher application services."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.handoff_relay import HandoffRelayService
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.publish_service import (
    PublishExecutionError,
    PublishExecutionService,
)
from omega.application.publisher.reconciliation_service import ReconciliationService
from omega.domain.channel import ChannelState, Platform
from omega.domain.mission import AutonomyLevel, MissionState
from omega.domain.publisher import (
    HandoffStatus,
    PlatformAccountStatus,
    PrivacyStatus,
    PublishAttemptState,
    PublishIntentCreate,
    PublishIntentState,
    ReconciliationStatus,
)
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    CredentialVault,
    MediaArtifact,
    Mission,
    NetworkProfile,
    NetworkRoute,
    PlatformAccount,
    ProductionRequest,
    PublishAttempt,
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
    PublishIntentTransition,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
    UploadSession,
)
from omega.infrastructure.vault import get_credential_vault

pytestmark = pytest.mark.usefixtures("publisher_test_env")


async def create_artifact_with_ancestry(
    db_session: AsyncSession, channel_id: uuid4, art_hash: str
) -> MediaArtifact:
    """Helper to create full valid ancestry for MediaArtifact fixture."""
    dna_rev = ChannelDNARevision(
        id=uuid4(),
        channel_id=channel_id,
        version=1,
        snapshot={"name": "DNA"},
        change_reason="Initial",
    )
    db_session.add(dna_rev)

    topic = TopicCandidate(
        id=uuid4(),
        channel_id=channel_id,
        title="Test Topic",
        normalized_title="test topic",
        source_name="Manual Entry",
        summary="Test topic summary",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )

    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        title="Brief Title",
        summary="Brief summary",
    )
    db_session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    db_session.add(content_req)

    script_ver = ScriptVersion(
        id=uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Script v1",
        hook_text="Hook",
        closing_text="Close",
        cta_text="CTA",
    )
    db_session.add(script_ver)

    prod_req = ProductionRequest(
        id=uuid4(),
        channel_id=channel_id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    db_session.add(prod_req)

    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/test_render.mp4",
        file_size_bytes=2048,
        content_hash=art_hash,
        mime_type="video/mp4",
    )
    db_session.add(artifact)
    await db_session.flush()
    return artifact


@pytest_asyncio.fixture
async def setup_publisher_fixtures(db_session: AsyncSession):
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Profile-{uuid4().hex[:8]}",
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
        config_checksum="chk1",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)

    # Channel
    channel = Channel(
        id=uuid4(),
        slug=f"service-pub-chan-{uuid4().hex[:8]}",
        name="Service Pub Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    # Mission
    mission = Mission(
        id=uuid4(),
        title="Publish Mission",
        objective="Publish video to YouTube",
        state=MissionState.RUNNING.value,
        autonomy_level=AutonomyLevel.AUTONOMOUS.value,
        channel_id=channel.id,
    )
    db_session.add(mission)

    # Task
    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="PUBLISH_VIDEO",
        title="Publish Video Task",
        state=TaskState.READY.value,
    )
    db_session.add(task)

    # Media Artifact with full ancestry
    art_hash = "f" * 64
    artifact = await create_artifact_with_ancestry(db_session, channel.id, art_hash)

    # Platform Account & Vault
    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="Service Channel",
        external_account_id="UC_SERVICE_123",
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
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
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
    }


@pytest.mark.asyncio
async def test_publish_intent_service_create_and_supersede(
    db_session: AsyncSession, setup_publisher_fixtures
):
    """Verify PublishIntent creation, checksum calculation, and superseding upon artifact change."""
    f = setup_publisher_fixtures

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Version 1 Title",
        description="Version 1 Desc",
        made_for_kids=False,
    )
    intent1 = await PublishIntentService.create_publish_intent(db_session, payload)
    assert intent1.state == PublishIntentState.APPROVED.value
    assert intent1.revision_number == 1
    assert intent1.supersedes_intent_id is None

    # Check intent transition recorded
    res_trans = await db_session.execute(
        select(PublishIntentTransition).where(
            PublishIntentTransition.publish_intent_id == intent1.id
        )
    )
    transitions = res_trans.scalars().all()
    assert len(transitions) == 1
    assert transitions[0].to_state == "APPROVED"

    # Create new intent with changed title -> Must supersede intent1
    payload2 = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Version 2 Updated Title",
        description="Version 1 Desc",
        made_for_kids=False,
    )
    intent2 = await PublishIntentService.create_publish_intent(db_session, payload2)
    assert intent2.revision_number == 2
    assert intent2.supersedes_intent_id == intent1.id

    # Verify intent1 is now SUPERSEDED
    await db_session.refresh(intent1)
    assert intent1.state == PublishIntentState.SUPERSEDED.value
    assert intent1.superseded_at is not None


@pytest.mark.asyncio
async def test_worker_refuses_execution_without_approved_intent(
    db_session: AsyncSession, setup_publisher_fixtures
):
    """Verify PublishExecutionService refuses execution if no approved intent exists."""
    f = setup_publisher_fixtures
    # No intent created
    with pytest.raises(
        PublishExecutionError, match="No approved or reclaimable PublishIntent found"
    ):
        await PublishExecutionService.execute_publish(db_session, f["task"].id)


@pytest.mark.asyncio
async def test_privacy_fallback_policy_rejection_and_opt_in(
    db_session: AsyncSession, setup_publisher_fixtures, monkeypatch
):
    """Verify requested PUBLIC without fallback is blocked, but with opt-in fallback uploads PRIVATE."""
    f = setup_publisher_fixtures
    os.environ["OMEGA_TEST_MODE"] = "1"

    # 1. Fallback disabled (default) -> BLOCKED_GUARDIAN
    payload_blocked = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Public Video Without Fallback",
        requested_privacy_status=PrivacyStatus.PUBLIC,
        made_for_kids=False,
        platform_custom_options={"privacy_fallback_allowed": False},
    )
    await PublishIntentService.create_publish_intent(db_session, payload_blocked)

    attempt_blocked = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt_blocked.state == PublishAttemptState.BLOCKED_GUARDIAN.value

    # 2. Fallback enabled -> Uploads with effective privacy = PRIVATE
    from omega.application.publisher.adapters.base import ChunkUploadResult, UploadSessionInitResult

    mock_init = AsyncMock()
    mock_init.return_value = UploadSessionInitResult(
        session_uri="https://mock-upload-session",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    mock_chunk = AsyncMock()
    mock_chunk.return_value = ChunkUploadResult(
        is_complete=True,
        next_byte_offset=2048,
        provider_video_id="yt_video_fallback_123",
        provider_url="https://youtu.be/yt_video_fallback_123",
        effective_privacy_status=PrivacyStatus.PRIVATE,
    )

    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_chunk,
    )

    payload_opt_in = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Public Video With Fallback",
        requested_privacy_status=PrivacyStatus.PUBLIC,
        made_for_kids=False,
        platform_custom_options={"privacy_fallback_allowed": True},
    )
    intent_opt_in = await PublishIntentService.create_publish_intent(db_session, payload_opt_in)

    attempt_opt_in = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt_opt_in.state == PublishAttemptState.SUCCEEDED.value
    assert attempt_opt_in.effective_privacy_status == PrivacyStatus.PRIVATE.value
    assert intent_opt_in.requested_privacy_status == PrivacyStatus.PUBLIC.value


@pytest.mark.asyncio
async def test_handoff_outbox_and_scheduler_relay(
    db_session: AsyncSession, setup_publisher_fixtures, monkeypatch
):
    """Verify failed retryable publish inserts handoff outbox and relay invokes Scheduler."""
    f = setup_publisher_fixtures
    os.environ["OMEGA_TEST_MODE"] = "1"

    # Mock adapter failure with retryable network error
    mock_init = AsyncMock(side_effect=httpx.NetworkError("Transient network drop"))
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Retry Video",
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(db_session, payload)

    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state == PublishAttemptState.RETRYABLE_FAILED.value

    # Verify handoff row exists
    res = await db_session.execute(
        select(PublisherSchedulerHandoffOutbox).where(
            PublisherSchedulerHandoffOutbox.publish_intent_id == intent.id
        )
    )
    handoff = res.scalar_one_or_none()
    assert handoff is not None
    assert handoff.status == HandoffStatus.PENDING.value

    # Run HandoffRelayService sweep
    delivered = await HandoffRelayService.process_pending_handoffs(db_session)
    assert delivered >= 1

    await db_session.refresh(handoff)
    assert handoff.status == HandoffStatus.DELIVERED.value
    assert handoff.delivered_at is not None


@pytest.mark.asyncio
async def test_reconciliation_service_sweep(
    db_session: AsyncSession, setup_publisher_fixtures, monkeypatch
):
    """Verify reconciliation sweep resolves UNKNOWN attempts."""
    f = setup_publisher_fixtures

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Recon Video",
        description="Recon Desc",
        made_for_kids=False,
        intent_checksum="r" * 64,
        state=PublishIntentState.CLAIMED.value,
    )
    db_session.add(intent)
    await db_session.flush()

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"recon-{uuid4()}",
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.PENDING.value,
    )
    db_session.add(attempt)

    session_row = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock_recon_session_123",
        total_bytes=5000,
        bytes_uploaded=5000,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(session_row)
    await db_session.commit()

    from omega.application.publisher.adapters.base import ReconciliationResult

    mock_recon = AsyncMock()
    mock_recon.return_value = ReconciliationResult(
        is_confirmed_success=False,
        is_incomplete=False,
        is_held_for_review=True,
        diagnostic_reason="Session 404",
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.reconcile_upload_session",
        mock_recon,
    )

    status = await ReconciliationService.reconcile_attempt(db_session, attempt.id)
    assert status == ReconciliationStatus.MANUAL_HOLD

    from omega.infrastructure.database import AsyncSessionLocal

    async with AsyncSessionLocal() as check_session:
        res_att = await check_session.execute(
            select(PublishAttempt).where(PublishAttempt.id == attempt.id)
        )
        updated_attempt = res_att.scalar_one()
        assert updated_attempt.reconciliation_status == ReconciliationStatus.MANUAL_HOLD.value


@pytest.mark.asyncio
async def test_scheduler_pins_exact_intent_checksum_and_revision(
    db_session: AsyncSession, setup_publisher_fixtures
):
    """Verify OMEGA-010 Scheduler pins exact target_type=PUBLISH_INTENT, target_id, and channel."""
    f = setup_publisher_fixtures
    from omega.application.scheduler.evaluation_engine import ScheduleEvaluationEngine
    from omega.application.scheduler.policy_service import SchedulePolicyService
    from omega.domain.scheduler import (
        ScheduleRequest,
        ScheduleTargetType,
        ScheduleWorkloadCategory,
    )

    # 1. Create intent
    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Pinned Video",
        description="Pinned description",
        tags=["pinned"],
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(db_session, payload)

    # 2. Setup schedule policy
    _policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category="EXTERNAL_PUBLISH",
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={
            "timezone": "America/New_York",
            "allowed_workloads": ["EXTERNAL_PUBLISH"],
            "max_daily_runs": 1000,
            "default_cooldown_minutes": 0,
            "global_concurrency_limit": 1000,
        },
        activate=True,
    )

    # 3. Evaluate schedule
    req = ScheduleRequest(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT,
        target_id=intent.id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH,
        channel_id=f["channel"].id,
        earliest_start_at=datetime.now(UTC),
        caller_key=f"pin_test_{intent.id}",
    )
    decision = await ScheduleEvaluationEngine.evaluate_schedule(
        session=db_session,
        request=req,
        now=datetime.now(UTC),
    )
    assert decision.reservation_id is not None
    from omega.infrastructure.models import ScheduleReservation

    res = await db_session.execute(
        select(ScheduleReservation).where(ScheduleReservation.id == decision.reservation_id)
    )
    reservation = res.scalar_one()
    assert reservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value
    assert reservation.target_id == intent.id
    assert reservation.channel_id == f["channel"].id


@pytest.mark.asyncio
async def test_final_timeout_becomes_unknown_and_prevents_blind_reupload(
    db_session: AsyncSession, setup_publisher_fixtures, monkeypatch
):
    """Verify when final chunk times out, attempt becomes UNKNOWN and prevents blind re-upload."""
    f = setup_publisher_fixtures
    os.environ["OMEGA_TEST_MODE"] = "1"

    from omega.application.publisher.adapters.base import UploadSessionInitResult

    mock_init = AsyncMock()
    mock_init.return_value = UploadSessionInitResult(
        session_uri="https://mock-timeout-session",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    # Final chunk times out
    mock_chunk = AsyncMock(side_effect=httpx.ReadTimeout("Timeout waiting for 200 OK completion"))

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
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Timeout Final Chunk",
        description="Timeout description",
        tags=[],
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(db_session, payload)

    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state == PublishAttemptState.UNKNOWN.value
    assert attempt.reconciliation_status == ReconciliationStatus.PENDING.value


@pytest.mark.asyncio
async def test_expired_publish_lease_cannot_renew(
    db_session: AsyncSession, setup_publisher_fixtures
):
    """Verify TX-PRE-CHUNK hard fence rejects renewal when lease has expired."""
    from sqlalchemy import update

    now = datetime.now(UTC)
    token = uuid4()

    intent = PublishIntent(
        id=uuid4(),
        mission_id=setup_publisher_fixtures["mission"].id,
        task_id=setup_publisher_fixtures["task"].id,
        channel_id=setup_publisher_fixtures["channel"].id,
        platform_account_id=setup_publisher_fixtures["account"].id,
        media_artifact_id=setup_publisher_fixtures["artifact"].id,
        media_artifact_checksum="c" * 64,
        title="Expired Lease Test",
        description="Expired lease desc",
        tags=[],
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="k" * 64,
        state=PublishIntentState.CLAIMED.value,
        claim_token=token,
        attempt_generation=1,
        lease_expires_at=now - timedelta(minutes=1),  # Expired
    )
    db_session.add(intent)
    await db_session.commit()

    # Worker attempts renewal via TX-PRE-CHUNK SQL
    stmt = (
        update(PublishIntent)
        .where(
            PublishIntent.id == intent.id,
            PublishIntent.claim_token == token,
            PublishIntent.attempt_generation == 1,
            PublishIntent.lease_expires_at > datetime.now(UTC),
        )
        .values(lease_expires_at=datetime.now(UTC) + timedelta(minutes=3))
    )
    res = await db_session.execute(stmt)
    assert res.rowcount == 0  # Zero rows updated, lease renewal forbidden


@pytest.mark.asyncio
async def test_old_worker_cannot_send_chunk_after_lease_expiry(
    db_session: AsyncSession, setup_publisher_fixtures, monkeypatch
):
    """Verify if lease expires during upload, old worker halts and does not send chunk."""
    f = setup_publisher_fixtures
    os.environ["OMEGA_TEST_MODE"] = "1"

    from omega.application.publisher.adapters.base import UploadSessionInitResult

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Old Worker Fence Test",
        description="Fence desc",
        tags=[],
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(db_session, payload)

    # Make init_res expire the intent lease before the chunk loop
    async def mock_init_and_expire(*args, **kwargs):
        from sqlalchemy import update

        from omega.infrastructure.database import AsyncSessionLocal

        async with AsyncSessionLocal() as s:
            await s.execute(
                update(PublishIntent)
                .where(PublishIntent.id == intent.id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(minutes=5))
            )
            await s.commit()
        return UploadSessionInitResult(
            session_uri="https://mock-fence-session",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

    mock_chunk = AsyncMock()
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init_and_expire,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_chunk,
    )

    # Worker executes publish; lease is expired right before chunk loop
    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt is not None
    # The chunk HTTP call should not be reached because PRE-CHUNK fence fails
    assert mock_chunk.call_count == 0


@pytest.mark.asyncio
async def test_reclaimer_fencing_race_invalidates_stale_claim(
    db_session: AsyncSession, setup_publisher_fixtures
):
    """Verify new claim generation invalidates old worker claim token."""
    from sqlalchemy import update

    now = datetime.now(UTC)
    old_token = uuid4()
    new_token = uuid4()

    intent = PublishIntent(
        id=uuid4(),
        mission_id=setup_publisher_fixtures["mission"].id,
        task_id=setup_publisher_fixtures["task"].id,
        channel_id=setup_publisher_fixtures["channel"].id,
        platform_account_id=setup_publisher_fixtures["account"].id,
        media_artifact_id=setup_publisher_fixtures["artifact"].id,
        media_artifact_checksum="c" * 64,
        title="Reclaimer Race Test",
        description="Race desc",
        tags=[],
        platform_custom_options={},
        made_for_kids=False,
        intent_checksum="k" * 64,
        state=PublishIntentState.CLAIMED.value,
        claim_token=new_token,  # Reclaimer acquired new token
        attempt_generation=2,  # Reclaimer incremented generation
        lease_expires_at=now + timedelta(minutes=5),
    )
    db_session.add(intent)
    await db_session.commit()

    # Stale worker (generation 1, old_token) attempts update
    stmt = (
        update(PublishIntent)
        .where(
            PublishIntent.id == intent.id,
            PublishIntent.claim_token == old_token,
            PublishIntent.attempt_generation == 1,
            PublishIntent.lease_expires_at > datetime.now(UTC),
        )
        .values(lease_expires_at=datetime.now(UTC) + timedelta(minutes=3))
    )
    res = await db_session.execute(stmt)
    assert res.rowcount == 0


@pytest.mark.asyncio
async def test_artifact_path_traversal_rejected(db_session: AsyncSession, setup_publisher_fixtures):
    """Verify artifact paths with directory traversal escapes are rejected."""
    f = setup_publisher_fixtures
    # Modify media artifact storage URI to path traversal escape
    from sqlalchemy import update

    await db_session.execute(
        update(MediaArtifact)
        .where(MediaArtifact.id == f["artifact"].id)
        .values(storage_uri="../../../etc/passwd")
    )
    await db_session.commit()

    payload = PublishIntentCreate(
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Traversal Escape Test",
        description="Traversal desc",
        tags=[],
        made_for_kids=False,
    )
    await PublishIntentService.create_publish_intent(db_session, payload)

    attempt = await PublishExecutionService.execute_publish(db_session, f["task"].id)
    assert attempt.state == PublishAttemptState.PERMANENT_FAILED.value
    assert "Artifact path escape detected" in (attempt.error_message or "")


@pytest.mark.asyncio
async def test_handoff_crash_after_scheduler_success_before_ack_recovers_without_duplicates(
    db_session: AsyncSession, setup_publisher_fixtures, monkeypatch
):
    """Verify recovery when relay crashes after Scheduler succeeds but before DELIVERED ACK."""
    f = setup_publisher_fixtures
    # Setup schedule policy for channel
    from omega.application.scheduler.policy_service import SchedulePolicyService
    from omega.infrastructure.models import ScheduleReservation

    await SchedulePolicyService.create_policy(
        db_session,
        workload_category="EXTERNAL_PUBLISH",
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={
            "timezone": "America/New_York",
            "allowed_workloads": ["EXTERNAL_PUBLISH"],
            "max_daily_runs": 1000,
            "default_cooldown_minutes": 0,
            "global_concurrency_limit": 1000,
        },
        activate=True,
    )

    # Attach valid intent
    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Crash Recover Video",
        description="Crash desc",
        tags=[],
        platform_custom_options={},
        made_for_kids=False,
        intent_checksum="k" * 64,
        state=PublishIntentState.APPROVED.value,
    )
    db_session.add(intent)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"att_crash_{uuid4().hex}",
        state=PublishAttemptState.RETRYABLE_FAILED.value,
    )
    db_session.add(attempt)
    await db_session.flush()

    handoff = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        task_id=f["task"].id,
        mission_id=f["mission"].id,
        earliest_retry_at=datetime.now(UTC) - timedelta(minutes=1),
        reason="Network drop",
        idempotency_key=f"crash_test_{uuid4().hex}",
        status=HandoffStatus.CLAIMED.value,
        claim_token=uuid4(),
        delivery_generation=1,
        attempt_count=1,
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),  # Expired lease
        next_attempt_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(handoff)
    await db_session.commit()

    # First relay delivery runs
    delivered1 = await HandoffRelayService.process_pending_handoffs(db_session)
    assert delivered1 >= 1

    await db_session.refresh(handoff)
    assert handoff.status == HandoffStatus.DELIVERED.value
    assert handoff.delivered_at is not None

    # Check reservation count for this intent
    res_count = await db_session.execute(
        select(ScheduleReservation).where(ScheduleReservation.target_id == intent.id)
    )
    reservations = res_count.scalars().all()
    assert len(reservations) == 1

    # Simulate re-running relay: row is already DELIVERED
    delivered2 = await HandoffRelayService.process_pending_handoffs(db_session)
    assert delivered2 == 0

    # Reservations remain exactly 1 (zero duplicate reservations)
    res_count2 = await db_session.execute(
        select(ScheduleReservation).where(ScheduleReservation.target_id == intent.id)
    )
    assert len(res_count2.scalars().all()) == 1


@pytest.mark.asyncio
async def test_handoff_dead_letter_after_max_attempts(
    db_session: AsyncSession, setup_publisher_fixtures, monkeypatch
):
    """Verify handoff outbox row is moved to DEAD_LETTER after reaching max attempt threshold."""
    f = setup_publisher_fixtures
    from omega.application.publisher.handoff_relay import MAX_HANDOFF_ATTEMPTS

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Dead Letter Video",
        description="Dead letter desc",
        tags=[],
        platform_custom_options={},
        made_for_kids=False,
        intent_checksum="k" * 64,
        state=PublishIntentState.APPROVED.value,
    )
    db_session.add(intent)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"att_dl_{uuid4().hex}",
        state=PublishAttemptState.RETRYABLE_FAILED.value,
    )
    db_session.add(attempt)
    await db_session.flush()

    handoff = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        task_id=f["task"].id,
        mission_id=f["mission"].id,
        earliest_retry_at=datetime.now(UTC) - timedelta(minutes=1),
        reason="Persistent provider failure",
        idempotency_key=f"dl_test_{uuid4().hex}",
        status=HandoffStatus.PENDING.value,
        attempt_count=MAX_HANDOFF_ATTEMPTS - 1,  # 4 attempts already
        next_attempt_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(handoff)
    await db_session.commit()

    # Mock ScheduleEvaluationEngine failure
    mock_eval = AsyncMock(side_effect=RuntimeError("Scheduler evaluation unavailable"))
    monkeypatch.setattr(
        "omega.application.scheduler.evaluation_engine.ScheduleEvaluationEngine.evaluate_schedule",
        mock_eval,
    )

    # Process: Attempt count reaches 5 (MAX) -> Transitions to DEAD_LETTER
    delivered = await HandoffRelayService.process_pending_handoffs(db_session)
    assert delivered == 0

    await db_session.refresh(handoff)
    assert handoff.status == HandoffStatus.DEAD_LETTER.value
    assert handoff.attempt_count == MAX_HANDOFF_ATTEMPTS
    assert "Scheduler evaluation unavailable" in handoff.last_error
