from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from omega.application.production_service import ProductionService, ProductionStateError
from omega.application.render_capabilities import get_production_render_capabilities
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.main import app


def test_capability_contract_uses_canonical_defaults_and_supported_fields():
    capabilities = get_production_render_capabilities()

    assert capabilities.subtitle.defaults == SubtitleRenderStyle()
    assert {field.name for field in capabilities.subtitle.fields} == set(
        SubtitleRenderStyle.model_fields
    )
    assert all(field.truth_state == "RENDER_APPLIED" for field in capabilities.subtitle.fields)
    assert next(
        field for field in capabilities.subtitle.fields if field.name == "min_font_size"
    ).user_editable is False
    assert capabilities.video.fps_mode == "CFR"
    assert capabilities.video.target_fps == 24
    assert capabilities.video.user_editable is False
    assert capabilities.text_fitting.truncation_provenance is True
    assert capabilities.subtitle_timing_label == "Full-sentence cue timing"


@pytest.mark.asyncio
async def test_render_capability_endpoint_is_read_only_and_typed():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system/render-capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["subtitle"]["defaults"]["font_size"] == 48
    assert payload["video"] == {
        "fps_mode": "CFR",
        "target_fps": 24,
        "user_editable": False,
    }


def test_subtitle_style_rejects_unsupported_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SubtitleRenderStyle.model_validate(
            {**SubtitleRenderStyle().model_dump(), "animation": "bounce"}
        )


@pytest.mark.asyncio
async def test_render_settings_persist_non_default_style_without_schema_change():
    channel_id, request_id = uuid4(), uuid4()
    request = SimpleNamespace(
        id=request_id,
        channel_id=channel_id,
        status="READY",
        metadata_={"existing": "preserved"},
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = request
    session = AsyncMock()
    session.execute.return_value = result
    style = SubtitleRenderStyle(
        font_size=54,
        primary_color="#FFD400",
        outline_color="#101010",
        outline_width=3,
        margin_v=135,
    )

    returned = await ProductionService.update_render_settings(
        object.__new__(ProductionService),
        session,
        channel_id,
        request_id,
        style,
    )

    assert returned is request
    assert request.metadata_["existing"] == "preserved"
    assert request.metadata_["render_settings"]["subtitle_style"] == style.model_dump()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_render_settings_reject_changes_while_rendering():
    request = SimpleNamespace(status="RUNNING", metadata_={})
    result = MagicMock()
    result.scalar_one_or_none.return_value = request
    session = AsyncMock()
    session.execute.return_value = result

    with pytest.raises(ProductionStateError, match="while a render is running"):
        await ProductionService.update_render_settings(
            object.__new__(ProductionService),
            session,
            uuid4(),
            uuid4(),
            SubtitleRenderStyle(),
        )

    session.commit.assert_not_awaited()
