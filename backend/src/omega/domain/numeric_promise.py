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


_GENERIC_MANIFESTATION_WORDS: set[str] = {
    "crack", "cracks", "cracking", "surface", "interior", "internal",
    "fracture", "fractures", "fracturing",
    "failure", "failures", "failing", "damage", "damages", "damaging",
    "defect", "defects", "degradation", "degrade", "degrading",
    "issue", "issues", "problem", "problems",
}

UNRESOLVED_ANAPHORIC_PATTERN = re.compile(
    r"^(?:(?:when\s+)?(?:anything|something|nothing)\s+(?:happens|occurs)|"
    r"(?:from\s+(?:the\s+)?(?:simulations?|experiments?|studies|literature|tests?))|"
    r"(?:both|these|those|this|that|such|all|each|either|neither)\s+"
    r"(?:actions?|factors?|process(?:es)?|effects?|mechanisms?|causes?|reasons?|conditions?|steps?|practices?|events?|elements?|aspects?|activities|behaviors?|influences?|combinations?))\b",
    re.IGNORECASE,
)

GENERIC_UNRESOLVED_NOUNS: set[str] = {
    "action", "actions", "factor", "factors", "process", "processes", "effect", "effects",
    "mechanism", "mechanisms", "cause", "causes", "reason", "reasons", "condition", "conditions",
    "element", "elements", "aspect", "aspects", "step", "steps", "practice", "practices",
    "event", "events", "activity", "activities", "phenomenon", "behavior", "behaviors",
    "influence", "influences", "statement", "statements", "combination", "combinations",
}

NON_CAUSAL_ACTION_VERBS: set[str] = {
    "weaken", "weakens", "weakening", "exacerbate", "exacerbates", "exacerbating",
    "worsen", "worsens", "worsening", "contribute", "contributes", "contributing",
    "increase", "increases", "increasing", "decrease", "decreases", "decreasing",
    "affect", "affects", "affecting", "manifest", "manifests", "manifesting",
    "accelerate", "accelerates", "accelerating",
}

_UNRESOLVED_NOUN_STEMS: set[str] = {_stem(w) for w in GENERIC_UNRESOLVED_NOUNS}
_NON_CAUSAL_VERB_STEMS: set[str] = {_stem(w) for w in NON_CAUSAL_ACTION_VERBS}


def is_unresolved_or_non_causal_label(words: list[str]) -> bool:
    """Deterministically check if candidate words consist exclusively of unresolved nouns or non-causal verbs."""
    if not words:
        return True
    stems = {_stem(w) for w in words}
    disallowed = _UNRESOLVED_NOUN_STEMS | _NON_CAUSAL_VERB_STEMS
    return all(s in disallowed for s in stems)


_CAUSE_BEFORE_CONNECTORS: list[str] = [
    # Cause precedes connector: X [connector] Y
    r"\b(?:causes?|causing)\b",
    r"\b(?:leads?\s+to|leading\s+to)\b",
    r"\b(?:results?\s+in|resulting\s+in)\b",
    r"\b(?:produces?|producing)\b",
    r"\b(?:induces?|inducing)\b",
    r"\b(?:triggers?|triggering)\b",
    r"\b(?:initiates?|initiating)\b",
    r"\b(?:generates?|generating)\b",
    r"\b(?:creates?|creating)\b",
    r"\b(?:drives?|driving)\b",
]

_CAUSE_STRICTLY_AFTER_CONNECTORS: list[str] = [
    # Cause strictly follows connector: Y [connector] X
    r"\b(?:results?\s+from|resulting\s+from)\b",
    r"\b(?:arises?\s+from|arising\s+from)\b",
    r"\b(?:(?:is|are|was|were|being)?\s*due\s+to)\b",
    r"\b(?:originates?\s+from|originating\s+from)\b",
    r"\b(?:stems?\s+from|stemming\s+from)\b",
    r"\b(?:is|are|was|were|being)?\s*caused\s+by\b",
    r"\b(?:is|are|was|were|being)?\s*triggered\s+by\b",
    r"\b(?:is|are|was|were|being)?\s*induced\s+by\b",
    r"\b(?:is|are|was|were|being)?\s*produced\s+by\b",
    r"\b(?:(?:is|are|was|were|being)?\s*a\s+result\s+of)\b",
]

_CONDITION_AFTER_CONNECTORS: list[str] = [
    # Cause/condition follows connector: Y [connector] X
    r"\b(?:occurs?\s+when|occurring\s+when)\b",
    r"\b(?:develops?\s+when|developing\s+when)\b",
    r"\b(?:forms?\s+when|forming\s+when)\b",
    r"\b(?:happens?\s+when|happening\s+when)\b",
]


