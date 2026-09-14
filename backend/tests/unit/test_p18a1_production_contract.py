from uuid import uuid4

import pytest
from pydantic import ValidationError

from omega.application.production_contract import (
    resolve_canonical_production_contract,
)
from omega.application.render_service import ProductionRenderService
from omega.domain.production import (
    NarrationProviderType,
    ProductionMode,
    SubtitleFallbackPolicy,
    SubtitleMode,
    VisualAssetMode,
)
from omega.infrastructure.models import ProductionRequest


def _make_dummy_request(
    mode: str = "MISSION_EXECUTION",
    with_execution: bool = True,
    metadata_: dict | None = None,
    voice_profile: dict | None = None,
) -> ProductionRequest:
    return ProductionRequest(
        id=uuid4(),
        channel_id=uuid4(),
        script_version_id=uuid4(),
        content_request_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        mission_execution_id=uuid4() if with_execution else None,
        mode=mode,
        target_width=1920,
        target_height=1080,
        fps=24,
        video_codec="h264",
        audio_codec="aac",
        container_format="mp4",
        voice_profile=voice_profile or {"voice_ref": "af_heart", "speed": 1.0},
        metadata_=metadata_ or {},
    )


# ── 1. Lineage & Mode Compatibility ──

def test_mission_execution_contract_lineage():
    req = _make_dummy_request(mode="MISSION_EXECUTION", with_execution=True)
    mission_id = uuid4()
    task_id = uuid4()
    render_job_id = uuid4()

    contract = resolve_canonical_production_contract(
        req,
        mission_id=mission_id,
        task_id=task_id,
        render_job_id=render_job_id,
    )

    assert contract.mode == ProductionMode.MISSION_EXECUTION
    assert contract.lineage.channel_id == req.channel_id
    assert contract.lineage.production_request_id == req.id
    assert contract.lineage.content_request_id == req.content_request_id
    assert contract.lineage.script_version_id == req.script_version_id
    assert contract.lineage.channel_dna_revision_id == req.channel_dna_revision_id
    assert contract.lineage.mission_id == mission_id
    assert contract.lineage.mission_execution_id == req.mission_execution_id
    assert contract.lineage.task_id == task_id
    assert contract.lineage.render_job_id == render_job_id


def test_interactive_contract_valid_without_execution_lineage():
    req = _make_dummy_request(mode="INTERACTIVE", with_execution=False)

    contract = resolve_canonical_production_contract(req)

    assert contract.mode == ProductionMode.INTERACTIVE
    assert contract.lineage.channel_id == req.channel_id
    assert contract.lineage.production_request_id == req.id
    # Interactive must legitimately have None for execution lineage
    assert contract.lineage.mission_id is None
    assert contract.lineage.mission_execution_id is None
    assert contract.lineage.task_id is None
    assert contract.lineage.render_job_id is None


# ── 2. Visual Asset Mode Normalization ──

def test_visual_asset_mode_default(monkeypatch):
    monkeypatch.delenv("OMEGA_VISUAL_ASSET_MODE", raising=False)
    req = _make_dummy_request()
    contract = resolve_canonical_production_contract(req)
    # Default env fallback is PEXELS
    assert contract.policy.visual_asset_mode == VisualAssetMode.PEXELS


def test_visual_asset_mode_env_override(monkeypatch):
    monkeypatch.setenv("OMEGA_VISUAL_ASSET_MODE", "LOCAL_TEMPLATE_ONLY")
    req = _make_dummy_request()
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.visual_asset_mode == VisualAssetMode.LOCAL_TEMPLATE_ONLY


def test_visual_asset_mode_request_override(monkeypatch):
    monkeypatch.setenv("OMEGA_VISUAL_ASSET_MODE", "PEXELS")
    req = _make_dummy_request(
        metadata_={"render_settings": {"visual_asset_mode": "LOCAL_TEMPLATE_ONLY"}}
    )
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.visual_asset_mode == VisualAssetMode.LOCAL_TEMPLATE_ONLY


def test_visual_asset_mode_invalid_fails_closed():
    req = _make_dummy_request(
        metadata_={"render_settings": {"visual_asset_mode": "INVALID_MODE"}}
    )
    with pytest.raises(ValueError, match="Unsupported visual_asset_mode: 'INVALID_MODE'"):
        resolve_canonical_production_contract(req)


# ── 3. Narration Provider Normalization ──

