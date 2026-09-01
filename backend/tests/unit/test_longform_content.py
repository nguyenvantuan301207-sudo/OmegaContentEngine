"""Unit tests for long-form educational content generation."""

from omega.application.content_provider import TemplateContentProvider


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

    intent = provider.generate_intent(topic, None, brief, dna)
    hooks = provider.generate_hooks(topic, brief, dna, intent)
    selected_hook = hooks[0]

    outline = provider.generate_outline(topic, brief, dna, intent, selected_hook, target_duration_seconds=450)
    assert len(outline["sections"]) == 6

    script = provider.generate_script(topic, brief, dna, intent, selected_hook, outline, target_duration_seconds=450)

    # Verify duration and word count constraints
    assert 950 <= script["estimated_word_count"] <= 1250, f"Actual words: {script['estimated_word_count']}"
    assert 400 <= script["estimated_duration_seconds"] <= 500, f"Actual duration: {script['estimated_duration_seconds']}"

    # Verify 6 structured sections with 5-7 statements each
    assert len(script["sections"]) == 6
    total_statements = sum(len(s["statements"]) for s in script["sections"])
    assert total_statements >= 34, f"Total statements: {total_statements}"

    # Verify section headings match explainer structure
    headings = [s["heading"] for s in script["sections"]]
    assert any("Problem Context" in h or "Stakes" in h for h in headings)
    assert any("Mechanics" in h or "Async" in h for h in headings)
    assert any("Code" in h or "Implementation" in h for h in headings)
    assert any("Benchmark" in h or "Evidence" in h for h in headings)
    assert any("Trade-offs" in h or "Resilience" in h for h in headings)
    assert any("Synthesis" in h or "Recap" in h for h in headings)
