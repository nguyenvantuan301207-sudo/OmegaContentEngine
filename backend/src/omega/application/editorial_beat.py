"""Application-layer Editorial Beat semantic models and timing contracts.

Defines the immutable semantic specifications for visual beats within scenes,
independent of physical rendering, media assets, or absolute timing.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field

from omega.application.storyboard_engine import VisualStrategy


class BeatSemanticRole(enum.StrEnum):
    """Semantic role of a visual beat within the narrative flow."""

    HOOK_TITLE = "HOOK_TITLE"
    CONTEXT = "CONTEXT"
    EXPLANATION = "EXPLANATION"
    MECHANISM = "MECHANISM"
    EVIDENCE = "EVIDENCE"
    PAYOFF = "PAYOFF"
    CLOSING = "CLOSING"


class BeatMotionIntent(enum.StrEnum):
    """Restrained motion language intent for visual presentation."""

    STATIC = "STATIC"
    SLOW_PUSH_IN = "SLOW_PUSH_IN"
    SLOW_PULL_OUT = "SLOW_PULL_OUT"
    PAN_LEFT = "PAN_LEFT"
    PAN_RIGHT = "PAN_RIGHT"
    DRIFT = "DRIFT"
    FOCAL_ZOOM = "FOCAL_ZOOM"


class BeatTransitionIntent(enum.StrEnum):
    """Editorial transition intent between consecutive beats."""

    HARD_CUT = "HARD_CUT"
    CROSSFADE = "CROSSFADE"


class AssetReuseIntent(enum.StrEnum):
    """Policy guiding visual asset binding across beats."""

    REUSE_PARENT = "REUSE_PARENT"
    LOCAL_EXPLAINER = "LOCAL_EXPLAINER"
    ALLOW_SECONDARY_PROVIDER = "ALLOW_SECONDARY_PROVIDER"


class EditorialBeatSpec(BaseModel):
    """Immutable semantic specification for an editorial visual beat.

    Contains only planning and semantic authority. Excludes physical
    render artifacts, asset IDs, storage paths, hashes, or absolute millisecond
    timings.
    """

    model_config = ConfigDict(frozen=True)

    beat_index: int = Field(ge=0, description="0-indexed position within the parent scene")
    parent_scene_index: int = Field(ge=1, description="1-indexed sequence index of parent scene")
    source_statement_references: tuple[int, ...] = Field(
        default_factory=tuple, description="Canonical statement references driving this beat"
    )
    narration_span: str = Field(description="Exact verbatim narration text span for this beat")
    semantic_role: BeatSemanticRole = Field(description="Editorial narrative role")
    word_weight: int = Field(ge=1, description="Deterministic weight based on narration word count")
    preferred_visual_strategy: VisualStrategy | None = Field(
        default=None, description="Preferred visual strategy prior to template resolution"
    )
    asset_reuse_intent: AssetReuseIntent = Field(
        default=AssetReuseIntent.REUSE_PARENT,
        description="Policy intent for visual asset reuse or acquisition",
    )
    motion_intent: BeatMotionIntent = Field(
        default=BeatMotionIntent.STATIC, description="Planned camera motion intent"
    )
    transition_intent: BeatTransitionIntent = Field(
        default=BeatTransitionIntent.HARD_CUT, description="Transition into this beat"
    )
    asset_query_hint: str | None = Field(
        default=None, description="Semantic hint for asset querying if justified"
    )


class EditorialBeatPlan(BaseModel):
    """Immutable collection of planned editorial beats for a scene."""

    model_config = ConfigDict(frozen=True)

    scene_index: int = Field(ge=1, description="Parent scene sequence index (1-indexed)")
    beats: tuple[EditorialBeatSpec, ...] = Field(
        default_factory=tuple, description="Ordered visual beats"
    )
    total_word_weight: int = Field(ge=0, description="Sum of word weights across all beats")


class EditorialBeatTiming(BaseModel):
    """Materialized physical timing for an individual visual beat interval."""

    model_config = ConfigDict(frozen=True)

    materialized_index: int = Field(
        ge=0, description="0-indexed position within the materialized timing plan"
    )
    source_beat_indices: tuple[int, ...] = Field(
        min_length=1,
        description="Canonically ordered source beat indices represented by this interval",
    )
    start_ms: int = Field(ge=0, description="Start offset in milliseconds from scene start")
    end_ms: int = Field(gt=0, description="End offset in milliseconds from scene start")
    duration_ms: int = Field(gt=0, description="Beat duration in milliseconds")

    @property
    def beat_index(self) -> int:
        """Convenience alias for materialized_index."""
        return self.materialized_index


class MaterializedBeatTimingPlan(BaseModel):
    """Materialized physical timing allocation for all beats in a scene."""

    model_config = ConfigDict(frozen=True)

    scene_index: int = Field(ge=1, description="Parent scene sequence index (1-indexed)")
    scene_duration_ms: int = Field(gt=0, description="Total authoritative scene duration in ms")
    timings: tuple[EditorialBeatTiming, ...] = Field(
        default_factory=tuple, description="Monotonic non-overlapping beat timings"
    )
