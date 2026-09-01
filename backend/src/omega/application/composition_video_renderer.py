import asyncio
import math
import tempfile
from pathlib import Path

from pydantic import BaseModel

from omega.application.composition_frame_renderer import CompositionFrameRenderer
from omega.application.motion_runtime import MotionRuntime
from omega.application.motion_spec import SceneMotionPlan
from omega.application.scene_composition import SceneComposition


class RenderResult(BaseModel):
    output_path: Path
    width: int
    height: int
    fps: int
    duration_seconds: float
    frame_count: int


class RendererError(Exception):
    pass


class CompositionVideoRenderer:
    def __init__(self):
        self.motion_runtime = MotionRuntime()
        self.frame_renderer = CompositionFrameRenderer()

    async def render_clip(
        self,
        composition: SceneComposition,
        motion_plan: SceneMotionPlan,
        output_path: Path,
        fps: int = 12,
        timeout_seconds: int = 60,
    ) -> RenderResult:
        frame_count = max(1, math.ceil(composition.duration_seconds * fps))

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            for i in range(frame_count):
                time_seconds = i / fps
                states = self.motion_runtime.evaluate(composition, motion_plan, time_seconds)
                svg_data = self.frame_renderer.render_svg(composition, states)

                frame_path = temp_path / f"frame_{i:05d}.svg"
                with open(frame_path, "w", encoding="utf-8") as f:
                    f.write(svg_data)

            cmd = [
                "ffmpeg",
                "-y",
                "-framerate", str(fps),
                "-i", str(temp_path / "frame_%05d.svg"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", str(fps),
                "-preset", "veryfast",
                "-crf", "23",
                "-vf", f"scale={composition.width}:{composition.height}",
                str(output_path)
            ]

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)

                if process.returncode != 0:
                    err_tail = stderr.decode().strip()[-1000:] if stderr else "Unknown error"
                    raise RendererError(f"FFmpeg failed with code {process.returncode}: {err_tail}")

                if not output_path.exists() or output_path.stat().st_size == 0:
                    raise RendererError("Output file is missing or empty after successful FFmpeg run")

            except TimeoutError as e:
                try:
                    process.kill()
                    await process.communicate()
                except Exception:
                    pass
                raise RendererError("FFmpeg process timed out") from e

        actual_duration = frame_count / fps
        return RenderResult(
            output_path=output_path,
            width=composition.width,
            height=composition.height,
            fps=fps,
            duration_seconds=actual_duration,
            frame_count=frame_count
        )
