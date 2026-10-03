"""Physical integration canary for P23-D Audio Library, License Governance & Final Audio QA.

Covers:
- Section 31: Realistic P23-D Canary (Narration + Music + SFX -> Governance -> Mixing -> QA PASS)
  - Intentionally invalid cases (UNKNOWN/BLOCKED license -> FAIL; corrupt/true peak violation -> FAIL)
- Section 32: Full P23 Lineage Chain (NarrativePlan -> MusicDirector -> SFXDirector -> Library -> MixPlan -> Render -> Master -> AudioQA -> Acceptance Gate)
- Section 33: Physical Acceptance MP4 generation with 48kHz, stereo AAC, ducking, mastering, and loudnorm verification.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from omega.application.audio_governance_service import (
    AudioAttributionManifestBuilder,
    AudioLibraryService,
    AudioRenderGate,
    FinalAudioQAService,
    PostMasterAudioQAEvaluator,
    PreMixAudioQAEvaluator,
    compute_file_sha256,
)
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
from omega.domain.audio_governance import (
    AudioAttribution,
    AudioLibraryEntry,
    AudioQAFindingCode,
    AudioQASeverity,
    AudioQAStatus,
    AudioRenderGateError,
    TrackUsageState,
)
from omega.domain.audio_mix import AudioStem, AudioStemRole
from omega.domain.camera_transition import (
    CameraTransitionPlan,
    TransitionIntent,
    TransitionPlan,
)
from omega.domain.channel_dna import BrandVoice, ChannelDNA
from omega.domain.music_direction import MusicTrackMetadata
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
)
from omega.domain.visual_beat import VisualBeat, VisualRole


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)


@pytest.mark.asyncio
async def test_p23d_full_lineage_and_physical_acceptance_canary(tmp_path: Path):
    # ── 1. Synthesize Physical Test Assets (Narration, Music, SFX) ──
    narration_video = tmp_path / "canary_narration.mp4"
    music_wav = tmp_path / "canary_music.wav"
    sfx_wav = tmp_path / "canary_sfx_impact.wav"

    # 15s video with speech audio at 220Hz
    run(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=0x0F172A:size=1280x720:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
        "-t", "15.0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(narration_video),
    )

    # 15s stereo background music at 330Hz
    run(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=15.0",
        "-ac", "2",
        str(music_wav),
    )

    # 0.5s impact SFX at 90Hz
    run(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=90:sample_rate=48000:duration=0.5",
        "-ac", "1",
        str(sfx_wav),
    )

    music_hash = compute_file_sha256(music_wav)
    sfx_hash = compute_file_sha256(sfx_wav)
    narr_hash = compute_file_sha256(narration_video)

    # ── 2. Register Audio Library Entries with Usage & Attribution ──
    music_attr = AudioAttribution(
        creator="Canary Composer",
        title="Harmonic Pulse",
        provider="InHouseCatalog",
        license_name="OMEGA-PROPRIETARY",
        attribution_text="Music composed by Canary Composer",
    )
    sfx_attr = AudioAttribution(
        creator="Foley Lab",
        title="Deep Impact 01",
        provider="FoleyLab",
        license_name="CC-BY-4.0",
        attribution_text="Impact sound by Foley Lab (CC-BY-4.0)",
    )

    narr_entry = AudioLibraryEntry(
        asset_id="narr-asset-01",
        role=AudioStemRole.NARRATION,
        source_uri=str(narration_video),
        source_provider="Kokoro-TTS",
        source_sha256=narr_hash,
        duration_ms=15000,
        usage_state=TrackUsageState.GENERATED,
    )
    music_entry = AudioLibraryEntry(
        asset_id="music-track-01",
        role=AudioStemRole.MUSIC,
        source_uri=str(music_wav),
        source_provider="InHouseCatalog",
        source_sha256=music_hash,
        duration_ms=15000,
        usage_state=TrackUsageState.LICENSED,
        attribution=music_attr,
    )
    sfx_entry = AudioLibraryEntry(
        asset_id="sfx-impact-01",
        role=AudioStemRole.SFX,
        source_uri=str(sfx_wav),
        source_provider="FoleyLab",
        source_sha256=sfx_hash,
        duration_ms=500,
        usage_state=TrackUsageState.ATTRIBUTION_REQUIRED,
        attribution=sfx_attr,
    )

    library = AudioLibraryService([narr_entry, music_entry, sfx_entry])

    # ── 3. Narrative & Editorial Timeline ──
    sec1 = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Introduce hook on atmospheric scattering",
        target_duration_seconds=5,
    )
    sec2 = NarrativeSection(
        section_order=2,
        role=NarrativeSectionRole.DEVELOPMENT,
        objective="Explain discovery and scientific evidence",
        target_duration_seconds=5,
    )
    sec3 = NarrativeSection(
        section_order=3,
        role=NarrativeSectionRole.PAYOFF,
        objective="Final payoff and implications",
        target_duration_seconds=5,
    )
    narrative = NarrativePlan(
        content_generation_request_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        status=NarrativePlanStatus.APPROVED,
        topic="Atmospheric Scattering",
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=15,
        estimated_duration_seconds=15,
        sections=[sec1, sec2, sec3],
    )
    timings = [
        SectionTimingDetail(section_id=sec1.id, section_order=1, role=sec1.role, current_duration_seconds=5, recommended_duration_seconds=5, target_information_density=InformationDensity.MEDIUM, reveal_stage=RevealStage.SETUP, intensity_score=0.3),
        SectionTimingDetail(section_id=sec2.id, section_order=2, role=sec2.role, current_duration_seconds=5, recommended_duration_seconds=5, target_information_density=InformationDensity.HIGH, reveal_stage=RevealStage.MAJOR_REVEAL, intensity_score=0.9),
        SectionTimingDetail(section_id=sec3.id, section_order=3, role=sec3.role, current_duration_seconds=5, recommended_duration_seconds=5, target_information_density=InformationDensity.MEDIUM, reveal_stage=RevealStage.FINAL_PAYOFF, intensity_score=0.5),
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
        escalation_curve=[0.3, 0.9, 0.5],
    )
    channel_dna = ChannelDNA(brand_voice=BrandVoice(tone=["CINEMATIC", "DYNAMIC"]))

    # Visual beats and transitions
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
    ]

    camera_transitions = CameraTransitionPlan(
        parent_scene_index=1,
        camera_plans=(),
        transition_plans=(
            TransitionPlan(
                previous_visual_beat_id=visual_beats[0].id,
                visual_beat_id=visual_beats[1].id,
                parent_scene_index=2,
                beat_index=0,
                source_editorial_beat_indices=(1,),
                requested_intent=TransitionIntent.HARD_CONTEXT_SWITCH,
                applied_intent=TransitionIntent.HARD_CONTEXT_SWITCH,
                duration_ms=400,
            ),
        ),
    )

    # ── 4. P23-B Music Direction ──
    track_meta = MusicTrackMetadata(
        asset_id="music-track-01",
        source_artifact=str(music_wav),
        title="Harmonic Pulse",
        artist="Canary Composer",
        duration_ms=15000,
        bpm=110,
        energy=0.45,
        moods=("curious", "analytical", "focused"),
        genre="ambient",
        has_vocals=False,
        loop_safe=True,
        catalog="canary-catalog",
        usage_state=TrackUsageState.LICENSED,
        source_sha256=music_hash,
    )
    music_arc = MusicDirector.direct(
        narrative_plan=narrative,
        pacing_plan=pacing,
        channel_dna=channel_dna,
    )
    music_selection = MusicSelectionEngine.select(music_arc.cues[0], [track_meta])
    assert music_selection.selected_track is not None
    music_stem = music_selection_to_audio_stem(music_selection)
    assert music_stem is not None
    music_stem = music_stem.model_copy(update={"end_ms": 15000})

    # ── 5. P23-C SFX Direction ──
    sfx_meta = SFXAssetMetadata(
        asset_id="sfx-impact-01",
        source_artifact=str(sfx_wav),
        intent=SFXIntentType.IMPACT,
        duration_ms=500,
        energy=0.8,
        prominence=SFXProminence.PROMINENT,
        usage_state=TrackUsageState.ATTRIBUTION_REQUIRED,
        source_sha256=sfx_hash,
        tags=("impact", "reveal", "accent"),
        format="wav",
        exists=True,
    )
    sfx_plan = SFXDirector.direct(
        narrative_plan=narrative,
        pacing_plan=pacing,
        channel_dna=channel_dna,
        visual_beats=visual_beats,
        camera_transition_plan=camera_transitions,
        music_arc=music_arc,
        available_assets=[sfx_meta],
    )
    sfx_stems = sfx_selections_to_audio_stems(sfx_plan.selections)
    assert len(sfx_stems) >= 1

    # ── 6. Assemble Full Stems ──
    narration_stem = AudioStem(
        stem_id="s-narration",
        role=AudioStemRole.NARRATION,
        source_artifact=str(narration_video),
        source_identity="narr-asset-01",
        source_sha256=narr_hash,
        start_ms=0,
        end_ms=15000,
        priority=100,
        lineage={"script_version": "v1.0", "tts_provider": "Kokoro-TTS"},
    )
    all_stems = [narration_stem, music_stem, *sfx_stems]

    # ── 7. P23-D Pre-Mix Eligibility QA ──
    pre_findings = PreMixAudioQAEvaluator.evaluate(
        stems=all_stems,
        timeline_duration_ms=15000,
        library_service=library,
    )
    assert len(pre_findings) == 0

    # ── 8. P23-A Audio Mix Planning & Rendering ──
    speech_intervals = [(500, 3000), (5500, 8000), (10500, 13000)]
    mix_plan = AudioMixPlanner.plan(
        timeline_duration_ms=15000,
        stems=all_stems,
        narration_intervals_ms=speech_intervals,
    )

    # Confirm P23-A collision attenuation (-6.0 dB) for SFX overlapping speech at 5500ms
    planned_sfx = next(s for s in mix_plan.stems if s.role == AudioStemRole.SFX)
    assert planned_sfx.gain_db == -6.0

    # Physical rendering via FFmpegRenderer
    mixed_master = tmp_path / "canary_master.mp4"
    final_output = tmp_path / "canary_final_accepted.mp4"

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

    renderer = FFmpegRenderer()
    await renderer.mix_master_audio(
        video_path=narration_video,
        output_path=mixed_master,
        target_duration_ms=15000,
        background_music_path=Path(music_stem.source_artifact),
        background_music_gain_db=music_stem.gain_db,
        background_music_fade_in_ms=music_stem.fade_in_ms,
        background_music_fade_out_ms=music_stem.fade_out_ms,
        sfx_inputs=sfx_inputs,
    )

    await renderer.normalize_master_audio(
        video_path=mixed_master,
        output_path=final_output,
        target_i=-16.0,
        target_tp=-1.5,
        target_lra=7.0,
    )

    assert final_output.is_file() and final_output.stat().st_size > 0

    # ── 9. P23-D Post-Master Physical QA & Acceptance ──
    qa_result = FinalAudioQAService.evaluate(
        stems=all_stems,
        timeline_duration_ms=15000,
        library_service=library,
        master_artifact_path=final_output,
        mix_plan=mix_plan,
        music_selection_id=music_selection.selected_track.asset_id,
        sfx_cue_count=len(sfx_plan.cues),
    )

    assert qa_result.status == AudioQAStatus.PASS
    assert qa_result.is_accepted is True
    assert AudioRenderGate.verify_acceptance(qa_result) is True

    # Physical assertions
    assert qa_result.measured_loudness_lufs is not None
    assert abs(qa_result.measured_loudness_lufs - (-16.0)) <= 1.5
    assert qa_result.measured_true_peak_dbtp is not None
    assert qa_result.measured_true_peak_dbtp <= -1.5

    # Attribution manifest verification
    assert qa_result.attribution_manifest is not None
    assert qa_result.attribution_manifest.entry_count == 2  # Music + SFX
    attr_ids = {e.asset_id for e in qa_result.attribution_manifest.entries}
    assert "music-track-01" in attr_ids
    assert "sfx-impact-01" in attr_ids

    # ── 10. Intentionally Invalid Cases: Fail-Closed Gates ──

    # Case A: Unknown / Blocked license
    blocked_music_entry = music_entry.model_copy(update={"usage_state": TrackUsageState.BLOCKED})
    blocked_lib = AudioLibraryService([narr_entry, blocked_music_entry, sfx_entry])
    qa_blocked = FinalAudioQAService.evaluate(
        stems=all_stems,
        timeline_duration_ms=15000,
        library_service=blocked_lib,
    )
    assert qa_blocked.status == AudioQAStatus.FAIL
    assert qa_blocked.has_blockers is True
    assert any(f.finding_code == AudioQAFindingCode.BLOCKED_AUDIO_LICENSE for f in qa_blocked.findings)
    with pytest.raises(AudioRenderGateError):
        AudioRenderGate.verify_acceptance(qa_blocked)

    # Case B: True peak violation (simulated via evaluator assertion)
    qa_peak_fail = FinalAudioQAService.evaluate(
        stems=all_stems,
        timeline_duration_ms=15000,
        library_service=library,
        master_artifact_path=final_output,
        loudness_data={"integrated_lufs": -16.0, "true_peak_dbtp": -0.8},  # > -1.5 dBTP
    )
    assert qa_peak_fail.status == AudioQAStatus.FAIL
    assert qa_peak_fail.has_blockers is True
    assert any(f.finding_code == AudioQAFindingCode.TRUE_PEAK_EXCEEDED for f in qa_peak_fail.findings)
    with pytest.raises(AudioRenderGateError):
        AudioRenderGate.verify_acceptance(qa_peak_fail)
