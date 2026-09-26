"""Pure deterministic editorial layout vocabulary and repetition controller.

Provides a rich visual vocabulary beyond the single split-screen template:
- FULL_BLEED_VISUAL: Edge-to-edge media with sleek lower-third safe overlay
- TEXT_OVER_VISUAL: Cinematic background media with prominent typography scrim
- SPLIT_LEFT_VISUAL: Text on left (50%), media on right (50%)
- SPLIT_RIGHT_VISUAL: Media on left (50%), text on right (50%)
- CENTERED_MECHANISM: Centered structured workflow / flow diagram
- DOCUMENT_FOCUS: Code editor / technical artifact inspection frame
- STATISTIC_FOCUS: Large hero metric callout with descriptive label
- QUOTE_FOCUS: Quote block with attribution
- IMAGE_WITH_CALLOUTS: Focused image with structured callout badges
- BROLL_WITH_MINIMAL_LABEL: Full-motion video with minimal lower-left pill label
- CHAPTER_TRANSITION: Clean section entry transition heading

Enforces repetition control:
- No 3 consecutive beats may use the same primary composition.
- Preserves semantic compatibility: never selects an incompatible layout merely for variety.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence

from omega.application.editorial_beat import BeatSemanticRole
from omega.application.storyboard_engine import VisualStrategy


class EditorialLayout(enum.StrEnum):
    """Deterministic editorial layout composition vocabulary."""

    FULL_BLEED_VISUAL = "FULL_BLEED_VISUAL"
    TEXT_OVER_VISUAL = "TEXT_OVER_VISUAL"
    SPLIT_LEFT_VISUAL = "SPLIT_LEFT_VISUAL"
    SPLIT_RIGHT_VISUAL = "SPLIT_RIGHT_VISUAL"
    CENTERED_MECHANISM = "CENTERED_MECHANISM"
    DOCUMENT_FOCUS = "DOCUMENT_FOCUS"
    STATISTIC_FOCUS = "STATISTIC_FOCUS"
    QUOTE_FOCUS = "QUOTE_FOCUS"
    IMAGE_WITH_CALLOUTS = "IMAGE_WITH_CALLOUTS"
    BROLL_WITH_MINIMAL_LABEL = "BROLL_WITH_MINIMAL_LABEL"
    CHAPTER_TRANSITION = "CHAPTER_TRANSITION"


# Deterministic mapping of (semantic_role, visual_strategy) to prioritized compatible layouts
_COMPATIBLE_LAYOUTS: dict[tuple[BeatSemanticRole, VisualStrategy], tuple[EditorialLayout, ...]] = {
    # HOOK_TITLE
    (BeatSemanticRole.HOOK_TITLE, VisualStrategy.TITLE_MOTION): (
        EditorialLayout.CHAPTER_TRANSITION,
        EditorialLayout.TEXT_OVER_VISUAL,
    ),
    (BeatSemanticRole.HOOK_TITLE, VisualStrategy.KINETIC_TEXT): (
        EditorialLayout.TEXT_OVER_VISUAL,
        EditorialLayout.CHAPTER_TRANSITION,
    ),
    # CONTEXT
    (BeatSemanticRole.CONTEXT, VisualStrategy.BROLL): (
        EditorialLayout.BROLL_WITH_MINIMAL_LABEL,
        EditorialLayout.FULL_BLEED_VISUAL,
        EditorialLayout.TEXT_OVER_VISUAL,
    ),
    (BeatSemanticRole.CONTEXT, VisualStrategy.IMAGE): (
        EditorialLayout.FULL_BLEED_VISUAL,
        EditorialLayout.SPLIT_LEFT_VISUAL,
        EditorialLayout.SPLIT_RIGHT_VISUAL,
    ),
    # EXPLANATION
    (BeatSemanticRole.EXPLANATION, VisualStrategy.IMAGE): (
        EditorialLayout.SPLIT_LEFT_VISUAL,
        EditorialLayout.SPLIT_RIGHT_VISUAL,
        EditorialLayout.IMAGE_WITH_CALLOUTS,
        EditorialLayout.TEXT_OVER_VISUAL,
    ),
    (BeatSemanticRole.EXPLANATION, VisualStrategy.BROLL): (
        EditorialLayout.TEXT_OVER_VISUAL,
        EditorialLayout.BROLL_WITH_MINIMAL_LABEL,
        EditorialLayout.FULL_BLEED_VISUAL,
    ),
    (BeatSemanticRole.EXPLANATION, VisualStrategy.CODE_DEMO): (
        EditorialLayout.DOCUMENT_FOCUS,
    ),
    (BeatSemanticRole.EXPLANATION, VisualStrategy.DIAGRAM): (
        EditorialLayout.CENTERED_MECHANISM,
    ),
    # MECHANISM
    (BeatSemanticRole.MECHANISM, VisualStrategy.DIAGRAM): (
        EditorialLayout.CENTERED_MECHANISM,
    ),
    (BeatSemanticRole.MECHANISM, VisualStrategy.CODE_DEMO): (
        EditorialLayout.DOCUMENT_FOCUS,
    ),
    (BeatSemanticRole.MECHANISM, VisualStrategy.IMAGE): (
        EditorialLayout.SPLIT_RIGHT_VISUAL,
        EditorialLayout.IMAGE_WITH_CALLOUTS,
        EditorialLayout.SPLIT_LEFT_VISUAL,
    ),
    (BeatSemanticRole.MECHANISM, VisualStrategy.BROLL): (
        EditorialLayout.TEXT_OVER_VISUAL,
        EditorialLayout.BROLL_WITH_MINIMAL_LABEL,
    ),
    # EVIDENCE
    (BeatSemanticRole.EVIDENCE, VisualStrategy.STATISTIC): (
        EditorialLayout.STATISTIC_FOCUS,
    ),
    (BeatSemanticRole.EVIDENCE, VisualStrategy.CODE_DEMO): (
        EditorialLayout.DOCUMENT_FOCUS,
    ),
    (BeatSemanticRole.EVIDENCE, VisualStrategy.IMAGE): (
        EditorialLayout.SPLIT_RIGHT_VISUAL,
        EditorialLayout.DOCUMENT_FOCUS,
        EditorialLayout.SPLIT_LEFT_VISUAL,
    ),
    (BeatSemanticRole.EVIDENCE, VisualStrategy.BROLL): (
        EditorialLayout.TEXT_OVER_VISUAL,
        EditorialLayout.BROLL_WITH_MINIMAL_LABEL,
    ),
    # PAYOFF / CLOSING
    (BeatSemanticRole.PAYOFF, VisualStrategy.BROLL): (
        EditorialLayout.FULL_BLEED_VISUAL,
        EditorialLayout.TEXT_OVER_VISUAL,
    ),
    (BeatSemanticRole.CLOSING, VisualStrategy.BROLL): (
        EditorialLayout.TEXT_OVER_VISUAL,
        EditorialLayout.FULL_BLEED_VISUAL,
    ),
    (BeatSemanticRole.CLOSING, VisualStrategy.CTA): (
        EditorialLayout.TEXT_OVER_VISUAL,
        EditorialLayout.CHAPTER_TRANSITION,
    ),
}

# Default fallbacks per strategy when role is not explicitly mapped
_STRATEGY_DEFAULTS: dict[VisualStrategy, tuple[EditorialLayout, ...]] = {
    VisualStrategy.TITLE_MOTION: (EditorialLayout.CHAPTER_TRANSITION, EditorialLayout.TEXT_OVER_VISUAL),
    VisualStrategy.KINETIC_TEXT: (EditorialLayout.TEXT_OVER_VISUAL,),
    VisualStrategy.DIAGRAM: (EditorialLayout.CENTERED_MECHANISM,),
    VisualStrategy.STATISTIC: (EditorialLayout.STATISTIC_FOCUS,),
    VisualStrategy.CODE_DEMO: (EditorialLayout.DOCUMENT_FOCUS,),
    VisualStrategy.INFOGRAPHIC: (EditorialLayout.CENTERED_MECHANISM,),
    VisualStrategy.IMAGE: (
        EditorialLayout.SPLIT_LEFT_VISUAL,
        EditorialLayout.SPLIT_RIGHT_VISUAL,
        EditorialLayout.FULL_BLEED_VISUAL,
        EditorialLayout.IMAGE_WITH_CALLOUTS,
    ),
    VisualStrategy.BROLL: (
        EditorialLayout.BROLL_WITH_MINIMAL_LABEL,
        EditorialLayout.TEXT_OVER_VISUAL,
        EditorialLayout.FULL_BLEED_VISUAL,
    ),
    VisualStrategy.SCREENSHOT: (EditorialLayout.DOCUMENT_FOCUS, EditorialLayout.SPLIT_LEFT_VISUAL),
    VisualStrategy.CTA: (EditorialLayout.TEXT_OVER_VISUAL,),
}


def get_compatible_layouts(
    semantic_role: BeatSemanticRole,
    visual_strategy: VisualStrategy,
) -> tuple[EditorialLayout, ...]:
    """Return all compatible layouts for given semantic role and visual strategy."""
    return _COMPATIBLE_LAYOUTS.get(
        (semantic_role, visual_strategy),
        _STRATEGY_DEFAULTS.get(visual_strategy, (EditorialLayout.SPLIT_LEFT_VISUAL,)),
    )


def select_editorial_layout(
    *,
    semantic_role: BeatSemanticRole,
    visual_strategy: VisualStrategy,
    history: Sequence[EditorialLayout] = (),
) -> EditorialLayout:
    """Deterministically select an editorial layout obeying semantic compatibility and repetition rules.

    Invariants:
    1. Returns a semantically compatible layout.
    2. Never selects the same layout 3 times consecutively if another compatible layout exists.
    3. Preserves deterministic behavior: same inputs yield same layout.
    """
    candidates = _COMPATIBLE_LAYOUTS.get(
        (semantic_role, visual_strategy),
        _STRATEGY_DEFAULTS.get(visual_strategy, (EditorialLayout.SPLIT_LEFT_VISUAL,)),
    )

    if not candidates:
        return EditorialLayout.SPLIT_LEFT_VISUAL

    # If only 1 compatible candidate exists, return it (semantic compatibility over counter)
    if len(candidates) == 1:
        return candidates[0]

    # Check repetition history (last 2 layouts)
    has_two_consecutive = (
        len(history) >= 2 and history[-1] == history[-2]
    )

    # Check if candidate 0 would make 3 consecutive identical layouts
    primary = candidates[0]
    if has_two_consecutive and history[-1] == primary:
        # Repetition limit reached: pick the next compatible candidate that differs
        for alt in candidates[1:]:
            if alt != primary:
                return alt

    # If previous 1 beat was identical, cycle to provide rich visual variation
    if len(history) >= 1 and history[-1] == primary and len(candidates) > 1:
        # Check if alternating provides balanced pacing
        alt = candidates[1]
        if len(history) >= 2 and history[-2] == alt:
            # Alternating pattern: primary -> alt -> primary
            return primary
        return alt

    return primary
