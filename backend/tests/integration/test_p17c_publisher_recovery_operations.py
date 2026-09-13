"""P17-C publisher retry, hold, dead-letter, and operator recovery contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.error_sanitizer import sanitize_error, sanitize_sensitive_text
from omega.application.publisher.failure_policy import (
    CURRENT_ERROR_TO_ACTION_MATRIX,
    PublisherFailureEvidence,
    PublisherRecoveryAction,
    determine_publisher_recovery_action,
)
from omega.application.publisher.handoff_relay import (
    MAX_HANDOFF_ATTEMPTS,
    HandoffRelayService,
    handoff_backoff_seconds,
)
from omega.application.publisher.recovery_operations import (
    PublisherRecoveryOperationsService,
    RecoveryOperationRejected,
)
from omega.domain.publisher import (
    HandoffStatus,
    PublishAttemptState,
    PublisherErrorCategory,
    PublishIntentState,
    ReconciliationStatus,
)
from omega.domain.scheduler import (
    ReservationState,
    ScheduleAction,
    ScheduleTargetType,
    ScheduleWorkloadCategory,
)
from omega.infrastructure.models import (
    PublishAttempt,
    PublishAttemptTransition,
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
    ScheduleDecision,
    ScheduleReservation,
    UploadSession,
)
from tests.integration.test_p17a_publish_scheduler import create_fixture_bundle
from tests.integration.test_publisher_services import (
    setup_publisher_fixtures as setup_p17c_fixtures,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("publisher_test_env")


def policy(
    category: PublisherErrorCategory | None,
    *,
    state: PublishAttemptState = PublishAttemptState.RETRYABLE_FAILED,
    reconciliation: ReconciliationStatus | None = None,
    evidence: PublisherFailureEvidence | None = None,
    handoff: HandoffStatus | None = None,
) -> PublisherRecoveryAction:
    return determine_publisher_recovery_action(
        error_category=category,
        attempt_state=state,
        reconciliation_status=reconciliation,
        evidence=evidence,
        handoff_status=handoff,
    )


def test_transient_network_maps_to_retry():
    assert policy(PublisherErrorCategory.NETWORK_TRANSIENT) == PublisherRecoveryAction.RETRY


def test_provider_5xx_maps_to_retry():
    assert policy(PublisherErrorCategory.PROVIDER_5XX) == PublisherRecoveryAction.RETRY


def test_rate_limit_maps_to_retry():
    assert policy(PublisherErrorCategory.RATE_LIMITED) == PublisherRecoveryAction.RETRY


def test_quota_exceeded_maps_to_retry():
    assert policy(PublisherErrorCategory.QUOTA_EXCEEDED) == PublisherRecoveryAction.RETRY


def test_invalid_media_is_terminal():
    assert policy(PublisherErrorCategory.INVALID_MEDIA) == PublisherRecoveryAction.TERMINAL


def test_invalid_metadata_is_terminal():
    assert policy(PublisherErrorCategory.INVALID_METADATA) == PublisherRecoveryAction.TERMINAL


def test_auth_revoked_is_terminal():
    assert policy(PublisherErrorCategory.AUTH_REVOKED) == PublisherRecoveryAction.TERMINAL


def test_auth_expired_without_refresh_path_is_terminal():
    evidence = PublisherFailureEvidence(auth_refresh_available=False)
    assert policy(PublisherErrorCategory.AUTH_EXPIRED, evidence=evidence) == PublisherRecoveryAction.TERMINAL


def test_unknown_outcome_never_blind_retries():
    assert (
        policy(PublisherErrorCategory.UNKNOWN_OUTCOME, state=PublishAttemptState.UNKNOWN)
        == PublisherRecoveryAction.MANUAL_HOLD
    )


def test_ambiguous_reconciliation_is_manual_hold():
    assert (
        policy(
            PublisherErrorCategory.NETWORK_TRANSIENT,
            state=PublishAttemptState.UNKNOWN,
            reconciliation=ReconciliationStatus.MANUAL_HOLD,
        )
        == PublisherRecoveryAction.MANUAL_HOLD
    )


def test_confirmed_provider_success_never_retries():
    assert (
        policy(
            PublisherErrorCategory.NETWORK_TRANSIENT,
            reconciliation=ReconciliationStatus.CONFIRMED_SUCCESS,
        )
        == PublisherRecoveryAction.CONFIRMED_SUCCESS
    )


def test_terminal_provider_session_is_manual_hold():
    evidence = PublisherFailureEvidence(has_upload_session=True, session_terminal=True)
    assert policy(PublisherErrorCategory.NETWORK_TRANSIENT, evidence=evidence) == PublisherRecoveryAction.MANUAL_HOLD


def test_duplicate_or_conflict_requires_manual_hold():
    assert policy(PublisherErrorCategory.DUPLICATE_OR_CONFLICT) == PublisherRecoveryAction.MANUAL_HOLD


def test_dead_letter_state_has_policy_precedence():
    assert policy(PublisherErrorCategory.NETWORK_TRANSIENT, handoff=HandoffStatus.DEAD_LETTER) == PublisherRecoveryAction.DEAD_LETTER


def test_error_action_matrix_covers_entire_existing_taxonomy():
    assert set(CURRENT_ERROR_TO_ACTION_MATRIX) == set(PublisherErrorCategory)


def test_handoff_backoff_is_deterministic_and_bounded():
    assert [handoff_backoff_seconds(i) for i in range(1, 7)] == [10, 20, 40, 80, 160, 300]


def test_stored_error_redacts_tokens_and_authorization():
    result = sanitize_error(
        RuntimeError("Authorization: Bearer top-secret access_token=also-secret")
    )
    assert "top-secret" not in result
    assert "also-secret" not in result
    assert "[REDACTED]" in result


def test_stored_error_redacts_session_uri_and_query_credentials():
    result = sanitize_sensitive_text(
        "upload failed https://upload.example/session/abc?access_token=secret-value"
    )
    assert "https://" not in result
    assert "secret-value" not in result
    assert "[REDACTED_URL]" in result


async def make_intent_attempt(
    db_session: AsyncSession,
    fixture: dict,
    *,
    state: PublishAttemptState = PublishAttemptState.RETRYABLE_FAILED,
    reconciliation: ReconciliationStatus | None = None,
    error: str = "safe failure",
) -> tuple[PublishIntent, PublishAttempt]:
    intent = PublishIntent(
        id=uuid4(),
        mission_id=fixture["mission"].id,
        task_id=fixture["task"].id,
        channel_id=fixture["channel"].id,
        platform_account_id=fixture["account"].id,
        media_artifact_id=fixture["artifact"].id,
        media_artifact_checksum=fixture["artifact"].content_hash,
        revision_number=1,
        title="P17-C recovery test",
        description="offline",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={},
        intent_checksum="a" * 64,
        state=PublishIntentState.APPROVED.value,
        attempt_generation=1,
    )
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=uuid4().hex + uuid4().hex,
        state=state.value,
        error_category=(
            PublisherErrorCategory.UNKNOWN_OUTCOME.value
            if state == PublishAttemptState.UNKNOWN
            else PublisherErrorCategory.NETWORK_TRANSIENT.value
        ),
        error_message=error,
        reconciliation_status=reconciliation.value if reconciliation else None,
    )
    db_session.add_all([intent, attempt])
    await db_session.commit()
    return intent, attempt


async def make_handoff(
    db_session: AsyncSession,
    fixture: dict,
    intent: PublishIntent,
    attempt: PublishAttempt,
    *,
    status: HandoffStatus = HandoffStatus.PENDING,
    attempt_count: int = 0,
    last_error: str | None = None,
) -> PublisherSchedulerHandoffOutbox:
    now = datetime.now(UTC)
    row = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        task_id=fixture["task"].id,
        mission_id=fixture["mission"].id,
        earliest_retry_at=now,
        reason="test retry",
        idempotency_key=uuid4().hex + uuid4().hex,
        status=status.value,
        attempt_count=attempt_count,
        next_attempt_at=now - timedelta(seconds=1),
        last_error=last_error,
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.mark.asyncio
async def test_retry_handoff_creation_is_idempotent(
    db_session: AsyncSession, setup_p17c_fixtures  # noqa: F811
):
    f = setup_p17c_fixtures
    intent, attempt = await make_intent_attempt(db_session, f)
    retry_at = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=30)
    first = await PublisherRecoveryOperationsService.create_retry_handoff(
        db_session,
        intent=intent,
        attempt=attempt,
        task_id=f["task"].id,
        earliest_retry_at=retry_at,
        reason="network transient",
    )
    second = await PublisherRecoveryOperationsService.create_retry_handoff(
        db_session,
        intent=intent,
        attempt=attempt,
        task_id=f["task"].id,
        earliest_retry_at=retry_at,
        reason="network transient",
    )
    await db_session.commit()
    count = (
        await db_session.execute(
            select(func.count()).select_from(PublisherSchedulerHandoffOutbox).where(
                PublisherSchedulerHandoffOutbox.publish_attempt_id == attempt.id
            )
        )
    ).scalar_one()
    assert first.id == second.id
    assert count == 1


@pytest.mark.asyncio
async def test_max_handoff_attempt_moves_to_dead_letter_and_no_sixth_claim(
    db_session: AsyncSession, setup_p17c_fixtures  # noqa: F811
):
    f = setup_p17c_fixtures
    intent, attempt = await make_intent_attempt(db_session, f)
    row = await make_handoff(
        db_session,
        f,
        intent,
        attempt,
        status=HandoffStatus.CLAIMED,
        attempt_count=MAX_HANDOFF_ATTEMPTS,
    )
    row.claim_token = uuid4()
    token = row.claim_token
    await db_session.commit()
    await HandoffRelayService._handle_handoff_failure(
        db_session, row.id, token, "https://secret/session?token=hidden"
    )
    await db_session.refresh(row)
    assert row.status == HandoffStatus.DEAD_LETTER.value
    assert "https://" not in (row.last_error or "")
    assert await HandoffRelayService.process_pending_handoffs(db_session, handoff_ids=[row.id]) == 0
    assert row.attempt_count == MAX_HANDOFF_ATTEMPTS


@pytest.mark.asyncio
async def test_dead_letter_manual_requeue_is_fenced_and_audited(
    db_session: AsyncSession, setup_p17c_fixtures  # noqa: F811
):
    f = setup_p17c_fixtures
    intent, attempt = await make_intent_attempt(db_session, f)
    row = await make_handoff(
        db_session,
        f,
        intent,
        attempt,
        status=HandoffStatus.DEAD_LETTER,
        attempt_count=MAX_HANDOFF_ATTEMPTS,
        last_error="old failure",
    )
    recovered = await PublisherRecoveryOperationsService.requeue_dead_letter(
        db_session, row.id, actor="operator@example", reason="provider incident resolved"
    )
    audit = (
        await db_session.execute(
            select(PublishAttemptTransition).where(
                PublishAttemptTransition.publish_attempt_id == attempt.id
            )
        )
    ).scalar_one()
    assert recovered.status == HandoffStatus.PENDING.value
    assert recovered.attempt_count == 0
    assert audit.actor == "operator@example"
    assert "prior_attempt_count=5" in audit.reason


@pytest.mark.asyncio
async def test_dead_letter_requeue_rejects_unresolved_unknown(
    db_session: AsyncSession, setup_p17c_fixtures  # noqa: F811
):
    f = setup_p17c_fixtures
    intent, attempt = await make_intent_attempt(
        db_session,
        f,
        state=PublishAttemptState.UNKNOWN,
        reconciliation=ReconciliationStatus.MANUAL_HOLD,
    )
    row = await make_handoff(
        db_session,
        f,
        intent,
        attempt,
        status=HandoffStatus.DEAD_LETTER,
        attempt_count=MAX_HANDOFF_ATTEMPTS,
    )
    with pytest.raises(RecoveryOperationRejected, match="UNKNOWN"):
        await PublisherRecoveryOperationsService.requeue_dead_letter(
            db_session, row.id, actor="operator", reason="unsafe request"
        )


@pytest.mark.asyncio
async def test_manual_hold_and_dead_letter_queries_are_secret_safe(
    db_session: AsyncSession, setup_p17c_fixtures  # noqa: F811
):
    f = setup_p17c_fixtures
    intent, attempt = await make_intent_attempt(
        db_session,
        f,
        state=PublishAttemptState.UNKNOWN,
        reconciliation=ReconciliationStatus.MANUAL_HOLD,
        error="Authorization: Bearer secret https://upload.example/session/123",
    )
    await make_handoff(
        db_session,
        f,
        intent,
        attempt,
        status=HandoffStatus.DEAD_LETTER,
        attempt_count=5,
        last_error="token=secret https://upload.example/session/123",
    )
    holds = await PublisherRecoveryOperationsService.get_manual_holds(db_session)
    letters = await PublisherRecoveryOperationsService.get_dead_letters(db_session)
    rendered = repr((holds, letters))
    assert "secret" not in rendered
    assert "upload.example" not in rendered
    assert "session_uri" not in rendered


@pytest.mark.asyncio
async def test_resumable_recovery_reuses_same_upload_session(
    db_session: AsyncSession, setup_p17c_fixtures  # noqa: F811
):
    f = setup_p17c_fixtures
    _intent, attempt = await make_intent_attempt(
        db_session,
        f,
        state=PublishAttemptState.UNKNOWN,
        reconciliation=ReconciliationStatus.MANUAL_HOLD,
    )
    upload = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://upload.example/existing-only",
        total_bytes=100,
        bytes_uploaded=40,
        chunk_size_bytes=10,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(upload)
    await db_session.commit()
    result = await PublisherRecoveryOperationsService.authorize_existing_session_resume(
        db_session, attempt.id, actor="operator", reason="offset authoritatively known"
    )
    context = await PublisherRecoveryOperationsService.get_attempt_recovery_context(
        db_session, attempt.id
    )
    assert result["upload_session_id"] == upload.id
    assert result["provider_offset"] == 40
    assert context and context["upload_session"]["session_id"] == upload.id
    assert "session_uri" not in context["upload_session"]


@pytest.mark.asyncio
async def test_dead_letter_is_not_automatically_claimed_by_two_workers(
    db_session: AsyncSession, setup_p17c_fixtures  # noqa: F811
):
    f = setup_p17c_fixtures
    intent, attempt = await make_intent_attempt(db_session, f)
    row = await make_handoff(
        db_session,
        f,
        intent,
        attempt,
        status=HandoffStatus.DEAD_LETTER,
        attempt_count=MAX_HANDOFF_ATTEMPTS,
    )
    first = await HandoffRelayService.process_pending_handoffs(
        db_session, worker_id="relay-a", handoff_ids=[row.id]
    )
    second = await HandoffRelayService.process_pending_handoffs(
        db_session, worker_id="relay-b", handoff_ids=[row.id]
    )
    assert (first, second) == (0, 0)


@pytest.mark.asyncio
async def test_recovery_observability_keeps_retry_layers_distinct(
    db_session: AsyncSession, setup_p17c_fixtures  # noqa: F811
):
    f = setup_p17c_fixtures
    intent, attempt = await make_intent_attempt(db_session, f)
    await make_handoff(db_session, f, intent, attempt)
    metrics = await PublisherRecoveryOperationsService.get_recovery_observability(db_session)
    assert metrics["retry_pending_count"] == 1
    assert metrics["max_attempts"] == MAX_HANDOFF_ATTEMPTS
    assert "durable_dispatch" not in metrics


def test_provider_handoff_and_broker_dispatch_are_distinct_layers():
    from omega.application import durable_dispatch

    assert HandoffStatus.DEAD_LETTER.value == durable_dispatch.DEAD_LETTER
    assert PublisherSchedulerHandoffOutbox.__tablename__ != "durable_dispatch_intents"


@pytest.mark.asyncio
async def test_human_schedule_survives_first_second_and_exhausted_technical_retries(
    db_session: AsyncSession,
):
    bundle = await create_fixture_bundle(db_session)
    now = datetime.now(UTC)
    human_schedule = now + timedelta(days=3)
    decision = ScheduleDecision(
        id=uuid4(),
        mission_id=bundle["mission"].id,
        task_id=bundle["task"].id,
        channel_id=bundle["channel"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        action=ScheduleAction.SCHEDULE.value,
        scheduled_start_at=human_schedule,
        scheduled_end_at=human_schedule + timedelta(minutes=10),
        reason="original human schedule",
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
        idempotency_key=f"p17c:{uuid4().hex}",
        evaluated_at=now,
    )
    reservation = ScheduleReservation(
        id=uuid4(),
        decision_id=decision.id,
        channel_id=bundle["channel"].id,
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=human_schedule,
        scheduled_end_at=human_schedule + timedelta(minutes=10),
        state=ReservationState.ACTIVE.value,
        priority_score=1.0,
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
    )
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex + uuid4().hex,
        state=PublishAttemptState.RETRYABLE_FAILED.value,
        error_category=PublisherErrorCategory.NETWORK_TRANSIENT.value,
    )
    handoff = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        publish_attempt_id=attempt.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        earliest_retry_at=now,
        reason="technical retry",
        idempotency_key=uuid4().hex + uuid4().hex,
        status=HandoffStatus.CLAIMED.value,
        claim_token=uuid4(),
        attempt_count=1,
        next_attempt_at=now,
    )
    db_session.add_all([decision, reservation, attempt, handoff])
    await db_session.commit()

    for attempt_count in (1, 2, MAX_HANDOFF_ATTEMPTS):
        handoff.status = HandoffStatus.CLAIMED.value
        handoff.claim_token = uuid4()
        handoff.attempt_count = attempt_count
        token = handoff.claim_token
        await db_session.commit()
        await HandoffRelayService._handle_handoff_failure(
            db_session, handoff.id, token, "bounded technical retry"
        )
        await db_session.refresh(reservation)
        assert reservation.scheduled_start_at == human_schedule

    await db_session.refresh(handoff)
    assert handoff.status == HandoffStatus.DEAD_LETTER.value
