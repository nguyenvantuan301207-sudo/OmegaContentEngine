import pytest

from omega.application.content_provider import TemplateContentProvider
from omega.application.storyboard_engine import (
    StoryboardEngine,
    VisualStrategy,
    extract_trustworthy_metric,
)


def test_storyboard_engine_longform():
    provider = TemplateContentProvider()
    topic = "Understanding the Global Logistics Supply Chain"
    verified_claims = [
        {
            "claim_id": f"c-00{i}",
            "text": f"Global supply chain logistics mechanism {i} requires verified tracking of cargo vessels across international freight lanes.",
            "citations": [{"evidence_id": f"ev-{i}", "source_id": f"src-{i}"}],
        }
        for i in range(1, 35)
    ]
    verified_claims[5]["text"] = "In this logistics pipeline, port congestion causes container dwell times to exceed operational limits."
    brief = {
        "id": "brief-logistics",
        "title": topic,
        "summary": "Verified logistics supply chain dynamics.",
        "verified_claims": verified_claims,
        "uncertain_claims": [],
        "contradictions": [],
    }
    dna = {"brand_voice": {"tone": "AUTHORITATIVE", "pace": "MODERATE"}}
    intent = provider.generate_intent(topic, None, brief, dna)
    hook = provider.generate_hooks(topic, brief, dna, intent)[0]
    sections = []
    for idx in range(6):
        c_slice = verified_claims[idx * 5 : (idx + 1) * 5]
        sections.append({
            "section_id": f"sec_{idx + 1}",
            "title": f"Section {idx + 1} on Supply Chain Operations",
            "key_points": [],
            "claim_refs": [c["claim_id"] for c in c_slice],
            "estimated_duration_seconds": 75,
        })
    outline = {"sections": sections, "narrative_plan_id": "np-1", "title": topic}
    script = provider.generate_script(topic, brief, dna, intent, hook, outline, 450)
    script["estimated_duration_seconds"] = 450

    # Inject actual code into one statement so longform storyboard exercises CODE_DEMO
    script["sections"][2]["statements"][0]["statement_text"] = "def optimize_pipeline(): return 42"

    engine = StoryboardEngine()
    plan = engine.generate_storyboard(script)

    assert plan.estimated_duration_seconds == script.get("estimated_duration_seconds")
    assert len(plan.scenes) > 15
    assert len(plan.scenes) <= 55

    # Check opening and ending
    assert plan.scenes[0].visual_strategy == VisualStrategy.TITLE_MOTION
    # Documentary script without explicit CTA statements must NOT default to CTA
    assert plan.scenes[-1].visual_strategy != VisualStrategy.CTA

    strategies = [s.visual_strategy for s in plan.scenes]

    # Check consecutive strategies
    for i in range(len(strategies) - 2):
        assert not (strategies[i] == strategies[i+1] == strategies[i+2]), f"Too many consecutive {strategies[i]}"

    static_cards = {VisualStrategy.DIAGRAM, VisualStrategy.INFOGRAPHIC, VisualStrategy.STATISTIC, VisualStrategy.KINETIC_TEXT}
    for i in range(len(strategies) - 2):
        is_s1 = strategies[i] in static_cards
        is_s2 = strategies[i+1] in static_cards
        is_s3 = strategies[i+2] in static_cards
        assert not (is_s1 and is_s2 and is_s3), "Too many consecutive static cards"

    assert VisualStrategy.DIAGRAM in strategies
    assert VisualStrategy.CODE_DEMO in strategies
    for scene in plan.scenes:
        if scene.visual_strategy == VisualStrategy.STATISTIC:
            assert extract_trustworthy_metric(scene.narration_excerpt) is not None

    # Check PRESENTER does not exist
    assert all(s != "PRESENTER" for s in strategies)

    # Sanity threshold for CODE_DEMO
    code_demo_count = strategies.count(VisualStrategy.CODE_DEMO)
    assert code_demo_count / len(strategies) <= 0.35, "CODE_DEMO dominates the storyboard (>35%)"


