from __future__ import annotations

from uuid import uuid4

import pytest

from omega.application.guardian.adapters.production_qa_adapter import (
    ProductionQAAdapter,
)
from omega.application.production_qa import ProductionQAEngine
from omega.application.production_runtime_truth import (
    ProductionRuntimeTruthSnapshot,
    RuntimeBeatVisualTruth,
    _build_beat_attribution_obligations,
)
from omega.domain.guardian import GuardianRiskType, GuardianSeverity
from omega.domain.production import ProductionQARuleCode, ProductionQAStatus


def _template(index: int, start: int, end: int, *, template: str):
    return {
        "parent_scene_index": 1,
        "materialized_beat_index": index,
        "source_editorial_beat_index": index,
        "source_statement_references": (index + 1,),
        "semantic_role": "EVIDENCE",
        "start_offset_ms": start,
        "end_offset_ms": end,
        "duration_ms": end - start,
        "template_id": template,
        "camera_motion_intent": "STATIC",
        "transition_intent": "HARD_CUT",
        "asset_action": "LOCAL_TEMPLATE",
        "reuse_from_beat_index": None,
        "visual_origin": "TEMPLATE",
        "license_status": "GENERATED",
        "rendered_beat_clip_sha256": chr(ord("a") + index) * 64,
    }


def _provider(
    index: int,
    start: int,
    end: int,
    *,
    asset: str = "A",
    reuse: int | None = None,
    kind: str = "BROLL",
    license_status: str = "LICENSED",
    attribution: str | None = None,
):
    return {
        "parent_scene_index": 1,
        "materialized_beat_index": index,
        "source_editorial_beat_index": index,
        "source_statement_references": (index + 1,),
        "semantic_role": "EVIDENCE",
        "start_offset_ms": start,
        "end_offset_ms": end,
        "duration_ms": end - start,
        "template_id": "BROLL_EXPLAINER" if kind == "BROLL" else "IMAGE_EXPLAINER",
        "camera_motion_intent": "DRIFT" if reuse is not None else "SLOW_PUSH_IN",
        "transition_intent": "HARD_CUT",
        "asset_action": "REUSE_COMPATIBLE" if reuse is not None else "ACQUIRE_IF_NEEDED",
        "reuse_from_beat_index": reuse,
        "visual_origin": "PROVIDER",
        "asset_kind": kind,
        "query": f"asset {asset}",
        "provider": "FAKE",
        "provider_asset_id": asset,
        "source_url": f"https://cdn.example/{asset}",
        "source_page_url": f"https://example/{asset}",
        "license_status": license_status,
        "license_name": "Fake License",
        "license_url": "https://example/license",
        "attribution": attribution,
        "allowed_attribution_channels": (
            ("PUBLISH_METADATA",) if attribution else ()
        ),
        "provider_metadata": {"asset": asset},
        "provider_asset_content_sha256": ("9" if asset == "A" else "8") * 64,
        "rendered_beat_clip_sha256": chr(ord("a") + index) * 64,
    }


def _snapshot(beats: list[dict]) -> ProductionRuntimeTruthSnapshot:
    artifact_id = uuid4()
    artifact_sha = "f" * 64
    beat_models = tuple(RuntimeBeatVisualTruth.model_validate(beat) for beat in beats)
    obligations = _build_beat_attribution_obligations(
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha,
        visual_beats=beat_models,
    )
    return ProductionRuntimeTruthSnapshot.model_validate(
        {
            "schema_version": 4,
            "lineage": {
                "channel_id": str(uuid4()),
                "production_request_id": str(uuid4()),
                "content_request_id": str(uuid4()),
                "script_version_id": str(uuid4()),
                "channel_dna_revision_id": str(uuid4()),
                "render_plan_id": str(uuid4()),
                "render_job_id": str(uuid4()),
                "media_artifact_id": str(artifact_id),
                "artifact_version": 1,
            },
            "scenes": [
                {
                    "sequence_index": 1,
                    "original_strategy": "BROLL",
                    "effective_strategy": "BROLL",
                    "template_id": None,
                    "start_ms": 0,
                    "end_ms": 1000,
                    "duration_ms": 1000,
                    "scene_content_sha256": "e" * 64,
                }
            ],
            "narration": [],
            "subtitles": {
                "requested_mode": "OFF",
                "effective_mode": "OFF",
                "fallback_applied": False,
                "timing_source": "NONE",
                "semantics_version": 3,
                "burn_applied": False,
            },
            "visual_beats": beats,
            "branding": {"policy_source": "test", "assets": []},
            "audio_mix": {},
            "render_target": {
                "duration_ms": 1000,
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
                "has_audio": False,
                "container": "mp4",
                "file_size_bytes": 10,
                "content_sha256": artifact_sha,
            },
            "probe": {},
            "artifact": {"artifact_id": str(artifact_id)},
            "fingerprints": {
                "canonical_contract": "c" * 64,
                "manifest_run": "d" * 64,
                "subtitle_semantics_version": 3,
            },
            "attribution_obligations": [
                obligation.model_dump(mode="json") for obligation in obligations
            ],
            "attribution_delivery_state": "UNKNOWN",
        }
    )


