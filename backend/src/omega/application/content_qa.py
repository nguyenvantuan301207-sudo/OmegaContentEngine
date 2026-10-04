"""Local Content QA Engine enforcing canonical rules."""

from __future__ import annotations

import re
from typing import Any

from omega.domain.content import (
    ContentStatementType,
    QARuleCode,
    QASeverity,
    ScriptQAStatus,
)

NUMERICAL_REGEX = re.compile(
    r"\b\d+(\.\d+)?%|\b\d+\s*(ms|seconds|minutes|hours|days|years|gb|mb|kb|tb|req/s|ops/s|fps|users|dollars)\b|\$\d+",
    re.IGNORECASE,
)
QUOTE_REGEX = re.compile(r'["“][^"“”]{6,}["”]')
FACTUAL_ASSERTION_REGEX = re.compile(
    r"\b("
    r"research (shows|proves|indicates|confirms|reveals|demonstrates|finds|found)|"
    r"studies (show|prove|indicate|confirm|reveal|demonstrate|find|found)|"
    r"experiments (show|demonstrate|reveal|prove|confirm)|"
    r"evidence (shows|proves|indicates|confirms|suggests|demonstrates)|"
    r"data (shows|proves|indicates|confirms|demonstrates|reveals)|"
    r"empirically|statistically|scientifically|"
    r"proven (to|that|finding)|"
    r"measured (at|by|to be)|"
    r"laboratory tests?|field tests?|"
    r"physical mechanism|chemical mechanism|biological mechanism|"
    r"documented evidence|clinical trials?"
    r")\b",
    re.IGNORECASE,
)

GENERIC_DISCOURSE_WORDS = {
    "a", "about", "above", "across", "after", "again", "against", "all", "almost", "along",
    "also", "although", "always", "am", "among", "an", "and", "another", "any", "are", "around",
    "as", "at", "back", "be", "became", "because", "become", "becomes", "becoming", "been",
    "before", "began", "begin", "beginning", "begins", "behind", "being", "below", "between",
    "both", "break", "breakdown", "brief", "bring", "brings", "brought", "but", "by", "can",
    "cannot", "case", "certain", "chapter", "clear", "clearly", "come", "comes", "coming",
    "common", "compare", "compared", "conclude", "conclusion", "connect", "connecting",
    "consider", "considering", "could", "crucial", "current", "currently", "deep", "deeper",
    "depth", "describe", "detail", "detailed", "details", "did", "different", "direct",
    "discuss", "discussed", "discussing", "discussion", "dive", "do", "does", "doing", "done",
    "down", "during", "each", "early", "effective", "effectively", "either", "element",
    "elements", "end", "ensure", "ensures", "especially", "essential", "even", "every",
    "everyone", "everything", "evidence", "exact", "examine", "examines", "examining",
    "example", "examples", "explain", "explains", "explaining", "explore", "exploring",
    "factor", "factors", "few", "final", "finally", "find", "finding", "findings", "finds",
    "first", "focus", "focuses", "focusing", "for", "form", "found", "four", "from", "further",
    "general", "generally", "get", "gets", "give", "given", "gives", "giving", "go", "goes",
    "going", "good", "guide", "had", "has", "have", "having", "he", "help", "helps", "her",
    "here", "highlight", "highlights", "him", "his", "how", "however", "i", "if", "important",
    "in", "include", "includes", "including", "individual", "insight", "insights", "into",
    "introduce", "introducing", "introduction", "is", "issue", "issues", "it", "its", "itself",
    "just", "keep", "keeps", "key", "kind", "knew", "know", "knowing", "knowledge", "known",
    "knows", "large", "last", "later", "lead", "leading", "leads", "learn", "learning", "least",
    "leave", "leaves", "led", "let", "lets", "like", "likely", "line", "little", "long",
    "look", "looking", "looks", "made", "main", "major", "make", "makes", "making", "many",
    "matter", "may", "me", "mean", "meaning", "means", "meant", "might", "more", "most",
    "move", "moves", "much", "must", "my", "need", "needed", "needs", "never", "new", "next",
    "no", "nor", "not", "note", "noted", "notes", "nothing", "now", "number", "numbers",
    "observe", "observed", "observes", "obvious", "obviously", "of", "off", "often", "on",
    "once", "one", "ones", "only", "onto", "open", "or", "order", "other", "others", "our",
    "ours", "out", "outline", "over", "overall", "overview", "own", "part", "particular",
    "particularly", "parts", "perspective", "perspectives", "per", "place", "point", "points",
    "possible", "practical", "practice", "practices", "present", "presents", "primary",
    "principle", "principles", "problem", "problems", "proceed", "proceeding", "process",
    "processes", "provide", "provided", "provides", "providing", "question", "questions",
    "quite", "rather", "ready", "real", "really", "reason", "reasons", "recap", "regard",
    "regarding", "relate", "related", "remain", "remains", "remember", "require", "required",
    "requires", "result", "results", "review", "role", "said", "same", "say", "saying", "says",
    "second", "section", "sections", "see", "seeing", "seem", "seemed", "seems", "seen",
    "sees", "set", "several", "shall", "she", "should", "show", "showing", "shown", "shows",
    "significant", "similar", "simple", "simply", "since", "so", "some", "someone", "something",
    "stage", "stages", "stand", "start", "starting", "starts", "step", "steps", "still",
    "structure", "study", "subject", "such", "summary", "synthesize", "synthesis", "take",
    "takeaway", "takeaways", "taken", "takes", "taking", "tell", "telling", "tells", "than",
    "that", "the", "their", "theirs", "them", "theme", "themselves", "then", "there",
    "therefore", "these", "they", "thing", "things", "think", "thinking", "thinks", "third",
    "this", "thorough", "those", "though", "thought", "thoughts", "three", "through",
    "throughout", "time", "times", "to", "today", "together", "told", "too", "took", "topic",
    "towards", "turn", "two", "under", "understand", "understanding", "understands", "understood",
    "until", "up", "upon", "us", "use", "used", "useful", "uses", "using", "valuable", "value",
    "various", "very", "view", "viewer", "viewers", "views", "walk", "walkthrough", "want",
    "wanted", "wants", "was", "watch", "way", "ways", "we", "well", "went", "were", "what",
    "whatever", "when", "where", "whether", "which", "while", "who", "whole", "whom", "whose",
    "why", "will", "with", "within", "without", "word", "words", "work", "working", "works",
    "world", "would", "yet", "you", "your", "yours", "yourself",
}


