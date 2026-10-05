"""Creation authority checks without database or runtime mutations."""

import inspect
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from omega.api.production import _get_production_service, router
from omega.application import content_service
from omega.application.production_service import ProductionLineageError, ProductionService
from omega.domain.production import ProductionRequestCreate
from omega.infrastructure.database import get_async_session
from omega.infrastructure.models import ContentGenerationRequest, ContentQAResult, ScriptVersion


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


def fixture():
    content = ContentGenerationRequest(
        id=uuid4(), channel_id=uuid4(), channel_dna_revision_id=uuid4()
    )
    script = ScriptVersion(
        id=uuid4(),
        content_request_id=content.id,
        content_request=content,
        is_current=True,
        qa_status="PASSED",
    )
    qa = ContentQAResult(id=uuid4(), script_version_id=script.id, status="PASSED")
    current_ids = [script.id]
    session = MagicMock()
    statements = []

    def execute(statement):
        statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        if entity is ContentGenerationRequest:
            return Result(content)
        if entity is ContentQAResult:
            return Result(qa if qa.status is not None else None)
        if statement.column_descriptions[0]["name"] == "id":
            return Result(current_ids)
        return Result(script)

    session.execute = AsyncMock(side_effect=execute)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    service = object.__new__(ProductionService)
    payload = ProductionRequestCreate(script_version_id=script.id)
    return service, session, content, script, qa, current_ids, statements, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PASSED", "PASSED_WITH_WARNINGS"])
async def test_current_eligible_creation_pins_draft(status):
    service, session, content, script, qa, ids, statements, payload = fixture()
    script.qa_status = qa.status = status
    request = await service.create_production_request(session, content.channel_id, payload)
    assert request.status == "DRAFT"
    assert request.script_version_id == script.id
    assert request.content_request_id == content.id
    assert request.channel_dna_revision_id == content.channel_dna_revision_id
    session.add.assert_called_once_with(request)
    # Parent lock matches regeneration's order, and script is refreshed under lock.
    assert "FOR UPDATE" in str(statements[1].compile(dialect=postgresql.dialect()))
    assert statements[1].column_descriptions[0]["entity"] is ContentGenerationRequest
    assert "FOR UPDATE" in str(statements[2].compile(dialect=postgresql.dialect()))
    assert statements[2].get_execution_options()["populate_existing"] is True
    writer = inspect.getsource(content_service.generate_content)
    assert writer.index("select(ContentGenerationRequest)") < writer.index("s.is_current = False")
    assert ".with_for_update()" in writer[: writer.index("s.is_current = False")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "historical",
        "missing_qa",
        "PENDING",
        "BLOCKED",
        "FAILED",
        "mismatch",
        "multiple_current",
        "no_current",
        "cross_channel",
    ],
)
async def test_invalid_authority_never_inserts(damage):
    service, session, content, script, qa, ids, statements, payload = fixture()
    channel_id = content.channel_id
    if damage == "historical":
        script.is_current = False
    elif damage == "missing_qa":
        qa.status = None
    elif damage in ("PENDING", "BLOCKED", "FAILED"):
        qa.status = script.qa_status = damage
    elif damage == "mismatch":
        qa.status = "PASSED_WITH_WARNINGS"
    elif damage == "multiple_current":
        ids.append(uuid4())
    elif damage == "no_current":
        ids.clear()
    else:
        channel_id = uuid4()
    with pytest.raises(ProductionLineageError):
        await service.create_production_request(session, channel_id, payload)
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_post_lock_refresh_rejects_superseded_script():
    service, session, content, script, qa, ids, statements, payload = fixture()
    execute = session.execute.side_effect

    def competing_switch(statement):
        if (
            statement.column_descriptions[0]["entity"] is ScriptVersion
            and statement._for_update_arg is not None
        ):
            script.is_current = False
            ids[:] = [uuid4()]
        return execute(statement)

    session.execute.side_effect = competing_switch
    with pytest.raises(ProductionLineageError):
        await service.create_production_request(session, content.channel_id, payload)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_idempotent_replay_preserves_historical_request():
    service, session, content, script, qa, ids, statements, payload = fixture()
    existing = object()
    script.is_current = False
    session.execute = AsyncMock(return_value=Result(existing))
    assert (
        await service.create_production_request(
            session, content.channel_id, payload, "existing-key"
        )
        is existing
    )
    session.execute.assert_awaited_once()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_api_rejects_authority_with_client_error():
    service, session, content, script, qa, ids, statements, payload = fixture()
    qa.status = None
    application = FastAPI()
    application.include_router(router)

    async def dependency():
        yield session

    application.dependency_overrides[get_async_session] = dependency
    application.dependency_overrides[_get_production_service] = lambda: service
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/channels/{content.channel_id}/production",
            json={"script_version_id": str(script.id)},
        )
    assert response.status_code == 400
    assert "ContentQAResult" in response.json()["detail"]
    session.add.assert_not_called()
