"""Local deterministic Music Director -> P23-A -> FFmpeg physical canary."""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest

from omega.application.audio_mix_v2_service import AudioMixPlanner
from omega.application.ffmpeg_renderer import FFmpegRenderer
from omega.application.music_director_service import (
    MusicDirector,
    MusicSelectionEngine,
    music_selection_to_audio_stem,
)
from omega.domain.audio_mix import AudioStem, AudioStemRole
from omega.domain.channel_dna import BrandVoice, ChannelDNA
from omega.domain.music_direction import MusicTrackMetadata, TrackUsageState
from omega.domain.narrative_pacing import (
    PacingPlan,
    PacingProfile,
    RevealStage,
    SectionTimingDetail,
)
from omega.domain.narrative_plan import (
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeSection,
    NarrativeSectionRole,
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)


def mean_220(path: Path, start: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-ss", str(start), "-t", "0.4", "-i", str(path),
            "-af", "bandpass=f=220:w=80,volumedetect", "-f", "null", "-",
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", result.stderr)
    assert match
    return float(match.group(1))


@pytest.mark.asyncio
async def test_p23b_realistic_music_canary(tmp_path: Path):
    section = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Open a restrained curiosity loop",
        target_duration_seconds=4,
    )
    narrative = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=NarrativePlanStatus.APPROVED,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=15,
        estimated_duration_seconds=4,
        sections=[section],
    )
    timing = SectionTimingDetail(
        section_id=section.id,
        section_order=1,
        role=section.role,
        current_duration_seconds=4,
        recommended_duration_seconds=4,
        target_information_density=InformationDensity.HIGH,
        reveal_stage=RevealStage.SETUP,
        intensity_score=0.45,
    )
    pacing = PacingPlan(
        narrative_plan_id=narrative.id,
        narrative_plan_version=1,
        pacing_profile=PacingProfile.BALANCED,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=4,
        initial_duration_sum_seconds=4,
        optimized_duration_sum_seconds=4,
        section_timings=[timing],
        open_loop_metrics=[], findings=[], recommended_adjustments=[],
        escalation_curve=[0.45],
    )
    arc = MusicDirector.direct(
        narrative_plan=narrative,
        pacing_plan=pacing,
        channel_dna=ChannelDNA(brand_voice=BrandVoice(tone=["CALM", "ANALYTICAL"])),
    )

    tracks: list[MusicTrackMetadata] = []
    for asset_id, frequency, energy, moods in (
        ("calm", 180, 0.15, ("reflective",)),
        ("neutral", 220, 0.35, ("curious",)),
        ("high", 330, 0.90, ("driving",)),
    ):
        path = tmp_path / f"{asset_id}.wav"
        run(
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"sine=frequency={frequency}:sample_rate=44100:duration=4", "-ac", "1", str(path),
        )
        tracks.append(
            MusicTrackMetadata(
                asset_id=asset_id,
                source_artifact=str(path),
                duration_ms=4_000,
                moods=moods,
                energy=energy,
                bpm=100,
                has_vocals=False,
                loop_safe=True,
                catalog="p23b-synthetic",
                usage_state=TrackUsageState.GENERATED,
            )
        )
    selection = MusicSelectionEngine.select(arc.cues[0], tracks)
    assert selection.selected_track is not None
    assert selection.selected_track.asset_id == "neutral"
    music_stem = music_selection_to_audio_stem(selection)
    assert music_stem is not None

    narration_video = tmp_path / "narration.mp4"
    mixed = tmp_path / "music_master.mp4"
    final = tmp_path / "final.mp4"
    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=24:d=4",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=4",
        "-filter_complex",
        "[1:a]volume='if(between(t,0.5,1.5)+between(t,2.5,3.3),0.8,0)':eval=frame[v]",
        "-map", "0:v", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", "4", str(narration_video),
    )
    narration_stem = AudioStem(
        stem_id="narration", role=AudioStemRole.NARRATION,
        source_artifact=str(narration_video), source_identity="synthetic-narration",
        start_ms=0, end_ms=4_000, priority=100,
    )
    mix_plan = AudioMixPlanner.plan(
        timeline_duration_ms=4_000,
        stems=[narration_stem, music_stem],
        narration_intervals_ms=[(500, 1_500), (2_500, 3_300)],
    )
    assert mix_plan.gain_envelopes[music_stem.stem_id][-1].offset_ms == 500

    renderer = FFmpegRenderer()
    await renderer.mix_master_audio(
        video_path=narration_video,
        output_path=mixed,
        target_duration_ms=4_000,
        background_music_path=selection.selected_track.source_artifact,
        background_music_gain_db=music_stem.gain_db,
        background_music_fade_in_ms=music_stem.fade_in_ms,
        background_music_fade_out_ms=music_stem.fade_out_ms,
    )
    await renderer.normalize_master_audio(video_path=mixed, output_path=final)
    assert mean_220(final, 1.8) >= mean_220(final, 0.8) + 2.0
    metadata = json.loads(run(
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(final)
    ).stdout)
    assert abs(float(metadata["format"]["duration"]) - 4.0) <= 0.08
    assert any(stream["codec_type"] == "audio" for stream in metadata["streams"])

    loudness = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", str(final), "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=7:print_format=json", "-f", "null", "-",
        ],
        check=True, capture_output=True, text=True, timeout=60,
    ).stderr
    stats_match = re.search(r"\{\s*\"input_i\".*?\}", loudness, re.DOTALL)
    assert stats_match
    stats = json.loads(stats_match.group(0))
    assert abs(float(stats["input_i"]) - (-16.0)) <= 1.0
    assert float(stats["input_tp"]) <= -1.0
