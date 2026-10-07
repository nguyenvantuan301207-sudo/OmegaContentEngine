"""Deterministic Topic-Grounded Research Query Planner (P0.3).

Generates intent-grounded, inspectable research queries without hard-coding
topic-specific answers or fabricating unverified entities.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from omega.domain.numeric_promise import NumericPromiseContract
from omega.domain.research import ClaimType, CorroborationTarget, ResearchQuery, ResearchQueryIntent

_RHETORICAL_PREFIXES: list[str] = [
    "why ",
    "how ",
    "what is ",
    "what are ",
    "the truth about ",
    "a guide to ",
    "guide to ",
    "understanding ",
    "mastering ",
    "everything you need to know about ",
]

_PACKAGING_SUFFIXES: list[str] = [
    "every civil engineer should understand",
    "every engineer should understand",
    "every engineer should know",
    "what you need to know",
    "explained",
    "a comprehensive guide",
    "best practices",
    "and why it matters",
    "and how to prevent it",
    "in 2026",
    "for beginners",
]


def extract_core_subject(topic_title: str) -> str:
    """Extract clean domain subject from topic title by removing rhetorical packaging."""
    cleaned = topic_title.strip()

    # If title has a colon or em-dash, examine parts
    parts = re.split(r"[:\—\–\-]", cleaned)
    primary = parts[0].strip()

    # If primary has leading rhetorical words, remove them
    primary_lower = primary.lower()
    for prefix in _RHETORICAL_PREFIXES:
        if primary_lower.startswith(prefix):
            primary = primary[len(prefix):].strip()
            break

    # If the second part has specific topic content and first was short, check both
    if len(parts) > 1 and len(primary.split()) <= 2:
        # Check if secondary has substantive subject words
        secondary = parts[1].strip()
        sec_lower = secondary.lower()
        for suffix in _PACKAGING_SUFFIXES:
            if suffix in sec_lower:
                sec_lower = sec_lower.replace(suffix, "").strip()
        # Remove numbers and enumerable nouns from secondary
        sec_clean = re.sub(r"\b\d+\s+[a-zA-Z]+\b", "", sec_lower).strip()
        if sec_clean and len(sec_clean.split()) >= 2:
            return f"{primary} {sec_clean}".strip().lower()

    # Remove any trailing packaging from primary
    prim_lower = primary.lower()
    for suffix in _PACKAGING_SUFFIXES:
        if suffix in prim_lower:
            prim_lower = prim_lower.replace(suffix, "").strip()

    # Strip any numeric patterns e.g. "5 Mechanisms"
    prim_clean = re.sub(r"\b\d+\s+[a-zA-Z]+\b", "", prim_lower).strip()
    prim_clean = re.sub(r"\s+", " ", prim_clean)

    return prim_clean or primary.lower()


def _get_plural_entity(entity_type: str) -> str:
    """Normalize entity type to its plural form for search queries."""
    ent = entity_type.strip().lower()
    if ent.endswith("s"):
        return ent
    if ent.endswith("y"):
        return ent[:-1] + "ies"
    return ent + "s"


STOPWORDS: set[str] = {
    "the", "and", "for", "with", "that", "this", "from", "when", "what", "which",
    "have", "has", "had", "been", "were", "was", "are", "is", "not", "can", "will",
    "these", "those", "there", "their", "they", "our", "out", "all", "any", "such",
    "into", "than", "more", "most", "also", "due", "other", "hand", "some", "well",
    "over", "under", "between", "before", "after", "during", "through", "about",
    "cause", "causes", "caused", "causing", "them", "because", "why", "how", "does",
    "makes", "make", "made", "take", "takes", "taken", "part", "both", "each",
    "occurs", "occur", "occurred", "occurring", "still", "already", "again",
    "often", "typically", "usually", "general", "generally", "depending",
    "depends", "result", "results", "resulting", "lead", "leads", "leading",
    "produces", "produce", "produced", "producing", "induces", "induce", "induced",
}

GENERIC_PACKAGING: set[str] = {
    "explanation", "overview", "summary", "figure", "table", "section", "chapter",
    "author", "volume", "paper", "study", "understanding", "understand", "explained",
    "shows", "shown", "presents", "presented", "article", "review", "journal", "research",
    "introduction", "keywords", "keyword", "reference", "references", "proceedings",
    "principle", "principles", "factor", "factors", "method", "methods", "approach",
    "aspect", "aspects", "important", "effective", "common", "primary", "various",
}


def extract_query_anchors(claim_text: str, subject: str = "") -> list[str]:
    """Extract 3-6 distinctive, meaningful original words from a proposition for search planning.

    Invariants:
    - Retains original words (not stems like 'shrinkag').
    - Excludes URLs, stopwords, packaging clutter, and subject words.
    - Deterministic order of appearance.
    - Zero invented terminology.
    """
    subj_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", subject.lower()))
    cleaned = re.sub(r"https?://\S+", "", claim_text)
    cleaned = re.sub(r"[\*\#\_\`\:\-\–\—\(\)\[\]]", " ", cleaned)
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", cleaned.lower())
    anchors: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        if t in STOPWORDS or t in GENERIC_PACKAGING or t in subj_words:
            continue
        if t not in seen:
            seen.add(t)
            anchors.append(t)
        if len(anchors) >= 6:
            break
    return anchors


def build_corroboration_targets(
    claims: list[Any],
    sources_map: dict[UUID, Any] | None = None,
) -> list[CorroborationTarget]:
    """Derive deterministic in-memory corroboration targets from candidate claims.

    Invariants:
    1. Only unverified claims qualify.
    2. Zero contradictory claims qualify.
    3. Metadata-like and boilerplate claims are rejected.
    4. Prioritizes 2-independent-source claims before 1-independent-source claims.
    5. Pure planning signal: unverified claims never become evidence or brief authority.
    """
    from omega.application.claim_extractor import _is_boilerplate, _is_scholarly_metadata_or_clutter

    targets: list[CorroborationTarget] = []
    seen_texts: set[str] = set()

    for c in claims:
        # 1. Firewall: Already verified claims must not become corroboration targets
        is_verified = getattr(c, "is_verified", False)
        if is_verified:
            continue

        # 2. Safety: Reject contradictory claims
        contradicting = getattr(c, "contradicting_sources_count", 0) or 0
        if contradicting > 0:
            continue

        claim_text = (
            getattr(c, "claim_text", "")
            if hasattr(c, "claim_text")
            else str(c.get("claim_text", "") if isinstance(c, dict) else "")
        )
        claim_text = claim_text.strip()
        if not claim_text or len(claim_text) < 15:
            continue

        # 3. Reject boilerplate / bibliographic metadata
        if _is_boilerplate(claim_text) or _is_scholarly_metadata_or_clutter(claim_text):
            continue

        norm_text = re.sub(r"\s+", " ", claim_text.lower())
        if norm_text in seen_texts:
            continue
        seen_texts.add(norm_text)

        indep_count = getattr(c, "independent_sources_count", 1) or 1
        conf_score = float(getattr(c, "confidence_score", 0.0) or 0.0)
        raw_type = getattr(c, "claim_type", ClaimType.FACT)
        claim_type = ClaimType.FACT
        if isinstance(raw_type, ClaimType):
            claim_type = raw_type
        elif isinstance(raw_type, str):
            try:
                claim_type = ClaimType(raw_type)
            except ValueError:
                claim_type = ClaimType.FACT

        # 4. Resolve supporting domains from evidence provenance
        supporting_domains: list[str] = []
        evidence_list = (
            getattr(c, "evidence", [])
            if hasattr(c, "evidence")
            else (c.get("evidence", []) if isinstance(c, dict) else [])
        ) or []
        if sources_map:
            for ev in evidence_list:
                sid = (
                    getattr(ev, "source_id", None)
                    if hasattr(ev, "source_id")
                    else (ev.get("source_id") if isinstance(ev, dict) else None)
                )
                if sid in sources_map:
                    src_obj = sources_map[sid]
                    url = (
                        getattr(src_obj, "url", "")
                        if hasattr(src_obj, "url")
                        else (src_obj.get("url", "") if isinstance(src_obj, dict) else "")
                    )
                    if url:
                        try:
                            from urllib.parse import urlparse
                            dom = urlparse(url).netloc.lower()
                            if dom and dom not in supporting_domains:
                                supporting_domains.append(dom)
                        except Exception:
                            pass

        # 5. Priority rule: 2 independent sources (priority 1) > 1 independent source (priority 2)
        if indep_count >= 2:
            priority = 1
        elif indep_count == 1:
            priority = 2
        else:
            priority = 3

        targets.append(
            CorroborationTarget(
                representative_claim_text=claim_text,
                claim_type=claim_type,
                independent_support_count=indep_count,
                supporting_domains=supporting_domains,
                confidence_score=conf_score,
                priority=priority,
            )
        )

    # Sort deterministically: priority ascending (1 before 2), then confidence descending
    targets.sort(key=lambda t: (t.priority, -t.confidence_score))
    return targets


def plan_research_queries(
    topic_title: str,
    contract: NumericPromiseContract | None = None,
    round_number: int = 1,
    already_supported_families: list[str] | None = None,
    max_queries: int = 3,
    corroboration_targets: list[CorroborationTarget] | None = None,
    issued_query_texts: set[str] | list[str] | None = None,
) -> list[ResearchQuery]:
    """Deterministically plan diversified, inspectable research queries.

    Invariants:
    1. Grounded in canonical topic subject.
    2. Zero hard-coded mechanism or answer names.
    3. Diversified across distinct intent families.
    4. Deduplicated and bounded by max_queries.
    5. Execution-local query history deduplication across rounds.
    6. Round 1 remains broad foundational discovery.
    7. Round 2+ prioritizes missing independent corroboration (2-source before 1-source).
    """
    subject = extract_core_subject(topic_title)

    queries: list[ResearchQuery] = []
    seen_texts: set[str] = set()
    seen_history: set[str] = {
        re.sub(r"\s+", " ", q.strip().lower()) for q in (issued_query_texts or [])
    }

    def add_query(text: str, intent: ResearchQueryIntent, reason: str) -> bool:
        norm_text = re.sub(r"\s+", " ", text.strip().lower())
        if (
            norm_text
            and norm_text not in seen_texts
            and norm_text not in seen_history
            and len(queries) < max_queries
        ):
            seen_texts.add(norm_text)
            queries.append(
                ResearchQuery(
                    query_text=norm_text,
                    intent=intent,
                    reason=reason,
                    round_number=round_number,
                )
            )
            return True
        return False

    # Round 1: Broad foundational discovery
    if round_number == 1:
        if contract and contract.promised_count and contract.promised_count > 0:
            plural_entity = _get_plural_entity(contract.entity_type)
            singular_entity = contract.entity_type.lower()
            add_query(
                f"{subject} {plural_entity} overview",
                ResearchQueryIntent.OVERVIEW,
                f"Establish authoritative overview of {plural_entity} for {subject}.",
            )
            add_query(
                f"primary {plural_entity} of {subject} engineering guide",
                ResearchQueryIntent.MECHANISMS
                if singular_entity == "mechanism"
                else ResearchQueryIntent.TYPES,
                f"Target core technical {plural_entity} and classifications.",
            )
            add_query(
                f"types of {plural_entity} in {subject} technical standards",
                ResearchQueryIntent.TECHNICAL_REFERENCE,
                "Identify standardized classifications and technical specifications.",
            )
        else:
            add_query(
                f"{subject} overview and fundamentals",
                ResearchQueryIntent.OVERVIEW,
                f"Establish core foundational principles for {subject}.",
            )
            add_query(
                f"{subject} technical explanation and engineering principles",
                ResearchQueryIntent.TECHNICAL_REFERENCE,
                f"Target technical and engineering mechanisms for {subject}.",
            )
            add_query(
                f"{subject} analysis and practical applications",
                ResearchQueryIntent.TYPES,
                f"Explore practical applications and domain taxonomy for {subject}.",
            )
        return queries

    # Round 2+: Prioritize corroboration deficits when available
    plural_entity = (
        _get_plural_entity(contract.entity_type)
        if contract and contract.promised_count
        else "aspects"
    )

    if corroboration_targets:
        # Sort targets by priority (2-independent before 1-independent), then confidence
        sorted_targets = sorted(corroboration_targets, key=lambda t: (t.priority, -t.confidence_score))
        for target in sorted_targets:
            if len(queries) >= max_queries:
                break
            anchors = extract_query_anchors(target.representative_claim_text, subject)
            if not anchors:
                continue
            a_slice = " ".join(anchors[:4])
            dom_info = f"; existing domains: {target.supporting_domains}" if target.supporting_domains else ""
            reason = (
                f"Target missing independent corroboration for candidate proposition "
                f"({target.independent_support_count} existing independent sources{dom_info})."
            )

            # Deterministic formulation variants grounded in the claim to prevent cross-round repetition
            variants = [
                (f"{subject} {a_slice} technical reference", ResearchQueryIntent.CORROBORATION),
                (f"{subject} {a_slice} engineering research", ResearchQueryIntent.CORROBORATION),
                (
                    f"{subject} {' '.join(anchors[1:5] if len(anchors) >= 5 else anchors[:4])} failure mechanism",
                    ResearchQueryIntent.CORROBORATION,
                ),
                (f"{subject} {' '.join(anchors[:5])} analysis", ResearchQueryIntent.CORROBORATION),
            ]
            for q_candidate, q_intent in variants:
                if add_query(q_candidate, q_intent, reason):
                    break

    # If query budget remains, add generic diversification query to expand coverage
    if len(queries) < max_queries:
        if contract and contract.promised_count and contract.promised_count > 0:
            add_query(
                f"additional {plural_entity} and failure modes in {subject}",
                ResearchQueryIntent.ADDITIONAL_COVERAGE,
                f"Expand coverage to discover distinct unrepresented {plural_entity} beyond initial findings.",
            )
            add_query(
                f"environmental and material {plural_entity} of {subject}",
                ResearchQueryIntent.CAUSES,
                f"Investigate environmental, chemical, and physical {plural_entity}.",
            )
            add_query(
                f"complete taxonomy and classification of {plural_entity} in {subject}",
                ResearchQueryIntent.TYPES,
                "Seek comprehensive multi-category taxonomy for complete enumeration.",
            )
            add_query(
                f"{subject} {plural_entity} empirical case studies and failure analysis",
                ResearchQueryIntent.ADDITIONAL_COVERAGE,
                f"Investigate specialized empirical evidence and failure investigations for {subject}.",
            )
            add_query(
                f"prevention and mitigation of {plural_entity} in {subject}",
                ResearchQueryIntent.CAUSES,
                f"Target mitigation techniques and causal pathways for {subject}.",
            )
        else:
            add_query(
                f"{subject} advanced technical guide and failure analysis",
                ResearchQueryIntent.ADDITIONAL_COVERAGE,
                f"Expand into advanced and specialized aspects of {subject}.",
            )
            add_query(
                f"{subject} industry standards and best practices",
                ResearchQueryIntent.TECHNICAL_REFERENCE,
                "Investigate industry guidelines and empirical references.",
            )
            add_query(
                f"{subject} comprehensive empirical research",
                ResearchQueryIntent.ADDITIONAL_COVERAGE,
                f"Seek additional empirical literature on {subject}.",
            )

    return queries
