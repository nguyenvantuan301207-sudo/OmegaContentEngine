"""Content Campaign API endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application import (
    campaign_admission_service,
    content_campaign_execution_service,
    content_campaign_service,
)
from omega.domain.content_campaign import (
    CampaignRuntimeAction,
    CampaignSummaryResponse,
    ContentCampaignCreate,
    ContentCampaignItemResponse,
    ContentCampaignResponse,
)
from omega.domain.content_campaign_execution import (
    ContentCampaignExecutionCreate,
    ContentCampaignExecutionResponse,
)

router = APIRouter(prefix="/api/v1/channels/{channel_id}/campaigns", tags=["Campaigns"])


async def _runtime_response(db: AsyncSession, channel_id: UUID, campaign_id: UUID) -> ContentCampaignResponse:
    campaign = await content_campaign_service.get_campaign(db, channel_id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found for channel.")
    return campaign


def _runtime_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, campaign_admission_service.CampaignFeatureDisabledError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "",
    response_model=ContentCampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_content_campaign(
    channel_id: UUID,
    request: ContentCampaignCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ContentCampaignResponse:
    """Create and persist an immutable bounded content campaign plan."""
    try:
        return await content_campaign_service.create_campaign(db, channel_id, request)
    except content_campaign_service.ContentCampaignNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except content_campaign_service.ContentCampaignConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except (content_campaign_service.ContentCampaignValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except content_campaign_service.ContentCampaignIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Campaign lineage integrity violation: {exc}",
        ) from exc


@router.get("", response_model=list[ContentCampaignResponse])
async def list_content_campaigns(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ContentCampaignResponse]:
    """List content campaigns for a channel, ordered by created_at DESC, id DESC."""
    try:
        return await content_campaign_service.list_campaigns(
            db, channel_id, limit=limit, offset=offset
        )
    except content_campaign_service.ContentCampaignIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Campaign lineage integrity violation: {exc}",
        ) from exc


@router.get("/{campaign_id}", response_model=ContentCampaignResponse)
async def get_content_campaign(
    channel_id: UUID,
    campaign_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ContentCampaignResponse:
    """Retrieve details for a specific campaign, verifying channel ownership."""
    try:
        campaign = await content_campaign_service.get_campaign(db, channel_id, campaign_id)
        if campaign is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Campaign '{campaign_id}' not found for channel '{channel_id}'.",
            )
        return campaign
    except HTTPException:
        raise
    except content_campaign_service.ContentCampaignIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Campaign lineage integrity violation: {exc}",
        ) from exc


@router.post(
    "/{campaign_id}/materialize",
    response_model=ContentCampaignExecutionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def materialize_campaign(
    channel_id: UUID,
    campaign_id: UUID,
    request: ContentCampaignExecutionCreate,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ContentCampaignExecutionResponse:
    """Materialize an immutable content campaign into bounded OMEGA Missions."""
    try:
        exec_resp, created = await content_campaign_execution_service.materialize_campaign(
            db, channel_id=channel_id, campaign_id=campaign_id, payload=request
        )
        if not created:
            response.status_code = status.HTTP_200_OK
        return exec_resp
    except content_campaign_execution_service.ContentCampaignExecutionModeConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (
        content_campaign_execution_service.ContentCampaignNotFoundError,
        content_campaign_service.ContentCampaignNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except content_campaign_execution_service.ContentCampaignExecutionIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Campaign execution lineage integrity violation: {exc}",
        ) from exc
    except (
        content_campaign_execution_service.ContentCampaignExecutionValidationError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
@router.get(
    "/{campaign_id}/execution",
    response_model=ContentCampaignExecutionResponse,
)
async def get_campaign_execution(
    channel_id: UUID,
    campaign_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ContentCampaignExecutionResponse:
    """Retrieve and validate historical campaign execution lineage."""
    try:
        return await content_campaign_execution_service.get_campaign_execution(
            db, channel_id=channel_id, campaign_id=campaign_id
        )
    except (
        content_campaign_execution_service.ContentCampaignNotFoundError,
        content_campaign_service.ContentCampaignNotFoundError,
        content_campaign_execution_service.ContentCampaignExecutionNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except content_campaign_execution_service.ContentCampaignExecutionIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Campaign execution lineage integrity violation: {exc}",
        ) from exc


@router.post("/{campaign_id}/start", response_model=ContentCampaignResponse)
async def start_campaign(channel_id: UUID, campaign_id: UUID, request: CampaignRuntimeAction, db: Annotated[AsyncSession, Depends(get_db)]) -> ContentCampaignResponse:
    try:
        await campaign_admission_service.start_campaign(db, channel_id, campaign_id, request.actor)
        return await _runtime_response(db, channel_id, campaign_id)
    except campaign_admission_service.CampaignRuntimeError as exc:
        raise _runtime_http_error(exc) from exc


@router.post("/{campaign_id}/pause", response_model=ContentCampaignResponse)
async def pause_campaign(channel_id: UUID, campaign_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> ContentCampaignResponse:
    try:
        await campaign_admission_service.pause_campaign(db, channel_id, campaign_id)
        return await _runtime_response(db, channel_id, campaign_id)
    except campaign_admission_service.CampaignRuntimeError as exc:
        raise _runtime_http_error(exc) from exc


@router.post("/{campaign_id}/resume", response_model=ContentCampaignResponse)
async def resume_campaign(channel_id: UUID, campaign_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> ContentCampaignResponse:
    try:
        await campaign_admission_service.resume_campaign(db, channel_id, campaign_id)
        return await _runtime_response(db, channel_id, campaign_id)
    except campaign_admission_service.CampaignRuntimeError as exc:
        raise _runtime_http_error(exc) from exc


@router.post("/{campaign_id}/cancel", response_model=ContentCampaignResponse)
async def cancel_campaign(channel_id: UUID, campaign_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> ContentCampaignResponse:
    try:
        await campaign_admission_service.cancel_campaign(db, channel_id, campaign_id)
        return await _runtime_response(db, channel_id, campaign_id)
    except campaign_admission_service.CampaignRuntimeError as exc:
        raise _runtime_http_error(exc) from exc


@router.post("/{campaign_id}/archive", response_model=ContentCampaignResponse)
async def archive_campaign(channel_id: UUID, campaign_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> ContentCampaignResponse:
    try:
        await campaign_admission_service.archive_campaign(db, channel_id, campaign_id)
        return await _runtime_response(db, channel_id, campaign_id)
    except campaign_admission_service.CampaignRuntimeError as exc:
        raise _runtime_http_error(exc) from exc


@router.get("/{campaign_id}/summary", response_model=CampaignSummaryResponse)
async def get_campaign_summary(channel_id: UUID, campaign_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> CampaignSummaryResponse:
    if await content_campaign_service.get_campaign(db, channel_id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found for channel.")
    return await campaign_admission_service.campaign_summary(db, campaign_id)


@router.get("/{campaign_id}/items", response_model=list[ContentCampaignItemResponse])
async def list_campaign_items(channel_id: UUID, campaign_id: UUID, db: Annotated[AsyncSession, Depends(get_db)], limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0) -> list[ContentCampaignItemResponse]:
    campaign = await content_campaign_service.get_campaign(db, channel_id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found for channel.")
    return campaign.items[offset:offset + limit]
