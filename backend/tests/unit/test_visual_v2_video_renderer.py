from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.visual_asset_binding import BoundBrollAsset
from omega.application.visual_direction import VisualAssetKind, VisualTemplateId
from omega.application.visual_template_renderer import RenderedTemplateDocument
from omega.infrastructure.browser_capture_runtime import BrowserCapturedFrame
from omega.infrastructure.visual_v2_video_renderer import (
    VisualV2VideoRenderer,
    VisualV2VideoRenderError,
)


def make_broll_asset(path: Path = Path("/tmp/video.mp4")) -> BoundBrollAsset:
    return BoundBrollAsset(
        asset_id="broll_1",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="0" * 64,
        local_path=path,
        duration_seconds=15.0,
        width=1920,
        height=1080,
    )


def make_doc(template_id: VisualTemplateId) -> RenderedTemplateDocument:
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=template_id,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body><div id="test">Test</div></body></html>""",
        semantic_element_ids=("test",),
        content_sha256="doc_sha",
    )


@pytest.mark.asyncio
async def test_broll_explainer_requires_broll_asset(tmp_path: Path):
    renderer = VisualV2VideoRenderer()
    doc = make_doc(VisualTemplateId.BROLL_EXPLAINER)
    mock_browser = MagicMock()

    with pytest.raises(VisualV2VideoRenderError, match="broll_asset is required for BROLL_EXPLAINER"):
        await renderer.render_clip(
            document=doc,
            motion_profile="broll_overlay",
            duration_seconds=3.0,
            output_path=tmp_path / "out.mp4",
            browser_runtime=mock_browser,
            broll_asset=None,
        )


@pytest.mark.asyncio
async def test_broll_asset_forbidden_on_non_broll_templates(tmp_path: Path):
    renderer = VisualV2VideoRenderer()
    doc = make_doc(VisualTemplateId.HERO_TITLE)
    mock_browser = MagicMock()
    broll = make_broll_asset()

    with pytest.raises(VisualV2VideoRenderError, match="broll_asset is not allowed for non-BROLL templates"):
        await renderer.render_clip(
            document=doc,
            motion_profile="title_reveal",
            duration_seconds=3.0,
            output_path=tmp_path / "out.mp4",
            browser_runtime=mock_browser,
            broll_asset=broll,
        )


@pytest.mark.asyncio
async def test_broll_ffmpeg_command_structure(tmp_path: Path, monkeypatch):
    renderer = VisualV2VideoRenderer()
    doc = make_doc(VisualTemplateId.BROLL_EXPLAINER)
    broll = make_broll_asset(tmp_path / "input_broll.mp4")
    out_file = tmp_path / "out.mp4"

    # Mock browser capture returning valid PNG frame
    mock_browser = MagicMock()
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (1920).to_bytes(4, "big") + (1080).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 50
    mock_frame = BrowserCapturedFrame(
        scene_index=1,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        width=1920,
        height=1080,
        png_bytes=png_bytes,
        png_sha256="fake_png_sha",
        source_html_sha256="fake_html_sha",
    )
    mock_browser.capture = AsyncMock(return_value=mock_frame)

    # Intercept subprocess.exec to inspect command line
    executed_cmds = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        executed_cmds.append(list(cmd))
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        # Write fake valid output mp4
        out_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
        return mock_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    res = await renderer.render_clip(
        document=doc,
        motion_profile="broll_overlay",
        duration_seconds=2.0,
        output_path=out_file,
        browser_runtime=mock_browser,
        fps=12,
        broll_asset=broll,
    )

    assert res.output_path == out_file
    assert len(executed_cmds) == 1
    cmd = executed_cmds[0]

    # Verify command constraints:
    # 1. Exactly one FFmpeg command
    assert cmd[0] == "ffmpeg"
    # 2. Input stream loop -1 present
    assert "-stream_loop" in cmd
    loop_idx = cmd.index("-stream_loop")
    assert cmd[loop_idx + 1] == "-1"
    # 3. B-roll input
    assert cmd[loop_idx + 2] == "-i"
    assert cmd[loop_idx + 3] == str(broll.local_path)
    # 4. Filter complex contains scale, crop, fps, overlay
    assert "-filter_complex" in cmd
    filter_str = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in filter_str
    assert "crop=1920:1080" in filter_str
    assert "fps=12" in filter_str
    assert "overlay=0:0:shortest=1" in filter_str
    # 5. Output mapping
    assert "-map" in cmd
    assert cmd[cmd.index("-map") + 1] == "[v]"
    # 6. Audio excluded
    assert "-an" in cmd
    # 7. Duration bounded
    assert "-t" in cmd
    assert cmd[cmd.index("-t") + 1] == "2.0"

    # 8. Transparent capture was requested
    assert mock_browser.capture.call_count == 24  # 2.0s * 12 fps = 24
    _, kwargs = mock_browser.capture.call_args
    assert kwargs.get("transparent_background") is True


@pytest.mark.asyncio
async def test_non_broll_command_unchanged(tmp_path: Path, monkeypatch):
    renderer = VisualV2VideoRenderer()
    doc = make_doc(VisualTemplateId.HERO_TITLE)
    out_file = tmp_path / "hero_out.mp4"

    mock_browser = MagicMock()
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (1920).to_bytes(4, "big") + (1080).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 50
    mock_frame = BrowserCapturedFrame(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        width=1920,
        height=1080,
        png_bytes=png_bytes,
        png_sha256="fake_png_sha",
        source_html_sha256="fake_html_sha",
    )
    mock_browser.capture = AsyncMock(return_value=mock_frame)

    executed_cmds = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        executed_cmds.append(list(cmd))
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        out_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
        return mock_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    await renderer.render_clip(
        document=doc,
        motion_profile="title_reveal",
        duration_seconds=1.0,
        output_path=out_file,
        browser_runtime=mock_browser,
        fps=12,
        broll_asset=None,
    )

    assert len(executed_cmds) == 1
    cmd = executed_cmds[0]
    assert "-stream_loop" not in cmd
    assert "-filter_complex" not in cmd
    assert "-an" in cmd

    # Non-broll transparent_background is False
    _, kwargs = mock_browser.capture.call_args
    assert kwargs.get("transparent_background") is False