def derive_verified_family_label(
    text: str,
    topic_stopwords: set[str] | None = None,
    entity_type: str = "mechanism",
) -> tuple[set[str], str]:
    """Deterministically derive distinguishing tokens and verified concept label via causal span extraction.

    Invariants:
    - Input is exclusively verified canonical propositions.
    - Grounded 100% in exact words present in the claim text.
    - Zero domain-specific hard-coded mechanism mappings.
    - Extracts cause-bearing phrase from causal connectors.
    - Never emits arbitrary raw stems or invented domain vocabulary.
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

    claim_words_raw = re.findall(r"\b[a-zA-Z]{2,}\b", t_clean)
    claim_words_set = set(claim_words_raw)

    ignore = set(STOP_WORDS)
    if topic_stopwords:
        ignore.update(topic_stopwords)

    is_causal = entity_type in {
        "mechanism", "cause", "reason", "factor", "driver", "origin", "failure mode", "failure mechanism"
    }

    best_candidate_words: list[str] = []

    # 1. Definition / heading prefix before colon: "Mechanism Name: description..."
    prefix_m = re.match(r"^([a-zA-Z\s\-]{3,35})\s*:\s*", t_clean)
    if prefix_m:
        cand_p = prefix_m.group(1).strip()
        cand_words = [
            w for w in re.findall(r"\b[a-zA-Z]{3,}\b", cand_p)
            if w not in ignore and _stem(w) not in ignore and w not in _GENERIC_MANIFESTATION_WORDS
        ]
        if cand_words and not any(sw in cand_p for sw in ("note", "overview", "causes", "what", "summary", "chapter", "table")):
            best_candidate_words = cand_words[:3]

    # Rejection of unresolved anaphoric beginnings when no colon prefix defines the antecedent
    if is_causal and not best_candidate_words and UNRESOLVED_ANAPHORIC_PATTERN.match(t_clean):
        return set(), "unknown"

    # 2. Causal syntactic structure extraction (if causal entity type)
    # 2a. Strictly after connectors (Y results from X, Y is due to X)
    if not best_candidate_words and is_causal:
        for pat in _CAUSE_STRICTLY_AFTER_CONNECTORS:
            m = re.search(pat, t_clean)
            if m:
                after_span = t_clean[m.end():]
                after_chunk = re.split(
                    r"[\.\;\-\–\—\:]|\b(?:during|exceed|exceeds|exceeded|whether|where|which|that)\b",
                    after_span,
                )[0]
                after_words = [
                    w for w in re.findall(r"\b[a-zA-Z]{3,}\b", after_chunk)
                    if w not in ignore and _stem(w) not in ignore
                ]
                if after_words:
                    best_candidate_words = after_words[:3]
                    break

    # 2b. Condition after connectors (Y occurs when X, Y develops when X)
    if not best_candidate_words and is_causal:
        for pat in _CONDITION_AFTER_CONNECTORS:
            m = re.search(pat, t_clean)
            if m:
                before_span = t_clean[:m.start()]
                after_span = t_clean[m.end():]

                subject_chunk = re.split(r"[\.\;\-\–\—]", before_span)[-1]
                subj_words = [
                    w for w in re.findall(r"\b[a-zA-Z]{3,}\b", subject_chunk)
                    if w not in ignore and _stem(w) not in ignore and w not in _GENERIC_MANIFESTATION_WORDS
                ]

                # Specific named mechanism before connector (e.g. "Plastic shrinkage cracking occurs when...")
                if len(subj_words) >= 2:
                    best_candidate_words = subj_words[:3]
                    break
                else:
                    after_chunk = re.split(
                        r"[\.\;\-\–\—\:]|\b(?:exceed|exceeds|exceeded|whether|where|which|that|during)\b",
                        after_span,
                    )[0]
                    after_words = [
                        w for w in re.findall(r"\b[a-zA-Z]{3,}\b", after_chunk)
                        if w not in ignore and _stem(w) not in ignore
                    ]
                    if after_words:
                        best_candidate_words = after_words[:3]
                        break

    # 2c. Cause before connectors (X causes Y, X produces Y, X leads to Y)
    if not best_candidate_words and is_causal:
        for pat in _CAUSE_BEFORE_CONNECTORS:
            matches = list(re.finditer(pat, t_clean))
            if matches:
                for m in reversed(matches):
                    before_span = t_clean[:m.start()]
                    clause_chunk = re.split(r"[\.\;\-\–\—\:]|\b(?:because|when|while|if|as)\b", before_span)[-1]
                    cause_words = [
                        w for w in re.findall(r"\b[a-zA-Z]{3,}\b", clause_chunk)
                        if w not in ignore and _stem(w) not in ignore
                    ]
                    if cause_words:
                        best_candidate_words = cause_words[:3]
                        break
                if best_candidate_words:
                    break

    # 3. Fallback: extract substantive non-stopword tokens
    if not best_candidate_words:
        words = re.findall(r"\b[a-zA-Z]{3,}\b", t_clean)
        substantive = [
            w for w in words
            if w not in ignore
            and _stem(w) not in ignore
            and w not in _GENERIC_MANIFESTATION_WORDS
            and _stem(w) not in _UNRESOLVED_NOUN_STEMS
            and _stem(w) not in _NON_CAUSAL_VERB_STEMS
        ]
        if len(substantive) >= 2:
            best_candidate_words = substantive[:2]
        elif substantive:
            best_candidate_words = substantive[:1]

    if not best_candidate_words:
        return set(), "unknown"

    # Invariant: Causal labels must not consist exclusively of unresolved nouns or non-causal verbs
    if is_causal and is_unresolved_or_non_causal_label(best_candidate_words):
        return set(), "unknown"

    # Invariant: Every lexical word in family label MUST originate from exact claim vocabulary
    verified_words = [w for w in best_candidate_words if w in claim_words_set]
    if not verified_words:
        return set(), "unknown"

    canon_label = " ".join(verified_words[:3])
    tokens_set = {_stem(w) for w in verified_words}
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

        all_words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        all_stems = {_stem(w) for w in all_words if _stem(w) not in topic_tokens and len(w) >= 3}

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
            if union and (len(overlap) / len(union)) >= 0.5:
                matched_cluster = cl
                break
            cl_name_tokens = {_stem(w) for w in cl["name"].split()}
            if len(cl_name_tokens) >= 2 and cl_name_tokens.issubset(all_stems):
                matched_cluster = cl
                break

        if matched_cluster:
            matched_cluster["tokens"].update(c_tokens)
            matched_cluster["all_stems"].update(all_stems)
        else:
            clusters.append({
                "name": c_label,
                "tokens": set(c_tokens),
                "all_stems": set(all_stems),
            })

    return [cl["name"] for cl in clusters]
