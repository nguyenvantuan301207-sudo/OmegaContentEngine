"""Storyboard Engine for converting scripts into visual plans."""

import enum
from typing import Any

from pydantic import BaseModel, Field


class VisualStrategy(enum.StrEnum):
    TITLE_MOTION = "TITLE_MOTION"
    DIAGRAM = "DIAGRAM"
    CODE_DEMO = "CODE_DEMO"
    INFOGRAPHIC = "INFOGRAPHIC"
    STATISTIC = "STATISTIC"
    KINETIC_TEXT = "KINETIC_TEXT"
    IMAGE = "IMAGE"
    BROLL = "BROLL"
    SCREENSHOT = "SCREENSHOT"
    CTA = "CTA"

class StoryboardScene(BaseModel):
    sequence_index: int
    section_id: str
    purpose: str
    source_statement_references: list[int]
    narration_excerpt: str
    estimated_duration_seconds: float
    visual_strategy: VisualStrategy
    visual_brief: str
    on_screen_text: str | None = None
    motion_hint: str | None = None
    asset_query_hint: str | None = None
    importance: str = "NORMAL"
    citations: list[dict[str, Any]] = Field(default_factory=list)

class StoryboardPlan(BaseModel):
    title: str
    estimated_duration_seconds: float
    scenes: list[StoryboardScene]

