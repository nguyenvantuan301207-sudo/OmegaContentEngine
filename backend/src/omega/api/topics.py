"""Topic Intelligence REST API endpoints.

Handles candidate ingestion, batch import, evaluation, recommendations,
selection, rejection, soft-archival, and TopicMemory queries.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application import topic_service
from omega.domain.topic import (
    TopicCandidateBatchImport,
    TopicCandidateCreate,
    TopicCandidateResponse,
    TopicCandidateUpdate,
    TopicEvaluateRequest,
    TopicMemoryResponse,
    TopicRejectRequest,
)

router = APIRouter(prefix="/api/v1/channels/{channel_id}/topics", tags=["Topics"])


@router.post("/candidates", response_model=TopicCandidateResponse, status_code=status.HTTP_201_CREATED)
async def ingest_topic_candidate(
    channel_id: UUID,
    candidate_in: TopicCandidateCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopicCandidateResponse:
    """Ingest a single topic candidate for a channel."""
    try:
        return await topic_service.ingest_candidate(db, channel_id, candidate_in)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/import", response_model=list[TopicCandidateResponse], status_code=status.HTTP_201_CREATED)
async def batch_import_candidates(
    channel_id: UUID,
    batch_in: TopicCandidateBatchImport,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[TopicCandidateResponse]:
    """Batch import multiple topic candidates for a channel."""
    try:
        return await topic_service.batch_ingest_candidates(db, channel_id, batch_in)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/candidates", response_model=list[TopicCandidateResponse])
async def list_channel_candidates(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    source_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TopicCandidateResponse]:
    """List topic candidates with optional filters and pagination."""
    return await topic_service.list_candidates(
        db, channel_id=channel_id, status=status, source_type=source_type, limit=limit, offset=offset
    )


@router.get("/candidates/{candidate_id}", response_model=TopicCandidateResponse)
async def get_candidate_by_id(
    channel_id: UUID,
    candidate_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopicCandidateResponse:
    """Retrieve full details of a topic candidate."""
    cand = await topic_service.get_candidate(db, candidate_id)
    if not cand or cand.channel_id != channel_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
    return cand


@router.patch("/candidates/{candidate_id}", response_model=TopicCandidateResponse)
async def update_candidate_details(
    channel_id: UUID,
    candidate_id: UUID,
    update_in: TopicCandidateUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopicCandidateResponse:
    """Update candidate title, summary, or manual signals."""
    try:
        cand = await topic_service.get_candidate(db, candidate_id)
        if not cand or cand.channel_id != channel_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
        updated = await topic_service.update_candidate(db, candidate_id, update_in)
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
        return updated
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/archive", response_model=TopicCandidateResponse)
async def soft_archive_candidate(
    channel_id: UUID,
    candidate_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopicCandidateResponse:
    """Soft-archive a candidate without deleting historical records."""
    cand = await topic_service.get_candidate(db, candidate_id)
    if not cand or cand.channel_id != channel_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
    archived = await topic_service.archive_candidate(db, candidate_id)
    if not archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
    return archived


@router.post("/candidates/{candidate_id}/evaluate", response_model=TopicCandidateResponse)
async def evaluate_single_candidate(
    channel_id: UUID,
    candidate_id: UUID,
    eval_req: TopicEvaluateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopicCandidateResponse:
    """Evaluate candidate against Channel DNA and Topic Memory."""
    try:
        cand = await topic_service.get_candidate(db, candidate_id)
        if not cand or cand.channel_id != channel_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
        return await topic_service.evaluate_candidate(
            db,
            candidate_id=candidate_id,
            mode=eval_req.mode,
            mission_execution_id=eval_req.mission_execution_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/evaluate-batch", response_model=list[TopicCandidateResponse])
async def evaluate_candidates_batch(
    channel_id: UUID,
    eval_req: TopicEvaluateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[TopicCandidateResponse]:
    """Evaluate all pending DISCOVERED candidates for a channel."""
    try:
        return await topic_service.evaluate_batch(
            db,
            channel_id=channel_id,
            mode=eval_req.mode,
            mission_execution_id=eval_req.mission_execution_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/recommendations", response_model=list[TopicCandidateResponse])
async def get_top_recommendations(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    min_score: Annotated[float, Query(ge=0.0, le=100.0)] = 60.0,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[TopicCandidateResponse]:
    """Retrieve top-ranked recommended topic candidates."""
    return await topic_service.list_recommendations(
        db, channel_id=channel_id, min_score=min_score, limit=limit
    )


@router.post("/candidates/{candidate_id}/select", response_model=TopicCandidateResponse)
async def select_topic_candidate(
    channel_id: UUID,
    candidate_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopicCandidateResponse:
    """Select candidate for production, updating TopicMemory times_selected."""
    try:
        cand = await topic_service.get_candidate(db, candidate_id)
        if not cand or cand.channel_id != channel_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
        return await topic_service.select_candidate(db, candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/reject", response_model=TopicCandidateResponse)
async def reject_topic_candidate(
    channel_id: UUID,
    candidate_id: UUID,
    reject_in: TopicRejectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopicCandidateResponse:
    """Reject candidate, updating TopicMemory times_rejected."""
    try:
        cand = await topic_service.get_candidate(db, candidate_id)
        if not cand or cand.channel_id != channel_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
        return await topic_service.reject_candidate(db, candidate_id, reject_in.reason)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/memory", response_model=list[TopicMemoryResponse])
async def list_channel_topic_memory(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TopicMemoryResponse]:
    """List TopicMemory records for a Channel."""
    return await topic_service.list_topic_memory(
        db, channel_id=channel_id, search=search, limit=limit, offset=offset
    )


@router.get("/memory/{memory_id}", response_model=TopicMemoryResponse)
async def get_topic_memory_record(
    channel_id: UUID,
    memory_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TopicMemoryResponse:
    """Get single TopicMemory record by ID."""
    mem = await topic_service.get_topic_memory(db, memory_id)
    if not mem or mem.channel_id != channel_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory record not found.")
    return mem
