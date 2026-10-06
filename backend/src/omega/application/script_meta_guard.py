"""Final deterministic admission guard for planning instructions and depth padding."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from omega.application.semantic_asset_query import tokens

_META_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:this|the|our)\s+(?:section|analysis|chapter|segment)\s+(?:examines?|explores?|connects?|explains?|discusses?)\b",
        r"\b(?:capture|grab)\s+(?:the\s+)?(?:viewer(?:'s)?\s+)?attention\b",
        r"\b(?:progressively\s+unpack|deliver\s+the\s+empirical\s+resolution|establish\s+the\s+viewer\s+contract)\b",
        r"\b(?:factual|key|core)\s+(?:detail|information|mechanism)\s+(?:for|of)\b.*\b(?:under|regarding)\b",
        r"\bpractitioners\s+(?:must|should)\s+(?:evaluate|examine)\b.*\b(?:process|components|mechanisms)\b",
        r"\b(?:the|our)\s+analysis\s+connects\b.*\b(?:principles|assumptions|rigorous)\b",
    )
)


def is_meta_content(text: str | None) -> bool:
    return any(pattern.search(text or "") for pattern in _META_PATTERNS)


def script_meta_evidence(script: dict[str, Any]) -> list[str]:
    """Inspect authored copy once; section narration duplicates statement text."""
    texts = [str(script.get(key) or "") for key in ("hook_text", "closing_text", "cta_text")]
    for section in script.get("sections") or []:
        statements = section.get("statements") or []
        texts.extend(str(st.get("statement_text") or "") for st in statements)
        if not statements:
            texts.append(str(section.get("narration_text") or ""))
        texts.append(str(section.get("heading") or ""))
    evidence = [text for text in texts if is_meta_content(text)]
    sentences = Counter(
        " ".join(tokens(sentence))
        for text in texts
        for sentence in re.split(r"[.!?]+", text)
        if len(tokens(sentence)) >= 12
    )
    evidence.extend(
        f"REPEATED_SENTENCE ({count}): {sentence}"
        for sentence, count in sentences.items()
        if count >= 3
    )
    return list(dict.fromkeys(evidence))


def is_source_proposition(text: str) -> bool:
    if is_meta_content(text):
        return False
    return bool(
        set(tokens(text))
        & {
            "is",
            "are",
            "causes",
            "cause",
            "reduces",
            "reduce",
            "increases",
            "increase",
            "forms",
            "form",
            "occurs",
            "happens",
            "loses",
            "contains",
            "produces",
            "leads",
            "results",
            "requires",
            "supports",
            "prevents",
            "can",
            "may",
            "exceeds",
        }
    )
