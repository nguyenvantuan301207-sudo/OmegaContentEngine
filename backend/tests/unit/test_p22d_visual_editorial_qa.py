"""Comprehensive unit tests for P22-D Visual Editorial QA & Acceptance Gate.

Covers Test Matrix A through L:
A. Continuity (valid, accidental repeat, motif preservation, comparison swap, document context loss)
B. Asset relevance (correct asset, irrelevant B-roll, wrong entity/context)
C. Grounding (valid generated visual provenance, ungrounded data, missing provenance, hash missing)
D. Diagram/chart readability (readable, text overflow, node overlap, excessive complexity, missing source label)
E. Camera (valid framing, out-of-bounds, aggressive motion, rapid direction reversal)
F. Transition (CUT, fallback used, duration violation)
G. Composition (valid safe frame, invalid dimensions, safe frame violation)
H. Repetition (intentional motif, accidental repetition, excessive static hold)
I. Artifact (valid image, blank frame, zero byte, corrupt artifact, missing artifact)
J. Timing (exact coverage, timing gap, timing overlap, duration drift)
K. Acceptance (PASS, REVISE, FAIL, blocker behavior)
L. Gate (PASS allows final acceptance/render path, REVISE blocks, FAIL blocks)
"""

from __future__ import annotations

import binascii
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from omega.application.visual_editorial_qa_service import (
    AssetAppropriatenessQAEvaluator,
    CameraQAEvaluator,
    CompositionQAEvaluator,
    DataFidelityQAEvaluator,
    ExplanatoryVisualReadabilityQAEvaluator,
    PhysicalArtifactQAEvaluator,
    TransitionQAEvaluator,
    VisualContinuityQAEvaluator,
    VisualEditorialQAService,
    VisualGroundingQAEvaluator,
    VisualRenderGate,
    VisualRepetitionQAEvaluator,
    VisualTimingQAEvaluator,
)
from omega.domain.camera_transition import (
    CameraFrameState,
    CameraIntent,
    CameraPlan,
    FocusRegion,
    MotionStrength,
    TransitionIntent,
    TransitionPlan,
)
from omega.domain.visual_beat import (
    AssetReusePolicy,
    ContinuityFinding,
    ContinuityFindingCode,
    ContinuityFindingSeverity,
    DocumentProgressStage,
    VisualBeat,
    VisualRole,
)
from omega.domain.visual_editorial_qa import (
    VisualQAFinding,
    VisualQAFindingCode,
    VisualQARecommendationAction,
    VisualQAResult,
    VisualQASeverity,
    VisualQAStatus,
    VisualQASubsystem,
    VisualRenderGateError,
)
from omega.domain.visual_explanation import (
    ChartSpec,
    DataPoint,
    DiagramEdge,
    DiagramNode,
    DiagramOrientation,
    DiagramSpec,
    EvidenceVisualSpec,
    ExplanationType,
    GeneratedVisualArtifact,
    GroundedEvidenceItem,
    GroundingState,
    VisualArtifactProvenance,
    VisualExplanationPlan,
)


def _make_beat(
    beat_index: int = 0,
    role: VisualRole = VisualRole.EXPLAIN,
    duration_ms: int = 3000,
    start_ms: int = 0,
    intent: str = "Explain the fundamental mechanism",
    reuse_policy: AssetReusePolicy = AssetReusePolicy.NEW_ACQUISITION,
) -> VisualBeat:
    return VisualBeat(
        id=uuid4(),
        scene_id="scene-1",
        parent_scene_index=1,
        beat_index=beat_index,
        source_editorial_beat_indices=(beat_index,),
        start_offset_ms=start_ms,
        end_offset_ms=start_ms + duration_ms,
        duration_ms=duration_ms,
        narration_text="The mechanism operates in three distinct phases.",
        visual_intent=intent,
        information_goal="Core mechanism clarity",
        visual_role=role,
        preferred_asset_type="IMAGE",
        asset_reuse_policy=reuse_policy,
    )


