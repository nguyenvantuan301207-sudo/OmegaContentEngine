import os
from typing import Any

from omega.application.brand_asset_resolver import BrandAssetResolver
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import get_narration_provider
from omega.application.render_service import ProductionRenderService
from omega.application.visual_asset_engine import VisualAssetEngine
from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator
from omega.application.visual_production_v2_service import VisualProductionV2Service
from omega.infrastructure.pexels_asset_provider import PexelsAssetProvider
from omega.infrastructure.visual_asset_cache import VisualAssetCache


class ProductionVisualV2Adapter:
    """Lazy adapter for Visual V2 execution."""

    def __init__(self, storage: LocalMediaStorageProvider):
        self.storage = storage

    async def render_mission_execution(
        self,
        *args: Any,
        **kwargs: Any
    ) -> Any:
        api_key = os.environ.get("PEXELS_API_KEY", "").strip()
        if not api_key:
            raise ValueError("PEXELS_API_KEY missing for Visual V2 production")

        cache_root = self.storage.base_root / "visual_asset_cache"
        cache = VisualAssetCache(root=cache_root)

        pexels_provider = PexelsAssetProvider(
            api_key=api_key,
            cache=cache,
        )

        engine = VisualAssetEngine()
        orchestrator = VisualAssetOrchestrator(
            engine=engine,
            providers=[pexels_provider],
        )

        narration_provider = get_narration_provider(self.storage)

        v2_service = VisualProductionV2Service(
            asset_orchestrator=orchestrator,
            output_root=self.storage.base_root / "visual_v2",
            narration_provider=narration_provider,
            narration_storage=self.storage,
            brand_asset_resolver=BrandAssetResolver(self.storage),
        )

        try:
            return await v2_service.render_mission_execution(*args, **kwargs)
        finally:
            await pexels_provider.close()


def build_production_render_service(
    storage: LocalMediaStorageProvider | None = None,
) -> ProductionRenderService:
    if storage is None:
        storage = LocalMediaStorageProvider()

    adapter = ProductionVisualV2Adapter(storage=storage)

    return ProductionRenderService(
        storage=storage,
        visual_production_service=adapter,
    )
