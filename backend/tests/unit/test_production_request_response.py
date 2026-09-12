"""Regression coverage for canonical and legacy-readable production statuses."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from omega.domain.content import ContentGenerationRequestResponse, ContentRequestStatus
from omega.domain.production import ProductionRequestResponse, ProductionRequestStatus
from omega.infrastructure.database import get_async_session
from omega.main import app


def _production_request(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        channel_id=uuid4(),
        script_version_id=uuid4(),
        content_request_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        mission_execution_id=None,
        mode="INTERACTIVE",
        status=status,
        outcome=None,
        target_width=1920,
        target_height=1080,
        fps=30,
        video_codec="h264",
        audio_codec="aac",
        container_format="mp4",
        created_at=datetime.now(UTC),
        started_at=None,
        completed_at=None,
        failed_at=None,
        metadata_={},
    )


class _ScalarResult:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._rows)


class _ReadOnlySession:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    async def execute(self, _statement: Any) -> _ExecuteResult:
        return _ExecuteResult(self._rows)


def test_canonical_production_statuses_are_unchanged() -> None:
    assert {status.value for status in ProductionRequestStatus} == {
        "DRAFT",
        "READY",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
    }

    for status in ProductionRequestStatus:
        response = ProductionRequestResponse.model_validate(_production_request(status.value))
        assert response.status == status


def test_legacy_approved_status_is_readable_but_not_canonical() -> None:
    response = ProductionRequestResponse.model_validate(_production_request("APPROVED"))

    assert response.status == "APPROVED"
    assert "APPROVED" not in ProductionRequestStatus


def test_unknown_production_status_remains_rejected() -> None:
    with pytest.raises(ValidationError):
        ProductionRequestResponse.model_validate(_production_request("UNKNOWN_LEGACY_STATUS"))


@pytest.mark.asyncio
async def test_list_endpoint_serializes_legacy_approved_status() -> None:
    channel_id = uuid4()
    row = _production_request("APPROVED")
    row.channel_id = channel_id
    session = _ReadOnlySession([row])

    async def override_session() -> AsyncIterator[_ReadOnlySession]:
        yield session

    app.dependency_overrides[get_async_session] = override_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/channels/{channel_id}/production")
    finally:
        app.dependency_overrides.pop(get_async_session, None)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert UUID(payload[0]["id"]) == row.id
    assert payload[0]["status"] == "APPROVED"


def _content_request(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        channel_id=uuid4(),
        topic_candidate_id=uuid4(),
        research_brief_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        mission_execution_id=None,
        mode="INTERACTIVE",
        status=status,
        outcome=None,
        content_type="YOUTUBE_LONGFORM",
        target_duration_seconds=300,
        target_word_count=700,
        language="en",
        region="US",
        creative_direction=None,
        created_at=datetime.now(UTC),
        started_at=None,
        completed_at=None,
        failed_at=None,
    )


def test_canonical_content_statuses_are_unchanged() -> None:
    assert {status.value for status in ContentRequestStatus} == {
        "DRAFT",
        "READY",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
    }

    for status in ContentRequestStatus:
        response = ContentGenerationRequestResponse.model_validate(_content_request(status.value))
        assert response.status == status


def test_legacy_approved_content_status_is_readable_but_not_canonical() -> None:
    response = ContentGenerationRequestResponse.model_validate(_content_request("APPROVED"))

    assert response.status == "APPROVED"
    assert "APPROVED" not in ContentRequestStatus


def test_unknown_content_status_remains_rejected() -> None:
    with pytest.raises(ValidationError):
        ContentGenerationRequestResponse.model_validate(_content_request("UNKNOWN_LEGACY_STATUS"))


@pytest.mark.asyncio
async def test_content_list_endpoint_serializes_legacy_approved_status() -> None:
    channel_id = uuid4()
    row = _content_request("APPROVED")
    row.channel_id = channel_id
    session = _ReadOnlySession([row])

    async def override_session() -> AsyncIterator[_ReadOnlySession]:
        yield session

    app.dependency_overrides[get_async_session] = override_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/channels/{channel_id}/content")
    finally:
        app.dependency_overrides.pop(get_async_session, None)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert UUID(payload[0]["id"]) == row.id
    assert payload[0]["status"] == "APPROVED"
