"""Conservative, source-grounded causal direction for research planning only.

This module cannot verify claims or confer numeric coverage. A lexical relation
is useful for a causal topic only when its effect matches that topic's outcome.
"""

from __future__ import annotations

import enum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CausalDirection(enum.StrEnum):
    CAUSE_OF_TOPIC_OUTCOME = "CAUSE_OF_TOPIC_OUTCOME"
    CONSEQUENCE_OF_TOPIC_OUTCOME = "CONSEQUENCE_OF_TOPIC_OUTCOME"
    CAUSAL_INTERMEDIATE = "CAUSAL_INTERMEDIATE"
    GENERIC_CAUSAL_RELATION = "GENERIC_CAUSAL_RELATION"
    AMBIGUOUS_RELATION = "AMBIGUOUS_RELATION"
    NON_CAUSAL_CONTEXT = "NON_CAUSAL_CONTEXT"


class CausalAssertion(BaseModel):
    cause_span: str = ""
    relation: str = ""
    effect_span: str = ""
    topic_outcome_match: bool = False
    direction: CausalDirection = CausalDirection.AMBIGUOUS_RELATION
    provenance: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(frozen=True)


_REVERSE = re.compile(
    r"\b(?:(?:is|are|was|were|may\s+be|can\s+be)\s+)?"
    r"(?:(?:caused|produced|induced|triggered)\s+by|results?\s+from|"
    r"arises?\s+from|due\s+to|because\s+of)\b",
    re.I,
)
_FORWARD = re.compile(
    r"\b(?:causes?|causing|produces?|producing|induces?|inducing|triggers?|triggering|"
    r"cracks?|creates?|creating|generates?|generating|leads?\s+to|leading\s+to|"
    r"results?\s+in|resulting\s+in|reduces?|reducing|shortens?|shortening|"
    r"degrades?|degrading|impairs?|impairing|disrupts?|disrupting)\b",
    re.I,
)
_CONDITION = re.compile(r"\b(?:occurs?|develops?|happens?|initiates?)\s+when\b", re.I)
_UNRESOLVED = re.compile(
    r"^(?:(?:on\s+the\s+other\s+hand\s+)?(?:it|they|this|that|these|those|such)\b|"
    r"(?:both|all|each|either|neither)\s+(?:actions?|factors?|effects?|processes|conditions)|"
    r"(?:anything|something|nothing)\b)",
    re.I,
)
_NON_CAUSAL = re.compile(
    r"\b(?:inspection|monitoring|repair|mitigation|prevention|table\s+of\s+contents|"
    r"advice\s+note|this\s+paper|this\s+study|covered\s+in\s+detail|"
    r"can\s+be\s+(?:controlled|prevented|repaired)|control\s+joints?)\b",
    re.I,
)
_FILLER = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "in",
        "for",
        "why",
        "how",
        "what",
        "are",
        "is",
        "does",
        "do",
        "mechanism",
        "mechanisms",
        "cause",
        "causes",
        "reason",
        "reasons",
        "primary",
        "every",
        "should",
        "understand",
        "know",
        "explained",
        "occurs",
        "occur",
        "happens",
    }
)


def lexical_tokens(text: str) -> list[str]:
    """Small grammatical normalization; no mechanism dictionary or embeddings."""
    result = []
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in _FILLER:
            continue
        if word in {"lose", "loses", "losing", "lost", "loss", "losses"}:
            word = "loss"
        elif word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif word.endswith("ing") and len(word) > 5:
            word = word[:-3]
            if len(word) > 2 and word[-1] == word[-2]:
                word = word[:-1]
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            word = word[:-1]
        result.append(word)
    return result


def _outcome_tokens(topic: str) -> list[str]:
    topic = re.split(r"[:—–]", topic, maxsplit=1)[0]
    topic = re.sub(
        r"^\s*\d+\s+(?:mechanisms?|causes?|reasons?)\s+(?:of|for)\s+", "", topic, flags=re.I
    )
    relation = _FORWARD.search(topic)
    if relation and relation.group().lower() not in {"crack", "cracks"}:
        topic = topic[relation.end() :]
    return lexical_tokens(topic)


def matches_topic_outcome(span: str, topic: str) -> bool:
    focus = _outcome_tokens(topic)
    tokens = lexical_tokens(span)
    # The grammatical outcome head, rather than any shared material/domain noun,
    # must match. Qualification remains in the exact source span for inspection.
    if not focus or focus[-1] not in tokens:
        return False
    # Generic event nouns need their lexical qualifier: packet loss and capacity
    # loss share a head but do not denote the same outcome.
    if (
        focus[-1] in {"loss", "failure", "reduction", "increase", "decrease", "disruption"}
        and len(focus) > 1
    ):
        return focus[-2] in tokens
    return True


def _clean_span(span: str) -> str:
    span = span.strip(" \t\n,.;:")
    span = re.sub(r"^(?:In\s+addition\s+to|What\s+Causes\s+Them:)\s*", "", span, flags=re.I)
    span = re.sub(r"\s+(?:can|may|will|would|also)\s*$", "", span, flags=re.I)
    span = re.sub(r"\s+that\s*$", "", span, flags=re.I)
    return span.strip()


