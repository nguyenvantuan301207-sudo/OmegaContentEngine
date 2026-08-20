"""Research Engine REST API router.

Exposes endpoints for research requests, source ingestion, claim and first-class
evidence management, conflict tracking, pipeline runs, and versioned immutable briefs.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application import research_service
from omega.domain.research import (
    ClaimEvidenceCreate,
    ClaimEvidenceResponse,
    ResearchBriefResponse,
    ResearchBriefSummaryResponse,
    ResearchClaimCreate,
    ResearchClaimResponse,
    ResearchConflictResponse,
    ResearchRequestCreate,
    ResearchRequestResponse,
    ResearchRunPayload,
    ResearchSourceBatchCreate,
    ResearchSourceCreate,
    ResearchSourceResponse,
)

router = APIRouter(prefix="/api/v1/channels/{channel_id}/research", tags=["Research Engine"])


@router.post(
    "",
    response_model=ResearchRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new research request",
)
async def create_research_request(
    channel_id: UUID,
    request_in: ResearchRequestCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResearchRequestResponse:
    try:
        return await research_service.create_research_request(db, channel_id, request_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get(
    "",
    response_model=list[ResearchRequestResponse],
    summary="List research requests for a channel",
)
async def list_research_requests(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    topic_candidate_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResearchRequestResponse]:
    return await research_service.list_research_requests(
        session=db,
        channel_id=channel_id,
        status=status_filter,
        topic_candidate_id=topic_candidate_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{request_id}",
    response_model=ResearchRequestResponse,
    summary="Retrieve a research request by ID",
)
async def get_research_request(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResearchRequestResponse:
    req = await research_service.get_research_request(db, request_id)
    if not req or req.channel_id != channel_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ResearchRequest '{request_id}' not found for channel '{channel_id}'.",
        )
    return req


@router.post(
    "/{request_id}/sources",
    response_model=ResearchSourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a normalized research source",
)
async def add_source(
    channel_id: UUID,
    request_id: UUID,
    source_in: ResearchSourceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResearchSourceResponse:
    try:
        return await research_service.add_source(db, request_id, source_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post(
    "/{request_id}/sources/batch",
    response_model=list[ResearchSourceResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Batch ingest research sources",
)
async def batch_add_sources(
    channel_id: UUID,
    request_id: UUID,
    batch_in: ResearchSourceBatchCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ResearchSourceResponse]:
    try:
        return await research_service.batch_add_sources(db, request_id, batch_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get(
    "/{request_id}/sources",
    response_model=list[ResearchSourceResponse],
    summary="List normalized sources for a research request",
)
async def list_sources(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResearchSourceResponse]:
    return await research_service.list_sources(db, request_id, limit=limit, offset=offset)


@router.post(
    "/{request_id}/claims",
    response_model=ResearchClaimResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an extracted research claim",
)
async def add_claim(
    channel_id: UUID,
    request_id: UUID,
    claim_in: ResearchClaimCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResearchClaimResponse:
    try:
        return await research_service.add_claim(db, request_id, claim_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get(
    "/{request_id}/claims",
    response_model=list[ResearchClaimResponse],
    summary="List claims for a research request",
)
async def list_claims(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    is_verified: Annotated[bool | None, Query()] = None,
    claim_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResearchClaimResponse]:
    return await research_service.list_claims(
        session=db,
        request_id=request_id,
        is_verified=is_verified,
        claim_type=claim_type,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{request_id}/claims/{claim_id}/evidence",
    response_model=ClaimEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add first-class evidence linked to a claim and source",
)
async def add_evidence(
    channel_id: UUID,
    request_id: UUID,
    claim_id: UUID,
    evidence_in: ClaimEvidenceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClaimEvidenceResponse:
    try:
        return await research_service.add_evidence(db, request_id, claim_id, evidence_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get(
    "/{request_id}/claims/{claim_id}/evidence",
    response_model=list[ClaimEvidenceResponse],
    summary="List all evidence items for a claim",
)
async def list_evidence(
    channel_id: UUID,
    request_id: UUID,
    claim_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ClaimEvidenceResponse]:
    return await research_service.list_evidence(db, claim_id)


@router.get(
    "/{request_id}/conflicts",
    response_model=list[ResearchConflictResponse],
    summary="List detected contradictions for a research request",
)
async def list_conflicts(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[ResearchConflictResponse]:
    return await research_service.list_conflicts(db, request_id, status=status_filter)


@router.post(
    "/{request_id}/run",
    response_model=ResearchBriefResponse,
    summary="Execute research pipeline and produce immutable ResearchBrief",
)
async def run_research(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: ResearchRunPayload | None = None,
) -> ResearchBriefResponse:
    try:
        return await research_service.run_research(db, request_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get(
    "/{request_id}/brief",
    response_model=ResearchBriefResponse,
    summary="Retrieve current or specific version of ResearchBrief",
)
async def get_brief(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ResearchBriefResponse:
    brief = await research_service.get_brief(db, request_id, version=version)
    if not brief or brief.channel_id != channel_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ResearchBrief for request '{request_id}' not found.",
        )
    return brief


@router.get(
    "/{request_id}/briefs",
    response_model=list[ResearchBriefSummaryResponse],
    summary="List all historical brief revisions for a research request",
)
async def list_briefs(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ResearchBriefSummaryResponse]:
    return await research_service.list_briefs(db, request_id)


@router.post(
    "/{request_id}/cancel",
    response_model=ResearchRequestResponse,
    summary="Cancel an in-flight or pending research request",
)
async def cancel_research_request(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResearchRequestResponse:
    try:
        return await research_service.cancel_research_request(db, request_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
