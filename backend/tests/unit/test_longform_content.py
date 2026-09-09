"""Unit tests for long-form educational content generation."""

import pytest

from omega.application.content_pacing import estimate_duration_seconds
from omega.application.content_provider import TemplateContentProvider
from omega.domain.content import ContentGenerationRequestCreate, ContentType


def _generate(provider, topic, brief, dna, duration):
    intent = provider.generate_intent(topic, None, brief, dna)
    selected_hook = provider.generate_hooks(topic, brief, dna, intent)[0]
    outline = provider.generate_outline(topic, brief, dna, intent, selected_hook, duration)
    script = provider.generate_script(topic, brief, dna, intent, selected_hook, outline, duration)
    return outline, script


@pytest.mark.parametrize("duration", [480, 720, 840, 960, 1320])
def test_long_form_runtime_hard_bounds_accept_supported_targets(duration):
    request = ContentGenerationRequestCreate(
        topic_candidate_id="00000000-0000-0000-0000-000000000001",
        research_brief_id="00000000-0000-0000-0000-000000000002",
        target_duration_seconds=duration,
    )
    assert request.target_duration_seconds == duration


@pytest.mark.parametrize("duration", [479, 1321])
def test_long_form_runtime_outside_hard_bounds_fails_closed(duration):
    with pytest.raises(ValueError, match="between 480 and 1320"):
        ContentGenerationRequestCreate(
            topic_candidate_id="00000000-0000-0000-0000-000000000001",
            research_brief_id="00000000-0000-0000-0000-000000000002",
            target_duration_seconds=duration,
        )


def test_short_form_runtime_semantics_remain_format_specific():
    request = ContentGenerationRequestCreate(
        topic_candidate_id="00000000-0000-0000-0000-000000000001",
        research_brief_id="00000000-0000-0000-0000-000000000002",
        content_type=ContentType.YOUTUBE_SHORT,
        target_duration_seconds=60,
    )
    assert request.target_duration_seconds == 60


def test_longform_script_generation_duration_and_depth():
    provider = TemplateContentProvider()
    topic = "FastAPI Microservices Architecture & High-Throughput Benchmarks"
    brief = {
        "id": "brief-123",
        "title": "FastAPI Benchmarks",
        "summary": "Verified FastAPI performance characteristics.",
        "verified_claims": [
            {
                "claim_id": "c-001",
                "text": "FastAPI handles over 50,000 requests per second under ASGI multiprocessing.",
                "citations": [{"evidence_id": "ev-01", "source_id": "src-01"}],
            }
        ],
        "uncertain_claims": [],
        "contradictions": [],
    }
    dna = {"brand_voice": {"tone": "AUTHORITATIVE", "pace": "MODERATE"}}

    outline_480, script_480 = _generate(provider, topic, brief, dna, 480)
    outline_840, script_840 = _generate(provider, topic, brief, dna, 840)
    outline_1320, script_1320 = _generate(provider, topic, brief, dna, 1320)

    assert len(outline_480["sections"]) < len(outline_840["sections"])
    assert len(outline_840["sections"]) < len(outline_1320["sections"])
    assert len(outline_480["sections"]) != len(outline_1320["sections"])
    assert len(outline_1320["sections"]) > len(outline_480["sections"])
    assert script_480["estimated_word_count"] < script_840["estimated_word_count"]
    assert script_840["estimated_word_count"] < script_1320["estimated_word_count"]
    assert script_840["estimated_duration_seconds"] == estimate_duration_seconds(
        script_840["estimated_word_count"], "MODERATE"
    )

    paragraphs = [section["narration_text"] for section in script_1320["sections"]]
    assert len(paragraphs) == len(set(paragraphs))
