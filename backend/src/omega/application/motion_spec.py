"""Motion Specification Engine."""

import enum
from typing import Any

from pydantic import BaseModel, Field

from omega.application.scene_composition import LayerType, SceneComposition
from omega.application.storyboard_engine import VisualStrategy


class MotionType(enum.StrEnum):
    NONE = "NONE"
    FADE_IN = "FADE_IN"
    FADE_OUT = "FADE_OUT"
    SLIDE_IN = "SLIDE_IN"
    SLIDE_OUT = "SLIDE_OUT"
    PAN = "PAN"
    ZOOM_IN = "ZOOM_IN"
    ZOOM_OUT = "ZOOM_OUT"
    REVEAL = "REVEAL"
    HIGHLIGHT = "HIGHLIGHT"
    COUNTER = "COUNTER"
    TYPE_ON = "TYPE_ON"

class Easing(enum.StrEnum):
    LINEAR = "LINEAR"
    EASE_IN = "EASE_IN"
    EASE_OUT = "EASE_OUT"
    EASE_IN_OUT = "EASE_IN_OUT"

class LayerMotion(BaseModel):
    layer_id: str
    motion_type: MotionType
    start_seconds: float
    duration_seconds: float
    easing: Easing = Easing.EASE_IN_OUT
    direction: str | None = None
    intensity: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

class SceneMotionPlan(BaseModel):
    scene_index: int
    scene_duration_seconds: float
    motions: list[LayerMotion] = Field(default_factory=list)
    transition_in: str | None = None
    transition_out: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class MotionSpecEngine:
    def plan_motion(self, comp: SceneComposition) -> SceneMotionPlan:
        motions: list[LayerMotion] = []
        strategy = comp.visual_strategy
        duration = comp.duration_seconds

        def add_motion(layer_id: str, mtype: MotionType, start: float, dur: float, easing: Easing = Easing.EASE_IN_OUT):
            if not any(layer.id == layer_id for layer in comp.layers):
                return
            dur = max(0.1, min(dur, duration - start))
            if start + dur > duration:
                dur = duration - start
            if dur > 0 and start < duration:
                motions.append(LayerMotion(
                    layer_id=layer_id,
                    motion_type=mtype,
                    start_seconds=round(start, 2),
                    duration_seconds=round(dur, 2),
                    easing=easing
                ))

        if strategy == VisualStrategy.TITLE_MOTION:
            add_motion("title", MotionType.SLIDE_IN, 0.0, 1.0)
            add_motion("subtitle", MotionType.FADE_IN, 0.5, 1.0)
            add_motion("accent", MotionType.REVEAL, 0.8, 0.8)

        elif strategy == VisualStrategy.DIAGRAM:
            add_motion("title", MotionType.FADE_IN, 0.0, 0.8)
            nodes = [layer for layer in comp.layers if layer.type == LayerType.DIAGRAM_NODE]
            edges = [layer for layer in comp.layers if layer.type == LayerType.DIAGRAM_EDGE]

            sequence = []
            for node in nodes:
                sequence.append(node.id)
                out_edges = [e for e in edges if e.content.get("from") == node.id]
                for edge in out_edges:
                    sequence.append(edge.id)

            if sequence:
                start_t = 0.5
                available_time = max(0.1, duration - start_t)
                step_interval = min(0.5, available_time / len(sequence))
                reveal_dur = min(0.5, step_interval * 1.5)

                t = start_t
                for layer_id in sequence:
                    add_motion(layer_id, MotionType.REVEAL, t, reveal_dur)
                    t += step_interval

        elif strategy == VisualStrategy.CODE_DEMO:
            add_motion("header", MotionType.FADE_IN, 0.0, 0.8)
            add_motion("code", MotionType.TYPE_ON, 0.5, min(2.0, duration - 1.0))
            add_motion("callout", MotionType.HIGHLIGHT, 2.5, 1.0)

        elif strategy == VisualStrategy.STATISTIC:
            metric_layer = next((layer for layer in comp.layers if layer.type == LayerType.METRIC), None)
            if metric_layer and metric_layer.content.get("value"):
                add_motion(metric_layer.id, MotionType.COUNTER, 0.0, 1.5)
            elif metric_layer:
                add_motion(metric_layer.id, MotionType.FADE_IN, 0.0, 1.5)

            add_motion("label", MotionType.FADE_IN, 1.0, 1.0)
            add_motion("icon", MotionType.REVEAL, 0.5, 0.8)

        elif strategy == VisualStrategy.INFOGRAPHIC:
            add_motion("title", MotionType.FADE_IN, 0.0, 0.8)
            blocks = [layer for layer in comp.layers if layer.id.startswith("block_") and layer.type == LayerType.SHAPE]
            t = 0.5
            for block in blocks:
                add_motion(block.id, MotionType.SLIDE_IN, t, 0.8)
                text_id = block.id.replace("_shape", "_text")
                add_motion(text_id, MotionType.FADE_IN, t + 0.3, 0.8)
                t += 0.8

        elif strategy == VisualStrategy.KINETIC_TEXT:
            add_motion("kinetic_text", MotionType.REVEAL, 0.0, 1.0)

        elif strategy == VisualStrategy.IMAGE:
            add_motion("image", MotionType.ZOOM_IN, 0.0, duration, Easing.LINEAR)
            add_motion("caption", MotionType.FADE_IN, 0.5, 1.0)

        elif strategy == VisualStrategy.BROLL:
            add_motion("video", MotionType.NONE, 0.0, duration)
            add_motion("lower_third_bg", MotionType.SLIDE_IN, 1.0, 0.8)
            add_motion("lower_third_text", MotionType.FADE_IN, 1.5, 0.8)

        elif strategy == VisualStrategy.SCREENSHOT:
            add_motion("screenshot", MotionType.ZOOM_IN, 0.0, min(3.0, duration))
            add_motion("highlight", MotionType.HIGHLIGHT, 1.0, 0.5)
            add_motion("explanation", MotionType.FADE_IN, 0.5, 1.0)

        elif strategy == VisualStrategy.CTA:
            add_motion("recap", MotionType.FADE_IN, 0.0, 1.0)
            add_motion("cta_text", MotionType.SLIDE_IN, 1.0, 1.0)
            add_motion("cta_icon", MotionType.REVEAL, 1.5, 0.8)

        return SceneMotionPlan(
            scene_index=comp.scene_index,
            scene_duration_seconds=comp.duration_seconds,
            motions=motions
        )
