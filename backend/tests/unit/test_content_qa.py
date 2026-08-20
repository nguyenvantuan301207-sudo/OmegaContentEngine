"""Unit tests for local Content QA Engine rules."""

import uuid

from omega.application.content_qa import run_content_qa_checks
from omega.domain.content import (
    QARuleCode,
    ScriptQAStatus,
)


def test_empty_structure_triggers_blocking():
    script_data = {"hook_text": "", "sections": []}
    status, findings = run_content_qa_checks(
        script_data=script_data,
        target_duration_seconds=480,
        dna_dict={},
        brief_dict={},
    )

    assert status == ScriptQAStatus.BLOCKED
    assert any(
        f["rule_code"] == QARuleCode.EMPTY_REQUIRED_HOOK_OR_STRUCTURE.value for f in findings
    )


def test_duration_out_of_bounds_triggers_warning():
    script_data = {
        "hook_text": "Engaging hook.",
        "sections": [
            {
                "heading": "Intro",
                "narration_text": "Section text.",
                "statements": [],
            }
        ],
        "estimated_duration_seconds": 100,  # 100s vs 480s target (>25% dev)
    }

    status, findings = run_content_qa_checks(
        script_data=script_data,
        target_duration_seconds=480,
        dna_dict={},
        brief_dict={},
    )

    assert status == ScriptQAStatus.PASSED_WITH_WARNINGS
    assert any(f["rule_code"] == QARuleCode.DURATION_OUT_OF_BOUNDS.value for f in findings)


def test_unsupported_statistic_triggers_blocking():
    script_data = {
        "hook_text": "Hook text.",
        "sections": [
            {
                "heading": "Section 1",
                "narration_text": "Latency dropped by 45% under load.",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "Latency dropped by 45% under load.",
                        "statement_type": "FACTUAL",
                        "citations": [],  # Missing citation!
                    }
                ],
            }
        ],
        "estimated_duration_seconds": 480,
    }

    status, findings = run_content_qa_checks(
        script_data=script_data,
        target_duration_seconds=480,
        dna_dict={},
        brief_dict={},
    )

    assert status == ScriptQAStatus.BLOCKED
    assert any(f["rule_code"] == QARuleCode.UNSUPPORTED_STATISTIC.value for f in findings)


def test_forbidden_topic_triggers_blocking():
    script_data = {
        "hook_text": "Hook discussing crypto gambling tactics.",
        "sections": [
            {
                "heading": "Intro",
                "narration_text": "We look at crypto gambling mechanisms.",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "We look at crypto gambling mechanisms.",
                        "statement_type": "CREATIVE",
                        "citations": [],
                    }
                ],
            }
        ],
        "estimated_duration_seconds": 480,
    }

    dna_dict = {
        "constraints": {
            "forbidden_topics": ["crypto gambling"],
        }
    }

    status, findings = run_content_qa_checks(
        script_data=script_data,
        target_duration_seconds=480,
        dna_dict=dna_dict,
        brief_dict={},
    )

    assert status == ScriptQAStatus.BLOCKED
    assert any(f["rule_code"] == QARuleCode.FORBIDDEN_TOPIC_OR_TERM.value for f in findings)


def test_avoided_vocabulary_triggers_warning():
    script_data = {
        "hook_text": "Hook text.",
        "sections": [
            {
                "heading": "Intro",
                "narration_text": "This paradigm shift will synergy your workflows.",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "This paradigm shift will synergy your workflows.",
                        "statement_type": "CREATIVE",
                        "citations": [],
                    }
                ],
            }
        ],
        "estimated_duration_seconds": 480,
    }

    dna_dict = {
        "constraints": {
            "avoided_vocabulary": ["synergy", "paradigm shift"],
        }
    }

    status, findings = run_content_qa_checks(
        script_data=script_data,
        target_duration_seconds=480,
        dna_dict=dna_dict,
        brief_dict={},
    )

    assert status == ScriptQAStatus.PASSED_WITH_WARNINGS
    assert any(f["rule_code"] == QARuleCode.AVOIDED_VOCABULARY.value for f in findings)


def test_open_high_conflict_triggers_blocking():
    conflict_claim_id = str(uuid.uuid4())
    script_data = {
        "hook_text": "Hook text.",
        "sections": [
            {
                "heading": "Intro",
                "narration_text": "Controverted assertion.",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "Controverted assertion.",
                        "statement_type": "FACTUAL",
                        "citations": [{"claim_id": conflict_claim_id}],
                    }
                ],
            }
        ],
        "estimated_duration_seconds": 480,
    }

    brief_dict = {
        "contradictions": [
            {
                "claim_id": conflict_claim_id,
                "severity": "HIGH",
                "description": "Direct experimental contradiction",
            }
        ]
    }

    status, findings = run_content_qa_checks(
        script_data=script_data,
        target_duration_seconds=480,
        dna_dict={},
        brief_dict=brief_dict,
    )

    assert status == ScriptQAStatus.BLOCKED
    assert any(f["rule_code"] == QARuleCode.OPEN_HIGH_RESEARCH_CONFLICT.value for f in findings)
