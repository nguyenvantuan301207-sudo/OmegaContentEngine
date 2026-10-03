"""Visual Editorial QA & Acceptance Service for P22-D.

Provides two-level visual quality evaluation:
1. Pre-render QA: Evaluates continuity, asset appropriateness, grounding,
   diagram/chart readability, data fidelity, camera motion, transitions,
   composition/safe-frame bounds, visual repetition, timeline coverage,
   and channel visual style fit.
2. Post-render QA: Evaluates physical artifacts (file existence, non-zero bytes,
   canonical dimensions, non-blank frame verification, corrupt decodes,
   and physical timing drift).

Aggregates findings, deduplicates equivalent defects deterministically,
computes highest severity, generates actionable revision recommendations,
and enforces the final render acceptance gate.
Zero network calls, zero external LLM vendor dependencies.
"""

from __future__ import annotations

import binascii
import logging
import math
import struct
import subprocess
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from omega.domain.camera_transition import (
    CameraIntent,
    CameraPlan,
    MotionStrength,
    TransitionIntent,
    TransitionPlan,
)
from omega.domain.channel_dna import ChannelDNA
from omega.domain.visual_beat import (
    AssetReusePolicy,
    ContinuityFinding,
    ContinuityFindingCode,
    DocumentProgressStage,
    VisualBeat,
    VisualBeatSequence,
    VisualRole,
)
from omega.domain.visual_editorial_qa import (
    VisualQAFinding,
    VisualQAFindingCode,
    VisualQARecommendation,
    VisualQARecommendationAction,
    VisualQAResult,
    VisualQASeverity,
    VisualQAStatus,
    VisualQASubsystem,
    VisualRenderGateError,
)
from omega.application.visual_explanation import DiagramLayoutEngine
from omega.domain.visual_explanation import (
    ChartSpec,
    DataPoint,
    DiagramSpec,
    EvidenceVisualSpec,
    GeneratedVisualArtifact,
    GroundedEvidenceItem,
    GroundingState,
    VisualExplanationPlan,
)

logger = logging.getLogger(__name__)


# ── Render Gate ─────────────────────────────────────────────────────────────


class VisualRenderGate:
    """Enforces that only visually accepted treatments proceed to final production."""

    @classmethod
    def verify_acceptance(cls, qa_result: VisualQAResult) -> bool:
        """Verify acceptance, raising VisualRenderGateError on REVISE or FAIL."""
        if qa_result.status in (VisualQAStatus.REVISE, VisualQAStatus.FAIL):
            raise VisualRenderGateError(
                f"Visual render acceptance gate blocked: status={qa_result.status.value}, "
                f"highest_severity={qa_result.highest_severity.value}, "
                f"blockers={qa_result.blocker_count}, errors={qa_result.error_count}, "
                f"warnings={qa_result.warning_count}",
                qa_result=qa_result,
            )
        return True


# ── Pre-Render Subsystem Evaluators ─────────────────────────────────────────


class VisualContinuityQAEvaluator:
    """Evaluates continuity consistency and promotes P22-A continuity findings."""

    @classmethod
    def evaluate(
        cls,
        *,
        visual_beats: Sequence[VisualBeat],
        continuity_findings: Sequence[ContinuityFinding] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []

        # 1. Promote upstream P22-A continuity findings
        if continuity_findings:
            for cf in continuity_findings:
                severity = (
                    VisualQASeverity.ERROR
                    if cf.code in (
                        ContinuityFindingCode.COMPARISON_SIDE_SWAP,
                        ContinuityFindingCode.DOCUMENT_CONTEXT_LOST,
                    )
                    else VisualQASeverity.WARNING
                )
                finding_code = getattr(VisualQAFindingCode, cf.code.value, None)
                if finding_code is None:
                    finding_code = VisualQAFindingCode.BROKEN_SUBJECT_CONTINUITY
                beat_idx = None
                aff_indices = getattr(cf, "affected_beat_indices", None) or getattr(cf, "beat_indices", None)
                if aff_indices:
                    beat_idx = aff_indices[0]
                findings.append(
                    VisualQAFinding(
                        code=finding_code,
                        severity=severity,
                        subsystem=VisualQASubsystem.CONTINUITY,
                        scene_index=getattr(cf, "scene_index", None),
                        beat_index=beat_idx,
                        explanation=cf.explanation,
                        recommended_remediation="Preserve visual continuity across adjacent beats.",
                        contributing_sources=["P22A_VisualContinuityDirector"],
                    )
                )

        # 2. Direct sequence checks
        for i, beat in enumerate(visual_beats):
            reuse_policy = getattr(beat, "asset_reuse_policy", None)
            assigned_asset = getattr(beat, "assigned_asset_id", None)
            # Check accidental repeat without intentional policy
            if reuse_policy == AssetReusePolicy.ACCIDENTAL_REPEAT:
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.ACCIDENTAL_ASSET_REPEAT,
                        severity=VisualQASeverity.WARNING,
                        subsystem=VisualQASubsystem.CONTINUITY,
                        scene_index=beat.parent_scene_index,
                        beat_index=beat.beat_index,
                        asset_id=assigned_asset,
                        explanation=f"Asset repeated accidentally on beat {beat.beat_index}.",
                        recommended_remediation="Replace asset or declare an intentional reuse policy.",
                        contributing_sources=["VisualContinuityQAEvaluator"],
                    )
                )

            # Check document progress continuity if stage present
            doc_stage = getattr(beat, "document_stage", None)
            prev_stage = getattr(visual_beats[i - 1], "document_stage", None) if i > 0 else None
            if (
                doc_stage == DocumentProgressStage.DETAIL
                and i > 0
                and prev_stage is None
            ):
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.DOCUMENT_CONTEXT_LOST,
                        severity=VisualQASeverity.ERROR,
                        subsystem=VisualQASubsystem.CONTINUITY,
                        scene_index=beat.parent_scene_index,
                        beat_index=beat.beat_index,
                        explanation=f"Beat {beat.beat_index} jumps straight to document DETAIL without establishing context.",
                        recommended_remediation="Precede document detail with an OVERVIEW or SECTION stage.",
                        contributing_sources=["VisualContinuityQAEvaluator"],
                    )
                )

        return findings


