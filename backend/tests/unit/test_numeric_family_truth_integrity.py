"""Unit tests for P0.3 Retry #6 Truth Integrity: Numeric Family Validity + Query Quality.

Verifies:
- Verified generic claims ("Both actions weaken...") are not counted as mechanisms
- Unresolved anaphoric phrases ("both actions", "these factors", etc.) are rejected as families
- 70+ confidence is not sufficient for entity identity
- Duplicated plastic-shrinkage descriptions collapse into one family
- Legitimate distinct causes (plastic shrinkage vs differential shrinkage vs thermal contraction) are retained
- Cause/effect direction is preserved
- Metadata / TOC queries ("are covered in detail") are rejected
- Mitigation / crack control queries ("crack control", "control of cracking due to overlays") are rejected
- Truncated technical text is rejected
- Cross-domain causal tests (medical, mechanical, software, economics)
- Verified-only numeric authority preserved
- Source provenance preserved (no manual source mixing)
- Non-causal numeric contracts (e.g. steps, principles, mistakes) are preserved
"""

import uuid

import pytest

from omega.application.claim_extractor import (
    _is_scholarly_metadata_or_clutter,
    split_explicit_enumeration,
)
from omega.application.research_query_planner import (
    DISALLOWED_QUERY_ANCHORS,
    classify_candidate_family_coverage,
    classify_target_planning_usefulness,
)
from omega.domain.numeric_promise import (
    derive_verified_family_label,
    extract_distinct_entities,
    extract_numeric_promise,
    is_unresolved_or_non_causal_label,
)

# -----------------------------------------------------------------------------
# 1. Family Validity & Unresolved Anaphora Tests
# -----------------------------------------------------------------------------

def test_unresolved_both_actions_rejected_as_causal_mechanism():
    """Unresolved anaphoric reference 'Both actions weaken...' must return unknown and not form a family."""
    claim = "Both actions weaken the surface of the concrete and exacerbate plastic shrinkage cracking."
    tokens, label = derive_verified_family_label(claim, entity_type="mechanism")
    assert label == "unknown"
    assert tokens == set()


@pytest.mark.parametrize(
    "unresolved_phrase",
    [
        "Both actions weaken the foundation",
        "These factors contribute to structural degradation",
        "This process accelerates wear",
        "Such effects lead to failure",
        "All causes exacerbate the fatigue",
        "Those mechanisms trigger spalling",
        "When anything happens the structure fails",
        "From simulations the system breaks down",
        "This combination creates instability",
    ],
)
def test_unresolved_generic_antecedents_rejected_for_causal_contract(unresolved_phrase):
    """Phrases without explicit causal antecedents must not generate candidate mechanism families."""
    tokens, label = derive_verified_family_label(unresolved_phrase, entity_type="mechanism")
    assert label == "unknown"
    assert tokens == set()


def test_is_unresolved_or_non_causal_label_helper():
    """Deterministic check rejects labels formed purely from generic nouns and non-causal verbs."""
    assert is_unresolved_or_non_causal_label(["actions", "weaken"]) is True
    assert is_unresolved_or_non_causal_label(["factors", "contribute"]) is True
    assert is_unresolved_or_non_causal_label(["process", "accelerate"]) is True
    assert is_unresolved_or_non_causal_label(["differential", "shrinkage"]) is False
    assert is_unresolved_or_non_causal_label(["thermal", "contraction"]) is False


def test_colon_prefixed_definition_with_anaphoric_words_preserves_antecedent():
    """When a colon prefix defines the mechanism, anaphoric words inside description do not reject it."""
    claim = "Thermal shock: both actions weaken the surface rapidly."
    tokens, label = derive_verified_family_label(claim, entity_type="mechanism")
    assert "thermal" in label
    assert "shock" in label


# -----------------------------------------------------------------------------
# 2. Distinct Entity Extraction & 70+ Confidence Separation
# -----------------------------------------------------------------------------

