"""Deterministic Scene Planner decomposing ScriptVersion into ordered production scenes."""

from __future__ import annotations

import uuid
from typing import Any

from omega.domain.production import (
    AssetRequirementStatus,
    AssetType,
    LicenseRequirement,
    SceneType,
)

# Speaking rate baseline: 140 WPM => 2.33 words per second => ~430 ms per word
MS_PER_WORD = 430
MIN_SCENE_DURATION_MS = 2500
MAX_SCENE_DURATION_MS = 12000


def estimate_narration_ms(text: str) -> int:
    """Calculate deterministic speaking duration in integer milliseconds."""
    words = [w for w in text.split() if w.strip()]
    count = len(words)
    if count == 0:
        return 0
    raw_ms = count * MS_PER_WORD
    return max(MIN_SCENE_DURATION_MS, min(raw_ms, MAX_SCENE_DURATION_MS))


def plan_scenes_from_script(
    production_request_id: uuid.UUID,
    script_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Deterministically generate production scenes and asset requirements from script data.

    Returns a list of scene dictionaries with nested 'asset_requirements'.
    """
    scenes: list[dict[str, Any]] = []
    scene_order = 1

    # 1. Opening Hook Scene (SceneType.TITLE or NARRATION)
    hook_text = str(script_data.get("hook_text", "")).strip()
    if hook_text:
        hook_duration_ms = estimate_narration_ms(hook_text)
        hook_scene_id = uuid.uuid4()
        scenes.append(
            {
                "id": hook_scene_id,
                "production_request_id": production_request_id,
                "scene_order": scene_order,
                "script_section_id": None,
                "start_statement_id": None,
                "end_statement_id": None,
                "scene_type": SceneType.TITLE.value,
                "narration_text": hook_text,
                "estimated_duration_ms": hook_duration_ms,
                "visual_intent": "Bold title opening card displaying the core hook proposition.",
                "transition_in": "FADE",
                "transition_out": "FADE",
                "asset_requirements": [
                    {
                        "id": uuid.uuid4(),
                        "scene_id": hook_scene_id,
                        "asset_type": AssetType.BACKGROUND.value,
                        "purpose": "Opening hook background card",
                        "query_hint": "Dark theme cinematic background",
                        "required": True,
                        "status": AssetRequirementStatus.PENDING.value,
                        "license_requirement": LicenseRequirement.COMMERCIAL_ALLOWED.value,
                    }
                ],
            }
        )
        scene_order += 1

    # 2. Process Sections & Statements
    sections = script_data.get("sections", [])
    for sec in sorted(sections, key=lambda s: s.get("section_order", 0)):
        sec_id_val = sec.get("id")
        sec_id = uuid.UUID(str(sec_id_val)) if sec_id_val else None
        statements = sec.get("statements", [])

        if not statements:
            # Empty section fallback
            heading = sec.get("heading", "Section")
            s_id = uuid.uuid4()
            scenes.append(
                {
                    "id": s_id,
                    "production_request_id": production_request_id,
                    "scene_order": scene_order,
                    "script_section_id": sec_id,
                    "start_statement_id": None,
                    "end_statement_id": None,
                    "scene_type": SceneType.NARRATION.value,
                    "narration_text": heading,
                    "estimated_duration_ms": estimate_narration_ms(heading),
                    "visual_intent": f"Visual scene for section: {heading}",
                    "transition_in": "FADE",
                    "transition_out": "FADE",
                    "asset_requirements": [
                        {
                            "id": uuid.uuid4(),
                            "scene_id": s_id,
                            "asset_type": AssetType.BACKGROUND.value,
                            "purpose": f"Background for {heading}",
                            "query_hint": None,
                            "required": True,
                            "status": AssetRequirementStatus.PENDING.value,
                            "license_requirement": LicenseRequirement.COMMERCIAL_ALLOWED.value,
                        }
                    ],
                }
            )
            scene_order += 1
            continue

        # Group statements or emit individual scenes
        for stmt in sorted(statements, key=lambda st: st.get("statement_order", 0)):
            stmt_id_val = stmt.get("id")
            stmt_id = uuid.UUID(str(stmt_id_val)) if stmt_id_val else None
            stmt_text = str(stmt.get("statement_text", "")).strip()
            stmt_type = str(stmt.get("statement_type", "CREATIVE"))

            if not stmt_text:
                continue

            # Detect scene type
            if stmt_type == "CTA":
                scene_type = SceneType.CTA
                visual_intent = "Call to action prompt overlay."
            elif '"' in stmt_text or "“" in stmt_text or stmt_type == "ATTRIBUTED":
                scene_type = SceneType.QUOTE
                visual_intent = "Attributed quotation callout card."
            elif any(char.isdigit() for char in stmt_text) and stmt_type == "FACTUAL":
                scene_type = SceneType.STATISTIC
                visual_intent = "Data metric and statistical infographic display."
            else:
                scene_type = SceneType.NARRATION
                visual_intent = f"Narration visual card for: {sec.get('heading', 'Content')}"

            dur_ms = estimate_narration_ms(stmt_text)
            s_id = uuid.uuid4()
            scenes.append(
                {
                    "id": s_id,
                    "production_request_id": production_request_id,
                    "scene_order": scene_order,
                    "script_section_id": sec_id,
                    "start_statement_id": stmt_id,
                    "end_statement_id": stmt_id,
                    "scene_type": scene_type.value,
                    "narration_text": stmt_text,
                    "estimated_duration_ms": dur_ms,
                    "visual_intent": visual_intent,
                    "transition_in": "FADE",
                    "transition_out": "FADE",
                    "asset_requirements": [
                        {
                            "id": uuid.uuid4(),
                            "scene_id": s_id,
                            "asset_type": AssetType.BACKGROUND.value,
                            "purpose": f"Visual background for Scene {scene_order}",
                            "query_hint": None,
                            "required": True,
                            "status": AssetRequirementStatus.PENDING.value,
                            "license_requirement": LicenseRequirement.COMMERCIAL_ALLOWED.value,
                        }
                    ],
                }
            )
            scene_order += 1

    # 3. Closing CTA scene if present
    closing_text = str(script_data.get("closing_text", "")).strip()
    cta_text = str(script_data.get("cta_text", "")).strip()
    combined_closing = f"{closing_text} {cta_text}".strip()
    if combined_closing and not any(s["scene_type"] == SceneType.CTA.value for s in scenes):
        s_id = uuid.uuid4()
        scenes.append(
            {
                "id": s_id,
                "production_request_id": production_request_id,
                "scene_order": scene_order,
                "script_section_id": None,
                "start_statement_id": None,
                "end_statement_id": None,
                "scene_type": SceneType.CTA.value,
                "narration_text": combined_closing,
                "estimated_duration_ms": estimate_narration_ms(combined_closing),
                "visual_intent": "Outro summary and call to action screen.",
                "transition_in": "FADE",
                "transition_out": "FADE",
                "asset_requirements": [
                    {
                        "id": uuid.uuid4(),
                        "scene_id": s_id,
                        "asset_type": AssetType.BACKGROUND.value,
                        "purpose": "Closing CTA visual card",
                        "query_hint": None,
                        "required": True,
                        "status": AssetRequirementStatus.PENDING.value,
                        "license_requirement": LicenseRequirement.COMMERCIAL_ALLOWED.value,
                    }
                ],
            }
        )

    return scenes
