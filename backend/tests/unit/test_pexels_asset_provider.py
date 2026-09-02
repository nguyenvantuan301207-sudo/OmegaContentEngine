from pathlib import Path

import httpx
import pytest

from omega.application.visual_asset_engine import VisualAssetCandidate, VisualAssetRequest
from omega.application.visual_direction import VisualAssetKind
from omega.infrastructure.pexels_asset_provider import PexelsAssetProvider, PexelsAssetProviderError
from omega.infrastructure.visual_asset_cache import VisualAssetCache


@pytest.fixture
def dummy_cache(tmp_path: Path) -> VisualAssetCache:
    return VisualAssetCache(tmp_path)


def test_auth_rejections(dummy_cache: VisualAssetCache):
    with pytest.raises(PexelsAssetProviderError, match="API key cannot be empty"):
        PexelsAssetProvider("", dummy_cache)
    with pytest.raises(PexelsAssetProviderError, match="API key cannot be empty"):
        PexelsAssetProvider("   ", dummy_cache)


@pytest.mark.asyncio
async def test_search_unsupported_kind(dummy_cache: VisualAssetCache):
    provider = PexelsAssetProvider("test-api-key", dummy_cache)
    req = VisualAssetRequest(
        scene_index=1,
        kind=VisualAssetKind.SCREENSHOT,
        query="test",
        purpose="test",
        required=True
    )
    with pytest.raises(PexelsAssetProviderError, match="Unsupported kind"):
        await provider.search(req)
    await provider.close()


@pytest.mark.asyncio
async def test_photo_search(dummy_cache: VisualAssetCache):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/search"
        assert request.url.query.decode() == "query=test+query&per_page=5"
        assert request.headers["authorization"] == "test-api-key"

        return httpx.Response(
            200,
            json={
                "photos": [
                    {
                        "id": 12345,
                        "width": 3024,
                        "height": 3024,
                        "url": "https://www.pexels.com/photo/12345",
                        "photographer": "John Doe",
                        "src": {
                            "original": "https://images.pexels.com/12345/original"
                        }
                    }
                ]
            }
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        req = VisualAssetRequest(
            scene_index=1,
            kind=VisualAssetKind.IMAGE,
            query="test query",
            purpose="test",
            required=True
        )
        candidates = await provider.search(req)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.provider_id == "12345"
        assert c.kind == VisualAssetKind.IMAGE
        assert c.provider == "pexels"
        assert c.source_url == "https://images.pexels.com/12345/original"
        assert c.source_page_url == "https://www.pexels.com/photo/12345"
        assert c.attribution_text == "Photo by John Doe on Pexels"
        assert c.license_name == "Pexels License"


@pytest.mark.asyncio
async def test_video_search_and_deterministic_selection(dummy_cache: VisualAssetCache):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/videos/search"

        return httpx.Response(
            200,
            json={
                "videos": [
                    {
                        "id": 999,
                        "duration": 15,
                        "user": {"name": "Video Creator"},
                        "video_files": [
                            {"id": 1, "file_type": "video/mp4", "width": 3840, "height": 2160, "link": "https://4k"},
                            {"id": 2, "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://1080p_landscape"},
                            {"id": 3, "file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://1080p_portrait"},
                            {"id": 4, "file_type": "video/mp4", "width": 1280, "height": 720, "link": "https://720p"}
                        ]
                    }
                ]
            }
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        req = VisualAssetRequest(
            scene_index=1,
            kind=VisualAssetKind.VIDEO,
            query="test query",
            purpose="test",
            required=True
        )
        candidates = await provider.search(req)

        assert len(candidates) == 1
        c = candidates[0]
        # Should prefer landscape 1080p over 4k
        assert c.source_url == "https://1080p_landscape"


@pytest.mark.asyncio
async def test_order_stability(dummy_cache: VisualAssetCache):
    provider = PexelsAssetProvider("test-api-key", dummy_cache)

    files1 = [
        {"id": 1, "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://A"},
        {"id": 2, "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://B"}
    ]
    files2 = [
        {"id": 2, "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://B"},
        {"id": 1, "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://A"}
    ]

    sel1 = provider._select_video_variant(files1)
    sel2 = provider._select_video_variant(files2)

    assert sel1 is not None and sel2 is not None
    assert sel1["link"] == sel2["link"]  # Tie break by ID deterministic
    await provider.close()


@pytest.mark.asyncio
async def test_error_handling(dummy_cache: VisualAssetCache):
    def mock_handler_429(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"X-Ratelimit-Limit": "200"})

    transport = httpx.MockTransport(mock_handler_429)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        req = VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="x", purpose="x", required=True)
        with pytest.raises(PexelsAssetProviderError, match="Rate limited") as exc:
            await provider.search(req)
        assert "test-api-key" not in str(exc.value)

    def mock_handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(mock_handler_500)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        req = VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="x", purpose="x", required=True)
        with pytest.raises(PexelsAssetProviderError, match="HTTP 500"):
            await provider.search(req)

