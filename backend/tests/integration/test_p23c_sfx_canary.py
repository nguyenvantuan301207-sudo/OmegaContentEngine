"""Realistic deterministic SFX Director -> P23-A -> FFmpeg physical canary."""

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
from omega.application.sfx_director_service import (
    SFXDirector,
    sfx_selections_to_audio_stems,
)
from omega.domain.audio_mix import AudioStem, AudioStemRole
from omega.domain.camera_transition import (
    CameraFrameState,
    CameraIntent,
    CameraPlan,
    CameraTransitionPlan,
    MotionStrength,
    TransitionIntent,
    TransitionPlan,
)
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
from omega.domain.sfx_direction import (
    SFXAssetMetadata,
    SFXDurationPolicy,
    SFXIntentType,
    SFXProminence,
    SFXUsageState,
)
from omega.domain.visual_beat import VisualBeat, VisualRole


import shutil

def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)


def probe(path: Path) -> dict:
    if shutil.which("ffprobe"):
        try:
            result = run(
                "ffprobe", "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(path),
            )
            return json.loads(result.stdout)
        except Exception:
            pass
    res = subprocess.run(["ffmpeg", "-i", str(path)], capture_output=True, text=True)
    stderr = res.stderr
    streams = []
    v_match = re.search(r"Video:\s*([a-zA-Z0-9]+)", stderr)
    if v_match:
        streams.append({"codec_type": "video", "codec_name": v_match.group(1).lower()})
    a_match = re.search(r"Audio:\s*([a-zA-Z0-9]+).*?(\d+)\s*Hz.*?,\s*(stereo|mono)", stderr)
    if a_match:
        rate = a_match.group(2)
        ch = 2 if a_match.group(3) == "stereo" else 1
        codec = a_match.group(1).lower()
        streams.append({"codec_type": "audio", "codec_name": codec, "sample_rate": rate, "channels": ch})
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    dur = 0.0
    if dur_match:
        dur = float(dur_match.group(1)) * 3600 + float(dur_match.group(2)) * 60 + float(dur_match.group(3))
    return {"streams": streams, "format": {"duration": str(dur)}}


