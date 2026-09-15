import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from omega.application.production_service import ProductionService
from omega.application.render_service import ProductionRenderService
from omega.application.scene_composition import SceneCompositionEngine
from omega.application.storyboard_engine import StoryboardEngine
from omega.domain.production import ProductionRequestCreate, ProductionRequestStatus
from omega.infrastructure.models import (
    NarrationSegment,
    ProductionAsset,
    ProductionScene,
    RenderPlan,
    SubtitleCue,
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def _script_fixture():
    statement = SimpleNamespace(
        id=uuid4(),
        statement_order=1,
        statement_text="The canonical body remains ordered.",
        statement_type="BODY",
        citations=[],
    )
    section = SimpleNamespace(
        id=uuid4(),
        section_order=1,
        heading="Body",
        narration_text=statement.statement_text,
        estimated_duration_seconds=4.0,
        statements=[statement],
    )
    return SimpleNamespace(
        id=uuid4(),
        title="Canonical cutover",
        hook_text="A deterministic hook.",
        closing_text="A deterministic close.",
        cta_text="Subscribe once.",
        estimated_duration_seconds=12.0,
        sections=[section],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["INTERACTIVE", "MISSION_EXECUTION"])
async def test_prepare_is_canonical_planning_only(mode):
    script = _script_fixture()
    request = SimpleNamespace(
        id=uuid4(),
        channel_id=uuid4(),
        mode=mode,
        script_version=script,
        scenes=[],
        target_width=1920,
        target_height=1080,
        fps=30,
        video_codec="h264",
        audio_codec="aac",
        container_format="mp4",
        status="DRAFT",
    )
    added = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=_ScalarResult(request))
    session.get = AsyncMock(return_value=None)
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add.side_effect = added.append

    service = object.__new__(ProductionService)
    service.storyboard_engine = StoryboardEngine()
    service.scene_composition_engine = SceneCompositionEngine()

    result = await service.prepare_production(
        session,
        request.channel_id,
        request.id,
    )

    planned_scenes = [item for item in added if isinstance(item, ProductionScene)]
    plans = [item for item in added if isinstance(item, RenderPlan)]
    narration = " ".join(scene.narration_text for scene in planned_scenes)

    assert result is request
    assert request.status == ProductionRequestStatus.READY.value
    assert [scene.scene_order for scene in planned_scenes] == [1, 2, 3]
    assert narration.count(script.hook_text) == 1
    assert narration.count(script.sections[0].statements[0].statement_text) == 1
    assert narration.count(script.closing_text) == 1
    assert narration.count(script.cta_text) == 1
    assert len(plans) == 1
    assert plans[0].audio_manifest == []
    assert plans[0].subtitle_manifest == []
    assert not any(isinstance(item, ProductionAsset) for item in added)
    assert not any(isinstance(item, NarrationSegment) for item in added)
    assert not any(isinstance(item, SubtitleCue) for item in added)


def test_canonical_v2_routing_is_mode_neutral_and_has_no_legacy_scene_call():
    service = ProductionRenderService(visual_production_service=object())

    assert service._should_use_v2(SimpleNamespace(mode="INTERACTIVE")) is True
    assert service._should_use_v2(SimpleNamespace(mode="MISSION_EXECUTION")) is True
    assert (
        ProductionRenderService(visual_production_service=None)._should_use_v2(
            SimpleNamespace(mode="INTERACTIVE")
        )
        is False
    )

    execute_source = inspect.getsource(ProductionRenderService.execute_render_job)
    assert "_render_synthetic_clip(" not in execute_source
    assert "render_scene_clip(" not in execute_source
    assert "render_canonical_production" in inspect.getsource(
        ProductionRenderService._render_v2_staging
    )


@pytest.mark.parametrize(
    "override, message",
    [
        ({"target_width": 1280, "target_height": 720}, "1920x1080"),
        ({"container_format": "mov"}, "mp4"),
        ({"video_codec": "vp9"}, "h264/libx264"),
    ],
)
def test_create_rejects_unsupported_canonical_target(override, message):
    with pytest.raises(ValidationError, match=message):
        ProductionRequestCreate(script_version_id=uuid4(), **override)
