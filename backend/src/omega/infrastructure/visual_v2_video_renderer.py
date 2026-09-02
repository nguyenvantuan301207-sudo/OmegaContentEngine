import asyncio
import hashlib
import math
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_dom_motion import VisualDomMotionError, VisualDomMotionRuntime
from omega.application.visual_template_renderer import RenderedTemplateDocument
from omega.infrastructure.browser_capture_runtime import BrowserCaptureError, BrowserCaptureRuntime


class VisualV2VideoRenderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_path: Path
    scene_index: int
    template_id: VisualTemplateId
    width: int
    height: int
    fps: int
    duration_seconds: float
    frame_count: int
    video_sha256: str
    source_html_sha256: str
    motion_profile: str | None


class VisualV2VideoRenderError(ValueError):
    pass


class VisualV2VideoRenderer:
    def __init__(self):
        self._motion_runtime = VisualDomMotionRuntime()

    async def render_clip(
        self,
        document: RenderedTemplateDocument,
        motion_profile: str | None,
        duration_seconds: float,
        output_path: Path,
        browser_runtime: BrowserCaptureRuntime,
        fps: int = 12,
        timeout_seconds: int = 120,
    ) -> VisualV2VideoRenderResult:

        if duration_seconds <= 0:
            raise VisualV2VideoRenderError("duration_seconds must be > 0.")
        if fps <= 0 or fps > 60:
            raise VisualV2VideoRenderError("fps must be > 0 and <= 60.")
        if document.width != 1920 or document.height != 1080:
            raise VisualV2VideoRenderError("document dimensions must be exactly 1920x1080.")
        if output_path.suffix.lower() != ".mp4":
            raise VisualV2VideoRenderError("output_path must have .mp4 suffix.")

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise VisualV2VideoRenderError(f"Failed to create output directory: {e}") from e

        frame_count = max(1, math.ceil(duration_seconds * fps))

        with tempfile.TemporaryDirectory() as temp_dir:
            for frame_index in range(frame_count):
                time_seconds = frame_index / fps

                try:
                    frame_doc = self._motion_runtime.render_frame(
                        document=document,
                        motion_profile=motion_profile,
                        time_seconds=time_seconds,
                        duration_seconds=duration_seconds,
                    )
                except VisualDomMotionError as e:
                    raise VisualV2VideoRenderError(f"VisualDomMotionError: {e}") from e

                try:
                    frame = await browser_runtime.capture(frame_doc)
                except BrowserCaptureError as e:
                    raise VisualV2VideoRenderError(f"BrowserCaptureError: {e}") from e
                except Exception as e:
                    raise VisualV2VideoRenderError(f"Browser capture failed unexpectedly: {e}") from e

                frame_name = f"frame_{frame_index:05d}.png"
                frame_path = os.path.join(temp_dir, frame_name)

                try:
                    with open(frame_path, "wb") as f:
                        f.write(frame.png_bytes)
                except Exception as e:
                    raise VisualV2VideoRenderError(f"Failed to write frame {frame_index}: {e}") from e

            for frame_index in range(frame_count):
                frame_name = f"frame_{frame_index:05d}.png"
                frame_path = os.path.join(temp_dir, frame_name)
                try:
                    if not os.path.exists(frame_path):
                        raise VisualV2VideoRenderError(f"Missing frame {frame_index}")
                    if os.path.getsize(frame_path) <= 0:
                        raise VisualV2VideoRenderError(f"Empty frame {frame_index}")
                except VisualV2VideoRenderError:
                    raise
                except Exception as e:
                    raise VisualV2VideoRenderError(f"Failed to validate frame {frame_index}: {e}") from e

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-framerate", str(fps),
                "-i", os.path.join(temp_dir, "frame_%05d.png"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", str(fps),
                "-preset", "veryfast",
                "-crf", "23",
                "-movflags", "+faststart",
                "-an",
                str(output_path),
            ]

            try:
                process = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except Exception as e:
                raise VisualV2VideoRenderError(f"Failed to start FFmpeg: {e}") from e

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
            except TimeoutError as e:
                process.kill()
                await process.communicate()
                raise VisualV2VideoRenderError(f"FFmpeg timed out after {timeout_seconds} seconds") from e

            if process.returncode != 0:
                raise VisualV2VideoRenderError(f"FFmpeg failed with exit code {process.returncode}:\n{stderr.decode(errors='ignore')}")

        try:
            if not output_path.exists():
                raise VisualV2VideoRenderError("FFmpeg output file missing.")
            if output_path.stat().st_size <= 0:
                raise VisualV2VideoRenderError("FFmpeg output file is empty.")

            with open(output_path, "rb") as f:
                header = f.read(1024)
                if b"ftyp" not in header:
                    raise VisualV2VideoRenderError("Output is not a valid MP4 (missing ftyp).")

                f.seek(0)
                sha256_hash = hashlib.sha256()
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(chunk)
                video_sha256 = sha256_hash.hexdigest()
        except VisualV2VideoRenderError:
            raise
        except Exception as e:
            raise VisualV2VideoRenderError(f"Failed to validate output MP4: {e}") from e

        return VisualV2VideoRenderResult(
            output_path=output_path,
            scene_index=document.scene_index,
            template_id=document.template_id,
            width=document.width,
            height=document.height,
            fps=fps,
            duration_seconds=duration_seconds,
            frame_count=frame_count,
            video_sha256=video_sha256,
            source_html_sha256=document.content_sha256,
            motion_profile=motion_profile,
        )
