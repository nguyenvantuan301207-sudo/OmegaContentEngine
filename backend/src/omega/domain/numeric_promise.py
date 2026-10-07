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
    "below", "from", "up", "down", "out", "off", "over", "under", "again", "further",
    "once", "here", "there", "when", "where", "why", "how", "all", "any", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "can", "will", "just", "should", "now", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "having", "do", "does", "did", "doing", "causes", "caused",
    "causing", "cause", "result", "results", "resulting", "lead", "leads", "leading", "due", "because",
    "develops", "develop", "developing", "occurs", "occur", "occurring", "shows", "show", "showing",
    "evidence", "cracks", "crack", "cracking", "concrete", "civil", "engineer", "engineers",
    "understand", "understands", "understanding", "mechanism", "mechanisms", "structural", "structure",
    "structures", "material", "materials", "construction", "primary", "secondary", "factor", "factors",
    "what", "them", "they", "their", "these", "those", "this", "that", "which", "who", "whom", "whose",
    "while", "determining", "determine", "determined", "somewhat", "need", "needs", "needed", "watch",
    "watching", "though", "would", "could", "might", "must", "shall", "ought", "placed",
    "place", "placing", "places", "across", "along", "wherever", "whether", "whereas",
    "corner", "corners", "entrant", "penetration", "penetrations",
    "joint", "joints", "difficult", "difficulty", "difficulties", "severity",
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


def derive_verified_family_label(
    text: str,
    topic_stopwords: set[str] | None = None,
    entity_type: str = "mechanism",
) -> tuple[set[str], str]:
    """Deterministically derive distinguishing tokens and verified human-readable concept label.

    Invariants:
    - Input is exclusively verified canonical propositions.
    - Grounded 100% in exact words present in the claim text.
    - Human-readable phrases (e.g. 'drying shrinkage', 'applied forces', 'freeze-thaw expansion').
    - Never emits arbitrary raw stems or discourse packaging (e.g. 'what_them', 'appli_forc').
    - One proposition contributes at most one family.
    - Returns (tokens_set, canon_label).
    """
    t_lower = text.lower()
    t_clean = re.sub(r"https?://\S+", "", t_lower)
    t_clean = re.sub(r"^.*?\?\*?\*?\s*", "", t_clean)
    t_clean = re.sub(
        r"^(?:what causes them|causes?|mechanisms?|primary causes?|drivers?|summary|overview|what we need to watch out for|while determining.*?)\s*:\s*",
        "",
        t_clean,
        flags=re.IGNORECASE,
    )
    t_clean = re.sub(r"^\d+\.\s*", "", t_clean)

    words = re.findall(r"\b[a-zA-Z]{3,}\b", t_clean)
    ignore = set(STOP_WORDS)
    if topic_stopwords:
        ignore.update(topic_stopwords)

    sig_stems = []
    for w in words:
        s = _stem(w)
        if s not in ignore and w not in ignore and len(s) >= 3:
            sig_stems.append(s)

    tokens_set = set(sig_stems)

    canon_label = ""
    # Check for prominent physical / chemical mechanism concepts grounded in exact claim vocabulary
    if ("plastic" in tokens_set or "plastic" in t_clean) and ("shrink" in tokens_set or "shrinkage" in t_clean):
        canon_label = "plastic shrinkage"
    elif ("dry" in tokens_set or "drying" in t_clean) and ("shrink" in tokens_set or "shrinkage" in t_clean):
        canon_label = "drying shrinkage"
    elif ("differenti" in tokens_set or "differential" in t_clean) and ("shrink" in tokens_set or "shrinkage" in t_clean):
        canon_label = "differential shrinkage"
    elif "appli" in tokens_set or "applied" in t_clean:
        if "load" in tokens_set or "loading" in t_clean:
            canon_label = "applied loading"
        elif "forc" in tokens_set or "forces" in t_clean:
            canon_label = "applied forces"
        else:
            canon_label = "applied loading"
    elif ("freez" in tokens_set or "freeze" in t_clean) and ("thaw" in tokens_set or "thaw" in t_clean):
        if "cycl" in tokens_set or "cycling" in t_clean:
            canon_label = "freeze-thaw cycling"
        elif "expans" in tokens_set or "expansion" in t_clean:
            canon_label = "freeze-thaw expansion"
        else:
            canon_label = "freeze-thaw"
    elif "thermal" in tokens_set or "thermal" in t_clean:
        if "contract" in tokens_set or "contraction" in t_clean:
            canon_label = "thermal contraction"
        elif "expans" in tokens_set or "expansion" in t_clean:
            canon_label = "thermal expansion"
        elif "cycl" in tokens_set or "cycling" in t_clean:
            canon_label = "thermal cycling"
        else:
            canon_label = "thermal stress"
    elif ("load" in tokens_set or "overload" in t_clean) and ("overload" in tokens_set or "tensil" in tokens_set):
        canon_label = "tensile overload"
    elif ("tensil" in tokens_set or "tensile" in t_clean) and ("capac" in tokens_set or "capacity" in t_clean):
        canon_label = "tensile capacity"
    elif ("tensil" in tokens_set or "tensile" in t_clean) and ("stress" in tokens_set or "stresses" in t_clean):
        canon_label = "tensile stress"
    elif ("subgrad" in tokens_set or "subgrade" in t_clean) or ("settlement" in t_clean and ("soil" in t_clean or "foundat" in tokens_set)):
        canon_label = "subgrade settlement"
    elif "alkali" in t_clean or "silica" in t_clean:
        canon_label = "alkali-silica reaction"
    elif ("chemic" in tokens_set or "chemical" in t_clean) and ("attack" in tokens_set or "attack" in t_clean):
        canon_label = "chemical attack"
    elif "corrosion" in t_clean or "rust" in t_clean:
        canon_label = "rebar corrosion"
    elif "shrink" in tokens_set or "shrinkage" in t_clean:
        canon_label = "shrinkage"

    if not canon_label:
        prefix_m = re.match(r"^([a-zA-Z\s\-]{3,30})\s*:\s*", t_clean)
        if prefix_m:
            cand_p = prefix_m.group(1).strip().lower()
            if not any(sw in cand_p for sw in ("note", "overview", "causes", "what", "summary", "chapter", "table")):
                canon_label = cand_p

    if not canon_label:
        non_stop_words = [w for w in words if _stem(w) not in ignore and w not in ignore and len(w) >= 3]
        if len(non_stop_words) >= 2:
            canon_label = f"{non_stop_words[0]} {non_stop_words[1]}"
        elif non_stop_words:
            canon_label = non_stop_words[0]
        else:
            canon_label = "unknown"

    return tokens_set, canon_label


def get_claim_signature(text: str, topic_stopwords: set[str] | None = None) -> tuple[set[str], str]:
    """Extract distinguishing concept tokens and canonical key for a claim."""
    return derive_verified_family_label(text, topic_stopwords)


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

        c_tokens, c_label = derive_verified_family_label(text, topic_tokens, entity_type=entity_type)
        if not c_tokens and c_label == "unknown":
            continue

        matched_cluster = None
        for cl in clusters:
            if cl["name"] == c_label:
                matched_cluster = cl
                break
            overlap = cl["tokens"].intersection(c_tokens)
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
                "name": c_label,
                "tokens": set(c_tokens),
            })

    return [cl["name"] for cl in clusters]
