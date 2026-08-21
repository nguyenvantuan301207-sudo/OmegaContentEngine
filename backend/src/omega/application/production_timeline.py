"""Production Timeline manager and timeline boundary validation."""

from __future__ import annotations

from typing import Any

# Tolerances in integer milliseconds
MAX_PERMITTED_NARRATION_GAP_MS = 1000
MAX_PERMITTED_SCENE_GAP_MS = 500
SUBTITLE_BOUND_TOLERANCE_MS = 500


class TimelineValidationError(ValueError):
    """Raised when timeline validation detects gaps, overlaps, or negative bounds."""

    pass


def align_production_timeline(
    scenes: list[dict[str, Any]],
    inter_scene_pause_ms: int = 200,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Calculate contiguous timeline timestamps for narration segments and subtitle cues.

    Returns:
        (narration_segments, subtitle_cues, total_duration_ms)
    """
    narration_segments: list[dict[str, Any]] = []
    subtitle_cues: list[dict[str, Any]] = []
    current_cursor_ms = 0
    cue_order = 1

    for scene in scenes:
        scene_id = scene["id"]
        text = str(scene.get("narration_text", "")).strip()
        duration_ms = int(scene.get("estimated_duration_ms", 0))

        start_ms = current_cursor_ms
        end_ms = start_ms + duration_ms

        # Narration segment
        narration_segments.append(
            {
                "scene_id": scene_id,
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": duration_ms,
            }
        )

        # Subtitle cue
        if text:
            subtitle_cues.append(
                {
                    "scene_id": scene_id,
                    "cue_order": cue_order,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                }
            )
            cue_order += 1

        current_cursor_ms = end_ms + inter_scene_pause_ms

    total_duration_ms = max(current_cursor_ms - inter_scene_pause_ms, 0)
    return narration_segments, subtitle_cues, total_duration_ms


def validate_timeline_integrity(
    narration_segments: list[dict[str, Any]],
    subtitle_cues: list[dict[str, Any]],
    total_duration_ms: int,
) -> list[dict[str, Any]]:
    """Validate timeline continuity, detecting gaps, overlaps, and boundary violations.

    Returns a list of finding dicts: {"code": str, "message": str, "severity": str}.
    """
    findings: list[dict[str, Any]] = []

    # 1. Narration Segment Checks
    for i in range(len(narration_segments)):
        curr = narration_segments[i]
        c_start = int(curr.get("start_ms", 0))
        c_end = int(curr.get("end_ms", 0))

        if c_start < 0:
            findings.append(
                {
                    "code": "TIMELINE_OVERLAP",
                    "severity": "BLOCKING",
                    "message": f"Negative start time {c_start}ms on narration segment {i + 1}.",
                }
            )
        if c_end <= c_start:
            findings.append(
                {
                    "code": "TIMELINE_OVERLAP",
                    "severity": "BLOCKING",
                    "message": f"Non-positive duration {c_end - c_start}ms on narration segment {i + 1}.",
                }
            )

        if i > 0:
            prev = narration_segments[i - 1]
            p_end = int(prev.get("end_ms", 0))

            if c_start < p_end:
                findings.append(
                    {
                        "code": "TIMELINE_OVERLAP",
                        "severity": "BLOCKING",
                        "message": f"Narration segment {i + 1} starts at {c_start}ms before previous segment ended at {p_end}ms.",
                    }
                )
            elif (c_start - p_end) > MAX_PERMITTED_NARRATION_GAP_MS:
                findings.append(
                    {
                        "code": "TIMELINE_GAP",
                        "severity": "BLOCKING",
                        "message": f"Unexplained timeline gap of {c_start - p_end}ms between narration segments {i} and {i + 1}.",
                    }
                )

    # 2. Subtitle Cue Checks
    for i in range(len(subtitle_cues)):
        cue = subtitle_cues[i]
        c_start = int(cue.get("start_ms", 0))
        c_end = int(cue.get("end_ms", 0))
        text = str(cue.get("text", "")).strip()

        if not text:
            findings.append(
                {
                    "code": "SUBTITLE_EMPTY",
                    "severity": "WARNING",
                    "message": f"Subtitle cue {cue.get('cue_order', i + 1)} has empty text.",
                }
            )

        if c_end > (total_duration_ms + SUBTITLE_BOUND_TOLERANCE_MS):
            findings.append(
                {
                    "code": "SUBTITLE_OUT_OF_RANGE",
                    "severity": "WARNING",
                    "message": f"Subtitle cue {cue.get('cue_order', i + 1)} ends at {c_end}ms, exceeding total duration {total_duration_ms}ms.",
                }
            )

        if i > 0:
            prev = subtitle_cues[i - 1]
            p_end = int(prev.get("end_ms", 0))
            if c_start < p_end:
                findings.append(
                    {
                        "code": "TIMELINE_OVERLAP",
                        "severity": "BLOCKING",
                        "message": f"Subtitle cue {cue.get('cue_order', i + 1)} overlaps previous cue by {p_end - c_start}ms.",
                    }
                )

    return findings
