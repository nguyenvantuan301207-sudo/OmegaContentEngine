"""Production QA engine executing the 17 canonical production quality and rights rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omega.application.media_storage import compute_sha256
from omega.domain.production import (
    LicenseStatus,
    ProductionQAFinding,
    ProductionQARuleCode,
    ProductionQASeverity,
    ProductionQAStatus,
)


class ProductionQAEngine:
    """Evaluates the 17 canonical Production QA rules against production context and media probe."""

    def evaluate(
        self,
        request_data: dict[str, Any],
        script_version_data: dict[str, Any],
        content_request_data: dict[str, Any],
        assets_data: list[dict[str, Any]],
        requirements_data: list[dict[str, Any]],
        narration_segments: list[dict[str, Any]],
        subtitle_cues: list[dict[str, Any]],
        media_probe_summary: dict[str, Any] | None,
        artifact_file_path: Path | str | None,
        expected_hash: str | None,
    ) -> tuple[ProductionQAStatus, list[ProductionQAFinding]]:
        """Run all 17 rules and return (status, findings)."""
        findings: list[ProductionQAFinding] = []

        # ── 1. SCRIPT_PIN_MISMATCH (BLOCKING) ──
        req_script_id = str(request_data.get("script_version_id"))
        actual_script_id = str(script_version_data.get("id"))
        if req_script_id != actual_script_id:
            findings.append(
                ProductionQAFinding(
                    rule_code=ProductionQARuleCode.SCRIPT_PIN_MISMATCH,
                    severity=ProductionQASeverity.BLOCKING,
                    message=f"ProductionRequest pinned script {req_script_id} does not match script {actual_script_id}.",
                )
            )

        # ── 2. DNA_LINEAGE_MISMATCH (BLOCKING) ──
        req_dna_id = str(request_data.get("channel_dna_revision_id"))
        content_dna_id = str(content_request_data.get("channel_dna_revision_id"))
        if req_dna_id != content_dna_id:
            findings.append(
                ProductionQAFinding(
                    rule_code=ProductionQARuleCode.DNA_LINEAGE_MISMATCH,
                    severity=ProductionQASeverity.BLOCKING,
                    message=f"ProductionRequest DNA revision {req_dna_id} does not match ContentRequest DNA {content_dna_id}.",
                )
            )

        # ── 3. MISSING_REQUIRED_ASSET (BLOCKING) ──
        resolved_req_ids = {
            str(a.get("asset_requirement_id")) for a in assets_data if a.get("asset_requirement_id")
        }
        for req in requirements_data:
            if req.get("required", True) and str(req.get("id")) not in resolved_req_ids:
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.MISSING_REQUIRED_ASSET,
                        severity=ProductionQASeverity.BLOCKING,
                        message=f"Required asset requirement {req.get('id')} ({req.get('purpose')}) was not resolved.",
                    )
                )

        # ── 4. BLOCKED_ASSET_RIGHTS (BLOCKING) ──
        for asset in assets_data:
            if asset.get("license_status") == LicenseStatus.BLOCKED.value:
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.BLOCKED_ASSET_RIGHTS,
                        severity=ProductionQASeverity.BLOCKING,
                        message=f"Asset {asset.get('id')} has BLOCKED license status: {asset.get('source_ref')}.",
                    )
                )

        # ── 5. UNKNOWN_REQUIRED_ASSET_RIGHTS (BLOCKING) ──
        for asset in assets_data:
            if asset.get("license_status") == LicenseStatus.UNKNOWN.value and asset.get(
                "asset_requirement_id"
            ):
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS,
                        severity=ProductionQASeverity.BLOCKING,
                        message=f"Required asset {asset.get('id')} has UNKNOWN rights status.",
                    )
                )

        # ── 6. MISSING_NARRATION (BLOCKING) ──
        if not narration_segments:
            findings.append(
                ProductionQAFinding(
                    rule_code=ProductionQARuleCode.MISSING_NARRATION,
                    severity=ProductionQASeverity.BLOCKING,
                    message="No narration segments exist for this production request.",
                )
            )

        # ── 7. TIMELINE_GAP & 8. TIMELINE_OVERLAP (BLOCKING) ──
        for i in range(len(narration_segments)):
            curr = narration_segments[i]
            c_start = int(curr.get("start_ms", 0))
            c_end = int(curr.get("end_ms", 0))

            if c_end <= c_start:
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.TIMELINE_OVERLAP,
                        severity=ProductionQASeverity.BLOCKING,
                        message=f"Narration segment {i + 1} has non-positive duration ({c_end - c_start}ms).",
                    )
                )

            if i > 0:
                prev = narration_segments[i - 1]
                p_end = int(prev.get("end_ms", 0))
                if c_start < p_end:
                    findings.append(
                        ProductionQAFinding(
                            rule_code=ProductionQARuleCode.TIMELINE_OVERLAP,
                            severity=ProductionQASeverity.BLOCKING,
                            message=f"Narration segment {i + 1} overlaps segment {i} by {p_end - c_start}ms.",
                        )
                    )
                elif (c_start - p_end) > 1000:
                    findings.append(
                        ProductionQAFinding(
                            rule_code=ProductionQARuleCode.TIMELINE_GAP,
                            severity=ProductionQASeverity.BLOCKING,
                            message=f"Unexplained timeline gap of {c_start - p_end}ms between narration segments {i} and {i + 1}.",
                        )
                    )

        # Subtitle overlap check
        for i in range(len(subtitle_cues)):
            cue = subtitle_cues[i]
            text = str(cue.get("text", "")).strip()

            # ── 10. SUBTITLE_EMPTY (WARNING) ──
            if not text:
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.SUBTITLE_EMPTY,
                        severity=ProductionQASeverity.WARNING,
                        message=f"Subtitle cue {cue.get('cue_order', i + 1)} has empty text.",
                    )
                )

            if i > 0:
                prev_cue = subtitle_cues[i - 1]
                if int(cue.get("start_ms", 0)) < int(prev_cue.get("end_ms", 0)):
                    findings.append(
                        ProductionQAFinding(
                            rule_code=ProductionQARuleCode.TIMELINE_OVERLAP,
                            severity=ProductionQASeverity.BLOCKING,
                            message=f"Subtitle cue {cue.get('cue_order', i + 1)} overlaps previous cue.",
                        )
                    )

        # ── 9. SUBTITLE_OUT_OF_RANGE (WARNING) ──
        total_dur_ms = media_probe_summary.get("duration_ms", 0) if media_probe_summary else 0
        if total_dur_ms > 0:
            for cue in subtitle_cues:
                if int(cue.get("end_ms", 0)) > (total_dur_ms + 500):
                    findings.append(
                        ProductionQAFinding(
                            rule_code=ProductionQARuleCode.SUBTITLE_OUT_OF_RANGE,
                            severity=ProductionQASeverity.WARNING,
                            message=f"Subtitle cue {cue.get('cue_order')} end time {cue.get('end_ms')}ms exceeds video duration {total_dur_ms}ms.",
                        )
                    )

        # ── Physical & Probe Rules (11 - 17) ──
        if artifact_file_path:
            p = Path(artifact_file_path)
            # ── 11. RENDER_FILE_MISSING (BLOCKING) ──
            if not p.is_file():
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.RENDER_FILE_MISSING,
                        severity=ProductionQASeverity.BLOCKING,
                        message=f"Rendered media file '{artifact_file_path}' does not exist on disk.",
                    )
                )
            else:
                # ── 12. RENDER_HASH_MISMATCH (BLOCKING) ──
                if expected_hash:
                    actual_hash = compute_sha256(p)
                    if actual_hash != expected_hash:
                        findings.append(
                            ProductionQAFinding(
                                rule_code=ProductionQARuleCode.RENDER_HASH_MISMATCH,
                                severity=ProductionQASeverity.BLOCKING,
                                message=f"File SHA-256 {actual_hash} does not match expected {expected_hash}.",
                            )
                        )

        if media_probe_summary:
            # ── 13. ZERO_DURATION_ARTIFACT (BLOCKING) ──
            dur = int(media_probe_summary.get("duration_ms", 0))
            if dur <= 0:
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.ZERO_DURATION_ARTIFACT,
                        severity=ProductionQASeverity.BLOCKING,
                        message="Media artifact has 0ms duration.",
                    )
                )

            # ── 14. VIDEO_DIMENSION_MISMATCH (BLOCKING) ──
            target_w = int(request_data.get("target_width", 1920))
            target_h = int(request_data.get("target_height", 1080))
            actual_w = media_probe_summary.get("width")
            actual_h = media_probe_summary.get("height")
            if actual_w is not None and (actual_w != target_w or actual_h != target_h):
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.VIDEO_DIMENSION_MISMATCH,
                        severity=ProductionQASeverity.BLOCKING,
                        message=f"Rendered dimensions {actual_w}x{actual_h} do not match requested {target_w}x{target_h}.",
                    )
                )

            # ── 15. VIDEO_CODEC_MISMATCH (BLOCKING) ──
            req_codec = str(request_data.get("video_codec", "h264")).lower()
            actual_codec = str(media_probe_summary.get("video_codec", "")).lower()
            if actual_codec and req_codec not in actual_codec and actual_codec not in req_codec:
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.VIDEO_CODEC_MISMATCH,
                        severity=ProductionQASeverity.BLOCKING,
                        message=f"Video codec '{actual_codec}' does not match requested '{req_codec}'.",
                    )
                )

            # ── 16. AUDIO_STREAM_MISSING (BLOCKING) ──
            if not media_probe_summary.get("has_audio", False):
                findings.append(
                    ProductionQAFinding(
                        rule_code=ProductionQARuleCode.AUDIO_STREAM_MISSING,
                        severity=ProductionQASeverity.BLOCKING,
                        message="Rendered MP4 artifact has no audio stream.",
                    )
                )
        elif artifact_file_path and Path(artifact_file_path).is_file():
            # ── 17. FFPROBE_VALIDATION_FAILED (BLOCKING) ──
            findings.append(
                ProductionQAFinding(
                    rule_code=ProductionQARuleCode.FFPROBE_VALIDATION_FAILED,
                    severity=ProductionQASeverity.BLOCKING,
                    message="Failed to probe media artifact via ffprobe.",
                )
            )

        # ── Calculate Overall Status ──
        has_blocking = any(
            f.severity in (ProductionQASeverity.BLOCKING, ProductionQASeverity.ERROR)
            for f in findings
        )
        has_warning = any(f.severity == ProductionQASeverity.WARNING for f in findings)

        if has_blocking:
            status = ProductionQAStatus.BLOCKED
        elif has_warning:
            status = ProductionQAStatus.PASSED_WITH_WARNINGS
        else:
            status = ProductionQAStatus.PASSED

        return status, findings
