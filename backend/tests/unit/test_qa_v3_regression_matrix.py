import uuid

from omega.application.guardian.adapters.production_qa_adapter import ProductionQAAdapter
from omega.application.guardian.decision_engine import GuardianDecisionEngine
from omega.application.production_qa import ProductionQAEngine
from omega.domain.guardian import (
    GuardianAction,
    GuardianCheckpoint,
    GuardianGateState,
    GuardianRiskType,
    GuardianSeverity,
)
from omega.domain.production import ProductionQARuleCode, ProductionQAStatus


def get_clean_baseline():
    request_id = str(uuid.uuid4())
    script_version_id = str(uuid.uuid4())
    channel_dna_revision_id = str(uuid.uuid4())
    asset_req_id = str(uuid.uuid4())

    request_data = {
        "id": request_id,
        "script_version_id": script_version_id,
        "channel_dna_revision_id": channel_dna_revision_id,
        "target_width": 1920,
        "target_height": 1080,
        "video_codec": "h264",
    }

    script_version_data = {
        "id": script_version_id,
    }

    content_request_data = {
        "channel_dna_revision_id": channel_dna_revision_id,
    }

    requirements_data = [
        {
            "id": asset_req_id,
            "purpose": "BACKGROUND",
            "required": True,
        }
    ]

    assets_data = [
        {
            "asset_type": "IMAGE",
            "provider_type": "SYSTEM",
            "source_ref": "valid_bg",
            "license_status": "GENERATED",
            "asset_requirement_id": asset_req_id,
        },
        {
            "asset_type": "AUDIO",
            "provider_type": "SYSTEM",
            "source_ref": "Gemini TTS",
            "license_status": "CLEARED",
            "narration_quality": "NEURAL_PRODUCTION",
        },
        {
            "asset_type": "SUBTITLE",
            "mime_type": "application/x-subrip",
            "provider_type": "SYSTEM",
            "license_status": "CLEARED",
        },
    ]

    narration_segments = [
        {
            "start_ms": 0,
            "end_ms": 5000,
        }
    ]

    subtitle_cues = []

    media_probe_summary = {
        "duration_ms": 5000,
        "width": 1920,
        "height": 1080,
        "video_codec": "h264",
        "has_audio": True,
        "mean_volume_db": -16.0,
    }

    return {
        "request_data": request_data,
        "script_version_data": script_version_data,
        "content_request_data": content_request_data,
        "requirements_data": requirements_data,
        "assets_data": assets_data,
        "narration_segments": narration_segments,
        "subtitle_cues": subtitle_cues,
        "media_probe_summary": media_probe_summary,
        "artifact_file_path": None,
        "expected_hash": None,
    }

def evaluate_full_contract(kwargs):
    # 1. Local QA
    engine = ProductionQAEngine()
    local_status, local_findings = engine.evaluate(**kwargs)

    # 2. Guardian Adapter
    adapter = ProductionQAAdapter()
    guardian_findings = adapter.evaluate(**kwargs)

    # Assert 1-to-1 finding mapping
    assert len(local_findings) == len(guardian_findings)
    local_codes = {f.rule_code.value for f in local_findings}
    guardian_codes = {f.rule_id for f in guardian_findings}
    assert local_codes == guardian_codes

    # 3. Guardian Decision
    findings_with_exceptions = [(f, None) for f in guardian_findings]
    action, gate_state, _reason = GuardianDecisionEngine.compute_decision(
        checkpoint=GuardianCheckpoint.POST_RENDER,
        findings_with_exceptions=findings_with_exceptions,
        detector_failures=[],
    )

    return local_status, local_findings, guardian_findings, action, gate_state


def test_case_1_clean_neural_production():
    kwargs = get_clean_baseline()
    local_status, local_findings, guardian_findings, action, gate_state = evaluate_full_contract(kwargs)

    assert local_status == ProductionQAStatus.PASSED
    assert local_findings == []
    assert guardian_findings == []
    assert action == GuardianAction.ALLOW
    assert gate_state == GuardianGateState.OPEN


def test_case_2_robotic_fallback_tts():
    kwargs = get_clean_baseline()
    for a in kwargs["assets_data"]:
        if a["asset_type"] == "AUDIO":
            a["narration_quality"] = "DEVELOPMENT_FALLBACK"
            a["source_ref"] = "Local TTS"
            break

    local_status, local_findings, guardian_findings, action, gate_state = evaluate_full_contract(kwargs)

    assert local_status == ProductionQAStatus.PASSED_WITH_WARNINGS
    assert {f.rule_code for f in local_findings} == {ProductionQARuleCode.ROBOTIC_FALLBACK_TTS}
    assert local_findings[0].severity.value == "WARNING"

    assert guardian_findings[0].rule_id == ProductionQARuleCode.ROBOTIC_FALLBACK_TTS.value
    assert guardian_findings[0].severity == GuardianSeverity.LOW

    assert action == GuardianAction.ALLOW_WITH_WARNING
    assert gate_state == GuardianGateState.RESTRICTED


