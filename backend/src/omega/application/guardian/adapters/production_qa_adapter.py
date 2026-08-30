"""Production QA Adapter for Guardian subsystem.

Adapts existing OMEGA-007 Production QA rule evaluation without duplicating rules.
Translates ProductionQAFinding items into standardized GuardianFindingData objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omega.application.production_qa import ProductionQAEngine
from omega.domain.guardian import GuardianFindingData, GuardianRiskType, GuardianSeverity
from omega.domain.production import ProductionQARuleCode

PROD_RULE_SEVERITY_RISK_MAP: dict[str, tuple[GuardianSeverity, GuardianRiskType, float]] = {
    ProductionQARuleCode.SCRIPT_PIN_MISMATCH.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.PIPELINE_RUNAWAY,
        1.0,
    ),
    ProductionQARuleCode.DNA_LINEAGE_MISMATCH.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.PIPELINE_RUNAWAY,
        1.0,
    ),
    ProductionQARuleCode.MISSING_REQUIRED_ASSET.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.CONTENT_QUALITY,
        0.95,
    ),
    ProductionQARuleCode.BLOCKED_ASSET_RIGHTS.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.COPYRIGHT_LICENSE,
        1.0,
    ),
    ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.COPYRIGHT_LICENSE,
        0.95,
    ),
    ProductionQARuleCode.MISSING_NARRATION.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.MEDIA_CORRUPTION,
        0.95,
    ),
    ProductionQARuleCode.TIMELINE_GAP.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.MEDIA_CORRUPTION,
        0.9,
    ),
    ProductionQARuleCode.TIMELINE_OVERLAP.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.MEDIA_CORRUPTION,
        0.9,
    ),
    ProductionQARuleCode.SUBTITLE_OUT_OF_RANGE.value: (
        GuardianSeverity.LOW,
        GuardianRiskType.MEDIA_CORRUPTION,
        0.8,
    ),
    ProductionQARuleCode.SUBTITLE_EMPTY.value: (
        GuardianSeverity.LOW,
        GuardianRiskType.MEDIA_CORRUPTION,
        0.8,
    ),
    ProductionQARuleCode.RENDER_FILE_MISSING.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.MEDIA_CORRUPTION,
        1.0,
    ),
    ProductionQARuleCode.RENDER_HASH_MISMATCH.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.MEDIA_CORRUPTION,
        1.0,
    ),
    ProductionQARuleCode.ZERO_DURATION_ARTIFACT.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.MEDIA_CORRUPTION,
        1.0,
    ),
    ProductionQARuleCode.VIDEO_DIMENSION_MISMATCH.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.MEDIA_CORRUPTION,
        0.95,
    ),
    ProductionQARuleCode.VIDEO_CODEC_MISMATCH.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.MEDIA_CORRUPTION,
        0.95,
    ),
    ProductionQARuleCode.AUDIO_STREAM_MISSING.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.MEDIA_CORRUPTION,
        1.0,
    ),
    ProductionQARuleCode.FFPROBE_VALIDATION_FAILED.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.MEDIA_CORRUPTION,
        1.0,
    ),
}


class ProductionQAAdapter:
    """Adapts OMEGA-007 Production QA into Guardian domain findings."""

    def __init__(self) -> None:
        self.engine = ProductionQAEngine()

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
    ) -> list[GuardianFindingData]:
        """Run OMEGA-007 canonical QA rules and return standardized Guardian findings."""
        _status, qa_findings = self.engine.evaluate(
            request_data=request_data,
            script_version_data=script_version_data,
            content_request_data=content_request_data,
            assets_data=assets_data,
            requirements_data=requirements_data,
            narration_segments=narration_segments,
            subtitle_cues=subtitle_cues,
            media_probe_summary=media_probe_summary,
            artifact_file_path=artifact_file_path,
            expected_hash=expected_hash,
        )

        findings: list[GuardianFindingData] = []
        for qf in qa_findings:
            rule_code = qf.rule_code.value if hasattr(qf.rule_code, "value") else str(qf.rule_code)
            severity, risk_type, conf = PROD_RULE_SEVERITY_RISK_MAP.get(
                rule_code,
                (GuardianSeverity.HIGH, GuardianRiskType.MEDIA_CORRUPTION, 0.9),
            )

            location_ref = {
                "production_request_id": str(request_data.get("id", "")),
            }
            if artifact_file_path:
                location_ref["artifact_path"] = str(artifact_file_path)

            findings.append(
                GuardianFindingData(
                    rule_id=rule_code,
                    severity=severity,
                    risk_type=risk_type,
                    confidence=conf,
                    evidence={"expected_hash": expected_hash, "media_probe": media_probe_summary},
                    location_reference=location_ref,
                    message=qf.message,
                )
            )

        return findings
