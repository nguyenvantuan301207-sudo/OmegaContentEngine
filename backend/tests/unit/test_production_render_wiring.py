from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from omega.api import production
from omega.application.brand_asset_resolver import BrandAssetResolver
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.production_render_factory import (
    ProductionVisualV2Adapter,
    build_production_render_service,
)
from omega.application.render_service import ProductionRenderService
from omega.infrastructure.models import ProductionRequest
from omega.worker import tasks


def test_factory_missing_pexels_api_key(monkeypatch):
    monkeypatch.delenv("OMEGA_VISUAL_ASSET_MODE", raising=False)
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


def test_factory_unknown_visual_asset_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("OMEGA_VISUAL_ASSET_MODE", "UNKNOWN")

    with pytest.raises(ValueError, match="Unsupported OMEGA_VISUAL_ASSET_MODE"):
        build_production_render_service()


@pytest.mark.asyncio
async def test_lazy_adapter_missing_api_key(monkeypatch):
    monkeypatch.delenv("OMEGA_VISUAL_ASSET_MODE", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    storage = LocalMediaStorageProvider()
    adapter = ProductionVisualV2Adapter(storage=storage)

    # 4. Lazy adapter with missing PEXELS_API_KEY raises
    with pytest.raises(ValueError, match="PEXELS_API_KEY missing for Visual V2 production"):
        await adapter.render_mission_execution()


@pytest.mark.asyncio
async def test_lazy_adapter_successful_wiring(monkeypatch, tmp_path):
    monkeypatch.setenv("OMEGA_VISUAL_ASSET_MODE", "PEXELS")
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
    mock_v2_cls.assert_called_once()
    v2_kwargs = mock_v2_cls.call_args.kwargs
    assert v2_kwargs["asset_orchestrator"] is mock_orchestrator_cls.return_value
    assert v2_kwargs["visual_asset_mode"] == "PEXELS"
    assert v2_kwargs["output_root"] == storage.base_root / "visual_v2"
    assert v2_kwargs["narration_provider"] is mock_get_narration.return_value
    assert v2_kwargs["narration_storage"] is storage
    brand_resolver = v2_kwargs["brand_asset_resolver"]
    assert isinstance(brand_resolver, BrandAssetResolver)
    assert brand_resolver._storage is storage

    # Downstream render call
    mock_v2_service.render_mission_execution.assert_called_once_with("arg1", kwarg1="val1")

    # Result returned unchanged
    assert result == "v2_result"

    # 6. Provider closed on success
    mock_provider.close.assert_called_once()


@pytest.mark.asyncio
async def test_lazy_adapter_local_template_only_skips_pexels(monkeypatch, tmp_path):
    monkeypatch.setenv("OMEGA_VISUAL_ASSET_MODE", "LOCAL_TEMPLATE_ONLY")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)

    mock_pexels_cls = MagicMock()
    mock_orchestrator_cls = MagicMock()
    mock_v2_cls = MagicMock()
    mock_v2_service = AsyncMock()
    mock_v2_service.render_mission_execution.return_value = "local_result"
    mock_v2_cls.return_value = mock_v2_service
    monkeypatch.setattr(
        "omega.application.production_render_factory.PexelsAssetProvider",
        mock_pexels_cls,
    )
    monkeypatch.setattr(
        "omega.application.production_render_factory.VisualAssetOrchestrator",
        mock_orchestrator_cls,
    )
    monkeypatch.setattr(
        "omega.application.production_render_factory.VisualProductionV2Service",
        mock_v2_cls,
    )
    monkeypatch.setattr(
        "omega.application.production_render_factory.get_narration_provider",
        MagicMock(),
    )

    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    result = await ProductionVisualV2Adapter(storage).render_mission_execution()

    assert result == "local_result"
    mock_pexels_cls.assert_not_called()
    mock_orchestrator_cls.assert_not_called()
    v2_kwargs = mock_v2_cls.call_args.kwargs
    assert v2_kwargs["asset_orchestrator"] is None
    assert v2_kwargs["visual_asset_mode"] == "LOCAL_TEMPLATE_ONLY"


