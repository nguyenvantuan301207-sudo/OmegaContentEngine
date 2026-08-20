"""Deterministic Claim Extractor for OMEGA-005.

Processes structured source claims and rule-based extractions.
Guarantees zero speculative hallucinations and direct source traceability.
"""

from __future__ import annotations

import re
from typing import Any

from omega.application.source_normalizer import normalize_source_text
from omega.domain.research import ClaimType


def extract_deterministic_claims_from_source(
    source_title: str,
    source_excerpt: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract claims deterministically from structured source metadata or explicit markers.

    Returns a list of dicts with: claim_text, claim_type, excerpt, strength_score.
    """
    results: list[dict[str, Any]] = []

    # 1. Check if structured claims were directly provided in metadata
    structured_claims = metadata.get("claims", [])
    if isinstance(structured_claims, list):
        for item in structured_claims:
            if isinstance(item, dict) and "text" in item:
                text = item["text"].strip()
                raw_type = item.get("type", "FACT").upper()
                claim_type = ClaimType.FACT
                try:
                    claim_type = ClaimType(raw_type)
                except ValueError:
                    claim_type = ClaimType.FACT

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

    # 2. If no structured claims in metadata, look for explicit bullet points or numbered facts
    if not results and source_excerpt:
        lines = source_excerpt.splitlines()
        for line in lines:
            line_clean = line.strip()
            # Match lines starting with bullet, asterisk, or number
            match = re.match(r"^(?:[\*\-\•]|\d+[\.\)])\s+(.*)$", line_clean)
            if match:
                statement = match.group(1).strip()
                if len(statement) >= 15:
                    # Detect if line contains numbers/percentages (STATISTIC) or quotes (QUOTE)
                    claim_type = ClaimType.FACT
                    if re.search(r"\b\d+(\.\d+)?%\b|\b\$\d+", statement):
                        claim_type = ClaimType.STATISTIC
                    elif statement.startswith('"') and statement.endswith('"'):
                        claim_type = ClaimType.QUOTE

                    results.append(
                        {
                            "claim_text": statement,
                            "claim_type": claim_type,
                            "excerpt": statement,
                            "strength_score": 80.0,
                            "source_location": None,
                        }
                    )

    # 3. Fallback: if no items found and excerpt has substantial text, extract the core topic summary sentence
    if not results and source_excerpt and len(source_excerpt) >= 20:
        first_sentence = source_excerpt.split(".")[0].strip()
        if len(first_sentence) >= 15:
            results.append(
                {
                    "claim_text": first_sentence,
                    "claim_type": ClaimType.FACT,
                    "excerpt": first_sentence,
                    "strength_score": 75.0,
                    "source_location": None,
                }
            )

    return results


def normalize_claim_text(claim_text: str) -> str:
    """Normalize claim text for deduplication and conflict comparison."""
    return normalize_source_text(claim_text)
