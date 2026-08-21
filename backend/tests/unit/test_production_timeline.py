"""Unit tests for Production Timeline alignment and exact tolerance checks."""

import uuid

from omega.application.production_timeline import (
    align_production_timeline,
    validate_timeline_integrity,
)


def test_align_production_timeline():
    """Verify monotonic timestamp assignment and subtitle cue generation."""
    s1_id = uuid.uuid4()
    s2_id = uuid.uuid4()
    scenes = [
        {"id": s1_id, "narration_text": "First scene narration", "estimated_duration_ms": 3000},
        {"id": s2_id, "narration_text": "Second scene narration", "estimated_duration_ms": 4000},
    ]

    narr_segs, cues, total_dur = align_production_timeline(scenes, inter_scene_pause_ms=200)

    assert len(narr_segs) == 2
    assert len(cues) == 2

    # Seg 1: 0 -> 3000
    assert narr_segs[0]["start_ms"] == 0
    assert narr_segs[0]["end_ms"] == 3000
    assert narr_segs[0]["duration_ms"] == 3000

    # Seg 2: 3200 -> 7200 (with 200ms pause)
    assert narr_segs[1]["start_ms"] == 3200
    assert narr_segs[1]["end_ms"] == 7200
    assert narr_segs[1]["duration_ms"] == 4000

    assert total_dur == 7200


def test_validate_timeline_integrity_clean():
    """Verify clean timeline has zero findings."""
    narr = [
        {"start_ms": 0, "end_ms": 3000},
        {"start_ms": 3200, "end_ms": 6000},
    ]
    cues = [
        {"cue_order": 1, "start_ms": 0, "end_ms": 3000, "text": "Hello"},
        {"cue_order": 2, "start_ms": 3200, "end_ms": 6000, "text": "World"},
    ]
    findings = validate_timeline_integrity(narr, cues, total_duration_ms=6000)
    assert len(findings) == 0


def test_validate_timeline_overlap_detected():
    """Verify narration overlap > 0ms is flagged as BLOCKING TIMELINE_OVERLAP."""
    narr = [
        {"start_ms": 0, "end_ms": 3000},
        {"start_ms": 2500, "end_ms": 6000},  # Overlaps previous by 500ms
    ]
    cues = []
    findings = validate_timeline_integrity(narr, cues, total_duration_ms=6000)
    assert any(f["code"] == "TIMELINE_OVERLAP" and f["severity"] == "BLOCKING" for f in findings)


def test_validate_timeline_gap_detected():
    """Verify narration gap > 1000ms is flagged as BLOCKING TIMELINE_GAP."""
    narr = [
        {"start_ms": 0, "end_ms": 2000},
        {"start_ms": 3500, "end_ms": 6000},  # Gap of 1500ms > 1000ms tolerance
    ]
    cues = []
    findings = validate_timeline_integrity(narr, cues, total_duration_ms=6000)
    assert any(f["code"] == "TIMELINE_GAP" and f["severity"] == "BLOCKING" for f in findings)