def _make_camera_plan(
    beat_index: int = 0,
    intent: CameraIntent = CameraIntent.STATIC,
    strength: MotionStrength = MotionStrength.MODERATE,
    focus_region: FocusRegion | None = None,
) -> CameraPlan:
    return CameraPlan(
        visual_beat_id=uuid4(),
        parent_scene_index=1,
        beat_index=beat_index,
        source_editorial_beat_indices=(beat_index,),
        duration_ms=3000,
        preferred_asset_type="IMAGE",
        intent=intent,
        strength=strength,
        focus_region=focus_region,
        start_state=CameraFrameState(scale=1.0, center_x=0.5, center_y=0.5),
        end_state=CameraFrameState(scale=1.0, center_x=0.5, center_y=0.5),
        editorial_purpose="Focus viewer attention",
    )


def _make_transition_plan(
    beat_index: int = 0,
    requested: TransitionIntent = TransitionIntent.CUT,
    applied: TransitionIntent = TransitionIntent.CUT,
    duration_ms: int = 0,
    fallback_reason: str | None = None,
) -> TransitionPlan:
    return TransitionPlan(
        visual_beat_id=uuid4(),
        parent_scene_index=1,
        beat_index=beat_index,
        source_editorial_beat_indices=(beat_index,),
        requested_intent=requested,
        applied_intent=applied,
        duration_ms=min(duration_ms, 600),
        fallback_reason=fallback_reason,
    )


def _create_test_png(
    path: Path,
    width: int = 1920,
    height: int = 1080,
    blank: bool = False,
) -> Path:
    """Create a minimal valid PNG file, optionally with non-blank patterned content."""
    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # filter type 0: None
        for x in range(width):
            if blank:
                raw_rows.extend((15, 23, 42))  # Uniform dark slate
            else:
                if (x + y) % 20 < 10:
                    raw_rows.extend((56, 189, 248))  # Cyan pattern
                else:
                    raw_rows.extend((15, 23, 42))

    compressed = zlib.compress(bytes(raw_rows), level=6)

    def chunk(tag_name: bytes, data: bytes) -> bytes:
        crc = binascii.crc32(tag_name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag_name + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png_bytes)
    return path


# ── A. Continuity QA Tests ──────────────────────────────────────────────────


def test_continuity_valid_sequence():
    beats = [_make_beat(0, start_ms=0), _make_beat(1, start_ms=3000)]
    findings = VisualContinuityQAEvaluator.evaluate(visual_beats=beats)
    assert not any(f.severity in (VisualQASeverity.ERROR, VisualQASeverity.BLOCKER) for f in findings)


def test_continuity_accidental_repeat():
    beats = [
        _make_beat(0),
        _make_beat(
            1,
            start_ms=3000,
            reuse_policy=AssetReusePolicy.ACCIDENTAL_REPEAT,
        ),
    ]
    findings = VisualContinuityQAEvaluator.evaluate(visual_beats=beats)
    codes = [f.code for f in findings]
    assert VisualQAFindingCode.ACCIDENTAL_ASSET_REPEAT in codes


def test_continuity_motif_preservation():
    beats = [
        _make_beat(0, reuse_policy=AssetReusePolicy.CONTINUITY_ANCHOR),
        _make_beat(1, start_ms=3000, reuse_policy=AssetReusePolicy.INTENTIONAL_REUSE),
    ]
    findings = VisualContinuityQAEvaluator.evaluate(visual_beats=beats)
    assert not any(f.code == VisualQAFindingCode.ACCIDENTAL_ASSET_REPEAT for f in findings)


def test_continuity_comparison_swap_promoted():
    cf = ContinuityFinding(
        code=ContinuityFindingCode.COMPARISON_SIDE_SWAP,
        severity=ContinuityFindingSeverity.ERROR,
        explanation="Entity swapped from LEFT to RIGHT unexpectedly.",
        affected_beat_indices=[1],
    )
    findings = VisualContinuityQAEvaluator.evaluate(
        visual_beats=[_make_beat(0), _make_beat(1, start_ms=3000)],
        continuity_findings=[cf],
    )
    assert any(f.code == VisualQAFindingCode.COMPARISON_SIDE_SWAP for f in findings)
    assert any(f.severity == VisualQASeverity.ERROR for f in findings)


def test_continuity_document_context_loss():
    b0 = _make_beat(0)
    b1 = _make_beat(1, start_ms=3000)
    object.__setattr__(b1, "document_stage", DocumentProgressStage.DETAIL)
    findings = VisualContinuityQAEvaluator.evaluate(visual_beats=[b0, b1])
    assert any(f.code == VisualQAFindingCode.DOCUMENT_CONTEXT_LOST for f in findings)


