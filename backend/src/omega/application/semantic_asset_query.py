"""Deterministic subject grounding shared by planning, acquisition and canonical QA."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit


def tokens(text: str | None) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text or "").casefold()))


def contains_phrase(text: str | None, phrase: str) -> bool:
    words, wanted = tokens(text), tokens(phrase)
    return any(words[i : i + len(wanted)] == wanted for i in range(len(words) - len(wanted) + 1))


_RHETORIC = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "this",
        "that",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "we",
        "you",
        "your",
        "our",
        "how",
        "why",
        "what",
        "when",
        "which",
        "as",
        "if",
        "then",
        "because",
        "every",
        "should",
        "must",
        "can",
        "will",
        "about",
        "under",
        "regarding",
        "overview",
        "introduction",
        "closing",
        "conclude",
        "conclusion",
        "section",
        "chapter",
        "hook",
        "promise",
        "context",
        "development",
        "escalation",
        "payoff",
        "takeaway",
        "capture",
        "attention",
        "highlighting",
        "highlight",
        "progressively",
        "unpack",
        "deliver",
        "empirical",
        "resolution",
        "establish",
        "viewer",
        "contract",
        "insights",
        "revealed",
        "factual",
        "detail",
        "core",
        "mechanism",
        "mechanisms",
        "understand",
        "understanding",
        "analysis",
        "examines",
        "examine",
        "explain",
        "explaining",
        "explores",
        "explore",
        "operational",
        "process",
        "foundational",
        "components",
        "factors",
        "observed",
        "primary",
        "boundary",
        "conditions",
        "practitioners",
        "evaluate",
        "govern",
        "topic",
        "valuable",
        "subscribe",
        "channel",
        "comments",
        "perspective",
        "research",
        "confirmed",
        "confirms",
        "verified",
        "evidence",
        "specific",
        "behavior",
        "principles",
        "identifies",
        "working",
        "assumptions",
        "rigorous",
        "care",
        "connects",
        "apply",
        "finding",
        "question",
        "answer",
        "thank",
        "exploring",
        "detailed",
        "consistent",
        "results",
        "requires",
        "continuous",
        "observation",
        "disciplined",
        "methodology",
        "reliance",
        "high",
        "practical",
        "prevention",
        "guidance",
        "key",
        "information",
        "title",
        "transition",
    ]
)

_DECORATIVE = (
    "bird",
    "birds",
    "diver",
    "divers",
    "underwater",
    "handshake",
    "handshakes",
    "coffee cup",
    "sunset",
    "forest",
    "beach",
    "abstract geometric",
    "decorative",
)

# Literal software subjects only. Broad engineering discourse is not a software domain.
_TECHNICAL_DOMAINS = (
    (("event loop", "asyncio", "coroutine", "python"), "python code on terminal monitor screen"),
    (
        ("software architecture", "server architecture"),
        "software engineering server architecture diagram",
    ),
    (
        ("server", "servers", "datacenter", "data center"),
        "modern datacenter server racks blinking lights",
    ),
    (
        ("programming", "software", "code", "computer"),
        "software developer typing code on computer terminal",
    ),
    (
        ("network", "api", "apis", "http", "endpoint"),
        "network switch fiber optic cables server data",
    ),
    (("database", "sql", "postgres", "redis"), "enterprise database server infrastructure storage"),
)
_SOFTWARE = frozenset(
    [
        "server",
        "servers",
        "datacenter",
        "software",
        "computer",
        "programming",
        "python",
        "asyncio",
        "api",
        "apis",
        "http",
        "sql",
        "postgres",
        "redis",
        "database",
        "network",
        "terminal",
    ]
)


def subject_tokens(text: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(w for w in tokens(text) if w not in _RHETORIC and not w.isdecimal()))


def _anchors(text: str | None) -> set[str]:
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in subject_tokens(text)}


def is_unrelated_stock_concept(query: str | None, *, source_text: str | None = None) -> bool:
    return any(
        contains_phrase(query, phrase) and not contains_phrase(source_text, phrase)
        for phrase in _DECORATIVE
    )


@dataclass(frozen=True)
class SemanticDecision:
    code: str
    source_anchors: tuple[str, ...]
    evidence: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.code == "VALID"

    def as_metadata(self) -> dict:
        return {
            "code": self.code,
            "source_anchors": list(self.source_anchors),
            "evidence": list(self.evidence),
        }


def validate_semantic_query(query: str | None, source_text: str | None) -> SemanticDecision:
    anchors = tuple(sorted(_anchors(source_text)))
    if not anchors:
        return SemanticDecision("INSUFFICIENT_SUBJECT_GROUNDING", anchors)
    if not subject_tokens(query):
        return SemanticDecision("RHETORICAL_ONLY_QUERY", anchors)
    source_words, query_words = set(tokens(source_text)), set(tokens(query))
    drift = sorted((query_words & _SOFTWARE) - source_words)
    software_source = bool(source_words & _SOFTWARE) or contains_phrase(source_text, "event loop")
    if drift and not software_source:
        return SemanticDecision("INVALID_DOMAIN_DRIFT", anchors, tuple(drift))
    if not (_anchors(query) & set(anchors)) and not (software_source and query_words & _SOFTWARE):
        return SemanticDecision(
            "INSUFFICIENT_SUBJECT_GROUNDING", anchors, tuple(subject_tokens(query))
        )
    return SemanticDecision("VALID", anchors)


def derive_semantic_asset_query(
    text: str | None,
    *,
    fallback_topic: str | None = None,
    subject_text: str | None = None,
    default_technical: str | None = None,
) -> str:
    """Use canonical title/segment, never an invented technical fallback.

    default_technical is retained for compatibility but cannot grant authority.
    Empty output means insufficient authority and admits only the local visual path.
    """
    authority = f"{subject_text or ''} {text or ''} {fallback_topic or ''}"
    for keywords, query in _TECHNICAL_DOMAINS:
        if (
            any(contains_phrase(authority, kw) for kw in keywords)
            and validate_semantic_query(query, authority).valid
        ):
            return query
    words = subject_tokens(subject_text) or subject_tokens(text) or subject_tokens(fallback_topic)
    return " ".join(words[:8])


class SemanticGroundingError(ValueError):
    def __init__(self, decision: SemanticDecision):
        self.decision = decision
        super().__init__(decision.code)


def validate_provider_semantics(
    *,
    source_text: str,
    query: str,
    metadata: dict,
    source_page_url: str | None = None,
) -> SemanticDecision:
    """Inspect observed descriptions/page path, never creator or echoed query as
    evidence of image content. Missing descriptive metadata is unknown.
    """
    query_decision = validate_semantic_query(query, source_text)
    acquired_query = metadata.get("search_query")
    if isinstance(acquired_query, str) and acquired_query != query:
        acquired_decision = validate_semantic_query(acquired_query, source_text)
        if not acquired_decision.valid:
            return SemanticDecision(
                "PROVIDER_ASSET_SEMANTIC_MISMATCH",
                query_decision.source_anchors,
                ("ACQUIRED_QUERY_" + acquired_decision.code,),
            )
    descriptions = []
    for key in ("title", "description", "tags", "alt"):
        value = metadata.get(key)
        if isinstance(value, str):
            descriptions.append(value)
        elif isinstance(value, (list, tuple)):
            descriptions.extend(v for v in value if isinstance(v, str))
    if source_page_url:
        descriptions.append(urlsplit(source_page_url).path.replace("-", " "))
    observed = " ".join(descriptions)
    unrelated_software = set(tokens(observed)) & _SOFTWARE
    source_software = set(tokens(source_text)) & _SOFTWARE
    if unrelated_software and not source_software:
        return SemanticDecision(
            "PROVIDER_ASSET_SEMANTIC_MISMATCH",
            query_decision.source_anchors,
            tuple(sorted(unrelated_software)),
        )
    # Positive domain evidence, not mere lack of lexical overlap: synonyms and
    # missing tags are not proof of mismatch. These families classify evidence;
    # they never manufacture a provider query or override canonical source text.
    families = {
        "materials": {
            "concrete",
            "cement",
            "steel",
            "wood",
            "material",
            "materials",
            "machinery",
            "industrial",
        },
        "ecology": {"coral", "reef", "reefs", "bleaching", "seawater", "ecosystem"},
        "astronomy": {"galaxy", "galaxies", "nebula", "stellar", "cosmos"},
        "music": {"concert", "festival", "musician", "music", "singing"},
        "software": _SOFTWARE,
    }
    source_domains = {k for k, terms in families.items() if set(tokens(source_text)) & terms}
    observed_domains = {k for k, terms in families.items() if set(tokens(observed)) & terms}
    if source_domains and observed_domains and source_domains.isdisjoint(observed_domains):
        return SemanticDecision(
            "PROVIDER_ASSET_SEMANTIC_MISMATCH",
            query_decision.source_anchors,
            tuple(sorted(observed_domains)),
        )
    if not query_decision.valid:
        return query_decision
    return SemanticDecision("VALID", query_decision.source_anchors, tuple(subject_tokens(observed)))