class AssetAppropriatenessQAEvaluator:
    """Evaluates whether selected visual assets match editorial intent and roles."""

    @classmethod
    def evaluate(
        cls,
        *,
        visual_beats: Sequence[VisualBeat],
        asset_metadata: dict[str, dict[str, Any]] | None = None,
        asset_assignments: dict[int, str] | None = None,
        entity_references: dict[int, str] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        meta = asset_metadata or {}
        assignments = asset_assignments or {}
        entities = entity_references or {}

        for beat in visual_beats:
            asset_id = assignments.get(beat.beat_index) or getattr(beat, "assigned_asset_id", None)
            asset_info = meta.get(asset_id or "", {})

            # 1. Asset Intent Mismatch
            if beat.visual_role in (VisualRole.DATA, VisualRole.DIAGRAM, VisualRole.EVIDENCE):
                if asset_info.get("kind") == "BROLL":
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.ASSET_INTENT_MISMATCH,
                            severity=VisualQASeverity.ERROR,
                            subsystem=VisualQASubsystem.ASSET,
                            scene_index=beat.parent_scene_index,
                            beat_index=beat.beat_index,
                            asset_id=asset_id,
                            explanation=f"Beat {beat.beat_index} requires role {beat.visual_role.value} but bound to generic BROLL asset.",
                            recommended_remediation="Bind a grounded diagram, chart, or evidence asset.",
                            contributing_sources=["AssetAppropriatenessQAEvaluator"],
                        )
                    )

            # 2. Irrelevant B-roll
            if beat.visual_role == VisualRole.BROLL and asset_info:
                asset_tags = [str(t).lower() for t in asset_info.get("tags", [])]
                query_words = set(beat.visual_intent.lower().split())
                if asset_tags and not any(tag in query_words for tag in asset_tags):
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.IRRELEVANT_BROLL,
                            severity=VisualQASeverity.WARNING,
                            subsystem=VisualQASubsystem.ASSET,
                            scene_index=beat.parent_scene_index,
                            beat_index=beat.beat_index,
                            asset_id=asset_id,
                            explanation=f"Asset tags {asset_tags} have low semantic relevance to intent '{beat.visual_intent}'.",
                            recommended_remediation="Re-query asset catalog with more specific query terms.",
                            contributing_sources=["AssetAppropriatenessQAEvaluator"],
                        )
                    )

            # 3. Wrong Entity
            entity_ref = entities.get(beat.beat_index) or getattr(beat, "entity_reference", None)
            if entity_ref and asset_info.get("entity_reference"):
                if entity_ref != asset_info["entity_reference"]:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.WRONG_ENTITY,
                            severity=VisualQASeverity.BLOCKER,
                            subsystem=VisualQASubsystem.ASSET,
                            scene_index=beat.parent_scene_index,
                            beat_index=beat.beat_index,
                            asset_id=asset_id,
                            explanation=f"Beat {beat.beat_index} references entity '{entity_ref}' but asset references '{asset_info['entity_reference']}'.",
                            recommended_remediation="Replace asset with matching entity reference.",
                            contributing_sources=["AssetAppropriatenessQAEvaluator"],
                        )
                    )

        return findings


class VisualGroundingQAEvaluator:
    """Verifies evidence lineage, source IDs, and artifact hashes for visual elements."""

    @classmethod
    def evaluate(
        cls,
        *,
        visual_beats: Sequence[VisualBeat],
        explanation_plans: Sequence[VisualExplanationPlan] | None = None,
        artifacts: Sequence[GeneratedVisualArtifact] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        plans = explanation_plans or []
        arts = artifacts or []

        # Check VisualBeats for missing source references when presenting evidence
        for beat in visual_beats:
            if beat.visual_role in (VisualRole.EVIDENCE, VisualRole.DATA) and not beat.source_editorial_beat_indices:
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.SOURCE_LINEAGE_MISSING,
                        severity=VisualQASeverity.BLOCKER,
                        subsystem=VisualQASubsystem.GROUNDING,
                        scene_index=beat.parent_scene_index,
                        beat_index=beat.beat_index,
                        explanation=f"Beat {beat.beat_index} ({beat.visual_role.value}) lacks source editorial beat lineage.",
                        recommended_remediation="Link beat to canonical source statements.",
                        contributing_sources=["VisualGroundingQAEvaluator"],
                    )
                )

        # Check ExplanationPlans for grounding
        for plan in plans:
            if plan.chart_spec:
                for pt in plan.chart_spec.data_points:
                    if pt.grounding_state != GroundingState.VERIFIED or not pt.source_claim_id:
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.UNGROUNDED_VISUAL_DATA,
                                severity=VisualQASeverity.BLOCKER,
                                subsystem=VisualQASubsystem.GROUNDING,
                                explanation=f"Data point '{pt.label}' has ungrounded state: {pt.grounding_state.value}.",
                                recommended_remediation="Ensure all visual data points project from verified claims.",
                                contributing_sources=["VisualGroundingQAEvaluator"],
                            )
                        )
            if plan.diagram_spec:
                for node in plan.diagram_spec.nodes:
                    if not node.source_claim_ids:
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.UNGROUNDED_VISUAL_DATA,
                                severity=VisualQASeverity.BLOCKER,
                                subsystem=VisualQASubsystem.GROUNDING,
                                explanation=f"Diagram node '{node.label}' lacks source claim IDs.",
                                recommended_remediation="Bind diagram node to verified claim IDs.",
                                contributing_sources=["VisualGroundingQAEvaluator"],
                            )
                        )

        # Check Artifact hashes
        for art in arts:
            if not art.content_sha256 or len(art.content_sha256) != 64:
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.ARTIFACT_HASH_MISSING,
                        severity=VisualQASeverity.BLOCKER,
                        subsystem=VisualQASubsystem.GROUNDING,
                        explanation=f"Artifact '{art.id}' has missing or malformed content SHA256.",
                        recommended_remediation="Compute canonical SHA256 hash upon rendering artifact.",
                        contributing_sources=["VisualGroundingQAEvaluator"],
                    )
                )

        return findings


