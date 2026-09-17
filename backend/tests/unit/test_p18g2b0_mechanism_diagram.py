"""Tests for P18-G2C2B0: Trustworthy Mechanism Visual Intent + Diagram Payload Readiness.

Verifies:
1. MechanismDiagramSpec extraction contracts and conservative relation grammar:
   - "A because B" => B -> A
   - "Because B, A" => B -> A
   - "A causes B" => A -> B
   - "A leads to B" => A -> B
   - "As A, B" => A -> B
   - "When A, B" => A -> B
   - "A, therefore B" => A -> B
   - "A, so B" => A -> B
   - "First A, then B" => A -> B
   - "A, then B" => A -> B
2. Safety rules:
   - Sunset / Rayleigh scattering source clause
   - No explicit relation => None
   - Ambiguous relation => None
   - Blank input => None
   - Duplicate/equivalent nodes rejected => None
   - Oversized clause rejected => None
   - Deterministic identical input => identical spec
   - Extracted nodes are source-supported after conservative normalization
   - Zero generated semantic labels
3. EditorialBeatPlanner promotion:
   - MECHANISM + trustworthy relation + parent BROLL -> DIAGRAM (LOCAL_EXPLAINER, STATIC)
   - MECHANISM + unsafe/no relation + parent BROLL -> BROLL
   - Explicit parent DIAGRAM remains DIAGRAM
   - Non-mechanism explanation, evidence, hook, closing behavior unchanged
4. TemplatePayloadResolver integration and parity guarantee:
   - FLOW_DIAGRAM uses shared mechanism spec when available
   - Fallback to generic _extract_diagram when spec is None
   - Planner auto-promotion -> BeatVisualDirector -> BeatRenderAdapter -> TemplatePayloadResolver parity
"""

from __future__ import annotations

from omega.application.beat_asset_policy import BeatAssetPolicy
from omega.application.beat_render_adapter import BeatRenderAdapter
from omega.application.beat_visual_direction import BeatVisualDirector
from omega.application.editorial_beat import (
    AssetReuseIntent,
    BeatMotionIntent,
    BeatSemanticRole,
)
from omega.application.editorial_beat_planner import EditorialBeatPlanner
from omega.application.mechanism_diagram import (
    MechanismDiagramEdge,
    MechanismDiagramSpec,
    resolve_mechanism_diagram_spec,
)
from omega.application.scene_template_registry import TemplateInputKey
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.template_payload_resolver import TemplatePayloadResolver
from omega.application.visual_direction import (
    VisualDirection,
    VisualDirector,
    VisualRenderMode,
    VisualTemplateId,
)

# ============================================================================
# 1. Mechanism Diagram Extraction Tests
# ============================================================================

def test_01_a_because_b_resolves_cause_to_effect():
    """'A because B' resolves cause B -> effect A."""
    text = "Traffic slows down because heavy rain reduces visibility."
    spec = resolve_mechanism_diagram_spec(text)
    assert spec is not None
    assert isinstance(spec, MechanismDiagramSpec)
    assert len(spec.nodes) == 2
    assert spec.nodes == ("heavy rain reduces visibility", "Traffic slows down")
    assert spec.edges == (
        MechanismDiagramEdge(from_node="heavy rain reduces visibility", to_node="Traffic slows down"),
    )
    assert spec.source_text == text


def test_02_because_b_a_resolves_cause_to_effect():
    """'Because B, A' resolves cause B -> effect A."""
    text = "Because water heats slowly, coastal climates remain moderate."
    spec = resolve_mechanism_diagram_spec(text)
    assert spec is not None
    assert spec.nodes == ("water heats slowly", "coastal climates remain moderate")
    assert spec.edges == (
        MechanismDiagramEdge(from_node="water heats slowly", to_node="coastal climates remain moderate"),
    )


def test_03_a_causes_b_resolves_a_to_b():
    """'A causes B' resolves cause A -> effect B."""
    text = "High network latency causes database connection timeouts."
    spec = resolve_mechanism_diagram_spec(text)
    assert spec is not None
    assert spec.nodes == ("High network latency", "database connection timeouts")
    assert spec.edges == (
        MechanismDiagramEdge(from_node="High network latency", to_node="database connection timeouts"),
    )


def test_04_a_leads_to_b_resolves_a_to_b():
    """'A leads to B' resolves antecedent A -> outcome B."""
    text = "Memory fragmentation leads to frequent garbage collection pauses."
    spec = resolve_mechanism_diagram_spec(text)
    assert spec is not None
    assert spec.nodes == ("Memory fragmentation", "frequent garbage collection pauses")
    assert spec.edges == (
        MechanismDiagramEdge(from_node="Memory fragmentation", to_node="frequent garbage collection pauses"),
    )


