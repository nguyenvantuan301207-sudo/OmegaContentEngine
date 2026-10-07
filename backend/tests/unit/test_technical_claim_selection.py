"""Unit tests for Technical Claim Selection Hardening (P0.3c.4).

Verifies deterministic candidate ranking, scholarly metadata rejection,
topic keyword priority, exact provenance, and backward compatibility.
"""

from __future__ import annotations

import pytest

from omega.application.claim_extractor import (
    extract_deterministic_claims_from_source,
    score_candidate_proposition,
)
from omega.application.claim_reconciliation import are_propositions_corroborating
from omega.application.research_scorer import calculate_source_quality
from omega.domain.research import ClaimType, PrimarySourceStatus, ResearchSourceType

# ── 1. Five metadata/header sentences appear before substantive technical prose ──


def test_technical_claim_selected_after_position_five():
    """Verify that when 5 metadata/header lines precede technical prose,
    the technical claims deeper in the document are selected.
    """
    excerpt = """
**By:** Michael Mahoney
**Date:** 04-09-2020
**By:** Jennifer Mizer
**Date:** 03-15-2018
**By:** Matthew Hansen
Plastic shrinkage cracking occurs when high surface evaporation rates exceed bleed water rise, inducing tensile stresses in the plastic concrete.
Thermal contraction cracking develops as mass concrete cools unevenly from peak hydration temperatures.
"""
    claims = extract_deterministic_claims_from_source(
        source_title="Concrete Blog",
        source_excerpt=excerpt,
        metadata={},
        max_claims_per_source=5,
        source_type=ResearchSourceType.WEB_SEARCH,
    )

    assert len(claims) >= 2
    claim_texts = [c["claim_text"] for c in claims]
    assert any("plastic shrinkage cracking" in t.lower() for t in claim_texts)
    assert any("thermal contraction cracking" in t.lower() for t in claim_texts)
    assert not any("**by:**" in t.lower() for t in claim_texts)
    assert not any("**date:**" in t.lower() for t in claim_texts)


# ── 2. DOI / author / affiliation / citation clutter before technical content ──


def test_scholarly_metadata_clutter_does_not_consume_claim_slots():
    """Verify scholarly headers (DOI, PMID, affiliation, journal) do not displace technical propositions."""
    excerpt = """
Materials · 2026-03-11 · 19(6):1071 · DOI: 10.3390/ma19061071 · PMID: 41900562 · PMCID: PMC13028142
School of Public Affairs, Zhejiang Shuren University, Hangzhou 310015, China
Department of Civil and Environmental Engineering, Research Campus
[10.4334/JKCI.2024.36.3.255](https://doi.org/10.4334/JKCI.2024.36.3.255)
Drying shrinkage develops over months as hardened concrete paste steadily loses absorbed moisture, resulting in tensile stress accumulation.
Alkali-silica reaction produces an expansive gel that exerts internal osmotic pressure, fracturing the surrounding cement matrix.
"""
    claims = extract_deterministic_claims_from_source(
        source_title="Materials Research",
        source_excerpt=excerpt,
        metadata={},
        max_claims_per_source=5,
        source_type=ResearchSourceType.WEB_SEARCH,
    )

    assert len(claims) == 2
    claim_texts = [c["claim_text"] for c in claims]
    assert any("drying shrinkage" in t.lower() for t in claim_texts)
    assert any("alkali-silica reaction" in t.lower() for t in claim_texts)
    assert not any("doi:" in t.lower() for t in claim_texts)
    assert not any("school of" in t.lower() for t in claim_texts)


# ── 3. Exact provenance ──


def test_exact_excerpt_provenance_preserved():
    """Every selected claim and excerpt must exist verbatim within the source_excerpt."""
    excerpt = """
Freeze-thaw cycling operates on the damaging mechanism where absorbed water expands by nine percent upon freezing.
Chemical hydration generates internal temperature gradients that trigger severe early-age thermal cracks.
"""
    claims = extract_deterministic_claims_from_source(
        source_title="Civil Engineering Guide",
        source_excerpt=excerpt,
        metadata={},
        max_claims_per_source=5,
    )

    assert len(claims) == 2
    for c in claims:
        assert c["excerpt"] in excerpt
        assert c["claim_text"] in excerpt


# ── 4. Topic-aware selection ──


