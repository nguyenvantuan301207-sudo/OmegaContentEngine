"""Application-layer Beat Visual Renderer.

Renders physical visual-only MP4 clips for each BeatRenderUnit in an eligible BeatRenderPlan
using deterministic camera motion execution and asset bindings.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from omega.application.beat_asset_executor import BeatAssetExecutionResult
from omega.application.beat_clip_assembler import RenderedBeatClip
from omega.application.beat_render_adapter import BeatRenderPlan
from omega.application.editorial_beat import BeatMotionIntent, BeatTransitionIntent
from omega.application.template_payload_resolver import TemplatePayloadResolver
from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_template_renderer import VisualTemplateRenderer
from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime
from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderer


class BeatVisualRenderError(ValueError):
    """Raised when beat visual render validation or execution fails."""


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

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    clips: tuple[RenderedBeatClip, ...] = Field(
        default_factory=tuple, description="Rendered beat clips ready for assembly"
    )
    beat_metadata: tuple[BeatClipMetadata, ...] = Field(
        default_factory=tuple, description="Per-beat physical render metadata"
    )


class BeatVisualRenderer:
    """Deterministic physical renderer for multi-beat visual plans."""

    def __init__(
        self,
        *,
        payload_resolver: TemplatePayloadResolver | None = None,
        template_renderer: VisualTemplateRenderer | None = None,
        video_renderer: VisualV2VideoRenderer | None = None,
    ):
        self._payload_resolver = payload_resolver or TemplatePayloadResolver()
        self._template_renderer = template_renderer or VisualTemplateRenderer()
        self._video_renderer = video_renderer or VisualV2VideoRenderer()

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

        output_dir.mkdir(parents=True, exist_ok=True)

        rendered_clips: list[RenderedBeatClip] = []
        clip_metadata: list[BeatClipMetadata] = []

        for unit, executed in zip(render_plan.units, asset_execution.assets, strict=True):
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
            rendered_clips.append(rendered_clip)

            clip_metadata.append(
                BeatClipMetadata(
                    beat_index=unit.materialized_index,
                    template_id=unit.direction_view.template_id,
                    camera_motion_intent=unit.camera_motion_intent,
                    video_sha256=video_result.video_sha256,
                )
            )

        return BeatVisualRenderResult(
            parent_scene_index=render_plan.parent_scene_index,
            clips=tuple(rendered_clips),
            beat_metadata=tuple(clip_metadata),
        )
