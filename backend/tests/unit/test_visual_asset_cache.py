import hashlib
from pathlib import Path

import pytest

from omega.application.visual_direction import VisualAssetKind
from omega.infrastructure.visual_asset_cache import VisualAssetCache, VisualAssetCacheError


def test_cache_round_trip(tmp_path: Path):
    cache = VisualAssetCache(tmp_path)
    content = b"fake_png_bytes"
    expected_hash = hashlib.sha256(content).hexdigest()

    asset = cache.store(
        content=content,
        kind=VisualAssetKind.IMAGE,
        provider="test_provider",
        mime_type="image/png",
        query="test query",
    )

    assert asset.content_sha256 == expected_hash
    assert asset.provider == "test_provider"
    assert asset.mime_type == "image/png"
    assert asset.query == "test query"

    assert str(tmp_path) in str(asset.local_path)

    asset2 = cache.get(expected_hash)
    assert asset2 is not None
    assert asset2.content_sha256 == expected_hash
    assert asset2.asset_id == expected_hash

    asset3 = cache.store(
        content=content,
        kind=VisualAssetKind.IMAGE,
        provider="test_provider",
        mime_type="image/png",
        query="test query",
    )
    assert asset3.content_sha256 == expected_hash


def test_cache_corruption(tmp_path: Path):
    cache = VisualAssetCache(tmp_path)
    content = b"corrupt_me"
    asset = cache.store(
        content=content,
        kind=VisualAssetKind.IMAGE,
        provider="test_provider",
        mime_type="image/png",
        query="test query",
    )

    with open(asset.local_path, "wb") as f:
        f.write(b"bad")

    with pytest.raises(VisualAssetCacheError, match="corruption"):
        cache.get(asset.content_sha256)


def test_path_safety(tmp_path: Path):
    cache = VisualAssetCache(tmp_path)

    with pytest.raises(VisualAssetCacheError, match="Invalid SHA256"):
        cache.get("../../../etc/passwd")

    with pytest.raises(VisualAssetCacheError, match="Invalid SHA256"):
        cache.get("C:\\temp\\evil")


def test_mime_safety(tmp_path: Path):
    cache = VisualAssetCache(tmp_path)
    content = b"data"

    with pytest.raises(VisualAssetCacheError, match="Unsupported MIME"):
        cache.store(
            content=content,
            kind=VisualAssetKind.IMAGE,
            provider="test",
            mime_type="application/octet-stream",
            query="query",
        )

def test_cache_sha_validation(tmp_path: Path):
    cache = VisualAssetCache(tmp_path)

    with pytest.raises(VisualAssetCacheError, match="Invalid SHA256"):
        cache.get("g" * 64)

    with pytest.raises(VisualAssetCacheError, match="Invalid SHA256"):
        cache.get(("a" * 64).upper())

    with pytest.raises(VisualAssetCacheError, match="Invalid SHA256"):
        cache.get("a" * 63)

def test_cache_partial_entry(tmp_path: Path):
    cache = VisualAssetCache(tmp_path)
    content = b"partial"
    expected_hash = hashlib.sha256(content).hexdigest()

    cache.store(content, VisualAssetKind.IMAGE, "test", "image/png", "test query")

    # Remove metadata
    import os
    os.remove(tmp_path / "sha256" / expected_hash[:2] / expected_hash / "metadata.json")

    with pytest.raises(VisualAssetCacheError, match="Partial cache entry"):
        cache.get(expected_hash)

    # Recreate and remove asset
    cache.store(content, VisualAssetKind.IMAGE, "test", "image/png", "test query")
    os.remove(tmp_path / "sha256" / expected_hash[:2] / expected_hash / "asset.bin")

    with pytest.raises(VisualAssetCacheError, match="Partial cache entry"):
        cache.get(expected_hash)

def test_cache_metadata_corruption(tmp_path: Path):
    import json
    cache = VisualAssetCache(tmp_path)
    content = b"meta"
    expected_hash = hashlib.sha256(content).hexdigest()

    cache.store(content, VisualAssetKind.IMAGE, "test", "image/png", "test query")
    meta_path = tmp_path / "sha256" / expected_hash[:2] / expected_hash / "metadata.json"

    def alter_meta(cb):
        with open(meta_path) as f:
            d = json.load(f)
        cb(d)
        with open(meta_path, "w") as f:
            json.dump(d, f)

    # Change asset_id
    alter_meta(lambda d: d.update({"asset_id": "bad"}))
    with pytest.raises(VisualAssetCacheError, match="metadata asset_id mismatch"):
        cache.get(expected_hash)

    # Reset
    cache.store(content, VisualAssetKind.IMAGE, "test", "image/png", "test query")
    alter_meta(lambda d: d.update({"content_sha256": "bad"}))
    with pytest.raises(VisualAssetCacheError, match="metadata hash mismatch"):
        cache.get(expected_hash)

    # Reset
    cache.store(content, VisualAssetKind.IMAGE, "test", "image/png", "test query")
    alter_meta(lambda d: d.update({"mime_type": "text/plain"}))
    with pytest.raises(VisualAssetCacheError, match="unsupported MIME"):
        cache.get(expected_hash)

    # Invalid JSON
    with open(meta_path, "w") as f:
        f.write("{bad json")
    with pytest.raises(VisualAssetCacheError, match="Failed to read metadata"):
        cache.get(expected_hash)

def test_cache_portability(tmp_path: Path):
    cache = VisualAssetCache(tmp_path)
    content = b"portability"
    expected_hash = hashlib.sha256(content).hexdigest()

    cache.store(content, VisualAssetKind.IMAGE, "test", "image/png", "test query")

    # Metadata should NOT contain absolute root path
    meta_path = tmp_path / "sha256" / expected_hash[:2] / expected_hash / "metadata.json"
    meta_content = meta_path.read_text()
    assert str(tmp_path) not in meta_content

    # But returned asset object local_path points to absolute path
    asset2 = cache.get(expected_hash)
    assert asset2 is not None
    assert isinstance(asset2.local_path, Path)
    assert str(tmp_path) in str(asset2.local_path)
    assert asset2.local_path.exists()
