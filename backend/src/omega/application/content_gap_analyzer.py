"""Local Content Gap Analyzer for Channel Topic Intelligence.

Evaluates how well a topic candidate addresses underserved content pillars
based solely on historical TopicMemory distribution without external network crawling.
"""

from __future__ import annotations

from typing import Any

from omega.application.duplicate_detector import normalize_text


def analyze_content_gap(
    candidate_title: str,
    candidate_keywords: list[str],
    content_pillars: list[str],
    memory_records: list[dict[str, Any]] | list[Any],
) -> tuple[float, list[str]]:
    """Calculate content gap score (0.0 to 100.0) and explainability reasons.

    Pillars with fewer historical memory associations represent content gaps and receive
    a higher score when a candidate addresses them.
    """
    if not content_pillars:
        return 50.0, []

    norm_pillars = [normalize_text(p) for p in content_pillars if normalize_text(p)]
    if not norm_pillars:
        return 50.0, []

    # Count historical memory topics associated with each pillar
    pillar_counts: dict[str, int] = {p: 0 for p in norm_pillars}

    for record in memory_records:
        canon_topic = normalize_text(
            record.canonical_topic
            if hasattr(record, "canonical_topic")
            else record.get("canonical_topic", "")
        )
        rec_keywords = [
            normalize_text(k)
            for k in (
                record.keywords if hasattr(record, "keywords") else record.get("keywords", [])
            )
        ]
        rec_text = canon_topic + " " + " ".join(rec_keywords)

        for p in norm_pillars:
            if p in rec_text or any(token in p for token in canon_topic.split()):
                pillar_counts[p] += 1

    # Determine matched pillar for this candidate
    cand_norm_text = (
        normalize_text(candidate_title)
        + " "
        + " ".join(normalize_text(k) for k in candidate_keywords)
    )
    matched_pillars = [
        p
        for p in norm_pillars
        if p in cand_norm_text or any(token in cand_norm_text for token in p.split())
    ]

    total_memories = len(memory_records)
    avg_per_pillar = total_memories / max(len(norm_pillars), 1)

    reasons: list[str] = []

    if not matched_pillars:
        # Does not directly address a defined pillar
        return 40.0, []

    best_gap_score = 50.0
    for p in matched_pillars:
        count = pillar_counts.get(p, 0)
        if count == 0:
            # Underserved pillar with zero coverage
            score = 100.0
            reasons.append(f"UNDERSERVED_CONTENT_PILLAR: {p} (0 previous topics)")
        elif count < avg_per_pillar:
            # Below average coverage
            score = 85.0
            reasons.append(f"BELOW_AVERAGE_PILLAR_COVERAGE: {p} ({count} topics)")
        elif count == int(avg_per_pillar):
            score = 65.0
        else:
            # Saturated pillar
            score = 45.0
            reasons.append(f"SATURATED_PILLAR: {p} ({count} topics)")

        if score > best_gap_score:
            best_gap_score = score

    return best_gap_score, reasons
