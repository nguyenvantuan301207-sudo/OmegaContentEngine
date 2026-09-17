"""Tests for P18-G2C2A Scene Source Statement Resolver.

Verifies:
1. Normal unique section resolves exact statements;
2. Two sections both containing statement_order=1 do not cross-contaminate;
3. Same statement orders in different section headings resolve by section;
4. Duplicate section headings but only one exact narration match resolves;
5. Duplicate headings + identical refs + identical narration is AMBIGUOUS;
6. Missing reference fails closed;
7. Changed narration fails closed;
8. Whitespace-only differences resolve;
9. Reordered statement references do not silently resolve;
10. Explicit HOOK statement_type survives resolution;
11. Explicit CLOSING/CTA statement types survive resolution.
"""

from __future__ import annotations

from typing import Any

from omega.application.beat_source_resolver import resolve_scene_source_statements
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy


def _make_scene(
    sequence_index: int = 1,
    section_id: str = "Introduction",
    references: list[int] | None = None,
    narration: str = "Hello world statement.",
) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=sequence_index,
        section_id=section_id,
        purpose="Purpose",
        source_statement_references=references if references is not None else [1],
        narration_excerpt=narration,
        estimated_duration_seconds=3.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Visual brief",
    )


def test_01_normal_unique_section_resolves_exact_statements():
    """A standard script section resolves statements cleanly matching references and narration."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Introduction",
                "statements": [
                    {"statement_order": 1, "statement_text": "First statement.", "statement_type": "STATEMENT"},
                    {"statement_order": 2, "statement_text": "Second statement.", "statement_type": "STATEMENT"},
                ],
            }
        ]
    }
    scene = _make_scene(
        section_id="Introduction",
        references=[1, 2],
        narration="First statement. Second statement.",
    )
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is True
    assert res.failure_reason is None
    assert len(res.statements) == 2
    assert res.statements[0]["statement_order"] == 1
    assert res.statements[1]["statement_order"] == 2


def test_02_two_sections_with_same_statement_orders_do_not_cross_contaminate():
    """Sections with overlapping statement_order (e.g. both start at 1) are isolated by section heading."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Section Alpha",
                "statements": [
                    {"statement_order": 1, "statement_text": "Alpha one.", "statement_type": "STATEMENT"},
                ],
            },
            {
                "heading": "Section Beta",
                "statements": [
                    {"statement_order": 1, "statement_text": "Beta one.", "statement_type": "STATEMENT"},
                ],
            },
        ]
    }
    scene_alpha = _make_scene(section_id="Section Alpha", references=[1], narration="Alpha one.")
    res_alpha = resolve_scene_source_statements(script_dict=script_dict, scene=scene_alpha)
    assert res_alpha.resolved is True
    assert res_alpha.statements[0]["statement_text"] == "Alpha one."

    scene_beta = _make_scene(section_id="Section Beta", references=[1], narration="Beta one.")
    res_beta = resolve_scene_source_statements(script_dict=script_dict, scene=scene_beta)
    assert res_beta.resolved is True
    assert res_beta.statements[0]["statement_text"] == "Beta one."


def test_03_same_statement_orders_in_different_headings_resolve_by_section():
    """Resolving selects the section matching scene.section_id even if statement_order arrays are identical."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Part 1",
                "statements": [
                    {"statement_order": 10, "statement_text": "Text in part 1.", "statement_type": "STATEMENT"},
                ],
            },
            {
                "heading": "Part 2",
                "statements": [
                    {"statement_order": 10, "statement_text": "Text in part 2.", "statement_type": "STATEMENT"},
                ],
            },
        ]
    }
    scene = _make_scene(section_id="Part 2", references=[10], narration="Text in part 2.")
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is True
    assert res.statements[0]["statement_text"] == "Text in part 2."


def test_04_duplicate_section_headings_resolved_by_unique_narration():
    """When duplicate section headings exist, exact narration matching isolates the single matching section."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Discussion",
                "statements": [
                    {"statement_order": 1, "statement_text": "First discussion.", "statement_type": "STATEMENT"},
                ],
            },
            {
                "heading": "Discussion",
                "statements": [
                    {"statement_order": 1, "statement_text": "Second discussion.", "statement_type": "STATEMENT"},
                ],
            },
        ]
    }
    scene = _make_scene(section_id="Discussion", references=[1], narration="Second discussion.")
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is True
    assert res.statements[0]["statement_text"] == "Second discussion."


