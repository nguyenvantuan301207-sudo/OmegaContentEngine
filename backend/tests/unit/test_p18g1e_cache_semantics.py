"""P18-G1E regression coverage for canonical physical render cache authority."""

import hashlib

import pytest

from omega.application.ffmpeg_renderer import FINAL_MASTER_SAMPLE_RATE_HZ
from omega.application.production_runtime_truth import RUNTIME_TRUTH_SCHEMA_VERSION
from omega.application.visual_production_v2_service import (
    CANONICAL_RENDER_SEMANTICS_VERSION,
    FINAL_MASTER_TARGET_I,
    FINAL_MASTER_TARGET_LRA,
    FINAL_MASTER_TARGET_TP,
    SUBTITLE_SEMANTICS_VERSION,
    _canonical_render_semantics_identity,
    _validate_cached_render_semantics,
)
from omega.domain.production import SubtitleFallbackPolicy, SubtitleMode


def _current_standard_manifest() -> dict:
    return {
        "runtime_truth_schema_version": RUNTIME_TRUTH_SCHEMA_VERSION,
        "subtitle_semantics_version": SUBTITLE_SEMANTICS_VERSION,
        "canonical_render_semantics_version": CANONICAL_RENDER_SEMANTICS_VERSION,
        "final_master_sample_rate_hz": FINAL_MASTER_SAMPLE_RATE_HZ,
        "requested_subtitle_mode": "STANDARD",
        "effective_subtitle_mode": "STANDARD",
        "subtitle_fallback_applied": False,
        "subtitle_fallback_reason": None,
        "subtitle_timing_source": "DERIVED_SEGMENT_TIMING",
        "subtitle_enabled": True,
        "karaoke_subtitles_enabled": False,
        "subtitle_mode": "sentence",
        "subtitle_mode_decision": {
            "requested_mode": "STANDARD",
            "effective_mode": "STANDARD",
            "fallback_applied": False,
            "fallback_reason": None,
            "timing_source": "DERIVED_SEGMENT_TIMING",
        },
    }


def _compatible(manifest: dict) -> bool:
    return _validate_cached_render_semantics(
        manifest=manifest,
        canonical_requested_mode=SubtitleMode.STANDARD,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
    )


def _fingerprint(base: str, **semantics: float | int) -> str:
    identity = _canonical_render_semantics_identity(**semantics)
    return hashlib.sha256(f"{base}:{identity}".encode()).hexdigest()


def test_legacy_pre_g1_manifest_is_not_current_cache_authority():
    legacy = _current_standard_manifest()
    legacy["subtitle_semantics_version"] = 2
    legacy.pop("canonical_render_semantics_version")

    assert not _compatible(legacy)


def test_current_render_and_subtitle_semantics_are_cache_compatible():
    assert SUBTITLE_SEMANTICS_VERSION == 3
    assert _compatible(_current_standard_manifest())


@pytest.mark.parametrize(
    ("render_version", "runtime_version", "compatible"),
    [
        pytest.param(6, 4, True, id="render6-runtime4"),
        pytest.param(5, 4, False, id="render5-runtime4"),
        pytest.param(4, 4, False, id="render4-runtime4"),
        pytest.param(3, 4, False, id="render3-runtime4"),
        pytest.param(2, 4, False, id="render2-runtime4"),
        pytest.param(6, 3, False, id="render6-runtime3"),
        pytest.param(5, 3, False, id="render5-runtime3"),
        pytest.param(4, 3, False, id="render4-runtime3"),
        pytest.param(3, 3, False, id="render3-runtime3"),
        pytest.param(2, 3, False, id="render2-runtime3"),
    ],
)
def test_render_and_runtime_truth_version_pair_is_exact(
    render_version, runtime_version, compatible
):
    manifest = _current_standard_manifest()
    manifest["canonical_render_semantics_version"] = render_version
    manifest["runtime_truth_schema_version"] = runtime_version

    assert _compatible(manifest) is compatible


@pytest.mark.parametrize(
    "version",
    [pytest.param(None, id="missing"), 0, 1, 2, 3, 4, 5, "6", 6.0, True],
)
def test_wrong_or_malformed_render_semantics_version_is_rejected(version):
    manifest = _current_standard_manifest()
    if version is None:
        manifest.pop("canonical_render_semantics_version")
    else:
        manifest["canonical_render_semantics_version"] = version

    assert not _compatible(manifest)


@pytest.mark.parametrize(
    "version",
    [pytest.param(None, id="missing"), 0, 3, "4", 4.0, True],
)
def test_wrong_or_malformed_runtime_truth_version_is_rejected(version):
    manifest = _current_standard_manifest()
    if version is None:
        manifest.pop("runtime_truth_schema_version")
    else:
        manifest["runtime_truth_schema_version"] = version

    assert not _compatible(manifest)


def test_physical_render_semantics_identity_is_deterministic():
    base = "identical-canonical-input"

    assert "canonical-render-semantics-v6" in _canonical_render_semantics_identity()
    assert _fingerprint(base) == _fingerprint(base)


def test_render_semantics_version_invalidates_fingerprint(monkeypatch):
    base = "identical-canonical-input"
    current = _fingerprint(base)
    monkeypatch.setattr(
        "omega.application.visual_production_v2_service."
        "CANONICAL_RENDER_SEMANTICS_VERSION",
        CANONICAL_RENDER_SEMANTICS_VERSION + 1,
    )
    future = _fingerprint(base)

    assert current != future


@pytest.mark.parametrize(
    ("field", "changed"),
    [("target_i", -15.0), ("target_tp", -1.0), ("target_lra", 8.0)],
)
def test_mastering_policy_targets_invalidate_fingerprint(field, changed):
    base = "identical-canonical-input"
    current_policy = {
        "target_i": FINAL_MASTER_TARGET_I,
        "target_tp": FINAL_MASTER_TARGET_TP,
        "target_lra": FINAL_MASTER_TARGET_LRA,
    }
    changed_policy = {**current_policy, field: changed}

    assert _fingerprint(base, **current_policy) != _fingerprint(
        base, **changed_policy
    )


def test_final_master_sample_rate_invalidates_fingerprint():
    base = "identical-canonical-input"

    assert _fingerprint(
        base, sample_rate_hz=FINAL_MASTER_SAMPLE_RATE_HZ
    ) != _fingerprint(base, sample_rate_hz=96_000)


def test_render_semantics_v1_manifest_is_rejected():
    manifest = _current_standard_manifest()
    manifest["canonical_render_semantics_version"] = 1

    assert not _compatible(manifest)


def test_p18g1_presentation_changes_cannot_reuse_pre_g1_cache():
    pre_g1_manifest = _current_standard_manifest()
    pre_g1_manifest.update(
        subtitle_semantics_version=2,
        scenes=[
            {"template_id": "HERO_TITLE", "viewer_title": "Hook"},
            {"template_id": "CTA", "viewer_title": "Closing"},
        ],
    )
    pre_g1_manifest.pop("canonical_render_semantics_version")

    assert not _compatible(pre_g1_manifest)
