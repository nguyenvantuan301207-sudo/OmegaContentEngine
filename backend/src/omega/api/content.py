"""Content Engine REST API endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application import content_service
from omega.domain.content import (
    ContentGenerationRequestCreate,
    ContentGenerationRequestResponse,
    ContentHookResponse,
    ContentIntentResponse,
    ContentOutlineResponse,
    ContentQAResultResponse,
    ContentRunPayload,
    HookSelectPayload,
    ScriptVersionResponse,
    ScriptVersionSummaryResponse,
)
from omega.infrastructure.database import get_async_session

router = APIRouter(prefix="/api/v1/channels/{channel_id}/content", tags=["Content Engine"])


@router.post(
    "", response_model=ContentGenerationRequestResponse, status_code=status.HTTP_201_CREATED
)
async def create_content_request(
    channel_id: UUID,
    payload: ContentGenerationRequestCreate,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> ContentGenerationRequestResponse:
    """Create a new ContentGenerationRequest pinned to an immutable ResearchBrief and ChannelDNARevision."""
    try:
        return await content_service.create_request(db, channel_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("", response_model=list[ContentGenerationRequestResponse])
async def list_content_requests(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[ContentGenerationRequestResponse]:
    """List all content generation requests for a channel."""
    return await content_service.list_requests(db, channel_id, status_filter)


@router.get("/{request_id}", response_model=ContentGenerationRequestResponse)
async def get_content_request(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> ContentGenerationRequestResponse:
    """Get a content generation request by ID."""
    req = await content_service.get_request(db, channel_id, request_id)
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content request not found."
        )
    return req


@router.post("/{request_id}/cancel", response_model=ContentGenerationRequestResponse)
async def cancel_content_request(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> ContentGenerationRequestResponse:
    """Cancel an active or draft content generation request."""
    try:
        return await content_service.cancel_request(db, channel_id, request_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/{request_id}/generate", response_model=ScriptVersionResponse)
async def generate_content(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    payload: ContentRunPayload | None = None,
) -> ScriptVersionResponse:
    """Execute content generation pipeline: Intent -> Hooks -> Outline -> Script v1 -> QA."""
    try:
        return await content_service.generate_content(db, channel_id, request_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/{request_id}/regenerate", response_model=ScriptVersionResponse)
async def regenerate_script(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    payload: ContentRunPayload | None = None,
) -> ScriptVersionResponse:
    """Regenerate a new immutable script revision (vN+1) using pinned DNA and ResearchBrief."""
    try:
        return await content_service.regenerate_script(db, channel_id, request_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/{request_id}/intent", response_model=ContentIntentResponse)
async def get_content_intent(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> ContentIntentResponse:
    """Get editorial ContentIntent for a request."""
    intent = await content_service.get_intent(db, channel_id, request_id)
    if not intent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content intent not found."
        )
    return intent


@router.get("/{request_id}/hooks", response_model=list[ContentHookResponse])
async def list_content_hooks(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[ContentHookResponse]:
    """List hook variants generated for a request."""
    return await content_service.list_hooks(db, channel_id, request_id)


@router.post("/{request_id}/hooks/{hook_id}/select", response_model=ContentHookResponse)
async def select_content_hook(
    channel_id: UUID,
    request_id: UUID,
    hook_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    payload: HookSelectPayload | None = None,
) -> ContentHookResponse:
    """Select a hook variant (enforcing single-selected-hook invariant)."""
    try:
        is_sel = payload.selected if payload else True
        return await content_service.select_hook(db, channel_id, request_id, hook_id, is_sel)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/{request_id}/outline", response_model=ContentOutlineResponse)
async def get_content_outline(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> ContentOutlineResponse:
    """Get structured ContentOutline for a request."""
    outline = await content_service.get_outline(db, channel_id, request_id)
    if not outline:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content outline not found."
        )
    return outline


@router.get("/{request_id}/scripts", response_model=list[ScriptVersionSummaryResponse])
async def list_script_versions(
    channel_id: UUID,
    request_id: UUID,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[ScriptVersionSummaryResponse]:
    """List summaries of all script revisions for a request."""
    return await content_service.list_scripts(db, channel_id, request_id)


@router.get("/{request_id}/scripts/{version}", response_model=ScriptVersionResponse)
async def get_script_version(
    channel_id: UUID,
    request_id: UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> ScriptVersionResponse:
    """Get a specific script revision with sections, statements, and citations."""
    script = await content_service.get_script(db, channel_id, request_id, version)
    if not script:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Script version not found."
        )
    return script


@router.get("/{request_id}/scripts/{version}/qa", response_model=ContentQAResultResponse)
async def get_script_qa_result(
    channel_id: UUID,
    request_id: UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> ContentQAResultResponse:
    """Get QA evaluation result for a script version."""
    qa = await content_service.get_qa_result(db, channel_id, request_id, version)
    if not qa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QA result not found.")
    return qa


@router.post("/{request_id}/scripts/{version}/qa", response_model=ContentQAResultResponse)
async def rerun_script_qa(
    channel_id: UUID,
    request_id: UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> ContentQAResultResponse:
    """Re-run local QA checks on a script version."""
    try:
        return await content_service.run_qa(db, channel_id, request_id, version)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
