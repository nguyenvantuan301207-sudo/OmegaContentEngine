"""REST API endpoints for OMEGA-012 Analytics Engine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.analytics.poll_service import AnalyticsPollService
from omega.application.analytics.quota_service import QuotaService
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsChannelLatestPointer,
    AnalyticsChannelSnapshot,
    AnalyticsComputedMetric,
    AnalyticsMetricLatestPointer,
    AnalyticsMetricObservation,
    AnalyticsQuotaBucket,
    AnalyticsWindow,
)
from omega.logging import get_logger

logger = get_logger(service="omega-analytics-api")

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


# ── Response Schemas ────────────────────────────────────────────────────────────


class MetricValueResponse(BaseModel):
    metric_name: str
    value: float | int | None
    quality: str
    classification: str


class VideoAnalyticsSummaryResponse(BaseModel):
    asset_id: UUID
    publish_intent_id: UUID
    provider_video_id: str
    asset_status: str
    lifecycle_phase: str
    published_at: datetime
    last_polled_at: datetime | None
    latest_metrics: dict[str, MetricValueResponse]


class TimelineWindowPoint(BaseModel):
    window_type: str
    window_state: str
    window_start_utc: datetime
    window_end_utc: datetime
    metrics: dict[str, MetricValueResponse]


class VideoAnalyticsTimelineResponse(BaseModel):
    asset_id: UUID
    publish_intent_id: UUID
    provider_video_id: str
    timeline: list[TimelineWindowPoint]


class ChannelAnalyticsSummaryResponse(BaseModel):
    channel_id: UUID
    snapshot_date: str
    total_views: int
    subscriber_count: int
    video_count: int
    aggregate_watch_time_seconds: float | None
    revision_sequence: int


class QuotaBucketHealth(BaseModel):
    provider: str
    quota_bucket: str
    method: str
    configured_daily_limit: int | None
    internal_budget_limit: int | None
    provider_authoritative_usage: int | None
    estimated_internal_units: int
    utilization_percent: float | None
    next_reset_at_utc: datetime


class AnalyticsHealthResponse(BaseModel):
    status: str
    active_assets_count: int
    quota_buckets: list[QuotaBucketHealth]


# ── API Routes ─────────────────────────────────────────────────────────────────


@router.get("/videos/{publish_intent_id}", response_model=VideoAnalyticsSummaryResponse)
async def get_video_analytics_summary(
    publish_intent_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VideoAnalyticsSummaryResponse:
    """Retrieve current performance summary for a published intent."""
    stmt = select(AnalyticsAsset).where(AnalyticsAsset.publish_intent_id == publish_intent_id)
    res = await session.execute(stmt)
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No analytics asset found for publish intent {publish_intent_id}",
        )

    # Fetch latest observations via pointer
    stmt_obs = (
        select(AnalyticsMetricObservation)
        .join(
            AnalyticsMetricLatestPointer,
            AnalyticsMetricLatestPointer.current_observation_id == AnalyticsMetricObservation.id,
        )
        .where(AnalyticsMetricObservation.asset_id == asset.id)
    )
    observations = (await session.execute(stmt_obs)).scalars().all()

    # Fetch latest computed metrics
    stmt_comp = select(AnalyticsComputedMetric).where(AnalyticsComputedMetric.asset_id == asset.id)
    computed = (await session.execute(stmt_comp)).scalars().all()

    metrics: dict[str, MetricValueResponse] = {}
    for obs in observations:
        val = obs.integer_value if obs.integer_value is not None else obs.numeric_value
        metrics[obs.metric_name] = MetricValueResponse(
            metric_name=obs.metric_name,
            value=val,
            quality=obs.metric_quality,
            classification=obs.classification,
        )

    for comp in computed:
        metrics[comp.metric_name] = MetricValueResponse(
            metric_name=comp.metric_name,
            value=comp.numeric_value,
            quality=comp.metric_quality,
            classification=comp.classification,
        )

    return VideoAnalyticsSummaryResponse(
        asset_id=asset.id,
        publish_intent_id=asset.publish_intent_id,
        provider_video_id=asset.provider_video_id,
        asset_status=asset.asset_status,
        lifecycle_phase=asset.lifecycle_phase,
        published_at=asset.published_at,
        last_polled_at=asset.last_polled_at,
        latest_metrics=metrics,
    )


@router.get("/videos/{publish_intent_id}/timeline", response_model=VideoAnalyticsTimelineResponse)
async def get_video_analytics_timeline(
    publish_intent_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VideoAnalyticsTimelineResponse:
    """Retrieve chronological metrics timeline across all evaluation windows."""
    stmt = select(AnalyticsAsset).where(AnalyticsAsset.publish_intent_id == publish_intent_id)
    res = await session.execute(stmt)
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No analytics asset found for publish intent {publish_intent_id}",
        )

    stmt_win = (
        select(AnalyticsWindow)
        .where(AnalyticsWindow.asset_id == asset.id)
        .order_by(AnalyticsWindow.window_start_utc.asc())
    )
    windows = (await session.execute(stmt_win)).scalars().all()

    timeline: list[TimelineWindowPoint] = []
    for win in windows:
        # Load observations for this window
        stmt_obs = select(AnalyticsMetricObservation).where(
            AnalyticsMetricObservation.window_id == win.id
        )
        obs_list = (await session.execute(stmt_obs)).scalars().all()

        stmt_comp = select(AnalyticsComputedMetric).where(
            AnalyticsComputedMetric.window_id == win.id
        )
        comp_list = (await session.execute(stmt_comp)).scalars().all()

        win_metrics: dict[str, MetricValueResponse] = {}
        for obs in obs_list:
            val = obs.integer_value if obs.integer_value is not None else obs.numeric_value
            win_metrics[obs.metric_name] = MetricValueResponse(
                metric_name=obs.metric_name,
                value=val,
                quality=obs.metric_quality,
                classification=obs.classification,
            )
        for comp in comp_list:
            win_metrics[comp.metric_name] = MetricValueResponse(
                metric_name=comp.metric_name,
                value=comp.numeric_value,
                quality=comp.metric_quality,
                classification=comp.classification,
            )

        timeline.append(
            TimelineWindowPoint(
                window_type=win.window_type,
                window_state=win.window_state,
                window_start_utc=win.window_start_utc,
                window_end_utc=win.window_end_utc,
                metrics=win_metrics,
            )
        )

    return VideoAnalyticsTimelineResponse(
        asset_id=asset.id,
        publish_intent_id=asset.publish_intent_id,
        provider_video_id=asset.provider_video_id,
        timeline=timeline,
    )


@router.get("/channels/{channel_id}", response_model=ChannelAnalyticsSummaryResponse)
async def get_channel_analytics_summary(
    channel_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelAnalyticsSummaryResponse:
    """Retrieve current channel-level aggregate metrics."""
    stmt = (
        select(AnalyticsChannelSnapshot)
        .join(
            AnalyticsChannelLatestPointer,
            AnalyticsChannelLatestPointer.current_channel_snapshot_id
            == AnalyticsChannelSnapshot.id,
        )
        .where(AnalyticsChannelSnapshot.channel_id == channel_id)
        .order_by(AnalyticsChannelSnapshot.snapshot_date.desc())
        .limit(1)
    )
    res = await session.execute(stmt)
    snapshot = res.scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No channel analytics snapshot found for channel {channel_id}",
        )

    return ChannelAnalyticsSummaryResponse(
        channel_id=snapshot.channel_id,
        snapshot_date=snapshot.snapshot_date.isoformat(),
        total_views=snapshot.total_views,
        subscriber_count=snapshot.subscriber_count,
        video_count=snapshot.video_count,
        aggregate_watch_time_seconds=snapshot.aggregate_watch_time_seconds,
        revision_sequence=snapshot.revision_sequence,
    )


@router.post("/videos/{publish_intent_id}/refresh")
async def request_manual_refresh(
    publish_intent_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Trigger on-demand manual refresh for an asset with rate-limiting and quota gating."""
    stmt = select(AnalyticsAsset).where(AnalyticsAsset.publish_intent_id == publish_intent_id)
    res = await session.execute(stmt)
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No analytics asset found for publish intent {publish_intent_id}",
        )

    now_utc = datetime.now(UTC)
    if asset.last_polled_at and (now_utc - asset.last_polled_at).total_seconds() < 900:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Manual refresh is rate-limited to once every 15 minutes per asset.",
        )

    manual_id = uuid4()
    poll_res = await AnalyticsPollService.execute_asset_poll(
        session=session,
        asset_id=asset.id,
        worker_id="manual-api",
        manual_refresh_id=manual_id,
    )

    if poll_res.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Refresh failed: {poll_res.get('message')}",
        )

    return {
        "status": "success",
        "manual_refresh_id": str(manual_id),
        "asset_id": str(asset.id),
        "result": poll_res,
    }


