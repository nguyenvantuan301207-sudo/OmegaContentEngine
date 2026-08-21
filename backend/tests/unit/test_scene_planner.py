"""Unit tests for Scene Planner in OMEGA-007 Production Engine."""

import uuid

from omega.application.scene_planner import estimate_narration_ms, plan_scenes_from_script
from omega.domain.production import SceneType


def test_estimate_narration_ms():
    """Verify word-count-based duration estimation with bounds."""
    # 0 words -> 0 ms
    assert estimate_narration_ms("") == 0
    assert estimate_narration_ms("   ") == 0

    # Few words clamped to MIN_SCENE_DURATION_MS (2500ms)
    assert estimate_narration_ms("Hello world") == 2500

    # Normal sentence (10 words -> ~4300ms)
    ten_words = "One two three four five six seven eight nine ten"
    assert estimate_narration_ms(ten_words) == 4300

    # Very long text clamped to MAX_SCENE_DURATION_MS (12000ms)
    long_text = "word " * 50
    assert estimate_narration_ms(long_text) == 12000


def test_plan_scenes_from_script():
    """Verify deterministic scene segmentation and scene type classification."""
    req_id = uuid.uuid4()
    script_data = {
        "title": "Quantum Computing Breakthrough",
        "hook_text": "Scientists just achieved quantum supremacy with 100 qubits!",
        "closing_text": "That wraps up our deep dive into quantum supremacy.",
        "cta_text": "Subscribe for more breakthrough science updates!",
        "sections": [
            {
                "id": str(uuid.uuid4()),
                "section_order": 1,
                "heading": "The Quantum Race",
                "statements": [
                    {
                        "id": str(uuid.uuid4()),
                        "statement_order": 1,
                        "statement_text": 'Dr. Smith stated: "This changes computational speed forever."',
                        "statement_type": "ATTRIBUTED",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "statement_order": 2,
                        "statement_text": "The new processor completed calculations in 200 seconds that would take classical supercomputers 10000 years.",
                        "statement_type": "FACTUAL",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "statement_order": 3,
                        "statement_text": "We are stepping into a radically new era of technology.",
                        "statement_type": "CREATIVE",
                    },
                ],
            }
        ],
    }

    scenes = plan_scenes_from_script(req_id, script_data)

    assert len(scenes) >= 4  # Hook + 3 statements + closing CTA
    assert scenes[0]["scene_type"] == SceneType.TITLE.value
    assert scenes[0]["narration_text"] == script_data["hook_text"]
    assert len(scenes[0]["asset_requirements"]) == 1

    # Statement 1 is quote
    assert scenes[1]["scene_type"] == SceneType.QUOTE.value

    # Statement 2 has numbers -> statistic
    assert scenes[2]["scene_type"] == SceneType.STATISTIC.value

    # Statement 3 is narration
    assert scenes[3]["scene_type"] == SceneType.NARRATION.value

    # All scenes have positive estimated duration
    for sc in scenes:
        assert sc["estimated_duration_ms"] > 0
        assert sc["production_request_id"] == req_id
