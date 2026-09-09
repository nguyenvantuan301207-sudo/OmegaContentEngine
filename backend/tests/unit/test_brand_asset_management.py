"""Focused deterministic offline contracts for P12 brand asset management."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omega.application.brand_asset_management_service import (
    BrandAssetErrorCode,
    BrandAssetManagementError,
    BrandAssetManagementService,
)
from omega.application.media_probe import MediaProbeError
from omega.application.media_storage import LocalMediaStorageProvider

PNG = b"\x89PNG\r\n\x1a\nbrand-logo"
MP4 = b"\x00\x00\x00\x18ftypmp42brand-video"


class FakeScalars:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self._values


class FakeSession:
    def __init__(self, channel_id, dna=None, revisions=None):
        self.channel_id = channel_id
        self.channel = SimpleNamespace(id=channel_id, dna=dna or {"brand_package": None})
        self.revisions = revisions or []

    async def get(self, model, key):
        return self.channel if key == self.channel_id else None

    async def execute(self, statement):
        return FakeScalars(self.revisions)


class FakeProbe:
    def __init__(self, *, duration_ms=0, has_video=False, fail=False):
        self.duration_ms = duration_ms
        self.has_video = has_video
        self.fail = fail

    async def probe_file(self, path):
        if self.fail:
            raise MediaProbeError("bad media")
        return {
            "file_size_bytes": Path(path).stat().st_size,
            "duration_ms": self.duration_ms,
            "format_name": "mov,mp4" if self.has_video else "png_pipe",
            "has_video": self.has_video,
            "has_audio": False,
            "width": 1920 if self.has_video else 400,
            "height": 1080 if self.has_video else 200,
            "video_codec": "h264" if self.has_video else "png",
            "audio_codec": None,
            "fps": 30.0 if self.has_video else None,
            "bit_rate": None,
            "streams_count": 1,
            "subtitle_streams_count": 0,
        }


def service(tmp_path: Path, probe: FakeProbe) -> BrandAssetManagementService:
    return BrandAssetManagementService(LocalMediaStorageProvider(str(tmp_path)), probe)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("role", "content", "duration_ms", "has_video", "mime", "suffix"),
    [
        ("logo", PNG, 0, False, "image/png", ".png"),
        ("intro", MP4, 2000, True, "video/mp4", ".mp4"),
        ("outro", MP4, 6000, True, "video/mp4", ".mp4"),
    ],
)
async def test_valid_uploads_return_canonical_reference_hash_and_probe(
    tmp_path, role, content, duration_ms, has_video, mime, suffix
):
    channel_id = uuid4()
    session = FakeSession(channel_id)
    result = await service(
        tmp_path, FakeProbe(duration_ms=duration_ms, has_video=has_video)
    ).upload(session, channel_id, role, BytesIO(content))  # type: ignore[arg-type]

    digest = hashlib.sha256(content).hexdigest()
    assert result.asset.reference == f"brand://channel/{role}/{digest}{suffix}"
    assert result.asset.content_hash == digest
    assert result.asset.mime_type == mime
    assert result.probe["file_size_bytes"] == len(content)
    assert result.probe["width"]
    assert "filename" not in result.model_dump_json()
    assert str(tmp_path) not in result.model_dump_json()


async def test_upload_does_not_mutate_dna(tmp_path):
    channel_id = uuid4()
    dna = {"brand_package": None, "marker": "unchanged"}
    session = FakeSession(channel_id, dna=dna)
    before = dict(session.channel.dna)
    await service(tmp_path, FakeProbe()).upload(
        session, channel_id, "logo", BytesIO(PNG)  # type: ignore[arg-type]
    )
    assert session.channel.dna == before


@pytest.mark.parametrize("role", ["favicon", "../logo", "logo/../../escape"])
async def test_invalid_role_rejected_without_writing(tmp_path, role):
    channel_id = uuid4()
    with pytest.raises(BrandAssetManagementError) as caught:
        await service(tmp_path, FakeProbe()).upload(
            FakeSession(channel_id), channel_id, role, BytesIO(PNG)  # type: ignore[arg-type]
        )
    assert caught.value.code == BrandAssetErrorCode.UNSUPPORTED_ROLE
    assert list(tmp_path.rglob("*")) == []


async def test_empty_upload_rejected_and_cleaned(tmp_path):
    channel_id = uuid4()
    with pytest.raises(BrandAssetManagementError) as caught:
        await service(tmp_path, FakeProbe()).upload(
            FakeSession(channel_id), channel_id, "logo", BytesIO()  # type: ignore[arg-type]
        )
    assert caught.value.code == BrandAssetErrorCode.EMPTY_UPLOAD
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize(
    ("role", "content", "probe"),
    [
        ("logo", MP4, FakeProbe(duration_ms=2000, has_video=True)),
        ("intro", PNG, FakeProbe()),
        ("outro", PNG, FakeProbe()),
    ],
)
async def test_invalid_media_family_rejected(tmp_path, role, content, probe):
    channel_id = uuid4()
    with pytest.raises(BrandAssetManagementError) as caught:
        await service(tmp_path, probe).upload(
            FakeSession(channel_id), channel_id, role, BytesIO(content)  # type: ignore[arg-type]
        )
    assert caught.value.code == BrandAssetErrorCode.INVALID_MEDIA_TYPE
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize(("role", "duration"), [("intro", 1499), ("intro", 3001), ("outro", 4999), ("outro", 8001)])
async def test_invalid_role_duration_rejected_and_cleaned(tmp_path, role, duration):
    channel_id = uuid4()
    with pytest.raises(BrandAssetManagementError) as caught:
        await service(tmp_path, FakeProbe(duration_ms=duration, has_video=True)).upload(
            FakeSession(channel_id), channel_id, role, BytesIO(MP4)  # type: ignore[arg-type]
        )
    assert caught.value.code == BrandAssetErrorCode.INVALID_DURATION
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.mp4"))


async def test_corrupt_unprobeable_upload_rejected_and_cleaned(tmp_path):
    channel_id = uuid4()
    with pytest.raises(BrandAssetManagementError) as caught:
        await service(tmp_path, FakeProbe(fail=True)).upload(
            FakeSession(channel_id), channel_id, "logo", BytesIO(b"corrupt")  # type: ignore[arg-type]
        )
    assert caught.value.code == BrandAssetErrorCode.UNPROBEABLE_MEDIA
    assert not list(tmp_path.rglob("*.tmp"))


async def test_missing_channel_rejected_before_write(tmp_path):
    channel_id = uuid4()
    with pytest.raises(BrandAssetManagementError) as caught:
        await service(tmp_path, FakeProbe()).upload(
            FakeSession(uuid4()), channel_id, "logo", BytesIO(PNG)  # type: ignore[arg-type]
        )
    assert caught.value.code == BrandAssetErrorCode.MISSING_CHANNEL
    assert list(tmp_path.rglob("*")) == []


async def test_preview_success_and_channel_isolation(tmp_path):
    channel_a, channel_b = uuid4(), uuid4()
    managed = service(tmp_path, FakeProbe())
    uploaded = await managed.upload(
        FakeSession(channel_a), channel_a, "logo", BytesIO(PNG)  # type: ignore[arg-type]
    )
    read = await managed.resolve_for_read(
        FakeSession(channel_a), channel_a, "logo", uploaded.storage_key
    )  # type: ignore[arg-type]
    assert read.path.read_bytes() == PNG
    assert read.mime_type == "image/png"
    with pytest.raises(BrandAssetManagementError) as caught:
        await managed.resolve_for_read(
            FakeSession(channel_b), channel_b, "logo", uploaded.storage_key
        )  # type: ignore[arg-type]
    assert caught.value.code == BrandAssetErrorCode.MISSING_ASSET


async def test_missing_preview_and_traversal_are_rejected(tmp_path):
    channel_id = uuid4()
    managed = service(tmp_path, FakeProbe())
    with pytest.raises(BrandAssetManagementError) as missing:
        await managed.resolve_for_read(
            FakeSession(channel_id), channel_id, "logo", "logo/missing.png"
        )  # type: ignore[arg-type]
    assert missing.value.code == BrandAssetErrorCode.MISSING_ASSET
    with pytest.raises(BrandAssetManagementError) as traversal:
        await managed.resolve_for_read(
            FakeSession(channel_id), channel_id, "logo", "logo/../../outside.png"
        )  # type: ignore[arg-type]
    assert traversal.value.code == BrandAssetErrorCode.MALFORMED_REFERENCE


async def test_delete_unused_asset_without_mutating_dna(tmp_path):
    channel_id = uuid4()
    dna = {"brand_package": None, "marker": "unchanged"}
    session = FakeSession(channel_id, dna=dna)
    managed = service(tmp_path, FakeProbe())
    uploaded = await managed.upload(session, channel_id, "logo", BytesIO(PNG))  # type: ignore[arg-type]
    before = dict(session.channel.dna)
    await managed.delete(session, channel_id, "logo", uploaded.storage_key)  # type: ignore[arg-type]
    assert session.channel.dna == before
    assert not list(tmp_path.rglob("*.png"))


async def test_delete_rejects_reference_in_preserved_revision(tmp_path):
    channel_id = uuid4()
    session = FakeSession(channel_id)
    managed = service(tmp_path, FakeProbe())
    uploaded = await managed.upload(session, channel_id, "logo", BytesIO(PNG))  # type: ignore[arg-type]
    session.revisions = [{"brand_package": {"logo_asset": uploaded.asset.model_dump()}}]
    with pytest.raises(BrandAssetManagementError) as caught:
        await managed.delete(session, channel_id, "logo", uploaded.storage_key)  # type: ignore[arg-type]
    assert caught.value.code == BrandAssetErrorCode.ASSET_IN_USE
    assert list(tmp_path.rglob("*.png"))


async def test_deterministic_duplicate_upload_preserves_existing_asset(tmp_path):
    channel_id = uuid4()
    session = FakeSession(channel_id)
    managed = service(tmp_path, FakeProbe())
    first = await managed.upload(session, channel_id, "logo", BytesIO(PNG))  # type: ignore[arg-type]
    second = await managed.upload(session, channel_id, "logo", BytesIO(PNG))  # type: ignore[arg-type]
    assert first == second
    assert len(list(tmp_path.rglob("*.png"))) == 1
    assert not list(tmp_path.rglob("*.tmp"))
