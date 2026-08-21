"""Unit tests for FFmpeg filter safety and string escaping."""

from omega.application.ffmpeg_renderer import escape_ffmpeg_filter_string


def test_escape_ffmpeg_filter_string_normal():
    """Verify clean string escaping."""
    s = "Simple title text"
    assert escape_ffmpeg_filter_string(s) == "Simple title text"


def test_escape_ffmpeg_filter_string_adversarial():
    """Verify all dangerous FFmpeg filter characters are strictly escaped."""
    adversarial = "Title: 'Quote' [Brackets] %Percent% ;Semicolon \\Backslash\nNewline"
    escaped = escape_ffmpeg_filter_string(adversarial)

    assert "\\:" in escaped
    assert "\\'" in escaped
    assert "\\[" in escaped
    assert "\\]" in escaped
    assert "\\%" in escaped
    assert "\\;" in escaped
    assert "\\\\" in escaped
    assert "\n" not in escaped


def test_escape_ffmpeg_filter_string_length_bound():
    """Verify max length clamp."""
    long_str = "A" * 500
    escaped = escape_ffmpeg_filter_string(long_str, max_chars=100)
    assert len(escaped) == 100
