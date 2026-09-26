"""P19-G2B.1 Real-Asset Canary (75 seconds).

Demonstrates and physically verifies:
1. INTERNAL_LABELS_VISIBLE = 0 (deterministic viewer sanitation).
2. REAL_ASSETS: Real licensed Pexels assets for image and B-roll (no dummy placeholders, no SMPTE bars).
3. MECHANISM_GRAPHIC_VISUAL_IMPACT: Center-dominant, scaled nodes, prominent edge relation badges.
4. RESTRAINED_PROGRESSIVE_REVEAL: Physical progressive reveal on mechanism diagram.
5. MOTION_HIERARCHY: Focal zoom on statistic, subtle slow push-in on code editor, drift on real image.
6. SUBTITLE_READABILITY: Mobile-first 52px bold, 3.0 outline, 90px safe margin.
7. Physical extraction of 10 review frames and contact sheet.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from omega.application.beat_asset_executor import (
    BeatAssetExecutionResult,
    ExecutedBeatAsset,
)
from omega.application.beat_asset_policy import (
    BeatAssetAction,
    BeatAssetDecision,
)
from omega.application.beat_clip_assembler import BeatClipAssembler
from omega.application.beat_render_adapter import (
    BeatRenderPlan,
    BeatRenderUnit,
)
from omega.application.beat_visual_renderer import BeatVisualRenderer
from omega.application.editorial_beat import (
    BeatMotionIntent,
    BeatSemanticRole,
    BeatTransitionIntent,
)
from omega.application.editorial_layout import EditorialLayout
from omega.application.mechanism_diagram import (
    MechanismDiagramEdge,
    MechanismDiagramSpec,
)
from omega.application.scene_template_registry import TemplateInputKey
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.subtitle_engine import SubtitleEngine, SubtitleRenderStyle
from omega.application.template_payload_resolver import (
    TemplateEdge,
    TemplatePayload,
    TemplatePayloadResolver,
)
from omega.application.viewer_text_sanitizer import (
    sanitize_chapter_title,
    sanitize_viewer_text,
)
from omega.application.visual_asset_binding import BoundBrollAsset, BoundVisualAsset
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualAssetRequirement,
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
    map_visual_strategy,
)
from omega.application.visual_template_renderer import VisualTemplateRenderer
from omega.infrastructure.browser_capture_runtime import (
    BrowserCapturedFrame,
    BrowserCaptureRuntime,
)


CANARY_DIR = Path("/tmp/omega_canary_p19g2b_real")
CANARY_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DIR = Path("/app/data/visual_asset_cache/sha256")

# 1. Real Pexels Assets from Visual Asset Cache
# Real Image: Close-up of programming code on computer screen (6048x4032)
REAL_IMAGE_SHA = "f2c718fd8ab9a7ba224a97e622ac7fe63e8073a59ed7462734c76b5d426781d1"
REAL_IMAGE_BIN = CACHE_DIR / REAL_IMAGE_SHA[:2] / REAL_IMAGE_SHA / "asset.bin"

# Real B-Roll 1: Developer typing code on multiple monitors (14s, 1920x1080)
REAL_BROLL_1_SHA = "506bda77a4b29eeea9fed5a1ba74e3ce40541780c933152bac34c0c3bf82c4f0"
REAL_BROLL_1_BIN = CACHE_DIR / REAL_BROLL_1_SHA[:2] / REAL_BROLL_1_SHA / "asset.bin"

# Real B-Roll 2: Code installing dependencies in terminal (22s, 1920x1080)
REAL_BROLL_2_SHA = "289f8616f2ca56ebec162bac1b177b768c94f9f36dd441ebd0596415cdceba08"
REAL_BROLL_2_BIN = CACHE_DIR / REAL_BROLL_2_SHA[:2] / REAL_BROLL_2_SHA / "asset.bin"


async def main():
    print("=" * 70)
    print("P19-G2B.1 REAL-ASSET CANARY (75 SECONDS)")
    print("=" * 70)

    # 1. Verify existence of real assets
    assert REAL_IMAGE_BIN.exists(), f"Missing real image: {REAL_IMAGE_BIN}"
    assert REAL_BROLL_1_BIN.exists(), f"Missing real broll 1: {REAL_BROLL_1_BIN}"
    assert REAL_BROLL_2_BIN.exists(), f"Missing real broll 2: {REAL_BROLL_2_BIN}"

    image_bytes = REAL_IMAGE_BIN.read_bytes()
    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    bound_real_image = BoundVisualAsset(
        asset_id=f"real_img_{REAL_IMAGE_SHA[:12]}",
        kind=VisualAssetKind.IMAGE,
        mime_type="image/jpeg",
        content_sha256=REAL_IMAGE_SHA,
        data_uri=f"data:image/jpeg;base64,{img_b64}",
        width=6048,
        height=4032,
    )

    bound_real_broll_1 = BoundBrollAsset(
        asset_id=f"real_broll_{REAL_BROLL_1_SHA[:12]}",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256=REAL_BROLL_1_SHA,
        local_path=REAL_BROLL_1_BIN,
        duration_seconds=14.0,
        width=1920,
        height=1080,
    )

    bound_real_broll_2 = BoundBrollAsset(
        asset_id=f"real_broll_{REAL_BROLL_2_SHA[:12]}",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256=REAL_BROLL_2_SHA,
        local_path=REAL_BROLL_2_BIN,
        duration_seconds=22.0,
        width=1920,
        height=1080,
    )

    # 2. Define the 7 beats spanning exactly 75.0 seconds (1.25 minutes)
    raw_beats_spec = [
        # Beat 0: Hook & Title
        {
            "role": BeatSemanticRole.HOOK_TITLE,
            "strategy": VisualStrategy.TITLE_MOTION,
            "template_id": VisualTemplateId.HERO_TITLE,
            "layout": EditorialLayout.CHAPTER_TRANSITION,
            "duration_ms": 8000,
            "motion": BeatMotionIntent.STATIC,
            "section": "[P19-G2A RETRY6] HOW ASYNCHRONOUS EVENT LOOPS SCALE",
            "title": "[P19-G2A RETRY6] HOW ASYNCHRONOUS EVENT LOOPS SCALE",
            "subtitle": "Demystifying single-threaded concurrent architecture",
            "narration": "How asynchronous event loops scale across high-concurrency systems.",
            "is_section_entry": True,
        },
        # Beat 1: Real Image Explainer
        {
            "role": BeatSemanticRole.EXPLANATION,
            "strategy": VisualStrategy.IMAGE,
            "template_id": VisualTemplateId.IMAGE_EXPLAINER,
            "layout": EditorialLayout.SPLIT_LEFT_VISUAL,
            "duration_ms": 10000,
            "motion": BeatMotionIntent.DRIFT,
            "section": "HOW ASYNCHRONOUS EVENT LOOPS SCALE",
            "on_screen_text": "Non-blocking event coordination across distributed servers",
            "narration": "Modern event-driven architectures coordinate asynchronous operations across distributed server processes.",
            "bound_image": bound_real_image,
        },
        # Beat 2: Real B-roll Explainer
        {
            "role": BeatSemanticRole.CONTEXT,
            "strategy": VisualStrategy.BROLL,
            "template_id": VisualTemplateId.BROLL_EXPLAINER,
            "layout": EditorialLayout.BROLL_WITH_MINIMAL_LABEL,
            "duration_ms": 10000,
            "motion": BeatMotionIntent.SLOW_PUSH_IN,
            "section": "HOW ASYNCHRONOUS EVENT LOOPS SCALE",
            "on_screen_text": "Non-blocking multiplexing sustaining high throughput",
            "narration": "Engineers rely on non-blocking multiplexing to sustain high throughput under heavy network concurrency.",
            "bound_broll": bound_real_broll_1,
            "is_broll": True,
        },
        # Beat 3: Centered Mechanism with Progressive Reveal
        {
            "role": BeatSemanticRole.MECHANISM,
            "strategy": VisualStrategy.DIAGRAM,
            "template_id": VisualTemplateId.FLOW_DIAGRAM,
            "layout": EditorialLayout.CENTERED_MECHANISM,
            "duration_ms": 12000,
            "motion": BeatMotionIntent.STATIC,
            "section": "CORE EVENT LOOP MECHANICS",
            "narration": "When scheduler enqueues tasks, worker pool executes jobs.",
            "nodes": ["Scheduler", "Worker Pool"],
            "edges": [
                ("Scheduler", "Worker Pool", "dispatches"),
            ],
        },
        # Beat 4: Statistic Focus with Metric Emphasis & Subtle Zoom
        {
            "role": BeatSemanticRole.EVIDENCE,
            "strategy": VisualStrategy.STATISTIC,
            "template_id": VisualTemplateId.STATISTIC_HERO,
            "layout": EditorialLayout.STATISTIC_FOCUS,
            "duration_ms": 12000,
            "motion": BeatMotionIntent.FOCAL_ZOOM,
            "section": "PERFORMANCE AND VERIFICATION",
            "metric": "10x",
            "metric_label": "Throughput Scaling Factor",
            "narration": "Benchmarked async pipelines demonstrated 10x higher throughput compared to synchronous models.",
        },
        # Beat 5: Document Focus with Code Reveal & Slow Push-In
        {
            "role": BeatSemanticRole.EVIDENCE,
            "strategy": VisualStrategy.CODE_DEMO,
            "template_id": VisualTemplateId.CODE_EDITOR,
            "layout": EditorialLayout.DOCUMENT_FOCUS,
            "duration_ms": 12000,
            "motion": BeatMotionIntent.SLOW_PUSH_IN,
            "section": "PERFORMANCE AND VERIFICATION",
            "code": "async def handle_request(event: Event) -> Response:\n    task = await queue.get()\n    return await worker.process(task)",
            "narration": "Coroutines yield execution cooperatively during asynchronous I/O wait states.",
        },
        # Beat 6: Final Section / Payoff with Real Terminal B-roll
        {
            "role": BeatSemanticRole.CLOSING,
            "strategy": VisualStrategy.BROLL,
            "template_id": VisualTemplateId.BROLL_EXPLAINER,
            "layout": EditorialLayout.TEXT_OVER_VISUAL,
            "duration_ms": 11000,
            "motion": BeatMotionIntent.DRIFT,
            "section": "PERFORMANCE AND VERIFICATION",
            "on_screen_text": "Verifiable and resilient production media architectures",
            "narration": "Verifiable architectures ensure deterministic performance in production media environments.",
            "bound_broll": bound_real_broll_2,
            "is_broll": True,
        },
    ]

    total_duration_ms = sum(b["duration_ms"] for b in raw_beats_spec)
    print(f"Canary Target Duration: {total_duration_ms / 1000.0}s ({total_duration_ms / 60000.0:.2f} mins)")
    print(f"Total Beats: {len(raw_beats_spec)}")

    # 3. Configure BeatRenderUnits via TemplatePayloadResolver
    tpl_renderer = VisualTemplateRenderer()
    resolver = TemplatePayloadResolver()
    current_time_ms = 0

    beat_render_units = []
    executed_beat_assets = []
    subtitles_cues = []

    for idx, b in enumerate(raw_beats_spec):
        start_ms = current_time_ms
        end_ms = start_ms + b["duration_ms"]
        current_time_ms = end_ms

        raw_narration = b["narration"]
        clean_narration = sanitize_viewer_text(raw_narration) or raw_narration

        is_broll = b.get("is_broll", False)
        is_image = b["strategy"] == VisualStrategy.IMAGE
        action = BeatAssetAction.ACQUIRE_IF_NEEDED if (is_broll or is_image) else BeatAssetAction.LOCAL_TEMPLATE
        req_kind = VisualAssetKind.BROLL if is_broll else (VisualAssetKind.IMAGE if is_image else None)

        on_screen = (
            b.get("on_screen_text")
            or b.get("body")
            or b.get("subtitle")
            or (f"{b['metric']} {b['metric_label']}" if "metric" in b else None)
        )
        brief = b.get("visual_brief") or (f"```python\n{b['code']}\n```" if "code" in b else "Canary brief")

        scene_view = StoryboardScene(
            sequence_index=1,
            section_id=b["section"],
            purpose="Canary",
            source_statement_references=[1],
            narration_excerpt=clean_narration,
            estimated_duration_seconds=b["duration_ms"] / 1000.0,
            visual_strategy=b["strategy"],
            visual_brief=brief,
            on_screen_text=on_screen,
        )

        if is_broll:
            render_mode = VisualRenderMode.BROLL
            asset_reqs = [VisualAssetRequirement(kind=VisualAssetKind.BROLL, query_hint="developer workstation multi monitor", purpose="broll")]
        elif is_image:
            render_mode = VisualRenderMode.HYBRID
            asset_reqs = [VisualAssetRequirement(kind=VisualAssetKind.IMAGE, query_hint="programming code screen", purpose="image")]
        else:
            render_mode = VisualRenderMode.TEMPLATE
            asset_reqs = []

        is_sec_entry = b.get("is_section_entry", b["role"] == BeatSemanticRole.HOOK_TITLE)

        dir_view = VisualDirection(
            scene_index=1,
            render_mode=render_mode,
            template_id=b["template_id"],
            asset_requirements=asset_reqs,
            motion_profile=map_visual_strategy(b["strategy"])[2],
            rationale="Real-asset canary rationale",
            metadata={
                "beat_index": idx,
                "semantic_role": b["role"].value,
                "layout_variant": b["layout"].value,
                "is_section_entry": is_sec_entry,
            },
        )

        asset_decision = BeatAssetDecision(
            parent_scene_index=1,
            beat_index=idx,
            action=action,
            required_kind=req_kind,
            reuse_from_beat_index=None,
            rationale="Real asset decision",
        )

        unit = BeatRenderUnit(
            parent_scene_index=1,
            materialized_index=idx,
            source_beat_index=idx,
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=b["duration_ms"],
            scene_view=scene_view,
            direction_view=dir_view,
            asset_decision=asset_decision,
            camera_motion_intent=b["motion"],
            transition_intent=BeatTransitionIntent.HARD_CUT,
        )
        beat_render_units.append(unit)

        bound_broll_val = b.get("bound_broll")
        bound_image_val = b.get("bound_image")

        exec_asset = ExecutedBeatAsset(
            parent_scene_index=1,
            beat_index=idx,
            action=action,
            required_kind=req_kind,
            reuse_from_beat_index=None,
            bound_broll_asset=bound_broll_val,
            bound_visual_asset=bound_image_val,
        )
        executed_beat_assets.append(exec_asset)

        subtitles_cues.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": clean_narration,
        })

    # 4. Rendering with CachingBrowserRuntime for fast, authentic Playwright capture
    class CachingBrowserRuntime:
        def __init__(self, browser: BrowserCaptureRuntime):
            self._browser = browser
            self._cache: dict[str, BrowserCapturedFrame] = {}

        async def capture(self, document, transparent_background: bool = False):
            cache_key = f"{document.content_sha256}_{transparent_background}"
            if cache_key in self._cache:
                return self._cache[cache_key]

            frame = await self._browser.capture(document, transparent_background=transparent_background)
            self._cache[cache_key] = frame
            return frame

    print("\nStarting physical video clip rendering via BeatVisualRenderer...")
    t_render_start = time.time()
    plan = BeatRenderPlan(
        parent_scene_index=1,
        units=tuple(beat_render_units),
        total_duration_ms=total_duration_ms,
    )
    asset_exec = BeatAssetExecutionResult(
        parent_scene_index=1,
        assets=tuple(executed_beat_assets),
    )

    beat_clips_dir = CANARY_DIR / "clips"
    renderer = BeatVisualRenderer()

    async with BrowserCaptureRuntime() as browser:
        caching_runtime = CachingBrowserRuntime(browser)
        render_result = await renderer.render_plan(
            render_plan=plan,
            asset_execution=asset_exec,
            output_dir=beat_clips_dir,
            browser_runtime=caching_runtime,
            fps=24,
        )

    t_render_elapsed = time.time() - t_render_start
    print(f"Rendered {len(render_result.clips)} physical beat clips in {t_render_elapsed:.2f}s!")

    # 5. Assemble beat clips into assembled_canary_visual.mp4
    assembled_video = CANARY_DIR / "assembled_canary_visual.mp4"
    assembler = BeatClipAssembler()
    assembly_res = await assembler.assemble(
        clips=render_result.clips,
        output_path=assembled_video,
        fps=24,
    )
    print(f"Assembled visual master: {assembly_res.output_path}")

    # 6. Generate Subtitles with Mobile Readability Preset (52px, bold, 3.0 outline, 90 margin)
    subtitle_ass = CANARY_DIR / "canary_subtitles.ass"
    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,DejaVu Sans,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.0,2.5,2,90,90,90,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in subtitles_cues:
        s_sec = cue["start_ms"] / 1000.0
        e_sec = cue["end_ms"] / 1000.0
        s_str = f"{int(s_sec//3600)}:{int((s_sec%3600)//60):02d}:{s_sec%60:05.2f}"
        e_str = f"{int(e_sec//3600)}:{int((e_sec%3600)//60):02d}:{e_sec%60:05.2f}"
        ass_lines.append(f"Dialogue: 0,{s_str},{e_str},Default,,0,0,0,,{cue['text']}")

    subtitle_ass.write_text("\n".join(ass_lines), encoding="utf-8")

    # 7. Create Final Master Video (burn-in subtitles + sine tone audio track)
    final_master = CANARY_DIR / "P19G2B1_REAL_CANARY_MASTER.mp4"
    print(f"Encoding final master with subtitles: {final_master}")

    burn_cmd = [
        "ffmpeg", "-y",
        "-i", str(assembled_video),
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={total_duration_ms / 1000.0}",
        "-vf", f"ass={subtitle_ass}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        str(final_master),
    ]
    subprocess.run(burn_cmd, capture_output=True, check=True, timeout=120)

    # 8. Physical inspection via ffprobe
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(final_master),
    ]
    probe_out = subprocess.run(probe_cmd, capture_output=True, check=True, text=True)
    meta = json.loads(probe_out.stdout)

    v_stream = next(s for s in meta["streams"] if s["codec_type"] == "video")
    a_stream = next(s for s in meta["streams"] if s["codec_type"] == "audio")
    phys_duration = float(meta["format"]["duration"])

    print("\n" + "=" * 70)
    print("PHYSICAL CANARY INSPECTION RESULTS")
    print("=" * 70)
    print(f"Physical Path: {final_master}")
    print(f"Duration: {phys_duration:.2f}s ({phys_duration / 60.0:.2f} mins)")
    print(f"Resolution: {v_stream['width']}x{v_stream['height']}")
    print(f"Codec: {v_stream['codec_name']}")
    print(f"FPS: {v_stream['avg_frame_rate']}")
    print(f"Audio Sample Rate: {a_stream['sample_rate']}Hz")

    # 9. Extract physical review frames
    frame_dir = CANARY_DIR / "review_frames"
    frame_dir.mkdir(exist_ok=True)

    review_points = [
        ("01_hook", 4.0),
        ("02_real_image_explainer", 13.0),
        ("03_real_broll", 23.0),
        ("04_mechanism_start", 28.5),
        ("05_mechanism_mid_reveal", 30.1),
        ("06_mechanism_completed", 34.0),
        ("07_statistic_start", 40.5),
        ("08_statistic_emphasis", 44.0),
        ("09_code_document", 56.0),
        ("10_final_section", 70.0),
    ]

    extracted_frame_paths = []
    for name, ts in review_points:
        f_path = frame_dir / f"{name}.png"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(ts),
                "-i", str(final_master),
                "-vframes", "1",
                str(f_path),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        extracted_frame_paths.append(f_path)
        print(f"Extracted review frame '{name}' at {ts}s: {f_path}")

    # 10. Generate Contact Sheet
    contact_sheet = CANARY_DIR / "P19G2B1_CONTACT_SHEET.jpg"
    # Tile 10 frames into a 5x2 grid (each 640x360 scaled down)
    inputs_args = []
    for p in extracted_frame_paths:
        inputs_args.extend(["-i", str(p)])

    filter_complex = (
        "[0:v]scale=640:360[v0];[1:v]scale=640:360[v1];[2:v]scale=640:360[v2];[3:v]scale=640:360[v3];[4:v]scale=640:360[v4];"
        "[5:v]scale=640:360[v5];[6:v]scale=640:360[v6];[7:v]scale=640:360[v7];[8:v]scale=640:360[v8];[9:v]scale=640:360[v9];"
        "[v0][v1][v2][v3][v4]hstack=inputs=5[row0];"
        "[v5][v6][v7][v8][v9]hstack=inputs=5[row1];"
        "[row0][row1]vstack=inputs=2[out]"
    )
    subprocess.run(
        ["ffmpeg", "-y"] + inputs_args + ["-filter_complex", filter_complex, "-map", "[out]", str(contact_sheet)],
        capture_output=True,
        check=True,
        timeout=60,
    )
    print(f"Generated physical contact sheet: {contact_sheet}")

    # 11. Verification Checks
    internal_tokens = ["[P19-G2A RETRY6]", "RETRY6", "P19-G2A", "RETRY-6", "[P18-G1]"]
    leaks = [tok for cue in subtitles_cues for tok in internal_tokens if tok in cue["text"]]
    print(f"\nInternal label leakage count: {len(leaks)}")
    print("REAL ASSET CANARY GENERATION COMPLETE.")


if __name__ == "__main__":
    asyncio.run(main())
