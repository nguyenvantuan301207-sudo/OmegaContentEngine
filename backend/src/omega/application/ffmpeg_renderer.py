"""FFmpeg Subprocess Renderer with strict argument arrays and filter escaping."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

DEFAULT_RENDER_TIMEOUT_SECONDS = 180


class FFmpegExecutionError(RuntimeError):
    """Raised when FFmpeg subprocess returns a non-zero exit code or fails."""

    pass


def escape_ffmpeg_filter_string(text: str, max_chars: int = 200) -> str:
    """Safely escape text for FFmpeg filter parameter interpolation."""
    clean = str(text).replace("\r", "").replace("\n", " ")[:max_chars]
    # Escape backslash, colon, single quote, semicolon, percent, and brackets
    escaped = (
        clean.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(";", "\\;")
        .replace("%", "\\%")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    return escaped


class FFmpegRenderer:
    """Executes FFmpeg operations purely via subprocess argument arrays with timeout guards."""

    async def render_scene_clip(
        self,
        image_path: Path | str,
        audio_path: Path | str,
        output_path: Path | str,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        video_codec: str = "h264",
        audio_codec: str = "aac",
        duration_sec: float | None = None,
        timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    ) -> None:
        """Render a single scene clip by pairing an image with an audio track."""
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-c:v",
            "libx264" if video_codec == "h264" else video_codec,
            "-tune",
            "stillimage",
            "-c:a",
            "aac" if audio_codec == "aac" else audio_codec,
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-s",
            f"{width}x{height}",
            "-shortest",
        ]
        if duration_sec:
            cmd.extend(["-t", f"{duration_sec:.3f}"])

        cmd.append(str(out_p))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError as exc:
            proc.kill()
            raise FFmpegExecutionError(
                f"FFmpeg scene render timed out after {timeout_seconds}s."
            ) from exc

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")[-500:] if stderr else "Unknown error"
            raise FFmpegExecutionError(
                f"FFmpeg scene render failed (code {proc.returncode}): {err_msg}"
            )

    async def concatenate_clips(
        self,
        clip_paths: list[Path | str],
        output_path: Path | str,
        srt_path: Path | str | None = None,
        timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    ) -> None:
        """Concatenate multiple scene clips into a final MP4 video using the concat demuxer."""
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        # Write concat manifest
        manifest_path = out_p.parent / f"concat_{out_p.stem}.txt"
        manifest_content = "\n".join(f"file '{Path(p).resolve().as_posix()}'" for p in clip_paths)
        manifest_path.write_text(manifest_content, encoding="utf-8")

        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest_path),
            "-c",
            "copy",
            str(out_p),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError as exc:
            proc.kill()
            raise FFmpegExecutionError(
                f"FFmpeg concatenation timed out after {timeout_seconds}s."
            ) from exc

        # Clean up concat manifest file
        with contextlib.suppress(OSError):
            manifest_path.unlink(missing_ok=True)

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")[-500:] if stderr else "Unknown error"
            raise FFmpegExecutionError(
                f"FFmpeg concatenation failed (code {proc.returncode}): {err_msg}"
            )