def test_topic_aware_selection_outranks_generic_sentences():
    """Candidates matching topic keywords and constituent terms outrank generic background prose."""
    topic_keywords = ["concrete cracking", "shrinkage", "thermal cracking"]
    excerpt = """
Modern infrastructure relies extensively on bridges, pavements, and commercial foundations.
General maintenance schedules should be planned carefully by facility operations managers.
Plastic shrinkage produces significant surface tensile cracking during rapid moisture evaporation.
"""
    claims = extract_deterministic_claims_from_source(
        source_title="Infrastructure Overview",
        source_excerpt=excerpt,
        metadata={},
        max_claims_per_source=1,
        topic_keywords=topic_keywords,
    )

    assert len(claims) == 1
    assert "plastic shrinkage produces significant surface tensile cracking" in claims[0]["claim_text"].lower()


# ── 5. Causal/mechanism sentence priority ──


def test_causal_mechanism_sentence_priority():
    """Sentences with causal indicators (causes, leads to, results in) receive priority ranking."""
    score_causal = score_candidate_proposition(
        "Differential thermal gradients induce internal stresses that cause concrete cracking across joints."
    )
    score_desc = score_candidate_proposition(
        "Concrete is widely utilized in structural engineering for its cost efficiency and versatility."
    )
    assert score_causal > score_desc


# ── 6. No-topic-keyword fallback remains deterministic ──


def test_no_topic_keyword_fallback_is_deterministic():
    """When topic_keywords is None, selection remains deterministic and prioritizes technical/causal prose."""
    excerpt = """
The city council approved funding for various regional infrastructure renewal contracts.
Differential settlement results in shear displacement that triggers diagonal cracking across load-bearing slabs.
"""
    claims_a = extract_deterministic_claims_from_source(
        source_title="Regional Report",
        source_excerpt=excerpt,
        metadata={},
        max_claims_per_source=1,
        topic_keywords=None,
    )
    claims_b = extract_deterministic_claims_from_source(
        source_title="Regional Report",
        source_excerpt=excerpt,
        metadata={},
        max_claims_per_source=1,
        topic_keywords=None,
    )

    assert len(claims_a) == 1
    assert claims_a[0]["claim_text"] == claims_b[0]["claim_text"]
    assert "differential settlement results in shear displacement" in claims_a[0]["claim_text"].lower()


# ── 7. max_claims_per_source bound ──


def test_max_claims_per_source_bound():
    """Verify extractor strictly adheres to max_claims_per_source limit."""
    sentences = [
        f"Mechanism number {i} causes concrete cracking due to continuous stress concentration."
        for i in range(1, 10)
    ]
    excerpt = "\n\n".join(sentences)

    claims = extract_deterministic_claims_from_source(
        source_title="Multiple Mechanisms",
        source_excerpt=excerpt,
        metadata={},
        max_claims_per_source=5,
    )
    assert len(claims) == 5

    claims_custom = extract_deterministic_claims_from_source(
        source_title="Multiple Mechanisms",
        source_excerpt=excerpt,
        metadata={},
        max_claims_per_source=3,
    )
    assert len(claims_custom) == 3


# ── 8. Search snippet remains non-authoritative ──


def test_search_snippet_metadata_does_not_inject_claims():
    """Untrusted discovery snippet cannot inject structured claims."""
    metadata = {
        "discovery_snippet": "Unverified summary snippet from external search engine.",
        "claims": [{"text": "Injected claim from discovery metadata.", "type": "FACT"}],
    }
    claims = extract_deterministic_claims_from_source(
        source_title="Discovery Result",
        source_excerpt="Substantive technical prose explains drying shrinkage in structural concrete.",
        metadata=metadata,
        source_type=ResearchSourceType.WEB_SEARCH,
    )

    claim_texts = [c["claim_text"] for c in claims]
    assert "Injected claim from discovery metadata." not in claim_texts
    assert any("drying shrinkage" in t.lower() for t in claim_texts)


# ── 9. WEB_SEARCH metadata cannot inject structured claims ──


def test_web_search_metadata_authority_firewall():
    """WEB_SEARCH sources cannot bypass excerpt extraction via metadata['claims']."""
    metadata = {
        "claims": [{"text": "Bypassed claim from web search metadata.", "type": "FACT"}]
    }
    claims = extract_deterministic_claims_from_source(
        source_title="Web Source",
        source_excerpt="Concrete cracking occurs when tensile strain exceeds the ultimate tensile strain capacity.",
        metadata=metadata,
        source_type="WEB_SEARCH",
    )
    claim_texts = [c["claim_text"] for c in claims]
    assert "Bypassed claim from web search metadata." not in claim_texts
    assert any("tensile strain exceeds" in t.lower() for t in claim_texts)