# ── B. Asset Appropriateness QA Tests ───────────────────────────────────────


def test_asset_relevance_correct():
    beats = [_make_beat(0, role=VisualRole.BROLL, intent="forest trees in wind")]
    meta = {"broll_1": {"kind": "BROLL", "tags": ["forest", "trees"]}}
    assignments = {0: "broll_1"}
    findings = AssetAppropriatenessQAEvaluator.evaluate(
        visual_beats=beats, asset_metadata=meta, asset_assignments=assignments
    )
    assert len(findings) == 0


def test_asset_relevance_intent_mismatch():
    beats = [_make_beat(0, role=VisualRole.DATA)]
    meta = {"broll_1": {"kind": "BROLL", "tags": ["nature"]}}
    assignments = {0: "broll_1"}
    findings = AssetAppropriatenessQAEvaluator.evaluate(
        visual_beats=beats, asset_metadata=meta, asset_assignments=assignments
    )
    assert any(f.code == VisualQAFindingCode.ASSET_INTENT_MISMATCH for f in findings)


def test_asset_relevance_wrong_entity():
    b = _make_beat(0)
    meta = {"ast_1": {"entity_reference": "Voyager 1"}}
    findings = AssetAppropriatenessQAEvaluator.evaluate(
        visual_beats=[b],
        asset_metadata=meta,
        asset_assignments={0: "ast_1"},
        entity_references={0: "Apollo 11"},
    )
    assert any(f.code == VisualQAFindingCode.WRONG_ENTITY for f in findings)


# ── C. Grounding & Lineage QA Tests ─────────────────────────────────────────


def test_grounding_valid_generated_provenance():
    claim_id = uuid4()
    evidence_id = uuid4()
    source_id = uuid4()
    plan = VisualExplanationPlan(
        visual_beat_id=uuid4(),
        source_editorial_beat_indices=(0,),
        explanation_type=ExplanationType.BAR_CHART,
        chart_spec=ChartSpec(
            id="chart_1",
            visual_beat_id=uuid4(),
            explanation_type=ExplanationType.BAR_CHART,
            title="Comparison",
            data_points=(
                DataPoint(
                    label="Alpha",
                    value=10.0,
                    source_claim_id=claim_id,
                    source_evidence_id=evidence_id,
                    source_id=source_id,
                    grounding_state=GroundingState.VERIFIED,
                ),
            ),
        ),
    )
    art = GeneratedVisualArtifact(
        artifact_id="art_1",
        explanation_type=ExplanationType.BAR_CHART,
        svg_path=Path("art.svg"),
        png_path=Path("art.png"),
        width=1920,
        height=1080,
        content_sha256="a" * 64,
        provenance=VisualArtifactProvenance(
            visual_beat_id=uuid4(),
            spec_id="chart_1",
            source_claim_ids=(claim_id,),
            source_evidence_ids=(evidence_id,),
            source_ids=(source_id,),
            generator_version="1.0",
            generated_at=datetime.now(UTC),
        ),
    )
    findings = VisualGroundingQAEvaluator.evaluate(
        visual_beats=[_make_beat(0)],
        explanation_plans=[plan],
        artifacts=[art],
    )
    assert len(findings) == 0


def test_grounding_ungrounded_data_and_missing_hash():
    claim_id = uuid4()
    plan = VisualExplanationPlan(
        visual_beat_id=uuid4(),
        source_editorial_beat_indices=(0,),
        explanation_type=ExplanationType.BAR_CHART,
        chart_spec=ChartSpec(
            id="chart_1",
            visual_beat_id=uuid4(),
            explanation_type=ExplanationType.BAR_CHART,
            title="Comparison",
            data_points=(
                DataPoint(
                    label="Ungrounded Point",
                    value=42.0,
                    source_claim_id=claim_id,
                    source_evidence_id=uuid4(),
                    source_id=uuid4(),
                    grounding_state=GroundingState.UNCERTAIN,
                ),
            ),
        ),
    )
    art_with_bad_hash = SimpleNamespace(
        id="art_bad",
        content_sha256="short_hash",
    )
    findings = VisualGroundingQAEvaluator.evaluate(
        visual_beats=[_make_beat(0)],
        explanation_plans=[plan],
        artifacts=[art_with_bad_hash],  # type: ignore
    )
    codes = [f.code for f in findings]
    assert VisualQAFindingCode.UNGROUNDED_VISUAL_DATA in codes
    assert VisualQAFindingCode.ARTIFACT_HASH_MISSING in codes


