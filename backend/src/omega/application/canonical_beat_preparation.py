"""Application-layer Canonical Beat Preparation Service.

Deterministically coordinates the G2 pipeline layers:
EditorialBeatPlanner -> allocate_timing -> VisualBeatProjector -> VisualContinuityDirector
-> BeatVisualDirector -> BeatAssetPolicy -> BeatRenderAdapter
into an immutable CanonicalBeatPreparationResult.

Enforces:
- Visual asset mode gating (PEXELS vs LOCAL_TEMPLATE_ONLY);
- External asset kind capability validation (IMAGE and BROLL supported, SCREENSHOT unsupported);
- Strict propagation of eligibility and deterministic fallback reasons;
- Authoritative scene duration consumption (>0 required).

Pure logic: zero TTS, zero DB, zero provider/network, zero FFmpeg, zero browser.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from omega.application.beat_asset_policy import (
    BeatAssetAction,
    BeatAssetPlan,
    BeatAssetPolicy,
    BeatAssetPolicyError,
)
from omega.application.beat_render_adapter import (
    BeatRenderAdapter,
    BeatRenderPlan,
)
from omega.application.beat_source_resolver import resolve_scene_source_statements
from omega.application.beat_visual_direction import (
    BeatVisualDirectionPlan,
    BeatVisualDirector,
)
from omega.application.editorial_beat import (
    EditorialBeatPlan,
    MaterializedBeatTimingPlan,
)
from omega.application.editorial_beat_planner import EditorialBeatPlanner
from omega.application.storyboard_engine import StoryboardScene
from omega.application.visual_continuity_director import (
    VisualBeatProjector,
    VisualContinuityDirector,
    VisualDirectorBeatAdapter,
)
from omega.application.visual_direction import VisualAssetKind
from omega.domain.visual_beat import VisualBeatSequence


class CanonicalBeatPreparationResult(BaseModel):
    """Immutable result of preparing multi-beat execution plans for a StoryboardScene."""

    model_config = ConfigDict(frozen=True)

    eligible: bool = Field(
        description="Whether the scene is eligible for multi-beat physical execution"
    )
    fallback_reason: str | None = Field(
        default=None, description="Deterministic fallback reason if ineligible"
    )
    source_statements: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple, description="Resolved source statements used for planning"
    )
    beat_plan: EditorialBeatPlan | None = Field(default=None, description="Editorial beat plan")
    timing_plan: MaterializedBeatTimingPlan | None = Field(
        default=None, description="Materialized beat timing plan"
    )
    visual_beat_sequence: VisualBeatSequence | None = Field(
        default=None, description="Visual continuity projection of materialized editorial beats"
    )
    direction_plan: BeatVisualDirectionPlan | None = Field(
        default=None, description="Beat visual direction plan"
    )
    asset_plan: BeatAssetPlan | None = Field(default=None, description="Beat asset policy plan")
    render_plan: BeatRenderPlan | None = Field(
        default=None, description="Materialized beat render plan when eligible"
    )


class CanonicalBeatPreparationService:
    """Deterministic orchestrator for canonical-compatible beat preparation."""

    @classmethod
    def prepare_from_script_dict(
        cls,
        *,
        script_dict: dict[str, Any],
        scene: StoryboardScene,
        scene_duration_ms: int,
        visual_asset_mode: str,
    ) -> CanonicalBeatPreparationResult:
        """Deterministically resolve source statements from script_dict and prepare execution plan.

        Enforces that source resolution cannot be bypassed in canonical composition.
        If resolution fails, EditorialBeatPlanner is never called.
        """
        resolution = resolve_scene_source_statements(
            script_dict=script_dict,
            scene=scene,
        )
        if not resolution.resolved:
            return CanonicalBeatPreparationResult(
                eligible=False,
                fallback_reason=resolution.failure_reason,
                source_statements=(),
            )

        return cls.prepare(
            scene=scene,
            source_statements=resolution.statements,
            scene_duration_ms=scene_duration_ms,
            visual_asset_mode=visual_asset_mode,
        )

    @classmethod
    def prepare(
        cls,
        *,
        scene: StoryboardScene,
        source_statements: Sequence[Any],
        scene_duration_ms: int,
        visual_asset_mode: str,
    ) -> CanonicalBeatPreparationResult:
        """Deterministically prepare the complete G2 execution plan for a StoryboardScene."""
        if scene_duration_ms <= 0:
            raise ValueError(f"scene_duration_ms must be > 0, got {scene_duration_ms}")

        stmt_list = [dict(s) if isinstance(s, dict) else s for s in source_statements]
        if not stmt_list:
            return CanonicalBeatPreparationResult(
                eligible=False,
                fallback_reason="SOURCE_STATEMENTS_NOT_RESOLVED",
                source_statements=(),
            )

        # 1. Editorial Beat Planning
        try:
            beat_plan = EditorialBeatPlanner.plan(
                scene=scene,
                source_statements=stmt_list,
            )
        except Exception as e:
            return CanonicalBeatPreparationResult(
                eligible=False,
                fallback_reason=f"BEAT_PLANNING_FAILED: {e}",
                source_statements=tuple(stmt_list),
            )

        # 2. Timing Allocation
        try:
            timing_plan = EditorialBeatPlanner.allocate_timing(
                beat_plan,
                scene_duration_ms=scene_duration_ms,
            )
        except Exception as e:
            return CanonicalBeatPreparationResult(
                eligible=False,
                fallback_reason=f"TIMING_ALLOCATION_FAILED: {e}",
                source_statements=tuple(stmt_list),
                beat_plan=beat_plan,
            )

        # 3. Visual beat projection and continuity enrichment
        try:
            projected_sequence = VisualBeatProjector().project(
                editorial_plan=beat_plan,
                timing_plan=timing_plan,
                scene=scene,
            )
            enriched_beats, continuity_findings = VisualContinuityDirector().analyze_sequence(
                projected_sequence
            )
            visual_beat_sequence = projected_sequence.model_copy(
                update={
                    "beats": enriched_beats,
                    "continuity_findings": continuity_findings,
                }
            )
        except Exception as e:
            return CanonicalBeatPreparationResult(
                eligible=False,
                fallback_reason=f"VISUAL_BEAT_PROJECTION_FAILED: {e}",
                source_statements=tuple(stmt_list),
                beat_plan=beat_plan,
                timing_plan=timing_plan,
            )

        # 4. Canonical visual direction, enriched by continuity for renderable 1:1 beats
        try:
            base_direction_plan = BeatVisualDirector.resolve_plan(
                scene=scene,
                beat_plan=beat_plan,
            )
            one_to_one = (
                len(visual_beat_sequence.beats) == len(beat_plan.beats)
                and all(
                    visual.source_editorial_beat_indices == (editorial.beat_index,)
                    for visual, editorial in zip(
                        visual_beat_sequence.beats, beat_plan.beats, strict=True
                    )
                )
            )
            if one_to_one:
                adapter = VisualDirectorBeatAdapter()
                directions = tuple(
                    adapter.enrich_beat_visual_direction(scene, visual, editorial)
                    for visual, editorial in zip(
                        visual_beat_sequence.beats, beat_plan.beats, strict=True
                    )
                )
                direction_plan = BeatVisualDirectionPlan(
                    parent_scene_index=scene.sequence_index,
                    directions=directions,
                )
            else:
                # Merged timing is already an explicit renderer fallback.  Keep the
                # canonical base direction plan for diagnostics without inventing a
                # second direction mapping for a non-renderable interval.
                direction_plan = base_direction_plan
        except Exception as e:
            return CanonicalBeatPreparationResult(
                eligible=False,
                fallback_reason=f"DIRECTION_PLANNING_FAILED: {e}",
                source_statements=tuple(stmt_list),
                beat_plan=beat_plan,
                timing_plan=timing_plan,
                visual_beat_sequence=visual_beat_sequence,
            )

        # 5. Asset Policy Planning
        try:
            asset_plan = BeatAssetPolicy.plan_assets(
                scene=scene,
                beat_plan=beat_plan,
                direction_plan=direction_plan,
            )
        except BeatAssetPolicyError as e:
            return CanonicalBeatPreparationResult(
                eligible=False,
                fallback_reason=str(e),
                source_statements=tuple(stmt_list),
                beat_plan=beat_plan,
                timing_plan=timing_plan,
                visual_beat_sequence=visual_beat_sequence,
                direction_plan=direction_plan,
            )

        # 6. Visual Asset Mode Enforcement
        mode = visual_asset_mode.strip().upper()
        if mode == "LOCAL_TEMPLATE_ONLY":
            for decision in asset_plan.decisions:
                if decision.action in (
                    BeatAssetAction.ACQUIRE_IF_NEEDED,
                    BeatAssetAction.REUSE_COMPATIBLE,
                ):
                    return CanonicalBeatPreparationResult(
                        eligible=False,
                        fallback_reason="EXTERNAL_ASSET_DISALLOWED_BY_MODE",
                        source_statements=tuple(stmt_list),
                        beat_plan=beat_plan,
                        timing_plan=timing_plan,
                        visual_beat_sequence=visual_beat_sequence,
                        direction_plan=direction_plan,
                        asset_plan=asset_plan,
                    )

        # 7. Unsupported Asset Kind Enforcement (e.g. SCREENSHOT)
        for decision in asset_plan.decisions:
            if (
                decision.action
                in (
                    BeatAssetAction.ACQUIRE_IF_NEEDED,
                    BeatAssetAction.REUSE_COMPATIBLE,
                )
                and decision.required_kind == VisualAssetKind.SCREENSHOT
            ):
                return CanonicalBeatPreparationResult(
                        eligible=False,
                        fallback_reason="UNSUPPORTED_BEAT_ASSET_KIND",
                        source_statements=tuple(stmt_list),
                        beat_plan=beat_plan,
                        timing_plan=timing_plan,
                        visual_beat_sequence=visual_beat_sequence,
                        direction_plan=direction_plan,
                        asset_plan=asset_plan,
                    )

        # 8. Render Adaptation
        adapt_res = BeatRenderAdapter.adapt(
            scene=scene,
            beat_plan=beat_plan,
            timing_plan=timing_plan,
            direction_plan=direction_plan,
            asset_plan=asset_plan,
        )

        if not adapt_res.eligible:
            return CanonicalBeatPreparationResult(
                eligible=False,
                fallback_reason=adapt_res.fallback_reason,
                source_statements=tuple(stmt_list),
                beat_plan=beat_plan,
                timing_plan=timing_plan,
                visual_beat_sequence=visual_beat_sequence,
                direction_plan=direction_plan,
                asset_plan=asset_plan,
                render_plan=None,
            )

        return CanonicalBeatPreparationResult(
            eligible=True,
            fallback_reason=None,
            source_statements=tuple(stmt_list),
            beat_plan=beat_plan,
            timing_plan=timing_plan,
            visual_beat_sequence=visual_beat_sequence,
            direction_plan=direction_plan,
            asset_plan=asset_plan,
            render_plan=adapt_res.plan,
        )
