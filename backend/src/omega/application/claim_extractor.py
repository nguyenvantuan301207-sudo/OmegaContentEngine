"""Deterministic Claim Extractor for OMEGA-005 & P0.3c.4.

Processes structured source claims and rule-based prose extractions.
Guarantees zero speculative hallucinations, strict boilerplate and scholarly
metadata rejection, deterministic candidate ranking before truncation, and
exact source excerpt traceability.
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

SCHOLARLY_METADATA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:[\*\-\•]|\d+[\.\)])?\s*(?:\*\*|\b)?(?:by|author[s]?|date|published|received|accepted|revised|edited by|issn|isbn|pmid|pmcid|doi)\b\s*:",
        re.I,
    ),
    re.compile(
        r"\b(?:materials|journal|volume|issue|proceedings)[\s·]+(?:\d{4}|\d+\(\d+\)|\d{4}-\d{2}-\d{2})",
        re.I,
    ),
    re.compile(
        r"\bdoi:\s*10\.\d+.*?\b(?:pmid|pmcid):\s*\d+",
        re.I,
    ),
    re.compile(r"^(?:https?://|doi\.org/|10\.\d{4,9}/)\S+$", re.I),
    re.compile(
        r"^(?:school of|department of|faculty of|institute of|college of|university)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:visit (?:the )?[a-z0-9_-]+ (?:store|university|website)|please visit|click here|read more)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:checkget notified|checksave papers|version of our website|how to cite)\b",
        re.I,
    ),
)

CAUSAL_INDICATORS = re.compile(
    r"\b(?:causes?|caused by|causing|leads? to|results? (?:in|from)|due to|because(?: of)?|mechanism[s]?|triggers?|produces?|induces?|develops? when|occurs? when)\b",
    re.I,
)

TECHNICAL_PROCESS_INDICATORS = re.compile(
    r"\b(?:stress(?:es)?|strain[s]?|tensile|hydration|expansion|shrinkage|thermal|freeze-thaw|corrosion|compressive|shear|deformation|micro-?cracks?|gradient|pressure|porosity|chemical reaction|moisture|elasticity)\b",
    re.I,
)


def _is_boilerplate(text: str) -> bool:
    """Check if candidate text contains web navigation or legal boilerplate."""
    return any(p.search(text) for p in BOILERPLATE_PATTERNS)


def _is_scholarly_metadata_or_clutter(text: str) -> bool:
    """Check if candidate text is bibliographic metadata, author/date headers, or UI clutter."""
    t = text.strip()
    for p in SCHOLARLY_METADATA_PATTERNS:
        if p.search(t):
            return True

    # Standalone markdown image or link lines without proposition
    if re.match(r"^!\[.*?\]\(.*?\)$", t):
        return True
    if re.match(r"^\[.*?\]\(.*?\)$", t) and len(t) < 120:
        return True

    # Dominant URL ratio
    urls = re.findall(r"https?://\S+|/[a-z0-9_/-]+", t)
    url_len = sum(len(u) for u in urls)
    return bool(url_len > 0 and (url_len / len(t)) > 0.40 and len(t) < 150)


def _classify_claim_type(statement: str) -> ClaimType:
    """Classify statement into a discrete ClaimType."""
    if re.search(r"\b\d+(\.\d+)?%\b|\b\$\d+", statement):
        return ClaimType.STATISTIC
    if statement.startswith('"') and statement.endswith('"'):
        return ClaimType.QUOTE
    return ClaimType.FACT


def score_candidate_proposition(
    statement: str,
    topic_keywords: list[str] | None = None,
) -> float:
    """Deterministically score a candidate claim for technical and topic relevance."""
    score = 0.0
    s_lower = statement.lower()

    # 1. Causal and mechanism reasoning language (+30)
    if CAUSAL_INDICATORS.search(s_lower):
        score += 30.0

    # 2. Technical process and material behavior vocabulary (up to +25)
    tech_matches = len(TECHNICAL_PROCESS_INDICATORS.findall(s_lower))
    if tech_matches > 0:
        score += min(25.0, tech_matches * 8.0)

    # 3. Topic keyword matching (up to +35)
    if topic_keywords:
        kw_matched = 0
        for kw in topic_keywords:
            kw_l = kw.strip().lower()
            if not kw_l:
                continue
            if kw_l in s_lower:
                kw_matched += 2
                continue
            terms = [t for t in re.findall(r"\w+", kw_l) if len(t) >= 3]
            if len(terms) > 1 and all(t in s_lower for t in terms):
                kw_matched += 1
        score += min(35.0, kw_matched * 10.0)

    # 4. Informative sentence length (+10 or -10)
    length = len(statement)
    if 60 <= length <= 350:
        score += 10.0
    elif length < 40:
        score -= 10.0

    # 5. Penalties for bare headings and high markdown markup
    if statement.startswith("#"):
        score -= 15.0
    if "[" in statement and "](" in statement:
        score -= 10.0

    return score


def _split_conjunction_list(raw_list: str) -> list[str]:
    """Deterministically parse an explicit list of items from a list phrase."""
    # Check for roman numerals / numbered list: i) ... ii) ... iii) or 1) ... 2) ...
    if re.search(r"(?:[iIvVxX]+\)|\d+[\.\)])\s*", raw_list):
        parts = re.split(r"(?:[iIvVxX]+\)|\d+[\.\)])\s*", raw_list)
        clean_parts = []
        for p in parts:
            p_c = re.sub(r"[\;\,\.]", "", p).strip()
            p_c = re.sub(r"\(.*?\)", "", p_c).strip()
            p_c = re.sub(r"^(?:and|or)\s+", "", p_c).strip()
            if p_c and len(p_c.split()) <= 6:
                clean_parts.append(p_c)
        if len(clean_parts) >= 2:
            return clean_parts

    delims = re.split(r",\s*(?:and|or)\s+|;\s*(?:and|or)\s+|\s+(?:and|or)\s+|,\s*|;\s*", raw_list)
    items = []
    for d in delims:
        d_clean = d.strip()
        if not d_clean:
            continue
        d_clean = re.sub(r"^[\*\-\•\s]+|[\*\-\•\s]+$", "", d_clean)
        words = d_clean.split()
        if 1 <= len(words) <= 6:
            items.append(d_clean)
        else:
            return []
    return items if len(items) >= 2 else []


def split_explicit_enumeration(sentence: str) -> list[str]:
    """Deterministically extract atomic causal propositions from explicit source enumerations.

    Guarantees:
    - Zero semantic invention: uses only lexical material in source sentence + existing connector words.
    - Preserves exact source excerpt provenance.
    - Leaves ambiguous or non-causal prose unsplit.
    """
    s = sentence.strip()
    # Strip leading markdown decoration or questions
    s_clean = re.sub(r"^(?:#+|\*\*|Why\s+does\s+[\w\s]+\?\s*\*?\*?)\s*", "", s).strip()

    # Pattern 1: <EFFECT> (is/are/can be)? (caused by | due to | results from | resulting from) [intro words]? <LIST>
    pat1 = re.compile(
        r"^(?P<effect>.+?)\s+(?P<conn>(?:is|are|can\s+be)?\s*(?:caused\s+by|due\s+to|results?\s+from|resulting\s+from))\s*(?:(?:several|multiple|different|various)\s+(?:reasons|causes|factors|mechanisms)\s*(?:like|such\s+as)?:?|(?:reasons|causes|factors|mechanisms)\s*(?:like|such\s+as):?|like|such\s+as)?\s*(?P<list>[^.]+)\.?$",
        re.I,
    )
    m1 = pat1.match(s_clean)
    if m1:
        effect = m1.group("effect").strip()
        conn = m1.group("conn").strip()
        raw_list = m1.group("list").strip()
        items = _split_conjunction_list(raw_list)
        if len(items) >= 2:
            return [f"{effect} {conn} {item}" for item in items]

    # Pattern 2: <LIST> (causes? | leads? to | results? in) <EFFECT>
    pat2 = re.compile(
        r"^(?P<list>.+?)\s+(?P<conn>(?:causes?|leads?\s+to|results?\s+in))\s+(?P<effect>[^.]+)\.?$",
        re.I,
    )
    m2 = pat2.match(s_clean)
    if m2:
        raw_list = m2.group("list").strip()
        conn = m2.group("conn").strip()
        effect = m2.group("effect").strip()
        items = _split_conjunction_list(raw_list)
        if len(items) >= 2 and not any(len(it.split()) > 6 for it in items):
            verb = "causes" if conn in ("cause", "causes") else conn
            return [f"{item} {verb} {effect}" for item in items]

    # Pattern 3: <EFFECT> (mechanisms|causes|reasons|factors|types) (include|are|can be categorised into) <LIST>
    pat3 = re.compile(
        r"^(?P<effect>.+?)\s+(?P<noun>mechanisms?|causes?|reasons?|factors?|types?)\s+(?P<verb>include|includes|are|can\s+be\s+categorised\s+into(?:\s+the\s+following\s+types)?):?\s*(?P<list>[^.]+)\.?$",
        re.I,
    )
    m3 = pat3.match(s_clean)
    if m3:
        effect = m3.group("effect").strip()
        noun = m3.group("noun").strip()
        verb = m3.group("verb").strip()
        raw_list = m3.group("list").strip()
        items = _split_conjunction_list(raw_list)
        if len(items) >= 2:
            return [f"{effect} {noun} {verb} {item}" for item in items]

    return [s]


def extract_deterministic_claims_from_source(
    source_title: str,
    source_excerpt: str,
    metadata: dict[str, Any],
    max_claims_per_source: int = 5,
    source_type: Any = None,
    topic_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract claims deterministically from structured metadata or real technical prose.

    V3 Pipeline:
    1. If structured claims in metadata for trusted sources (MANUAL/IMPORT/SEED), validate and return them.
       Untrusted discovery/WEB_SEARCH metadata cannot inject claims (Authority Firewall).
    2. Collect all candidate propositions from bullets and prose across the entire source excerpt.
    3. Filter out boilerplate, scholarly metadata, DOIs, affiliations, and navigation links.
    4. Deterministically score and rank candidates based on causal indicators, technical vocabulary,
       and topic keyword relevance.
    5. Deduplicate and select the top max_claims_per_source propositions with exact excerpt provenance.

    Returns a list of dicts with: claim_text, claim_type, excerpt, strength_score, source_location.
    """
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
        results: list[dict[str, Any]] = []
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
                atoms = split_explicit_enumeration(text)
                if len(atoms) >= 2:
                    for atom in atoms:
                        results.append(
                            {
                                "claim_text": atom,
                                "claim_type": _classify_claim_type(atom),
                                "excerpt": excerpt,
                                "strength_score": min(max(strength, 0.0), 100.0),
                                "source_location": item.get("source_location"),
                                "is_atomic": True,
                            }
                        )
                else:
                    results.append(
                        {
                            "claim_text": text,
                            "claim_type": claim_type,
                            "excerpt": excerpt,
                            "strength_score": min(max(strength, 0.0), 100.0),
                            "source_location": item.get("source_location"),
                            "is_atomic": False,
                        }
                    )
                if len(results) >= max_claims_per_source:
                    return results
        if results:
            return results

    if not source_excerpt or len(source_excerpt.strip()) < 15:
        return []

    candidate_pool: list[dict[str, Any]] = []

    # 2. Check for explicit bullet points or numbered facts
    lines = source_excerpt.splitlines()
    for line in lines:
        line_clean = line.strip()
        match = re.match(r"^(?:[\*\-\•]|\d+[\.\)])\s+(.*)$", line_clean)
        if match:
            statement = match.group(1).strip()
            if (
                len(statement) >= 20
                and not _is_boilerplate(statement)
                and not _is_scholarly_metadata_or_clutter(statement)
            ):
                norm = normalize_claim_text(statement)
                if norm not in seen_normalized:
                    seen_normalized.add(norm)
                    exact_excerpt = statement if statement in source_excerpt else line_clean
                    candidate_pool.append(
                        {
                            "statement": statement,
                            "excerpt": exact_excerpt,
                            "score": score_candidate_proposition(statement, topic_keywords),
                        }
                    )

    # 3. Unstructured Prose Extraction: Paragraph & sentence segmentation
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", source_excerpt) if p.strip()]
    if not paragraphs:
        paragraphs = [source_excerpt.strip()]

    for para in paragraphs:
        raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", para) if s.strip()]
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
            # Boilerplate and scholarly metadata rejection
            if _is_boilerplate(s_clean) or _is_scholarly_metadata_or_clutter(s_clean):
                continue

            norm = normalize_claim_text(s_clean)
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)

            # Exact excerpt provenance check: verify s_clean exists in source_excerpt
            if s_clean not in source_excerpt:
                continue

            candidate_pool.append(
                {
                    "statement": s_clean,
                    "excerpt": s_clean,
                    "score": score_candidate_proposition(s_clean, topic_keywords),
                }
            )

    if not candidate_pool:
        return []

    # 4. Deterministic ranking: score descending, then length descending, then statement text
    candidate_pool.sort(
        key=lambda c: (-c["score"], -len(c["statement"]), c["statement"])
    )

    top_candidates = candidate_pool[:max_claims_per_source]
    extracted_claims: list[dict[str, Any]] = []
    for c in top_candidates:
        stmt = c["statement"]
        ex = c["excerpt"]
        atoms = split_explicit_enumeration(stmt)
        if len(atoms) >= 2:
            for atom in atoms:
                extracted_claims.append(
                    {
                        "claim_text": atom,
                        "claim_type": _classify_claim_type(atom),
                        "excerpt": ex,
                        "strength_score": 80.0,
                        "source_location": None,
                        "is_atomic": True,
                    }
                )
        else:
            extracted_claims.append(
                {
                    "claim_text": stmt,
                    "claim_type": _classify_claim_type(stmt),
                    "excerpt": ex,
                    "strength_score": 80.0,
                    "source_location": None,
                    "is_atomic": False,
                }
            )
    return extracted_claims


def normalize_claim_text(claim_text: str) -> str:
    """Normalize claim text for deduplication and conflict comparison."""
    return normalize_source_text(claim_text)
