"""Pure deterministic application-layer Editorial Beat Planner and Timing Allocator.

Transforms StoryboardScenes and canonical source statements into structured
visual beat plans and materializes non-overlapping, gap-free physical timings.
Zero I/O, zero external dependencies, zero narration rewrites.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from omega.application.editorial_beat import (
    AssetReuseIntent,
    BeatMotionIntent,
    BeatSemanticRole,
    BeatTransitionIntent,
    EditorialBeatPlan,
    EditorialBeatSpec,
    EditorialBeatTiming,
    MaterializedBeatTimingPlan,
)
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy

MIN_BEAT_DURATION_MS: int = 1500
MIN_WORDS_FOR_BEAT: int = 4
LONG_HOOK_MIN_DURATION_SECONDS: float = 4.5
LONG_HOOK_MIN_WORDS: int = 10


def _split_into_sentences(text: str) -> list[str]:
    """Conservative sentence segmentation preserving exact source spans."""
    clean = text.strip()
    if not clean:
        return []

    # Split where sentence-ending punctuation is followed by whitespace and a capital/quote/digit
    pattern = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'“\(\[])')
    parts = pattern.split(clean)

    merged: list[str] = []
    abbrs = {"e.g.", "i.e.", "etc.", "vs.", "al.", "dr.", "mr.", "mrs."}

    for part in parts:
        p_str = part.strip()
        if not p_str:
            continue
        if merged and any(merged[-1].lower().endswith(a) for a in abbrs):
            merged[-1] = f"{merged[-1]} {p_str}"
        else:
            merged.append(p_str)

    return merged if merged else [clean]


def _calc_word_count(text: str) -> int:
    """Calculate deterministic word count from text span."""
    words = text.strip().split()
    return max(1, len(words))


def _extract_statement_fields(stmt: Any) -> tuple[int, str, str]:
    """Extract (statement_order, statement_text, statement_type) deterministically."""
    if isinstance(stmt, dict):
        order = int(stmt.get("statement_order", stmt.get("order", 1)))
        text = str(stmt.get("statement_text", stmt.get("text", ""))).strip()
        stype = str(stmt.get("statement_type", stmt.get("type", ""))).strip()
        return order, text, stype

    order = getattr(stmt, "statement_order", getattr(stmt, "order", 1))
    text = getattr(stmt, "statement_text", getattr(stmt, "text", ""))
    stype = getattr(stmt, "statement_type", getattr(stmt, "type", ""))
    return int(order), str(text).strip(), str(stype).strip()


def allocate_beat_timing(
    beat_specs: Sequence[EditorialBeatSpec],
    *,
    scene_duration_ms: int,
    min_beat_duration_ms: int = MIN_BEAT_DURATION_MS,
) -> tuple[EditorialBeatTiming, ...]:
    """Pure deterministic timing allocator using narration word weights.

    Invariants guaranteed:
    1. first start_ms = 0
    2. every next start_ms = previous end_ms
    3. every duration_ms > 0
    4. no gaps, no overlaps
    5. final end_ms = scene_duration_ms exactly
    6. sum(duration_ms) = scene_duration_ms exactly
    7. deterministic minimum-duration merging
    8. full source provenance: every source beat index represented exactly once
    """
    if scene_duration_ms <= 0:
        raise ValueError(f"scene_duration_ms must be > 0, got {scene_duration_ms}")
    if not beat_specs:
        raise ValueError("beat_specs sequence must not be empty")

    # If the scene is shorter than minimum duration or only 1 beat exists, materialize 1 beat
    if scene_duration_ms < min_beat_duration_ms or len(beat_specs) == 1:
        return (
            EditorialBeatTiming(
                materialized_index=0,
                source_beat_indices=tuple(range(len(beat_specs))),
                start_ms=0,
                end_ms=scene_duration_ms,
                duration_ms=scene_duration_ms,
            ),
        )

    # Candidate active beat tracking: list of [source_beat_indices, word_weight]
    active_beats: list[list[Any]] = [
        [[spec.beat_index], max(1, spec.word_weight)] for spec in beat_specs
    ]

    # Iteratively merge beats that fall below min_beat_duration_ms
    while len(active_beats) > 1:
        total_w = sum(w for _, w in active_beats)
        undersized_index: int | None = None

        for idx, (_, w) in enumerate(active_beats):
            raw_dur = round(scene_duration_ms * w / total_w)
            if raw_dur < min_beat_duration_ms:
                undersized_index = idx
                break

        if undersized_index is None:
            break

        # Merge with adjacent beat (prefer previous, otherwise next)
        target_idx = (
            undersized_index - 1 if undersized_index > 0 else undersized_index + 1
        )

        # Preserve canonical source order across participating source indices
        if target_idx > undersized_index:
            # undersized is earlier (e.g. 0 merged into 1): (0,) + (1,)
            active_beats[target_idx][0] = (
                active_beats[undersized_index][0] + active_beats[target_idx][0]
            )
        else:
            # undersized is later (e.g. 1 merged into 0): (0,) + (1,)
            active_beats[target_idx][0] = (
                active_beats[target_idx][0] + active_beats[undersized_index][0]
            )

        active_beats[target_idx][1] += active_beats[undersized_index][1]
        active_beats.pop(undersized_index)

    # Calculate final proportional durations
    final_count = len(active_beats)
    total_w = sum(w for _, w in active_beats)

    durations: list[int] = []
    accumulated_dur = 0

    for idx in range(final_count - 1):
        _, w = active_beats[idx]
        dur = round(scene_duration_ms * w / total_w)
        dur = max(1, dur)
        durations.append(dur)
        accumulated_dur += dur

    # Final beat absorbs residual rounding
    last_dur = scene_duration_ms - accumulated_dur
    if last_dur <= 0:
        # Extreme rounding edge case guard: borrow from previous
        last_dur = 1
        if durations:
            durations[-1] = max(1, durations[-1] - 1)
    durations.append(last_dur)

    # Build non-overlapping, gap-free timing models with provenance
    timings: list[EditorialBeatTiming] = []
    current_ms = 0

    for idx, dur in enumerate(durations):
        start = current_ms
        end = start + dur
        timings.append(
            EditorialBeatTiming(
                materialized_index=idx,
                source_beat_indices=tuple(active_beats[idx][0]),
                start_ms=start,
                end_ms=end,
                duration_ms=dur,
            )
        )
        current_ms = end

    return tuple(timings)


class EditorialBeatPlanner:
    """Deterministic, pure application-layer semantic beat planner."""

    @classmethod
    def plan(
        cls,
        *,
        scene: StoryboardScene,
        source_statements: Sequence[Any] | None = None,
    ) -> EditorialBeatPlan:
        """Plan semantic visual beats for a StoryboardScene without I/O or rendering.

        Preserves exact source narration spans and statement references.
        """
        scene_idx = scene.sequence_index
        narration = scene.narration_excerpt.strip()
        total_words = _calc_word_count(narration)

        # 1. Resolve canonical statement units relevant to this scene
        statement_items: list[tuple[int, str, str]] = []
        if source_statements:
            refs = set(scene.source_statement_references)
            for stmt in source_statements:
                order, text, stype = _extract_statement_fields(stmt)
                if (not refs or order in refs) and text:
                    statement_items.append((order, text, stype))

        # Fallback if caller provided no matching statement objects
        if not statement_items:
            first_ref = (
                scene.source_statement_references[0]
                if scene.source_statement_references
                else 1
            )
            statement_items = [(first_ref, narration, "")]

        # 2. Check for Hook Policy
        # Primary authority: explicit statement_type == "HOOK" (case-insensitive)
        has_explicit_hook = any(
            stype.strip().upper() == "HOOK" for _, _, stype in statement_items
        )
        has_any_statement_type = any(
            bool(stype.strip()) for _, _, stype in statement_items
        )

        # Conservative fallback for legacy/caller data without statement_type:
        # First scene (sequence_index == 1) AND TITLE_MOTION
        is_hook = has_explicit_hook or (
            not has_any_statement_type
            and scene_idx == 1
            and scene.visual_strategy == VisualStrategy.TITLE_MOTION
        )

        is_long_hook = (
            is_hook
            and (
                scene.estimated_duration_seconds >= LONG_HOOK_MIN_DURATION_SECONDS
                or total_words >= LONG_HOOK_MIN_WORDS
            )
        )

        if is_long_hook:
            return cls._plan_long_hook(
                scene=scene,
                statement_items=statement_items,
                total_words=total_words,
            )

        # 3. Check for Closing / Payoff Policy
        is_closing = (
            "closing" in scene.purpose.lower()
            or "payoff" in scene.purpose.lower()
            or scene.visual_strategy == VisualStrategy.CTA
        )

        # 4. Standard Multi-Sentence / Explanatory Segmentation
        beats: list[EditorialBeatSpec] = []

        # Break each statement into conservative sentence units
        sentence_units: list[tuple[int, str, str]] = []
        for order, text, stype in statement_items:
            sents = _split_into_sentences(text)
            # Merge tiny fragments (< 4 words) with adjacent in same statement
            merged_sents: list[str] = []
            for s in sents:
                if merged_sents and len(s.split()) < MIN_WORDS_FOR_BEAT:
                    merged_sents[-1] = f"{merged_sents[-1]} {s}"
                else:
                    merged_sents.append(s)

            for s in merged_sents:
                sentence_units.append((order, s, stype))

        if not sentence_units:
            sentence_units = [(1, narration, "")]

        total_units = len(sentence_units)

        for beat_idx, (order, span, stype) in enumerate(sentence_units):
            words = _calc_word_count(span)

            # Determine semantic role and preferred visual intent
            role, strat, motion, reuse = cls._classify_unit(
                span=span,
                stype=stype,
                unit_index=beat_idx,
                total_units=total_units,
                parent_strategy=scene.visual_strategy,
                is_closing=is_closing,
                is_hook=is_hook,
            )

            beats.append(
                EditorialBeatSpec(
                    beat_index=beat_idx,
                    parent_scene_index=scene_idx,
                    source_statement_references=(order,),
                    narration_span=span,
                    semantic_role=role,
                    word_weight=words,
                    preferred_visual_strategy=strat,
                    asset_reuse_intent=reuse,
                    motion_intent=motion,
                    transition_intent=BeatTransitionIntent.HARD_CUT,
                    asset_query_hint=scene.asset_query_hint if beat_idx == 0 else None,
                )
            )

        total_weight = sum(b.word_weight for b in beats)
        return EditorialBeatPlan(
            scene_index=scene_idx,
            beats=tuple(beats),
            total_word_weight=total_weight,
        )

    @classmethod
    def _plan_long_hook(
        cls,
        *,
        scene: StoryboardScene,
        statement_items: list[tuple[int, str, str]],
        total_words: int,
    ) -> EditorialBeatPlan:
        """Plan a two-beat hook visual subdivision (HOOK_TITLE -> CONTEXT).

        Visual subdivision over shared narration authority:
        Both visual beats reference the same hook statement without duplicating
        audio output.
        """
        scene_idx = scene.sequence_index
        item_refs = tuple(dict.fromkeys(order for order, _, _ in statement_items))
        all_refs = item_refs if item_refs else tuple(
            scene.source_statement_references
            if scene.source_statement_references
            else (1,)
        )

        # Collect all sentences across hook statements
        all_sentences: list[str] = []
        for _, text, _ in statement_items:
            all_sentences.extend(_split_into_sentences(text))

        if len(all_sentences) >= 2:
            # Natural sentence split
            span_a = all_sentences[0]
            span_b = " ".join(all_sentences[1:])
            weight_a = _calc_word_count(span_a)
            weight_b = _calc_word_count(span_b)
        else:
            # Single sentence: shared narration text with proportional weight split
            full_span = scene.narration_excerpt.strip()
            span_a = full_span
            span_b = full_span
            # Proportional weight split: ~38% title presentation, ~62% context imagery
            weight_a = max(1, round(total_words * 0.38))
            weight_b = max(1, total_words - weight_a)

        beat_a = EditorialBeatSpec(
            beat_index=0,
            parent_scene_index=scene_idx,
            source_statement_references=all_refs,
            narration_span=span_a,
            semantic_role=BeatSemanticRole.HOOK_TITLE,
            word_weight=weight_a,
            preferred_visual_strategy=VisualStrategy.TITLE_MOTION,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
            motion_intent=BeatMotionIntent.STATIC,
            transition_intent=BeatTransitionIntent.HARD_CUT,
            asset_query_hint=None,
        )

        beat_b = EditorialBeatSpec(
            beat_index=1,
            parent_scene_index=scene_idx,
            source_statement_references=all_refs,
            narration_span=span_b,
            semantic_role=BeatSemanticRole.CONTEXT,
            word_weight=weight_b,
            preferred_visual_strategy=VisualStrategy.BROLL,
            asset_reuse_intent=AssetReuseIntent.REUSE_PARENT,
            motion_intent=BeatMotionIntent.SLOW_PUSH_IN,
            transition_intent=BeatTransitionIntent.HARD_CUT,
            asset_query_hint=scene.asset_query_hint,
        )

        return EditorialBeatPlan(
            scene_index=scene_idx,
            beats=(beat_a, beat_b),
            total_word_weight=weight_a + weight_b,
        )

    @classmethod
    def _classify_unit(
        cls,
        *,
        span: str,
        stype: str,
        unit_index: int,
        total_units: int,
        parent_strategy: VisualStrategy,
        is_closing: bool,
        is_hook: bool = False,
    ) -> tuple[BeatSemanticRole, VisualStrategy, BeatMotionIntent, AssetReuseIntent]:
        """Deterministically determine role, strategy, motion, and asset reuse for a unit."""
        lower = span.lower()

        # Hook handling (short hook single beat)
        if is_hook:
            return (
                BeatSemanticRole.HOOK_TITLE,
                VisualStrategy.TITLE_MOTION,
                BeatMotionIntent.STATIC,
                AssetReuseIntent.REUSE_PARENT,
            )

        # Closing / Payoff handling
        if is_closing:
            if unit_index == total_units - 1:
                return (
                    BeatSemanticRole.CLOSING,
                    VisualStrategy.BROLL,
                    BeatMotionIntent.SLOW_PUSH_IN,
                    AssetReuseIntent.REUSE_PARENT,
                )
            return (
                BeatSemanticRole.PAYOFF,
                VisualStrategy.BROLL,
                BeatMotionIntent.SLOW_PUSH_IN,
                AssetReuseIntent.REUSE_PARENT,
            )

        # Evidence handling (statistics, numerical proof)
        has_stats = bool(re.search(r"(\d+(?:\.\d+)?(?:%|x|k|M|m|s|ms|percent))", span))
        if has_stats or parent_strategy == VisualStrategy.STATISTIC:
            strat = (
                VisualStrategy.STATISTIC
                if parent_strategy == VisualStrategy.STATISTIC
                else VisualStrategy.BROLL
            )
            reuse = (
                AssetReuseIntent.LOCAL_EXPLAINER
                if strat == VisualStrategy.STATISTIC
                else AssetReuseIntent.REUSE_PARENT
            )
            return BeatSemanticRole.EVIDENCE, strat, BeatMotionIntent.STATIC, reuse

        # Mechanism / Technical explanation handling
        mechanism_cues = (
            "because",
            "scatter",
            "wavelength",
            "molecule",
            "mechanism",
            "process",
            "path",
        )
        if parent_strategy == VisualStrategy.DIAGRAM or any(
            cue in lower for cue in mechanism_cues
        ):
            strat = (
                VisualStrategy.DIAGRAM
                if parent_strategy == VisualStrategy.DIAGRAM
                else VisualStrategy.BROLL
            )
            reuse = (
                AssetReuseIntent.LOCAL_EXPLAINER
                if strat == VisualStrategy.DIAGRAM
                else (
                    AssetReuseIntent.REUSE_PARENT
                    if unit_index == 0
                    else AssetReuseIntent.ALLOW_SECONDARY_PROVIDER
                )
            )
            return BeatSemanticRole.MECHANISM, strat, BeatMotionIntent.STATIC, reuse

        # Context (establishing scene baseline)
        if unit_index == 0:
            strat = (
                parent_strategy
                if parent_strategy in (VisualStrategy.BROLL, VisualStrategy.IMAGE)
                else VisualStrategy.BROLL
            )
            return (
                BeatSemanticRole.CONTEXT,
                strat,
                BeatMotionIntent.SLOW_PUSH_IN,
                AssetReuseIntent.REUSE_PARENT,
            )

        # General explanation
        strat = (
            parent_strategy
            if parent_strategy in (VisualStrategy.BROLL, VisualStrategy.IMAGE)
            else VisualStrategy.BROLL
        )
        return (
            BeatSemanticRole.EXPLANATION,
            strat,
            BeatMotionIntent.DRIFT,
            AssetReuseIntent.REUSE_PARENT,
        )

    @classmethod
    def allocate_timing(
        cls,
        plan: EditorialBeatPlan,
        *,
        scene_duration_ms: int,
        min_beat_duration_ms: int = MIN_BEAT_DURATION_MS,
    ) -> MaterializedBeatTimingPlan:
        """Materialize physical non-overlapping timings for a planned beat plan."""
        timings = allocate_beat_timing(
            plan.beats,
            scene_duration_ms=scene_duration_ms,
            min_beat_duration_ms=min_beat_duration_ms,
        )
        return MaterializedBeatTimingPlan(
            scene_index=plan.scene_index,
            scene_duration_ms=scene_duration_ms,
            timings=timings,
        )