class StoryboardEngine:
    """Deterministic Storyboard Engine."""

    def __init__(self):
        self._static_cards = {
            VisualStrategy.DIAGRAM,
            VisualStrategy.INFOGRAPHIC,
            VisualStrategy.STATISTIC,
            VisualStrategy.KINETIC_TEXT,
        }

    def generate_storyboard(self, script_dict: dict[str, Any]) -> StoryboardPlan:
        scenes = []
        sequence_index = 1

        sections = script_dict.get("sections", [])
        total_script_duration = script_dict.get("estimated_duration_seconds", 0)

        is_first_scene = True

        for sec_idx, section in enumerate(sections):
            section_heading = section.get("heading", f"Section {sec_idx + 1}")
            statements = section.get("statements", [])

            current_group = []
            current_word_count = 0

            for i, stmt in enumerate(statements):
                current_group.append(stmt)
                words = len(stmt.get("statement_text", "").split())
                current_word_count += words

                is_last_statement = (i == len(statements) - 1)
                is_last_section = (sec_idx == len(sections) - 1)
                is_very_last = is_last_statement and is_last_section

                # Create a scene if we reach ~20 words or at the end of the section
                if current_word_count >= 18 or is_last_statement:
                    scene = self._create_scene(
                        sequence_index=sequence_index,
                        section_heading=section_heading,
                        statements=current_group,
                        is_first=is_first_scene,
                        is_last=is_very_last,
                        history=[s.visual_strategy for s in scenes[-2:]]
                    )
                    scenes.append(scene)
                    sequence_index += 1
                    is_first_scene = False

                    current_group = []
                    current_word_count = 0

        # Adjust estimated durations proportionally
        if scenes:
            total_words = sum(len(s.narration_excerpt.split()) for s in scenes)
            if total_words > 0:
                for s in scenes:
                    s.estimated_duration_seconds = round(
                        (len(s.narration_excerpt.split()) / total_words) * total_script_duration, 1
                    )

        return StoryboardPlan(
            title=script_dict.get("title", "Storyboard"),
            estimated_duration_seconds=total_script_duration,
            scenes=scenes
        )

    def _create_scene(
        self,
        sequence_index: int,
        section_heading: str,
        statements: list[dict[str, Any]],
        is_first: bool,
        is_last: bool,
        history: list[VisualStrategy]
    ) -> StoryboardScene:
        narration = " ".join(s.get("statement_text", "") for s in statements)
        word_count = len(narration.split())

        citations = []
        for s in statements:
            cits = s.get("citations")
            if cits:
                citations.extend(cits)

        refs = [s.get("statement_order", 0) for s in statements]

        raw_strategy = self._select_strategy(narration, word_count, is_first, is_last)
        strategy = self._enforce_variety_rules(raw_strategy, history)

        purpose = f"Illustrate {section_heading}"
        brief = f"Visual for: {narration[:60]}..."
        motion_hint = "Smooth fade"

        if strategy == VisualStrategy.TITLE_MOTION:
            purpose = "Hook the viewer and introduce the topic."
            brief = "Dynamic title animation covering the core topic."
            motion_hint = "Kinetic typography animation."
        elif strategy == VisualStrategy.CTA:
            purpose = "Call to action."
            brief = "Subscribe and comment prompt."
            motion_hint = "Pop-in CTA elements."
        elif strategy == VisualStrategy.CODE_DEMO:
            purpose = "Show implementation details."
            brief = "Code snippet demonstrating the concept."
            motion_hint = "Typing effect or syntax highlight."
        elif strategy == VisualStrategy.DIAGRAM:
            purpose = "Explain system architecture."
            brief = "Flowchart or architecture diagram."
            motion_hint = "Sequential reveal of diagram nodes."
        elif strategy == VisualStrategy.STATISTIC:
            purpose = "Highlight key metrics."
            brief = "Large number with descriptive label."
            motion_hint = "Number counter rolling up."
        elif strategy == VisualStrategy.INFOGRAPHIC:
            purpose = "Compare technical tradeoffs."
            brief = "Multi-factor visual comparison."
            motion_hint = "Slide-in infographic."
        elif strategy == VisualStrategy.KINETIC_TEXT:
            purpose = "Emphasize key statement."
            brief = "Bold animated text filling the screen."
            motion_hint = "Word-by-word pop-in."
        elif strategy == VisualStrategy.BROLL:
            purpose = "Provide visual context."
            brief = "Relevant stock BROLL matching the narrative."
            motion_hint = "Slow pan."
        elif strategy == VisualStrategy.IMAGE:
            purpose = "Concrete visual example."
            brief = "High quality image illustrating the concept."
            motion_hint = "Ken Burns effect."
        elif strategy == VisualStrategy.SCREENSHOT:
            purpose = "UI demonstration."
            brief = "Screenshot of relevant interface."
            motion_hint = "Zoom into relevant area."

        # Ensure we have some short on-screen text
        on_screen_text = None
        if strategy in self._static_cards or strategy == VisualStrategy.TITLE_MOTION:
            on_screen_text = narration[:30] + "..." if len(narration) > 30 else narration

        return StoryboardScene(
            sequence_index=sequence_index,
            section_id=section_heading,
            purpose=purpose,
            source_statement_references=refs,
            narration_excerpt=narration,
            estimated_duration_seconds=0.0,
            visual_strategy=strategy,
            visual_brief=brief,
            on_screen_text=on_screen_text,
            motion_hint=motion_hint,
            asset_query_hint="abstract technology background",
            importance="HIGH" if citations else "NORMAL",
            citations=citations
        )

    def _select_strategy(self, narration: str, word_count: int, is_first: bool, is_last: bool) -> VisualStrategy:
        if is_first:
            return VisualStrategy.TITLE_MOTION
        if is_last:
            return VisualStrategy.CTA

        n_lower = narration.lower()

        if any(w in n_lower for w in ["source code", "snippet", "syntax", "function", "class", "endpoint", "pydantic", "concrete programming"]):
            return VisualStrategy.CODE_DEMO

        if any(w in n_lower for w in ["architecture", "system", "flow", "loop", "orchestrate", "infrastructure", "multiplexing", "mechanics", "async", "coroutine", "implementation"]):
            return VisualStrategy.DIAGRAM

        if any(w in n_lower for w in ["percent", "%", "benchmark", "latency", "milliseconds", "throughput", "metrics", "rate"]):
            return VisualStrategy.STATISTIC

        if any(w in n_lower for w in ["comparison", "tradeoff", "vs", "multi-factor", "rules", "principles"]):
            return VisualStrategy.INFOGRAPHIC

        if any(w in n_lower for w in ["ui", "interface", "screen", "dashboard"]):
            return VisualStrategy.SCREENSHOT

        if word_count < 15:
            return VisualStrategy.KINETIC_TEXT

        # Alternate naturally depending on some hash to keep it deterministic
        if len(n_lower) % 2 == 0:
            return VisualStrategy.BROLL
        return VisualStrategy.IMAGE

    def _enforce_variety_rules(self, strategy: VisualStrategy, history: list[VisualStrategy]) -> VisualStrategy:
        if not history:
            return strategy

        # Max 2 consecutive of same strategy
        if len(history) == 2 and history[0] == history[1] == strategy:
            strategy = self._get_alternative(strategy, is_static_card=False)

        # Max 2 consecutive static cards
        if strategy in self._static_cards:
            static_count = sum(1 for h in history if h in self._static_cards)
            if static_count >= 2:
                strategy = self._get_alternative(strategy, is_static_card=True)

        return strategy

    def _get_alternative(self, current: VisualStrategy, is_static_card: bool) -> VisualStrategy:
        if is_static_card:
            return VisualStrategy.BROLL

        if current == VisualStrategy.BROLL:
            return VisualStrategy.IMAGE
        if current == VisualStrategy.IMAGE:
            return VisualStrategy.BROLL

        return VisualStrategy.BROLL
