"""Scene Composition Engine."""

import enum
import re
from typing import Any

from pydantic import BaseModel, Field

from omega.application.storyboard_engine import StoryboardScene, VisualStrategy


class LayerType(enum.StrEnum):
    TEXT = "TEXT"
    SHAPE = "SHAPE"
    ICON = "ICON"
    IMAGE_SLOT = "IMAGE_SLOT"
    VIDEO_SLOT = "VIDEO_SLOT"
    CODE_BLOCK = "CODE_BLOCK"
    DIAGRAM_NODE = "DIAGRAM_NODE"
    DIAGRAM_EDGE = "DIAGRAM_EDGE"
    METRIC = "METRIC"
    CHART = "CHART"
    DIVIDER = "DIVIDER"

class Layer(BaseModel):
    id: str
    type: LayerType
    x: int
    y: int
    width: int
    height: int
    z_index: int
    opacity: float = 1.0
    content: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)

class AssetRequirement(BaseModel):
    kind: str
    query: str
    purpose: str
    preferred_aspect_ratio: str = "16:9"

class SceneComposition(BaseModel):
    scene_index: int
    source_storyboard_scene_index: int
    width: int = 1920
    height: int = 1080
    background: str = "#ffffff"
    layout_type: str
    visual_strategy: VisualStrategy
    duration_seconds: float
    motion_hint: str | None = None
    layers: list[Layer] = Field(default_factory=list)
    asset_requirements: list[AssetRequirement] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class SceneCompositionEngine:
    def __init__(self):
        self.width = 1920
        self.height = 1080
        self.margin_x = 100
        self.margin_top = 80
        self.margin_bottom = 100
        self.safe_width = self.width - (2 * self.margin_x)
        self.safe_height = self.height - self.margin_top - self.margin_bottom

    def compose(self, scene: StoryboardScene) -> SceneComposition:
        layers: list[Layer] = []
        reqs: list[AssetRequirement] = []
        layout = "DEFAULT"

        strategy = scene.visual_strategy

        # Handle STATISTIC fallback
        metric = None
        if strategy == VisualStrategy.STATISTIC:
            metric = self._extract_metric(scene.narration_excerpt)
            if not metric:
                strategy = VisualStrategy.KINETIC_TEXT

        if strategy == VisualStrategy.TITLE_MOTION:
            layout = "CENTERED_TITLE"
            layers.append(self._create_layer("title", LayerType.TEXT, self.margin_x, 400, self.safe_width, 200, 10, content={"text": scene.section_id, "font_size": 96, "align": "center"}))
            layers.append(self._create_layer("subtitle", LayerType.TEXT, self.margin_x, 650, self.safe_width, 100, 10, content={"text": scene.on_screen_text or "", "font_size": 48, "align": "center"}))
            layers.append(self._create_layer("accent", LayerType.SHAPE, self.width // 2 - 100, 620, 200, 8, 5, style={"color": "#ff0000"}))

        elif strategy == VisualStrategy.DIAGRAM:
            layout = "FLOW_DIAGRAM"
            layers.append(self._create_layer("title", LayerType.TEXT, self.margin_x, self.margin_top, self.safe_width, 100, 10, content={"text": scene.section_id, "font_size": 64}))

            # 3 Nodes horizontally
            node_w = 300
            node_h = 150
            y_center = self.height // 2 - node_h // 2
            x1 = self.margin_x + 100
            x2 = self.width // 2 - node_w // 2
            x3 = self.width - self.margin_x - 100 - node_w

            layers.append(self._create_layer("node_1", LayerType.DIAGRAM_NODE, x1, y_center, node_w, node_h, 10, content={"label": "Client"}))
            layers.append(self._create_layer("node_2", LayerType.DIAGRAM_NODE, x2, y_center, node_w, node_h, 10, content={"label": "API"}))
            layers.append(self._create_layer("node_3", LayerType.DIAGRAM_NODE, x3, y_center, node_w, node_h, 10, content={"label": "Database"}))

            layers.append(self._create_layer("edge_1", LayerType.DIAGRAM_EDGE, x1 + node_w, y_center + node_h // 2, x2 - (x1 + node_w), 4, 5, content={"from": "node_1", "to": "node_2"}))
            layers.append(self._create_layer("edge_2", LayerType.DIAGRAM_EDGE, x2 + node_w, y_center + node_h // 2, x3 - (x2 + node_w), 4, 5, content={"from": "node_2", "to": "node_3"}))

        elif strategy == VisualStrategy.CODE_DEMO:
            layout = "EDITOR_PANEL"
            layers.append(self._create_layer("header", LayerType.TEXT, self.margin_x, self.margin_top, self.safe_width, 80, 10, content={"text": scene.section_id, "font_size": 48}))
            layers.append(self._create_layer("code", LayerType.CODE_BLOCK, self.margin_x, 200, self.safe_width, 600, 10, content={"code": scene.narration_excerpt[:100] + "...", "language": "python"}))
            layers.append(self._create_layer("callout", LayerType.SHAPE, self.margin_x - 20, 180, self.safe_width + 40, 640, 5, style={"bg": "#1e1e1e", "radius": 16}))

        elif strategy == VisualStrategy.INFOGRAPHIC:
            layout = "GRID_2_COL"
            layers.append(self._create_layer("title", LayerType.TEXT, self.margin_x, self.margin_top, self.safe_width, 100, 10, content={"text": scene.section_id, "font_size": 64}))
            layers.append(self._create_layer("block_1_shape", LayerType.SHAPE, self.margin_x, 300, 800, 400, 5, style={"bg": "#f0f0f0"}))
            layers.append(self._create_layer("block_1_text", LayerType.TEXT, self.margin_x + 50, 350, 700, 300, 10, content={"text": "Factor A", "font_size": 48}))
            layers.append(self._create_layer("block_2_shape", LayerType.SHAPE, self.width - self.margin_x - 800, 300, 800, 400, 5, style={"bg": "#f0f0f0"}))
            layers.append(self._create_layer("block_2_text", LayerType.TEXT, self.width - self.margin_x - 750, 350, 700, 300, 10, content={"text": "Factor B", "font_size": 48}))

        elif strategy == VisualStrategy.STATISTIC:
            layout = "HERO_METRIC"
            layers.append(self._create_layer("metric", LayerType.METRIC, self.margin_x, 300, self.safe_width, 300, 10, content={"value": metric, "font_size": 200, "align": "center"}))
            layers.append(self._create_layer("label", LayerType.TEXT, self.margin_x, 650, self.safe_width, 100, 10, content={"text": scene.on_screen_text or "", "font_size": 48, "align": "center"}))
            layers.append(self._create_layer("icon", LayerType.ICON, self.width // 2 - 50, 150, 100, 100, 10, content={"icon_name": "chart"}))

        elif strategy == VisualStrategy.KINETIC_TEXT:
            layout = "FULL_TYPOGRAPHY"
            layers.append(self._create_layer("kinetic_text", LayerType.TEXT, self.margin_x, self.height // 2 - 200, self.safe_width, 400, 10, content={"text": scene.on_screen_text or "", "font_size": 120, "align": "center", "weight": "bold"}))

        elif strategy == VisualStrategy.IMAGE:
            layout = "IMAGE_WITH_CAPTION"
            layers.append(self._create_layer("image", LayerType.IMAGE_SLOT, self.margin_x, self.margin_top, self.safe_width, self.safe_height - 150, 5))
            layers.append(self._create_layer("caption", LayerType.TEXT, self.margin_x, self.height - self.margin_bottom - 100, self.safe_width, 100, 10, content={"text": scene.on_screen_text or "", "font_size": 40}))
            reqs.append(AssetRequirement(kind="IMAGE", query=scene.asset_query_hint or scene.visual_brief, purpose="Visual support"))

        elif strategy == VisualStrategy.BROLL:
            layout = "FULL_VIDEO_LOWER_THIRD"
            layers.append(self._create_layer("video", LayerType.VIDEO_SLOT, 0, 0, self.width, self.height, 1))
            layers.append(self._create_layer("lower_third_bg", LayerType.SHAPE, self.margin_x, self.height - 250, 1200, 150, 5, style={"bg": "#000000", "opacity": 0.8}))
            layers.append(self._create_layer("lower_third_text", LayerType.TEXT, self.margin_x + 50, self.height - 200, 1100, 100, 10, content={"text": scene.on_screen_text or "", "font_size": 48, "color": "#ffffff"}))
            reqs.append(AssetRequirement(kind="BROLL", query=scene.asset_query_hint or scene.visual_brief, purpose="B-Roll context"))

        elif strategy == VisualStrategy.SCREENSHOT:
            layout = "SCREENSHOT_HIGHLIGHT"
            layers.append(self._create_layer("screenshot", LayerType.IMAGE_SLOT, self.margin_x, self.margin_top, self.safe_width, self.safe_height - 200, 5))
            layers.append(self._create_layer("highlight", LayerType.SHAPE, self.width // 2 - 200, self.height // 2 - 100, 400, 200, 10, style={"border": "4px solid red", "fill": "transparent"}))
            layers.append(self._create_layer("explanation", LayerType.TEXT, self.margin_x, self.height - self.margin_bottom - 150, self.safe_width, 150, 15, content={"text": scene.on_screen_text or "", "font_size": 48, "bg": "#ffffff"}))
            reqs.append(AssetRequirement(kind="SCREENSHOT", query=scene.asset_query_hint or scene.visual_brief, purpose="UI Example"))

        elif strategy == VisualStrategy.CTA:
            layout = "OUTRO_CTA"
            layers.append(self._create_layer("recap", LayerType.TEXT, self.margin_x, 300, self.safe_width, 200, 10, content={"text": scene.section_id, "font_size": 80, "align": "center"}))
            layers.append(self._create_layer("cta_text", LayerType.TEXT, self.margin_x, 600, self.safe_width, 100, 10, content={"text": scene.on_screen_text or "", "font_size": 60, "align": "center"}))
            layers.append(self._create_layer("cta_icon", LayerType.ICON, self.width // 2 - 60, 750, 120, 120, 10, content={"icon_name": "subscribe"}))

        return SceneComposition(
            scene_index=scene.sequence_index,
            source_storyboard_scene_index=scene.sequence_index,
            width=self.width,
            height=self.height,
            layout_type=layout,
            visual_strategy=strategy,
            duration_seconds=scene.estimated_duration_seconds,
            motion_hint=scene.motion_hint,
            layers=layers,
            asset_requirements=reqs,
            metadata={"citations": scene.citations}
        )

    def _create_layer(self, layer_id: str, ltype: LayerType, x: int, y: int, w: int, h: int, z: int, content: dict | None = None, style: dict | None = None) -> Layer:
        return Layer(
            id=layer_id,
            type=ltype,
            x=x,
            y=y,
            width=w,
            height=h,
            z_index=z,
            content=content or {},
            style=style or {}
        )

    def _extract_metric(self, text: str) -> str | None:
        match = re.search(r'(\d+(?:\.\d+)?(?:%|x|ms|s|k|m|b)?\b|\b\d+%)', text)
        if match:
            return match.group(1)
        return None
