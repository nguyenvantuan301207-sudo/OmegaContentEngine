"""Pure deterministic semantic asset query derivation and relevance enforcement.

Prioritizes literal technical subjects over generic stock or abstract metaphors:
1. Literal subject relevance (servers, terminals, code, data flow, architecture)
2. Mechanism/process relevance (pipelines, queues, execution)
3. Rejection of unrelated lifestyle/nature/abstract stock (birds, divers, handshakes)
"""

from __future__ import annotations

import re
from typing import Final

# Technical domain concepts mapped to focused, high-quality search queries
_TECHNICAL_DOMAINS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    # Async, event loops, concurrency
    (
        ("event loop", "asyncio", "async", "coroutine", "concurrency", "callback", "non-blocking"),
        "python code on terminal monitor screen",
    ),
    # Architecture, pipelines, systems
    (
        ("architecture", "pipeline", "workflow", "orchestration", "control flow", "system design"),
        "software engineering server architecture diagram",
    ),
    # Datacenter, servers, infrastructure
    (
        ("server", "servers", "datacenter", "data center", "infrastructure", "cloud", "cluster"),
        "modern datacenter server racks blinking lights",
    ),
    # Code, programming, software engineering
    (
        ("code", "programming", "python", "developer", "function", "syntax", "editor", "script"),
        "software developer typing code on computer terminal",
    ),
    # Networking, APIs, data movement
    (
        ("network", "api", "apis", "http", "rest", "request", "endpoint", "data transmission", "traffic"),
        "network switch fiber optic cables server data",
    ),
    # Databases, storage, persistence
    (
        ("database", "sql", "storage", "postgres", "redis", "query", "schema", "persistence"),
        "enterprise database server infrastructure storage",
    ),
    # Verification, provenance, testing, security
    (
        ("verification", "provenance", "validation", "audit", "security", "checksum", "testing", "contract"),
        "software verification testing security code review",
    ),
)

# Unrelated decorative concepts that must NEVER be selected for technical narration
_UNRELATED_DECORATIVE_TERMS: Final[frozenset[str]] = frozenset({
    "bird", "birds", "flying", "diver", "divers", "diving", "underwater", "ocean", "fish",
    "handshake", "handshakes", "businessman", "businessmen", "meeting room",
    "coffee cup", "sunset", "sunrise", "forest", "mountain", "beach", "flowers",
    "abstract", "geometric", "shape", "shapes", "abstract shapes", "abstract geometric", "decorative",
})


def is_unrelated_stock_concept(query: str | None) -> bool:
    """Detect if an asset query contains unrelated decorative or lifestyle terms."""
    if not query or not query.strip():
        return False
    lower = query.strip().lower()
    for phrase in _UNRELATED_DECORATIVE_TERMS:
        if phrase in lower:
            return True
    words = set(re.findall(r"\b\w+\b", lower))
    return bool(words & _UNRELATED_DECORATIVE_TERMS)


def derive_semantic_asset_query(
    text: str | None,
    *,
    fallback_topic: str | None = None,
    default_technical: str = "software engineering server technology",
) -> str:
    """Derive a concrete, literal technical asset search query from narration/topic text.

    Guarantees that software, server, architecture, and verification concepts receive
    literal, relevant technical visual search queries rather than generic stock.
    """
    combined = f"{text or ''} {fallback_topic or ''}".lower()

    # 1. Match specific technical domain
    for keywords, query in _TECHNICAL_DOMAINS:
        if any(kw in combined for kw in keywords):
            return query

    # 2. General technical words check
    if any(term in combined for term in (
        "system", "process", "component", "module", "engine", "service", "execution", "runtime",
        "performance", "memory", "compute", "scale", "worker", "task", "job", "artifact"
    )):
        return "computer technology server datacenter infrastructure"

    # 3. Clean topic fallback
    if fallback_topic and fallback_topic.strip():
        clean_topic = re.sub(r"\[.*?\]", "", fallback_topic)
        clean_topic = re.sub(r"\b(?:P\d+-[A-Z0-9_-]+|RETRY-?\d+)\b", "", clean_topic, flags=re.IGNORECASE)
        words = [w for w in re.findall(r"\b\w+\b", clean_topic) if w.lower() not in _UNRELATED_DECORATIVE_TERMS]
        if words:
            return f"{' '.join(words[:4])} technology"

    return default_technical