def test_narration_provider_default(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    req = _make_dummy_request()
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.narration_provider == NarrationProviderType.LOCAL_TTS


def test_narration_provider_env_gemini(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    req = _make_dummy_request()
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.narration_provider == NarrationProviderType.GEMINI


def test_narration_provider_request_override(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "local")
    req = _make_dummy_request(
        metadata_={"render_settings": {"narration_provider": "neural"}}
    )
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.narration_provider == NarrationProviderType.NEURAL


def test_narration_provider_invalid_fails_closed():
    req = _make_dummy_request(
        metadata_={"render_settings": {"narration_provider": "INVALID_TTS"}}
    )
    with pytest.raises(ValueError, match="Unsupported narration_provider: 'INVALID_TTS'"):
        resolve_canonical_production_contract(req)


# ── 4. Subtitle Mode Normalization ──

def test_subtitle_mode_disabled_yields_off():
    req = _make_dummy_request(
        metadata_={"render_settings": {"subtitle_enabled": False}}
    )
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.subtitle_mode == SubtitleMode.OFF


def test_subtitle_mode_explicit_off():
    req = _make_dummy_request(
        metadata_={"render_settings": {"subtitle_mode": "OFF"}}
    )
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.subtitle_mode == SubtitleMode.OFF


def test_subtitle_mode_default_enabled_is_standard():
    req = _make_dummy_request()
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.subtitle_mode == SubtitleMode.STANDARD


def test_subtitle_mode_legacy_karaoke_bool_yields_karaoke():
    req = _make_dummy_request(
        metadata_={
            "render_settings": {
                "subtitle_style": {"karaoke": True, "font_family": "Arial"}
            }
        }
    )
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.subtitle_mode == SubtitleMode.KARAOKE


def test_subtitle_mode_explicit_karaoke_string():
    req = _make_dummy_request(
        metadata_={"render_settings": {"subtitle_mode": "KARAOKE"}}
    )
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.subtitle_mode == SubtitleMode.KARAOKE


def test_subtitle_mode_invalid_string_fails_closed():
    req = _make_dummy_request(
        metadata_={"render_settings": {"subtitle_mode": "FANCY_EFFECTS"}}
    )
    with pytest.raises(ValueError, match="Invalid subtitle_mode: 'FANCY_EFFECTS'"):
        resolve_canonical_production_contract(req)


def test_subtitle_fallback_policy_default_and_invalid():
    req = _make_dummy_request()
    contract = resolve_canonical_production_contract(req)
    assert contract.policy.subtitle_fallback_policy == SubtitleFallbackPolicy.STANDARD_FALLBACK

    req_invalid = _make_dummy_request(
        metadata_={"render_settings": {"subtitle_fallback_policy": "NOOP_FALLBACK"}}
    )
    with pytest.raises(ValueError, match="Invalid subtitle_fallback_policy: 'NOOP_FALLBACK'"):
        resolve_canonical_production_contract(req_invalid)


# ── 5. Immutability & Snapshot Behavior ──

def test_contract_immutability_and_source_mutation_isolation():
    source_voice = {"voice_ref": "af_bella", "speed": 1.1}
    source_meta = {
        "render_settings": {
            "subtitle_style": {"font_size": 52, "bold": True},
            "channel_bug_enabled": True,
        }
    }
    req = _make_dummy_request(
        voice_profile=source_voice,
        metadata_=source_meta,
    )

    contract = resolve_canonical_production_contract(req)

    # 1. Mutating the source dicts after resolution MUST NOT mutate the contract
    source_voice["voice_ref"] = "MUTATED"
    source_meta["render_settings"]["channel_bug_enabled"] = False
    assert contract.policy.voice_profile["voice_ref"] == "af_bella"
    assert contract.policy.channel_bug_enabled is True

    # 2. Mutating the contract directly MUST raise ValidationError (frozen models)
    with pytest.raises(ValidationError):
        contract.policy.channel_bug_enabled = False

    with pytest.raises(ValidationError):
        contract.policy.subtitle_mode = SubtitleMode.OFF

    with pytest.raises(ValidationError):
        contract.lineage.mission_id = uuid4()


# ── 6. Determinism & Provenance Serialization ──

def test_contract_deterministic_fingerprint():
    channel_id = uuid4()
    req_id = uuid4()
    content_req_id = uuid4()
    script_id = uuid4()
    dna_id = uuid4()
    exec_id = uuid4()

    def make_req():
        return ProductionRequest(
            id=req_id,
            channel_id=channel_id,
            script_version_id=script_id,
            content_request_id=content_req_id,
            channel_dna_revision_id=dna_id,
            mission_execution_id=exec_id,
            mode="MISSION_EXECUTION",
            target_width=1920,
            target_height=1080,
            fps=24,
            voice_profile={"voice_ref": "af_heart"},
            metadata_={"render_settings": {"subtitle_mode": "STANDARD"}},
        )

    contract1 = resolve_canonical_production_contract(make_req())
    contract2 = resolve_canonical_production_contract(make_req())

    assert contract1 == contract2
    assert contract1.canonical_fingerprint() == contract2.canonical_fingerprint()
    assert contract1.to_provenance_dict() == contract2.to_provenance_dict()

    # Verify provenance dict has no volatile runtime tokens
    prov = contract1.to_provenance_dict()
    assert prov["contract_version"] == "v1"
    assert prov["mode"] == "MISSION_EXECUTION"
    assert prov["lineage"]["production_request_id"] == str(req_id)
    assert prov["policy"]["subtitle_mode"] == "STANDARD"


# ── 7. Regression Test: Render Routing Unchanged ──

def test_production_render_routing_remains_unchanged():
    """Verify that P18-A1 does NOT modify ProductionRenderService._should_use_v2 routing."""
    service = ProductionRenderService(visual_production_service=object())

    interactive_req = ProductionRequest(mode="INTERACTIVE")
    assert service._should_use_v2(interactive_req) is False

    mission_req = ProductionRequest(mode="MISSION_EXECUTION")
    assert service._should_use_v2(mission_req) is True

    # When visual_production_service is None, should always be False
    service_no_v2 = ProductionRenderService(visual_production_service=None)
    assert service_no_v2._should_use_v2(mission_req) is False
