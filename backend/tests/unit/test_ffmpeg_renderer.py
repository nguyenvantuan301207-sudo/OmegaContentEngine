"""Unit tests for FFmpeg filter safety and string escaping."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omega.application.ffmpeg_renderer import (
    FFmpegExecutionError,
    FFmpegRenderer,
    escape_ffmpeg_filter_string,
)


def test_escape_ffmpeg_filter_string_normal():
    """Verify clean string escaping."""
    s = "Simple title text"
    assert escape_ffmpeg_filter_string(s) == "Simple title text"


def test_escape_ffmpeg_filter_string_adversarial():
    """Verify all dangerous FFmpeg filter characters are strictly escaped."""
    adversarial = "Title: 'Quote' [Brackets] %Percent% ;Semicolon \\Backslash\nNewline"
    escaped = escape_ffmpeg_filter_string(adversarial)

    assert "\\:" in escaped
    assert "\\'" in escaped
    assert "\\[" in escaped
    assert "\\]" in escaped
    assert "\\%" in escaped
    assert "\\;" in escaped
    assert "\\\\" in escaped
    assert "\n" not in escaped


def test_escape_ffmpeg_filter_string_length_bound():
    """Verify max length clamp."""
    long_str = "A" * 500
    escaped = escape_ffmpeg_filter_string(long_str, max_chars=100)
    assert len(escaped) == 100


@pytest.mark.asyncio
async def test_mux_video_audio_command_construction():
    renderer = FFmpegRenderer()

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await renderer.mux_video_audio(
            video_path="input.mp4",
            audio_path="input.wav",
            output_path="out.mp4"
        )

        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0]

        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd

        idx_i_v = cmd.index("input.mp4")
        assert cmd[idx_i_v - 1] == "-i"

        idx_i_a = cmd.index("input.wav")
        assert cmd[idx_i_a - 1] == "-i"

        assert "-c:v" in cmd
        assert cmd[cmd.index("-c:v") + 1] == "copy"

        assert "-c:a" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "aac"

        assert "-b:a" in cmd
        assert cmd[cmd.index("-b:a") + 1] == "192k"

        assert "-shortest" in cmd

        assert cmd[-1].endswith("out.mp4")


@pytest.mark.asyncio
async def test_burn_ass_subtitles_command_construction(tmp_path):
    renderer = FFmpegRenderer()
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    in_vid = tmp_path / "input.mp4"
    in_ass = tmp_path / "subs.ass"
    out_vid = tmp_path / "out.mp4"

    in_vid.write_bytes(b"dummy")
    in_ass.write_bytes(b"dummy")

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"dummy_out")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock) as mock_exec:
        await renderer.burn_ass_subtitles(in_vid, in_ass, out_vid)

        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0]

        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd

        idx_i = cmd.index(str(in_vid))
        assert cmd[idx_i - 1] == "-i"

        assert "-vf" in cmd
        idx_vf = cmd.index("-vf")
        assert cmd[idx_vf + 1].startswith("ass='")
        assert cmd[idx_vf + 1].endswith(f"{escape_ffmpeg_filter_string(in_ass.as_posix())}'")

        assert "-map" in cmd
        assert "0:v:0" in cmd
        assert "0:a?" in cmd

        assert "-c:v" in cmd
        assert cmd[cmd.index("-c:v") + 1] == "libx264"

        assert "-pix_fmt" in cmd
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"

        assert "-c:a" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "copy"

        assert "-shortest" not in cmd
        assert cmd[-1].endswith("out.mp4")

@pytest.mark.asyncio
async def test_burn_ass_subtitles_missing_input(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "missing.mp4"
    in_ass = tmp_path / "subs.ass"
    out_vid = tmp_path / "out.mp4"
    in_ass.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="Input video missing or empty"):
        await renderer.burn_ass_subtitles(in_vid, in_ass, out_vid)

@pytest.mark.asyncio
async def test_burn_ass_subtitles_missing_ass(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_ass = tmp_path / "missing.ass"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="ASS subtitle missing or empty"):
        await renderer.burn_ass_subtitles(in_vid, in_ass, out_vid)

@pytest.mark.asyncio
async def test_burn_ass_subtitles_wrong_suffix(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_ass = tmp_path / "subs.srt"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_ass.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="ASS subtitle must have \\.ass suffix"):
        await renderer.burn_ass_subtitles(in_vid, in_ass, out_vid)

@pytest.mark.asyncio
async def test_burn_ass_subtitles_same_paths(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_ass = tmp_path / "subs.ass"
    in_vid.write_bytes(b"dummy")
    in_ass.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="Input and output video paths cannot be the same"):
        await renderer.burn_ass_subtitles(in_vid, in_ass, in_vid)

@pytest.mark.asyncio
async def test_burn_ass_subtitles_nonzero_exit(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_ass = tmp_path / "subs.ass"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_ass.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"Error detail")
    mock_proc.returncode = 1

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(FFmpegExecutionError, match="FFmpeg ASS burn-in failed \\(code 1\\): Error detail"),
    ):
        await renderer.burn_ass_subtitles(in_vid, in_ass, out_vid)

@pytest.mark.asyncio
async def test_burn_ass_subtitles_timeout(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_ass = tmp_path / "subs.ass"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_ass.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = TimeoutError()
    mock_proc.kill = MagicMock()

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(FFmpegExecutionError, match="FFmpeg ASS burn-in timed out"),
    ):
        await renderer.burn_ass_subtitles(in_vid, in_ass, out_vid)

    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()

@pytest.mark.asyncio
async def test_burn_ass_subtitles_missing_output(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_ass = tmp_path / "subs.ass"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_ass.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(FFmpegExecutionError, match="FFmpeg succeeded but output is missing or empty"),
    ):
        await renderer.burn_ass_subtitles(in_vid, in_ass, out_vid)

@pytest.mark.asyncio
async def test_burn_ass_subtitles_long_ass_path(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")

    long_dir = tmp_path
    for i in range(25):
        long_dir = long_dir / f"dir_{i:04d}"
    long_dir.mkdir(parents=True)
    in_ass = long_dir / "long_subs.ass"
    in_ass.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"dummy_out")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock) as mock_exec:
        await renderer.burn_ass_subtitles(in_vid, in_ass, out_vid)

    cmd = mock_exec.call_args[0]
    idx_vf = cmd.index("-vf")
    vf_arg = cmd[idx_vf + 1]

    assert "ass='" in vf_arg
    expected_path = escape_ffmpeg_filter_string(in_ass.as_posix(), max_chars=max(len(in_ass.as_posix()), 1))
    assert vf_arg == f"ass='{expected_path}'"
    assert len(vf_arg) > 200
