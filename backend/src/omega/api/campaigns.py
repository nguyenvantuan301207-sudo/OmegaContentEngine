"""Content Campaign API endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application import (
    content_campaign_execution_service,
    content_campaign_service,
)
from omega.domain.content_campaign import (
    ContentCampaignCreate,
    ContentCampaignResponse,
)
from omega.domain.content_campaign_execution import (
    ContentCampaignExecutionCreate,
    ContentCampaignExecutionResponse,
)

router = APIRouter(prefix="/api/v1/channels/{channel_id}/campaigns", tags=["Campaigns"])


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
    except (
        content_campaign_execution_service.ContentCampaignNotFoundError,
        content_campaign_service.ContentCampaignNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (
        content_campaign_execution_service.ContentCampaignExecutionValidationError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except content_campaign_execution_service.ContentCampaignExecutionIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Campaign execution lineage integrity violation: {exc}",
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
