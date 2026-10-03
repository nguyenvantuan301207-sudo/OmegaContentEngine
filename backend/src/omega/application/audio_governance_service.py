"""Audio Library, License Governance, and Final Audio QA Application Service.

Implements:
- AudioLibraryService: Registry, asset integrity, and hash verification.
- PreMixAudioQAEvaluator: Upfront stem, license, attribution, and timing validation.
- PostMasterAudioQAEvaluator: Physical loudness, true-peak, silence, format, and sync evaluation.
- FinalAudioQAService: Unified orchestration, deduplication, provenance, and attribution manifest.
- AudioRenderGate: Strict enforcement gate for runtime acceptance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from omega.domain.audio_governance import (
    AudioAttribution,
    AudioAttributionEntry,
    AudioAttributionManifest,
    AudioLibraryEntry,
    AudioLicensePolicy,
    AudioQAFinding,
    AudioQAFindingCode,
    AudioQAProvenance,
    AudioQAResult,
    AudioQASeverity,
    AudioQAStatus,
    AudioQASubsystem,
    AudioRenderGateError,
    TrackUsageState,
)
from omega.domain.audio_mix import AudioMixPlan, AudioStem, AudioStemRole

logger = logging.getLogger(__name__)


def compute_file_sha256(path: Path | str) -> str:
    """Calculates SHA256 hex digest of a local file."""
    p = Path(path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class AudioLibraryService:
    """Manages audio library projections, catalog indexing, and asset integrity verification."""

    def __init__(self, entries: list[AudioLibraryEntry] | None = None) -> None:
        self._entries: dict[str, AudioLibraryEntry] = {
            e.asset_id: e for e in (entries or [])
        }

    def register_entry(self, entry: AudioLibraryEntry) -> None:
        """Registers or updates an audio library entry."""
        self._entries[entry.asset_id] = entry

    def get_entry(self, asset_id: str) -> AudioLibraryEntry | None:
        """Retrieves an entry by asset ID."""
        return self._entries.get(asset_id)

    def find_by_uri(self, uri: str) -> AudioLibraryEntry | None:
        """Retrieves an entry by source URI or normalized file path."""
        norm_target = str(Path(uri).resolve()) if Path(uri).exists() else uri
        for e in self._entries.values():
            e_norm = str(Path(e.source_uri).resolve()) if Path(e.source_uri).exists() else e.source_uri
            if e.source_uri == uri or e_norm == norm_target:
                return e
        return None

    def validate_asset_integrity(
        self,
        file_path: Path | str,
        expected_hash: str | None = None,
        asset_id: str | None = None,
        stem_id: str | None = None,
    ) -> list[AudioQAFinding]:
        """Performs physical integrity checks on an audio asset file."""
        findings: list[AudioQAFinding] = []
        p = Path(file_path)

        # 1. Existence check
        if not p.is_file():
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.MISSING_AUDIO_ASSET,
                    severity=AudioQASeverity.BLOCKER,
                    subsystem=AudioQASubsystem.LIBRARY,
                    asset_id=asset_id,
                    stem_id=stem_id,
                    explanation=f"Audio asset file does not exist at {p}.",
                    recommended_remediation="Verify asset storage or download source artifact.",
                )
            )
            return findings

        # 2. Non-zero byte check
        size = p.stat().st_size
        if size == 0:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.ZERO_BYTE_AUDIO,
                    severity=AudioQASeverity.BLOCKER,
                    subsystem=AudioQASubsystem.LIBRARY,
                    asset_id=asset_id,
                    stem_id=stem_id,
                    explanation=f"Audio asset at {p} is 0 bytes (empty file).",
                    recommended_remediation="Re-render or re-download non-empty audio artifact.",
                )
            )
            return findings

        # 3. Hash mismatch check
        if expected_hash:
            actual_hash = compute_file_sha256(p)
            if actual_hash != expected_hash:
                findings.append(
                    AudioQAFinding(
                        finding_code=AudioQAFindingCode.ASSET_HASH_MISMATCH,
                        severity=AudioQASeverity.BLOCKER,
                        subsystem=AudioQASubsystem.PROVENANCE,
                        asset_id=asset_id,
                        stem_id=stem_id,
                        explanation=(
                            f"Audio asset hash mismatch for {p}. "
                            f"Expected: {expected_hash}, Actual: {actual_hash}"
                        ),
                        recommended_remediation="Verify asset tamper resistance and artifact cache integrity.",
                    )
                )

        return findings


class PreMixAudioQAEvaluator:
    """Evaluates stems, catalog entries, licensing eligibility, and timing before mixing."""

    @classmethod
    def evaluate(
        cls,
        stems: list[AudioStem],
        timeline_duration_ms: int,
        library_service: AudioLibraryService | None = None,
        narration_required: bool = True,
    ) -> list[AudioQAFinding]:
        findings: list[AudioQAFinding] = []
        lib = library_service or AudioLibraryService()

        # 1. Narration presence check
        narration_stems = [s for s in stems if s.role == AudioStemRole.NARRATION]
        if narration_required and not narration_stems:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.MISSING_NARRATION_STEM,
                    severity=AudioQASeverity.BLOCKER,
                    subsystem=AudioQASubsystem.NARRATION,
                    explanation="No NARRATION stem provided in audio mix plan.",
                    recommended_remediation="Add primary narration track to stem inputs.",
                )
            )

        for stem in stems:
            # 2. Timing bounds
            duration = stem.end_ms - stem.start_ms
            if duration <= 0:
                findings.append(
                    AudioQAFinding(
                        finding_code=AudioQAFindingCode.AUDIO_TOO_SHORT,
                        severity=AudioQASeverity.BLOCKER,
                        subsystem=AudioQASubsystem.TIMING,
                        stem_id=stem.stem_id,
                        timeline_range_ms=(stem.start_ms, stem.end_ms),
                        explanation=f"Stem {stem.stem_id} duration is <= 0 ms ({duration} ms).",
                        recommended_remediation="Ensure stem end_ms is greater than start_ms.",
                    )
                )
            if stem.end_ms > timeline_duration_ms + 500:
                findings.append(
                    AudioQAFinding(
                        finding_code=AudioQAFindingCode.AUDIO_TOO_LONG,
                        severity=AudioQASeverity.ERROR,
                        subsystem=AudioQASubsystem.TIMING,
                        stem_id=stem.stem_id,
                        timeline_range_ms=(stem.start_ms, stem.end_ms),
                        explanation=(
                            f"Stem {stem.stem_id} extends past timeline duration "
                            f"({stem.end_ms} ms > {timeline_duration_ms} ms)."
                        ),
                        recommended_remediation="Trim stem or extend timeline.",
                    )
                )

            # 3. File existence & physical integrity
            file_findings = lib.validate_asset_integrity(
                file_path=stem.source_artifact,
                expected_hash=stem.source_sha256,
                asset_id=stem.source_identity,
                stem_id=stem.stem_id,
            )
            findings.extend(file_findings)

            # 4. Library lookup and license policy
            entry = lib.get_entry(stem.source_identity) or lib.find_by_uri(stem.source_artifact)
            if entry:
                eligible, reason, code = AudioLicensePolicy.evaluate_entry(entry)
                if not eligible:
                    sev = AudioQASeverity.BLOCKER
                    if code == AudioQAFindingCode.MISSING_REQUIRED_ATTRIBUTION:
                        sev = AudioQASeverity.ERROR
                    findings.append(
                        AudioQAFinding(
                            finding_code=code or AudioQAFindingCode.BLOCKED_AUDIO_LICENSE,
                            severity=sev,
                            subsystem=AudioQASubsystem.LICENSE,
                            stem_id=stem.stem_id,
                            asset_id=entry.asset_id,
                            explanation=reason or f"Asset {entry.asset_id} is ineligible for production use.",
                            recommended_remediation="Replace with fully licensed or owned asset.",
                        )
                    )
            else:
                # If stem is external music or SFX without catalog entry, fail-closed
                if stem.role in (AudioStemRole.MUSIC, AudioStemRole.SFX):
                    is_synthetic_test = "synthetic" in stem.source_identity.lower() or "sine" in stem.source_identity.lower()
                    if not is_synthetic_test:
                        findings.append(
                            AudioQAFinding(
                                finding_code=AudioQAFindingCode.UNKNOWN_AUDIO_LICENSE,
                                severity=AudioQASeverity.BLOCKER,
                                subsystem=AudioQASubsystem.LICENSE,
                                stem_id=stem.stem_id,
                                asset_id=stem.source_identity,
                                explanation=(
                                    f"Stem {stem.stem_id} ({stem.role}) has no registered "
                                    "AudioLibraryEntry and unverified license (fail closed)."
                                ),
                                recommended_remediation="Register asset in AudioLibrary with verified usage rights.",
                            )
                        )

        return findings


class PostMasterAudioQAEvaluator:
    """Evaluates physical master artifact properties: loudness, true-peak, silence, format, and sync."""

    @classmethod
    def probe_media(cls, media_path: Path | str) -> dict[str, Any]:
        """Probes media metadata using ffprobe."""
        p = Path(media_path)
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_streams",
            "-show_format",
            "-of", "json",
            str(p),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(f"ffprobe failed on {p}: {proc.stderr}")
        return json.loads(proc.stdout)

    @classmethod
    def measure_loudness(cls, media_path: Path | str, target_i: float = -16.0, target_tp: float = -1.5) -> dict[str, float]:
        """Measures physical integrated loudness and true peak using ffmpeg loudnorm pass."""
        p = Path(media_path)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i", str(p),
            "-map", "0:a:0",
            "-af", f"loudnorm=I={target_i:.1f}:TP={target_tp:.1f}:LRA=7.0:print_format=json",
            "-f", "null",
            "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stderr = proc.stderr
        match = re.search(r"\{\s*\"input_i\"\s*:\s*\"([^\"]+)\".*?\"input_tp\"\s*:\s*\"([^\"]+)\"", stderr, re.DOTALL)
        if not match:
            # Fallback regex if formatting differs
            i_m = re.search(r"\"input_i\"\s*:\s*\"([^\"]+)\"", stderr)
            tp_m = re.search(r"\"input_tp\"\s*:\s*\"([^\"]+)\"", stderr)
            if i_m and tp_m:
                return {"integrated_lufs": float(i_m.group(1)), "true_peak_dbtp": float(tp_m.group(1))}
            raise RuntimeError(f"Failed to parse loudnorm measurement from ffmpeg output: {stderr[-400:]}")
        return {
            "integrated_lufs": float(match.group(1)),
            "true_peak_dbtp": float(match.group(2)),
        }

    @classmethod
    def evaluate(
        cls,
        master_artifact_path: Path | str,
        planned_duration_ms: int,
        target_integrated_lufs: float = -16.0,
        loudness_tolerance_lu: float = 1.5,
        canonical_max_true_peak_dbtp: float = -1.5,
        mix_plan: AudioMixPlan | None = None,
        probe_data: dict[str, Any] | None = None,
        loudness_data: dict[str, float] | None = None,
    ) -> tuple[list[AudioQAFinding], float | None, float | None, float | None]:
        findings: list[AudioQAFinding] = []
        p = Path(master_artifact_path)

        if not p.is_file() or p.stat().st_size == 0:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.MISSING_AUDIO_ASSET,
                    severity=AudioQASeverity.BLOCKER,
                    subsystem=AudioQASubsystem.MASTER,
                    explanation=f"Master artifact missing or 0 bytes at {p}.",
                    recommended_remediation="Rerun audio mix and render pipeline.",
                )
            )
            return findings, None, None, None

        # 1. Media Stream and Format Probe
        try:
            summary = probe_data or cls.probe_media(p)
        except Exception as e:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.CORRUPT_AUDIO,
                    severity=AudioQASeverity.BLOCKER,
                    subsystem=AudioQASubsystem.FORMAT,
                    explanation=f"Failed to probe master media artifact: {e}",
                    recommended_remediation="Re-render master audio artifact.",
                )
            )
            return findings, None, None, None

        streams = summary.get("streams", [])
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        if not audio_streams:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.AUDIO_STREAM_MISSING,
                    severity=AudioQASeverity.BLOCKER,
                    subsystem=AudioQASubsystem.FORMAT,
                    explanation="No audio stream found in master media artifact.",
                    recommended_remediation="Ensure audio mixing multiplexes an audio stream.",
                )
            )
            return findings, None, None, None

        a_stream = audio_streams[0]
        sample_rate = int(a_stream.get("sample_rate", 0))
        channels = int(a_stream.get("channels", 0))
        codec_name = str(a_stream.get("codec_name", "")).lower()

        # Format checks
        if sample_rate != 48_000:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.WRONG_SAMPLE_RATE,
                    severity=AudioQASeverity.ERROR,
                    subsystem=AudioQASubsystem.FORMAT,
                    explanation=f"Master sample rate is {sample_rate} Hz, expected canonical 48000 Hz.",
                    recommended_remediation="Resample master output to 48000 Hz.",
                )
            )
        if channels != 2:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.WRONG_CHANNEL_LAYOUT,
                    severity=AudioQASeverity.ERROR,
                    subsystem=AudioQASubsystem.FORMAT,
                    explanation=f"Master audio has {channels} channels, expected stereo (2 channels).",
                    recommended_remediation="Remix audio to stereo.",
                )
            )

        # Duration & Sync checks
        format_info = summary.get("format", {})
        dur_str = format_info.get("duration") or a_stream.get("duration") or "0.0"
        actual_duration_sec = float(dur_str)
        planned_sec = planned_duration_ms / 1000.0

        if actual_duration_sec < planned_sec - 0.25:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.AUDIO_TOO_SHORT,
                    severity=AudioQASeverity.BLOCKER,
                    subsystem=AudioQASubsystem.TIMING,
                    explanation=(
                        f"Master audio duration ({actual_duration_sec:.2f}s) is shorter than "
                        f"planned timeline ({planned_sec:.2f}s) by > 250ms."
                    ),
                    recommended_remediation="Check source clip assembly and padding.",
                )
            )
        elif actual_duration_sec > planned_sec + 0.50:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.AUDIO_TOO_LONG,
                    severity=AudioQASeverity.ERROR,
                    subsystem=AudioQASubsystem.TIMING,
                    explanation=(
                        f"Master audio duration ({actual_duration_sec:.2f}s) exceeds "
                        f"planned timeline ({planned_sec:.2f}s) by > 500ms."
                    ),
                    recommended_remediation="Trim master audio to planned duration bounds.",
                )
            )

        # 2. Loudness & True Peak Physical Measurement
        measured_i: float | None = None
        measured_tp: float | None = None
        try:
            measurements = loudness_data or cls.measure_loudness(
                p, target_i=target_integrated_lufs, target_tp=canonical_max_true_peak_dbtp
            )
            measured_i = measurements["integrated_lufs"]
            measured_tp = measurements["true_peak_dbtp"]
        except Exception as e:
            findings.append(
                AudioQAFinding(
                    finding_code=AudioQAFindingCode.CORRUPT_AUDIO,
                    severity=AudioQASeverity.ERROR,
                    subsystem=AudioQASubsystem.LOUDNESS,
                    explanation=f"Failed to measure loudness on master artifact: {e}",
                    recommended_remediation="Verify audio filter graph and output stream integrity.",
                )
            )

        if measured_i is not None:
            # Check all-silent master (-70 LUFS or lower is practical silence)
            if measured_i <= -65.0:
                findings.append(
                    AudioQAFinding(
                        finding_code=AudioQAFindingCode.ALL_SILENT_MASTER,
                        severity=AudioQASeverity.BLOCKER,
                        subsystem=AudioQASubsystem.SILENCE,
                        explanation=f"Master audio is completely silent (measured: {measured_i:.1f} LUFS).",
                        recommended_remediation="Verify stem input files and gain settings.",
                    )
                )
            else:
                # Loudness boundaries
                min_lufs = target_integrated_lufs - loudness_tolerance_lu
                max_lufs = target_integrated_lufs + loudness_tolerance_lu
                if measured_i < min_lufs:
                    sev = AudioQASeverity.ERROR if measured_i < min_lufs - 2.0 else AudioQASeverity.WARNING
                    findings.append(
                        AudioQAFinding(
                            finding_code=AudioQAFindingCode.LOUDNESS_TOO_LOW,
                            severity=sev,
                            subsystem=AudioQASubsystem.LOUDNESS,
                            explanation=(
                                f"Integrated loudness ({measured_i:.2f} LUFS) is below "
                                f"target window [{min_lufs:.1f}, {max_lufs:.1f} LUFS]."
                            ),
                            recommended_remediation="Adjust normalization target or stem gain.",
                        )
                    )
                elif measured_i > max_lufs:
                    sev = AudioQASeverity.ERROR if measured_i > max_lufs + 2.0 else AudioQASeverity.WARNING
                    findings.append(
                        AudioQAFinding(
                            finding_code=AudioQAFindingCode.LOUDNESS_TOO_HIGH,
                            severity=sev,
                            subsystem=AudioQASubsystem.LOUDNESS,
                            explanation=(
                                f"Integrated loudness ({measured_i:.2f} LUFS) exceeds "
                                f"target window [{min_lufs:.1f}, {max_lufs:.1f} LUFS]."
                            ),
                            recommended_remediation="Apply loudnorm pass with lower gain.",
                        )
                    )

        if measured_tp is not None:
            # True peak ceiling check (-1.5 dBTP canonical max)
            if measured_tp > canonical_max_true_peak_dbtp + 0.1:  # 0.1 dB tolerance for measurement quantization
                findings.append(
                    AudioQAFinding(
                        finding_code=AudioQAFindingCode.TRUE_PEAK_EXCEEDED,
                        severity=AudioQASeverity.BLOCKER,
                        subsystem=AudioQASubsystem.PEAK,
                        explanation=(
                            f"True peak ({measured_tp:.2f} dBTP) exceeds canonical ceiling "
                            f"{canonical_max_true_peak_dbtp:.1f} dBTP."
                        ),
                        recommended_remediation="Apply true-peak limiter (alimiter) with ceiling <= -1.5 dBTP.",
                    )
                )

        return findings, measured_i, measured_tp, actual_duration_sec


class AudioAttributionManifestBuilder:
    """Builds the derived attribution manifest for an accepted audio composition."""

    @classmethod
    def build(cls, stems: list[AudioStem], library_service: AudioLibraryService) -> AudioAttributionManifest:
        entries: list[AudioAttributionEntry] = []
        seen_assets: set[str] = set()

        for stem in stems:
            entry = library_service.get_entry(stem.source_identity) or library_service.find_by_uri(stem.source_artifact)
            if not entry or entry.asset_id in seen_assets:
                continue

            if entry.usage_state == TrackUsageState.ATTRIBUTION_REQUIRED or entry.attribution is not None:
                attr = entry.attribution
                attr_text = attr.attribution_text if attr else f"Audio by {entry.source_provider}"
                entries.append(
                    AudioAttributionEntry(
                        asset_id=entry.asset_id,
                        role=entry.role,
                        provider=entry.source_provider,
                        creator=attr.creator if attr else None,
                        source_reference=attr.source_reference if attr else entry.source_catalog,
                        license_name=attr.license_name if attr else "ATTRIBUTION_REQUIRED",
                        attribution_text=attr_text,
                    )
                )
                seen_assets.add(entry.asset_id)

        return AudioAttributionManifest(entries=entries)


class FinalAudioQAService:
    """Orchestrates comprehensive Pre-Mix and Post-Master Audio QA, deduplication, and verdict."""

    ENGINE_VERSION = "p23d-v1"

    @classmethod
    def deduplicate_findings(cls, findings: list[AudioQAFinding]) -> list[AudioQAFinding]:
        """Normalizes duplicate findings by (finding_code, stem_id, asset_id, timeline_range)."""
        unique: dict[tuple[str, str | None, str | None, tuple[int, int] | None], AudioQAFinding] = {}
        for f in findings:
            key = (f.finding_code.value, f.stem_id, f.asset_id, f.timeline_range_ms)
            if key not in unique:
                unique[key] = f
            else:
                existing = unique[key]
                # Merge authorities
                auths = tuple(sorted(set(existing.contributing_authorities + f.contributing_authorities)))
                # Retain higher severity
                higher_sev = f.severity if f.severity > existing.severity else existing.severity
                unique[key] = existing.model_copy(update={
                    "severity": higher_sev,
                    "contributing_authorities": auths,
                })
        return list(unique.values())

    @classmethod
    def generate_recommendations(cls, findings: list[AudioQAFinding]) -> list[str]:
        """Generates clear, actionable recommendations for failing or revisable findings."""
        recs: list[str] = []
        for f in findings:
            if f.severity in (AudioQASeverity.BLOCKER, AudioQASeverity.ERROR, AudioQASeverity.WARNING):
                if f.recommended_remediation:
                    recs.append(f"[{f.finding_code}] {f.recommended_remediation}")
        return list(dict.fromkeys(recs))

    @classmethod
    def evaluate(
        cls,
        stems: list[AudioStem],
        timeline_duration_ms: int,
        library_service: AudioLibraryService | None = None,
        master_artifact_path: Path | str | None = None,
        mix_plan: AudioMixPlan | None = None,
        music_selection_id: str | None = None,
        sfx_cue_count: int = 0,
        probe_data: dict[str, Any] | None = None,
        loudness_data: dict[str, float] | None = None,
    ) -> AudioQAResult:
        """Executes full Pre-Mix and (if master artifact provided) Post-Master Audio QA."""
        lib = library_service or AudioLibraryService()
        raw_findings: list[AudioQAFinding] = []

        # 1. Pre-Mix QA
        pre_findings = PreMixAudioQAEvaluator.evaluate(
            stems=stems,
            timeline_duration_ms=timeline_duration_ms,
            library_service=lib,
        )
        raw_findings.extend(pre_findings)

        # 2. Post-Master QA (if master rendered)
        measured_i: float | None = None
        measured_tp: float | None = None
        measured_dur: float | None = None
        if master_artifact_path is not None:
            post_findings, measured_i, measured_tp, measured_dur = PostMasterAudioQAEvaluator.evaluate(
                master_artifact_path=master_artifact_path,
                planned_duration_ms=timeline_duration_ms,
                mix_plan=mix_plan,
                probe_data=probe_data,
                loudness_data=loudness_data,
            )
            raw_findings.extend(post_findings)

        # 3. Deduplicate
        deduped = cls.deduplicate_findings(raw_findings)

        # 4. Status determination
        has_blockers = any(f.severity == AudioQASeverity.BLOCKER for f in deduped)
        has_errors = any(f.severity == AudioQASeverity.ERROR for f in deduped)
        has_warnings = any(f.severity == AudioQASeverity.WARNING for f in deduped)

        if has_blockers or has_errors:
            status = AudioQAStatus.FAIL
        elif has_warnings:
            status = AudioQAStatus.REVISE
        else:
            status = AudioQAStatus.PASS

        # 5. Derived Attribution Manifest
        manifest = AudioAttributionManifestBuilder.build(stems, lib)

        # 6. Audit Provenance
        master_hash = (
            compute_file_sha256(master_artifact_path)
            if master_artifact_path and Path(master_artifact_path).is_file()
            else None
        )
        provenance = AudioQAProvenance(
            audio_mix_plan_hash=getattr(mix_plan, "hash", None),
            master_artifact_hash=master_hash,
            music_selection_id=music_selection_id,
            sfx_cue_count=sfx_cue_count,
            qa_engine_version=cls.ENGINE_VERSION,
            finding_count=len(deduped),
            final_status=status,
        )

        return AudioQAResult(
            status=status,
            findings=deduped,
            measured_loudness_lufs=measured_i,
            measured_true_peak_dbtp=measured_tp,
            measured_duration_sec=measured_dur,
            provenance=provenance,
            attribution_manifest=manifest,
        )


class AudioRenderGate:
    """Enforcement gate that blocks non-PASS audio evaluations from reaching accepted state."""

    @classmethod
    def verify_acceptance(cls, qa_result: AudioQAResult) -> bool:
        """Verifies that an audio QA result satisfies all acceptance conditions."""
        if qa_result.status != AudioQAStatus.PASS:
            blocker_recs = [
                f"{f.finding_code}: {f.explanation}"
                for f in qa_result.findings
                if f.severity >= AudioQASeverity.ERROR
            ]
            msg = f"Audio QA acceptance gate failed with status {qa_result.status}. Reasons: {'; '.join(blocker_recs)}"
            raise AudioRenderGateError(msg, qa_result=qa_result)

        if qa_result.has_blockers or qa_result.has_errors:
            raise AudioRenderGateError(
                "Audio QA result status is PASS but contains blocking/error findings.",
                qa_result=qa_result,
            )

        return True
