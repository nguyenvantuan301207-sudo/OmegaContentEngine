from omega.application.content_provider import TemplateContentProvider
from omega.application.storyboard_engine import StoryboardEngine, VisualStrategy


def test_storyboard_engine_longform():
    provider = TemplateContentProvider()
    intent = provider.generate_intent("Understanding the Global Logistics Supply Chain", None, {}, {})
    hooks = provider.generate_hooks("Understanding the Global Logistics Supply Chain", {}, {}, intent)
    outline = provider.generate_outline("Understanding the Global Logistics Supply Chain", {}, {}, intent, hooks[0], 450)
    script = provider.generate_script("Understanding the Global Logistics Supply Chain", {}, {}, intent, hooks[0], outline, 450)

    engine = StoryboardEngine()
    plan = engine.generate_storyboard(script)

    assert plan.estimated_duration_seconds == script.get("estimated_duration_seconds")
    assert len(plan.scenes) > 15
    assert len(plan.scenes) <= 55

    # Check opening and ending
    assert plan.scenes[0].visual_strategy == VisualStrategy.TITLE_MOTION
    assert plan.scenes[-1].visual_strategy == VisualStrategy.CTA

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
    assert (VisualStrategy.STATISTIC in strategies or VisualStrategy.INFOGRAPHIC in strategies)

    # Check PRESENTER does not exist
    assert all(s != "PRESENTER" for s in strategies)

    # Sanity threshold for CODE_DEMO
    code_demo_count = strategies.count(VisualStrategy.CODE_DEMO)
    assert code_demo_count / len(strategies) <= 0.35, "CODE_DEMO dominates the storyboard (>35%)"


def test_storyboard_engine_routing_v2():
    engine = StoryboardEngine()

    def check_strat(narration: str, expected: VisualStrategy, word_count: int = 20, is_first: bool = False, is_last: bool = False):
        strat = engine._select_strategy(narration, word_count, is_first, is_last)
        assert strat == expected, f"Expected {expected} for '{narration}', got {strat}"

    # A. Code signal
    check_strat("Here is a python code snippet showing the syntax.", VisualStrategy.CODE_DEMO)
    check_strat("We define a function.", VisualStrategy.CODE_DEMO)

    # B. Architecture signal
    check_strat("The system architecture defines the pipeline stages.", VisualStrategy.DIAGRAM)

    # C. Strong statistics
    check_strat("We saw a 50% increase in throughput.", VisualStrategy.STATISTIC)
    check_strat("Latency dropped to 120ms.", VisualStrategy.STATISTIC)

    # D. False positive guard (code mentioned loosely)
    check_strat("The moral code of the story is interesting.", VisualStrategy.IMAGE)

    # E. KINETIC_TEXT fallback for short punchy text
    check_strat("Short text.", VisualStrategy.KINETIC_TEXT, word_count=2)

    # F. CTA for last scene
    check_strat("Subscribe now.", VisualStrategy.CTA, is_last=True)

    # G. TITLE_MOTION for first scene
    check_strat("Welcome.", VisualStrategy.TITLE_MOTION, is_first=True)

    # H. BROLL routing
    check_strat("In the real world, this looks beautiful.", VisualStrategy.BROLL)
