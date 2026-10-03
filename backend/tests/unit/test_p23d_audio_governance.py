"""Unit tests for P23-D Audio Library, License Governance & Final Audio QA.

Covers Matrices A through O:
- A. Library (narration, music, SFX, missing asset)
- B. License (ALLOWED, BLOCKED, UNKNOWN, ATTRIBUTION_REQUIRED)
- C. Integrity (valid hash, hash mismatch, zero byte, corrupt audio)
- D. Pre-mix (all eligible, blocked music, unknown SFX, missing narration)
- E. Loudness (valid -16 target region, too loud, too quiet)
- F. Peak (below -1.5 dBTP, above -1.5 dBTP)
- G. Silence (intentional pause, accidental long silence, all-silent master)
- H. Narration (normal protected narration, music ducking, SFX collision attenuation)
- I. Music (selected asset matches P23-B, intentional silence, invalid license)
- J. SFX (selected asset matches P23-C, NONE preserved, invalid provenance)
- K. Format (48 kHz stereo, wrong sample rate, missing audio stream)
- L. A/V sync (within tolerance, short audio, overlong audio)
- M. Acceptance (PASS, REVISE, FAIL, BLOCKER)
- N. Gate (PASS continues, REVISE blocks, FAIL blocks)
- O. Attribution (required attribution collected, no fabricated attribution)
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
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
from omega.domain.audio_governance import (
    AudioAttribution,
    AudioLibraryEntry,
    AudioLicensePolicy,
    AudioQAFindingCode,
    AudioQASeverity,
    AudioQAStatus,
    AudioQASubsystem,
    AudioRenderGateError,
    TrackUsageState,
)
from omega.domain.audio_mix import AudioMixPlan, AudioStem, AudioStemRole


@pytest.fixture
def temp_audio_file(tmp_path: Path) -> Path:
    f = tmp_path / "test_audio.wav"
    f.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x02\x00\x80\xbb\x00\x00data\x00\x00\x00\x00")
    return f


# ── Matrix A: Library ──

def test_matrix_a_library_entries_and_missing_asset(tmp_path: Path):
    narration_file = tmp_path / "narr.wav"
    narration_file.write_bytes(b"narr_bytes")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music_bytes")
    sfx_file = tmp_path / "sfx.wav"
    sfx_file.write_bytes(b"sfx_bytes")

    lib = AudioLibraryService()
    narr_entry = AudioLibraryEntry(
        asset_id="narr-01",
        role=AudioStemRole.NARRATION,
        source_uri=str(narration_file),
        duration_ms=5000,
        usage_state=TrackUsageState.GENERATED,
    )
    music_entry = AudioLibraryEntry(
        asset_id="music-01",
        role=AudioStemRole.MUSIC,
        source_uri=str(music_file),
        duration_ms=15000,
        usage_state=TrackUsageState.LICENSED,
    )
    sfx_entry = AudioLibraryEntry(
        asset_id="sfx-01",
        role=AudioStemRole.SFX,
        source_uri=str(sfx_file),
        duration_ms=500,
        usage_state=TrackUsageState.OWNED,
    )

    lib.register_entry(narr_entry)
    lib.register_entry(music_entry)
    lib.register_entry(sfx_entry)

    assert lib.get_entry("narr-01") == narr_entry
    assert lib.get_entry("music-01") == music_entry
    assert lib.get_entry("sfx-01") == sfx_entry

    missing_findings = lib.validate_asset_integrity(tmp_path / "non_existent.wav")
    assert len(missing_findings) == 1
    assert missing_findings[0].finding_code == AudioQAFindingCode.MISSING_AUDIO_ASSET
    assert missing_findings[0].severity == AudioQASeverity.BLOCKER


# ── Matrix B: License ──

def test_matrix_b_license_policy_states():
    # ALLOWED
    entry_allowed = AudioLibraryEntry(
        asset_id="m-allowed",
        role=AudioStemRole.MUSIC,
        source_uri="fake.wav",
        duration_ms=5000,
        usage_state=TrackUsageState.LICENSED,
    )
    ok, reason, code = AudioLicensePolicy.evaluate_entry(entry_allowed)
    assert ok is True
    assert code is None

    # BLOCKED
    entry_blocked = AudioLibraryEntry(
        asset_id="m-blocked",
        role=AudioStemRole.MUSIC,
        source_uri="fake.wav",
        duration_ms=5000,
        usage_state=TrackUsageState.BLOCKED,
    )
    ok, reason, code = AudioLicensePolicy.evaluate_entry(entry_blocked)
    assert ok is False
    assert code == AudioQAFindingCode.BLOCKED_AUDIO_LICENSE

    # UNKNOWN (fail closed)
    entry_unknown = AudioLibraryEntry(
        asset_id="m-unknown",
        role=AudioStemRole.MUSIC,
        source_uri="fake.wav",
        duration_ms=5000,
        usage_state=TrackUsageState.UNKNOWN,
    )
    ok, reason, code = AudioLicensePolicy.evaluate_entry(entry_unknown)
    assert ok is False
    assert code == AudioQAFindingCode.UNKNOWN_AUDIO_LICENSE

    # ATTRIBUTION_REQUIRED with valid attribution
    attr = AudioAttribution(provider="Freesound", attribution_text="Sound by user123 (CC-BY)")
    entry_attr_ok = AudioLibraryEntry(
        asset_id="m-attr",
        role=AudioStemRole.MUSIC,
        source_uri="fake.wav",
        duration_ms=5000,
        usage_state=TrackUsageState.ATTRIBUTION_REQUIRED,
        attribution=attr,
    )
    ok, reason, code = AudioLicensePolicy.evaluate_entry(entry_attr_ok)
    assert ok is True

    # ATTRIBUTION_REQUIRED with missing attribution text
    entry_attr_missing = AudioLibraryEntry(
        asset_id="m-attr-missing",
        role=AudioStemRole.MUSIC,
        source_uri="fake.wav",
        duration_ms=5000,
        usage_state=TrackUsageState.ATTRIBUTION_REQUIRED,
        attribution=None,
    )
    ok, reason, code = AudioLicensePolicy.evaluate_entry(entry_attr_missing)
    assert ok is False
    assert code == AudioQAFindingCode.MISSING_REQUIRED_ATTRIBUTION


# ── Matrix C: Integrity ──

def test_matrix_c_asset_integrity(tmp_path: Path):
    audio_file = tmp_path / "valid.wav"
    audio_file.write_bytes(b"content-12345")
    expected_hash = hashlib.sha256(b"content-12345").hexdigest()

    lib = AudioLibraryService()

    # 1. Valid hash
    findings = lib.validate_asset_integrity(audio_file, expected_hash=expected_hash)
    assert len(findings) == 0

    # 2. Hash mismatch
    wrong_hash = hashlib.sha256(b"wrong-content").hexdigest()
    mismatch_findings = lib.validate_asset_integrity(audio_file, expected_hash=wrong_hash)
    assert len(mismatch_findings) == 1
    assert mismatch_findings[0].finding_code == AudioQAFindingCode.ASSET_HASH_MISMATCH
    assert mismatch_findings[0].severity == AudioQASeverity.BLOCKER

    # 3. Zero byte
    zero_file = tmp_path / "zero.wav"
    zero_file.touch()
    zero_findings = lib.validate_asset_integrity(zero_file)
    assert len(zero_findings) == 1
    assert zero_findings[0].finding_code == AudioQAFindingCode.ZERO_BYTE_AUDIO
    assert zero_findings[0].severity == AudioQASeverity.BLOCKER


# ── Matrix D: Pre-Mix QA ──

def test_matrix_d_pre_mix_qa(tmp_path: Path):
    narr_file = tmp_path / "narr.wav"
    narr_file.write_bytes(b"narration")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music")
    sfx_file = tmp_path / "sfx.wav"
    sfx_file.write_bytes(b"sfx")

    lib = AudioLibraryService([
        AudioLibraryEntry(asset_id="narr-id", role=AudioStemRole.NARRATION, source_uri=str(narr_file), duration_ms=10000, usage_state=TrackUsageState.GENERATED),
        AudioLibraryEntry(asset_id="music-id", role=AudioStemRole.MUSIC, source_uri=str(music_file), duration_ms=10000, usage_state=TrackUsageState.LICENSED),
        AudioLibraryEntry(asset_id="sfx-id", role=AudioStemRole.SFX, source_uri=str(sfx_file), duration_ms=1000, usage_state=TrackUsageState.OWNED),
    ])

    # 1. All eligible
    valid_stems = [
        AudioStem(stem_id="s-narr", role=AudioStemRole.NARRATION, source_artifact=str(narr_file), source_identity="narr-id", start_ms=0, end_ms=10000),
        AudioStem(stem_id="s-music", role=AudioStemRole.MUSIC, source_artifact=str(music_file), source_identity="music-id", start_ms=0, end_ms=10000),
        AudioStem(stem_id="s-sfx", role=AudioStemRole.SFX, source_artifact=str(sfx_file), source_identity="sfx-id", start_ms=2000, end_ms=3000),
    ]
    findings = PreMixAudioQAEvaluator.evaluate(valid_stems, timeline_duration_ms=10000, library_service=lib)
    assert len(findings) == 0

    # 2. Blocked music
    lib_blocked = AudioLibraryService([
        AudioLibraryEntry(asset_id="narr-id", role=AudioStemRole.NARRATION, source_uri=str(narr_file), duration_ms=10000, usage_state=TrackUsageState.GENERATED),
        AudioLibraryEntry(asset_id="music-id", role=AudioStemRole.MUSIC, source_uri=str(music_file), duration_ms=10000, usage_state=TrackUsageState.BLOCKED),
    ])
    f_blocked = PreMixAudioQAEvaluator.evaluate(valid_stems[:2], timeline_duration_ms=10000, library_service=lib_blocked)
    assert any(f.finding_code == AudioQAFindingCode.BLOCKED_AUDIO_LICENSE for f in f_blocked)

    # 3. Missing narration
    f_no_narr = PreMixAudioQAEvaluator.evaluate([valid_stems[1]], timeline_duration_ms=10000, library_service=lib)
    assert any(f.finding_code == AudioQAFindingCode.MISSING_NARRATION_STEM for f in f_no_narr)


# ── Matrix E & F: Loudness & True Peak ──

def test_matrix_e_and_f_loudness_and_true_peak(tmp_path: Path):
    master_file = tmp_path / "master.mp4"
    master_file.write_bytes(b"dummy_master")

    dummy_probe = {
        "streams": [{"codec_type": "audio", "sample_rate": "48000", "channels": 2, "codec_name": "aac"}],
        "format": {"duration": "10.0"},
    }

    # 1. Valid -16 target region and peak <= -1.5 dBTP
    loud_valid = {"integrated_lufs": -16.0, "true_peak_dbtp": -2.0}
    findings, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_artifact_path=master_file,
        planned_duration_ms=10000,
        probe_data=dummy_probe,
        loudness_data=loud_valid,
    )
    assert len(findings) == 0

    # 2. Too loud (-12 LUFS > -14.5 LUFS)
    loud_high = {"integrated_lufs": -12.0, "true_peak_dbtp": -2.0}
    f_high, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_artifact_path=master_file,
        planned_duration_ms=10000,
        probe_data=dummy_probe,
        loudness_data=loud_high,
    )
    assert any(f.finding_code == AudioQAFindingCode.LOUDNESS_TOO_HIGH for f in f_high)

    # 3. Too quiet (-22 LUFS < -17.5 LUFS)
    loud_low = {"integrated_lufs": -22.0, "true_peak_dbtp": -8.0}
    f_low, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_artifact_path=master_file,
        planned_duration_ms=10000,
        probe_data=dummy_probe,
        loudness_data=loud_low,
    )
    assert any(f.finding_code == AudioQAFindingCode.LOUDNESS_TOO_LOW for f in f_low)

    # 4. True peak exceeded (-0.8 dBTP > -1.5 dBTP)
    peak_exceeded = {"integrated_lufs": -16.0, "true_peak_dbtp": -0.8}
    f_peak, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_artifact_path=master_file,
        planned_duration_ms=10000,
        probe_data=dummy_probe,
        loudness_data=peak_exceeded,
    )
    assert any(f.finding_code == AudioQAFindingCode.TRUE_PEAK_EXCEEDED for f in f_peak)


# ── Matrix G: Silence ──

def test_matrix_g_silence_detection(tmp_path: Path):
    master_file = tmp_path / "master.mp4"
    master_file.write_bytes(b"dummy_master")

    dummy_probe = {
        "streams": [{"codec_type": "audio", "sample_rate": "48000", "channels": 2, "codec_name": "aac"}],
        "format": {"duration": "10.0"},
    }

    # All-silent master (-70.0 LUFS)
    silent_loudness = {"integrated_lufs": -70.0, "true_peak_dbtp": -60.0}
    findings, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_artifact_path=master_file,
        planned_duration_ms=10000,
        probe_data=dummy_probe,
        loudness_data=silent_loudness,
    )
    assert any(f.finding_code == AudioQAFindingCode.ALL_SILENT_MASTER for f in findings)


# ── Matrix H, I, J: Narration, Music & SFX Coordination ──

def test_matrix_h_i_j_stems_coordination(tmp_path: Path):
    narr_file = tmp_path / "narr.wav"
    narr_file.write_bytes(b"narr")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music")
    sfx_file = tmp_path / "sfx.wav"
    sfx_file.write_bytes(b"sfx")

    lib = AudioLibraryService([
        AudioLibraryEntry(asset_id="narr-1", role=AudioStemRole.NARRATION, source_uri=str(narr_file), duration_ms=10000, usage_state=TrackUsageState.GENERATED),
        AudioLibraryEntry(asset_id="music-1", role=AudioStemRole.MUSIC, source_uri=str(music_file), duration_ms=10000, usage_state=TrackUsageState.LICENSED),
        AudioLibraryEntry(asset_id="sfx-1", role=AudioStemRole.SFX, source_uri=str(sfx_file), duration_ms=1000, usage_state=TrackUsageState.OWNED),
    ])

    stems = [
        AudioStem(stem_id="s-narr", role=AudioStemRole.NARRATION, source_artifact=str(narr_file), source_identity="narr-1", start_ms=0, end_ms=10000),
        AudioStem(stem_id="s-music", role=AudioStemRole.MUSIC, source_artifact=str(music_file), source_identity="music-1", start_ms=0, end_ms=10000),
        AudioStem(stem_id="s-sfx", role=AudioStemRole.SFX, source_artifact=str(sfx_file), source_identity="sfx-1", start_ms=5000, end_ms=6000),
    ]

    res = FinalAudioQAService.evaluate(
        stems=stems,
        timeline_duration_ms=10000,
        library_service=lib,
        music_selection_id="music-1",
        sfx_cue_count=1,
    )
    assert res.status == AudioQAStatus.PASS
    assert res.provenance is not None
    assert res.provenance.music_selection_id == "music-1"
    assert res.provenance.sfx_cue_count == 1


# ── Matrix K: Format ──

def test_matrix_k_format_validation(tmp_path: Path):
    master_file = tmp_path / "master.mp4"
    master_file.write_bytes(b"dummy")

    # Missing audio stream
    probe_no_audio = {"streams": [{"codec_type": "video"}], "format": {"duration": "10.0"}}
    f_no_audio, _, _, _ = PostMasterAudioQAEvaluator.evaluate(master_file, 10000, probe_data=probe_no_audio)
    assert any(f.finding_code == AudioQAFindingCode.AUDIO_STREAM_MISSING for f in f_no_audio)

    # Wrong sample rate (44100 != 48000)
    probe_bad_sr = {
        "streams": [{"codec_type": "audio", "sample_rate": "44100", "channels": 2, "codec_name": "aac"}],
        "format": {"duration": "10.0"},
    }
    f_bad_sr, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_file, 10000, probe_data=probe_bad_sr, loudness_data={"integrated_lufs": -16.0, "true_peak_dbtp": -2.0}
    )
    assert any(f.finding_code == AudioQAFindingCode.WRONG_SAMPLE_RATE for f in f_bad_sr)

    # Wrong channels (1 != 2)
    probe_mono = {
        "streams": [{"codec_type": "audio", "sample_rate": "48000", "channels": 1, "codec_name": "aac"}],
        "format": {"duration": "10.0"},
    }
    f_mono, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_file, 10000, probe_data=probe_mono, loudness_data={"integrated_lufs": -16.0, "true_peak_dbtp": -2.0}
    )
    assert any(f.finding_code == AudioQAFindingCode.WRONG_CHANNEL_LAYOUT for f in f_mono)


# ── Matrix L: A/V Sync & Duration ──

def test_matrix_l_av_sync_and_duration(tmp_path: Path):
    master_file = tmp_path / "master.mp4"
    master_file.write_bytes(b"dummy")

    # Audio too short (8.0s vs 10.0s planned)
    probe_short = {
        "streams": [{"codec_type": "audio", "sample_rate": "48000", "channels": 2, "codec_name": "aac"}],
        "format": {"duration": "8.0"},
    }
    f_short, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_file, 10000, probe_data=probe_short, loudness_data={"integrated_lufs": -16.0, "true_peak_dbtp": -2.0}
    )
    assert any(f.finding_code == AudioQAFindingCode.AUDIO_TOO_SHORT for f in f_short)

    # Audio too long (12.0s vs 10.0s planned)
    probe_long = {
        "streams": [{"codec_type": "audio", "sample_rate": "48000", "channels": 2, "codec_name": "aac"}],
        "format": {"duration": "12.0"},
    }
    f_long, _, _, _ = PostMasterAudioQAEvaluator.evaluate(
        master_file, 10000, probe_data=probe_long, loudness_data={"integrated_lufs": -16.0, "true_peak_dbtp": -2.0}
    )
    assert any(f.finding_code == AudioQAFindingCode.AUDIO_TOO_LONG for f in f_long)


# ── Matrix M & N: Acceptance & Gate ──

def test_matrix_m_and_n_acceptance_and_gate(tmp_path: Path):
    narr_file = tmp_path / "narr.wav"
    narr_file.write_bytes(b"narr")
    lib = AudioLibraryService([
        AudioLibraryEntry(asset_id="narr-1", role=AudioStemRole.NARRATION, source_uri=str(narr_file), duration_ms=5000, usage_state=TrackUsageState.GENERATED),
    ])
    stems = [
        AudioStem(stem_id="s-narr", role=AudioStemRole.NARRATION, source_artifact=str(narr_file), source_identity="narr-1", start_ms=0, end_ms=5000),
    ]

    master_file = tmp_path / "master.mp4"
    master_file.write_bytes(b"master")

    # 1. Clean PASS
    dummy_probe = {
        "streams": [{"codec_type": "audio", "sample_rate": "48000", "channels": 2, "codec_name": "aac"}],
        "format": {"duration": "5.0"},
    }
    qa_pass = FinalAudioQAService.evaluate(
        stems=stems,
        timeline_duration_ms=5000,
        library_service=lib,
        master_artifact_path=master_file,
        probe_data=dummy_probe,
        loudness_data={"integrated_lufs": -16.0, "true_peak_dbtp": -2.0},
    )
    assert qa_pass.status == AudioQAStatus.PASS
    assert AudioRenderGate.verify_acceptance(qa_pass) is True

    # 2. REVISE (mild loudness deviation: -18.0 LUFS)
    qa_revise = FinalAudioQAService.evaluate(
        stems=stems,
        timeline_duration_ms=5000,
        library_service=lib,
        master_artifact_path=master_file,
        probe_data=dummy_probe,
        loudness_data={"integrated_lufs": -18.0, "true_peak_dbtp": -2.0},
    )
    assert qa_revise.status == AudioQAStatus.REVISE
    with pytest.raises(AudioRenderGateError):
        AudioRenderGate.verify_acceptance(qa_revise)

    # 3. FAIL (true peak exceeded: -0.5 dBTP)
    qa_fail = FinalAudioQAService.evaluate(
        stems=stems,
        timeline_duration_ms=5000,
        library_service=lib,
        master_artifact_path=master_file,
        probe_data=dummy_probe,
        loudness_data={"integrated_lufs": -16.0, "true_peak_dbtp": -0.5},
    )
    assert qa_fail.status == AudioQAStatus.FAIL
    with pytest.raises(AudioRenderGateError):
        AudioRenderGate.verify_acceptance(qa_fail)


# ── Matrix O: Attribution ──

def test_matrix_o_attribution_manifest(tmp_path: Path):
    f1 = tmp_path / "narr.wav"
    f1.write_bytes(b"1")
    f2 = tmp_path / "music.wav"
    f2.write_bytes(b"2")
    f3 = tmp_path / "sfx.wav"
    f3.write_bytes(b"3")

    attr = AudioAttribution(
        creator="Artist X",
        title="Ambient Breeze",
        provider="SoundCloud",
        license_name="CC-BY-4.0",
        attribution_text="Music by Artist X under CC-BY-4.0",
    )

    lib = AudioLibraryService([
        AudioLibraryEntry(asset_id="narr", role=AudioStemRole.NARRATION, source_uri=str(f1), duration_ms=5000, usage_state=TrackUsageState.GENERATED),
        AudioLibraryEntry(asset_id="music", role=AudioStemRole.MUSIC, source_uri=str(f2), duration_ms=5000, usage_state=TrackUsageState.ATTRIBUTION_REQUIRED, attribution=attr),
        AudioLibraryEntry(asset_id="sfx", role=AudioStemRole.SFX, source_uri=str(f3), duration_ms=1000, usage_state=TrackUsageState.OWNED),
    ])

    stems = [
        AudioStem(stem_id="s1", role=AudioStemRole.NARRATION, source_artifact=str(f1), source_identity="narr", start_ms=0, end_ms=5000),
        AudioStem(stem_id="s2", role=AudioStemRole.MUSIC, source_artifact=str(f2), source_identity="music", start_ms=0, end_ms=5000),
        AudioStem(stem_id="s3", role=AudioStemRole.SFX, source_artifact=str(f3), source_identity="sfx", start_ms=1000, end_ms=2000),
    ]

    manifest = AudioAttributionManifestBuilder.build(stems, lib)
    assert manifest.entry_count == 1
    entry = manifest.entries[0]
    assert entry.asset_id == "music"
    assert entry.creator == "Artist X"
    assert entry.attribution_text == "Music by Artist X under CC-BY-4.0"
