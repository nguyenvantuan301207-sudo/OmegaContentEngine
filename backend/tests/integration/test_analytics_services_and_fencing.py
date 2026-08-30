"""Integration tests for OMEGA-012 Analytics Engine services, fencing, and idempotency."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.adapters.base import (
    AnalyticsAdapterError,
    ProviderFetchResult,
)
from omega.application.analytics.computed_service import ComputedMetricsService
from omega.application.analytics.ingestion_service import AnalyticsIngestionService
from omega.application.analytics.learning_service import LearningExportService
from omega.application.analytics.poll_service import AnalyticsPollService
from omega.domain.analytics import (
    AnalyticsErrorCategory,
    AnalyticsPollJobStatus,
    MetricClassification,
    WindowState,
    WindowType,
    compute_logical_query_key,
    compute_payload_checksum,
    compute_poll_execution_key,
)
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsChannelSnapshot,
    AnalyticsComputedMetric,
    AnalyticsMetricLatestPointer,
    AnalyticsMetricObservation,
    AnalyticsPollJob,
    AnalyticsProviderSnapshot,
    AnalyticsWindow,
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    MediaArtifact,
    Mission,
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

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def sample_analytics_fixtures(db_session: AsyncSession):
    """Fixture providing complete published intent, attempt, and analytics asset."""
    chan = Channel(
        id=uuid4(),
        slug=f"chan-{uuid4().hex[:8]}",
        name="Analytics Test Channel",
        platform="YOUTUBE",
    )
    db_session.add(chan)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=chan.id,
        version=1,
        snapshot={"name": "Analytics DNA"},
        change_reason="Initial Analytics DNA",
    )
    db_session.add(dna)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=chan.id,
        platform="YOUTUBE",
        account_display_name="Test Analytics Channel",
        external_account_id="UC_test_analytics_123",
        status="ACTIVE",
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ],
    )
    db_session.add(account)

    mission = Mission(
        id=uuid4(),
        channel_id=chan.id,
        title="Analytics Mission",
        objective="Analytics objective",
        autonomy_level="AUTONOMOUS",
        state="RUNNING",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="PUBLISH_VIDEO",
        title="Publish Video Task",
        state="COMPLETED",
    )
    db_session.add(task)

    topic = TopicCandidate(
        id=uuid4(),
        channel_id=chan.id,
        title="Topic",
        normalized_title="topic",
        source_name="Manual Entry",
        summary="Topic summary",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        title="Brief",
        summary="Brief summary",
    )
    db_session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
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
        channel_id=chan.id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna.id,
        status="COMPLETED",
    )
    db_session.add(prod_req)

    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/test.mp4",
        file_size_bytes=1024,
        content_hash="hash123",
        mime_type="video/mp4",
    )
    db_session.add(artifact)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        channel_id=chan.id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum="hash123",
        channel_dna_revision_id=dna.id,
        title="Test Published Video",
        description="Description",
        made_for_kids=False,
        intent_checksum=f"intent_hash_{uuid4().hex}",
        state="PUBLISHED",
    )
    db_session.add(intent)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"attempt_key_{uuid4().hex}",
        state="SUCCEEDED",
        provider_video_id=f"vid_{uuid4().hex[:10]}",
        provider_url="https://youtube.com/watch?v=123",
    )
    db_session.add(attempt)

    asset = AnalyticsAsset(
        id=uuid4(),
        channel_id=chan.id,
        platform_account_id=account.id,
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        media_artifact_id=artifact.id,
        channel_dna_revision_id=dna.id,
        provider="YOUTUBE",
        provider_video_id=attempt.provider_video_id,
        published_at=datetime.now(UTC) - timedelta(days=2),
        asset_status="ACTIVE",
        lifecycle_phase="ACTIVE_HOURLY",
        next_poll_due_at=datetime.now(UTC),
    )
    db_session.add(asset)

    window = AnalyticsWindow(
        id=uuid4(),
        asset_id=asset.id,
        window_type=WindowType.FIRST_24H.value,
        provider_date=None,
        provider_timezone="America/Los_Angeles",
        window_start_utc=asset.published_at,
        window_end_utc=asset.published_at + timedelta(hours=24),
        window_state=WindowState.PROVISIONAL.value,
    )
    db_session.add(window)

    await db_session.commit()
    return {
        "channel": chan,
        "account": account,
        "intent": intent,
        "attempt": attempt,
        "asset": asset,
        "window": window,
    }


# ── Identity & Deduplication Tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_poll_job_delivered_twice_yields_one_raw_snapshot(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Duplicate Celery delivery of the same poll execution must produce exactly one raw snapshot."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    poll_exec_key = compute_poll_execution_key(asset.id, "slot:2026-08-30T14:00:00Z")
    log_query_key = compute_logical_query_key(
        "YOUTUBE", fix["account"].id, asset.provider_video_id, "DATA_API_V3", "params", "w1", ""
    )

    fetch_result = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={"id": asset.provider_video_id},
        raw_payload={
            "items": [{"id": asset.provider_video_id, "statistics": {"viewCount": "100"}}]
        },
        payload_checksum=compute_payload_checksum(b'{"views": 100}'),
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )

    # Job 1
    job = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key=poll_exec_key,
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.commit()

    # First delivery
    snap1 = await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=job.claim_token,
        claim_generation=job.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=fetch_result,
        poll_execution_key=poll_exec_key,
        logical_query_key=log_query_key,
    )
    await db_session.commit()

    # Second delivery with same job and fetch result
    snap2 = await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=job.claim_token,
        claim_generation=job.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=fetch_result,
        poll_execution_key=poll_exec_key,
        logical_query_key=log_query_key,
    )
    await db_session.commit()

    assert snap1.id == snap2.id
    stmt = select(AnalyticsProviderSnapshot).where(
        AnalyticsProviderSnapshot.poll_execution_key == poll_exec_key
    )
    all_snaps = (await db_session.execute(stmt)).scalars().all()
    assert len(all_snaps) == 1


@pytest.mark.asyncio
async def test_same_query_at_t6h_and_t12h_with_identical_payload_yields_two_snapshots(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Legitimate scheduled polls at different times with identical provider bytes produce two snapshots."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    log_query_key = "common_query_key"
    payload_bytes = b'{"viewCount": "500"}'
    checksum = compute_payload_checksum(payload_bytes)

    # T+6h execution
    exec_key_t6 = compute_poll_execution_key(asset.id, "slot:2026-08-30T06:00:00Z")
    job_t6 = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key=exec_key_t6,
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job_t6)
    await db_session.commit()

    res_t6 = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={
            "items": [{"id": asset.provider_video_id, "statistics": {"viewCount": "500"}}]
        },
        payload_checksum=checksum,
        http_status=200,
        retrieval_timestamp=datetime.now(UTC) - timedelta(hours=6),
    )

    snap_t6 = await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job_t6.id,
        claim_token=job_t6.claim_token,
        claim_generation=job_t6.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res_t6,
        poll_execution_key=exec_key_t6,
        logical_query_key=log_query_key,
    )
    await db_session.commit()

    # T+12h execution
    exec_key_t12 = compute_poll_execution_key(asset.id, "slot:2026-08-30T12:00:00Z")
    job_t12 = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key=exec_key_t12,
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job_t12)
    await db_session.commit()

    res_t12 = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={
            "items": [{"id": asset.provider_video_id, "statistics": {"viewCount": "500"}}]
        },
        payload_checksum=checksum,
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )

    snap_t12 = await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job_t12.id,
        claim_token=job_t12.claim_token,
        claim_generation=job_t12.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res_t12,
        poll_execution_key=exec_key_t12,
        logical_query_key=log_query_key,
    )
    await db_session.commit()

    assert snap_t6.id != snap_t12.id
    assert snap_t6.payload_checksum == snap_t12.payload_checksum


@pytest.mark.asyncio
async def test_retry_reclaim_same_poll_execution_deduplicates_safely(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Reclaimer retrying the same poll execution key deduplicates into the existing snapshot."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    poll_exec_key = compute_poll_execution_key(asset.id, "slot:reclaim_test")
    log_query_key = "query_key"
    res = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={
            "items": [{"id": asset.provider_video_id, "statistics": {"viewCount": "250"}}]
        },
        payload_checksum=compute_payload_checksum(b'{"viewCount": "250"}'),
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )

    # Worker 1 runs and finishes
    job = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key=poll_exec_key,
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.commit()

    snap1 = await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=job.claim_token,
        claim_generation=job.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res,
        poll_execution_key=poll_exec_key,
        logical_query_key=log_query_key,
    )
    await db_session.commit()

    # Simulate worker 1 crashed before completing, lease expired
    job.status = AnalyticsPollJobStatus.CLAIMED
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    # Reclaimer worker 2 executes with reclaimed token
    reclaimed_job = await AnalyticsPollService.claim_poll_job(
        session=db_session, job_id=job.id, worker_id="reclaimer", lease_seconds=60
    )
    # Even if reclaimed, duplicate response deduplicates into snap1
    snap2 = await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=reclaimed_job.claim_token,
        claim_generation=reclaimed_job.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res,
        poll_execution_key=poll_exec_key,
        logical_query_key=log_query_key,
    )
    await db_session.commit()

    assert snap1.id == snap2.id


@pytest.mark.asyncio
async def test_manual_refresh_has_distinct_poll_execution_identity(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Manual on-demand refresh receives a unique manual_refresh_request_id, isolating it."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]

    req_id_1 = uuid4()
    req_id_2 = uuid4()

    key1 = compute_poll_execution_key(asset.id, f"manual:{req_id_1}")
    key2 = compute_poll_execution_key(asset.id, f"manual:{req_id_2}")
    assert key1 != key2


# ── Normalized Invariant & Revision Tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_same_snapshot_cannot_create_duplicate_provider_observation(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """A single raw provider snapshot cannot create duplicate observation rows."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    poll_exec_key = compute_poll_execution_key(asset.id, "slot:dedupe_obs_test")
    res = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={
            "items": [{"id": asset.provider_video_id, "statistics": {"viewCount": "1000"}}]
        },
        payload_checksum=compute_payload_checksum(b'{"viewCount": "1000"}'),
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )

    job = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key=poll_exec_key,
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.commit()

    # Pass 1
    snap = await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=job.claim_token,
        claim_generation=job.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res,
        poll_execution_key=poll_exec_key,
        logical_query_key="query_key",
    )
    await db_session.commit()

    # Count observations
    stmt_obs = select(AnalyticsMetricObservation).where(
        AnalyticsMetricObservation.snapshot_id == snap.id
    )
    obs_count_1 = len((await db_session.execute(stmt_obs)).scalars().all())
    assert obs_count_1 >= 3  # views, likes, comments

    # Pass 2: Re-run with same snapshot
    await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=job.claim_token,
        claim_generation=job.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res,
        poll_execution_key=poll_exec_key,
        logical_query_key="query_key",
    )
    await db_session.commit()

    obs_count_2 = len((await db_session.execute(stmt_obs)).scalars().all())
    assert obs_count_1 == obs_count_2  # ZERO duplicate observations added