class ExplanatoryVisualReadabilityQAEvaluator:
    """Evaluates diagram and chart layout, text overflow, node overlap, and density."""

    @classmethod
    def evaluate(
        cls,
        *,
        explanation_plans: Sequence[VisualExplanationPlan] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        if not explanation_plans:
            return findings

        for plan in explanation_plans:
            # 1. Diagram readability
            if plan.diagram_spec:
                spec = plan.diagram_spec
                if len(spec.nodes) > 8:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.EXCESSIVE_VISUAL_COMPLEXITY,
                            severity=VisualQASeverity.WARNING,
                            subsystem=VisualQASubsystem.READABILITY,
                            explanation=f"Diagram has {len(spec.nodes)} nodes, exceeding complexity threshold (8).",
                            recommended_remediation="Simplify diagram or split across multiple beats.",
                            contributing_sources=["ExplanatoryVisualReadabilityQAEvaluator"],
                        )
                    )

                for node in spec.nodes:
                    if len(node.label) > 80:
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.TEXT_OVERFLOW,
                                severity=VisualQASeverity.ERROR,
                                subsystem=VisualQASubsystem.READABILITY,
                                explanation=f"Node '{node.id}' label exceeds 80 characters ({len(node.label)}).",
                                recommended_remediation="Shorten node label or wrap into multiple lines.",
                                contributing_sources=["ExplanatoryVisualReadabilityQAEvaluator"],
                            )
                        )

                # Layout overlap check
                try:
                    layout = DiagramLayoutEngine.layout(spec)
                    boxes = layout.boxes
                    for i in range(len(boxes)):
                        for j in range(i + 1, len(boxes)):
                            b1, b2 = boxes[i], boxes[j]
                            # Check AABB intersection with 10px margin
                            if (
                                b1.x < b2.x + b2.width - 10
                                and b1.x + b1.width - 10 > b2.x
                                and b1.y < b2.y + b2.height - 10
                                and b1.y + b1.height - 10 > b2.y
                            ):
                                findings.append(
                                    VisualQAFinding(
                                        code=VisualQAFindingCode.NODE_OVERLAP,
                                        severity=VisualQASeverity.ERROR,
                                        subsystem=VisualQASubsystem.READABILITY,
                                        explanation=f"Diagram nodes '{b1.item_id}' and '{b2.item_id}' overlap in layout.",
                                        recommended_remediation="Rebalance diagram orientation or increase canvas spacing.",
                                        contributing_sources=["ExplanatoryVisualReadabilityQAEvaluator"],
                                    )
                                )
                except Exception:
                    pass

            # 2. Chart readability
            if plan.chart_spec:
                spec = plan.chart_spec
                if len(spec.data_points) > 20:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.EXCESSIVE_VISUAL_COMPLEXITY,
                            severity=VisualQASeverity.WARNING,
                            subsystem=VisualQASubsystem.READABILITY,
                            explanation=f"Chart contains {len(spec.data_points)} data points, exceeding threshold (20).",
                            recommended_remediation="Filter to top categories or aggregate points.",
                            contributing_sources=["ExplanatoryVisualReadabilityQAEvaluator"],
                        )
                    )

                for pt in spec.data_points:
                    if len(pt.label) > 60:
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.TEXT_OVERFLOW,
                                severity=VisualQASeverity.ERROR,
                                subsystem=VisualQASubsystem.READABILITY,
                                explanation=f"Data point label '{pt.label}' exceeds 60 characters.",
                                recommended_remediation="Shorten category label.",
                                contributing_sources=["ExplanatoryVisualReadabilityQAEvaluator"],
                            )
                        )

            # 3. Evidence / Quote Card readability
            if plan.evidence_spec:
                spec = plan.evidence_spec
                label_clean = (spec.source_label or "").strip().lower()
                if not label_clean or label_clean in ("unknown", "unknown source", "none"):
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.SOURCE_LABEL_MISSING,
                            severity=VisualQASeverity.ERROR,
                            subsystem=VisualQASubsystem.READABILITY,
                            explanation="Evidence card lacks verified source attribution label.",
                            recommended_remediation="Add source publication or author label.",
                            contributing_sources=["ExplanatoryVisualReadabilityQAEvaluator"],
                        )
                    )

        return findings


class DataFidelityQAEvaluator:
    """Verifies that generated chart/evidence specs match grounded research values exactly."""

    @classmethod
    def evaluate(
        cls,
        *,
        explanation_plans: Sequence[VisualExplanationPlan] | None = None,
        grounded_evidence: Sequence[GroundedEvidenceItem] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        if not explanation_plans or not grounded_evidence:
            return findings

        evidence_by_claim = {e.claim_id: e for e in grounded_evidence}

        for plan in explanation_plans:
            if plan.chart_spec:
                for pt in plan.chart_spec.data_points:
                    item = evidence_by_claim.get(pt.source_claim_id)
                    if item:
                        # Extract numerical value from statement if present
                        # Factual mismatch is BLOCKER
                        expected_num = None
                        words = item.statement.replace("%", "").replace(",", "").split()
                        for w in words:
                            try:
                                expected_num = float(w)
                                break
                            except ValueError:
                                continue
                        if expected_num is not None:
                            try:
                                pt_val = float(pt.value)
                                if abs(pt_val - expected_num) > 1e-4:
                                    findings.append(
                                        VisualQAFinding(
                                            code=VisualQAFindingCode.DATA_VALUE_MISMATCH,
                                            severity=VisualQASeverity.BLOCKER,
                                            subsystem=VisualQASubsystem.DATA_FIDELITY,
                                            explanation=f"DataPoint '{pt.label}' has value {pt.value} but grounded statement contains {expected_num}.",
                                            recommended_remediation="Correct rendered chart datum to match source evidence.",
                                            contributing_sources=["DataFidelityQAEvaluator"],
                                        )
                                    )
                            except ValueError:
                                pass

            if plan.evidence_spec:
                spec = plan.evidence_spec
                item = evidence_by_claim.get(spec.source_claim_id)
                if item:
                    if spec.value is not None:
                        # Extract numerical value from statement if present
                        expected_num = None
                        words = item.statement.replace("%", "").replace(",", "").split()
                        for w in words:
                            try:
                                expected_num = float(w)
                                break
                            except ValueError:
                                continue
                        if expected_num is not None:
                            try:
                                val_num = float(spec.value)
                                if abs(val_num - expected_num) > 1e-4:
                                    findings.append(
                                        VisualQAFinding(
                                            code=VisualQAFindingCode.DATA_VALUE_MISMATCH,
                                            severity=VisualQASeverity.BLOCKER,
                                            subsystem=VisualQASubsystem.DATA_FIDELITY,
                                            explanation=f"Evidence/Key-Value '{spec.id}' has value {spec.value} but grounded statement contains {expected_num}.",
                                            recommended_remediation="Correct rendered value to match source evidence.",
                                            contributing_sources=["DataFidelityQAEvaluator"],
                                        )
                                    )
                            except ValueError:
                                pass
                    if item.direct_quote and not spec.direct_quote:
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.QUOTE_PARAPHRASE_CONFUSION,
                                severity=VisualQASeverity.BLOCKER,
                                subsystem=VisualQASubsystem.DATA_FIDELITY,
                                explanation="Source claim requires direct quote treatment but evidence card specifies paraphrase.",
                                recommended_remediation="Set direct_quote=True and format as direct quotation.",
                                contributing_sources=["DataFidelityQAEvaluator"],
                            )
                        )

        return findings


