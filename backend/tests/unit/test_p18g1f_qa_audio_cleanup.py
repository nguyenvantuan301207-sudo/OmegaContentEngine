"""P18-G1F regression coverage for QA semantics and final AAC format."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from omega.application.ffmpeg_renderer import (
    FINAL_MASTER_SAMPLE_RATE_HZ,
    FFmpegRenderer,
)
from omega.application.production_qa import ProductionQAEngine
from omega.application.production_runtime_truth import RUNTIME_TRUTH_SCHEMA_VERSION
from omega.application.visual_production_v2_service import (
    CANONICAL_RENDER_SEMANTICS_VERSION,
    SUBTITLE_SEMANTICS_VERSION,
    _canonical_render_semantics_identity,
    _validate_cached_render_semantics,
)
from omega.domain.production import (
    ProductionQARuleCode,
    SubtitleFallbackPolicy,
    SubtitleMode,
)


def _qa_context(
    *,
    narration_quality: str | None = "NEURAL_PRODUCTION",
    source_ref: str = "Local TTS (engine: kokoro-onnx, voice: test-voice)",
    closing_text: str = "",
    cta_text: str = "",
    heading: str = "Body",
    statement_type: str = "FACTUAL",
    runtime_statement_types: list[str] | None = None,
) -> dict:
    return {
        "request_data": {
            "id": "production-request",
            "script_version_id": "script-version",
            "channel_dna_revision_id": "dna-revision",
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        "script_version_data": {
            "id": "script-version",
            "hook_text": "A complete opening hook.",
            "closing_text": closing_text,
            "cta_text": cta_text,
            "sections": [
                {
                    "section_order": 1,
                    "heading": heading,
                    "narration_text": "A complete documentary body.",
                    "statements": [
                        {
                            "statement_order": 1,
                            "statement_text": "A complete documentary body.",
                            "statement_type": statement_type,
                        }
                    ],
                }
            ],
        },
        "content_request_data": {"channel_dna_revision_id": "dna-revision"},
        "assets_data": [
            {
                "id": "audio",
                "asset_type": "AUDIO",
                "provider_type": "SYSTEM",
                "source_ref": source_ref,
                "license_status": "GENERATED",
                "narration_quality": narration_quality,
            },
            {
                "id": "subtitle",
                "asset_type": "SUBTITLE",
                "mime_type": "application/x-subrip",
                "license_status": "GENERATED",
            },
        ],
        "requirements_data": [],
        "narration_segments": [
            {"id": "narration", "start_ms": 0, "end_ms": 2_000}
        ],
        "subtitle_cues": [
            {
                "cue_order": 1,
                "start_ms": 0,
                "end_ms": 2_000,
                "text": "A complete documentary body.",
            }
        ],
        "media_probe_summary": {
            "duration_ms": 2_000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
            "mean_volume_db": -20.0,
        },
        "artifact_file_path": None,
        "expected_hash": None,
        "scenes_data": [
            {
                "sequence_index": 1,
                "effective_strategy": "BROLL",
                "statement_types": runtime_statement_types or [],
            }
        ],
    }


def _codes(**overrides) -> set[ProductionQARuleCode]:
    context = _qa_context(**overrides)
    _status, findings = ProductionQAEngine().evaluate(**context)
    return {finding.rule_code for finding in findings}


def test_local_kokoro_neural_quality_is_not_fallback():
    assert ProductionQARuleCode.ROBOTIC_FALLBACK_TTS not in _codes()


def test_other_neural_provider_is_not_fallback():
    assert ProductionQARuleCode.ROBOTIC_FALLBACK_TTS not in _codes(
        source_ref="Cloud neural narration"
    )


def test_development_fallback_quality_warns():
    assert ProductionQARuleCode.ROBOTIC_FALLBACK_TTS in _codes(
        narration_quality="DEVELOPMENT_FALLBACK"
    )


@pytest.mark.parametrize("quality", [None, "", "UNKNOWN"])
def test_ambiguous_narration_quality_fails_closed(quality):
    assert ProductionQARuleCode.ROBOTIC_FALLBACK_TTS in _codes(
        narration_quality=quality
    )


def test_canonical_closing_text_satisfies_outro():
    assert ProductionQARuleCode.MISSING_OUTRO not in _codes(
        closing_text="A truthful documentary conclusion."
    )


def test_final_runtime_closing_semantics_satisfies_outro():
    assert ProductionQARuleCode.MISSING_OUTRO not in _codes(
        runtime_statement_types=["CLOSING"]
    )


def test_final_source_closing_statement_satisfies_outro():
    assert ProductionQARuleCode.MISSING_OUTRO not in _codes(
        statement_type="CLOSING"
    )


def test_explicit_cta_satisfies_outro():
    assert ProductionQARuleCode.MISSING_OUTRO not in _codes(
        cta_text="Subscribe for the next documentary."
    )


@pytest.mark.parametrize("heading", ["Recap", "Documentary Outro"])
def test_explicit_recap_or_outro_heading_satisfies_outro(heading):
    assert ProductionQARuleCode.MISSING_OUTRO not in _codes(heading=heading)


def test_missing_ending_warns_and_arbitrary_heading_does_not_suppress():
    assert ProductionQARuleCode.MISSING_OUTRO in _codes(heading="Background")


def _run(command: list[str]) -> str:
    completed = __import__("subprocess").run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout + completed.stderr


@pytest.mark.asyncio
async def test_final_master_is_48khz_aac_with_stream_copied_video(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are required for physical master validation")

    source = tmp_path / "source.mp4"
    mastered = tmp_path / "mastered.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=24:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ]
    )

    await FFmpegRenderer().normalize_master_audio(
        video_path=source,
        output_path=mastered,
    )

    probe = json.loads(
        _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-of",
                "json",
                str(mastered),
            ]
        )
    )
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == (
        "h264",
        640,
        360,
    )
    assert audio["codec_name"] == "aac"
    assert int(audio["sample_rate"]) == FINAL_MASTER_SAMPLE_RATE_HZ

    source_hash = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-f",
            "streamhash",
            "-hash",
            "sha256",
            "-",
        ]
    ).strip()
    mastered_hash = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(mastered),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-f",
            "streamhash",
            "-hash",
            "sha256",
            "-",
        ]
    ).strip()
    assert mastered_hash == source_hash

    ebur128 = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(mastered),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ]
    )
    integrated = re.search(
        r"Integrated loudness:\s+I:\s+([-\d.]+)\s+LUFS", ebur128
    )
    true_peak = re.search(r"True peak:\s+Peak:\s+([-\d.]+)\s+dBFS", ebur128)
    assert integrated is not None
    assert abs(float(integrated.group(1)) - (-16.0)) <= 2.5
    assert true_peak is not None
    assert float(true_peak.group(1)) <= -1.0


def test_normalization_is_applied_exactly_once():
    service_source = Path(
        "/app/src/omega/application/visual_production_v2_service.py"
    ).read_text(encoding="utf-8")
    assert service_source.count("normalize_master_audio") == 1


def _current_manifest() -> dict:
    return {
        "runtime_truth_schema_version": RUNTIME_TRUTH_SCHEMA_VERSION,
        "subtitle_semantics_version": SUBTITLE_SEMANTICS_VERSION,
        "canonical_render_semantics_version": CANONICAL_RENDER_SEMANTICS_VERSION,
        "final_master_sample_rate_hz": FINAL_MASTER_SAMPLE_RATE_HZ,
        "requested_subtitle_mode": "STANDARD",
        "effective_subtitle_mode": "STANDARD",
        "subtitle_fallback_applied": False,
        "subtitle_fallback_reason": None,
        "subtitle_timing_source": "DERIVED_SEGMENT_TIMING",
        "subtitle_enabled": True,
        "karaoke_subtitles_enabled": False,
        "subtitle_mode": "sentence",
        "subtitle_mode_decision": {
            "requested_mode": "STANDARD",
            "effective_mode": "STANDARD",
            "fallback_applied": False,
            "fallback_reason": None,
            "timing_source": "DERIVED_SEGMENT_TIMING",
        },
    }


def _cache_compatible(manifest: dict) -> bool:
    return _validate_cached_render_semantics(
        manifest=manifest,
        canonical_requested_mode=SubtitleMode.STANDARD,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
    )


def test_current_semantic_authorities_and_cache_compatibility():
    assert CANONICAL_RENDER_SEMANTICS_VERSION == 3
    assert SUBTITLE_SEMANTICS_VERSION == 3
    assert FINAL_MASTER_SAMPLE_RATE_HZ == 48_000
    assert _cache_compatible(_current_manifest())


def test_semantics_v1_cache_is_rejected():
    historical = _current_manifest()
    historical["canonical_render_semantics_version"] = 1
    historical.pop("final_master_sample_rate_hz")
    assert not _cache_compatible(historical)


def test_final_master_sample_rate_participates_in_fingerprint():
    current = _canonical_render_semantics_identity()
    changed = _canonical_render_semantics_identity(sample_rate_hz=96_000)
    assert "sample_rate=48000" in current
    assert current != changed
