"""Deterministic P22-B camera and transition direction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from omega.domain.camera_transition import (
    CameraFrameState,
    CameraIntent,
    CameraPlan,
    CameraTransitionPlan,
    FocusRegion,
    MotionFinding,
    MotionFindingCode,
    MotionStrength,
    TransitionIntent,
    TransitionPlan,
)
from omega.domain.narrative_pacing import PacingProfile
from omega.domain.narrative_plan import InformationDensity
from omega.domain.visual_beat import (
    ComparisonSide,
    ContinuityDecisionType,
    DocumentProgressStage,
    VisualBeat,
    VisualRole,
)

_MOVING = frozenset(CameraIntent) - {CameraIntent.STATIC}
_HORIZONTAL_DIRECTIONS = {
    CameraIntent.PAN_LEFT: -1,
    CameraIntent.PAN_RIGHT: 1,
}


class CameraTransitionDirector:
    """Derive recomputable camera/transition plans without taking visual authority."""

    @classmethod
    def direct(
        cls,
        beats: Sequence[VisualBeat],
        *,
        pacing: PacingProfile = PacingProfile.BALANCED,
        information_density: InformationDensity = InformationDensity.MEDIUM,
        document_stages: Mapping[int, DocumentProgressStage] | None = None,
        comparison_focus: Mapping[int, ComparisonSide] | None = None,
    ) -> CameraTransitionPlan:
        if not beats:
            raise ValueError("camera direction requires at least one VisualBeat")
        ordered = sorted(beats, key=lambda beat: beat.beat_index)
        scene_index = ordered[0].parent_scene_index
        if any(beat.parent_scene_index != scene_index for beat in ordered):
            raise ValueError("camera direction cannot cross parent scenes")

        doc_stages = document_stages or {}
        comparison_focus = comparison_focus or {}
        camera = tuple(
            cls._camera_plan(
                beat,
                pacing=pacing,
                density=information_density,
                document_stage=doc_stages.get(beat.beat_index),
                comparison_side=comparison_focus.get(beat.beat_index),
            )
            for beat in ordered
        )
        transitions = tuple(
            cls._transition_plan(ordered[index - 1] if index else None, beat, pacing)
            for index, beat in enumerate(ordered)
        )
        findings = cls._motion_findings(camera, pacing)
        return CameraTransitionPlan(
            parent_scene_index=scene_index,
            camera_plans=camera,
            transition_plans=transitions,
            findings=findings,
        )

    @classmethod
    def _camera_plan(
        cls,
        beat: VisualBeat,
        *,
        pacing: PacingProfile,
        density: InformationDensity,
        document_stage: DocumentProgressStage | None,
        comparison_side: ComparisonSide | None,
    ) -> CameraPlan:
        intent = CameraIntent.STATIC
        focus: FocusRegion | None = None
        purpose = "Hold a stable frame; no editorially justified camera move."
        asset_type = beat.preferred_asset_type.strip().upper()

        if beat.visual_role == VisualRole.DOCUMENT:
            stage = document_stage or cls._infer_document_stage(beat.narration_text)
            if stage == DocumentProgressStage.SECTION:
                intent, focus, purpose = (
                    CameraIntent.REFRAME,
                    FocusRegion(x=0.1, y=0.12, width=0.8, height=0.7, label="document section"),
                    "Progress within the same document.",
                )
            elif stage == DocumentProgressStage.DETAIL:
                intent, focus, purpose = (
                    CameraIntent.DETAIL_FOCUS,
                    FocusRegion(x=0.18, y=0.2, width=0.64, height=0.45, label="document detail"),
                    "Focus a bounded document detail.",
                )
            elif stage == DocumentProgressStage.RETURN_TO_CONTEXT:
                intent, purpose = (
                    CameraIntent.RETURN_TO_CONTEXT,
                    "Restore the established document context.",
                )
            else:
                purpose = "Establish the document overview before inspection."
        elif beat.visual_role == VisualRole.COMPARE:
            side = comparison_side
            if side == ComparisonSide.LEFT:
                intent, focus = (
                    CameraIntent.REFRAME,
                    FocusRegion(x=0.03, y=0.08, width=0.45, height=0.84, label="comparison LEFT"),
                )
            elif side == ComparisonSide.RIGHT:
                intent, focus = (
                    CameraIntent.REFRAME,
                    FocusRegion(x=0.52, y=0.08, width=0.45, height=0.84, label="comparison RIGHT"),
                )
            purpose = "Preserve fixed LEFT/RIGHT comparison coordinates."
        elif beat.visual_role in (VisualRole.DATA, VisualRole.DIAGRAM):
            purpose = "Keep data and diagram coordinates stable."
        elif beat.visual_role in (VisualRole.REVEAL, VisualRole.EMPHASIZE):
            intent, purpose = (
                CameraIntent.PUSH_IN,
                "Apply bounded emphasis to a reveal or key point.",
            )
        elif beat.visual_role == VisualRole.BROLL and beat.duration_ms >= 2500:
            intent = CameraIntent.PAN_LEFT if beat.beat_index % 2 == 0 else CameraIntent.PAN_RIGHT
            purpose = "Add restrained movement to supporting B-roll."
        elif beat.visual_role == VisualRole.ESTABLISH and beat.duration_ms >= 4500:
            intent, purpose = CameraIntent.PULL_OUT, "Gently establish wider visual context."

        supported_asset_types = {
            "IMAGE",
            "BROLL",
            "VIDEO",
            "SCREENSHOT",
            "DOCUMENT",
            "DATA_CARD",
            "DIAGRAM",
            "LOCAL_TEMPLATE",
        }
        if asset_type not in supported_asset_types:
            intent, focus, purpose = (
                CameraIntent.STATIC,
                None,
                f"Use a safe static frame for unsupported asset type {asset_type or 'UNKNOWN'}.",
            )

        decision = beat.continuity_decision
        if decision == ContinuityDecisionType.REFRAME_LATER:
            intent, purpose = CameraIntent.REFRAME, "Honor the continuity-directed reframe."
            focus = focus or FocusRegion(
                x=0.1, y=0.1, width=0.8, height=0.8, label="continuity reframe"
            )
        elif decision == ContinuityDecisionType.RETURN_TO_MOTIF:
            intent, purpose = (
                CameraIntent.RETURN_TO_CONTEXT,
                "Return to the known motif framing family.",
            )
        elif decision in (ContinuityDecisionType.KEEP, ContinuityDecisionType.PROGRESS_DIAGRAM):
            if beat.visual_role not in (VisualRole.REVEAL, VisualRole.EMPHASIZE):
                intent, focus, purpose = (
                    CameraIntent.STATIC,
                    None,
                    "Preserve the prior visual coordinate system.",
                )

        # Dense or very short material needs reading stability, not camera ornament.
        if beat.duration_ms < 1400 or (
            density == InformationDensity.HIGH
            and beat.visual_role
            not in (VisualRole.REVEAL, VisualRole.EMPHASIZE, VisualRole.DOCUMENT)
        ):
            intent, focus, purpose = (
                CameraIntent.STATIC,
                None,
                "Protect comprehension in a short or information-dense beat.",
            )

        strength = cls._strength(beat, pacing, intent)
        start, end = cls._states(intent, strength, focus)
        return CameraPlan(
            visual_beat_id=beat.id,
            parent_scene_index=beat.parent_scene_index,
            beat_index=beat.beat_index,
            source_editorial_beat_indices=beat.source_editorial_beat_indices,
            duration_ms=beat.duration_ms,
            preferred_asset_type=asset_type,
            intent=intent,
            strength=strength,
            focus_region=focus,
            start_state=start,
            end_state=end,
            editorial_purpose=purpose,
        )

    @staticmethod
    def _infer_document_stage(text: str) -> DocumentProgressStage:
        lower = text.lower()
        if any(cue in lower for cue in ("in context", "overall", "return to")):
            return DocumentProgressStage.RETURN_TO_CONTEXT
        if any(cue in lower for cue in ("quote", "specifically", "clause", "line")):
            return DocumentProgressStage.DETAIL
        if any(cue in lower for cue in ("page", "section", "table")):
            return DocumentProgressStage.SECTION
        return DocumentProgressStage.OVERVIEW

    @staticmethod
    def _strength(beat: VisualBeat, pacing: PacingProfile, intent: CameraIntent) -> MotionStrength:
        if intent == CameraIntent.STATIC:
            return MotionStrength.SUBTLE
        if beat.importance.upper() in ("HIGH", "CRITICAL") and beat.visual_role in (
            VisualRole.REVEAL,
            VisualRole.EMPHASIZE,
        ):
            return MotionStrength.EMPHATIC
        if pacing == PacingProfile.DELIBERATE:
            return MotionStrength.SUBTLE
        return MotionStrength.MODERATE

    @staticmethod
    def _states(
        intent: CameraIntent, strength: MotionStrength, focus: FocusRegion | None
    ) -> tuple[CameraFrameState, CameraFrameState]:
        scale_delta = {
            MotionStrength.SUBTLE: 0.025,
            MotionStrength.MODERATE: 0.04,
            MotionStrength.EMPHATIC: 0.06,
        }[strength]
        center_x = focus.x + focus.width / 2 if focus else 0.5
        center_y = focus.y + focus.height / 2 if focus else 0.5
        center_x = min(0.9, max(0.1, center_x))
        center_y = min(0.9, max(0.1, center_y))
        start = CameraFrameState(scale=1.0, center_x=0.5, center_y=0.5)
        if intent in (CameraIntent.PUSH_IN, CameraIntent.DETAIL_FOCUS, CameraIntent.REFRAME):
            return start, CameraFrameState(
                scale=1.0 + scale_delta, center_x=center_x, center_y=center_y
            )
        if intent in (CameraIntent.PULL_OUT, CameraIntent.RETURN_TO_CONTEXT):
            return CameraFrameState(
                scale=1.0 + scale_delta, center_x=center_x, center_y=center_y
            ), start
        pan = 0.015 if strength == MotionStrength.SUBTLE else 0.025
        if intent == CameraIntent.PAN_LEFT:
            return CameraFrameState(scale=1.04, center_x=0.5 - pan, center_y=0.5), CameraFrameState(
                scale=1.04, center_x=0.5 + pan, center_y=0.5
            )
        if intent == CameraIntent.PAN_RIGHT:
            return CameraFrameState(scale=1.04, center_x=0.5 + pan, center_y=0.5), CameraFrameState(
                scale=1.04, center_x=0.5 - pan, center_y=0.5
            )
        if intent == CameraIntent.PAN_UP:
            return CameraFrameState(scale=1.04, center_x=0.5, center_y=0.5 - pan), CameraFrameState(
                scale=1.04, center_x=0.5, center_y=0.5 + pan
            )
        if intent == CameraIntent.PAN_DOWN:
            return CameraFrameState(scale=1.04, center_x=0.5, center_y=0.5 + pan), CameraFrameState(
                scale=1.04, center_x=0.5, center_y=0.5 - pan
            )
        return start, start

    @classmethod
    def _transition_plan(
        cls, previous: VisualBeat | None, current: VisualBeat, pacing: PacingProfile
    ) -> TransitionPlan:
        requested = TransitionIntent.CUT
        if previous is not None:
            decision = current.continuity_decision
            if decision in (
                ContinuityDecisionType.KEEP,
                ContinuityDecisionType.REUSE,
                ContinuityDecisionType.RETURN_TO_MOTIF,
                ContinuityDecisionType.PROGRESS_DOCUMENT,
                ContinuityDecisionType.PROGRESS_DIAGRAM,
            ):
                requested = TransitionIntent.MATCH_CONTINUITY
            elif decision == ContinuityDecisionType.SWITCH_CONTEXT:
                requested = TransitionIntent.HARD_CONTEXT_SWITCH
            elif current.visual_role == VisualRole.REVEAL:
                requested = TransitionIntent.CROSSFADE

        # The current assembler's only physical transition primitive is a lossless cut.
        # Preserve requested editorial intent while adapting unsupported operations explicitly.
        applied = TransitionIntent.CUT
        fallback = (
            None
            if requested in (TransitionIntent.CUT, TransitionIntent.HARD_CONTEXT_SWITCH)
            else "RENDERER_SUPPORTS_CUT_ONLY"
        )
        if applied == TransitionIntent.CUT:
            duration = 0
        else:
            base = {
                PacingProfile.FAST: 120,
                PacingProfile.BALANCED: 220,
                PacingProfile.DELIBERATE: 360,
            }[pacing]
            duration = min(500, base, max(0, current.duration_ms // 6))
        return TransitionPlan(
            previous_visual_beat_id=previous.id if previous else None,
            visual_beat_id=current.id,
            parent_scene_index=current.parent_scene_index,
            beat_index=current.beat_index,
            source_editorial_beat_indices=current.source_editorial_beat_indices,
            requested_intent=requested,
            applied_intent=applied,
            duration_ms=duration,
            fallback_reason=fallback,
        )

    @classmethod
    def _motion_findings(
        cls, plans: tuple[CameraPlan, ...], pacing: PacingProfile
    ) -> tuple[MotionFinding, ...]:
        findings: list[MotionFinding] = []
        moving = [plan for plan in plans if plan.intent in _MOVING]
        max_ratio = {
            PacingProfile.FAST: 0.75,
            PacingProfile.BALANCED: 0.6,
            PacingProfile.DELIBERATE: 0.45,
        }[pacing]
        if len(plans) >= 3 and len(moving) / len(plans) > max_ratio:
            findings.append(
                MotionFinding(
                    code=MotionFindingCode.EXCESSIVE_CAMERA_MOTION,
                    affected_beat_indices=tuple(plan.beat_index for plan in moving),
                    explanation="Moving-camera ratio exceeds the canonical pacing allowance.",
                )
            )
        for plan in moving:
            if plan.duration_ms < 1400:
                findings.append(
                    MotionFinding(
                        code=MotionFindingCode.MOTION_TOO_FAST,
                        affected_beat_indices=(plan.beat_index,),
                        explanation="The beat is too short for its camera path.",
                    )
                )
            if plan.duration_ms > 12000:
                findings.append(
                    MotionFinding(
                        code=MotionFindingCode.MOTION_TOO_SLOW,
                        affected_beat_indices=(plan.beat_index,),
                        explanation="The camera path would progress too slowly to remain perceptible.",
                    )
                )
        for left, right in zip(plans, plans[1:], strict=False):
            left_direction = _HORIZONTAL_DIRECTIONS.get(left.intent, 0)
            right_direction = _HORIZONTAL_DIRECTIONS.get(right.intent, 0)
            if (
                left_direction != 0
                and left_direction == -right_direction
                and min(left.duration_ms, right.duration_ms) < 3000
            ):
                findings.append(
                    MotionFinding(
                        code=MotionFindingCode.RAPID_DIRECTION_REVERSAL,
                        affected_beat_indices=(left.beat_index, right.beat_index),
                        explanation="Adjacent short beats reverse horizontal direction.",
                    )
                )
        if len(plans) >= 4 and all(plan.intent != CameraIntent.STATIC for plan in plans[-4:]):
            findings.append(
                MotionFinding(
                    code=MotionFindingCode.CAMERA_CHANGE_TOO_FREQUENT,
                    affected_beat_indices=tuple(plan.beat_index for plan in plans[-4:]),
                    explanation="Four consecutive beats change camera state.",
                )
            )
        for plan in moving:
            if not plan.editorial_purpose.strip():
                findings.append(
                    MotionFinding(
                        code=MotionFindingCode.MOTION_WITHOUT_EDITORIAL_PURPOSE,
                        affected_beat_indices=(plan.beat_index,),
                        explanation="Camera movement has no recorded editorial purpose.",
                    )
                )
        return tuple(findings)
