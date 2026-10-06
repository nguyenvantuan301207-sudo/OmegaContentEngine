"""Deterministic Claim Extractor for OMEGA-005 & P0.3a.1.

Processes structured source claims and rule-based prose extractions.
Guarantees zero speculative hallucinations, strict boilerplate rejection,
and exact source excerpt traceability.
"""

from __future__ import annotations

import re
from typing import Any

from omega.application.source_normalizer import normalize_source_text
from omega.domain.research import ClaimType

BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:cookies?|cookie policy|privacy policy|terms of (?:service|use)|terms and conditions)\b",
        re.I,
    ),
    re.compile(r"\b(?:all rights reserved|copyright|\(c\)|&copy;)\b", re.I),
    re.compile(
        r"\b(?:subscribe|newsletter|sign up|sign in|log in|register now|join now)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:click here|read more|learn more|skip to content|back to top|table of contents)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:menu|navigation|search for:|follow us|contact us|about us|share on)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:advertisement|sponsored|leave a comment|comments are closed)\b",
        re.I,
    ),
)

CAUSAL_INDICATORS = re.compile(
    r"\b(?:causes?|caused by|causing|leads? to|results? (?:in|from)|due to|mechanism|because of|triggers?)\b",
    re.I,
)


def _is_boilerplate(text: str) -> bool:
    """Check if candidate text contains web navigation or legal boilerplate."""
    return any(p.search(text) for p in BOILERPLATE_PATTERNS)


def _classify_claim_type(statement: str) -> ClaimType:
    """Classify statement into a discrete ClaimType."""
    if re.search(r"\b\d+(\.\d+)?%\b|\b\$\d+", statement):
        return ClaimType.STATISTIC
    if statement.startswith('"') and statement.endswith('"'):
        return ClaimType.QUOTE
    return ClaimType.FACT


def extract_deterministic_claims_from_source(
    source_title: str,
    source_excerpt: str,
    metadata: dict[str, Any],
    max_claims_per_source: int = 5,
    source_type: Any = None,
) -> list[dict[str, Any]]:
    """Extract claims deterministically from structured metadata or real technical prose.

    V2 Pipeline:
    1. If structured claims in metadata for trusted sources (MANUAL/IMPORT/SEED), validate and return them.
       Untrusted discovery/WEB_SEARCH metadata cannot inject claims.
    2. Explicit bullet/numbered lines extraction.
    3. Paragraph and sentence segmentation for unstructured prose:
       - Rejection of navigation/legal boilerplate
       - Length and word-count threshold gating
       - Duplicate suppression
       - Exact excerpt provenance (excerpt in source_excerpt is guaranteed)
       - Strict bound on maximum claims per source

    Returns a list of dicts with: claim_text, claim_type, excerpt, strength_score, source_location.
    """
    results: list[dict[str, Any]] = []
    seen_normalized: set[str] = set()

    # Authority Firewall: Discovery / WEB_SEARCH metadata cannot inject structured claims.
    is_web_or_discovery = (
        (isinstance(source_type, str) and source_type.upper() == "WEB_SEARCH")
        or (hasattr(source_type, "value") and str(source_type.value).upper() == "WEB_SEARCH")
        or "discovery" in metadata
        or "discovery_snippet" in metadata
    )

    # 1. Check if structured claims were directly provided in metadata for trusted non-discovery sources
    structured_claims = [] if is_web_or_discovery else metadata.get("claims", [])
    if isinstance(structured_claims, list) and structured_claims:
        for item in structured_claims:
            if isinstance(item, dict) and "text" in item:
                text = item["text"].strip()
                if not text or _is_boilerplate(text):
                    continue
                norm = normalize_claim_text(text)
                if norm in seen_normalized:
                    continue
                seen_normalized.add(norm)

                raw_type = item.get("type", "FACT").upper()
                claim_type = ClaimType.FACT
                try:
                    claim_type = ClaimType(raw_type)
                except ValueError:
                    claim_type = _classify_claim_type(text)

                excerpt = item.get("excerpt", text)
                strength = float(item.get("strength_score", 85.0))
                results.append(
                    {
                        "claim_text": text,
                        "claim_type": claim_type,
                        "excerpt": excerpt,
                        "strength_score": min(max(strength, 0.0), 100.0),
                        "source_location": item.get("source_location"),
                    }
                )
                if len(results) >= max_claims_per_source:
                    return results

    if results:
        return results

    if not source_excerpt or len(source_excerpt.strip()) < 15:
        return []

    # 2. Check for explicit bullet points or numbered facts
    lines = source_excerpt.splitlines()
    for line in lines:
        line_clean = line.strip()
        match = re.match(r"^(?:[\*\-\•]|\d+[\.\)])\s+(.*)$", line_clean)
        if match:
            statement = match.group(1).strip()
            if len(statement) >= 20 and not _is_boilerplate(statement):
                norm = normalize_claim_text(statement)
                if norm not in seen_normalized:
                    seen_normalized.add(norm)
                    # Provenance: ensure statement exists in source_excerpt
                    exact_excerpt = statement if statement in source_excerpt else line_clean
                    results.append(
                        {
                            "claim_text": statement,
                            "claim_type": _classify_claim_type(statement),
                            "excerpt": exact_excerpt,
                            "strength_score": 80.0,
                            "source_location": None,
                        }
                    )
                    if len(results) >= max_claims_per_source:
                        return results

    # 3. Unstructured Prose Extraction V2: Paragraph & sentence segmentation
    # Split into paragraphs, then into individual sentences
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", source_excerpt) if p.strip()]
    if not paragraphs:
        paragraphs = [source_excerpt.strip()]

    for para in paragraphs:
        # Segment sentences on sentence terminators (. ! ?)
        raw_sentences = re.split(r"(?<=[.!?])\s+", para)
        for raw_s in raw_sentences:
            s_clean = raw_s.strip()
            # Bounded length & word count gating
            if len(s_clean) < 25 or len(s_clean) > 500:
                continue
            words = s_clean.split()
            if len(words) < 4:
                continue
            # Must contain letters
            if not re.search(r"[a-zA-Z]{3,}", s_clean):
                continue
            # Boilerplate rejection
            if _is_boilerplate(s_clean):
                continue

            norm = normalize_claim_text(s_clean)
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)

            # Exact excerpt provenance check: verify s_clean exists in source_excerpt
            if s_clean not in source_excerpt:
                continue

            results.append(
                {
                    "claim_text": s_clean,
                    "claim_type": _classify_claim_type(s_clean),
                    "excerpt": s_clean,
                    "strength_score": 80.0,
                    "source_location": None,
                }
            )
            if len(results) >= max_claims_per_source:
                return results

    return results


def normalize_claim_text(claim_text: str) -> str:
    """Normalize claim text for deduplication and conflict comparison."""
    return normalize_source_text(claim_text)