# ── D. Diagram / Chart Readability QA Tests ─────────────────────────────────


def test_diagram_readability_text_overflow():
    node = DiagramNode(
        id="node_1",
        label="This label is exceptionally long and exceeds eighty characters in total length for test",
        order=0,
        source_claim_ids=(uuid4(),),
    )
    plan = VisualExplanationPlan(
        visual_beat_id=uuid4(),
        source_editorial_beat_indices=(0,),
        explanation_type=ExplanationType.PROCESS_FLOW,
        diagram_spec=DiagramSpec(
            id="diag_1",
            visual_beat_id=uuid4(),
            explanation_type=ExplanationType.PROCESS_FLOW,
            title="Flow",
            nodes=(node,),
        ),
    )
    findings = ExplanatoryVisualReadabilityQAEvaluator.evaluate(explanation_plans=[plan])
    assert any(f.code == VisualQAFindingCode.TEXT_OVERFLOW for f in findings)


def test_diagram_readability_excessive_complexity():
    nodes = tuple(
        DiagramNode(id=f"node_{i}", label=f"N{i}", order=i, source_claim_ids=(uuid4(),))
        for i in range(15)
    )
    plan = VisualExplanationPlan(
        visual_beat_id=uuid4(),
        source_editorial_beat_indices=(0,),
        explanation_type=ExplanationType.PROCESS_FLOW,
        diagram_spec=DiagramSpec(
            id="diag_1",
            visual_beat_id=uuid4(),
            explanation_type=ExplanationType.PROCESS_FLOW,
            title="Complex Flow",
            nodes=nodes,
        ),
    )
    findings = ExplanatoryVisualReadabilityQAEvaluator.evaluate(explanation_plans=[plan])
    assert any(f.code == VisualQAFindingCode.EXCESSIVE_VISUAL_COMPLEXITY for f in findings)


def test_evidence_card_missing_source_label():
    plan = VisualExplanationPlan(
        visual_beat_id=uuid4(),
        source_editorial_beat_indices=(0,),
        explanation_type=ExplanationType.EVIDENCE_CARD,
        evidence_spec=EvidenceVisualSpec(
            id="ev_1",
            visual_beat_id=uuid4(),
            explanation_type=ExplanationType.EVIDENCE_CARD,
            title="Evidence",
            statement="Valid statement.",
            source_label="Unknown Source",  # Triggers missing verified attribution
            source_claim_id=uuid4(),
            source_evidence_id=uuid4(),
            source_id=uuid4(),
        ),
    )
    findings = ExplanatoryVisualReadabilityQAEvaluator.evaluate(explanation_plans=[plan])
    assert any(f.code == VisualQAFindingCode.SOURCE_LABEL_MISSING for f in findings)


# ── E. Camera QA Tests ──────────────────────────────────────────────────────


def test_camera_valid_framing():
    plan = _make_camera_plan(
        0,
        intent=CameraIntent.PUSH_IN,
        focus_region=FocusRegion(x=0.2, y=0.2, width=0.6, height=0.6),
    )
    findings = CameraQAEvaluator.evaluate(camera_plans=[plan])
    assert len(findings) == 0


def test_camera_aggressive_motion():
    plan = _make_camera_plan(
        0,
        intent=CameraIntent.PUSH_IN,
        strength=MotionStrength.EMPHATIC,
    )
    findings = CameraQAEvaluator.evaluate(camera_plans=[plan])
    assert any(f.code == VisualQAFindingCode.MOTION_TOO_AGGRESSIVE for f in findings)


def test_camera_rapid_direction_reversal():
    p1 = _make_camera_plan(0, intent=CameraIntent.PAN_LEFT)
    p2 = _make_camera_plan(1, intent=CameraIntent.PAN_RIGHT)
    findings = CameraQAEvaluator.evaluate(camera_plans=[p1, p2])
    assert any(f.code == VisualQAFindingCode.RAPID_DIRECTION_REVERSAL for f in findings)


