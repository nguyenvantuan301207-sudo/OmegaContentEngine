"""P15-D Final Production Canary Runner.

Renders ONE real final production canary meeting all P15-D requirements:
  - Kokoro en-US, voice am_michael, quality, speed 1.0
  - LOCAL_TEMPLATE_ONLY
  - 20–35 seconds, 2–3 scenes
  - H.264 video codec
  - AAC 44100Hz audio codec
  - 24 FPS CFR (r_frame_rate=24/1, avg_frame_rate=24/1)
  - Full-sentence subtitles (karaoke=False, no word-by-word reveal)
  - Bottom-center placement (alignment=2, margin_v=80)
  - No narration truncation, no trailing silence
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import get_narration_provider
from omega.application.visual_production_v2_service import VisualProductionV2Service
from omega.domain.channel_style import ChannelStyleProfile
from omega.infrastructure.models import (
    Channel,
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


def _probe(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _check_silence(path: Path) -> list[str]:
    completed = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-40dB:d=0.8", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    lines = [line for line in completed.stderr.splitlines() if "silence_end" in line or "silence_start" in line]
    return lines


class InMemorySession:
    def __init__(self, execution: MissionExecution, request: ContentGenerationRequest, channel: Channel):
        self.execution = execution
        self.request = request
        self.channel = channel

    async def execute(self, statement):
        result = MagicMock()
        sql = str(statement).lower()
        if "mission_executions" in sql:
            result.scalar_one_or_none.return_value = self.execution
        elif "content_generation_requests" in sql:
            result.scalar_one_or_none.return_value = self.request
        elif "channels" in sql:
            result.scalar_one_or_none.return_value = self.channel
        else:
            result.scalar_one_or_none.return_value = None
        return result


def _make_lineage(channel_style: ChannelStyleProfile):
    channel_id, dna_id, mission_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    execution_id, request_id = uuid.uuid4(), uuid.uuid4()

    channel = Channel(
        id=channel_id,
        name="P15-D Final Production Channel",
        slug="p15d-final-prod",
        state="ACTIVE",
        metadata_={"style_profile": channel_style.model_dump(mode="json")},
    )

    mission = Mission(id=mission_id, channel_id=channel_id, title="P15-D Final Production Canary")
    dna = ChannelDNARevision(
        id=dna_id,
        channel_id=channel_id,
        version=1,
        snapshot={},
        change_reason="P15-D Final Production test fixture",
    )
    execution = MissionExecution(
        id=execution_id,
        mission_id=mission_id,
        channel_dna_revision_id=dna_id,
    )
    execution.mission = mission
    execution.channel_dna_revision = dna

    statements = [
        "Autonomous video engines require rigorous production pipelines that guarantee constant twenty-four frames per second.",
        "Full sentence subtitles appear immediately at the bottom of the frame with no progressive word reveals.",
        "Every production release verifies deterministic rendering, pure broadcast timing, and crystal clear narration.",
    ]

    script = ScriptVersion(
        id=uuid.uuid4(),
        version=1,
        title="P15-D Production Defaults",
        estimated_duration_seconds=24,
    )
    script.sections = []
    section = ScriptSection(
        id=uuid.uuid4(),
        section_order=1,
        heading="Deterministic Production Standards",
        narration_text=" ".join(statements),
        estimated_duration_seconds=24,
    )
    section.statements = []
    for idx, text in enumerate(statements, start=1):
        stmt = ScriptStatement(
            id=uuid.uuid4(),
            statement_order=idx,
            statement_text=text,
            statement_type="ASSERTION",
        )
        stmt.citations = []
        section.statements.append(stmt)
    script.sections.append(section)

    request = ContentGenerationRequest(
        id=request_id,
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        mission_execution_id=execution_id,
    )
    request.scripts = [script]
    return channel, execution, request


async def run_final_canary() -> dict[str, Any]:
    print("=" * 80)
    print("OMEGA P15-D FINAL PRODUCTION CANARY RUNNER")
    print("=" * 80)

    # 1. Environment and network isolation
    model_dir = Path("/app/models/tts/kokoro")
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "PEXELS_API_KEY", "ELEVENLABS_API_KEY"):
        os.environ.pop(k, None)
    os.environ.update({
        "TTS_PROVIDER": "local",
        "LOCAL_TTS_PROFILE": "quality",
        "LOCAL_TTS_MODEL_DIR": str(model_dir),
        "LOCAL_TTS_DEVICE": "auto",
        "OMEGA_VISUAL_ASSET_MODE": "LOCAL_TEMPLATE_ONLY",
    })

    # Guard outbound network
    original_connect = socket.socket.connect
    blocked_sockets = []

    def guarded_connect(sock, address):
        if isinstance(address, tuple) and str(address[0]).lower() not in {"127.0.0.1", "::1", "localhost"}:
            blocked_sockets.append(str(address))
            raise RuntimeError(f"Outbound network blocked: {address}")
        return original_connect(sock, address)

    socket.socket.connect = guarded_connect

    run_root = Path("/app/artifacts/p15d")
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    frames_dir = run_root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Production default style profile
    style_profile = ChannelStyleProfile(
        profile_id="default",
        subtitle_preset_id="default",
        pacing="BALANCED",
        accent_color="#3B82F6",
        bg_color="#0B0F19",
    )

    channel, execution, request = _make_lineage(style_profile)
    session = InMemorySession(execution, request, channel)

    media_root = run_root / "media"
    render_root = run_root / "render"

    storage = LocalMediaStorageProvider(base_root=str(media_root))
    narration_provider = get_narration_provider(storage)

    service = VisualProductionV2Service(
        asset_orchestrator=None,
        output_root=render_root,
        narration_provider=narration_provider,
        narration_storage=storage,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )

    voice_profile = {"voice": "am_michael", "speed": 1.0}

    print("\nStarting render_mission_execution with 24 FPS and default full-sentence subtitles...")
    render_res = await service.render_mission_execution(
        session=session,
        mission_execution_id=execution.id,
        content_request_id=request.id,
        fps=24,
        voice_profile=voice_profile,
        subtitle_enabled=True,
        style_profile=style_profile,
    )

    src_mp4 = Path(render_res.output_path)
    final_mp4 = run_root / "p15d_final_canary.mp4"
    shutil.copy2(src_mp4, final_mp4)

    # 2. FFprobe analysis
    probe_data = _probe(final_mp4)
    video_stream = next(s for s in probe_data["streams"] if s["codec_type"] == "video")
    audio_stream = next(s for s in probe_data["streams"] if s["codec_type"] == "audio")
    v_duration = float(video_stream.get("duration", probe_data["format"]["duration"]))
    a_duration = float(audio_stream.get("duration", probe_data["format"]["duration"]))
    frame_count = int(video_stream.get("nb_frames", round(v_duration * 24)))

    r_fps = video_stream.get("r_frame_rate")
    avg_fps = video_stream.get("avg_frame_rate")
    v_codec = video_stream.get("codec_name")
    a_codec = audio_stream.get("codec_name")
    a_sample_rate = int(audio_stream.get("sample_rate", 0))

    mp4_sha = _sha256(final_mp4)

    # 3. Trailing silence check
    silence_log = _check_silence(final_mp4)

    # 4. Check ASS file to prove full-sentence and no \kf tags
    work_dirs = list((render_root / str(execution.id)).glob("**/work"))
    ass_files = []
    ass_checks = []
    if work_dirs:
        for p in work_dirs[0].glob("*.ass"):
            content = p.read_text(encoding="utf-8")
            ass_files.append(str(p.name))
            has_kf = r"\kf" in content or r"\k" in content
            ass_checks.append({
                "file": p.name,
                "has_kf_tags": has_kf,
                "lines": [line for line in content.splitlines() if line.startswith("Dialogue:")],
            })

    # 5. Extract representative frames for visual proof
    # Target points: 3.0s (scene 1), 11.0s (scene 2), 19.0s (scene 3)
    frame_times = [3.0, 11.0, 19.0]
    extracted_frames = []
    for idx, t in enumerate(frame_times, start=1):
        frame_name = f"proof_frame_{idx}_t{int(t)}s.png"
        frame_path = frames_dir / frame_name
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(final_mp4), "-vframes", "1", "-pix_fmt", "rgb24", str(frame_path)],
            check=True,
            capture_output=True,
        )
        extracted_frames.append({
            "timestamp": t,
            "filename": frame_name,
            "path": str(frame_path),
            "sha256": _sha256(frame_path),
        })

    result = {
        "final_canary_path": str(final_mp4),
        "final_canary_sha256": mp4_sha,
        "video_codec": v_codec,
        "audio_codec": a_codec,
        "audio_sample_rate": a_sample_rate,
        "r_frame_rate": r_fps,
        "avg_frame_rate": avg_fps,
        "frame_count": frame_count,
        "video_duration": v_duration,
        "audio_duration": a_duration,
        "silence_log": silence_log,
        "extracted_frames": extracted_frames,
        "ass_checks": ass_checks,
        "network_blocked": blocked_sockets,
    }

    result_json = run_root / "final_canary_evidence.json"
    result_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("\n" + "=" * 80)
    print("CANARY EXECUTION COMPLETE")
    print(f"File: {final_mp4}")
    print(f"SHA256: {mp4_sha}")
    print(f"FPS: {r_fps} (avg: {avg_fps})")
    print(f"Frames: {frame_count}")
    print(f"Video Duration: {v_duration:.3f}s | Audio Duration: {a_duration:.3f}s")
    print(f"Video Codec: {v_codec} | Audio Codec: {a_codec} ({a_sample_rate}Hz)")
    print(f"Frames extracted: {[f['filename'] for f in extracted_frames]}")
    print("=" * 80)
    return result


if __name__ == "__main__":
    asyncio.run(run_final_canary())
