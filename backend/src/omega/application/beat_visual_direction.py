"""Application-layer Beat Visual Direction models and resolver.

Resolves EditorialBeatPlan into a BeatVisualDirectionPlan by determining
render modes, template IDs, motion profiles, camera motion intents, and
asset requirements per visual beat.
Pure deterministic logic: zero I/O, zero network, zero external providers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from omega.application.editorial_beat import (
    BeatMotionIntent,
    BeatSemanticRole,
    BeatTransitionIntent,
    EditorialBeatPlan,
    EditorialBeatSpec,
)
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualAssetRequirement,
    VisualRenderMode,
    VisualTemplateId,
    is_meaningful_query,
    map_visual_strategy,
)


from omega.application.editorial_layout import EditorialLayout, select_editorial_layout
from omega.application.semantic_asset_query import (
    derive_semantic_asset_query,
    is_unrelated_stock_concept,
)


def resolve_beat_query_hint(
    beat: EditorialBeatSpec, scene: StoryboardScene
) -> str | None:
    """Resolve asset query hint with deterministic priority: beat -> scene -> brief -> derived."""
    candidate = None
    if is_meaningful_query(beat.asset_query_hint) and not is_unrelated_stock_concept(beat.asset_query_hint):
        candidate = beat.asset_query_hint
    elif is_meaningful_query(scene.asset_query_hint) and not is_unrelated_stock_concept(scene.asset_query_hint):
        candidate = scene.asset_query_hint
    elif is_meaningful_query(scene.visual_brief) and not is_unrelated_stock_concept(scene.visual_brief):
        candidate = scene.visual_brief

    if not candidate or is_unrelated_stock_concept(candidate):
        candidate = derive_semantic_asset_query(
            beat.narration_span or scene.narration_excerpt,
            fallback_topic=scene.section_id,
        )

    return candidate



class BeatVisualDirection(BaseModel):
    """Immutable visual direction for an individual editorial beat."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    beat_index: int = Field(ge=0, description="0-indexed position within scene")
    source_statement_references: tuple[int, ...] = Field(
        default_factory=tuple, description="Canonical statement references"
    )
    semantic_role: BeatSemanticRole
    render_mode: VisualRenderMode
    template_id: VisualTemplateId | None
    asset_requirements: tuple[VisualAssetRequirement, ...] = Field(default_factory=tuple)
    camera_motion_intent: BeatMotionIntent = BeatMotionIntent.STATIC
    transition_intent: BeatTransitionIntent = BeatTransitionIntent.HARD_CUT
    template_motion_profile: str | None = None
    rationale: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class BeatVisualDirectionPlan(BaseModel):
    """Immutable collection of visual directions for all beats in a scene."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    directions: tuple[BeatVisualDirection, ...] = Field(default_factory=tuple)


class BeatVisualDirector:
    """Pure deterministic resolver from EditorialBeatPlan to BeatVisualDirectionPlan."""

    @classmethod
    def resolve_beat(
        cls,
        *,
        scene: StoryboardScene,
        beat: EditorialBeatSpec,
        history: Sequence[EditorialLayout] = (),
    ) -> BeatVisualDirection:
        """Resolve visual direction for an individual beat."""
        # 1. Strategy authority: preferred beat strategy wins, parent strategy is fallback
        strategy = beat.preferred_visual_strategy or scene.visual_strategy

        # 2. Canonical mapping to render mode, template ID, template motion, rationale
        render_mode, template_id, template_motion, rationale = map_visual_strategy(
            strategy
        )

        # 3. Resolve asset requirements based on resolved strategy
        assets: list[VisualAssetRequirement] = []
        query_hint = resolve_beat_query_hint(beat, scene)

        if strategy == VisualStrategy.IMAGE:
            assets.append(
                VisualAssetRequirement(
                    kind=VisualAssetKind.IMAGE,
                    query_hint=query_hint,
                    purpose=f"Primary visual for beat {beat.beat_index} image explainer",
                )
            )
        elif strategy == VisualStrategy.BROLL:
            assets.append(
                VisualAssetRequirement(
                    kind=VisualAssetKind.BROLL,
                    query_hint=query_hint,
                    purpose=f"Primary visual for beat {beat.beat_index} broll explainer",
                )
            )
        elif strategy == VisualStrategy.SCREENSHOT:
            assets.append(
                VisualAssetRequirement(
                    kind=VisualAssetKind.SCREENSHOT,
                    query_hint=query_hint,
                    purpose=f"Primary visual for beat {beat.beat_index} screenshot focus",
                )
            )

        # 4. Resolve editorial layout with repetition control
        layout = select_editorial_layout(
            semantic_role=beat.semantic_role,
            visual_strategy=strategy,
            history=history,
        )

        return BeatVisualDirection(
            parent_scene_index=scene.sequence_index,
            beat_index=beat.beat_index,
            source_statement_references=beat.source_statement_references,
            semantic_role=beat.semantic_role,
            render_mode=render_mode,
            template_id=template_id,
            asset_requirements=tuple(assets),
            camera_motion_intent=beat.motion_intent,
            transition_intent=beat.transition_intent,
            template_motion_profile=template_motion,
            rationale=f"Beat {beat.beat_index} ({beat.semantic_role.value}): {rationale}",
            metadata={
                "visual_strategy": strategy.value,
                "asset_reuse_intent": beat.asset_reuse_intent.value,
                "layout_variant": layout.value,
            },
        )

    @classmethod
    def resolve_plan(
        cls,
        *,
        scene: StoryboardScene,
        beat_plan: EditorialBeatPlan,
    ) -> BeatVisualDirectionPlan:
        """Resolve complete visual direction plan for a scene's beat plan."""
        if scene.sequence_index != beat_plan.scene_index:
            raise ValueError(
                f"Scene index mismatch: scene has {scene.sequence_index}, "
                f"beat_plan has {beat_plan.scene_index}"
            )

        history: list[EditorialLayout] = []
        directions = []
        for beat in beat_plan.beats:
            direction = cls.resolve_beat(scene=scene, beat=beat, history=history)
            directions.append(direction)
            layout_val = direction.metadata.get("layout_variant")
            if layout_val:
                try:
                    history.append(EditorialLayout(layout_val))
                except ValueError:
                    pass

        return BeatVisualDirectionPlan(
            parent_scene_index=scene.sequence_index,
            directions=tuple(directions),
        )
