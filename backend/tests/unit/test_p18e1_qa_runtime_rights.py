from types import SimpleNamespace

import pytest

from omega.application.production_qa import ProductionQAEngine
from omega.domain.production import LicenseStatus, ProductionQARuleCode


def _snapshot(
    license_status: LicenseStatus,
    *,
    scene_index: int = 1,
    origin: str = "PROVIDER",
):
    scene = SimpleNamespace(
        sequence_index=scene_index,
        duration_ms=1000,
        scene_content_sha256="a" * 64,
        template_id="kinetic_text",
    )
    visual = SimpleNamespace(
        scene_index=scene_index,
        origin=origin,
        template_id="kinetic_text" if origin == "TEMPLATE" else None,
        provider="pexels" if origin == "PROVIDER" else None,
        provider_asset_id="asset-1" if origin == "PROVIDER" else None,
        content_sha256="b" * 64,
        license_status=license_status,
    )
    return SimpleNamespace(
        subtitles=SimpleNamespace(
            effective_mode="OFF",
            burn_applied=False,
            cues=(),
            artifacts=(),
        ),
        scenes=(scene,),
        visuals=(visual,),
    )


def _evaluate(*, snapshot, prepared_status, required_scene_index=1):
    _, findings = ProductionQAEngine().evaluate(
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
                "provider_type": "PEXELS",
                "source_ref": "prepared-only",
                "asset_requirement_id": "requirement-1",
                "license_status": prepared_status.value,
            }
        ],
        requirements_data=[
            {
                "id": "requirement-1",
                "scene_index": required_scene_index,
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
    return {finding.rule_code for finding in findings}


def test_runtime_blocked_overrides_prepared_valid():
    rules = _evaluate(
        snapshot=_snapshot(LicenseStatus.BLOCKED),
        prepared_status=LicenseStatus.LICENSED,
    )

    assert ProductionQARuleCode.BLOCKED_ASSET_RIGHTS in rules


@pytest.mark.parametrize("runtime_status", [LicenseStatus.LICENSED, LicenseStatus.GENERATED])
def test_runtime_valid_overrides_prepared_blocked(runtime_status):
    rules = _evaluate(
        snapshot=_snapshot(
            runtime_status,
            origin="TEMPLATE" if runtime_status == LicenseStatus.GENERATED else "PROVIDER",
        ),
        prepared_status=LicenseStatus.BLOCKED,
    )

    assert ProductionQARuleCode.BLOCKED_ASSET_RIGHTS not in rules


def test_runtime_unknown_required_overrides_prepared_valid():
    rules = _evaluate(
        snapshot=_snapshot(LicenseStatus.UNKNOWN),
        prepared_status=LicenseStatus.LICENSED,
    )

    assert ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS in rules


def test_runtime_valid_overrides_prepared_unknown():
    rules = _evaluate(
        snapshot=_snapshot(LicenseStatus.LICENSED),
        prepared_status=LicenseStatus.UNKNOWN,
    )

    assert ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS not in rules


def test_runtime_unknown_unrelated_scene_is_not_treated_as_required():
    rules = _evaluate(
        snapshot=_snapshot(LicenseStatus.UNKNOWN, scene_index=1),
        prepared_status=LicenseStatus.LICENSED,
        required_scene_index=2,
    )

    assert ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS not in rules


@pytest.mark.parametrize(
    ("prepared_status", "expected_rule"),
    [
        (LicenseStatus.BLOCKED, ProductionQARuleCode.BLOCKED_ASSET_RIGHTS),
        (LicenseStatus.UNKNOWN, ProductionQARuleCode.UNKNOWN_REQUIRED_ASSET_RIGHTS),
    ],
)
def test_legacy_prepared_rights_behavior_remains_without_runtime(
    prepared_status,
    expected_rule,
):
    rules = _evaluate(snapshot=None, prepared_status=prepared_status)

    assert expected_rule in rules


def test_rights_outcome_is_snapshot_scoped_and_repeatable():
    blocked = _evaluate(
        snapshot=_snapshot(LicenseStatus.BLOCKED),
        prepared_status=LicenseStatus.LICENSED,
    )
    valid = _evaluate(
        snapshot=_snapshot(LicenseStatus.LICENSED),
        prepared_status=LicenseStatus.BLOCKED,
    )
    cached_equivalent = _evaluate(
        snapshot=_snapshot(LicenseStatus.LICENSED),
        prepared_status=LicenseStatus.BLOCKED,
    )

    assert ProductionQARuleCode.BLOCKED_ASSET_RIGHTS in blocked
    assert ProductionQARuleCode.BLOCKED_ASSET_RIGHTS not in valid
    assert valid == cached_equivalent
