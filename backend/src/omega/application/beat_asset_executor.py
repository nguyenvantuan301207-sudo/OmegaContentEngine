"""Application-layer Beat Asset Executor for multi-beat visual storytelling.

Deterministically executes in-memory asset resolution and materialization dependencies
for an eligible BeatRenderPlan.
Enforces:
- LOCAL_TEMPLATE / NONE => zero external resolver calls;
- ACQUIRE_IF_NEEDED => exactly one resolver call, strict query/kind verification, materialization;
- REUSE_COMPATIBLE => zero provider calls, shared provenance with prior beat;
- Strict preceding-dependency order;
- SCREENSHOT rejection as unsupported external kind.

Does NOT render physical beat clips. Pure asset orchestration and materialization.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from omega.application.beat_asset_policy import BeatAssetAction
from omega.application.beat_render_adapter import BeatRenderPlan
from omega.application.visual_asset_binding import BoundBrollAsset, BoundVisualAsset
from omega.application.visual_asset_engine import (
    ResolvedVisualAsset,
    VisualAssetEngine,
    VisualAssetRequest,
)
from omega.application.semantic_asset_query import SemanticGroundingError
from omega.application.visual_direction import VisualAssetKind
from omega.infrastructure.visual_asset_materializer import VisualAssetMaterializer

ProviderFallbackReason = Literal[
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_RESOLUTION_FAILED",
    "PROVIDER_MATERIALIZATION_FAILED",
    "QUERY_SEMANTIC_REJECTED",
    "PROVIDER_SEMANTIC_MISMATCH",
]


@runtime_checkable
class BeatAssetResolver(Protocol):
    """Protocol for resolving external visual asset requests."""

    async def resolve(
        self,
        request: VisualAssetRequest,
    ) -> ResolvedVisualAsset:
        """Resolve a candidate visual asset request into a fully qualified ResolvedVisualAsset."""
        ...


class BeatAssetExecutionError(ValueError):
    """Raised when beat asset dependency validation or execution fails."""


class ExecutedBeatAsset(BaseModel):
    """Immutable record of asset resolution and materialization binding for a single beat."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    beat_index: int = Field(ge=0, description="0-indexed beat index within scene")
    action: BeatAssetAction = Field(description="Executed asset policy action")
    required_kind: VisualAssetKind | None = Field(
        default=None, description="Visual asset kind required by beat direction"
    )
    reuse_from_beat_index: int | None = Field(
        default=None, description="Preceding beat index reused from, if REUSE_COMPATIBLE"
    )
    resolved_asset: ResolvedVisualAsset | None = Field(
        default=None, description="External resolved asset descriptor carrying full provenance"
    )
    fallback_reason_code: ProviderFallbackReason | None = None
    bound_visual_asset: BoundVisualAsset | None = Field(
        default=None, description="Materialized image binding with data URI"
    )
    bound_broll_asset: BoundBrollAsset | None = Field(
        default=None, description="Materialized broll binding"
    )


class BeatAssetExecutionResult(BaseModel):
    """Immutable result of executing asset dependencies for all beats in a scene."""

    model_config = ConfigDict(frozen=True)

    parent_scene_index: int = Field(ge=1, description="1-indexed parent scene index")
    assets: tuple[ExecutedBeatAsset, ...] = Field(
        default_factory=tuple, description="Executed asset bindings in beat order"
    )


