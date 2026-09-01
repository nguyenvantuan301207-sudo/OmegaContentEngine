"""Media storage management, safe path resolution, and SHA-256 computation."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from pathlib import Path
from uuid import UUID

# Default media base root inside container
MEDIA_BASE_ROOT = os.getenv("MEDIA_STORAGE_ROOT", "/app/data")


class StorageSecurityError(ValueError):
    """Raised when an unsafe path or directory traversal attempt is detected."""

    pass


class LocalMediaStorageProvider:
    """Local media storage manager ensuring channel/request isolation and safe path resolution."""

    def __init__(self, base_root: str | None = None) -> None:
        root_val = base_root or os.getenv("MEDIA_STORAGE_ROOT", MEDIA_BASE_ROOT)
        self.base_root = Path(root_val).resolve()

    def get_channel_dir(self, channel_id: UUID) -> Path:
        """Resolve the channel directory."""
        path = (self.base_root / "channels" / str(channel_id)).resolve()
        self._assert_safe_path(path)
        return path

    def get_production_dir(self, channel_id: UUID, request_id: UUID) -> Path:
        """Resolve the production request root directory."""
        path = (self.get_channel_dir(channel_id) / "production" / str(request_id)).resolve()
        self._assert_safe_path(path)
        return path

    def get_staging_dir(self, channel_id: UUID, request_id: UUID, job_id: UUID) -> Path:
        """Resolve isolated staging directory for a render job."""
        path = (self.get_production_dir(channel_id, request_id) / "staging" / str(job_id)).resolve()
        self._assert_safe_path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_artifacts_dir(self, channel_id: UUID, request_id: UUID) -> Path:
        """Resolve immutable artifacts directory for a production request."""
        path = (self.get_production_dir(channel_id, request_id) / "artifacts").resolve()
        self._assert_safe_path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_assets_dir(self, channel_id: UUID, request_id: UUID) -> Path:
        """Resolve assets directory for a production request."""
        path = (self.get_production_dir(channel_id, request_id) / "assets").resolve()
        self._assert_safe_path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_narration_dir(self, channel_id: UUID, request_id: UUID) -> Path:
        """Resolve narration audio directory for a production request."""
        path = (self.get_production_dir(channel_id, request_id) / "narration").resolve()
        self._assert_safe_path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_subtitles_dir(self, channel_id: UUID, request_id: UUID) -> Path:
        """Resolve subtitles directory for a production request."""
        path = (self.get_production_dir(channel_id, request_id) / "subtitles").resolve()
        self._assert_safe_path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_stored_uri(self, channel_id: UUID, request_id: UUID, relative_uri: str) -> Path:
        """Resolve and validate a relative storage URI against the production request root."""
        clean_rel = relative_uri.lstrip("/\\")
        prod_dir = self.get_production_dir(channel_id, request_id)
        resolved = (prod_dir / clean_rel).resolve()
        self._assert_within_root(resolved, prod_dir)
        return resolved

    def resolve_artifact_path(
        self,
        channel_id: UUID,
        production_request_id: UUID | None,
        storage_uri: str,
    ) -> Path:
        """Resolve physical path for a MediaArtifact across scoped and flat layouts."""
        if production_request_id:
            scoped_path = self.resolve_stored_uri(channel_id, production_request_id, storage_uri)
            if scoped_path.exists():
                return scoped_path
        clean_rel = storage_uri.lstrip("/\\")
        flat_path = (self.base_root / clean_rel).resolve()
        self._assert_safe_path(flat_path)
        if production_request_id and not flat_path.exists():
            return self.resolve_stored_uri(channel_id, production_request_id, storage_uri)
        return flat_path

    def to_relative_uri(self, channel_id: UUID, request_id: UUID, absolute_path: Path | str) -> str:
        """Convert an absolute path to a relative storage URI scoped to the production request."""
        prod_dir = self.get_production_dir(channel_id, request_id)
        abs_p = Path(absolute_path).resolve()
        self._assert_within_root(abs_p, prod_dir)
        return str(abs_p.relative_to(prod_dir)).replace("\\", "/")

    def cleanup_directory(self, dir_path: Path | str) -> None:
        """Safely delete a directory if it exists."""
        p = Path(dir_path)
        if p.exists() and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)

    def cleanup_file(self, file_path: Path | str) -> None:
        """Safely delete a file if it exists."""
        p = Path(file_path)
        if p.exists() and p.is_file():
            with contextlib.suppress(OSError):
                p.unlink(missing_ok=True)

    def _assert_safe_path(self, target: Path) -> None:
        """Assert target path is strictly within the media base root."""
        try:
            target.resolve().relative_to(self.base_root)
        except ValueError as exc:
            raise StorageSecurityError(
                f"Access denied: path '{target}' escapes media storage root."
            ) from exc

    def _assert_within_root(self, target: Path, root: Path) -> None:
        """Assert target path is strictly within the specified root directory."""
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise StorageSecurityError(
                f"Access denied: path '{target}' escapes scope '{root}'."
            ) from exc


def compute_sha256(file_path: Path | str) -> str:
    """Compute SHA-256 digest over file bytes."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"File '{file_path}' not found for SHA-256 calculation.")
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()
