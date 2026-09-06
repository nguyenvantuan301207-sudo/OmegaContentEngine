import pytest

from omega.application.guardian.adapters.production_qa_adapter import (
    PROD_RULE_SEVERITY_RISK_MAP,
)
from omega.application.guardian.decision_engine import GuardianDecisionEngine
from omega.domain.guardian import (
    GuardianAction,
    GuardianCheckpoint,
    GuardianFindingData,
    GuardianGateState,
    GuardianRiskType,
    GuardianSeverity,
)
from omega.domain.production import ProductionQARuleCode


def test_robotic_fallback_tts_preserves_warning_semantics():
    severity, risk_type, confidence = PROD_RULE_SEVERITY_RISK_MAP[
        ProductionQARuleCode.ROBOTIC_FALLBACK_TTS.value
    ]

    assert severity == GuardianSeverity.LOW
    assert risk_type == GuardianRiskType.MEDIA_CORRUPTION
    assert confidence == 0.8

    finding = GuardianFindingData(
        rule_id=ProductionQARuleCode.ROBOTIC_FALLBACK_TTS.value,
        severity=severity,
        risk_type=risk_type,
        confidence=confidence,
        evidence={},
        location_reference={},
        message="Local fallback TTS warning.",
    )

    action, gate_state, _reason = GuardianDecisionEngine.compute_decision(
        checkpoint=GuardianCheckpoint.POST_RENDER,
        findings_with_exceptions=[(finding, None)],
        detector_failures=[],
    )

    assert action == GuardianAction.ALLOW_WITH_WARNING
    assert gate_state == GuardianGateState.RESTRICTED


def test_contract_completeness():
    required_rules = [
        "SCRIPT_PIN_MISMATCH",
        "DNA_LINEAGE_MISMATCH",
        "MISSING_REQUIRED_ASSET",
        "BLOCKED_ASSET_RIGHTS",
        "UNKNOWN_REQUIRED_ASSET_RIGHTS",
        "MISSING_NARRATION",
        "TIMELINE_GAP",
        "TIMELINE_OVERLAP",
        "SUBTITLE_OUT_OF_RANGE",
        "SUBTITLE_EMPTY",
        "RENDER_FILE_MISSING",
        "RENDER_HASH_MISMATCH",
        "ZERO_DURATION_ARTIFACT",
        "VIDEO_DIMENSION_MISMATCH",
        "VIDEO_CODEC_MISMATCH",
        "AUDIO_STREAM_MISSING",
        "FFPROBE_VALIDATION_FAILED",
        "SILENT_AUDIO_STREAM",
        "DURATION_BELOW_DNA_MINIMUM",
        "PLACEHOLDER_ONLY_VISUALS",
        "NO_CONTENTFUL_VISUAL_ASSET",
        "MISSING_SUBTITLE_RENDER",
        "ROBOTIC_FALLBACK_TTS",
        "SUBTITLE_OCCLUSION_RISK",
    ]
    for rule in required_rules:
        assert getattr(ProductionQARuleCode, rule).value in PROD_RULE_SEVERITY_RISK_MAP


@pytest.mark.parametrize(
    "rule_enum, expected_risk",
    [
        (ProductionQARuleCode.DURATION_BELOW_DNA_MINIMUM, GuardianRiskType.CONTENT_QUALITY),
        (ProductionQARuleCode.SUBTITLE_OCCLUSION_RISK, GuardianRiskType.MEDIA_CORRUPTION),
    ]
)
def test_warning_semantics(rule_enum, expected_risk):
    severity, risk_type, confidence = PROD_RULE_SEVERITY_RISK_MAP[rule_enum.value]
    assert severity == GuardianSeverity.LOW
    assert risk_type == expected_risk

    finding = GuardianFindingData(
        rule_id=rule_enum.value,
        severity=severity,
        risk_type=risk_type,
        confidence=confidence,
        evidence={},
        location_reference={},
        message="Test warning",
    )
    action, gate_state, _reason = GuardianDecisionEngine.compute_decision(
        checkpoint=GuardianCheckpoint.POST_RENDER,
        findings_with_exceptions=[(finding, None)],
        detector_failures=[],
    )
    assert action == GuardianAction.ALLOW_WITH_WARNING
    assert gate_state == GuardianGateState.RESTRICTED


@pytest.mark.parametrize(
    "rule_enum, expected_severity, expected_risk",
    [
        (ProductionQARuleCode.SILENT_AUDIO_STREAM, GuardianSeverity.CRITICAL, GuardianRiskType.MEDIA_CORRUPTION),
        (ProductionQARuleCode.PLACEHOLDER_ONLY_VISUALS, GuardianSeverity.HIGH, GuardianRiskType.CONTENT_QUALITY),
        (ProductionQARuleCode.NO_CONTENTFUL_VISUAL_ASSET, GuardianSeverity.HIGH, GuardianRiskType.CONTENT_QUALITY),
        (ProductionQARuleCode.MISSING_SUBTITLE_RENDER, GuardianSeverity.HIGH, GuardianRiskType.MEDIA_CORRUPTION),
    ]
)
def test_blocking_contract(rule_enum, expected_severity, expected_risk):
    severity, risk_type, confidence = PROD_RULE_SEVERITY_RISK_MAP[rule_enum.value]
    assert severity == expected_severity
    assert risk_type == expected_risk


def test_unmapped_fail_closed():
    with pytest.raises(KeyError):
        _ = PROD_RULE_SEVERITY_RISK_MAP["UNMAPPED_TEST_RULE"]
