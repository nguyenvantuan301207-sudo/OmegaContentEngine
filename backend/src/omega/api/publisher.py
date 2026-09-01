"""Publisher REST API Router for OMEGA-011.

Provides secure endpoints for OAuth account connection, intent management,
attempt diagnostics, upload progression, and execution triggering.
All secrets (tokens, verifiers, full session URIs) are strictly redacted.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.oauth_service import OAuthService
from omega.application.publisher.publish_service import PublishExecutionService
from omega.domain.publisher import (
    Platform,
    PlatformAccountResponse,
    PublishAttemptResponse,
    PublishIntentCreate,
    PublishIntentResponse,
    UploadProgressResponse,
)
from omega.infrastructure.models import (
    PlatformAccount,
    PublishAttempt,
    PublishIntent,
    UploadSession,
)
from omega.logging import get_logger

logger = get_logger(service="omega-api-publisher")

router = APIRouter(prefix="/api/v1/publisher", tags=["publisher"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


class OAuthAuthorizeRequest(BaseModel):
    """Request payload to generate OAuth authorization redirect URL."""

    channel_id: UUID
    platform: Platform = Platform.YOUTUBE


class OAuthAuthorizeResponse(BaseModel):
    """Response containing the OAuth authorization redirect URL."""

    authorization_url: str
    platform: Platform


class DisconnectRequest(BaseModel):
    """Confirmation payload to revoke a connected platform account."""

    confirm_disconnect: bool = Field(..., description="Must be True to confirm revocation")


class ExecutePublishRequest(BaseModel):
    """Request to trigger immediate execution of a task with an approved intent."""

    task_id: UUID


# ── Account & OAuth Endpoints ──


@router.get("/accounts", response_model=list[PlatformAccountResponse])
async def list_platform_accounts(
    session: DBSession,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
    platform: Annotated[Platform | None, Query(description="Filter by platform")] = None,
) -> list[PlatformAccount]:
    """List connected platform accounts with tokens redacted."""
    stmt = select(PlatformAccount)
    if channel_id:
        stmt = stmt.where(PlatformAccount.channel_id == channel_id)
    if platform:
        stmt = stmt.where(PlatformAccount.platform == platform.value)

    res = await session.execute(stmt)
    return list(res.scalars().all())


@router.post("/accounts/youtube/authorize-url", response_model=OAuthAuthorizeResponse)
async def create_youtube_authorize_url(
    payload: OAuthAuthorizeRequest,
    session: DBSession,
) -> OAuthAuthorizeResponse:
    """Generate secure Google OAuth 2.0 authorization URL with PKCE challenge."""
    try:
        url = await OAuthService.create_authorization_url(
            session=session,
            channel_id=payload.channel_id,
            platform=payload.platform,
        )
        return OAuthAuthorizeResponse(authorization_url=url, platform=payload.platform)
    except Exception as exc:
        logger.error("Failed to create OAuth authorization URL", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/accounts/youtube/callback", response_model=PlatformAccountResponse)
async def handle_youtube_oauth_callback(
    code: Annotated[str, Query(...)],
    state: Annotated[str, Query(...)],
    session: DBSession,
) -> PlatformAccount:
    """Handle OAuth redirect callback from Google, consume state hash, and exchange tokens."""
    try:
        account = await OAuthService.handle_oauth_callback(
            session=session,
            state=state,
            code=code,
        )
        return account
    except Exception as exc:
        logger.error("OAuth callback processing failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/accounts/{account_id}/disconnect", response_model=PlatformAccountResponse)
async def disconnect_platform_account(
    account_id: UUID,
    payload: DisconnectRequest,
    session: DBSession,
) -> PlatformAccount:
    """Disconnect and revoke an active platform account."""
    try:
        account = await OAuthService.disconnect_account(
            session=session,
            account_id=account_id,
            confirm_disconnect=payload.confirm_disconnect,
        )
        return account
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# ── Publish Intent Endpoints ──


@router.post("/intents", response_model=PublishIntentResponse, status_code=status.HTTP_201_CREATED)
async def create_publish_intent(
    payload: PublishIntentCreate,
    session: DBSession,
) -> PublishIntent:
    """Create or supersede an approved PublishIntent snapshot before scheduling."""
    try:
        intent = await PublishIntentService.create_publish_intent(
            session=session,
            payload=payload,
            actor="API_USER",
        )
        return intent
    except Exception as exc:
        logger.error("Failed to create publish intent", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/intents", response_model=list[PublishIntentResponse])
async def list_publish_intents(
    session: DBSession,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
    mission_id: Annotated[UUID | None, Query(description="Filter by mission ID")] = None,
    state: Annotated[str | None, Query(description="Filter by intent state")] = None,
) -> list[PublishIntent]:
    """List publication intents matching criteria."""
    stmt = select(PublishIntent).order_by(PublishIntent.created_at.desc())
    if channel_id:
        stmt = stmt.where(PublishIntent.channel_id == channel_id)
    if mission_id:
        stmt = stmt.where(PublishIntent.mission_id == mission_id)
    if state:
        stmt = stmt.where(PublishIntent.state == state.upper())

    res = await session.execute(stmt)
    return list(res.scalars().all())


@router.get("/intents/{intent_id}", response_model=PublishIntentResponse)
async def get_publish_intent(
    intent_id: UUID,
    session: DBSession,
) -> PublishIntent:
    """Retrieve detailed PublishIntent metadata."""
    stmt = select(PublishIntent).where(PublishIntent.id == intent_id)
    res = await session.execute(stmt)
    intent = res.scalar_one_or_none()
    if not intent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PublishIntent {intent_id} not found.",
        )
    return intent


@router.post("/intents/{intent_id}/approve", response_model=PublishIntentResponse)
async def approve_publish_intent(
    intent_id: UUID,
    session: DBSession,
) -> PublishIntent:
    """Approve a DRAFT PublishIntent for scheduling and execution."""
    try:
        intent = await PublishIntentService.approve_intent(
            session=session,
            intent_id=intent_id,
            actor="API_USER",
        )
        return intent
    except Exception as exc:
        logger.error("Failed to approve publish intent", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# ── Publish Attempt & Progression Endpoints ──


@router.get("/attempts/{attempt_id}", response_model=PublishAttemptResponse)
async def get_publish_attempt(
    attempt_id: UUID,
    session: DBSession,
) -> PublishAttempt:
    """Retrieve detailed PublishAttempt diagnostics."""
    stmt = select(PublishAttempt).where(PublishAttempt.id == attempt_id)
    res = await session.execute(stmt)
    attempt = res.scalar_one_or_none()
    if not attempt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PublishAttempt {attempt_id} not found.",
        )
    return attempt


@router.get("/attempts/{attempt_id}/progress", response_model=UploadProgressResponse)
async def get_upload_progress(
    attempt_id: UUID,
    session: DBSession,
) -> UploadProgressResponse:
    """Retrieve safe chunk upload progression without leaking session URIs."""
    stmt = select(UploadSession).where(UploadSession.publish_attempt_id == attempt_id)
    res = await session.execute(stmt)
    sess_row = res.scalar_one_or_none()
    if not sess_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"UploadSession for attempt {attempt_id} not found.",
        )

    pct = (
        round((sess_row.bytes_uploaded / sess_row.total_bytes) * 100.0, 2)
        if sess_row.total_bytes > 0
        else 0.0
    )
    return UploadProgressResponse(
        publish_attempt_id=attempt_id,
        total_bytes=sess_row.total_bytes,
        bytes_uploaded=sess_row.bytes_uploaded,
        progress_percentage=pct,
        is_complete=sess_row.bytes_uploaded >= sess_row.total_bytes,
        expires_at=sess_row.expires_at,
    )


@router.post("/execute", response_model=PublishAttemptResponse)
async def execute_publish(
    payload: ExecutePublishRequest,
    session: DBSession,
) -> PublishAttempt:
    """Execute publication for an approved intent."""
    try:
        attempt = await PublishExecutionService.execute_publish(
            session=session,
            task_id=payload.task_id,
            worker_id="api_direct_worker",
        )
        return attempt
    except Exception as exc:
        logger.error("Publish execution error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
