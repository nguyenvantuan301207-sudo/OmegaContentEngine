"""Canonical production contract and deterministic policy resolver.

Establishes an immutable, normalized production contract and lineage snapshot
prior to render execution, without performing network calls, provider calls,
or database writes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.domain.channel_style import (
    ChannelStyleProfile,
    extract_channel_style_profile,
    resolve_effective_subtitle_style,
)
from omega.domain.production import (
    NarrationProviderType,
    ProductionMode,
    SubtitleFallbackPolicy,
    SubtitleMode,
    VisualAssetMode,
)


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to safely retrieve an attribute or dict key."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_visual_asset_mode(raw_value: Any) -> VisualAssetMode:
    """Normalize visual asset mode following deterministic precedence.

    Raises ValueError on unrecognized explicit values.
    """
    if raw_value is None:
        env_val = os.environ.get("OMEGA_VISUAL_ASSET_MODE", "PEXELS").strip()
        raw = env_val
    elif isinstance(raw_value, VisualAssetMode):
        return raw_value
    else:
        raw = str(raw_value).strip()

    val = raw.upper()
    if val == "LOCAL_TEMPLATE_ONLY":
        return VisualAssetMode.LOCAL_TEMPLATE_ONLY
    elif val == "PEXELS":
        return VisualAssetMode.PEXELS
    else:
        raise ValueError(f"Unsupported visual_asset_mode: '{raw_value}'")


def _normalize_narration_provider(raw_value: Any) -> NarrationProviderType:
    """Normalize narration provider following deterministic precedence.

    Raises ValueError on unrecognized explicit values.
    """
    if raw_value is None:
        env_val = os.environ.get("TTS_PROVIDER", "local").strip()
        raw = env_val
    elif isinstance(raw_value, NarrationProviderType):
        return raw_value
    else:
        raw = str(raw_value).strip()

    val = raw.lower()
    if val in ("local", "local_tts", "kokoro"):
        return NarrationProviderType.LOCAL_TTS
    elif val == "gemini":
        return NarrationProviderType.GEMINI
    elif val in ("neural", "openai", "cloud"):
        return NarrationProviderType.NEURAL
    else:
        raise ValueError(f"Unsupported narration_provider: '{raw_value}'")


def _normalize_subtitle_mode(
    raw_mode: Any,
    subtitle_enabled: bool | None,
    style_karaoke: bool | None,
) -> SubtitleMode:
    """Normalize subtitle mode into canonical SubtitleMode enum.

    Precedence:
    1. Explicit subtitle_mode (if supplied)
    2. Explicit subtitle_enabled == False -> OFF
    3. style_karaoke == True -> KARAOKE
    4. Default -> STANDARD

    Raises ValueError on unrecognized explicit subtitle_mode values.
    """
    if raw_mode is not None:
        if isinstance(raw_mode, SubtitleMode):
            return raw_mode
        s = str(raw_mode).strip().upper()
        if s in ("OFF", "DISABLED", "NONE", "FALSE"):
            return SubtitleMode.OFF
        elif s in ("STANDARD", "SENTENCE", "TRUE"):
            return SubtitleMode.STANDARD
        elif s in ("KARAOKE",):
            return SubtitleMode.KARAOKE
        else:
            raise ValueError(f"Invalid subtitle_mode: '{raw_mode}'")

    if subtitle_enabled is False:
        return SubtitleMode.OFF

    if style_karaoke is True:
        return SubtitleMode.KARAOKE

    return SubtitleMode.STANDARD


def _normalize_subtitle_fallback_policy(raw_policy: Any) -> SubtitleFallbackPolicy:
    """Normalize subtitle fallback policy."""
    if raw_policy is None:
        return SubtitleFallbackPolicy.STANDARD_FALLBACK
    if isinstance(raw_policy, SubtitleFallbackPolicy):
        return raw_policy
    s = str(raw_policy).strip().upper()
    if s in ("STANDARD_FALLBACK", "STANDARD"):
        return SubtitleFallbackPolicy.STANDARD_FALLBACK
    raise ValueError(f"Invalid subtitle_fallback_policy: '{raw_policy}'")


class CanonicalProductionLineage(BaseModel):
    """Immutable production lineage snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_id: UUID
    production_request_id: UUID
    content_request_id: UUID
    script_version_id: UUID
    channel_dna_revision_id: UUID

    mission_id: UUID | None = None
    mission_execution_id: UUID | None = None
    task_id: UUID | None = None
    render_job_id: UUID | None = None


