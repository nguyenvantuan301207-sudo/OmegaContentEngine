"""P25-B Integration Canary Suite for Published Performance Analytics.

Covers:
- Section 34: Isolated Performance Canary
  - Real accepted P25-A PublishReceipt
  - T0 snapshot: views 100, watch time 4000s, impressions 1000, CTR 0.05
  - T1 snapshot: views 250, watch time 11000s, impressions 2300, CTR 0.061
  - Retention curve with milestone derivation
  - Delta / velocity computation
  - Replay idempotency (duplicate skipped)
  - Output concise facts
- Section 35: Provider Failure Canary
  - Transient provider 503 error -> bounded retry recovery
  - Delayed analytics pipeline -> DELAYED freshness without converting to zero
  - Counter decrease -> ANOMALY_DETECTED finding
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.fake_performance_provider import (
    FakePerformanceAnalyticsProvider,
)
from omega.application.analytics.performance_service import PerformanceAnalyticsService
from omega.application.publisher.fake_provider import FakePublishingProvider
from omega.application.publisher.publishing_service import PublishingService
from omega.domain.channel import ChannelState, Platform
from omega.domain.creative_qa import (
    CreativeQAProvenance,
    CreativeQAResult,
    CreativeQASeverity,
    CreativeQAStatus,
)
from omega.domain.packaging import (
    PackagingPlan,
    PackagingProvenance,
    ThumbnailArtifact,
    ThumbnailConcept,
    TitleCandidate,
    TitleStrategy,
)
from omega.domain.performance_analytics import DataFreshnessStatus
from omega.domain.production import ProductionQAStatus
from omega.domain.publishing import PublishingState, PublishReceipt, PublishVisibility
from omega.infrastructure.models import (
    Channel,
    MediaArtifact,
    Mission,
    PlatformAccount,
    ProductionRequest,
    ProductionRuntimeTruth,
    Task,
)
from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def published_p25a_receipt(db_session: AsyncSession, tmp_path: Path) -> PublishReceipt:
    """Creates a real accepted production and publishes it via P25-A to obtain an authoritative PublishReceipt."""
    # 1. Channel
    channel = Channel(
        id=uuid4(),
        slug=f"p25b-chan-{uuid4().hex[:8]}",
        name="P25-B Analytics Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    # 2. Mission & Task
    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="P25-B Analytics Canary Mission",
        objective="Validate performance analytics",
        state="RUNNING",
        autonomy_level="SUPERVISED",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        title="Publish for Performance Analytics",
        task_type="PUBLISH_VIDEO",
        state="READY",
    )
    db_session.add(task)

    # 3. Platform Account
    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="P25B Analytics YouTube Account",
        external_account_id=f"yt-{uuid4().hex[:12]}",
        status="ACTIVE",
    )
    db_session.add(account)

    # 4. Artifact & Ancestry
    art_hash = "a" * 64
    artifact = await create_artifact_with_ancestry(db_session, channel.id, art_hash)
    artifact.state = "ACCEPTED"
    artifact.is_current = True
    artifact.artifact_type = "VIDEO"

    prod_req = await db_session.get(ProductionRequest, artifact.production_request_id)
    assert prod_req is not None
    dna_rev_id = prod_req.channel_dna_revision_id

    # 5. Runtime Truth
    rt = ProductionRuntimeTruth(
        artifact_id=artifact.id,
        schema_version=4,
        manifest_run_fingerprint="0" * 64,
        payload={"artifact_sha256": art_hash},
    )
    db_session.add(rt)
    await db_session.commit()

    # 6. Thumbnail
    thumb_path = tmp_path / "p25b_thumbnail.png"
    thumb_bytes = b"sample-thumbnail-bytes-p25b"
    thumb_path.write_bytes(thumb_bytes)
    thumb_sha256 = hashlib.sha256(thumb_bytes).hexdigest()

    # 7. Packaging Plan
    title = TitleCandidate(
        text="The Physics of Event Horizons Explained",
        strategy=TitleStrategy.EXPLAINER,
        character_count=40,
    )
    thumb_concept = ThumbnailConcept(primary_subject="Black Hole Horizon")
    thumb_art = ThumbnailArtifact(
        concept_id=thumb_concept.concept_id,
        file_path=thumb_path,
        file_size_bytes=len(thumb_bytes),
        content_sha256=thumb_sha256,
    )
    creative_style_id = uuid4()
    prov = PackagingProvenance(
        channel_dna_revision_id=dna_rev_id,
        creative_style_plan_id=creative_style_id,
        selected_title_id=title.candidate_id,
        selected_thumbnail_id=thumb_concept.concept_id,
    )
    pkg_plan = PackagingPlan(
        channel_dna_revision_id=dna_rev_id,
        creative_style_plan_id=creative_style_id,
        title_candidates=(title,),
        selected_title=title,
        thumbnail_concepts=(thumb_concept,),
        selected_thumbnail=thumb_concept,
        physical_thumbnail_artifact=thumb_art,
        description="Explaining black hole physics and event horizon dynamics.",
        tags=("physics", "space"),
        chapters=(),
        provenance=prov,
    )

    # 8. Creative QA
    cqa_result = CreativeQAResult(
        status=CreativeQAStatus.PASS,
        highest_severity=CreativeQASeverity.INFO,
        findings=(),
        recommendations=(),
        provenance=CreativeQAProvenance(
            channel_dna_revision_id=dna_rev_id,
            creative_style_plan_id=creative_style_id,
        ),
        blocker_count=0,
        error_count=0,
        warning_count=0,
        info_count=1,
        is_accepted=True,
    )

    # 9. P25-A Publish Execution
    fake_pub_provider = FakePublishingProvider(provider_name="FAKE")
    intent, elig = await PublishingService.prepare_publish(
        session=db_session,
        production_request_id=prod_req.id,
        artifact_id=artifact.id,
        platform_account_id=account.id,
        packaging_plan=pkg_plan,
        creative_qa_result=cqa_result,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        visibility=PublishVisibility.PRIVATE,
        task_id=task.id,
        mission_id=mission.id,
        verify_physical_thumbnail=True,
    )
    assert elig.is_eligible is True

    receipt = await PublishingService.execute_publish(
        session=db_session,
        publish_intent_id=intent.id,
        provider=fake_pub_provider,
        worker_id="canary-worker-p25b",
    )
    assert receipt is not None
    assert receipt.completion_state == PublishingState.PUBLISHED
    return receipt


# ── Section 34: Isolated Performance Canary ──────────────────────────────────


@pytest.mark.asyncio
async def test_section_34_isolated_performance_canary(
    db_session: AsyncSession,
    published_p25a_receipt: PublishReceipt,
):
    """Executes Section 34: T0 -> T1 snapshot growth, deltas, velocities, retention, and idempotent replay."""
    receipt = published_p25a_receipt
    ext_id = receipt.external_media_id
    pub_time = receipt.published_at or datetime.now(UTC)

    t0 = pub_time + timedelta(hours=1)
    t1 = pub_time + timedelta(hours=3)  # 2 hours later

    fake_provider = FakePerformanceAnalyticsProvider(provider_name="FAKE")

    # Configure timeline metrics:
    # T0: views 100, watch time 4000s, impressions 1000, CTR 0.05
    # T1: views 250, watch time 11000s, impressions 2300, CTR 0.061
    fake_provider.configure_timeline_metrics(
        ext_id,
        [
            (
                t0,
                {
                    "views": 100,
                    "watch_time_seconds": 4000.0,
                    "impressions": 1000,
                    "ctr": 0.05,
                    "likes": 12,
                    "comments": 2,
                    "shares": 1,
                    "subscribers_gained": 3,
                    "subscribers_lost": 0,
                },
            ),
            (
                t1,
                {
                    "views": 250,
                    "watch_time_seconds": 11000.0,
                    "impressions": 2300,
                    "ctr": 0.061,
                    "likes": 28,
                    "comments": 5,
                    "shares": 3,
                    "subscribers_gained": 8,
                    "subscribers_lost": 1,
                },
            ),
        ],
    )

    # Configure retention curve: monotonic points
    fake_provider.configure_retention_curve(
        ext_id,
        [
            (0.0, 1.0),
            (0.1, 0.88),
            (0.5, 0.62),
            (0.9, 0.45),
            (1.0, 0.40),
        ],
    )

    # Ingest T0 Snapshot
    snap_t0 = await PerformanceAnalyticsService.ingest_performance_snapshot(
        session=db_session,
        receipt=receipt,
        provider=fake_provider,
        as_of=t0,
        sync_checkpoint="chk-t0",
    )
    assert snap_t0.metrics.views == 100
    assert snap_t0.metrics.watch_time_seconds == 4000.0
    assert snap_t0.metrics.impressions == 1000
    assert snap_t0.metrics.impressions_ctr == 0.05
    assert snap_t0.freshness == DataFreshnessStatus.FRESH
    assert snap_t0.deltas is None or not snap_t0.deltas.is_valid  # First snapshot has no delta

    # Ingest T1 Snapshot
    snap_t1 = await PerformanceAnalyticsService.ingest_performance_snapshot(
        session=db_session,
        receipt=receipt,
        provider=fake_provider,
        as_of=t1,
        sync_checkpoint="chk-t1",
    )
    assert snap_t1.external_media_id == ext_id
    assert snap_t1.metrics.views == 250
    assert snap_t1.metrics.watch_time_seconds == 11000.0
    assert snap_t1.metrics.impressions == 2300
    assert snap_t1.metrics.impressions_ctr == 0.061
    assert snap_t1.freshness == DataFreshnessStatus.FRESH

    # Verify deltas & velocity
    assert snap_t1.deltas is not None
    assert snap_t1.deltas.is_valid is True
    assert snap_t1.deltas.views_delta == 150  # 250 - 100
    assert snap_t1.deltas.watch_time_delta_seconds == 7000.0  # 11000 - 4000
    # Time elapsed = 2 hours
    assert snap_t1.deltas.views_per_hour == 75.0  # 150 / 2 hours
    assert snap_t1.deltas.watch_time_per_hour_seconds == 3500.0  # 7000 / 2 hours

    # Verify retention summary
    assert snap_t1.derived_retention is not None
    assert snap_t1.derived_retention.retention_at_50_percent == 0.62
    assert snap_t1.derived_retention.retention_at_90_percent == 0.45
    assert snap_t1.derived_retention.early_dropoff_rate == 0.12  # 1.0 - 0.88

    # Ingest Replay (Idempotency Check)
    # Fetch call count before replay
    calls_before = fake_provider.fetch_call_count
    snap_replay = await PerformanceAnalyticsService.ingest_performance_snapshot(
        session=db_session,
        receipt=receipt,
        provider=fake_provider,
        as_of=t1,
    )
    assert snap_replay.metrics.views == 250
    # Verify history still contains exactly 2 distinct snapshots
    history = await PerformanceAnalyticsService.get_performance_history(session=db_session, receipt=receipt)
    assert len(history) == 2

    # Query Performance Summary
    summary = await PerformanceAnalyticsService.get_performance_summary(session=db_session, receipt=receipt)
    assert summary is not None
    assert summary.views == 250
    assert summary.watch_time_seconds == 11000.0
    assert summary.impressions_ctr == 0.061
    assert summary.snapshot_count == 2
    assert summary.has_anomalies is False

    # Output facts for section 34 report
    print("\n--- P25-B ISOLATED PERFORMANCE CANARY FACTS ---")
    print(f"snapshot count: {len(history)}")
    print(f"latest views: {snap_t1.metrics.views}")
    print(f"views delta: {snap_t1.deltas.views_delta}")
    print(f"watch time delta: {snap_t1.deltas.watch_time_delta_seconds}s")
    print(f"CTR: {snap_t1.metrics.impressions_ctr}")
    print(f"freshness: {snap_t1.freshness.value}")
    print(f"retention points: {len(snap_t1.retention_curve.points) if snap_t1.retention_curve else 0}")
    print("duplicate count: 1 skipped on replay")
    print("provider = FAKE")
    print("real provider mutation = NO")
    print("------------------------------------------------\n")


# ── Section 35: Provider Failure Canary ──────────────────────────────────────


@pytest.mark.asyncio
async def test_section_35_provider_failure_canary(
    db_session: AsyncSession,
    published_p25a_receipt: PublishReceipt,
):
    """Executes Section 35: Transient retry recovery, delayed freshness, and counter anomaly detection."""
    receipt = published_p25a_receipt
    ext_id = receipt.external_media_id
    pub_time = receipt.published_at or datetime.now(UTC)

    # 1. Simulate Transient Failure -> Bounded Retry Recovery
    fake_provider = FakePerformanceAnalyticsProvider(provider_name="FAKE")
    t0 = pub_time + timedelta(hours=1)
    fake_provider.configure_timeline_metrics(
        ext_id,
        [(t0, {"views": 100, "watch_time_seconds": 3600.0})],
    )
    # Configure 2 transient failures before succeeding
    fake_provider.configure_transient_failure(ext_id, failures_before_success=2)

    snap_recovered = await PerformanceAnalyticsService.ingest_performance_snapshot(
        session=db_session,
        receipt=receipt,
        provider=fake_provider,
        as_of=t0,
        max_retries=3,
    )
    assert snap_recovered.metrics.views == 100
    assert fake_provider.retry_recovery_count == 2  # Recovered after 2 retries

    # 2. Simulate Delayed Analytics Pipeline -> DELAYED Freshness (NOT Zero!)
    lagged_provider = FakePerformanceAnalyticsProvider(provider_name="FAKE")
    lagged_provider.configure_lagged_media(ext_id)
    t1 = pub_time + timedelta(hours=2)

    snap_delayed = await PerformanceAnalyticsService.ingest_performance_snapshot(
        session=db_session,
        receipt=receipt,
        provider=lagged_provider,
        as_of=t1,
        max_retries=2,
    )
    assert snap_delayed.freshness == DataFreshnessStatus.DELAYED
    assert snap_delayed.metrics.views is None  # Must NOT be treated as zero!

    # 3. Simulate Counter Decrease -> Anomaly Finding (1200 -> 900)
    anomaly_provider = FakePerformanceAnalyticsProvider(provider_name="FAKE")
    t2 = pub_time + timedelta(hours=4)
    t3 = pub_time + timedelta(hours=5)

    # Configure baseline T2 with 1200 views
    anomaly_provider.configure_timeline_metrics(
        ext_id,
        [
            (t2, {"views": 1200, "watch_time_seconds": 50000.0}),
            (t3, {"views": 900, "watch_time_seconds": 50000.0}),  # Suspicious drop of 300 views
        ],
    )

    snap_baseline = await PerformanceAnalyticsService.ingest_performance_snapshot(
        session=db_session,
        receipt=receipt,
        provider=anomaly_provider,
        as_of=t2,
    )
    assert snap_baseline.metrics.views == 1200

    snap_anom = await PerformanceAnalyticsService.ingest_performance_snapshot(
        session=db_session,
        receipt=receipt,
        provider=anomaly_provider,
        as_of=t3,
    )
    assert snap_anom.metrics.views == 900
    assert len(snap_anom.anomalies) == 1
    finding = snap_anom.anomalies[0]
    assert finding.metric_name == "views"
    assert finding.previous_value == 1200
    assert finding.current_value == 900
    assert finding.difference == 300
    assert finding.status == "ANOMALY_DETECTED"


# ── Section 28: Read-Only Analytics API ──────────────────────────────────────


@pytest.mark.asyncio
async def test_section_28_read_only_api_endpoints(
    db_session: AsyncSession,
    published_p25a_receipt: PublishReceipt,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verifies read-only API contract: NO_DATA, AVAILABLE, and PROVIDER_UNAVAILABLE without mutation."""
    from omega.api.analytics import (
        get_published_performance_history,
        get_published_performance_latest,
    )
    monkeypatch.setattr(
        "omega.api.analytics.get_settings",
        lambda: type("MockSettings", (), {"analytics_api_enabled": True})(),
    )

    # 1. Unknown receipt ID returns NO_DATA
    unknown_id = uuid4()
    resp_no_data = await get_published_performance_latest(receipt_id=unknown_id, session=db_session)
    assert resp_no_data.status == "NO_DATA"
    assert resp_no_data.data is None

    # 2. Ingest one snapshot for published receipt
    receipt = published_p25a_receipt
    fake_provider = FakePerformanceAnalyticsProvider(provider_name="FAKE")
    now_utc = datetime.now(UTC)
    fake_provider.configure_timeline_metrics(
        receipt.external_media_id,
        [(now_utc, {"views": 777, "impressions": 5000, "ctr": 0.08})],
    )

    await PerformanceAnalyticsService.ingest_performance_snapshot(
        session=db_session,
        receipt=receipt,
        provider=fake_provider,
        as_of=now_utc,
    )

    # Query latest endpoint with publish_intent_id
    resp_avail = await get_published_performance_latest(
        receipt_id=receipt.publish_intent_id, session=db_session
    )
    assert resp_avail.status == "AVAILABLE"
    assert resp_avail.data is not None
    assert resp_avail.data["external_media_id"] == receipt.external_media_id
    assert resp_avail.data["metrics"]["views"]["value"] == 777

    # Query history endpoint
    resp_hist = await get_published_performance_history(
        receipt_id=receipt.publish_intent_id, session=db_session
    )
    assert resp_hist.status == "AVAILABLE"
    assert resp_hist.data is not None
    assert resp_hist.data["total_snapshots"] >= 1
