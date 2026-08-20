"""Unit tests for Channel domain model, state transitions, and validation."""

from __future__ import annotations

import pytest

from omega.domain.channel import (
    ChannelCreate,
    ChannelState,
    InvalidStateTransitionError,
    Platform,
    validate_channel_transition,
    validate_language_tag,
    validate_region_tag,
    validate_slug,
    validate_timezone,
)


def test_valid_channel_state_transitions() -> None:
    """Test permissible state transitions for Channel."""
    # DRAFT -> ACTIVE, ARCHIVED
    validate_channel_transition(ChannelState.DRAFT, ChannelState.ACTIVE)
    validate_channel_transition(ChannelState.DRAFT, ChannelState.ARCHIVED)

    # ACTIVE -> PAUSED, ARCHIVED
    validate_channel_transition(ChannelState.ACTIVE, ChannelState.PAUSED)
    validate_channel_transition(ChannelState.ACTIVE, ChannelState.ARCHIVED)

    # PAUSED -> ACTIVE, ARCHIVED
    validate_channel_transition(ChannelState.PAUSED, ChannelState.ACTIVE)
    validate_channel_transition(ChannelState.PAUSED, ChannelState.ARCHIVED)


def test_invalid_channel_state_transitions() -> None:
    """Test disallowed state transitions for Channel."""
    # ARCHIVED is terminal
    with pytest.raises(InvalidStateTransitionError):
        validate_channel_transition(ChannelState.ARCHIVED, ChannelState.ACTIVE)
    with pytest.raises(InvalidStateTransitionError):
        validate_channel_transition(ChannelState.ARCHIVED, ChannelState.DRAFT)

    # DRAFT cannot go directly to PAUSED
    with pytest.raises(InvalidStateTransitionError):
        validate_channel_transition(ChannelState.DRAFT, ChannelState.PAUSED)


def test_slug_validation() -> None:
    """Test URL-safe slug validation."""
    # Valid slugs
    assert validate_slug("tech-daily") == "tech-daily"
    assert validate_slug("ai-news-123") == "ai-news-123"
    assert validate_slug("abc") == "abc"

    # Invalid slugs
    with pytest.raises(ValueError, match="between 3 and 100"):
        validate_slug("ab")
    with pytest.raises(ValueError, match="only lowercase alphanumeric"):
        validate_slug("Tech_Daily")
    with pytest.raises(ValueError, match="only lowercase alphanumeric"):
        validate_slug("tech daily")
    with pytest.raises(ValueError, match="only lowercase alphanumeric"):
        validate_slug("tech--daily")


def test_language_tag_validation() -> None:
    """Test supported BCP-47 normalized subset language tags."""
    # Valid language tags
    assert validate_language_tag("en") == "en"
    assert validate_language_tag("vi") == "vi"
    assert validate_language_tag("en-US") == "en-US"
    assert validate_language_tag("pt-BR") == "pt-BR"
    assert validate_language_tag("es-419") == "es-419"
    assert validate_language_tag("zh-Hans") == "zh-Hans"

    # Invalid language tags
    with pytest.raises(ValueError, match="Invalid language tag"):
        validate_language_tag("english_us")
    with pytest.raises(ValueError, match="Invalid language tag"):
        validate_language_tag("12345")
    with pytest.raises(ValueError, match="Invalid language tag"):
        validate_language_tag("e")


def test_region_tag_validation() -> None:
    """Test ISO-3166-1 alpha-2 region validation."""
    assert validate_region_tag("US") == "US"
    assert validate_region_tag("vn") == "VN"
    assert validate_region_tag("GB") == "GB"

    with pytest.raises(ValueError, match="Invalid region code"):
        validate_region_tag("USA")
    with pytest.raises(ValueError, match="Invalid region code"):
        validate_region_tag("1")


def test_timezone_validation() -> None:
    """Test IANA timezone validation."""
    assert validate_timezone("UTC") == "UTC"
    assert validate_timezone("America/New_York") == "America/New_York"
    assert validate_timezone("Asia/Ho_Chi_Minh") == "Asia/Ho_Chi_Minh"

    with pytest.raises(ValueError, match="Invalid IANA timezone"):
        validate_timezone("Mars/Olympus_Mons")
    with pytest.raises(ValueError, match="Invalid IANA timezone"):
        validate_timezone("GMT+7")


def test_channel_create_schema_platform_validation() -> None:
    """Test that ChannelCreate strictly requires supported platforms (YOUTUBE only in OMEGA-003)."""
    # YOUTUBE is valid
    cc = ChannelCreate(
        name="Valid Tech Channel",
        slug="valid-tech",
        platform=Platform.YOUTUBE,
    )
    assert cc.platform == Platform.YOUTUBE

    # Reserved platforms are rejected in OMEGA-003
    with pytest.raises(ValueError, match="Platform 'TIKTOK' is reserved and not supported"):
        ChannelCreate(
            name="TikTok Channel",
            slug="tiktok-channel",
            platform=Platform.TIKTOK,
        )
