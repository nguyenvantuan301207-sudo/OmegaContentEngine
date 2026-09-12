"""Run one isolated real Kokoro-to-MP4 production canary without DB writes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

from omega.application.local_tts.policy import resolve_narration_policy
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import (
    LocalTTSNarrationProvider,
    get_narration_provider,
)
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
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _model_signature(model_dir: Path) -> list[dict]:
    return [
        {
            "name": item.name,
            "size": item.stat().st_size,
            "mtime_ns": item.stat().st_mtime_ns,
            "sha256": _sha256(item),
        }
        for item in sorted(model_dir.iterdir())
        if item.is_file()
    ]


class InMemorySession:
    """Serve mandatory lineage reads from non-persisted ORM fixtures."""

    def __init__(self, mission_execution, content_request) -> None:
        self.mission_execution = mission_execution
        self.content_request = content_request
        self.execute_count = 0

    async def execute(self, statement):
        self.execute_count += 1
        result = MagicMock()
        sql = str(statement).lower()
        if "mission_executions" in sql:
            result.scalar_one_or_none.return_value = self.mission_execution
        elif "content_generation_requests" in sql:
            result.scalar_one_or_none.return_value = self.content_request
        elif "script_versions" in sql:
            result.scalars.return_value.first.return_value = self.content_request.scripts[0]
        else:
            raise RuntimeError(f"Unexpected in-memory query: {sql[:160]}")
        return result


def _make_lineage():
    channel_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    request_id = uuid.uuid4()

    mission = Mission(
        id=mission_id,
        channel_id=channel_id,
        title="P14-B isolated Kokoro canary",
    )
    dna = ChannelDNARevision(
        id=dna_id,
        channel_id=channel_id,
        version=1,
        snapshot={},
        change_reason="P14-B non-persisted canary fixture",
    )
    execution = MissionExecution(
        id=execution_id,
        mission_id=mission_id,
        channel_dna_revision_id=dna_id,
    )
    execution.mission = mission
    execution.channel_dna_revision = dna

    texts = [
        "A script enters OMEGA, where deterministic planning separates the story into clear visual scenes.",
        "Local Kokoro speech synthesis creates canonical narration while FFmpeg aligns the audio with each visual.",
        "The renderer joins every scene and delivers a verified video with traceable voice provenance.",
    ]
    headings = ["Plan the story", "Create local narration", "Assemble the video"]
    script = ScriptVersion(
        id=uuid.uuid4(),
        version=1,
        title="How OMEGA turns a script into a finished video",
        estimated_duration_seconds=18,
    )
    sections = []
    for index, (heading, text) in enumerate(zip(headings, texts, strict=True), start=1):
        section = ScriptSection(
            id=uuid.uuid4(),
            section_order=index,
            heading=heading,
            narration_text=text,
            estimated_duration_seconds=6,
        )
        statement = ScriptStatement(
            id=uuid.uuid4(),
            statement_order=1,
            statement_text=text,
            statement_type="ASSERTION",
        )
        statement.citations = []
        section.statements = [statement]
        sections.append(section)
    script.sections = sections

    request = ContentGenerationRequest(
        id=request_id,
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        mission_execution_id=execution_id,
    )
    request.scripts = [script]
    return channel_id, execution, request, script, texts


def _last_match(pattern: str, text: str):
    matches = re.findall(pattern, text)
    return matches[-1] if matches else None


async def main() -> None:
    run_id = uuid.uuid4().hex[:12]
    root = Path(f"/tmp/omega_p14b_kokoro_video_canary_{run_id}")
    media_root = root / "media"
    render_root = root / "render"
    root.mkdir(parents=True, exist_ok=False)

    model_dir = Path("/app/models/tts/kokoro")
    models_before = _model_signature(model_dir)
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "PEXELS_API_KEY",
        "ELEVENLABS_API_KEY",
        "GOOGLE_API_KEY",
    ):
        os.environ.pop(name, None)
    os.environ.update(
        {
            "TTS_PROVIDER": "local",
            "LOCAL_TTS_PROFILE": "quality",
            "LOCAL_TTS_MODEL_DIR": str(model_dir),
            "LOCAL_TTS_DEVICE": "auto",
            "OMEGA_VISUAL_ASSET_MODE": "LOCAL_TEMPLATE_ONLY",
        }
    )

    external_socket_attempts: list[str] = []
    original_connect = socket.socket.connect

    def guarded_connect(sock, address):
        if isinstance(address, tuple):
            host = str(address[0]).lower()
            if host not in {"127.0.0.1", "::1", "localhost"}:
                external_socket_attempts.append(str(address))
                raise RuntimeError(f"Outbound network blocked for canary: {address}")
        return original_connect(sock, address)

    socket.socket.connect = guarded_connect
    channel_id, execution, request, script, texts = _make_lineage()
    storage = LocalMediaStorageProvider(base_root=str(media_root))
    requested_policy = {
        "provider": "local",
        "profile": "quality",
        "language": "en-US",
        "voice": "am_michael",
        "speed": 1.0,
        "device": "auto",
    }
    policy = resolve_narration_policy(
        request_policy=requested_policy,
        actual_provider="LOCAL_TTS",
    )
    provider = get_narration_provider(storage)
    if not isinstance(provider, LocalTTSNarrationProvider):
        raise RuntimeError(f"Unexpected narration provider: {type(provider).__name__}")

    narration_records: list[dict] = []
    original_synthesize = provider.synthesize_segment_audio

    async def recorded_synthesize(
        channel_id,
        request_id,
        segment,
        voice_profile=None,
    ):
        received = (
            voice_profile.to_dict()
            if hasattr(voice_profile, "to_dict")
            else dict(voice_profile or {})
        )
        started = time.perf_counter()
        asset = await original_synthesize(
            channel_id,
            request_id,
            segment,
            voice_profile,
        )
        audio_path = storage.resolve_stored_uri(
            channel_id,
            request_id,
            asset["storage_uri"],
        )
        narration_records.append(
            {
                "segment_id": str(asset["id"]),
                "scene_index": len(narration_records) + 1,
                "text_sha256": hashlib.sha256(
                    str(segment["text"]).encode("utf-8")
                ).hexdigest(),
                "text_excerpt": str(segment["text"])[:72],
                "provider_received": received,
                "provider": asset.get("provider"),
                "provider_type": asset.get("provider_type"),
                "engine": asset.get("engine"),
                "model": asset.get("model"),
                "profile": asset.get("profile"),
                "language": asset.get("language"),
                "voice": asset.get("voice"),
                "speed": asset.get("speed"),
                "device": asset.get("device"),
                "native_sample_rate": asset.get("native_sample_rate"),
                "canonical_sample_rate": asset.get("canonical_sample_rate"),
                "duration_ms": asset.get("duration_ms"),
                "generation_duration_ms": asset.get("generation_duration_ms"),
                "wall_duration_ms": int((time.perf_counter() - started) * 1000),
                "content_hash": asset.get("content_hash"),
                "storage_uri": asset.get("storage_uri"),
                "storage_path": str(audio_path),
                "source_ref": asset.get("source_ref"),
                "attribution": asset.get("attribution"),
            }
        )
        return asset

    provider.synthesize_segment_audio = recorded_synthesize
    service = VisualProductionV2Service(
        asset_orchestrator=None,
        output_root=render_root,
        narration_provider=provider,
        narration_storage=storage,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    session = InMemorySession(execution, request)
    try:
        result = await service.render_mission_execution(
            session,
            execution.id,
            request.id,
            voice_profile=requested_policy,
            subtitle_enabled=False,
        )
    finally:
        socket.socket.connect = original_connect

    final_path = result.output_path
    final_probe = _probe(final_path)
    narration_path = Path(narration_records[0]["storage_path"])
    narration_probe = _probe(narration_path)
    audio_stream = next(
        item for item in final_probe["streams"] if item.get("codec_type") == "audio"
    )
    video_stream = next(
        item for item in final_probe["streams"] if item.get("codec_type") == "video"
    )
    narration_stream = next(
        item
        for item in narration_probe["streams"]
        if item.get("codec_type") == "audio"
    )

    volume = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(final_path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stderr
    astats = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(final_path),
            "-af",
            "astats=metadata=0:reset=0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stderr
    silence = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(final_path),
            "-af",
            "silencedetect=noise=-45dB:d=0.35",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stderr

    mean_volume = _last_match(r"mean_volume:\s*([-\w.]+)\s*dB", volume)
    max_volume = _last_match(r"max_volume:\s*([-\w.]+)\s*dB", volume)
    rms_level = _last_match(r"RMS level dB:\s*([-\w.]+)", astats)
    peak_level = _last_match(r"Peak level dB:\s*([-\w.]+)", astats)
    entropy = _last_match(r"Entropy:\s*([-\w.]+)", astats)
    zero_crossings = _last_match(r"Zero crossings:\s*([0-9]+)", astats)
    final_duration = float(final_probe["format"]["duration"])
    silence_starts = [
        float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", silence)
    ]
    silence_ends = [
        float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", silence)
    ]
    trailing_silence = 0.0
    if silence_starts:
        last_start = silence_starts[-1]
        if len(silence_ends) < len(silence_starts):
            trailing_silence = max(0.0, final_duration - last_start)
        elif silence_ends and silence_ends[-1] >= final_duration - 0.12:
            trailing_silence = max(0.0, silence_ends[-1] - last_start)

    fps_raw = video_stream.get("avg_frame_rate") or "0/1"
    numerator, denominator = (float(part) for part in fps_raw.split("/"))
    narration_total_ms = sum(int(item["duration_ms"]) for item in narration_records)
    scene_total_ms = round(
        sum(scene.duration_seconds for scene in result.runtime_scenes) * 1000
    )
    final_duration_ms = round(final_duration * 1000)
    models_after = _model_signature(model_dir)

    report = {
        "canary_execution_path": (
            "resolve_narration_policy -> get_narration_provider(LocalTTSNarrationProvider) "
            "-> KokoroLocalTTSEngine -> canonical WAV -> VisualProductionV2Service "
            "-> VisualV2VideoRenderer -> FFmpegRenderer mux/concat -> MP4"
        ),
        "root": str(root),
        "db_mutation_required": False,
        "db_mutated": False,
        "in_memory_session_reads": session.execute_count,
        "visual_mode": "LOCAL_TEMPLATE_ONLY",
        "external_visual_calls": 0,
        "requested_policy": requested_policy,
        "resolved_policy": policy.to_dict(),
        "provider_class": type(provider).__name__,
        "engine_class": type(provider.engine).__name__,
        "kokoro_load_count": provider.engine.load_count,
        "narration_segments": narration_records,
        "narration_probe": {
            "codec": narration_stream.get("codec_name"),
            "sample_rate": int(narration_stream.get("sample_rate", 0)),
            "channels": int(narration_stream.get("channels", 0)),
            "duration": float(narration_stream.get("duration", 0)),
            "sha256": _sha256(narration_path),
        },
        "final_mp4": {
            "path": str(final_path),
            "sha256": _sha256(final_path),
            "container": final_probe["format"].get("format_name"),
            "duration": final_duration,
            "video_codec": video_stream.get("codec_name"),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "fps": numerator / denominator if denominator else 0.0,
            "audio_codec": audio_stream.get("codec_name"),
            "audio_sample_rate": int(audio_stream.get("sample_rate", 0)),
            "audio_channels": int(audio_stream.get("channels", 0)),
        },
        "audio_analysis": {
            "mean_volume_db": mean_volume,
            "max_volume_db": max_volume,
            "rms_level_db": rms_level,
            "peak_level_db": peak_level,
            "entropy": entropy,
            "zero_crossings": zero_crossings,
            "non_silent": mean_volume not in (None, "-inf")
            and max_volume not in (None, "-inf"),
            "trailing_silence_seconds": round(trailing_silence, 3),
        },
        "timing": {
            "narration_total_ms": narration_total_ms,
            "runtime_timeline_ms": result.runtime_timeline_duration_ms,
            "scene_total_ms": scene_total_ms,
            "final_duration_ms": final_duration_ms,
            "final_minus_narration_ms": final_duration_ms - narration_total_ms,
        },
        "model_files_before": models_before,
        "model_files_after": models_after,
        "model_downloads_during_run": 0
        if models_before == models_after
        else "MODEL_FILES_CHANGED",
        "external_socket_attempts": external_socket_attempts,
        "external_tts_calls": 0,
        "external_publish_calls": 0,
        "mission_e2e_run": False,
        "worker_started": False,
        "beat_started": False,
        "celery_consumed": False,
        "script_title": script.title,
        "script_text_sha256": hashlib.sha256(
            "\n".join(texts).encode("utf-8")
        ).hexdigest(),
        "runtime_scenes": [
            scene.model_dump(mode="json") for scene in result.runtime_scenes
        ],
    }
    report_path = root / "canary_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"P14B_CANARY_REPORT_PATH={report_path}")
    print("P14B_CANARY_REPORT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
