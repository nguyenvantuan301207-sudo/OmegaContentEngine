"""Narrative strategy taxonomy, constraints, and selection models for P21-B Narrative Director.

Defines canonical story structures (e.g. HOW_IT_WORKS, MYTH_REALITY, PROBLEM_SOLUTION,
MYSTERY_REVEAL, CHRONOLOGICAL), suitability boundaries for format profiles, topic signals,
and draft candidate representations.
"""

from __future__ import annotations

import enum
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from omega.domain.narrative_plan import (
    FORMAT_PROFILE_CONSTRAINTS,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeSection,
    NarrativeSectionRole,
)


class NarrativeStrategy(enum.StrEnum):
    """Canonical narrative strategies defining distinct story structures."""

    PROBLEM_SOLUTION = "PROBLEM_SOLUTION"
    QUESTION_ANSWER = "QUESTION_ANSWER"
    MYSTERY_REVEAL = "MYSTERY_REVEAL"
    CAUSE_EFFECT = "CAUSE_EFFECT"
    CHRONOLOGICAL = "CHRONOLOGICAL"
    CONTRAST_COMPARISON = "CONTRAST_COMPARISON"
    MYTH_REALITY = "MYTH_REALITY"
    ESCALATION_PAYOFF = "ESCALATION_PAYOFF"
    HOW_IT_WORKS = "HOW_IT_WORKS"
    CASE_STUDY = "CASE_STUDY"


class NarrativeStrategyDefinition(BaseModel):
    """Declarative specification for a narrative strategy."""

    strategy: NarrativeStrategy
    name: str
    description: str
    suitable_profiles: set[NarrativeFormatProfile]
    preferred_roles: list[NarrativeSectionRole]
    has_open_loop: bool = True
    topic_keywords: list[str] = Field(default_factory=list)
    intent_signals: list[str] = Field(default_factory=list)
    min_sections: int = 3
    max_sections: int = 14

    model_config = ConfigDict(frozen=True)


