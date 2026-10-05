import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from omega.application.beat_asset_executor import resolve_provider_asset
from omega.application.beat_asset_policy import BeatAssetAction
from omega.application.visual_asset_engine import VisualAssetCandidate, VisualAssetRequest
from omega.application.visual_direction import VisualAssetKind
from omega.domain.production import LicenseStatus
from omega.infrastructure.pexels_asset_provider import PexelsAssetProvider, PexelsAssetProviderError
from omega.infrastructure.video_probe import PhysicalVideoMetadata, VideoProbeError
from omega.infrastructure.visual_asset_cache import VisualAssetCache
from omega.infrastructure.visual_asset_materializer import VisualAssetMaterializer

PROBE = "omega.infrastructure.pexels_asset_provider.probe_video_metadata"
CONTENT = b"\x00\x00\x00\x18ftypmp42exact-downloaded-video"


def candidate(kind=VisualAssetKind.BROLL):
    return VisualAssetCandidate(
        provider_id="123", kind=kind, provider="pexels",
        source_url="https://videos.example.test/selected.mp4", source_page_url="https://example.test/video/123",
        mime_type="video/mp4", width=1920, height=1080, duration_seconds=9.0,
        license_status=LicenseStatus.LICENSED, license_name="Pexels License",
        license_url="https://www.pexels.com/license/", attribution_text="Video by Author on Pexels",
        metadata={"search_query": "server infrastructure", "creator": "Author"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [VisualAssetKind.BROLL, VisualAssetKind.VIDEO])
async def test_fetch_physical_cache_reload_bound_and_unchanged_content(tmp_path, monkeypatch, kind):
    original = candidate(kind)
    before = original.model_dump_json()
    cache = VisualAssetCache(tmp_path / "cache")
    probe = AsyncMock(return_value=PhysicalVideoMetadata(1280, 720, 9.173333))
    monkeypatch.setattr(PROBE, probe)
    store = cache.store

    def physical_store(**kwargs):
        probe.assert_awaited_once_with(CONTENT)
        assert (kwargs["width"], kwargs["height"], kwargs["duration_seconds"]) == (1280, 720, 9.173333)
        return store(**kwargs)

    monkeypatch.setattr(cache, "store", physical_store)

    def respond(request):
        assert str(request.url) == original.source_url
        assert "authorization" not in request.headers
        return httpx.Response(200, content=CONTENT, headers={"Content-Type": "video/mp4"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        resolved = await PexelsAssetProvider("synthetic-key", cache, client).fetch(original)
    for asset in (resolved, cache.get(resolved.content_sha256)):
        assert (asset.width, asset.height) == (1280, 720)
        assert asset.duration_seconds == pytest.approx(9.173333, abs=1e-6)
        assert asset.content_sha256 == hashlib.sha256(CONTENT).hexdigest()
        assert asset.local_path.read_bytes() == CONTENT
        for field in ("provider", "kind", "source_url", "source_page_url", "mime_type", "license_status",
                      "license_name", "license_url", "attribution_text", "metadata"):
            assert getattr(asset, field) == getattr(original, field)
        assert asset.query == original.metadata["search_query"]
    meta = json.loads(resolved.local_path.with_name("metadata.json").read_text())
    assert (meta["width"], meta["height"]) == (1280, 720)
    assert meta["duration_seconds"] == pytest.approx(9.173333, abs=1e-6)
    if kind == VisualAssetKind.BROLL:
        bound = VisualAssetMaterializer.materialize_broll(resolved)
        assert (bound.width, bound.height) == (1280, 720)
        assert bound.duration_seconds == pytest.approx(9.173333, abs=1e-6)
        assert bound.content_sha256 == resolved.content_sha256
    assert original.model_dump_json() == before


@pytest.mark.asyncio
async def test_probe_failure_no_cache_and_existing_failsafe(tmp_path, monkeypatch):
    cache = VisualAssetCache(tmp_path / "cache")
    probe = AsyncMock(side_effect=VideoProbeError("private /path and stderr secret"))
    monkeypatch.setattr(PROBE, probe)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=CONTENT, headers={"Content-Type": "video/mp4"})
    )) as client:
        provider = PexelsAssetProvider("synthetic-key", cache, client)
        with pytest.raises(PexelsAssetProviderError, match="^Invalid downloaded video physical metadata$"):
            await provider.fetch(candidate())

        class Resolver:
            async def resolve(self, request):
                return await provider.fetch(candidate())

        result = await resolve_provider_asset(resolver=Resolver(), request=VisualAssetRequest(
            scene_index=1, kind=VisualAssetKind.BROLL, query="servers", purpose="explain", required=True,
        ))
        assert result.action == BeatAssetAction.LOCAL_TEMPLATE
        assert result.fallback_reason_code == "PROVIDER_RESOLUTION_FAILED"
        assert result.resolved_asset is None and result.bound_broll_asset is None
        assert result.bound_visual_asset is None
    assert not list(tmp_path.rglob("metadata.json"))
