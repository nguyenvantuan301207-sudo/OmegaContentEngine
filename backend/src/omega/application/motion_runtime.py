"""Motion Runtime Engine."""

from pydantic import BaseModel

from omega.application.motion_spec import Easing, MotionType, SceneMotionPlan
from omega.application.scene_composition import SceneComposition


class LayerFrameState(BaseModel):
    layer_id: str
    visible: bool = True
    opacity: float = 1.0
    translate_x: float = 0.0
    translate_y: float = 0.0
    scale: float = 1.0
    reveal_progress: float = 1.0
    highlight_progress: float = 1.0
    counter_progress: float = 1.0
    type_on_progress: float = 1.0

class MotionRuntime:
    def _ease(self, progress: float, easing: Easing) -> float:
        p = max(0.0, min(1.0, progress))
        if easing == Easing.LINEAR:
            return p
        elif easing == Easing.EASE_IN:
            return p * p
        elif easing == Easing.EASE_OUT:
            return p * (2.0 - p)
        elif easing == Easing.EASE_IN_OUT:
            if p < 0.5:
                return 2.0 * p * p
            else:
                return -1.0 + (4.0 - 2.0 * p) * p
        return p

    def evaluate(self, comp: SceneComposition, plan: SceneMotionPlan, time_seconds: float) -> list[LayerFrameState]:
        time_seconds = max(0.0, min(time_seconds, comp.duration_seconds))

        states = {layer.id: LayerFrameState(layer_id=layer.id) for layer in comp.layers}

        layer_motions = {}
        for m in plan.motions:
            if m.layer_id not in layer_motions:
                layer_motions[m.layer_id] = []
            layer_motions[m.layer_id].append(m)

        for layer_id, motions in layer_motions.items():
            if layer_id not in states:
                continue

            state = states[layer_id]
            motions = sorted(motions, key=lambda x: x.start_seconds)

            for m in motions:
                end_time = m.start_seconds + m.duration_seconds

                if time_seconds <= m.start_seconds:
                    raw_p = 0.0
                elif time_seconds >= end_time:
                    raw_p = 1.0
                else:
                    raw_p = (time_seconds - m.start_seconds) / m.duration_seconds

                eased_p = self._ease(raw_p, m.easing)

                if m.motion_type == MotionType.FADE_IN:
                    state.opacity = eased_p
                elif m.motion_type == MotionType.FADE_OUT:
                    state.opacity = 1.0 - eased_p
                elif m.motion_type == MotionType.SLIDE_IN:
                    dist = 50.0
                    if m.direction == "left":
                        state.translate_x = dist * (1.0 - eased_p)
                    elif m.direction == "right":
                        state.translate_x = -dist * (1.0 - eased_p)
                    elif m.direction == "down":
                        state.translate_y = -dist * (1.0 - eased_p)
                    else:
                        state.translate_y = dist * (1.0 - eased_p)
                elif m.motion_type == MotionType.SLIDE_OUT:
                    dist = 50.0
                    state.translate_y = dist * eased_p
                elif m.motion_type == MotionType.ZOOM_IN:
                    state.scale = 1.0 + (0.08 * eased_p)
                elif m.motion_type == MotionType.ZOOM_OUT:
                    state.scale = 1.08 - (0.08 * eased_p)
                elif m.motion_type == MotionType.REVEAL:
                    state.reveal_progress = eased_p
                elif m.motion_type == MotionType.HIGHLIGHT:
                    state.highlight_progress = eased_p
                elif m.motion_type == MotionType.COUNTER:
                    state.counter_progress = eased_p
                elif m.motion_type == MotionType.TYPE_ON:
                    state.type_on_progress = eased_p
                elif m.motion_type == MotionType.PAN:
                    state.translate_x = -20.0 + (40.0 * eased_p)

        return list(states.values())
