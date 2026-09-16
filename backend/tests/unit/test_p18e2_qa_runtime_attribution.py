from types import SimpleNamespace

import pytest

from omega.application.production_qa import ProductionQAEngine
from omega.domain.production import (
    LicenseStatus,
    ProductionQARuleCode,
    ProductionQASeverity,
    ProductionQAStatus,
)

ATTRIBUTION_RULE = ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION


def _snapshot(
    license_status: LicenseStatus,
    *,
    attribution: str | None,
    scene_index: int = 1,
):
    return SimpleNamespace(
        subtitles=SimpleNamespace(
            effective_mode="OFF",
            burn_applied=False,
            cues=(),
            artifacts=(),
        ),
        scenes=(
            SimpleNamespace(
                sequence_index=scene_index,
                duration_ms=1000,
                scene_content_sha256="a" * 64,
                template_id="kinetic_text",
            ),
        ),
        visuals=(
            SimpleNamespace(
                scene_index=scene_index,
                origin="PROVIDER",
                template_id=None,
                provider="test-provider",
                provider_asset_id="asset-1",
                content_sha256="b" * 64,
                license_status=license_status,
                attribution=attribution,
            ),
        ),
    )


def _evaluate(
    *,
    snapshot,
    prepared_status: LicenseStatus = LicenseStatus.LICENSED,
    prepared_attribution: str | None = None,
):
    status, findings = ProductionQAEngine().evaluate(
        request_data={
            "script_version_id": "script",
            "channel_dna_revision_id": "dna",
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        script_version_data={"id": "script"},
        content_request_data={"channel_dna_revision_id": "dna"},
        assets_data=[
            {
                "id": "prepared-visual",
                "asset_type": "IMAGE",
                "provider_type": "SYSTEM",
                "source_ref": "prepared-only",
                "asset_requirement_id": "requirement-1",
                "license_status": prepared_status.value,
                "attribution": prepared_attribution,
            }
        ],
        requirements_data=[
            {
                "id": "requirement-1",
                "scene_index": 1,
                "required": True,
            }
        ],
        narration_segments=[{"start_ms": 0, "end_ms": 1000}],
        subtitle_cues=[],
        media_probe_summary={
            "duration_ms": 1000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
        },
        artifact_file_path=None,
        expected_hash=None,
        scenes_data=[],
        runtime_truth_snapshot=snapshot,
    )
    return status, findings


def _rules(findings):
    return {finding.rule_code for finding in findings}


def test_attribution_required_with_valid_runtime_attribution_passes_rule():
    _status, findings = _evaluate(
        snapshot=_snapshot(
            LicenseStatus.ATTRIBUTION_REQUIRED,
            attribution="Photo by Example",
        )
    )

    assert ATTRIBUTION_RULE not in _rules(findings)


@pytest.mark.parametrize("attribution", [None, "", "   "])
def test_attribution_required_with_missing_or_blank_runtime_attribution_blocks(
    attribution,
):
    status, findings = _evaluate(
        snapshot=_snapshot(
            LicenseStatus.ATTRIBUTION_REQUIRED,
            attribution=attribution,
        )
    )
    attribution_findings = [
        finding for finding in findings if finding.rule_code == ATTRIBUTION_RULE
    ]

    assert status == ProductionQAStatus.BLOCKED
    assert len(attribution_findings) == 1
    assert attribution_findings[0].severity == ProductionQASeverity.BLOCKING


@pytest.mark.parametrize(
    "license_status",
    [
        LicenseStatus.LICENSED,
        LicenseStatus.GENERATED,
        LicenseStatus.OWNED,
        LicenseStatus.PUBLIC_DOMAIN,
    ],
)
def test_non_attribution_status_without_attribution_does_not_emit_finding(
    license_status,
):
    _status, findings = _evaluate(
        snapshot=_snapshot(license_status, attribution=None)
    )

    assert ATTRIBUTION_RULE not in _rules(findings)


def test_runtime_missing_attribution_overrides_prepared_valid_attribution():
    _status, findings = _evaluate(
        snapshot=_snapshot(
            LicenseStatus.ATTRIBUTION_REQUIRED,
            attribution=None,
        ),
        prepared_status=LicenseStatus.ATTRIBUTION_REQUIRED,
        prepared_attribution="Prepared attribution",
    )

    assert ATTRIBUTION_RULE in _rules(findings)


def test_runtime_valid_attribution_overrides_prepared_missing_attribution():
    _status, findings = _evaluate(
        snapshot=_snapshot(
            LicenseStatus.ATTRIBUTION_REQUIRED,
            attribution="Runtime attribution",
        ),
        prepared_status=LicenseStatus.ATTRIBUTION_REQUIRED,
        prepared_attribution=None,
    )

    assert ATTRIBUTION_RULE not in _rules(findings)


def test_existing_blocked_runtime_rights_behavior_is_unchanged():
    _status, findings = _evaluate(
        snapshot=_snapshot(LicenseStatus.BLOCKED, attribution=None)
    )

    assert ProductionQARuleCode.BLOCKED_ASSET_RIGHTS in _rules(findings)
    assert ATTRIBUTION_RULE not in _rules(findings)


def test_existing_required_unknown_runtime_rights_behavior_is_unchanged():
    _status, findings = _evaluate(
        snapshot=_snapshot(LicenseStatus.UNKNOWN, attribution=None)
    )

    assert ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS in _rules(findings)
    assert ATTRIBUTION_RULE not in _rules(findings)


def test_legacy_no_runtime_path_does_not_invent_attribution_policy():
    _status, findings = _evaluate(
        snapshot=None,
        prepared_status=LicenseStatus.ATTRIBUTION_REQUIRED,
        prepared_attribution=None,
    )

    assert ATTRIBUTION_RULE not in _rules(findings)


def test_attribution_finding_is_deterministic_and_identifies_runtime_visual():
    snapshot = _snapshot(
        LicenseStatus.ATTRIBUTION_REQUIRED,
        attribution=" ",
    )
    first_status, first_findings = _evaluate(snapshot=snapshot)
    second_status, second_findings = _evaluate(snapshot=snapshot)
    first = [finding for finding in first_findings if finding.rule_code == ATTRIBUTION_RULE]
    second = [finding for finding in second_findings if finding.rule_code == ATTRIBUTION_RULE]

    assert first_status == second_status == ProductionQAStatus.BLOCKED
    assert [finding.model_dump() for finding in first] == [
        finding.model_dump() for finding in second
    ]
    assert first[0].message == (
        "Rendered visual asset-1 requires attribution but runtime attribution is "
        "missing or blank."
    )