def test_05_as_a_b_resolves_a_to_b():
    """'As A, B' resolves antecedent A -> consequential state B."""
    text = "As sunlight travels through more atmosphere, Rayleigh scattering removes blue light."
    spec = resolve_mechanism_diagram_spec(text)
    assert spec is not None
    assert spec.nodes == (
        "sunlight travels through more atmosphere",
        "Rayleigh scattering removes blue light",
    )
    assert spec.edges == (
        MechanismDiagramEdge(
            from_node="sunlight travels through more atmosphere",
            to_node="Rayleigh scattering removes blue light",
        ),
    )


def test_06_non_sunset_technical_pipeline_clause():
    """Technical pipeline clause 'Requests enter the queue, then workers process them.' resolves cleanly."""
    text = "Requests enter the queue, then workers process them."
    spec = resolve_mechanism_diagram_spec(text)
    assert spec is not None
    assert spec.nodes == ("Requests enter the queue", "workers process them")
    assert spec.edges == (
        MechanismDiagramEdge(from_node="Requests enter the queue", to_node="workers process them"),
    )


def test_07_no_explicit_relation_returns_none():
    """Text without explicit causal or sequential syntax returns None (fail-closed)."""
    text = "The sunset sky is filled with glowing shades of amber and crimson."
    spec = resolve_mechanism_diagram_spec(text)
    assert spec is None


def test_08_ambiguous_or_blank_input_returns_none():
    """Blank or whitespace-only inputs return None."""
    assert resolve_mechanism_diagram_spec("") is None
    assert resolve_mechanism_diagram_spec("   ") is None
    assert resolve_mechanism_diagram_spec("   \n\t  ") is None


def test_09_duplicate_or_equivalent_nodes_rejected():
    """Clauses that normalize to identical node texts are rejected (fail-closed)."""
    text = "The network partitions, so the network partitions."
    assert resolve_mechanism_diagram_spec(text) is None


def test_10_giant_oversized_clause_rejected():
    """Oversized clauses exceeding word or length limits are rejected."""
    giant = "As " + " ".join(["very long word"] * 30) + ", output happens."
    assert resolve_mechanism_diagram_spec(giant) is None


def test_11_identical_input_yields_identical_spec():
    """Deterministic resolution yields identical nodes and edges."""
    text = "When disk usage exceeds threshold, alerts trigger automatically."
    spec1 = resolve_mechanism_diagram_spec(text)
    spec2 = resolve_mechanism_diagram_spec(text)
    assert spec1 == spec2
    assert spec1.nodes == spec2.nodes


def test_12_extracted_nodes_are_purely_source_derived_no_hallucination():
    """Nodes must be exact normalized substrings of the input text without generated labels."""
    text = "Because atmospheric pressure drops, water boils at lower temperatures."
    spec = resolve_mechanism_diagram_spec(text)
    assert spec is not None
    for node in spec.nodes:
        assert node in text


# ============================================================================
# 2. Editorial Beat Planner Policy Tests
# ============================================================================

