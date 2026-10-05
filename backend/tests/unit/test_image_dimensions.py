"""Synthetic headers exercise metadata probing without decoding or external IO."""
import hashlib
import json
import struct
import zlib
from unittest.mock import AsyncMock

import httpx
import pytest

from omega.application.beat_asset_executor import resolve_provider_asset
from omega.application.beat_asset_policy import BeatAssetAction
from omega.application.visual_asset_engine import VisualAssetCandidate, VisualAssetRequest
from omega.application.visual_direction import VisualAssetKind
from omega.domain.production import LicenseStatus
from omega.infrastructure.image_dimensions import ImageDimensionError, probe_image_dimensions
from omega.infrastructure.pexels_asset_provider import PexelsAssetProvider, PexelsAssetProviderError
from omega.infrastructure.video_probe import PhysicalVideoMetadata
from omega.infrastructure.visual_asset_cache import VisualAssetCache
from omega.infrastructure.visual_asset_materializer import VisualAssetMaterializer


def jpeg(width=1880, height=1255, marker=0xC0):
    frame = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    return b"\xff\xd8\xff\xe0\x00\x04ab\xff" + bytes([marker]) + struct.pack(">H", len(frame) + 2) + frame + b"\xff\xd9"


def png(width=1880, height=1255):
    data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + data + struct.pack(">I", zlib.crc32(b"IHDR" + data))


def webp(kind=b"VP8 ", width=1880, height=1255):
    if kind == b"VP8 ":
        data = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", width, height)
    elif kind == b"VP8L":
        data = b"\x2f" + struct.pack("<I", (width - 1) | ((height - 1) << 14))
    else:
        data = b"\x00" * 4 + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    chunk = kind + struct.pack("<I", len(data)) + data + b"\x00" * (len(data) & 1)
    return b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP" + chunk


IMAGES = [("image/jpeg", jpeg()), ("image/png", png())] + [
    ("image/webp", webp(kind)) for kind in (b"VP8 ", b"VP8L", b"VP8X")
]


@pytest.mark.parametrize("marker", [0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF])
def test_jpeg_sof_variants(marker):
    assert probe_image_dimensions(jpeg(marker=marker), "image/jpeg") == (1880, 1255)


@pytest.mark.parametrize("mime,content", IMAGES)
def test_physical_dimensions(mime, content):
    assert probe_image_dimensions(content, mime) == (1880, 1255)


@pytest.mark.parametrize("mime,content", IMAGES)
def test_every_truncated_header_fails_closed(mime, content):
    # JPEG only requires the full SOF header, not the trailing EOI marker.
    header_end = len(content) - 2 if mime == "image/jpeg" else len(content)
    for size in range(header_end):
        with pytest.raises(ImageDimensionError):
            probe_image_dimensions(content[:size], mime)


@pytest.mark.parametrize("mime,content", [
    ("image/jpeg", b"\xff\xd8\xff\xe0\x00\x01"),
    ("image/jpeg", b"\xff\xd8\xff\xe0\xff\xffshort"),
    ("image/jpeg", b"\xff\xd8\xff\xda\x00\x02"),
    ("image/jpeg", jpeg(0, 10)), ("image/jpeg", jpeg(10, 0)),
    ("image/png", png(0, 10)), ("image/png", png(10, 0)),
    ("image/png", png().replace(b"IHDR", b"IDAT")),
    ("image/png", png()[:29] + b"\x00\x00\x00\x00"),
    ("image/png", png().replace(b"\x00\x00\x00\r", b"\x00\x00\x00\x0c", 1)),
    ("image/webp", webp(width=0)), ("image/webp", webp(height=0)),
    ("image/webp", webp().replace(b"\x9d\x01\x2a", b"bad")),
    ("image/webp", webp().replace(b"VP8 ", b"JUNK")),
    ("image/webp", webp(b"VP8L").replace(b"\x2f", b"\x00", 1)),
    ("image/webp", webp(b"VP8L")[:24] + bytes([webp(b"VP8L")[24] | 0xE0]) + webp(b"VP8L")[25:]),
    ("image/webp", webp(b"VP8X")[:21] + b"\x01" + webp(b"VP8X")[22:]),
    ("image/webp", webp() + b"extra"),
    ("image/gif", b"GIF89a"),
])
def test_invalid_headers(mime, content):
    with pytest.raises(ImageDimensionError):
        probe_image_dimensions(content, mime)


