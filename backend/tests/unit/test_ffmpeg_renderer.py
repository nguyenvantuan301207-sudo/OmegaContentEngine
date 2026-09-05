"""Unit tests for FFmpeg filter safety and string escaping."""

from unittest.mock import AsyncMock, patch

import pytest

from omega.application.ffmpeg_renderer import FFmpegRenderer, escape_ffmpeg_filter_string


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
