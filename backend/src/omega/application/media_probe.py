"""Media probe using ffprobe JSON parsing."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any


class MediaProbeError(RuntimeError):
    """Raised when ffprobe execution or JSON parsing fails."""

    pass


class MediaProbe:
    """Probes media files using ffprobe subprocess argument arrays."""

    async def probe_file(self, file_path: Path | str) -> dict[str, Any]:
        """Execute ffprobe and return structured media format and stream metadata."""
        p = Path(file_path).resolve()
        if not p.is_file():
            raise MediaProbeError(f"Media file '{file_path}' does not exist.")

        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(p),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace") if stderr else "ffprobe error"
            raise MediaProbeError(f"ffprobe failed for '{file_path}': {err}")

        try:
            data = json.loads(stdout.decode("utf-8"))
        except Exception as exc:
            raise MediaProbeError(
                f"Failed to parse ffprobe JSON output for '{file_path}': {exc}"
            ) from exc

        summary = self._extract_summary(data, p.stat().st_size)
        if summary.get("has_audio"):
            vol = await self.detect_audio_volume(p)
            summary["mean_volume_db"] = vol.get("mean_volume_db")
            summary["max_volume_db"] = vol.get("max_volume_db")
        else:
            summary["mean_volume_db"] = None
            summary["max_volume_db"] = None
        return summary

    async def detect_audio_volume(self, file_path: Path | str) -> dict[str, float | None]:
        """Detect mean and max audio volume in dBFS via FFmpeg volumedetect filter."""
        p = Path(file_path).resolve()
        cmd = ["ffmpeg", "-y", "-i", str(p), "-af", "volumedetect", "-f", "null", "-"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            text = stderr.decode("utf-8", errors="replace") if stderr else ""
            mean_vol: float | None = None
            max_vol: float | None = None
            for line in text.splitlines():
                if "mean_volume:" in line:
                    parts = line.split("mean_volume:")[-1].replace("dB", "").strip()
                    with contextlib.suppress(ValueError):
                        mean_vol = float(parts)
                elif "max_volume:" in line:
                    parts = line.split("max_volume:")[-1].replace("dB", "").strip()
                    with contextlib.suppress(ValueError):
                        max_vol = float(parts)
            return {"mean_volume_db": mean_vol, "max_volume_db": max_vol}
        except Exception:
            return {"mean_volume_db": None, "max_volume_db": None}

    def _extract_summary(self, raw_data: dict[str, Any], file_size_bytes: int) -> dict[str, Any]:
        """Extract clean normalized metadata summary from raw ffprobe JSON."""
        format_info = raw_data.get("format", {})
        streams = raw_data.get("streams", [])

        video_stream: dict[str, Any] | None = None
        audio_stream: dict[str, Any] | None = None
        subtitle_streams: list[dict[str, Any]] = []

        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video" and not video_stream:
                video_stream = stream
            elif codec_type == "audio" and not audio_stream:
                audio_stream = stream
            elif codec_type == "subtitle":
                subtitle_streams.append(stream)

        duration_sec = float(format_info.get("duration", 0.0))
        if duration_sec == 0.0 and video_stream:
            duration_sec = float(video_stream.get("duration", 0.0))
        if duration_sec == 0.0 and audio_stream:
            duration_sec = float(audio_stream.get("duration", 0.0))

        fps = 0.0
        if video_stream:
            r_frame_rate = str(video_stream.get("r_frame_rate", "0/1"))
            if "/" in r_frame_rate:
                num, den = r_frame_rate.split("/")
                fps = float(num) / float(den) if float(den) > 0 else 0.0

        return {
            "file_size_bytes": file_size_bytes,
            "duration_ms": int(duration_sec * 1000),
            "format_name": format_info.get("format_name", ""),
            "has_video": video_stream is not None,
            "has_audio": audio_stream is not None,
            "width": int(video_stream.get("width", 0)) if video_stream else None,
            "height": int(video_stream.get("height", 0)) if video_stream else None,
            "video_codec": video_stream.get("codec_name") if video_stream else None,
            "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
            "fps": round(fps, 2) if fps > 0 else None,
            "bit_rate": int(format_info.get("bit_rate", 0))
            if format_info.get("bit_rate")
            else None,
            "streams_count": len(streams),
            "subtitle_streams_count": len(subtitle_streams),
        }
