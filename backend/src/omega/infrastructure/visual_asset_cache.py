import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from omega.application.visual_asset_engine import ResolvedVisualAsset, VisualAssetKind


class VisualAssetCacheError(ValueError):
    pass


class VisualAssetCache:
    ALLOWED_MIME_TYPES = frozenset([
        "image/png",
        "image/jpeg",
        "image/webp",
        "video/mp4",
    ])

    def __init__(self, root: Path):
        self._root = Path(root)

    def _get_hash_path(self, sha256_hash: str) -> Path:
        if not sha256_hash or not re.fullmatch(r"[0-9a-f]{64}", sha256_hash):
            raise VisualAssetCacheError("Invalid SHA256 format")
        prefix = sha256_hash[:2]
        return self._root / "sha256" / prefix / sha256_hash

    def store(
        self,
        content: bytes,
        kind: VisualAssetKind,
        provider: str,
        mime_type: str,
        query: str,
        source_url: str | None = None,
        source_page_url: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_seconds: float | None = None,
        license_name: str | None = None,
        license_url: str | None = None,
        attribution_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResolvedVisualAsset:

        if not content:
            raise VisualAssetCacheError("Content cannot be empty")

        if mime_type not in self.ALLOWED_MIME_TYPES:
            raise VisualAssetCacheError(f"Unsupported MIME type: {mime_type}")

        sha256_hash = hashlib.sha256(content).hexdigest()
        asset_id = sha256_hash

        target_dir = self._get_hash_path(sha256_hash)
        target_dir.mkdir(parents=True, exist_ok=True)

        asset_file = target_dir / "asset.bin"
        meta_file = target_dir / "metadata.json"

        meta_dict = {
            "asset_id": asset_id,
            "kind": kind.value,
            "provider": provider,
            "source_url": source_url,
            "source_page_url": source_page_url,
            "mime_type": mime_type,
            "width": width,
            "height": height,
            "duration_seconds": duration_seconds,
            "content_sha256": sha256_hash,
            "license_name": license_name,
            "license_url": license_url,
            "attribution_text": attribution_text,
            "query": query,
            "metadata": metadata or {},
        }

        fd, temp_path = tempfile.mkstemp(dir=str(target_dir))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise VisualAssetCacheError(f"Failed to write asset temp file: {e}") from e

        meta_json = json.dumps(meta_dict, sort_keys=True, separators=(",", ":"))
        fd_meta, temp_meta_path = tempfile.mkstemp(dir=str(target_dir))
        try:
            with os.fdopen(fd_meta, "w", encoding="utf-8") as f:
                f.write(meta_json)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if os.path.exists(temp_meta_path):
                os.remove(temp_meta_path)
            raise VisualAssetCacheError(f"Failed to write metadata temp file: {e}") from e

        try:
            os.replace(temp_path, asset_file)
            os.replace(temp_meta_path, meta_file)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if os.path.exists(temp_meta_path):
                os.remove(temp_meta_path)
            raise VisualAssetCacheError(f"Failed to replace final cache files: {e}") from e

        meta_dict["local_path"] = asset_file.absolute()
        try:
            return ResolvedVisualAsset(**meta_dict)
        except Exception as e:
            raise VisualAssetCacheError(str(e)) from e

    def get(self, content_sha256: str) -> ResolvedVisualAsset | None:
        target_dir = self._get_hash_path(content_sha256)
        asset_file = target_dir / "asset.bin"
        meta_file = target_dir / "metadata.json"

        asset_exists = asset_file.exists()
        meta_exists = meta_file.exists()

        if not asset_exists and not meta_exists:
            return None

        if asset_exists != meta_exists:
            raise VisualAssetCacheError(f"Partial cache entry for {content_sha256}")

        try:
            with open(asset_file, "rb") as f:
                computed_hash = hashlib.sha256(f.read()).hexdigest()
            if computed_hash != content_sha256:
                raise VisualAssetCacheError(f"Cache corruption: hash mismatch for {content_sha256}")
        except VisualAssetCacheError:
            raise
        except Exception as e:
            raise VisualAssetCacheError(f"Failed to read asset file for {content_sha256}: {e}") from e

        try:
            with open(meta_file, encoding="utf-8") as f:
                meta_dict = json.load(f)
        except Exception as e:
            raise VisualAssetCacheError(f"Failed to read metadata file for {content_sha256}: {e}") from e

        if not isinstance(meta_dict, dict):
            raise VisualAssetCacheError("Metadata is not a dictionary")

        if meta_dict.get("content_sha256") != content_sha256:
            raise VisualAssetCacheError("Cache corruption: metadata hash mismatch")

        if meta_dict.get("asset_id") != content_sha256:
            raise VisualAssetCacheError("Cache corruption: metadata asset_id mismatch")

        if meta_dict.get("mime_type") not in self.ALLOWED_MIME_TYPES:
            raise VisualAssetCacheError(f"Cache corruption: unsupported MIME type {meta_dict.get('mime_type')}")

        meta_dict["local_path"] = asset_file.absolute()
        try:
            return ResolvedVisualAsset(**meta_dict)
        except Exception as e:
            raise VisualAssetCacheError(str(e)) from e
