import hashlib
from pathlib import Path

import pytest

from omega.application.visual_asset_engine import ResolvedVisualAsset
from omega.application.visual_direction import VisualAssetKind
from omega.infrastructure.visual_asset_materializer import (
    VisualAssetMaterializer,
    VisualAssetMaterializerError,
)

# Minimal MP4 mock data with ftyp box
VALID_MP4_HEADER = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
VALID_MP4_DATA = VALID_MP4_HEADER + b"\x00" * 100


def make_resolved_broll(
    path: Path,
    content: bytes = VALID_MP4_DATA,
    mime_type: str = "video/mp4",
    kind: VisualAssetKind = VisualAssetKind.BROLL,
    duration_seconds: float = 10.0,
    width: int = 1920,
    height: int = 1080,
    sha: str | None = None,
) -> ResolvedVisualAsset:
    path.write_bytes(content)
    content_sha = sha if sha is not None else hashlib.sha256(content).hexdigest()
    return ResolvedVisualAsset(
        asset_id="broll_1",
        kind=kind,
        provider="pexels",
        source_url="https://videos.pexels.com/video-files/123/123.mp4",
        source_page_url="https://www.pexels.com/video/123/",
        local_path=path,
        mime_type=mime_type,
        width=width,
        height=height,
        duration_seconds=duration_seconds,
        content_sha256=content_sha,
        license_name="Pexels License",
        license_url="https://www.pexels.com/license/",
        attribution_text="Video by Test on Pexels",
        query="data center server racks",
        metadata={"search_query": "data center server racks"},
    )


def test_materialize_valid_broll(tmp_path: Path):
    mp4_path = tmp_path / "valid.mp4"
    resolved = make_resolved_broll(mp4_path)

    bound = VisualAssetMaterializer.materialize_broll(resolved)

    assert bound.asset_id == "broll_1"
    assert bound.kind == VisualAssetKind.BROLL
    assert bound.mime_type == "video/mp4"
    assert bound.content_sha256 == resolved.content_sha256
    assert bound.local_path == mp4_path.resolve()
    assert bound.duration_seconds == 10.0
    assert bound.width == 1920
    assert bound.height == 1080


def test_materialize_broll_wrong_kind(tmp_path: Path):
    mp4_path = tmp_path / "wrong_kind.mp4"
    resolved = make_resolved_broll(mp4_path, kind=VisualAssetKind.IMAGE)

    with pytest.raises(VisualAssetMaterializerError, match="Unsupported asset kind"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_wrong_mime(tmp_path: Path):
    mp4_path = tmp_path / "wrong_mime.mp4"
    resolved = make_resolved_broll(mp4_path, mime_type="video/webm")

    with pytest.raises(VisualAssetMaterializerError, match="Unsupported video MIME type"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_missing_file(tmp_path: Path):
    mp4_path = tmp_path / "non_existent.mp4"
    resolved = ResolvedVisualAsset(
        asset_id="broll_missing",
        kind=VisualAssetKind.BROLL,
        provider="pexels",
        source_url="https://videos.pexels.com/video-files/123/123.mp4",
        source_page_url=None,
        local_path=mp4_path,
        mime_type="video/mp4",
        width=1920,
        height=1080,
        duration_seconds=10.0,
        content_sha256="0" * 64,
        license_name=None,
        license_url=None,
        attribution_text=None,
        query="query",
        metadata={},
    )

    with pytest.raises(VisualAssetMaterializerError, match="Video file cannot be accessed"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_directory(tmp_path: Path):
    dir_path = tmp_path / "a_dir.mp4"
    dir_path.mkdir()
    resolved = ResolvedVisualAsset(
        asset_id="broll_dir",
        kind=VisualAssetKind.BROLL,
        provider="pexels",
        source_url=None,
        source_page_url=None,
        local_path=dir_path,
        mime_type="video/mp4",
        width=1920,
        height=1080,
        duration_seconds=10.0,
        content_sha256="0" * 64,
        license_name=None,
        license_url=None,
        attribution_text=None,
        query="query",
        metadata={},
    )

    with pytest.raises(VisualAssetMaterializerError, match="Video path is not a regular file"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_zero_byte(tmp_path: Path):
    mp4_path = tmp_path / "empty.mp4"
    resolved = make_resolved_broll(mp4_path, content=b"")

    with pytest.raises(VisualAssetMaterializerError, match="Video file is empty"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_size_ceiling_exceeded(tmp_path: Path, monkeypatch):
    mp4_path = tmp_path / "huge.mp4"
    mp4_path.write_bytes(VALID_MP4_DATA)
    resolved = make_resolved_broll(mp4_path)

    import os
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path) == str(mp4_path):
            return os.stat_result((
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                st.st_uid,
                st.st_gid,
                150 * 1024 * 1024 + 1,
                st.st_atime,
                st.st_mtime,
                st.st_ctime,
            ))
        return st

    monkeypatch.setattr(os, "stat", fake_stat)

    with pytest.raises(VisualAssetMaterializerError, match="exceeds 150 MiB ceiling"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_bad_sha(tmp_path: Path):
    mp4_path = tmp_path / "bad_sha.mp4"
    resolved = make_resolved_broll(mp4_path, sha="f" * 64)

    with pytest.raises(VisualAssetMaterializerError, match="Video SHA256 mismatch"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_missing_ftyp(tmp_path: Path):
    mp4_path = tmp_path / "missing_ftyp.mp4"
    resolved = make_resolved_broll(mp4_path, content=b"\x00" * 100)

    with pytest.raises(VisualAssetMaterializerError, match="Invalid MP4 container: ftyp box missing"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_invalid_duration(tmp_path: Path):
    mp4_path = tmp_path / "invalid_duration.mp4"
    resolved = make_resolved_broll(mp4_path, duration_seconds=0.0)

    with pytest.raises(VisualAssetMaterializerError, match="Invalid video duration"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_invalid_dimensions(tmp_path: Path):
    mp4_path = tmp_path / "invalid_dim.mp4"
    resolved = make_resolved_broll(mp4_path, width=0)

    with pytest.raises(VisualAssetMaterializerError, match="Invalid video width"):
        VisualAssetMaterializer.materialize_broll(resolved)


def test_materialize_broll_streaming_sha(tmp_path: Path):
    # Tests that streaming read (chunked) correctly computes hash on content larger than 4KB
    mp4_path = tmp_path / "streaming.mp4"
    content = VALID_MP4_HEADER + (b"X" * 10000)
    resolved = make_resolved_broll(mp4_path, content=content)

    bound = VisualAssetMaterializer.materialize_broll(resolved)
    assert bound.content_sha256 == hashlib.sha256(content).hexdigest()