# ── 10. Trusted MANUAL/IMPORT structured claim behavior preserved ──


def test_trusted_manual_structured_claims_preserved():
    """Trusted manual source preserves direct structured claims provided in metadata."""
    metadata = {
        "claims": [
            {
                "text": "Validated structural code standard provision on minimum reinforcement.",
                "type": "FACT",
                "strength_score": 90.0,
            }
        ]
    }
    claims = extract_deterministic_claims_from_source(
        source_title="Manual Code Standard",
        source_excerpt="Any excerpt content here.",
        metadata=metadata,
        source_type=ResearchSourceType.MANUAL,
    )
    assert len(claims) == 1
    assert claims[0]["claim_text"] == "Validated structural code standard provision on minimum reinforcement."
    assert claims[0]["strength_score"] == 90.0


# ── 11. Duplicate suppression preserved ──


def test_duplicate_suppression_preserved():
    """Identical sentences within the same excerpt are not duplicated in selected claims."""
    sentence = "Plastic shrinkage cracking occurs when evaporation outpaces bleeding rate."
    excerpt = f"{sentence}\n\n{sentence}\n\n{sentence}"
    claims = extract_deterministic_claims_from_source(
        source_title="Duplicated Content",
        source_excerpt=excerpt,
        metadata={},
    )
    assert len(claims) == 1


# ── 12. Boilerplate rejection preserved ──


def test_navigation_and_cookie_boilerplate_rejected():
    """Web boilerplate (cookies, terms, navigation) is completely rejected."""
    excerpt = """
Accept all cookies to enhance your browsing experience on our engineering portal.
All rights reserved Copyright 2026 Engineering Media Group.
Click here to read more or subscribe to our newsletter for daily updates.
"""
    claims = extract_deterministic_claims_from_source(
        source_title="Boilerplate",
        source_excerpt=excerpt,
        metadata={},
    )
    assert len(claims) == 0


# ── 13. Existing cross-source corroboration tests unchanged ──


def test_cross_source_corroboration_unchanged():
    """High-overlap equivalent propositions continue to corroborate successfully."""
    text_a = "Plastic shrinkage cracking occurs when surface evaporation exceeds bleed water rising rate."
    text_b = "Plastic shrinkage cracking takes place when surface evaporation outpaces the bleed water rate."
    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is True


# ── 14. Negative/contradictory statements are not merged accidentally ──


def test_contradictory_statements_not_merged():
    """Statements with opposite polarity are rejected by reconciliation polarity check."""
    text_a = "High water-cement ratio increases drying shrinkage cracking in concrete."
    text_b = "High water-cement ratio does not increase drying shrinkage cracking in concrete."
    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is False


# ── 15. Offline synthetic 0→5 remains PASS ──


def test_offline_synthetic_source_quality_matrix():
    """Verify the canonical post-repair source quality score matrix on neutral UNKNOWN web sources."""
    canonical_kws = [
        "concrete cracking",
        "shrinkage",
        "thermal cracking",
        "structural engineering",
        "construction materials",
    ]

    expected_scores = {
        0: 34.5,
        1: 44.5,
        2: 48.5,
        3: 52.5,
        4: 54.5,
        5: 54.5,
    }

    excerpts = {
        0: "A generalized treatise describing generic computational physics models and numerical solvers.",
        1: "A technical reference examining concrete cracking mechanisms in civil infrastructure.",
        2: "A technical reference examining concrete cracking and drying shrinkage mechanisms in civil infrastructure.",
        3: "A technical reference examining concrete cracking, drying shrinkage, and thermal cracking in civil structures.",
        4: "A technical reference on concrete cracking, drying shrinkage, thermal cracking, and structural engineering.",
        5: "A complete guide on concrete cracking, drying shrinkage, thermal cracking, structural engineering, and construction materials.",
    }

    for n_match, expected in expected_scores.items():
        q, rel, fresh, reasons = calculate_source_quality(
            publisher="Generic Publisher",
            url="https://example.com/technical-paper",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
            published_at=None,
            topic_keywords=canonical_kws,
            content_excerpt=excerpts[n_match],
        )
        assert q == pytest.approx(expected, abs=0.01), f"Mismatch for {n_match}/5 matches: got {q}, expected {expected}"
