"""Application-layer Beat Render Adapter for physical multi-beat visual storytelling.

Adapts G2A EditorialBeatPlan, timing, G2B BeatVisualDirectionPlan, and BeatAssetPlan
into renderer-compatible StoryboardScene and VisualDirection views without modifying
underlying template renderers or canonical production paths.
Enforces deterministic one-to-one timing eligibility gates and viewer-facing text safety.
Pure logic: zero I/O, zero network, zero external providers.
"""

from __future__ import annotations

from collections.abc import Sequence

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
    VisualTemplateId,
    is_meaningful_query,
)
from omega.domain.camera_transition import (
    CameraIntent,
    CameraPlan,
    FocusRegion,
    MotionStrength,
    TransitionIntent,
    TransitionPlan,
)

_CAMERA_CAPABLE_TEMPLATES = frozenset(
    {
        VisualTemplateId.IMAGE_EXPLAINER,
        VisualTemplateId.BROLL_EXPLAINER,
        VisualTemplateId.STATISTIC_HERO,
        VisualTemplateId.CODE_EDITOR,
    }
)


def adapt_camera_plan(
    plan: CameraPlan,
    template_id: VisualTemplateId | None,
    *,
    legacy_motion_intent: BeatMotionIntent | None = None,
) -> tuple[BeatMotionIntent, FocusRegion | None, MotionStrength, str | None]:
    """Translate a renderer-neutral CameraPlan with deterministic static fallback."""
    if template_id not in _CAMERA_CAPABLE_TEMPLATES:
        return (
            BeatMotionIntent.STATIC,
            None,
            MotionStrength.SUBTLE,
            "UNSUPPORTED_TEMPLATE_STATIC_FALLBACK",
        )
    if (
        plan.intent == CameraIntent.STATIC
        and plan.preferred_asset_type == "BROLL"
        and legacy_motion_intent is not None
    ):
        # Existing BeatVisualDirector motion remains authoritative for B-roll
        # when P22-B adds no more specific camera instruction.
        return legacy_motion_intent, None, plan.strength, None
    mapping = {
        CameraIntent.STATIC: BeatMotionIntent.STATIC,
        CameraIntent.PUSH_IN: BeatMotionIntent.SLOW_PUSH_IN,
        CameraIntent.PULL_OUT: BeatMotionIntent.SLOW_PULL_OUT,
        CameraIntent.PAN_LEFT: BeatMotionIntent.PAN_LEFT,
        CameraIntent.PAN_RIGHT: BeatMotionIntent.PAN_RIGHT,
        CameraIntent.PAN_UP: BeatMotionIntent.PAN_UP,
        CameraIntent.PAN_DOWN: BeatMotionIntent.PAN_DOWN,
        CameraIntent.REFRAME: BeatMotionIntent.FOCAL_ZOOM,
        CameraIntent.DETAIL_FOCUS: BeatMotionIntent.FOCAL_ZOOM,
        CameraIntent.RETURN_TO_CONTEXT: BeatMotionIntent.SLOW_PULL_OUT,
    }
    mapped = mapping.get(plan.intent)
    if mapped is None:
        return (
            BeatMotionIntent.STATIC,
            None,
            MotionStrength.SUBTLE,
            "UNSUPPORTED_CAMERA_INTENT_STATIC_FALLBACK",
        )
    return mapped, plan.focus_region, plan.strength, None


def adapt_transition_plan(plan: TransitionPlan) -> tuple[BeatTransitionIntent, str | None]:
    """Translate supported physical transitions; every unsupported intent becomes CUT."""
    if plan.applied_intent != TransitionIntent.CUT:
        return BeatTransitionIntent.HARD_CUT, "UNSUPPORTED_TRANSITION_CUT_FALLBACK"
    return BeatTransitionIntent.HARD_CUT, plan.fallback_reason


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
        subject_text=parent_scene.subject_text,
        importance=parent_scene.importance,
        citations=list(parent_scene.citations),
    )


