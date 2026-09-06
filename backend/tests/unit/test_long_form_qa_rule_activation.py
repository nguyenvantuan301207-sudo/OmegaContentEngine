
from omega.application.guardian.adapters.production_qa_adapter import (
    PROD_RULE_SEVERITY_RISK_MAP,
    ProductionQAAdapter,
)
from omega.application.production_qa import ProductionQAEngine
from omega.domain.guardian import GuardianRiskType, GuardianSeverity
from omega.domain.production import ProductionQARuleCode, ProductionQASeverity, ProductionQAStatus


def _minimal_baseline(script_data=None, scenes_data=None):
    if script_data is None:
        script_data = {"id": "test_script_id"}

    # We must provide some baseline that passes other rules so we don't get BLOCKED.
    request_data = {
        "id": "test_req_id",
        "script_version_id": "test_script_id",
        "channel_dna_revision_id": "test_dna_id"
    }
    content_request_data = {
        "channel_dna_revision_id": "test_dna_id"
    }
    assets_data = []
    requirements_data = []
    narration_segments = [{"start_ms": 0, "end_ms": 1000}]
    subtitle_cues = []
    media_probe_summary = None
    artifact_file_path = None
    expected_hash = None

    return {
        "request_data": request_data,
        "script_version_data": script_data,
        "content_request_data": content_request_data,
        "assets_data": assets_data,
        "requirements_data": requirements_data,
        "narration_segments": narration_segments,
        "subtitle_cues": subtitle_cues,
        "media_probe_summary": media_probe_summary,
        "artifact_file_path": artifact_file_path,
        "expected_hash": expected_hash,
        "scenes_data": scenes_data
    }


def test_visual_repetition_positive():
    engine = ProductionQAEngine()
    scenes = [
        {"effective_strategy": "BROLL"},
        {"effective_strategy": "BROLL"},
        {"effective_strategy": "BROLL"}
    ]
    status, findings = engine.evaluate(**_minimal_baseline(scenes_data=scenes))
    assert any(f.rule_code == ProductionQARuleCode.VISUAL_REPETITION for f in findings)


def test_visual_repetition_negative():
    engine = ProductionQAEngine()
    scenes = [
        {"effective_strategy": "BROLL"},
        {"effective_strategy": "BROLL"},
        {"effective_strategy": "IMAGE"}
    ]
    status, findings = engine.evaluate(**_minimal_baseline(scenes_data=scenes))
    assert not any(f.rule_code == ProductionQARuleCode.VISUAL_REPETITION for f in findings)


def test_excessive_static_scenes_positive():
    engine = ProductionQAEngine()
    scenes = [
        {"effective_strategy": "DIAGRAM"},
        {"effective_strategy": "INFOGRAPHIC"},
        {"effective_strategy": "STATISTIC"}
    ]
    status, findings = engine.evaluate(**_minimal_baseline(scenes_data=scenes))
    assert any(f.rule_code == ProductionQARuleCode.EXCESSIVE_STATIC_SCENES for f in findings)


def test_excessive_static_scenes_negative():
    engine = ProductionQAEngine()
    scenes = [
        {"effective_strategy": "DIAGRAM"},
        {"effective_strategy": "INFOGRAPHIC"},
        {"effective_strategy": "BROLL"}
    ]
    status, findings = engine.evaluate(**_minimal_baseline(scenes_data=scenes))
    assert not any(f.rule_code == ProductionQARuleCode.EXCESSIVE_STATIC_SCENES for f in findings)


def test_missing_intro_positive():
    engine = ProductionQAEngine()
    script = {
        "id": "test_script_id",
        "hook_text": "",
        "sections": [{"heading": "Body 1"}]
    }
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert any(f.rule_code == ProductionQARuleCode.MISSING_INTRO for f in findings)


def test_missing_intro_negative_hook():
    engine = ProductionQAEngine()
    script = {
        "id": "test_script_id",
        "hook_text": "Hey guys",
        "sections": [{"heading": "Body 1"}]
    }
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert not any(f.rule_code == ProductionQARuleCode.MISSING_INTRO for f in findings)


def test_missing_intro_negative_heading():
    engine = ProductionQAEngine()
    script = {
        "id": "test_script_id",
        "hook_text": "",
        "sections": [{"heading": "The setup of the system"}]
    }
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert not any(f.rule_code == ProductionQARuleCode.MISSING_INTRO for f in findings)


def test_missing_outro_positive():
    engine = ProductionQAEngine()
    script = {
        "id": "test_script_id",
        "cta_text": "",
        "sections": [{"heading": "Body 1"}]
    }
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert any(f.rule_code == ProductionQARuleCode.MISSING_OUTRO for f in findings)


def test_missing_outro_negative_cta():
    engine = ProductionQAEngine()
    script = {
        "id": "test_script_id",
        "cta_text": "Subscribe",
        "sections": [{"heading": "Body 1"}]
    }
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert not any(f.rule_code == ProductionQARuleCode.MISSING_OUTRO for f in findings)


def test_missing_outro_negative_heading():
    engine = ProductionQAEngine()
    script = {
        "id": "test_script_id",
        "cta_text": "",
        "sections": [{"heading": "Final recap"}]
    }
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert not any(f.rule_code == ProductionQARuleCode.MISSING_OUTRO for f in findings)


def test_missing_script_context_skips():
    engine = ProductionQAEngine()
    script = {"id": "test_script_id"}
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert not any(f.rule_code == ProductionQARuleCode.MISSING_INTRO for f in findings)
    assert not any(f.rule_code == ProductionQARuleCode.MISSING_OUTRO for f in findings)


