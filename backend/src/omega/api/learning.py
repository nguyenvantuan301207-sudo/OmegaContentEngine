"""REST API endpoints for OMEGA-013 Learning Engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.learning.job_service import LearningJobService
from omega.infrastructure.models import (
    Channel,
    LearningBaseline,
    LearningHypothesis,
    LearningHypothesisEvaluation,
    LearningHypothesisLatestPointer,
    LearningJob,
    LearningKnowledgeItem,
    LearningKnowledgeLatestPointer,
)
from omega.logging import get_logger

logger = get_logger(service="omega-learning-api")

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])


# ── Response Schemas ────────────────────────────────────────────────────────────


class KnowledgeItemResponse(BaseModel):
    knowledge_family_id: UUID
    knowledge_item_id: UUID
    channel_id: UUID
    knowledge_type: str
    structured_claim: dict[str, Any]
    human_readable_summary: str
    evidence_type: str
    confidence_class: str
    effect_size_absolute: float
    effect_size_relative_percent: float | None
    cliffs_delta: float
    sample_size_treatment: int
    sample_size_control: int
    current_status: str
    status_reason: str | None
    revision_number: int
    created_at: datetime
    updated_at: datetime


class HypothesisSummaryResponse(BaseModel):
    hypothesis_family_id: UUID
    hypothesis_id: UUID
    channel_id: UUID
    cohort_id: UUID
    hypothesis_slug: str
    description: str
    factor_name: str
    treatment_definition: dict[str, Any]
    control_definition: dict[str, Any]
    target_outcome_metric: str
    target_evaluation_window: str
    current_version: int
    current_status: str
    updated_at: datetime


class HypothesisEvaluationResponse(BaseModel):
    evaluation_id: UUID
    hypothesis_id: UUID
    evaluation_version: int
    sample_size_treatment: int
    sample_size_control: int
    treatment_median: float
    control_median: float
    effect_size_absolute: float
    effect_size_relative_percent: float | None
    cliffs_delta: float
    p_value_raw: float
    p_value_adjusted: float
    confidence_class: str
    resulting_status: str
    evaluated_at: datetime


class BaselineResponse(BaseModel):
    baseline_id: UUID
    cohort_id: UUID
    outcome_metric: str
    evaluation_window: str
    baseline_version: int
    member_count: int
    metric_median: float
    metric_mean: float
    metric_stddev: float
    metric_iqr: float
    metric_mad: float
    calculated_at: datetime


class RefreshResponse(BaseModel):
    job_id: UUID
    channel_id: UUID
    status: str
    created_at: datetime


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get(
    "/channels/{channel_id}/knowledge",
    response_model=list[KnowledgeItemResponse],
    summary="Get active and historical knowledge items for a channel",
)
async def get_channel_knowledge(
    channel_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = Query(None, alias="status"),
) -> list[KnowledgeItemResponse]:
    """Retrieve validated institutional knowledge claims for a channel."""
    stmt = (
        select(LearningKnowledgeLatestPointer, LearningKnowledgeItem)
        .join(
            LearningKnowledgeItem,
            LearningKnowledgeItem.id == LearningKnowledgeLatestPointer.current_knowledge_item_id,
        )
        .where(LearningKnowledgeLatestPointer.channel_id == channel_id)
    )
    if status_filter:
        stmt = stmt.where(LearningKnowledgeLatestPointer.current_status == status_filter.upper())

    rows = (await session.execute(stmt)).all()
    results: list[KnowledgeItemResponse] = []
    for ptr, item in rows:
        results.append(
            KnowledgeItemResponse(
                knowledge_family_id=ptr.knowledge_family_id,
                knowledge_item_id=item.id,
                channel_id=ptr.channel_id,
                knowledge_type=item.knowledge_type,
                structured_claim=item.structured_claim,
                human_readable_summary=item.human_readable_summary,
                evidence_type=item.evidence_type,
                confidence_class=item.confidence_class,
                effect_size_absolute=item.effect_size_absolute,
                effect_size_relative_percent=item.effect_size_relative_percent,
                cliffs_delta=item.cliffs_delta,
                sample_size_treatment=item.sample_size_treatment,
                sample_size_control=item.sample_size_control,
                current_status=ptr.current_status,
                status_reason=ptr.status_reason,
                revision_number=item.revision_number,
                created_at=item.created_at,
                updated_at=ptr.updated_at,
            )
        )
    return results


@router.get(
    "/channels/{channel_id}/hypotheses",
    response_model=list[HypothesisSummaryResponse],
    summary="Get all hypotheses and lifecycle states for a channel",
)
async def get_channel_hypotheses(
    channel_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[HypothesisSummaryResponse]:
    """List all investigational hypotheses tracked for a channel."""
    stmt = (
        select(LearningHypothesisLatestPointer, LearningHypothesis)
        .join(
            LearningHypothesis,
            LearningHypothesis.id == LearningHypothesisLatestPointer.current_hypothesis_id,
        )
        .where(LearningHypothesisLatestPointer.channel_id == channel_id)
        .order_by(LearningHypothesisLatestPointer.updated_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    results: list[HypothesisSummaryResponse] = []
    for ptr, hyp in rows:
        results.append(
            HypothesisSummaryResponse(
                hypothesis_family_id=ptr.hypothesis_family_id,
                hypothesis_id=hyp.id,
                channel_id=ptr.channel_id,
                cohort_id=hyp.cohort_id,
                hypothesis_slug=hyp.hypothesis_slug,
                description=hyp.description,
                factor_name=hyp.factor_name,
                treatment_definition=hyp.treatment_definition,
                control_definition=hyp.control_definition,
                target_outcome_metric=hyp.target_outcome_metric,
                target_evaluation_window=hyp.target_evaluation_window,
                current_version=ptr.current_version,
                current_status=ptr.current_status,
                updated_at=ptr.updated_at,
            )
        )
    return results


@router.get(
    "/hypotheses/{hypothesis_family_id}",
    response_model=HypothesisSummaryResponse,
    summary="Get details of a specific hypothesis family",
)
async def get_hypothesis_detail(
    hypothesis_family_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> HypothesisSummaryResponse:
    """Retrieve current definition and state for a hypothesis family."""
    stmt = (
        select(LearningHypothesisLatestPointer, LearningHypothesis)
        .join(
            LearningHypothesis,
            LearningHypothesis.id == LearningHypothesisLatestPointer.current_hypothesis_id,
        )
        .where(LearningHypothesisLatestPointer.hypothesis_family_id == hypothesis_family_id)
    )
    row = (await session.execute(stmt)).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hypothesis family not found",
        )
    ptr, hyp = row
    return HypothesisSummaryResponse(
        hypothesis_family_id=ptr.hypothesis_family_id,
        hypothesis_id=hyp.id,
        channel_id=ptr.channel_id,
        cohort_id=hyp.cohort_id,
        hypothesis_slug=hyp.hypothesis_slug,
        description=hyp.description,
        factor_name=hyp.factor_name,
        treatment_definition=hyp.treatment_definition,
        control_definition=hyp.control_definition,
        target_outcome_metric=hyp.target_outcome_metric,
        target_evaluation_window=hyp.target_evaluation_window,
        current_version=ptr.current_version,
        current_status=ptr.current_status,
        updated_at=ptr.updated_at,
    )


@router.get(
    "/hypotheses/{hypothesis_family_id}/history",
    response_model=list[HypothesisEvaluationResponse],
    summary="Get chronological evaluation history for a hypothesis family",
)
async def get_hypothesis_history(
    hypothesis_family_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[HypothesisEvaluationResponse]:
    """Retrieve immutable evaluation history for a hypothesis family."""
    stmt = (
        select(LearningHypothesisEvaluation)
        .where(LearningHypothesisEvaluation.hypothesis_family_id == hypothesis_family_id)
        .order_by(LearningHypothesisEvaluation.evaluated_at.asc())
    )
    evals = (await session.execute(stmt)).scalars().all()
    return [
        HypothesisEvaluationResponse(
            evaluation_id=e.id,
            hypothesis_id=e.hypothesis_id,
            evaluation_version=e.evaluation_version,
            sample_size_treatment=e.sample_size_treatment,
            sample_size_control=e.sample_size_control,
            treatment_median=e.treatment_median,
            control_median=e.control_median,
            effect_size_absolute=e.effect_size_absolute,
            effect_size_relative_percent=e.effect_size_relative_percent,
            cliffs_delta=e.cliffs_delta,
            p_value_raw=e.p_value_raw,
            p_value_adjusted=e.p_value_adjusted,
            confidence_class=e.confidence_class,
            resulting_status=e.resulting_status,
            evaluated_at=e.evaluated_at,
        )
        for e in evals
    ]


@router.get(
    "/channels/{channel_id}/baselines",
    response_model=list[BaselineResponse],
    summary="Get rolling performance baselines for a channel",
)
async def get_channel_baselines(
    channel_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[BaselineResponse]:
    """Retrieve latest performance baselines across cohorts for a channel."""
    stmt = select(LearningBaseline).order_by(
        LearningBaseline.cohort_id,
        LearningBaseline.outcome_metric,
        LearningBaseline.baseline_version.desc(),
    )
    baselines = (await session.execute(stmt)).scalars().all()
    # Deduplicate in Python to return latest version per (cohort, metric, window)
    seen: set[tuple[UUID, str, str]] = set()
    latest_baselines: list[BaselineResponse] = []
    for b in baselines:
        key = (b.cohort_id, b.outcome_metric, b.evaluation_window)
        if key not in seen:
            seen.add(key)
            latest_baselines.append(
                BaselineResponse(
                    baseline_id=b.id,
                    cohort_id=b.cohort_id,
                    outcome_metric=b.outcome_metric,
                    evaluation_window=b.evaluation_window,
                    baseline_version=b.baseline_version,
                    member_count=b.member_count,
                    metric_median=b.metric_median,
                    metric_mean=b.metric_mean,
                    metric_stddev=b.metric_stddev,
                    metric_iqr=b.metric_iqr,
                    metric_mad=b.metric_mad,
                    calculated_at=b.calculated_at,
                )
            )
    return latest_baselines


@router.post(
    "/channels/{channel_id}/refresh",
    response_model=RefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger an idempotent, rate-limited on-demand learning sweep",
)
async def trigger_learning_refresh(
    channel_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    request_id: UUID | None = None,
) -> RefreshResponse:
    """Enqueues an asynchronous learning evaluation sweep for a channel."""
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel {channel_id} not found",
        )

    from omega.domain.learning import compute_job_dedupe_key

    req_id = request_id or uuid4()
    job_key = compute_job_dedupe_key(channel_id, "MANUAL_REFRESH", str(req_id))

    # Check if exact request already exists (idempotent replay)
    stmt_existing = select(LearningJob).where(
        LearningJob.job_dedupe_key == job_key,
    )
    existing_job = (await session.execute(stmt_existing)).scalars().first()
    if existing_job is not None:
        return RefreshResponse(
            job_id=existing_job.id,
            channel_id=channel_id,
            status=existing_job.status,
            created_at=existing_job.created_at,
        )

    # Rate limiting: maximum 1 new refresh per 60 seconds per channel
    recent_cutoff = datetime.now(UTC) - timedelta(seconds=60)
    stmt_recent = select(LearningJob).where(
        LearningJob.channel_id == channel_id,
        LearningJob.job_type == "MANUAL_REFRESH",
        LearningJob.created_at >= recent_cutoff,
    )
    recent_job = (await session.execute(stmt_recent)).first()
    if recent_job is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Learning refresh rate limited. Please wait 60 seconds between refresh requests.",
        )

    job, is_new = await LearningJobService.get_or_create_job(
        session=session,
        channel_id=channel_id,
        job_type="MANUAL_REFRESH",
        execution_identity=str(req_id),
    )
    await session.commit()

    return RefreshResponse(
        job_id=job.id,
        channel_id=channel_id,
        status=job.status,
        created_at=job.created_at,
    )
