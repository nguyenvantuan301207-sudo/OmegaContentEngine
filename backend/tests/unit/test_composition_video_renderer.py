from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omega.application.composition_video_renderer import CompositionVideoRenderer, RendererError
from omega.application.motion_spec import SceneMotionPlan
from omega.application.scene_composition import Layer, LayerType, SceneComposition


@pytest.fixture
def mock_composition():
    layer = Layer(id="test", type=LayerType.SHAPE, x=0, y=0, width=100, height=100, z_index=1)
    return SceneComposition(
        scene_index=1,
        source_storyboard_scene_index=1,
        width=1920,
        height=1080,
        layout_type="TEST",
        visual_strategy="INFOGRAPHIC",
        duration_seconds=2.0,
        layers=[layer]
    )


@pytest.fixture
def mock_motion_plan():
    return SceneMotionPlan(scene_index=1, scene_duration_seconds=2.0, layers=[])


@pytest.fixture
def temp_output_dir(tmp_path):
    out = tmp_path / "out"
    return out


@pytest.mark.asyncio
async def test_successful_render(mock_composition, mock_motion_plan, temp_output_dir):
    renderer = CompositionVideoRenderer()

    # Mock evaluate and render_svg to track calls
    renderer.motion_runtime.evaluate = MagicMock(return_value=[])
    renderer.frame_renderer.render_svg = MagicMock(return_value="<svg></svg>")

    output_mp4 = temp_output_dir / "test.mp4"

    async def mock_exec_side_effect(*args, **kwargs):
        process_mock = AsyncMock()
        process_mock.returncode = 0
        process_mock.communicate.return_value = (b"", b"")
        output_mp4.parent.mkdir(parents=True, exist_ok=True)
        output_mp4.write_text("dummy")
        return process_mock

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec_side_effect) as mock_exec:
        result = await renderer.render_clip(mock_composition, mock_motion_plan, output_mp4, fps=12)

        # 1. correct frame count: 2.0s * 12fps = 24 frames
        assert result.frame_count == 24
        assert result.fps == 12
        assert result.duration_seconds == 2.0

        # 2. runtime is evaluated for frames & timestamps stay within duration
        assert renderer.motion_runtime.evaluate.call_count == 24
        for i, call in enumerate(renderer.motion_runtime.evaluate.call_args_list):
            t = call[0][2]
            assert t == i / 12.0
            assert t < 2.0

        # 3. SVG renderer is used
        assert renderer.frame_renderer.render_svg.call_count == 24

        # 4. exactly one FFmpeg process is requested
        mock_exec.assert_called_once()

        # 5. NO scale=2x legacy behavior, NO zoompan
        cmd_args = mock_exec.call_args[0]
        assert cmd_args[0] == "ffmpeg"
        assert "-framerate" in cmd_args
        assert "12" in cmd_args
        assert "-c:v" in cmd_args
        assert "libx264" in cmd_args
        assert "yuv420p" in cmd_args
        assert "-vf" in cmd_args
        assert "scale=1920:1080" in cmd_args

        assert "zoompan" not in cmd_args
        assert "scale=2x" not in cmd_args

        # 6. output directory creation works
        assert temp_output_dir.exists()


@pytest.mark.asyncio
async def test_ffmpeg_failure(mock_composition, mock_motion_plan, tmp_path):
    renderer = CompositionVideoRenderer()

    process_mock = AsyncMock()
    process_mock.returncode = 1
    process_mock.communicate.return_value = (b"", b"Error message")

    output_mp4 = tmp_path / "fail.mp4"

    with patch("asyncio.create_subprocess_exec", return_value=process_mock), \
         pytest.raises(RendererError, match="FFmpeg failed with code 1"):
        await renderer.render_clip(mock_composition, mock_motion_plan, output_mp4, fps=12)


@pytest.mark.asyncio
async def test_ffmpeg_timeout(mock_composition, mock_motion_plan, tmp_path):
    renderer = CompositionVideoRenderer()

    process_mock = AsyncMock()
    process_mock.communicate.side_effect = TimeoutError()
    process_mock.kill = MagicMock()

    output_mp4 = tmp_path / "timeout.mp4"

    with patch("asyncio.create_subprocess_exec", return_value=process_mock), \
         pytest.raises(RendererError, match="FFmpeg process timed out"):
        await renderer.render_clip(mock_composition, mock_motion_plan, output_mp4, fps=12)

        process_mock.kill.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_occurs(mock_composition, mock_motion_plan, tmp_path):
    renderer = CompositionVideoRenderer()

    process_mock = AsyncMock()
    process_mock.returncode = 0
    process_mock.communicate.return_value = (b"", b"")

    output_mp4 = tmp_path / "clean.mp4"

    captured_temp_path = None

    async def side_effect(*args, **kwargs):
        nonlocal captured_temp_path
        # Extract temp path from '-i' argument which is 'temp_dir/frame_%05d.svg'
        idx = args.index("-i")
        input_pattern = args[idx + 1]
        captured_temp_path = Path(input_pattern).parent

        # Verify files follow frame_%05d.svg ordering inside the mock execution
        files = list(captured_temp_path.glob("frame_*.svg"))
        assert len(files) == 24
        assert (captured_temp_path / "frame_00000.svg").exists()
        assert (captured_temp_path / "frame_00023.svg").exists()

        output_mp4.parent.mkdir(parents=True, exist_ok=True)
        output_mp4.write_text("dummy")

        return process_mock

    with patch("asyncio.create_subprocess_exec", side_effect=side_effect):
        await renderer.render_clip(mock_composition, mock_motion_plan, output_mp4, fps=12)

    # Verify cleanup occurs on success
    assert captured_temp_path is not None
    assert not captured_temp_path.exists()
