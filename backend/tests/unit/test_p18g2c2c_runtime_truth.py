from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from omega.application.production_runtime_truth import (
    ProductionRuntimeTruthSnapshot,
    RuntimeBeatVisualTruth,
    _build_beat_attribution_obligations,
    read_attribution_foundation,
)
from omega.domain.attribution_delivery import AttributionDeliveryState


def _beat(index: int, start: int, end: int, **updates):
    data = {
        "parent_scene_index": 1,
        "materialized_beat_index": index,
        "source_editorial_beat_index": index,
        "source_statement_references": (index + 1,),
        "semantic_role": "EVIDENCE",
        "start_offset_ms": start,
        "end_offset_ms": end,
        "duration_ms": end - start,
        "template_id": "FLOW_DIAGRAM",
        "camera_motion_intent": "STATIC",
        "transition_intent": "HARD_CUT",
        "asset_action": "LOCAL_TEMPLATE",
        "reuse_from_beat_index": None,
        "visual_origin": "TEMPLATE",
        "license_status": "GENERATED",
        "rendered_beat_clip_sha256": chr(ord("a") + index) * 64,
    }
    data.update(updates)
    return data


def _payload(beats):
    artifact_id = uuid4()
    artifact_sha = "f" * 64
    models = tuple(RuntimeBeatVisualTruth.model_validate(item) for item in beats)
    obligations = _build_beat_attribution_obligations(
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha,
        visual_beats=models,
    )
    return {
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
        "scenes": [{
            "sequence_index": 1,
            "original_strategy": "DIAGRAM",
            "effective_strategy": "DIAGRAM",
            "template_id": None,
            "start_ms": 0,
            "end_ms": 1000,
            "duration_ms": 1000,
            "scene_content_sha256": "e" * 64,
        }],
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
        "attribution_obligations": [item.model_dump(mode="json") for item in obligations],
        "attribution_delivery_state": "UNKNOWN",
    }


def _provider(index, start, end, *, asset="A", reuse=None, template="BROLL_EXPLAINER"):
    return _beat(
        index,
        start,
        end,
        template_id=template,
        camera_motion_intent="DRIFT" if reuse is not None else "SLOW_PUSH_IN",
        asset_action="REUSE_COMPATIBLE" if reuse is not None else "ACQUIRE_IF_NEEDED",
        reuse_from_beat_index=reuse,
        visual_origin="PROVIDER",
        asset_kind="BROLL" if template == "BROLL_EXPLAINER" else "IMAGE",
        query=f"asset {asset}",
        provider="FAKE",
        provider_asset_id=asset,
        source_url=f"https://cdn.example/{asset}",
        source_page_url=f"https://example/{asset}",
        license_status="ATTRIBUTION_REQUIRED",
        license_name="Fake License",
        license_url="https://example/license",
        attribution="Shared Creator",
        allowed_attribution_channels=("PUBLISH_METADATA",),
        provider_metadata={"asset": asset},
        provider_asset_content_sha256=("9" if asset == "A" else "8") * 64,
    )


def test_schema_v4_accepts_mixed_three_beat_physical_truth_and_deduplicates_reuse():
    beats = [
        _provider(0, 0, 300),
        _beat(1, 300, 700, template_id="FLOW_DIAGRAM"),
        _provider(2, 700, 1000, reuse=0),
    ]
    snapshot = ProductionRuntimeTruthSnapshot.model_validate(_payload(beats))
    assert snapshot.schema_version == 4
    assert [beat.visual_origin for beat in snapshot.visual_beats] == [
        "PROVIDER", "TEMPLATE", "PROVIDER"
    ]
    assert [beat.template_id for beat in snapshot.visual_beats] == [
        "BROLL_EXPLAINER", "FLOW_DIAGRAM", "BROLL_EXPLAINER"
    ]
    assert len(snapshot.attribution_obligations) == 1
    assert snapshot.visual_beats[0].provider_asset_content_sha256 == snapshot.visual_beats[2].provider_asset_content_sha256
    assert snapshot.visual_beats[0].rendered_beat_clip_sha256 != snapshot.visual_beats[2].rendered_beat_clip_sha256


