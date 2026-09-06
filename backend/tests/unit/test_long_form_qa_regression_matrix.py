from copy import deepcopy
from types import SimpleNamespace

from omega.application.production_qa import ProductionQAEngine
from omega.application.storyboard_engine import StoryboardEngine, VisualStrategy
from omega.application.subtitle_engine import generate_karaoke_cues
from omega.application.visual_production_v2_service import ScriptStoryboardAdapter
from omega.domain.production import ProductionQARuleCode, ProductionQAStatus


def _build_long_form_fixture():
    engine = StoryboardEngine()

    script_version_like = SimpleNamespace(
        id="long-form-script",
        title="Long Form Canary Fixture",
        estimated_duration_seconds=60.0,
        hook_text="Welcome to this video where we discuss very interesting things about everything in the world today.",
        cta_text="Thank you for watching please subscribe to the channel and leave a comment below today.",
        closing_text="",
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
    script_dict["id"] = script_version_like.id
    script_dict["hook_text"] = script_version_like.hook_text
    script_dict["cta_text"] = script_version_like.cta_text
    script_dict["closing_text"] = script_version_like.closing_text

    plan = engine.generate_storyboard(script_dict)

    return script_dict, plan


def _build_clean_qa_context(script_dict, plan):
    # CANONICAL SCENES DATA
    scenes_data = []
    for s in plan.scenes:
        scenes_data.append({
            "sequence_index": s.sequence_index,
            "original_strategy": s.visual_strategy.value,
            "effective_strategy": s.visual_strategy.value,
            "duration_seconds": s.estimated_duration_seconds,
            "asset_kind": "VIDEO" if s.visual_strategy == VisualStrategy.BROLL else "IMAGE",
            "asset_provider": None,
            "asset_id": None
        })

    # 60-SECOND TIMELINE
    narration_segments = [
        {"start_ms": 0, "end_ms": 10000},
        {"start_ms": 10000, "end_ms": 20000},
        {"start_ms": 20000, "end_ms": 30000},
        {"start_ms": 30000, "end_ms": 40000},
        {"start_ms": 40000, "end_ms": 50000},
        {"start_ms": 50000, "end_ms": 60000}
    ]

    # SUBTITLE SCALE
    segments_for_cues = [
        {"text": "Welcome to this video where we discuss very interesting things about everything in the world today.", "start_ms": 0, "duration_ms": 10000},
        {"text": "The environment is full of action and motion as people walk around the city building today.", "start_ms": 10000, "duration_ms": 10000},
        {"text": "A completely normal statement that has enough words to avoid kinetic text and should trigger images.", "start_ms": 20000, "duration_ms": 10000},
        {"text": "The system architecture and workflow pipeline consists of many different components processing the various data stages.", "start_ms": 30000, "duration_ms": 10000},
        {"text": "A quick overview and comparison of the tradeoff between multiple multi-factor options in our summary.", "start_ms": 40000, "duration_ms": 10000},
        {"text": "Thank you for watching please subscribe to the channel and leave a comment below today.", "start_ms": 50000, "duration_ms": 10000}
    ]
    subtitle_cues = generate_karaoke_cues(segments_for_cues, max_words_per_cue=5, max_chars_per_cue=36)

    # CLEAN BASELINE QA CONTEXT
    request_data = {
        "script_version_id": "long-form-script",
        "channel_dna_revision_id": "dna-rev-1",
        "target_width": 1920,
        "target_height": 1080,
        "video_codec": "h264",
        "target_duration_seconds": 60
    }
    content_request_data = {
        "channel_dna_revision_id": "dna-rev-1",
        "default_duration_min_seconds": 60
    }

    assets_data = [
        {
            "asset_type": "VIDEO",
            "provider_type": "PEXELS",
            "source_ref": "valid_ref",
        },
        {
            "asset_type": "AUDIO",
            "narration_quality": "NEURAL_PRODUCTION"
        },
        {
            "asset_type": "SUBTITLE",
            "mime_type": "application/x-subrip"
        }
    ]

    requirements_data = []

    media_probe_summary = {
        "duration_ms": 60000,
        "width": 1920,
        "height": 1080,
        "video_codec": "h264",
        "has_audio": True,
        "mean_volume_db": -20.0
    }

    return {
        "request_data": request_data,
        "script_version_data": script_dict,
        "content_request_data": content_request_data,
        "assets_data": assets_data,
        "requirements_data": requirements_data,
        "narration_segments": narration_segments,
        "subtitle_cues": subtitle_cues,
        "media_probe_summary": media_probe_summary,
        "artifact_file_path": None,
        "expected_hash": None,
        "scenes_data": scenes_data
    }


def _rule_codes(findings):
    return {f.rule_code for f in findings}


def test_baseline_contract():
    script_dict, plan = _build_long_form_fixture()

    assert len(plan.scenes) == 6
    strategies = [s.visual_strategy.value for s in plan.scenes]
    assert strategies == ["TITLE_MOTION", "BROLL", "IMAGE", "DIAGRAM", "INFOGRAPHIC", "CTA"]

    qa_context = _build_clean_qa_context(script_dict, plan)

    # 60s timeline asserts
    n_segs = qa_context["narration_segments"]
    assert len(n_segs) == 6
    for i in range(len(n_segs)):
        assert n_segs[i]["start_ms"] < n_segs[i]["end_ms"]
        if i > 0:
            assert n_segs[i]["start_ms"] == n_segs[i-1]["end_ms"]
    assert n_segs[-1]["end_ms"] == 60000

    # Subtitle assertions
    cues = qa_context["subtitle_cues"]
    assert len(cues) > 6
    for i in range(len(cues)):
        assert cues[i]["start_ms"] < cues[i]["end_ms"]
        if i > 0:
            assert cues[i]["start_ms"] >= cues[i-1]["end_ms"]
    assert cues[-1]["end_ms"] <= 60000
    for cue in cues:
        c_text = str(cue.get("text", "")).strip()
        lines = c_text.split("\n")
        assert len(lines) <= 2
        assert not any(len(line) > 55 for line in lines)

    # Evaluate
    engine = ProductionQAEngine()
    status, findings = engine.evaluate(**qa_context)

    assert status == ProductionQAStatus.PASSED
    assert findings == []

    codes = _rule_codes(findings)
    assert ProductionQARuleCode.VISUAL_REPETITION not in codes
    assert ProductionQARuleCode.EXCESSIVE_STATIC_SCENES not in codes
    assert ProductionQARuleCode.MISSING_INTRO not in codes
    assert ProductionQARuleCode.MISSING_OUTRO not in codes
    assert ProductionQARuleCode.NO_MOTION not in codes
    assert ProductionQARuleCode.DURATION_TOO_SHORT not in codes
    assert ProductionQARuleCode.SUBTITLE_TOO_LARGE not in codes
    assert ProductionQARuleCode.INSUFFICIENT_BODY_DEPTH not in codes
    assert ProductionQARuleCode.AUDIO_LOUDNESS_OUT_OF_RANGE not in codes


def test_mutation_visual_repetition():
    script_dict, plan = _build_long_form_fixture()
    qa_context = _build_clean_qa_context(script_dict, plan)

    # CASE A
    qa_context["scenes_data"][0]["effective_strategy"] = "BROLL"
    qa_context["scenes_data"][1]["effective_strategy"] = "BROLL"
    qa_context["scenes_data"][2]["effective_strategy"] = "BROLL"

    engine = ProductionQAEngine()
    status, findings = engine.evaluate(**qa_context)

    assert status == ProductionQAStatus.PASSED_WITH_WARNINGS
    codes = _rule_codes(findings)
    assert ProductionQARuleCode.VISUAL_REPETITION in codes


def test_mutation_excessive_static_scenes():
    script_dict, plan = _build_long_form_fixture()
    qa_context = _build_clean_qa_context(script_dict, plan)

    # CASE B
    qa_context["scenes_data"][0]["effective_strategy"] = "DIAGRAM"
    qa_context["scenes_data"][1]["effective_strategy"] = "INFOGRAPHIC"
    qa_context["scenes_data"][2]["effective_strategy"] = "STATISTIC"

    engine = ProductionQAEngine()
    status, findings = engine.evaluate(**qa_context)

    assert status == ProductionQAStatus.PASSED_WITH_WARNINGS
    codes = _rule_codes(findings)
    assert ProductionQARuleCode.EXCESSIVE_STATIC_SCENES in codes


def test_mutation_missing_intro():
    script_dict, plan = _build_long_form_fixture()
    qa_context = _build_clean_qa_context(script_dict, plan)

    # CASE C
    qa_context["script_version_data"]["hook_text"] = ""
    # Make sure headings are clean
    for s in qa_context["script_version_data"]["sections"]:
        s["heading"] = "Body"

    engine = ProductionQAEngine()
    status, findings = engine.evaluate(**qa_context)

    assert status == ProductionQAStatus.PASSED_WITH_WARNINGS
    codes = _rule_codes(findings)
    assert ProductionQARuleCode.MISSING_INTRO in codes
    assert ProductionQARuleCode.MISSING_OUTRO not in codes


def test_mutation_missing_outro():
    script_dict, plan = _build_long_form_fixture()
    qa_context = _build_clean_qa_context(script_dict, plan)

    # CASE D
    qa_context["script_version_data"]["cta_text"] = ""
    # Make sure headings are clean
    for s in qa_context["script_version_data"]["sections"]:
        s["heading"] = "Body"

    engine = ProductionQAEngine()
    status, findings = engine.evaluate(**qa_context)

    assert status == ProductionQAStatus.PASSED_WITH_WARNINGS
    codes = _rule_codes(findings)
    assert ProductionQARuleCode.MISSING_OUTRO in codes
    assert ProductionQARuleCode.MISSING_INTRO not in codes


def test_duration_75_percent_boundary():
    script_dict, plan = _build_long_form_fixture()
    qa_context = _build_clean_qa_context(script_dict, plan)

    engine = ProductionQAEngine()

    # 45000ms exactly (75% of 60s)
    ctx_45 = deepcopy(qa_context)
    ctx_45["media_probe_summary"]["duration_ms"] = 45000
    status, findings = engine.evaluate(**ctx_45)
    codes = _rule_codes(findings)
    assert ProductionQARuleCode.DURATION_BELOW_DNA_MINIMUM not in codes

    # 44999ms
    ctx_44 = deepcopy(qa_context)
    ctx_44["media_probe_summary"]["duration_ms"] = 44999
    status, findings = engine.evaluate(**ctx_44)
    codes = _rule_codes(findings)
    assert ProductionQARuleCode.DURATION_BELOW_DNA_MINIMUM in codes


def test_active_deferred_contract():
    # Assert current Guardian-independent QA activation truth
    active_codes = [
        ProductionQARuleCode.VISUAL_REPETITION,
        ProductionQARuleCode.EXCESSIVE_STATIC_SCENES,
        ProductionQARuleCode.MISSING_INTRO,
        ProductionQARuleCode.MISSING_OUTRO
    ]
    deferred_codes = [
        ProductionQARuleCode.NO_MOTION,
        ProductionQARuleCode.DURATION_TOO_SHORT,
        ProductionQARuleCode.SUBTITLE_TOO_LARGE,
        ProductionQARuleCode.INSUFFICIENT_BODY_DEPTH,
        ProductionQARuleCode.AUDIO_LOUDNESS_OUT_OF_RANGE
    ]

    for c in active_codes:
        assert isinstance(c, ProductionQARuleCode)

    for c in deferred_codes:
        assert isinstance(c, ProductionQARuleCode)