def candidate(kind=VisualAssetKind.IMAGE):
    return VisualAssetCandidate(
        provider_id="123", kind=kind, provider="pexels",
        source_url="https://images.example.test/selected", source_page_url="https://example.test/photo/123",
        mime_type="image/jpeg" if kind == VisualAssetKind.IMAGE else "video/mp4",
        width=6016, height=4016, duration_seconds=57 if kind == VisualAssetKind.BROLL else None,
        license_status=LicenseStatus.LICENSED, license_name="Pexels License",
        license_url="https://www.pexels.com/license/", attribution_text="Photo by Author on Pexels",
        metadata={"search_query": "server infrastructure", "photographer": "Author"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mime,content", IMAGES + [("video/mp4", b"\x00\x00\x00\x18ftypmp42synthetic")])
async def test_fetch_cache_bound_dimensions_and_provenance(tmp_path, monkeypatch, mime, content):
    original = candidate(VisualAssetKind.BROLL if mime == "video/mp4" else VisualAssetKind.IMAGE)
    cache = VisualAssetCache(tmp_path)

    def respond(request):
        assert str(request.url) == original.source_url
        assert "authorization" not in request.headers
        return httpx.Response(200, content=content, headers={"Content-Type": mime})

    probe = AsyncMock(return_value=PhysicalVideoMetadata(6016, 4016, 57))
    monkeypatch.setattr("omega.infrastructure.pexels_asset_provider.probe_video_metadata", probe)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        resolved = await PexelsAssetProvider("synthetic-key", cache, client).fetch(original)
    probe.assert_awaited_once_with(content) if mime == "video/mp4" else probe.assert_not_awaited()
    expected = (6016, 4016) if mime == "video/mp4" else (1880, 1255)
    assert (resolved.width, resolved.height) == expected
    assert (original.width, original.height) == (6016, 4016)
    meta = json.loads(resolved.local_path.with_name("metadata.json").read_text())
    assert (meta["width"], meta["height"]) == expected
    reloaded = cache.get(resolved.content_sha256)
    assert (reloaded.width, reloaded.height) == expected
    bound = (VisualAssetMaterializer.materialize_broll(resolved) if mime == "video/mp4"
             else VisualAssetMaterializer.materialize(resolved))
    assert (bound.width, bound.height) == expected
    assert resolved.mime_type == mime
    assert resolved.local_path.read_bytes() == content
    assert resolved.content_sha256 == bound.content_sha256 == hashlib.sha256(content).hexdigest()
    for field in ("source_url", "source_page_url", "license_status", "license_name", "license_url",
                  "attribution_text", "metadata", "duration_seconds"):
        assert getattr(resolved, field) == getattr(original, field)
    assert resolved.query == original.metadata["search_query"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
async def test_unprobeable_fetch_is_bounded_and_degrades(tmp_path, mime):
    cache = VisualAssetCache(tmp_path)
    original = candidate()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"malformed", headers={"Content-Type": mime})
    )) as client:
        provider = PexelsAssetProvider("synthetic-key", cache, client)
        with pytest.raises(PexelsAssetProviderError, match="^Invalid downloaded image dimension header$"):
            await provider.fetch(original)

        class Resolver:
            async def resolve(self, request):
                return await provider.fetch(original)

        request = VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="servers", purpose="explain", required=True)
        executed = await resolve_provider_asset(resolver=Resolver(), request=request)
        assert executed.action == BeatAssetAction.LOCAL_TEMPLATE
        assert executed.fallback_reason_code == "PROVIDER_RESOLUTION_FAILED"
        assert executed.resolved_asset is None
    assert not list(tmp_path.rglob("metadata.json"))
