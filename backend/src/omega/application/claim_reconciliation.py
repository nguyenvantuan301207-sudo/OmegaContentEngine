"""Deterministic cross-source claim corroboration and reconciliation for OMEGA.

Reconciles equivalent factual propositions from independent research sources
without LLMs. Guarantees conservative matching, strict contradiction safety,
exact source excerpt provenance, and reconciliation idempotency.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.claim_extractor import normalize_claim_text
from omega.domain.research import ClaimType, EvidenceDirection
from omega.infrastructure.models import ClaimEvidence, ResearchClaim, ResearchSource

# Common English stopwords to ignore in proposition comparison
STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
        "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between",
        "both", "but", "by", "can't", "cannot", "could", "couldn't", "did", "didn't", "do",
        "does", "doesn't", "doing", "don't", "down", "during", "each", "few", "for", "from",
        "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "her",
        "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is",
        "isn't", "it", "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
        "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our",
        "ours", "ourselves", "out", "over", "own", "same", "shan't", "she", "should", "shouldn't",
        "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves",
        "then", "there", "these", "they", "this", "those", "through", "to", "too", "under",
        "until", "up", "very", "was", "wasn't", "we", "were", "weren't", "what", "when", "where",
        "which", "while", "who", "whom", "why", "with", "won't", "would", "wouldn't", "you",
        "your", "yours", "yourself", "yourselves",
    }
)

# Negation and contradiction keywords to prevent merging contradictory claims
NEGATION_TERMS: frozenset[str] = frozenset(
    {
        "not", "no", "never", "neither", "nor", "none", "cannot", "doesn't", "doesnt",
        "don't", "dont", "isn't", "isnt", "aren't", "arent", "wasn't", "wasnt", "weren't",
        "werent", "unlikely", "prevents", "preventing", "disproves", "refutes", "contradicts",
    }
)


def extract_proposition_stems(text: str) -> set[str]:
    """Extract normalized content stems for deterministic proposition comparison."""
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = clean.split()
    stems: set[str] = set()
    for tok in tokens:
        if len(tok) < 3 or tok in STOPWORDS:
            continue
        # Suffix stripping for common English inflections
        stem = tok
        for suffix in (
            "ing", "tion", "tions", "ment", "ments", "ated", "ates", "ate", "ed", "es", "s"
        ):
            if stem.endswith(suffix) and len(stem) - len(suffix) >= 3:
                stem = stem[:-len(suffix)]
                break
        stems.add(stem)
    return stems


def extract_numerical_tokens(text: str) -> set[str]:
    """Extract percentage, currency, or discrete numeric tokens from text."""
    return set(re.findall(r"\b\d+(?:\.\d+)?%|\$\d+(?:\.\d+)?\b|\b\d+\b", text.lower()))


def are_propositions_corroborating(
    text_a: str,
    type_a: ClaimType | str,
    text_b: str,
    type_b: ClaimType | str,
) -> bool:
    """Deterministically check if two propositions corroborate the same factual claim.

    Conservative Policy:
    1. ClaimType must match (FACT to FACT, STATISTIC to STATISTIC, etc.).
    2. Polarity must match: one statement having a negation term and the other not
       is an immediate rejection (contradiction safety).
    3. Numerical tokens must match if present in both statements.
    4. Exact normalized text match => True.
    5. Content stem overlap must be high:
       - Shared stems >= 4, overlap with shorter >= 0.50, and Jaccard similarity >= 0.25
       OR
       - Shared stems >= 5, overlap with shorter >= 0.40, and Jaccard similarity >= 0.18
       OR
       - Shared stems >= 6 and overlap with shorter >= 0.35
    """
    str_type_a = type_a.value if isinstance(type_a, ClaimType) else str(type_a).upper()
    str_type_b = type_b.value if isinstance(type_b, ClaimType) else str(type_b).upper()
    if str_type_a != str_type_b:
        return False

    # Polarity check: negation presence must be identical
    words_a = set(re.findall(r"\b\w+\b", text_a.lower()))
    words_b = set(re.findall(r"\b\w+\b", text_b.lower()))
    has_neg_a = bool(words_a & NEGATION_TERMS)
    has_neg_b = bool(words_b & NEGATION_TERMS)
    if has_neg_a != has_neg_b:
        return False

    # Numerical token check
    nums_a = extract_numerical_tokens(text_a)
    nums_b = extract_numerical_tokens(text_b)
    if nums_a and nums_b and nums_a != nums_b:
        return False

    # Exact or normalized string identity
    norm_a = normalize_claim_text(text_a)
    norm_b = normalize_claim_text(text_b)
    if norm_a == norm_b:
        return True

    # Stem overlap evaluation
    stems_a = extract_proposition_stems(text_a)
    stems_b = extract_proposition_stems(text_b)
    if not stems_a or not stems_b:
        return False

    common = stems_a & stems_b
    min_len = min(len(stems_a), len(stems_b))
    union_len = len(stems_a | stems_b)
    overlap_ratio = len(common) / min_len if min_len else 0.0
    jaccard = len(common) / union_len if union_len else 0.0

    cond_tight = len(common) >= 4 and overlap_ratio >= 0.50 and jaccard >= 0.25
    cond_standard = len(common) >= 5 and overlap_ratio >= 0.40 and jaccard >= 0.18
    cond_deep = len(common) >= 6 and overlap_ratio >= 0.35
    return bool(cond_tight or cond_standard or cond_deep)


def reconcile_source_extractions_into_claims(
    session: AsyncSession,
    existing_claims: list[ResearchClaim],
    extracted_items: list[dict[str, Any]],
    source: ResearchSource,
    channel_id: UUID,
    request_id: UUID,
) -> list[ResearchClaim]:
    """Reconcile extracted items from a source into existing claims or create new ones.

    Idempotent:
    - Never adds duplicate evidence links for the same source and excerpt.
    - Preserves exact source excerpt provenance on every ClaimEvidence item.
    - When an extraction corroborates an existing claim, attaches new ClaimEvidence
      to the canonical claim.
    - When no corroborating claim exists, creates a new ResearchClaim with initial evidence.
    """
    for item in extracted_items:
        claim_text = item["claim_text"].strip()
        claim_type = item["claim_type"]
        type_str = claim_type.value if isinstance(claim_type, ClaimType) else str(claim_type).upper()
        raw_excerpt = item.get("excerpt", claim_text).strip()
        location = item.get("source_location")
        strength = float(item.get("strength_score", 80.0))

        # 1. Check idempotency: has this source already provided evidence for this specific proposition?
        # A single source excerpt can support multiple distinct atomic propositions,
        # but the same source must never link twice to the same canonical proposition.
        already_linked_to_identical_claim = any(
            c.claim_text.strip().lower() == claim_text.lower()
            and any(ev.source_id == source.id for ev in (c.evidence or []))
            for c in existing_claims
        )
        if already_linked_to_identical_claim:
            continue

        # 2. Check if this proposition corroborates an existing canonical claim
        matched_claim: ResearchClaim | None = None
        for c in existing_claims:
            if are_propositions_corroborating(
                text_a=c.claim_text,
                type_a=c.claim_type,
                text_b=claim_text,
                type_b=type_str,
            ):
                matched_claim = c
                break

        # 3. If corroborated, attach evidence to the existing canonical claim
        if matched_claim is not None:
            # Check if this source already has evidence on this matched claim
            has_source_evidence = any(
                ev.source_id == source.id for ev in (matched_claim.evidence or [])
            )
            if not has_source_evidence:
                ev_obj = ClaimEvidence(
                    id=uuid.uuid4(),
                    claim_id=matched_claim.id,
                    source_id=source.id,
                    support_direction=EvidenceDirection.SUPPORTS.value,
                    excerpt=raw_excerpt,
                    source_location=location,
                    strength_score=strength,
                )
                matched_claim.evidence.append(ev_obj)
                session.add(ev_obj)
        else:
            # 4. If no corroboration, create a new canonical ResearchClaim
            c_id = uuid.uuid4()
            claim_obj = ResearchClaim(
                id=c_id,
                research_request_id=request_id,
                channel_id=channel_id,
                claim_text=claim_text,
                normalized_claim=normalize_claim_text(claim_text),
                claim_type=type_str,
                metadata_={"is_atomic": True} if item.get("is_atomic") else {},
            )
            ev_obj = ClaimEvidence(
                id=uuid.uuid4(),
                claim_id=c_id,
                source_id=source.id,
                support_direction=EvidenceDirection.SUPPORTS.value,
                excerpt=raw_excerpt,
                source_location=location,
                strength_score=strength,
            )
            claim_obj.evidence.append(ev_obj)
            session.add(claim_obj)
            existing_claims.append(claim_obj)

    return existing_claims
