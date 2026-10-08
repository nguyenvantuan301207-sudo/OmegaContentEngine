from types import SimpleNamespace
from uuid import uuid4

import pytest

from omega.application.research_query_planner import (
    build_corroboration_targets,
    classify_target_planning_usefulness,
    plan_research_queries,
)
from omega.domain.causal_direction import CausalDirection, extract_causal_assertions
from omega.domain.numeric_promise import extract_numeric_promise
from omega.domain.research import ResearchQueryIntent


@pytest.mark.parametrize(
    ("topic", "cause", "consequence"),
    [
        (
            "Why Concrete Cracks: 5 Mechanisms",
            "Thermal contraction causes cracking.",
            "Cracking reduces building performance.",
        ),
        (
            "Why Outages Occur: 5 Mechanisms",
            "Network partitions cause outages.",
            "Outages reduce service availability.",
        ),
        (
            "Why Battery Capacity Loss Occurs: 5 Mechanisms",
            "Lithium plating causes capacity loss.",
            "Capacity loss shortens operating time.",
        ),
        (
            "Why Packet Loss Occurs: 5 Mechanisms",
            "Congestion causes packet loss.",
            "Packet loss causes application timeouts.",
        ),
    ],
)
def test_topic_direction_across_domains(topic, cause, consequence):
    assert (
        extract_causal_assertions(cause, topic)[0].direction
        == CausalDirection.CAUSE_OF_TOPIC_OUTCOME
    )
    assert (
        extract_causal_assertions(consequence, topic)[0].direction
        == CausalDirection.CONSEQUENCE_OF_TOPIC_OUTCOME
    )


def test_reverse_grammar_and_explicit_chain():
    reverse = extract_causal_assertions(
        "Cracking results from drying shrinkage.", "Concrete cracks"
    )[0]
    assert reverse.cause_span == "drying shrinkage"
    assert reverse.effect_span == "Cracking"
    assert reverse.direction == CausalDirection.CAUSE_OF_TOPIC_OUTCOME
    chain = extract_causal_assertions(
        "Temperature gradients induce tensile stress that produces cracking.", "Concrete cracks"
    )
    assert [a.direction for a in chain] == [
        CausalDirection.CAUSAL_INTERMEDIATE,
        CausalDirection.CAUSE_OF_TOPIC_OUTCOME,
    ]
    assert chain[0].provenance["chain_effect"] == "cracking"


@pytest.mark.parametrize(
    "text",
    [
        "Both actions weaken the surface and cause cracking.",
        "This movement of moisture causes cracking.",
        "Internal or external processes cause cracking.",
        "Internal (autogenous) and external (environmental) shrinkage causes cracking.",
    ],
)
def test_unresolved_and_unsafe_hybrids_never_grant_topic_cause(text):
    assert all(
        a.direction == CausalDirection.AMBIGUOUS_RELATION
        for a in extract_causal_assertions(text, "Concrete cracks")
    )


def test_explicit_atomic_enumeration_only_uses_source_words():
    text = "Cracks have several causes including thermal contraction, drying shrinkage, and subgrade settlement."
    assertions = extract_causal_assertions(text, "Concrete cracks")
    assert len(assertions) == 3
    assert all(
        a.cause_span in text and a.direction == CausalDirection.CAUSE_OF_TOPIC_OUTCOME
        for a in assertions
    )


def test_unrelated_causal_edge_and_unknown_grammar_not_topic_cause():
    assert (
        extract_causal_assertions("Rainfall causes flooding.", "Concrete cracks")[0].direction
        == CausalDirection.GENERIC_CAUSAL_RELATION
    )
    assert extract_causal_assertions(
        "Several interacting processes affect materials.", "Concrete cracks"
    )[0].direction not in {
        CausalDirection.CAUSE_OF_TOPIC_OUTCOME,
        CausalDirection.CAUSAL_INTERMEDIATE,
    }


def candidates(texts, topic="Why Concrete Cracks: 5 Mechanisms", foreign=False):
    request_id = uuid4()
    sources, claims = {}, []
    for text in texts:
        sid = uuid4()
        sources[sid] = SimpleNamespace(
            id=sid,
            research_request_id=uuid4() if foreign else request_id,
            url=f"https://source-{sid}.example/article",
            content_excerpt=text,
        )
        claims.append(
            SimpleNamespace(
                id=uuid4(),
                research_request_id=request_id,
                claim_text=text,
                is_verified=False,
                confidence_score=53.75,
                independent_sources_count=1,
                contradicting_sources_count=0,
                evidence=[
                    SimpleNamespace(source_id=sid, excerpt=text, support_direction="SUPPORTS")
                ],
            )
        )
    contract = extract_numeric_promise(topic)
    targets = build_corroboration_targets(claims, sources, contract, topic_title=topic)
    return claims, targets, plan_research_queries(topic, contract, 2, corroboration_targets=targets)


