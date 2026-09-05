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
        motion_effect: str | None = None,
        timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    ) -> None:
        """Render a single scene clip by pairing an image with an audio track and optional motion effect."""
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        me = (motion_effect or "NONE").upper()
        # Build video filter for subtle 2D motion effects
        vf_filters: list[str] = []
        if me in ("KEN_BURNS", "SLOW_ZOOM_IN"):
            # Subtle slow zoom in: 1.0 -> 1.08 over clip duration
            vf_filters.append(f"scale={width*2}x{height*2},zoompan=z='min(zoom+0.0005,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}")
        elif me == "SLOW_ZOOM_OUT":
            # Subtle slow zoom out: 1.08 -> 1.0 over clip duration
            vf_filters.append(f"scale={width*2}x{height*2},zoompan=z='if(lte(zoom,1.0),1.08,max(1.001,zoom-0.0005))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}")
        elif me == "PAN_RIGHT":
            # Subtle horizontal pan
            vf_filters.append(f"scale={width*2}x{height*2},zoompan=z=1.05:x='if(lte(on,1),(iw-iw/zoom)/2,min((iw-iw/zoom),x+0.5))':y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps={fps}")

        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
        ]

        if vf_filters:
            cmd.extend(["-vf", ",".join(vf_filters)])

        cmd.extend([
            "-c:v",
            "libx264" if video_codec == "h264" else video_codec,
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
        ])
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

        if srt_path and Path(srt_path).is_file() and Path(srt_path).stat().st_size > 0:
            clean_srt = str(Path(srt_path).resolve().as_posix()).replace(":", r"\:")
            # Subtitle V2 Style: sleek 18pt font, bottom-center margin 40, non-intrusive dark outline box
            vf_arg = f"subtitles='{clean_srt}':force_style='FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,MarginV=40,FontName=Sans,Alignment=2'"
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest_path),
                "-vf",
                vf_arg,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(out_p),
            ]
        else:
            cmd = [
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

    async def mux_video_audio(
        self,
        video_path: Path | str,
        audio_path: Path | str,
        output_path: Path | str,
        timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    ) -> None:
        """Mux a video stream and an audio stream into an MP4 file."""
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
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
                f"FFmpeg mux timed out after {timeout_seconds}s."
            ) from exc

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")[-500:] if stderr else "Unknown error"
            raise FFmpegExecutionError(
                f"FFmpeg mux failed (code {proc.returncode}): {err_msg}"
            )

    async def burn_ass_subtitles(
        self,
        video_path: Path | str,
        ass_path: Path | str,
        output_path: Path | str,
        timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    ) -> None:
        """Burn an ASS subtitle file into a video, preserving audio."""
        v_p = Path(video_path).resolve()
        a_p = Path(ass_path).resolve()
        out_p = Path(output_path).resolve()

        if not v_p.is_file() or v_p.stat().st_size == 0:
            raise ValueError(f"Input video missing or empty: {v_p}")

        if not a_p.is_file() or a_p.stat().st_size == 0:
            raise ValueError(f"ASS subtitle missing or empty: {a_p}")

        if a_p.suffix.lower() != ".ass":
            raise ValueError(f"ASS subtitle must have .ass suffix: {a_p}")

        if v_p == out_p:
            raise ValueError("Input and output video paths cannot be the same")

        out_p.parent.mkdir(parents=True, exist_ok=True)

        ass_filter_path = a_p.as_posix()
        clean_ass = escape_ffmpeg_filter_string(
            ass_filter_path,
            max_chars=max(len(ass_filter_path), 1),
        )
        vf_arg = f"ass='{clean_ass}'"

        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(v_p),
            "-vf", vf_arg,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
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
            await proc.wait()
            raise FFmpegExecutionError(
                f"FFmpeg ASS burn-in timed out after {timeout_seconds}s."
            ) from exc

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")[-500:] if stderr else "Unknown error"
            raise FFmpegExecutionError(
                f"FFmpeg ASS burn-in failed (code {proc.returncode}): {err_msg}"
            )

        if not out_p.is_file() or out_p.stat().st_size == 0:
            raise FFmpegExecutionError(f"FFmpeg succeeded but output is missing or empty: {out_p}")
