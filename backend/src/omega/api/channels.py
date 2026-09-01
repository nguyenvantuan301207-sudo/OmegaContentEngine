"""Channels REST API Router.

Provides endpoints for Channel CRUD, lifecycle transitions (activate, pause, archive),
Channel DNA management, historical revision tracking, and unified ChannelContext.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application import channel_service
from omega.domain.channel import (
    ChannelCreate,
    ChannelDNARevisionResponse,
    ChannelDNAUpdateRequest,
    ChannelResponse,
    ChannelUpdate,
    InvalidStateTransitionError,
)
from omega.domain.channel_context import ChannelContext
from omega.domain.channel_dna import ChannelDNA

router = APIRouter(prefix="/api/v1/channels", tags=["Channels"])


@router.post("", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_channel(
    channel_in: ChannelCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelResponse:
    """Create a new channel in DRAFT state with initial DNA and Revision 1."""
    try:
        return await channel_service.create_channel(db, channel_in)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/count", response_model=dict[str, int])
async def count_channels(
    db: Annotated[AsyncSession, Depends(get_db)],
    state: str | None = None,
    platform: str | None = None,
    search: str | None = None,
) -> dict[str, int]:
    """Count channels matching optional state, platform, and search query filters."""
    total = await channel_service.count_channels(
        db, state=state, platform=platform, search=search
    )
    return {"total": total}


@router.get("", response_model=list[ChannelResponse])
async def list_channels(
    db: Annotated[AsyncSession, Depends(get_db)],
    state: str | None = None,
    platform: str | None = None,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ChannelResponse]:
    """List channels with pagination, search, and optional filters."""
    return await channel_service.list_channels(
        db, state=state, platform=platform, search=search, limit=limit, offset=offset
    )


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelResponse:
    """Retrieve details for a single channel by ID."""
    channel = await channel_service.get_channel(db, channel_id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{channel_id}' not found",
        )
    return channel


@router.patch("/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    channel_id: UUID,
    channel_in: ChannelUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelResponse:
    """Update mutable identity attributes of a channel."""
    try:
        updated = await channel_service.update_channel(db, channel_id, channel_in)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel '{channel_id}' not found",
            )
        return updated
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{channel_id}/activate", response_model=ChannelResponse)
async def activate_channel(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelResponse:
    """Activate a channel (DRAFT/PAUSED -> ACTIVE)."""
    try:
        activated = await channel_service.activate_channel(db, channel_id)
        if not activated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel '{channel_id}' not found",
            )
        return activated
    except (InvalidStateTransitionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{channel_id}/pause", response_model=ChannelResponse)
async def pause_channel(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelResponse:
    """Pause an active channel (ACTIVE -> PAUSED)."""
    try:
        paused = await channel_service.pause_channel(db, channel_id)
        if not paused:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel '{channel_id}' not found",
            )
        return paused
    except (InvalidStateTransitionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{channel_id}/archive", response_model=ChannelResponse)
async def archive_channel(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelResponse:
    """Archive a channel (DRAFT/ACTIVE/PAUSED -> ARCHIVED)."""
    try:
        archived = await channel_service.archive_channel(db, channel_id)
        if not archived:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel '{channel_id}' not found",
            )
        return archived
    except (InvalidStateTransitionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{channel_id}/dna", response_model=ChannelDNA)
async def get_channel_dna(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelDNA:
    """Retrieve active validated Channel DNA."""
    dna = await channel_service.get_channel_dna(db, channel_id)
    if not dna:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{channel_id}' not found",
        )
    return dna


@router.patch("/{channel_id}/dna", response_model=ChannelDNA)
async def update_channel_dna(
    channel_id: UUID,
    dna_in: ChannelDNAUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelDNA:
    """Update Channel DNA, creating a new revision."""
    try:
        updated_dna = await channel_service.update_channel_dna(
            db,
            channel_id=channel_id,
            new_dna=dna_in.dna,
            change_reason=dna_in.change_reason,
            actor=dna_in.actor,
        )
        if not updated_dna:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel '{channel_id}' not found",
            )
        return updated_dna
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{channel_id}/dna/revisions", response_model=list[ChannelDNARevisionResponse])
async def list_dna_revisions(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ChannelDNARevisionResponse]:
    """List historical DNA revisions for a channel."""
    # Ensure channel exists
    channel = await channel_service.get_channel(db, channel_id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{channel_id}' not found",
        )
    return await channel_service.list_dna_revisions(db, channel_id)


@router.get("/{channel_id}/context", response_model=ChannelContext)
async def get_channel_context(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChannelContext:
    """Retrieve consolidated ChannelContext for downstream consumers."""
    context = await channel_service.get_channel_context(db, channel_id)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{channel_id}' not found",
        )
    return context
