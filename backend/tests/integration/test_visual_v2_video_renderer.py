from pathlib import Path

import pytest

from omega.application.scene_template_registry import SceneTemplateRegistry, TemplateInputKey
from omega.application.template_payload_resolver import TemplatePayload
from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_template_renderer import (
    RenderedTemplateDocument,
    VisualTemplateRenderer,
)
from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime
from omega.infrastructure.visual_v2_video_renderer import (
    VisualV2VideoRenderer,
    VisualV2VideoRenderError,
)


@pytest.fixture
def hero_doc():
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body><div id="hero-title">My Title</div></body></html>""",
        semantic_element_ids=("hero-title",),
        content_sha256="fake",
    )


@pytest.fixture
def stat_doc():
    return RenderedTemplateDocument(
        scene_index=2,
        template_id=VisualTemplateId.STATISTIC_HERO,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body><div id="metric-value">72%</div></body></html>""",
        semantic_element_ids=(),
        content_sha256="fake2",
    )


@pytest.mark.asyncio
async def test_hero_real_video(tmp_path: Path):
    registry = SceneTemplateRegistry()
    template_renderer = VisualTemplateRenderer(registry)

    payload = TemplatePayload(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        inputs={
            TemplateInputKey.TITLE: "Why Distributed Systems Scale",
            TemplateInputKey.SUBTITLE: "From one request to millions",
        },
        asset_requirements=(),
        motion_profile="title_reveal",
        metadata={},
    )

    doc = template_renderer.render(payload)

    output_path = tmp_path / "hero.mp4"
    renderer = VisualV2VideoRenderer()

    async with BrowserCaptureRuntime() as browser:
        result = await renderer.render_clip(
            document=doc,
            motion_profile="title_reveal",
            duration_seconds=1.0,
            output_path=output_path,
            browser_runtime=browser,
            fps=6,
        )

    assert result.frame_count == 6
    assert result.width == 1920
    assert result.height == 1080
    assert result.output_path.exists()
    assert result.output_path.stat().st_size > 0
    assert len(result.video_sha256) == 64
    assert result.source_html_sha256 == doc.content_sha256
    assert result.motion_profile == "title_reveal"


@pytest.mark.asyncio
async def test_frame_motion_change(tmp_path: Path, hero_doc: RenderedTemplateDocument):
    async with BrowserCaptureRuntime() as browser:
        from omega.application.visual_dom_motion import VisualDomMotionRuntime

        motion = VisualDomMotionRuntime()
        doc1 = motion.render_frame(hero_doc, "title_reveal", 0.0, 1.0)
        doc2 = motion.render_frame(hero_doc, "title_reveal", 0.5, 1.0)

        frame1 = await browser.capture(doc1)
        frame2 = await browser.capture(doc2)

        assert frame1.png_sha256 != frame2.png_sha256


@pytest.mark.asyncio
async def test_caller_browser_reuse(tmp_path: Path, hero_doc: RenderedTemplateDocument, stat_doc: RenderedTemplateDocument):
    renderer = VisualV2VideoRenderer()
    out1 = tmp_path / "1.mp4"
    out2 = tmp_path / "2.mp4"

    async with BrowserCaptureRuntime() as browser:
        res1 = await renderer.render_clip(hero_doc, "title_reveal", 0.5, out1, browser, fps=2)
        res2 = await renderer.render_clip(stat_doc, "metric_emphasis", 0.5, out2, browser, fps=2)

    assert res1.frame_count == 1
    assert res2.frame_count == 1
    assert out1.exists()
    assert out2.exists()


@pytest.mark.asyncio
async def test_validation_errors(tmp_path: Path, hero_doc: RenderedTemplateDocument):
    renderer = VisualV2VideoRenderer()

    # Browser is NOT started
    browser = BrowserCaptureRuntime()

    with pytest.raises(VisualV2VideoRenderError, match="duration"):
        await renderer.render_clip(hero_doc, "title_reveal", 0.0, tmp_path / "out.mp4", browser)

    with pytest.raises(VisualV2VideoRenderError, match="fps"):
        await renderer.render_clip(hero_doc, "title_reveal", 1.0, tmp_path / "out.mp4", browser, fps=-1)

    with pytest.raises(VisualV2VideoRenderError, match="fps"):
        await renderer.render_clip(hero_doc, "title_reveal", 1.0, tmp_path / "out.mp4", browser, fps=70)

    with pytest.raises(VisualV2VideoRenderError, match="mp4"):
        await renderer.render_clip(hero_doc, "title_reveal", 1.0, tmp_path / "out.avi", browser)

    bad_doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        width=100,
        height=100,
        html="",
        semantic_element_ids=(),
        content_sha256="",
    )
    with pytest.raises(VisualV2VideoRenderError, match="dimensions"):
        await renderer.render_clip(bad_doc, "title_reveal", 1.0, tmp_path / "out.mp4", browser)


@pytest.mark.asyncio
async def test_filesystem_error_regression(tmp_path: Path, hero_doc: RenderedTemplateDocument):
    renderer = VisualV2VideoRenderer()

    # Create a file where a directory should be
    blocked_path = tmp_path / "blocked"
    blocked_path.write_text("i am a file")

    output_path = blocked_path / "out.mp4"

    # Browser is NOT started
    browser = BrowserCaptureRuntime()
    with pytest.raises(VisualV2VideoRenderError, match="Failed to create output directory"):
        await renderer.render_clip(hero_doc, "title_reveal", 1.0, output_path, browser)


@pytest.mark.asyncio
async def test_unknown_motion(tmp_path: Path, hero_doc: RenderedTemplateDocument):
    renderer = VisualV2VideoRenderer()
    async with BrowserCaptureRuntime() as browser:
        with pytest.raises(VisualV2VideoRenderError, match="Unknown motion profile"):
            await renderer.render_clip(hero_doc, "bad_profile", 1.0, tmp_path / "out.mp4", browser)