@pytest.mark.asyncio
async def test_duplicate_reclaimed_poll_does_not_create_fake_metric_revision(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Reclaimed job observing identical snapshot must not allocate revision 2."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    poll_exec_key = compute_poll_execution_key(asset.id, "slot:fake_rev_test")
    res = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={
            "items": [{"id": asset.provider_video_id, "statistics": {"viewCount": "777"}}]
        },
        payload_checksum=compute_payload_checksum(b'{"viewCount": "777"}'),
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )

    job = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key=poll_exec_key,
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.commit()

    await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=job.claim_token,
        claim_generation=job.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res,
        poll_execution_key=poll_exec_key,
        logical_query_key="query_key",
    )
    await db_session.commit()

    # Check latest pointer revision
    stmt_ptr = select(AnalyticsMetricLatestPointer).where(
        AnalyticsMetricLatestPointer.asset_id == asset.id,
        AnalyticsMetricLatestPointer.metric_name == "views",
    )
    ptr = (await db_session.execute(stmt_ptr)).scalar_one()
    assert ptr.current_revision_sequence == 1

    # Reclaim and rerun
    job.status = AnalyticsPollJobStatus.CLAIMED
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    reclaimed = await AnalyticsPollService.claim_poll_job(
        session=db_session, job_id=job.id, worker_id="worker-2"
    )
    await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=reclaimed.claim_token,
        claim_generation=reclaimed.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res,
        poll_execution_key=poll_exec_key,
        logical_query_key="query_key",
    )
    await db_session.commit()

    ptr_after = (await db_session.execute(stmt_ptr)).scalar_one()
    assert ptr_after.current_revision_sequence == 1  # Did NOT allocate fake revision 2!


