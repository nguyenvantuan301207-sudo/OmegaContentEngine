"""Focused contracts for deterministic fail-closed brand asset resolution."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from omega.application.brand_asset_resolver import (
    BrandAssetResolutionError,
    BrandAssetResolver,
    BrandMediaKind,
)
from omega.application.media_storage import LocalMediaStorageProvider
from omega.domain.channel_dna import BrandAssetReference

PNG = b"\x89PNG\r\n\x1a\nbrand-logo"
MP4 = b"\x00\x00\x00\x18ftypmp42brand-video"
WAV = b"RIFF\x10\x00\x00\x00WAVEbrand-audio"


def _reference(key: str, content: bytes, mime_type: str) -> BrandAssetReference:
    return BrandAssetReference(
        reference=f"brand://channel/{key}",
        content_hash=hashlib.sha256(content).hexdigest(),
        mime_type=mime_type,
    )


def _store(storage: LocalMediaStorageProvider, channel_id, key: str, content: bytes) -> Path:
    path = storage.get_brand_dir(channel_id) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path.resolve()


def test_no_brand_performs_no_resolution() -> None:
    class RejectingStorage:
        def resolve_brand_reference(self, channel_id, reference):
            raise AssertionError("storage must not be called")

    resolver = BrandAssetResolver(RejectingStorage())  # type: ignore[arg-type]
    assert resolver.resolve_optional(uuid4(), None, BrandMediaKind.IMAGE) is None


@pytest.mark.parametrize(
    ("key", "content", "mime_type", "kind"),
    [
        ("logo.png", PNG, "image/png", BrandMediaKind.IMAGE),
        ("intro.mp4", MP4, "video/mp4", BrandMediaKind.VIDEO),
        ("outro.mp4", MP4, "video/mp4", BrandMediaKind.VIDEO),
        ("sonic.wav", WAV, "audio/wav", BrandMediaKind.AUDIO),
    ],
)
def test_valid_brand_media_resolves_deterministically(
    tmp_path: Path,
    key: str,
    content: bytes,
    mime_type: str,
    kind: BrandMediaKind,
) -> None:
    channel_id = uuid4()
    storage = LocalMediaStorageProvider(str(tmp_path))
    expected_path = _store(storage, channel_id, key, content)
    reference = _reference(key, content, mime_type)
    resolver = BrandAssetResolver(storage)

    first = resolver.resolve(channel_id, reference, kind)
    replay = resolver.resolve(channel_id, reference, kind)

    assert first == replay
    assert first.local_path == expected_path
    assert first.local_path.read_bytes() == content
    assert first.content_hash == reference.content_hash
    assert first.media_kind == kind
    assert first.reference == reference.reference


@pytest.mark.parametrize("key", ["missing.png", "directory"])
def test_missing_or_non_file_reference_fails_closed(tmp_path: Path, key: str) -> None:
    channel_id = uuid4()
    storage = LocalMediaStorageProvider(str(tmp_path))
    if key == "directory":
        (storage.get_brand_dir(channel_id) / key).mkdir(parents=True)
    resolver = BrandAssetResolver(storage)

    with pytest.raises(BrandAssetResolutionError, match="does not resolve to a file"):
        resolver.resolve(channel_id, _reference(key, PNG, "image/png"), BrandMediaKind.IMAGE)


def test_inaccessible_reference_fails_closed(tmp_path: Path, monkeypatch) -> None:
    channel_id = uuid4()
    storage = LocalMediaStorageProvider(str(tmp_path))
    reference = _reference("logo.png", PNG, "image/png")
    _store(storage, channel_id, "logo.png", PNG)

    def deny_open(self, *args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", deny_open)
    with pytest.raises(BrandAssetResolutionError, match="inaccessible"):
        BrandAssetResolver(storage).resolve(channel_id, reference, BrandMediaKind.IMAGE)


def test_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    channel_id = uuid4()
    storage = LocalMediaStorageProvider(str(tmp_path))
    _store(storage, channel_id, "logo.png", PNG + b"tampered")

    with pytest.raises(BrandAssetResolutionError, match="content hash mismatch"):
        BrandAssetResolver(storage).resolve(
            channel_id,
            _reference("logo.png", PNG, "image/png"),
            BrandMediaKind.IMAGE,
        )


@pytest.mark.parametrize(
    ("content", "mime_type", "kind"),
    [
        (MP4, "image/png", BrandMediaKind.IMAGE),
        (PNG, "video/mp4", BrandMediaKind.VIDEO),
        (PNG, "audio/wav", BrandMediaKind.AUDIO),
        (PNG, "image/png", BrandMediaKind.VIDEO),
    ],
)
def test_wrong_declared_or_resolved_media_kind_fails_closed(
    tmp_path: Path,
    content: bytes,
    mime_type: str,
    kind: BrandMediaKind,
) -> None:
    channel_id = uuid4()
    storage = LocalMediaStorageProvider(str(tmp_path))
    _store(storage, channel_id, "asset.bin", content)

    with pytest.raises(BrandAssetResolutionError, match="media kind mismatch"):
        BrandAssetResolver(storage).resolve(
            channel_id,
            _reference("asset.bin", content, mime_type),
            kind,
        )


@pytest.mark.parametrize(
    "reference",
    [
        "C:/temp/logo.png",
        "../logo.png",
        "brand://channel/../logo.png",
        "brand://channel/nested//logo.png",
        "https://example.test/logo.png",
    ],
)
def test_noncanonical_or_unsafe_reference_is_rejected(reference: str) -> None:
    with pytest.raises(ValidationError):
        BrandAssetReference(
            reference=reference,
            content_hash="a" * 64,
            mime_type="image/png",
        )
