from types import SimpleNamespace

from omega.application.storyboard_engine import StoryboardEngine, VisualStrategy
from omega.application.subtitle_engine import generate_karaoke_cues
from omega.application.visual_production_v2_service import ScriptStoryboardAdapter


def test_long_form_fixture_contract():
    engine = StoryboardEngine()

    script_version_like = SimpleNamespace(
        title="Long Form Canary Fixture",
        estimated_duration_seconds=60.0,
        sections=[
            SimpleNamespace(
                section_order=1,
                heading="Intro Hook",
                narration_text="Welcome to this video where we discuss very interesting things about everything in the world today.",
                estimated_duration_seconds=10.0,
                statements=[
                    SimpleNamespace(
                        statement_order=1,
                        statement_text="Welcome to this video where we discuss very interesting things about everything in the world today.",
                        statement_type="NARRATION",
                        citations=[],
                    )
                ]
            ),
            SimpleNamespace(
                section_order=2,
                heading="Broll Section",
                narration_text="The environment is full of action and motion as people walk around the city building today.",
                estimated_duration_seconds=10.0,
                statements=[
                    SimpleNamespace(
                        statement_order=2,
                        statement_text="The environment is full of action and motion as people walk around the city building today.",
                        statement_type="NARRATION",
                        citations=[],
                    )
                ]
            ),
            SimpleNamespace(
                section_order=3,
                heading="Image Section",
                narration_text="A completely normal statement that has enough words to avoid kinetic text and should trigger images.",
                estimated_duration_seconds=10.0,
                statements=[
                    SimpleNamespace(
                        statement_order=3,
                        statement_text="A completely normal statement that has enough words to avoid kinetic text and should trigger images.",
                        statement_type="NARRATION",
                        citations=[],
                    )
                ]
            ),
            SimpleNamespace(
                section_order=4,
                heading="Diagram Section",
                narration_text="The system architecture and workflow pipeline consists of many different components processing the various data stages.",
                estimated_duration_seconds=10.0,
                statements=[
                    SimpleNamespace(
                        statement_order=4,
                        statement_text="The system architecture and workflow pipeline consists of many different components processing the various data stages.",
                        statement_type="NARRATION",
                        citations=[],
                    )
                ]
            ),
            SimpleNamespace(
                section_order=5,
                heading="Infographic Section",
                narration_text="A quick overview and comparison of the tradeoff between multiple multi-factor options in our summary.",
                estimated_duration_seconds=10.0,
                statements=[
                    SimpleNamespace(
                        statement_order=5,
                        statement_text="A quick overview and comparison of the tradeoff between multiple multi-factor options in our summary.",
                        statement_type="NARRATION",
                        citations=[],
                    )
                ]
            ),
            SimpleNamespace(
                section_order=6,
                heading="CTA Section",
                narration_text="Thank you for watching please subscribe to the channel and leave a comment below today.",
                estimated_duration_seconds=10.0,
                statements=[
                    SimpleNamespace(
                        statement_order=6,
                        statement_text="Thank you for watching please subscribe to the channel and leave a comment below today.",
                        statement_type="NARRATION",
                        citations=[],
                    )
                ]
            ),
        ]
    )

    script_dict = ScriptStoryboardAdapter.to_script_dict(script_version_like)
    plan = engine.generate_storyboard(script_dict)

    assert len(plan.scenes) == 6
    for i, s in enumerate(plan.scenes):
        assert s.sequence_index == i + 1
        assert s.narration_excerpt
        assert s.estimated_duration_seconds > 0

    assert plan.scenes[0].visual_strategy == VisualStrategy.TITLE_MOTION
    assert plan.scenes[-1].visual_strategy == VisualStrategy.CTA

    headings = [s.section_id for s in plan.scenes]
    assert headings == [
        "Intro Hook",
        "Broll Section",
        "Image Section",
        "Diagram Section",
        "Infographic Section",
        "CTA Section"
    ]

    strategies = [s.visual_strategy for s in plan.scenes]
    assert strategies.count(VisualStrategy.TITLE_MOTION) == 1
    assert strategies.count(VisualStrategy.CTA) == 1
    assert strategies.count(VisualStrategy.BROLL) == 1
    assert strategies.count(VisualStrategy.IMAGE) == 1
    assert strategies.count(VisualStrategy.DIAGRAM) == 1
    assert strategies.count(VisualStrategy.INFOGRAPHIC) == 1

    # EXPECTED_GEMINI_CALLS = storyboard scene count
    assert len(plan.scenes) == 6

    # MAX_EXTERNAL_VISUAL_SCENES = 2 (BROLL + IMAGE)
    external_visuals = [s for s in plan.scenes if s.visual_strategy in (VisualStrategy.BROLL, VisualStrategy.IMAGE)]
    assert len(external_visuals) == 2


def test_subtitle_scale_contract():
    segments = [
        {"text": "Welcome to this video where we discuss very interesting things about everything in the world today.", "start_ms": 0, "duration_ms": 10000},
        {"text": "The environment is full of action and motion as people walk around the city building today.", "start_ms": 10000, "duration_ms": 10000},
        {"text": "A completely normal statement that has enough words to avoid kinetic text and should trigger images.", "start_ms": 20000, "duration_ms": 10000},
        {"text": "The system architecture and workflow pipeline consists of many different components processing the various data stages.", "start_ms": 30000, "duration_ms": 10000},
        {"text": "A quick overview and comparison of the tradeoff between multiple multi-factor options in our summary.", "start_ms": 40000, "duration_ms": 10000},
        {"text": "Thank you for watching please subscribe to the channel and leave a comment below today.", "start_ms": 50000, "duration_ms": 10000}
    ]

    cues = generate_karaoke_cues(segments, max_words_per_cue=5, max_chars_per_cue=36)
    assert len(cues) > 6

    for i in range(len(cues)):
        assert cues[i]["start_ms"] < cues[i]["end_ms"]
        if i > 0:
            assert cues[i]["start_ms"] >= cues[i-1]["end_ms"]