STRATEGY_CATALOG: dict[NarrativeStrategy, NarrativeStrategyDefinition] = {
    NarrativeStrategy.HOW_IT_WORKS: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.HOW_IT_WORKS,
        name="How It Works",
        description="Deconstructs a technical system, mechanism, or principle into sequential functional mechanics.",
        suitable_profiles={NarrativeFormatProfile.SHORT, NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=True,
        topic_keywords=["how", "works", "mechanism", "architecture", "system", "engine", "internals", "algorithm"],
        intent_signals=["explain", "technical breakdown", "understand how", "deep dive"],
        min_sections=3,
        max_sections=12,
    ),
    NarrativeStrategy.MYTH_REALITY: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.MYTH_REALITY,
        name="Myth vs Reality",
        description="Challenges widespread misconceptions or common fallacies with empirical evidence and counter-proofs.",
        suitable_profiles={NarrativeFormatProfile.SHORT, NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=True,
        topic_keywords=["myth", "wrong", "truth", "misconception", "lie", "actually", "fallacy", "debunked"],
        intent_signals=["disprove", "debunk", "correct misconception", "reveal the truth"],
        min_sections=3,
        max_sections=10,
    ),
    NarrativeStrategy.PROBLEM_SOLUTION: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.PROBLEM_SOLUTION,
        name="Problem → Solution",
        description="Establishes a critical pain point or system bottleneck, evaluates failed attempts, and presents the optimal solution.",
        suitable_profiles={NarrativeFormatProfile.SHORT, NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=True,
        topic_keywords=["problem", "solution", "fix", "solve", "failure", "bottleneck", "resolve", "optimize"],
        intent_signals=["overcome", "solve problem", "fix issue", "remediation"],
        min_sections=3,
        max_sections=12,
    ),
    NarrativeStrategy.MYSTERY_REVEAL: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.MYSTERY_REVEAL,
        name="Mystery → Reveal",
        description="Opens a compelling paradox or anomaly, delays resolution with investigating clues, and resolves in a surprising payoff.",
        suitable_profiles={NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.ESCALATION,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=True,
        topic_keywords=["mystery", "secret", "anomaly", "paradox", "unsolved", "why did", "bizarre", "hidden"],
        intent_signals=["uncover", "investigate", "reveal mystery", "solve puzzle"],
        min_sections=5,
        max_sections=14,
    ),
    NarrativeStrategy.QUESTION_ANSWER: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.QUESTION_ANSWER,
        name="Question → Answer",
        description="Focuses directly on answering a fundamental question, providing authoritative context and factual evidence.",
        suitable_profiles={NarrativeFormatProfile.SHORT, NarrativeFormatProfile.MEDIUM},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
        ],
        has_open_loop=True,
        topic_keywords=["what", "why", "who", "which", "where", "can", "does", "is"],
        intent_signals=["answer question", "direct explanation", "clear answer"],
        min_sections=3,
        max_sections=8,
    ),
    NarrativeStrategy.CAUSE_EFFECT: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.CAUSE_EFFECT,
        name="Cause & Effect",
        description="Traces the causal mechanisms and cascading downstream consequences of a specific action, decision, or phenomenon.",
        suitable_profiles={NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.ESCALATION,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=False,
        topic_keywords=["cause", "effect", "consequence", "impact", "result", "led to", "domino", "cascading"],
        intent_signals=["trace causality", "consequences", "impact analysis"],
        min_sections=4,
        max_sections=12,
    ),
    NarrativeStrategy.CHRONOLOGICAL: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.CHRONOLOGICAL,
        name="Chronological Progression",
        description="Follows a timeline progression, exploring historical origins, milestone events, and modern culmination.",
        suitable_profiles={NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.ESCALATION,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=False,
        topic_keywords=["history", "timeline", "origin", "evolution", "rise", "fall", "first", "chronology"],
        intent_signals=["historical account", "evolution", "chronological progression"],
        min_sections=5,
        max_sections=14,
    ),
    NarrativeStrategy.CONTRAST_COMPARISON: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.CONTRAST_COMPARISON,
        name="Contrast & Comparison",
        description="Compares two or more paradigms, technologies, or approaches across objective evaluation dimensions.",
        suitable_profiles={NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.ESCALATION,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=False,
        topic_keywords=["vs", "versus", "comparison", "difference", "compare", "better", "alternative"],
        intent_signals=["compare", "trade-off analysis", "versus evaluation"],
        min_sections=4,
        max_sections=12,
    ),
    NarrativeStrategy.ESCALATION_PAYOFF: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.ESCALATION_PAYOFF,
        name="Escalation & Payoff",
        description="Progressively raises stakes, technical complexity, or tension until a dramatic breakthrough or synthesis.",
        suitable_profiles={NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.PROMISE,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.ESCALATION,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=True,
        topic_keywords=["crisis", "danger", "critical", "catastrophe", "breakthrough", "stakes", "limit"],
        intent_signals=["escalating tension", "high stakes", "dramatic resolution"],
        min_sections=5,
        max_sections=14,
    ),
    NarrativeStrategy.CASE_STUDY: NarrativeStrategyDefinition(
        strategy=NarrativeStrategy.CASE_STUDY,
        name="Empirical Case Study",
        description="Examines a concrete real-world incident, deployment, or experiment to extract universal engineering principles.",
        suitable_profiles={NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG},
        preferred_roles=[
            NarrativeSectionRole.HOOK,
            NarrativeSectionRole.CONTEXT,
            NarrativeSectionRole.DEVELOPMENT,
            NarrativeSectionRole.PAYOFF,
            NarrativeSectionRole.TAKEAWAY,
            NarrativeSectionRole.CLOSING,
        ],
        has_open_loop=False,
        topic_keywords=["case study", "outage", "incident", "production", "postmortem", "empirical", "real world"],
        intent_signals=["case study", "postmortem breakdown", "practical lessons"],
        min_sections=4,
        max_sections=12,
    ),
}


class StrategySelectionScore(BaseModel):
    """Scored evaluation of a NarrativeStrategy for a given topic and context."""

    strategy: NarrativeStrategy
    score: float = Field(ge=0.0, le=100.0)
    is_eligible: bool = True
    match_reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)


