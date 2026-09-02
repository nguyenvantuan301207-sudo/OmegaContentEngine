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