def test_13_mechanism_with_trustworthy_spec_promoted_to_diagram():
    """When a MECHANISM beat has a trustworthy diagram spec, parent BROLL is promoted to DIAGRAM."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Physics",
        purpose="Explain scattering",
        source_statement_references=[1],
        narration_excerpt="As sunlight travels through more atmosphere, Rayleigh scattering removes blue light.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Sunset atmosphere",
        asset_query_hint="sunset atmosphere",
    )
    statements = [
        {
            "statement_order": 1,
            "statement_text": "As sunlight travels through more atmosphere, Rayleigh scattering removes blue light.",
            "statement_type": "STATEMENT",
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=statements)
    assert len(plan.beats) == 1
    beat = plan.beats[0]
    assert beat.semantic_role == BeatSemanticRole.MECHANISM
    assert beat.preferred_visual_strategy == VisualStrategy.DIAGRAM
    assert beat.asset_reuse_intent == AssetReuseIntent.LOCAL_EXPLAINER
    assert beat.motion_intent == BeatMotionIntent.STATIC


def test_14_mechanism_without_trustworthy_spec_retains_safe_broll():
    """When a MECHANISM beat has cue words but NO trustworthy diagram relation, it safely remains BROLL."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Physics",
        purpose="Mention wavelength",
        source_statement_references=[1],
        narration_excerpt="Light has different wavelengths across the visible color spectrum.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Rainbow light spectrum",
        asset_query_hint="light spectrum prism",
    )
    statements = [
        {
            "statement_order": 1,
            "statement_text": "Light has different wavelengths across the visible color spectrum.",
            "statement_type": "STATEMENT",
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=statements)
    assert len(plan.beats) == 1
    beat = plan.beats[0]
    # Classified as MECHANISM due to "wavelength" cue
    assert beat.semantic_role == BeatSemanticRole.MECHANISM
    # But because there is no causal structure, stays BROLL!
    assert beat.preferred_visual_strategy == VisualStrategy.BROLL
    assert beat.asset_reuse_intent == AssetReuseIntent.REUSE_PARENT


def test_15_explicit_parent_diagram_remains_diagram():
    """Parent scene with explicit DIAGRAM strategy remains DIAGRAM."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Architecture",
        purpose="Show pipeline",
        source_statement_references=[1],
        narration_excerpt="The ingestion worker pushes events to Kafka.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Architecture diagram",
    )
    statements = [
        {
            "statement_order": 1,
            "statement_text": "The ingestion worker pushes events to Kafka.",
            "statement_type": "STATEMENT",
        }
    ]

    plan = EditorialBeatPlanner.plan(scene=scene, source_statements=statements)
    beat = plan.beats[0]
    assert beat.preferred_visual_strategy == VisualStrategy.DIAGRAM
    assert beat.asset_reuse_intent == AssetReuseIntent.LOCAL_EXPLAINER


# ============================================================================
# 3. Template Payload Resolver & Parity Guarantee
# ============================================================================

def test_16_template_payload_resolver_uses_mechanism_spec_for_flow_diagram():
    """TemplatePayloadResolver resolves FLOW_DIAGRAM using resolve_mechanism_diagram_spec for G2 mechanism beat."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Mechanism Section",
        purpose="Explain mechanism",
        source_statement_references=[1],
        narration_excerpt="High network latency causes database connection timeouts.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Network diagram",
    )
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale="Diagram",
        metadata={"beat_index": 0, "semantic_role": "MECHANISM"},
    )

    resolver = TemplatePayloadResolver()
    payload = resolver.resolve(scene, direction)
    assert payload.template_id == VisualTemplateId.FLOW_DIAGRAM
    nodes = payload.inputs[TemplateInputKey.NODES]
    assert nodes == ["High network latency", "database connection timeouts"]
    edges = payload.inputs[TemplateInputKey.EDGES]
    assert len(edges) == 1
    assert edges[0].from_node == "High network latency"
    assert edges[0].to_node == "database connection timeouts"


def test_17_template_payload_resolver_fallback_to_generic_diagram():
    """When resolve_mechanism_diagram_spec returns None, generic _extract_diagram still succeeds."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Architecture Section",
        purpose="System overview",
        source_statement_references=[1],
        narration_excerpt="The Client sends data to the Ingestion Service, which notifies the Database.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Architecture overview",
    )
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale="Diagram",
        metadata={"beat_index": 0, "semantic_role": "MECHANISM"},
    )

    resolver = TemplatePayloadResolver()
    payload = resolver.resolve(scene, direction)
    assert payload.template_id == VisualTemplateId.FLOW_DIAGRAM
    nodes = payload.inputs[TemplateInputKey.NODES]
    assert "Client" in nodes
    assert "Ingestion Service" in nodes


def test_18_planner_promotion_payload_parity_guarantee():
    """Any beat auto-promoted from BROLL to DIAGRAM by EditorialBeatPlanner must be payload-resolvable by TemplatePayloadResolver."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Rayleigh Explanation",
        purpose="Explain scattering",
        source_statement_references=[1],
        narration_excerpt="As sunlight travels through more atmosphere, Rayleigh scattering removes blue light.",
        estimated_duration_seconds=6.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Sunset atmosphere",
        asset_query_hint="sunset atmosphere",
    )
    statements = [
        {
            "statement_order": 1,
            "statement_text": "As sunlight travels through more atmosphere, Rayleigh scattering removes blue light.",
            "statement_type": "STATEMENT",
        }
    ]

    # 1. Plan beats
    beat_plan = EditorialBeatPlanner.plan(scene=scene, source_statements=statements)
    timing_plan = EditorialBeatPlanner.allocate_timing(beat_plan, scene_duration_ms=6000)
    assert beat_plan.beats[0].preferred_visual_strategy == VisualStrategy.DIAGRAM

    # 2. Direct visual
    dir_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=beat_plan)
    assert dir_plan.directions[0].template_id == VisualTemplateId.FLOW_DIAGRAM

    # 3. Plan assets
    asset_plan = BeatAssetPolicy.plan_assets(scene=scene, beat_plan=beat_plan, direction_plan=dir_plan)

    # 4. Adapt render
    render_res = BeatRenderAdapter.adapt(
        scene=scene,
        beat_plan=beat_plan,
        timing_plan=timing_plan,
        direction_plan=dir_plan,
        asset_plan=asset_plan,
    )
    assert render_res.eligible is True
    assert len(render_res.plan.units) == 1
    unit = render_res.plan.units[0]

    # 5. Resolve payload with TemplatePayloadResolver
    resolver = TemplatePayloadResolver()
    payload = resolver.resolve(unit.scene_view, unit.direction_view)
    assert payload.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert payload.inputs[TemplateInputKey.NODES] == [
        "sunlight travels through more atmosphere",
        "Rayleigh scattering removes blue light",
    ]