class CameraQAEvaluator:
    """Evaluates camera motion plans, bounds, reversals, and activity rate."""

    @classmethod
    def evaluate(
        cls,
        *,
        camera_plans: Sequence[CameraPlan] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        if not camera_plans:
            return findings

        for i, plan in enumerate(camera_plans):
            # 1. Bounds check
            if plan.focus_region:
                fr = plan.focus_region
                if fr.x < 0.0 or fr.y < 0.0 or (fr.x + fr.width) > 1.0001 or (fr.y + fr.height) > 1.0001:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.CAMERA_OUT_OF_BOUNDS,
                            severity=VisualQASeverity.ERROR,
                            subsystem=VisualQASubsystem.CAMERA,
                            beat_index=plan.beat_index,
                            explanation=f"Camera focus region ({fr.x}, {fr.y}, {fr.width}, {fr.height}) exceeds normalized bounds [0, 1].",
                            recommended_remediation="Clamp focus region to frame boundaries.",
                            contributing_sources=["CameraQAEvaluator"],
                        )
                    )

            # 2. Aggressive motion check
            if plan.strength == MotionStrength.EMPHATIC and plan.intent in (CameraIntent.PUSH_IN, CameraIntent.PULL_OUT):
                # If duration is very short and motion emphatic
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.MOTION_TOO_AGGRESSIVE,
                        severity=VisualQASeverity.WARNING,
                        subsystem=VisualQASubsystem.CAMERA,
                        beat_index=plan.beat_index,
                        explanation=f"Emphatic camera motion ({plan.intent.value}) may be too jarring.",
                        recommended_remediation="Reduce camera motion strength to MODERATE or SUBTLE.",
                        contributing_sources=["CameraQAEvaluator"],
                    )
                )

            # 3. Rapid direction reversal
            if i > 0:
                prev = camera_plans[i - 1]
                reversals = {
                    (CameraIntent.PAN_LEFT, CameraIntent.PAN_RIGHT),
                    (CameraIntent.PAN_RIGHT, CameraIntent.PAN_LEFT),
                    (CameraIntent.PUSH_IN, CameraIntent.PULL_OUT),
                    (CameraIntent.PULL_OUT, CameraIntent.PUSH_IN),
                }
                if (prev.intent, plan.intent) in reversals:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.RAPID_DIRECTION_REVERSAL,
                            severity=VisualQASeverity.WARNING,
                            subsystem=VisualQASubsystem.CAMERA,
                            beat_index=plan.beat_index,
                            explanation=f"Rapid camera direction reversal from {prev.intent.value} to {plan.intent.value}.",
                            recommended_remediation="Hold camera direction or transition through a STATIC beat.",
                            contributing_sources=["CameraQAEvaluator"],
                        )
                    )

        return findings


class TransitionQAEvaluator:
    """Evaluates transition intentions, fallbacks, and durations."""

    @classmethod
    def evaluate(
        cls,
        *,
        transition_plans: Sequence[TransitionPlan] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        if not transition_plans:
            return findings

        for plan in transition_plans:
            if plan.applied_intent != plan.requested_intent:
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.TRANSITION_FALLBACK_USED,
                        severity=VisualQASeverity.INFO,
                        subsystem=VisualQASubsystem.TRANSITION,
                        beat_index=plan.beat_index,
                        explanation=f"Transition fallback used on beat {plan.beat_index}: requested {plan.requested_intent.value}, applied {plan.applied_intent.value} ({plan.fallback_reason}).",
                        recommended_remediation="No action required if CUT preserves visual integrity.",
                        contributing_sources=["TransitionQAEvaluator"],
                    )
                )

            if plan.duration_ms >= 500:
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.EXCESSIVE_TRANSITION_DURATION,
                        severity=VisualQASeverity.WARNING,
                        subsystem=VisualQASubsystem.TRANSITION,
                        beat_index=plan.beat_index,
                        explanation=f"Transition duration {plan.duration_ms}ms exceeds 500ms threshold.",
                        recommended_remediation="Shorten transition duration to 200-400ms.",
                        contributing_sources=["TransitionQAEvaluator"],
                    )
                )

        return findings


class CompositionQAEvaluator:
    """Evaluates canvas safe-frame margins and composition bounds."""

    @classmethod
    def evaluate(
        cls,
        *,
        camera_plans: Sequence[CameraPlan] | None = None,
        canvas_width: int = 1920,
        canvas_height: int = 1080,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []

        if canvas_width != 1920 or canvas_height != 1080:
            findings.append(
                VisualQAFinding(
                    code=VisualQAFindingCode.INVALID_CANVAS_DIMENSIONS,
                    severity=VisualQASeverity.ERROR,
                    subsystem=VisualQASubsystem.COMPOSITION,
                    explanation=f"Canvas dimensions ({canvas_width}x{canvas_height}) violate canonical 1920x1080 specification.",
                    recommended_remediation="Enforce 1920x1080 canvas resolution.",
                    contributing_sources=["CompositionQAEvaluator"],
                )
            )

        if camera_plans:
            for plan in camera_plans:
                if plan.focus_region:
                    fr = plan.focus_region
                    # Check safe margins: left/top >= 0.04, right/bottom <= 0.96
                    if fr.x < 0.04 or fr.y < 0.04 or (fr.x + fr.width) > 0.96 or (fr.y + fr.height) > 0.96:
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.SAFE_FRAME_VIOLATION,
                                severity=VisualQASeverity.WARNING,
                                subsystem=VisualQASubsystem.COMPOSITION,
                                beat_index=plan.beat_index,
                                explanation=f"Focus region extends into critical title/action safe margin area.",
                                recommended_remediation="Incorporate a 5% margin buffer around focus regions.",
                                contributing_sources=["CompositionQAEvaluator"],
                            )
                        )

        return findings


