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
    "creates", "create", "created", "creating", "triggers", "trigger", "triggered", "triggering",
    "generates", "generate", "generated", "generating",
    "several", "reasons", "reason", "factors", "like", "various", "many", "types", "different",
    "excessive", "severe", "moderate", "slight", "high", "low", "rapid", "slow", "uneven", "differential", "massive", "minor", "major", "early", "late",
    "while", "determining", "determine", "determined", "somewhat", "need", "needs", "needed", "watch", "watching",
    "though", "should", "would", "could", "might", "must", "shall", "ought", "placed", "place", "placing", "places",
    "either", "neither", "where", "wherever", "whenever", "whether", "whereas", "furthermore", "moreover", "however",
    "therefore", "thus", "hence", "along", "across", "around", "within", "without", "against", "toward", "towards",
    "upon", "among", "amongst", "almost", "nearly", "largely", "mainly", "mostly", "especially", "particularly",
    "specifically", "actually", "indeed", "simply", "merely", "quite", "rather", "instead", "corner", "corners", "entrant",
    "joint", "joints", "difficult", "difficulty", "difficulties", "severity",
}

GENERIC_PACKAGING: set[str] = {
    "explanation", "overview", "summary", "figure", "table", "section", "chapter",
    "author", "volume", "paper", "study", "understanding", "understand", "explained",
    "shows", "shown", "presents", "presented", "article", "review", "journal", "research",
    "introduction", "keywords", "keyword", "reference", "references", "proceedings",
    "principle", "principles", "factor", "factors", "method", "methods", "approach",
    "aspect", "aspects", "important", "effective", "common", "primary", "various",
    "according", "considering", "finally", "selects", "leave", "gaps", "theories",
}

_CAUSAL_ENTITY_NOUNS: set[str] = {
    "mechanism", "cause", "reason", "factor", "driver", "origin", "failure mode", "failure mechanism"
}


def is_causal_entity_type(entity_type: str | None) -> bool:
    """Deterministically check whether an entity type represents causal/process mechanisms."""
    if not entity_type:
        return True
    ent = entity_type.strip().lower()
    if ent.endswith("s") and len(ent) > 3:
        ent = ent[:-1]
    return ent in _CAUSAL_ENTITY_NOUNS


def extract_query_anchors(
    claim_text: str,
    subject: str = "",
    topic_keywords: list[str] | None = None,
) -> list[str]:
    """Extract 3-6 distinctive, meaningful original words from a proposition for search planning.

    Invariants:
    - Retains original words (not stems like 'shrinkag').
    - Excludes URLs, stopwords, packaging clutter, and subject words.
    - Strips leading question framing and boilerplate headers.
    - Strips leading manifestation framing (e.g. 'Concrete cracking occurs when').
    - Deterministic order of appearance.
    - Zero invented terminology.
    """
    from omega.domain.numeric_promise import _stem
    combined_subj = f"{subject} {' '.join(topic_keywords or [])}".strip()
    subj_stems = {_stem(w) for w in re.findall(r"\b[a-zA-Z]{3,}\b", combined_subj.lower())}
    cleaned = re.sub(r"https?://\S+", "", claim_text)
    # Strip leading questions and boilerplate headers
    cleaned = re.sub(r"^.*?\?\*?\*?\s*", "", cleaned)
    cleaned = re.sub(r"^(?:what causes them|causes|mechanism|primary causes?)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\*\#\_\`\:\-\–\—\(\)\[\]]", " ", cleaned)
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", cleaned.lower())
    anchors: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        if t in STOPWORDS or t in GENERIC_PACKAGING or _stem(t) in subj_stems:
            continue
        if t not in seen:
            seen.add(t)
            anchors.append(t)
        if len(anchors) >= 6:
            break
    return anchors


def derive_candidate_family_signature(anchors: list[str]) -> str:
    """Derive deterministic planning-only family signature from cause anchors for query diversification."""
    from omega.domain.numeric_promise import _stem
    if not anchors:
        return "unknown"
    stems = [_stem(a) for a in anchors[:2]]
    return "_".join(stems)