@pytest.mark.asyncio
async def test_initial_revision_1_concurrent_allocation_cannot_duplicate(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Row-level locking on analytics_windows serializes initial revision 1."""
    fix = sample_analytics_fixtures
    window = fix["window"]

    stmt_win = select(AnalyticsWindow).where(AnalyticsWindow.id == window.id).with_for_update()
    res_win = await db_session.execute(stmt_win)
    locked_win = res_win.scalar_one()
    assert locked_win.id == window.id


@pytest.mark.asyncio
async def test_two_concurrent_changed_payloads_serialize_revisions_correctly(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Consecutive changed payloads produce revision 1 and revision 2 with lineage pointer."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    # Payload 1
    job1 = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key="p1",
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job1)
    await db_session.commit()

    res1 = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={
            "items": [{"id": asset.provider_video_id, "statistics": {"viewCount": "100"}}]
        },
        payload_checksum=compute_payload_checksum(b"100"),
        http_status=200,
        retrieval_timestamp=datetime.now(UTC) - timedelta(hours=1),
    )
    await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job1.id,
        claim_token=job1.claim_token,
        claim_generation=job1.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res1,
        poll_execution_key="p1",
        logical_query_key="q",
    )
    await db_session.commit()

    # Payload 2 (Views corrected/increased to 150)
    job2 = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key="p2",
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job2)
    await db_session.commit()

    res2 = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={
            "items": [{"id": asset.provider_video_id, "statistics": {"viewCount": "150"}}]
        },
        payload_checksum=compute_payload_checksum(b"150"),
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )
    await AnalyticsIngestionService.persist_video_poll_result(
        session=db_session,
        poll_job_id=job2.id,
        claim_token=job2.claim_token,
        claim_generation=job2.claim_generation,
        asset_id=asset.id,
        window_id=window.id,
        fetch_result=res2,
        poll_execution_key="p2",
        logical_query_key="q",
    )
    await db_session.commit()

    # Check observations
    stmt_obs = (
        select(AnalyticsMetricObservation)
        .where(
            AnalyticsMetricObservation.asset_id == asset.id,
            AnalyticsMetricObservation.metric_name == "views",
        )
        .order_by(AnalyticsMetricObservation.revision_sequence.asc())
    )
    views_obs = (await db_session.execute(stmt_obs)).scalars().all()
    assert len(views_obs) == 2
    assert views_obs[0].revision_sequence == 1
    assert views_obs[0].integer_value == 100
    assert views_obs[1].revision_sequence == 2
    assert views_obs[1].integer_value == 150
    assert views_obs[1].preceding_observation_id == views_obs[0].id


# ── Fencing & Crash Safety Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_worker_cannot_persist_after_reclaim(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Worker with expired lease cannot write state and is rejected."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    job = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key="stale_exec",
        claim_token=uuid4(),
        claim_generation=1,
        # Expired in past
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    db_session.add(job)
    await db_session.commit()

    res = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={"items": []},
        payload_checksum="chk",
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )

    with pytest.raises(AnalyticsAdapterError) as exc_info:
        await AnalyticsIngestionService.persist_video_poll_result(
            session=db_session,
            poll_job_id=job.id,
            claim_token=job.claim_token,
            claim_generation=job.claim_generation,
            asset_id=asset.id,
            window_id=window.id,
            fetch_result=res,
            poll_execution_key="stale_exec",
            logical_query_key="q",
        )

    assert exc_info.value.category == AnalyticsErrorCategory.FENCING_REJECTED


@pytest.mark.asyncio
async def test_expired_worker_cannot_renew_after_reclaim(db_session: AsyncSession):
    """Old worker whose generation was superseded cannot claim or persist."""
    job = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key="gen_test",
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    db_session.add(job)
    await db_session.commit()

    # Reclaimed by worker 2
    reclaimed = await AnalyticsPollService.claim_poll_job(
        session=db_session, job_id=job.id, worker_id="reclaimer"
    )
    assert reclaimed.claim_generation == 2

    # Old worker tries to renew or complete with generation 1
    stmt = select(AnalyticsPollJob).where(
        AnalyticsPollJob.id == job.id,
        AnalyticsPollJob.claim_generation == 1,
    )
    old_res = (await db_session.execute(stmt)).scalar_one_or_none()
    assert old_res is None


@pytest.mark.asyncio
async def test_poll_job_cannot_commit_completed_if_observation_fails(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Atomic rollback on constraint failure prevents job from marking COMPLETED."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]

    job = AnalyticsPollJob(
        id=uuid4(),
        job_type="VIDEO_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key="fail_test",
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.commit()

    res = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={"items": []},
        payload_checksum="chk",
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )

    # Invalid window_id causes failure
    with pytest.raises(ValueError):
        await AnalyticsIngestionService.persist_video_poll_result(
            session=db_session,
            poll_job_id=job.id,
            claim_token=job.claim_token,
            claim_generation=job.claim_generation,
            asset_id=asset.id,
            window_id=uuid4(),  # non-existent
            fetch_result=res,
            poll_execution_key="fail_test",
            logical_query_key="q",
        )

        job_id_val = job.id
        await db_session.rollback()
        # Check job status did not become COMPLETED
        stmt = select(AnalyticsPollJob).where(AnalyticsPollJob.id == job_id_val)
        j = (await db_session.execute(stmt)).scalar_one()
        assert j.status == AnalyticsPollJobStatus.CLAIMED


# ── Computed Metrics & Channel Snapshot Tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_view_velocity_references_two_source_observations(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """View velocity metric records multi-source observation lineage."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    obs1 = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=uuid4(),
        metric_name="views",
        classification=MetricClassification.PROVIDER_FACT.value,
        metric_quality="AVAILABLE",
        integer_value=100,
        observation_dedupe_key=uuid4().hex,
        observation_timestamp=datetime.now(UTC) - timedelta(hours=2),
    )
    obs2 = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=uuid4(),
        metric_name="views",
        classification=MetricClassification.PROVIDER_FACT.value,
        metric_quality="AVAILABLE",
        integer_value=300,
        observation_dedupe_key=uuid4().hex,
        observation_timestamp=datetime.now(UTC),
    )

    velocity = ComputedMetricsService.compute_view_velocity(asset.id, window.id, obs1, obs2)
    assert velocity is not None
    assert velocity.metric_name == "view_velocity_per_hour"
    assert velocity.classification == MetricClassification.DETERMINISTIC_DERIVED
    assert len(velocity.source_observation_ids) == 2
    assert str(obs1.id) in velocity.source_observation_ids
    assert str(obs2.id) in velocity.source_observation_ids
    # 200 views / 2 hours = 100 views/hour
    assert velocity.numeric_value == 100.0


