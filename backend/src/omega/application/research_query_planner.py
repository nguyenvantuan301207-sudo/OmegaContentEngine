"""Deterministic Topic-Grounded Research Query Planner (P0.3).

Generates intent-grounded, inspectable research queries without hard-coding
topic-specific answers or fabricating unverified entities.
"""

from __future__ import annotations

import re

from omega.domain.numeric_promise import NumericPromiseContract
from omega.domain.research import ResearchQuery, ResearchQueryIntent

_RHETORICAL_PREFIXES: list[str] = [
    "why ",
    "how ",
    "what is ",
    "what are ",
    "the truth about ",
    "a guide to ",
    "guide to ",
    "understanding ",
    "mastering ",
    "everything you need to know about ",
]

_PACKAGING_SUFFIXES: list[str] = [
    "every civil engineer should understand",
    "every engineer should understand",
    "every engineer should know",
    "what you need to know",
    "explained",
    "a comprehensive guide",
    "best practices",
    "and why it matters",
    "and how to prevent it",
    "in 2026",
    "for beginners",
]


def extract_core_subject(topic_title: str) -> str:
    """Extract clean domain subject from topic title by removing rhetorical packaging."""
    cleaned = topic_title.strip()

    # If title has a colon or em-dash, examine parts
    parts = re.split(r"[:\—\–\-]", cleaned)
    primary = parts[0].strip()

    # If primary has leading rhetorical words, remove them
    primary_lower = primary.lower()
    for prefix in _RHETORICAL_PREFIXES:
        if primary_lower.startswith(prefix):
            primary = primary[len(prefix):].strip()
            break

    # If the second part has specific topic content and first was short, check both
    if len(parts) > 1 and len(primary.split()) <= 2:
        # Check if secondary has substantive subject words
        secondary = parts[1].strip()
        sec_lower = secondary.lower()
        for suffix in _PACKAGING_SUFFIXES:
            if suffix in sec_lower:
                sec_lower = sec_lower.replace(suffix, "").strip()
        # Remove numbers and enumerable nouns from secondary
        sec_clean = re.sub(r"\b\d+\s+[a-zA-Z]+\b", "", sec_lower).strip()
        if sec_clean and len(sec_clean.split()) >= 2:
            return f"{primary} {sec_clean}".strip().lower()

    # Remove any trailing packaging from primary
    prim_lower = primary.lower()
    for suffix in _PACKAGING_SUFFIXES:
        if suffix in prim_lower:
            prim_lower = prim_lower.replace(suffix, "").strip()

    # Strip any numeric patterns e.g. "5 Mechanisms"
    prim_clean = re.sub(r"\b\d+\s+[a-zA-Z]+\b", "", prim_lower).strip()
    prim_clean = re.sub(r"\s+", " ", prim_clean)

    return prim_clean or primary.lower()


def _get_plural_entity(entity_type: str) -> str:
    """Normalize entity type to its plural form for search queries."""
    ent = entity_type.strip().lower()
    if ent.endswith("s"):
        return ent
    if ent.endswith("y"):
        return ent[:-1] + "ies"
    return ent + "s"


def plan_research_queries(
    topic_title: str,
    contract: NumericPromiseContract | None = None,
    round_number: int = 1,
    already_supported_families: list[str] | None = None,
    max_queries: int = 3,
) -> list[ResearchQuery]:
    """Deterministically plan diversified, inspectable research queries.

    Invariants:
    1. Grounded in canonical topic subject.
    2. Zero hard-coded mechanism or answer names.
    3. Diversified across distinct intent families.
    4. Deduplicated and bounded by max_queries.
    """
    subject = extract_core_subject(topic_title)

    queries: list[ResearchQuery] = []
    seen_texts: set[str] = set()

    def add_query(text: str, intent: ResearchQueryIntent, reason: str) -> None:
        norm_text = re.sub(r"\s+", " ", text.strip().lower())
        if norm_text and norm_text not in seen_texts and len(queries) < max_queries:
            seen_texts.add(norm_text)
            queries.append(
                ResearchQuery(
                    query_text=norm_text,
                    intent=intent,
                    reason=reason,
                    round_number=round_number,
                )
            )

    if contract and contract.promised_count and contract.promised_count > 0:
        plural_entity = _get_plural_entity(contract.entity_type)
        singular_entity = contract.entity_type.lower()

        if round_number == 1:
            # Round 1: Foundation, technical classification, industry standards
            add_query(
                f"{subject} {plural_entity} overview",
                ResearchQueryIntent.OVERVIEW,
                f"Establish authoritative overview of {plural_entity} for {subject}.",
            )
            add_query(
                f"primary {plural_entity} of {subject} engineering guide",
                ResearchQueryIntent.MECHANISMS
                if singular_entity == "mechanism"
                else ResearchQueryIntent.TYPES,
                f"Target core technical {plural_entity} and classifications.",
            )
            add_query(
                f"types of {plural_entity} in {subject} technical standards",
                ResearchQueryIntent.TECHNICAL_REFERENCE,
                "Identify standardized classifications and technical specifications.",
            )
        else:
            # Round 2+: Diversification, additional mechanisms, environmental/material causes
            add_query(
                f"additional {plural_entity} and failure modes in {subject}",
                ResearchQueryIntent.ADDITIONAL_COVERAGE,
                f"Expand coverage to discover distinct unrepresented {plural_entity} beyond initial findings.",
            )
            add_query(
                f"environmental and material {plural_entity} of {subject}",
                ResearchQueryIntent.CAUSES,
                f"Investigate environmental, chemical, and physical {plural_entity}.",
            )
            add_query(
                f"complete taxonomy and classification of {plural_entity} in {subject}",
                ResearchQueryIntent.TYPES,
                "Seek comprehensive multi-category taxonomy for complete enumeration.",
            )
    else:
        # Non-numeric topic
        if round_number == 1:
            add_query(
                f"{subject} overview and fundamentals",
                ResearchQueryIntent.OVERVIEW,
                f"Establish core foundational principles for {subject}.",
            )
            add_query(
                f"{subject} technical explanation and engineering principles",
                ResearchQueryIntent.TECHNICAL_REFERENCE,
                f"Target technical and engineering mechanisms for {subject}.",
            )
            add_query(
                f"{subject} analysis and practical applications",
                ResearchQueryIntent.TYPES,
                f"Explore practical applications and domain taxonomy for {subject}.",
            )
        else:
            add_query(
                f"{subject} advanced technical guide and failure analysis",
                ResearchQueryIntent.ADDITIONAL_COVERAGE,
                f"Expand into advanced and specialized aspects of {subject}.",
            )
            add_query(
                f"{subject} industry standards and best practices",
                ResearchQueryIntent.TECHNICAL_REFERENCE,
                "Investigate industry guidelines and empirical references.",
            )

    return queries
