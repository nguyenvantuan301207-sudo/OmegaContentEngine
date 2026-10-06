"""Numeric Promise Contract domain models, extraction, and distinct entity clustering.

Provides deterministic parsing and validation for titles with explicit enumeration promises
(e.g., "5 Mechanisms", "7 Causes", "3 Mistakes", "10 Steps") to ensure upstream research
and downstream content generation cannot pass when authority is incomplete.
"""

from __future__ import annotations

import re
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

ENUMERABLE_NOUNS: dict[str, str] = {
    "mechanism": "mechanism",
    "mechanisms": "mechanism",
    "cause": "cause",
    "causes": "cause",
    "reason": "reason",
    "reasons": "reason",
    "mistake": "mistake",
    "mistakes": "mistake",
    "step": "step",
    "steps": "step",
    "method": "method",
    "methods": "method",
    "principle": "principle",
    "principles": "principle",
    "factor": "factor",
    "factors": "factor",
    "way": "way",
    "ways": "way",
    "rule": "rule",
    "rules": "rule",
    "tip": "tip",
    "tips": "tip",
    "lesson": "lesson",
    "lessons": "lesson",
    "law": "law",
    "laws": "law",
    "sign": "sign",
    "signs": "sign",
    "strategy": "strategy",
    "strategies": "strategy",
    "pillar": "pillar",
    "pillars": "pillar",
    "technique": "technique",
    "techniques": "technique",
    "component": "component",
    "components": "component",
    "practice": "practice",
    "practices": "practice",
    "error": "error",
    "errors": "error",
    "habit": "habit",
    "habits": "habit",
}

NON_ENUMERABLE_UNITS: set[str] = {
    "day",
    "days",
    "hour",
    "hours",
    "minute",
    "minutes",
    "second",
    "seconds",
    "week",
    "weeks",
    "month",
    "months",
    "year",
    "years",
    "mpa",
    "gpa",
    "kpa",
    "psi",
    "ksi",
    "bar",
    "hz",
    "khz",
    "mhz",
    "ghz",
    "kg",
    "g",
    "mg",
    "ton",
    "tons",
    "tonne",
    "tonnes",
    "lb",
    "lbs",
    "m",
    "cm",
    "mm",
    "km",
    "ft",
    "in",
    "meter",
    "meters",
    "metre",
    "metres",
    "percent",
    "pct",
    "requirements",
    "outlook",
    "architecture",
    "standard",
    "standards",
    "edition",
    "release",
    "build",
    "update",
    "part",
    "volume",
}

EXCLUDED_PREFIXES: set[str] = {
    "version",
    "v",
    "iso",
    "astm",
    "aci",
    "ieee",
    "bs",
    "en",
    "din",
    "at",
    "in",
    "for",
}

NUMERIC_PROMISE_PATTERN = re.compile(
    r"(?:\b)"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
    r"\s+"
    r"(?:(?:key|critical|common|essential|major|core|fundamental|proven|simple|biggest|vital|top|crucial|classic|important|main|deadliest)\s+)?"
    r"([a-zA-Z]+)"
    r"(?:\b)",
    re.IGNORECASE,
)

STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "then", "of", "to", "in", "on", "at", "by", "for",
    "with", "about", "against", "between", "into", "through", "during", "before", "after", "above",
    "below", "from", "up", "down", "in", "out", "on", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how", "all", "any", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "can", "will", "just", "should", "now", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "having", "do", "does", "did", "doing", "causes", "caused",
    "causing", "cause", "result", "results", "resulting", "lead", "leads", "leading", "due", "because",
    "develops", "develop", "developing", "occurs", "occur", "occurring", "shows", "show", "showing",
    "evidence", "cracks", "crack", "cracking", "concrete", "civil", "engineer", "engineers",
    "understand", "understands", "understanding", "mechanism", "mechanisms", "structural", "structure",
    "structures", "material", "materials", "construction", "primary", "secondary", "factor", "factors",
}


