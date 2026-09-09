"""Channel-scoped API for reusable brand asset management."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.brand_asset_management_service import (
    BrandAssetErrorCode,
    BrandAssetManagementError,
    BrandAssetManagementService,
    BrandAssetUploadResult,
)

router = APIRouter(
    prefix="/api/v1/channels/{channel_id}/brand-assets",
    tags=["Brand Assets"],
)

_ERROR_STATUS = {
    BrandAssetErrorCode.UNSUPPORTED_ROLE: status.HTTP_400_BAD_REQUEST,
    BrandAssetErrorCode.EMPTY_UPLOAD: status.HTTP_400_BAD_REQUEST,
    BrandAssetErrorCode.INVALID_MEDIA_TYPE: status.HTTP_422_UNPROCESSABLE_ENTITY,
    BrandAssetErrorCode.INVALID_DURATION: status.HTTP_422_UNPROCESSABLE_ENTITY,
    BrandAssetErrorCode.UNPROBEABLE_MEDIA: status.HTTP_422_UNPROCESSABLE_ENTITY,
    BrandAssetErrorCode.STORAGE_FAILURE: status.HTTP_500_INTERNAL_SERVER_ERROR,
    BrandAssetErrorCode.MISSING_CHANNEL: status.HTTP_404_NOT_FOUND,
    BrandAssetErrorCode.MISSING_ASSET: status.HTTP_404_NOT_FOUND,
    BrandAssetErrorCode.ASSET_IN_USE: status.HTTP_409_CONFLICT,
    BrandAssetErrorCode.MALFORMED_REFERENCE: status.HTTP_400_BAD_REQUEST,
}


def get_brand_asset_service() -> BrandAssetManagementService:
    """Create a request-local service using canonical configured media storage."""
    return BrandAssetManagementService()


def _raise_api_error(exc: BrandAssetManagementError) -> None:
    raise HTTPException(
        status_code=_ERROR_STATUS[exc.code],
        detail={"code": exc.code.value, "message": str(exc)},
    ) from exc


@router.post("/{role}", response_model=BrandAssetUploadResult, status_code=status.HTTP_201_CREATED)
async def upload_brand_asset(
    channel_id: UUID,
    role: str,
    file: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[BrandAssetManagementService, Depends(get_brand_asset_service)],
) -> BrandAssetUploadResult:
    """Store and validate an uploaded asset without mutating Channel DNA."""
    try:
        return await service.upload(db, channel_id, role, file.file)
    except BrandAssetManagementError as exc:
        _raise_api_error(exc)


@router.get("/{role}/{asset_name}", response_class=FileResponse)
async def preview_brand_asset(
    channel_id: UUID,
    role: str,
    asset_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[BrandAssetManagementService, Depends(get_brand_asset_service)],
) -> FileResponse:
    """Serve one canonical channel asset for browser preview."""
    try:
        asset = await service.resolve_for_read(db, channel_id, role, f"{role}/{asset_name}")
    except BrandAssetManagementError as exc:
        _raise_api_error(exc)
    return FileResponse(asset.path, media_type=asset.mime_type)


@router.delete("/{role}/{asset_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand_asset(
    channel_id: UUID,
    role: str,
    asset_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[BrandAssetManagementService, Depends(get_brand_asset_service)],
) -> None:
    """Delete an unreferenced canonical asset without mutating Channel DNA."""
    try:
        await service.delete(db, channel_id, role, f"{role}/{asset_name}")
    except BrandAssetManagementError as exc:
        _raise_api_error(exc)