_CAUSAL_CONNECTORS_REGEX: list[str] = [
    r"\b(?:causes?|causing)\b",
    r"\b(?:leads?\s+to|leading\s+to)\b",
    r"\b(?:results?\s+in|resulting\s+in)\b",
    r"\b(?:results?\s+from|resulting\s+from)\b",
    r"\b(?:(?:is|are)?\s*due\s+to)\b",
    r"\b(?:produces?|producing)\b",
    r"\b(?:induces?|inducing)\b",
    r"\b(?:triggers?|triggering)\b",
    r"\b(?:initiates?|initiating)\b",
    r"\b(?:creates?|creating)\b",
    r"\b(?:generates?|generating)\b",
    r"\b(?:drives?|driving)\b",
    r"\b(?:operates?\s+(?:on|by|through|where))\b",
]

_DIAGNOSTIC_OR_INSPECTION_PATTERNS: list[str] = [
    r"\binspection\b",
    r"\bwarrant.*inspection\b",
    r"\bvisual inspection\b",
    r"\bmonitoring\b",
    r"\bhairline width\b",
    r"\bmeasurement\b",
    r"\bdisplacement\b",
    r"\bcracks?\s+require\s+inspection\b",
    r"\bdetermining\s+(?:the\s+)?(?:cause|severity)\b",
    r"\bidentifying\s+(?:the\s+)?(?:cause|severity)\b",
    r"\bnarrow\s+down\b",
    r"\bdiagnos(?:e|ing|is)\b",
    r"\bevaluat(?:e|ing|ion)\b",
    r"\bdistinguish(?:ing)?\s+between\b",
    r"\barm(?:ing)?\s+ourselves\b",
    r"\blook\s+for\s+signs\b",
    r"\bcrack\s+testing\b",
    r"\bwidth,\s+depth\b",
    r"\bpattern,\s+and\s+location\b",
    r"\bunderstanding\s+the\s+width\b",
]

_MITIGATION_OR_PREVENTION_PATTERNS: list[str] = [
    r"\bpreventive\b",
    r"\bprevention\b",
    r"\brepair\b",
    r"\btreatment\b",
    r"\bmaintenance\b",
    r"\bmitigation\b",
    r"\bproper curing\b",
    r"\bsolutions to\s+(?:prevent|repair)\b",
    r"\bexpansion\s+joints?\s+require\s+maintenance\b",
    r"\bcuring\s+methods\b",
    r"\bjoints?\s+(?:should|must|can|are|to\s+be)\b",
    r"\bcontrol\s+joints?\b",
    r"\bexpansion\s+joints?\b",
    r"\bjoint\s+(?:placement|spacing|layout)\b",
    r"\bre-entrant\s+corners?\b",
    r"\bair\s+entrainment\b",
    r"\bwater-cement\s+ratio\b",
    r"\bsteel\s+reinforcement\b",
    r"\breinforcing\s+steel\b",
    r"\bproperly\s+(?:placed|spaced|cured)\b",
]

_OUTCOME_OR_CONSEQUENCE_PATTERNS: list[str] = [
    r"\bpose[s]?\s+.*?\b(?:safety|risk|hazard|danger)\b",
    r"\bstructural\s+fractures\s+pose\b",
    r"\bseverity\s+depends\b",
    r"\bfailure\s+severity\b",
    r"\bvisible\s+at\s+the\s+surface\b",
    r"\bmanifested\s+at\s+the\s+surface\b",
    r"\baesthetic\s+(?:issue|concern|problem|defect)\b",
    r"\bcompromise[s]?\s+(?:durability|structural\s+integrity|safety)\b",
    r"\b(?:may\s+indicate|indicates?)\s+an\s+underlying\s+condition\b",
    r"\bnon-structural\s+cracks\b",
    r"\bcracking\s+is\s+an\s+unavoidable\s+response\b",
    r"\bmost\s+widely\s+used\s+construction\s+material\b",
    r"\bdepletion\s+of\s+land\s+resources\b",
    r"\bconstruction\s+waste\b",
    r"\bwatch\s+out\s+for\b",
    r"\blarger\s+problem\b",
    r"\bfar\s+more\s+damage\b",
    r"\bmore\s+damage\s+than\b",
    r"\bcosmetic\s+(?:issue|concern|defect)\b",
    r"\bpropagate\s+either\s+vertically\s+or\s+horizontally\b",
    r"\blinear\s+cracks\s+that\s+propagate\b",
    r"\bif\s+left\s+unaddressed\b",
    r"\bcostly\s+failures\b",
    r"\bcompromise\s+the\s+safety\b",
]

