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


@pytest.mark.asyncio
async def test_mix_master_audio_success(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"dummy_out")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock) as mock_exec:
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
            background_music_gain_db=-20.0,
        )

        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0]

        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert str(in_vid) in cmd
        assert str(in_bgm) in cmd
        assert "-c:v" in cmd
        assert cmd[cmd.index("-c:v") + 1] == "copy"
        assert "-c:a" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "aac"
        assert "-b:a" in cmd
        assert cmd[cmd.index("-b:a") + 1] == "192k"

        idx_fc = cmd.index("-filter_complex")
        fc_arg = cmd[idx_fc + 1]
        assert "amix=" in fc_arg
        assert "volume=-20.0dB" in fc_arg
        assert "atrim=0:5.0" in fc_arg

        assert "-map" in cmd
        assert "0:v:0" in cmd
        assert "[aout]" in cmd
        assert cmd[-1].endswith("out.mp4")

@pytest.mark.asyncio
async def test_mix_master_audio_music_looping(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"dummy_out")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock) as mock_exec:
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
            background_music_loop_required=True,
        )

        cmd = mock_exec.call_args[0]
        idx_bgm = cmd.index(str(in_bgm))
        assert cmd[idx_bgm - 1] == "-i"
        assert cmd[idx_bgm - 2] == "-1"
        assert cmd[idx_bgm - 3] == "-stream_loop"

@pytest.mark.asyncio
async def test_mix_master_audio_music_fades(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"dummy_out")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock) as mock_exec:
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
            background_music_fade_in_ms=1000,
            background_music_fade_out_ms=1500,
        )

        cmd = mock_exec.call_args[0]
        idx_fc = cmd.index("-filter_complex")
        fc_arg = cmd[idx_fc + 1]

        assert "afade=t=in:st=0:d=1.0" in fc_arg
        # target = 5.0, fade_out = 1.5, st = 5.0 - 1.5 = 3.5
        assert "afade=t=out:st=3.5:d=1.5" in fc_arg

@pytest.mark.asyncio
async def test_mix_master_audio_sfx(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_sfx = tmp_path / "sfx.wav"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_sfx.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"dummy_out")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock) as mock_exec:
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            sfx_inputs=[
                FFmpegRenderer.SFXMixInput(audio_path=in_sfx, start_ms=1000, duration_ms=500, gain_db=-5.0)
            ],
        )

        cmd = mock_exec.call_args[0]
        assert str(in_sfx) in cmd
        idx_fc = cmd.index("-filter_complex")
        fc_arg = cmd[idx_fc + 1]

        assert "atrim=0:0.5" in fc_arg
        assert "volume=-5.0dB" in fc_arg
        assert "adelay=1000|1000" in fc_arg
        assert "amix=inputs=2" in fc_arg

@pytest.mark.asyncio
async def test_mix_master_audio_overlapping_sfx(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_sfx1 = tmp_path / "sfx1.wav"
    in_sfx2 = tmp_path / "sfx2.wav"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_sfx1.write_bytes(b"dummy")
    in_sfx2.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"dummy_out")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock) as mock_exec:
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            sfx_inputs=[
                FFmpegRenderer.SFXMixInput(audio_path=in_sfx1, start_ms=1000, duration_ms=500, gain_db=-5.0),
                FFmpegRenderer.SFXMixInput(audio_path=in_sfx2, start_ms=1000, duration_ms=1000, gain_db=0.0)
            ],
        )

        cmd = mock_exec.call_args[0]
        idx_fc = cmd.index("-filter_complex")
        fc_arg = cmd[idx_fc + 1]
        assert "amix=inputs=3" in fc_arg

@pytest.mark.asyncio
async def test_mix_master_audio_music_and_sfx(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    in_sfx1 = tmp_path / "sfx1.wav"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")
    in_sfx1.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"dummy_out")
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock) as mock_exec:
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
            sfx_inputs=[
                FFmpegRenderer.SFXMixInput(audio_path=in_sfx1, start_ms=1000, duration_ms=500, gain_db=-5.0)
            ],
        )

        cmd = mock_exec.call_args[0]
        idx_fc = cmd.index("-filter_complex")
        fc_arg = cmd[idx_fc + 1]
        assert "amix=inputs=3" in fc_arg
        assert "[bgm]" in fc_arg
        assert "[sfx2]" in fc_arg

@pytest.mark.asyncio
async def test_mix_master_audio_no_mix_input(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="No music or SFX provided"):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
        )

@pytest.mark.asyncio
async def test_mix_master_audio_missing_input(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "missing.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_bgm.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="Input video missing or empty"):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
        )

@pytest.mark.asyncio
async def test_mix_master_audio_missing_music(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "missing.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="Background music missing or empty"):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
        )

@pytest.mark.asyncio
async def test_mix_master_audio_invalid_target_duration(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="target_duration_ms must be > 0"):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=0,
            background_music_path=in_bgm,
        )

@pytest.mark.asyncio
async def test_mix_master_audio_invalid_sfx(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_sfx = tmp_path / "sfx.wav"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_sfx.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="SFX exceeds target duration"):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            sfx_inputs=[
                FFmpegRenderer.SFXMixInput(audio_path=in_sfx, start_ms=4000, duration_ms=2000, gain_db=-5.0)
            ],
        )

@pytest.mark.asyncio
async def test_mix_master_audio_same_path(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="Input and output video paths cannot be the same"):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=in_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
        )

@pytest.mark.asyncio
async def test_mix_master_audio_timeout(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = TimeoutError()
    mock_proc.kill = MagicMock()

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(FFmpegExecutionError, match="FFmpeg master mix timed out"),
    ):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
        )

    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()

@pytest.mark.asyncio
async def test_mix_master_audio_nonzero_exit(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"Error detail")
    mock_proc.returncode = 1

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(FFmpegExecutionError, match="FFmpeg master mix failed \\(code 1\\): Error detail"),
    ):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
        )

@pytest.mark.asyncio
async def test_mix_master_audio_missing_output(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(FFmpegExecutionError, match="FFmpeg master mix succeeded but output is missing or empty"),
    ):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
        )

@pytest.mark.asyncio
async def test_mix_master_audio_zero_byte_output(tmp_path):
    renderer = FFmpegRenderer()
    in_vid = tmp_path / "input.mp4"
    in_bgm = tmp_path / "bgm.mp3"
    out_vid = tmp_path / "out.mp4"
    in_vid.write_bytes(b"dummy")
    in_bgm.write_bytes(b"dummy")

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    async def create_sub_mock(*args, **kwargs):
        out_vid.write_bytes(b"")
        return mock_proc

    with (
        patch("asyncio.create_subprocess_exec", side_effect=create_sub_mock),
        pytest.raises(FFmpegExecutionError, match="FFmpeg master mix succeeded but output is missing or empty"),
    ):
        await renderer.mix_master_audio(
            video_path=in_vid,
            output_path=out_vid,
            target_duration_ms=5000,
            background_music_path=in_bgm,
        )
