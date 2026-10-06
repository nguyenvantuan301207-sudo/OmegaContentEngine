"""Application-layer Visual Continuity & Editorial Beat Architecture (P22-A).

Coordinates visual beat projection from canonical EditorialBeats,
visual role taxonomy mapping, timing boundary enforcement, and visual
continuity analysis across scenes.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from omega.application.editorial_beat import (
    EditorialBeatPlan,
    EditorialBeatSpec,
    MaterializedBeatTimingPlan,
)
from omega.application.editorial_beat_planner import EditorialBeatPlanner
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualAssetRequirement,
    VisualDirection,
    VisualDirector,
    VisualRenderMode,
    VisualTemplateId,
)
from omega.domain.narrative_plan import NarrativePlan, NarrativeSectionRole
from omega.domain.visual_beat import (
    AssetReusePolicy,
    ComparisonContinuityState,
    ComparisonSide,
    ContinuityDecisionType,
    ContinuityFinding,
    ContinuityFindingCode,
    ContinuityFindingSeverity,
    ContinuityGroup,
    DocumentContinuityState,
    DocumentProgressStage,
    VisualBeat,
    VisualBeatSequence,
    VisualRole,
)

CANONICAL_PACING_VALUES: tuple[str, ...] = ("FAST", "BALANCED", "DELIBERATE")


class InformationVisualMapper:
    """Deterministically maps narrative information goals and content to VisualRole and asset types."""

    ROLE_MAPPING: dict[VisualRole, tuple[VisualRenderMode, VisualTemplateId, str]] = {
        VisualRole.ESTABLISH: (VisualRenderMode.BROLL, VisualTemplateId.BROLL_EXPLAINER, "Establish scene baseline"),
        VisualRole.EXPLAIN: (VisualRenderMode.HYBRID, VisualTemplateId.IMAGE_EXPLAINER, "General conceptual explanation"),
        VisualRole.EVIDENCE: (VisualRenderMode.TEMPLATE, VisualTemplateId.NEWS_CARD, "Primary evidence backing statement"),
        VisualRole.COMPARE: (VisualRenderMode.TEMPLATE, VisualTemplateId.COMPARISON, "Comparative evaluation of two entities"),
        VisualRole.EMPHASIZE: (VisualRenderMode.TEMPLATE, VisualTemplateId.KINETIC_TEXT, "High impact verbal emphasis"),
        VisualRole.REVEAL: (VisualRenderMode.TEMPLATE, VisualTemplateId.HERO_TITLE, "Major thematic reveal or punchline"),
        VisualRole.CONTEXTUALIZE: (VisualRenderMode.BROLL, VisualTemplateId.BROLL_EXPLAINER, "Atmospheric environmental context"),
        VisualRole.DOCUMENT: (VisualRenderMode.SCREENSHOT, VisualTemplateId.SCREENSHOT_FOCUS, "Document or artifact inspection"),
        VisualRole.DATA: (VisualRenderMode.TEMPLATE, VisualTemplateId.STATISTIC_HERO, "Quantitative proof point"),
        VisualRole.DIAGRAM: (VisualRenderMode.TEMPLATE, VisualTemplateId.FLOW_DIAGRAM, "System flow or mechanism diagram"),
        VisualRole.BROLL: (VisualRenderMode.BROLL, VisualTemplateId.BROLL_EXPLAINER, "Visual cutaway or ambient b-roll"),
        VisualRole.QUOTE: (VisualRenderMode.TEMPLATE, VisualTemplateId.QUOTE, "Direct quote or testimony"),
        VisualRole.SUMMARY: (VisualRenderMode.TEMPLATE, VisualTemplateId.RECAP, "Summary synthesis of key takeaways"),
        VisualRole.CTA: (VisualRenderMode.TEMPLATE, VisualTemplateId.CTA, "Call to action prompt"),
    }

    @classmethod
    def map_goal_to_role(
        cls,
        text: str,
        section_role: NarrativeSectionRole | None = None,
        strategy: VisualStrategy | None = None,
    ) -> tuple[VisualRole, str]:
        """Maps narration span and cues to a VisualRole and preferred asset type."""
        lower = text.lower()

        # 1. Direct Strategy Alignment
        if strategy == VisualStrategy.CTA:
            return VisualRole.CTA, "TEMPLATE"
        if strategy == VisualStrategy.CODE_DEMO:
            return VisualRole.EXPLAIN, "CODE_SNIPPET"
        if strategy == VisualStrategy.STATISTIC:
            return VisualRole.DATA, "DATA_CARD"
        if strategy == VisualStrategy.DIAGRAM:
            return VisualRole.DIAGRAM, "DIAGRAM"

        # 2. Comparison cues
        comparison_cues = ("vs", "versus", "compared to", "while", "on the other hand", "whereas", "in contrast")
        if any(re.search(rf"\b{re.escape(cue)}\b", lower) for cue in comparison_cues):
            return VisualRole.COMPARE, "COMPARISON_FRAME"

        # 3. Numeric & Data proof
        stat_keyword = any(k in lower for k in ("numbers show", "data reveals", "percent", "metric", "benchmark"))
        if re.search(r"\b\d+(?:\.\d+)?(?:%|x|k|M|m|s|ms)\b", text) or "%" in text or stat_keyword:
            return VisualRole.DATA, "DATA_CARD"

        # 4. Document / Primary Evidence cues
        doc_patterns = (r"\bdocument\b", r"\bpaper\b", r"\bfiling\b", r"\bmemo\b", r"\breport\b", r"\barticle\b", r"\bcourt record\b", r"\bsec filing\b", r"\bwhitepaper\b")
        if any(re.search(pat, lower) for pat in doc_patterns):
            return VisualRole.DOCUMENT, "DOCUMENT"

        # 5. Quotes / Testimony
        quote_cues = (r"\bquote\b", r"\btestified\b", r"\bstated that\b", r"\bsaid:\b", r"\bwrote:\b")
        if any(re.search(pat, lower) for pat in quote_cues) or ('"' in text and len(text.split('"')) >= 3):
            return VisualRole.QUOTE, "QUOTE_CARD"

        # 6. Mechanism explanation
        mechanism_cues = (r"\bhow it works\b", r"\bmechanism\b", r"\bstep by step\b", r"\barchitecture\b", r"\bunder the hood\b", r"\bprocess involves\b")
        if any(re.search(pat, lower) for pat in mechanism_cues):
            return VisualRole.DIAGRAM, "DIAGRAM"

        # 7. Major reveal / Climax / Escalation
        if section_role in (NarrativeSectionRole.ESCALATION, NarrativeSectionRole.PAYOFF) or "turns out" in lower or "shocking truth" in lower:
            return VisualRole.REVEAL, "IMAGE"

        # 8. Summary / Payoff / Takeaway
        if section_role in (NarrativeSectionRole.TAKEAWAY, NarrativeSectionRole.CLOSING) or "in summary" in lower or "to recap" in lower:
            return VisualRole.SUMMARY, "RECAP_CARD"

        # 9. Hook / Establish
        if section_role in (NarrativeSectionRole.HOOK, NarrativeSectionRole.CONTEXT) or "imagine" in lower or "in a world" in lower:
            return VisualRole.ESTABLISH, "BROLL"

        # 10. General context vs explanation
        if any(cue in lower for cue in ("city", "landscape", "office", "streets", "background", "atmosphere")):
            return VisualRole.CONTEXTUALIZE, "BROLL"

        return VisualRole.EXPLAIN, "IMAGE"


class BeatDurationPolicy:
    """Enforces deterministic timing boundaries and detects pacing defects."""

    def __init__(self, pacing: str = "BALANCED"):
        pacing_norm = str(pacing).upper()
        if pacing_norm == "RELAXED":
            pacing_norm = "DELIBERATE"
        self.pacing = pacing_norm if pacing_norm in CANONICAL_PACING_VALUES else "BALANCED"

        if self.pacing == "FAST":
            self.min_duration_ms = 1200
            self.target_duration_ms = 2500
            self.max_duration_ms = 5000
            self.hold_limit_ms = 6000
        elif self.pacing == "DELIBERATE":
            self.min_duration_ms = 2000
            self.target_duration_ms = 4500
            self.max_duration_ms = 9000
            self.hold_limit_ms = 10000
        else:  # BALANCED
            self.min_duration_ms = 1500
            self.target_duration_ms = 3500
            self.max_duration_ms = 7000
            self.hold_limit_ms = 8000

    def evaluate_beats(self, beats: list[VisualBeat]) -> list[ContinuityFinding]:
        """Scans visual beats for timing boundary violations and excessive churn."""
        findings: list[ContinuityFinding] = []
        if not beats:
            return findings

        fast_cut_run: list[int] = []

        for beat in beats:
            # 1. Cut too fast
            if beat.duration_ms < self.min_duration_ms:
                findings.append(
                    ContinuityFinding(
                        code=ContinuityFindingCode.VISUAL_CUT_TOO_FAST,
                        severity=ContinuityFindingSeverity.WARNING,
                        explanation=(
                            f"Beat {beat.beat_index} duration {beat.duration_ms}ms is below "
                            f"minimum threshold {self.min_duration_ms}ms."
                        ),
                        affected_beat_indices=[beat.beat_index],
                        remediation="Merge short beat with adjacent statement or extend duration.",
                    )
                )

            # 2. Hold too long
            if beat.duration_ms > self.hold_limit_ms:
                findings.append(
                    ContinuityFinding(
                        code=ContinuityFindingCode.VISUAL_HOLD_TOO_LONG,
                        severity=ContinuityFindingSeverity.WARNING,
                        explanation=(
                            f"Beat {beat.beat_index} duration {beat.duration_ms}ms exceeds "
                            f"maximum hold threshold {self.hold_limit_ms}ms without visual cut."
                        ),
                        affected_beat_indices=[beat.beat_index],
                        remediation="Split beat or introduce progressive reframe in P22-B.",
                    )
                )

            # Fast cut churn tracking
            if beat.duration_ms < 2000:
                fast_cut_run.append(beat.beat_index)
            else:
                if len(fast_cut_run) >= 3:
                    findings.append(
                        ContinuityFinding(
                            code=ContinuityFindingCode.EXCESSIVE_VISUAL_CHURN,
                            severity=ContinuityFindingSeverity.WARNING,
                            explanation=(
                                f"Excessive visual churn: {len(fast_cut_run)} consecutive rapid cuts "
                                f"detected on beats {fast_cut_run}."
                            ),
                            affected_beat_indices=list(fast_cut_run),
                            remediation="Group adjacent short statements to stabilize pacing.",
                        )
                    )
                fast_cut_run = []

        if len(fast_cut_run) >= 3:
            findings.append(
                ContinuityFinding(
                    code=ContinuityFindingCode.EXCESSIVE_VISUAL_CHURN,
                    severity=ContinuityFindingSeverity.WARNING,
                    explanation=(
                        f"Excessive visual churn: {len(fast_cut_run)} consecutive rapid cuts "
                        f"detected on beats {fast_cut_run}."
                    ),
                    affected_beat_indices=list(fast_cut_run),
                    remediation="Group adjacent short statements to stabilize pacing.",
                )
            )

        return findings


class VisualBeatProjector:
    """Canonical projector from EditorialBeatPlan to VisualBeatSequence.

    Takes the semantic and physical timing outputs of the canonical
    EditorialBeatPlanner and enriches them with visual roles, continuity
    identifiers, overlay intents, and asset reuse policies.
    """

    def __init__(self, pacing: str = "BALANCED"):
        self.pacing = pacing
        self.duration_policy = BeatDurationPolicy(pacing)

    def project_scene(
        self,
        scene: StoryboardScene,
        script_version: Any | None = None,
        narrative_plan: NarrativePlan | dict[str, Any] | None = None,
    ) -> VisualBeatSequence:
        """Projects a StoryboardScene into VisualBeats using the canonical EditorialBeatPlanner."""
        resolved_stmts = self._resolve_statements(scene, script_version)
        scene_duration_ms = max(int(scene.estimated_duration_seconds * 1000), 1500)

        # 1. Sole canonical planning authority: EditorialBeatPlanner
        editorial_plan = EditorialBeatPlanner.plan(
            scene=scene,
            source_statements=resolved_stmts,
        )

        # 2. Sole canonical timing allocation authority: EditorialBeatPlanner
        timing_plan = EditorialBeatPlanner.allocate_timing(
            editorial_plan,
            scene_duration_ms=scene_duration_ms,
        )

        # 3. Project into VisualBeatSequence
        return self.project(
            editorial_plan=editorial_plan,
            timing_plan=timing_plan,
            scene=scene,
            script_version=script_version,
            narrative_plan=narrative_plan,
        )

    def project(
        self,
        editorial_plan: EditorialBeatPlan,
        timing_plan: MaterializedBeatTimingPlan,
        scene: StoryboardScene,
        script_version: Any | None = None,
        narrative_plan: NarrativePlan | dict[str, Any] | None = None,
    ) -> VisualBeatSequence:
        """Project materialized editorial timing intervals into visual beats.

        The timing plan may merge short EditorialBeatSpecs.  Each projected
        VisualBeat therefore records every source editorial beat index instead
        of inventing fallback timing for source beats that were merged away.
        """
        if editorial_plan.scene_index != scene.sequence_index:
            raise ValueError("Editorial beat plan does not belong to the supplied scene")
        if timing_plan.scene_index != scene.sequence_index:
            raise ValueError("Editorial timing plan does not belong to the supplied scene")

        section_role = self._resolve_section_role(scene, script_version, narrative_plan)
        allocated_beats: list[VisualBeat] = []
        specs_by_index = {spec.beat_index: spec for spec in editorial_plan.beats}

        for timing in timing_plan.timings:
            try:
                source_specs = [specs_by_index[index] for index in timing.source_beat_indices]
            except KeyError as exc:
                raise ValueError(
                    f"Timing interval references unknown editorial beat index {exc.args[0]}"
                ) from exc

            narration = " ".join(spec.narration_span for spec in source_specs).strip()
            statement_ids = tuple(
                dict.fromkeys(
                    statement_id
                    for spec in source_specs
                    for statement_id in spec.source_statement_references
                )
            )
            preferred_strategy = next(
                (spec.preferred_visual_strategy for spec in source_specs if spec.preferred_visual_strategy),
                scene.visual_strategy,
            )

            # Determine visual role & preferred asset type
            role, asset_type = InformationVisualMapper.map_goal_to_role(
                narration,
                section_role=section_role,
                strategy=preferred_strategy,
            )

            continuity_group = self._derive_continuity_group(narration, scene)
            overlay = self._extract_overlay_intent(narration, role, scene)
            scene_id = str(scene.section_id or f"scene_{scene.sequence_index}")
            lineage_key = (
                f"{scene_id}:{scene.sequence_index}:{','.join(map(str, timing.source_beat_indices))}:"
                f"{narration}"
            )

            beat = VisualBeat(
                id=uuid5(NAMESPACE_URL, lineage_key),
                scene_id=scene_id,
                parent_scene_index=scene.sequence_index,
                beat_index=timing.materialized_index,
                source_editorial_beat_indices=timing.source_beat_indices,
                start_offset_ms=timing.start_ms,
                end_offset_ms=timing.end_ms,
                duration_ms=timing.duration_ms,
                narrative_section_role=section_role,
                script_statement_ids=list(statement_ids),
                narration_text=narration,
                visual_intent=f"Show {role.value.lower()} visual for: {narration[:70]}...",
                information_goal=f"Convey: {narration[:90]}",
                visual_role=role,
                continuity_group_id=continuity_group,
                preferred_asset_type=asset_type,
                asset_reuse_policy=(
                    AssetReusePolicy.CONTINUITY_ANCHOR
                    if timing.materialized_index == 0 and continuity_group
                    else AssetReusePolicy.NEW_ACQUISITION
                ),
                asset_query_hint=(
                    next((spec.asset_query_hint for spec in source_specs if spec.asset_query_hint), None)
                    or scene.asset_query_hint
                    or scene.visual_brief
                ),
                text_overlay_intent=overlay,
                grounding_references=scene.citations if hasattr(scene, "citations") else [],
                importance=scene.importance,
            )
            allocated_beats.append(beat)

        timing_findings = self.duration_policy.evaluate_beats(allocated_beats)

        return VisualBeatSequence(
            parent_scene_index=scene.sequence_index,
            scene_id=str(scene.section_id or f"scene_{scene.sequence_index}"),
            beats=allocated_beats,
            total_duration_ms=timing_plan.scene_duration_ms,
            continuity_findings=timing_findings,
        )

    def _resolve_statements(self, scene: StoryboardScene, script_version: Any | None) -> list[dict[str, Any]]:
        """Resolves source statements for the scene from ScriptVersion or fallback narration."""
        resolved: list[dict[str, Any]] = []
        if script_version is not None:
            sections = []
            if isinstance(script_version, dict):
                sections = script_version.get("sections", [])
            elif hasattr(script_version, "sections"):
                sections = script_version.sections

            for sec in sections:
                stmts = sec.get("statements", []) if isinstance(sec, dict) else getattr(sec, "statements", [])
                for stmt in stmts:
                    s_id = stmt.get("statement_order", 0) if isinstance(stmt, dict) else getattr(stmt, "statement_order", 0)
                    if s_id in scene.source_statement_references:
                        resolved.append(
                            {
                                "id": s_id,
                                "statement_order": s_id,
                                "text": stmt.get("statement_text", "") if isinstance(stmt, dict) else getattr(stmt, "statement_text", ""),
                                "statement_text": stmt.get("statement_text", "") if isinstance(stmt, dict) else getattr(stmt, "statement_text", ""),
                                "statement_type": stmt.get("statement_type", "") if isinstance(stmt, dict) else getattr(stmt, "statement_type", ""),
                                "citations": stmt.get("citations", []) if isinstance(stmt, dict) else getattr(stmt, "citations", []),
                            }
                        )

        if not resolved:
            resolved.append(
                {
                    "id": scene.source_statement_references[0] if scene.source_statement_references else 1,
                    "statement_order": scene.source_statement_references[0] if scene.source_statement_references else 1,
                    "text": scene.narration_excerpt,
                    "statement_text": scene.narration_excerpt,
                    "statement_type": "",
                    "citations": getattr(scene, "citations", []),
                }
            )
        return resolved

    def _resolve_section_role(
        self,
        scene: StoryboardScene,
        script_version: Any | None,
        narrative_plan: NarrativePlan | dict[str, Any] | None,
    ) -> NarrativeSectionRole | None:
        """Determines narrative section role from NarrativePlan or heuristics."""
        if narrative_plan is not None:
            sections = []
            if isinstance(narrative_plan, dict):
                sections = narrative_plan.get("sections", [])
            elif hasattr(narrative_plan, "sections"):
                sections = narrative_plan.sections

            for sec in sections:
                sec_id = sec.get("id") if isinstance(sec, dict) else getattr(sec, "id", None)
                if str(sec_id) == str(scene.section_id):
                    role_val = sec.get("section_role") if isinstance(sec, dict) else getattr(sec, "section_role", None)
                    if role_val:
                        try:
                            return NarrativeSectionRole(str(role_val))
                        except ValueError:
                            pass

        if scene.sequence_index == 1:
            return NarrativeSectionRole.HOOK
        return NarrativeSectionRole.DEVELOPMENT

    def _derive_continuity_group(self, text: str, scene: StoryboardScene) -> str | None:
        lower = text.lower()
        if "raft" in lower or "consensus" in lower:
            return "motif_raft_architecture"
        if "document" in lower or "paper" in lower or "report" in lower:
            return "motif_primary_document"
        if "latency" in lower or "benchmark" in lower or "metrics" in lower:
            return "motif_performance_data"
        if " vs " in lower or "versus" in lower:
            return "motif_comparative_eval"
        if scene.visual_strategy == VisualStrategy.DIAGRAM:
            return "motif_core_diagram"
        return None

    def _extract_overlay_intent(self, text: str, role: VisualRole, scene: StoryboardScene) -> str | None:
        if scene.on_screen_text and role in (VisualRole.EMPHASIZE, VisualRole.DATA, VisualRole.REVEAL):
            return scene.on_screen_text
        metric_match = re.search(r"\b(\d+(?:\.\d+)?(?:%|ms|x|k|M|m|s))\b", text)
        if metric_match:
            return f"STAT: {metric_match.group(1)}"
        if role == VisualRole.COMPARE:
            return "COMPARISON: A vs B"
        return None


class VisualContinuityDirector:
    """Directs editorial continuity, tracks motifs/documents/comparisons, and detects visual defects."""

    def __init__(self):
        self.continuity_groups: dict[str, ContinuityGroup] = {}
        self.document_states: dict[str, DocumentContinuityState] = {}
        self.comparison_states: dict[str, ComparisonContinuityState] = {}

    def analyze_sequence(self, sequence: VisualBeatSequence) -> tuple[list[VisualBeat], list[ContinuityFinding]]:
        """Analyzes an ordered sequence of visual beats, assigning continuity decisions and checking coherence."""
        enriched_beats: list[VisualBeat] = []
        findings: list[ContinuityFinding] = list(sequence.continuity_findings)

        prev_beat: VisualBeat | None = None
        consecutive_same_role = 0
        consecutive_same_query = 0

        for idx, beat in enumerate(sequence.beats):
            # 1. Evaluate continuity decision relative to previous beat
            decision, reuse_policy = self._evaluate_beat_continuity(prev_beat, beat)

            # 2. Track document progression
            doc_findings = self._update_document_continuity(beat)
            findings.extend(doc_findings)

            # 3. Track comparison consistency
            comp_findings = self._update_comparison_continuity(beat)
            findings.extend(comp_findings)

            # 4. Check visual repetition
            if prev_beat is not None:
                if prev_beat.visual_role == beat.visual_role:
                    consecutive_same_role += 1
                else:
                    consecutive_same_role = 0

                if prev_beat.asset_query_hint and prev_beat.asset_query_hint == beat.asset_query_hint:
                    consecutive_same_query += 1
                else:
                    consecutive_same_query = 0

                if consecutive_same_role >= 3:
                    findings.append(
                        ContinuityFinding(
                            code=ContinuityFindingCode.MISSING_VISUAL_PROGRESS,
                            severity=ContinuityFindingSeverity.WARNING,
                            explanation=(
                                f"Visual role {beat.visual_role.value} repeated across "
                                f"{consecutive_same_role + 1} consecutive beats without visual progression."
                            ),
                            affected_beat_indices=[idx - 1, idx],
                            remediation="Vary visual role or merge adjacent beats.",
                        )
                    )

                if consecutive_same_query >= 2 and reuse_policy == AssetReusePolicy.ACCIDENTAL_REPEAT:
                    findings.append(
                        ContinuityFinding(
                            code=ContinuityFindingCode.ACCIDENTAL_ASSET_REPEAT,
                            severity=ContinuityFindingSeverity.ERROR,
                            explanation=(
                                f"Identical asset query '{beat.asset_query_hint}' repeated without "
                                f"continuity group or intentional anchor policy."
                            ),
                            affected_beat_indices=[idx - 1, idx],
                            remediation="Assign distinct asset query or declare an explicit continuity group.",
                        )
                    )

            # Construct updated beat with continuity decision & reuse policy
            updated_beat = VisualBeat(
                id=beat.id,
                scene_id=beat.scene_id,
                parent_scene_index=beat.parent_scene_index,
                beat_index=beat.beat_index,
                source_editorial_beat_indices=beat.source_editorial_beat_indices,
                start_offset_ms=beat.start_offset_ms,
                end_offset_ms=beat.end_offset_ms,
                duration_ms=beat.duration_ms,
                narrative_section_role=beat.narrative_section_role,
                script_statement_ids=beat.script_statement_ids,
                narration_text=beat.narration_text,
                visual_intent=beat.visual_intent,
                information_goal=beat.information_goal,
                visual_role=beat.visual_role,
                continuity_group_id=beat.continuity_group_id,
                preferred_asset_type=beat.preferred_asset_type,
                asset_reuse_policy=reuse_policy,
                asset_query_hint=beat.asset_query_hint,
                text_overlay_intent=beat.text_overlay_intent,
                grounding_references=beat.grounding_references,
                importance=beat.importance,
                continuity_decision=decision,
            )
            enriched_beats.append(updated_beat)
            prev_beat = updated_beat

        return enriched_beats, findings

    def _evaluate_beat_continuity(
        self, prev_beat: VisualBeat | None, curr_beat: VisualBeat
    ) -> tuple[ContinuityDecisionType, AssetReusePolicy]:
        """Determines continuity decision and asset reuse policy between consecutive beats."""
        if prev_beat is None:
            if curr_beat.continuity_group_id:
                return ContinuityDecisionType.KEEP, AssetReusePolicy.CONTINUITY_ANCHOR
            return ContinuityDecisionType.SWITCH_CONTEXT, AssetReusePolicy.NEW_ACQUISITION

        # 1. Document progression check
        if curr_beat.visual_role == VisualRole.DOCUMENT:
            if prev_beat.visual_role == VisualRole.DOCUMENT:
                return ContinuityDecisionType.PROGRESS_DOCUMENT, AssetReusePolicy.INTENTIONAL_REUSE
            return ContinuityDecisionType.SWITCH_CONTEXT, AssetReusePolicy.NEW_ACQUISITION

        # 2. Diagram progression check
        if curr_beat.visual_role == VisualRole.DIAGRAM:
            if prev_beat.visual_role == VisualRole.DIAGRAM:
                return ContinuityDecisionType.PROGRESS_DIAGRAM, AssetReusePolicy.INTENTIONAL_REUSE
            return ContinuityDecisionType.SWITCH_CONTEXT, AssetReusePolicy.NEW_ACQUISITION

        # 3. Continuity group / motif matching
        if curr_beat.continuity_group_id and curr_beat.continuity_group_id == prev_beat.continuity_group_id:
            if curr_beat.visual_role == prev_beat.visual_role:
                return ContinuityDecisionType.REFRAME_LATER, AssetReusePolicy.INTENTIONAL_REUSE
            return ContinuityDecisionType.REUSE, AssetReusePolicy.CONCEPTUAL_VARIATION

        if curr_beat.continuity_group_id and curr_beat.continuity_group_id in self.continuity_groups:
            return ContinuityDecisionType.RETURN_TO_MOTIF, AssetReusePolicy.CALLBACK_VISUAL

        # 4. Same subject/query without explicit motif
        if curr_beat.asset_query_hint and curr_beat.asset_query_hint == prev_beat.asset_query_hint:
            if curr_beat.visual_role == prev_beat.visual_role:
                return ContinuityDecisionType.KEEP, AssetReusePolicy.ACCIDENTAL_REPEAT
            return ContinuityDecisionType.REUSE, AssetReusePolicy.INTENTIONAL_REUSE

        # 5. Distinct topic / context switch
        return ContinuityDecisionType.SWITCH_CONTEXT, AssetReusePolicy.NEW_ACQUISITION

    def _update_document_continuity(self, beat: VisualBeat) -> list[ContinuityFinding]:
        findings: list[ContinuityFinding] = []
        if beat.visual_role != VisualRole.DOCUMENT:
            return findings

        doc_id = beat.continuity_group_id or "default_document"
        lower = beat.narration_text.lower()

        stage = DocumentProgressStage.OVERVIEW
        if "page" in lower or "section" in lower or "table" in lower:
            stage = DocumentProgressStage.SECTION
        elif "quote" in lower or "specifically" in lower or "clause" in lower or "line" in lower:
            stage = DocumentProgressStage.DETAIL
        elif "in context" in lower or "overall" in lower:
            stage = DocumentProgressStage.RETURN_TO_CONTEXT

        if doc_id not in self.document_states:
            if stage == DocumentProgressStage.DETAIL:
                findings.append(
                    ContinuityFinding(
                        code=ContinuityFindingCode.DOCUMENT_CONTEXT_LOST,
                        severity=ContinuityFindingSeverity.WARNING,
                        explanation=f"Document '{doc_id}' jumped straight to DETAIL without establishing OVERVIEW.",
                        affected_beat_indices=[beat.beat_index],
                        remediation="Establish document overview before inspecting specific detail.",
                    )
                )
            self.document_states[doc_id] = DocumentContinuityState(
                document_id=doc_id,
                document_title=beat.visual_intent,
                current_stage=stage,
            )
        else:
            self.document_states[doc_id] = DocumentContinuityState(
                document_id=doc_id,
                document_title=beat.visual_intent,
                current_stage=stage,
            )

        return findings

    def _update_comparison_continuity(self, beat: VisualBeat) -> list[ContinuityFinding]:
        findings: list[ContinuityFinding] = []
        if beat.visual_role != VisualRole.COMPARE:
            return findings

        comp_id = beat.continuity_group_id or "comparison_main"
        text = beat.narration_text
        match = re.search(r"(\w+)\s+(?:vs|versus|compared to)\s+(\w+)", text, re.IGNORECASE)
        if match:
            ent_a, ent_b = match.group(1).lower(), match.group(2).lower()
            if comp_id in self.comparison_states:
                state = self.comparison_states[comp_id]
                if ent_a == state.entity_b.lower() and ent_b == state.entity_a.lower():
                    findings.append(
                        ContinuityFinding(
                            code=ContinuityFindingCode.COMPARISON_SIDE_SWAP,
                            severity=ContinuityFindingSeverity.ERROR,
                            explanation=(
                                f"Comparison side swap detected: '{ent_a}' was on {state.side_b.value}, "
                                f"now mapped to {state.side_a.value}."
                            ),
                            affected_beat_indices=[beat.beat_index],
                            remediation="Preserve stable spatial alignment (Entity A left, Entity B right).",
                        )
                    )
            else:
                self.comparison_states[comp_id] = ComparisonContinuityState(
                    comparison_id=comp_id,
                    entity_a=ent_a,
                    entity_b=ent_b,
                    side_a=ComparisonSide.LEFT,
                    side_b=ComparisonSide.RIGHT,
                )

        return findings

    def register_motif(self, group: ContinuityGroup) -> None:
        """Explicitly registers an inspectable continuity group/motif."""
        self.continuity_groups[group.id] = group


class VisualDirectorBeatAdapter:
    """Seamlessly connects VisualBeat + ContinuityDecision to existing VisualDirector."""

    def __init__(self, visual_director: VisualDirector | None = None):
        self.visual_director = visual_director or VisualDirector()

    def resolve_beat_direction(
        self,
        scene: StoryboardScene,
        beat: VisualBeat,
        continuity_decision: ContinuityDecisionType | None = None,
    ) -> VisualDirection:
        """Produces an enriched VisualDirection for a VisualBeat without replacing VisualDirector."""
        decision = continuity_decision or beat.continuity_decision or ContinuityDecisionType.SWITCH_CONTEXT
        render_mode, template_id, rationale = InformationVisualMapper.ROLE_MAPPING.get(
            beat.visual_role,
            (VisualRenderMode.HYBRID, VisualTemplateId.IMAGE_EXPLAINER, "Fallback beat direction"),
        )

        asset_reqs: list[VisualAssetRequirement] = []
        if beat.preferred_asset_type in ("IMAGE", "BROLL", "SCREENSHOT"):
            kind = (
                VisualAssetKind.BROLL
                if beat.preferred_asset_type == "BROLL"
                else (
                    VisualAssetKind.SCREENSHOT
                    if beat.preferred_asset_type == "SCREENSHOT"
                    else VisualAssetKind.IMAGE
                )
            )
            asset_reqs.append(
                VisualAssetRequirement(
                    kind=kind,
                    query_hint=beat.asset_query_hint or scene.asset_query_hint,
                    source_text=scene.asset_source_text,
                    purpose=f"Visual asset for beat {beat.beat_index} ({beat.visual_role.value})",
                    required=True,
                )
            )

        metadata: dict[str, Any] = {
            "beat_id": str(beat.id),
            "beat_index": beat.beat_index,
            "source_editorial_beat_indices": beat.source_editorial_beat_indices,
            "scene_index": scene.sequence_index,
            "start_offset_ms": beat.start_offset_ms,
            "end_offset_ms": beat.end_offset_ms,
            "duration_ms": beat.duration_ms,
            "visual_role": beat.visual_role.value,
            "continuity_decision": decision.value,
            "asset_reuse_policy": beat.asset_reuse_policy.value,
            "continuity_group_id": beat.continuity_group_id,
            "text_overlay_intent": beat.text_overlay_intent,
            "information_goal": beat.information_goal,
        }

        return VisualDirection(
            scene_index=scene.sequence_index,
            render_mode=render_mode,
            template_id=template_id,
            asset_requirements=asset_reqs,
            motion_profile="beat_pacing",
            rationale=f"{rationale} (Continuity: {decision.value})",
            metadata=metadata,
        )

    def enrich_beat_visual_direction(
        self,
        scene: StoryboardScene,
        beat: VisualBeat,
        editorial_beat: EditorialBeatSpec,
        continuity_decision: ContinuityDecisionType | None = None,
    ) -> Any:
        """Enrich the canonical beat direction without remapping its visual authority."""
        from omega.application.beat_visual_direction import BeatVisualDirector

        if beat.source_editorial_beat_indices != (editorial_beat.beat_index,):
            raise ValueError("VisualBeat does not map one-to-one to the supplied EditorialBeatSpec")

        decision = continuity_decision or beat.continuity_decision or ContinuityDecisionType.SWITCH_CONTEXT
        canonical = BeatVisualDirector.resolve_beat(scene=scene, beat=editorial_beat)
        return canonical.model_copy(
            update={
                "rationale": f"{canonical.rationale} (Continuity: {decision.value})",
                "metadata": {
                    **canonical.metadata,
                    "beat_id": str(beat.id),
                    "beat_index": beat.beat_index,
                    "source_editorial_beat_indices": beat.source_editorial_beat_indices,
                    "visual_role": beat.visual_role.value,
                    "continuity_decision": decision.value,
                    "asset_reuse_policy": beat.asset_reuse_policy.value,
                    "continuity_group_id": beat.continuity_group_id,
                    "text_overlay_intent": beat.text_overlay_intent,
                    "information_goal": beat.information_goal,
                },
            }
        )



def verify_beat_lineage(
    beat: VisualBeat,
    scene: StoryboardScene,
    script_version: Any | None = None,
    narrative_plan: Any | None = None,
    editorial_beats: Any | None = None,
) -> bool:
    """Verifies complete lineage: VisualBeat -> EditorialBeat -> StoryboardScene -> ScriptVersion -> NarrativePlan."""
    # 1. Beat belongs to scene
    if beat.parent_scene_index != scene.sequence_index:
        return False
    if not beat.source_editorial_beat_indices:
        return False
    if tuple(sorted(set(beat.source_editorial_beat_indices))) != beat.source_editorial_beat_indices:
        return False

    # 2. EditorialBeat verification if provided
    if editorial_beats is not None:
        supplied = (
            tuple(editorial_beats)
            if isinstance(editorial_beats, (list, tuple))
            else (editorial_beats,)
        )
        supplied_indices = tuple(getattr(item, "beat_index", None) for item in supplied)
        if beat.source_editorial_beat_indices != supplied_indices:
            return False
        supplied_narration = " ".join(
            str(getattr(item, "narration_span", "")) for item in supplied
        ).strip()
        if supplied_narration != beat.narration_text:
            return False

    # 3. Scene source statements match beat script statement IDs
    if beat.script_statement_ids and not all(
        sid in scene.source_statement_references for sid in beat.script_statement_ids
    ):
        return False

    # 4. ScriptVersion matches StoryboardPlan if available
    if script_version is not None and narrative_plan is not None:
        s_np_id = getattr(script_version, "narrative_plan_id", None)
        np_id = getattr(narrative_plan, "id", None)
        if s_np_id and np_id and str(s_np_id) != str(np_id):
            return False

    return True