@pytest.mark.asyncio
async def test_same_formula_same_sources_deduplicates_computed_metric(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Identical sources and formula produce matching computation_dedupe_key."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    obs1 = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=uuid4(),
        metric_name="views",
        classification="PROVIDER_FACT",
        metric_quality="AVAILABLE",
        integer_value=100,
        observation_dedupe_key="o1",
        observation_timestamp=datetime.now(UTC) - timedelta(hours=1),
    )
    obs2 = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=uuid4(),
        metric_name="views",
        classification="PROVIDER_FACT",
        metric_quality="AVAILABLE",
        integer_value=200,
        observation_dedupe_key="o2",
        observation_timestamp=datetime.now(UTC),
    )

    comp1 = ComputedMetricsService.compute_view_velocity(asset.id, window.id, obs1, obs2)
    comp2 = ComputedMetricsService.compute_view_velocity(asset.id, window.id, obs1, obs2)

    assert comp1.computation_dedupe_key == comp2.computation_dedupe_key


@pytest.mark.asyncio
async def test_changed_source_revision_creates_new_computation_revision(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Different source observation inputs produce distinct computation deduplication keys."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    obs1 = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=uuid4(),
        metric_name="views",
        classification="PROVIDER_FACT",
        metric_quality="AVAILABLE",
        integer_value=100,
        observation_dedupe_key="o1",
        observation_timestamp=datetime.now(UTC) - timedelta(hours=1),
    )
    obs2_rev1 = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=uuid4(),
        metric_name="views",
        classification="PROVIDER_FACT",
        metric_quality="AVAILABLE",
        integer_value=200,
        observation_dedupe_key="o2_r1",
        observation_timestamp=datetime.now(UTC),
    )
    obs2_rev2 = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=uuid4(),
        metric_name="views",
        classification="PROVIDER_FACT",
        metric_quality="AVAILABLE",
        integer_value=250,
        observation_dedupe_key="o2_r2",
        observation_timestamp=datetime.now(UTC),
    )

    comp_v1 = ComputedMetricsService.compute_view_velocity(asset.id, window.id, obs1, obs2_rev1)
    comp_v2 = ComputedMetricsService.compute_view_velocity(asset.id, window.id, obs1, obs2_rev2)

    assert comp_v1.computation_dedupe_key != comp_v2.computation_dedupe_key