def test_missing_scene_context_skips():
    engine = ProductionQAEngine()

    status_none, findings_none = engine.evaluate(**_minimal_baseline(scenes_data=None))
    assert not any(f.rule_code == ProductionQARuleCode.VISUAL_REPETITION for f in findings_none)
    assert not any(f.rule_code == ProductionQARuleCode.EXCESSIVE_STATIC_SCENES for f in findings_none)

    status_empty, findings_empty = engine.evaluate(**_minimal_baseline(scenes_data=[]))
    assert not any(f.rule_code == ProductionQARuleCode.VISUAL_REPETITION for f in findings_empty)
    assert not any(f.rule_code == ProductionQARuleCode.EXCESSIVE_STATIC_SCENES for f in findings_empty)


def test_all_four_findings_are_warning_and_passed_with_warnings():
    engine = ProductionQAEngine()
    script = {
        "id": "test_script_id",
        "hook_text": "",
        "cta_text": "",
        "sections": [{"heading": "Body 1"}]
    }
    scenes = [
        {"effective_strategy": "DIAGRAM"},
        {"effective_strategy": "DIAGRAM"},
        {"effective_strategy": "DIAGRAM"}
    ]
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script, scenes_data=scenes))

    codes = {f.rule_code for f in findings}
    assert ProductionQARuleCode.VISUAL_REPETITION in codes
    assert ProductionQARuleCode.EXCESSIVE_STATIC_SCENES in codes
    assert ProductionQARuleCode.MISSING_INTRO in codes
    assert ProductionQARuleCode.MISSING_OUTRO in codes

    # Assert they are WARNING
    for f in findings:
        if f.rule_code in {
            ProductionQARuleCode.VISUAL_REPETITION,
            ProductionQARuleCode.EXCESSIVE_STATIC_SCENES,
            ProductionQARuleCode.MISSING_INTRO,
            ProductionQARuleCode.MISSING_OUTRO
        }:
            assert f.severity == ProductionQASeverity.WARNING

    # With no blocking findings, status is PASSED_WITH_WARNINGS
    assert status == ProductionQAStatus.PASSED_WITH_WARNINGS


def test_guardian_mapping():
    adapter = ProductionQAAdapter()
    script = {
        "id": "test_script_id",
        "hook_text": "",
        "cta_text": "",
        "sections": [{"heading": "Body 1"}]
    }
    scenes = [
        {"effective_strategy": "DIAGRAM"},
        {"effective_strategy": "DIAGRAM"},
        {"effective_strategy": "DIAGRAM"}
    ]
    guardian_findings = adapter.evaluate(**_minimal_baseline(script_data=script, scenes_data=scenes))

    mapped_rules = {f.rule_id for f in guardian_findings}
    assert ProductionQARuleCode.VISUAL_REPETITION.value in mapped_rules
    assert ProductionQARuleCode.EXCESSIVE_STATIC_SCENES.value in mapped_rules
    assert ProductionQARuleCode.MISSING_INTRO.value in mapped_rules
    assert ProductionQARuleCode.MISSING_OUTRO.value in mapped_rules

    for f in guardian_findings:
        if f.rule_id in {
            ProductionQARuleCode.VISUAL_REPETITION.value,
            ProductionQARuleCode.EXCESSIVE_STATIC_SCENES.value,
            ProductionQARuleCode.MISSING_INTRO.value,
            ProductionQARuleCode.MISSING_OUTRO.value
        }:
            assert f.severity == GuardianSeverity.LOW
            assert f.risk_type == GuardianRiskType.CONTENT_QUALITY
            assert f.confidence == 0.8


def test_guardian_mapped_count():
    assert len(PROD_RULE_SEVERITY_RISK_MAP) == 28


def test_visual_repetition_empty_strategy_breaks_chain():
    engine = ProductionQAEngine()
    scenes = [
        {"effective_strategy": "BROLL"},
        {"effective_strategy": ""},
        {"effective_strategy": "BROLL"},
        {"effective_strategy": "BROLL"}
    ]
    status, findings = engine.evaluate(**_minimal_baseline(scenes_data=scenes))
    assert not any(f.rule_code == ProductionQARuleCode.VISUAL_REPETITION for f in findings)


def test_visual_repetition_none_strategy_breaks_chain():
    engine = ProductionQAEngine()
    scenes = [
        {"effective_strategy": "BROLL"},
        {"effective_strategy": None},
        {"effective_strategy": "BROLL"},
        {"effective_strategy": "BROLL"}
    ]
    status, findings = engine.evaluate(**_minimal_baseline(scenes_data=scenes))
    assert not any(f.rule_code == ProductionQARuleCode.VISUAL_REPETITION for f in findings)


def test_missing_intro_none_hook_is_missing():
    engine = ProductionQAEngine()
    script = {
        "id": "...",
        "hook_text": None,
        "sections": [{"heading": "Body"}]
    }
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert any(f.rule_code == ProductionQARuleCode.MISSING_INTRO for f in findings)


def test_missing_outro_none_cta_is_missing():
    engine = ProductionQAEngine()
    script = {
        "id": "...",
        "cta_text": None,
        "sections": [{"heading": "Body"}]
    }
    status, findings = engine.evaluate(**_minimal_baseline(script_data=script))
    assert any(f.rule_code == ProductionQARuleCode.MISSING_OUTRO for f in findings)