class CanonicalProductionPolicy(BaseModel):
    """Normalized production rendering policy snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    visual_asset_mode: VisualAssetMode
    narration_provider: NarrationProviderType
    subtitle_mode: SubtitleMode
    subtitle_fallback_policy: SubtitleFallbackPolicy
    subtitle_style: SubtitleRenderStyle

    target_width: int = 1920
    target_height: int = 1080
    target_fps: int = 24
    fps_mode: str = "CFR"
    video_codec: str = "h264"
    audio_codec: str = "aac"
    container_format: str = "mp4"

    channel_bug_enabled: bool = True
    intro_enabled: bool = True
    outro_enabled: bool = True
    brand_spec_reference: str | None = None

    voice_profile: dict[str, Any] = Field(default_factory=dict)


class CanonicalProductionContract(BaseModel):
    """Authoritative canonical production contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str = "v1"
    mode: ProductionMode
    lineage: CanonicalProductionLineage
    policy: CanonicalProductionPolicy

    def to_provenance_dict(self) -> dict[str, Any]:
        """Deterministic serialization suitable for provenance and snapshot comparisons."""
        return {
            "contract_version": self.contract_version,
            "mode": self.mode.value,
            "lineage": {
                "channel_id": str(self.lineage.channel_id),
                "production_request_id": str(self.lineage.production_request_id),
                "content_request_id": str(self.lineage.content_request_id),
                "script_version_id": str(self.lineage.script_version_id),
                "channel_dna_revision_id": str(self.lineage.channel_dna_revision_id),
                "mission_id": str(self.lineage.mission_id) if self.lineage.mission_id else None,
                "mission_execution_id": str(self.lineage.mission_execution_id)
                if self.lineage.mission_execution_id
                else None,
                "task_id": str(self.lineage.task_id) if self.lineage.task_id else None,
                "render_job_id": str(self.lineage.render_job_id)
                if self.lineage.render_job_id
                else None,
            },
            "policy": {
                "visual_asset_mode": self.policy.visual_asset_mode.value,
                "narration_provider": self.policy.narration_provider.value,
                "subtitle_mode": self.policy.subtitle_mode.value,
                "subtitle_fallback_policy": self.policy.subtitle_fallback_policy.value,
                "subtitle_style": self.policy.subtitle_style.model_dump(),
                "target_width": self.policy.target_width,
                "target_height": self.policy.target_height,
                "target_fps": self.policy.target_fps,
                "fps_mode": self.policy.fps_mode,
                "video_codec": self.policy.video_codec,
                "audio_codec": self.policy.audio_codec,
                "container_format": self.policy.container_format,
                "channel_bug_enabled": self.policy.channel_bug_enabled,
                "intro_enabled": self.policy.intro_enabled,
                "outro_enabled": self.policy.outro_enabled,
                "brand_spec_reference": self.policy.brand_spec_reference,
                "voice_profile": copy.deepcopy(self.policy.voice_profile),
            },
        }

    def canonical_fingerprint(self) -> str:
        """SHA-256 fingerprint of the serialized canonical contract."""
        data = json.dumps(self.to_provenance_dict(), sort_keys=True)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()


