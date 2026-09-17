"""Application-layer Beat Asset Policy models and decision engine.

Evaluates BeatVisualDirectionPlan and EditorialBeatPlan to decide whether each
visual beat reuses a compatible prior asset from the parent scene, uses local
template rendering, or permits provider acquisition.
Pure deterministic logic: zero I/O, zero network, zero external provider calls.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from omega.application.beat_visual_direction import BeatVisualDirectionPlan
from omega.application.editorial_beat import (
    AssetReuseIntent,
    EditorialBeatPlan,
)
from omega.application.storyboard_engine import StoryboardScene
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualRenderMode,
    is_meaningful_query,
)


class BeatAssetAction(enum.StrEnum):
    """Decision action for a visual beat's asset lifecycle."""

    NONE = "NONE"
    REUSE_COMPATIBLE = "REUSE_COMPATIBLE"
    LOCAL_TEMPLATE = "LOCAL_TEMPLATE"
    ACQUIRE_IF_NEEDED = "ACQUIRE_IF_NEEDED"


class BeatAssetPolicyError(ValueError):
    """Raised when an asset policy evaluation encounters an invalid or unresolved contract."""


class AvailableBeatAsset(BaseModel):
    """Abstract representation of an asset available from a visual beat."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    beat_index: int = Field(ge=0, description="0-indexed beat position within scene")
    kind: VisualAssetKind = Field(description="Visual asset kind")


class BeatAssetDecision(BaseModel):
    """Immutable policy decision for a visual beat's asset requirement."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    beat_index: int = Field(ge=0, description="0-indexed beat position within scene")
    action: BeatAssetAction = Field(description="Asset action to perform")
    required_kind: VisualAssetKind | None = Field(
        default=None, description="Kind of visual asset required, if any"
    )
    query_hint: str | None = Field(
        default=None, description="Resolved query hint for acquisition, if applicable"
    )
    reuse_from_beat_index: int | None = Field(
        default=None, description="Source beat index to reuse asset from, if applicable"
    )
    provider_acquisition_allowed: bool = Field(
        default=False, description="Whether external provider search/fetch is permitted"
    )
    rationale: str = Field(description="Deterministic policy rationale for this decision")