class BeatAssetExecutor:
    """Deterministic executor for multi-beat visual asset dependencies."""

    def __init__(
        self,
        *,
        resolver: BeatAssetResolver | None = None,
        engine: VisualAssetEngine | None = None,
        materializer: type[VisualAssetMaterializer] | None = None,
    ):
        self._resolver = resolver
        self._engine = engine or VisualAssetEngine()
        self._materializer = materializer or VisualAssetMaterializer

    async def execute_plan(
        self,
        *,
        render_plan: BeatRenderPlan,
        provider_acquisition_allowed: bool = True,
    ) -> BeatAssetExecutionResult:
        """Execute asset decisions for each beat in strict ascending beat_index order."""
        scene_idx = render_plan.parent_scene_index
        sorted_units = sorted(render_plan.units, key=lambda u: u.source_beat_index)

        # Validate strictly contiguous ascending beats without duplicates
        seen_indices: set[int] = set()
        for u in sorted_units:
            if u.parent_scene_index != scene_idx:
                raise BeatAssetExecutionError(
                    f"Cross-scene beat detected: unit has scene {u.parent_scene_index}, expected {scene_idx}."
                )
            if u.source_beat_index in seen_indices:
                raise BeatAssetExecutionError(
                    f"Duplicate beat index {u.source_beat_index} detected in render plan."
                )
            seen_indices.add(u.source_beat_index)

        executed_by_beat: dict[int, ExecutedBeatAsset] = {}
        executed_list: list[ExecutedBeatAsset] = []

        for unit in sorted_units:
            decision = unit.asset_decision
            b_idx = unit.source_beat_index
            action = decision.action

            # 1. LOCAL_TEMPLATE / NONE: zero external resolver calls
            if action in (BeatAssetAction.LOCAL_TEMPLATE, BeatAssetAction.NONE):
                executed = ExecutedBeatAsset(
                    parent_scene_index=scene_idx,
                    beat_index=b_idx,
                    action=action,
                    required_kind=decision.required_kind,
                    reuse_from_beat_index=None,
                    resolved_asset=None,
                    bound_visual_asset=None,
                    bound_broll_asset=None,
                )
                executed_by_beat[b_idx] = executed
                executed_list.append(executed)
                continue

            # 2. ACQUIRE_IF_NEEDED: external acquisition
            if action == BeatAssetAction.ACQUIRE_IF_NEEDED:
                if not provider_acquisition_allowed:
                    raise BeatAssetExecutionError(
                        f"Provider acquisition not allowed for beat {b_idx}."
                    )

                if decision.required_kind == VisualAssetKind.SCREENSHOT:
                    raise BeatAssetExecutionError(
                        f"Unsupported beat asset kind: {decision.required_kind}"
                    )

                if decision.required_kind not in (VisualAssetKind.IMAGE, VisualAssetKind.BROLL):
                    raise BeatAssetExecutionError(
                        f"Unsupported beat asset kind: {decision.required_kind}"
                    )

                # Locate matching requirement in adapted direction_view
                reqs = unit.direction_view.asset_requirements
                matching_req = None
                for req in reqs:
                    if req.kind == decision.required_kind:
                        matching_req = req
                        break

                if not matching_req:
                    raise BeatAssetExecutionError(
                        f"No matching requirement in direction for kind {decision.required_kind} on beat {b_idx}."
                    )

                if decision.required_kind != matching_req.kind:
                    raise BeatAssetExecutionError(
                        f"Decision kind {decision.required_kind} != requirement kind {matching_req.kind}."
                    )

                # Build request through existing VisualAssetEngine
                try:
                    req_obj = self._engine.build_request(scene_idx, matching_req)
                except Exception as e:
                    raise BeatAssetExecutionError(
                        f"Failed to build asset request for beat {b_idx}: {e}"
                    ) from e

                if req_obj is None:
                    raise BeatAssetExecutionError(
                        f"Asset request could not be built for beat {b_idx}: meaningless query."
                    )

                if req_obj.kind != decision.required_kind:
                    raise BeatAssetExecutionError(
                        f"Request kind {req_obj.kind} does not match decision kind {decision.required_kind}."
                    )

                if decision.query_hint and req_obj.query != decision.query_hint.strip():
                    raise BeatAssetExecutionError(
                        f"Request query '{req_obj.query}' does not match planned query '{decision.query_hint}'."
                    )

                executed = await resolve_provider_asset(
                    resolver=self._resolver,
                    request=req_obj,
                    beat_index=b_idx,
                    materializer=self._materializer,
                )
                executed_by_beat[b_idx] = executed
                executed_list.append(executed)
                continue

            # 3. REUSE_COMPATIBLE: reuse already-executed asset from preceding beat
            if action == BeatAssetAction.REUSE_COMPATIBLE:
                reuse_from = decision.reuse_from_beat_index
                if reuse_from is None:
                    raise BeatAssetExecutionError(
                        f"reuse_from_beat_index cannot be None for REUSE_COMPATIBLE on beat {b_idx}."
                    )

                if reuse_from >= b_idx:
                    raise BeatAssetExecutionError(
                        f"Cannot reuse from future or current beat {reuse_from} (current is {b_idx})."
                    )

                source_executed = executed_by_beat.get(reuse_from)
                if source_executed is None:
                    raise BeatAssetExecutionError(
                        f"Missing reuse source beat {reuse_from} for beat {b_idx}."
                    )

                if source_executed.parent_scene_index != scene_idx:
                    raise BeatAssetExecutionError("Cross-scene reuse rejected")
                if source_executed.required_kind != decision.required_kind:
                    raise BeatAssetExecutionError("Cross-kind reuse rejected")
                if source_executed.fallback_reason_code:
                    executed = source_executed.model_copy(update={
                        "beat_index": b_idx,
                        "reuse_from_beat_index": reuse_from,
                    })
                    executed_by_beat[b_idx] = executed
                    executed_list.append(executed)
                    continue
                if source_executed.action not in (
                    BeatAssetAction.ACQUIRE_IF_NEEDED,
                    BeatAssetAction.REUSE_COMPATIBLE,
                ):
                    raise BeatAssetExecutionError(
                        f"Cannot reuse from beat {reuse_from} with action {source_executed.action}."
                    )
                if source_executed.resolved_asset is None:
                    raise BeatAssetExecutionError("Reuse source has no resolved external asset")

                if source_executed.resolved_asset.kind != decision.required_kind:
                    raise BeatAssetExecutionError(
                        f"Resolved asset kind {source_executed.resolved_asset.kind} != requested {decision.required_kind}."
                    )

                executed = ExecutedBeatAsset(
                    parent_scene_index=scene_idx,
                    beat_index=b_idx,
                    action=action,
                    required_kind=decision.required_kind,
                    reuse_from_beat_index=reuse_from,
                    resolved_asset=source_executed.resolved_asset,
                    bound_visual_asset=source_executed.bound_visual_asset,
                    bound_broll_asset=source_executed.bound_broll_asset,
                )
                executed_by_beat[b_idx] = executed
                executed_list.append(executed)
                continue

            raise BeatAssetExecutionError(f"Unknown beat asset action: {action}")

        return BeatAssetExecutionResult(
            parent_scene_index=scene_idx,
            assets=tuple(executed_list),
        )


