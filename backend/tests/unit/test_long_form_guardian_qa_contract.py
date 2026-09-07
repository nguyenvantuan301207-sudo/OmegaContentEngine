import uuid
from copy import deepcopy

import pytest

from omega.application.guardian.adapters.production_qa_adapter import (
    PROD_RULE_SEVERITY_RISK_MAP,
    ProductionQAAdapter,
)
from omega.application.guardian.detectors.base import GuardianEvaluationContext
from omega.application.guardian.detectors.media_integrity import MediaIntegrityDetector
from omega.application.production_qa import ProductionQAEngine
from omega.domain.guardian import (
    CheckTriggerType,
    GuardianCheckpoint,
    GuardianRiskType,
    GuardianSeverity,
)
from omega.domain.production import (
    ProductionQARuleCode,
    ProductionQASeverity,
    ProductionQAStatus,
)

DEFERRED_RULES = {
    ProductionQARuleCode.NO_MOTION,
    ProductionQARuleCode.DURATION_TOO_SHORT,
    ProductionQARuleCode.SUBTITLE_TOO_LARGE,
    ProductionQARuleCode.INSUFFICIENT_BODY_DEPTH,
    ProductionQARuleCode.AUDIO_LOUDNESS_OUT_OF_RANGE,
}

LONG_FORM_RULES = {
    ProductionQARuleCode.VISUAL_REPETITION,
    ProductionQARuleCode.EXCESSIVE_STATIC_SCENES,
    ProductionQARuleCode.MISSING_INTRO,
    ProductionQARuleCode.MISSING_OUTRO,
}


def _long_form_context() -> dict:
    narration_texts = [
        "Welcome to this long-form guide and its practical lessons.",
        "People move through a busy city while the story develops.",
        "A representative image establishes the next important idea.",
        "The system architecture connects each processing stage clearly.",
        "A comparison summarizes the available options and tradeoffs.",
        "Thank you for watching; subscribe and share your perspective.",
    ]
    headings = [
        "Opening Hook",
        "City Movement",
        "Representative Example",
        "System Architecture",
        "Option Comparison",
        "Call to Action",
    ]
    strategies = [
        "TITLE_MOTION",
        "BROLL",
        "IMAGE",
        "DIAGRAM",
        "INFOGRAPHIC",
        "CTA",
    ]

    sections = [
        {
            "section_order": index,
            "heading": heading,
            "narration_text": narration_text,
            "estimated_duration_seconds": 10.0,
        }
        for index, (heading, narration_text) in enumerate(
            zip(headings, narration_texts, strict=True), start=1
        )
    ]
    scenes_data = [
        {
            "sequence_index": index,
            "original_strategy": strategy,
            "effective_strategy": strategy,
            "duration_seconds": 10.0,
            "asset_kind": "VIDEO" if strategy == "BROLL" else "IMAGE",
            "asset_provider": "PEXELS" if strategy in {"BROLL", "IMAGE"} else None,
            "asset_id": f"runtime-asset-{index}",
        }
        for index, strategy in enumerate(strategies, start=1)
    ]
    narration_segments = [
        {
            "scene_index": index,
            "start_ms": (index - 1) * 10_000,
            "end_ms": index * 10_000,
            "duration_ms": 10_000,
        }
        for index in range(1, 7)
    ]
    subtitle_cues = [
        {
            "cue_order": index,
            "scene_index": index,
            "start_ms": (index - 1) * 10_000,
            "end_ms": index * 10_000,
            "text": f"Long-form section {index}",
        }
        for index in range(1, 7)
    ]

    return {
        "request_data": {
            "id": "long-form-production-request",
            "script_version_id": "long-form-script",
            "channel_dna_revision_id": "dna-rev-1",
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
            "target_duration_seconds": 60,
        },
        "script_version_data": {
            "id": "long-form-script",
            "hook_text": narration_texts[0],
            "cta_text": narration_texts[-1],
            "closing_text": "",
            "sections": sections,
        },
        "content_request_data": {
            "channel_dna_revision_id": "dna-rev-1",
            "default_duration_min_seconds": 60,
        },
        "assets_data": [
            {
                "id": "visual-asset",
                "asset_type": "VIDEO",
                "provider_type": "PEXELS",
                "source_ref": "pexels-long-form-visual",
                "license_status": "LICENSED",
            },
            {
                "id": "narration-asset",
                "asset_type": "AUDIO",
                "provider_type": "SYSTEM",
                "source_ref": "Neural production narration",
                "license_status": "GENERATED",
                "narration_quality": "NEURAL_PRODUCTION",
            },
            {
                "id": "subtitle-asset",
                "asset_type": "SUBTITLE",
                "provider_type": "SYSTEM",
                "mime_type": "application/x-subrip",
                "license_status": "GENERATED",
            },
        ],
        "requirements_data": [],
        "narration_segments": narration_segments,
        "subtitle_cues": subtitle_cues,
        "media_probe_summary": {
            "duration_ms": 60_000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
            "mean_volume_db": -20.0,
        },
        "artifact_file_path": None,
        "expected_hash": None,
        "scenes_data": scenes_data,
    }


def _qa_rule_codes(findings) -> set[str]:
    return {
        finding.rule_code.value
        if hasattr(finding.rule_code, "value")
        else str(finding.rule_code)
        for finding in findings
    }


def _guardian_rule_codes(findings) -> set[str]:
    return {finding.rule_id for finding in findings}


def _evaluate_parity(context: dict):
    status, qa_findings = ProductionQAEngine().evaluate(**context)
    guardian_findings = ProductionQAAdapter().evaluate(**context)

    assert _qa_rule_codes(qa_findings) == _guardian_rule_codes(guardian_findings)
    assert len(qa_findings) == len(guardian_findings)
    return status, qa_findings, guardian_findings