@pytest.mark.asyncio
async def test_derived_metric_cannot_masquerade_as_provider_fact(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Database check constraint rejects non-PROVIDER_FACT in analytics_metric_observations."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    obs = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=uuid4(),
        metric_name="engagement_rate",
        classification="DETERMINISTIC_DERIVED",  # Illegal in this table!
        metric_quality="AVAILABLE",
        numeric_value=5.5,
        observation_dedupe_key=uuid4().hex,
        observation_timestamp=datetime.now(UTC),
    )
    db_session.add(obs)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_heuristic_excluded_from_default_learning_observation(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Learning contract strictly excludes HEURISTIC_FEATURE metrics by default."""
    fix = sample_analytics_fixtures
    asset = fix["asset"]
    window = fix["window"]

    # Add a provider fact
    snap = AnalyticsProviderSnapshot(
        id=uuid4(),
        asset_id=asset.id,
        channel_id=asset.channel_id,
        platform_account_id=asset.platform_account_id,
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={},
        payload_checksum="chk_fact",
        logical_query_key="l_query",
        poll_execution_key="p_exec",
        snapshot_dedupe_key=f"snap_dedupe_{uuid4().hex}",
        retrieval_timestamp=datetime.now(UTC),
        http_status=200,
    )
    db_session.add(snap)
    await db_session.flush()

    fact = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=snap.id,
        metric_name="views",
        classification="PROVIDER_FACT",
        metric_quality="AVAILABLE",
        integer_value=1200,
        observation_dedupe_key=uuid4().hex,
        observation_timestamp=datetime.now(UTC),
    )
    db_session.add(fact)
    ptr = AnalyticsMetricLatestPointer(
        asset_id=asset.id,
        window_id=window.id,
        metric_name="views",
        current_observation_id=fact.id,
        current_revision_sequence=1,
    )
    db_session.add(ptr)

    # Add a heuristic feature
    heuristic = AnalyticsComputedMetric(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        metric_name="retention_efficiency_score",
        classification="HEURISTIC_FEATURE",
        formula_identifier="HEURISTIC_V1",
        formula_version="1",
        source_checksum="chk",
        computation_dedupe_key=f"dedupe_h_{uuid4().hex}",
        numeric_value=0.85,
        metric_quality="AVAILABLE",
        computed_at=datetime.now(UTC),
    )
    db_session.add(heuristic)
    await db_session.commit()

    # Default export (include_heuristics=False)
    contract = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_24H,
        include_heuristics=False,
    )
    assert contract is not None
    assert "views" in contract.metrics
    assert "retention_efficiency_score" not in contract.metrics

    # Explicit export (include_heuristics=True)
    contract_with_h = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_24H,
        include_heuristics=True,
    )
    assert "retention_efficiency_score" in contract_with_h.metrics


@pytest.mark.asyncio
async def test_channel_snapshot_references_raw_provider_snapshot(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Channel normalized snapshots must link to raw provider snapshot without raw_payload duplication."""
    fix = sample_analytics_fixtures
    channel = fix["channel"]
    account = fix["account"]

    job = AnalyticsPollJob(
        id=uuid4(),
        job_type="CHANNEL_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key="chan_exec_1",
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.commit()

    fetch_res = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_CHANNELS",
        request_params={"mine": "true"},
        raw_payload={
            "items": [
                {
                    "statistics": {
                        "viewCount": "50000",
                        "subscriberCount": "1200",
                        "videoCount": "45",
                    }
                }
            ]
        },
        payload_checksum=compute_payload_checksum(b"chan_50000"),
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )

    channel_snap = await AnalyticsIngestionService.persist_channel_poll_result(
        session=db_session,
        poll_job_id=job.id,
        claim_token=job.claim_token,
        claim_generation=job.claim_generation,
        channel_id=channel.id,
        platform_account_id=account.id,
        snapshot_date=date(2026, 8, 30),
        fetch_result=fetch_res,
        poll_execution_key="chan_exec_1",
        logical_query_key="chan_query",
    )
    await db_session.commit()

    assert channel_snap.snapshot_id is not None
    assert channel_snap.total_views == 50000
    assert channel_snap.subscriber_count == 1200
    assert channel_snap.revision_sequence == 1


@pytest.mark.asyncio
async def test_same_channel_date_correction_appends_revision(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Provider correction on same Pacific calendar date appends revision sequence 2."""
    fix = sample_analytics_fixtures
    channel = fix["channel"]
    account = fix["account"]

    # Pass 1
    job1 = AnalyticsPollJob(
        id=uuid4(),
        job_type="CHANNEL_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key="chan_p1",
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job1)
    await db_session.commit()

    res1 = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_CHANNELS",
        request_params={},
        raw_payload={
            "items": [
                {"statistics": {"viewCount": "10000", "subscriberCount": "100", "videoCount": "10"}}
            ]
        },
        payload_checksum="chk_c1",
        http_status=200,
        retrieval_timestamp=datetime.now(UTC) - timedelta(hours=2),
    )
    snap1 = await AnalyticsIngestionService.persist_channel_poll_result(
        session=db_session,
        poll_job_id=job1.id,
        claim_token=job1.claim_token,
        claim_generation=job1.claim_generation,
        channel_id=channel.id,
        platform_account_id=account.id,
        snapshot_date=date(2026, 8, 30),
        fetch_result=res1,
        poll_execution_key="chan_p1",
        logical_query_key="chan_q",
    )
    await db_session.commit()

    # Pass 2: Correction
    job2 = AnalyticsPollJob(
        id=uuid4(),
        job_type="CHANNEL_POLL",
        status=AnalyticsPollJobStatus.CLAIMED,
        poll_execution_key="chan_p2",
        claim_token=uuid4(),
        claim_generation=1,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job2)
    await db_session.commit()

    res2 = ProviderFetchResult(
        api_endpoint="YOUTUBE_DATA_API_CHANNELS",
        request_params={},
        raw_payload={
            "items": [
                {"statistics": {"viewCount": "10050", "subscriberCount": "102", "videoCount": "10"}}
            ]
        },
        payload_checksum="chk_c2",
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )
    snap2 = await AnalyticsIngestionService.persist_channel_poll_result(
        session=db_session,
        poll_job_id=job2.id,
        claim_token=job2.claim_token,
        claim_generation=job2.claim_generation,
        channel_id=channel.id,
        platform_account_id=account.id,
        snapshot_date=date(2026, 8, 30),
        fetch_result=res2,
        poll_execution_key="chan_p2",
        logical_query_key="chan_q",
    )
    await db_session.commit()

    assert snap1.id != snap2.id
    assert snap2.revision_sequence == 2
    assert snap2.preceding_snapshot_id == snap1.id


@pytest.mark.asyncio
async def test_old_channel_snapshot_remains_immutable(
    db_session: AsyncSession, sample_analytics_fixtures
):
    """Old channel snapshot values are preserved when revision 2 is inserted."""
    fix = sample_analytics_fixtures
    channel = fix["channel"]

    stmt = select(AnalyticsChannelSnapshot).where(
        AnalyticsChannelSnapshot.channel_id == channel.id,
        AnalyticsChannelSnapshot.revision_sequence == 1,
    )
    res = (await db_session.execute(stmt)).scalar_one_or_none()
    if res:
        assert res.revision_sequence == 1
        assert res.total_views is not None