def test_case_3_duration_below_dna_minimum():
    kwargs = get_clean_baseline()
    kwargs["content_request_data"]["default_duration_min_seconds"] = 10

    local_status, local_findings, guardian_findings, action, gate_state = evaluate_full_contract(kwargs)

    assert local_status == ProductionQAStatus.PASSED_WITH_WARNINGS
    assert {f.rule_code for f in local_findings} == {ProductionQARuleCode.DURATION_BELOW_DNA_MINIMUM}
    assert local_findings[0].severity.value == "WARNING"

    assert guardian_findings[0].severity == GuardianSeverity.LOW
    assert guardian_findings[0].risk_type == GuardianRiskType.CONTENT_QUALITY

    assert action == GuardianAction.ALLOW_WITH_WARNING
    assert gate_state == GuardianGateState.RESTRICTED


def test_case_4_subtitle_occlusion_risk():
    kwargs = get_clean_baseline()
    kwargs["subtitle_cues"] = [
        {
            "start_ms": 0,
            "end_ms": 5000,
            "cue_order": 1,
            "text": "A" * 56, # exceed 55 characters on one line
        }
    ]

    local_status, local_findings, guardian_findings, action, gate_state = evaluate_full_contract(kwargs)

    assert local_status == ProductionQAStatus.PASSED_WITH_WARNINGS
    assert {f.rule_code for f in local_findings} == {ProductionQARuleCode.SUBTITLE_OCCLUSION_RISK}
    assert local_findings[0].severity.value == "WARNING"

    assert guardian_findings[0].severity == GuardianSeverity.LOW
    assert guardian_findings[0].risk_type == GuardianRiskType.MEDIA_CORRUPTION

    assert action == GuardianAction.ALLOW_WITH_WARNING
    assert gate_state == GuardianGateState.RESTRICTED


def test_case_5_timeline_overlap():
    kwargs = get_clean_baseline()
    kwargs["narration_segments"] = [
        {
            "start_ms": 0,
            "end_ms": 3000,
        },
        {
            "start_ms": 2500,
            "end_ms": 5000,
        },
    ]

    local_status, local_findings, guardian_findings, action, gate_state = evaluate_full_contract(kwargs)

    assert local_status == ProductionQAStatus.BLOCKED
    assert {f.rule_code for f in local_findings} == {ProductionQARuleCode.TIMELINE_OVERLAP}
    assert local_findings[0].severity.value == "BLOCKING"

    assert guardian_findings[0].severity == GuardianSeverity.HIGH
    assert guardian_findings[0].risk_type == GuardianRiskType.MEDIA_CORRUPTION

    assert action == GuardianAction.REQUIRE_REVIEW
    assert gate_state == GuardianGateState.BLOCKED


def test_case_6_silent_audio_stream():
    kwargs = get_clean_baseline()
    kwargs["media_probe_summary"]["mean_volume_db"] = -91.0

    local_status, local_findings, guardian_findings, action, gate_state = evaluate_full_contract(kwargs)

    assert local_status == ProductionQAStatus.BLOCKED
    assert {f.rule_code for f in local_findings} == {ProductionQARuleCode.SILENT_AUDIO_STREAM}
    assert local_findings[0].severity.value == "BLOCKING"

    assert guardian_findings[0].severity == GuardianSeverity.CRITICAL
    assert guardian_findings[0].risk_type == GuardianRiskType.MEDIA_CORRUPTION

    assert action == GuardianAction.PAUSE
    assert gate_state == GuardianGateState.BLOCKED


def test_case_7_render_hash_mismatch(tmp_path):
    kwargs = get_clean_baseline()

    artifact = tmp_path / "artifact.mp4"
    artifact.write_bytes(b"arbitrary bytes")

    kwargs["artifact_file_path"] = artifact
    kwargs["expected_hash"] = "deliberately incorrect SHA-256 string"

    local_status, local_findings, guardian_findings, action, gate_state = evaluate_full_contract(kwargs)

    assert local_status == ProductionQAStatus.BLOCKED
    assert {f.rule_code for f in local_findings} == {ProductionQARuleCode.RENDER_HASH_MISMATCH}
    assert local_findings[0].severity.value == "BLOCKING"

    assert guardian_findings[0].severity == GuardianSeverity.CRITICAL
    assert guardian_findings[0].risk_type == GuardianRiskType.MEDIA_CORRUPTION

    # Unrecoverable rule precedence over generic CRITICAL handling
    assert action == GuardianAction.FORCE_FAIL
    assert gate_state == GuardianGateState.BLOCKED


def test_case_8_zero_duration_artifact():
    kwargs = get_clean_baseline()
    kwargs["media_probe_summary"]["duration_ms"] = 0
    kwargs["subtitle_cues"] = []

    local_status, local_findings, guardian_findings, action, gate_state = evaluate_full_contract(kwargs)

    assert local_status == ProductionQAStatus.BLOCKED
    assert {f.rule_code for f in local_findings} == {ProductionQARuleCode.ZERO_DURATION_ARTIFACT}
    assert local_findings[0].severity.value == "BLOCKING"

    assert guardian_findings[0].severity == GuardianSeverity.CRITICAL
    assert guardian_findings[0].risk_type == GuardianRiskType.MEDIA_CORRUPTION

    assert action == GuardianAction.FORCE_FAIL
    assert gate_state == GuardianGateState.BLOCKED
