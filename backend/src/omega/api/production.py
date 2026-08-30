"""FastAPI router for OMEGA-007 Production Engine endpoints with HTTP Range media streaming."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.media_storage import LocalMediaStorageProvider, StorageSecurityError
from omega.application.production_service import (
    ProductionLineageError,
    ProductionService,
    ProductionStateError,
)
from omega.application.render_service import ProductionRenderService
from omega.domain.production import (
    MediaArtifactResponse,
    NarrationSegmentResponse,
    ProductionAssetResponse,
    ProductionQAResultResponse,
    ProductionRenderJobResponse,
    ProductionRenderPayload,
    ProductionRequestCreate,
    ProductionRequestResponse,
    ProductionRequestStatus,
    ProductionRerenderPayload,
    ProductionSceneResponse,
    RenderPlanResponse,
    SubtitleCueResponse,
)
from omega.infrastructure.database import get_async_session
from omega.infrastructure.models import (
    MediaArtifact,
    NarrationSegment,
    ProductionAsset,
    ProductionQAResult,
    ProductionRenderJob,
    ProductionRequest,
    ProductionScene,
    RenderPlan,
    SubtitleCue,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/channels/{channel_id}/production", tags=["Production Engine"])


def _get_production_service() -> ProductionService:
    return ProductionService()


def _get_render_service() -> ProductionRenderService:
    return ProductionRenderService()


def _get_storage_provider() -> LocalMediaStorageProvider:
    return LocalMediaStorageProvider()


# ── 1. Create Production Request ──
@router.post(
    "",
    response_model=ProductionRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new Production Request pinned to a ScriptVersion",
)
async def create_production_request(
    channel_id: UUID,
    payload: ProductionRequestCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    service: Annotated[ProductionService, Depends(_get_production_service)],
) -> ProductionRequest:
    try:
        return await service.create_production_request(session, channel_id, payload)
    except ProductionLineageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── 2. List Production Requests ──
@router.get(
    "",
    response_model=list[ProductionRequestResponse],
    summary="List Production Requests for a channel",
)
async def list_production_requests(
    channel_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[ProductionRequest]:
    stmt = (
        select(ProductionRequest)
        .where(ProductionRequest.channel_id == channel_id)
        .order_by(ProductionRequest.created_at.desc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ── 3. Get Production Request ──
@router.get(
    "/{request_id}",
    response_model=ProductionRequestResponse,
    summary="Get Production Request details",
)
async def get_production_request(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ProductionRequest:
    stmt = select(ProductionRequest).where(
        ProductionRequest.id == request_id,
        ProductionRequest.channel_id == channel_id,
    )
    res = await session.execute(stmt)
    req = res.scalar_one_or_none()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProductionRequest {request_id} not found.",
        )
    return req


# ── 4. Prepare Production ──
@router.post(
    "/{request_id}/prepare",
    response_model=ProductionRequestResponse,
    summary="Run Scene Planner, placeholder visual assets, narration, subtitles, and RenderPlan v1",
)
async def prepare_production(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    service: Annotated[ProductionService, Depends(_get_production_service)],
) -> ProductionRequest:
    try:
        return await service.prepare_production(session, channel_id, request_id)
    except ProductionLineageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── 5. List Scenes ──
@router.get(
    "/{request_id}/scenes",
    response_model=list[ProductionSceneResponse],
    summary="Get planned production scenes",
)
async def get_scenes(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[ProductionScene]:
    stmt = (
        select(ProductionScene)
        .where(ProductionScene.production_request_id == request_id)
        .order_by(ProductionScene.scene_order.asc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ── 6. List Assets & Requirements ──
@router.get(
    "/{request_id}/assets",
    response_model=list[ProductionAssetResponse],
    summary="Get resolved visual and audio production assets",
)
async def get_assets(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[ProductionAsset]:
    stmt = (
        select(ProductionAsset)
        .where(
            ProductionAsset.channel_id == channel_id,
            ProductionAsset.production_request_id == request_id,
        )
        .order_by(ProductionAsset.created_at.asc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ── 7. List Narration Segments ──
@router.get(
    "/{request_id}/narration",
    response_model=list[NarrationSegmentResponse],
    summary="Get narration audio segments and timestamps",
)
async def get_narration(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[NarrationSegment]:
    stmt = (
        select(NarrationSegment)
        .where(NarrationSegment.production_request_id == request_id)
        .order_by(NarrationSegment.start_ms.asc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ── 8. List Subtitle Cues ──
@router.get(
    "/{request_id}/subtitles",
    response_model=list[SubtitleCueResponse],
    summary="Get subtitle cues",
)
async def get_subtitles(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[SubtitleCue]:
    stmt = (
        select(SubtitleCue)
        .where(SubtitleCue.production_request_id == request_id)
        .order_by(SubtitleCue.cue_order.asc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ── 9. Download SRT File ──
@router.get(
    "/{request_id}/subtitles/srt",
    response_class=PlainTextResponse,
    summary="Download SRT subtitle file content",
)
async def get_srt_file(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    storage: Annotated[LocalMediaStorageProvider, Depends(_get_storage_provider)],
) -> PlainTextResponse:
    stmt = select(ProductionAsset).where(
        ProductionAsset.channel_id == channel_id,
        ProductionAsset.production_request_id == request_id,
        ProductionAsset.mime_type == "application/x-subrip",
    )
    res = await session.execute(stmt)
    asset = res.scalars().first()
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SRT subtitle file not found. Call /prepare first.",
        )
    file_path = storage.resolve_stored_uri(channel_id, request_id, asset.storage_uri)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="SRT file missing from disk."
        )
    return PlainTextResponse(file_path.read_text(encoding="utf-8"), media_type="text/plain")


# ── 10. Get Render Plan ──
@router.get(
    "/{request_id}/render-plan",
    response_model=RenderPlanResponse,
    summary="Get the latest RenderPlan manifest",
)
async def get_render_plan(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> RenderPlan:
    stmt = (
        select(RenderPlan)
        .where(RenderPlan.production_request_id == request_id)
        .order_by(RenderPlan.version.desc())
    )
    res = await session.execute(stmt)
    plan = res.scalars().first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RenderPlan not found. Call /prepare first.",
        )
    return plan


# ── 11. Dispatch Render Job (Idempotent) ──
@router.post(
    "/{request_id}/render",
    response_model=ProductionRenderJobResponse,
    summary="Dispatch render execution (idempotent with idempotency_key)",
)
async def render_production(
    channel_id: UUID,
    request_id: UUID,
    payload: ProductionRenderPayload,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    prod_service: Annotated[ProductionService, Depends(_get_production_service)],
    render_service: Annotated[ProductionRenderService, Depends(_get_render_service)],
) -> ProductionRenderJob:
    try:
        job, _, is_new = await prod_service.allocate_render_job(
            session=session,
            channel_id=channel_id,
            request_id=request_id,
            idempotency_key=payload.idempotency_key,
            is_rerender=False,
        )
    except (ProductionLineageError, ProductionStateError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if is_new:
        # Execute render synchronously in local environment (or dispatch Celery task)
        try:
            await render_service.execute_render_job(
                session=session,
                channel_id=channel_id,
                request_id=request_id,
                job_id=job.id,
            )
            await session.refresh(job)
        except Exception as exc:
            logger.error("Render failed synchronously", error=str(exc), exc_info=True)
            await session.refresh(job)

    return job


# ── 12. Explicit Rerender ($vN \to vN+1$) ──
@router.post(
    "/{request_id}/rerender",
    response_model=ProductionRenderJobResponse,
    summary="Explicitly rerender generating next media artifact version ($vN+1$)",
)
async def rerender_production(
    channel_id: UUID,
    request_id: UUID,
    payload: ProductionRerenderPayload,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    prod_service: Annotated[ProductionService, Depends(_get_production_service)],
    render_service: Annotated[ProductionRenderService, Depends(_get_render_service)],
) -> ProductionRenderJob:
    try:
        job, _, is_new = await prod_service.allocate_render_job(
            session=session,
            channel_id=channel_id,
            request_id=request_id,
            idempotency_key=payload.idempotency_key,
            is_rerender=True,
        )
    except (ProductionLineageError, ProductionStateError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if is_new:
        try:
            await render_service.execute_render_job(
                session=session,
                channel_id=channel_id,
                request_id=request_id,
                job_id=job.id,
            )
            await session.refresh(job)
        except Exception:
            await session.refresh(job)

    return job


# ── 13. List Render Jobs ──
@router.get(
    "/{request_id}/render-jobs",
    response_model=list[ProductionRenderJobResponse],
    summary="List render execution jobs",
)
async def get_render_jobs(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[ProductionRenderJob]:
    stmt = (
        select(ProductionRenderJob)
        .where(ProductionRenderJob.production_request_id == request_id)
        .order_by(ProductionRenderJob.created_at.desc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ── 14. List Media Artifacts ──
@router.get(
    "/{request_id}/artifacts",
    response_model=list[MediaArtifactResponse],
    summary="List rendered media artifacts ($v1, v2, ...$)",
)
async def get_artifacts(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[MediaArtifact]:
    stmt = (
        select(MediaArtifact)
        .where(MediaArtifact.production_request_id == request_id)
        .order_by(MediaArtifact.version.desc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ── 15. Safe Media Delivery Endpoint (HTTP Range & Streaming Supported) ──
@router.get(
    "/{request_id}/artifacts/{artifact_id}/media",
    summary="Safely stream a rendered media artifact with HTTP Range (206) support",
)
async def stream_media_artifact(
    channel_id: UUID,
    request_id: UUID,
    artifact_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    storage: Annotated[LocalMediaStorageProvider, Depends(_get_storage_provider)],
    range: Annotated[str | None, Header()] = None,
) -> Response:
    # 1. Authoritative lookup ensuring channel and request isolation
    stmt = (
        select(MediaArtifact)
        .join(ProductionRequest, MediaArtifact.production_request_id == ProductionRequest.id)
        .where(
            MediaArtifact.id == artifact_id,
            MediaArtifact.production_request_id == request_id,
            ProductionRequest.channel_id == channel_id,
        )
    )
    res = await session.execute(stmt)
    art = res.scalar_one_or_none()

    if not art:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media artifact not found on this channel/request.",
        )

    # 2. Resolve path safely via storage provider
    try:
        file_path = storage.resolve_stored_uri(channel_id, request_id, art.storage_uri)
    except StorageSecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Security violation: {exc}",
        ) from exc

    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Physical media file missing from storage.",
        )

    file_size = file_path.stat().st_size
    mime_type = art.mime_type or "video/mp4"

    # 3. Handle HTTP Range Requests (206 Partial Content)
    if range:
        try:
            byte_range = range.replace("bytes=", "").split("-")
            start = int(byte_range[0])
            end = int(byte_range[1]) if byte_range[1] else file_size - 1
            if start >= file_size or end >= file_size or start > end:
                raise ValueError("Range not satisfiable")
        except Exception:
            return Response(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        chunk_size = end - start + 1

        def iter_file():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    read_len = min(65536, remaining)
                    data = f.read(read_len)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            "Content-Type": mime_type,
        }
        return StreamingResponse(iter_file(), status_code=206, headers=headers)

    # 4. Standard 200 OK Delivery
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": mime_type,
    }

    def iter_full_file():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(iter_full_file(), status_code=200, headers=headers)


# ── 16. Get Latest QA Results ──
@router.get(
    "/{request_id}/qa",
    response_model=ProductionQAResultResponse,
    summary="Get latest Production QA evaluation result",
)
async def get_qa_results(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ProductionQAResult:
    stmt = (
        select(ProductionQAResult)
        .where(ProductionQAResult.production_request_id == request_id)
        .order_by(ProductionQAResult.executed_at.desc())
    )
    res = await session.execute(stmt)
    qa = res.scalars().first()
    if not qa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Production QA result found.",
        )
    return qa


# ── 17. Cancel Production Request ──
@router.post(
    "/{request_id}/cancel",
    response_model=ProductionRequestResponse,
    summary="Cancel active production request",
)
async def cancel_production(
    channel_id: UUID,
    request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ProductionRequest:
    stmt = select(ProductionRequest).where(
        ProductionRequest.id == request_id,
        ProductionRequest.channel_id == channel_id,
    )
    res = await session.execute(stmt)
    req = res.scalar_one_or_none()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProductionRequest {request_id} not found.",
        )
    req.status = ProductionRequestStatus.CANCELLED.value
    await session.commit()
    await session.refresh(req)
    return req
