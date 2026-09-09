"""Channel-scoped management of canonical reusable brand assets."""

from __future__ import annotations

import enum
import os
import uuid
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.media_probe import MediaProbe, MediaProbeError
from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.domain.channel_dna import BrandAssetReference, BrandPackage
from omega.infrastructure.models import Channel, ChannelDNARevision


class BrandAssetErrorCode(enum.StrEnum):
    UNSUPPORTED_ROLE = "UNSUPPORTED_ROLE"
    EMPTY_UPLOAD = "EMPTY_UPLOAD"
    INVALID_MEDIA_TYPE = "INVALID_MEDIA_TYPE"
    INVALID_DURATION = "INVALID_DURATION"
    UNPROBEABLE_MEDIA = "UNPROBEABLE_MEDIA"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    MISSING_CHANNEL = "MISSING_CHANNEL"
    MISSING_ASSET = "MISSING_ASSET"
    ASSET_IN_USE = "ASSET_IN_USE"
    MALFORMED_REFERENCE = "MALFORMED_CANONICAL_REFERENCE"


class BrandAssetManagementError(RuntimeError):
    """Structured management failure suitable for stable API translation."""

    def __init__(self, code: BrandAssetErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class BrandAssetRole(enum.StrEnum):
    LOGO = "logo"
    INTRO = "intro"
    OUTRO = "outro"


class BrandAssetUploadResult(BaseModel):
    """Metadata returned after an asset is stored and validated."""

    model_config = ConfigDict(frozen=True)

    role: BrandAssetRole
    storage_key: str
    asset: BrandAssetReference
    probe: dict[str, Any]


class BrandAssetReadResult(BaseModel):
    """Internal browser-delivery handle; local paths are never serialized by the API."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    path: Path
    mime_type: str


class BrandAssetManagementService:
    """Upload, resolve, and safely delete canonical channel brand assets."""

    _EXTENSIONS = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
    }

    def __init__(
        self,
        storage: LocalMediaStorageProvider | None = None,
        probe: MediaProbe | None = None,
    ) -> None:
        self._storage = storage or LocalMediaStorageProvider()
        self._probe = probe or MediaProbe()

    async def upload(
        self,
        session: AsyncSession,
        channel_id: UUID,
        role_value: str,
        source: BinaryIO,
    ) -> BrandAssetUploadResult:
        role = self._parse_role(role_value)
        await self._require_channel(session, channel_id)
        brand_dir = self._storage.get_brand_dir(channel_id)
        brand_dir.mkdir(parents=True, exist_ok=True)
        candidate = brand_dir / f".upload-{uuid.uuid4().hex}.tmp"
        accepted = False
        try:
            self._write_candidate(source, candidate)
            digest = compute_sha256(candidate)
            try:
                probe = await self._probe.probe_file(candidate)
            except MediaProbeError as exc:
                raise BrandAssetManagementError(
                    BrandAssetErrorCode.UNPROBEABLE_MEDIA,
                    "Uploaded media is corrupt or could not be probed.",
                ) from exc
            mime_type = self._actual_mime(candidate, probe)
            reference = self._build_reference(role, digest, mime_type, probe)
            storage_key = f"{role.value}/{digest}{self._EXTENSIONS[mime_type]}"
            destination = self._storage.resolve_brand_reference(
                channel_id, f"brand://channel/{storage_key}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if not destination.is_file() or compute_sha256(destination) != digest:
                    raise BrandAssetManagementError(
                        BrandAssetErrorCode.STORAGE_FAILURE,
                        "Canonical asset destination conflicts with existing storage.",
                    )
                candidate.unlink()
            else:
                os.replace(candidate, destination)
            accepted = True
            return BrandAssetUploadResult(
                role=role,
                storage_key=storage_key,
                asset=reference.model_copy(
                    update={"reference": f"brand://channel/{storage_key}"}
                ),
                probe=probe,
            )
        except BrandAssetManagementError:
            raise
        except (OSError, ValueError, ValidationError) as exc:
            raise BrandAssetManagementError(
                BrandAssetErrorCode.STORAGE_FAILURE,
                "Brand asset storage or hash operation failed.",
            ) from exc
        finally:
            if not accepted and candidate.is_file():
                self._storage.cleanup_file(candidate)

    async def resolve_for_read(
        self,
        session: AsyncSession,
        channel_id: UUID,
        role_value: str,
        storage_key: str,
    ) -> BrandAssetReadResult:
        role = self._parse_role(role_value)
        await self._require_channel(session, channel_id)
        reference = self._canonical_reference(role, storage_key)
        try:
            path = self._storage.resolve_brand_reference(channel_id, reference)
        except (OSError, ValueError) as exc:
            raise BrandAssetManagementError(
                BrandAssetErrorCode.MALFORMED_REFERENCE, "Malformed canonical brand reference."
            ) from exc
        if not path.is_file():
            raise BrandAssetManagementError(
                BrandAssetErrorCode.MISSING_ASSET, "Brand asset was not found."
            )
        mime_type = self._mime_from_suffix(path.suffix)
        if mime_type is None:
            raise BrandAssetManagementError(
                BrandAssetErrorCode.MALFORMED_REFERENCE, "Malformed canonical brand reference."
            )
        return BrandAssetReadResult(path=path, mime_type=mime_type)

    async def delete(
        self,
        session: AsyncSession,
        channel_id: UUID,
        role_value: str,
        storage_key: str,
    ) -> None:
        read = await self.resolve_for_read(session, channel_id, role_value, storage_key)
        reference = self._canonical_reference(self._parse_role(role_value), storage_key)
        revisions = (
            await session.execute(
                select(ChannelDNARevision.snapshot).where(ChannelDNARevision.channel_id == channel_id)
            )
        ).scalars()
        if any(self._snapshot_references(snapshot, reference) for snapshot in revisions):
            raise BrandAssetManagementError(
                BrandAssetErrorCode.ASSET_IN_USE,
                "Brand asset is referenced by current or preserved Channel DNA.",
            )
        try:
            read.path.unlink()
        except OSError as exc:
            raise BrandAssetManagementError(
                BrandAssetErrorCode.STORAGE_FAILURE, "Brand asset deletion failed."
            ) from exc

    async def _require_channel(self, session: AsyncSession, channel_id: UUID) -> Channel:
        channel = await session.get(Channel, channel_id)
        if channel is None:
            raise BrandAssetManagementError(
                BrandAssetErrorCode.MISSING_CHANNEL, f"Channel '{channel_id}' not found."
            )
        return channel

    @staticmethod
    def _parse_role(value: str) -> BrandAssetRole:
        try:
            return BrandAssetRole(value.lower())
        except ValueError as exc:
            raise BrandAssetManagementError(
                BrandAssetErrorCode.UNSUPPORTED_ROLE,
                "Brand asset role must be one of: logo, intro, outro.",
            ) from exc

    @staticmethod
    def _write_candidate(source: BinaryIO, candidate: Path) -> None:
        total = 0
        with candidate.open("xb") as target:
            while chunk := source.read(64 * 1024):
                target.write(chunk)
                total += len(chunk)
            target.flush()
            os.fsync(target.fileno())
        if total == 0:
            raise BrandAssetManagementError(
                BrandAssetErrorCode.EMPTY_UPLOAD, "Uploaded brand asset must not be empty."
            )

    def _build_reference(
        self,
        role: BrandAssetRole,
        digest: str,
        mime_type: str,
        probe: dict[str, Any],
    ) -> BrandAssetReference:
        expected_prefix = "image/" if role == BrandAssetRole.LOGO else "video/"
        if not mime_type.startswith(expected_prefix):
            raise BrandAssetManagementError(
                BrandAssetErrorCode.INVALID_MEDIA_TYPE,
                f"Role '{role.value}' requires {expected_prefix[:-1]} media.",
            )
        duration_ms = int(probe.get("duration_ms") or 0)
        duration = duration_ms / 1000 if duration_ms > 0 else None
        values = {
            "reference": f"brand://channel/pending/{digest}",
            "content_hash": digest,
            "mime_type": mime_type,
            "width": probe.get("width"),
            "height": probe.get("height"),
            "duration_seconds": duration,
        }
        try:
            asset = BrandAssetReference.model_validate(values)
            package_field = f"{role.value}_asset"
            BrandPackage.model_validate({package_field: asset})
            return asset
        except ValidationError as exc:
            messages = " ".join(str(error.get("msg", "")) for error in exc.errors())
            code = (
                BrandAssetErrorCode.INVALID_DURATION
                if "duration" in messages.lower()
                else BrandAssetErrorCode.INVALID_MEDIA_TYPE
            )
            raise BrandAssetManagementError(code, messages or "Brand asset validation failed.") from exc

    @staticmethod
    def _actual_mime(path: Path, probe: dict[str, Any]) -> str:
        header = path.read_bytes()[:4096]
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image/webp"
        if b"ftyp" in header and probe.get("has_video"):
            return "video/mp4"
        raise BrandAssetManagementError(
            BrandAssetErrorCode.INVALID_MEDIA_TYPE, "Uploaded media type is not supported."
        )

    @staticmethod
    def _canonical_reference(role: BrandAssetRole, storage_key: str) -> str:
        if not storage_key.startswith(f"{role.value}/"):
            raise BrandAssetManagementError(
                BrandAssetErrorCode.MALFORMED_REFERENCE, "Malformed canonical brand reference."
            )
        try:
            return BrandAssetReference(
                reference=f"brand://channel/{storage_key}",
                content_hash="0" * 64,
                mime_type="application/octet-stream",
            ).reference
        except ValidationError as exc:
            raise BrandAssetManagementError(
                BrandAssetErrorCode.MALFORMED_REFERENCE, "Malformed canonical brand reference."
            ) from exc

    @classmethod
    def _mime_from_suffix(cls, suffix: str) -> str | None:
        return next((mime for mime, ext in cls._EXTENSIONS.items() if ext == suffix.lower()), None)

    @staticmethod
    def _snapshot_references(value: Any, reference: str) -> bool:
        if isinstance(value, dict):
            return value.get("reference") == reference or any(
                BrandAssetManagementService._snapshot_references(item, reference)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                BrandAssetManagementService._snapshot_references(item, reference) for item in value
            )
        return False
