"""PostgreSQL integration tests for RollupService, HybridQueryEngine, and Backfill CLI."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
import sys
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

backend_dir = Path(__file__).resolve().parent.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
from scripts.backfill_analytics_rollups import run_backfill

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

    # Truly concurrent recomputation workers on separate sessions
    session_maker = async_sessionmaker(session.bind, expire_on_commit=False)

    async def _worker():
        async with session_maker() as s:
            return await RollupService.recompute_and_replace_bucket(
                s, MetricFamily.RENDER_RELIABILITY.value, t_start, t_end
            )

    results = await asyncio.gather(_worker(), _worker())
    assert all(r > 0 for r in results)

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


@pytest.mark.asyncio
async def test_08_true_hybrid_rollup_plus_dynamic(db_session: AsyncSession):
    """Prove true hybrid execution with dynamic first segment, rollup middle segment, and dynamic final segment.

    Range:
    - start = Day D 12:00 UTC
    - complete middle day = Day D+1 00:00 -> D+2 00:00 UTC
    - end = current/open Day D+2 12:00 UTC

    Execution paths:
    - [D 12:00, D+1 00:00) -> dynamic authoritative SQL (is_rollup=False, is_live=False)
    - [D+1 00:00, D+2 00:00) -> persisted DAILY ROLLUP (is_rollup=True, is_live=False)
    - [D+2 00:00, D+2 12:00) -> dynamic authoritative SQL (is_rollup=False, is_live=True)

    Seed distinguishable counts in each segment:
    - Segment 1: 1 succeeded job
    - Segment 2: 2 succeeded jobs
    - Segment 3: 3 succeeded jobs

    Verify exact final aggregate:
    - 0 overlap, 0 gap, 0 double counting
    - Total succeeded = 6, total terminal = 6
    """
    session = db_session
    day_d = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_start = day_d + timedelta(hours=12)          # D 12:00 UTC
    d_plus_1 = day_d + timedelta(days=1)           # D+1 00:00 UTC
    d_plus_2 = day_d + timedelta(days=2)           # D+2 00:00 UTC
    t_end = day_d + timedelta(days=2, hours=12)    # D+2 12:00 UTC

    ch, req = await _create_test_hierarchy(session)
    plan = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")

    # Segment 1 job: D 15:00 UTC (1 job)
    j_seg1 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-seg1-1",
        state="SUCCEEDED",
        completed_at=day_d + timedelta(hours=15),
    )

    # Segment 2 jobs: D+1 06:00 and D+1 18:00 UTC (2 jobs)
    j_seg2_1 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-seg2-1",
        state="SUCCEEDED",
        completed_at=d_plus_1 + timedelta(hours=6),
    )
    j_seg2_2 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-seg2-2",
        state="SUCCEEDED",
        completed_at=d_plus_1 + timedelta(hours=18),
    )

    # Segment 3 jobs: D+2 03:00, D+2 06:00, D+2 09:00 UTC (3 jobs)
    j_seg3_1 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-seg3-1",
        state="SUCCEEDED",
        completed_at=d_plus_2 + timedelta(hours=3),
    )
    j_seg3_2 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-seg3-2",
        state="SUCCEEDED",
        completed_at=d_plus_2 + timedelta(hours=6),
    )
    j_seg3_3 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-seg3-3",
        state="SUCCEEDED",
        completed_at=d_plus_2 + timedelta(hours=9),
    )

    session.add_all([plan, j_seg1, j_seg2_1, j_seg2_2, j_seg3_1, j_seg3_2, j_seg3_3])
    await session.commit()

    # Pre-populate rollup ONLY for middle complete day [D+1 00:00, D+2 00:00)
    await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, d_plus_1, d_plus_2
    )

    # Query hybrid series with now_utc = t_end (simulating current open day at D+2 12:00)
    series = await HybridQueryEngine.query_hybrid_series(
        session,
        family=MetricFamily.RENDER_RELIABILITY.value,
        start_time=t_start,
        end_time=t_end,
        now_utc=t_end,
    )

    assert len(series) == 3

    # Segment 1: [D 12:00, D+1 00:00) -> dynamic SQL
    seg1 = series[0]
    assert seg1["bucket_start"] == t_start
    assert seg1["bucket_end"] == d_plus_1
    assert seg1["is_rollup"] is False
    assert seg1["is_live"] is False
    assert seg1["metrics"]["succeeded_jobs"] == 1
    assert seg1["metrics"]["total_terminal_jobs"] == 1

    # Segment 2: [D+1 00:00, D+2 00:00) -> persisted DAILY ROLLUP
    seg2 = series[1]
    assert seg2["bucket_start"] == d_plus_1
    assert seg2["bucket_end"] == d_plus_2
    assert seg2["is_rollup"] is True
    assert seg2["is_live"] is False
    assert seg2["metrics"]["succeeded_jobs"] == 2
    assert seg2["metrics"]["total_terminal_jobs"] == 2

    # Segment 3: [D+2 00:00, D+2 12:00) -> dynamic live SQL
    seg3 = series[2]
    assert seg3["bucket_start"] == d_plus_2
    assert seg3["bucket_end"] == t_end
    assert seg3["is_rollup"] is False
    assert seg3["is_live"] is True
    assert seg3["metrics"]["succeeded_jobs"] == 3
    assert seg3["metrics"]["total_terminal_jobs"] == 3

    total_succeeded = sum(s["metrics"]["succeeded_jobs"] for s in series)
    total_terminal = sum(s["metrics"]["total_terminal_jobs"] for s in series)
    assert total_succeeded == 6
    assert total_terminal == 6


@pytest.mark.asyncio
async def test_09_hybrid_missing_rollup_fallback(db_session: AsyncSession):
    """When the middle closed-day rollup is missing/deleted, verify clean fallback to dynamic SQL with identical aggregates."""
    session = db_session
    day_d = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_start = day_d + timedelta(hours=12)
    d_plus_1 = day_d + timedelta(days=1)
    d_plus_2 = day_d + timedelta(days=2)
    t_end = day_d + timedelta(days=2, hours=12)

    ch, req = await _create_test_hierarchy(session)
    plan = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")

    # Segment 1 job: 1 job
    j_seg1 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-fb-1",
        state="SUCCEEDED",
        completed_at=day_d + timedelta(hours=15),
    )
    # Segment 2 jobs: 2 jobs
    j_seg2_1 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-fb-2",
        state="SUCCEEDED",
        completed_at=d_plus_1 + timedelta(hours=6),
    )
    j_seg2_2 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-fb-3",
        state="SUCCEEDED",
        completed_at=d_plus_1 + timedelta(hours=18),
    )
    # Segment 3 jobs: 3 jobs
    j_seg3_1 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-fb-4",
        state="SUCCEEDED",
        completed_at=d_plus_2 + timedelta(hours=3),
    )
    j_seg3_2 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-fb-5",
        state="SUCCEEDED",
        completed_at=d_plus_2 + timedelta(hours=6),
    )
    j_seg3_3 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-fb-6",
        state="SUCCEEDED",
        completed_at=d_plus_2 + timedelta(hours=9),
    )

    session.add_all([plan, j_seg1, j_seg2_1, j_seg2_2, j_seg3_1, j_seg3_2, j_seg3_3])
    await session.commit()

    # Pre-populate rollup for middle day
    await RollupService.recompute_and_replace_bucket(
        session, MetricFamily.RENDER_RELIABILITY.value, d_plus_1, d_plus_2
    )

    # 1. Query with rollup present
    series_with_rollup = await HybridQueryEngine.query_hybrid_series(
        session,
        family=MetricFamily.RENDER_RELIABILITY.value,
        start_time=t_start,
        end_time=t_end,
        now_utc=t_end,
    )
    assert series_with_rollup[1]["is_rollup"] is True

    # 2. Delete the middle rollup row to make it unavailable
    del_stmt = text(
        "DELETE FROM pipeline_analytics_rollups WHERE metric_family = :fam AND bucket_start = :b_start"
    )
    await session.execute(del_stmt, {"fam": MetricFamily.RENDER_RELIABILITY.value, "b_start": d_plus_1})
    await session.commit()

    # 3. Re-run identical query
    series_without_rollup = await HybridQueryEngine.query_hybrid_series(
        session,
        family=MetricFamily.RENDER_RELIABILITY.value,
        start_time=t_start,
        end_time=t_end,
        now_utc=t_end,
    )

    assert len(series_without_rollup) == 3
    # Middle segment fell back to authoritative dynamic SQL
    assert series_without_rollup[1]["is_rollup"] is False
    assert series_without_rollup[1]["metrics"]["succeeded_jobs"] == 2
    assert series_without_rollup[1]["metrics"]["total_terminal_jobs"] == 2

    # Exact final metrics match prior request
    for i in range(3):
        assert series_without_rollup[i]["metrics"] == series_with_rollup[i]["metrics"]
        assert series_without_rollup[i]["sample_count"] == series_with_rollup[i]["sample_count"]


@pytest.mark.asyncio
async def test_10_backfill_vs_periodic_rollup_parity(db_session: AsyncSession):
    """Prove canonical rowset parity between periodic RollupService computation and backfill CLI."""
    session = db_session
    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    ch, req = await _create_test_hierarchy(session)
    plan = RenderPlan(id=uuid4(), production_request_id=req.id, version=1, video_codec="h264")
    j1 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-parity-1",
        state="SUCCEEDED",
        completed_at=t_start + timedelta(hours=5),
    )
    j2 = ProductionRenderJob(
        id=uuid4(),
        production_request_id=req.id,
        render_plan_id=plan.id,
        idempotency_key="j-parity-2",
        state="FAILED",
        error_code="WORKER_LEASE_EXPIRED",
        completed_at=t_start + timedelta(hours=10),
    )
    session.add_all([plan, j1, j2])
    await session.commit()

    # A. Generate rollup via RollupService for all families
    for fam in [
        MetricFamily.RENDER_RELIABILITY.value,
        MetricFamily.RENDER_PERFORMANCE.value,
        MetricFamily.SCHEDULER_RELIABILITY.value,
        MetricFamily.QA_QUALITY.value,
    ]:
        await RollupService.recompute_and_replace_bucket(session, fam, t_start, t_end)

    # Capture canonical rows
    stmt = (
        select(
            PipelineAnalyticsRollup.metric_family,
            PipelineAnalyticsRollup.dimension_type,
            PipelineAnalyticsRollup.dimension_value,
            PipelineAnalyticsRollup.sample_count,
            PipelineAnalyticsRollup.metrics,
        )
        .where(PipelineAnalyticsRollup.bucket_start == t_start)
        .order_by(
            PipelineAnalyticsRollup.metric_family,
            PipelineAnalyticsRollup.dimension_type,
            PipelineAnalyticsRollup.dimension_value,
        )
    )
    periodic_rows = (await session.execute(stmt)).fetchall()
    assert len(periodic_rows) > 0

    # B. Clear derived rollup rows for that day
    del_stmt = text("DELETE FROM pipeline_analytics_rollups WHERE bucket_start = :b_start")
    await session.execute(del_stmt, {"b_start": t_start})
    await session.commit()

    # C. Generate using backfill CLI
    rows_written = await run_backfill(t_start, t_end, session=session)
    assert rows_written == len(periodic_rows)

    # Compare complete canonical rowsets
    backfill_rows = (await session.execute(stmt)).fetchall()
    assert len(backfill_rows) == len(periodic_rows)

    for p_row, b_row in zip(periodic_rows, backfill_rows, strict=True):
        assert p_row.metric_family == b_row.metric_family
        assert p_row.dimension_type == b_row.dimension_type
        assert p_row.dimension_value == b_row.dimension_value
        assert p_row.sample_count == b_row.sample_count
        assert p_row.metrics == b_row.metrics