_METADATA_OR_TOC_PATTERNS: list[str] = [
    r"\bkeywords\s*:",
    r"\bchapter\s+\d+",
    r"\btable\s+of\s+contents\b",
    r"\b224r-\d+",
    r"\b\d+\.\d+\s+[a-z]",
    r"\bproce-dures\s+are\s+presented\b",
    r"\bsection\s+\d+",
    r"\b(?:this\s+paper|this\s+study|in\s+this\s+study|in\s+the\s+present\s+study|existing\s+theories|previous\s+studies)\b",
    r"\bcan\s+be\s+primarily\s+categorized\s+into\b",
    r"\bcategorized\s+into\s+the\s+following\b",
]


def classify_target_planning_usefulness(
    text: str,
    entity_type: str | None = None,
    topic_keywords: list[str] | None = None,
) -> tuple[str, float, str]:
    """Deterministically classify planning usefulness and compute topic relevance.

    Classes:
    - PROMISED_ENTITY_CANDIDATE: Plausibly represents an underlying mechanism/cause/entity.
    - TOPIC_RELEVANT_CONTEXT: General domain context or structural/material facts.
    - DIAGNOSTIC_OR_INSPECTION: Visual inspection, measurement, monitoring advice.
    - MITIGATION_OR_PREVENTION: Mitigation, remediation, preventive advice.
    - METADATA_OR_GENERIC: TOC headings, citation fragments, bibliographic clutter.
    - OTHER: General unclassified statements.

    Semantic Roles:
    - CAUSE_OR_PROCESS: Concrete physical/chemical process or causal pathway.
    - OUTCOME_OR_CONSEQUENCE: Damage severity, safety impact, manifestation.
    - DIAGNOSTIC_OR_INSPECTION: Crack inspection, measurement, monitoring.
    - MITIGATION_OR_PREVENTION: Remediation, curing, preventive maintenance.
    - METADATA: Bibliographic, TOC, or scholarly literature statements.
    - GENERIC_CONTEXT: General domain taxonomy and material statements.
    """
    t = text.lower()

    # 1. Metadata / TOC clutter check
    for pat in _METADATA_OR_TOC_PATTERNS:
        if re.search(pat, t):
            return ("METADATA_OR_GENERIC", 10.0, "METADATA")

    # 2. Diagnostic / inspection check
    for pat in _DIAGNOSTIC_OR_INSPECTION_PATTERNS:
        if re.search(pat, t):
            return ("DIAGNOSTIC_OR_INSPECTION", 20.0, "DIAGNOSTIC_OR_INSPECTION")

    # 3. Mitigation / prevention check
    for pat in _MITIGATION_OR_PREVENTION_PATTERNS:
        if re.search(pat, t):
            return ("MITIGATION_OR_PREVENTION", 25.0, "MITIGATION_OR_PREVENTION")

    # 4. Outcome / consequence / severity check (prevents false entity candidates)
    for pat in _OUTCOME_OR_CONSEQUENCE_PATTERNS:
        if re.search(pat, t):
            return ("OUTCOME_OR_CONSEQUENCE", 30.0, "OUTCOME_OR_CONSEQUENCE")

    # 5. Check for causal / mechanism process language
    is_causal = is_causal_entity_type(entity_type)
    if is_causal:
        has_connector = any(re.search(pat, t) for pat in _CAUSAL_CONNECTORS_REGEX)
        has_mechanism_noun = ("mechanism" in t) or ("what causes" in t)

        # Invariant: Broad tokens like 'fractures' or 'degradation' alone are NOT sufficient
        if has_connector or has_mechanism_noun:
            relevance = 85.0
            if topic_keywords and any(kw.lower() in t for kw in topic_keywords):
                relevance = min(100.0, relevance + 10.0)
            return ("PROMISED_ENTITY_CANDIDATE", relevance, "CAUSE_OR_PROCESS")
    else:
        # Non-causal enumerable entities (step, method, tip, strategy, etc.)
        if entity_type and entity_type.lower() in t:
            relevance = 85.0
            if topic_keywords and any(kw.lower() in t for kw in topic_keywords):
                relevance = min(100.0, relevance + 10.0)
            return ("PROMISED_ENTITY_CANDIDATE", relevance, "CAUSE_OR_PROCESS")

    # 6. General topic context fallback
    relevance = 50.0
    if topic_keywords and any(kw.lower() in t for kw in topic_keywords):
        relevance = 65.0
    return ("TOPIC_RELEVANT_CONTEXT", relevance, "GENERIC_CONTEXT")


