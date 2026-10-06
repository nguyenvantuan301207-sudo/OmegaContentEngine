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
    label: str | None = Field(default=None, description="Action or relation along the edge")



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

    from omega.application.script_meta_guard import is_meta_content
    from omega.application.semantic_asset_query import subject_tokens

    if is_meta_content(cleaned) or not subject_tokens(cleaned):
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

    m = re.fullmatch(
        r"(?:the\s+)?(.+?)\s+(moves|flows)\s+through\s+(.+?)\s+into\s+(.+?)\.?",
        norm_text, flags=re.IGNORECASE,
    )
    if m:
        nodes = tuple(_clean_node(m.group(i)) for i in (1, 3, 4))
        if all(nodes) and len(set(nodes)) == 3:
            return MechanismDiagramSpec(
                nodes=nodes,
                edges=(MechanismDiagramEdge(from_node=nodes[0], to_node=nodes[1], label=m.group(2)),
                       MechanismDiagramEdge(from_node=nodes[1], to_node=nodes[2], label=m.group(2))),
                source_text=text,
            )

    # Explicit transfer/flow grammar: relation verbs, not capitalization or order.
    m = re.fullmatch(
        r"(?:the\s+)?(.+?)\s+(ships|sends|transfers|passes|feeds|moves|flows)\s+"
        r"(?:products\s+|data\s+|them\s+)?(?:to|through|into)\s+(?:the\s+)?(.+?)"
        r"(?:,\s*which\s+(sends|transfers|passes|feeds|moves|notifies)\s+(?:them\s+|data\s+)?(?:to\s+)?(?:the\s+)?(.+?))?\.?",
        norm_text, flags=re.IGNORECASE,
    )
    if m:
        raw_nodes = [m.group(1), m.group(3)] + ([m.group(5)] if m.group(5) else [])
        nodes = [_clean_node(n) for n in raw_nodes]
        if all(nodes) and len(set(nodes)) == len(nodes):
            labels = [m.group(2), m.group(4)]
            return MechanismDiagramSpec(
                nodes=tuple(nodes),
                edges=tuple(MechanismDiagramEdge(from_node=nodes[i], to_node=nodes[i + 1], label=labels[i]) for i in range(len(nodes) - 1)),
                source_text=text,
            )

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
    edges = (MechanismDiagramEdge(from_node=n1, to_node=n2, label=None),)

    return MechanismDiagramSpec(
        nodes=nodes,
        edges=edges,
        source_text=source_text,
    )


def derive_relation_label(from_node: str, to_node: str, text: str = "") -> str:
    """Deterministically derive a concise action/relation label between two diagram nodes."""
    fn = from_node.lower()
    tn = to_node.lower()
    tx = text.lower()

    if "request" in fn and "queue" in tn:
        return "enqueues"
    if "queue" in fn and "worker" in tn:
        return "dispatches"
    if "worker" in fn and ("artifact" in tn or "result" in tn or "output" in tn):
        return "produces"
    if "source" in fn and "hash" in tn:
        return "hashes"
    if "hash" in fn and "truth" in tn:
        return "binds"
    if "truth" in fn and "state" in tn:
        return "verifies"
    if "data" in fn and "transform" in tn:
        return "transforms"
    if "event" in fn and "loop" in tn:
        return "registers"
    if "loop" in fn and "queue" in tn:
        return "polls"

    if "causes" in tx:
        return "causes"
    if "dispatch" in tx:
        return "dispatches"
    if "enqueue" in tx:
        return "enqueues"
    if "produce" in tx:
        return "produces"
    if "leads to" in tx:
        return "leads to"
    if "results in" in tx:
        return "yields"
    if "because" in tx:
        return "triggers"
    if "then" in tx:
        return "then"
    if "verify" in tx or "validat" in tx:
        return "verifies"
    if "transform" in tx or "process" in tx:
        return "processes"

    return "advances"
