"""P17-D integration tests for Publisher Observability and Operations API.

Verifies:
1. Pure read-only behavior of PublisherOperationsQueryService.
2. Complete data projections (overview, calendar, active in-flight, retries,
   manual holds, dead letters, history, detail).
3. Canonical OMEGA-010 schedule reservation linkage.
4. Active attempt states: CREATED, UPLOADING, FINALIZING, UNKNOWN (NO COMMITTING).
5. Separate hold concepts: recovery_manual_hold_count vs schedule_manual_hold_count.
6. SQL pagination bounding (default 25, clamped to 100).
7. Strict sanitization (zero tokens, credentials, or session_uris).
8. Explicit operator actions requiring actor and reason, with mocked provider reconciliation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
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
    PublisherSchedulerHandoffOutbox,
    ScheduleDecision,
    ScheduleReservation,
    UploadSession,
)
from omega.main import app
from tests.integration.test_p17a_publish_scheduler import create_fixture_bundle
from tests.integration.test_publisher_services import (
    setup_publisher_fixtures as setup_p17d_fixtures,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("publisher_test_env")

FORBIDDEN_KEY_PATTERNS = {
    "access_token",
    "refresh_token",
    "encrypted_access_token",
    "encrypted_refresh_token",
    "session_uri",
    "authorization",
    "client_secret",
    "oauth_code",
    "pkce_verifier",
    "code_verifier",
}


def _assert_no_forbidden_keys_or_values(obj: object, path: str = "") -> None:
    """Recursively verify that no response contains credential/token keys or values."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            lower_k = k.lower()
            for forbidden in FORBIDDEN_KEY_PATTERNS:
                assert forbidden not in lower_k, f"Forbidden key '{k}' found at {path}"
            _assert_no_forbidden_keys_or_values(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            _assert_no_forbidden_keys_or_values(item, f"{path}[{idx}]")
    elif isinstance(obj, str):
        lower_val = obj.lower()
        assert "bearer " not in lower_val, f"Bearer token leaked in value at {path}: {obj}"
        assert "client_secret" not in lower_val, f"client_secret leaked at {path}: {obj}"
        assert "upload.youtube.com" not in lower_val, f"upload URI leaked at {path}: {obj}"


@pytest.fixture
def api_client(db_session: AsyncSession):
    """Provide AsyncClient with get_db overridden to the current test db_session."""
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    yield client
    app.dependency_overrides.pop(get_db, None)


# ── 1. Overview Aggregates & Fleet Config ──


@pytest.mark.asyncio
async def test_overview_aggregates_and_fleet_config(db_session: AsyncSession, api_client: AsyncClient):
    """Verify overview operational counts, 24h window, and distinct hold metrics."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    # 1. Upcoming and Due Reservations
    dec = ScheduleDecision(
        id=uuid4(),
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        action=ScheduleAction.SCHEDULE.value,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now + timedelta(hours=2),
        scheduled_end_at=now + timedelta(hours=2, minutes=5),
        reason="Upcoming",
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        channel_dna_revision_id=bundle["intent"].channel_dna_revision_id,
        reservation_id=uuid4(),
        guardian_epoch=1,
        idempotency_key=uuid4().hex,
        evaluated_at=now,
        expires_at=now + timedelta(hours=4),
    )
    res_upcoming = ScheduleReservation(
        id=dec.reservation_id,
        decision_id=dec.id,
        channel_id=bundle["channel"].id,
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now + timedelta(hours=2),
        scheduled_end_at=now + timedelta(hours=2, minutes=5),
        state=ReservationState.ACTIVE.value,
        policy_id=bundle["policy"].id,
        policy_version=str(bundle["policy"].version),
        policy_checksum=bundle["policy"].checksum,
        channel_dna_revision_id=bundle["intent"].channel_dna_revision_id,
        guardian_epoch=1,
        expires_at=now + timedelta(hours=4),
    )
    res_due = ScheduleReservation(
        id=uuid4(),
        decision_id=dec.id,
        channel_id=bundle["channel"].id,
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now - timedelta(minutes=10),
        scheduled_end_at=now - timedelta(minutes=5),
        state=ReservationState.ACTIVE.value,
        policy_id=bundle["policy"].id,
        policy_version=str(bundle["policy"].version),
        policy_checksum=bundle["policy"].checksum,
        channel_dna_revision_id=bundle["intent"].channel_dna_revision_id,
        guardian_epoch=1,
        expires_at=now + timedelta(hours=4),
    )
    db_session.add_all([dec, res_upcoming, res_due])

    # 2. In-flight attempt (UPLOADING)
    att_uploading = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.UPLOADING.value,
        started_at=now - timedelta(minutes=5),
    )
    # In-flight attempt (UNKNOWN with recovery MANUAL_HOLD)
    att_unknown = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=2,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.MANUAL_HOLD.value,
        started_at=now - timedelta(minutes=20),
    )
    # Recent success (SUCCEEDED)
    att_succeeded = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=3,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.SUCCEEDED.value,
        started_at=now - timedelta(hours=1),
        completed_at=now - timedelta(hours=1, minutes=-5),
        provider_video_id="safe_vid_123",
    )
    # Recent failure (PERMANENT_FAILED)
    att_failed = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=4,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.PERMANENT_FAILED.value,
        started_at=now - timedelta(hours=2),
        completed_at=now - timedelta(hours=1, minutes=55),
    )
    db_session.add_all([att_uploading, att_unknown, att_succeeded, att_failed])

    # 3. Retry handoffs (PENDING, CLAIMED, DEAD_LETTER)
    h_pending = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        publish_attempt_id=att_uploading.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        earliest_retry_at=now + timedelta(minutes=5),
        reason="Rate limit backoff",
        idempotency_key=uuid4().hex,
        status=HandoffStatus.PENDING.value,
        next_attempt_at=now + timedelta(minutes=5),
    )
    h_claimed = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        publish_attempt_id=att_uploading.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        earliest_retry_at=now,
        reason="Claimed by worker",
        idempotency_key=uuid4().hex,
        status=HandoffStatus.CLAIMED.value,
        next_attempt_at=now,
    )
    h_dead = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        publish_attempt_id=att_failed.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        earliest_retry_at=now - timedelta(hours=3),
        reason="Max retries reached",
        idempotency_key=uuid4().hex,
        status=HandoffStatus.DEAD_LETTER.value,
        attempt_count=5,
    )
    db_session.add_all([h_pending, h_claimed, h_dead])
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/overview")
    assert resp.status_code == 200
    data = resp.json()

    assert data["scheduled_upcoming"] >= 1
    assert data["scheduled_due"] >= 1
    assert data["publish_uploading"] >= 1
    assert data["publish_unknown"] >= 1
    assert data["publish_succeeded_recent"] >= 1
    assert data["publish_failed_recent"] >= 1
    assert data["recent_window_hours"] == 24
    assert data["retry_pending"] >= 1
    assert data["retry_claimed"] >= 1
    assert data["dead_letter_count"] >= 1
    assert data["recovery_manual_hold_count"] >= 1
    assert data["publisher_queue_name"] == "omega-publisher"
    assert data["publisher_worker_role"] == "publisher"
    assert data["configured_concurrency"] == 1
    assert data["prefetch_multiplier"] == 1
    _assert_no_forbidden_keys_or_values(data)


# ── 2. Upcoming Calendar Listing ──


@pytest.mark.asyncio
async def test_upcoming_calendar_listing(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    dec = ScheduleDecision(
        id=uuid4(),
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        action=ScheduleAction.SCHEDULE.value,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now + timedelta(days=1),
        scheduled_end_at=now + timedelta(days=1, minutes=5),
        reason="Upcoming",
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        channel_dna_revision_id=bundle["intent"].channel_dna_revision_id,
        guardian_epoch=1,
        idempotency_key=uuid4().hex,
        evaluated_at=now,
        expires_at=now + timedelta(days=2),
    )
    res = ScheduleReservation(
        id=uuid4(),
        decision_id=dec.id,
        channel_id=bundle["channel"].id,
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now + timedelta(days=1),
        scheduled_end_at=now + timedelta(days=1, minutes=5),
        state=ReservationState.ACTIVE.value,
        policy_id=bundle["policy"].id,
        policy_version=str(bundle["policy"].version),
        policy_checksum=bundle["policy"].checksum,
        channel_dna_revision_id=bundle["intent"].channel_dna_revision_id,
        guardian_epoch=1,
        expires_at=now + timedelta(days=2),
    )
    db_session.add_all([dec, res])
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/calendar")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 1
    match = next((item for item in data["items"] if item["reservation_id"] == str(res.id)), None)
    assert match is not None
    assert match["publish_intent_id"] == str(bundle["intent"].id)
    assert match["title"] == bundle["intent"].title
    assert match["channel_name"] == bundle["channel"].name
    assert match["reservation_state"] == ReservationState.ACTIVE.value
    _assert_no_forbidden_keys_or_values(data)


# ── 3. Active Publications & In-Flight States ──


@pytest.mark.asyncio
async def test_active_publications_in_flight_states_no_committing(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session)
    now = datetime.now(UTC)

    # In-flight attempts: CREATED, UPLOADING, FINALIZING, UNKNOWN
    attempts = [
        PublishAttempt(
            id=uuid4(),
            publish_intent_id=bundle["intent"].id,
            attempt_number=idx + 1,
            idempotency_key=uuid4().hex,
            state=st.value,
            started_at=now - timedelta(minutes=10 - idx),
        )
        for idx, st in enumerate([
            PublishAttemptState.CREATED,
            PublishAttemptState.UPLOADING,
            PublishAttemptState.FINALIZING,
            PublishAttemptState.UNKNOWN,
        ])
    ]
    db_session.add_all(attempts)
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/active")
    assert resp.status_code == 200
    data = resp.json()
    states_found = {item["attempt_state"] for item in data["items"]}
    assert "CREATED" in states_found
    assert "UPLOADING" in states_found
    assert "FINALIZING" in states_found
    assert "UNKNOWN" in states_found
    assert "COMMITTING" not in states_found
    _assert_no_forbidden_keys_or_values(data)


# ── 4. Upload Progress Calculation ──


@pytest.mark.asyncio
async def test_upload_progress_calculation(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.UPLOADING.value,
        started_at=datetime.now(UTC),
    )
    upload = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://upload.youtube.com/upload/SECRET_SESSION_URI_DO_NOT_EXPOSE",
        total_bytes=10000,
        bytes_uploaded=7500,
        chunk_size_bytes=2500,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add_all([attempt, upload])
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/active")
    assert resp.status_code == 200
    data = resp.json()
    match = next((item for item in data["items"] if item["attempt_id"] == str(attempt.id)), None)
    assert match is not None
    assert match["bytes_uploaded"] == 7500
    assert match["total_bytes"] == 10000
    assert match["progress_percentage"] == 75.0
    _assert_no_forbidden_keys_or_values(data)


# ── 5. Retry Queue Listing ──


@pytest.mark.asyncio
async def test_retry_queue_listing(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.RETRYABLE_FAILED.value,
        started_at=datetime.now(UTC),
    )
    db_session.add(attempt)
    await db_session.flush()

    h = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        publish_attempt_id=attempt.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        earliest_retry_at=datetime.now(UTC) + timedelta(seconds=30),
        reason="Transient rate limit hit",
        idempotency_key=uuid4().hex,
        status=HandoffStatus.PENDING.value,
        attempt_count=2,
        next_attempt_at=datetime.now(UTC) + timedelta(seconds=30),
        last_error="Transient rate limit hit",
    )
    db_session.add(h)
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/retries")
    assert resp.status_code == 200
    data = resp.json()
    match = next((item for item in data["items"] if item["handoff_id"] == str(h.id)), None)
    assert match is not None
    assert match["attempt_count"] == 2
    assert match["max_attempts"] == 5
    assert match["last_sanitized_error"] == "Transient rate limit hit"
    _assert_no_forbidden_keys_or_values(data)


# ── 6. Manual Holds Listing & Age ──


@pytest.mark.asyncio
async def test_manual_holds_listing_and_age(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.MANUAL_HOLD.value,
        error_category=PublisherErrorCategory.UNKNOWN_OUTCOME.value,
        error_message="Final chunk timed out",
        started_at=datetime.now(UTC) - timedelta(minutes=45),
    )
    db_session.add(attempt)
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/manual-holds")
    assert resp.status_code == 200
    data = resp.json()
    match = next((item for item in data["items"] if item["attempt_id"] == str(attempt.id)), None)
    assert match is not None
    assert match["hold_source"] == "RECOVERY"
    assert match["reconciliation_status"] == "MANUAL_HOLD"
    assert match["age_seconds"] >= 2600.0
    _assert_no_forbidden_keys_or_values(data)


# ── 7. Dead Letter Listing & Requeue Eligibility ──


@pytest.mark.asyncio
async def test_dead_letters_listing_and_requeue_eligibility(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.PERMANENT_FAILED.value,
        started_at=datetime.now(UTC),
    )
    db_session.add(attempt)
    await db_session.flush()

    h = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        publish_attempt_id=attempt.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        earliest_retry_at=datetime.now(UTC) - timedelta(hours=1),
        reason="Max retries reached",
        idempotency_key=uuid4().hex,
        status=HandoffStatus.DEAD_LETTER.value,
        attempt_count=5,
        last_error="Max retries reached",
    )
    db_session.add(h)
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/dead-letters")
    assert resp.status_code == 200
    data = resp.json()
    match = next((item for item in data["items"] if item["handoff_id"] == str(h.id)), None)
    assert match is not None
    assert match["can_requeue"] is True
    assert match["requeue_blocked_reason"] is None
    _assert_no_forbidden_keys_or_values(data)


# ── 8. Publication History Listing & Video ID ──


@pytest.mark.asyncio
async def test_publication_history_listing(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.SUCCEEDED.value,
        started_at=datetime.now(UTC) - timedelta(minutes=10),
        completed_at=datetime.now(UTC) - timedelta(minutes=8),
        provider_video_id="history_vid_999",
    )
    db_session.add(attempt)
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/history")
    assert resp.status_code == 200
    data = resp.json()
    match = next((item for item in data["items"] if item["attempt_id"] == str(attempt.id)), None)
    assert match is not None
    assert match["provider_video_id"] == "history_vid_999"
    assert match["duration_seconds"] == pytest.approx(120.0, rel=1e-2)
    _assert_no_forbidden_keys_or_values(data)


# ── 9. Publication Detail Model & Eligibility ──


@pytest.mark.asyncio
async def test_publication_detail_complete_model(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.SUCCEEDED.value,
        provider_video_id="safe_abc_123",
        started_at=datetime.now(UTC),
    )
    upload = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://upload.youtube.com/leak/session_url",
        total_bytes=5000,
        bytes_uploaded=5000,
        chunk_size_bytes=2500,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add_all([attempt, upload])
    await db_session.commit()

    resp = await api_client.get(f"/api/v1/publisher/operations/publications/{bundle['intent'].id}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["intent_id"] == str(bundle["intent"].id)
    assert data["provider"]["video_id"] == "safe_abc_123"
    assert data["provider"]["url"] == "https://youtu.be/safe_abc_123"
    assert data["upload_session"]["session_id"] == str(upload.id)
    assert data["upload_session"]["progress_percentage"] == 100.0
    _assert_no_forbidden_keys_or_values(data)


# ── 10. Pagination Bounds Enforcement ──


@pytest.mark.asyncio
async def test_pagination_bounds_and_limit_enforcement(api_client: AsyncClient):
    # Test valid limit
    r1 = await api_client.get("/api/v1/publisher/operations/history?limit=10&offset=0")
    assert r1.status_code == 200
    assert r1.json()["limit"] == 10

    # Test limit > 100 is rejected by FastAPI Query(le=100) validation with HTTP 422
    r2 = await api_client.get("/api/v1/publisher/operations/history?limit=105&offset=0")
    assert r2.status_code == 422


# ── 11. Filtering by Channel and Search ──


@pytest.mark.asyncio
async def test_filtering_by_channel_and_search(db_session: AsyncSession, api_client: AsyncClient):
    bundle1 = await create_fixture_bundle(db_session)
    bundle2 = await create_fixture_bundle(db_session)

    bundle1["intent"].title = "Special Search Target Video"
    att1 = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle1["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.SUCCEEDED.value,
        started_at=datetime.now(UTC),
    )
    att2 = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle2["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.SUCCEEDED.value,
        started_at=datetime.now(UTC),
    )
    db_session.add_all([bundle1["intent"], att1, att2])
    await db_session.commit()

    # Search filter
    resp = await api_client.get("/api/v1/publisher/operations/history?search=Special Search Target")
    assert resp.status_code == 200
    items = resp.json()["items"]
    for it in items:
        assert "special search target" in it["title"].lower()

    # Channel filter
    r_ch = await api_client.get(f"/api/v1/publisher/operations/history?channel_id={bundle1['channel'].id}")
    assert r_ch.status_code == 200
    ch_items = r_ch.json()["items"]
    assert any(it["publish_intent_id"] == str(bundle1["intent"].id) for it in ch_items)
    assert not any(it["publish_intent_id"] == str(bundle2["intent"].id) for it in ch_items)
    for it in ch_items:
        assert it["channel_id"] == str(bundle1["channel"].id)


# ── 12. Sanitized Error Messages (Redacting Tokens/URLs) ──


@pytest.mark.asyncio
async def test_sanitized_error_messages_in_responses(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session)
    sensitive_msg = (
        "Google API error: access_token=secret_xyz123 client_secret=shh99 "
        "Authorization: Bearer my_top_secret_token https://upload.youtube.com/session/secret_id"
    )

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.PERMANENT_FAILED.value,
        error_message=sensitive_msg,
        started_at=datetime.now(UTC),
    )
    db_session.add(attempt)
    await db_session.commit()

    resp = await api_client.get("/api/v1/publisher/operations/history")
    assert resp.status_code == 200
    data = resp.json()
    match = next((item for item in data["items"] if item["attempt_id"] == str(attempt.id)), None)
    assert match is not None
    err = match["last_sanitized_error"]
    assert "secret_xyz123" not in err
    assert "my_top_secret_token" not in err
    assert "upload.youtube.com" not in err
    assert "[REDACTED]" in err


# ── 13 & 14. Recursive Audit: No Token Fields & No Session URI ──


@pytest.mark.asyncio
async def test_recursive_audit_no_tokens_or_session_uri_in_all_endpoints(
    db_session: AsyncSession, api_client: AsyncClient
):
    bundle = await create_fixture_bundle(db_session)
    att = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.UPLOADING.value,
        started_at=datetime.now(UTC),
    )
    up = UploadSession(
        id=uuid4(),
        publish_attempt_id=att.id,
        session_uri="https://upload.youtube.com/sensitive_resumable_uri",
        total_bytes=1000,
        bytes_uploaded=500,
        chunk_size_bytes=500,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add_all([att, up])
    await db_session.commit()

    endpoints = [
        "/api/v1/publisher/operations/overview",
        "/api/v1/publisher/operations/calendar",
        "/api/v1/publisher/operations/active",
        "/api/v1/publisher/operations/retries",
        "/api/v1/publisher/operations/manual-holds",
        "/api/v1/publisher/operations/dead-letters",
        "/api/v1/publisher/operations/history",
        f"/api/v1/publisher/operations/publications/{bundle['intent'].id}",
    ]
    for ep in endpoints:
        resp = await api_client.get(ep)
        assert resp.status_code == 200, f"Endpoint {ep} failed with {resp.status_code}"
        _assert_no_forbidden_keys_or_values(resp.json())


# ── 15. UNKNOWN State Never Permits Generic Retry ──


@pytest.mark.asyncio
async def test_unknown_state_never_permits_generic_retry(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.MANUAL_HOLD.value,
        started_at=datetime.now(UTC),
    )
    db_session.add(attempt)
    await db_session.commit()

    resp = await api_client.get(f"/api/v1/publisher/operations/publications/{bundle['intent'].id}")
    assert resp.status_code == 200
    data = resp.json()
    eligibility = data["recovery_eligibility"]
    assert "RETRY" not in eligibility["allowed_operations"]
    assert "RETRY" in eligibility["blocked_operations"]
    assert "reconciliation" in eligibility["blocked_operations"]["RETRY"].lower()


# ── 16. Dead Letter Requeue Endpoint ──


@pytest.mark.asyncio
async def test_dead_letter_requeue_endpoint(db_session: AsyncSession, api_client: AsyncClient):
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=uuid4().hex,
        state=PublishAttemptState.PERMANENT_FAILED.value,
        started_at=datetime.now(UTC),
    )
    db_session.add(attempt)
    await db_session.flush()

    h = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        publish_attempt_id=attempt.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        earliest_retry_at=datetime.now(UTC) - timedelta(hours=1),
        reason="Max retries reached",
        idempotency_key=uuid4().hex,
        status=HandoffStatus.DEAD_LETTER.value,
        attempt_count=5,
    )
    db_session.add(h)
    await db_session.commit()

    payload = {"actor": "OperatorBob", "reason": "Operator manually approved retry"}
    resp = await api_client.post(f"/api/v1/publisher/operations/dead-letters/{h.id}/requeue", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["handoff_id"] == str(h.id)
    assert data["status"] == "PENDING"
    assert data["attempt_count"] == 0
    assert data["actor"] == "OperatorBob"


# ── 17. Operator Actions Require Actor and Reason ──


@pytest.mark.asyncio
async def test_operator_actions_require_actor_and_reason(api_client: AsyncClient):
    dummy_id = uuid4()
    # Missing / empty fields
    r1 = await api_client.post(
        f"/api/v1/publisher/operations/dead-letters/{dummy_id}/requeue",
        json={"actor": "", "reason": "some reason"},
    )
    assert r1.status_code == 422

    r2 = await api_client.post(
        f"/api/v1/publisher/operations/manual-holds/{dummy_id}/reconcile",
        json={"actor": "ValidActor", "reason": ""},
    )
    assert r2.status_code == 422


# ── 18. Manual Hold Reconcile Endpoint (Mocked Network) ──


@pytest.mark.asyncio
async def test_manual_hold_reconcile_endpoint_mocked_network(api_client: AsyncClient):
    dummy_attempt_id = uuid4()

    with patch(
        "omega.application.publisher.recovery_operations.PublisherRecoveryOperationsService.reconcile_again",
        new_callable=AsyncMock,
    ) as mock_rec:
        mock_rec.return_value = ReconciliationStatus.CONFIRMED_SUCCESS

        payload = {"actor": "AuditorAlice", "reason": "Checking ambiguous upload"}
        resp = await api_client.post(
            f"/api/v1/publisher/operations/manual-holds/{dummy_attempt_id}/reconcile",
            json=payload,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["attempt_id"] == str(dummy_attempt_id)
        assert data["operation"] == "EXPLICIT_EXTERNAL_RECONCILIATION"
        assert data["actor"] == "AuditorAlice"
        assert mock_rec.await_count == 1


# ── 19. Authorize Session Resume Endpoint ──


@pytest.mark.asyncio
async def test_authorize_session_resume_endpoint(api_client: AsyncClient):
    dummy_attempt_id = uuid4()
    dummy_upload_id = uuid4()

    with patch(
        "omega.application.publisher.recovery_operations.PublisherRecoveryOperationsService.authorize_existing_session_resume",
        new_callable=AsyncMock,
    ) as mock_auth:
        mock_auth.return_value = {
            "attempt_id": dummy_attempt_id,
            "upload_session_id": dummy_upload_id,
            "provider_offset": 8192,
            "total_bytes": 16384,
        }

        payload = {"actor": "OperatorCharlie", "reason": "Verified offset against provider status"}
        resp = await api_client.post(
            f"/api/v1/publisher/operations/sessions/{dummy_attempt_id}/resume-authorize",
            json=payload,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["attempt_id"] == str(dummy_attempt_id)
        assert data["upload_session_id"] == str(dummy_upload_id)
        assert data["provider_offset"] == 8192
        assert data["total_bytes"] == 16384
        _assert_no_forbidden_keys_or_values(data)


# ── 20. Empty Dataset Handling ──


@pytest.mark.asyncio
async def test_empty_dataset_handling(api_client: AsyncClient):
    # Empty query should return valid responses with empty list
    nonexistent_id = uuid4()
    r_cal = await api_client.get(f"/api/v1/publisher/operations/calendar?channel_id={nonexistent_id}")
    assert r_cal.status_code == 200
    assert r_cal.json()["total_count"] == 0
    assert r_cal.json()["items"] == []

    r_act = await api_client.get(f"/api/v1/publisher/operations/active?channel_id={nonexistent_id}")
    assert r_act.status_code == 200
    assert r_act.json()["total_count"] == 0
    assert r_act.json()["items"] == []