def stem_word(w: str) -> str:
    w = re.sub(r"[^a-z0-9]", "", w.lower())
    if len(w) > 4:
        for suffix in (
            "ing",
            "tions",
            "tion",
            "ments",
            "ment",
            "ness",
            "able",
            "ible",
            "ies",
            "ied",
            "ed",
            "es",
            "ers",
            "er",
            "al",
            "ic",
            "ly",
        ):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                w = w[: -len(suffix)]
                break
    elif w.endswith("s") and len(w) > 3:
        w = w[:-1]
    return w


def extract_domain_tokens(text: str) -> set[str]:
    raw_words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    tokens: set[str] = set()
    for w in raw_words:
        if w not in GENERIC_DISCOURSE_WORDS:
            stemmed = stem_word(w)
            if stemmed and stemmed not in GENERIC_DISCOURSE_WORDS and len(stemmed) >= 3:
                tokens.add(stemmed)
    return tokens


def run_content_qa_checks(
    script_data: dict[str, Any],
    target_duration_seconds: int,
    dna_dict: dict[str, Any],
    brief_dict: dict[str, Any],
    topic_title: str | None = None,
    topic_summary: str | None = None,
    narrative_plan_dict: dict[str, Any] | None = None,
) -> tuple[ScriptQAStatus, list[dict[str, Any]]]:
    """Execute canonical local QA checks against script draft, Channel DNA, and ResearchBrief."""
    findings: list[dict[str, Any]] = []

    sections = script_data.get("sections", [])
    hook_text = script_data.get("hook_text", "").strip()
    est_duration = script_data.get("estimated_duration_seconds", 0)

    constraints = dna_dict.get("constraints", {})
    forbidden_topics = constraints.get("forbidden_topics", [])
    avoided_vocab = constraints.get("avoided_vocabulary", [])

    conflicts = brief_dict.get("contradictions", [])
    high_conflict_claim_ids = {
        str(c.get("claim_id"))
        for c in conflicts
        if c.get("severity") in ("HIGH", "CRITICAL") and c.get("claim_id")
    }

    # 1. EMPTY_REQUIRED_HOOK_OR_STRUCTURE
    if not hook_text or not sections:
        findings.append(
            {
                "rule_code": QARuleCode.EMPTY_REQUIRED_HOOK_OR_STRUCTURE.value,
                "severity": QASeverity.BLOCKING.value,
                "message": "Script is missing hook text or required narrative body sections.",
                "section_index": None,
                "statement_order": None,
                "details": {"sections_count": len(sections), "has_hook": bool(hook_text)},
            }
        )

    # 2. DURATION_OUT_OF_BOUNDS (±25% tolerance)
    if target_duration_seconds > 0:
        ratio = abs(est_duration - target_duration_seconds) / target_duration_seconds
        if ratio > 0.25:
            findings.append(
                {
                    "rule_code": QARuleCode.DURATION_OUT_OF_BOUNDS.value,
                    "severity": QASeverity.WARNING.value,
                    "message": f"Estimated duration ({est_duration}s) deviates by {ratio * 100:.1f}% from target ({target_duration_seconds}s).",
                    "section_index": None,
                    "statement_order": None,
                    "details": {
                        "estimated_duration": est_duration,
                        "target_duration": target_duration_seconds,
                    },
                }
            )

    # Section & Statement checks
    section_headings: list[str] = []
    full_script_text_parts: list[str] = [
        hook_text,
        script_data.get("closing_text", ""),
        script_data.get("cta_text", ""),
    ]

    for s_idx, sec in enumerate(sections):
        heading = sec.get("heading", "").strip()
        section_headings.append(heading.lower())
        full_script_text_parts.append(sec.get("narration_text", ""))

        statements = sec.get("statements", [])
        for stmt in statements:
            s_order = stmt.get("statement_order")
            text = stmt.get("statement_text", "")
            s_type = stmt.get("statement_type")
            citations = stmt.get("citations", [])

            # 3. FACTUAL_PROVENANCE_MISSING
            is_factual_claim = s_type == ContentStatementType.FACTUAL.value or bool(
                FACTUAL_ASSERTION_REGEX.search(text)
            )
            if is_factual_claim and not citations:
                findings.append(
                    {
                        "rule_code": QARuleCode.FACTUAL_PROVENANCE_MISSING.value,
                        "severity": QASeverity.BLOCKING.value,
                        "message": f"Factual statement lacks provenance citation: '{text[:60]}...'",
                        "section_index": s_idx + 1,
                        "statement_order": s_order,
                        "details": {"statement_text": text},
                    }
                )

            # 4. UNSUPPORTED_STATISTIC
            if NUMERICAL_REGEX.search(text) and not citations:
                findings.append(
                    {
                        "rule_code": QARuleCode.UNSUPPORTED_STATISTIC.value,
                        "severity": QASeverity.BLOCKING.value,
                        "message": f"Statistical/numerical assertion lacks verified citation: '{text[:60]}...'",
                        "section_index": s_idx + 1,
                        "statement_order": s_order,
                        "details": {"statement_text": text},
                    }
                )

            # 5. UNSUPPORTED_QUOTE
            if QUOTE_REGEX.search(text) and not citations:
                findings.append(
                    {
                        "rule_code": QARuleCode.UNSUPPORTED_QUOTE.value,
                        "severity": QASeverity.BLOCKING.value,
                        "message": f"Verbatim quote assertion lacks citation attribution: '{text[:60]}...'",
                        "section_index": s_idx + 1,
                        "statement_order": s_order,
                        "details": {"statement_text": text},
                    }
                )

            # 6. OPEN_HIGH_RESEARCH_CONFLICT & BLOCKED_CLAIM_USED
            for cit in citations:
                cid = str(cit.get("claim_id"))
                if cid in high_conflict_claim_ids:
                    findings.append(
                        {
                            "rule_code": QARuleCode.OPEN_HIGH_RESEARCH_CONFLICT.value,
                            "severity": QASeverity.BLOCKING.value,
                            "message": f"Statement references a claim with open HIGH conflict: claim {cid[:8]}",
                            "section_index": s_idx + 1,
                            "statement_order": s_order,
                            "details": {"claim_id": cid},
                        }
                    )

    full_script_text = " ".join(full_script_text_parts).lower()

    # 7. FORBIDDEN_TOPIC_OR_TERM
    for forbidden in forbidden_topics:
        if forbidden.lower() in full_script_text:
            findings.append(
                {
                    "rule_code": QARuleCode.FORBIDDEN_TOPIC_OR_TERM.value,
                    "severity": QASeverity.BLOCKING.value,
                    "message": f"Script text contains forbidden topic/term from Channel DNA: '{forbidden}'",
                    "section_index": None,
                    "statement_order": None,
                    "details": {"forbidden_term": forbidden},
                }
            )

    # 8. AVOIDED_VOCABULARY
    for avoided in avoided_vocab:
        # Match whole word
        pattern = rf"\b{re.escape(avoided.lower())}\b"
        if re.search(pattern, full_script_text):
            findings.append(
                {
                    "rule_code": QARuleCode.AVOIDED_VOCABULARY.value,
                    "severity": QASeverity.WARNING.value,
                    "message": f"Script uses avoided vocabulary term: '{avoided}'",
                    "section_index": None,
                    "statement_order": None,
                    "details": {"avoided_word": avoided},
                }
            )

    # 9. DUPLICATE_OR_REPEATED_SECTION
    if len(section_headings) != len(set(section_headings)):
        findings.append(
            {
                "rule_code": QARuleCode.DUPLICATE_OR_REPEATED_SECTION.value,
                "severity": QASeverity.WARNING.value,
                "message": "Script contains duplicate section headings.",
                "section_index": None,
                "statement_order": None,
                "details": {"headings": section_headings},
            }
        )

    # 10. TOPIC_AUTHORITY_MISMATCH
    effective_title = (
        topic_title
        or script_data.get("title")
        or script_data.get("topic_title")
        or brief_dict.get("topic_title")
        or brief_dict.get("title")
        or ""
    )
    effective_summary = (
        topic_summary
        or script_data.get("topic_summary")
        or brief_dict.get("topic_summary")
        or brief_dict.get("summary")
        or ""
    )

    authority_corpus_parts: list[str] = []
    if effective_title:
        authority_corpus_parts.append(str(effective_title))
    if effective_summary:
        authority_corpus_parts.append(str(effective_summary))
    for vc in brief_dict.get("verified_claims", []):
        c_text = vc.get("text") or vc.get("claim_text") or vc.get("statement") or ""
        if c_text:
            authority_corpus_parts.append(str(c_text))
    plan_data = (
        narrative_plan_dict
        or script_data.get("narrative_plan")
        or script_data.get("narrative_plan_dict")
        or {}
    )
    if isinstance(plan_data, dict):
        for p_sec in plan_data.get("sections", []):
            if p_sec.get("objective"):
                authority_corpus_parts.append(str(p_sec["objective"]))
            for kp in p_sec.get("key_information", []) or p_sec.get("key_points", []):
                authority_corpus_parts.append(str(kp))

    authority_text = " ".join(authority_corpus_parts)
    authority_tokens = extract_domain_tokens(authority_text)
    title_tokens = extract_domain_tokens(effective_title)

    if len(authority_tokens) >= 3:
        for s_idx, sec in enumerate(sections):
            heading = sec.get("heading", "")
            narration = sec.get("narration_text", "")
            sec_text = f"{heading} {narration}"
            sec_tokens = extract_domain_tokens(sec_text)

            if len(sec_tokens) >= 8:
                overlap = sec_tokens.intersection(authority_tokens)
                overlap_ratio = len(overlap) / len(sec_tokens)
                non_title_overlap = overlap - title_tokens

                is_mismatch = False
                if len(overlap) == 0:
                    is_mismatch = True
                elif len(non_title_overlap) == 0 and overlap_ratio < 0.15 and len(sec_tokens) >= 12:
                    is_mismatch = True

                if is_mismatch:
                    unmatched_sample = sorted(list(sec_tokens - overlap))[:8]
                    findings.append(
                        {
                            "rule_code": QARuleCode.TOPIC_AUTHORITY_MISMATCH.value,
                            "severity": QASeverity.BLOCKING.value,
                            "message": (
                                f"Section {s_idx + 1} ('{heading}') introduces unrelated domain vocabulary "
                                f"not grounded in topic or research authority: {unmatched_sample}"
                            ),
                            "section_index": s_idx + 1,
                            "statement_order": None,
                            "details": {
                                "heading": heading,
                                "section_domain_token_count": len(sec_tokens),
                                "authority_match_count": len(overlap),
                                "unmatched_sample": unmatched_sample,
                            },
                        }
                    )

    # Determine overall status
    has_blocking = any(
        f["severity"] in (QASeverity.BLOCKING.value, QASeverity.ERROR.value) for f in findings
    )
    has_warning = any(f["severity"] == QASeverity.WARNING.value for f in findings)

    status = (
        ScriptQAStatus.BLOCKED
        if has_blocking
        else (ScriptQAStatus.PASSED_WITH_WARNINGS if has_warning else ScriptQAStatus.PASSED)
    )

    return status, findings
