"""Integration tests for OMEGA-011 Controlled Visibility Policy (P17-F0).

Matrix covering:
1. PRIVATE approved -> allowed
2. PRIVATE canary mode -> allowed
3. UNLISTED under private-canary mode -> blocked
4. PUBLIC under private-canary mode -> blocked
5. UNLISTED without explicit channel/policy permission -> blocked
6. PUBLIC without explicit permission -> blocked
7. UNLISTED permitted + explicit approval -> policy passes
8. PUBLIC permitted + explicit approval -> policy passes
9. visibility changed after approval -> approval invalid/fail closed
10. PRIVATE never auto-upgrades
11. UNLISTED never auto-upgrades to PUBLIC
12. provider-forced PRIVATE + fallback disabled -> blocked
13. provider-forced PRIVATE + explicit fallback allowed -> effective PRIVATE
14. fallback is auditable
15. requested/effective privacy persisted correctly
16. invalid visibility request causes zero provider calls
17. scheduler/outbox path unchanged by visibility policy
18. duplicate-delivery protection unchanged
19. PRIVATE P17-E behavior regression preserved
20. dashboard/API expose requested/effective state safely if already supported
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.operations_query_service import PublisherOperationsQueryService
from omega.application.publisher.publish_service import (
    PublishExecutionError,
    PublishExecutionService,
)
from omega.application.publisher.visibility_policy import (
    ControlledVisibilityPolicy,
)
from omega.application.scheduler.policy_service import SchedulePolicyService
from omega.config import Settings
from omega.domain.publisher import (
    PrivacyStatus,
    PublishAttemptState,
    PublisherErrorCategory,
    PublishIntentState,
    compute_publish_intent_checksum,
)
from omega.domain.scheduler import (
    DispatchOutboxStatus,
    ReservationState,
    ScheduleAction,
    ScheduleTargetType,
    ScheduleWorkloadCategory,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    CredentialVault,
    Mission,
    MissionExecution,
    PlatformAccount,
    ProductionRequest,
    PublishAttempt,
    PublishAttemptTransition,
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


@pytest.fixture(autouse=True)
def set_test_mode(monkeypatch):
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")


async def create_p17f_bundle(
    db_session: AsyncSession,
    *,
    requested_privacy: str = "PRIVATE",
    channel_metadata: dict | None = None,
    platform_custom_options: dict | None = None,
    intent_state: str = "APPROVED",
    platform: str = "YOUTUBE",
    tamper_checksum: bool = False,
) -> dict:
    """Construct complete coherent fixture hierarchy for visibility tests."""
    now = datetime.now(UTC)
    channel = Channel(
        id=uuid4(),
        name=f"Channel-{uuid4().hex[:6]}",
        slug=f"chan-{uuid4().hex[:8]}",
        platform=platform.lower(),
        state="ACTIVE",
        metadata_=channel_metadata or {},
    )
    db_session.add(channel)

    art_hash = "a" * 64
    artifact = await create_artifact_with_ancestry(db_session, channel.id, art_hash)
    dna = (
        await db_session.execute(
            select(ChannelDNARevision).where(ChannelDNARevision.channel_id == channel.id)
        )
    ).scalar_one()

    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="Visibility Mission",
        objective="Policy testing",
        state="RUNNING",
        guardian_epoch=1,
        priority=1,
    )
    db_session.add(mission)
    await db_session.flush()

    execution = MissionExecution(
        id=uuid4(),
        mission_id=mission.id,
        channel_dna_revision_id=dna.id,
        state="RUNNING",
    )
    db_session.add(execution)
    await db_session.flush()

    prod_req = await db_session.get(ProductionRequest, artifact.production_request_id)
    if prod_req:
        prod_req.mission_execution_id = execution.id

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        execution_id=execution.id,
        task_type="publish",
        title="Publish Task",
        state="READY",
    )
    db_session.add(task)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=platform.upper(),
        account_display_name="Test Account",
        external_account_id=f"ext-{uuid4().hex[:8]}",
        status="ACTIVE",
    )
    db_session.add(account)
    await db_session.flush()

    vault = get_credential_vault()
    enc_access, key_ver = vault.encrypt("mock-access-token")
    enc_refresh, _ = vault.encrypt("mock-refresh-token")
    db_session.add(
        CredentialVault(
            id=uuid4(),
            platform_account_id=account.id,
            encrypted_access_token=enc_access,
            access_token_expires_at=now + timedelta(hours=2),
            encrypted_refresh_token=enc_refresh,
            token_type="Bearer",
            key_version=key_ver,
        )
    )

    custom_opts = platform_custom_options or {}
    checksum = compute_publish_intent_checksum(
        task_id=task.id,
        media_artifact_checksum=artifact.content_hash,
        channel_dna_revision_id=dna.id,
        platform=account.platform,
        title="Policy Test Video",
        description="Policy Test Desc",
        tags=["omega", "test"],
        requested_privacy_status=requested_privacy,
        category_id="28",
        made_for_kids=False,
        platform_custom_options=custom_opts,
    )
    if tamper_checksum:
        checksum = "tampered_bad_checksum_" + "0" * 42

    intent = PublishIntent(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        channel_id=channel.id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum=artifact.content_hash,
        channel_dna_revision_id=dna.id,
        title="Policy Test Video",
        description="Policy Test Desc",
        tags=["omega", "test"],
        requested_privacy_status=requested_privacy,
        category_id="28",
        made_for_kids=False,
        platform_custom_options=custom_opts,
        intent_checksum=checksum,
        state=intent_state,
        attempt_generation=1,
    )
    db_session.add(intent)
    await db_session.commit()
    await db_session.refresh(channel)
    await db_session.refresh(account)
    await db_session.refresh(intent)
    await db_session.refresh(task)

    return {
        "channel": channel,
        "account": account,
        "mission": mission,
        "execution": execution,
        "task": task,
        "artifact": artifact,
        "intent": intent,
        "dna": dna,
    }


# ── Test 1: PRIVATE approved -> allowed ──
@pytest.mark.asyncio
async def test_01_private_approved_allowed(db_session: AsyncSession):
    b = await create_p17f_bundle(db_session, requested_privacy="PRIVATE", intent_state="APPROVED")
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is True
    assert decision.requested_privacy == PrivacyStatus.PRIVATE
    assert decision.effective_privacy == PrivacyStatus.PRIVATE
    assert decision.fallback_applied is False


# ── Test 2: PRIVATE canary mode -> allowed ──
@pytest.mark.asyncio
async def test_02_private_canary_mode_allowed(db_session: AsyncSession):
    b = await create_p17f_bundle(db_session, requested_privacy="PRIVATE", intent_state="APPROVED")
    settings = Settings(publisher_private_canary_mode=True)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is True
    assert decision.effective_privacy == PrivacyStatus.PRIVATE


# ── Test 3: UNLISTED under private-canary mode -> blocked ──
@pytest.mark.asyncio
async def test_03_unlisted_under_canary_mode_blocked(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="UNLISTED",
        channel_metadata={"policy_permit_unlisted": True},
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=True)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is False
    assert decision.error_category == PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED
    assert "Private canary mode rejects PUBLIC and UNLISTED requests" in (decision.error_message or "")


# ── Test 4: PUBLIC under private-canary mode -> blocked ──
@pytest.mark.asyncio
async def test_04_public_under_canary_mode_blocked(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PUBLIC",
        channel_metadata={"policy_permit_public": True},
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=True)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is False
    assert decision.error_category == PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED
    assert "Private canary mode rejects PUBLIC and UNLISTED requests" in (decision.error_message or "")


# ── Test 5: UNLISTED without explicit channel/policy permission -> blocked ──
@pytest.mark.asyncio
async def test_05_unlisted_without_permission_blocked(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="UNLISTED",
        channel_metadata={},  # No permission
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is False
    assert decision.error_category == PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED
    assert "not authorized by channel or publication policy" in (decision.error_message or "")


# ── Test 6: PUBLIC without explicit permission -> blocked ──
@pytest.mark.asyncio
async def test_06_public_without_permission_blocked(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PUBLIC",
        channel_metadata={},  # No permission
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is False
    assert decision.error_category == PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED
    assert "not authorized by channel or publication policy" in (decision.error_message or "")


# ── Test 7: UNLISTED permitted + explicit approval -> policy passes ──
@pytest.mark.asyncio
async def test_07_unlisted_permitted_and_approved_passes(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="UNLISTED",
        channel_metadata={"allowed_visibilities": ["PRIVATE", "UNLISTED"]},
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is True
    assert decision.requested_privacy == PrivacyStatus.UNLISTED
    assert decision.effective_privacy == PrivacyStatus.UNLISTED
    assert decision.fallback_applied is False


# ── Test 8: PUBLIC permitted + explicit approval -> policy passes ──
@pytest.mark.asyncio
async def test_08_public_permitted_and_approved_passes(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PUBLIC",
        channel_metadata={"policy_permit_public": True},
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is True
    assert decision.requested_privacy == PrivacyStatus.PUBLIC
    assert decision.effective_privacy == PrivacyStatus.PUBLIC
    assert decision.fallback_applied is False


# ── Test 9: visibility changed after approval -> approval invalid/fail closed ──
@pytest.mark.asyncio
async def test_09_visibility_changed_after_approval_invalidates(db_session: AsyncSession):
    # Bundle created and approved with requested_privacy="PRIVATE"
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PRIVATE",
        channel_metadata={"policy_permit_public": True},
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    # Stale tamper: change requested_privacy_status to PUBLIC without updating approval checksum
    b["intent"].requested_privacy_status = "PUBLIC"

    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is False
    assert decision.error_category == PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED
    assert "checksum mismatch" in (decision.error_message or "")


# ── Test 10: PRIVATE never auto-upgrades ──
@pytest.mark.asyncio
async def test_10_private_never_auto_upgrades(db_session: AsyncSession):
    # Even when channel permits all visibilities
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PRIVATE",
        channel_metadata={
            "allowed_visibilities": ["PRIVATE", "UNLISTED", "PUBLIC"],
            "policy_permit_public": True,
            "policy_permit_unlisted": True,
        },
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is True
    assert decision.requested_privacy == PrivacyStatus.PRIVATE
    assert decision.effective_privacy == PrivacyStatus.PRIVATE


# ── Test 11: UNLISTED never auto-upgrades to PUBLIC ──
@pytest.mark.asyncio
async def test_11_unlisted_never_auto_upgrades_to_public(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="UNLISTED",
        channel_metadata={
            "allowed_visibilities": ["PRIVATE", "UNLISTED", "PUBLIC"],
            "policy_permit_public": True,
            "policy_permit_unlisted": True,
        },
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is True
    assert decision.requested_privacy == PrivacyStatus.UNLISTED
    assert decision.effective_privacy == PrivacyStatus.UNLISTED


# ── Test 12: provider-forced PRIVATE + fallback disabled -> blocked ──
@pytest.mark.asyncio
async def test_12_unverified_project_fallback_disabled_blocked(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PUBLIC",
        channel_metadata={"policy_permit_public": True},
        platform_custom_options={
            "youtube_project_verified": False,  # Unverified
            "privacy_fallback_allowed": False,  # Fallback disabled
        },
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is False
    assert decision.error_category == PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED
    assert "requires verified YouTube API project (privacy fallback disabled)" in (
        decision.error_message or ""
    )


# ── Test 13: provider-forced PRIVATE + explicit fallback allowed -> effective PRIVATE ──
@pytest.mark.asyncio
async def test_13_unverified_project_fallback_allowed_effective_private(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PUBLIC",
        channel_metadata={"policy_permit_public": True},
        platform_custom_options={
            "youtube_project_verified": False,  # Unverified
            "privacy_fallback_allowed": True,  # Fallback enabled
        },
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is True
    assert decision.requested_privacy == PrivacyStatus.PUBLIC
    assert decision.effective_privacy == PrivacyStatus.PRIVATE
    assert decision.fallback_applied is True
    assert "downgraded to PRIVATE" in (decision.fallback_reason or "")


# ── Test 14: fallback is auditable ──
@pytest.mark.asyncio
async def test_14_fallback_is_auditable(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="UNLISTED",
        channel_metadata={"policy_permit_unlisted": True},
        platform_custom_options={
            "youtube_project_verified": False,
            "privacy_fallback_allowed": True,
        },
        intent_state="APPROVED",
    )
    settings = Settings(publisher_private_canary_mode=False)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.fallback_applied is True
    assert decision.fallback_reason is not None
    assert "explicit channel fallback policy" in decision.fallback_reason


# ── Test 15: requested/effective privacy persisted correctly ──
@pytest.mark.asyncio
async def test_15_requested_and_effective_privacy_persisted(db_session: AsyncSession, monkeypatch):
    monkeypatch.setenv("PUBLISHER_PRIVATE_CANARY_MODE", "false")
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="UNLISTED",
        channel_metadata={"policy_permit_unlisted": True},
        platform_custom_options={
            "youtube_project_verified": False,
            "privacy_fallback_allowed": True,
        },
        intent_state="APPROVED",
    )
    init_mock = AsyncMock(
        return_value=AsyncMock(
            session_uri="https://mock.upload.uri",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    chunk_mock = AsyncMock(
        return_value=AsyncMock(
            is_complete=True,
            bytes_uploaded=1024,
            provider_video_id="mock-vid-15",
            provider_url="https://youtu.be/mock-vid-15",
            effective_privacy_status=PrivacyStatus.PRIVATE,
        )
    )
    permit_mock = MagicMock(is_expired=lambda: False, is_valid_for=lambda t: True)
    # Mock preflight and adapter to observe execute_publish DB state
    with patch(
        "omega.application.network.preflight.NetworkPreflightService.preflight",
        new=AsyncMock(return_value=(None, permit_mock)),
    ), patch(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        new=init_mock,
    ), patch(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        new=chunk_mock,
    ), patch(
        "omega.application.publisher.publish_service.GuardianEngine.execute_check",
        new=AsyncMock(return_value=AsyncMock(decision=AsyncMock(action=AsyncMock(value="ALLOW")))),
    ):
        attempt = await PublishExecutionService.execute_publish(
            db_session, task_id=b["task"].id, worker_id="worker-test-15"
        )

        assert attempt.state == PublishAttemptState.SUCCEEDED.value
        assert attempt.effective_privacy_status == "PRIVATE"
        assert b["intent"].requested_privacy_status == "UNLISTED"

        # Check that fallback transition audit row was persisted
        trans_rows = (
            await db_session.execute(
                select(PublishAttemptTransition).where(
                    PublishAttemptTransition.publish_attempt_id == attempt.id
                )
            )
        ).scalars().all()
        fallback_trans = [
            t for t in trans_rows if "downgraded to PRIVATE" in (t.reason or "")
        ]
        assert len(fallback_trans) == 1


# ── Test 16: invalid visibility request causes zero provider calls ──
@pytest.mark.asyncio
async def test_16_invalid_visibility_zero_provider_calls(db_session: AsyncSession):
    # Public intent without channel permission
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PUBLIC",
        channel_metadata={},  # No permission
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )

    mock_refresh = AsyncMock()
    mock_init = AsyncMock()

    with patch(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.refresh_access_token",
        new=mock_refresh,
    ), patch(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        new=mock_init,
    ), patch(
        "omega.application.publisher.publish_service.GuardianEngine.execute_check",
        new=AsyncMock(return_value=AsyncMock(decision=AsyncMock(action=AsyncMock(value="ALLOW")))),
    ):
        attempt = await PublishExecutionService.execute_publish(
            db_session, task_id=b["task"].id, worker_id="worker-test-16"
        )

        # Attempt must be BLOCKED_GUARDIAN
        assert attempt.state == PublishAttemptState.BLOCKED_GUARDIAN.value
        assert attempt.error_category == PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED.value

        # Zero OAuth calls
        assert mock_refresh.call_count == 0

        # Zero adapter/provider calls
        assert mock_init.call_count == 0

        # Zero UploadSessions created
        upload_sessions = (
            await db_session.execute(
                select(UploadSession).where(UploadSession.publish_attempt_id == attempt.id)
            )
        ).scalars().all()
        assert len(upload_sessions) == 0

        # Zero provider videos
        assert attempt.provider_video_id is None


# ── Test 17: scheduler/outbox path unchanged by visibility policy ──
@pytest.mark.asyncio
async def test_17_scheduler_outbox_path_unchanged(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="UNLISTED",
        channel_metadata={"policy_permit_unlisted": True},
        platform_custom_options={"youtube_project_verified": True},
        intent_state="APPROVED",
    )
    now = datetime.now(UTC)
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={
            "allowed_workloads": [ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value],
            "global_concurrency_limit": 10,
        },
        activate=True,
    )
    await db_session.flush()

    dec = ScheduleDecision(
        id=uuid4(),
        mission_id=b["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=b["intent"].id,
        action=ScheduleAction.SCHEDULE.value,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now,
        scheduled_end_at=now + timedelta(minutes=5),
        reason="Test",
        policy_id=policy.id,
        policy_version="1.0.0",
        policy_checksum="chk",
        guardian_epoch=1,
        idempotency_key=uuid4().hex,
        evaluated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(dec)
    await db_session.flush()

    res = ScheduleReservation(
        id=uuid4(),
        decision_id=dec.id,
        channel_id=b["channel"].id,
        mission_id=b["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=b["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now,
        scheduled_end_at=now + timedelta(minutes=5),
        state=ReservationState.ACTIVE.value,
        policy_id=dec.policy_id,
        policy_version="1.0.0",
        policy_checksum="chk",
        guardian_epoch=1,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(res)
    await db_session.flush()

    outbox = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=res.id,
        task_id=b["task"].id,
        mission_id=b["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={
            "task_id": str(b["task"].id),
            "requested_privacy_status": b["intent"].requested_privacy_status,
        },
        idempotency_key=f"outbox-{uuid4().hex[:16]}",
        status=DispatchOutboxStatus.PENDING.value,
        scheduled_send_at=now,
    )
    db_session.add(outbox)
    await db_session.commit()

    loaded = await db_session.get(SchedulerDispatchOutbox, outbox.id)
    assert loaded is not None
    assert loaded.celery_args["requested_privacy_status"] == "UNLISTED"
    assert loaded.status == DispatchOutboxStatus.PENDING.value


# ── Test 18: duplicate-delivery protection unchanged ──
@pytest.mark.asyncio
async def test_18_duplicate_delivery_protection_unchanged(db_session: AsyncSession):
    b = await create_p17f_bundle(db_session, requested_privacy="PRIVATE", intent_state="APPROVED")

    # Worker 1 claims intent
    b["intent"].state = PublishIntentState.CLAIMED.value
    b["intent"].claimed_by_worker_id = "worker-1"
    b["intent"].lease_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    await db_session.commit()

    # Worker 2 attempts execute_publish on claimed intent
    with pytest.raises(PublishExecutionError, match="No approved or reclaimable PublishIntent found"):
        await PublishExecutionService.execute_publish(
            db_session, task_id=b["task"].id, worker_id="worker-2"
        )


# ── Test 19: PRIVATE P17-E behavior regression preserved ──
@pytest.mark.asyncio
async def test_19_private_p17e_regression_preserved(db_session: AsyncSession):
    # Strict canonical canary setup under private canary mode
    b = await create_p17f_bundle(db_session, requested_privacy="PRIVATE", intent_state="APPROVED")
    settings = Settings(publisher_private_canary_mode=True)
    decision = ControlledVisibilityPolicy.evaluate_visibility(
        intent=b["intent"], channel=b["channel"], account=b["account"], settings=settings
    )
    assert decision.is_allowed is True
    assert decision.effective_privacy == PrivacyStatus.PRIVATE
    assert decision.fallback_applied is False


# ── Test 20: dashboard/API expose requested/effective state safely if already supported ──
@pytest.mark.asyncio
async def test_20_dashboard_api_exposes_requested_and_effective(db_session: AsyncSession):
    b = await create_p17f_bundle(
        db_session,
        requested_privacy="PUBLIC",
        channel_metadata={"policy_permit_public": True},
        platform_custom_options={
            "youtube_project_verified": False,
            "privacy_fallback_allowed": True,
        },
        intent_state="APPROVED",
    )
    now = datetime.now(UTC)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=b["intent"].id,
        attempt_number=1,
        state=PublishAttemptState.SUCCEEDED.value,
        idempotency_key=f"idem-{uuid4().hex[:12]}",
        effective_privacy_status="PRIVATE",
        provider_video_id="vid-test-20",
        provider_url="https://youtu.be/vid-test-20",
        started_at=now - timedelta(minutes=2),
        completed_at=now,
    )
    db_session.add(attempt)
    await db_session.commit()

    # Query details through PublisherOperationsQueryService
    detail = await PublisherOperationsQueryService.get_publication_detail(
        db_session, intent_id=b["intent"].id
    )
    assert detail is not None
    assert detail["requested_privacy_status"] == "PUBLIC"
    assert len(detail["attempts"]) == 1
    assert detail["attempts"][0]["effective_privacy_status"] == "PRIVATE"

    # Query history
    history = await PublisherOperationsQueryService.get_recent_history(
        db_session, channel_id=b["channel"].id
    )
    assert history["total_count"] >= 1
    hist_item = [h for h in history["items"] if h["requested_privacy_status"] == "PUBLIC"][0]
    assert hist_item["requested_privacy_status"] == "PUBLIC"
    assert hist_item["effective_privacy_status"] == "PRIVATE"