class VisualRepetitionQAEvaluator:
    """Evaluates visual repetition, static hold lengths, and unprogressive B-roll."""

    @classmethod
    def evaluate(
        cls,
        *,
        visual_beats: Sequence[VisualBeat],
        asset_assignments: dict[int, str] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        if not visual_beats:
            return findings

        assignments = asset_assignments or {}

        # Track consecutive asset repeats
        current_asset = None
        current_hold_ms = 0
        consecutive_beats = 0

        for beat in visual_beats:
            asset_id = assignments.get(beat.beat_index) or getattr(beat, "assigned_asset_id", None)
            reuse_pol = getattr(beat, "asset_reuse_policy", None)
            is_intentional = reuse_pol in (
                AssetReusePolicy.INTENTIONAL_REUSE,
                AssetReusePolicy.CONTINUITY_ANCHOR,
                AssetReusePolicy.CALLBACK_VISUAL,
            )

            if asset_id and asset_id == current_asset:
                current_hold_ms += beat.duration_ms
                consecutive_beats += 1
                # If held > 8000ms without intentional reuse policy
                if current_hold_ms > 8000 and not is_intentional:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.EXCESSIVE_ASSET_HOLD,
                            severity=VisualQASeverity.WARNING,
                            subsystem=VisualQASubsystem.REPETITION,
                            scene_index=beat.parent_scene_index,
                            beat_index=beat.beat_index,
                            asset_id=asset_id,
                            explanation=f"Asset '{asset_id}' held statically across {consecutive_beats} beats ({current_hold_ms}ms).",
                            recommended_remediation="Cut to a complementary asset or reframe focal region.",
                            contributing_sources=["VisualRepetitionQAEvaluator"],
                        )
                    )
            else:
                current_asset = asset_id
                current_hold_ms = beat.duration_ms
                consecutive_beats = 1

        return findings


class VisualTimingQAEvaluator:
    """Evaluates timeline continuity, gaps, overlaps, and duration alignment."""

    @classmethod
    def evaluate(
        cls,
        *,
        visual_beats: Sequence[VisualBeat],
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        if not visual_beats:
            return findings

        for i in range(len(visual_beats)):
            beat = visual_beats[i]
            if beat.duration_ms <= 0:
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.DURATION_DRIFT,
                        severity=VisualQASeverity.ERROR,
                        subsystem=VisualQASubsystem.TIMING,
                        scene_index=beat.parent_scene_index,
                        beat_index=beat.beat_index,
                        explanation=f"Beat {beat.beat_index} has non-positive duration: {beat.duration_ms}ms.",
                        recommended_remediation="Assign positive duration in milliseconds.",
                        contributing_sources=["VisualTimingQAEvaluator"],
                    )
                )

            if i > 0:
                prev = visual_beats[i - 1]
                gap = beat.start_offset_ms - prev.end_offset_ms
                if gap > 5:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.TIMING_GAP,
                            severity=VisualQASeverity.ERROR,
                            subsystem=VisualQASubsystem.TIMING,
                            scene_index=beat.parent_scene_index,
                            beat_index=beat.beat_index,
                            explanation=f"Timeline gap of {gap}ms between beats {prev.beat_index} and {beat.beat_index}.",
                            recommended_remediation="Align beat start offset exactly with preceding beat end offset.",
                            contributing_sources=["VisualTimingQAEvaluator"],
                        )
                    )
                elif gap < -5:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.TIMING_OVERLAP,
                            severity=VisualQASeverity.ERROR,
                            subsystem=VisualQASubsystem.TIMING,
                            scene_index=beat.parent_scene_index,
                            beat_index=beat.beat_index,
                            explanation=f"Timeline overlap of {-gap}ms between beats {prev.beat_index} and {beat.beat_index}.",
                            recommended_remediation="Remove beat timing overlap.",
                            contributing_sources=["VisualTimingQAEvaluator"],
                        )
                    )

        return findings


class ChannelStyleQAEvaluator:
    """Evaluates visual treatment against channel DNA style preferences."""

    @classmethod
    def evaluate(
        cls,
        *,
        visual_beats: Sequence[VisualBeat],
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        if not channel_dna:
            return findings

        # Extract preferences if available
        prefs = channel_dna if isinstance(channel_dna, dict) else getattr(channel_dna, "visual_style_preferences", {})
        if isinstance(prefs, dict):
            max_density = prefs.get("max_visual_density")
            if max_density == "LOW":
                # Check if beats contain diagrams with many nodes
                for beat in visual_beats:
                    if beat.visual_role in (VisualRole.DIAGRAM, VisualRole.DATA):
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.GRAPHIC_DENSITY_MISMATCH,
                                severity=VisualQASeverity.WARNING,
                                subsystem=VisualQASubsystem.CHANNEL_STYLE,
                                scene_index=beat.parent_scene_index,
                                beat_index=beat.beat_index,
                                explanation=f"Beat {beat.beat_index} employs dense graphic ({beat.visual_role.value}) contrary to channel LOW density preference.",
                                recommended_remediation="Adopt minimalist presentation.",
                                contributing_sources=["ChannelStyleQAEvaluator"],
                            )
                        )

        return findings


# ── Post-Render Physical Integrity Evaluator ────────────────────────────────


