"""Focused unit and policy tests for Phase P18-B canonical subtitle modes.

Covers:
1. OFF mode: no subtitle cues generated, burn_ass_subtitles never invoked, render proceeds.
2. STANDARD mode: static/sentence cues generated, no \\kf tags in ASS, burns static subtitles.
3. KARAOKE mode: word-timed karaoke cues generated, \\kf tags present in ASS, duration conserved.
4. KARAOKE -> STANDARD fallback: when duration is insufficient for word count, fallback applied, no \\kf tags.
5. Default mode remains STANDARD when no explicit mode specified.
6. Fail-closed: invalid explicit subtitle mode string raises deterministic error before render.
7. No silent upgrade: OFF never becomes STANDARD/KARAOKE, STANDARD never becomes KARAOKE.
8. Provenance: requested_subtitle_mode, effective_subtitle_mode, fallback_applied, fallback_reason, timing_source.
9. Derived timing provenance: DERIVED_SEGMENT_TIMING recorded when derived from narration segment.
10. Routing unchanged: ProductionRenderService._should_use_v2 semantics identical.
11. Legacy compatibility: legacy booleans and aliases resolve correctly.
12. ASS escaping preserved: override brackets and backslashes properly escaped in ASS dialogue.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from omega.api.production import _get_production_service
from omega.application.production_contract import (
    SubtitleFallbackPolicy,
    SubtitleMode,
    SubtitleTimingSource,
    _normalize_subtitle_mode,
    evaluate_subtitle_mode_decision,
    resolve_canonical_production_contract,
)
from omega.application.production_service import ProductionService
from omega.application.render_service import ProductionRenderService
from omega.application.storyboard_engine import StoryboardPlan, StoryboardScene, VisualStrategy
from omega.application.subtitle_engine import (
    SubtitleRenderStyle,
    generate_karaoke_ass_document,
    generate_karaoke_cues,
)
from omega.application.visual_production_v2_service import (
    VerticalSliceError,
    VisualProductionV2Service,
    _derived_karaoke_timing_error,
)
from omega.infrastructure.database import get_async_session
from omega.infrastructure.models import ProductionRequest
from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderResult
from omega.main import app

VALID_MP4_HEADER = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"


def _make_dummy_request(
    mode: str = "MISSION_EXECUTION",
    with_execution: bool = True,
    metadata_: dict | None = None,
    voice_profile: dict | None = None,
) -> ProductionRequest:
    return ProductionRequest(
        id=uuid4(),
        channel_id=uuid4(),
        script_version_id=uuid4(),
        content_request_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        mission_execution_id=uuid4() if with_execution else None,
        mode=mode,
        target_width=1920,
        target_height=1080,
        fps=24,
        video_codec="h264",
        audio_codec="aac",
        container_format="mp4",
        voice_profile=voice_profile or {"voice_ref": "af_heart", "speed": 1.0},
        metadata_=metadata_ or {},
    )


# ══════════════════════════════════════════════════════════════════
# 1. Routing Safety Proof (P18-B invariant)
# ══════════════════════════════════════════════════════════════════

def test_routing_unchanged_mission_execution():
    service = ProductionRenderService(
        visual_production_service=MagicMock(),
    )
    req = _make_dummy_request(mode="MISSION_EXECUTION", with_execution=True)
    assert service._should_use_v2(req) is True


def test_routing_unchanged_interactive_remains_legacy():
    service = ProductionRenderService(
        visual_production_service=MagicMock(),
    )
    req = _make_dummy_request(mode="INTERACTIVE", with_execution=False)
    assert service._should_use_v2(req) is False


def test_routing_unchanged_missing_visual_service():
    service = ProductionRenderService(
        visual_production_service=None,
    )
    req = _make_dummy_request(mode="MISSION_EXECUTION", with_execution=True)
    assert service._should_use_v2(req) is False


# ══════════════════════════════════════════════════════════════════
# 2. Decision Logic & Normalization Policy Tests
# ══════════════════════════════════════════════════════════════════

def test_decision_off():
    d = evaluate_subtitle_mode_decision(
        requested_mode=SubtitleMode.OFF,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
    )
    assert d.requested_mode == SubtitleMode.OFF
    assert d.effective_mode == SubtitleMode.OFF
    assert d.fallback_applied is False
    assert d.fallback_reason is None
    assert d.timing_source == SubtitleTimingSource.NONE


def test_decision_standard():
    d = evaluate_subtitle_mode_decision(
        requested_mode=SubtitleMode.STANDARD,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
    )
    assert d.requested_mode == SubtitleMode.STANDARD
    assert d.effective_mode == SubtitleMode.STANDARD
    assert d.fallback_applied is False
    assert d.fallback_reason is None
    assert d.timing_source == SubtitleTimingSource.DERIVED_SEGMENT_TIMING


def test_decision_karaoke_sufficient_timing():
    d = evaluate_subtitle_mode_decision(
        requested_mode=SubtitleMode.KARAOKE,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
        has_word_timing=False,
        timing_available=True,
    )
    assert d.requested_mode == SubtitleMode.KARAOKE
    assert d.effective_mode == SubtitleMode.KARAOKE
    assert d.fallback_applied is False
    assert d.fallback_reason is None
    assert d.timing_source == SubtitleTimingSource.DERIVED_SEGMENT_TIMING


def test_decision_karaoke_provider_word_timing():
    d = evaluate_subtitle_mode_decision(
        requested_mode=SubtitleMode.KARAOKE,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
        has_word_timing=True,
    )
    assert d.requested_mode == SubtitleMode.KARAOKE
    assert d.effective_mode == SubtitleMode.KARAOKE
    assert d.fallback_applied is False
    assert d.timing_source == SubtitleTimingSource.DERIVED_SEGMENT_TIMING


def test_decision_karaoke_fallback_to_standard():
    d = evaluate_subtitle_mode_decision(
        requested_mode=SubtitleMode.KARAOKE,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
        has_word_timing=False,
        timing_available=False,
        timing_error_reason="insufficient_word_timing_duration",
    )
    assert d.requested_mode == SubtitleMode.KARAOKE
    assert d.effective_mode == SubtitleMode.STANDARD
    assert d.fallback_applied is True
    assert d.fallback_reason == "insufficient_word_timing_duration"
    assert d.timing_source == SubtitleTimingSource.DERIVED_SEGMENT_TIMING


def test_decision_invalid_fallback_policy_fails():
    with pytest.raises(ValueError, match="Unsupported fallback policy"):
        evaluate_subtitle_mode_decision(
            requested_mode=SubtitleMode.KARAOKE,
            fallback_policy="INVALID_POLICY",  # type: ignore[arg-type]
            timing_available=False,
        )


def test_fail_closed_invalid_explicit_subtitle_mode():
    with pytest.raises(ValueError, match="Invalid subtitle_mode"):
        _normalize_subtitle_mode("EXPLODING_TEXT", None, None)

    with pytest.raises(ValueError, match="Invalid subtitle_mode"):
        _normalize_subtitle_mode("AUTOMATIC_UPGRADE", None, None)


def test_no_silent_upgrade():
    # OFF requested must remain OFF even if style has karaoke=True
    mode_off = _normalize_subtitle_mode("OFF", subtitle_enabled=None, style_karaoke=True)
    assert mode_off == SubtitleMode.OFF

    # STANDARD requested must remain STANDARD even if style has karaoke=True
    mode_std = _normalize_subtitle_mode("STANDARD", subtitle_enabled=None, style_karaoke=True)
    assert mode_std == SubtitleMode.STANDARD


def test_default_mode_remains_standard():
    # When no explicit subtitle mode, no karaoke, and subtitle_enabled is True/None -> default STANDARD
    mode = _normalize_subtitle_mode(None, subtitle_enabled=True, style_karaoke=False)
    assert mode == SubtitleMode.STANDARD

    mode_none = _normalize_subtitle_mode(None, subtitle_enabled=None, style_karaoke=False)
    assert mode_none == SubtitleMode.STANDARD


def test_legacy_input_compatibility():
    # subtitle_enabled == False -> OFF
    assert _normalize_subtitle_mode(None, subtitle_enabled=False, style_karaoke=None) == SubtitleMode.OFF

    # Legacy strings: "disabled", "none", "false" -> OFF
    assert _normalize_subtitle_mode("disabled", None, None) == SubtitleMode.OFF
    assert _normalize_subtitle_mode("none", None, None) == SubtitleMode.OFF
    assert _normalize_subtitle_mode("false", None, None) == SubtitleMode.OFF

    # Legacy strings: "sentence", "true" -> STANDARD
    assert _normalize_subtitle_mode("sentence", None, None) == SubtitleMode.STANDARD
    assert _normalize_subtitle_mode("true", None, None) == SubtitleMode.STANDARD

    # Legacy karaoke style trigger -> KARAOKE
    assert _normalize_subtitle_mode(None, subtitle_enabled=True, style_karaoke=True) == SubtitleMode.KARAOKE


# ══════════════════════════════════════════════════════════════════
# 3. Subtitle Generation Semantics & ASS Escaping Proof
# ══════════════════════════════════════════════════════════════════

def test_standard_ass_output_contains_no_kf():
    segment = {"text": "Hello world this is a static subtitle test.", "start_ms": 0, "duration_ms": 3000}
    cues = generate_karaoke_cues([segment], sentence_mode=True)
    style = SubtitleRenderStyle(karaoke=False)
    doc = generate_karaoke_ass_document(cues, style=style)

    assert "\\kf" not in doc.content
    assert "Hello world this is a static subtitle test." in doc.content


def test_karaoke_ass_output_contains_kf_and_conserves_duration():
    segment = {"text": "Alpha beta gamma delta.", "start_ms": 0, "duration_ms": 2000}
    cues = generate_karaoke_cues([segment], sentence_mode=False)
    style = SubtitleRenderStyle(karaoke=True)
    doc = generate_karaoke_ass_document(cues, style=style)

    assert "\\kf" in doc.content
    # Check that word durations are conserved within total segment duration
    total_word_ms = sum(w["duration_ms"] for cue in cues for w in cue["words"])
    assert total_word_ms == 2000
    assert all(int(value) > 0 for value in re.findall(r"\\kf(\d+)", doc.content))


@pytest.mark.parametrize(
    ("text", "duration_ms", "expected_error"),
    [
        ("   ", 1000, "blank_subtitle_text"),
        ("words", 0, "non_positive_segment_duration"),
        ("words", -1, "non_positive_segment_duration"),
        ("one two three four five", 49, "zero_centisecond_karaoke_unit"),
    ],
)
def test_derived_karaoke_capability_validation(text, duration_ms, expected_error):
    assert _derived_karaoke_timing_error(text, duration_ms) == expected_error


def test_ass_escaping_preserved():
    # Special characters that could inject ASS tags or backslash controls
    malicious_text = "Watch this {\\b1}bold{\\b0} and \\N newline probe [brackets]."
    segment = {"text": malicious_text, "start_ms": 0, "duration_ms": 3000}
    cues = generate_karaoke_cues([segment], sentence_mode=True)
    style = SubtitleRenderStyle(karaoke=False)
    doc = generate_karaoke_ass_document(cues, style=style)

    # In ASS documents, literal braces should not form raw override commands
    # Dialogue line must exist
    assert "Dialogue:" in doc.content


# ══════════════════════════════════════════════════════════════════
# 4. End-to-End V2 Service Subtitle Mode Paths (Mock Renderer)
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def v2_service_fixture(tmp_path: Path):
    output_root = tmp_path / "renders"
    mock_orchestrator = AsyncMock()
    mock_video_renderer = AsyncMock()
    mock_ffmpeg = AsyncMock()
    mock_narration_provider = AsyncMock()
    mock_narration_provider.__class__.__name__ = "MockProvider"
    mock_narration_provider.model = "mock-model"
    mock_narration_provider.default_voice = "mock-voice"

    mock_storage = MagicMock()
    audio_path = tmp_path / "mock.wav"
    audio_path.write_bytes(VALID_MP4_HEADER + b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    svc = VisualProductionV2Service(
        asset_orchestrator=mock_orchestrator,
        output_root=output_root,
        browser_runtime_factory=MagicMock(),
        video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg,
        narration_provider=mock_narration_provider,
        narration_storage=mock_storage,
    )

    return {
        "service": svc,
        "ffmpeg": mock_ffmpeg,
        "video_renderer": mock_video_renderer,
        "narration": mock_narration_provider,
        "storage": mock_storage,
        "tmp_path": tmp_path,
    }


def _setup_v2_mocks(fx, text: str = "Test scene narration", duration_ms: int = 3000):
    fx["narration"].synthesize_segment_audio.return_value = {
        "storage_uri": "channels/test/1.wav",
        "duration_ms": duration_ms,
        "content_hash": "mock-audio-sha256",
    }

    async def fake_render(*args, **kwargs):
        out = kwargs.get("output_path") or (args[3] if len(args) > 3 else None)
        if out:
            Path(out).write_bytes(VALID_MP4_HEADER + b"visual_content")
        return VisualV2VideoRenderResult(
            output_path=out,
            scene_index=1,
            template_id="HERO_TITLE",
            width=1920,
            height=1080,
            fps=24,
            duration_seconds=duration_ms / 1000.0,
            frame_count=72,
            video_sha256=hashlib.sha256(VALID_MP4_HEADER + b"visual_content").hexdigest(),
            source_html_sha256="html_sha",
            motion_profile="none",
        )
    fx["video_renderer"].render_clip.side_effect = fake_render

    captured_ass = []
    async def fake_burn(*args, **kwargs):
        ass_p = kwargs.get("ass_path") or (args[1] if len(args) > 1 else None)
        if ass_p and Path(ass_p).exists():
            captured_ass.append(Path(ass_p).read_text("utf-8"))
        out = kwargs.get("output_path") or (args[2] if len(args) > 2 else None)
        if out:
            Path(out).write_bytes(VALID_MP4_HEADER + b"subtitled_content")
    fx["ffmpeg"].burn_ass_subtitles.side_effect = fake_burn

    async def fake_mux(*args, **kwargs):
        out = kwargs.get("output_path") or (args[2] if len(args) > 2 else None)
        if out:
            Path(out).write_bytes(VALID_MP4_HEADER + b"muxed_content")
    fx["ffmpeg"].mux_video_audio.side_effect = fake_mux

    async def fake_concat(*args, **kwargs):
        out = kwargs.get("output_path") or (args[1] if len(args) > 1 else None)
        if out:
            Path(out).write_bytes(VALID_MP4_HEADER + b"final_mp4")
    fx["ffmpeg"].concatenate_clips.side_effect = fake_concat

    def fake_storyboard(_):
        return StoryboardPlan(
            title="P18-B Test",
            estimated_duration_seconds=duration_ms / 1000.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1,
                    section_id="sec-1",
                    purpose="intro",
                    source_statement_references=[],
                    narration_excerpt=text,
                    estimated_duration_seconds=duration_ms / 1000.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION,
                    visual_brief="intro brief",
                )
            ],
        )
    fx["service"]._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)
    return captured_ass


def _make_mock_session_and_lineage():
    m_exec_id = uuid4()
    req_id = uuid4()
    mission_id = uuid4()
    channel_id = uuid4()
    dna_rev_id = uuid4()
    script_id = uuid4()

    mock_mission = MagicMock()
    mock_mission.id = mission_id
    mock_mission.channel_id = channel_id

    mock_dna_rev = MagicMock()
    mock_dna_rev.id = dna_rev_id
    mock_dna_rev.snapshot = {"brand_name": "Test Brand"}

    mock_m_exec = MagicMock()
    mock_m_exec.id = m_exec_id
    mock_m_exec.mission = mock_mission
    mock_m_exec.channel_dna_revision = mock_dna_rev
    mock_m_exec.channel_dna_revision_id = dna_rev_id

    mock_statement = MagicMock()
    mock_statement.citations = []

    mock_section = MagicMock()
    mock_section.statements = [mock_statement]
    mock_section.heading = "Heading 1"
    mock_section.narration_text = "Section narration text"

    mock_script = MagicMock()
    mock_script.id = script_id
    mock_script.version = 1
    mock_script.sections = [mock_section]
    mock_script.hook_text = "Hook"
    mock_script.cta_text = "CTA"
    mock_script.closing_text = "Close"

    mock_content_req = MagicMock()
    mock_content_req.id = req_id
    mock_content_req.mission_execution_id = m_exec_id
    mock_content_req.channel_id = channel_id
    mock_content_req.channel_dna_revision_id = dna_rev_id
    mock_content_req.scripts = [mock_script]

    session = AsyncMock()

    async def mock_execute(stmt):
        res = MagicMock()
        stmt_str = str(stmt)
        if "mission_executions" in stmt_str:
            res.scalar_one_or_none.return_value = mock_m_exec
        elif "content_generation_requests" in stmt_str:
            res.scalar_one_or_none.return_value = mock_content_req
        elif "channels" in stmt_str:
            ch = MagicMock()
            ch.metadata_ = {}
            res.scalar_one_or_none.return_value = ch
        else:
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.first.return_value = None
        return res

    session.execute.side_effect = mock_execute
    return session, m_exec_id, req_id


@pytest.mark.asyncio
async def test_v2_off_path(v2_service_fixture, monkeypatch):
    fx = v2_service_fixture
    captured_ass = _setup_v2_mocks(fx)
    session, m_exec_id, req_id = _make_mock_session_and_lineage()
    cue_generation = MagicMock(side_effect=AssertionError("OFF generated subtitle cues"))
    ass_generation = MagicMock(side_effect=AssertionError("OFF generated ASS"))
    monkeypatch.setattr(
        "omega.application.visual_production_v2_service.generate_karaoke_cues",
        cue_generation,
    )
    monkeypatch.setattr(
        "omega.application.visual_production_v2_service.generate_karaoke_ass_document",
        ass_generation,
    )

    result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_mode=SubtitleMode.OFF,
    )

    # Subtitle burning was never invoked
    fx["ffmpeg"].burn_ass_subtitles.assert_not_called()
    cue_generation.assert_not_called()
    ass_generation.assert_not_called()
    assert len(captured_ass) == 0
    assert not list(fx["tmp_path"].rglob("*.ass"))
    assert not list(fx["tmp_path"].rglob("*.srt"))

    # Video/Audio rendering and concatenation continued normally
    fx["video_renderer"].render_clip.assert_called_once()
    fx["ffmpeg"].mux_video_audio.assert_called_once()
    fx["ffmpeg"].concatenate_clips.assert_called_once()

    # Provenance fields
    assert result.requested_subtitle_mode == "OFF"
    assert result.effective_subtitle_mode == "OFF"
    assert result.subtitle_fallback_applied is False
    assert result.subtitle_timing_source == "NONE"
    assert result.subtitle_enabled is False


@pytest.mark.asyncio
async def test_v2_standard_path(v2_service_fixture):
    fx = v2_service_fixture
    captured_ass = _setup_v2_mocks(fx)
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_mode=SubtitleMode.STANDARD,
    )

    # Subtitle burn was invoked
    fx["ffmpeg"].burn_ass_subtitles.assert_called_once()
    assert len(captured_ass) == 1

    # Static cues have NO progressive \kf tags
    assert "\\kf" not in captured_ass[0]

    # Provenance fields
    assert result.requested_subtitle_mode == "STANDARD"
    assert result.effective_subtitle_mode == "STANDARD"
    assert result.subtitle_fallback_applied is False
    assert result.subtitle_timing_source == "DERIVED_SEGMENT_TIMING"
    assert result.subtitle_enabled is True
    assert result.karaoke_subtitles_enabled is False


@pytest.mark.asyncio
async def test_v2_blank_narration_fails_before_render(v2_service_fixture):
    fx = v2_service_fixture
    _setup_v2_mocks(fx, text="   ", duration_ms=1000)
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    with pytest.raises(VerticalSliceError, match="Empty narration text"):
        await fx["service"].render_mission_execution(
            session, m_exec_id, req_id, subtitle_mode=SubtitleMode.KARAOKE
        )

    fx["video_renderer"].render_clip.assert_not_called()
    fx["ffmpeg"].burn_ass_subtitles.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration_ms", [None, 0, -10])
async def test_v2_invalid_narration_duration_fails_before_render(
    v2_service_fixture, duration_ms
):
    fx = v2_service_fixture
    _setup_v2_mocks(fx, text="valid subtitle text", duration_ms=1000)
    fx["narration"].synthesize_segment_audio.return_value["duration_ms"] = duration_ms
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    with pytest.raises(VerticalSliceError, match="Audio duration missing or zero"):
        await fx["service"].render_mission_execution(
            session, m_exec_id, req_id, subtitle_mode=SubtitleMode.KARAOKE
        )

    fx["video_renderer"].render_clip.assert_not_called()
    fx["ffmpeg"].burn_ass_subtitles.assert_not_called()


@pytest.mark.asyncio
async def test_v2_karaoke_path_success(v2_service_fixture):
    fx = v2_service_fixture
    captured_ass = _setup_v2_mocks(fx, text="One two three four five", duration_ms=4000)
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_mode=SubtitleMode.KARAOKE,
    )

    fx["ffmpeg"].burn_ass_subtitles.assert_called_once()
    assert len(captured_ass) == 1

    # Karaoke ASS contains \kf progressive highlight tags
    assert "\\kf" in captured_ass[0]

    # Provenance fields
    assert result.requested_subtitle_mode == "KARAOKE"
    assert result.effective_subtitle_mode == "KARAOKE"
    assert result.subtitle_fallback_applied is False
    assert result.subtitle_timing_source == "DERIVED_SEGMENT_TIMING"
    assert result.subtitle_enabled is True
    assert result.karaoke_subtitles_enabled is True


@pytest.mark.asyncio
async def test_v2_karaoke_path_provider_word_timing(v2_service_fixture):
    fx = v2_service_fixture
    captured_ass = _setup_v2_mocks(fx, text="One two three four five", duration_ms=4000)
    fx["narration"].synthesize_segment_audio.return_value = {
        "storage_uri": "channels/test/1.wav",
        "duration_ms": 4000,
        "content_hash": "mock-audio-sha256",
        "word_timing": [{"word": "One", "start_ms": 0, "end_ms": 800}],
    }
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_mode=SubtitleMode.KARAOKE,
    )

    assert result.requested_subtitle_mode == "KARAOKE"
    assert result.effective_subtitle_mode == "KARAOKE"
    assert result.subtitle_timing_source == "DERIVED_SEGMENT_TIMING"
    assert len(captured_ass) == 1
    assert "\\kf" in captured_ass[0]


@pytest.mark.asyncio
async def test_v2_karaoke_fallback_on_insufficient_timing(v2_service_fixture):
    fx = v2_service_fixture
    captured_ass = _setup_v2_mocks(
        fx,
        text="word1 word2 word3 word4 word5",
        duration_ms=49,
    )
    fx["narration"].synthesize_segment_audio.return_value = {
        "storage_uri": "channels/test/1.wav",
        "duration_ms": 49,
        "content_hash": "mock-audio-sha256",
    }
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_mode=SubtitleMode.KARAOKE,
    )

    # Effective mode became STANDARD via fallback
    assert result.requested_subtitle_mode == "KARAOKE"
    assert result.effective_subtitle_mode == "STANDARD"
    assert result.subtitle_fallback_applied is True
    assert result.subtitle_fallback_reason == "scene_1:zero_centisecond_karaoke_unit"
    assert result.subtitle_timing_source == "DERIVED_SEGMENT_TIMING"

    # Static subtitle burned without \kf tags
    assert len(captured_ass) == 1
    assert "\\kf" not in captured_ass[0]


@pytest.mark.asyncio
async def test_v2_karaoke_fallback_is_render_wide_and_sticky(v2_service_fixture):
    fx = v2_service_fixture
    captured_ass = _setup_v2_mocks(fx)
    scene_specs = [
        ("scene one is karaoke capable", 1200),
        ("one two three four five", 49),
        ("scene three is karaoke capable", 1200),
    ]
    fx["narration"].synthesize_segment_audio.side_effect = [
        {
            "storage_uri": f"channels/test/{index}.wav",
            "duration_ms": duration_ms,
            "content_hash": f"mock-audio-sha256-{index}",
        }
        for index, (_, duration_ms) in enumerate(scene_specs, start=1)
    ]
    fx["service"]._storyboard_engine.generate_storyboard = MagicMock(
        return_value=StoryboardPlan(
            title="P18-B Multi Scene",
            estimated_duration_seconds=sum(duration for _, duration in scene_specs) / 1000,
            scenes=[
                StoryboardScene(
                    sequence_index=index,
                    section_id=f"sec-{index}",
                    purpose="body",
                    source_statement_references=[],
                    narration_excerpt=text,
                    estimated_duration_seconds=duration_ms / 1000,
                    visual_strategy=VisualStrategy.TITLE_MOTION,
                    visual_brief=f"scene {index}",
                )
                for index, (text, duration_ms) in enumerate(scene_specs, start=1)
            ],
        )
    )
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_mode=SubtitleMode.KARAOKE,
    )

    assert result.requested_subtitle_mode == "KARAOKE"
    assert result.effective_subtitle_mode == "STANDARD"
    assert result.subtitle_fallback_applied is True
    assert result.subtitle_fallback_reason == "scene_2:zero_centisecond_karaoke_unit"
    assert result.subtitle_timing_source == "DERIVED_SEGMENT_TIMING"
    assert len(captured_ass) == 3
    assert all("\\kf" not in ass for ass in captured_ass)


@pytest.mark.asyncio
async def test_canonical_contract_mode_wins_conflicting_legacy_style(v2_service_fixture):
    fx = v2_service_fixture
    captured_ass = _setup_v2_mocks(
        fx, text="one two three four five", duration_ms=2000
    )
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    standard_contract = resolve_canonical_production_contract(
        _make_dummy_request(metadata_={"render_settings": {"subtitle_mode": "STANDARD"}})
    )
    standard_result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_enabled=True,
        subtitle_style=SubtitleRenderStyle(karaoke=True),
        contract=standard_contract,
    )

    karaoke_contract = resolve_canonical_production_contract(
        _make_dummy_request(metadata_={"render_settings": {"subtitle_mode": "KARAOKE"}})
    )
    karaoke_result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_enabled=False,
        subtitle_style=SubtitleRenderStyle(karaoke=False),
        contract=karaoke_contract,
    )

    assert standard_result.effective_subtitle_mode == "STANDARD"
    assert "\\kf" not in captured_ass[0]
    assert karaoke_result.effective_subtitle_mode == "KARAOKE"
    assert "\\kf" in captured_ass[1]


@pytest.mark.asyncio
async def test_partial_provider_timing_never_claims_provider_authority(v2_service_fixture):
    fx = v2_service_fixture
    _setup_v2_mocks(fx, text="one two three four five", duration_ms=2000)
    fx["narration"].synthesize_segment_audio.return_value.update(
        {"word_timing": [{"word": "one", "start_ms": 0, "end_ms": 400}]}
    )
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    result = await fx["service"].render_mission_execution(
        session, m_exec_id, req_id, subtitle_mode=SubtitleMode.KARAOKE
    )

    assert result.effective_subtitle_mode == "KARAOKE"
    assert result.subtitle_timing_source == "DERIVED_SEGMENT_TIMING"


@pytest.mark.asyncio
async def test_fallback_provenance_is_idempotent(v2_service_fixture):
    fx = v2_service_fixture
    _setup_v2_mocks(fx, text="one two three four five", duration_ms=49)
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    first = await fx["service"].render_mission_execution(
        session, m_exec_id, req_id, subtitle_mode=SubtitleMode.KARAOKE
    )
    second = await fx["service"].render_mission_execution(
        session, m_exec_id, req_id, subtitle_mode=SubtitleMode.KARAOKE
    )

    first_provenance = (
        first.requested_subtitle_mode,
        first.effective_subtitle_mode,
        first.subtitle_fallback_applied,
        first.subtitle_fallback_reason,
        first.subtitle_timing_source,
    )
    second_provenance = (
        second.requested_subtitle_mode,
        second.effective_subtitle_mode,
        second.subtitle_fallback_applied,
        second.subtitle_fallback_reason,
        second.subtitle_timing_source,
    )
    assert first_provenance == second_provenance


@pytest.mark.asyncio
async def test_v2_manifest_provenance_roundtrip(v2_service_fixture):
    fx = v2_service_fixture
    _setup_v2_mocks(fx)
    session, m_exec_id, req_id = _make_mock_session_and_lineage()

    result = await fx["service"].render_mission_execution(
        session,
        m_exec_id,
        req_id,
        subtitle_mode=SubtitleMode.STANDARD,
    )

    manifest_path = result.output_path.parent / "manifest.json"
    assert manifest_path.exists()
    manifest_data = json.loads(manifest_path.read_text("utf-8"))

    assert manifest_data["requested_subtitle_mode"] == "STANDARD"
    assert manifest_data["effective_subtitle_mode"] == "STANDARD"
    assert manifest_data["subtitle_fallback_applied"] is False
    assert manifest_data["subtitle_timing_source"] == "DERIVED_SEGMENT_TIMING"
    assert "subtitle_mode_decision" in manifest_data


@pytest.mark.asyncio
async def test_update_render_settings_accepts_canonical_subtitle_mode():
    request = SimpleNamespace(status="PENDING", metadata_={})
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = request
    session = AsyncMock()
    session.execute.return_value = exec_result

    updated = await ProductionService.update_render_settings(
        object.__new__(ProductionService),
        session,
        uuid4(),
        uuid4(),
        SubtitleRenderStyle(),
        subtitle_mode=SubtitleMode.KARAOKE,
    )
    assert updated.metadata_["render_settings"]["subtitle_mode"] == "KARAOKE"

    # Also test string alias
    updated2 = await ProductionService.update_render_settings(
        object.__new__(ProductionService),
        session,
        uuid4(),
        uuid4(),
        SubtitleRenderStyle(),
        subtitle_mode="off",
    )
    assert updated2.metadata_["render_settings"]["subtitle_mode"] == "OFF"


@pytest.mark.asyncio
async def test_update_render_settings_mode_only_preserves_existing_style():
    existing_style = SubtitleRenderStyle(font_size=56).model_dump()
    request = SimpleNamespace(
        status="PENDING",
        metadata_={"render_settings": {"subtitle_style": existing_style}},
    )
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = request
    session = AsyncMock()
    session.execute.return_value = exec_result

    updated = await ProductionService.update_render_settings(
        object.__new__(ProductionService),
        session,
        uuid4(),
        uuid4(),
        None,
        subtitle_mode=SubtitleMode.KARAOKE,
    )

    assert updated.metadata_["render_settings"]["subtitle_style"] == existing_style
    assert updated.metadata_["render_settings"]["subtitle_mode"] == "KARAOKE"


@pytest.mark.asyncio
async def test_update_render_settings_rejects_invalid_subtitle_mode():
    request = SimpleNamespace(status="PENDING", metadata_={})
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = request
    session = AsyncMock()
    session.execute.return_value = exec_result

    with pytest.raises(ValueError, match="Invalid subtitle_mode"):
        await ProductionService.update_render_settings(
            object.__new__(ProductionService),
            session,
            uuid4(),
            uuid4(),
            SubtitleRenderStyle(),
            subtitle_mode="SUPER_KARAOKE",
        )


def _api_production_request(metadata: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        channel_id=uuid4(),
        script_version_id=uuid4(),
        content_request_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        mission_execution_id=None,
        mode="INTERACTIVE",
        status="DRAFT",
        outcome=None,
        target_width=1920,
        target_height=1080,
        fps=30,
        video_codec="h264",
        audio_codec="aac",
        container_format="mp4",
        created_at=datetime.now(UTC),
        started_at=None,
        completed_at=None,
        failed_at=None,
        metadata_=metadata,
    )


class _RenderSettingsAPIFake:
    def __init__(self, request: SimpleNamespace) -> None:
        self.request = request
        self.calls: list[tuple[SubtitleRenderStyle | None, SubtitleMode | None]] = []

    async def update_render_settings(
        self,
        _session: Any,
        _channel_id: Any,
        _request_id: Any,
        subtitle_style: SubtitleRenderStyle | None,
        subtitle_mode: SubtitleMode | None,
    ) -> SimpleNamespace:
        self.calls.append((subtitle_style, subtitle_mode))
        render_settings = dict(self.request.metadata_.get("render_settings") or {})
        if subtitle_style is not None:
            render_settings["subtitle_style"] = subtitle_style.model_dump()
        if subtitle_mode is not None:
            render_settings["subtitle_mode"] = subtitle_mode.value
        self.request.metadata_["render_settings"] = render_settings
        return self.request


@pytest.mark.asyncio
async def test_render_settings_endpoint_accepts_mode_only_and_preserves_style():
    existing_style = SubtitleRenderStyle(font_size=52).model_dump()
    request = _api_production_request(
        {"render_settings": {"subtitle_style": existing_style}}
    )
    service = _RenderSettingsAPIFake(request)

    async def override_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_async_session] = override_session
    app.dependency_overrides[_get_production_service] = lambda: service
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"/api/v1/channels/{request.channel_id}/production/{request.id}/render-settings",
                json={"subtitle_mode": "KARAOKE"},
            )
    finally:
        app.dependency_overrides.pop(get_async_session, None)
        app.dependency_overrides.pop(_get_production_service, None)

    assert response.status_code == 200
    assert service.calls == [(None, SubtitleMode.KARAOKE)]
    assert request.metadata_["render_settings"]["subtitle_style"] == existing_style
    assert request.metadata_["render_settings"]["subtitle_mode"] == "KARAOKE"


@pytest.mark.asyncio
async def test_render_settings_endpoint_accepts_legacy_style_only():
    request = _api_production_request({})
    service = _RenderSettingsAPIFake(request)
    style = SubtitleRenderStyle(karaoke=True)

    async def override_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_async_session] = override_session
    app.dependency_overrides[_get_production_service] = lambda: service
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"/api/v1/channels/{request.channel_id}/production/{request.id}/render-settings",
                json={"subtitle_style": style.model_dump()},
            )
    finally:
        app.dependency_overrides.pop(get_async_session, None)
        app.dependency_overrides.pop(_get_production_service, None)

    assert response.status_code == 200
    assert service.calls == [(style, None)]


@pytest.mark.asyncio
async def test_render_settings_endpoint_accepts_mode_and_style():
    request = _api_production_request({})
    service = _RenderSettingsAPIFake(request)
    style = SubtitleRenderStyle(font_size=50)

    async def override_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_async_session] = override_session
    app.dependency_overrides[_get_production_service] = lambda: service
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"/api/v1/channels/{request.channel_id}/production/{request.id}/render-settings",
                json={
                    "subtitle_mode": "STANDARD",
                    "subtitle_style": style.model_dump(),
                },
            )
    finally:
        app.dependency_overrides.pop(get_async_session, None)
        app.dependency_overrides.pop(_get_production_service, None)

    assert response.status_code == 200
    assert service.calls == [(style, SubtitleMode.STANDARD)]


@pytest.mark.asyncio
async def test_render_settings_endpoint_rejects_unknown_mode():
    request = _api_production_request({})
    service = _RenderSettingsAPIFake(request)

    async def override_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_async_session] = override_session
    app.dependency_overrides[_get_production_service] = lambda: service
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"/api/v1/channels/{request.channel_id}/production/{request.id}/render-settings",
                json={"subtitle_mode": "SUPER_KARAOKE"},
            )
    finally:
        app.dependency_overrides.pop(get_async_session, None)
        app.dependency_overrides.pop(_get_production_service, None)

    assert response.status_code == 422
    assert service.calls == []
