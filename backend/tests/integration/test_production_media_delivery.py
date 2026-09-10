"""Integration tests for safe media streaming endpoint and HTTP Range (206) support."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.media_storage import LocalMediaStorageProvider
from omega.infrastructure.models import MediaArtifact
from omega.main import app
from tests.integration.test_production_lineage_pinning import _create_test_script


@pytest.mark.asyncio
async def test_media_streaming_and_http_range_support(
    db_session: AsyncSession,
) -> None:
    """Verify media streaming endpoint delivers correct headers and supports Range requests (206)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup Channel & Script
        slug = f"media-{uuid.uuid4().hex[:6]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Media Delivery Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        ch_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{ch_id}/activate")
        _, script_id, _ = await _create_test_script(client, ch_id)

        # Create and prepare the production request, but do not invoke asynchronous rendering.
        p_res = await client.post(
            f"/api/v1/channels/{ch_id}/production", json={"script_version_id": script_id}
        )
        req_id = p_res.json()["id"]
        await client.post(f"/api/v1/channels/{ch_id}/production/{req_id}/prepare")

        channel_uuid = uuid.UUID(ch_id)
        request_uuid = uuid.UUID(req_id)
        media_bytes = bytes(range(128))
        storage = LocalMediaStorageProvider()
        artifact_path = storage.get_artifacts_dir(channel_uuid, request_uuid) / "range-test.mp4"
        artifact_path.write_bytes(media_bytes)
        artifact = MediaArtifact(
            id=uuid.uuid4(),
            production_request_id=request_uuid,
            artifact_type="VIDEO",
            version=1,
            is_current=True,
            storage_uri=storage.to_relative_uri(channel_uuid, request_uuid, artifact_path),
            file_size_bytes=len(media_bytes),
            content_hash=hashlib.sha256(media_bytes).hexdigest(),
            mime_type="video/mp4",
        )
        db_session.add(artifact)
        await db_session.commit()

        arts_res = await client.get(f"/api/v1/channels/{ch_id}/production/{req_id}/artifacts")
        assert arts_res.status_code == 200
        artifacts = arts_res.json()
        assert [item["id"] for item in artifacts] == [str(artifact.id)]

        stream_res = await client.get(
            f"/api/v1/channels/{ch_id}/production/{req_id}/artifacts/{artifact.id}/media"
        )
        assert stream_res.status_code == 200
        assert stream_res.headers["content-type"] == "video/mp4"
        assert stream_res.headers["accept-ranges"] == "bytes"
        assert stream_res.headers["content-length"] == str(len(media_bytes))
        assert stream_res.content == media_bytes

        range_res = await client.get(
            f"/api/v1/channels/{ch_id}/production/{req_id}/artifacts/{artifact.id}/media",
            headers={"Range": "bytes=0-49"},
        )
        assert range_res.status_code == 206
        assert range_res.headers["content-type"] == "video/mp4"
        assert range_res.headers["accept-ranges"] == "bytes"
        assert range_res.headers["content-length"] == "50"
        assert range_res.headers["content-range"] == f"bytes 0-49/{len(media_bytes)}"
        assert range_res.content == media_bytes[:50]
