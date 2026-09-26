import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_template_renderer import RenderedTemplateDocument
from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime


def make_doc() -> RenderedTemplateDocument:
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body>Hello</body></html>""",
        semantic_element_ids=(),
        content_sha256="fake_sha",
    )


@pytest.mark.asyncio
async def test_capture_default_not_transparent():
    runtime = BrowserCaptureRuntime()
    runtime._started = True
    mock_page = MagicMock()

    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (1920).to_bytes(4, "big")
        + (1080).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 50
    )
    mock_page.set_content = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=png_bytes)
    runtime._page = mock_page

    doc = make_doc()
    frame = await runtime.capture(doc)

    assert frame.width == 1920
    assert frame.height == 1080
    mock_page.screenshot.assert_awaited_once_with(
        type="png", full_page=False, animations="disabled"
    )


@pytest.mark.asyncio
async def test_capture_transparent_mode_requests_omit_background():
    runtime = BrowserCaptureRuntime()
    runtime._started = True
    mock_page = MagicMock()

    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (1920).to_bytes(4, "big")
        + (1080).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 50
    )
    mock_page.set_content = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=png_bytes)
    runtime._page = mock_page

    doc = make_doc()
    frame = await runtime.capture(doc, transparent_background=True)

    assert frame.width == 1920
    assert frame.height == 1080
    mock_page.screenshot.assert_awaited_once_with(
        type="png", full_page=False, animations="disabled", omit_background=True
    )


@pytest.mark.asyncio
async def test_capture_in_memory_cache_deduplication():
    runtime = BrowserCaptureRuntime()
    runtime._started = True
    mock_page = MagicMock()

    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (1920).to_bytes(4, "big")
        + (1080).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 50
    )
    mock_page.set_content = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=png_bytes)
    runtime._page = mock_page

    doc = make_doc()
    # First capture
    frame1 = await runtime.capture(doc)
    # Second capture with same doc and transparent flag
    frame2 = await runtime.capture(doc)

    assert frame1.png_sha256 == frame2.png_sha256
    # Page screenshot should only have been called once due to caching
    assert mock_page.screenshot.call_count == 1


@pytest.mark.asyncio
async def test_page_pool_crash_recovery():
    from omega.infrastructure.browser_capture_runtime import BrowserCaptureError

    runtime = BrowserCaptureRuntime(max_concurrency=1)
    runtime._started = True

    bad_page = MagicMock()
    bad_page.set_content = AsyncMock(side_effect=RuntimeError("Browser process disconnected"))
    bad_page.close = AsyncMock()

    fresh_page = MagicMock()
    fresh_page.close = AsyncMock()

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=fresh_page)
    runtime._context = mock_context
    runtime._page = bad_page

    doc = make_doc()
    with pytest.raises(BrowserCaptureError) as exc_info:
        await runtime.capture(doc)

    assert "Failed to capture document" in str(exc_info.value)
    # Verify bad page was closed
    bad_page.close.assert_awaited_once()
    mock_context.new_page.assert_awaited_once()
    assert fresh_page in runtime._pages


@pytest.mark.asyncio
async def test_capture_cache_collision_prevention():
    runtime = BrowserCaptureRuntime()
    runtime._started = True
    mock_page = MagicMock()

    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (1920).to_bytes(4, "big")
        + (1080).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 50
    )
    mock_page.set_content = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=png_bytes)
    runtime._page = mock_page

    doc = make_doc()

    # 1. Non-transparent capture
    await runtime.capture(doc, transparent_background=False)
    assert mock_page.screenshot.call_count == 1

    # 2. Transparent capture with same document must NOT alias and must invoke screenshot again
    await runtime.capture(doc, transparent_background=True)
    assert mock_page.screenshot.call_count == 2

    # 3. Document with same HTML but different dimensions must NOT alias
    doc_resized = RenderedTemplateDocument(
        scene_index=doc.scene_index,
        template_id=doc.template_id,
        width=1280,
        height=720,
        html=doc.html,
        semantic_element_ids=doc.semantic_element_ids,
        content_sha256=doc.content_sha256,
    )
    await runtime.capture(doc_resized, transparent_background=False)
    assert mock_page.screenshot.call_count == 3


@pytest.mark.asyncio
async def test_resource_safety_page_pool_bounds_and_lifecycle():
    runtime = BrowserCaptureRuntime(max_concurrency=2)
    runtime._started = True

    valid_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (1920).to_bytes(4, "big")
        + (1080).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 50
    )
    p1 = MagicMock()
    p1.set_content = AsyncMock()
    p1.screenshot = AsyncMock(return_value=valid_png)
    p1.close = AsyncMock()

    p2 = MagicMock()
    p2.set_content = AsyncMock()
    p2.screenshot = AsyncMock(return_value=valid_png)
    p2.close = AsyncMock()

    mock_ctx = MagicMock()
    mock_ctx.close = AsyncMock()
    mock_browser = MagicMock()
    mock_browser.close = AsyncMock()

    runtime._pages = [p1, p2]
    pool = asyncio.Queue()
    pool.put_nowait(p1)
    pool.put_nowait(p2)
    runtime._page_pool = pool
    runtime._context = mock_ctx
    runtime._browser = mock_browser

    # Run multiple captures
    doc = make_doc()
    await runtime.capture(doc)
    # Ensure all pages are returned to the pool (pool size matches max_concurrency)
    assert runtime._page_pool.qsize() == 2

    # Cleanup lifecycle
    await runtime._cleanup()
    p1.close.assert_awaited_once()
    p2.close.assert_awaited_once()
    mock_ctx.close.assert_awaited_once()
    mock_browser.close.assert_awaited_once()
    assert len(runtime._pages) == 0
    assert runtime._page_pool is None
    assert runtime._started is False
