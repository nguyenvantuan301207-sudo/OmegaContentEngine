"""Unit tests for Subtitle Engine and SRT formatting."""

from omega.application.subtitle_engine import format_srt_time, generate_srt_content


def test_format_srt_time():
    """Verify SRT timestamp string formatting HH:MM:SS,mmm."""
    assert format_srt_time(0) == "00:00:00,000"
    assert format_srt_time(1500) == "00:00:01,500"
    assert format_srt_time(65432) == "00:01:05,432"
    assert format_srt_time(3661005) == "01:01:01,005"


def test_generate_srt_content():
    """Verify canonical SubRip block construction."""
    cues = [
        {"cue_order": 1, "start_ms": 0, "end_ms": 2500, "text": "Welcome to OMEGA."},
        {
            "cue_order": 2,
            "start_ms": 2700,
            "end_ms": 5000,
            "text": "Autonomous Content Operating System.",
        },
    ]
    srt = generate_srt_content(cues)

    assert "1\n00:00:00,000 --> 00:00:02,500\nWelcome to OMEGA." in srt
    assert "2\n00:00:02,700 --> 00:00:05,000\nAutonomous Content Operating System." in srt