# ── F. Transition QA Tests ──────────────────────────────────────────────────


def test_transition_cut_and_fallback():
    t1 = _make_transition_plan(0, requested=TransitionIntent.CUT, applied=TransitionIntent.CUT)
    t2 = _make_transition_plan(
        1,
        requested=TransitionIntent.CROSSFADE,
        applied=TransitionIntent.CUT,
        fallback_reason="UNSUPPORTED_TRANSITION_CUT_FALLBACK",
    )
    findings = TransitionQAEvaluator.evaluate(transition_plans=[t1, t2])
    assert any(f.code == VisualQAFindingCode.TRANSITION_FALLBACK_USED for f in findings)
    assert all(f.severity == VisualQASeverity.INFO for f in findings)


def test_transition_duration_violation():
    t = _make_transition_plan(
        0,
        requested=TransitionIntent.CROSSFADE,
        applied=TransitionIntent.CROSSFADE,
        duration_ms=600,
    )
    findings = TransitionQAEvaluator.evaluate(transition_plans=[t])
    assert any(f.code == VisualQAFindingCode.EXCESSIVE_TRANSITION_DURATION for f in findings)


# ── G. Composition & Safe Frame QA Tests ────────────────────────────────────


def test_composition_invalid_dimensions():
    findings = CompositionQAEvaluator.evaluate(canvas_width=1280, canvas_height=720)
    assert any(f.code == VisualQAFindingCode.INVALID_CANVAS_DIMENSIONS for f in findings)


def test_composition_safe_frame_violation():
    # Focus region extending too close to edge (< 0.04)
    plan = _make_camera_plan(
        0,
        focus_region=FocusRegion(x=0.01, y=0.01, width=0.5, height=0.5),
    )
    findings = CompositionQAEvaluator.evaluate(camera_plans=[plan])
    assert any(f.code == VisualQAFindingCode.SAFE_FRAME_VIOLATION for f in findings)


# ── H. Visual Repetition QA Tests ───────────────────────────────────────────


def test_repetition_excessive_hold():
    beats = [
        _make_beat(0, duration_ms=4500),
        _make_beat(1, start_ms=4500, duration_ms=4500),
    ]
    findings = VisualRepetitionQAEvaluator.evaluate(
        visual_beats=beats, asset_assignments={0: "same_ast", 1: "same_ast"}
    )
    assert any(f.code == VisualQAFindingCode.EXCESSIVE_ASSET_HOLD for f in findings)


# ── I. Blank / Broken Artifact Detection Tests ──────────────────────────────


def test_artifact_valid(tmp_path: Path):
    png = _create_test_png(tmp_path / "valid.png", blank=False)
    findings = PhysicalArtifactQAEvaluator.evaluate_file(png)
    assert len(findings) == 0


def test_artifact_missing(tmp_path: Path):
    findings = PhysicalArtifactQAEvaluator.evaluate_file(tmp_path / "nonexistent.png")
    assert any(f.code == VisualQAFindingCode.MISSING_ARTIFACT for f in findings)
    assert any(f.severity == VisualQASeverity.BLOCKER for f in findings)


def test_artifact_zero_byte(tmp_path: Path):
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    findings = PhysicalArtifactQAEvaluator.evaluate_file(empty)
    assert any(f.code == VisualQAFindingCode.ZERO_BYTE_ARTIFACT for f in findings)


def test_artifact_blank_frame(tmp_path: Path):
    blank_png = _create_test_png(tmp_path / "blank.png", blank=True)
    findings = PhysicalArtifactQAEvaluator.evaluate_file(blank_png)
    assert any(f.code == VisualQAFindingCode.BLANK_FRAME_DETECTED for f in findings)
    assert any(f.severity == VisualQASeverity.BLOCKER for f in findings)


def test_artifact_corrupt(tmp_path: Path):
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"NOT_A_PNG_FILE_DATA_CORRUPT")
    findings = PhysicalArtifactQAEvaluator.evaluate_file(corrupt)
    assert any(f.code == VisualQAFindingCode.CORRUPT_ARTIFACT for f in findings)


# ── J. Timing QA Tests ──────────────────────────────────────────────────────