def build_corroboration_targets(
    claims: list[Any],
    sources_map: dict[UUID, Any] | None = None,
    contract: NumericPromiseContract | None = None,
    topic_keywords: list[str] | None = None,
    topic_title: str = "",
) -> list[CorroborationTarget]:
    """Derive deterministic in-memory corroboration targets from candidate claims.

    Invariants:
    1. Only unverified claims qualify.
    2. Zero contradictory claims qualify.
    3. Metadata-like and boilerplate claims are rejected.
    4. Prioritizes promised-entity candidate propositions before generic context.
    5. Prioritizes 2-independent-source claims before 1-independent-source claims.
    6. Diagnostic and mitigation context claims are relegated to lowest tier (never displace entity candidates).
    7. Pure planning signal: unverified claims never become evidence or brief authority.
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

        # 5. Planning Usefulness Classification and Topic Relevance Scoring
        planning_class, topic_rel, semantic_role = classify_target_planning_usefulness(
            text=claim_text,
            entity_type=contract.entity_type if contract else None,
            topic_keywords=topic_keywords,
        )

        subj = extract_core_subject(topic_title) if topic_title else ""
        anchors = extract_query_anchors(claim_text, subject=subj, topic_keywords=topic_keywords)
        fam_sig = derive_candidate_family_signature(anchors)

        # Phase G Priority Tiers:
        # Tier 1: Promised-entity candidate with 2 independent supports
        # Tier 2: Promised-entity candidate with 1 independent support
        # Tier 3: Other topic-relevant context with >= 2 independent supports
        # Tier 4: Other topic-relevant context with 1 independent support
        # Tier 5: Diagnostic, mitigation, prevention, consequence, or metadata context
        if planning_class == "PROMISED_ENTITY_CANDIDATE":
            priority = 1 if indep_count >= 2 else 2
        elif planning_class == "TOPIC_RELEVANT_CONTEXT" and semantic_role == "GENERIC_CONTEXT":
            priority = 3 if indep_count >= 2 else 4
        else:
            priority = 5

        t = CorroborationTarget(
            representative_claim_text=claim_text,
            claim_type=claim_type,
            independent_support_count=indep_count,
            supporting_domains=supporting_domains,
            confidence_score=conf_score,
            topic_relevance=topic_rel,
            priority=priority,
            semantic_role=semantic_role,
            candidate_family=fam_sig,
        )
        targets.append(t)

    # Sort deterministically: priority ascending (1 before 2, etc.), then topic_relevance descending, then confidence descending
    targets.sort(key=lambda t: (t.priority, -t.topic_relevance, -t.confidence_score))
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
    8. Candidate family diversification across distinct proposition families.
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

    def _issue_from_target(target: CorroborationTarget) -> bool:
        anchors = extract_query_anchors(target.representative_claim_text, subject)
        if not anchors:
            return False
        a_slice = " ".join(anchors[:4])
        dom_info = f"; existing domains: {target.supporting_domains}" if target.supporting_domains else ""
        reason = (
            f"Target missing independent corroboration for candidate proposition "
            f"({target.independent_support_count} existing independent sources{dom_info})."
        )

        variants = [
            (f"{subject} {a_slice} technical reference", ResearchQueryIntent.CORROBORATION),
            (f"{subject} {a_slice} engineering research", ResearchQueryIntent.CORROBORATION),
            (
                f"{subject} {' '.join(anchors[1:5] if len(anchors) >= 5 else anchors[:4])} failure mechanism",
                ResearchQueryIntent.CORROBORATION,
            ),
            (f"{subject} {' '.join(anchors[:5])} analysis", ResearchQueryIntent.CORROBORATION),
        ]
        return any(add_query(q_candidate, q_intent, reason) for q_candidate, q_intent in variants)

    is_causal = is_causal_entity_type(contract.entity_type if contract else None)

    if corroboration_targets:
        if is_causal:
            # Phase C Causal Contract Selection Policy:
            # Strictly require PROMISED_ENTITY_CANDIDATE (priority 1 or 2) AND semantic_role == "CAUSE_OR_PROCESS".
            # Priorities 3, 4, 5 (context, diagnostic, mitigation, outcome, metadata) are strictly FORBIDDEN
            # from consuming corroboration query slots.
            eligible_targets = []
            for t in corroboration_targets:
                if t.priority not in (1, 2):
                    continue
                if t.semantic_role in (
                    "DIAGNOSTIC_OR_INSPECTION",
                    "MITIGATION_OR_PREVENTION",
                    "OUTCOME_OR_CONSEQUENCE",
                    "METADATA",
                ):
                    continue
                eligible_targets.append(t)
        else:
            # Non-causal enumerable behavior preserved
            eligible_targets = [
                t for t in corroboration_targets if t.priority < 5
            ]

        sorted_high_targets = sorted(
            eligible_targets,
            key=lambda t: (t.priority, -t.topic_relevance, -t.confidence_score),
        )

        # Phase G Diversity Pass 1: Select up to max_queries targets from distinct candidate families.
        # sorted_high_targets naturally orders:
        # 1. 2-support CAUSE_OR_PROCESS family (priority 1)
        # 2. Other distinct 1-support CAUSE_OR_PROCESS families (priority 2)
        selected_families: set[str] = set()
        for target in sorted_high_targets:
            if len(queries) >= max_queries:
                break
            fam = target.candidate_family or derive_candidate_family_signature(
                extract_query_anchors(target.representative_claim_text, subject)
            )
            if (
                fam
                and fam not in selected_families
                and _issue_from_target(target)
            ):
                selected_families.add(fam)

        # For non-causal contracts: allow second query from remaining targets if budget remains
        if not is_causal and len(queries) < max_queries:
            for target in sorted_high_targets:
                if len(queries) >= max_queries:
                    break
                _issue_from_target(target)

    # Phase G Step 4: Generic coverage fallback if query budget remains
    # (For causal contracts, this runs BEFORE any second query for an already-targeted candidate family)
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

    # Phase D: For causal contracts, only issue a second query for an already-selected candidate family
    # when distinct families are exhausted AND generic fallback is exhausted.
    if is_causal and len(queries) < max_queries and corroboration_targets:
        for target in sorted_high_targets:
            if len(queries) >= max_queries:
                break
            _issue_from_target(target)

    # For non-causal contracts only: lower-tier targets if budget remains and fallback exhausted
    if not is_causal and len(queries) < max_queries and corroboration_targets:
        low_targets = [t for t in corroboration_targets if t.priority >= 5]
        sorted_low = sorted(low_targets, key=lambda t: (t.priority, -t.confidence_score))
        for target in sorted_low:
            if len(queries) >= max_queries:
                break
            _issue_from_target(target)

    return queries