def _hybrid(span: str) -> bool:
    # Explicit disjunction/hybrid cause categories must be atomicized elsewhere;
    # conjunction inside a named process is retained (e.g. expansion/contraction).
    return bool(re.search(r"\b(?:both|either)\b|\b(?:or|and)\b|,\s*\w", span, re.I))


def _assertion(
    cause: str, relation: str, effect: str, topic: str, text: str, provenance: dict[str, Any]
) -> CausalAssertion:
    cause, effect = _clean_span(cause), _clean_span(effect)
    cause_match, effect_match = (
        matches_topic_outcome(cause, topic),
        matches_topic_outcome(effect, topic),
    )
    direction = CausalDirection.GENERIC_CAUSAL_RELATION
    if (
        not cause
        or not effect
        or _UNRESOLVED.search(cause)
        or _hybrid(cause)
        or cause_match
        and effect_match
    ):
        direction = CausalDirection.AMBIGUOUS_RELATION
    elif effect_match:
        direction = CausalDirection.CAUSE_OF_TOPIC_OUTCOME
    elif cause_match:
        direction = CausalDirection.CONSEQUENCE_OF_TOPIC_OUTCOME
    details = {
        **provenance,
        "claim_text": text,
        "topic_outcome": topic,
        "cause_in_claim": cause in text,
        "effect_in_claim": effect in text,
    }
    return CausalAssertion(
        cause_span=cause,
        relation=relation,
        effect_span=effect,
        topic_outcome_match=cause_match or effect_match,
        direction=direction,
        provenance=details,
    )


def extract_causal_assertions(
    text: str, topic: str, provenance: dict[str, Any] | None = None
) -> list[CausalAssertion]:
    """Extract explicit relations, reversed grammar, conditions and relative chains.

    Only an explicit relative-clause chain licenses a causal intermediate. We
    never infer a missing edge from proximity, topical words, or keyword lists.
    """
    provenance = provenance or {}
    if _NON_CAUSAL.search(text):
        return [
            CausalAssertion(
                direction=CausalDirection.NON_CAUSAL_CONTEXT,
                provenance={**provenance, "claim_text": text},
            )
        ]
    if _UNRESOLVED.search(text.strip()):
        return [
            CausalAssertion(
                provenance={**provenance, "claim_text": text, "reason": "unresolved_antecedent"}
            )
        ]
    # Safe explicit causes enumeration: source substrings only, with atomic edges.
    enumeration = re.search(
        r"\b(?:have|has)\s+(?:several\s+)?causes?\s+(?:including|such\s+as|:)\s*", text, re.I
    )
    if enumeration:
        effect = _clean_span(text[: enumeration.start()])
        tail = text[enumeration.end() :].strip(" .")
        if "(" not in tail and ")" not in tail:
            items = re.split(r",\s*(?:and\s+)?|\s+and\s+", tail)
            if 2 <= len(items) <= 12 and all(1 <= len(i.split()) <= 6 for i in items):
                return [
                    _assertion(
                        i,
                        enumeration.group(),
                        effect,
                        topic,
                        text,
                        {**provenance, "atomic_enumeration": True},
                    )
                    for i in items
                ]
        return [
            CausalAssertion(
                provenance={**provenance, "claim_text": text, "reason": "unsupported_hybrid"}
            )
        ]
    # Parse distinct clauses separately so one reverse relation cannot borrow
    # a cause from a later clause with a different subject/relation.
    clauses = re.split(
        r",\s*(?=\w+\s+(?:also\s+)?(?:results?\s+from|(?:is|are)\s+caused\s+by))", text, flags=re.I
    )
    if len(clauses) > 1:
        return [
            a for clause in clauses for a in extract_causal_assertions(clause, topic, provenance)
        ]
    reverse = _REVERSE.search(text)
    if reverse:
        cause = re.split(
            r"[.;]|\b(?:and\s+is|which|that|during)\b", text[reverse.end() :], maxsplit=1
        )[0]
        items = re.split(r",\s*(?:and\s+)?|\s+and\s+", cause)
        if (
            2 <= len(items) <= 12
            and all(1 <= len(i.split()) <= 6 for i in items)
            and not re.search(r"\b(?:both|either|or)\b|[()]", cause, re.I)
        ):
            return [
                _assertion(
                    i,
                    reverse.group(),
                    text[: reverse.start()],
                    topic,
                    text,
                    {**provenance, "atomic_enumeration": True},
                )
                for i in items
            ]
        return [
            _assertion(cause, reverse.group(), text[: reverse.start()], topic, text, provenance)
        ]
    condition = _CONDITION.search(text)
    if condition and len(text[: condition.start()].split()) > 8:
        return [
            CausalAssertion(
                provenance={
                    **provenance,
                    "claim_text": text,
                    "reason": "condition_subject_not_isolated",
                }
            )
        ]
    if condition:
        tail = text[condition.end() :].strip(" .")
        nested = _FORWARD.search(tail)
        if nested and not _hybrid(tail[: nested.start()]):
            return [
                _assertion(
                    tail[: nested.start()],
                    condition.group() + " / " + nested.group(),
                    text[: condition.start()],
                    topic,
                    text,
                    {**provenance, "condition_span": tail, "explicit_conditional_chain": True},
                )
            ]
        return [
            _assertion(
                text[condition.end() :].strip(" ."),
                condition.group(),
                text[: condition.start()],
                topic,
                text,
                provenance,
            )
        ]
    forward = [
        m
        for m in _FORWARD.finditer(text)
        if not re.match(r"\s+(?:of|including|include|such as)\b", text[m.end() :], re.I)
        and not (m.group().lower() in {"crack", "cracks"} and m.start() == 0)
    ]
    if forward:
        first = forward[0]
        cause, effect = (
            text[: first.start()],
            text[
                first.start()
                if first.group().lower() in {"crack", "cracks", "cracking"}
                else first.end() :
            ].strip(" ."),
        )
        if len(forward) > 1:
            second = forward[1]
            bridge = text[first.end() : second.start()]
            if re.search(r"\b(?:that|which)\s*$", bridge, re.I):
                intermediate = _clean_span(bridge)
                terminal = text[
                    second.start()
                    if second.group().lower() in {"crack", "cracks", "cracking"}
                    else second.end() :
                ].strip(" .")
                downstream = _assertion(
                    intermediate, second.group(), terminal, topic, text, provenance
                )
                if downstream.direction == CausalDirection.CAUSE_OF_TOPIC_OUTCOME:
                    chain = _assertion(
                        cause,
                        first.group(),
                        intermediate,
                        topic,
                        text,
                        {**provenance, "chain_effect": terminal, "chain_relation": second.group()},
                    )
                    if chain.direction == CausalDirection.GENERIC_CAUSAL_RELATION:
                        chain = chain.model_copy(
                            update={
                                "direction": CausalDirection.CAUSAL_INTERMEDIATE,
                                "topic_outcome_match": True,
                            }
                        )
                    return [chain, downstream]
        return [_assertion(cause, first.group(), effect, topic, text, provenance)]
    return [
        CausalAssertion(
            direction=CausalDirection.AMBIGUOUS_RELATION,
            provenance={**provenance, "claim_text": text},
        )
    ]


