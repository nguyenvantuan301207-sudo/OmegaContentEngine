"""Domain models for Channel Style Profile and styling configuration."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.application.subtitle_presets import resolve_preset_style


class ChannelStyleProfile(BaseModel):
    """Channel-level aesthetic, pacing, and subtitle styling profile."""

    model_config = ConfigDict(extra="ignore")

    preset_id: str = "default"
    custom_subtitle_style: SubtitleRenderStyle | None = None
    pacing: str = "BALANCED"
    accent_color: str = "#3B82F6"
    bg_color: str = "#0B0F19"
    notes: str | None = None

    @field_validator("accent_color", "bg_color")
    @classmethod
    def _validate_hex_color(cls, v: str) -> str:
        clean = v.strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", clean):
            raise ValueError(f"Color must be a valid #RRGGBB hex string, got '{v}'")
        return clean

    @field_validator("pacing")
    @classmethod
    def _validate_pacing(cls, v: str) -> str:
        clean = v.strip().upper()
        if clean not in ("FAST", "BALANCED", "RELAXED"):
            return "BALANCED"
        return clean

    def resolve_effective_style(self) -> SubtitleRenderStyle:
        """Resolve effective style: custom override takes precedence over preset."""
        if self.custom_subtitle_style is not None:
            return self.custom_subtitle_style
        return resolve_preset_style(self.preset_id)


class ChannelStyleProfileResponse(BaseModel):
    """API representation of a channel's style profile."""

    model_config = ConfigDict(from_attributes=True)

    channel_id: uuid.UUID
    preset_id: str
    custom_subtitle_style: SubtitleRenderStyle | None = None
    effective_subtitle_style: SubtitleRenderStyle
    pacing: str = "BALANCED"
    accent_color: str = "#3B82F6"
    bg_color: str = "#0B0F19"
    notes: str | None = None


class ChannelStyleProfileUpdate(BaseModel):
    """Payload for updating a channel's style profile."""

    model_config = ConfigDict(extra="forbid")

    preset_id: str = Field(default="default", max_length=50)
    custom_subtitle_style: SubtitleRenderStyle | None = None
    pacing: str = Field(default="BALANCED", max_length=20)
    accent_color: str = Field(default="#3B82F6", max_length=7)
    bg_color: str = Field(default="#0B0F19", max_length=7)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("accent_color", "bg_color")
    @classmethod
    def _validate_hex_color(cls, v: str) -> str:
        clean = v.strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", clean):
            raise ValueError(f"Color must be a valid #RRGGBB hex string, got '{v}'")
        return clean

    @field_validator("pacing")
    @classmethod
    def _validate_pacing(cls, v: str) -> str:
        clean = v.strip().upper()
        if clean not in ("FAST", "BALANCED", "RELAXED"):
            raise ValueError(f"Pacing must be one of FAST, BALANCED, RELAXED, got '{v}'")
        return clean


def extract_channel_style_profile(metadata: dict[str, Any] | None) -> ChannelStyleProfile:
    """Extract ChannelStyleProfile from channel metadata JSON dict."""
    if not metadata:
        return ChannelStyleProfile()
    style_raw = metadata.get("style_profile")
    if not isinstance(style_raw, dict):
        return ChannelStyleProfile()
    try:
        return ChannelStyleProfile.model_validate(style_raw)
    except Exception:
        return ChannelStyleProfile()


def resolve_effective_subtitle_style(
    channel_style: ChannelStyleProfile | None,
    request_subtitle_style: SubtitleRenderStyle | None = None,
) -> SubtitleRenderStyle:
    """Resolve effective subtitle style following strict precedence:
    1. Request-level explicit override (if present)
    2. Channel style profile (custom style or preset)
    3. Global default style
    """
    if request_subtitle_style is not None:
        return request_subtitle_style
    if channel_style is not None:
        return channel_style.resolve_effective_style()
    return resolve_preset_style("default")
