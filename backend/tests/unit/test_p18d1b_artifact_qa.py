from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from omega.api.production import get_artifact_qa_result


@pytest.mark.asyncio
async def test_artifact_qa_returns_only_the_exact_scoped_result():
    channel_id = uuid4()
    request_id = uuid4()
    artifact_id = uuid4()
    qa = SimpleNamespace(
        id=uuid4(),
        production_request_id=request_id,
        artifact_id=artifact_id,
        status="PASSED",
        findings=[],
    )
    result = SimpleNamespace(scalar_one_or_none=lambda: qa)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    response = await get_artifact_qa_result(
        channel_id, request_id, artifact_id, session
    )

    assert response is qa
    statement = session.execute.await_args.args[0]
    params = statement.compile().params.values()
    assert channel_id in params
    assert request_id in params
    assert artifact_id in params
    sql = str(statement)
    assert "production_qa_results.artifact_id" in sql
    assert "production_qa_results.production_request_id" in sql
    assert "media_artifacts.production_request_id" in sql
    assert "production_requests.channel_id" in sql


@pytest.mark.asyncio
async def test_artifact_qa_missing_is_explicit_and_never_falls_back():
    result = SimpleNamespace(scalar_one_or_none=lambda: None)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    with pytest.raises(HTTPException) as exc_info:
        await get_artifact_qa_result(uuid4(), uuid4(), uuid4(), session)

    assert exc_info.value.status_code == 404
    assert "channel/request/artifact" in str(exc_info.value.detail)
