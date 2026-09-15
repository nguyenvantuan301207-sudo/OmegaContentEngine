from types import SimpleNamespace

import pytest

from omega.application.production_qa import ProductionQAEngine
from omega.domain.production import ProductionQARuleCode


def _subtitle_truth(mode: str, *, rendered: bool):
    return SimpleNamespace(
        effective_mode=mode,
        burn_applied=rendered,
        cues=(object(),) if rendered else (),
        artifacts=(object(),) if rendered else (),
    )


def _scene(scene_index: int = 1):
    return SimpleNamespace(
        sequence_index=scene_index,
        duration_ms=1000,
        scene_content_sha256="a" * 64,
        template_id="kinetic_text",
    )


def _visual(origin: str, scene_index: int = 1):
    if origin == "TEMPLATE":
        return SimpleNamespace(
            scene_index=scene_index,
            origin=origin,
            template_id="kinetic_text",
            provider=None,
        )
    return SimpleNamespace(
        scene_index=scene_index,
        origin=origin,
        template_id=None,
        provider="pexels",
        provider_asset_id="asset-1",
    )


def _snapshot(*, subtitles, scenes=None, visuals=None):
    return SimpleNamespace(
        subtitles=subtitles,
        scenes=tuple(scenes if scenes is not None else [_scene()]),
        visuals=tuple(visuals if visuals is not None else [_visual("TEMPLATE")]),
    )


def _evaluate(*, snapshot, requirements=None, assets=None, subtitle_cues=None):
    _, findings = ProductionQAEngine().evaluate(
        request_data={
            "script_version_id": "script",
            "channel_dna_revision_id": "dna",
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        script_version_data={
            "id": "script",
            "hook_text": "Hook",
            "cta_text": "CTA",
            "sections": [],
        },
        content_request_data={"channel_dna_revision_id": "dna"},
        assets_data=list(assets or []),
        requirements_data=list(requirements or []),
        narration_segments=[{"start_ms": 0, "end_ms": 1000}],
        subtitle_cues=list(subtitle_cues or []),
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


@pytest.mark.parametrize("mode", ["OFF", "STANDARD", "KARAOKE"])
def test_runtime_subtitle_evidence_does_not_require_prepared_srt(mode):
    rendered = mode != "OFF"
    rules = _evaluate(
        snapshot=_snapshot(
            subtitles=_subtitle_truth(mode, rendered=rendered),
        ),
        subtitle_cues=(
            [{"cue_order": 1, "start_ms": 0, "end_ms": 900, "text": "Caption"}]
            if rendered
            else []
        ),
    )

    assert ProductionQARuleCode.MISSING_SUBTITLE_RENDER not in rules


@pytest.mark.parametrize("mode", ["STANDARD", "KARAOKE"])
def test_runtime_missing_subtitle_evidence_blocks_despite_prepared_srt(mode):
    rules = _evaluate(
        snapshot=_snapshot(
            subtitles=_subtitle_truth(mode, rendered=False),
        ),
        assets=[{"asset_type": "SUBTITLE", "mime_type": "application/x-subrip"}],
        subtitle_cues=[
            {"cue_order": 1, "start_ms": 0, "end_ms": 900, "text": "Prepared"}
        ],
    )

    assert ProductionQARuleCode.MISSING_SUBTITLE_RENDER in rules


@pytest.mark.parametrize("origin", ["TEMPLATE", "PROVIDER"])
def test_runtime_visual_satisfies_same_scene_requirement_without_prepared_asset(origin):
    rules = _evaluate(
        snapshot=_snapshot(
            subtitles=_subtitle_truth("OFF", rendered=False),
            visuals=[_visual(origin)],
        ),
        requirements=[
            {"id": "requirement-1", "scene_index": 1, "required": True}
        ],
    )

    assert ProductionQARuleCode.MISSING_REQUIRED_ASSET not in rules
    assert ProductionQARuleCode.NO_CONTENTFUL_VISUAL_ASSET not in rules


def test_runtime_visual_does_not_satisfy_different_scene_requirement():
    rules = _evaluate(
        snapshot=_snapshot(
            subtitles=_subtitle_truth("OFF", rendered=False),
        ),
        requirements=[
            {"id": "requirement-2", "scene_index": 2, "required": True}
        ],
    )

    assert ProductionQARuleCode.MISSING_REQUIRED_ASSET in rules


def test_runtime_missing_visual_blocks_despite_prepared_visual():
    rules = _evaluate(
        snapshot=_snapshot(
            subtitles=_subtitle_truth("OFF", rendered=False),
            scenes=[],
            visuals=[],
        ),
        requirements=[
            {"id": "requirement-1", "scene_index": 1, "required": True}
        ],
        assets=[
            {
                "id": "prepared-image",
                "asset_type": "IMAGE",
                "provider_type": "PEXELS",
                "asset_requirement_id": "requirement-1",
            }
        ],
    )

    assert ProductionQARuleCode.MISSING_REQUIRED_ASSET in rules
    assert ProductionQARuleCode.NO_CONTENTFUL_VISUAL_ASSET in rules


def test_legacy_prepared_evidence_behavior_remains_without_runtime_truth():
    rules = _evaluate(
        snapshot=None,
        requirements=[
            {"id": "requirement-1", "scene_index": 1, "required": True}
        ],
        assets=[
            {
                "id": "prepared-image",
                "asset_type": "IMAGE",
                "provider_type": "PEXELS",
                "asset_requirement_id": "requirement-1",
            }
        ],
    )

    assert ProductionQARuleCode.MISSING_REQUIRED_ASSET not in rules
    assert ProductionQARuleCode.NO_CONTENTFUL_VISUAL_ASSET not in rules
