"""Content QA Adapter for Guardian subsystem.

Adapts existing OMEGA-006 Content QA rule evaluation without duplicating rules.
Translates ScriptQA findings into standardized GuardianFindingData objects.
"""

from __future__ import annotations

from typing import Any

from omega.application.content_qa import run_content_qa_checks
from omega.domain.content import QARuleCode
from omega.domain.guardian import GuardianFindingData, GuardianRiskType, GuardianSeverity

RULE_SEVERITY_RISK_MAP: dict[str, tuple[GuardianSeverity, GuardianRiskType, float]] = {
    QARuleCode.EMPTY_REQUIRED_HOOK_OR_STRUCTURE.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.CONTENT_QUALITY,
        1.0,
    ),
    QARuleCode.FACTUAL_PROVENANCE_MISSING.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.CONTENT_QUALITY,
        1.0,
    ),
    QARuleCode.BLOCKED_CLAIM_USED.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.CONTENT_QUALITY,
        1.0,
    ),
    QARuleCode.UNSUPPORTED_STATISTIC.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.CONTENT_QUALITY,
        0.95,
    ),
    QARuleCode.UNSUPPORTED_QUOTE.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.CONTENT_QUALITY,
        0.95,
    ),
    QARuleCode.OPEN_HIGH_RESEARCH_CONFLICT.value: (
        GuardianSeverity.HIGH,
        GuardianRiskType.CONTENT_QUALITY,
        0.9,
    ),
    QARuleCode.FORBIDDEN_TOPIC_OR_TERM.value: (
        GuardianSeverity.CRITICAL,
        GuardianRiskType.POLICY_VIOLATION,
        1.0,
    ),
    QARuleCode.DURATION_OUT_OF_BOUNDS.value: (
        GuardianSeverity.MEDIUM,
        GuardianRiskType.CONTENT_QUALITY,
        0.85,
    ),
    QARuleCode.AVOIDED_VOCABULARY.value: (
        GuardianSeverity.LOW,
        GuardianRiskType.CONTENT_QUALITY,
        0.8,
    ),
    QARuleCode.DUPLICATE_OR_REPEATED_SECTION.value: (
        GuardianSeverity.MEDIUM,
        GuardianRiskType.CONTENT_QUALITY,
        0.9,
    ),
}


class ContentQAAdapter:
    """Adapts OMEGA-006 Content QA into Guardian domain findings."""

    @classmethod
    def evaluate(
        cls,
        script_data: dict[str, Any],
        target_duration_seconds: int,
        dna_dict: dict[str, Any],
        brief_dict: dict[str, Any],
    ) -> list[GuardianFindingData]:
        """Run OMEGA-006 canonical QA checks and return standardized Guardian findings."""
        _status, qa_findings = run_content_qa_checks(
            script_data=script_data,
            target_duration_seconds=target_duration_seconds,
            dna_dict=dna_dict,
            brief_dict=brief_dict,
        )

        findings: list[GuardianFindingData] = []
        for qf in qa_findings:
            rule_code = qf.get("rule_code", "UNKNOWN_CONTENT_RULE")
            severity, risk_type, conf = RULE_SEVERITY_RISK_MAP.get(
                rule_code,
                (GuardianSeverity.MEDIUM, GuardianRiskType.CONTENT_QUALITY, 0.8),
            )

            location_ref: dict[str, Any] = {}
            if qf.get("section_index") is not None:
                location_ref["section_index"] = qf["section_index"]
            if qf.get("statement_order") is not None:
                location_ref["statement_order"] = qf["statement_order"]

            findings.append(
                GuardianFindingData(
                    rule_id=rule_code,
                    severity=severity,
                    risk_type=risk_type,
                    confidence=conf,
                    evidence=qf.get("details", {}),
                    location_reference=location_ref,
                    message=qf.get("message", f"Content QA rule violation: {rule_code}"),
                )
            )

        return findings
