"""Curated, renderer-backed subtitle style presets for channels and productions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omega.application.subtitle_engine import SubtitleRenderStyle


class SubtitlePreset(BaseModel):
    """Immutable subtitle style preset with validated render properties."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    style: SubtitleRenderStyle


_SUBTITLE_PRESETS: tuple[SubtitlePreset, ...] = (
    SubtitlePreset(
        id="default",
        name="Default Clean Documentary",
        description="Clean documentary-style subtitles, bottom-centered with dark outline and shadow.",
        style=SubtitleRenderStyle(
            font_family="Arial",
            font_size=48,
            min_font_size=32,
            bold=False,
            primary_color="#FFFFFF",
            outline_color="#000000",
            outline_width=2.0,
            shadow=2.0,
            background_box=False,
            alignment=2,
            margin_v=80,
            max_lines=2,
            max_width_ratio=0.82,
            karaoke=False,
        ),
    ),
    SubtitlePreset(
        id="bold_yellow",
        name="Bold Yellow Impact",
        description="High-contrast bold yellow text with heavy stroke for mobile engagement.",
        style=SubtitleRenderStyle(
            font_family="Arial",
            font_size=52,
            min_font_size=32,
            bold=True,
            primary_color="#FFD400",
            outline_color="#000000",
            outline_width=3.0,
            shadow=2.0,
            background_box=False,
            alignment=2,
            margin_v=90,
            max_lines=2,
            max_width_ratio=0.82,
            karaoke=False,
        ),
    ),
    SubtitlePreset(
        id="boxed_highlight",
        name="Boxed Highlight",
        description="Clean readable subtitles on a dark translucent background box.",
        style=SubtitleRenderStyle(
            font_family="Arial",
            font_size=44,
            min_font_size=32,
            bold=False,
            primary_color="#FFFFFF",
            outline_color="#000000",
            outline_width=0.0,
            shadow=0.0,
            background_box=True,
            alignment=2,
            margin_v=80,
            max_lines=2,
            max_width_ratio=0.82,
            karaoke=False,
        ),
    ),
    SubtitlePreset(
        id="cinematic_top",
        name="Cinematic Minimal",
        description="Lower-third cinematic subtitles with understated typography and subtle outline.",
        style=SubtitleRenderStyle(
            font_family="DejaVu Sans",
            font_size=40,
            min_font_size=28,
            bold=False,
            primary_color="#F8FAFC",
            outline_color="#0F172A",
            outline_width=1.5,
            shadow=0.0,
            background_box=False,
            alignment=2,
            margin_v=75,
            max_lines=2,
            max_width_ratio=0.82,
            karaoke=False,
        ),
    ),
    SubtitlePreset(
        id="minimal_clean",
        name="Minimal Clean",
        description="Understated modern sans-serif typography with subtle outline.",
        style=SubtitleRenderStyle(
            font_family="DejaVu Sans",
            font_size=40,
            min_font_size=28,
            bold=False,
            primary_color="#F8FAFC",
            outline_color="#0F172A",
            outline_width=1.5,
            shadow=0.0,
            background_box=False,
            alignment=2,
            margin_v=70,
            max_lines=2,
            max_width_ratio=0.85,
            karaoke=False,
        ),
    ),
)

_PRESET_MAP: dict[str, SubtitlePreset] = {p.id: p for p in _SUBTITLE_PRESETS}
if "cinematic_top" in _PRESET_MAP:
    _PRESET_MAP["cinematic_minimal"] = _PRESET_MAP["cinematic_top"]


def get_subtitle_presets() -> tuple[SubtitlePreset, ...]:
    """Return all registered subtitle style presets."""
    return _SUBTITLE_PRESETS


def get_subtitle_preset(preset_id: str) -> SubtitlePreset | None:
    """Retrieve a preset by unique identifier."""
    return _PRESET_MAP.get(preset_id)


def resolve_preset_style(preset_id: str | None) -> SubtitleRenderStyle:
    """Resolve a SubtitleRenderStyle from a preset ID, falling back to default."""
    if preset_id and preset_id in _PRESET_MAP:
        return _PRESET_MAP[preset_id].style
    return _PRESET_MAP["default"].style