class NumericPromiseContract(BaseModel):
    """Deterministic contract representing an enumerated content promise in a topic title."""

    promised_count: int = Field(ge=1, description="Number of distinct items promised")
    entity_type: str = Field(min_length=1, description="Normalized singular entity noun")
    source_text: str = Field(description="Exact substring from title representing the promise")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    contract_kind: str = Field(default="ENUMERATION")

    model_config = ConfigDict(extra="ignore")

    def to_dict(self) -> dict[str, Any]:
        return {
            "promised_count": self.promised_count,
            "entity_type": self.entity_type,
            "source_text": self.source_text,
            "confidence": self.confidence,
            "contract_kind": self.contract_kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NumericPromiseContract:
        return cls(
            promised_count=data["promised_count"],
            entity_type=data["entity_type"],
            source_text=data.get("source_text", ""),
            confidence=float(data.get("confidence", 1.0)),
            contract_kind=data.get("contract_kind", "ENUMERATION"),
        )


def normalize_entity_type(noun: str) -> str:
    """Normalize plural/singular noun to canonical entity type."""
    cleaned = noun.lower().strip()
    if cleaned in ENUMERABLE_NOUNS:
        return ENUMERABLE_NOUNS[cleaned]
    if cleaned.endswith("s") and len(cleaned) > 3:
        singular = cleaned[:-1]
        if singular in ENUMERABLE_NOUNS:
            return ENUMERABLE_NOUNS[singular]
        return singular
    return cleaned


def extract_numeric_promise(title: str | None) -> NumericPromiseContract | None:
    """Extract an explicit enumeration promise from a title, rejecting false positives."""
    if not title or not title.strip():
        return None

    title_clean = title.strip()

    for match in NUMERIC_PROMISE_PATTERN.finditer(title_clean):
        raw_count = match.group(1).lower()
        raw_noun = match.group(2).lower()

        # Check prefix word preceding the match
        start_pos = match.start()
        prefix_slice = title_clean[:start_pos].strip()
        last_prefix_word = prefix_slice.split()[-1].lower() if prefix_slice.split() else ""
        last_prefix_word = re.sub(r"[^a-z]", "", last_prefix_word)

        # False positive check 1: Preceded by version, ISO, "at", etc.
        if last_prefix_word in EXCLUDED_PREFIXES:
            continue

        # Parse count
        if raw_count.isdigit():
            count = int(raw_count)
            # False positive check 2: Calendar years (1900-2099)
            if 1900 <= count <= 2099:
                continue
            # False positive check 3: Bounded plausible enumeration range (2 to 100)
            if count < 2 or count > 100:
                continue
        elif raw_count in NUMBER_WORDS:
            count = NUMBER_WORDS[raw_count]
        else:
            continue

        # False positive check 4: Non-enumerable units of measure / specifications
        if raw_noun in NON_ENUMERABLE_UNITS:
            continue

        # Entity normalization
        if raw_noun in ENUMERABLE_NOUNS:
            norm_entity = ENUMERABLE_NOUNS[raw_noun]
            return NumericPromiseContract(
                promised_count=count,
                entity_type=norm_entity,
                source_text=match.group(0),
                confidence=1.0,
                contract_kind="ENUMERATION",
            )

    return None


def _stem(w: str) -> str:
    w = re.sub(r"[^a-z0-9]", "", w.lower())
    for suff in ("ing", "tion", "tions", "ment", "ments", "ness", "es", "ed", "s"):
        if w.endswith(suff) and len(w) - len(suff) >= 3:
            return w[:-len(suff)]
    return w


def get_claim_signature(text: str, topic_stopwords: set[str] | None = None) -> tuple[set[str], str]:
    """Extract distinguishing concept tokens and canonical key for a claim."""
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    ignore = set(STOP_WORDS)
    if topic_stopwords:
        ignore.update(topic_stopwords)

    sig_stems = []
    for w in words:
        s = _stem(w)
        if s not in ignore and w not in ignore and len(s) >= 3:
            sig_stems.append(s)

    bigrams = []
    for i in range(len(sig_stems) - 1):
        bigrams.append(f"{sig_stems[i]}_{sig_stems[i+1]}")

    tokens_set = set(sig_stems)
    canon_key = bigrams[0] if bigrams else ("_".join(sorted(sig_stems)[:2]) if sig_stems else "unknown")
    return tokens_set, canon_key


def extract_distinct_entities(
    claims: list[dict[str, Any] | str],
    topic_title: str | None = None,
    entity_type: str = "mechanism",
) -> list[str]:
    """Group claims into distinct semantic entity families, collapsing rephrased duplicates."""
    topic_tokens = set()
    if topic_title:
        for w in re.findall(r"\b[a-zA-Z]{3,}\b", topic_title.lower()):
            topic_tokens.add(_stem(w))

    clusters: list[dict[str, Any]] = []

    for c in claims:
        text = c if isinstance(c, str) else (c.get("claim_text") or c.get("text") or "")
        if not text.strip():
            continue

        c_tokens, c_key = get_claim_signature(text, topic_tokens)
        if not c_tokens:
            continue

        matched_cluster = None
        for cl in clusters:
            overlap = cl["tokens"].intersection(c_tokens)
            if cl["name"] == c_key:
                matched_cluster = cl
                break
            if len(overlap) >= 2 or (len(overlap) >= 1 and len(cl["tokens"]) == 1 and len(c_tokens) == 1):
                matched_cluster = cl
                break
            union = cl["tokens"].union(c_tokens)
            if union and (len(overlap) / len(union)) >= 0.4:
                matched_cluster = cl
                break

        if matched_cluster:
            matched_cluster["tokens"].update(c_tokens)
        else:
            clusters.append({
                "name": c_key,
                "tokens": set(c_tokens),
            })

    return [cl["name"] for cl in clusters]
