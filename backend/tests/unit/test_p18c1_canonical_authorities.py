import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import (
    LocalTTSNarrationProvider,
    NarrationProviderError,
    get_narration_provider,
)
from omega.application.production_contract import (
    CanonicalProductionContract,
    CanonicalProductionLineage,
    CanonicalProductionPolicy,
)
from omega.application.production_render_factory import ProductionVisualV2Adapter
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.domain.production import (
    NarrationProviderType,
    ProductionMode,
    SubtitleFallbackPolicy,
    SubtitleMode,
    VisualAssetMode,
)


def _contract(visual_asset_mode: VisualAssetMode):
    ids = [uuid.uuid4() for _ in range(5)]
    return CanonicalProductionContract(
        mode=ProductionMode.INTERACTIVE,
        lineage=CanonicalProductionLineage(
            channel_id=ids[0],
            production_request_id=ids[1],
            content_request_id=ids[2],
            script_version_id=ids[3],
            channel_dna_revision_id=ids[4],
        ),
        policy=CanonicalProductionPolicy(
            visual_asset_mode=visual_asset_mode,
            narration_provider=NarrationProviderType.LOCAL_TTS,
            subtitle_mode=SubtitleMode.OFF,
            subtitle_fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
            subtitle_style=SubtitleRenderStyle(),
        ),
    )


@pytest.mark.asyncio
async def test_factory_local_contract_ignores_pexels_environment(tmp_path, monkeypatch):
    contract = _contract(VisualAssetMode.LOCAL_TEMPLATE_ONLY)
    storage = LocalMediaStorageProvider(base_root=tmp_path)
    adapter = ProductionVisualV2Adapter(storage)
    monkeypatch.setattr(adapter, "visual_asset_mode", "PEXELS")
    monkeypatch.setenv("PEXELS_API_KEY", "must-not-be-read")
    rendered = AsyncMock(return_value="rendered")

    with (
        patch("omega.application.production_render_factory.PexelsAssetProvider") as pexels,
        patch(
            "omega.application.production_render_factory.get_narration_provider",
            return_value=MagicMock(),
        ) as narration_factory,
        patch(
            "omega.application.production_render_factory.VisualProductionV2Service"
        ) as service_type,
    ):
        service_type.return_value.render_canonical_production = rendered
        result = await adapter.render_canonical_production(
            AsyncMock(), contract.lineage.production_request_id, contract=contract
        )

    assert result == "rendered"
    pexels.assert_not_called()
    narration_factory.assert_called_once_with(storage, "LOCAL_TTS")
    assert service_type.call_args.kwargs["visual_asset_mode"] == ("LOCAL_TEMPLATE_ONLY")


@pytest.mark.asyncio
async def test_factory_pexels_contract_without_capability_fails_closed(tmp_path, monkeypatch):
    contract = _contract(VisualAssetMode.PEXELS)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    adapter = ProductionVisualV2Adapter(LocalMediaStorageProvider(base_root=tmp_path))

    with pytest.raises(ValueError, match="PEXELS_API_KEY missing"):
        await adapter.render_canonical_production(
            AsyncMock(), contract.lineage.production_request_id, contract=contract
        )


def test_explicit_local_provider_maps_deterministically(tmp_path):
    storage = LocalMediaStorageProvider(base_root=tmp_path)

    provider = get_narration_provider(storage, "LOCAL_TTS")

    assert isinstance(provider, LocalTTSNarrationProvider)


def test_unknown_explicit_provider_fails_closed(tmp_path):
    storage = LocalMediaStorageProvider(base_root=tmp_path)

    with pytest.raises(NarrationProviderError, match="Unsupported explicit"):
        get_narration_provider(storage, "unknown-provider")


def test_explicit_cloud_provider_maps_without_network_call(tmp_path, monkeypatch):
    storage = LocalMediaStorageProvider(base_root=tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-construction-only")

    with patch(
        "omega.application.narration_provider.NeuralTTSNarrationProvider",
        return_value=MagicMock(),
    ) as provider_type:
        selected = get_narration_provider(storage, "NEURAL")

    assert selected is provider_type.return_value
    provider_type.assert_called_once_with(storage)


def test_omitted_provider_preserves_legacy_default(tmp_path, monkeypatch):
    storage = LocalMediaStorageProvider(base_root=tmp_path)
    monkeypatch.delenv("TTS_PROVIDER", raising=False)

    provider = get_narration_provider(storage)

    assert isinstance(provider, LocalTTSNarrationProvider)
