"""Regression and acceptance tests for render admission and visual failsafe.

Covers:
A. Visual Template Admission & Deterministic Fallback
B. Render Error Classification & Causal Exception Unwrapping
C. Duplicate Normal Render Admission Authority & Concurrency
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from omega.application.beat_asset_policy import BeatAssetAction, BeatAssetDecision
from omega.application.beat_render_adapter import BeatRenderPlan, BeatRenderUnit
from omega.application.canonical_beat_preparation import CanonicalBeatPreparationResult
from omega.application.editorial_beat import BeatMotionIntent, BeatTransitionIntent
from omega.application.editorial_beat_planner import BeatSemanticRole
from omega.application.mechanism_diagram import resolve_mechanism_diagram_spec
from omega.application.production_service import (
    ProductionService,
    ProductionStateError,
)
from omega.application.render_service import (
    FFmpegExecutionError,
    _classify_phase2_error,
)
from omega.application.storyboard_engine import (
    StoryboardEngine,
    StoryboardScene,
    VisualStrategy,
)
from omega.application.template_payload_resolver import (
    TemplatePayloadError,
    can_resolve_diagram_payload,
)
from omega.application.visual_direction import (
    VisualDirection,
    VisualDirector,
    VisualRenderMode,
    VisualTemplateId,
)
from omega.application.visual_production_v2_service import (
    VerticalSliceError,
    VisualProductionV2Service,
)
from omega.domain.production import (
    ProductionRequestStatus,
    RenderErrorCode,
    RenderJobState,
)
from omega.infrastructure.models import (
    ProductionRenderJob,
    ProductionRequest,
    RenderPlan,
)

# ============================================================================
# A. VISUAL TEMPLATE ADMISSION & DETERMINISTIC FALLBACK
# ============================================================================

CANARY_TOPIC = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"

# Scene 17 equivalent: discusses concrete cracking mechanisms / structural behavior,
# but contains NO extractable, trustworthy causal/sequential node pairs.
SCENE17_CANARY_PROSE = (
    "Mechanisms of degradation in reinforced concrete structures often manifest "
    "through complex internal chemical interactions and stress concentrations that "
    "develop over decades of environmental exposure without immediate surface signs."
)

# Real mechanism prose: contains concrete causal progression that CAN be resolved.
REAL_MECHANISM_PROSE = (
    "Water ingress causes freeze-thaw expansion, which leads to internal micro-cracking."
)


def test_scene17_canary_prose_cannot_resolve_diagram_payload():
    """Authoritative canary prose with < 2 nodes must report false for diagram resolution."""
    assert can_resolve_diagram_payload(SCENE17_CANARY_PROSE) is False
    assert can_resolve_diagram_payload(SCENE17_CANARY_PROSE, is_g2_mechanism=True) is False


def test_real_mechanism_prose_can_resolve_diagram_payload():
    """Genuine causal mechanism prose with >= 2 nodes successfully resolves."""
    assert can_resolve_diagram_payload(REAL_MECHANISM_PROSE, is_g2_mechanism=True) is True
    spec = resolve_mechanism_diagram_spec(REAL_MECHANISM_PROSE)
    assert spec is not None
    assert len(spec.nodes) >= 2
    # Verify no fabricated nodes
    for node in spec.nodes:
        assert node.lower() in REAL_MECHANISM_PROSE.lower()


def test_storyboard_engine_does_not_commit_flow_diagram_for_unresolvable_prose():
    """StoryboardEngine must not select DIAGRAM when narration cannot produce >= 2 nodes."""
    engine = StoryboardEngine()
    script_dict = {
        "title": CANARY_TOPIC,
        "estimated_duration_seconds": 60,
        "sections": [
            {
                "heading": "Degradation Mechanisms",
                "statements": [
                    {
                        "statement_order": 17,
                        "statement_text": SCENE17_CANARY_PROSE,
                        "statement_type": "STATEMENT",
                    }
                ],
            }
        ],
    }

    plan = engine.generate_storyboard(script_dict)
    assert len(plan.scenes) == 1
    scene = plan.scenes[0]
    # Must NOT be DIAGRAM because SCENE17_CANARY_PROSE lacks trustworthy diagram nodes
    assert scene.visual_strategy != VisualStrategy.DIAGRAM


def test_visual_director_deterministic_fallback_to_kinetic_text():
    """If a scene arrives with DIAGRAM strategy but lacks nodes, fallback to KINETIC_TEXT."""
    director = VisualDirector()
    scene = StoryboardScene(
        sequence_index=17,
        section_id="Mechanism 5",
        purpose="Explain structural degradation",
        source_statement_references=[17],
        narration_excerpt=SCENE17_CANARY_PROSE,
        estimated_duration_seconds=6.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Structural mechanism explanation",
    )

    direction = director.resolve(scene)
    # Safely falls back to KINETIC_TEXT before rendering
    assert direction.template_id == VisualTemplateId.KINETIC_TEXT


def test_no_fabricated_diagram_nodes_on_unresolvable_content():
    """Verifies resolve_mechanism_diagram_spec fails closed instead of inventing nodes."""
    spec = resolve_mechanism_diagram_spec(SCENE17_CANARY_PROSE)
    assert spec is None


# ============================================================================
# B. ERROR CLASSIFICATION & CAUSAL EXCEPTION UNWRAPPING
# ============================================================================

def test_template_payload_error_classifies_as_input_invalid():
    """Direct TemplatePayloadError maps to INPUT_INVALID."""
    err = TemplatePayloadError("Could not extract at least two trustworthy diagram nodes.")
    assert _classify_phase2_error(err) == RenderErrorCode.INPUT_INVALID


def test_wrapped_template_payload_error_classifies_as_input_invalid():
    """VerticalSliceError wrapping TemplatePayloadError unrolls cause to INPUT_INVALID."""
    root_cause = TemplatePayloadError("Could not extract at least two trustworthy diagram nodes.")
    wrapped = VerticalSliceError(
        f"Committed multi-beat render failed for scene 17: {root_cause}"
    )
    wrapped.__cause__ = root_cause

    assert _classify_phase2_error(wrapped) == RenderErrorCode.INPUT_INVALID


def test_deeply_nested_template_payload_error_classifies_as_input_invalid():
    """Deep causal chain with TemplatePayloadError anywhere maps to INPUT_INVALID."""
    root_cause = TemplatePayloadError("Could not extract at least two trustworthy diagram nodes.")
    middle = RuntimeError("Beat render failed")
    middle.__cause__ = root_cause
    outer = VerticalSliceError("Committed multi-beat render failed for scene 17")
    outer.__cause__ = middle

    assert _classify_phase2_error(outer) == RenderErrorCode.INPUT_INVALID


def test_real_ffmpeg_error_classifies_as_ffmpeg_failed():
    """Genuine FFmpegExecutionError maps to FFMPEG_FAILED."""
    err = FFmpegExecutionError("ffmpeg process exited with code 1")
    assert _classify_phase2_error(err) == RenderErrorCode.FFMPEG_FAILED


def test_wrapped_ffmpeg_error_classifies_as_ffmpeg_failed():
    """VerticalSliceError wrapping FFmpegExecutionError maps to FFMPEG_FAILED."""
    root_cause = FFmpegExecutionError("ffmpeg scene encode failed")
    wrapped = VerticalSliceError("Committed render failed")
    wrapped.__cause__ = root_cause

    assert _classify_phase2_error(wrapped) == RenderErrorCode.FFMPEG_FAILED


def test_unrelated_error_classifies_as_unknown():
    """Unrelated error during Phase 2 is NOT misclassified as FFmpeg."""
    err = RuntimeError("Unexpected filesystem error")
    assert _classify_phase2_error(err) == RenderErrorCode.UNKNOWN


# ============================================================================
# C. DUPLICATE NORMAL RENDER ADMISSION AUTHORITY
# ============================================================================

class MockScalarResult:
    def __init__(self, items):
        self._items = items if isinstance(items, list) else ([items] if items is not None else [])

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalar_one(self):
        if not self._items:
            raise ValueError("No items in scalar_one")
        return self._items[0]

    def scalars(self):
        return self

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None


class InMemoryAsyncSession:
    """In-memory async session mock for testing render job allocation."""

    def __init__(self):
        self.records: dict[tuple[type, uuid.UUID], Any] = {}
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def add(self, entity):
        key = getattr(entity, "id", uuid.uuid4())
        self.records[(type(entity), key)] = entity

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def flush(self):
        pass

    async def refresh(self, entity):
        pass

    async def get(self, model, entity_id):
        return self.records.get((model, entity_id))

    async def execute(self, statement):
        stmt_str = str(statement).lower()
        where_clauses = getattr(statement, "_where_criteria", ())

        def _get_filter_val(col_name: str):
            for crit in where_clauses:
                left = getattr(crit, "left", None)
                l_name = getattr(left, "key", "") or getattr(left, "name", "")
                if l_name == col_name:
                    right = getattr(crit, "right", None)
                    return getattr(right, "value", right)
            return None

        if "from production_requests" in stmt_str:
            reqs = [r for (m, _), r in self.records.items() if m == ProductionRequest]
            target_id = _get_filter_val("id")
            if target_id is not None:
                reqs = [r for r in reqs if getattr(r, "id", None) == target_id]
            channel_id = _get_filter_val("channel_id")
            if channel_id is not None:
                reqs = [r for r in reqs if getattr(r, "channel_id", None) == channel_id]
            return MockScalarResult(reqs)

        if "from production_render_jobs" in stmt_str:
            jobs = [j for (m, _), j in self.records.items() if m == ProductionRenderJob]
            req_id = _get_filter_val("production_request_id")
            if req_id is not None:
                jobs = [j for j in jobs if getattr(j, "production_request_id", None) == req_id]
            key_val = _get_filter_val("idempotency_key")
            if key_val is not None:
                jobs = [j for j in jobs if getattr(j, "idempotency_key", None) == key_val]

            # Handle state.in_ filter
            if "state in" in stmt_str or "state in (" in stmt_str or "production_render_jobs.state in" in stmt_str:
                active_states = (
                    RenderJobState.PENDING.value,
                    RenderJobState.QUEUED.value,
                    RenderJobState.RUNNING.value,
                    RenderJobState.RETRY.value,
                )
                jobs = [j for j in jobs if getattr(j, "state", None) in active_states]

            # Sort by created_at desc
            jobs.sort(key=lambda j: getattr(j, "created_at", datetime.min), reverse=True)
            return MockScalarResult(jobs)

        if "from render_plans" in stmt_str:
            plans = [p for (m, _), p in self.records.items() if m == RenderPlan]
            req_id = _get_filter_val("production_request_id")
            if req_id is not None:
                plans = [p for p in plans if getattr(p, "production_request_id", None) == req_id]
            return MockScalarResult(plans)

        return MockScalarResult([])


def _setup_test_environment():
    channel_id = uuid.uuid4()
    req_id = uuid.uuid4()
    script_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    session = InMemoryAsyncSession()
    req = ProductionRequest(
        id=req_id,
        channel_id=channel_id,
        script_version_id=script_id,
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.READY.value,
        metadata_={},
    )
    plan = RenderPlan(
        id=plan_id,
        production_request_id=req_id,
        version=1,
        width=1920,
        height=1080,
        fps=24,
        video_codec="h264",
        audio_codec="aac",
        container="mp4",
        total_duration_ms=5000,
        scene_manifest=[],
        audio_manifest=[],
        subtitle_manifest=[],
    )
    session.add(req)
    session.add(plan)
    return session, channel_id, req_id


@pytest.mark.asyncio
async def test_same_key_replay_returns_existing_job():
    """Same idempotency key replay returns existing job with is_new_job=False."""
    session, channel_id, req_id = _setup_test_environment()
    prod_service = ProductionService()

    job1, plan1, is_new1 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="render-key-1",
        is_rerender=False,
    )
    assert is_new1 is True
    assert job1.state == RenderJobState.QUEUED.value

    # Replay with same key
    job2, plan2, is_new2 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="render-key-1",
        is_rerender=False,
    )
    assert is_new2 is False
    assert job2.id == job1.id


@pytest.mark.asyncio
async def test_different_key_while_queued_reuses_active_job():
    """Different idempotency key while prior job is QUEUED reuses active job, blocking duplicate."""
    session, channel_id, req_id = _setup_test_environment()
    prod_service = ProductionService()

    job1, _, is_new1 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="render-key-1",
        is_rerender=False,
    )
    assert is_new1 is True

    # Call with a DIFFERENT timestamp key while job1 is QUEUED
    job2, _, is_new2 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="render-key-2026-10-05T02:00:00",
        is_rerender=False,
    )
    assert is_new2 is False
    assert job2.id == job1.id

    # Total jobs allocated must be exactly 1
    total_jobs = [j for (m, _), j in session.records.items() if m == ProductionRenderJob]
    assert len(total_jobs) == 1


@pytest.mark.asyncio
async def test_different_key_while_running_reuses_active_job():
    """Different idempotency key while prior job is RUNNING reuses active job, blocking duplicate."""
    session, channel_id, req_id = _setup_test_environment()
    prod_service = ProductionService()

    job1, _, is_new1 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="render-key-1",
        is_rerender=False,
    )
    assert is_new1 is True

    # Simulate transition to RUNNING
    job1.state = RenderJobState.RUNNING.value

    job2, _, is_new2 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="render-key-running-attempt",
        is_rerender=False,
    )
    assert is_new2 is False
    assert job2.id == job1.id

    total_jobs = [j for (m, _), j in session.records.items() if m == ProductionRenderJob]
    assert len(total_jobs) == 1


@pytest.mark.asyncio
async def test_concurrent_render_calls_allocate_at_most_one_job():
    """Multiple concurrent normal render calls result in exactly one active render job."""
    session, channel_id, req_id = _setup_test_environment()
    prod_service = ProductionService()

    async def _call(key: str):
        return await prod_service.allocate_render_job(
            session=session,
            channel_id=channel_id,
            request_id=req_id,
            idempotency_key=key,
            is_rerender=False,
        )

    results = await asyncio.gather(
        _call("render-key-concurrent-1"),
        _call("render-key-concurrent-2"),
        _call("render-key-concurrent-3"),
    )

    # Exactly one was new
    new_flags = [r[2] for r in results]
    assert new_flags.count(True) == 1
    assert new_flags.count(False) == 2

    # All returned the exact same job ID
    job_ids = {r[0].id for r in results}
    assert len(job_ids) == 1

    total_jobs = [j for (m, _), j in session.records.items() if m == ProductionRenderJob]
    assert len(total_jobs) == 1


@pytest.mark.asyncio
async def test_rerender_requires_all_prior_jobs_terminal():
    """Explicit rerender remains separately governed and requires all prior jobs terminal."""
    session, channel_id, req_id = _setup_test_environment()
    prod_service = ProductionService()

    job1, _, is_new1 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="render-key-initial",
        is_rerender=False,
    )
    assert is_new1 is True

    # Rerender while active job exists -> must raise ProductionStateError
    with pytest.raises(ProductionStateError, match="Cannot rerender while a render job is currently running."):
        await prod_service.allocate_render_job(
            session=session,
            channel_id=channel_id,
            request_id=req_id,
            idempotency_key="render-rerender-key",
            is_rerender=True,
        )

    # Terminate job1
    job1.state = RenderJobState.FAILED.value
    job1.completed_at = datetime.now(UTC)

    # Rerender is now permitted
    job2, _, is_new2 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="render-rerender-key-2",
        is_rerender=True,
    )
    assert is_new2 is True
    assert job2.id != job1.id


# ============================================================================
# D. RUNTIME MULTI-BEAT FALLBACK EXECUTION IN VISUAL PRODUCTION V2
# ============================================================================

def _make_mock_service(tmp_path: Path, prep, executor, renderer, assembler):
    legacy_renderer = MagicMock()
    legacy_renderer.render_clip = AsyncMock()
    return VisualProductionV2Service(
        asset_orchestrator=MagicMock(),
        output_root=tmp_path,
        video_renderer=legacy_renderer,
        beat_preparation_service=prep,
        beat_asset_executor=executor,
        beat_visual_renderer=renderer,
        beat_clip_assembler=assembler,
    )


def _build_multi_beat_plan_with_diagram(
    narration_excerpt: str, on_screen_text: str | None = None,
) -> BeatRenderPlan:
    """Construct the actual immutable production plan with a FLOW_DIAGRAM unit."""
    scene_view_0 = StoryboardScene(
        sequence_index=17,
        section_id="Mechanism",
        purpose="Explain degradation",
        source_statement_references=[1],
        narration_excerpt=narration_excerpt,
        estimated_duration_seconds=3.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Diagram of degradation",
        on_screen_text=on_screen_text,
    )
    direction_view_0 = VisualDirection(
        scene_index=17,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale="Mechanism explanation",
        metadata={
            "semantic_role": BeatSemanticRole.MECHANISM.value,
            "beat_index": 0,
        },
    )
    unit_0 = BeatRenderUnit(
        parent_scene_index=17,
        materialized_index=0,
        source_beat_index=0,
        start_ms=0,
        end_ms=3000,
        duration_ms=3000,
        scene_view=scene_view_0,
        direction_view=direction_view_0,
        asset_decision=BeatAssetDecision(
            parent_scene_index=17,
            beat_index=0,
            rationale="Local template only",
            action=BeatAssetAction.LOCAL_TEMPLATE,
            required_kind=None,
            reuse_from_beat_index=None,
            query_hint=None,
        ),
        camera_motion_intent=BeatMotionIntent.STATIC,
        transition_intent=BeatTransitionIntent.HARD_CUT,
    )

    scene_view_1 = StoryboardScene(
        sequence_index=17,
        section_id="Mechanism",
        purpose="Context continuation",
        source_statement_references=[2],
        narration_excerpt="Engineers inspect structural elements regularly.",
        estimated_duration_seconds=2.0,
        visual_strategy=VisualStrategy.KINETIC_TEXT,
        visual_brief="Inspection note",
    )
    direction_view_1 = VisualDirection(
        scene_index=17,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.KINETIC_TEXT,
        asset_requirements=[],
        motion_profile="kinetic_phrase",
        rationale="Context",
        metadata={
            "semantic_role": BeatSemanticRole.CONTEXT.value,
            "beat_index": 1,
        },
    )
    unit_1 = BeatRenderUnit(
        parent_scene_index=17,
        materialized_index=1,
        source_beat_index=1,
        start_ms=3000,
        end_ms=5000,
        duration_ms=2000,
        scene_view=scene_view_1,
        direction_view=direction_view_1,
        asset_decision=BeatAssetDecision(
            parent_scene_index=17,
            beat_index=1,
            rationale="Local template only",
            action=BeatAssetAction.LOCAL_TEMPLATE,
            required_kind=None,
            reuse_from_beat_index=None,
            query_hint=None,
        ),
        camera_motion_intent=BeatMotionIntent.STATIC,
        transition_intent=BeatTransitionIntent.HARD_CUT,
    )

    return BeatRenderPlan(
        parent_scene_index=17,
        units=(unit_0, unit_1),
        total_duration_ms=5000,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("on_screen_text", [None, "Authored inspection guidance"])
async def test_runtime_render_parent_visual_multi_beat_unresolvable_flow_diagram_falls_back_to_kinetic_text(tmp_path, on_screen_text):
    """Real frozen models survive fallback without mutating prepared truth."""
    plan = _build_multi_beat_plan_with_diagram(SCENE17_CANARY_PROSE, on_screen_text)
    parent_scene = StoryboardScene(
        sequence_index=17,
        section_id="Mechanism",
        purpose="Explain degradation",
        source_statement_references=[1, 2],
        narration_excerpt=SCENE17_CANARY_PROSE,
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Structural mechanism explanation",
    )

    original_parent_scene = parent_scene.model_dump()
    prep = MagicMock()
    preparation = CanonicalBeatPreparationResult(eligible=True, render_plan=plan)
    original_preparation = preparation.model_dump()
    prep.prepare_from_script_dict.return_value = preparation

    executor = MagicMock()
    executed_assets = [
        SimpleNamespace(
            action=BeatAssetAction.LOCAL_TEMPLATE,
            required_kind=None,
            reuse_from_beat_index=None,
            resolved_asset=None,
        ),
        SimpleNamespace(
            action=BeatAssetAction.LOCAL_TEMPLATE,
            required_kind=None,
            reuse_from_beat_index=None,
            resolved_asset=None,
        ),
    ]
    executor.execute_plan = AsyncMock(
        return_value=SimpleNamespace(parent_scene_index=17, assets=tuple(executed_assets))
    )

    renderer = MagicMock()
    rendered_metadata = [
        SimpleNamespace(template_id=VisualTemplateId.KINETIC_TEXT, video_sha256="1" * 64),
        SimpleNamespace(template_id=VisualTemplateId.KINETIC_TEXT, video_sha256="2" * 64),
    ]
    renderer.render_plan = AsyncMock(
        return_value=SimpleNamespace(clips=(0, 1), beat_metadata=tuple(rendered_metadata))
    )

    assembler = MagicMock()
    async def _assemble(_clips, *, output_path, fps):
        Path(output_path).write_bytes(b"assembled-video")
        return SimpleNamespace(
            parent_scene_index=17,
            expected_duration_ms=5000,
            content_sha256="f" * 64,
        )
    assembler.assemble = AsyncMock(side_effect=_assemble)

    service = _make_mock_service(tmp_path, prep, executor, renderer, assembler)

    result = await service._render_parent_visual(
        script_dict={"sections": []},
        scene=parent_scene,
        duration_seconds=5.0,
        canonical_visual_mode="LOCAL_TEMPLATE_ONLY",
        work_dir=tmp_path,
        browser=MagicMock(),
        fps=24,
        style_profile=None,
        narration_enabled=True,
    )

    # 1. Execution completed successfully through multi-beat path
    assert result["execution_mode"] == "MULTI_BEAT"

    # Original preparation, plan, unit and nested views retain prepared truth.
    assert parent_scene.model_dump() == original_parent_scene
    assert preparation.model_dump() == original_preparation
    assert preparation.render_plan is plan
    unit_0 = plan.units[0]
    assert unit_0.direction_view.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert unit_0.scene_view.visual_strategy == VisualStrategy.DIAGRAM
    assert unit_0.scene_view.on_screen_text == on_screen_text

    executor.execute_plan.assert_awaited_once()
    renderer.render_plan.assert_awaited_once()
    assembler.assemble.assert_awaited_once_with(
        renderer.render_plan.return_value.clips,
        output_path=tmp_path / "scene_017_visual.mp4",
        fps=24,
    )
    rendered_plan = renderer.render_plan.call_args.kwargs["render_plan"]
    assert isinstance(rendered_plan, BeatRenderPlan)
    assert rendered_plan is not plan
    assert executor.execute_plan.call_args.kwargs["render_plan"] is rendered_plan
    assert executor.execute_plan.call_args.kwargs["provider_acquisition_allowed"] is False
    replacement = rendered_plan.units[0]
    assert isinstance(replacement, BeatRenderUnit)
    assert replacement is not unit_0
    assert replacement.direction_view is not unit_0.direction_view
    assert replacement.scene_view is not unit_0.scene_view
    assert replacement.direction_view.template_id == VisualTemplateId.KINETIC_TEXT
    assert replacement.direction_view.render_mode == VisualRenderMode.TEMPLATE
    assert replacement.scene_view.visual_strategy == VisualStrategy.KINETIC_TEXT
    assert replacement.scene_view.on_screen_text == (on_screen_text or SCENE17_CANARY_PROSE)
    assert replacement.scene_view.narration_excerpt == unit_0.scene_view.narration_excerpt
    assert resolve_mechanism_diagram_spec(replacement.scene_view.narration_excerpt) is None
    assert replacement.asset_decision is unit_0.asset_decision
    assert replacement.start_ms == unit_0.start_ms
    assert replacement.end_ms == unit_0.end_ms
    assert rendered_plan.units[1] is plan.units[1]
    assert rendered_plan.total_duration_ms == plan.total_duration_ms
    assert result["runtime_beats"][0].template_id == VisualTemplateId.KINETIC_TEXT.value


@pytest.mark.asyncio
async def test_runtime_render_parent_visual_multi_beat_resolvable_flow_diagram_remains_flow_diagram(tmp_path):
    """When a multi-beat unit has genuine resolvable diagram prose, it remains FLOW_DIAGRAM."""
    plan = _build_multi_beat_plan_with_diagram(REAL_MECHANISM_PROSE)
    parent_scene = StoryboardScene(
        sequence_index=17,
        section_id="Mechanism",
        purpose="Explain degradation",
        source_statement_references=[1, 2],
        narration_excerpt=REAL_MECHANISM_PROSE,
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Structural mechanism explanation",
    )

    original_parent_scene = parent_scene.model_dump()
    prep = MagicMock()
    preparation = CanonicalBeatPreparationResult(eligible=True, render_plan=plan)
    original_preparation = preparation.model_dump()
    prep.prepare_from_script_dict.return_value = preparation

    executor = MagicMock()
    executed_assets = [
        SimpleNamespace(
            action=BeatAssetAction.LOCAL_TEMPLATE,
            required_kind=None,
            reuse_from_beat_index=None,
            resolved_asset=None,
        ),
        SimpleNamespace(
            action=BeatAssetAction.LOCAL_TEMPLATE,
            required_kind=None,
            reuse_from_beat_index=None,
            resolved_asset=None,
        ),
    ]
    executor.execute_plan = AsyncMock(
        return_value=SimpleNamespace(parent_scene_index=17, assets=tuple(executed_assets))
    )

    renderer = MagicMock()
    rendered_metadata = [
        SimpleNamespace(template_id=VisualTemplateId.FLOW_DIAGRAM, video_sha256="1" * 64),
        SimpleNamespace(template_id=VisualTemplateId.KINETIC_TEXT, video_sha256="2" * 64),
    ]
    renderer.render_plan = AsyncMock(
        return_value=SimpleNamespace(clips=(0, 1), beat_metadata=tuple(rendered_metadata))
    )

    assembler = MagicMock()
    async def _assemble(_clips, *, output_path, fps):
        Path(output_path).write_bytes(b"assembled-video")
        return SimpleNamespace(
            parent_scene_index=17,
            expected_duration_ms=5000,
            content_sha256="f" * 64,
        )
    assembler.assemble = AsyncMock(side_effect=_assemble)

    service = _make_mock_service(tmp_path, prep, executor, renderer, assembler)

    result = await service._render_parent_visual(
        script_dict={"sections": []},
        scene=parent_scene,
        duration_seconds=5.0,
        canonical_visual_mode="LOCAL_TEMPLATE_ONLY",
        work_dir=tmp_path,
        browser=MagicMock(),
        fps=24,
        style_profile=None,
        narration_enabled=True,
    )

    assert result["execution_mode"] == "MULTI_BEAT"
    unit_0 = plan.units[0]
    assert unit_0.direction_view.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert unit_0.direction_view.render_mode == VisualRenderMode.TEMPLATE
    assert unit_0.scene_view.visual_strategy == VisualStrategy.DIAGRAM

    assert parent_scene.model_dump() == original_parent_scene
    assert preparation.model_dump() == original_preparation
    executor.execute_plan.assert_awaited_once()
    renderer.render_plan.assert_awaited_once()
    assembler.assemble.assert_awaited_once()
    assert executor.execute_plan.call_args.kwargs["render_plan"] is plan
    assert renderer.render_plan.call_args.kwargs["render_plan"] is plan
    assert executor.execute_plan.call_args.kwargs["provider_acquisition_allowed"] is False


def test_real_beat_models_forbid_field_assignment():
    plan = _build_multi_beat_plan_with_diagram(SCENE17_CANARY_PROSE)
    with pytest.raises(ValidationError) as unit_error:
        plan.units[0].direction_view = plan.units[0].direction_view.model_copy()
    assert unit_error.value.errors()[0]["type"] == "frozen_instance"
    with pytest.raises(ValidationError) as plan_error:
        plan.units = ()
    assert plan_error.value.errors()[0]["type"] == "frozen_instance"


@pytest.mark.asyncio
async def test_parent_scene_diagram_fallback_copies_scene_before_preparation(tmp_path):
    scene = _build_multi_beat_plan_with_diagram(SCENE17_CANARY_PROSE).units[0].scene_view
    original = scene.model_dump()
    prep = MagicMock()
    prep.prepare_from_script_dict.return_value = CanonicalBeatPreparationResult(eligible=False)
    executor, renderer, assembler = MagicMock(), MagicMock(), MagicMock()
    service = _make_mock_service(tmp_path, prep, executor, renderer, assembler)
    service._video_renderer.render_clip.return_value = SimpleNamespace(
        video_sha256="f" * 64, width=1920, height=1080,
    )
    result = await service._render_parent_visual(
        script_dict={"sections": []}, scene=scene, duration_seconds=3.0,
        canonical_visual_mode="LOCAL_TEMPLATE_ONLY", work_dir=tmp_path,
        browser=MagicMock(), fps=24, style_profile=None, narration_enabled=True,
    )
    assert scene.model_dump() == original
    prepared_scene = prep.prepare_from_script_dict.call_args.kwargs["scene"]
    assert prepared_scene is not scene
    assert prepared_scene.visual_strategy == VisualStrategy.KINETIC_TEXT
    assert prepared_scene.on_screen_text == SCENE17_CANARY_PROSE
    assert result["template_id"] == VisualTemplateId.KINETIC_TEXT.value
    service._video_renderer.render_clip.assert_awaited_once()
    service._orchestrator.resolve.assert_not_called()
