"""Application-layer Intra-Scene Visual Clip Assembler.

Assembles multiple rendered visual-only beat video clips into a single contiguous
scene-level visual clip with deterministic CFR timing, zero audio streams, and
strict validation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from omega.application.ffmpeg_renderer import FFmpegRenderer


class RenderedBeatClip(BaseModel):
    """Immutable metadata and file reference for an already-rendered visual beat clip."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    materialized_index: int = Field(
        ge=0, description="0-indexed position within scene timing plan"
    )
    source_beat_index: int = Field(ge=0, description="0-indexed source editorial beat index")
    start_ms: int = Field(ge=0, description="Start offset in milliseconds from scene start")
    end_ms: int = Field(gt=0, description="End offset in milliseconds from scene start")
    duration_ms: int = Field(gt=0, description="Beat duration in milliseconds")
    path: Path = Field(description="Local physical filesystem path to rendered beat MP4")


class BeatClipAssemblyResult(BaseModel):
    """Immutable result of assembling beat clips into a single scene visual clip."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    output_path: Path = Field(description="Path to concatenated scene visual MP4")
    beat_count: int = Field(ge=1, description="Number of assembled beat clips")
    expected_duration_ms: int = Field(gt=0, description="Expected duration in milliseconds")
    physical_duration_ms: float = Field(
        description="Authoritative physical duration in milliseconds probed via ffprobe"
    )
    content_sha256: str | None = Field(
        default=None, description="SHA-256 hex digest of the assembled video file"
    )


class BeatClipAssemblerError(ValueError):
    """Raised when beat clip assembly validation or execution fails."""


class BeatClipAssembler:
    """Deterministic assembler for intra-scene visual beat clips."""

    def __init__(self, renderer: FFmpegRenderer | None = None):
        self._renderer = renderer or FFmpegRenderer()

    def validate_clips(self, clips: Sequence[RenderedBeatClip]) -> list[RenderedBeatClip]:
        """Strictly validate contiguous ordering, timestamps, and input files.

        Returns clips sorted deterministically by materialized_index.
        """
        if not clips:
            raise BeatClipAssemblerError("Cannot assemble zero clips.")

        sorted_clips = sorted(clips, key=lambda c: c.materialized_index)

        # 1. Same parent_scene_index
        expected_scene_idx = sorted_clips[0].parent_scene_index
        for c in sorted_clips:
            if c.parent_scene_index != expected_scene_idx:
                raise BeatClipAssemblerError(
                    f"Cross-scene clip detected: clip index {c.materialized_index} "
                    f"has scene index {c.parent_scene_index}, expected {expected_scene_idx}."
                )

        # 2. Contiguous indices starting at 0 with no duplicates
        indices = [c.materialized_index for c in sorted_clips]
        if indices != list(range(len(sorted_clips))):
            raise BeatClipAssemblerError(
                f"Materialized indices must be contiguous starting from 0. Got: {indices}"
            )

        # 3. Timing validation: monotonic, contiguous, gapless, non-overlapping
        if sorted_clips[0].start_ms != 0:
            raise BeatClipAssemblerError(
                f"First clip must start at 0 ms. Got: {sorted_clips[0].start_ms}"
            )

        expected_time = 0
        for c in sorted_clips:
            if c.duration_ms <= 0:
                raise BeatClipAssemblerError(
                    f"Clip {c.materialized_index} has non-positive duration: {c.duration_ms} ms."
                )
            if c.end_ms - c.start_ms != c.duration_ms:
                raise BeatClipAssemblerError(
                    f"Clip {c.materialized_index} duration inconsistency: "
                    f"end_ms ({c.end_ms}) - start_ms ({c.start_ms}) != duration_ms ({c.duration_ms})."
                )
            if c.start_ms != expected_time:
                if c.start_ms < expected_time:
                    raise BeatClipAssemblerError(
                        f"Timing overlap detected at clip {c.materialized_index}: "
                        f"starts at {c.start_ms} ms, previous ended at {expected_time} ms."
                    )
                raise BeatClipAssemblerError(
                    f"Timing gap detected at clip {c.materialized_index}: "
                    f"starts at {c.start_ms} ms, previous ended at {expected_time} ms."
                )
            expected_time = c.end_ms

            # 4. File existence and non-empty check
            p = Path(c.path)
            if not p.is_file():
                raise BeatClipAssemblerError(
                    f"Clip file not found for beat {c.materialized_index}: {p}"
                )
            if p.stat().st_size == 0:
                raise BeatClipAssemblerError(
                    f"Clip file is empty (0 bytes) for beat {c.materialized_index}: {p}"
                )

        return sorted_clips

    async def assemble(
        self,
        clips: Sequence[RenderedBeatClip],
        *,
        output_path: Path | str,
        fps: int = 24,
    ) -> BeatClipAssemblyResult:
        """Validate and concatenate visual-only beat clips into a scene visual MP4."""
        if not 1 <= fps <= 60:
            raise BeatClipAssemblerError(f"Requested fps must be between 1 and 60, got {fps}.")

        validated_clips = self.validate_clips(clips)
        out_p = Path(output_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        # 1-frame tolerance + 2ms epsilon for ffprobe float conversions
        frame_tolerance_ms = (1000.0 / fps) + 2.0

        # 1. Physically probe every input clip before concatenation
        for c in validated_clips:
            p = Path(c.path)
            meta = await self._probe_media(p)
            fmt = meta.get("format", {})
            format_name = fmt.get("format_name", "")
            if not any(k in format_name for k in ("mp4", "mov", "matroska", "webm", "avi")):
                raise BeatClipAssemblerError(
                    f"Input clip {c.materialized_index} has unreadable or invalid container: {format_name}."
                )

            streams = meta.get("streams", [])
            video_streams = [s for s in streams if s.get("codec_type") == "video"]
            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

            if len(video_streams) != 1:
                raise BeatClipAssemblerError(
                    f"Input clip {c.materialized_index} must contain exactly one video stream, found {len(video_streams)}."
                )
            if len(audio_streams) > 0:
                raise BeatClipAssemblerError(
                    f"Input clip {c.materialized_index} contains {len(audio_streams)} audio stream(s); "
                    f"visual-only clips required."
                )

            duration_s_str = fmt.get("duration")
            if not duration_s_str:
                raise BeatClipAssemblerError(
                    f"Input clip {c.materialized_index} has no readable duration metadata."
                )
            probed_dur_ms = float(duration_s_str) * 1000.0
            if probed_dur_ms <= 0:
                raise BeatClipAssemblerError(
                    f"Input clip {c.materialized_index} has non-positive physical duration: {probed_dur_ms}ms."
                )
            if abs(probed_dur_ms - c.duration_ms) > frame_tolerance_ms:
                raise BeatClipAssemblerError(
                    f"Input clip {c.materialized_index} duration drift: physical duration {probed_dur_ms:.1f}ms "
                    f"exceeds tolerance ({frame_tolerance_ms:.1f}ms) against declared {c.duration_ms}ms."
                )

        clip_paths = [c.path for c in validated_clips]
        total_duration_ms = validated_clips[-1].end_ms
        scene_idx = validated_clips[0].parent_scene_index

        # 2. Concatenate clips using visual-only CFR encoding
        await self._renderer.concatenate_visual_clips(
            clip_paths=clip_paths,
            output_path=out_p,
            target_fps=fps,
        )

        if not out_p.is_file() or out_p.stat().st_size == 0:
            raise BeatClipAssemblerError(f"Assembly output missing or empty: {out_p}")

        # 3. Compute SHA256 of assembled file
        h = hashlib.sha256()
        with open(out_p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        content_sha256 = h.hexdigest()

        # 4. Physically probe final output video
        out_meta = await self._probe_media(out_p)
        out_fmt = out_meta.get("format", {})
        out_format_name = out_fmt.get("format_name", "")
        if not any(k in out_format_name for k in ("mp4", "mov", "matroska", "webm", "avi")):
            raise BeatClipAssemblerError(
                f"Assembled video has unreadable or invalid container: {out_format_name}."
            )

        out_streams = out_meta.get("streams", [])
        out_video = [s for s in out_streams if s.get("codec_type") == "video"]
        out_audio = [s for s in out_streams if s.get("codec_type") == "audio"]

        if len(out_video) != 1:
            raise BeatClipAssemblerError(
                f"Assembled video must contain exactly one video stream, found {len(out_video)}."
            )
        if len(out_audio) > 0:
            raise BeatClipAssemblerError(
                f"Assembled video contains {len(out_audio)} audio stream(s); visual-only output required."
            )

        v = out_video[0]
        if v.get("codec_name") != "h264":
            raise BeatClipAssemblerError(
                f"Assembled video codec is {v.get('codec_name')}, expected h264."
            )
        if int(v.get("width", 0)) <= 0 or int(v.get("height", 0)) <= 0:
            raise BeatClipAssemblerError("Assembled video has non-positive dimensions.")
        if v.get("pix_fmt") != "yuv420p":
            raise BeatClipAssemblerError(
                f"Assembled video pixel format is {v.get('pix_fmt')}, expected yuv420p."
            )

        # 5. FPS / CFR verification (avg_frame_rate and r_frame_rate)
        avg_fps_str = v.get("avg_frame_rate", "")
        if "/" in avg_fps_str:
            num, den = avg_fps_str.split("/")
            probed_avg_fps = float(num) / float(den) if float(den) > 0 else 0.0
        else:
            probed_avg_fps = float(avg_fps_str) if avg_fps_str else 0.0

        if abs(probed_avg_fps - fps) > 0.05:
            raise BeatClipAssemblerError(
                f"Assembled video average frame rate ({avg_fps_str} -> {probed_avg_fps:.2f}) "
                f"does not match requested fps {fps}."
            )

        r_fps_str = v.get("r_frame_rate", "")
        if "/" in r_fps_str:
            r_num, r_den = r_fps_str.split("/")
            probed_r_fps = float(r_num) / float(r_den) if float(r_den) > 0 else 0.0
        else:
            probed_r_fps = float(r_fps_str) if r_fps_str else 0.0

        if abs(probed_r_fps - fps) > 0.05:
            raise BeatClipAssemblerError(
                f"Assembled video real frame rate ({r_fps_str} -> {probed_r_fps:.2f}) "
                f"is incompatible with requested fps {fps}."
            )

        # 6. Physical duration verification against timeline
        out_duration_str = out_fmt.get("duration")
        if not out_duration_str:
            raise BeatClipAssemblerError("Assembled video has no readable duration metadata.")
        physical_duration_s = float(out_duration_str)
        physical_duration_ms = physical_duration_s * 1000.0

        if abs(physical_duration_ms - total_duration_ms) > frame_tolerance_ms:
            raise BeatClipAssemblerError(
                f"Assembled video duration drift: physical duration {physical_duration_ms:.1f}ms "
                f"exceeds tolerance ({frame_tolerance_ms:.1f}ms) against expected {total_duration_ms}ms."
            )

        return BeatClipAssemblyResult(
            parent_scene_index=scene_idx,
            output_path=out_p,
            beat_count=len(validated_clips),
            expected_duration_ms=total_duration_ms,
            physical_duration_ms=round(physical_duration_ms, 2),
            content_sha256=content_sha256,
        )

    async def _probe_media(self, video_path: Path) -> dict:
        """Probe physical video metadata via ffprobe."""
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(video_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise BeatClipAssemblerError(
                f"ffprobe failed for {video_path}: {stderr.decode('utf-8', errors='replace')}"
            )
        return json.loads(stdout.decode("utf-8"))