ELIGIBLE_DIRECTIONS = frozenset(
    {CausalDirection.CAUSE_OF_TOPIC_OUTCOME, CausalDirection.CAUSAL_INTERMEDIATE}
)


def _source_text(text: str) -> str:
    # PDF line-break hyphenation is a formatting change, not semantic inference.
    text = re.sub(r"(?<=[a-z])-\s*(?=[a-z])", "", text.lower())
    return re.sub(r"\s+", " ", text)


def ground_causal_assertion(
    assertion: CausalAssertion | None, claim: Any, sources: dict, topic: str
) -> tuple[bool, list]:
    """Require an explicit matching edge in this claim's canonical source evidence.

    Snippets, metadata and unrelated request sources cannot supply an edge.
    Matching source text, source ID and the exact relation remain inspectable.
    """
    if assertion is None or assertion.direction not in ELIGIBLE_DIRECTIONS:
        return False, []
    source_ids, edges = [], []
    for ev in getattr(claim, "evidence", []) or []:
        sid = getattr(ev, "source_id", None)
        src = sources.get(sid)
        if src is None or str(getattr(ev, "support_direction", "")) != "SUPPORTS":
            continue
        request_id = getattr(claim, "research_request_id", None)
        if request_id is None or getattr(src, "research_request_id", None) != request_id:
            continue
        content = _source_text(getattr(src, "content_excerpt", "") or "")
        excerpt = _source_text(getattr(ev, "excerpt", "") or "")
        if not excerpt or excerpt not in content:
            continue
        for sentence in re.split(r"(?<=[.!?;])\s+|\n+", content):
            for edge in extract_causal_assertions(sentence, topic):
                if edge.direction not in ELIGIBLE_DIRECTIONS:
                    continue
                if _source_text(edge.cause_span) != _source_text(assertion.cause_span):
                    continue
                # Match the explicit intermediate/terminal edge, not just cause words.
                if edge.direction != assertion.direction:
                    continue
                source_ids.append(sid)
                edges.append(
                    {
                        "source_id": str(sid),
                        "source_url": getattr(src, "url", None),
                        "source_sentence": sentence,
                        "source_relation": edge.relation,
                        "cause_span": edge.cause_span,
                        "effect_span": edge.effect_span,
                    }
                )
                break
    # The assertion is frozen. Provenance contents are deliberately assembled
    # here without touching any claim status, confidence or source independence.
    assertion.provenance.update(source_grounded=bool(source_ids), source_edges=edges)
    return bool(source_ids), list(dict.fromkeys(source_ids))