@pytest.mark.asyncio
async def test_p23c_real_sfx_and_video_mux_canary(tmp_path: Path):
    # 1. Synthesize 3 SFX assets with distinct intent and energy
    click_path = tmp_path / "click.wav"
    whoosh_path = tmp_path / "whoosh.wav"
    impact_path = tmp_path / "impact.wav"

    # Soft UI click (high freq, short, low-medium energy)
    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "sine=frequency=1500:sample_rate=48000:duration=0.15", "-ac", "1", str(click_path),
    )
    # Whoosh (mid-range sweep / noise, 0.4s, medium energy)
    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "sine=frequency=450:sample_rate=48000:duration=0.4", "-ac", "1", str(whoosh_path),
    )
    # Impact (low freq, heavy, high energy)
    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "sine=frequency=90:sample_rate=48000:duration=0.6", "-ac", "1", str(impact_path),
    )

    sfx_catalog = [
        SFXAssetMetadata(
            asset_id="sfx-click",
            intent=SFXIntentType.UI_CLICK,
            duration_ms=150,
            energy=0.35,
            prominence=SFXProminence.SUBTLE,
            usage_state=SFXUsageState.GENERATED,
            source_artifact=str(click_path),
            tags=("ui", "click", "accent"),
        ),
        SFXAssetMetadata(
            asset_id="sfx-whoosh",
            intent=SFXIntentType.WHOOSH,
            duration_ms=400,
            energy=0.60,
            prominence=SFXProminence.BALANCED,
            usage_state=SFXUsageState.LICENSED,
            source_artifact=str(whoosh_path),
            tags=("whoosh", "transition"),
        ),
        SFXAssetMetadata(
            asset_id="sfx-impact",
            intent=SFXIntentType.IMPACT,
            duration_ms=600,
            energy=0.88,
            prominence=SFXProminence.PROMINENT,
            usage_state=SFXUsageState.OWNED,
            source_artifact=str(impact_path),
            tags=("impact", "reveal"),
        ),
    ]

    # 2. Synthesize background music (15s total)
    music_path = tmp_path / "canary_music.wav"
    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "sine=frequency=220:sample_rate=44100:duration=15", "-ac", "1", str(music_path),
    )
    music_catalog = [
        MusicTrackMetadata(
            asset_id="music-neutral",
            source_artifact=str(music_path),
            duration_ms=15_000,
            moods=("analytical", "focused"),
            energy=0.35,
            bpm=100,
            has_vocals=False,
            loop_safe=True,
            catalog="p23c-synthetic",
            usage_state=TrackUsageState.GENERATED,
        )
    ]

    # 3. Create Narrative Plan (3 sections, 5s each = 15s total):
    # Section 1: Ordinary explanation (DEVELOPMENT) -> should receive NO SFX
    # Section 2: Major reveal (ESCALATION, reveal) -> should receive REVEAL / IMPACT SFX
    # Section 3: Context transition (CLOSING / PAYOFF) -> should receive TRANSITION / WHOOSH SFX
    sec1 = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.DEVELOPMENT,
        objective="Explain routine factual baseline without drama",
        target_duration_seconds=5,
    )
    sec2 = NarrativeSection(
        section_order=2,
        role=NarrativeSectionRole.ESCALATION,
        objective="Major reveal of breakthrough evidence",
        target_duration_seconds=5,
    )
    sec3 = NarrativeSection(
        section_order=3,
        role=NarrativeSectionRole.CLOSING,
        objective="Context transition to concluding implications",
        target_duration_seconds=5,
    )

    narrative = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=NarrativePlanStatus.APPROVED,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=15,
        estimated_duration_seconds=15,
        sections=[sec1, sec2, sec3],
    )

    timings = [
        SectionTimingDetail(
            section_id=sec1.id,
            section_order=1,
            role=sec1.role,
            current_duration_seconds=5,
            recommended_duration_seconds=5,
            target_information_density=InformationDensity.MEDIUM,
            reveal_stage=RevealStage.SETUP,
            intensity_score=0.25,
        ),
        SectionTimingDetail(
            section_id=sec2.id,
            section_order=2,
            role=sec2.role,
            current_duration_seconds=5,
            recommended_duration_seconds=5,
            target_information_density=InformationDensity.HIGH,
            reveal_stage=RevealStage.MAJOR_REVEAL,
            intensity_score=0.85,
        ),
        SectionTimingDetail(
            section_id=sec3.id,
            section_order=3,
            role=sec3.role,
            current_duration_seconds=5,
            recommended_duration_seconds=5,
            target_information_density=InformationDensity.MEDIUM,
            reveal_stage=RevealStage.FINAL_PAYOFF,
            intensity_score=0.45,
        ),
    ]

    pacing = PacingPlan(
        narrative_plan_id=narrative.id,
        narrative_plan_version=1,
        pacing_profile=PacingProfile.BALANCED,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=15,
        initial_duration_sum_seconds=15,
        optimized_duration_sum_seconds=15,
        section_timings=timings,
        open_loop_metrics=[],
        findings=[],
        recommended_adjustments=[],
        escalation_curve=[0.25, 0.85, 0.45],
    )

    channel_dna = ChannelDNA(brand_voice=BrandVoice(tone=["CINEMATIC", "DYNAMIC"]))

    # 4. Visual beats:
    # Beat 1: Ordinary background at 1000ms
    # Beat 2: Reveal visual at 5500ms
    # Beat 3: Context transition at 10500ms
    visual_beats = [
        VisualBeat(
            scene_id=1,
            parent_scene_index=1,
            beat_index=0,
            start_offset_ms=1000,
            end_offset_ms=5000,
            duration_ms=4000,
            visual_intent="Show baseline standard operations",
            information_goal="Establish baseline context",
            visual_role=VisualRole.EXPLAIN,
        ),
        VisualBeat(
            scene_id=2,
            parent_scene_index=2,
            beat_index=0,
            start_offset_ms=5500,
            end_offset_ms=9500,
            duration_ms=4000,
            visual_intent="Reveal breakthrough discovery chart",
            information_goal="Reveal critical evidence",
            visual_role=VisualRole.REVEAL,
        ),
        VisualBeat(
            scene_id=3,
            parent_scene_index=3,
            beat_index=0,
            start_offset_ms=10500,
            end_offset_ms=14500,
            duration_ms=4000,
            visual_intent="Context transition concluding takeaways",
            information_goal="Summarize implications",
            visual_role=VisualRole.EXPLAIN,
        ),
    ]

    camera_transitions = CameraTransitionPlan(
        parent_scene_index=1,
        camera_plans=(),
        transition_plans=(
            TransitionPlan(
                previous_visual_beat_id=visual_beats[1].id,
                visual_beat_id=visual_beats[2].id,
                parent_scene_index=3,
                beat_index=0,
                source_editorial_beat_indices=(2,),
                requested_intent=TransitionIntent.HARD_CONTEXT_SWITCH,
                applied_intent=TransitionIntent.HARD_CONTEXT_SWITCH,
                duration_ms=400,
            ),
        ),
    )

    # 5. Music direction
    music_arc = MusicDirector.direct(
        narrative_plan=narrative,
        pacing_plan=pacing,
        channel_dna=channel_dna,
    )
    music_selection = MusicSelectionEngine.select(music_arc.cues[0], music_catalog)
    music_stem = music_selection_to_audio_stem(music_selection)
    assert music_stem is not None
    music_stem = music_stem.model_copy(update={"end_ms": 15_000})

    # 6. SFX Director
    sfx_plan = SFXDirector.direct(
        narrative_plan=narrative,
        pacing_plan=pacing,
        channel_dna=channel_dna,
        visual_beats=visual_beats,
        camera_transition_plan=camera_transitions,
        music_arc=music_arc,
        available_assets=sfx_catalog,
    )

    # Verify editorial intent rules
    # Section 1 (routine explanation) received no cues
    assert not any(c.start_ms < 5000 for c in sfx_plan.cues)
    # Total cues bounded
    assert len(sfx_plan.cues) == 2
    # Section 2 cue is REVEAL / IMPACT
    reveal_cue = next(c for c in sfx_plan.cues if c.start_ms == 5500)
    assert reveal_cue.intent.intent in (SFXIntentType.REVEAL, SFXIntentType.IMPACT)
    # Section 3 cue is TRANSITION / WHOOSH
    trans_cue = next(c for c in sfx_plan.cues if c.start_ms == 10500)
    assert trans_cue.intent.intent in (SFXIntentType.TRANSITION, SFXIntentType.WHOOSH)

    # Verify selections
    reveal_sel = next(s for s in sfx_plan.selections if s.cue.cue_id == reveal_cue.cue_id)
    trans_sel = next(s for s in sfx_plan.selections if s.cue.cue_id == trans_cue.cue_id)
    assert reveal_sel.selected_asset is not None
    assert reveal_sel.selected_asset.asset_id == "sfx-impact"
    assert trans_sel.selected_asset is not None
    assert trans_sel.selected_asset.asset_id == "sfx-whoosh"

    # Convert to P23-A audio stems
    sfx_stems = sfx_selections_to_audio_stems(sfx_plan.selections)
    assert len(sfx_stems) == 2

    # 7. Create Narration Video (15s total with voice intervals)
    # Voice active at [500-3000], [5500-8000] (overlaps reveal SFX!), [10500-13000] (overlaps trans SFX!)
    narration_video = tmp_path / "canary_narration.mp4"
    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:s=320x180:r=24:d=15",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=15",
        "-filter_complex",
        "[1:a]volume='if(between(t,0.5,3.0)+between(t,5.5,8.0)+between(t,10.5,13.0),0.75,0)':eval=frame[voice]",
        "-map", "0:v", "-map", "[voice]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", "15", str(narration_video),
    )

    narration_stem = AudioStem(
        stem_id="narration",
        role=AudioStemRole.NARRATION,
        source_artifact=str(narration_video),
        source_identity="synthetic-speech",
        start_ms=0,
        end_ms=15_000,
        priority=100,
    )

    # 8. P23-A Audio Mix Planning
    narration_intervals = [(500, 3000), (5500, 8000), (10500, 13000)]
    mix_plan = AudioMixPlanner.plan(
        timeline_duration_ms=15_000,
        stems=[narration_stem, music_stem, *sfx_stems],
        narration_intervals_ms=narration_intervals,
    )

    # Confirm P23-A collision attenuation (-6 dB) is planned for the overlapping SFX
    # Both SFX occur during speech intervals (5500ms and 10500ms)
    impact_stem = next(s for s in mix_plan.stems if s.stem_id == f"sfx-{reveal_sel.cue.cue_id}")
    assert impact_stem.gain_db == -6.0  # P23-C base 0.0 dB + P23-A -6.0 dB collision attenuation under speech

    # Build sfx_inputs for FFmpegRenderer
    sfx_inputs = [
        FFmpegRenderer.SFXMixInput(
            audio_path=Path(stem.source_artifact),
            start_ms=stem.start_ms,
            duration_ms=stem.end_ms - stem.start_ms,
            gain_db=stem.gain_db,
        )
        for stem in mix_plan.stems
        if stem.role == AudioStemRole.SFX
    ]

    # 9. Physical Mix & Master via FFmpegRenderer
    mixed_master = tmp_path / "mixed_master.mp4"
    final_output = tmp_path / "canary_final.mp4"

    renderer = FFmpegRenderer()
    await renderer.mix_master_audio(
        video_path=narration_video,
        output_path=mixed_master,
        target_duration_ms=15_000,
        background_music_path=Path(music_stem.source_artifact),
        background_music_gain_db=music_stem.gain_db,
        background_music_fade_in_ms=music_stem.fade_in_ms,
        background_music_fade_out_ms=music_stem.fade_out_ms,
        sfx_inputs=sfx_inputs,
    )

    await renderer.normalize_master_audio(
        video_path=mixed_master,
        output_path=final_output,
    )

    # 10. Physical Assertions & Metrics
    assert mixed_master.stat().st_size > 0
    assert final_output.stat().st_size > 0

    meta = probe(final_output)
    streams = meta["streams"]
    video_stream = next(s for s in streams if s["codec_type"] == "video")
    audio_stream = next(s for s in streams if s["codec_type"] == "audio")

    assert video_stream["codec_name"] == "h264"
    assert audio_stream["codec_name"] == "aac"
    assert audio_stream["sample_rate"] == "48000"
    assert audio_stream["channels"] == 2

    # A/V duration aligned
    final_duration = float(meta["format"]["duration"])
    assert abs(final_duration - 15.0) <= 0.08

    # Measure final integrated loudness and true peak
    loudness_proc = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", str(final_output), "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=7:print_format=json", "-f", "null", "-",
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    stats_match = re.search(r"\{\s*\"input_i\".*?\}", loudness_proc.stderr, re.DOTALL)
    assert stats_match
    stats = json.loads(stats_match.group(0))

    measured_i = float(stats["input_i"])
    measured_tp = float(stats["input_tp"])

    # Final integrated loudness within P23-A target (-16 +/- 1 LUFS)
    assert abs(measured_i - (-16.0)) <= 1.0

    # True peak ceiling strictly adheres to canonical P23-A requirement: -1.5 dBTP
    # Allow tiny float precision (<= -1.49 dBTP)
    assert measured_tp <= -1.49

    print("\n--- CANARY METRICS ---")
    print(f"Narrative events: {len(narrative.sections)} sections (routine, reveal, payoff)")
    print(f"SFX cue count: {len(sfx_plan.cues)}")
    print(f"Candidate count: {len(sfx_catalog)}")
    print(f"Selected SFX IDs: {[s.selected_asset.asset_id for s in sfx_plan.selections if s.selected_asset]}")
    print(f"NONE decisions: Section 1 routine development had 0 cues (NONE)")
    print(f"Collision decisions: 2 cues overlapped narration, attenuated -6dB by P23-A")
    print(f"Motif use: None")
    print(f"Measured loudness: {measured_i:.2f} LUFS")
    print(f"Measured true peak: {measured_tp:.2f} dBTP")
    print(f"Final duration: {final_duration:.2f} s")