class BeatAssetPlan(BaseModel):
    """Immutable collection of asset decisions for all beats in a scene."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    decisions: tuple[BeatAssetDecision, ...] = Field(
        default_factory=tuple, description="Ordered asset decisions per beat"
    )


class BeatAssetPolicy:
    """Pure deterministic asset planning policy for multi-beat visual storytelling."""

    @classmethod
    def plan_assets(
        cls,
        *,
        scene: StoryboardScene,
        beat_plan: EditorialBeatPlan,
        direction_plan: BeatVisualDirectionPlan,
        initial_available_assets: Sequence[AvailableBeatAsset] = (),
    ) -> BeatAssetPlan:
        """Evaluate asset actions and reuse links across visual beats in a scene."""
        scene_idx = scene.sequence_index
        if beat_plan.scene_index != scene_idx or direction_plan.parent_scene_index != scene_idx:
            raise ValueError(
                f"Scene index mismatch: scene={scene_idx}, "
                f"beat_plan={beat_plan.scene_index}, "
                f"direction_plan={direction_plan.parent_scene_index}"
            )

        if len(beat_plan.beats) != len(direction_plan.directions):
            raise ValueError(
                f"Beat count mismatch: beat_plan has {len(beat_plan.beats)} beats, "
                f"direction_plan has {len(direction_plan.directions)} directions"
            )

        # Pool of available assets within the parent scene scope
        # Strictly ignore any assets outside this parent scene (no cross-scene reuse)
        available_pool: list[AvailableBeatAsset] = [
            a for a in initial_available_assets if a.parent_scene_index == scene_idx
        ]

        decisions: list[BeatAssetDecision] = []

        for beat, direction in zip(
            beat_plan.beats, direction_plan.directions, strict=True
        ):
            if beat.beat_index != direction.beat_index:
                raise ValueError(
                    f"Beat index ordering mismatch: beat={beat.beat_index}, "
                    f"direction={direction.beat_index}"
                )

            # Case A: Beat requires zero external assets (Template or purely graphic)
            if not direction.asset_requirements:
                if (
                    beat.asset_reuse_intent == AssetReuseIntent.LOCAL_EXPLAINER
                    or direction.render_mode == VisualRenderMode.TEMPLATE
                ):
                    action = BeatAssetAction.LOCAL_TEMPLATE
                    rationale = "Local template requires zero external media."
                else:
                    action = BeatAssetAction.NONE
                    rationale = "Beat does not require external media."

                decisions.append(
                    BeatAssetDecision(
                        parent_scene_index=scene_idx,
                        beat_index=beat.beat_index,
                        action=action,
                        required_kind=None,
                        query_hint=None,
                        reuse_from_beat_index=None,
                        provider_acquisition_allowed=False,
                        rationale=rationale,
                    )
                )
                continue

            # Case B: Beat requires external media (IMAGE, BROLL, SCREENSHOT)
            req = direction.asset_requirements[0]
            req_kind = req.kind
            query = req.query_hint

            # 1. LOCAL_EXPLAINER inconsistency check:
            # Cannot require external media when reuse intent is strictly LOCAL_EXPLAINER
            if beat.asset_reuse_intent == AssetReuseIntent.LOCAL_EXPLAINER:
                raise BeatAssetPolicyError(
                    f"Inconsistent contract: beat {beat.beat_index} specifies LOCAL_EXPLAINER "
                    f"but direction requires external asset kind {req_kind.value}"
                )

            # 2. REUSE_PARENT intent:
            if beat.asset_reuse_intent == AssetReuseIntent.REUSE_PARENT:
                # Find compatible prior assets in the same parent scene (kind must match exactly)
                compatible = [
                    a
                    for a in available_pool
                    if a.parent_scene_index == scene_idx
                    and a.beat_index < beat.beat_index
                    and a.kind == req_kind
                ]

                if compatible:
                    # Nearest preceding selection: max beat_index
                    nearest = max(compatible, key=lambda a: a.beat_index)
                    decisions.append(
                        BeatAssetDecision(
                            parent_scene_index=scene_idx,
                            beat_index=beat.beat_index,
                            action=BeatAssetAction.REUSE_COMPATIBLE,
                            required_kind=req_kind,
                            query_hint=query,
                            reuse_from_beat_index=nearest.beat_index,
                            provider_acquisition_allowed=False,
                            rationale=(
                                f"Planned dependency reuse: reusing compatible {req_kind.value} from "
                                f"planned asset/acquisition at nearest preceding beat {nearest.beat_index}."
                            ),
                        )
                    )
                    continue

                # Fallback when no compatible reusable asset exists in parent scene
                if is_meaningful_query(query):
                    decisions.append(
                        BeatAssetDecision(
                            parent_scene_index=scene_idx,
                            beat_index=beat.beat_index,
                            action=BeatAssetAction.ACQUIRE_IF_NEEDED,
                            required_kind=req_kind,
                            query_hint=query,
                            reuse_from_beat_index=None,
                            provider_acquisition_allowed=True,
                            rationale=(
                                f"No reusable compatible {req_kind.value} found in parent scene; "
                                f"acquiring if needed with query '{query}'."
                            ),
                        )
                    )
                    # This newly acquired asset is now available for subsequent beats
                    available_pool.append(
                        AvailableBeatAsset(
                            parent_scene_index=scene_idx,
                            beat_index=beat.beat_index,
                            kind=req_kind,
                        )
                    )
                    continue

                # If no reusable asset exists and query is not meaningful, fail closed deterministically
                raise BeatAssetPolicyError(
                    f"Unresolved required asset: beat {beat.beat_index} requires {req_kind.value} "
                    f"with REUSE_PARENT but has no compatible prior asset and no meaningful query."
                )

            # 3. ALLOW_SECONDARY_PROVIDER intent:
            if beat.asset_reuse_intent == AssetReuseIntent.ALLOW_SECONDARY_PROVIDER:
                if is_meaningful_query(query):
                    decisions.append(
                        BeatAssetDecision(
                            parent_scene_index=scene_idx,
                            beat_index=beat.beat_index,
                            action=BeatAssetAction.ACQUIRE_IF_NEEDED,
                            required_kind=req_kind,
                            query_hint=query,
                            reuse_from_beat_index=None,
                            provider_acquisition_allowed=True,
                            rationale=(
                                f"Secondary provider acquisition permitted for {req_kind.value} "
                                f"with query '{query}'."
                            ),
                        )
                    )
                    available_pool.append(
                        AvailableBeatAsset(
                            parent_scene_index=scene_idx,
                            beat_index=beat.beat_index,
                            kind=req_kind,
                        )
                    )
                    continue

                raise BeatAssetPolicyError(
                    f"Unresolved required asset: beat {beat.beat_index} permits secondary "
                    f"provider for {req_kind.value} but has no meaningful query."
                )

        return BeatAssetPlan(
            parent_scene_index=scene_idx,
            decisions=tuple(decisions),
        )