def resolve_canonical_production_contract(
    production_request: Any,
    *,
    channel: Any | None = None,
    channel_metadata: dict[str, Any] | None = None,
    mission_id: UUID | None = None,
    task_id: UUID | None = None,
    render_job_id: UUID | None = None,
    overrides: dict[str, Any] | None = None,
) -> CanonicalProductionContract:
    """Resolve an authoritative CanonicalProductionContract from existing request and channel state.

    This function is 100% pure with respect to side effects:
    - 0 network calls
    - 0 provider calls
    - 0 database queries or writes
    - 0 rendering operations
    """
    overrides = overrides or {}

    # 1. Lineage extraction
    channel_id = _get_val(production_request, "channel_id")
    if isinstance(channel_id, str):
        channel_id = UUID(channel_id)
    prod_req_id = _get_val(production_request, "id")
    if isinstance(prod_req_id, str):
        prod_req_id = UUID(prod_req_id)
    content_req_id = _get_val(production_request, "content_request_id")
    if isinstance(content_req_id, str):
        content_req_id = UUID(content_req_id)
    script_version_id = _get_val(production_request, "script_version_id")
    if isinstance(script_version_id, str):
        script_version_id = UUID(script_version_id)
    channel_dna_revision_id = _get_val(production_request, "channel_dna_revision_id")
    if isinstance(channel_dna_revision_id, str):
        channel_dna_revision_id = UUID(channel_dna_revision_id)

    mission_execution_id = _get_val(production_request, "mission_execution_id")
    if isinstance(mission_execution_id, str):
        mission_execution_id = UUID(mission_execution_id)

    # Resolve mission_id if not passed directly but available on request relationship
    resolved_mission_id = mission_id
    if resolved_mission_id is None:
        mission_exec_rel = _get_val(production_request, "mission_execution")
        if mission_exec_rel is not None:
            resolved_mission_id = _get_val(mission_exec_rel, "mission_id")
            if isinstance(resolved_mission_id, str):
                resolved_mission_id = UUID(resolved_mission_id)

    lineage = CanonicalProductionLineage(
        channel_id=channel_id,
        production_request_id=prod_req_id,
        content_request_id=content_req_id,
        script_version_id=script_version_id,
        channel_dna_revision_id=channel_dna_revision_id,
        mission_id=resolved_mission_id,
        mission_execution_id=mission_execution_id,
        task_id=task_id,
        render_job_id=render_job_id,
    )

    # 2. Mode determination
    raw_mode = _get_val(production_request, "mode", ProductionMode.INTERACTIVE.value)
    if isinstance(raw_mode, ProductionMode):
        mode = raw_mode
    elif hasattr(raw_mode, "value"):
        mode = ProductionMode(raw_mode.value)
    else:
        try:
            mode = ProductionMode(str(raw_mode).strip().upper())
        except ValueError:
            mode = ProductionMode.INTERACTIVE

    # 3. Metadata & render_settings extraction
    req_meta = copy.deepcopy(dict(_get_val(production_request, "metadata_", None) or {}))
    render_settings = dict(req_meta.get("render_settings") or {})

    ch_meta = None
    if channel_metadata is not None:
        ch_meta = copy.deepcopy(channel_metadata)
    elif channel is not None:
        ch_meta = copy.deepcopy(dict(_get_val(channel, "metadata_", None) or _get_val(channel, "metadata", None) or {}))

    channel_style: ChannelStyleProfile | None = None
    if ch_meta:
        channel_style = extract_channel_style_profile(ch_meta)

    # 4. Subtitle Style & Mode Normalization
    raw_style = (
        overrides.get("subtitle_style")
        or render_settings.get("subtitle_style")
        or req_meta.get("subtitle_style")
    )
    request_subtitle_style: SubtitleRenderStyle | None = None
    if raw_style is not None:
        if isinstance(raw_style, SubtitleRenderStyle):
            request_subtitle_style = raw_style
        elif isinstance(raw_style, dict):
            request_subtitle_style = SubtitleRenderStyle.model_validate(raw_style)

    effective_subtitle_style = resolve_effective_subtitle_style(
        channel_style=channel_style,
        request_subtitle_style=request_subtitle_style,
    )

    raw_sub_enabled = (
        overrides.get("subtitle_enabled")
        if "subtitle_enabled" in overrides
        else render_settings.get("subtitle_enabled", req_meta.get("subtitle_enabled"))
    )
    raw_sub_mode = (
        overrides.get("subtitle_mode")
        or render_settings.get("subtitle_mode")
        or req_meta.get("subtitle_mode")
    )

    subtitle_mode = _normalize_subtitle_mode(
        raw_mode=raw_sub_mode,
        subtitle_enabled=raw_sub_enabled,
        style_karaoke=effective_subtitle_style.karaoke,
    )

    raw_fallback = (
        overrides.get("subtitle_fallback_policy")
        or render_settings.get("subtitle_fallback_policy")
        or req_meta.get("subtitle_fallback_policy")
    )
    subtitle_fallback_policy = _normalize_subtitle_fallback_policy(raw_fallback)

    # 5. Visual Asset Mode Normalization
    raw_vam = (
        overrides.get("visual_asset_mode")
        or render_settings.get("visual_asset_mode")
        or req_meta.get("visual_asset_mode")
        or (ch_meta.get("visual_asset_mode") if ch_meta else None)
    )
    visual_asset_mode = _normalize_visual_asset_mode(raw_vam)

    # 6. Narration Provider Normalization
    raw_np = (
        overrides.get("narration_provider")
        or render_settings.get("narration_provider")
        or req_meta.get("narration_provider")
        or (ch_meta.get("narration_provider") if ch_meta else None)
    )
    narration_provider = _normalize_narration_provider(raw_np)

    # 7. Render Profile & Dimensions
    target_width = int(overrides.get("target_width") or _get_val(production_request, "target_width", 1920) or 1920)
    target_height = int(overrides.get("target_height") or _get_val(production_request, "target_height", 1080) or 1080)
    target_fps = int(overrides.get("fps") or _get_val(production_request, "fps", 24) or 24)
    fps_mode = str(overrides.get("fps_mode") or "CFR")
    video_codec = str(overrides.get("video_codec") or _get_val(production_request, "video_codec", "h264") or "h264")
    audio_codec = str(overrides.get("audio_codec") or _get_val(production_request, "audio_codec", "aac") or "aac")
    container_format = str(
        overrides.get("container_format") or _get_val(production_request, "container_format", "mp4") or "mp4"
    )

    # 8. Branding Overlays
    channel_bug_enabled = bool(
        overrides.get("channel_bug_enabled", render_settings.get("channel_bug_enabled", True))
    )
    intro_enabled = bool(
        overrides.get("intro_enabled", render_settings.get("intro_enabled", True))
    )
    outro_enabled = bool(
        overrides.get("outro_enabled", render_settings.get("outro_enabled", True))
    )
    brand_spec_reference = (
        overrides.get("brand_spec_reference") or render_settings.get("brand_spec_reference")
    )

    # 9. Voice Profile Snapshot
    vp = copy.deepcopy(dict(_get_val(production_request, "voice_profile", None) or {}))
    if "voice_profile" in overrides and isinstance(overrides["voice_profile"], dict):
        vp.update(overrides["voice_profile"])

    policy = CanonicalProductionPolicy(
        visual_asset_mode=visual_asset_mode,
        narration_provider=narration_provider,
        subtitle_mode=subtitle_mode,
        subtitle_fallback_policy=subtitle_fallback_policy,
        subtitle_style=effective_subtitle_style,
        target_width=target_width,
        target_height=target_height,
        target_fps=target_fps,
        fps_mode=fps_mode,
        video_codec=video_codec,
        audio_codec=audio_codec,
        container_format=container_format,
        channel_bug_enabled=channel_bug_enabled,
        intro_enabled=intro_enabled,
        outro_enabled=outro_enabled,
        brand_spec_reference=brand_spec_reference,
        voice_profile=vp,
    )

    return CanonicalProductionContract(
        contract_version="v1",
        mode=mode,
        lineage=lineage,
        policy=policy,
    )