def _assert_low_content_quality_mapping(guardian_findings, rule_code):
    finding = next(finding for finding in guardian_findings if finding.rule_id == rule_code.value)
    assert finding.severity == GuardianSeverity.LOW
    assert finding.risk_type == GuardianRiskType.CONTENT_QUALITY
    assert finding.confidence == 0.8


def test_clean_long_form_baseline_and_deferred_rule_safety():
    context = _long_form_context()

    assert len(context["script_version_data"]["sections"]) == 6
    assert len(context["scenes_data"]) == 6
    assert [scene["effective_strategy"] for scene in context["scenes_data"]] == [
        "TITLE_MOTION",
        "BROLL",
        "IMAGE",
        "DIAGRAM",
        "INFOGRAPHIC",
        "CTA",
    ]
    assert context["narration_segments"][-1]["end_ms"] == 60_000

    status, qa_findings, guardian_findings = _evaluate_parity(context)

    assert status == ProductionQAStatus.PASSED
    assert qa_findings == []
    assert guardian_findings == []
    assert not {rule.value for rule in DEFERRED_RULES} & _qa_rule_codes(qa_findings)


@pytest.mark.parametrize(
    ("rule_code", "mutate"),
    [
        (
            ProductionQARuleCode.VISUAL_REPETITION,
            lambda context: [
                context["scenes_data"][index].update(effective_strategy="BROLL")
                for index in range(3)
            ],
        ),
        (
            ProductionQARuleCode.EXCESSIVE_STATIC_SCENES,
            lambda context: [
                context["scenes_data"][index].update(effective_strategy=strategy)
                for index, strategy in enumerate(("DIAGRAM", "INFOGRAPHIC", "STATISTIC"))
            ],
        ),
        (
            ProductionQARuleCode.MISSING_INTRO,
            lambda context: (
                context["script_version_data"].update(hook_text=""),
                [section.update(heading="Body") for section in context["script_version_data"]["sections"]],
            ),
        ),
        (
            ProductionQARuleCode.MISSING_OUTRO,
            lambda context: (
                context["script_version_data"].update(cta_text=""),
                [section.update(heading="Body") for section in context["script_version_data"]["sections"]],
            ),
        ),
    ],
)
def test_long_form_warning_finding_set_and_mapping_parity(rule_code, mutate):
    context = _long_form_context()
    mutate(context)

    status, qa_findings, guardian_findings = _evaluate_parity(context)
    qa_codes = _qa_rule_codes(qa_findings)

    assert status == ProductionQAStatus.PASSED_WITH_WARNINGS
    assert qa_codes == {rule_code.value}
    assert next(finding for finding in qa_findings if finding.rule_code == rule_code).severity == (
        ProductionQASeverity.WARNING
    )
    _assert_low_content_quality_mapping(guardian_findings, rule_code)

    if rule_code == ProductionQARuleCode.MISSING_INTRO:
        assert ProductionQARuleCode.MISSING_OUTRO.value not in qa_codes
    if rule_code == ProductionQARuleCode.MISSING_OUTRO:
        assert ProductionQARuleCode.MISSING_INTRO.value not in qa_codes


def test_duration_warning_uses_current_canonical_guardian_mapping():
    context = _long_form_context()
    context["media_probe_summary"]["duration_ms"] = 44_999

    status, qa_findings, guardian_findings = _evaluate_parity(context)

    assert status == ProductionQAStatus.PASSED_WITH_WARNINGS
    assert ProductionQARuleCode.DURATION_BELOW_DNA_MINIMUM.value in _qa_rule_codes(qa_findings)
    _assert_low_content_quality_mapping(
        guardian_findings,
        ProductionQARuleCode.DURATION_BELOW_DNA_MINIMUM,
    )


def test_active_production_rules_have_complete_fail_closed_guardian_mapping():
    active_rules = set(ProductionQARuleCode) - DEFERRED_RULES
    active_rule_values = {rule.value for rule in active_rules}

    assert len(active_rules) == 28
    assert len(PROD_RULE_SEVERITY_RISK_MAP) == 28
    assert set(PROD_RULE_SEVERITY_RISK_MAP) == active_rule_values
    for rule_code in active_rule_values:
        assert PROD_RULE_SEVERITY_RISK_MAP[rule_code]

    with pytest.raises(KeyError):
        _ = PROD_RULE_SEVERITY_RISK_MAP[ProductionQARuleCode.NO_MOTION.value]


@pytest.mark.asyncio
async def test_media_integrity_direct_path_preserves_truthful_runtime_scenes():
    context_data = deepcopy(_long_form_context())
    for index in range(3):
        context_data["scenes_data"][index]["effective_strategy"] = "BROLL"

    detector = MediaIntegrityDetector()
    guardian_context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=uuid.uuid4(),
        diagnostic_context=context_data,
    )

    def no_database_session():
        raise AssertionError("Direct diagnostic MediaIntegrity path must not access the database")

    guardian_findings = await detector.evaluate(guardian_context, no_database_session)
    _status, qa_findings = ProductionQAEngine().evaluate(**context_data)

    assert _guardian_rule_codes(guardian_findings) == _qa_rule_codes(qa_findings)
    assert _guardian_rule_codes(guardian_findings) == {
        ProductionQARuleCode.VISUAL_REPETITION.value
    }
    _assert_low_content_quality_mapping(
        guardian_findings,
        ProductionQARuleCode.VISUAL_REPETITION,
    )