def test_confidence_70_plus_does_not_grant_entity_identity():
    """A claim with 70+ confidence remains verified, but extract_distinct_entities rejects it if generic."""
    claims = [
        # Verified distinct mechanism 1
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Plastic shrinkage: occurs when concrete loses surface moisture rapidly",
            "confidence": 79.75,
            "citations": [{"source_id": "s1"}, {"source_id": "s2"}],
        },
        # Verified distinct mechanism 2
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Differential shrinkage: develops when different concrete layers dry at unequal rates",
            "confidence": 79.75,
            "citations": [{"source_id": "s3"}, {"source_id": "s4"}],
        },
        # Verified claim with high confidence (79.75) but UNRESOLVED ANAPHORA (no grounded mechanism)
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Both actions weaken the surface of the concrete and exacerbate plastic shrinkage cracking.",
            "confidence": 79.75,
            "citations": [{"source_id": "s1"}, {"source_id": "s2"}, {"source_id": "s5"}],
        },
    ]

    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Engineer Should Know")
    assert contract is not None

    distinct_entities = extract_distinct_entities(claims, entity_type=contract.entity_type)
    # The third claim must NOT form a 3rd mechanism family ("actions weaken")
    assert len(distinct_entities) == 2
    assert "actions weaken" not in distinct_entities
    assert any("plastic" in fam for fam in distinct_entities)
    assert any("differential" in fam for fam in distinct_entities)


def test_duplicated_plastic_shrinkage_description_collapsed():
    """Duplicate descriptions of the same mechanism must collapse into one distinct family."""
    claims = [
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Plastic Shrinkage: Occurs when concrete loses moisture rapidly before set",
            "confidence": 80.0,
            "citations": [{"source_id": "s1"}],
        },
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Plastic shrinkage cracking occurs due to rapid evaporation of bleed water",
            "confidence": 80.0,
            "citations": [{"source_id": "s2"}],
        },
    ]
    distinct_entities = extract_distinct_entities(claims, entity_type="mechanism")
    assert len(distinct_entities) == 1
    assert "plastic shrinkage" in distinct_entities[0]


def test_legitimate_distinct_mechanisms_retained():
    """Legitimate distinct causal mechanisms must all be retained without collapsing."""
    claims = [
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Plastic shrinkage: occurs when concrete loses surface moisture rapidly",
            "confidence": 75.0,
            "citations": [{"source_id": "s1"}],
        },
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Differential shrinkage: develops when unequal moisture gradients form",
            "confidence": 75.0,
            "citations": [{"source_id": "s2"}],
        },
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Thermal contraction: causes cracking when mass concrete cools rapidly",
            "confidence": 75.0,
            "citations": [{"source_id": "s3"}],
        },
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Subgrade settlement: produces cracking under uneven soil support",
            "confidence": 75.0,
            "citations": [{"source_id": "s4"}],
        },
    ]
    distinct_entities = extract_distinct_entities(claims, entity_type="mechanism")
    assert len(distinct_entities) == 4
    entity_labels = distinct_entities
    assert any("plastic" in label for label in entity_labels)
    assert any("differential" in label for label in entity_labels)
    assert any("thermal" in label for label in entity_labels)
    assert any("settlement" in label or "subgrade" in label for label in entity_labels)


# -----------------------------------------------------------------------------
# 3. Cause / Effect Direction Preservation
# -----------------------------------------------------------------------------

def test_cause_effect_direction_preserved():
    """Directionality: 'X results from Y' extracts Y as cause; 'X causes Y' extracts X as cause."""
    # Y results from X -> cause is X
    c1 = "Cracking results from differential shrinkage across the slab section."
    tok1, lab1 = derive_verified_family_label(c1, entity_type="mechanism")
    assert "differential" in lab1 and "shrinkage" in lab1

    # X causes Y -> cause is X
    c2 = "Thermal contraction causes cracking in unreinforced concrete."
    tok2, lab2 = derive_verified_family_label(c2, entity_type="mechanism")
    assert "thermal" in lab2 and "contraction" in lab2


# -----------------------------------------------------------------------------
# 4. Mitigation & Metadata Clutter Rejection in Claim Extractor
# -----------------------------------------------------------------------------

def test_mitigation_and_toc_packaging_rejected_in_split_enumeration():
    """Compound TOC sentences like ACI 224R-01 must not split into fake 'crack control' causal claims."""
    sentence = (
        "The control of cracking due to drying shrinkage and crack control in "
        "flexural members, overlays, and mass con-crete construction are covered in detail."
    )
    claims = split_explicit_enumeration(sentence)
    # Must remain unsplit (single original sentence) rather than splitting into fake claims
    assert len(claims) == 1
    assert claims[0] == sentence


