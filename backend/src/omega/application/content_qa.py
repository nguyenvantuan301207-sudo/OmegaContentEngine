"""Local Content QA Engine enforcing canonical rules."""

from __future__ import annotations

import re
from typing import Any

from omega.domain.content import (
    ContentStatementType,
    QARuleCode,
    QASeverity,
    ScriptQAStatus,
)

NUMERICAL_REGEX = re.compile(
    r"\b\d+(\.\d+)?%|\b\d+\s*(ms|seconds|minutes|hours|days|years|gb|mb|kb|tb|req/s|ops/s|fps|users|dollars)\b|\$\d+",
    re.IGNORECASE,
)
QUOTE_REGEX = re.compile(r'["“][^"“”]{6,}["”]')


def run_content_qa_checks(
    script_data: dict[str, Any],
    target_duration_seconds: int,
    dna_dict: dict[str, Any],
    brief_dict: dict[str, Any],
) -> tuple[ScriptQAStatus, list[dict[str, Any]]]:
    """Execute canonical local QA checks against script draft, Channel DNA, and ResearchBrief."""
    findings: list[dict[str, Any]] = []

    sections = script_data.get("sections", [])
    hook_text = script_data.get("hook_text", "").strip()
    est_duration = script_data.get("estimated_duration_seconds", 0)

    constraints = dna_dict.get("constraints", {})
    forbidden_topics = constraints.get("forbidden_topics", [])
    avoided_vocab = constraints.get("avoided_vocabulary", [])

    conflicts = brief_dict.get("contradictions", [])
    high_conflict_claim_ids = {
        str(c.get("claim_id"))
        for c in conflicts
        if c.get("severity") in ("HIGH", "CRITICAL") and c.get("claim_id")
    }

    # 1. EMPTY_REQUIRED_HOOK_OR_STRUCTURE
    if not hook_text or not sections:
        findings.append(
            {
                "rule_code": QARuleCode.EMPTY_REQUIRED_HOOK_OR_STRUCTURE.value,
                "severity": QASeverity.BLOCKING.value,
                "message": "Script is missing hook text or required narrative body sections.",
                "section_index": None,
                "statement_order": None,
                "details": {"sections_count": len(sections), "has_hook": bool(hook_text)},
            }
        )

    # 2. DURATION_OUT_OF_BOUNDS (±25% tolerance)
    if target_duration_seconds > 0:
        ratio = abs(est_duration - target_duration_seconds) / target_duration_seconds
        if ratio > 0.25:
            findings.append(
                {
                    "rule_code": QARuleCode.DURATION_OUT_OF_BOUNDS.value,
                    "severity": QASeverity.WARNING.value,
                    "message": f"Estimated duration ({est_duration}s) deviates by {ratio * 100:.1f}% from target ({target_duration_seconds}s).",
                    "section_index": None,
                    "statement_order": None,
                    "details": {
                        "estimated_duration": est_duration,
                        "target_duration": target_duration_seconds,
                    },
                }
            )

    # Section & Statement checks
    section_headings: list[str] = []
    full_script_text_parts: list[str] = [
        hook_text,
        script_data.get("closing_text", ""),
        script_data.get("cta_text", ""),
    ]

    for s_idx, sec in enumerate(sections):
        heading = sec.get("heading", "").strip()
        section_headings.append(heading.lower())
        full_script_text_parts.append(sec.get("narration_text", ""))

        statements = sec.get("statements", [])
        for stmt in statements:
            s_order = stmt.get("statement_order")
            text = stmt.get("statement_text", "")
            s_type = stmt.get("statement_type")
            citations = stmt.get("citations", [])

            # 3. FACTUAL_PROVENANCE_MISSING
            if s_type == ContentStatementType.FACTUAL.value and not citations:
                findings.append(
                    {
                        "rule_code": QARuleCode.FACTUAL_PROVENANCE_MISSING.value,
                        "severity": QASeverity.BLOCKING.value,
                        "message": f"Factual statement lacks provenance citation: '{text[:60]}...'",
                        "section_index": s_idx + 1,
                        "statement_order": s_order,
                        "details": {"statement_text": text},
                    }
                )

            # 4. UNSUPPORTED_STATISTIC
            if NUMERICAL_REGEX.search(text) and not citations:
                findings.append(
                    {
                        "rule_code": QARuleCode.UNSUPPORTED_STATISTIC.value,
                        "severity": QASeverity.BLOCKING.value,
                        "message": f"Statistical/numerical assertion lacks verified citation: '{text[:60]}...'",
                        "section_index": s_idx + 1,
                        "statement_order": s_order,
                        "details": {"statement_text": text},
                    }
                )

            # 5. UNSUPPORTED_QUOTE
            if QUOTE_REGEX.search(text) and not citations:
                findings.append(
                    {
                        "rule_code": QARuleCode.UNSUPPORTED_QUOTE.value,
                        "severity": QASeverity.BLOCKING.value,
                        "message": f"Verbatim quote assertion lacks citation attribution: '{text[:60]}...'",
                        "section_index": s_idx + 1,
                        "statement_order": s_order,
                        "details": {"statement_text": text},
                    }
                )

            # 6. OPEN_HIGH_RESEARCH_CONFLICT & BLOCKED_CLAIM_USED
            for cit in citations:
                cid = str(cit.get("claim_id"))
                if cid in high_conflict_claim_ids:
                    findings.append(
                        {
                            "rule_code": QARuleCode.OPEN_HIGH_RESEARCH_CONFLICT.value,
                            "severity": QASeverity.BLOCKING.value,
                            "message": f"Statement references a claim with open HIGH conflict: claim {cid[:8]}",
                            "section_index": s_idx + 1,
                            "statement_order": s_order,
                            "details": {"claim_id": cid},
                        }
                    )

    full_script_text = " ".join(full_script_text_parts).lower()

    # 7. FORBIDDEN_TOPIC_OR_TERM
    for forbidden in forbidden_topics:
        if forbidden.lower() in full_script_text:
            findings.append(
                {
                    "rule_code": QARuleCode.FORBIDDEN_TOPIC_OR_TERM.value,
                    "severity": QASeverity.BLOCKING.value,
                    "message": f"Script text contains forbidden topic/term from Channel DNA: '{forbidden}'",
                    "section_index": None,
                    "statement_order": None,
                    "details": {"forbidden_term": forbidden},
                }
            )

    # 8. AVOIDED_VOCABULARY
    for avoided in avoided_vocab:
        # Match whole word
        pattern = rf"\b{re.escape(avoided.lower())}\b"
        if re.search(pattern, full_script_text):
            findings.append(
                {
                    "rule_code": QARuleCode.AVOIDED_VOCABULARY.value,
                    "severity": QASeverity.WARNING.value,
                    "message": f"Script uses avoided vocabulary term: '{avoided}'",
                    "section_index": None,
                    "statement_order": None,
                    "details": {"avoided_word": avoided},
                }
            )

    # 9. DUPLICATE_OR_REPEATED_SECTION
    if len(section_headings) != len(set(section_headings)):
        findings.append(
            {
                "rule_code": QARuleCode.DUPLICATE_OR_REPEATED_SECTION.value,
                "severity": QASeverity.WARNING.value,
                "message": "Script contains duplicate section headings.",
                "section_index": None,
                "statement_order": None,
                "details": {"headings": section_headings},
            }
        )

    # Determine overall status
    has_blocking = any(
        f["severity"] in (QASeverity.BLOCKING.value, QASeverity.ERROR.value) for f in findings
    )
    has_warning = any(f["severity"] == QASeverity.WARNING.value for f in findings)

    status = (
        ScriptQAStatus.BLOCKED
        if has_blocking
        else (ScriptQAStatus.PASSED_WITH_WARNINGS if has_warning else ScriptQAStatus.PASSED)
    )

    return status, findings
