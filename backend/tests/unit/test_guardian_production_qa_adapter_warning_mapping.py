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
