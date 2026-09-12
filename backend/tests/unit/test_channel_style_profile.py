"""Unit tests for Channel Style Profile models, resolution precedence, and service."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application import channel_service
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.application.subtitle_presets import get_subtitle_preset
from omega.domain.channel_style import (
    ChannelStyleProfile,
    ChannelStyleProfileResponse,
    ChannelStyleProfileUpdate,
    extract_channel_style_profile,
    resolve_effective_subtitle_style,
)
from omega.infrastructure.models import Channel


def test_channel_style_profile_defaults():
    profile = ChannelStyleProfile()
    assert profile.preset_id == "default"
    assert profile.custom_subtitle_style is None
    effective = profile.resolve_effective_style()
    assert effective == get_subtitle_preset("default").style


def test_channel_style_profile_preset_resolution():
    profile = ChannelStyleProfile(preset_id="bold_yellow")
    effective = profile.resolve_effective_style()
    assert effective.bold is True
    assert effective.primary_color == "#FFD400"
    assert effective == get_subtitle_preset("bold_yellow").style


def test_channel_style_profile_custom_override_takes_precedence_over_preset():
    custom = SubtitleRenderStyle(
        font_family="DejaVu Sans",
        font_size=60,
        primary_color="#00FFCC",
        outline_width=4.0,
    )
    profile = ChannelStyleProfile(preset_id="bold_yellow", custom_subtitle_style=custom)
    effective = profile.resolve_effective_style()
    assert effective == custom
    assert effective.font_size == 60
    assert effective.primary_color == "#00FFCC"


def test_extract_channel_style_profile():
    # None or empty
    assert extract_channel_style_profile(None).preset_id == "default"
    assert extract_channel_style_profile({}).preset_id == "default"
    assert extract_channel_style_profile({"style_profile": "not-a-dict"}).preset_id == "default"

    # Valid dict with preset
    data = {"style_profile": {"preset_id": "cinematic_top", "notes": "Top banner style"}}
    extracted = extract_channel_style_profile(data)
    assert extracted.preset_id == "cinematic_top"
    assert extracted.notes == "Top banner style"
    assert extracted.resolve_effective_style().alignment == 2

    # Corrupt fields fallback gracefully to default
    corrupt_data = {"style_profile": {"preset_id": "default", "custom_subtitle_style": {"font_size": "invalid"}}}
    fallback = extract_channel_style_profile(corrupt_data)
    assert fallback.preset_id == "default"


def test_resolve_effective_subtitle_style_precedence():
    request_override = SubtitleRenderStyle(
        font_family="Arial",
        font_size=72,
        primary_color="#E11D48",
    )
    channel_custom = SubtitleRenderStyle(
        font_family="DejaVu Sans",
        font_size=54,
        primary_color="#2563EB",
    )
    channel_profile = ChannelStyleProfile(preset_id="bold_yellow", custom_subtitle_style=channel_custom)

    # 1. Request-level override beats channel custom and preset
    resolved = resolve_effective_subtitle_style(
        channel_style=channel_profile,
        request_subtitle_style=request_override,
    )
    assert resolved == request_override

    # 2. Channel custom beats preset when request override is None
    resolved = resolve_effective_subtitle_style(
        channel_style=channel_profile,
        request_subtitle_style=None,
    )
    assert resolved == channel_custom

    # 3. Channel preset is used when channel custom is None
    channel_preset_only = ChannelStyleProfile(preset_id="boxed_highlight", custom_subtitle_style=None)
    resolved = resolve_effective_subtitle_style(
        channel_style=channel_preset_only,
        request_subtitle_style=None,
    )
    assert resolved == get_subtitle_preset("boxed_highlight").style

    # 4. Global default is used when channel_style is None
    resolved = resolve_effective_subtitle_style(
        channel_style=None,
        request_subtitle_style=None,
    )
    assert resolved == get_subtitle_preset("default").style


@pytest.mark.asyncio
async def test_get_and_update_channel_style_profile_service():
    channel_id = uuid.uuid4()
    mock_channel = Channel(
        id=channel_id,
        name="Tech Test Channel",
        slug="tech-test-channel",
        state="ACTIVE",
        metadata_={"style_profile": {"preset_id": "minimal_clean", "notes": "Minimal test"}},
    )

    mock_session = AsyncMock()
    mock_execute = MagicMock()
    mock_execute.scalar_one_or_none.return_value = mock_channel
    mock_session.execute.return_value = mock_execute

    # Test GET service
    res = await channel_service.get_channel_style_profile(mock_session, channel_id)
    assert res is not None
    assert isinstance(res, ChannelStyleProfileResponse)
    assert res.channel_id == channel_id
    assert res.preset_id == "minimal_clean"
    assert res.notes == "Minimal test"
    assert res.effective_subtitle_style.margin_v == 70

    # Test UPDATE service
    update_payload = ChannelStyleProfileUpdate(
        preset_id="bold_yellow",
        notes="Updated to high retention yellow",
    )
    res_updated = await channel_service.update_channel_style_profile(
        mock_session, channel_id, update_payload
    )
    assert res_updated is not None
    assert res_updated.preset_id == "bold_yellow"
    assert res_updated.notes == "Updated to high retention yellow"
    assert res_updated.effective_subtitle_style.bold is True
    assert mock_session.commit.called


def test_channel_style_profile_pacing_and_palette_dimensions():
    profile = ChannelStyleProfile(
        preset_id="bold_yellow",
        pacing="FAST",
        accent_color="#FF5500",
        bg_color="#101020",
    )
    assert profile.pacing == "FAST"
    assert profile.accent_color == "#FF5500"
    assert profile.bg_color == "#101020"

    # Default fallback on invalid pacing
    invalid_pacing = ChannelStyleProfile(pacing="ULTRA_FAST")
    assert invalid_pacing.pacing == "BALANCED"

    # Invalid color hex raises validation error
    with pytest.raises(ValueError, match="valid #RRGGBB hex string"):
        ChannelStyleProfile(accent_color="not-a-color")


def test_style_pacing_affects_storyboard_scene_segmentation():
    from omega.application.storyboard_engine import StoryboardEngine

    engine = StoryboardEngine()
    # A script with 6 statements of 10 words each = 60 words
    script = {
        "estimated_duration_seconds": 30,
        "sections": [
            {
                "heading": "Intro",
                "statements": [
                    {"statement_text": "One two three four five six seven eight nine ten."},
                    {"statement_text": "One two three four five six seven eight nine ten."},
                    {"statement_text": "One two three four five six seven eight nine ten."},
                    {"statement_text": "One two three four five six seven eight nine ten."},
                    {"statement_text": "One two three four five six seven eight nine ten."},
                    {"statement_text": "One two three four five six seven eight nine ten."},
                ],
            }
        ],
    }

    # FAST (target 12 words) creates more scenes than RELAXED (target 24 words)
    sb_fast = engine.generate_storyboard(script, pacing="FAST")
    sb_relaxed = engine.generate_storyboard(script, pacing="RELAXED")

    assert len(sb_fast.scenes) > len(sb_relaxed.scenes)
    assert len(sb_fast.scenes) == 3
    assert len(sb_relaxed.scenes) == 2


def test_style_palette_affects_template_renderer_css():
    from omega.application.visual_template_renderer import (
        TemplateInputKey,
        TemplatePayload,
        VisualTemplateId,
        VisualTemplateRenderer,
    )

    renderer = VisualTemplateRenderer()
    payload = TemplatePayload(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        inputs={
            TemplateInputKey.TITLE: "Style Palette Test",
            TemplateInputKey.SUBTITLE: "Verification",
        },
        asset_requirements=(),
        motion_profile="test",
        metadata={},
    )

    doc_default = renderer.render(payload)
    doc_custom = renderer.render(
        payload,
        accent_color="#FF5722",
        bg_color="#1E1E2E",
    )

    assert doc_default.content_sha256 != doc_custom.content_sha256
    assert "--accent: #FF5722;" in doc_custom.html
    assert "--bg: #1E1E2E;" in doc_custom.html
