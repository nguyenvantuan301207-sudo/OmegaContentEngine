"""Database-free publisher safety checks for P16-B.2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omega.application.publisher.adapters.youtube import YouTubeDataApiAdapter
from omega.config import Settings
from omega.domain.network import NetworkEgressPermit, ServiceCategory
from omega.domain.publisher import PrivacyStatus


def _wrong_destination_permit() -> NetworkEgressPermit:
    return NetworkEgressPermit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://example.invalid",
        service_category=ServiceCategory.YOUTUBE_API,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def test_private_canary_mode_defaults_enabled(monkeypatch) -> None:
    monkeypatch.delenv("PUBLISHER_PRIVATE_CANARY_MODE", raising=False)
    assert Settings().publisher_private_canary_mode is True


def test_permit_path_binding_uses_a_segment_boundary() -> None:
    permit = NetworkEgressPermit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://www.googleapis.com/upload/youtube/v3/videos",
        service_category=ServiceCategory.YOUTUBE_API,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert permit.is_valid_for(
        "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test"
    )
    assert not permit.is_valid_for("https://www.googleapis.com/upload/youtube/v3/videos-evil")


@pytest.mark.asyncio
async def test_wrong_destination_permit_blocks_every_adapter_socket(monkeypatch) -> None:
    adapter = YouTubeDataApiAdapter()
    permit = _wrong_destination_permit()
    mock_get = AsyncMock()
    mock_post = AsyncMock()
    mock_put = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)
    monkeypatch.setattr("httpx.AsyncClient.post", mock_post)
    monkeypatch.setattr("httpx.AsyncClient.put", mock_put)

    validation = await adapter.validate_credentials("token", permit)
    assert validation.is_valid is False
    with pytest.raises(RuntimeError, match="another destination"):
        await adapter.refresh_access_token("refresh", "client", "secret", permit)
    with pytest.raises(RuntimeError, match="another destination"):
        await adapter.initialize_resumable_upload(
            title="Test",
            description="",
            tags=[],
            category_id="28",
            requested_privacy=PrivacyStatus.PRIVATE,
            made_for_kids=False,
            total_bytes=1,
            access_token="token",
            permit=permit,
        )
    session_uri = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test"
    with pytest.raises(RuntimeError, match="another destination"):
        await adapter.upload_chunk(session_uri, b"x", 0, 1, permit)
    with pytest.raises(RuntimeError, match="another destination"):
        await adapter.query_upload_progress(session_uri, 1, permit)
    with pytest.raises(RuntimeError, match="another destination"):
        await adapter.reconcile_upload_session(session_uri, 1, permit)

    mock_get.assert_not_called()
    mock_post.assert_not_called()
    mock_put.assert_not_called()