@pytest.mark.asyncio
async def test_lazy_adapter_provider_close_on_exception(monkeypatch):
    monkeypatch.delenv("OMEGA_VISUAL_ASSET_MODE", raising=False)
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


@pytest.mark.parametrize(
    ("endpoint", "is_rerender"),
    [
        (production.render_production, False),
        (production.rerender_production, True),
    ],
)
@pytest.mark.asyncio
async def test_new_render_job_is_allocated_and_published_once(
    monkeypatch,
    endpoint,
    is_rerender,
):
    channel_id = uuid4()
    request_id = uuid4()
    job = SimpleNamespace(id=uuid4(), state="QUEUED")
    prod_service = SimpleNamespace(
        allocate_render_job=AsyncMock(return_value=(job, object(), True))
    )
    publish = MagicMock()
    monkeypatch.setattr(production.execute_production_render_task, "delay", publish)

    result = await endpoint(
        channel_id=channel_id,
        request_id=request_id,
        payload=SimpleNamespace(idempotency_key="render-key"),
        session=MagicMock(),
        prod_service=prod_service,
    )

    assert result is job
    assert result.state == "QUEUED"
    prod_service.allocate_render_job.assert_awaited_once_with(
        session=prod_service.allocate_render_job.await_args.kwargs["session"],
        channel_id=channel_id,
        request_id=request_id,
        idempotency_key="render-key",
        is_rerender=is_rerender,
    )
    publish.assert_called_once_with(str(channel_id), str(request_id), str(job.id))


@pytest.mark.asyncio
async def test_idempotent_render_replay_does_not_publish(monkeypatch):
    job = SimpleNamespace(id=uuid4(), state="QUEUED")
    prod_service = SimpleNamespace(
        allocate_render_job=AsyncMock(return_value=(job, object(), False))
    )
    publish = MagicMock()
    monkeypatch.setattr(production.execute_production_render_task, "delay", publish)

    result = await production.render_production(
        channel_id=uuid4(),
        request_id=uuid4(),
        payload=SimpleNamespace(idempotency_key="existing-key"),
        session=MagicMock(),
        prod_service=prod_service,
    )

    assert result is job
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_publication_failure_is_not_silently_converted_to_success(monkeypatch):
    job = SimpleNamespace(id=uuid4(), state="QUEUED")
    prod_service = SimpleNamespace(
        allocate_render_job=AsyncMock(return_value=(job, object(), True))
    )
    monkeypatch.setattr(
        production.execute_production_render_task,
        "delay",
        MagicMock(side_effect=RuntimeError("broker unavailable")),
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        await production.render_production(
            channel_id=uuid4(),
            request_id=uuid4(),
            payload=SimpleNamespace(idempotency_key="render-key"),
            session=MagicMock(),
            prod_service=prod_service,
        )


class _WorkerSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_worker_render_task_uses_async_worker_session_and_existing_service(monkeypatch):
    channel_id = uuid4()
    request_id = uuid4()
    job_id = uuid4()
    artifact = SimpleNamespace(id=uuid4())
    qa_status = SimpleNamespace(value="PASSED")
    session = object()
    session_factory = MagicMock(return_value=_WorkerSessionContext(session))
    render_service = SimpleNamespace(
        execute_render_job=AsyncMock(return_value=(artifact, qa_status))
    )
    service_factory = MagicMock(return_value=render_service)

    monkeypatch.setattr(
        "omega.infrastructure.database.AsyncWorkerSessionLocal",
        session_factory,
    )
    monkeypatch.setattr(
        "omega.application.production_render_factory.build_production_render_service",
        service_factory,
    )

    result = tasks.execute_production_render_task.run(
        str(channel_id),
        str(request_id),
        str(job_id),
    )

    session_factory.assert_called_once_with()
    service_factory.assert_called_once_with()
    render_service.execute_render_job.assert_awaited_once_with(
        session,
        channel_id,
        request_id,
        job_id,
    )
    assert result == {
        "status": "success",
        "artifact_id": str(artifact.id),
        "qa_status": "PASSED",
    }


def test_render_task_has_no_celery_retry_policy():
    assert tasks.execute_production_render_task.name == "omega.production.render"
    assert getattr(tasks.execute_production_render_task, "autoretry_for", ()) == ()
