"""Application-layer Beat Render Adapter for physical multi-beat visual storytelling.

Adapts G2A EditorialBeatPlan, timing, G2B BeatVisualDirectionPlan, and BeatAssetPlan
into renderer-compatible StoryboardScene and VisualDirection views without modifying
underlying template renderers or canonical production paths.
Enforces deterministic one-to-one timing eligibility gates and viewer-facing text safety.
Pure logic: zero I/O, zero network, zero external providers.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omega.application.beat_asset_policy import BeatAssetDecision, BeatAssetPlan
from omega.application.beat_visual_direction import (
    BeatVisualDirection,
    BeatVisualDirectionPlan,
)
from omega.application.editorial_beat import (
    BeatMotionIntent,
    BeatTransitionIntent,
    EditorialBeatPlan,
    EditorialBeatSpec,
    EditorialBeatTiming,
    MaterializedBeatTimingPlan,
)
from omega.application.storyboard_engine import StoryboardScene
from omega.application.visual_direction import (
    VisualDirection,
    is_meaningful_query,
)


def resolve_beat_on_screen_text(
    *,
    parent_on_screen_text: str | None,
    parent_narration: str,
) -> str | None:
    """Safely resolve viewer-facing on_screen_text for beat view.

    Prevents duplicating full narration into template body text (P18-G1 hardening).
    Preserves explicit distinct authored text; suppresses verbatim narration copies.
    Never synthesizes a body paragraph from beat narration.
    """
    if not parent_on_screen_text or not parent_on_screen_text.strip():
        return None
    cleaned_ost = parent_on_screen_text.strip()
    ost_norm = " ".join(cleaned_ost.split()).lower()
    narr_norm = " ".join(parent_narration.strip().split()).lower()
    if ost_norm == narr_norm:
        return None
    return cleaned_ost


def adapt_storyboard_scene_view(
    *,
    parent_scene: StoryboardScene,
    beat: EditorialBeatSpec,
    timing: EditorialBeatTiming,
    asset_decision: BeatAssetDecision,
) -> StoryboardScene:
    """Construct a temporary beat-specific StoryboardScene view.

    Carries parent section identity and citations while binding beat-level narration
    excerpt, visual strategy, duration, and safe viewer-facing on_screen_text.
    """
    strategy = beat.preferred_visual_strategy or parent_scene.visual_strategy
    duration_s = timing.duration_ms / 1000.0

    # Resolve meaningful query hint
    query_hint = None
    if is_meaningful_query(asset_decision.query_hint):
        query_hint = asset_decision.query_hint
    elif is_meaningful_query(beat.asset_query_hint):
        query_hint = beat.asset_query_hint
    elif is_meaningful_query(parent_scene.asset_query_hint):
        query_hint = parent_scene.asset_query_hint

    safe_ost = resolve_beat_on_screen_text(
        parent_on_screen_text=parent_scene.on_screen_text,
        parent_narration=parent_scene.narration_excerpt,
    )

    return StoryboardScene(
        sequence_index=parent_scene.sequence_index,
        section_id=parent_scene.section_id,
        purpose=parent_scene.purpose,
        source_statement_references=list(beat.source_statement_references),
        narration_excerpt=beat.narration_span,
        estimated_duration_seconds=duration_s,
        visual_strategy=strategy,
        visual_brief=parent_scene.visual_brief,
        on_screen_text=safe_ost,
        motion_hint=parent_scene.motion_hint,
        asset_query_hint=query_hint,
        importance=parent_scene.importance,
        citations=list(parent_scene.citations),
    )


def adapt_visual_direction_view(
    *,
    beat_direction: BeatVisualDirection,
    parent_scene_index: int,
) -> VisualDirection:
    """Adapt BeatVisualDirection into standard VisualDirection for TemplatePayloadResolver."""
    return VisualDirection(
        scene_index=parent_scene_index,
        render_mode=beat_direction.render_mode,
        template_id=beat_direction.template_id,
        asset_requirements=list(beat_direction.asset_requirements),
        motion_profile=beat_direction.template_motion_profile,
        rationale=beat_direction.rationale,
        metadata={
            **beat_direction.metadata,
            "beat_index": beat_direction.beat_index,
            "semantic_role": beat_direction.semantic_role.value,
        },
    )


class BeatRenderUnit(BaseModel):
    """Immutable physical rendering unit for a single visual beat."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    materialized_index: int = Field(ge=0, description="0-indexed position within scene timing plan")
    source_beat_index: int = Field(ge=0, description="0-indexed source editorial beat index")
    start_ms: int = Field(ge=0, description="Beat start offset in milliseconds from scene start")
    end_ms: int = Field(gt=0, description="Beat end offset in milliseconds from scene start")
    duration_ms: int = Field(gt=0, description="Beat duration in milliseconds")
    scene_view: StoryboardScene = Field(description="Temporary beat-scoped StoryboardScene view")
    direction_view: VisualDirection = Field(description="Adapted standard VisualDirection")
    asset_decision: BeatAssetDecision = Field(description="Deterministic asset policy decision")
    camera_motion_intent: BeatMotionIntent = Field(
        description="Preserved camera motion intent for G2C2"
    )
    transition_intent: BeatTransitionIntent = Field(description="Transition intent into this beat")