def test_two_unique_provider_assets_both_survive():
    beats = [
        _provider(0, 0, 300, asset="A"),
        _provider(1, 300, 700, asset="B", template="IMAGE_EXPLAINER"),
        _provider(2, 700, 1000, asset="A", reuse=0),
    ]
    snapshot = ProductionRuntimeTruthSnapshot.model_validate(_payload(beats))
    assert [beat.provider_asset_id for beat in snapshot.visual_beats] == ["A", "B", "A"]
    assert len(snapshot.attribution_obligations) == 2
    assert len({item.obligation_id for item in snapshot.attribution_obligations}) == 2


def test_case_a_three_local_templates_retain_every_real_template_id():
    beats = [
        _beat(0, 0, 300, template_id="FLOW_DIAGRAM"),
        _beat(1, 300, 700, template_id="STATISTIC_HERO"),
        _beat(2, 700, 1000, template_id="FLOW_DIAGRAM"),
    ]
    snapshot = ProductionRuntimeTruthSnapshot.model_validate(_payload(beats))
    assert [beat.template_id for beat in snapshot.visual_beats] == [
        "FLOW_DIAGRAM", "STATISTIC_HERO", "FLOW_DIAGRAM"
    ]
    assert snapshot.attribution_obligations == ()


def test_case_b_provider_acquire_and_two_reuses_retains_three_physical_beats():
    beats = [
        _provider(0, 0, 300),
        _provider(1, 300, 700, reuse=0),
        _provider(2, 700, 1000, reuse=0),
    ]
    snapshot = ProductionRuntimeTruthSnapshot.model_validate(_payload(beats))
    assert [beat.asset_action for beat in snapshot.visual_beats] == [
        "ACQUIRE_IF_NEEDED", "REUSE_COMPATIBLE", "REUSE_COMPATIBLE"
    ]
    assert len(snapshot.attribution_obligations) == 1


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda b: b.__setitem__(0, {**b[0], "start_offset_ms": 1, "duration_ms": 399}), "gapless"),
        (lambda b: b.__setitem__(1, {**b[1], "start_offset_ms": 399, "duration_ms": 601}), "gapless"),
        (lambda b: b.__setitem__(1, {**b[1], "end_offset_ms": 999, "duration_ms": 599}), "full parent"),
        (lambda b: b.__setitem__(1, {**b[1], "rendered_beat_clip_sha256": "BAD"}), "lowercase SHA-256"),
        (
            lambda b: b.__setitem__(
                1, {**b[1], "reuse_from_beat_index": 1}
            ),
            "preceding source beat",
        ),
        (lambda b: b.__setitem__(0, {**b[0], "materialized_beat_index": 1}), "contiguous"),
        (lambda b: b.__setitem__(1, {**b[1], "materialized_beat_index": 0}), "contiguous"),
        (lambda b: b.__setitem__(1, {**b[1], "parent_scene_index": 2}), "unknown scene"),
        (lambda b: b.__setitem__(1, {**b[1], "duration_ms": 0}), "greater than 0"),
        (lambda b: b.__setitem__(1, {**b[1], "duration_ms": -1}), "greater than 0"),
    ],
)
def test_schema_v4_rejects_incoherent_physical_beats(mutate, match):
    beats = [_beat(0, 0, 400), _beat(1, 400, 1000)]
    mutate(beats)
    with pytest.raises(ValidationError, match=match):
        ProductionRuntimeTruthSnapshot.model_validate(_payload(beats))


def test_schema_v4_rejects_provider_and_template_provenance_lies():
    with pytest.raises(ValidationError, match="provider identity"):
        RuntimeBeatVisualTruth.model_validate(
            _beat(0, 0, 1000, visual_origin="PROVIDER")
        )
    with pytest.raises(ValidationError, match="cannot claim provider provenance"):
        RuntimeBeatVisualTruth.model_validate(
            _beat(0, 0, 1000, provider="FAKE")
        )


def test_schema_v4_rejects_reuse_provenance_mismatch():
    beats = [_provider(0, 0, 500), _provider(1, 500, 1000, asset="B", reuse=0)]
    with pytest.raises(ValidationError, match="does not match source"):
        ProductionRuntimeTruthSnapshot.model_validate(_payload(beats))


