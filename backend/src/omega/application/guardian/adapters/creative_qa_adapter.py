"""Creative QA Adapter for Guardian subsystem.

Adapts P24-D Creative QA & Brand Acceptance findings into standardized GuardianFindingData objects.
Enables Guardian runtime safety acceptance without duplicating creative QA logic or superseding P20 authorities.
"""

from __future__ import annotations

from typing import Any, Sequence

from omega.domain.creative_qa import (
    CreativeQAFinding,
    CreativeQAFindingCode,
    CreativeQAResult,
    CreativeQASeverity,
)
from omega.domain.guardian import GuardianFindingData, GuardianRiskType, GuardianSeverity


SEVERITY_MAP: dict[CreativeQASeverity, GuardianSeverity] = {
    CreativeQASeverity.INFO: GuardianSeverity.INFO,
    CreativeQASeverity.WARNING: GuardianSeverity.LOW,
    CreativeQASeverity.ERROR: GuardianSeverity.HIGH,
    CreativeQASeverity.BLOCKER: GuardianSeverity.CRITICAL,
}

RISK_TYPE_MAP: dict[CreativeQAFindingCode, GuardianRiskType] = {
    CreativeQAFindingCode.HARD_CONSTRAINT_VIOLATION: GuardianRiskType.POLICY_VIOLATION,
    CreativeQAFindingCode.PROHIBITED_PHRASE_DETECTED: GuardianRiskType.POLICY_VIOLATION,
    CreativeQAFindingCode.MISLEADING_CLICKBAIT: GuardianRiskType.POLICY_VIOLATION,
    CreativeQAFindingCode.FABRICATED_CLAIM: GuardianRiskType.CONTENT_QUALITY,
    CreativeQAFindingCode.UNSUPPORTED_MEDICAL_CLAIM: GuardianRiskType.POLICY_VIOLATION,
    CreativeQAFindingCode.PROFANITY_DETECTED: GuardianRiskType.POLICY_VIOLATION,
    CreativeQAFindingCode.CONTENT_PILLAR_MISMATCH: GuardianRiskType.CONTENT_QUALITY,
    CreativeQAFindingCode.CHANNEL_REVISION_MISMATCH: GuardianRiskType.PIPELINE_RUNAWAY,
    CreativeQAFindingCode.AVOID_PATTERN_VIOLATION: GuardianRiskType.CONTENT_QUALITY,
    CreativeQAFindingCode.TITLE_UNGROUNDED: GuardianRiskType.CONTENT_QUALITY,
    CreativeQAFindingCode.THUMBNAIL_UNSUPPORTED_CLAIM: GuardianRiskType.CONTENT_QUALITY,
    CreativeQAFindingCode.MISSING_PHYSICAL_THUMBNAIL: GuardianRiskType.MEDIA_CORRUPTION,
    CreativeQAFindingCode.INVALID_PHYSICAL_THUMBNAIL: GuardianRiskType.MEDIA_CORRUPTION,
    CreativeQAFindingCode.VIDEO_ARTIFACT_LINEAGE_MISMATCH: GuardianRiskType.PIPELINE_RUNAWAY,
    CreativeQAFindingCode.OVERPROMISE: GuardianRiskType.CONTENT_QUALITY,
    CreativeQAFindingCode.CALM_NARRATION_HYPERACTIVE_PRODUCTION: GuardianRiskType.CONTENT_QUALITY,
}


class CreativeQAAdapter:
    """Adapts P24-D Creative QA findings into Guardian domain findings."""

    @classmethod
    def adapt_findings(
        cls,
        findings: Sequence[CreativeQAFinding],
    ) -> list[GuardianFindingData]:
        guardian_findings: list[GuardianFindingData] = []

        for f in findings:
            g_sev = SEVERITY_MAP.get(f.severity, GuardianSeverity.MEDIUM)
            g_risk = RISK_TYPE_MAP.get(f.finding_code, GuardianRiskType.CONTENT_QUALITY)
            confidence = 1.0 if f.severity in (CreativeQASeverity.BLOCKER, CreativeQASeverity.ERROR) else 0.85

            guardian_findings.append(
                GuardianFindingData(
                    rule_id=f"CREATIVE_{f.finding_code.value}",
                    severity=g_sev,
                    risk_type=g_risk,
                    confidence=confidence,
                    evidence={
                        "source_authority": f.source_authority,
                        "subsystem": f.subsystem.value,
                        "recommended_remediation": f.recommended_remediation,
                        "lineage": f.lineage,
                    },
                    location_reference={
                        "artifact": f.affected_artifact,
                    },
                    message=f.explanation,
                )
            )

        return guardian_findings

    @classmethod
    def adapt_result(
        cls,
        qa_result: CreativeQAResult,
    ) -> list[GuardianFindingData]:
        return cls.adapt_findings(qa_result.findings)
