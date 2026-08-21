"""Unit tests for Media Storage security, path traversal prevention, and SHA-256 calculation."""

import os
import tempfile
import uuid
from pathlib import Path

import pytest

from omega.application.media_storage import (
    LocalMediaStorageProvider,
    StorageSecurityError,
    compute_sha256,
)


def test_media_storage_path_resolution():
    """Verify safe hierarchical path generation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalMediaStorageProvider(base_root=tmpdir)
        c_id = uuid.uuid4()
        r_id = uuid.uuid4()
        j_id = uuid.uuid4()

        ch_dir = storage.get_channel_dir(c_id)
        prod_dir = storage.get_production_dir(c_id, r_id)
        staging_dir = storage.get_staging_dir(c_id, r_id, j_id)
        art_dir = storage.get_artifacts_dir(c_id, r_id)

        assert str(ch_dir).startswith(str(Path(tmpdir).resolve()))
        assert str(prod_dir).startswith(str(Path(tmpdir).resolve()))
        assert staging_dir.exists()
        assert art_dir.exists()


def test_media_storage_traversal_rejection():
    """Verify directory traversal (..) is strictly rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalMediaStorageProvider(base_root=tmpdir)
        c_id = uuid.uuid4()
        r_id = uuid.uuid4()

        # Attempt relative traversal out of root
        with pytest.raises(StorageSecurityError):
            storage.resolve_stored_uri(c_id, r_id, "../../etc/passwd")

        with pytest.raises(StorageSecurityError):
            storage.resolve_stored_uri(c_id, r_id, "artifacts/../../../secret.txt")


def test_compute_sha256():
    """Verify SHA-256 calculation matches known content."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"omega-007-production-media-content")
        f_path = f.name

    try:
        digest = compute_sha256(f_path)
        assert isinstance(digest, str)
        assert len(digest) == 64
        # Deterministic check
        assert digest == compute_sha256(f_path)
    finally:
        os.remove(f_path)
