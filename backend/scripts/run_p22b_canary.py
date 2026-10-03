"""Bounded, isolated P22-B multi-beat physical render canary."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from uuid import UUID

from omega.application.beat_clip_assembler import BeatClipAssembler, RenderedBeatClip
from omega.application.beat_render_adapter import adapt_camera_plan
from omega.application.camera_transition_director import CameraTransitionDirector
from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_template_renderer import RenderedTemplateDocument
from omega.domain.visual_beat import ContinuityDecisionType, VisualBeat, VisualRole
from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime
from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderer


def _beat(index: int, role: VisualRole, decision: ContinuityDecisionType | None) -> VisualBeat:
    return VisualBeat(
        id=UUID(int=index + 101),
        scene_id="p22b-canary",
        parent_scene_index=1,
        beat_index=index,
        source_editorial_beat_indices=(index,),
        start_offset_ms=index * 1500,
        end_offset_ms=(index + 1) * 1500,
        duration_ms=1500,
        narration_text=f"Canary beat {index}",
        visual_intent=f"P22-B state {index}",
        information_goal=f"Verify physical state {index}",
        visual_role=role,
        preferred_asset_type="DATA_CARD",
        importance="HIGH" if role == VisualRole.REVEAL else "NORMAL",
        continuity_decision=decision,
    )


def _document(index: int) -> RenderedTemplateDocument:
    colors = ("#172554", "#14532d", "#581c87", "#7f1d1d")
    html = f"""<!doctype html><html><head><style>
    html,body{{margin:0;width:1920px;height:1080px;overflow:hidden;background:{colors[index]};}}
    .stat-content{{position:absolute;inset:0;display:grid;place-items:center;color:white;
      font:700 92px Arial,sans-serif;}}
    </style></head><body><div class="stat-content">P22-B BEAT {index + 1}</div></body></html>"""
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.STATISTIC_HERO,
        width=1920,
        height=1080,
        html=html,
        semantic_element_ids=("stat-content",),
        content_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
    )


async def run(output: Path) -> dict[str, object]:
    beats = (
        _beat(0, VisualRole.DATA, None),
        _beat(1, VisualRole.REVEAL, ContinuityDecisionType.REPLACE),
        _beat(2, VisualRole.EXPLAIN, ContinuityDecisionType.KEEP),
        _beat(3, VisualRole.REVEAL, ContinuityDecisionType.SWITCH_CONTEXT),
    )
    directed = CameraTransitionDirector.direct(beats)
    output.parent.mkdir(parents=True, exist_ok=True)
    clips: list[RenderedBeatClip] = []
    renderer = VisualV2VideoRenderer()
    async with BrowserCaptureRuntime(max_concurrency=2) as browser:
        for index, camera_plan in enumerate(directed.camera_plans):
            motion, focus, strength, _fallback = adapt_camera_plan(
                camera_plan, VisualTemplateId.STATISTIC_HERO
            )
            clip_path = output.parent / f"p22b_beat_{index}.mp4"
            await renderer.render_clip(
                document=_document(index),
                motion_profile=None,
                duration_seconds=1.5,
                output_path=clip_path,
                browser_runtime=browser,
                fps=6,
                camera_motion_intent=motion,
                camera_focus_region=focus,
                camera_motion_strength=strength,
                frame_count_override=9,
            )
            clips.append(
                RenderedBeatClip(
                    parent_scene_index=1,
                    materialized_index=index,
                    source_beat_index=index,
                    start_ms=index * 1500,
                    end_ms=(index + 1) * 1500,
                    duration_ms=1500,
                    path=clip_path,
                )
            )

    assembled = await BeatClipAssembler().assemble(clips, output_path=output, fps=6)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(output)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    rendered_duration = float(json.loads(probe.stdout)["format"]["duration"])
    result = {
        "artifact": str(output),
        "beat_count": assembled.beat_count,
        "camera_operations": [plan.intent.value for plan in directed.camera_plans],
        "transition_types_requested": [
            plan.requested_intent.value for plan in directed.transition_plans
        ],
        "transition_types_applied": [
            plan.applied_intent.value for plan in directed.transition_plans
        ],
        "planned_duration_seconds": 6.0,
        "rendered_duration_seconds": rendered_duration,
        "all_beats_present": len({clip.path.stat().st_size for clip in clips}) >= 2
        and assembled.beat_count == 4,
        "safe_bounds": all(
            1.0 <= state.scale <= 1.2
            and 0.0 <= state.center_x <= 1.0
            and 0.0 <= state.center_y <= 1.0
            for plan in directed.camera_plans
            for state in (plan.start_state, plan.end_state)
        ),
        "duration_preserved": abs(rendered_duration - 6.0) <= (1 / 6 + 0.01),
        "sha256": assembled.content_sha256,
    }
    result["qa_pass"] = bool(
        result["all_beats_present"] and result["safe_bounds"] and result["duration_preserved"]
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output.resolve())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
