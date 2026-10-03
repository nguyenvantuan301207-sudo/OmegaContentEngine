"""Unit test matrix for P22-A Visual Continuity & Editorial Beat Architecture."""

import uuid
import pytest

from omega.application.storyboard_engine import (
    StoryboardEngine,
    StoryboardPlan,
    StoryboardScene,
    VisualStrategy,
)
from omega.application.visual_continuity_director import (
    BeatDurationPolicy,
    EditorialBeatPlanner,
    InformationVisualMapper,
    VisualContinuityDirector,
    VisualDirectorBeatAdapter,
    verify_beat_lineage,
)
from omega.application.visual_direction import VisualDirector, VisualRenderMode, VisualTemplateId
from omega.domain.narrative_plan import (
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.visual_beat import (
    AssetReusePolicy,
    ComparisonContinuityState,
    ComparisonSide,
    ContinuityDecisionType,
    ContinuityFindingCode,
    ContinuityFindingSeverity,
    ContinuityGroup,
    DocumentContinuityState,
    DocumentProgressStage,
    VisualBeat,
    VisualBeatSequence,
    VisualRole,
)


def make_test_scene(
    seq_index: int = 1,
    narration: str = "This is a simple baseline explanation.",
    duration: float = 6.0,
    strategy: VisualStrategy = VisualStrategy.IMAGE,
    on_screen_text: str | None = None,
    query_hint: str | None = "distributed systems",
    statement_refs: list[int] | None = None,
) -> StoryboardScene:
    return StoryboardScene(
        sequence_index=seq_index,
        section_id=f"sec_{seq_index}",
        purpose=f"Demonstrate section {seq_index}",
        source_statement_references=statement_refs or [seq_index],
        narration_excerpt=narration,
        estimated_duration_seconds=duration,
        visual_strategy=strategy,
        visual_brief=f"Visual brief for scene {seq_index}",
        on_screen_text=on_screen_text,
        asset_query_hint=query_hint,
    )


# ======================================================================
# A. BEAT PLANNING
# ======================================================================

def test_single_informational_beat():
    scene = make_test_scene(narration="A brief intro to Raft consensus.", duration=3.0)
    planner = EditorialBeatPlanner(pacing="BALANCED")
    seq = planner.plan_scene_beats(scene)

    assert seq.beat_count == 1
    assert seq.beats[0].beat_index == 0
    assert seq.beats[0].start_offset_ms == 0
    assert seq.beats[0].end_offset_ms == 3000
    assert seq.beats[0].duration_ms == 3000


def test_multi_beat_scene_factual_transition():
    # Long scene with contrast and data -> should split into distinct informational beats
    narration = (
        "Traditional Paxos protocols suffered from severe mental complexity and implementation bugs. "
        "However, the Raft protocol introduced a leader-based model that reduced consensus latency by 45%."
    )
    scene = make_test_scene(narration=narration, duration=8.0, statement_refs=[10, 11])
    script_version = {
        "sections": [
            {
                "statements": [
                    {"statement_order": 10, "statement_text": "Traditional Paxos protocols suffered from severe mental complexity and implementation bugs."},
                    {"statement_order": 11, "statement_text": "However, the Raft protocol introduced a leader-based model that reduced consensus latency by 45%."},
                ]
            }
        ]
    }
    planner = EditorialBeatPlanner(pacing="BALANCED")
    seq = planner.plan_scene_beats(scene, script_version=script_version)

    assert seq.beat_count >= 2
    assert seq.beats[0].beat_index == 0
    assert seq.beats[1].beat_index == 1
    # Check stable chronological order and non-overlapping offsets
    assert seq.beats[0].start_offset_ms == 0
    assert seq.beats[0].end_offset_ms == seq.beats[1].start_offset_ms
    assert seq.beats[-1].end_offset_ms == 8000


def test_no_mechanical_sentence_splitting():
    # Short tight sentences that belong to the same concept should not be cut mechanically
    narration = "Consensus is critical. It ensures integrity."
    scene = make_test_scene(narration=narration, duration=3.0)
    planner = EditorialBeatPlanner(pacing="BALANCED")
    seq = planner.plan_scene_beats(scene)

    assert seq.beat_count == 1
    assert "Consensus is critical" in seq.beats[0].narration_text


def test_duration_bounds_and_coverage():
    scene = make_test_scene(duration=10.0)
    planner = EditorialBeatPlanner(pacing="BALANCED")
    seq = planner.plan_scene_beats(scene)

    total_planned = sum(b.duration_ms for b in seq.beats)
    assert total_planned == 10000
    assert seq.total_duration_ms == 10000


# ======================================================================
# B. CONTINUITY
# ======================================================================

def test_continuity_same_subject_and_motif_reuse():
    director = VisualContinuityDirector()
    director.register_motif(
        ContinuityGroup(
            id="motif_raft_architecture",
            motif_type="DIAGRAM",
            primary_subject="Raft Cluster",
        )
    )

    beat1 = VisualBeat(
        scene_id="sec_1",
        parent_scene_index=1,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        visual_intent="Show Raft cluster architecture overview",
        information_goal="Establish Raft leader and follower nodes",
        visual_role=VisualRole.DIAGRAM,
        continuity_group_id="motif_raft_architecture",
        preferred_asset_type="DIAGRAM",
    )

    beat2 = VisualBeat(
        scene_id="sec_1",
        parent_scene_index=1,
        beat_index=1,
        start_offset_ms=3000,
        end_offset_ms=6000,
        duration_ms=3000,
        visual_intent="Highlight leader election in Raft cluster",
        information_goal="Explain leader election stage",
        visual_role=VisualRole.DIAGRAM,
        continuity_group_id="motif_raft_architecture",
        preferred_asset_type="DIAGRAM",
    )

    seq = VisualBeatSequence(
        parent_scene_index=1,
        scene_id="sec_1",
        beats=[beat1, beat2],
        total_duration_ms=6000,
    )

    enriched, findings = director.analyze_sequence(seq)
    assert len(enriched) == 2
    assert enriched[0].continuity_decision == ContinuityDecisionType.KEEP
    assert enriched[0].asset_reuse_policy == AssetReusePolicy.CONTINUITY_ANCHOR
    assert enriched[1].continuity_decision == ContinuityDecisionType.PROGRESS_DIAGRAM
    assert enriched[1].asset_reuse_policy == AssetReusePolicy.INTENTIONAL_REUSE


def test_accidental_repeat_detection():
    director = VisualContinuityDirector()
    beat1 = VisualBeat(
        scene_id="sec_1",
        parent_scene_index=1,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        visual_intent="Server racks in datacenter",
        information_goal="Discuss cloud infrastructure",
        visual_role=VisualRole.EXPLAIN,
        asset_query_hint="generic server room",
    )
    beat2 = VisualBeat(
        scene_id="sec_1",
        parent_scene_index=1,
        beat_index=1,
        start_offset_ms=3000,
        end_offset_ms=6000,
        duration_ms=3000,
        visual_intent="Server racks in datacenter",
        information_goal="Discuss machine learning cost",
        visual_role=VisualRole.EXPLAIN,
        asset_query_hint="generic server room",
    )
    beat3 = VisualBeat(
        scene_id="sec_1",
        parent_scene_index=1,
        beat_index=2,
        start_offset_ms=6000,
        end_offset_ms=9000,
        duration_ms=3000,
        visual_intent="Server racks in datacenter",
        information_goal="Discuss security patch cadence",
        visual_role=VisualRole.EXPLAIN,
        asset_query_hint="generic server room",
    )
    seq = VisualBeatSequence(
        parent_scene_index=1,
        scene_id="sec_1",
        beats=[beat1, beat2, beat3],
        total_duration_ms=9000,
    )
    _, findings = director.analyze_sequence(seq)
    codes = [f.code for f in findings]
    assert ContinuityFindingCode.ACCIDENTAL_ASSET_REPEAT in codes


def test_context_switch_decision():
    director = VisualContinuityDirector()
    beat1 = VisualBeat(
        scene_id="sec_1",
        parent_scene_index=1,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        visual_intent="Server racks",
        information_goal="Infrastructure context",
        visual_role=VisualRole.CONTEXTUALIZE,
        asset_query_hint="servers",
    )
    beat2 = VisualBeat(
        scene_id="sec_1",
        parent_scene_index=1,
        beat_index=1,
        start_offset_ms=3000,
        end_offset_ms=6000,
        duration_ms=3000,
        visual_intent="User phone notification",
        information_goal="Mobile experience",
        visual_role=VisualRole.EXPLAIN,
        asset_query_hint="smartphone screen",
    )
    seq = VisualBeatSequence(
        parent_scene_index=1,
        scene_id="sec_1",
        beats=[beat1, beat2],
        total_duration_ms=6000,
    )
    enriched, _ = director.analyze_sequence(seq)
    assert enriched[1].continuity_decision == ContinuityDecisionType.SWITCH_CONTEXT
    assert enriched[1].asset_reuse_policy == AssetReusePolicy.NEW_ACQUISITION


# ======================================================================
# C. DOCUMENTS & EVIDENCE CONTINUITY
# ======================================================================

def test_document_overview_to_detail_progression():
    director = VisualContinuityDirector()
    beat1 = VisualBeat(
        scene_id="sec_doc",
        parent_scene_index=2,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        narration_text="According to the SEC 10-K filing submitted by the company.",
        visual_intent="SEC 10-K document overview",
        information_goal="Introduce primary document",
        visual_role=VisualRole.DOCUMENT,
        continuity_group_id="motif_sec_10k",
    )
    beat2 = VisualBeat(
        scene_id="sec_doc",
        parent_scene_index=2,
        beat_index=1,
        start_offset_ms=3000,
        end_offset_ms=6000,
        duration_ms=3000,
        narration_text="Specifically, looking at section four on page twelve.",
        visual_intent="Focus on section four page twelve",
        information_goal="Examine section",
        visual_role=VisualRole.DOCUMENT,
        continuity_group_id="motif_sec_10k",
    )
    beat3 = VisualBeat(
        scene_id="sec_doc",
        parent_scene_index=2,
        beat_index=2,
        start_offset_ms=6000,
        end_offset_ms=9000,
        duration_ms=3000,
        narration_text="The clause explicitly states that revenue was overstated.",
        visual_intent="Drill into quote clause",
        information_goal="Inspect quote detail",
        visual_role=VisualRole.DOCUMENT,
        continuity_group_id="motif_sec_10k",
    )
    seq = VisualBeatSequence(
        parent_scene_index=2,
        scene_id="sec_doc",
        beats=[beat1, beat2, beat3],
        total_duration_ms=9000,
    )
    enriched, findings = director.analyze_sequence(seq)
    assert enriched[1].continuity_decision == ContinuityDecisionType.PROGRESS_DOCUMENT
    assert enriched[2].continuity_decision == ContinuityDecisionType.PROGRESS_DOCUMENT
    assert not any(f.code == ContinuityFindingCode.DOCUMENT_CONTEXT_LOST for f in findings)


def test_document_context_lost_finding():
    director = VisualContinuityDirector()
    # Jumping directly to detail without overview
    beat1 = VisualBeat(
        scene_id="sec_doc",
        parent_scene_index=2,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        narration_text="Examining specifically the line item clause.",
        visual_intent="Drill into quote clause",
        information_goal="Inspect quote detail",
        visual_role=VisualRole.DOCUMENT,
        continuity_group_id="motif_sudden_doc",
    )
    seq = VisualBeatSequence(
        parent_scene_index=2,
        scene_id="sec_doc",
        beats=[beat1],
        total_duration_ms=3000,
    )
    _, findings = director.analyze_sequence(seq)
    codes = [f.code for f in findings]
    assert ContinuityFindingCode.DOCUMENT_CONTEXT_LOST in codes


# ======================================================================
# D. COMPARISON CONTINUITY
# ======================================================================

def test_comparison_stable_side_mapping():
    director = VisualContinuityDirector()
    beat1 = VisualBeat(
        scene_id="sec_comp",
        parent_scene_index=3,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        narration_text="When comparing Postgres vs MongoDB for ACID transactions.",
        visual_intent="Postgres vs MongoDB comparison frame",
        information_goal="Establish comparison sides",
        visual_role=VisualRole.COMPARE,
        continuity_group_id="motif_db_comp",
    )
    beat2 = VisualBeat(
        scene_id="sec_comp",
        parent_scene_index=3,
        beat_index=1,
        start_offset_ms=3000,
        end_offset_ms=6000,
        duration_ms=3000,
        narration_text="Postgres provides serializable guarantees compared to MongoDB.",
        visual_intent="Postgres advantages vs MongoDB",
        information_goal="Follow-up comparative evaluation",
        visual_role=VisualRole.COMPARE,
        continuity_group_id="motif_db_comp",
    )
    seq = VisualBeatSequence(
        parent_scene_index=3,
        scene_id="sec_comp",
        beats=[beat1, beat2],
        total_duration_ms=6000,
    )
    _, findings = director.analyze_sequence(seq)
    assert not any(f.code == ContinuityFindingCode.COMPARISON_SIDE_SWAP for f in findings)


def test_comparison_side_swap_finding():
    director = VisualContinuityDirector()
    beat1 = VisualBeat(
        scene_id="sec_comp",
        parent_scene_index=3,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        narration_text="Postgres vs MongoDB architecture.",
        visual_intent="Postgres vs MongoDB comparison",
        information_goal="Establish comparison",
        visual_role=VisualRole.COMPARE,
        continuity_group_id="motif_db_comp",
    )
    beat2 = VisualBeat(
        scene_id="sec_comp",
        parent_scene_index=3,
        beat_index=1,
        start_offset_ms=3000,
        end_offset_ms=6000,
        duration_ms=3000,
        narration_text="MongoDB vs Postgres performance metrics.",
        visual_intent="MongoDB vs Postgres comparison inverted",
        information_goal="Evaluate sides inverted",
        visual_role=VisualRole.COMPARE,
        continuity_group_id="motif_db_comp",
    )
    seq = VisualBeatSequence(
        parent_scene_index=3,
        scene_id="sec_comp",
        beats=[beat1, beat2],
        total_duration_ms=6000,
    )
    _, findings = director.analyze_sequence(seq)
    codes = [f.code for f in findings]
    assert ContinuityFindingCode.COMPARISON_SIDE_SWAP in codes


# ======================================================================
# E. INFORMATION-TO-VISUAL MAPPING
# ======================================================================

def test_information_visual_mapping_rules():
    role, asset = InformationVisualMapper.map_goal_to_role("Under the hood, the consensus mechanism coordinates nodes.")
    assert role == VisualRole.DIAGRAM
    assert asset == "DIAGRAM"

    role, asset = InformationVisualMapper.map_goal_to_role("Performance tests showed an 85% drop in memory latency.")
    assert role == VisualRole.DATA
    assert asset == "DATA_CARD"

    role, asset = InformationVisualMapper.map_goal_to_role("According to the published whitepaper and audit report.")
    assert role == VisualRole.DOCUMENT
    assert asset == "DOCUMENT"

    role, asset = InformationVisualMapper.map_goal_to_role("A busy downtown city street at dusk.", section_role=NarrativeSectionRole.CONTEXT)
    assert role in (VisualRole.ESTABLISH, VisualRole.CONTEXTUALIZE)
    assert asset == "BROLL"

    role, asset = InformationVisualMapper.map_goal_to_role("The shocking truth was finally revealed to everyone.", section_role=NarrativeSectionRole.PAYOFF)
    assert role == VisualRole.REVEAL


# ======================================================================
# F. REPETITION & DURATION FINDINGS
# ======================================================================

def test_beat_duration_policy_bounds():
    policy = BeatDurationPolicy("BALANCED")
    beat_too_fast = VisualBeat(
        scene_id="s1", parent_scene_index=1, beat_index=0,
        start_offset_ms=0, end_offset_ms=800, duration_ms=800,
        visual_intent="Quick flash", information_goal="Test", visual_role=VisualRole.EXPLAIN
    )
    beat_too_long = VisualBeat(
        scene_id="s1", parent_scene_index=1, beat_index=1,
        start_offset_ms=800, end_offset_ms=10800, duration_ms=10000,
        visual_intent="Static view", information_goal="Test", visual_role=VisualRole.EXPLAIN
    )
    findings = policy.evaluate_beats([beat_too_fast, beat_too_long])
    codes = [f.code for f in findings]
    assert ContinuityFindingCode.VISUAL_CUT_TOO_FAST in codes
    assert ContinuityFindingCode.VISUAL_HOLD_TOO_LONG in codes


def test_excessive_visual_churn_finding():
    policy = BeatDurationPolicy("BALANCED")
    beats = [
        VisualBeat(
            scene_id="s1", parent_scene_index=1, beat_index=i,
            start_offset_ms=i * 1200, end_offset_ms=(i + 1) * 1200, duration_ms=1200,
            visual_intent=f"Fast beat {i}", information_goal="Fast churn", visual_role=VisualRole.BROLL
        )
        for i in range(4)
    ]
    findings = policy.evaluate_beats(beats)
    codes = [f.code for f in findings]
    assert ContinuityFindingCode.EXCESSIVE_VISUAL_CHURN in codes


# ======================================================================
# G. LINEAGE VERIFICATION
# ======================================================================

def test_beat_lineage_traceability():
    np_id = uuid.uuid4()
    scene = make_test_scene(seq_index=2, statement_refs=[5, 6])

    beat = VisualBeat(
        scene_id="sec_2",
        parent_scene_index=2,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        script_statement_ids=[5],
        visual_intent="Traceable beat",
        information_goal="Traceability",
        visual_role=VisualRole.EXPLAIN,
    )

    class DummyScriptVersion:
        narrative_plan_id = np_id

    class DummyNarrativePlan:
        id = np_id

    assert verify_beat_lineage(beat, scene, DummyScriptVersion(), DummyNarrativePlan()) is True

    # Corrupt scene index
    bad_beat = VisualBeat(
        scene_id="sec_99",
        parent_scene_index=99,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        script_statement_ids=[5],
        visual_intent="Corrupt beat",
        information_goal="Traceability",
        visual_role=VisualRole.EXPLAIN,
    )
    assert verify_beat_lineage(bad_beat, scene, DummyScriptVersion(), DummyNarrativePlan()) is False


# ======================================================================
# H. BACKWARD COMPATIBILITY
# ======================================================================

def test_visual_director_seam_and_backward_compatibility():
    director = VisualDirector()
    scene = make_test_scene(strategy=VisualStrategy.DIAGRAM)

    # 1. Existing resolve(scene) continues to work identically
    direction = director.resolve(scene)
    assert direction.render_mode == VisualRenderMode.TEMPLATE
    assert direction.template_id == VisualTemplateId.FLOW_DIAGRAM

    # 2. resolve_beat(scene, beat) enriches direction with continuity metadata
    beat = VisualBeat(
        scene_id=scene.section_id,
        parent_scene_index=scene.sequence_index,
        beat_index=0,
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        visual_intent="Enriched beat",
        information_goal="Seam validation",
        visual_role=VisualRole.DIAGRAM,
        continuity_decision=ContinuityDecisionType.PROGRESS_DIAGRAM,
    )
    beat_dir = director.resolve_beat(scene, beat)
    assert beat_dir.scene_index == scene.sequence_index
    assert beat_dir.template_id == VisualTemplateId.FLOW_DIAGRAM
    assert "Continuity: PROGRESS_DIAGRAM" in beat_dir.rationale
    assert beat_dir.metadata["visual_role"] == "DIAGRAM"
    assert beat_dir.metadata["continuity_decision"] == "PROGRESS_DIAGRAM"


def test_storyboard_engine_backward_compatibility():
    engine = StoryboardEngine()
    script = {
        "title": "Compatibility Test",
        "estimated_duration_seconds": 30.0,
        "sections": [
            {
                "heading": "Intro",
                "statements": [
                    {"statement_order": 1, "statement_text": "Welcome to the test run."},
                    {"statement_order": 2, "statement_text": "We verify baseline behavior."},
                ],
            }
        ],
    }
    plan = engine.generate_storyboard(script)
    assert isinstance(plan, StoryboardPlan)
    assert len(plan.scenes) >= 1
    assert plan.scenes[0].sequence_index == 1
