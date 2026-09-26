"""Application-layer Beat Visual Renderer.

Renders physical visual-only MP4 clips for each BeatRenderUnit in an eligible BeatRenderPlan
using deterministic camera motion execution and asset bindings.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from omega.application.beat_asset_executor import BeatAssetExecutionResult
from omega.application.beat_clip_assembler import RenderedBeatClip
from omega.application.beat_render_adapter import BeatRenderPlan, BeatRenderUnit
from omega.application.editorial_beat import BeatMotionIntent, BeatTransitionIntent
from omega.application.template_payload_resolver import TemplatePayloadResolver
from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_template_renderer import VisualTemplateRenderer
from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime
from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderer


class BeatVisualRenderError(ValueError):
    """Raised when beat visual render validation or execution fails."""


def _ceil_div(numerator: int, denominator: int) -> int:
    """Exact integer ceiling division for non-negative numerator and positive denominator."""
    return (numerator + denominator - 1) // denominator


def quantize_parent_frame_counts(
    intervals: Sequence[tuple[int, int]],
    global_duration_ms: int,
    fps: int,
) -> tuple[int, ...]:
    """Calculate deterministic cumulative frame-boundary allocations for parent scenes.

    Derives physical parent frame allocations aligned to the authoritative global timeline:
        start_frame_i = ceil_div(scene_start_ms * fps, 1000)
        end_frame_i = ceil_div(scene_end_ms * fps, 1000)
        parent_frame_count_i = end_frame_i - start_frame_i

    Guarantees:
        sum(parent_frame_counts) == ceil_div(global_duration_ms * fps, 1000) exactly.
    """
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise BeatVisualRenderError(f"fps must be a positive integer, got {fps!r}")
    if not intervals:
        raise BeatVisualRenderError("Cannot quantize frame counts for empty intervals")
    if isinstance(global_duration_ms, bool) or not isinstance(global_duration_ms, int) or global_duration_ms <= 0:
        raise BeatVisualRenderError(
            f"global_duration_ms must be a positive integer, got {global_duration_ms!r}"
        )

    first_start, _ = intervals[0]
    if first_start != 0:
        raise BeatVisualRenderError(
            f"First scene interval must start at 0 ms, got {first_start} ms"
        )

    for idx, (start_ms, end_ms) in enumerate(intervals):
        if end_ms <= start_ms:
            raise BeatVisualRenderError(
                f"Scene interval {idx} has non-positive duration: start={start_ms} ms, end={end_ms} ms"
            )
        if idx > 0:
            prev_end = intervals[idx - 1][1]
            if start_ms != prev_end:
                raise BeatVisualRenderError(
                    f"Non-contiguous scene intervals at index {idx}: "
                    f"starts at {start_ms} ms, previous ended at {prev_end} ms"
                )

    last_end = intervals[-1][1]
    if last_end != global_duration_ms:
        raise BeatVisualRenderError(
            f"Final scene end_ms ({last_end}) does not match global_duration_ms ({global_duration_ms})"
        )

    parent_frames: list[int] = []
    for idx, (start_ms, end_ms) in enumerate(intervals):
        start_frame = _ceil_div(start_ms * fps, 1000)
        end_frame = _ceil_div(end_ms * fps, 1000)
        count = end_frame - start_frame
        if count <= 0:
            raise BeatVisualRenderError(
                f"Scene interval {idx} quantized to non-positive frame count: {count}"
            )
        parent_frames.append(count)

    expected_total = _ceil_div(global_duration_ms * fps, 1000)
    if sum(parent_frames) != expected_total:
        raise BeatVisualRenderError(
            f"Sum of parent frames ({sum(parent_frames)}) != expected global total ({expected_total})"
        )

    return tuple(parent_frames)


def quantize_beat_frame_counts(
    units: Sequence[BeatRenderUnit],
    total_duration_ms: int,
    fps: int,
    *,
    timeline_start_ms: int = 0,
) -> tuple[int, ...]:
    """Calculate deterministic cumulative frame-boundary allocations for beat units.

    When timeline_start_ms is provided (for parent scenes in a global timeline),
    boundaries are quantized against the global absolute timeline:
        absolute_end_frame = ceil_div((timeline_start_ms + unit.end_ms) * fps, 1000)
        beat_frames = absolute_end_frame - previous_absolute_frame

    Guarantees:
        sum(beat_frames) == ceil_div((timeline_start_ms + total_duration_ms) * fps, 1000)
                           - ceil_div(timeline_start_ms * fps, 1000) exactly.
    """
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise BeatVisualRenderError(f"fps must be a positive integer, got {fps!r}")
    if isinstance(timeline_start_ms, bool) or not isinstance(timeline_start_ms, int) or timeline_start_ms < 0:
        raise BeatVisualRenderError(
            f"timeline_start_ms must be a non-negative integer, got {timeline_start_ms!r}"
        )
    if not units:
        raise BeatVisualRenderError("Cannot quantize frame counts for empty units")

    expected_total_frames = (
        _ceil_div((timeline_start_ms + total_duration_ms) * fps, 1000)
        - _ceil_div(timeline_start_ms * fps, 1000)
    )

    # Validate units ordering, contiguity, and positive durations
    for idx, unit in enumerate(units):
        if unit.materialized_index != idx:
            raise BeatVisualRenderError(
                f"Units must be ordered by materialized index: expected {idx}, got {unit.materialized_index}"
            )
        if unit.duration_ms <= 0:
            raise BeatVisualRenderError(
                f"Beat unit {unit.materialized_index} has non-positive duration: {unit.duration_ms} ms"
            )
        if unit.end_ms - unit.start_ms != unit.duration_ms:
            raise BeatVisualRenderError(
                f"Beat unit {unit.materialized_index} duration inconsistency: "
                f"end_ms ({unit.end_ms}) - start_ms ({unit.start_ms}) != duration_ms ({unit.duration_ms})"
            )
        if idx == 0:
            if unit.start_ms != 0:
                raise BeatVisualRenderError(
                    f"First beat unit must start at 0 ms, got {unit.start_ms} ms"
                )
        else:
            prev_unit = units[idx - 1]
            if unit.start_ms != prev_unit.end_ms:
                raise BeatVisualRenderError(
                    f"Non-contiguous beat boundaries at unit {unit.materialized_index}: "
                    f"starts at {unit.start_ms} ms, previous ended at {prev_unit.end_ms} ms"
                )

    if units[-1].end_ms != total_duration_ms:
        raise BeatVisualRenderError(
            f"Final beat end_ms ({units[-1].end_ms}) does not match total_duration_ms ({total_duration_ms})"
        )

    frame_counts: list[int] = []
    prev_end_frame = _ceil_div(timeline_start_ms * fps, 1000)

    for unit in units:
        cum_end_frame = _ceil_div((timeline_start_ms + unit.end_ms) * fps, 1000)
        frames = cum_end_frame - prev_end_frame
        if frames <= 0:
            raise BeatVisualRenderError(
                f"Beat unit {unit.materialized_index} quantized to non-positive frame count: {frames}"
            )
        frame_counts.append(frames)
        prev_end_frame = cum_end_frame

    if sum(frame_counts) != expected_total_frames:
        raise BeatVisualRenderError(
            f"Cumulative frame sum ({sum(frame_counts)}) does not match expected total ({expected_total_frames})"
        )

    return tuple(frame_counts)


class BeatClipMetadata(BaseModel):
    """Immutable per-beat metadata recorded during physical rendering."""

    model_config = ConfigDict(frozen=True)

    beat_index: int = Field(ge=0, description="0-indexed beat index")
    template_id: VisualTemplateId = Field(description="Resolved template ID")
    camera_motion_intent: BeatMotionIntent = Field(description="Applied camera motion intent")
    video_sha256: str = Field(description="SHA-256 hash of rendered beat MP4")


class BeatVisualRenderResult(BaseModel):
    """Immutable result of physically rendering all beats for a scene."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=0, description="0-indexed parent scene index")
    clips: tuple[RenderedBeatClip, ...] = Field(
        description="Physical beat clips rendered in order"
    )
    beat_metadata: tuple[BeatClipMetadata, ...] = Field(
        description="Per-beat rendering metadata"
    )


