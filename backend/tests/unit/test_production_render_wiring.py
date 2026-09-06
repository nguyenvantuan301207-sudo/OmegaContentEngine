from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.production_render_factory import (
    ProductionVisualV2Adapter,
    build_production_render_service,
)
from omega.application.render_service import ProductionRenderService
from omega.infrastructure.models import ProductionRequest


def test_factory_missing_pexels_api_key(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)

    # 1. Factory construction with PEXELS_API_KEY absent
    service = build_production_render_service()

    assert isinstance(service, ProductionRenderService)
    assert service.visual_production_service is not None
    assert isinstance(service.visual_production_service, ProductionVisualV2Adapter)

def test_factory_shared_storage():
    storage = LocalMediaStorageProvider(base_root="test")
    service = build_production_render_service(storage=storage)

    # 2. Factory uses one shared LocalMediaStorageProvider instance
    assert service.storage is storage
    assert service.visual_production_service.storage is storage

def test_factory_selection_logic():
    service = build_production_render_service()

    # 3. Preserves P6-B selection
    req_interactive = ProductionRequest(mode="INTERACTIVE")
    assert service._should_use_v2(req_interactive) is False

    req_mission = ProductionRequest(mode="MISSION_EXECUTION")
    assert service._should_use_v2(req_mission) is True

@pytest.mark.asyncio
async def test_lazy_adapter_missing_api_key(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    storage = LocalMediaStorageProvider()
    adapter = ProductionVisualV2Adapter(storage=storage)

    # 4. Lazy adapter with missing PEXELS_API_KEY raises
    with pytest.raises(ValueError, match="PEXELS_API_KEY missing for Visual V2 production"):
        await adapter.render_mission_execution()

@pytest.mark.asyncio
async def test_lazy_adapter_successful_wiring(monkeypatch, tmp_path):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    # Mocks
    mock_cache_cls = MagicMock()
    mock_pexels_cls = MagicMock()
    mock_engine_cls = MagicMock()
    mock_orchestrator_cls = MagicMock()
    mock_v2_cls = MagicMock()
    mock_get_narration = MagicMock()

    # Provider instances
    mock_provider = AsyncMock()
    mock_pexels_cls.return_value = mock_provider

    mock_v2_service = AsyncMock()
    mock_v2_service.render_mission_execution.return_value = "v2_result"
    mock_v2_cls.return_value = mock_v2_service

    monkeypatch.setattr("omega.application.production_render_factory.VisualAssetCache", mock_cache_cls)
    monkeypatch.setattr("omega.application.production_render_factory.PexelsAssetProvider", mock_pexels_cls)
    monkeypatch.setattr("omega.application.production_render_factory.VisualAssetEngine", mock_engine_cls)
    monkeypatch.setattr("omega.application.production_render_factory.VisualAssetOrchestrator", mock_orchestrator_cls)
    monkeypatch.setattr("omega.application.production_render_factory.VisualProductionV2Service", mock_v2_cls)
    monkeypatch.setattr("omega.application.production_render_factory.get_narration_provider", mock_get_narration)

    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    adapter = ProductionVisualV2Adapter(storage=storage)

    # Call
    result = await adapter.render_mission_execution("arg1", kwarg1="val1")

    # 5. Assert wiring
    # Cache root
    mock_cache_cls.assert_called_once_with(root=storage.base_root / "visual_asset_cache")

    # Pexels provider
    mock_pexels_cls.assert_called_once_with(
        api_key="test-key",
        cache=mock_cache_cls.return_value,
    )

    # Orchestrator
    mock_orchestrator_cls.assert_called_once_with(
        engine=mock_engine_cls.return_value,
        providers=[mock_provider],
    )

    # V2 service
    mock_v2_cls.assert_called_once_with(
        asset_orchestrator=mock_orchestrator_cls.return_value,
        output_root=storage.base_root / "visual_v2",
        narration_provider=mock_get_narration.return_value,
        narration_storage=storage,
    )

    # Downstream render call
    mock_v2_service.render_mission_execution.assert_called_once_with("arg1", kwarg1="val1")

    # Result returned unchanged
    assert result == "v2_result"

    # 6. Provider closed on success
    mock_provider.close.assert_called_once()

@pytest.mark.asyncio
async def test_lazy_adapter_provider_close_on_exception(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    mock_pexels_cls = MagicMock()
    mock_provider = AsyncMock()
    mock_pexels_cls.return_value = mock_provider
    monkeypatch.setattr("omega.application.production_render_factory.PexelsAssetProvider", mock_pexels_cls)
    monkeypatch.setattr("omega.application.production_render_factory.VisualAssetCache", MagicMock())
    monkeypatch.setattr("omega.application.production_render_factory.VisualAssetEngine", MagicMock())
    monkeypatch.setattr("omega.application.production_render_factory.VisualAssetOrchestrator", MagicMock())
    monkeypatch.setattr("omega.application.production_render_factory.get_narration_provider", MagicMock())

    mock_v2_cls = MagicMock()
    mock_v2_service = AsyncMock()
    mock_v2_service.render_mission_execution.side_effect = RuntimeError("V2 Error")
    mock_v2_cls.return_value = mock_v2_service
    monkeypatch.setattr("omega.application.production_render_factory.VisualProductionV2Service", mock_v2_cls)

    storage = LocalMediaStorageProvider()
    adapter = ProductionVisualV2Adapter(storage=storage)

    # 7. Provider close on downstream Visual V2 exception
    with pytest.raises(RuntimeError, match="V2 Error"):
        await adapter.render_mission_execution()

    mock_provider.close.assert_called_once()


def test_fastapi_render_wiring():
    # 8. FastAPI _get_render_service() returns configured adapter
    from omega.api.production import _get_render_service
    service = _get_render_service()
    assert isinstance(service, ProductionRenderService)
    assert isinstance(service.visual_production_service, ProductionVisualV2Adapter)

def test_celery_render_wiring():
    # 9. Celery production-render service construction

    # Read the tasks file
    tasks_path = Path("src/omega/worker/tasks.py")
    if not tasks_path.exists():
        tasks_path = Path("backend/src/omega/worker/tasks.py") # fallback for local execution

    content = tasks_path.read_text()

    # Verify build_production_render_service is in the file and called
    assert "build_production_render_service()" in content
    assert "from omega.application.production_render_factory import build_production_render_service" in content

def test_api_render_rerender_wiring():
    """Both production render endpoints use the same render-service dependency."""
    from typing import Annotated, get_args, get_origin, get_type_hints

    from fastapi.params import Depends as DependsParam

    from omega.api.production import (
        _get_render_service,
        render_production,
        rerender_production,
    )

    def render_service_dependency(func):
        hints = get_type_hints(func, include_extras=True)
        annotation = hints["render_service"]

        assert get_origin(annotation) is Annotated

        dependencies = [
            metadata
            for metadata in get_args(annotation)[1:]
            if isinstance(metadata, DependsParam)
        ]

        assert len(dependencies) == 1
        return dependencies[0].dependency

    assert render_service_dependency(render_production) is _get_render_service
    assert render_service_dependency(rerender_production) is _get_render_service