@pytest.mark.asyncio
async def test_search_limit_validation(dummy_cache: VisualAssetCache):
    provider = PexelsAssetProvider("test-api-key", dummy_cache)
    req = VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="x", purpose="x", required=True)

    with pytest.raises(PexelsAssetProviderError, match="between 1 and 20"):
        await provider.search(req, limit=0)

    with pytest.raises(PexelsAssetProviderError, match="between 1 and 20"):
        await provider.search(req, limit=21)
    await provider.close()

@pytest.mark.asyncio
async def test_response_shape_validation(dummy_cache: VisualAssetCache):
    # Top level not a dict
    def mock_not_dict(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not a dict"])

    transport = httpx.MockTransport(mock_not_dict)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        req = VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="x", purpose="x", required=True)
        with pytest.raises(PexelsAssetProviderError, match="not a JSON object"):
            await provider.search(req)

    # Photos not a list
    def mock_photos_not_list(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"photos": "wrong"})

    transport = httpx.MockTransport(mock_photos_not_list)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        req = VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="x", purpose="x", required=True)
        with pytest.raises(PexelsAssetProviderError, match="'photos' is not a list"):
            await provider.search(req)

    # Videos not a list
    def mock_videos_not_list(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"videos": {}})

    transport = httpx.MockTransport(mock_videos_not_list)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        req = VisualAssetRequest(scene_index=1, kind=VisualAssetKind.VIDEO, query="x", purpose="x", required=True)
        with pytest.raises(PexelsAssetProviderError, match="'videos' is not a list"):
            await provider.search(req)


@pytest.mark.asyncio
async def test_fetch_success(dummy_cache: VisualAssetCache):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://download.test/"
        return httpx.Response(200, content=b"fake_image_bytes", headers={"Content-Type": "image/jpeg", "Content-Length": "16"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)

        c = VisualAssetCandidate(
            provider_id="1",
            kind=VisualAssetKind.IMAGE,
            provider="pexels",
            source_url="https://download.test/",
            source_page_url=None,
            mime_type="image/jpeg",
            width=100,
            height=100,
            duration_seconds=None,
            license_name="Pexels License",
            license_url=None,
            attribution_text=None,
            metadata={"search_query": "original query"}
        )

        asset = await provider.fetch(c)
        assert asset.kind == VisualAssetKind.IMAGE
        assert asset.query == "original query"
        assert asset.mime_type == "image/jpeg"
        assert asset.local_path.exists()
        assert asset.content_sha256 is not None


@pytest.mark.asyncio
async def test_stream_size_abort(dummy_cache: VisualAssetCache):
    async def mock_stream(request: httpx.Request) -> httpx.Response:
        # Simulate a 25 MiB + 1 byte stream without Content-Length
        async def generator():
            yield b"a" * (25 * 1024 * 1024)
            yield b"b"
            yield b"c"

        return httpx.Response(200, content=generator(), headers={"Content-Type": "image/jpeg"})

    transport = httpx.MockTransport(mock_stream)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)

        c = VisualAssetCandidate(
            provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
            source_url="https://test/stream", source_page_url=None, mime_type="image/jpeg",
            width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
            attribution_text=None, metadata={"search_query": "q"}
        )
        with pytest.raises(PexelsAssetProviderError, match="Received body exceeds limit"):
            await provider.fetch(c)

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_content_length_validation(dummy_cache: VisualAssetCache):
    def mock_handler_malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data", headers={"Content-Type": "image/jpeg", "Content-Length": "nonsense"})

    transport = httpx.MockTransport(mock_handler_malformed)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        c = VisualAssetCandidate(
            provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
            source_url="https://test/x", source_page_url=None, mime_type="image/jpeg",
            width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
            attribution_text=None, metadata={"search_query": "q"}
        )
        with pytest.raises(PexelsAssetProviderError, match="Malformed Content-Length"):
            await provider.fetch(c)

    def mock_handler_oversize(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"a" * 10, headers={"Content-Type": "image/jpeg", "Content-Length": str(25 * 1024 * 1024 + 1)})

    transport = httpx.MockTransport(mock_handler_oversize)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        with pytest.raises(PexelsAssetProviderError, match="Content-Length exceeds limit"):
            await provider.fetch(c)

