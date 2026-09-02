import hashlib
import os
from pathlib import Path

import pytest

from omega.application.visual_asset_engine import ResolvedVisualAsset
from omega.application.visual_direction import VisualAssetKind
from omega.infrastructure.visual_asset_materializer import (
    VisualAssetMaterializer,
    VisualAssetMaterializerError,
)

# Minimal valid magic bytes
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
WEBP_BYTES = b"RIFF\x14\x00\x00\x00WEBPVP8 \x08\x00\x00\x00"


def make_resolved(
    path: Path,
    mime_type: str,
    content: bytes,
    kind: VisualAssetKind = VisualAssetKind.IMAGE,
    sha: str | None = None,
) -> ResolvedVisualAsset:
    path.write_bytes(content)
    content_sha = sha if sha is not None else hashlib.sha256(content).hexdigest()
    return ResolvedVisualAsset(
        asset_id="test_asset_1",
        kind=kind,
        provider="pexels",
        source_url="https://images.pexels.com/photos/123/test.jpg",
        source_page_url="https://www.pexels.com/photo/123/",
        local_path=path,
        mime_type=mime_type,
        width=1920,
        height=1080,
        duration_seconds=None,
        content_sha256=content_sha,
        license_name="Pexels License",
        license_url="https://www.pexels.com/license/",
        attribution_text="Photo by Test on Pexels",
        query="data center server racks",
        metadata={"search_query": "data center server racks"},
    )


def test_materialize_valid_jpeg(tmp_path: Path):
    img_path = tmp_path / "test.jpg"
    resolved = make_resolved(img_path, "image/jpeg", JPEG_BYTES)

    bound = VisualAssetMaterializer.materialize(resolved)

    assert bound.asset_id == "test_asset_1"
    assert bound.kind == VisualAssetKind.IMAGE
    assert bound.mime_type == "image/jpeg"
    assert bound.content_sha256 == resolved.content_sha256
    assert bound.width == 1920
    assert bound.height == 1080
    assert bound.data_uri.startswith("data:image/jpeg;base64,")
    assert "https://" not in bound.data_uri


def test_materialize_valid_png(tmp_path: Path):
    img_path = tmp_path / "test.png"
    resolved = make_resolved(img_path, "image/png", PNG_BYTES)

    bound = VisualAssetMaterializer.materialize(resolved)

    assert bound.mime_type == "image/png"
    assert bound.data_uri.startswith("data:image/png;base64,")


def test_materialize_valid_webp(tmp_path: Path):
    img_path = tmp_path / "test.webp"
    resolved = make_resolved(img_path, "image/webp", WEBP_BYTES)

    bound = VisualAssetMaterializer.materialize(resolved)

    assert bound.mime_type == "image/webp"
    assert bound.data_uri.startswith("data:image/webp;base64,")


def test_materialize_wrong_kind_rejected(tmp_path: Path):
    img_path = tmp_path / "test.mp4"
    resolved = make_resolved(
        img_path, "image/jpeg", JPEG_BYTES, kind=VisualAssetKind.BROLL
    )

    with pytest.raises(VisualAssetMaterializerError, match=r"^Unsupported asset kind$"):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_unsupported_mime_rejected(tmp_path: Path):
    img_path = tmp_path / "test.gif"
    resolved = make_resolved(img_path, "image/gif", b"GIF89a...")

    with pytest.raises(
        VisualAssetMaterializerError, match=r"^Unsupported image MIME type$"
    ):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_mime_signature_mismatch(tmp_path: Path):
    img_path = tmp_path / "test.jpg"
    # Declares JPEG, but content is PNG bytes
    resolved = make_resolved(img_path, "image/jpeg", PNG_BYTES)

    with pytest.raises(
        VisualAssetMaterializerError, match=r"^Image content does not match MIME type$"
    ):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_missing_file_rejected(tmp_path: Path):
    img_path = tmp_path / "non_existent.jpg"
    content_sha = hashlib.sha256(JPEG_BYTES).hexdigest()
    resolved = ResolvedVisualAsset(
        asset_id="test_missing",
        kind=VisualAssetKind.IMAGE,
        provider="pexels",
        source_url="https://images.pexels.com/photos/123/test.jpg",
        source_page_url=None,
        local_path=img_path,
        mime_type="image/jpeg",
        width=100,
        height=100,
        duration_seconds=None,
        content_sha256=content_sha,
        license_name=None,
        license_url=None,
        attribution_text=None,
        query="query",
        metadata={},
    )

    with pytest.raises(
        VisualAssetMaterializerError, match=r"Image file cannot be accessed"
    ):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_non_file_path_rejected(tmp_path: Path):
    dir_path = tmp_path / "a_directory.jpg"
    dir_path.mkdir()
    content_sha = "a" * 64
    resolved = ResolvedVisualAsset(
        asset_id="test_dir",
        kind=VisualAssetKind.IMAGE,
        provider="pexels",
        source_url="https://images.pexels.com/photos/123/test.jpg",
        source_page_url=None,
        local_path=dir_path,
        mime_type="image/jpeg",
        width=100,
        height=100,
        duration_seconds=None,
        content_sha256=content_sha,
        license_name=None,
        license_url=None,
        attribution_text=None,
        query="query",
        metadata={},
    )

    with pytest.raises(
        VisualAssetMaterializerError, match=r"Image path is not a regular file"
    ):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_zero_byte_rejected(tmp_path: Path):
    img_path = tmp_path / "empty.jpg"
    resolved = make_resolved(img_path, "image/jpeg", b"")

    with pytest.raises(VisualAssetMaterializerError, match=r"Image file is empty"):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_size_ceiling_exceeded_rejected(tmp_path: Path, monkeypatch):
    img_path = tmp_path / "huge.jpg"
    img_path.write_bytes(JPEG_BYTES)

    resolved = make_resolved(img_path, "image/jpeg", JPEG_BYTES)

    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path) == str(img_path):
            return os.stat_result((
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                st.st_uid,
                st.st_gid,
                25 * 1024 * 1024 + 1,  # size > 25 MiB
                st.st_atime,
                st.st_mtime,
                st.st_ctime,
            ))
        return st

    monkeypatch.setattr(os, "stat", fake_stat)

    with pytest.raises(VisualAssetMaterializerError, match=r"exceeds 25 MiB ceiling"):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_invalid_content_sha256_format(tmp_path: Path):
    img_path = tmp_path / "test.jpg"
    resolved = make_resolved(
        img_path, "image/jpeg", JPEG_BYTES, sha="invalid_not_64_chars"
    )

    with pytest.raises(
        VisualAssetMaterializerError, match=r"^Invalid content_sha256 format$"
    ):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_sha256_mismatch(tmp_path: Path):
    img_path = tmp_path / "test.jpg"
    resolved = make_resolved(img_path, "image/jpeg", JPEG_BYTES, sha="0" * 64)

    with pytest.raises(VisualAssetMaterializerError, match=r"^Image SHA256 mismatch$"):
        VisualAssetMaterializer.materialize(resolved)


def test_materialize_deterministic_output(tmp_path: Path):
    p1 = tmp_path / "1.jpg"
    p2 = tmp_path / "2.jpg"
    r1 = make_resolved(p1, "image/jpeg", JPEG_BYTES)
    r2 = make_resolved(p2, "image/jpeg", JPEG_BYTES)

    b1 = VisualAssetMaterializer.materialize(r1)
    b2 = VisualAssetMaterializer.materialize(r2)

    assert b1.data_uri == b2.data_uri
    assert b1.content_sha256 == b2.content_sha256
