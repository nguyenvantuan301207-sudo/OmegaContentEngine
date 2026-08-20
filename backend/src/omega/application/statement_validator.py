"""Statement classification and citation provenance validation."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from omega.domain.content import ClaimUsagePolicy, ContentStatementType

# Regular expressions detecting empirical statistics, percentages, units, and verbatim quotes
NUMERICAL_STAT_REGEX = re.compile(
    r"\b\d+(\.\d+)?%|\b\d+\s*(ms|seconds|minutes|hours|days|years|gb|mb|kb|tb|req/s|ops/s|fps|users|dollars)\b|\$\d+",
    re.IGNORECASE,
)
QUOTE_REGEX = re.compile(r'["“][^"“”]{6,}["”]')


def validate_and_classify_statement(
    statement_text: str,
    suggested_type: str,
    citations: list[dict[str, Any]],
    pinned_brief_id: UUID,
    brief_verified_claim_ids: set[str],
    brief_conflict_claim_ids: set[str],
) -> tuple[ContentStatementType, ClaimUsagePolicy, list[str]]:
    """Validate statement classification against untrusted provider output and claim usage policy.

    Guarantees that statistical, numerical, or quote assertions cannot bypass provenance
    verification by falsely masquerading as 'CREATIVE'.
    """
    reasons: list[str] = []
    text_clean = statement_text.strip()

    has_numerical = bool(NUMERICAL_STAT_REGEX.search(text_clean))
    has_quote = bool(QUOTE_REGEX.search(text_clean))

    effective_type: ContentStatementType
    try:
        effective_type = ContentStatementType(suggested_type)
    except ValueError:
        effective_type = ContentStatementType.CREATIVE

    # Invariant: If statement asserts statistics/numbers/quotes, it cannot be plain CREATIVE/TRANSITION
    if (has_numerical or has_quote) and effective_type in (
        ContentStatementType.CREATIVE,
        ContentStatementType.TRANSITION,
    ):
        effective_type = ContentStatementType.FACTUAL
        reasons.append("FORCED_RECLASSIFICATION_STATISTICAL_CONTENT")

    policy = ClaimUsagePolicy.VERIFIED_FACT

    if effective_type == ContentStatementType.FACTUAL:
        if not citations:
            policy = ClaimUsagePolicy.BLOCKED_NO_EVIDENCE
            reasons.append("FACTUAL_STATEMENT_MISSING_CITATIONS")
        else:
            # Check citations against pinned brief
            for cit in citations:
                cit_brief_id = str(cit.get("research_brief_id"))
                cit_claim_id = str(cit.get("claim_id"))

                if cit_brief_id != str(pinned_brief_id):
                    policy = ClaimUsagePolicy.BLOCKED_UNVERIFIED
                    reasons.append("CITATION_OUTSIDE_PINNED_BRIEF")
                    break

                if cit_claim_id in brief_conflict_claim_ids:
                    policy = ClaimUsagePolicy.BLOCKED_CONFLICT
                    reasons.append("CITATION_REFERENCES_CONFLICTED_CLAIM")
                    break

                if cit_claim_id not in brief_verified_claim_ids:
                    policy = ClaimUsagePolicy.BLOCKED_UNVERIFIED
                    reasons.append("CITATION_REFERENCES_UNVERIFIED_CLAIM")
                    break

    elif effective_type == ContentStatementType.INTERPRETIVE:
        policy = ClaimUsagePolicy.QUALIFIED_ALLOWED

    return effective_type, policy, reasons
