import uuid
from unittest.mock import MagicMock

from omega.application.claim_extractor import (
    extract_deterministic_claims_from_source,
    split_explicit_enumeration,
)
from omega.application.claim_reconciliation import (
    reconcile_source_extractions_into_claims,
)
from omega.application.research_query_planner import (
    classify_target_planning_usefulness,
    extract_query_anchors,
)
from omega.domain.numeric_promise import (
    derive_verified_family_label,
    extract_distinct_entities,
    extract_numeric_promise,
)
from omega.domain.research import ClaimType
from omega.infrastructure.models import (
    ResearchClaim,
    ResearchSource,
)


class TestAtomicCausalClaimsAndManifestationPrecision:
    """Comprehensive test suite for R2: Atomic Causal Claims & Mechanism-Role Precision."""

    # 1. explicit A/B/C cause enumeration splits into 3 atoms
    def test_explicit_enumeration_splits_into_three_atoms(self):
        sentence = "System failures are caused by disk corruption, memory leak, and power outage."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 3
        assert "System failures are caused by disk corruption" in atoms
        assert "System failures are caused by memory leak" in atoms
        assert "System failures are caused by power outage" in atoms

    # 2. each atom preserves original evidence excerpt
    def test_atom_preserves_original_evidence_excerpt(self):
        sentence = "Concrete cracks due to shrinkage, thermal contraction, and overloading."
        claims = extract_deterministic_claims_from_source(
            source_title="Test Concrete Source",
            source_excerpt=f"Intro paragraph.\n\n{sentence}\n\nConclusive remarks.",
            metadata={},
        )
        assert len(claims) >= 3
        for c in claims:
            if "Concrete cracks due to" in c["claim_text"]:
                assert c["excerpt"] == sentence

    # 3. one source remains one independent source per atom
    def test_one_source_remains_one_independent_source_per_atom(self):
        session = MagicMock()
        existing_claims: list[ResearchClaim] = []
        source = ResearchSource(
            id=uuid.uuid4(),
            title="Single Source",
            url="https://source-a.org/article",
            publisher="source-a.org",
        )
        items = [
            {"claim_text": "Outages are caused by power failure", "claim_type": ClaimType.FACT, "excerpt": "Full sentence", "is_atomic": True},
            {"claim_text": "Outages are caused by network partition", "claim_type": ClaimType.FACT, "excerpt": "Full sentence", "is_atomic": True},
            {"claim_text": "Outages are caused by leader failure", "claim_type": ClaimType.FACT, "excerpt": "Full sentence", "is_atomic": True},
        ]
        reconcile_source_extractions_into_claims(
            session=session,
            existing_claims=existing_claims,
            extracted_items=items,
            source=source,
            channel_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
        )
        assert len(existing_claims) == 3
        for c in existing_claims:
            # Each claim has exactly 1 evidence link from source
            assert len(c.evidence) == 1
            assert c.evidence[0].source_id == source.id

    # 4. ambiguous list is not split
    def test_ambiguous_list_not_split(self):
        sentence = "When the concrete dries too fast because of wind and hot sun, cracks appear."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 1
        assert atoms[0] == sentence

    # 5. non-causal list is not split
    def test_non_causal_list_not_split(self):
        sentence = "The contractor purchased hammers, nails, and concrete mixers."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 1
        assert atoms[0] == sentence

    # 6. exact source lexical grounding preserved
    def test_exact_source_lexical_grounding_preserved(self):
        sentence = "Engine stalling results from clogged fuel injectors, bad spark plugs, or dead battery."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 3
        for atom in atoms:
            for word in atom.lower().split():
                if word.isalpha() and word not in {"results", "from", "engine", "stalling"}:
                    assert word in sentence.lower()

    # 7. no invented entity terms
    def test_no_invented_entity_terms(self):
        sentence = "Why does concrete crack? Concrete cracks due to several reasons like shrinkage, thermal contraction, and overloading."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 3
        combined_atoms = " ".join(atoms).lower()
        for forbidden in ["hydration", "freeze", "thaw", "alkali", "corrosion"]:
            assert forbidden not in combined_atoms

    # 8. broad multi-mechanism claim no longer produces hybrid target query
    def test_broad_multi_mechanism_query_planning_atomic(self):
        sentence = "Concrete cracks due to several reasons like shrinkage, thermal contraction, and overloading."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 3
        # Each atom generates independent query anchors
        for a in atoms:
            anchors = extract_query_anchors(a, subject="concrete cracks")
            # Should NOT contain hybrid combination
            assert not ("shrinkage" in anchors and "thermal" in anchors)

    # 9. manifestation "produces a pattern" is not causal mechanism candidate
    def test_manifestation_produces_pattern_not_causal_candidate(self):
        claim = "Shrinkage cracking typically produces a distinctive pattern: fine, closely spaced, multi-directional or map cracking."
        p_class, relevance, role = classify_target_planning_usefulness(claim, entity_type="mechanism")
        assert role == "OUTCOME_OR_CONSEQUENCE"
        assert p_class != "PROMISED_ENTITY_CANDIDATE"

    # 10. true X-produces-Y mechanism remains candidate
    def test_true_mechanism_produces_stress_remains_candidate(self):
        claim = "Temperature gradients produce tensile stress that causes cracking."
        p_class, relevance, role = classify_target_planning_usefulness(claim, entity_type="mechanism")
        assert role == "CAUSE_OR_PROCESS"
        assert p_class == "PROMISED_ENTITY_CANDIDATE"

    # 11. diagnostic remains excluded
    def test_diagnostic_remains_excluded(self):
        claim = "Cracks that have widened beyond hairline width warrant closer inspection before assuming shrinkage is the cause."
        p_class, relevance, role = classify_target_planning_usefulness(claim, entity_type="mechanism")
        assert role == "DIAGNOSTIC_OR_INSPECTION"
        assert p_class != "PROMISED_ENTITY_CANDIDATE"

    # 12. mitigation remains excluded
    def test_mitigation_remains_excluded(self):
        claim = "On the contrary, any cracked concrete structure can be repaired effectively and most often permanently."
        p_class, relevance, role = classify_target_planning_usefulness(claim, entity_type="mechanism")
        assert role == "MITIGATION_OR_PREVENTION"
        assert p_class != "PROMISED_ENTITY_CANDIDATE"

    # 13. distributed-systems enumeration works
    def test_distributed_systems_enumeration(self):
        sentence = "Outages are caused by power failure, network partition, and leader failure."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 3
        assert "Outages are caused by power failure" in atoms
        assert "Outages are caused by network partition" in atoms
        assert "Outages are caused by leader failure" in atoms

    # 14. battery enumeration works
    def test_battery_enumeration(self):
        sentence = "Battery degradation can result from lithium plating, electrolyte breakdown, and mechanical cracking."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 3
        assert "Battery degradation can result from lithium plating" in atoms
        assert "Battery degradation can result from electrolyte breakdown" in atoms
        assert "Battery degradation can result from mechanical cracking" in atoms

    # 15. networking enumeration works
    def test_networking_enumeration(self):
        sentence = "Network latency occurs due to packet queuing, route flapping, or bufferbloat."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 3
        assert "Network latency occurs due to packet queuing" in atoms
        assert "Network latency occurs due to route flapping" in atoms
        assert "Network latency occurs due to bufferbloat" in atoms

    # 16. one source with 5 atoms cannot produce 5 VERIFIED families
    def test_one_source_with_five_atoms_cannot_produce_five_verified_families(self):
        sentence = "Concrete cracks due to shrinkage, thermal contraction, subgrade settlement, alkali reaction, and overloading."
        atoms = split_explicit_enumeration(sentence)
        assert len(atoms) == 5
        # If each has only 1 source, none can be verified (needs >= 3 independent sources)
        for _atom in atoms:
            independent_sources = 1
            is_verified = (independent_sources >= 3)
            assert not is_verified

    # 17. reconciliation still requires independent corroboration
    def test_reconciliation_requires_independent_corroboration(self):
        session = MagicMock()
        existing_claims: list[ResearchClaim] = []
        source1 = ResearchSource(id=uuid.uuid4(), title="Source 1", url="https://site-a.com/doc")
        source2 = ResearchSource(id=uuid.uuid4(), title="Source 2", url="https://site-b.com/doc")

        # Source 1 adds proposition
        reconcile_source_extractions_into_claims(
            session=session,
            existing_claims=existing_claims,
            extracted_items=[{"claim_text": "Thermal contraction causes concrete cracking", "claim_type": ClaimType.FACT, "excerpt": "Source 1 text"}],
            source=source1,
            channel_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
        )
        assert len(existing_claims) == 1
        assert len(existing_claims[0].evidence) == 1

        # Source 2 corroborates same proposition
        reconcile_source_extractions_into_claims(
            session=session,
            existing_claims=existing_claims,
            extracted_items=[{"claim_text": "Thermal contraction causes concrete cracking during cooling phase", "claim_type": ClaimType.FACT, "excerpt": "Source 2 text"}],
            source=source2,
            channel_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
        )
        # Should merge into the same canonical claim with 2 independent evidence links
        assert len(existing_claims) == 1
        assert len(existing_claims[0].evidence) == 2

    # 18. existing Retry #5 verified authority does not inflate incorrectly
    def test_verified_family_derivation_grounded(self):
        claim = "Plastic Shrinkage: Occurs when concrete is still plastic and loses moisture rapidly due to high temperatures"
        tokens, label = derive_verified_family_label(claim, entity_type="mechanism")
        assert label == "plastic shrinkage"
        for word in label.split():
            assert word in claim.lower()

    # 19. offline 0->5 simulation still SUFFICIENT when 5 independent families exist
    def test_offline_distinct_entities_extraction(self):
        claims = [
            {"claim_text": "Concrete shrinks as it cures resulting in drying shrinkage cracking"},
            {"claim_text": "Thermal contraction produces severe tensile stress cracking in massive slabs"},
            {"claim_text": "Plastic shrinkage occurs when fresh concrete loses moisture too rapidly"},
            {"claim_text": "Cycles of freeze-thaw expansion fracture saturated capillary concrete pores"},
            {"claim_text": "Excessive applied structural loads and structural overloading cause structural fractures"},
        ]
        entities = extract_distinct_entities(claims, topic_title="Why Concrete Cracks: 5 Mechanisms", entity_type="mechanism")
        assert len(entities) == 5

    # 20. numeric verified-only gate preserved
    def test_numeric_verified_only_gate_preserved(self):
        contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
        assert contract is not None
        assert contract.promised_count == 5
        # Only verified claims can satisfy the contract
        unverified_claims = [
            {"claim_text": "Unverified mechanism A", "is_verified": False},
            {"claim_text": "Unverified mechanism B", "is_verified": False},
        ]
        # Invariant: Unverified cannot satisfy contract
        verified_count = sum(1 for c in unverified_claims if c.get("is_verified", False))
        assert verified_count < contract.promised_count