class BeatVisualRenderer:
    """Deterministic physical renderer for multi-beat visual plans."""

    def __init__(
        self,
        *,
        payload_resolver: TemplatePayloadResolver | None = None,
        template_renderer: VisualTemplateRenderer | None = None,
        video_renderer: VisualV2VideoRenderer | None = None,
        max_concurrency: int = 2,
    ):
        self._payload_resolver = payload_resolver or TemplatePayloadResolver()
        self._template_renderer = template_renderer or VisualTemplateRenderer()
        self._video_renderer = video_renderer or VisualV2VideoRenderer()
        self._max_concurrency = max(1, max_concurrency)

    async def render_plan(
        self,
        *,
        render_plan: BeatRenderPlan,
        asset_execution: BeatAssetExecutionResult,
        output_dir: Path,
        browser_runtime: BrowserCaptureRuntime,
        fps: int = 24,
        accent_color: str | None = None,
        bg_color: str | None = None,
        timeline_start_ms: int = 0,
    ) -> BeatVisualRenderResult:
        """Render all units in a BeatRenderPlan into physical MP4 clips."""
        if render_plan.parent_scene_index != asset_execution.parent_scene_index:
            raise BeatVisualRenderError(
                f"Scene index mismatch: plan={render_plan.parent_scene_index} "
                f"execution={asset_execution.parent_scene_index}"
            )

        if len(render_plan.units) != len(asset_execution.assets):
            raise BeatVisualRenderError(
                f"Beat count mismatch: plan has {len(render_plan.units)} units, "
                f"execution has {len(asset_execution.assets)} assets"
            )

        executed_indices = [a.beat_index for a in asset_execution.assets]
        if len(set(executed_indices)) != len(executed_indices):
            raise BeatVisualRenderError("Duplicate beat indices found in executed assets")

        # Quantize cumulative physical frame boundaries for all units
        beat_frame_counts = quantize_beat_frame_counts(
            render_plan.units,
            render_plan.total_duration_ms,
            fps,
            timeline_start_ms=timeline_start_ms,
        )

        output_dir.mkdir(parents=True, exist_ok=True)

        sem = asyncio.Semaphore(self._max_concurrency)

        async def render_one(unit: BeatRenderUnit, executed: ExecutedBeatAsset, frame_count: int):
            async with sem:
                if unit.materialized_index != executed.beat_index:
                    raise BeatVisualRenderError(
                        f"Beat index mismatch: unit={unit.materialized_index} executed={executed.beat_index}"
                    )

                if unit.asset_decision.action != executed.action:
                    raise BeatVisualRenderError(
                        f"Action mismatch at beat {unit.materialized_index}: "
                        f"plan={unit.asset_decision.action} executed={executed.action}"
                    )

                if unit.asset_decision.required_kind != executed.required_kind:
                    raise BeatVisualRenderError(
                        f"Kind mismatch at beat {unit.materialized_index}: "
                        f"plan={unit.asset_decision.required_kind} executed={executed.required_kind}"
                    )

                if unit.asset_decision.reuse_from_beat_index != executed.reuse_from_beat_index:
                    raise BeatVisualRenderError(
                        f"Reuse origin mismatch at beat {unit.materialized_index}: "
                        f"plan={unit.asset_decision.reuse_from_beat_index} "
                        f"executed={executed.reuse_from_beat_index}"
                    )

                if unit.transition_intent != BeatTransitionIntent.HARD_CUT:
                    raise BeatVisualRenderError(
                        f"Unsupported transition intent: {unit.transition_intent}. "
                        "Only HARD_CUT is supported in G2C2B."
                    )

                # Asset binding validation
                is_broll = unit.direction_view.template_id == VisualTemplateId.BROLL_EXPLAINER
                is_image = unit.direction_view.template_id == VisualTemplateId.IMAGE_EXPLAINER

                if is_broll:
                    if executed.bound_broll_asset is None:
                        raise BeatVisualRenderError(
                            f"Missing bound BROLL asset for BROLL beat {unit.materialized_index}"
                        )
                    if executed.bound_visual_asset is not None:
                        raise BeatVisualRenderError(
                            f"Unexpected visual asset on BROLL beat {unit.materialized_index}"
                        )
                    bound_assets = (executed.bound_broll_asset,)
                    broll_asset = executed.bound_broll_asset
                elif is_image:
                    if executed.bound_visual_asset is None:
                        raise BeatVisualRenderError(
                            f"Missing bound visual asset for IMAGE beat {unit.materialized_index}"
                        )
                    if executed.bound_broll_asset is not None:
                        raise BeatVisualRenderError(
                            f"Unexpected BROLL asset on IMAGE beat {unit.materialized_index}"
                        )
                    bound_assets = (executed.bound_visual_asset,)
                    broll_asset = None
                else:
                    if executed.bound_broll_asset is not None or executed.bound_visual_asset is not None:
                        raise BeatVisualRenderError(
                            f"Unexpected external assets on local template beat {unit.materialized_index}"
                        )
                    bound_assets = ()
                    broll_asset = None

                # 1. Resolve payload
                payload = self._payload_resolver.resolve(unit.scene_view, unit.direction_view)

                # 2. Render HTML template document
                doc = self._template_renderer.render(
                    payload,
                    assets=bound_assets,
                    accent_color=accent_color,
                    bg_color=bg_color,
                )

                # 3. Output path
                clip_name = f"scene_{unit.parent_scene_index:03d}_beat_{unit.materialized_index:03d}.mp4"
                clip_path = output_dir / clip_name

                # 4. Render clip
                duration_s = unit.duration_ms / 1000.0
                video_result = await self._video_renderer.render_clip(
                    document=doc,
                    motion_profile=unit.direction_view.motion_profile,
                    duration_seconds=duration_s,
                    output_path=clip_path,
                    browser_runtime=browser_runtime,
                    fps=fps,
                    broll_asset=broll_asset,
                    camera_motion_intent=unit.camera_motion_intent,
                    frame_count_override=frame_count,
                )

                # 5. Validate output MP4
                if not clip_path.exists() or clip_path.stat().st_size <= 0:
                    raise BeatVisualRenderError(f"Rendered beat clip {clip_path} is missing or empty")

                rendered_clip = RenderedBeatClip(
                    parent_scene_index=unit.parent_scene_index,
                    materialized_index=unit.materialized_index,
                    source_beat_index=unit.source_beat_index,
                    start_ms=unit.start_ms,
                    end_ms=unit.end_ms,
                    duration_ms=unit.duration_ms,
                    path=clip_path,
                )

                metadata = BeatClipMetadata(
                    beat_index=unit.materialized_index,
                    template_id=unit.direction_view.template_id,
                    camera_motion_intent=unit.camera_motion_intent,
                    video_sha256=video_result.video_sha256,
                )
                return rendered_clip, metadata

        tasks = [
            render_one(u, e, fc)
            for u, e, fc in zip(render_plan.units, asset_execution.assets, beat_frame_counts, strict=True)
        ]
        results = await asyncio.gather(*tasks)

        rendered_clips = [r[0] for r in results]
        clip_metadata = [r[1] for r in results]

        return BeatVisualRenderResult(
            parent_scene_index=render_plan.parent_scene_index,
            clips=tuple(rendered_clips),
            beat_metadata=tuple(clip_metadata),
        )