class BeatRenderPlan(BaseModel):
    """Immutable physical rendering plan for all beats in a scene."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    units: tuple[BeatRenderUnit, ...] = Field(default_factory=tuple)
    total_duration_ms: int = Field(gt=0, description="Total scene duration in milliseconds")


class BeatRenderPlanResult(BaseModel):
    """Deterministic result of evaluating beat render eligibility."""

    model_config = ConfigDict(frozen=True)

    eligible: bool = Field(description="Whether the scene is eligible for multi-beat physical rendering")
    fallback_reason: str | None = Field(
        default=None, description="Deterministic reason for fallback/ineligibility if not eligible"
    )
    plan: BeatRenderPlan | None = Field(
        default=None, description="Materialized BeatRenderPlan when eligible"
    )


class BeatRenderAdapter:
    """Pure deterministic adapter evaluating timing eligibility and generating BeatRenderPlan."""

    @classmethod
    def adapt(
        cls,
        *,
        scene: StoryboardScene,
        beat_plan: EditorialBeatPlan,
        timing_plan: MaterializedBeatTimingPlan,
        direction_plan: BeatVisualDirectionPlan,
        asset_plan: BeatAssetPlan,
    ) -> BeatRenderPlanResult:
        """Evaluate one-to-one timing gate and adapt beat models into BeatRenderPlan."""
        scene_idx = scene.sequence_index

        # Validate parent scene identity across all plans
        if (
            beat_plan.scene_index != scene_idx
            or timing_plan.scene_index != scene_idx
            or direction_plan.parent_scene_index != scene_idx
            or asset_plan.parent_scene_index != scene_idx
        ):
            return BeatRenderPlanResult(
                eligible=False,
                fallback_reason="SCENE_INDEX_MISMATCH",
                plan=None,
            )

        # 1. One-to-one count check
        if len(timing_plan.timings) != len(beat_plan.beats):
            return BeatRenderPlanResult(
                eligible=False,
                fallback_reason="MERGED_BEAT_TIMING_UNSUPPORTED",
                plan=None,
            )

        if (
            len(direction_plan.directions) != len(beat_plan.beats)
            or len(asset_plan.decisions) != len(beat_plan.beats)
        ):
            return BeatRenderPlanResult(
                eligible=False,
                fallback_reason="BEAT_PLAN_COUNT_MISMATCH",
                plan=None,
            )

        units: list[BeatRenderUnit] = []

        for i, beat in enumerate(beat_plan.beats):
            timing = timing_plan.timings[i]
            direction = direction_plan.directions[i]
            decision = asset_plan.decisions[i]

            # 1. Identity & timing consistency
            if (
                beat.parent_scene_index != scene_idx
                or direction.parent_scene_index != scene_idx
                or decision.parent_scene_index != scene_idx
            ):
                return BeatRenderPlanResult(
                    eligible=False,
                    fallback_reason="PLAN_IDENTITY_MISMATCH",
                    plan=None,
                )

            # Strict 1-to-1 timing provenance
            if timing.source_beat_indices != (beat.beat_index,):
                return BeatRenderPlanResult(
                    eligible=False,
                    fallback_reason="MERGED_BEAT_TIMING_UNSUPPORTED",
                    plan=None,
                )

            if direction.beat_index != beat.beat_index or decision.beat_index != beat.beat_index:
                return BeatRenderPlanResult(
                    eligible=False,
                    fallback_reason="PLAN_IDENTITY_MISMATCH",
                    plan=None,
                )

            # 2. Source statement references consistency
            if direction.source_statement_references != beat.source_statement_references:
                return BeatRenderPlanResult(
                    eligible=False,
                    fallback_reason="PLAN_SEMANTIC_MISMATCH",
                    plan=None,
                )

            # 3. Semantic role consistency
            if direction.semantic_role != beat.semantic_role:
                return BeatRenderPlanResult(
                    eligible=False,
                    fallback_reason="PLAN_SEMANTIC_MISMATCH",
                    plan=None,
                )

            # 4. Motion intent consistency
            if direction.camera_motion_intent != beat.motion_intent:
                return BeatRenderPlanResult(
                    eligible=False,
                    fallback_reason="PLAN_MOTION_MISMATCH",
                    plan=None,
                )

            # 5. Transition intent consistency
            if direction.transition_intent != beat.transition_intent:
                return BeatRenderPlanResult(
                    eligible=False,
                    fallback_reason="PLAN_TRANSITION_MISMATCH",
                    plan=None,
                )

            # Transition intent support: G2C1 supports HARD_CUT only
            if beat.transition_intent != BeatTransitionIntent.HARD_CUT:
                return BeatRenderPlanResult(
                    eligible=False,
                    fallback_reason="UNSUPPORTED_TRANSITION_INTENT",
                    plan=None,
                )

            # 6. Asset decision / visual direction consistency
            from omega.application.beat_asset_policy import BeatAssetAction

            if len(direction.asset_requirements) == 1:
                req_kind = direction.asset_requirements[0].kind
                if decision.action in (
                    BeatAssetAction.ACQUIRE_IF_NEEDED,
                    BeatAssetAction.REUSE_COMPATIBLE,
                ):
                    if decision.required_kind != req_kind:
                        return BeatRenderPlanResult(
                            eligible=False,
                            fallback_reason="ASSET_DIRECTION_INCONSISTENT",
                            plan=None,
                        )
                elif decision.action in (BeatAssetAction.LOCAL_TEMPLATE, BeatAssetAction.NONE):
                    return BeatRenderPlanResult(
                        eligible=False,
                        fallback_reason="ASSET_DIRECTION_INCONSISTENT",
                        plan=None,
                    )
            elif len(direction.asset_requirements) == 0:
                if decision.action in (
                    BeatAssetAction.ACQUIRE_IF_NEEDED,
                    BeatAssetAction.REUSE_COMPATIBLE,
                ):
                    return BeatRenderPlanResult(
                        eligible=False,
                        fallback_reason="ASSET_DIRECTION_INCONSISTENT",
                        plan=None,
                    )

            scene_view = adapt_storyboard_scene_view(
                parent_scene=scene,
                beat=beat,
                timing=timing,
                asset_decision=decision,
            )

            direction_view = adapt_visual_direction_view(
                beat_direction=direction,
                parent_scene_index=scene_idx,
            )

            unit = BeatRenderUnit(
                parent_scene_index=scene_idx,
                materialized_index=timing.materialized_index,
                source_beat_index=beat.beat_index,
                start_ms=timing.start_ms,
                end_ms=timing.end_ms,
                duration_ms=timing.duration_ms,
                scene_view=scene_view,
                direction_view=direction_view,
                asset_decision=decision,
                camera_motion_intent=direction.camera_motion_intent,
                transition_intent=beat.transition_intent,
            )
            units.append(unit)

        render_plan = BeatRenderPlan(
            parent_scene_index=scene_idx,
            units=tuple(units),
            total_duration_ms=timing_plan.scene_duration_ms,
        )

        return BeatRenderPlanResult(
            eligible=True,
            fallback_reason=None,
            plan=render_plan,
        )
