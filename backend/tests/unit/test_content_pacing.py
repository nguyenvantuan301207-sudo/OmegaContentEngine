"""Unit tests for content pacing and retention beat planner."""

from omega.application.content_pacing import (
    estimate_duration_seconds,
    estimate_target_word_count,
    plan_retention_beats,
)
from omega.domain.content import RetentionBeatType


def test_estimate_duration_seconds():
    # 290 words at MODERATE (145 WPM -> ~2.416 words/sec) => ~120 seconds
    duration = estimate_duration_seconds(290, "MODERATE")
    assert duration == 120

    # 120 words at SLOW (120 WPM -> 2 words/sec) => 60 seconds
    assert estimate_duration_seconds(120, "SLOW") == 60

    # 330 words at FAST (165 WPM -> 2.75 words/sec) => 120 seconds
    assert estimate_duration_seconds(330, "FAST") == 120

    # Unknown pace falls back to MODERATE
    assert estimate_duration_seconds(145, "UNKNOWN_PACE") == 60


def test_estimate_target_word_count():
    # 60s at MODERATE (145 WPM) => 145 words
    assert estimate_target_word_count(60, "MODERATE") == 145
    # 60s at SLOW (120 WPM) => 120 words
    assert estimate_target_word_count(60, "SLOW") == 120


def test_plan_retention_beats():
    beats = plan_retention_beats(target_duration_seconds=480, num_sections=4)
    assert len(beats) == 4

    assert beats[0]["beat_type"] == RetentionBeatType.HOOK.value
    assert beats[1]["beat_type"] == RetentionBeatType.OPEN_LOOP.value
    assert beats[2]["beat_type"] == RetentionBeatType.PATTERN_INTERRUPT.value
    assert beats[3]["beat_type"] == RetentionBeatType.REVEAL.value

    # Timestamps are strictly ordered
    timestamps = [b["timestamp_estimate_seconds"] for b in beats]
    assert timestamps == sorted(timestamps)
    assert timestamps[0] < timestamps[1] < timestamps[2] < timestamps[3]


def test_plan_retention_beats_empty():
    assert plan_retention_beats(480, 0) == []