def test_05_duplicate_headings_identical_refs_identical_narration_is_ambiguous():
    """When duplicate section headings have identical references and identical narration, resolution fails as ambiguous."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Identical",
                "statements": [
                    {"statement_order": 1, "statement_text": "Cloned text.", "statement_type": "STATEMENT"},
                ],
            },
            {
                "heading": "Identical",
                "statements": [
                    {"statement_order": 1, "statement_text": "Cloned text.", "statement_type": "STATEMENT"},
                ],
            },
        ]
    }
    scene = _make_scene(section_id="Identical", references=[1], narration="Cloned text.")
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is False
    assert res.failure_reason == "SOURCE_STATEMENTS_AMBIGUOUS"


def test_06_missing_reference_fails_closed():
    """If a referenced statement_order is missing from the candidate section, resolution fails closed."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Overview",
                "statements": [
                    {"statement_order": 1, "statement_text": "Only statement one.", "statement_type": "STATEMENT"},
                ],
            }
        ]
    }
    # Scene references statement 1 and 2
    scene = _make_scene(section_id="Overview", references=[1, 2], narration="Only statement one.")
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is False
    assert res.failure_reason == "SOURCE_STATEMENTS_NOT_RESOLVED"


def test_07_changed_narration_fails_closed():
    """If joined statement text does not equal narration_excerpt, resolution fails closed."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Overview",
                "statements": [
                    {"statement_order": 1, "statement_text": "Original text.", "statement_type": "STATEMENT"},
                ],
            }
        ]
    }
    scene = _make_scene(section_id="Overview", references=[1], narration="Completely modified narration.")
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is False
    assert res.failure_reason == "SOURCE_STATEMENTS_NOT_RESOLVED"


def test_08_whitespace_only_differences_resolve():
    """Conservative whitespace normalization allows minor spacing/newline variations to resolve safely."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "  Spaced   Heading  ",
                "statements": [
                    {"statement_order": 1, "statement_text": "Line one.\n\nLine two.", "statement_type": "STATEMENT"},
                ],
            }
        ]
    }
    scene = _make_scene(section_id="Spaced Heading", references=[1], narration="Line one. Line two.")
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is True
    assert res.statements[0]["statement_order"] == 1


def test_09_reordered_statement_references_do_not_silently_resolve():
    """Reordered statement references (e.g. [2, 1]) relative to section order fail closed."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Sequenced",
                "statements": [
                    {"statement_order": 1, "statement_text": "Statement 1.", "statement_type": "STATEMENT"},
                    {"statement_order": 2, "statement_text": "Statement 2.", "statement_type": "STATEMENT"},
                ],
            }
        ]
    }
    # Scene provides inverted references [2, 1]
    scene = _make_scene(section_id="Sequenced", references=[2, 1], narration="Statement 2. Statement 1.")
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is False
    assert res.failure_reason == "SOURCE_STATEMENTS_NOT_RESOLVED"


def test_10_explicit_hook_statement_type_survives():
    """Explicit HOOK statement_type is preserved in resolved statement dicts."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Hook Section",
                "statements": [
                    {"statement_order": 1, "statement_text": "Shocking fact!", "statement_type": "HOOK"},
                ],
            }
        ]
    }
    scene = _make_scene(section_id="Hook Section", references=[1], narration="Shocking fact!")
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is True
    assert res.statements[0]["statement_type"] == "HOOK"


def test_11_explicit_closing_and_cta_statement_types_survive():
    """Explicit CLOSING and CTA statement types are preserved verbatim."""
    script_dict: dict[str, Any] = {
        "sections": [
            {
                "heading": "Conclusion",
                "statements": [
                    {"statement_order": 5, "statement_text": "In summary.", "statement_type": "CLOSING"},
                    {"statement_order": 6, "statement_text": "Subscribe now.", "statement_type": "CTA"},
                ],
            }
        ]
    }
    scene = _make_scene(
        section_id="Conclusion",
        references=[5, 6],
        narration="In summary. Subscribe now.",
    )
    res = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert res.resolved is True
    assert len(res.statements) == 2
    assert res.statements[0]["statement_type"] == "CLOSING"
    assert res.statements[1]["statement_type"] == "CTA"