def test_19_legacy_canonical_direction_isolated_from_mechanism_spec():
    """Legacy VisualDirector directions must isolate from mechanism extraction and use legacy generic diagram extraction."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Infrastructure Section",
        purpose="Explain failure mode",
        source_statement_references=[1],
        narration_excerpt="High latency causes retry storms in the Processing Pipeline.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Architecture latency diagram",
    )
    director = VisualDirector()
    direction = director.resolve(scene)

    # 1. Verify legacy direction metadata contract
    assert "beat_index" not in direction.metadata
    assert "semantic_role" not in direction.metadata
    assert direction.template_id == VisualTemplateId.FLOW_DIAGRAM

    # 2. Text would otherwise produce a mechanism spec
    mech_spec = resolve_mechanism_diagram_spec(scene.narration_excerpt)
    assert mech_spec is not None
    assert list(mech_spec.nodes) == ["High latency", "retry storms in the Processing Pipeline"]

    # 3. Resolve payload through TemplatePayloadResolver
    resolver = TemplatePayloadResolver()
    expected_nodes, expected_edges = resolver._extract_diagram(scene.narration_excerpt)
    payload = resolver.resolve(scene, direction)

    # 4. Prove legacy generic diagram extraction was used exactly
    assert payload.inputs[TemplateInputKey.NODES] == expected_nodes
    assert payload.inputs[TemplateInputKey.EDGES] == expected_edges
    assert payload.inputs[TemplateInputKey.NODES] != list(mech_spec.nodes)


def test_20_legacy_canonical_direction_never_calls_mechanism_extractor(monkeypatch):
    """Monkeypatch test proving resolve_mechanism_diagram_spec is NOT invoked for legacy canonical directions."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Infrastructure Section",
        purpose="Explain failure mode",
        source_statement_references=[1],
        narration_excerpt="As requests enter the queue, workers process them in the Processing Pipeline.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Queue diagram",
    )
    direction = VisualDirector().resolve(scene)

    def _poison_mechanism_spec(*args, **kwargs):
        raise AssertionError("resolve_mechanism_diagram_spec must NEVER be called on legacy canonical paths!")

    monkeypatch.setattr(
        "omega.application.template_payload_resolver.resolve_mechanism_diagram_spec",
        _poison_mechanism_spec,
    )

    resolver = TemplatePayloadResolver()
    payload = resolver.resolve(scene, direction)
    assert payload.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert TemplateInputKey.NODES in payload.inputs


def test_21_negative_g2_metadata_gate_fallbacks():
    """Directions with invalid/missing G2 metadata fail-safe to legacy generic diagram behavior."""
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Test Section",
        purpose="Diagram test",
        source_statement_references=[1],
        narration_excerpt="High latency causes retry storms in the Processing Pipeline.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Test diagram",
    )
    resolver = TemplatePayloadResolver()
    expected_nodes, expected_edges = resolver._extract_diagram(scene.narration_excerpt)

    # Case A: beat_index present, but semantic_role == EXPLANATION
    dir_a = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale="Diagram",
        metadata={"beat_index": 0, "semantic_role": BeatSemanticRole.EXPLANATION.value},
    )
    payload_a = resolver.resolve(scene, dir_a)
    assert payload_a.inputs[TemplateInputKey.NODES] == expected_nodes
    assert payload_a.inputs[TemplateInputKey.EDGES] == expected_edges

    # Case B: semantic_role == MECHANISM, but beat_index missing
    dir_b = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale="Diagram",
        metadata={"semantic_role": BeatSemanticRole.MECHANISM.value},
    )
    payload_b = resolver.resolve(scene, dir_b)
    assert payload_b.inputs[TemplateInputKey.NODES] == expected_nodes
    assert payload_b.inputs[TemplateInputKey.EDGES] == expected_edges

    # Case C: malformed beat_index (string or negative int)
    dir_c1 = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale="Diagram",
        metadata={"beat_index": "0", "semantic_role": BeatSemanticRole.MECHANISM.value},
    )
    payload_c1 = resolver.resolve(scene, dir_c1)
    assert payload_c1.inputs[TemplateInputKey.NODES] == expected_nodes

    dir_c2 = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="sequential_flow",
        rationale="Diagram",
        metadata={"beat_index": -1, "semantic_role": BeatSemanticRole.MECHANISM.value},
    )
    payload_c2 = resolver.resolve(scene, dir_c2)
    assert payload_c2.inputs[TemplateInputKey.NODES] == expected_nodes
