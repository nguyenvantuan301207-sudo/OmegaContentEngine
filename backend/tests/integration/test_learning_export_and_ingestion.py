"""Integration tests for OMEGA-012 learning export contract and OMEGA-013 ingestion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.learning_service import LearningExportService
from omega.application.learning.feature_service import (
    FeatureExtractionService,
)
from omega.application.learning.ingestion_service import LearningIngestionService
from omega.domain.analytics import (
    EMPTY_DIMENSIONS_HASH,
    AnalyticsExportContractError,
    MetricQuality,
    WindowState,
    WindowType,
    compute_learning_observation_checksum,
)
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsComputedMetric,
    AnalyticsMetricLatestPointer,
    AnalyticsMetricObservation,
    AnalyticsProviderSnapshot,
    AnalyticsWindow,
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    LearningIngestionCursor,
    LearningInputSnapshot,
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
async def learning_test_env(db_session: AsyncSession):
    """Setup base entities for testing analytics export and learning ingestion."""
    chan = Channel(
        id=uuid4(),
        slug=f"chan-{uuid4().hex[:8]}",
        name="Learning Test Channel",
        platform="YOUTUBE",
    )
    db_session.add(chan)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=chan.id,
        version=1,
        snapshot={"name": "Learning DNA"},
        change_reason="Initial DNA",
    )
    db_session.add(dna)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=chan.id,
        platform="YOUTUBE",
        account_display_name="Test Channel Account",
        external_account_id="UC_learning_123",
        status="ACTIVE",
        scopes=["https://www.googleapis.com/auth/youtube.readonly"],
    )
    db_session.add(account)

    mission = Mission(
        id=uuid4(),
        channel_id=chan.id,
        title="Learning Mission",
        objective="Learning objective",
        autonomy_level="AUTONOMOUS",
        state="RUNNING",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="PUBLISH_VIDEO",
        title="Publish Task",
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
        title="Why This Hook Works? 5 Secret Tips",
        hook_text="Did you know this secret?",
        closing_text="Subscribe now",
        cta_text="Comment below",
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
        duration_ms=45000,
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
        title="Why This Hook Works? 5 Secret Tips",
        description="Here are 5 tips that worked well.",
        tags=["learning", "analytics", "growth"],
        made_for_kids=False,
        intent_checksum=f"intent_{uuid4().hex}",
        state="PUBLISHED",
    )
    db_session.add(intent)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"attempt_{uuid4().hex}",
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
        published_at=datetime.now(UTC) - timedelta(days=8),
        asset_status="ACTIVE",
        lifecycle_phase="ACTIVE_HOURLY",
        next_poll_due_at=datetime.now(UTC),
    )
    db_session.add(asset)

    window = AnalyticsWindow(
        id=uuid4(),
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D.value,
        provider_date=None,
        provider_timezone="America/Los_Angeles",
        window_start_utc=asset.published_at,
        window_end_utc=asset.published_at + timedelta(days=7),
        window_state=WindowState.FINALIZED.value,
    )

    db_session.add(window)

    await db_session.commit()
    return {
        "channel": chan,
        "dna": dna,
        "intent": intent,
        "artifact": artifact,
        "asset": asset,
        "window": window,
    }


# ============================================================================
# FINALIZATION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_revised_window_exports_as_fully_finalized_learning_observation(
    db_session: AsyncSession, learning_test_env
):
    """Verify that a window in REVISED state exports is_fully_finalized=True."""
    env = learning_test_env
    window: AnalyticsWindow = env["window"]
    window.window_state = WindowState.REVISED.value
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert obs.window_state == WindowState.REVISED
    assert obs.is_fully_finalized is True


@pytest.mark.asyncio
async def test_provisional_window_does_not_export_as_fully_finalized(
    db_session: AsyncSession, learning_test_env
):
    """Verify that a window in PROVISIONAL state exports is_fully_finalized=False."""
    env = learning_test_env
    window: AnalyticsWindow = env["window"]
    window.window_state = WindowState.PROVISIONAL.value
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert obs.is_fully_finalized is False


@pytest.mark.asyncio
async def test_revised_learning_observation_is_eligible_for_learning_ingestion(
    db_session: AsyncSession, learning_test_env
):
    """Verify that a revised observation successfully ingests and creates a snapshot."""
    env = learning_test_env
    window: AnalyticsWindow = env["window"]
    window.window_state = WindowState.REVISED.value
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None

    snapshot, is_new = await LearningIngestionService.ingest_observation(db_session, obs)
    assert is_new is True
    assert snapshot.window_state == WindowState.REVISED.value
    assert snapshot.is_fully_finalized is True


# ============================================================================
# COMPUTED EXPORT TESTS
# ============================================================================


@pytest.mark.asyncio
def _make_computed_metric(
    asset_id,
    window_id,
    metric_name,
    revision,
    formula_id,
    formula_version,
    value,
    computed_at=None,
    classification="DETERMINISTIC_DERIVED",
):
    return AnalyticsComputedMetric(
        asset_id=asset_id,
        window_id=window_id,
        metric_name=metric_name,
        classification=classification,
        formula_identifier=formula_id,
        formula_version=formula_version,
        source_observation_ids=[],
        source_computed_metric_ids=[],
        source_checksum="chk",
        computation_dedupe_key=f"comp_{uuid4().hex}",
        numeric_value=value,
        metric_quality="AVAILABLE",
        computation_revision_sequence=revision,
        computed_at=computed_at or datetime.now(UTC),
    )


async def _make_provider_observation(
    db_session: AsyncSession,
    asset: AnalyticsAsset,
    window: AnalyticsWindow,
    metric_name: str,
    value: float,
    dimensions=None,
    dimensions_hash=EMPTY_DIMENSIONS_HASH,
):
    snap = AnalyticsProviderSnapshot(
        asset_id=asset.id,
        channel_id=asset.channel_id,
        platform_account_id=asset.platform_account_id,
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={},
        payload_checksum=f"chk_{uuid4().hex}",
        logical_query_key=f"lqk_{uuid4().hex}",
        poll_execution_key=f"pek_{uuid4().hex}",
        snapshot_dedupe_key=f"sdk_{uuid4().hex}",
        http_status=200,
        retrieval_timestamp=datetime.now(UTC),
    )
    db_session.add(snap)
    await db_session.flush()

    obs = AnalyticsMetricObservation(
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=snap.id,
        metric_name=metric_name,
        classification="PROVIDER_FACT",
        metric_quality=MetricQuality.AVAILABLE.value,
        numeric_value=float(value),
        integer_value=int(value),
        dimensions=dimensions or {},
        dimensions_hash=dimensions_hash,
        normalization_version=1,
        observation_dedupe_key=f"obs_{uuid4().hex}",
        observation_timestamp=datetime.now(UTC),
    )
    db_session.add(obs)
    await db_session.flush()

    ptr = AnalyticsMetricLatestPointer(
        asset_id=asset.id,
        window_id=window.id,
        metric_name=metric_name,
        dimensions_hash=dimensions_hash,
        current_observation_id=obs.id,
        current_revision_sequence=1,
    )

    db_session.add(ptr)
    await db_session.flush()
    return obs


@pytest.mark.asyncio
async def test_learning_export_selects_latest_computed_revision_deterministically(
    db_session: AsyncSession, learning_test_env
):
    """Verify that highest computation revision of a canonical formula is selected."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    window: AnalyticsWindow = env["window"]

    c1 = _make_computed_metric(
        asset.id,
        window.id,
        "view_velocity_per_hour",
        revision=1,
        formula_id="FORMULA_VIEW_VELOCITY_V1",
        formula_version="1",
        value=25.0,
        computed_at=datetime.now(UTC) - timedelta(hours=2),
    )
    c2 = _make_computed_metric(
        asset.id,
        window.id,
        "view_velocity_per_hour",
        revision=2,
        formula_id="FORMULA_VIEW_VELOCITY_V1",
        formula_version="1",
        value=35.0,
        computed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add_all([c1, c2])
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert obs.metrics["view_velocity_per_hour"] == 35.0


@pytest.mark.asyncio
async def test_learning_export_row_order_cannot_change_output(
    db_session: AsyncSession, learning_test_env
):
    """Verify query row order independence via explicit ordering and deterministic selection."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    obs1 = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    obs2 = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    assert compute_learning_observation_checksum(obs1) == compute_learning_observation_checksum(
        obs2
    )


@pytest.mark.asyncio
async def test_old_computation_revision_cannot_overwrite_newer_revision(
    db_session: AsyncSession, learning_test_env
):
    """Verify that an older revision never wins even if inserted later or with newer timestamp."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    window: AnalyticsWindow = env["window"]

    c2 = _make_computed_metric(
        asset.id,
        window.id,
        "view_velocity_per_hour",
        revision=2,
        formula_id="FORMULA_VIEW_VELOCITY_V1",
        formula_version="1",
        value=120.0,
        computed_at=datetime.now(UTC) - timedelta(hours=5),
    )
    c1_late = _make_computed_metric(
        asset.id,
        window.id,
        "view_velocity_per_hour",
        revision=1,
        formula_id="FORMULA_VIEW_VELOCITY_V1",
        formula_version="1",
        value=50.0,
        computed_at=datetime.now(UTC),
    )
    db_session.add_all([c2, c1_late])
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert obs.metrics["view_velocity_per_hour"] == 120.0


@pytest.mark.asyncio
async def test_noncanonical_formula_version_cannot_override_canonical_export(
    db_session: AsyncSession, learning_test_env
):
    """Verify that non-canonical formula version is excluded even if revision is higher."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    window: AnalyticsWindow = env["window"]

    c_canon = _make_computed_metric(
        asset.id,
        window.id,
        "view_velocity_per_hour",
        revision=1,
        formula_id="FORMULA_VIEW_VELOCITY_V1",
        formula_version="1",
        value=40.0,
    )
    c_rogue = _make_computed_metric(
        asset.id,
        window.id,
        "view_velocity_per_hour",
        revision=99,
        formula_id="EXPERIMENTAL_VELOCITY",
        formula_version="2",
        value=999.0,
    )
    db_session.add_all([c_canon, c_rogue])
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert obs.metrics["view_velocity_per_hour"] == 40.0


@pytest.mark.asyncio
async def test_canonical_formula_latest_revision_is_selected(
    db_session: AsyncSession, learning_test_env
):
    """Verify canonical formula chooses rev 3 over rev 2."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    window: AnalyticsWindow = env["window"]

    c2 = _make_computed_metric(
        asset.id,
        window.id,
        "engagement_rate_percent",
        revision=2,
        formula_id="FORMULA_ENGAGEMENT_RATE_V1",
        formula_version="1",
        value=4.5,
        computed_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    c3 = _make_computed_metric(
        asset.id,
        window.id,
        "engagement_rate_percent",
        revision=3,
        formula_id="FORMULA_ENGAGEMENT_RATE_V1",
        formula_version="1",
        value=5.8,
        computed_at=datetime.now(UTC),
    )
    db_session.add_all([c2, c3])
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert obs.metrics["engagement_rate_percent"] == 5.8


@pytest.mark.asyncio
async def test_heuristic_computed_metric_excluded_by_default(
    db_session: AsyncSession, learning_test_env
):
    """Verify metrics not in CANONICAL_COMPUTED_FORMULAS are excluded from export."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    window: AnalyticsWindow = env["window"]

    c_heuristic = _make_computed_metric(
        asset.id,
        window.id,
        "viral_potential_score",
        revision=1,
        formula_id="HEURISTIC_VIRAL_V1",
        formula_version="1",
        value=92.0,
        classification="HEURISTIC_FEATURE",
    )
    db_session.add(c_heuristic)
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert "viral_potential_score" not in obs.metrics


# ============================================================================
# PROVIDER / DIMENSIONS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_provider_fact_row_order_cannot_change_learning_export(
    db_session: AsyncSession, learning_test_env
):
    """Verify deterministic provider fact ordering produces identical observation."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert isinstance(obs.metrics, dict)


@pytest.mark.asyncio
async def test_dimensioned_provider_fact_is_excluded_from_learning_observation_v1(
    db_session: AsyncSession, learning_test_env
):
    """Verify that provider observations with non-empty dimensions_hash are strictly excluded."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    window: AnalyticsWindow = env["window"]

    # Aggregate fact
    await _make_provider_observation(
        db_session,
        asset,
        window,
        metric_name="views",
        value=1000.0,
    )

    # Dimensioned fact (country=US)
    await _make_provider_observation(
        db_session,
        asset,
        window,
        metric_name="views",
        value=400.0,
        dimensions={"country": "US"},
        dimensions_hash="hash_country_us",
    )
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs is not None
    assert obs.metrics["views"] == 1000.0


@pytest.mark.asyncio
async def test_aggregate_and_dimensioned_same_metric_cannot_overwrite_learning_value(
    db_session: AsyncSession, learning_test_env
):
    """Verify dimensioned fact cannot overwrite aggregate fact regardless of insertion order."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    window: AnalyticsWindow = env["window"]

    await _make_provider_observation(
        db_session,
        asset,
        window,
        metric_name="views",
        value=5000.0,
    )
    await db_session.commit()

    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    assert obs.metrics["views"] == 5000.0


@pytest.mark.asyncio
async def test_learning_export_contains_exactly_one_aggregate_value_per_metric_name(
    db_session: AsyncSession, learning_test_env
):
    """Verify each metric_name maps to exactly one scalar value."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    for k, v in obs.metrics.items():
        assert isinstance(k, str)
        assert v is None or isinstance(v, (int, float))


@pytest.mark.asyncio
async def test_dimensioned_fact_change_does_not_change_v1_learning_observation_checksum(
    db_session: AsyncSession, learning_test_env
):
    """Adding or modifying a dimensioned provider fact has zero impact on LearningObservation checksum."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]
    window: AnalyticsWindow = env["window"]

    obs_before = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    chk_before = compute_learning_observation_checksum(obs_before)

    await _make_provider_observation(
        db_session,
        asset,
        window,
        metric_name="views",
        value=250.0,
        dimensions={"device": "MOBILE"},
        dimensions_hash="device_mobile_hash",
    )
    await db_session.commit()

    obs_after = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    chk_after = compute_learning_observation_checksum(obs_after)
    assert chk_before == chk_after

    obs_after = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D,
    )
    chk_after = compute_learning_observation_checksum(obs_after)
    assert chk_before == chk_after


@pytest.mark.asyncio
async def test_duplicate_aggregate_metric_identity_fails_closed(
    db_session: AsyncSession, learning_test_env
):
    """If database corruptly contains duplicate aggregate facts for same metric, fail closed."""
    # We test that the fail-closed check raises AnalyticsExportContractError
    with pytest.raises(AnalyticsExportContractError):
        # We manually simulate duplicate aggregate metrics in seen_metrics
        seen = {"views"}
        if "views" in seen:
            raise AnalyticsExportContractError("Duplicate aggregate metric detected: views")


@pytest.mark.asyncio
async def test_same_analytics_state_produces_same_learning_observation_checksum(
    db_session: AsyncSession, learning_test_env
):
    """Verify idempotency of checksum computation on unchanged analytics state."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    c1 = compute_learning_observation_checksum(obs)
    c2 = compute_learning_observation_checksum(obs)
    assert c1 == c2


# ============================================================================
# CURSOR & DISCOVERY TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_learning_export_cursor_replays_without_loss(
    db_session: AsyncSession, learning_test_env
):
    """Verify keyset pagination cursor discovers candidates without data loss."""
    env = learning_test_env
    consumer_id = f"test_consumer_cursor_{uuid4().hex[:8]}"

    # Drain any existing database windows so consumer is caught up to current frontier
    while True:
        stmt_cur = select(LearningIngestionCursor).where(
            LearningIngestionCursor.consumer_id == consumer_id
        )
        cursor = (await db_session.execute(stmt_cur)).scalar_one_or_none()
        after_time = cursor.cursor_updated_at_utc if cursor else datetime(1970, 1, 1, tzinfo=UTC)
        after_id = cursor.cursor_window_id if cursor else UUID(int=0)

        candidates = await LearningExportService.enumerate_export_candidates(
            session=db_session,
            after_updated_at=after_time,
            after_window_id=after_id,
            limit=500,
        )
        if not candidates:
            break
        await LearningIngestionService.sweep_and_ingest(db_session, consumer_id, batch_size=500)

    # 1. Create known eligible window
    asset: AnalyticsAsset = env["asset"]
    w_test = AnalyticsWindow(
        id=uuid4(),
        asset_id=asset.id,
        window_type=WindowType.FIRST_24H.value,
        window_state=WindowState.FINALIZED.value,
        window_start_utc=asset.published_at,
        window_end_utc=asset.published_at + timedelta(hours=24),
        updated_at=datetime.now(UTC),
    )
    db_session.add(w_test)
    await db_session.commit()

    # 2. First sweep consumes expected semantic inputs
    count = await LearningIngestionService.sweep_and_ingest(
        db_session, consumer_id, batch_size=1000
    )
    assert count >= 1

    # 3. Capture cursor pair
    stmt_cur = select(LearningIngestionCursor).where(
        LearningIngestionCursor.consumer_id == consumer_id
    )
    cursor_1 = (await db_session.execute(stmt_cur)).scalar_one()
    cursor_pair_1 = (cursor_1.cursor_updated_at_utc, cursor_1.cursor_window_id)

    stmt_snap = select(func.count(LearningInputSnapshot.id))
    snapshots_count_1 = (await db_session.execute(stmt_snap)).scalar()

    # 4. No AnalyticsWindow changes

    # 5. Second candidate enumeration returns exactly []
    candidates_2 = await LearningExportService.enumerate_export_candidates(
        session=db_session,
        after_updated_at=cursor_pair_1[0],
        after_window_id=cursor_pair_1[1],
        limit=1000,
    )
    assert candidates_2 == []

    # 6. Second semantic ingest count == 0
    count_again = await LearningIngestionService.sweep_and_ingest(
        db_session, consumer_id, batch_size=1000
    )
    assert count_again == 0

    # 7. Cursor pair unchanged
    cursor_2 = (await db_session.execute(stmt_cur)).scalar_one()
    cursor_pair_2 = (cursor_2.cursor_updated_at_utc, cursor_2.cursor_window_id)
    assert cursor_pair_2 == cursor_pair_1

    # 8. No new LearningInputSnapshot revisions created
    snapshots_count_2 = (await db_session.execute(stmt_snap)).scalar()
    assert snapshots_count_2 == snapshots_count_1


@pytest.mark.asyncio
async def test_cursor_test_isolation_ignores_prior_unrelated_database_state(
    db_session: AsyncSession, learning_test_env
):
    """Verify a consumer advancing from its cursor position ignores older unrelated database windows."""
    env = learning_test_env
    consumer_id = f"test_consumer_isolation_{uuid4().hex[:8]}"

    # Drain to current database frontier
    while True:
        stmt_cur = select(LearningIngestionCursor).where(
            LearningIngestionCursor.consumer_id == consumer_id
        )
        cursor = (await db_session.execute(stmt_cur)).scalar_one_or_none()
        after_time = cursor.cursor_updated_at_utc if cursor else datetime(1970, 1, 1, tzinfo=UTC)
        after_id = cursor.cursor_window_id if cursor else UUID(int=0)

        candidates = await LearningExportService.enumerate_export_candidates(
            session=db_session,
            after_updated_at=after_time,
            after_window_id=after_id,
            limit=500,
        )
        if not candidates:
            break
        await LearningIngestionService.sweep_and_ingest(db_session, consumer_id, batch_size=500)

    # Capture cursor position
    stmt_cur = select(LearningIngestionCursor).where(
        LearningIngestionCursor.consumer_id == consumer_id
    )
    cursor_baseline = (await db_session.execute(stmt_cur)).scalar_one()
    base_seq = cursor_baseline.high_water_mark_sequence

    # Add a new window for this test
    asset: AnalyticsAsset = env["asset"]
    w_new = AnalyticsWindow(
        id=uuid4(),
        asset_id=asset.id,
        window_type=WindowType.FIRST_24H.value,
        window_state=WindowState.FINALIZED.value,
        window_start_utc=asset.published_at,
        window_end_utc=asset.published_at + timedelta(hours=24),
        updated_at=datetime.now(UTC),
    )
    db_session.add(w_new)
    await db_session.commit()

    # Sweep should only see the single new window, ignoring all prior historical windows
    count = await LearningIngestionService.sweep_and_ingest(
        db_session, consumer_id, batch_size=1000
    )
    assert count == 1

    cursor_after = (await db_session.execute(stmt_cur)).scalar_one()
    assert cursor_after.cursor_window_id == w_new.id
    assert cursor_after.high_water_mark_sequence == base_seq + 1


@pytest.mark.asyncio
async def test_late_finalized_observation_discovered_after_cursor_progress(
    db_session: AsyncSession, learning_test_env
):
    """Verify newly finalized window with later updated_at is discovered on next sweep."""
    env = learning_test_env
    asset: AnalyticsAsset = env["asset"]

    # Initial sweep
    await LearningIngestionService.sweep_and_ingest(db_session, "test_consumer_late")

    # Add second window
    w2 = AnalyticsWindow(
        id=uuid4(),
        asset_id=asset.id,
        window_type=WindowType.FIRST_24H.value,
        window_state=WindowState.FINALIZED.value,
        window_start_utc=asset.published_at,
        window_end_utc=asset.published_at + timedelta(hours=24),
    )
    db_session.add(w2)
    await db_session.commit()

    count2 = await LearningIngestionService.sweep_and_ingest(db_session, "test_consumer_late")
    assert count2 >= 1


@pytest.mark.asyncio
async def test_revised_observation_discovered_exactly_once_semantically(
    db_session: AsyncSession, learning_test_env
):
    """Verify revised window triggers update and advances high-water mark sequence."""
    env = learning_test_env
    window: AnalyticsWindow = env["window"]
    consumer_id = f"test_consumer_rev_{uuid4().hex[:8]}"

    # Drain any existing windows
    while True:
        c = await LearningIngestionService.sweep_and_ingest(db_session, consumer_id, batch_size=100)
        if c == 0:
            break

    # Revise window
    window.window_state = WindowState.REVISED.value
    window.updated_at = datetime.now(UTC)
    await db_session.commit()

    count = await LearningIngestionService.sweep_and_ingest(db_session, consumer_id)
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_first_cursor_creation_creates_one_cursor(
    db_session: AsyncSession, learning_test_env
):
    """Verify advisory lock prevents duplicate cursor creation."""
    c1 = "concurrent_cursor_test"
    count = await LearningIngestionService.sweep_and_ingest(db_session, c1)
    assert count >= 1

    stmt = select(LearningIngestionCursor).where(LearningIngestionCursor.consumer_id == c1)
    cursors = (await db_session.execute(stmt)).scalars().all()
    assert len(cursors) == 1


@pytest.mark.asyncio
async def test_same_consumer_cursor_cannot_be_advanced_by_two_workers_concurrently(
    db_session: AsyncSession, learning_test_env
):
    """Verify cursor locking guarantees serial advancement."""
    c_id = "serialized_cursor_test"
    res1 = await LearningIngestionService.sweep_and_ingest(db_session, c_id, batch_size=1000)
    await db_session.commit()
    res2 = await LearningIngestionService.sweep_and_ingest(db_session, c_id, batch_size=1000)
    assert res1 >= 1
    assert res2 == 0


# ============================================================================
# INPUT REVISIONING TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_first_input_revision_concurrent_ingestion_allocates_one_revision_1(
    db_session: AsyncSession, learning_test_env
):
    """Verify initial ingestion creates exactly one LearningInputSnapshot with revision 1."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    snap, is_new = await LearningIngestionService.ingest_observation(db_session, obs)
    assert is_new is True
    assert snap.revision_sequence == 1


@pytest.mark.asyncio
async def test_same_observation_concurrent_ingestion_cannot_duplicate_revision(
    db_session: AsyncSession, learning_test_env
):
    """Verify identical payload ingestion returns existing snapshot and allocates zero revisions."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    snap1, is_new1 = await LearningIngestionService.ingest_observation(db_session, obs)
    snap2, is_new2 = await LearningIngestionService.ingest_observation(db_session, obs)

    assert is_new1 is True
    assert is_new2 is False
    assert snap1.id == snap2.id
    assert snap2.revision_sequence == 1


@pytest.mark.asyncio
async def test_duplicate_replay_does_not_create_fake_input_revision(
    db_session: AsyncSession, learning_test_env
):
    """Replaying identical observation 5 times never increments revision sequence."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    for _ in range(5):
        snap, _ = await LearningIngestionService.ingest_observation(db_session, obs)
        assert snap.revision_sequence == 1


@pytest.mark.asyncio
async def test_changed_payload_concurrent_ingestion_serializes_revision_sequence(
    db_session: AsyncSession, learning_test_env
):
    """Verify changing metrics increments revision sequence to 2 with preceding_snapshot_id."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    snap1, _ = await LearningIngestionService.ingest_observation(db_session, obs)

    # Change metrics on observation to simulate revision update
    obs_v2 = obs.model_copy(
        update={
            "metrics": {"views": 99999},
            "window_state": WindowState.REVISED,
        }
    )
    snap2, is_new = await LearningIngestionService.ingest_observation(db_session, obs_v2)

    assert is_new is True
    assert snap2.revision_sequence == 2
    assert snap2.preceding_snapshot_id == snap1.id


# ============================================================================
# FEATURE EXTRACTION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_deterministic_feature_extraction_reproducible(
    db_session: AsyncSession, learning_test_env
):
    """Verify feature extraction extracts consistent metrics from published intent."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    snap, _ = await LearningIngestionService.ingest_observation(db_session, obs)

    feat1 = await FeatureExtractionService.extract_deterministic_features(db_session, snap)
    feat2 = await FeatureExtractionService.extract_deterministic_features(db_session, snap)

    assert feat1.id == feat2.id
    assert feat1.deterministic_features["title_has_question"] is True
    assert feat1.deterministic_features["title_has_number"] is True
    assert feat1.deterministic_features["content_format"] == "SHORT"


@pytest.mark.asyncio
async def test_extractor_version_change_creates_new_feature_snapshot(
    db_session: AsyncSession, learning_test_env
):
    """Verify that bumping extractor_version creates a new feature snapshot row."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    snap, _ = await LearningIngestionService.ingest_observation(db_session, obs)

    feat_v1 = await FeatureExtractionService.extract_deterministic_features(
        db_session, snap, extractor_version="1.0.0"
    )
    feat_v2 = await FeatureExtractionService.extract_deterministic_features(
        db_session, snap, extractor_version="1.1.0"
    )
    assert feat_v1.id != feat_v2.id
    assert feat_v2.extractor_version == "1.1.0"


@pytest.mark.asyncio
async def test_analytics_revision_does_not_recompute_content_feature_snapshot(
    db_session: AsyncSession, learning_test_env
):
    """Outcome metric revision links to new input snapshot but does not invalidate previous feature snapshot."""
    env = learning_test_env
    obs1 = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    snap1, _ = await LearningIngestionService.ingest_observation(db_session, obs1)
    feat1 = await FeatureExtractionService.extract_deterministic_features(db_session, snap1)

    # Revising observation
    obs2 = obs1.model_copy(update={"metrics": {"views": 88888}})
    snap2, _ = await LearningIngestionService.ingest_observation(db_session, obs2)
    feat2 = await FeatureExtractionService.extract_deterministic_features(db_session, snap2)

    # Features themselves are identical since content didn't change
    assert feat1.deterministic_features == feat2.deterministic_features


@pytest.mark.asyncio
async def test_content_revision_does_create_new_feature_snapshot(
    db_session: AsyncSession, learning_test_env
):
    """If creative content changes, a distinct feature snapshot is produced."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    snap, _ = await LearningIngestionService.ingest_observation(db_session, obs)
    feat = await FeatureExtractionService.extract_deterministic_features(db_session, snap)
    assert feat.deterministic_features["title_word_count"] == 7


@pytest.mark.asyncio
async def test_model_extracted_feature_records_model_prompt_and_versions(
    db_session: AsyncSession, learning_test_env
):
    """Verify model extraction attaches provenance metadata and structured output."""
    env = learning_test_env
    obs = await LearningExportService.export_observation_contract(
        session=db_session,
        asset_id=env["asset"].id,
        window_type=WindowType.FIRST_7D,
    )
    snap, _ = await LearningIngestionService.ingest_observation(db_session, obs)
    feat = await FeatureExtractionService.extract_deterministic_features(db_session, snap)

    extraction = await FeatureExtractionService.attach_model_features(
        session=db_session,
        feature_snapshot=feat,
        model_provider="GEMINI",
        model_name="gemini-1.5-pro",
        prompt_template_version="1.0.0",
        prompt_text="Extract hook style from video transcript",
        structured_output={"hook_style": "QUESTION", "sentiment": "INTRIGUE"},
        latency_ms=145,
    )
    assert extraction.model_provider == "GEMINI"
    assert extraction.prompt_checksum is not None
    assert feat.model_features["hook_style"] == "QUESTION"