async def resolve_provider_asset(
    *,
    resolver: BeatAssetResolver | None,
    request: VisualAssetRequest,
    beat_index: int = 0,
    materializer: type[VisualAssetMaterializer] = VisualAssetMaterializer,
) -> ExecutedBeatAsset:
    """Degrade only external resolution/materialization; validate kind outside those boundaries."""
    if request.kind not in (VisualAssetKind.IMAGE, VisualAssetKind.BROLL):
        raise BeatAssetExecutionError("Unsupported external asset kind")
    reason: ProviderFallbackReason | None = None
    resolved = None
    bound_vis = None
    bound_br = None
    if resolver is None:
        reason = "PROVIDER_UNAVAILABLE"
    else:
        try:
            if request.source_text is not None:
                from omega.application.semantic_asset_query import validate_semantic_query

                decision = validate_semantic_query(request.query, request.source_text)
                if not decision.valid:
                    raise SemanticGroundingError(decision)
            resolved = await resolver.resolve(request)
        except SemanticGroundingError as exc:
            reason = "PROVIDER_SEMANTIC_MISMATCH" if exc.decision.code == "PROVIDER_ASSET_SEMANTIC_MISMATCH" else "QUERY_SEMANTIC_REJECTED"
        except Exception:
            reason = "PROVIDER_RESOLUTION_FAILED"
        if resolved is None:
            reason = reason or "PROVIDER_RESOLUTION_FAILED"
    if resolved is not None:
        if resolved.kind != request.kind:
            raise BeatAssetExecutionError("Resolved asset kind does not match requested kind")
        try:
            if request.source_text is not None:
                from omega.application.semantic_asset_query import validate_provider_semantics

                decision = validate_provider_semantics(
                    source_text=request.source_text, query=request.query,
                    metadata=resolved.metadata, source_page_url=resolved.source_page_url,
                )
                if not decision.valid:
                    raise SemanticGroundingError(decision)
                resolved = resolved.model_copy(update={"metadata": {
                    **resolved.metadata, "semantic_relevance": decision.as_metadata(),
                }})
            if request.kind == VisualAssetKind.IMAGE:
                bound_vis = materializer.materialize(resolved)
            else:
                bound_br = materializer.materialize_broll(resolved)
        except SemanticGroundingError:
            reason = "PROVIDER_SEMANTIC_MISMATCH"
            resolved = None
        except Exception:
            reason = "PROVIDER_MATERIALIZATION_FAILED"
            resolved = None
    return ExecutedBeatAsset(
        parent_scene_index=request.scene_index,
        beat_index=beat_index,
        action=BeatAssetAction.LOCAL_TEMPLATE if reason else BeatAssetAction.ACQUIRE_IF_NEEDED,
        required_kind=request.kind,
        resolved_asset=resolved,
        bound_visual_asset=bound_vis,
        bound_broll_asset=bound_br,
        fallback_reason_code=reason,
    )
