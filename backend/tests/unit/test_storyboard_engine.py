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
