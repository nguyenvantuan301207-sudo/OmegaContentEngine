"""Unit tests for long-form educational content generation."""

import pytest

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

    # Duration growth cannot manufacture depth from one verified sentence.
    outlines = []
    for duration in (480, 840, 1320):
        intent = provider.generate_intent(topic, None, brief, dna)
        hook = provider.generate_hooks(topic, brief, dna, intent)[0]
        outline = provider.generate_outline(topic, brief, dna, intent, hook, duration)
        outlines.append(outline)
        with pytest.raises(ValueError, match="INSUFFICIENT_GROUNDED_SCRIPT_CONTENT"):
            provider.generate_script(topic, brief, dna, intent, hook, outline, duration)
    assert len(outlines[0]["sections"]) < len(outlines[1]["sections"]) < len(outlines[2]["sections"])
