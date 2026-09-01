"""Integration tests for P0-CODE-1 Publish Readiness Validation & Shadow Mode.

Tests all 15 required verification criteria:
1. valid readiness path returns ready
2. no initialize_resumable_upload call
3. no upload_chunk call
4. no PublishAttempt row created
5. artifact missing -> fail closed
6. checksum mismatch -> fail closed
7. inactive account -> fail closed
8. privacy policy violation -> fail closed
9. Guardian failure -> no provider call
10. expired/invalid OAuth readiness -> fail closed
11. network preflight failure -> fail closed
12. sanitized result contains no secrets
13. repeated same semantic validation is idempotent/auditable
14. shadow RESULT_CONFIRMED clearly means internal validation only
15. existing real publish path remains unchanged and still works
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.dispatch_service import AutonomyDispatchService
from omega.application.publisher.adapters.base import (
    ChunkUploadResult,
    UploadSessionInitResult,
)
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.publish_service import (
    PublishExecutionService,
)
from omega.domain.autonomy import (
    ActionAttemptOutcomeStatus,
    ActionRiskClass,
    ActionType,
    AutonomyLevel,
    AutonomyLoopState,
)
from omega.domain.channel import ChannelState, Platform
from omega.domain.mission import MissionState
from omega.domain.publisher import (
    PlatformAccountStatus,
    PrivacyStatus,
    PublishAttemptState,
    PublishIntentCreate,
    PublishIntentState,
)
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    AutonomyActionPlan,
    AutonomyPolicySnapshot,
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
    PublishIntent,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
)
from omega.infrastructure.vault import get_credential_vault

pytestmark = pytest.mark.usefixtures("publisher_test_env")


async def _create_artifact_with_ancestry(
    db_session: AsyncSession, channel_id: uuid4, art_hash: str
) -> MediaArtifact:
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
        title="Shadow Topic",
        normalized_title="shadow topic",
        source_name="Manual Entry",
        summary="Shadow topic summary",
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
        title="Shadow Brief Title",
        summary="Shadow brief summary",
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
        title="Shadow Script v1",
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
async def setup_shadow_fixtures(db_session: AsyncSession, monkeypatch):
    monkeypatch.setenv("OMEGA_TEST_MODE", "1")
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

    channel = Channel(
        id=uuid4(),
        slug=f"shadow-pub-chan-{uuid4().hex[:8]}",
        name="Shadow Pub Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        title="Shadow Mission",
        objective="Validate readiness without side effects",
        state=MissionState.RUNNING.value,
        autonomy_level=AutonomyLevel.SUPERVISED.value,
        channel_id=channel.id,
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="PUBLISH_VIDEO",
        title="Shadow Publish Task",
        state=TaskState.READY.value,
    )
    db_session.add(task)

    art_hash = "a" * 64
    artifact = await _create_artifact_with_ancestry(db_session, channel.id, art_hash)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="Shadow Channel Account",
        external_account_id="UC_SHADOW_123",
        status=PlatformAccountStatus.ACTIVE.value,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    db_session.add(account)
    await db_session.flush()

    vault = get_credential_vault()
    enc_acc, v1 = vault.encrypt("mock_secret_access_token_12345")
    enc_ref, v2 = vault.encrypt("mock_secret_refresh_token_67890")
    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=enc_acc,
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=2),
        encrypted_refresh_token=enc_ref,
        key_version=v1,
    )
    db_session.add(vault_entry)

    # Publish Intent in APPROVED state
    intent_payload = PublishIntentCreate(
        mission_id=mission.id,
        task_id=task.id,
        channel_id=channel.id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum=artifact.content_hash,
        title="Canary Video Title",
        description="Canary Video Description",
        tags=["canary", "shadow"],
        requested_privacy_status=PrivacyStatus.PRIVATE,
        made_for_kids=False,
    )
    intent = await PublishIntentService.create_publish_intent(
        db_session, intent_payload, initial_state=PublishIntentState.APPROVED
    )

    await db_session.commit()
    return {
        "channel": channel,
        "mission": mission,
        "task": task,
        "artifact": artifact,
        "account": account,
        "vault_entry": vault_entry,
        "intent": intent,
    }


@pytest.mark.asyncio
async def test_01_valid_readiness_path_returns_ready(
    db_session: AsyncSession, setup_shadow_fixtures, monkeypatch
):
    """1. Valid readiness path returns is_ready=True with all check booleans True."""
    f = setup_shadow_fixtures

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is True
    assert report.artifact_verified is True
    assert report.guardian_valid is True
    assert report.account_valid is True
    assert report.privacy_valid is True
    assert report.credentials_ready is True
    assert report.network_preflight_passed is True
    assert report.payload_digest is not None
    assert len(report.validation_errors) == 0


@pytest.mark.asyncio
async def test_02_no_initialize_resumable_upload_call(
    db_session: AsyncSession, setup_shadow_fixtures, monkeypatch
):
    """2. Verifies adapter.initialize_resumable_upload is NEVER called."""
    f = setup_shadow_fixtures

    mock_init = AsyncMock(
        side_effect=AssertionError("initialize_resumable_upload MUST NOT BE CALLED in shadow mode")
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is True
    mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_03_no_upload_chunk_call(
    db_session: AsyncSession, setup_shadow_fixtures, monkeypatch
):
    """3. Verifies adapter.upload_chunk is NEVER called."""
    f = setup_shadow_fixtures

    mock_chunk = AsyncMock(
        side_effect=AssertionError("upload_chunk MUST NOT BE CALLED in shadow mode")
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_chunk,
    )

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is True
    mock_chunk.assert_not_called()


@pytest.mark.asyncio
async def test_04_no_publish_attempt_row_created(db_session: AsyncSession, setup_shadow_fixtures):
    """4. Verifies zero PublishAttempt rows are created during shadow readiness validation."""
    f = setup_shadow_fixtures

    # Count before
    count_before = (
        await db_session.execute(
            select(func.count(PublishAttempt.id)).where(
                PublishAttempt.publish_intent_id == f["intent"].id
            )
        )
    ).scalar()

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )
    assert report.is_ready is True

    # Count after
    count_after = (
        await db_session.execute(
            select(func.count(PublishAttempt.id)).where(
                PublishAttempt.publish_intent_id == f["intent"].id
            )
        )
    ).scalar()

    assert count_before == 0
    assert count_after == 0


@pytest.mark.asyncio
async def test_05_missing_artifact_fails_closed(
    db_session: AsyncSession, setup_shadow_fixtures, monkeypatch
):
    """5. Missing artifact fails closed (is_ready=False, artifact_verified=False)."""
    f = setup_shadow_fixtures

    # Disable OMEGA_TEST_MODE so non-existent file on disk fails check
    monkeypatch.delenv("OMEGA_TEST_MODE", raising=False)

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is False
    assert report.artifact_verified is False
    assert any("does not exist on disk" in err.lower() for err in report.validation_errors)


@pytest.mark.asyncio
async def test_06_checksum_mismatch_fails_closed(
    db_session: AsyncSession, setup_shadow_fixtures, tmp_path, monkeypatch
):
    """6. Checksum mismatch fails closed."""
    f = setup_shadow_fixtures

    # Create dummy file with mismatching content on disk
    test_file = tmp_path / "videos" / "test_render.mp4"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_bytes(b"actual content on disk that does not match")

    monkeypatch.setenv("MEDIA_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("OMEGA_TEST_MODE", raising=False)

    # Artifact recorded hash is different
    f["artifact"].content_hash = "0" * 64
    await db_session.commit()

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is False
    assert report.artifact_verified is False
    assert any("checksum mismatch" in err.lower() for err in report.validation_errors)


@pytest.mark.asyncio
async def test_07_inactive_account_fails_closed(db_session: AsyncSession, setup_shadow_fixtures):
    """7. Inactive account fails closed."""
    f = setup_shadow_fixtures

    f["account"].status = PlatformAccountStatus.REVOKED.value
    await db_session.commit()

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is False
    assert report.account_valid is False
    assert any("not active" in err.lower() for err in report.validation_errors)


@pytest.mark.asyncio
async def test_08_privacy_policy_violation_fails_closed(
    db_session: AsyncSession, setup_shadow_fixtures
):
    """8. Privacy policy violation (PUBLIC without fallback) fails closed."""
    f = setup_shadow_fixtures

    f["intent"].requested_privacy_status = PrivacyStatus.PUBLIC.value
    f["intent"].platform_custom_options = {"privacy_fallback_allowed": False}
    await db_session.commit()

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is False
    assert report.privacy_valid is False
    assert any("privacy" in err.lower() for err in report.validation_errors)


@pytest.mark.asyncio
async def test_09_guardian_failure_prevents_provider_call(
    db_session: AsyncSession, setup_shadow_fixtures, monkeypatch
):
    """9. Guardian block prevents readiness and makes zero provider calls."""
    f = setup_shadow_fixtures

    from omega.domain.guardian import (
        CheckTriggerType,
        GuardianAction,
        GuardianCheckpoint,
        GuardianCheckResponse,
        GuardianDecisionResponse,
        GuardianGateState,
    )

    check_id = uuid4()
    now = datetime.now(UTC)
    mock_engine_check = AsyncMock(
        return_value=GuardianCheckResponse(
            id=check_id,
            mission_id=f["mission"].id,
            trigger_type=CheckTriggerType.PRE_EXTERNAL_SIDE_EFFECT,
            checkpoint=GuardianCheckpoint.PRE_EXTERNAL_SIDE_EFFECT,
            ruleset_id=uuid4(),
            ruleset_version="1.0.0",
            ruleset_checksum="chk",
            status="COMPLETED",
            idempotency_key=uuid4().hex,
            guardian_epoch=1,
            diagnostic_context={},
            started_at=now,
            created_at=now,
            decision=GuardianDecisionResponse(
                id=uuid4(),
                guardian_check_id=check_id,
                action=GuardianAction.FORCE_FAIL,
                reason="Simulated Guardian policy rejection",
                resulting_gate_state=GuardianGateState.BLOCKED,
                actor="guardian-test",
                created_at=now,
            ),
        )
    )
    monkeypatch.setattr(
        "omega.application.guardian.engine.GuardianEngine.execute_check",
        mock_engine_check,
    )

    mock_init = AsyncMock(side_effect=AssertionError("Must not call provider on Guardian block"))
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is False
    assert report.guardian_valid is False
    assert any("guardian validation blocked" in err.lower() for err in report.validation_errors)
    mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_10_missing_or_invalid_credentials_fails_closed(
    db_session: AsyncSession, setup_shadow_fixtures
):
    """10. Missing or corrupt credential vault entry fails closed."""
    f = setup_shadow_fixtures

    # Corrupt ciphertext
    f["vault_entry"].encrypted_access_token = "invalid_corrupt_ciphertext_bytes"
    await db_session.commit()

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is False
    assert report.credentials_ready is False
    assert any("credential decryption failed" in err.lower() for err in report.validation_errors)


@pytest.mark.asyncio
async def test_11_network_preflight_failure_fails_closed(
    db_session: AsyncSession, setup_shadow_fixtures, monkeypatch
):
    """11. Network preflight failure fails closed."""
    f = setup_shadow_fixtures

    mock_preflight = AsyncMock(
        side_effect=RuntimeError("Egress connection blocked by network policy")
    )
    monkeypatch.setattr(
        "omega.application.network.preflight.NetworkPreflightService.preflight",
        mock_preflight,
    )

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report.is_ready is False
    assert report.network_preflight_passed is False
    assert any("network preflight failed" in err.lower() for err in report.validation_errors)


@pytest.mark.asyncio
async def test_12_sanitized_result_contains_no_secrets(
    db_session: AsyncSession, setup_shadow_fixtures
):
    """12. Sanitized report contains zero tokens, secrets, or raw headers."""
    f = setup_shadow_fixtures

    report = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    raw_json = report.model_dump_json()
    raw_dict = report.model_dump()

    # Secret strings from setup fixture
    assert "mock_secret_access_token_12345" not in raw_json
    assert "mock_secret_refresh_token_67890" not in raw_json
    assert "Authorization" not in raw_json
    assert "Bearer" not in raw_json
    assert "access_token" not in raw_dict
    assert "refresh_token" not in raw_dict


@pytest.mark.asyncio
async def test_13_repeated_same_semantic_validation_is_idempotent(
    db_session: AsyncSession, setup_shadow_fixtures
):
    """13. Repeated validation returns identical payload digest and zero DB mutations."""
    f = setup_shadow_fixtures

    report_1 = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )
    report_2 = await PublishExecutionService.validate_publish_readiness(
        session=db_session,
        task_id=f["task"].id,
    )

    assert report_1.is_ready is True
    assert report_2.is_ready is True
    assert report_1.payload_digest == report_2.payload_digest

    # Verify intent state remained unchanged
    res = await db_session.execute(select(PublishIntent).where(PublishIntent.id == f["intent"].id))
    current_intent = res.scalar_one()
    assert current_intent.state == PublishIntentState.APPROVED.value


@pytest.mark.asyncio
async def test_14_shadow_result_confirmed_means_internal_validation_only(
    db_session: AsyncSession, setup_shadow_fixtures
):
    """14. Shadow dispatch records RESULT_CONFIRMED representing internal validation only."""
    f = setup_shadow_fixtures

    from omega.application.autonomy.helpers import compute_precondition_fingerprint
    from omega.application.autonomy.loop_service import AutonomyLoopService

    # Initialize loop properly via AutonomyLoopService
    loop = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=f["mission"].id,
        channel_id=f["channel"].id,
        autonomy_level=AutonomyLevel.SUPERVISED,
        daily_cap_usd=Decimal("10.0000"),
        lifetime_cap_usd=Decimal("50.0000"),
    )

    from omega.application.autonomy.observation_service import (
        AutonomyObservationService,
    )

    # Capture observation snapshot
    obs = await AutonomyObservationService.capture_snapshot(
        session=db_session,
        loop_id=loop.id,
    )

    stmt_pol = select(AutonomyPolicySnapshot).where(AutonomyPolicySnapshot.loop_id == loop.id)
    policy = (await db_session.execute(stmt_pol)).scalars().first()

    # Allocate iteration 1
    iteration = await AutonomyLoopService.allocate_iteration(
        session=db_session,
        loop_id=loop.id,
        observation_snapshot_id=obs.id,
        policy_snapshot_id=policy.id,
    )

    # Transition to PLANNING
    await AutonomyLoopService.update_operational_state(
        session=db_session,
        loop_id=loop.id,
        new_state=AutonomyLoopState.PLANNING,
    )

    fingerprint = compute_precondition_fingerprint(
        action_type=ActionType.REQUEST_PUBLISH.value,
        target_entity_id=f["task"].id,
        entity_state_token=str(f["task"].id),
        guardian_context_checksum=obs.guardian_context_checksum,
        dna_revision_id=obs.dna_revision_id,
        policy_checksum=policy.policy_checksum,
        mission_state=obs.raw_observation.get("mission_state", "ACTIVE"),
    )

    plan = AutonomyActionPlan(
        id=uuid4(),
        iteration_id=iteration.id,
        plan_sequence=1,
        action_type=ActionType.REQUEST_PUBLISH.value,
        risk_class=ActionRiskClass.EXTERNAL_SIDE_EFFECT.value,
        target_subsystem="PUBLISHER",
        requires_approval=False,
        target_entity_id=f["task"].id,
        parameters={"is_shadow_mode": True, "validate_readiness_only": True},
        estimated_cost_usd=Decimal("0.0000"),
        precondition_fingerprint=fingerprint,
        action_checksum="act_chk_shadow",
    )
    db_session.add(plan)
    await db_session.commit()

    # Dispatch action through AutonomyDispatchService
    attempt, outcome = await AutonomyDispatchService.prepare_and_dispatch(
        session=db_session,
        action_plan_id=plan.id,
        worker_id="shadow-worker-test",
    )

    assert outcome.status == ActionAttemptOutcomeStatus.RESULT_CONFIRMED.value
    resp = outcome.subsystem_response
    assert resp["operation"] == "PUBLISH_READINESS_VALIDATION"
    assert resp["provider_side_effect"] is False
    assert resp["provider_upload_performed"] is False
    assert resp["is_ready"] is True
    assert "report" in resp

    # Confirm zero PublishAttempt rows created
    count = (
        await db_session.execute(
            select(func.count(PublishAttempt.id)).where(
                PublishAttempt.publish_intent_id == f["intent"].id
            )
        )
    ).scalar()
    assert count == 0


@pytest.mark.asyncio
async def test_15_existing_real_publish_path_works_unchanged(
    db_session: AsyncSession, setup_shadow_fixtures, monkeypatch
):
    """15. Existing real publish path remains unchanged and succeeds."""
    f = setup_shadow_fixtures

    mock_init = AsyncMock(
        return_value=UploadSessionInitResult(
            session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock123",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    mock_chunk = AsyncMock(
        return_value=ChunkUploadResult(
            next_byte_offset=2048,
            is_complete=True,
            provider_video_id="YT_VIDEO_REAL_123",
        )
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload",
        mock_init,
    )
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.upload_chunk",
        mock_chunk,
    )

    attempt = await PublishExecutionService.execute_publish(
        session=db_session,
        task_id=f["task"].id,
    )

    assert attempt.state == PublishAttemptState.SUCCEEDED.value
    assert attempt.provider_video_id == "YT_VIDEO_REAL_123"
    mock_init.assert_called_once()
    mock_chunk.assert_called_once()