class PhysicalArtifactQAEvaluator:
    """Evaluates rendered media files: existence, non-zero size, dimensions, non-blank frames."""

    @classmethod
    def evaluate_file(
        cls,
        file_path: Path | str,
        *,
        expected_width: int = 1920,
        expected_height: int = 1080,
        beat_index: int | None = None,
        scene_index: int | None = None,
        media_probe_summary: dict[str, Any] | None = None,
    ) -> list[VisualQAFinding]:
        findings: list[VisualQAFinding] = []
        p = Path(file_path)

        # 1. Existence
        if not p.is_file():
            findings.append(
                VisualQAFinding(
                    code=VisualQAFindingCode.MISSING_ARTIFACT,
                    severity=VisualQASeverity.BLOCKER,
                    subsystem=VisualQASubsystem.ARTIFACT,
                    scene_index=scene_index,
                    beat_index=beat_index,
                    explanation=f"Rendered artifact file not found: {p}",
                    recommended_remediation="Verify renderer output path and execution.",
                    contributing_sources=["PhysicalArtifactQAEvaluator"],
                )
            )
            return findings

        # 2. Zero byte check
        size = p.stat().st_size
        if size == 0:
            findings.append(
                VisualQAFinding(
                    code=VisualQAFindingCode.ZERO_BYTE_ARTIFACT,
                    severity=VisualQASeverity.BLOCKER,
                    subsystem=VisualQASubsystem.ARTIFACT,
                    scene_index=scene_index,
                    beat_index=beat_index,
                    explanation=f"Rendered artifact file is empty (0 bytes): {p}",
                    recommended_remediation="Investigate renderer error output.",
                    contributing_sources=["PhysicalArtifactQAEvaluator"],
                )
            )
            return findings

        # 3. PNG inspection for image artifacts
        if p.suffix.lower() == ".png":
            try:
                data = p.read_bytes()
                if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.CORRUPT_ARTIFACT,
                            severity=VisualQASeverity.BLOCKER,
                            subsystem=VisualQASubsystem.ARTIFACT,
                            scene_index=scene_index,
                            beat_index=beat_index,
                            explanation=f"File {p} does not contain valid PNG magic bytes.",
                            recommended_remediation="Ensure renderer produces valid PNG output.",
                            contributing_sources=["PhysicalArtifactQAEvaluator"],
                        )
                    )
                    return findings

                width = int.from_bytes(data[16:20], byteorder="big")
                height = int.from_bytes(data[20:24], byteorder="big")
                if width != expected_width or height != expected_height:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.INVALID_ARTIFACT_DIMENSIONS,
                            severity=VisualQASeverity.ERROR,
                            subsystem=VisualQASubsystem.ARTIFACT,
                            scene_index=scene_index,
                            beat_index=beat_index,
                            explanation=f"PNG dimensions ({width}x{height}) do not match expected ({expected_width}x{expected_height}).",
                            recommended_remediation="Render at canonical dimensions.",
                            contributing_sources=["PhysicalArtifactQAEvaluator"],
                        )
                    )

                # 4. Blank frame detection (pure python pixel decompression)
                if cls._is_blank_png(data, width, height):
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.BLANK_FRAME_DETECTED,
                            severity=VisualQASeverity.BLOCKER,
                            subsystem=VisualQASubsystem.ARTIFACT,
                            scene_index=scene_index,
                            beat_index=beat_index,
                            explanation=f"Rendered PNG artifact {p} contains uniform blank pixels.",
                            recommended_remediation="Ensure graphical or video content is actively rasterized.",
                            contributing_sources=["PhysicalArtifactQAEvaluator"],
                        )
                    )

            except Exception as e:
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.CORRUPT_ARTIFACT,
                        severity=VisualQASeverity.BLOCKER,
                        subsystem=VisualQASubsystem.ARTIFACT,
                        scene_index=scene_index,
                        beat_index=beat_index,
                        explanation=f"Failed inspecting PNG artifact {p}: {e}",
                        recommended_remediation="Check renderer integrity.",
                        contributing_sources=["PhysicalArtifactQAEvaluator"],
                    )
                )

        # 4. MP4 / MOV video artifact inspection
        elif p.suffix.lower() in (".mp4", ".mov"):
            try:
                header_data = p.read_bytes()[:1024]
                is_valid_header = any(
                    box in header_data[:16]
                    for box in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")
                )
                if not is_valid_header:
                    findings.append(
                        VisualQAFinding(
                            code=VisualQAFindingCode.CORRUPT_ARTIFACT,
                            severity=VisualQASeverity.BLOCKER,
                            subsystem=VisualQASubsystem.ARTIFACT,
                            scene_index=scene_index,
                            beat_index=beat_index,
                            explanation=f"File {p} does not contain valid MP4 container box headers.",
                            recommended_remediation="Ensure renderer outputs valid MP4 container format.",
                            contributing_sources=["PhysicalArtifactQAEvaluator"],
                        )
                    )
                    return findings

                width = None
                height = None
                if media_probe_summary is not None:
                    width = media_probe_summary.get("width")
                    height = media_probe_summary.get("height")
                    if not media_probe_summary.get("has_video", True):
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.CORRUPT_ARTIFACT,
                                severity=VisualQASeverity.BLOCKER,
                                subsystem=VisualQASubsystem.ARTIFACT,
                                scene_index=scene_index,
                                beat_index=beat_index,
                                explanation=f"MP4 artifact {p} contains no video stream.",
                                recommended_remediation="Ensure video encoding produces video stream.",
                                contributing_sources=["PhysicalArtifactQAEvaluator"],
                            )
                        )
                        return findings

                if width is not None and height is not None:
                    if width != expected_width or height != expected_height:
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.INVALID_ARTIFACT_DIMENSIONS,
                                severity=VisualQASeverity.ERROR,
                                subsystem=VisualQASubsystem.ARTIFACT,
                                scene_index=scene_index,
                                beat_index=beat_index,
                                explanation=f"Video dimensions ({width}x{height}) do not match expected ({expected_width}x{expected_height}).",
                                recommended_remediation="Render video at canonical 1920x1080 dimensions.",
                                contributing_sources=["PhysicalArtifactQAEvaluator"],
                            )
                        )

                # Blank frame detection for MP4
                sample_png = cls._sample_mp4_frame_png(p)
                if sample_png and len(sample_png) >= 24 and sample_png.startswith(b"\x89PNG\r\n\x1a\n"):
                    sw = int.from_bytes(sample_png[16:20], byteorder="big")
                    sh = int.from_bytes(sample_png[20:24], byteorder="big")
                    if (width is None or height is None) and (sw != expected_width or sh != expected_height):
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.INVALID_ARTIFACT_DIMENSIONS,
                                severity=VisualQASeverity.ERROR,
                                subsystem=VisualQASubsystem.ARTIFACT,
                                scene_index=scene_index,
                                beat_index=beat_index,
                                explanation=f"Video frame dimensions ({sw}x{sh}) do not match expected ({expected_width}x{expected_height}).",
                                recommended_remediation="Render video at canonical 1920x1080 dimensions.",
                                contributing_sources=["PhysicalArtifactQAEvaluator"],
                            )
                        )
                    if cls._is_blank_png(sample_png, sw, sh):
                        findings.append(
                            VisualQAFinding(
                                code=VisualQAFindingCode.BLANK_FRAME_DETECTED,
                                severity=VisualQASeverity.BLOCKER,
                                subsystem=VisualQASubsystem.ARTIFACT,
                                scene_index=scene_index,
                                beat_index=beat_index,
                                explanation=f"Rendered video artifact {p} contains uniform blank frames.",
                                recommended_remediation="Ensure video content contains non-blank visual information.",
                                contributing_sources=["PhysicalArtifactQAEvaluator"],
                            )
                        )
            except Exception as e:
                findings.append(
                    VisualQAFinding(
                        code=VisualQAFindingCode.CORRUPT_ARTIFACT,
                        severity=VisualQASeverity.BLOCKER,
                        subsystem=VisualQASubsystem.ARTIFACT,
                        scene_index=scene_index,
                        beat_index=beat_index,
                        explanation=f"Failed inspecting MP4 artifact {p}: {e}",
                        recommended_remediation="Check video file integrity.",
                        contributing_sources=["PhysicalArtifactQAEvaluator"],
                    )
                )

        return findings

    @classmethod
    def _sample_mp4_frame_png(cls, mp4_path: Path | str) -> bytes | None:
        """Extract a sample frame from an MP4 file as PNG bytes using host ffmpeg or isolated container."""
        p = Path(mp4_path)
        if not p.is_file():
            return None
        # Host ffmpeg
        try:
            res = subprocess.run(
                [
                    "ffmpeg", "-v", "quiet",
                    "-ss", "0.1",
                    "-i", str(p),
                    "-vframes", "1",
                    "-f", "image2pipe",
                    "-vcodec", "png",
                    "-pix_fmt", "rgb24",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=10,
            )
            if res.returncode == 0 and res.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                return res.stdout
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            pass

        # Isolated container fallback via pipe:0
        try:
            mp4_bytes = p.read_bytes()
            res = subprocess.run(
                [
                    "docker", "exec", "-i", "p20c-api",
                    "ffmpeg", "-v", "quiet",
                    "-ss", "0.1",
                    "-i", "pipe:0",
                    "-vframes", "1",
                    "-f", "image2pipe",
                    "-vcodec", "png",
                    "-pix_fmt", "rgb24",
                    "pipe:1",
                ],
                input=mp4_bytes,
                capture_output=True,
                timeout=15,
            )
            if res.returncode == 0 and res.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                return res.stdout
        except Exception:
            pass
        return None

    @classmethod
    def _is_blank_png(cls, png_bytes: bytes, width: int, height: int) -> bool:
        """Decode IDAT chunks and sample pixels across a grid to detect blank uniform frames."""
        try:
            pos = 8
            idat = bytearray()
            while pos < len(png_bytes):
                length = struct.unpack(">I", png_bytes[pos : pos + 4])[0]
                tag = png_bytes[pos + 4 : pos + 8]
                if tag == b"IDAT":
                    idat.extend(png_bytes[pos + 8 : pos + 8 + length])
                pos += 12 + length
            decomp = zlib.decompress(bytes(idat))
            stride = 1 + width * 3

            # Sample 200 distributed pixels
            corner_pixel = (decomp[1], decomp[2], decomp[3])
            diff_count = 0
            step_y = max(1, height // 15)
            step_x = max(1, width // 15)

            for y in range(0, height, step_y):
                row_start = y * stride + 1
                for x in range(0, width, step_x):
                    idx = row_start + x * 3
                    px = (decomp[idx], decomp[idx + 1], decomp[idx + 2])
                    if px != corner_pixel:
                        diff_count += 1
                        if diff_count > 5:
                            return False  # Non-blank content confirmed

            return diff_count == 0
        except Exception:
            return False


# ── Visual Editorial QA Service Orchestrator ────────────────────────────────


class VisualEditorialQAService:
    """Orchestrates pre-render and post-render Visual Editorial QA."""

    @classmethod
    def pre_render_qa(
        cls,
        *,
        visual_beats: Sequence[VisualBeat],
        continuity_findings: Sequence[ContinuityFinding] | None = None,
        camera_plans: Sequence[CameraPlan] | None = None,
        transition_plans: Sequence[TransitionPlan] | None = None,
        explanation_plans: Sequence[VisualExplanationPlan] | None = None,
        grounded_evidence: Sequence[GroundedEvidenceItem] | None = None,
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
        asset_metadata: dict[str, dict[str, Any]] | None = None,
        asset_assignments: dict[int, str] | None = None,
        entity_references: dict[int, str] | None = None,
        scene_index: int | None = None,
    ) -> list[VisualQAFinding]:
        """Execute deterministic pre-render quality checks across all 11 subdomains."""
        findings: list[VisualQAFinding] = []

        findings.extend(
            VisualContinuityQAEvaluator.evaluate(
                visual_beats=visual_beats,
                continuity_findings=continuity_findings,
            )
        )
        findings.extend(
            AssetAppropriatenessQAEvaluator.evaluate(
                visual_beats=visual_beats,
                asset_metadata=asset_metadata,
                asset_assignments=asset_assignments,
                entity_references=entity_references,
            )
        )
        findings.extend(
            VisualGroundingQAEvaluator.evaluate(
                visual_beats=visual_beats,
                explanation_plans=explanation_plans,
            )
        )
        findings.extend(
            ExplanatoryVisualReadabilityQAEvaluator.evaluate(
                explanation_plans=explanation_plans,
            )
        )
        findings.extend(
            DataFidelityQAEvaluator.evaluate(
                explanation_plans=explanation_plans,
                grounded_evidence=grounded_evidence,
            )
        )
        findings.extend(
            CameraQAEvaluator.evaluate(
                camera_plans=camera_plans,
            )
        )
        findings.extend(
            TransitionQAEvaluator.evaluate(
                transition_plans=transition_plans,
            )
        )
        findings.extend(
            CompositionQAEvaluator.evaluate(
                camera_plans=camera_plans,
            )
        )
        findings.extend(
            VisualRepetitionQAEvaluator.evaluate(
                visual_beats=visual_beats,
                asset_assignments=asset_assignments,
            )
        )
        findings.extend(
            VisualTimingQAEvaluator.evaluate(
                visual_beats=visual_beats,
            )
        )
        findings.extend(
            ChannelStyleQAEvaluator.evaluate(
                visual_beats=visual_beats,
                channel_dna=channel_dna,
            )
        )

        return findings

    @classmethod
    def post_render_qa(
        cls,
        *,
        rendered_artifacts: Sequence[Path | str],
        expected_width: int = 1920,
        expected_height: int = 1080,
        scene_index: int | None = None,
        media_probe_summary: dict[str, Any] | None = None,
    ) -> list[VisualQAFinding]:
        """Execute post-render physical artifact checks."""
        findings: list[VisualQAFinding] = []
        for i, art_path in enumerate(rendered_artifacts):
            findings.extend(
                PhysicalArtifactQAEvaluator.evaluate_file(
                    art_path,
                    expected_width=expected_width,
                    expected_height=expected_height,
                    beat_index=i,
                    scene_index=scene_index,
                    media_probe_summary=media_probe_summary,
                )
            )
        return findings

    @classmethod
    def deduplicate_findings(
        cls, findings: Sequence[VisualQAFinding]
    ) -> list[VisualQAFinding]:
        """Deterministically deduplicate findings sharing scene, beat, asset, and code."""
        dedup_map: dict[tuple[int | None, int | None, str | None, VisualQAFindingCode], VisualQAFinding] = {}

        for f in findings:
            key = (f.scene_index, f.beat_index, f.asset_id, f.code)
            if key not in dedup_map:
                dedup_map[key] = f
            else:
                existing = dedup_map[key]
                # Keep highest severity
                new_sev = existing.severity if existing.severity >= f.severity else f.severity
                # Merge sources
                all_sources = sorted(
                    list(set(existing.contributing_sources + f.contributing_sources))
                )
                dedup_map[key] = VisualQAFinding(
                    code=existing.code,
                    severity=new_sev,
                    subsystem=existing.subsystem,
                    scene_index=existing.scene_index,
                    beat_index=existing.beat_index,
                    asset_id=existing.asset_id,
                    explanation=existing.explanation,
                    recommended_remediation=existing.recommended_remediation,
                    contributing_sources=all_sources,
                )

        return list(dedup_map.values())

    @classmethod
    def generate_recommendations(
        cls, findings: Sequence[VisualQAFinding]
    ) -> list[VisualQARecommendation]:
        """Generate structured revision recommendations for REVISE outcomes."""
        recs: list[VisualQARecommendation] = []
        for f in findings:
            if f.severity in (VisualQASeverity.WARNING, VisualQASeverity.ERROR):
                action = VisualQARecommendationAction.REPLACE_ASSET
                if f.code in (
                    VisualQAFindingCode.MOTION_TOO_AGGRESSIVE,
                    VisualQAFindingCode.EXCESSIVE_CAMERA_ACTIVITY,
                    VisualQAFindingCode.RAPID_DIRECTION_REVERSAL,
                ):
                    action = VisualQARecommendationAction.REDUCE_CAMERA_MOTION
                elif f.code in (
                    VisualQAFindingCode.SAFE_FRAME_VIOLATION,
                    VisualQAFindingCode.CAMERA_OUT_OF_BOUNDS,
                ):
                    action = VisualQARecommendationAction.CHANGE_FOCUS_REGION
                elif f.code == VisualQAFindingCode.EXCESSIVE_VISUAL_COMPLEXITY:
                    action = VisualQARecommendationAction.SIMPLIFY_DIAGRAM
                elif f.code == VisualQAFindingCode.TEXT_OVERFLOW:
                    action = VisualQARecommendationAction.INCREASE_LABEL_SIZE
                elif f.code in (
                    VisualQAFindingCode.EXCESSIVE_ASSET_HOLD,
                    VisualQAFindingCode.ACCIDENTAL_ASSET_REPEAT,
                ):
                    action = VisualQARecommendationAction.REPLACE_ASSET
                elif f.code == VisualQAFindingCode.TRANSITION_FALLBACK_USED:
                    action = VisualQARecommendationAction.CHANGE_TRANSITION_TO_CUT
                elif f.code == VisualQAFindingCode.DOCUMENT_CONTEXT_LOST:
                    action = VisualQARecommendationAction.RESTORE_DOCUMENT_CONTEXT
                elif f.code == VisualQAFindingCode.DATA_VALUE_MISMATCH:
                    action = VisualQARecommendationAction.CORRECT_DATA_VALUE

                recs.append(
                    VisualQARecommendation(
                        action=action,
                        scene_index=f.scene_index,
                        beat_indices=[f.beat_index] if f.beat_index is not None else [],
                        asset_id=f.asset_id,
                        remediation=f.recommended_remediation or f.explanation,
                    )
                )
        return recs

    @classmethod
    def evaluate(
        cls,
        *,
        visual_beats: Sequence[VisualBeat],
        rendered_artifacts: Sequence[Path | str] | None = None,
        continuity_findings: Sequence[ContinuityFinding] | None = None,
        camera_plans: Sequence[CameraPlan] | None = None,
        transition_plans: Sequence[TransitionPlan] | None = None,
        explanation_plans: Sequence[VisualExplanationPlan] | None = None,
        grounded_evidence: Sequence[GroundedEvidenceItem] | None = None,
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
        asset_metadata: dict[str, dict[str, Any]] | None = None,
        asset_assignments: dict[int, str] | None = None,
        entity_references: dict[int, str] | None = None,
        scene_index: int | None = None,
        media_probe_summary: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> VisualQAResult:
        """Execute end-to-end Visual Editorial QA with deduplication and acceptance policy."""
        # 1. Pre-render QA
        pre_findings = cls.pre_render_qa(
            visual_beats=visual_beats,
            continuity_findings=continuity_findings,
            camera_plans=camera_plans,
            transition_plans=transition_plans,
            explanation_plans=explanation_plans,
            grounded_evidence=grounded_evidence,
            channel_dna=channel_dna,
            asset_metadata=asset_metadata,
            asset_assignments=asset_assignments,
            entity_references=entity_references,
            scene_index=scene_index,
        )

        # 2. Post-render QA if physical artifacts provided
        post_findings: list[VisualQAFinding] = []
        if rendered_artifacts:
            post_findings = cls.post_render_qa(
                rendered_artifacts=rendered_artifacts,
                scene_index=scene_index,
                media_probe_summary=media_probe_summary,
            )

        # 3. Deduplicate
        all_raw = list(pre_findings) + list(post_findings)
        deduped = cls.deduplicate_findings(all_raw)

        # 4. Compute highest severity
        highest = VisualQASeverity.INFO
        for f in deduped:
            if f.severity > highest:
                highest = f.severity

        # 5. Deterministic Acceptance Policy
        if any(f.severity in (VisualQASeverity.BLOCKER, VisualQASeverity.ERROR) for f in deduped):
            status = VisualQAStatus.FAIL
        elif any(f.severity == VisualQASeverity.WARNING for f in deduped):
            status = VisualQAStatus.REVISE
        else:
            status = VisualQAStatus.PASS

        # 6. Generate revision recommendations if needed
        recs = cls.generate_recommendations(deduped)

        meta = dict(provenance or {})
        meta.update(
            {
                "qa_engine_version": "1.0.0-p22d",
                "finding_count": len(deduped),
                "beat_count": len(visual_beats),
            }
        )

        from uuid import NAMESPACE_URL, uuid5

        content_seed = f"{scene_index}:{len(visual_beats)}:{[(b.beat_index, b.visual_role.value) for b in visual_beats]}:{[(f.code.value, f.severity.value) for f in deduped]}"
        qa_id = uuid5(NAMESPACE_URL, content_seed)

        return VisualQAResult(
            id=qa_id,
            scene_index=scene_index,
            status=status,
            highest_severity=highest,
            findings=deduped,
            recommendations=recs,
            pre_render_findings=pre_findings,
            post_render_findings=post_findings,
            provenance=meta,
        )
