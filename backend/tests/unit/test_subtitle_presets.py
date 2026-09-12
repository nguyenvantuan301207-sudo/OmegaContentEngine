"""Unit tests for subtitle style presets and render capabilities integration."""

from omega.application.render_capabilities import get_production_render_capabilities
from omega.application.subtitle_engine import (
    SubtitleRenderStyle,
    generate_karaoke_ass_document,
    generate_karaoke_cues,
)
from omega.application.subtitle_presets import (
    SubtitlePreset,
    get_subtitle_preset,
    get_subtitle_presets,
    resolve_preset_style,
)


def test_registered_presets_are_valid_and_distinct():
    presets = get_subtitle_presets()
    assert len(presets) >= 5

    ids = [p.id for p in presets]
    names = [p.name for p in presets]
    assert len(ids) == len(set(ids)), "Preset IDs must be unique"
    assert len(names) == len(set(names)), "Preset names must be unique"

    for preset in presets:
        assert isinstance(preset, SubtitlePreset)
        assert isinstance(preset.style, SubtitleRenderStyle)
        assert preset.id
        assert preset.name
        assert preset.description


def test_preset_lookup_and_fallback():
    assert get_subtitle_preset("default") is not None
    assert get_subtitle_preset("default").style.alignment == 2

    bold_yellow = get_subtitle_preset("bold_yellow")
    assert bold_yellow is not None
    assert bold_yellow.style.bold is True
    assert bold_yellow.style.primary_color == "#FFD400"
    assert bold_yellow.style.outline_width == 3.0

    boxed = get_subtitle_preset("boxed_highlight")
    assert boxed is not None
    assert boxed.style.background_box is True

    top = get_subtitle_preset("cinematic_top")
    assert top is not None
    assert top.style.alignment == 2

    minimal = get_subtitle_preset("minimal_clean")
    assert minimal is not None
    assert minimal.style.margin_v == 70

    assert get_subtitle_preset("non_existent_preset_xyz") is None
    fallback_style = resolve_preset_style("non_existent_preset_xyz")
    assert fallback_style == get_subtitle_preset("default").style


def test_render_capabilities_exposes_presets():
    caps = get_production_render_capabilities()
    assert len(caps.subtitle.presets) >= 5
    preset_ids = {p.id for p in caps.subtitle.presets}
    assert "default" in preset_ids
    assert "bold_yellow" in preset_ids
    assert "boxed_highlight" in preset_ids
    assert "cinematic_top" in preset_ids
    assert "minimal_clean" in preset_ids


def test_all_presets_generate_valid_ass_subtitles():
    segments = [
        {"start_ms": 0, "duration_ms": 2000, "text": "This is a test of subtitle presets."},
        {"start_ms": 2000, "duration_ms": 3000, "text": "Each preset applies distinct ASS styling parameters."},
    ]
    cues = generate_karaoke_cues(segments)

    for preset in get_subtitle_presets():
        doc = generate_karaoke_ass_document(cues, style=preset.style)
        assert doc.content
        assert len(doc.layout) == len(cues)
        assert "[Script Info]" in doc.content
        assert "[V4+ Styles]" in doc.content
        assert "[Events]" in doc.content
        assert f"Style: OMEGA_KARAOKE,{preset.style.font_family},{preset.style.font_size}" in doc.content

        # Check alignment mapping
        if preset.style.alignment == 8 or preset.style.alignment == 2:
            assert f",{preset.style.alignment},30,30,{preset.style.margin_v}" in doc.content