def test_planner_uses_distinct_grounded_causes_and_preserves_authority():
    claims, targets, queries = candidates(
        [
            "Cracking reduces building performance.",
            "Thermal contraction causes cracking.",
            "Cracking results from subgrade settlement.",
            "Service loads cause cracking.",
            "Inspection detects cracking.",
            "Internal (autogenous) and external (environmental) shrinkage causes cracking.",
        ]
    )
    assert len(targets) == 3
    assert len(queries) == 3
    assert all(q.intent == ResearchQueryIntent.CORROBORATION for q in queries)
    assert len({q.candidate_family for q in queries}) == 3
    assert all(
        q.target_claim_id
        and q.target_source_ids
        and q.causal_assertion.provenance["source_grounded"]
        for q in queries
    )
    assert all(
        not c.is_verified and c.confidence_score == 53.75 and c.independent_sources_count == 1
        for c in claims
    )


def test_foreign_sources_cannot_supply_planning_authority():
    _, targets, queries = candidates(["Thermal contraction causes cracking."], foreign=True)
    assert targets == []
    assert all(q.intent != ResearchQueryIntent.CORROBORATION for q in queries)


def test_masonry_persisted_consequence_is_not_promised_entity():
    text = (
        "Shrinkage related cracking in concrete masonry construction is an aesthetic distraction "
        "from the beauty of concrete masonry and can result in reducing the functionality and performance of the building."
    )
    assertion = extract_causal_assertions(text, "Concrete cracks")[0]
    assert assertion.direction not in {
        CausalDirection.CAUSE_OF_TOPIC_OUTCOME,
        CausalDirection.CAUSAL_INTERMEDIATE,
    }
    assert (
        classify_target_planning_usefulness(text, "mechanism", topic_title="Concrete cracks")[2]
        != "CAUSE_OR_PROCESS"
    )
    _, targets, queries = candidates([text])
    assert not targets
    assert all(q.intent != ResearchQueryIntent.CORROBORATION for q in queries)


def test_noncausal_numeric_contract_discovery_preserved():
    topic = "5 Strategies for Reliable Deployments"
    contract = extract_numeric_promise(topic)
    queries = plan_research_queries(topic, contract, 2)
    assert len(queries) == 3
    assert all(q.causal_assertion is None for q in queries)


def test_shared_material_or_generic_loss_word_does_not_match_outcome():
    assert (
        extract_causal_assertions("Concrete leaching causes discoloration.", "Concrete cracks")[
            0
        ].direction
        == CausalDirection.GENERIC_CAUSAL_RELATION
    )
    assert (
        extract_causal_assertions("Congestion causes packet loss.", "Battery capacity loss")[
            0
        ].direction
        == CausalDirection.GENERIC_CAUSAL_RELATION
    )


def test_explicit_condition_with_nested_process_preserves_topic_edge():
    text = "Concrete cracking occurs when thermal gradients produce differential cooling and internal stresses."
    assertion = extract_causal_assertions(text, "Concrete cracks")[0]
    assert assertion.cause_span == "thermal gradients"
    assert assertion.direction == CausalDirection.CAUSE_OF_TOPIC_OUTCOME
    assert assertion.provenance["condition_span"] in text


def test_topic_qualification_is_retained_in_planner_provenance():
    _, _, queries = candidates(["Thermal contraction causes cracking."])
    assertion = queries[0].causal_assertion
    assert assertion.provenance["topic_outcome"] == "concrete cracks"


def test_numeric_planner_requires_actual_source_not_claimed_priority():
    from omega.domain.research import CorroborationTarget

    topic = "Why Concrete Cracks: 5 Mechanisms"
    hint = CorroborationTarget(
        representative_claim_text="Thermal contraction causes cracking.",
        priority=1,
        semantic_role="CAUSE_OR_PROCESS",
    )
    queries = plan_research_queries(
        topic, extract_numeric_promise(topic), 2, corroboration_targets=[hint]
    )
    assert all(q.intent != ResearchQueryIntent.CORROBORATION for q in queries)


def test_citation_heading_with_causal_words_cannot_take_a_slot():
    _, targets, queries = candidates(
        [
            "224R-11 3.2—Cause of cracking due to drying shrinkage 3.3—Drying shrinkage 3.4—Control of cracking."
        ]
    )
    assert targets == []
    assert all(q.intent != ResearchQueryIntent.CORROBORATION for q in queries)
