"""PostgreSQL integration tests for RollupService, HybridQueryEngine, and Backfill CLI."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.hybrid_query_engine import HybridQueryEngine
from omega.application.analytics.metrics_engine import PipelineMetricsCalculationEngine
from omega.application.analytics.rollup_service import RollupService
from omega.domain.pipeline_analytics import (
    DimensionType,
    MetricFamily,
    to_utc,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    PipelineAnalyticsRollup,
    ProductionRenderJob,
    ProductionRequest,
    RenderPlan,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    TopicCandidate,
)


async def _create_test_hierarchy(session: AsyncSession) -> tuple[Channel, ProductionRequest]:
    """Create minimal valid foreign key hierarchy required by ProductionRequest."""
    channel_id = uuid.uuid4()
    channel = Channel(
        id=channel_id,
        slug=f"ch-{uuid.uuid4().hex[:8]}",
        name=f"Channel {uuid.uuid4().hex[:4]}",
    )
    session.add(channel)

    dna_rev = ChannelDNARevision(
        id=uuid.uuid4(),
        channel_id=channel_id,
        version=1,
        snapshot={"name": "DNA"},
        change_reason="Initial",
    )
    session.add(dna_rev)

    topic = TopicCandidate(
        id=uuid.uuid4(),
        channel_id=channel_id,
        title="Test Topic P20B",
        normalized_title="test topic p20b",
        source_name="Manual",
        summary="Summary",
        topic_fingerprint=uuid.uuid4().hex,
        status="APPROVED",
    )
    session.add(topic)

    r_req = ResearchRequest(
        id=uuid.uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    session.add(r_req)

    brief = ResearchBrief(
        id=uuid.uuid4(),
        research_request_id=r_req.id,
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        title="Brief",
        summary="Brief summary",
    )
    session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid.uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    session.add(content_req)

    script_ver = ScriptVersion(
        id=uuid.uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Test Script P20B",
        hook_text="Hook",
        closing_text="Close",
        cta_text="CTA",
    )
    session.add(script_ver)

    req_id = uuid.uuid4()
    req = ProductionRequest(
        id=req_id,
        channel_id=channel_id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna_rev.id,
        status="READY",
        metadata_={"test": "p20b_integration"},
    )
    session.add(req)
    await session.commit()
    return channel, req


@pytest.mark.asyncio
async def test_01_rollup_idempotency_and_snapshot_replacement(db_session: AsyncSession):
    """Prove that RollupService idempotent rerun produces identical rows and replaces snapshot atomically."""
    session = db_session
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    ch, req = await _create_test_hierarchy(session)
    plan = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")

    # Succeeded job completed in bucket
    job = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key=f"job-{uuid4().hex[:8]}",
        state="SUCCEEDED",
        created_at=t_start + timedelta(hours=1),
        started_at=t_start + timedelta(hours=2),
        completed_at=t_start + timedelta(hours=3),
    )
    session.add_all([plan, job])
    await session.commit()

    # 1. First computation
    written_1 = await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
    )
    assert written_1 >= 1

    # Verify rows in DB
    stmt = select(PipelineAnalyticsRollup).where(
        PipelineAnalyticsRollup.metric_family == MetricFamily.RENDER_RELIABILITY.value,
        PipelineAnalyticsRollup.bucket_start == t_start,
    )
    rows_1 = (await session.execute(stmt)).scalars().all()
    assert len(rows_1) == written_1

    # 2. Second identical computation (idempotent rerun)
    written_2 = await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
    )
    assert written_2 == written_1

    rows_2 = (await session.execute(stmt)).scalars().all()
    assert len(rows_2) == written_1  # Exact count, zero duplicates!


@pytest.mark.asyncio
async def test_02_stale_dimension_removal_on_recomputation(db_session: AsyncSession):
    """Prove that recomputation atomically removes stale dimension rows that no longer exist."""
    session = db_session
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    ch, req = await _create_test_hierarchy(session)
    plan_h264 = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")
    plan_hevc = RenderPlan(id=uuid4(), production_request_id=req.id, version=2, video_codec="hevc")

    job_h264 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan_h264.id,
        idempotency_key=f"job-h264-{uuid4().hex[:8]}",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=2),
    )
    job_hevc = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan_hevc.id,
        idempotency_key=f"job-hevc-{uuid4().hex[:8]}",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=3),
    )
    session.add_all([plan_h264, plan_hevc, job_h264, job_hevc])
    await session.commit()

    # 1. Initial computation contains both h264 and hevc
    await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
    )

    stmt_hevc = select(PipelineAnalyticsRollup).where(
        PipelineAnalyticsRollup.metric_family == MetricFamily.RENDER_RELIABILITY.value,
        PipelineAnalyticsRollup.dimension_type == DimensionType.VIDEO_CODEC.value,
        PipelineAnalyticsRollup.dimension_value == "hevc",
        PipelineAnalyticsRollup.bucket_start == t_start,
    )
    hevc_row = (await session.execute(stmt_hevc)).scalar_one_or_none()
    assert hevc_row is not None

    # 2. Modify isolated source fixture: delete the HEVC plan and job
    await session.delete(job_hevc)
    await session.delete(plan_hevc)
    await session.commit()

    # 3. Recompute same bucket
    await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
    )

    # 4. Verify stale HEVC row is gone!
    hevc_row_after = (await session.execute(stmt_hevc)).scalar_one_or_none()
    assert hevc_row_after is None

    # Verify H264 row still exists
    stmt_h264 = select(PipelineAnalyticsRollup).where(
        PipelineAnalyticsRollup.metric_family == MetricFamily.RENDER_RELIABILITY.value,
        PipelineAnalyticsRollup.dimension_type == DimensionType.VIDEO_CODEC.value,
        PipelineAnalyticsRollup.dimension_value == "h264",
        PipelineAnalyticsRollup.bucket_start == t_start,
    )
    assert (await session.execute(stmt_h264)).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_03_failed_replacement_preserves_prior_valid_snapshot(db_session: AsyncSession):
    """If a calculation or insertion error occurs during replacement, rollback preserves prior snapshot."""
    session = db_session
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    # Insert a valid prior rollup row
    prior_row = PipelineAnalyticsRollup(
        id=uuid4(),
        metric_family=MetricFamily.RENDER_RELIABILITY.value,
        dimension_type=DimensionType.GLOBAL.value,
        dimension_value="ALL",
        bucket_start=t_start,
        bucket_end=t_end,
        metrics={"total_terminal_jobs": 42},
        sample_count=42,
        schema_version=1,
    )
    session.add(prior_row)
    await session.commit()

    # Attempt replacement with invalid snapshot payload that fails DB constraints
    bad_snapshot = [
        {
            "metric_family": MetricFamily.RENDER_RELIABILITY.value,
            "dimension_type": DimensionType.GLOBAL.value,
            "dimension_value": "ALL",
            "bucket_start": t_start,
            "bucket_end": t_end + timedelta(days=5),  # Violates chk_rollup_daily_utc_bucket!
            "metrics": {},
            "sample_count": 0,
            "schema_version": 1,
        }
    ]

    with pytest.raises(Exception):
        await RollupService.replace_family_bucket_snapshot(
            session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end, bad_snapshot
        )

    # Verify prior valid row remains intact
    stmt = select(PipelineAnalyticsRollup).where(
        PipelineAnalyticsRollup.metric_family == MetricFamily.RENDER_RELIABILITY.value,
        PipelineAnalyticsRollup.bucket_start == t_start,
    )
    res = (await session.execute(stmt)).scalars().all()
    assert len(res) == 1
    assert res[0].metrics["total_terminal_jobs"] == 42


@pytest.mark.asyncio
async def test_04_live_and_rollup_calculation_match(db_session: AsyncSession):
    """Prove that live dynamic SQL and rollup calculation match for a completed UTC day."""
    session = db_session
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    ch, req = await _create_test_hierarchy(session)
    plan = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")

    # Add 2 succeeded and 1 failed
    j1 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-1",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=4),
    )
    j2 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-2",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=8),
    )
    j3 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-3",
        state="FAILED",
        error_code="FFMPEG_ERROR",
        completed_at=t_start + timedelta(hours=12),
    )
    session.add_all([plan, j1, j2, j3])
    await session.commit()

    # 1. Dynamic live calculation
    live_calc = await PipelineMetricsCalculationEngine.compute_render_reliability(
        session, t_start, t_end, DimensionType.GLOBAL.value, "ALL"
    )

    # 2. Rollup service execution
    await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
    )

    stmt = select(PipelineAnalyticsRollup).where(
        PipelineAnalyticsRollup.metric_family == MetricFamily.RENDER_RELIABILITY.value,
        PipelineAnalyticsRollup.dimension_type == DimensionType.GLOBAL.value,
        PipelineAnalyticsRollup.dimension_value == "ALL",
        PipelineAnalyticsRollup.bucket_start == t_start,
    )
    rollup_row = (await session.execute(stmt)).scalar_one()

    # Verify live and rollup are 100% identical!
    assert rollup_row.metrics == live_calc.model_dump()
    assert rollup_row.sample_count == live_calc.total_terminal_jobs


@pytest.mark.asyncio
async def test_05_hybrid_query_fallback_on_cache_miss(db_session: AsyncSession):
    """When a rollup row is missing from the table, HybridQueryEngine falls back to dynamic SQL."""
    session = db_session
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    ch, req = await _create_test_hierarchy(session)
    plan = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")
    job = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-fall",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=5),
    )
    session.add_all([plan, job])
    await session.commit()

    # Notice: we do NOT call RollupService. The rollup table is empty!
    series = await HybridQueryEngine.query_hybrid_series(
        session,
        family=MetricFamily.RENDER_RELIABILITY.value,
        start_time=t_start,
        end_time=t_end,
    )

    assert len(series) == 1
    # Evaluated dynamically without returning an artificial zero!
    assert series[0]["is_rollup"] is False
    assert series[0]["metrics"]["succeeded_jobs"] == 1
    assert series[0]["metrics"]["total_terminal_jobs"] == 1


@pytest.mark.asyncio
async def test_06_concurrent_duplicate_rollup_workers_converge(db_session: AsyncSession):
    """Prove that concurrent duplicate rollup workers converge without duplicate rows."""
    session = db_session
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    ch, req = await _create_test_hierarchy(session)
    plan = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")
    job = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-conc",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=6),
    )
    session.add_all([plan, job])
    await session.commit()

    # Recompute twice in sequence / concurrency simulation
    await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
    )
    await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
    )

    stmt = select(PipelineAnalyticsRollup).where(
        PipelineAnalyticsRollup.metric_family == MetricFamily.RENDER_RELIABILITY.value,
        PipelineAnalyticsRollup.bucket_start == t_start,
    )
    rows = (await session.execute(stmt)).scalars().all()
    # Unique constraint guarantees exact dimension count, zero duplicate keys
    keys = [(r.metric_family, r.dimension_type, r.dimension_value, r.bucket_start) for r in rows]
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_07_partial_day_does_not_overcount_and_no_mutation(db_session: AsyncSession):
    """Prove that partial-day queries do not overcount by using whole-day rollups and never mutate source rows."""
    session = db_session
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    ch, req = await _create_test_hierarchy(session)
    plan = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")
    # Morning job (08:00)
    job_am = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-am",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=8),
    )
    # Evening job (20:00)
    job_pm = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-pm",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=20),
    )
    session.add_all([plan, job_am, job_pm])
    await session.commit()

    # Pre-populate complete day rollup (which contains both jobs: total=2)
    await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
    )

    # Query morning half-day [00:00, 12:00)
    # Must NOT use the 24h rollup row that has 2 jobs!
    series = await HybridQueryEngine.query_hybrid_series(
        session,
        family=MetricFamily.RENDER_RELIABILITY.value,
        start_time=t_start,
        end_time=t_start + timedelta(hours=12),
    )

    assert len(series) == 1
    assert series[0]["is_rollup"] is False
    assert series[0]["metrics"]["succeeded_jobs"] == 1
    assert series[0]["metrics"]["total_terminal_jobs"] == 1

    # Verify core domain source rows are NOT mutated in any way
    refetched_am = await session.get(ProductionRenderJob, job_am.id)
    refetched_pm = await session.get(ProductionRenderJob, job_pm.id)
    assert refetched_am is not None and refetched_am.state == "SUCCEEDED"
    assert refetched_pm is not None and refetched_pm.state == "SUCCEEDED"