def _inputs(snapshot: ProductionRuntimeTruthSnapshot) -> dict:
    return {
        "request_data": {
            "id": "request-1",
            "script_version_id": "script",
            "channel_dna_revision_id": "dna",
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        "script_version_data": {"id": "script"},
        "content_request_data": {"channel_dna_revision_id": "dna"},
        "assets_data": [
            {
                "id": "planned-but-not-used",
                "asset_type": "IMAGE",
                "asset_requirement_id": "requirement-1",
                "license_status": "BLOCKED",
            }
        ],
        "requirements_data": [
            {"id": "requirement-1", "scene_index": 1, "required": True}
        ],
        "narration_segments": [{"start_ms": 0, "end_ms": 1000}],
        "subtitle_cues": [],
        "media_probe_summary": {
            "duration_ms": 1000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
        },
        "artifact_file_path": None,
        "expected_hash": None,
        "scenes_data": None,
        "runtime_truth_snapshot": snapshot,
    }


def _qa(snapshot: ProductionRuntimeTruthSnapshot):
    return ProductionQAEngine().evaluate(**_inputs(snapshot))


def _rule_findings(findings, rule: ProductionQARuleCode):
    return [finding for finding in findings if finding.rule_code == rule]


def _mixed_valid_beats():
    return [
        _provider(0, 0, 300, attribution="Creator A"),
        _template(1, 300, 700, template="FLOW_DIAGRAM"),
        _provider(2, 700, 1000, reuse=0, attribution="Creator A"),
    ]


def test_local_schema4_positive_canary_and_guardian_use_physical_beats():
    snapshot = _snapshot(_mixed_valid_beats())
    status, findings = _qa(snapshot)
    rules = {finding.rule_code for finding in findings}
    guardian = ProductionQAAdapter().evaluate(**_inputs(snapshot))
    guardian_rules = {finding.rule_id for finding in guardian}

    assert snapshot.schema_version == 4
    assert status == ProductionQAStatus.PASSED
    assert ProductionQARuleCode.MISSING_REQUIRED_ASSET not in rules
    assert ProductionQARuleCode.NO_CONTENTFUL_VISUAL_ASSET not in rules
    assert ProductionQARuleCode.BLOCKED_ASSET_RIGHTS not in rules
    assert ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS not in rules
    assert ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION not in rules
    assert guardian_rules == {rule.value for rule in rules}


@pytest.mark.parametrize(
    "beats",
    [
        [
            _template(0, 0, 300, template="FLOW_DIAGRAM"),
            _template(1, 300, 700, template="STATISTIC_HERO"),
            _template(2, 700, 1000, template="FLOW_DIAGRAM"),
        ],
        [
            _provider(0, 0, 300),
            _provider(1, 300, 700, reuse=0),
            _provider(2, 700, 1000, reuse=0),
        ],
        _mixed_valid_beats(),
        [
            _provider(0, 0, 300, asset="A"),
            _provider(1, 300, 700, asset="B", kind="IMAGE"),
            _provider(2, 700, 1000, asset="A", reuse=0),
        ],
    ],
    ids=("templates", "provider-reuse", "mixed", "two-provider-assets"),
)
def test_multi_beat_matrix_is_one_contentful_parent_scene(beats):
    _status, findings = _qa(_snapshot(beats))
    rules = {finding.rule_code for finding in findings}

    assert ProductionQARuleCode.MISSING_REQUIRED_ASSET not in rules
    assert ProductionQARuleCode.NO_CONTENTFUL_VISUAL_ASSET not in rules
    assert ProductionQARuleCode.BLOCKED_ASSET_RIGHTS not in rules
    assert ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS not in rules


@pytest.mark.parametrize(
    ("license_status", "attribution", "expected_rule"),
    [
        ("BLOCKED", None, ProductionQARuleCode.BLOCKED_ASSET_RIGHTS),
        ("UNKNOWN", None, ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS),
        (
            "ATTRIBUTION_REQUIRED",
            None,
            ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION,
        ),
    ],
)
def test_negative_schema4_rights_canaries(
    license_status, attribution, expected_rule
):
    beats = [
        _provider(
            0,
            0,
            500,
            license_status=license_status,
            attribution=attribution,
        ),
        _provider(
            1,
            500,
            1000,
            reuse=0,
            license_status=license_status,
            attribution=attribution,
        ),
    ]
    status, findings = _qa(_snapshot(beats))

    assert status == ProductionQAStatus.BLOCKED
    assert len(_rule_findings(findings, expected_rule)) == 1


def test_valid_required_attribution_and_template_do_not_emit_provider_findings():
    attributed = _snapshot(
        [
            _provider(
                0,
                0,
                1000,
                license_status="ATTRIBUTION_REQUIRED",
                attribution="Creator A",
            )
        ]
    )
    template_data = _template(0, 0, 1000, template="FLOW_DIAGRAM")
    template_data["license_status"] = "BLOCKED"
    template_only = _snapshot([template_data])

    for snapshot in (attributed, template_only):
        _status, findings = _qa(snapshot)
        rules = {finding.rule_code for finding in findings}
        assert ProductionQARuleCode.BLOCKED_ASSET_RIGHTS not in rules
        assert ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS not in rules
        assert ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION not in rules


def test_provider_reuse_missing_attribution_emits_one_finding():
    beats = [
        _provider(0, 0, 500, license_status="ATTRIBUTION_REQUIRED"),
        _provider(
            1,
            500,
            1000,
            reuse=0,
            license_status="ATTRIBUTION_REQUIRED",
        ),
    ]
    _status, findings = _qa(_snapshot(beats))

    missing = _rule_findings(
        findings, ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION
    )
    assert len(missing) == 1
    assert " A " in f" {missing[0].message} "


def test_later_reused_beat_block_is_not_hidden_by_identity_deduplication():
    beats = [
        _provider(0, 0, 500, license_status="LICENSED"),
        _provider(1, 500, 1000, reuse=0, license_status="BLOCKED"),
    ]
    _status, findings = _qa(_snapshot(beats))

    blocked = _rule_findings(findings, ProductionQARuleCode.BLOCKED_ASSET_RIGHTS)
    assert len(blocked) == 1
    assert blocked[0].message == "Rendered visual A has BLOCKED license status."


def test_distinct_provider_assets_are_not_deduplicated():
    beats = [
        _provider(0, 0, 500, asset="A", license_status="BLOCKED", attribution="Shared"),
        _provider(
            1,
            500,
            1000,
            asset="B",
            kind="IMAGE",
            license_status="BLOCKED",
            attribution="Shared",
        ),
    ]
    _status, findings = _qa(_snapshot(beats))

    blocked = _rule_findings(findings, ProductionQARuleCode.BLOCKED_ASSET_RIGHTS)
    assert len(blocked) == 2
    assert {finding.message for finding in blocked} == {
        "Rendered visual A has BLOCKED license status.",
        "Rendered visual B has BLOCKED license status.",
    }


@pytest.mark.parametrize(
    ("rule", "severity", "risk", "confidence"),
    [
        (
            ProductionQARuleCode.BLOCKED_ASSET_RIGHTS,
            GuardianSeverity.CRITICAL,
            GuardianRiskType.COPYRIGHT_LICENSE,
            1.0,
        ),
        (
            ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS,
            GuardianSeverity.HIGH,
            GuardianRiskType.COPYRIGHT_LICENSE,
            0.95,
        ),
        (
            ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION,
            GuardianSeverity.HIGH,
            GuardianRiskType.COPYRIGHT_LICENSE,
            0.95,
        ),
    ],
)
def test_guardian_propagates_runtime_v4_rights_findings(
    rule, severity, risk, confidence
):
    status_by_rule = {
        ProductionQARuleCode.BLOCKED_ASSET_RIGHTS: "BLOCKED",
        ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS: "UNKNOWN",
        ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION: (
            "ATTRIBUTION_REQUIRED"
        ),
    }
    snapshot = _snapshot(
        [_provider(0, 0, 1000, license_status=status_by_rule[rule])]
    )
    matches = [
        finding
        for finding in ProductionQAAdapter().evaluate(**_inputs(snapshot))
        if finding.rule_id == rule.value
    ]

    assert len(matches) == 1
    assert matches[0].severity == severity
    assert matches[0].risk_type == risk
    assert matches[0].confidence == confidence


def test_runtime_truth_overrides_conflicting_planned_assets_data():
    snapshot = _snapshot(_mixed_valid_beats())
    inputs = _inputs(snapshot)
    inputs["assets_data"][0]["license_status"] = "BLOCKED"

    _status, findings = ProductionQAEngine().evaluate(**inputs)

    assert not _rule_findings(findings, ProductionQARuleCode.BLOCKED_ASSET_RIGHTS)
