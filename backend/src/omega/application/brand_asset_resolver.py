"""Deterministic resolution of pinned channel brand assets from canonical media storage."""

from __future__ import annotations

import enum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.domain.channel_dna import BrandAssetReference


class BrandAssetResolutionError(ValueError):
    """Raised when a configured brand asset cannot be safely resolved and verified."""


class BrandMediaKind(enum.StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"


class ResolvedBrandAsset(BaseModel):
    """Immutable renderer-facing handle to a verified repository brand asset."""

    model_config = ConfigDict(frozen=True)

    local_path: Path
    content_hash: str
    media_kind: BrandMediaKind
    mime_type: str
    reference: str


class BrandAssetResolver:
    """Thin verifier around the repository's channel-scoped local media storage."""

    def __init__(self, storage: LocalMediaStorageProvider) -> None:
        self._storage = storage

    def resolve_optional(
        self,
        channel_id: UUID,
        asset: BrandAssetReference | None,
        expected_kind: BrandMediaKind,
    ) -> ResolvedBrandAsset | None:
        if asset is None:
            return None
        return self.resolve(channel_id, asset, expected_kind)

    def resolve(
        self,
        channel_id: UUID,
        asset: BrandAssetReference,
        expected_kind: BrandMediaKind,
    ) -> ResolvedBrandAsset:
        try:
            path = self._storage.resolve_brand_reference(channel_id, asset.reference)
        except (OSError, ValueError) as exc:
            raise BrandAssetResolutionError("Brand asset reference is invalid or inaccessible.") from exc

        try:
            if not path.is_file():
                raise BrandAssetResolutionError("Brand asset reference does not resolve to a file.")
            actual_hash = compute_sha256(path)
            with path.open("rb") as stream:
                header = stream.read(4096)
        except BrandAssetResolutionError:
            raise
        except OSError as exc:
            raise BrandAssetResolutionError("Brand asset reference is inaccessible.") from exc

        if actual_hash != asset.content_hash:
            raise BrandAssetResolutionError("Brand asset content hash mismatch.")
        if not self._matches_media_kind(header, asset.mime_type, expected_kind):
            raise BrandAssetResolutionError("Brand asset media kind mismatch.")

        return ResolvedBrandAsset(
            local_path=path,
            content_hash=actual_hash,
            media_kind=expected_kind,
            mime_type=asset.mime_type,
            reference=asset.reference,
        )

    @staticmethod
    def _matches_media_kind(header: bytes, mime_type: str, expected_kind: BrandMediaKind) -> bool:
        normalized_mime = mime_type.lower()
        if expected_kind == BrandMediaKind.IMAGE:
            declared = normalized_mime.startswith("image/")
            detected = (
                header.startswith(b"\x89PNG\r\n\x1a\n")
                or header.startswith(b"\xff\xd8\xff")
                or (len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
            )
        elif expected_kind == BrandMediaKind.VIDEO:
            declared = normalized_mime.startswith("video/")
            detected = b"ftyp" in header
        else:
            declared = normalized_mime.startswith("audio/")
            detected = (
                (header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WAVE")
                or header.startswith(b"ID3")
                or header.startswith((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))
                or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0)
            )
        return declared and detected
