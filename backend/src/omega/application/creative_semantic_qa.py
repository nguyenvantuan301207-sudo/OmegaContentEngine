"""Blocking semantic gates over canonical script and artifact-scoped visual truth."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from omega.application.mechanism_diagram import resolve_mechanism_diagram_spec
from omega.application.script_meta_guard import script_meta_evidence
from omega.application.semantic_asset_query import (
    contains_phrase,
    subject_tokens,
    validate_provider_semantics,
    validate_semantic_query,
)
from omega.domain.production import ProductionQAFinding, ProductionQARuleCode, ProductionQASeverity


def semantic_production_findings(
    script: dict[str, Any],
    *,
    snapshot: Any = None,
    scenes: list[dict[str, Any]] | None = None,
) -> list[ProductionQAFinding]:
    findings = []

    def block(code: str, message: str) -> None:
        findings.append(
            ProductionQAFinding(
                rule_code=ProductionQARuleCode(code),
                severity=ProductionQASeverity.BLOCKING,
                message=message,
            )
        )

    meta = script_meta_evidence(script)
    if meta:
        block(
            "SCRIPT_META_CONTENT",
            f"Viewer copy contains planning/padding patterns ({len(meta)} records).",
        )
    if snapshot is not None:
        data = snapshot.model_dump(mode="json") if hasattr(snapshot, "model_dump") else snapshot
        scene_rows = data.get("scenes") or []
        visual_rows = data.get("visual_beats") or []
    else:
        scene_rows = scenes or []
        visual_rows = scene_rows
    by_scene = {s.get("sequence_index", s.get("scene_index")): s for s in scene_rows}
    title = str(script.get("title") or "")
    providers = []
    for visual in visual_rows:
        idx = visual.get(
            "parent_scene_index", visual.get("scene_index", visual.get("sequence_index"))
        )
        scene = by_scene.get(idx, {})
        source = f"{title} {scene.get('narration_text') or ''}"
        if visual.get("template_id") == "FLOW_DIAGRAM":
            # Existing snapshots do not bind payload nodes; unsupported source must
            # still block, rather than accepting capitalization as a relationship.
            evidence = (visual.get("provider_metadata") or {}).get("diagram_semantics") or {}
            source_text = evidence.get("source_text") or scene.get("narration_text") or ""
            spec = resolve_mechanism_diagram_spec(str(source_text))
            inputs = evidence.get("inputs") or {}
            nodes_match = not inputs or (
                spec is not None and list(spec.nodes) == inputs.get("NODES")
            )
            source_matches = not evidence.get("source_text") or contains_phrase(
                scene.get("narration_text"), str(source_text)
            )
            if spec is None or not nodes_match or not source_matches:
                block(
                    "DIAGRAM_SEMANTIC_INVALID",
                    f"Scene {idx}: no supported source relationship for FLOW_DIAGRAM.",
                )
        if visual.get("visual_origin") != "PROVIDER":
            continue
        providers.append(visual)
        query = str(visual.get("query") or "")
        decision = validate_semantic_query(query, source)
        if not decision.valid:
            block(
                "PROVIDER_QUERY_SEMANTIC_DRIFT",
                f"Scene {idx}: {decision.code}; unsupported query terms {decision.evidence}.",
            )
        metadata = visual.get("provider_metadata") or {}
        relevance = validate_provider_semantics(
            source_text=source,
            query=query,
            metadata=metadata,
            source_page_url=visual.get("source_page_url"),
        )
        if relevance.code == "PROVIDER_ASSET_SEMANTIC_MISMATCH":
            block(
                "PROVIDER_ASSET_SEMANTIC_MISMATCH",
                f"Scene {idx}: descriptive provider evidence conflicts with source: {relevance.evidence}.",
            )

    # Detection only, not P1 selection scheduling. Evaluate global physical beat
    # use even when parent strategies alternate or content hashes recur later.
    groups = defaultdict(list)
    for visual in providers:
        query = str(visual.get("query") or "")
        family = tuple(sorted(subject_tokens(query)))
        groups[("query", family)].append(visual)
        asset = visual.get("provider_asset_content_sha256") or visual.get("provider_asset_id")
        if asset:
            groups[("asset", asset)].append(visual)
        if validate_semantic_query(query, title).code == "INVALID_DOMAIN_DRIFT":
            groups[("query_family", "unsupported_domain")].append(visual)
    for (kind, _key), uses in groups.items():
        indexes = {
            v.get("parent_scene_index", v.get("scene_index", v.get("sequence_index"))) for v in uses
        }
        if len(uses) >= 8 and len(indexes) >= 4 and len(uses) / len(providers) >= 0.6:
            block(
                "PROVIDER_QUERY_EXCESSIVE_REPETITION",
                f"Dominant {kind}: {len(uses)}/{len(providers)} provider beats across {len(indexes)} scenes (threshold >=8 beats, >=4 scenes, >=60%).",
            )
            break
    return findings