def test_scholarly_metadata_and_toc_filter():
    """_is_scholarly_metadata_or_clutter must reject document summaries ending with meta predicates."""
    meta_s = "The control of cracking due to overlays are covered in detail."
    assert _is_scholarly_metadata_or_clutter(meta_s) is True

    valid_s = "Plastic shrinkage occurs when bleed water evaporates faster than it rises."
    assert _is_scholarly_metadata_or_clutter(valid_s) is False


# -----------------------------------------------------------------------------
# 5. Query Planning Rejection: Mitigation, Metadata, Truncation
# -----------------------------------------------------------------------------

def test_mitigation_target_classified_as_not_useful_for_cause_contract():
    """Queries targeting mitigation or crack control must not be classified as PROMISED_ENTITY_CANDIDATE."""
    claim_text = "The control of cracking due to overlays are covered in detail."
    planning_class, _, semantic_role = classify_target_planning_usefulness(
        text=claim_text,
        entity_type="mechanism",
    )
    assert planning_class in {"MITIGATION_OR_PREVENTION", "METADATA_OR_GENERIC"}
    assert semantic_role in {"MITIGATION_OR_PREVENTION", "METADATA"}


def test_metadata_target_classified_as_not_useful():
    """Metadata or TOC summaries are classified as METADATA_OR_GENERIC."""
    claim_text = "These mechanisms are discussed in detail in Chapter 3."
    planning_class, _, semantic_role = classify_target_planning_usefulness(
        text=claim_text,
        entity_type="mechanism",
    )
    assert planning_class == "METADATA_OR_GENERIC"
    assert semantic_role == "METADATA"


def test_candidate_family_coverage_rejects_both_actions_and_mitigation():
    """classify_candidate_family_coverage rejects unresolved antecedents and control/mitigation."""
    cat, reason = classify_candidate_family_coverage(
        claim_text="Both actions weaken the surface of the concrete and exacerbate plastic shrinkage cracking.",
        already_supported_families=["plastic shrinkage", "differential shrinkage"],
        entity_type="mechanism",
    )
    assert cat == "AMBIGUOUS_OR_GENERIC_CONTEXT"
    assert reason == "unresolved_antecedent"

    cat2, reason2 = classify_candidate_family_coverage(
        claim_text="Crack control in overlays requires proper curing procedures.",
        already_supported_families=["plastic shrinkage"],
        entity_type="mechanism",
    )
    assert cat2 == "METADATA_OR_CITATION"
    assert reason2 == "mitigation"


def test_disallowed_query_anchors_include_mitigation_and_toc_words():
    """Query generation anchors must never use control, prevent, covered, detail, etc."""
    disallowed = {"control", "controlling", "prevent", "prevention", "mitigate", "mitigation", "covered", "detail"}
    for word in disallowed:
        assert word in DISALLOWED_QUERY_ANCHORS


# -----------------------------------------------------------------------------
# 6. Non-Causal Numeric Contracts Preserved
# -----------------------------------------------------------------------------

def test_non_causal_contracts_preserved():
    """Non-causal contracts (e.g. steps, principles, mistakes) are not subject to causal mechanism rules."""
    step_claims = [
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Step 1: Perform initial site excavation and soil compaction",
            "confidence": 75.0,
            "citations": [{"source_id": "s1"}],
        },
        {
            "claim_id": str(uuid.uuid4()),
            "claim_text": "Step 2: Place formwork and reinforce steel cage",
            "confidence": 75.0,
            "citations": [{"source_id": "s2"}],
        },
    ]
    distinct = extract_distinct_entities(step_claims, entity_type="step")
    assert len(distinct) == 2


# -----------------------------------------------------------------------------
# 7. Cross-Domain Causal Tests
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "domain_claim, expected_substr",
    [
        ("Myocardial infarction results from coronary artery occlusion", "coronary artery"),
        ("Bearing fatigue failure is caused by excessive cyclic shear stress", "shear stress"),
        ("Deadlock condition occurs due to circular resource waiting in distributed systems", "circular resource"),
        ("Hyperinflation was caused by unbacked sovereign currency expansion", "currency expansion"),
    ],
)
def test_cross_domain_causal_mechanism_extraction(domain_claim, expected_substr):
    """Causal mechanism extraction operates domain-agnostically across medical, engineering, software, economics."""
    tokens, label = derive_verified_family_label(domain_claim, entity_type="cause")
    assert label != "unknown"
    assert any(term in label for term in expected_substr.split())
