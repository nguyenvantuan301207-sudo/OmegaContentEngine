"""Run one isolated P15-A render-truth canary without DB writes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock

from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import LocalTTSNarrationProvider, get_narration_provider
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.application.visual_production_v2_service import VisualProductionV2Service
from omega.infrastructure.models import (
    ChannelDNARevision,
    ContentGenerationRequest,
    Mission,
    MissionExecution,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe(path: Path) -> dict:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class InMemorySession:
    def __init__(self, execution: MissionExecution, request: ContentGenerationRequest):
        self.execution = execution
        self.request = request
        self.execute_count = 0

    async def execute(self, statement):
        self.execute_count += 1
        result = MagicMock()
        sql = str(statement).lower()
        if "mission_executions" in sql:
            result.scalar_one_or_none.return_value = self.execution
        elif "content_generation_requests" in sql:
            result.scalar_one_or_none.return_value = self.request
        else:
            raise RuntimeError(f"Unexpected in-memory query: {sql[:160]}")
        return result


def _make_lineage():
    channel_id, dna_id, mission_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    execution_id, request_id = uuid.uuid4(), uuid.uuid4()
    mission = Mission(id=mission_id, channel_id=channel_id, title="P15-A render truth canary")
    dna = ChannelDNARevision(
        id=dna_id,
        channel_id=channel_id,
        version=1,
        snapshot={},
        change_reason="P15-A non-persisted fixture",
    )
    execution = MissionExecution(
        id=execution_id,
        mission_id=mission_id,
        channel_dna_revision_id=dna_id,
    )
    execution.mission = mission
    execution.channel_dna_revision = dna
    texts = [
        "A long production title enters OMEGA, where deterministic wrapping preserves readable words, honest layout decisions, and complete narration timing.",
        "The final renderer burns bright yellow subtitles, keeps every spoken phrase intact, and normalizes each joined scene to a truthful constant frame rate.",
    ]
    script = ScriptVersion(
        id=uuid.uuid4(),
        version=1,
        title="P15-A render truth",
        estimated_duration_seconds=18,
    )
    script.sections = []
    for index, text in enumerate(texts, start=1):
        section = ScriptSection(
            id=uuid.uuid4(),
            section_order=index,
            heading=("A deliberately long title that must fit predictably" if index == 1 else "Render truth"),
            narration_text=text,
            estimated_duration_seconds=8,
        )
        statement = ScriptStatement(
            id=uuid.uuid4(),
            statement_order=1,
            statement_text=text,
            statement_type="ASSERTION",
        )
        statement.citations = []
        section.statements = [statement]
        script.sections.append(section)
    request = ContentGenerationRequest(
        id=request_id,
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        mission_execution_id=execution_id,
    )
    request.scripts = [script]
    return channel_id, execution, request


def _fraction(value: str) -> float:
    numerator, denominator = (int(part) for part in value.split("/"))
    return numerator / denominator if denominator else 0.0


async def main() -> None:
    run_id = uuid.uuid4().hex[:12]
    root = Path(f"/tmp/omega_p15a_render_truth_canary_{run_id}")
    media_root, render_root = root / "media", root / "render"
    subtitle_dir, frame_dir = root / "subtitles", root / "frames"
    subtitle_dir.mkdir(parents=True)
    frame_dir.mkdir(parents=True)
    model_dir = Path("/app/models/tts/kokoro")
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "PEXELS_API_KEY", "ELEVENLABS_API_KEY"):
        os.environ.pop(name, None)
    os.environ.update({
        "TTS_PROVIDER": "local",
        "LOCAL_TTS_PROFILE": "quality",
        "LOCAL_TTS_MODEL_DIR": str(model_dir),
        "LOCAL_TTS_DEVICE": "auto",
        "OMEGA_VISUAL_ASSET_MODE": "LOCAL_TEMPLATE_ONLY",
    })

    external_socket_attempts: list[str] = []
    original_connect = socket.socket.connect

    def guarded_connect(sock, address):
        if isinstance(address, tuple) and str(address[0]).lower() not in {"127.0.0.1", "::1", "localhost"}:
            external_socket_attempts.append(str(address))
            raise RuntimeError(f"Outbound network blocked for canary: {address}")
        return original_connect(sock, address)

    socket.socket.connect = guarded_connect
    channel_id, execution, request = _make_lineage()
    storage = LocalMediaStorageProvider(base_root=str(media_root))
    provider = get_narration_provider(storage)
    if not isinstance(provider, LocalTTSNarrationProvider):
        raise RuntimeError(f"Unexpected narration provider: {type(provider).__name__}")
    style = SubtitleRenderStyle(
        font_family="DejaVu Sans",
        font_size=54,
        min_font_size=36,
        bold=True,
        primary_color="#FFD400",
        outline_color="#101010",
        outline_width=3,
        shadow=1,
        alignment=2,
        margin_v=135,
        max_lines=2,
        max_width_ratio=0.72,
    )
    service = VisualProductionV2Service(
        asset_orchestrator=None,
        output_root=render_root,
        narration_provider=provider,
        narration_storage=storage,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    original_burn = service._ffmpeg_renderer.burn_ass_subtitles
    subtitle_paths: list[Path] = []

    async def captured_burn(*args, **kwargs):
        ass_path = Path(kwargs["ass_path"])
        captured_path = subtitle_dir / ass_path.name
        shutil.copy2(ass_path, captured_path)
        subtitle_paths.append(captured_path)
        await original_burn(*args, **kwargs)

    service._ffmpeg_renderer.burn_ass_subtitles = captured_burn
    session = InMemorySession(execution, request)
    try:
        result = await service.render_mission_execution(
            session,
            execution.id,
            request.id,
            fps=12,
            voice_profile={"voice": "am_michael", "speed": 1.0},
            subtitle_enabled=True,
            subtitle_style=style,
        )
    finally:
        socket.socket.connect = original_connect

    final_path = result.output_path
    probe = _probe(final_path)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    scene_starts = [0.8, result.runtime_scenes[0].duration_seconds + 0.8]
    frame_paths: list[Path] = []
    for index, timestamp in enumerate(scene_starts, start=1):
        frame_path = frame_dir / f"scene_{index:03d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(final_path), "-frames:v", "1", str(frame_path)],
            check=True,
            capture_output=True,
        )
        frame_paths.append(frame_path)

    silence = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(final_path), "-af", "silencedetect=noise=-45dB:d=0.35", "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
    ).stderr
    duration = float(probe["format"]["duration"])
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", silence)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", silence)]
    trailing_silence = 0.0
    if starts and (len(ends) < len(starts) or (ends and ends[-1] >= duration - 0.12)):
        trailing_silence = max(0.0, duration - starts[-1])

    report = {
        "root": str(root),
        "scene_count": result.scene_count,
        "scene_durations": [scene.duration_seconds for scene in result.runtime_scenes],
        "text_fitting": [scene.text_fitting for scene in result.runtime_scenes],
        "text_truncated": [scene.text_truncated for scene in result.runtime_scenes],
        "subtitle_style_resolved": result.subtitle_style_applied.model_dump(),
        "subtitle_artifact_paths": [str(path) for path in subtitle_paths],
        "subtitle_cue_count": len(result.runtime_subtitle_cues),
        "subtitle_text_truncated": [scene.subtitle_text_truncated for scene in result.runtime_scenes],
        "target_fps": result.fps,
        "effective_fps_mode": result.effective_fps_mode,
        "r_frame_rate": video["r_frame_rate"],
        "avg_frame_rate": video["avg_frame_rate"],
        "avg_fps": _fraction(video["avg_frame_rate"]),
        "frame_count": int(video.get("nb_frames", 0)),
        "time_base": video["time_base"],
        "video_duration": float(video["duration"]),
        "audio_duration": float(audio["duration"]),
        "container_duration": duration,
        "runtime_timeline_ms": result.runtime_timeline_duration_ms,
        "video_codec": video["codec_name"],
        "audio_codec": audio["codec_name"],
        "audio_sample_rate": int(audio["sample_rate"]),
        "trailing_silence_seconds": round(trailing_silence, 3),
        "final_mp4_path": str(final_path),
        "final_mp4_sha256": _sha256(final_path),
        "representative_frame_paths": [str(path) for path in frame_paths],
        "db_mutated": False,
        "in_memory_session_reads": session.execute_count,
        "worker_started": False,
        "beat_started": False,
        "celery_consumed": False,
        "mission_e2e_run": False,
        "external_publish": False,
        "external_socket_attempts": external_socket_attempts,
    }
    if report["r_frame_rate"] != "12/1" or report["avg_frame_rate"] != "12/1":
        raise RuntimeError("Final MP4 did not satisfy the 12 fps CFR contract")
    if report["audio_codec"] != "aac" or report["audio_sample_rate"] != 44100:
        raise RuntimeError("Final audio contract failed")
    if report["scene_count"] < 2 or not 12 <= report["container_duration"] <= 25:
        raise RuntimeError("Canary scene-count or duration contract failed")
    if report["subtitle_cue_count"] < 2 or external_socket_attempts:
        raise RuntimeError("Canary subtitle or network-isolation contract failed")
    if report["audio_duration"] + 0.001 < result.runtime_timeline_duration_ms / 1000:
        raise RuntimeError("Narration was truncated")
    report_path = root / "canary_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"P15A_CANARY_REPORT_PATH={report_path}")
    print("P15A_CANARY_REPORT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