def test_reuse_index_uses_source_editorial_namespace_not_materialized_namespace():
    beats = [
        _provider(0, 0, 500),
        _provider(1, 500, 1000, reuse=0),
    ]
    beats[0]["source_editorial_beat_index"] = 10
    beats[1]["source_editorial_beat_index"] = 20
    with pytest.raises(ValidationError, match="source index is incompatible"):
        ProductionRuntimeTruthSnapshot.model_validate(_payload(beats))

    beats[1]["reuse_from_beat_index"] = 10
    snapshot = ProductionRuntimeTruthSnapshot.model_validate(_payload(beats))
    assert snapshot.visual_beats[1].reuse_from_beat_index == 10


def test_legacy_single_scene_is_one_full_coverage_beat():
    snapshot = ProductionRuntimeTruthSnapshot.model_validate(
        _payload([_beat(0, 0, 1000)])
    )
    assert len(snapshot.visual_beats) == 1
    assert snapshot.visual_beats[0].duration_ms == snapshot.scenes[0].duration_ms


def test_schema_v4_has_one_visual_authority_and_requires_nonempty_beats():
    assert "visual_beats" in ProductionRuntimeTruthSnapshot.model_fields
    assert "visuals" not in ProductionRuntimeTruthSnapshot.model_fields

    with pytest.raises(ValidationError, match="one or more visual beats"):
        ProductionRuntimeTruthSnapshot.model_validate(_payload([]))

    duplicate_authority = _payload([_beat(0, 0, 1000)])
    duplicate_authority["visuals"] = []
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProductionRuntimeTruthSnapshot.model_validate(duplicate_authority)


def test_historical_schema3_attribution_foundation_remains_readable():
    payload = _payload([_beat(0, 0, 1000)])
    payload["schema_version"] = 3
    payload["visuals"] = [{
        "scene_index": 1,
        "origin": "TEMPLATE",
        "visual_mode": "LOCAL_TEMPLATE_ONLY",
        "template_id": "FLOW_DIAGRAM",
        "license_status": "GENERATED",
    }]
    payload["attribution_obligations"] = []
    obligations, state = read_attribution_foundation(payload)
    assert obligations == ()
    assert state == AttributionDeliveryState.UNKNOWN


def test_attribution_reader_rejects_boolean_schema_version():
    payload = _payload([_beat(0, 0, 1000)])
    payload["schema_version"] = True
    with pytest.raises(ValueError, match="malformed"):
        read_attribution_foundation(payload)


def test_schema4_attribution_reader_fails_closed_against_beat_truth():
    valid = _payload([_provider(0, 0, 500), _provider(1, 500, 1000, reuse=0)])
    obligations, state = read_attribution_foundation(valid)
    assert len(obligations) == 1
    assert state == AttributionDeliveryState.UNKNOWN

    missing = deepcopy(valid)
    missing["attribution_obligations"] = []
    with pytest.raises(ValueError, match="do not match"):
        read_attribution_foundation(missing)

    extra = deepcopy(valid)
    other = _payload([_provider(0, 0, 1000, asset="B")])
    extra["attribution_obligations"].append(other["attribution_obligations"][0])
    extra["attribution_obligations"].sort(key=lambda item: item["obligation_id"])
    with pytest.raises(ValueError):
        read_attribution_foundation(extra)

    altered_id = deepcopy(valid)
    altered_id["visual_beats"][0]["provider_asset_id"] = "fabricated"
    with pytest.raises(ValueError, match="do not match"):
        read_attribution_foundation(altered_id)

    altered_sha = deepcopy(valid)
    altered_sha["visual_beats"][0]["provider_asset_content_sha256"] = "7" * 64
    with pytest.raises(ValueError):
        read_attribution_foundation(altered_sha)

    two_assets = _payload(
        [_provider(0, 0, 500, asset="A"), _provider(1, 500, 1000, asset="B")]
    )
    two_obligations, _ = read_attribution_foundation(two_assets)
    assert len(two_obligations) == 2
