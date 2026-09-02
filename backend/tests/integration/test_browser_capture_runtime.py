import pytest

from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_template_renderer import RenderedTemplateDocument
from omega.infrastructure.browser_capture_runtime import (
    BrowserCaptureError,
    BrowserCaptureRuntime,
)


@pytest.fixture
def hero_document():
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        width=1920,
        height=1080,
        html="""<!doctype html><html><body style="margin: 0; background: red;"><div id="test">Hero</div></body></html>""",
        semantic_element_ids=("test",),
        content_sha256="fake_sha_123",
    )


@pytest.fixture
def stat_document():
    return RenderedTemplateDocument(
        scene_index=2,
        template_id=VisualTemplateId.STATISTIC_HERO,
        width=1920,
        height=1080,
        html="""<!doctype html><html><body style="margin: 0; background: blue;"><div id="stat">72%</div></body></html>""",
        semantic_element_ids=("stat",),
        content_sha256="fake_sha_456",
    )


@pytest.fixture
def network_document():
    return RenderedTemplateDocument(
        scene_index=3,
        template_id=VisualTemplateId.IMAGE_EXPLAINER,
        width=1920,
        height=1080,
        html="""<!doctype html><html><body><img src="https://example.com/test.jpg"></body></html>""",
        semantic_element_ids=(),
        content_sha256="fake_sha_net",
    )


@pytest.mark.asyncio
async def test_capture_basic_hero(hero_document):
    async with BrowserCaptureRuntime() as runtime:
        frame = await runtime.capture(hero_document)

    assert frame.scene_index == 1
    assert frame.template_id == VisualTemplateId.HERO_TITLE
    assert frame.width == 1920
    assert frame.height == 1080
    assert frame.source_html_sha256 == "fake_sha_123"
    assert frame.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(frame.png_sha256) == 64


@pytest.mark.asyncio
async def test_determinism_repeat(hero_document):
    async with BrowserCaptureRuntime() as runtime:
        frame1 = await runtime.capture(hero_document)
        frame2 = await runtime.capture(hero_document)

    assert frame1.png_sha256 == frame2.png_sha256


@pytest.mark.asyncio
async def test_sequential_reuse(hero_document, stat_document):
    async with BrowserCaptureRuntime() as runtime:
        frame1 = await runtime.capture(hero_document)
        frame2 = await runtime.capture(stat_document)

    assert frame1.template_id == VisualTemplateId.HERO_TITLE
    assert frame2.template_id == VisualTemplateId.STATISTIC_HERO
    assert frame1.png_sha256 != frame2.png_sha256


@pytest.mark.asyncio
async def test_lifecycle_not_started(hero_document):
    runtime = BrowserCaptureRuntime()
    with pytest.raises(BrowserCaptureError, match="Browser runtime not started"):
        await runtime.capture(hero_document)


@pytest.mark.asyncio
async def test_network_isolation(network_document):
    aborted_urls = []

    async with BrowserCaptureRuntime() as runtime:
        runtime._page.on("requestfailed", lambda req: aborted_urls.append(req.url))
        frame = await runtime.capture(network_document)

    assert frame.width == 1920
    assert "https://example.com/test.jpg" in aborted_urls


@pytest.mark.asyncio
async def test_structural_guarantees(hero_document, stat_document):
    async with BrowserCaptureRuntime() as runtime:
        page_id_1 = id(runtime._page)
        browser_id_1 = id(runtime._browser)

        await runtime.capture(hero_document)
        await runtime.capture(stat_document)

        page_id_2 = id(runtime._page)
        browser_id_2 = id(runtime._browser)

    assert page_id_1 == page_id_2
    assert browser_id_1 == browser_id_2
