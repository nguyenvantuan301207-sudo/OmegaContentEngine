"""Content pacing, duration estimation, and retention beat planning."""

from __future__ import annotations

import math
from typing import Any

from omega.domain.content import RetentionBeatType

# Normalized words per minute by pace name
PACE_WPM_MAP: dict[str, int] = {
    "SLOW": 120,
    "MODERATE": 145,
    "FAST": 165,
    "ENERGETIC": 180,
}
DEFAULT_PACE = "MODERATE"


def estimate_duration_seconds(word_count: int, pace: str = DEFAULT_PACE) -> int:
    """Calculate estimated spoken duration in seconds from word count and narration pace."""
    wpm = PACE_WPM_MAP.get(pace.upper(), PACE_WPM_MAP[DEFAULT_PACE])
    if wpm <= 0:
        return 0
    return max(1, round(word_count * 60.0 / wpm))


def estimate_target_word_count(target_duration_seconds: int, pace: str = DEFAULT_PACE) -> int:
    """Calculate target word count from desired duration in seconds and narration pace."""
    wpm = PACE_WPM_MAP.get(pace.upper(), PACE_WPM_MAP[DEFAULT_PACE])
    words_per_second = wpm / 60.0
    return max(10, math.ceil(target_duration_seconds * words_per_second))


def plan_retention_beats(
    target_duration_seconds: int,
    num_sections: int,
) -> list[dict[str, Any]]:
    """Plan a structured sequence of retention beats across the duration timeline."""
    if num_sections <= 0:
        return []

    beats: list[dict[str, Any]] = []
    section_duration = target_duration_seconds / num_sections

    # Sequence of archetypes for engaging narrative progression
    progression = [
        (RetentionBeatType.HOOK, "Capture initial curiosity and set stakes"),
        (RetentionBeatType.OPEN_LOOP, "Introduce core question or central mystery"),
        (RetentionBeatType.PATTERN_INTERRUPT, "Shift perspective with contrasting evidence"),
        (RetentionBeatType.REVEAL, "Deliver primary empirical takeaway or finding"),
        (RetentionBeatType.PAYOFF, "Synthesize actionable resolution and value"),
        (RetentionBeatType.RECAP, "Summarize key lessons learned"),
        (RetentionBeatType.CTA, "Direct viewer to next steps or subscription"),
    ]

    for i in range(num_sections):
        est_sec = int((i + 0.5) * section_duration)
        archetype, purpose = progression[i % len(progression)]
        beats.append(
            {
                "timestamp_estimate_seconds": est_sec,
                "beat_type": archetype.value,
                "purpose": purpose,
                "text_hint": f"Section {i + 1} narrative beat",
            }
        )

    return beats