def test_storyboard_engine_routing_v2():
    engine = StoryboardEngine()

    def check_strat(
        narration: str,
        expected: VisualStrategy,
        word_count: int = 20,
        is_first: bool = False,
        is_last: bool = False,
        statements: list[dict] | None = None,
    ):
        strat = engine._select_strategy(
            narration, word_count, is_first, is_last, statements=statements
        )
        assert strat == expected, f"Expected {expected} for '{narration}', got {strat}"

    # A. Code signal
    check_strat("def calculate_latency(): return 42", VisualStrategy.CODE_DEMO)
    check_strat("function run() { return 42; }", VisualStrategy.CODE_DEMO)
    check_strat("SELECT id FROM jobs", VisualStrategy.CODE_DEMO)
    check_strat("```python\ndef run():\n    return 42\n```", VisualStrategy.CODE_DEMO)

    # A1. Prose must NOT route to CODE_DEMO
    check_strat("Here is a python code snippet showing the syntax.", VisualStrategy.IMAGE)
    check_strat("We define a function.", VisualStrategy.BROLL)

    # B. Architecture signal
    check_strat("In this pipeline, high ingress traffic causes queue overflow across consumer nodes.", VisualStrategy.DIAGRAM)
    check_strat("The system architecture defines the pipeline stages.", VisualStrategy.IMAGE)

    # C. Strong statistics
    check_strat("We saw a 50% increase in throughput.", VisualStrategy.STATISTIC)
    check_strat("Latency dropped to 120ms.", VisualStrategy.STATISTIC)

    # D. False positive guard (code mentioned loosely)
    check_strat("The moral code of the story is interesting.", VisualStrategy.IMAGE)

    # E. KINETIC_TEXT fallback for short punchy text
    check_strat("Short text.", VisualStrategy.KINETIC_TEXT, word_count=2)

    # F. CTA only when explicit CTA statement exists
    check_strat(
        "Subscribe now.",
        VisualStrategy.CTA,
        is_last=True,
        statements=[{"statement_type": "CTA", "statement_text": "Subscribe now."}],
    )
    # Closing-only without CTA statement passes to normal visual strategy (not CTA)
    check_strat(
        "In the real world, this looks beautiful.",
        VisualStrategy.BROLL,
        is_last=True,
        statements=[{"statement_type": "CLOSING", "statement_text": "In the real world, this looks beautiful."}],
    )

    # G. TITLE_MOTION for first scene
    check_strat("Welcome.", VisualStrategy.TITLE_MOTION, is_first=True)

    # H. BROLL routing
    check_strat("In the real world, this looks beautiful.", VisualStrategy.BROLL)

    # I. INFOGRAPHIC routing
    check_strat("A comparison of the available choices.", VisualStrategy.INFOGRAPHIC)


def test_storyboard_preserves_full_on_screen_text_without_silent_ellipsis():
    narration = "A script enters OMEGA, where deterministic planning preserves every source word."
    scene = StoryboardEngine()._create_scene(
        sequence_index=1,
        section_heading="Render truth",
        statements=[{"statement_order": 1, "statement_text": narration}],
        is_first=True,
        is_last=False,
        history=[],
    )

    assert scene.on_screen_text == narration
    assert not scene.on_screen_text.endswith("...")


@pytest.mark.parametrize(
    "narration",
    [
        "The growth rate remained stable during the observation window.",
        "The report discusses metrics, growth, and latency without quantitative results.",
        (
            "In this section, we explore architectural deep dive. Building resilient "
            "systems around Why Leaves Change Color in Autumn requires balancing "
            "throughput, isolation, and maintainability."
        ),
    ],
)
def test_statistic_routing_requires_a_trustworthy_numeric_metric(narration):
    strategy = StoryboardEngine()._select_strategy(
        narration,
        word_count=len(narration.split()),
        is_first=False,
        is_last=False,
    )

    assert strategy != VisualStrategy.STATISTIC


@pytest.mark.parametrize("narration", ["50% faster", "120ms latency", "2x growth", "3.5s runtime"])
def test_statistic_routing_accepts_resolver_compatible_metrics(narration):
    strategy = StoryboardEngine()._select_strategy(
        narration,
        word_count=len(narration.split()),
        is_first=False,
        is_last=False,
    )

    assert strategy == VisualStrategy.STATISTIC


@pytest.mark.parametrize(
    "prose",
    [
        "Implementation details are important for resilient systems.",
        "A command strategy can coordinate distributed workers.",
        "The syntax of the architecture is discussed conceptually.",
        "Here is a code snippet concept without literal source code.",
        "A function of the system is to isolate failures.",
        "A system class defines a category of workloads.",
        (
            "We will examine the core event loop mechanics, review syntax patterns, "
            "analyze benchmark results, and construct a robust production deployment checklist."
        ),
        (
            "For Common architectural bottlenecks and failure modes, this chapter "
            "examines a concrete implementation example and its design rationale."
        ),
        "Now let us examine concrete production implementation patterns that maximize reliability.",
    ],
)
def test_code_demo_routing_rejects_pure_prose(prose):
    strategy = StoryboardEngine()._select_strategy(
        prose,
        word_count=len(prose.split()),
        is_first=False,
        is_last=False,
    )
    assert strategy != VisualStrategy.CODE_DEMO


@pytest.mark.parametrize(
    "code_sample",
    [
        "def calculate_latency(): return 42",
        "function run() { return 42; }",
        "SELECT id FROM jobs",
        "```python\ndef run():\n    return 42\n```",
    ],
)
def test_code_demo_routing_accepts_trustworthy_code(code_sample):
    strategy = StoryboardEngine()._select_strategy(
        code_sample,
        word_count=len(code_sample.split()),
        is_first=False,
        is_last=False,
    )
    assert strategy == VisualStrategy.CODE_DEMO