class NarrativePlanDraft(BaseModel):
    """Unpersisted candidate narrative plan produced by the Narrative Director."""

    strategy: NarrativeStrategy
    format_profile: NarrativeFormatProfile
    target_duration_seconds: int
    estimated_duration_seconds: int
    sections: list[NarrativeSection]
    rationale: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class NarrativeStrategySelector:
    """Intelligent, multi-layered selection engine for NarrativeStrategy."""

    @classmethod
    def evaluate_strategies(
        cls,
        topic_title: str,
        topic_summary: str | None,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        content_intent: dict[str, Any],
        format_profile: NarrativeFormatProfile,
    ) -> list[StrategySelectionScore]:
        """Evaluate and rank all strategies based on research evidence, format bounds, and editorial intent."""
        scores: list[StrategySelectionScore] = []
        text_corpus = f"{topic_title} {topic_summary or ''} {content_intent.get('primary_goal', '')} {content_intent.get('central_question', '')}".lower()

        # Check research shape
        contradictions = brief_dict.get("contradictions", []) or []
        verified_claims = brief_dict.get("verified_claims", []) or []
        has_contradictions = len(contradictions) > 0

        # Check ChannelDNA narrative preference
        dna_prefs = dna_dict.get("narrative_preferences", {})
        preferred_strategy_name = dna_prefs.get("default_strategy")

        for strategy, defn in STRATEGY_CATALOG.items():
            match_reasons: list[str] = []
            rejection_reasons: list[str] = []
            score = 10.0  # Baseline

            # 1. Format profile eligibility
            if format_profile not in defn.suitable_profiles:
                rejection_reasons.append(
                    f"Strategy '{strategy.value}' is not suitable for format profile '{format_profile.value}'"
                )
                scores.append(
                    StrategySelectionScore(
                        strategy=strategy,
                        score=0.0,
                        is_eligible=False,
                        match_reasons=[],
                        rejection_reasons=rejection_reasons,
                    )
                )
                continue

            # 2. Topic keyword matching
            matched_keywords = [kw for kw in defn.topic_keywords if re.search(r"\b" + re.escape(kw) + r"\b", text_corpus)]
            if matched_keywords:
                boost = min(len(matched_keywords) * 12.0, 36.0)
                score += boost
                match_reasons.append(f"Matched topic signals: {', '.join(matched_keywords)} (+{boost:.0f})")

            # 3. Content intent matching
            intent_text = f"{content_intent.get('primary_goal', '')} {content_intent.get('audience_intent', '')}".lower()
            matched_intents = [sig for sig in defn.intent_signals if sig in intent_text]
            if matched_intents:
                score += 15.0
                match_reasons.append(f"Matched intent signals: {', '.join(matched_intents)} (+15)")

            # 4. Research shape alignment
            if strategy == NarrativeStrategy.MYTH_REALITY and has_contradictions:
                score += 30.0
                match_reasons.append(f"Research brief contains {len(contradictions)} contradiction(s) (+30)")
            elif strategy == NarrativeStrategy.CONTRAST_COMPARISON and has_contradictions:
                score += 20.0
                match_reasons.append(f"Research brief contains {len(contradictions)} contradiction(s) (+20)")

            if strategy == NarrativeStrategy.HOW_IT_WORKS and any("how" in c.lower() for c in [topic_title, content_intent.get("central_question", "")]):
                score += 20.0
                match_reasons.append("Central question or title asks how system operates (+20)")

            if strategy == NarrativeStrategy.QUESTION_ANSWER and "?" in topic_title:
                score += 20.0
                match_reasons.append("Topic title is an explicit question (+20)")

            if strategy == NarrativeStrategy.CHRONOLOGICAL and any(yr in text_corpus for yr in ["history", "19", "20", "origin", "evolution"]):
                score += 20.0
                match_reasons.append("Historical progression detected in context (+20)")

            # 5. ChannelDNA preference alignment (influences but does not override research truth)
            if preferred_strategy_name and preferred_strategy_name.upper() == strategy.value:
                score += 10.0
                match_reasons.append("Pinned ChannelDNA revision specifies this strategy (+10)")

            final_score = min(max(score, 1.0), 100.0)
            scores.append(
                StrategySelectionScore(
                    strategy=strategy,
                    score=final_score,
                    is_eligible=True,
                    match_reasons=match_reasons,
                    rejection_reasons=[],
                )
            )

        # Sort descending by score
        return sorted(scores, key=lambda s: (s.is_eligible, s.score), reverse=True)

    @classmethod
    def select_best_strategy(
        cls,
        topic_title: str,
        topic_summary: str | None,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        content_intent: dict[str, Any],
        format_profile: NarrativeFormatProfile,
    ) -> NarrativeStrategy:
        """Select top-ranked eligible strategy with deterministic fallback."""
        ranked = cls.evaluate_strategies(
            topic_title=topic_title,
            topic_summary=topic_summary,
            brief_dict=brief_dict,
            dna_dict=dna_dict,
            content_intent=content_intent,
            format_profile=format_profile,
        )
        eligible = [s for s in ranked if s.is_eligible]
        if eligible:
            return eligible[0].strategy

        # Fallback by format profile
        if format_profile == NarrativeFormatProfile.SHORT:
            return NarrativeStrategy.HOW_IT_WORKS
        return NarrativeStrategy.PROBLEM_SOLUTION