def adapt_visual_direction_view(
    *,
    beat_direction: BeatVisualDirection,
    parent_scene_index: int,
) -> VisualDirection:
    """Adapt BeatVisualDirection into standard VisualDirection for TemplatePayloadResolver."""
    is_section_entry = (beat_direction.beat_index == 0 and parent_scene_index == 1) or (
        beat_direction.semantic_role.value in ("HOOK_TITLE", "CHAPTER_TRANSITION")
    )
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
            "is_section_entry": is_section_entry,
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
    camera_plan: CameraPlan | None = None
    transition_plan: TransitionPlan | None = None
    camera_focus_region: FocusRegion | None = None
    camera_motion_strength: MotionStrength = MotionStrength.MODERATE
    camera_fallback_reason: str | None = None
    transition_fallback_reason: str | None = None


class BeatRenderPlan(BaseModel):
    """Immutable physical rendering plan for all beats in a scene."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    units: tuple[BeatRenderUnit, ...] = Field(default_factory=tuple)
    total_duration_ms: int = Field(gt=0, description="Total scene duration in milliseconds")


class BeatRenderPlanResult(BaseModel):
    """Deterministic result of evaluating beat render eligibility."""

    model_config = ConfigDict(frozen=True)

    eligible: bool = Field(
        description="Whether the scene is eligible for multi-beat physical rendering"
    )
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
        camera_plans: Sequence[CameraPlan] | None = None,
        transition_plans: Sequence[TransitionPlan] | None = None,
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

        if camera_plans is not None and len(camera_plans) != len(beat_plan.beats):
            return BeatRenderPlanResult(
                eligible=False, fallback_reason="CAMERA_PLAN_COUNT_MISMATCH", plan=None
            )
        if transition_plans is not None and len(transition_plans) != len(beat_plan.beats):
            return BeatRenderPlanResult(
                eligible=False, fallback_reason="TRANSITION_PLAN_COUNT_MISMATCH", plan=None
            )

        # 1. One-to-one count check
        if len(timing_plan.timings) != len(beat_plan.beats):
            return BeatRenderPlanResult(
                eligible=False,
                fallback_reason="MERGED_BEAT_TIMING_UNSUPPORTED",
                plan=None,
            )

        if len(direction_plan.directions) != len(beat_plan.beats) or len(
            asset_plan.decisions
        ) != len(beat_plan.beats):
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

            # Legacy callers retain the prior strict contract. P22-B plans carry an
            # explicit applied transition and deterministic fallback at this boundary.
            if transition_plans is None and beat.transition_intent != BeatTransitionIntent.HARD_CUT:
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

            camera_plan = camera_plans[i] if camera_plans is not None else None
            transition_plan = transition_plans[i] if transition_plans is not None else None
            if camera_plan is not None and (
                camera_plan.beat_index != beat.beat_index
                or camera_plan.parent_scene_index != scene_idx
                or camera_plan.source_editorial_beat_indices != timing.source_beat_indices
            ):
                return BeatRenderPlanResult(
                    eligible=False, fallback_reason="CAMERA_PLAN_IDENTITY_MISMATCH", plan=None
                )
            if transition_plan is not None and (
                transition_plan.beat_index != beat.beat_index
                or transition_plan.parent_scene_index != scene_idx
                or transition_plan.source_editorial_beat_indices != timing.source_beat_indices
            ):
                return BeatRenderPlanResult(
                    eligible=False, fallback_reason="TRANSITION_PLAN_IDENTITY_MISMATCH", plan=None
                )

            if camera_plan is None:
                camera_intent = direction.camera_motion_intent
                focus_region = None
                motion_strength = MotionStrength.MODERATE
                camera_fallback = None
            else:
                camera_intent, focus_region, motion_strength, camera_fallback = adapt_camera_plan(
                    camera_plan,
                    direction_view.template_id,
                    legacy_motion_intent=direction.camera_motion_intent,
                )
            if transition_plan is None:
                transition_intent = beat.transition_intent
                transition_fallback = None
            else:
                transition_intent, transition_fallback = adapt_transition_plan(transition_plan)

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
                camera_motion_intent=camera_intent,
                transition_intent=transition_intent,
                camera_plan=camera_plan,
                transition_plan=transition_plan,
                camera_focus_region=focus_region,
                camera_motion_strength=motion_strength,
                camera_fallback_reason=camera_fallback,
                transition_fallback_reason=transition_fallback,
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