@router.get("/health", response_model=AnalyticsHealthResponse)
async def get_analytics_health(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AnalyticsHealthResponse:
    """Retrieve engine operational health and quota bucket utilization."""
    stmt_count = (
        select(func.count())
        .select_from(AnalyticsAsset)
        .where(AnalyticsAsset.asset_status == "ACTIVE")
    )
    active_count = (await session.execute(stmt_count)).scalar() or 0

    stmt_buckets = select(AnalyticsQuotaBucket)
    buckets = (await session.execute(stmt_buckets)).scalars().all()

    bucket_responses: list[QuotaBucketHealth] = []
    for b in buckets:
        pct = QuotaService.get_utilization_percent(b)
        bucket_responses.append(
            QuotaBucketHealth(
                provider=b.provider,
                quota_bucket=b.quota_bucket,
                method=b.method,
                configured_daily_limit=b.configured_daily_limit,
                internal_budget_limit=b.internal_budget_limit,
                provider_authoritative_usage=b.provider_authoritative_usage,
                estimated_internal_units=b.estimated_internal_units,
                utilization_percent=pct,
                next_reset_at_utc=b.next_reset_at_utc,
            )
        )

    return AnalyticsHealthResponse(
        status="healthy",
        active_assets_count=active_count,
        quota_buckets=bucket_responses,
    )
