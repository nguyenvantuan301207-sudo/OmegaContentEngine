"""Application-layer Scene Source Statement Resolver for physical multi-beat visual storytelling.

Deterministically resolves exact canonical source statements for a StoryboardScene from script_dict.
Guarantees section-scoped isolation (prevents cross-section statement_order pollution),
strict order preservation, and exact whitespace-normalized narration matching.
Pure logic: zero I/O, zero network, zero external dependencies.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from omega.application.storyboard_engine import StoryboardScene


def _norm_ws(text: str) -> str:
    """Conservatively normalize whitespace only."""
    return " ".join(text.strip().split())


class SceneSourceStatementResolution(BaseModel):
    """Immutable result of resolving source statements for a StoryboardScene."""

    model_config = ConfigDict(frozen=True)

    resolved: bool = Field(description="Whether source statements were resolved unambiguously")
    statements: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple, description="Resolved source statement dicts in authoritative order"
    )
    failure_reason: str | None = Field(
        default=None, description="Deterministic failure reason if not resolved"
    )


def resolve_scene_source_statements(
    *,
    script_dict: dict[str, Any],
    scene: StoryboardScene,
) -> SceneSourceStatementResolution:
    """Deterministically resolve source statements for a scene from script_dict."""
    if not isinstance(script_dict, dict):
        return SceneSourceStatementResolution(
            resolved=False, failure_reason="SOURCE_STATEMENTS_NOT_RESOLVED"
        )

    sections = script_dict.get("sections", [])
    if not isinstance(sections, list) or not sections:
        return SceneSourceStatementResolution(
            resolved=False, failure_reason="SOURCE_STATEMENTS_NOT_RESOLVED"
        )

    norm_scene_section = _norm_ws(scene.section_id)
    norm_scene_narration = _norm_ws(scene.narration_excerpt)
    target_refs = list(scene.source_statement_references)

    if not target_refs:
        return SceneSourceStatementResolution(
            resolved=False, failure_reason="SOURCE_STATEMENTS_NOT_RESOLVED"
        )

    # 1. Filter candidate sections whose heading matches scene.section_id
    matching_sections: list[dict[str, Any]] = []
    for s in sections:
        if not isinstance(s, dict):
            continue
        heading = s.get("heading", "")
        if _norm_ws(str(heading)) == norm_scene_section:
            matching_sections.append(s)

    if not matching_sections:
        return SceneSourceStatementResolution(
            resolved=False, failure_reason="SOURCE_STATEMENTS_NOT_RESOLVED"
        )

    valid_candidates: list[tuple[dict[str, Any], ...]] = []
    has_internal_ambiguity = False

    for section in matching_sections:
        stmts = section.get("statements", [])
        if not isinstance(stmts, list):
            continue

        # 2. Check for duplicate statement_order inside this section
        all_orders = [st.get("statement_order") for st in stmts if isinstance(st, dict)]
        order_counts: dict[int, int] = {}
        for o in all_orders:
            if o is not None:
                order_counts[o] = order_counts.get(o, 0) + 1

        section_has_ambiguous_ref = False
        for ref in target_refs:
            if order_counts.get(ref, 0) > 1:
                section_has_ambiguous_ref = True
                has_internal_ambiguity = True
                break

        if section_has_ambiguous_ref:
            continue

        # Check if all target_refs exist in this section
        if any(order_counts.get(ref, 0) == 0 for ref in target_refs):
            continue

        # Extract matching statements preserving target_refs order
        selected_stmts: list[dict[str, Any]] = []
        stmt_indices: list[int] = []
        possible_match = True

        for ref in target_refs:
            found = False
            for idx, st in enumerate(stmts):
                if isinstance(st, dict) and st.get("statement_order") == ref:
                    selected_stmts.append(st)
                    stmt_indices.append(idx)
                    found = True
                    break
            if not found:
                possible_match = False
                break

        if not possible_match:
            continue

        # Verify statements appear in strictly monotonically increasing order in the section (no reordering)
        if stmt_indices != sorted(stmt_indices) or len(stmt_indices) != len(set(stmt_indices)):
            continue

        # Join statement texts with " "
        joined_text = " ".join(str(st.get("statement_text", "")) for st in selected_stmts)
        if _norm_ws(joined_text) != norm_scene_narration:
            continue

        valid_candidates.append(tuple(selected_stmts))

    if len(valid_candidates) > 1 or (has_internal_ambiguity and not valid_candidates):
        return SceneSourceStatementResolution(
            resolved=False, failure_reason="SOURCE_STATEMENTS_AMBIGUOUS"
        )

    if len(valid_candidates) == 0:
        return SceneSourceStatementResolution(
            resolved=False, failure_reason="SOURCE_STATEMENTS_NOT_RESOLVED"
        )

    return SceneSourceStatementResolution(
        resolved=True,
        statements=valid_candidates[0],
        failure_reason=None,
    )
