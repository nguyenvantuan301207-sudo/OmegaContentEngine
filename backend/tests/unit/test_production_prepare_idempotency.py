"""Focused preparation retries and fail-closed lifecycle coverage."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from omega.api.production import _get_production_service, router
from omega.application.production_service import ProductionService, ProductionStateError
from omega.application.scene_composition import SceneCompositionEngine
from omega.application.storyboard_engine import StoryboardEngine, VisualStrategy
from omega.application.visual_production_v2_service import ScriptStoryboardAdapter
from omega.infrastructure.database import get_async_session
from omega.infrastructure.models import AssetRequirement, ProductionScene, RenderPlan
from tests.unit.test_p18c2_canonical_v2_cutover import _ScalarResult, _script_fixture


def setup_prepare():
    request = SimpleNamespace(
        id=uuid4(),
        channel_id=uuid4(),
        script_version=_script_fixture(),
        scenes=[],
        render_plans=[],
        status="DRAFT",
        target_width=1920,
        target_height=1080,
        fps=30,
        video_codec="h264",
        audio_codec="aac",
        container_format="mp4",
        script_version_id=uuid4(),
        content_request_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        mode="INTERACTIVE",
        created_at=datetime.now(UTC),
        metadata_={},
    )
    rows = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=_ScalarResult(request))
    session.get = AsyncMock(return_value=None)
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    def add(row):
        rows.append(row)
        if isinstance(row, ProductionScene):
            request.scenes.append(row)
        elif isinstance(row, RenderPlan):
            request.render_plans.append(row)

    session.add.side_effect = add
    service = object.__new__(ProductionService)
    service.storyboard_engine = StoryboardEngine()
    storyboard = service.storyboard_engine.generate_storyboard(
        ScriptStoryboardAdapter.to_script_dict(request.script_version)
    )
    # Include an asset-backed scene so retries also cover requirement identity.
    storyboard.scenes[0].visual_strategy = VisualStrategy.IMAGE
    service.storyboard_engine.generate_storyboard = MagicMock(return_value=storyboard)
    service.scene_composition_engine = SceneCompositionEngine()
    return service, session, request, rows


@pytest.mark.asyncio
async def test_prepare_twice_preserves_all_planning_rows():
    service, session, request, rows = setup_prepare()
    first = await service.prepare_production(session, request.channel_id, request.id)
    assert first.status == "READY"
    assert len(request.scenes) == 3
    assert len(request.render_plans) == 1
    assert request.render_plans[0].version == 1
    assert any(isinstance(row, AssetRequirement) for row in rows)
    original_rows = list(rows)
    scene_ids = [scene.id for scene in request.scenes]
    plan_id = request.render_plans[0].id
    service.storyboard_engine.generate_storyboard = MagicMock(
        side_effect=AssertionError("Retry rebuilt storyboard")
    )
    second = await service.prepare_production(session, request.channel_id, request.id)
    assert second is first
    assert second.status == "READY"
    assert rows == original_rows
    assert [scene.id for scene in request.scenes] == scene_ids
    assert request.render_plans[0].id == plan_id
    assert request.render_plans[0].version == 1
    assert all(isinstance(row, (ProductionScene, AssetRequirement, RenderPlan)) for row in rows)
    session.delete.assert_not_called()
    statement = session.execute.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert statement.get_execution_options()["populate_existing"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"])
async def test_active_terminal_prepare_preserves_plan(state):
    service, session, request, rows = setup_prepare()
    await service.prepare_production(session, request.channel_id, request.id)
    request.status = state
    original = list(rows)
    with pytest.raises(ProductionStateError, match=state):
        await service.prepare_production(session, request.channel_id, request.id)
    assert rows == original
    assert request.status == state
    session.delete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    ["missing_plan", "missing_scenes", "manifest", "profile", "draft_plan", "draft_scenes"],
)
async def test_inconsistent_preparation_fails_closed(damage):
    service, session, request, rows = setup_prepare()
    await service.prepare_production(session, request.channel_id, request.id)
    if damage == "missing_plan":
        request.render_plans = []
    elif damage == "missing_scenes":
        request.scenes = []
    elif damage == "manifest":
        request.render_plans[0].scene_manifest = []
    elif damage == "profile":
        request.render_plans[0].width = 1280
    elif damage == "draft_plan":
        request.status = "DRAFT"
        request.scenes = []
    else:
        request.status = "DRAFT"
        request.render_plans = []
    original = list(rows)
    with pytest.raises(ProductionStateError):
        await service.prepare_production(session, request.channel_id, request.id)
    assert rows == original
    session.delete.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_http_retry_and_state_conflict():
    service, session, request, rows = setup_prepare()
    application = FastAPI()
    application.include_router(router)

    async def session_dependency():
        yield session

    application.dependency_overrides[get_async_session] = session_dependency
    application.dependency_overrides[_get_production_service] = lambda: service
    path = f"/api/v1/channels/{request.channel_id}/production/{request.id}/prepare"
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        first = await client.post(path)
        second = await client.post(path)
        assert first.status_code == second.status_code == 200
        assert first.json()["status"] == second.json()["status"] == "READY"
        request.status = "RUNNING"
        blocked = await client.post(path)
        assert blocked.status_code == 409