def test_timing_exact_coverage():
    b0 = _make_beat(0, start_ms=0, duration_ms=2000)
    b1 = _make_beat(1, start_ms=2000, duration_ms=3000)
    findings = VisualTimingQAEvaluator.evaluate(visual_beats=[b0, b1])
    assert len(findings) == 0


def test_timing_gap_and_overlap():
    b0 = _make_beat(0, start_ms=0, duration_ms=2000)
    b_gap = _make_beat(1, start_ms=2100, duration_ms=2000)
    findings_gap = VisualTimingQAEvaluator.evaluate(visual_beats=[b0, b_gap])
    assert any(f.code == VisualQAFindingCode.TIMING_GAP for f in findings_gap)

    b_overlap = _make_beat(1, start_ms=1900, duration_ms=2000)
    findings_overlap = VisualTimingQAEvaluator.evaluate(visual_beats=[b0, b_overlap])
    assert any(f.code == VisualQAFindingCode.TIMING_OVERLAP for f in findings_overlap)


# ── K. Acceptance Policy & Deduplication Tests ──────────────────────────────


def test_acceptance_policy_pass():
    b0 = _make_beat(0, start_ms=0, duration_ms=3000)
    res = VisualEditorialQAService.evaluate(visual_beats=[b0])
    assert res.status == VisualQAStatus.PASS
    assert res.is_accepted is True


def test_acceptance_policy_revise():
    b0 = _make_beat(0, duration_ms=5000)
    b1 = _make_beat(1, start_ms=5000, duration_ms=5000)
    res = VisualEditorialQAService.evaluate(
        visual_beats=[b0, b1],
        asset_assignments={0: "same_ast", 1: "same_ast"},
    )
    assert res.status == VisualQAStatus.REVISE
    assert res.requires_revision is True
    assert len(res.recommendations) > 0


def test_acceptance_policy_fail_on_blocker(tmp_path: Path):
    b0 = _make_beat(0, start_ms=0, duration_ms=3000)
    blank_png = _create_test_png(tmp_path / "blank.png", blank=True)
    res = VisualEditorialQAService.evaluate(
        visual_beats=[b0],
        rendered_artifacts=[blank_png],
    )
    assert res.status == VisualQAStatus.FAIL
    assert res.is_blocked is True
    assert res.blocker_count > 0


def test_deduplication():
    f1 = VisualQAFinding(
        code=VisualQAFindingCode.ASSET_INTENT_MISMATCH,
        severity=VisualQASeverity.WARNING,
        subsystem=VisualQASubsystem.ASSET,
        beat_index=0,
        asset_id="a1",
        explanation="Mismatch from source A",
        contributing_sources=["SourceA"],
    )
    f2 = VisualQAFinding(
        code=VisualQAFindingCode.ASSET_INTENT_MISMATCH,
        severity=VisualQASeverity.ERROR,
        subsystem=VisualQASubsystem.ASSET,
        beat_index=0,
        asset_id="a1",
        explanation="Mismatch from source B",
        contributing_sources=["SourceB"],
    )
    deduped = VisualEditorialQAService.deduplicate_findings([f1, f2])
    assert len(deduped) == 1
    assert deduped[0].severity == VisualQASeverity.ERROR
    assert set(deduped[0].contributing_sources) == {"SourceA", "SourceB"}


# ── L. Render Acceptance Gate Tests ─────────────────────────────────────────


def test_render_gate_permits_pass():
    res = VisualQAResult(
        status=VisualQAStatus.PASS,
        highest_severity=VisualQASeverity.INFO,
    )
    assert VisualRenderGate.verify_acceptance(res) is True


def test_render_gate_blocks_revise_and_fail():
    revise_res = VisualQAResult(
        status=VisualQAStatus.REVISE,
        highest_severity=VisualQASeverity.WARNING,
    )
    with pytest.raises(VisualRenderGateError) as exc_revise:
        VisualRenderGate.verify_acceptance(revise_res)
    assert "blocked: status=REVISE" in str(exc_revise.value)

    fail_res = VisualQAResult(
        status=VisualQAStatus.FAIL,
        highest_severity=VisualQASeverity.BLOCKER,
    )
    with pytest.raises(VisualRenderGateError) as exc_fail:
        VisualRenderGate.verify_acceptance(fail_res)
    assert "blocked: status=FAIL" in str(exc_fail.value)