@pytest.mark.asyncio
async def test_https_validation(dummy_cache: VisualAssetCache):
    provider = PexelsAssetProvider("test-api-key", dummy_cache)
    c_http = VisualAssetCandidate(
        provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
        source_url="http://example.test/file.jpg", source_page_url=None, mime_type="image/jpeg",
        width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
        attribution_text=None, metadata={"search_query": "q"}
    )
    with pytest.raises(PexelsAssetProviderError, match="Final URL scheme must be https"):
        await provider.fetch(c_http)

    requested_urls = []
    # Redirect to http
    def mock_redirect(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"Location": "http://redirect.test/file.jpg"})
        return httpx.Response(200, content=b"data", headers={"Content-Type": "image/jpeg"})

    transport = httpx.MockTransport(mock_redirect)
    async with httpx.AsyncClient(transport=transport) as client:
        provider2 = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        c_https = VisualAssetCandidate(
            provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
            source_url="https://example.test/file.jpg", source_page_url=None, mime_type="image/jpeg",
            width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
            attribution_text=None, metadata={"search_query": "q"}
        )
        with pytest.raises(PexelsAssetProviderError, match="Redirected to non-https URL"):
            await provider2.fetch(c_https)

        assert len(requested_urls) == 1
        assert requested_urls[0] == "https://example.test/file.jpg"

    requested_urls_safe = []
    # Safe redirect to https
    def mock_safe_redirect(request: httpx.Request) -> httpx.Response:
        requested_urls_safe.append(str(request.url))
        if str(request.url) == "https://one.test/file":
            return httpx.Response(302, headers={"Location": "https://two.test/file"})
        return httpx.Response(200, content=b"data", headers={"Content-Type": "image/jpeg", "Content-Length": "4"})

    transport_safe = httpx.MockTransport(mock_safe_redirect)
    async with httpx.AsyncClient(transport=transport_safe) as client:
        provider3 = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        c_safe = VisualAssetCandidate(
            provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
            source_url="https://one.test/file", source_page_url=None, mime_type="image/jpeg",
            width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
            attribution_text=None, metadata={"search_query": "q"}
        )
        await provider3.fetch(c_safe)

        assert len(requested_urls_safe) == 2
        assert requested_urls_safe[0] == "https://one.test/file"
        assert requested_urls_safe[1] == "https://two.test/file"

    # Redirect limit
    def mock_redirect_loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://loop.test/file"})

    transport_loop = httpx.MockTransport(mock_redirect_loop)
    async with httpx.AsyncClient(transport=transport_loop) as client:
        provider4 = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        c_loop = VisualAssetCandidate(
            provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
            source_url="https://loop.test/file", source_page_url=None, mime_type="image/jpeg",
            width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
            attribution_text=None, metadata={"search_query": "q"}
        )
        with pytest.raises(PexelsAssetProviderError, match="Redirect limit exceeded"):
            await provider4.fetch(c_loop)

@pytest.mark.asyncio
async def test_mime_validation(dummy_cache: VisualAssetCache):
    provider = PexelsAssetProvider("test-api-key", dummy_cache)

    # Pre-flight invalid candidate mime
    c_img_mp4 = VisualAssetCandidate(
        provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
        source_url="https://test/file.jpg", source_page_url=None, mime_type="video/mp4",
        width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
        attribution_text=None, metadata={"search_query": "q"}
    )
    with pytest.raises(PexelsAssetProviderError, match="Unsupported image candidate MIME: video/mp4"):
        await provider.fetch(c_img_mp4)

    c_vid_jpg = VisualAssetCandidate(
        provider_id="1", kind=VisualAssetKind.VIDEO, provider="pexels",
        source_url="https://test/file.mp4", source_page_url=None, mime_type="image/jpeg",
        width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
        attribution_text=None, metadata={"search_query": "q"}
    )
    with pytest.raises(PexelsAssetProviderError, match="Unsupported video candidate MIME: image/jpeg"):
        await provider.fetch(c_vid_jpg)

    # Response mismatch
    def mock_handler_mismatch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data", headers={"Content-Type": "image/png"})

    transport = httpx.MockTransport(mock_handler_mismatch)
    async with httpx.AsyncClient(transport=transport) as client:
        provider2 = PexelsAssetProvider("test-api-key", dummy_cache, client=client)
        c_mismatch = VisualAssetCandidate(
            provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
            source_url="https://test/file.jpg", source_page_url=None, mime_type="image/jpeg",
            width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
            attribution_text=None, metadata={"search_query": "q"}
        )
        with pytest.raises(PexelsAssetProviderError, match="Response MIME image/png does not match candidate MIME image/jpeg"):
            await provider2.fetch(c_mismatch)

@pytest.mark.asyncio
async def test_search_query_validation(dummy_cache: VisualAssetCache):
    provider = PexelsAssetProvider("test-api-key", dummy_cache)

    for invalid_meta in [{}, {"search_query": ""}, {"search_query": "   "}, {"search_query": 123}]:
        c = VisualAssetCandidate(
            provider_id="1", kind=VisualAssetKind.IMAGE, provider="pexels",
            source_url="https://test/file.jpg", source_page_url=None, mime_type="image/jpeg",
            width=100, height=100, duration_seconds=None, license_name=None, license_url=None,
            attribution_text=None, metadata=invalid_meta
        )
        with pytest.raises(PexelsAssetProviderError, match="search_query"):
            await provider.fetch(c)
