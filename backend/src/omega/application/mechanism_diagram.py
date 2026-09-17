"""Shared authority for trustworthy mechanism diagram eligibility and payload extraction.

Deterministically extracts source-derived mechanism diagram nodes and causal/sequential edges
from canonical text clauses without fabrication or generative labeling.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field


class MechanismDiagramEdge(BaseModel):
    """Immutable directed edge between two diagram nodes."""

    model_config = ConfigDict(frozen=True)

    from_node: str = Field(description="Origin node label")
    to_node: str = Field(description="Destination node label")


class MechanismDiagramSpec(BaseModel):
    """Immutable trustworthy diagram specification derived directly from source text."""

    model_config = ConfigDict(frozen=True)

    nodes: tuple[str, ...] = Field(description="Ordered, unique, source-derived node labels")
    edges: tuple[MechanismDiagramEdge, ...] = Field(description="Directed edges connecting nodes")
    source_text: str = Field(description="Original source text from which the diagram was resolved")


def _clean_node(phrase: str) -> str | None:
    """Conservatively normalize a candidate node phrase while preserving exact source wording.

    Rejects:
    - empty or blank phrases
    - single-word phrases with <= 2 characters
    - clauses longer than 80 characters or more than 10 words
    """
    if not phrase:
        return None

    # Collapse multiple whitespace characters
    cleaned = " ".join(phrase.strip().split())
    # Strip leading/trailing non-alphanumeric punctuation except quotes/brackets
    cleaned = re.sub(r"^[\s,;:.\-–—]+", "", cleaned)
    cleaned = re.sub(r"[\s,;:.\-–—]+$", "", cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        return None

    words = cleaned.split()
    # Reject oversized clauses
    if len(cleaned) > 85 or len(words) > 10:
        return None

    # Reject trivial 1-word fragments
    if len(words) == 1 and (
        len(cleaned) <= 3
        or cleaned.lower() in ("and", "the", "then", "that", "this", "with", "from")
    ):
        return None

    return cleaned


def resolve_mechanism_diagram_spec(text: str) -> MechanismDiagramSpec | None:
    """Pure deterministic extractor for source-derived mechanism diagrams.

    Inspects explicit causal, sequential, and conditional relation patterns:
    1. 'Because B, A'   => B -> A
    2. 'A because B'   => B -> A
    3. 'As A, B'       => A -> B
    4. 'When A, B'     => A -> B
    5. 'A causes B'    => A -> B
    6. 'A leads to B'  => A -> B
    7. 'A results in B'=> A -> B
    8. 'A, therefore B'=> A -> B
    9. 'A, so B'       => A -> B
    10. 'First A, then B' => A -> B
    11. 'A, then B'    => A -> B

    Returns MechanismDiagramSpec if at least 2 distinct, valid, source-derived nodes can be extracted.
    Otherwise returns None (fail-closed, false negatives preferred over fabrication).
    """
    if not text or not isinstance(text, str):
        return None

    norm_text = " ".join(text.strip().split())
    if not norm_text:
        return None

    # Try explicit structural patterns in priority order

    # Pattern 1: 'Because B, A' => B -> A
    m = re.match(r"^because\s+(.+?),\s*(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 2: 'A because B' => B -> A
    # Make sure 'because' is preceded and succeeded by valid boundaries
    m = re.match(r"^(.+?)\s+because\s+(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        effect, cause = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 3: 'As A, B' => A -> B
    m = re.match(r"^as\s+(.+?),\s*(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 4: 'When A, B' => A -> B
    m = re.match(r"^when\s+(.+?),\s*(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 5: 'A causes B' => A -> B
    m = re.match(r"^(.+?)\s+causes\s+(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 6: 'A leads to B' => A -> B
    m = re.match(r"^(.+?)\s+leads\s+to\s+(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 7: 'A results in B' => A -> B
    m = re.match(r"^(.+?)\s+results\s+in\s+(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 8: 'A, therefore B' => A -> B
    m = re.match(r"^(.+?),\s*(?:and\s+)?therefore\s+(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 9: 'A, so B' => A -> B
    m = re.match(r"^(.+?),\s*so\s+(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 10: 'First A, then B' => A -> B
    m = re.match(r"^first\s+(.+?),\s*then\s+(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    # Pattern 11: 'A, then B' or 'A then B' => A -> B
    m = re.match(r"^(.+?),\s*then\s+(.+)$", norm_text, flags=re.IGNORECASE)
    if m:
        cause, effect = m.group(1), m.group(2)
        return _build_spec(cause, effect, source_text=text)

    return None


def _build_spec(from_raw: str, to_raw: str, *, source_text: str) -> MechanismDiagramSpec | None:
    """Clean candidate clauses, validate node quality gate, and assemble MechanismDiagramSpec."""
    n1 = _clean_node(from_raw)
    n2 = _clean_node(to_raw)

    if not n1 or not n2:
        return None

    # Nodes must be distinct after whitespace and case normalization
    if n1.lower() == n2.lower():
        return None

    nodes = (n1, n2)
    edges = (MechanismDiagramEdge(from_node=n1, to_node=n2),)

    return MechanismDiagramSpec(
        nodes=nodes,
        edges=edges,
        source_text=source_text,
    )
