"""Application-layer Visual Continuity & Editorial Beat Architecture (P22-A).

Coordinates editorial beat planning, visual role taxonomy mapping,
timing boundary enforcement, and visual continuity analysis across scenes.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

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
        has_stats = bool(re.search(r"\b\d+(?:\.\d+)?(?:%|x|k|M|m|s|ms|percent)?(?:\s|$|[.,])", text) and any(c in text for c in "%0123456789"))
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
        if pacing_norm == "FAST":
            self.min_duration_ms = 1200
            self.target_duration_ms = 2500
            self.max_duration_ms = 5000
            self.hold_limit_ms = 6000
        elif pacing_norm == "RELAXED":
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

        for idx, beat in enumerate(beats):
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


class EditorialBeatPlanner:
    """Application-layer planner creating authoritative VisualBeats for Storyboard scenes."""

    def __init__(self, pacing: str = "BALANCED"):
        self.pacing = pacing
        self.duration_policy = BeatDurationPolicy(pacing)

    def plan_scene_beats(
        self,
        scene: StoryboardScene,
        script_version: Any | None = None,
        narrative_plan: NarrativePlan | dict[str, Any] | None = None,
    ) -> VisualBeatSequence:
        """Plans ordered visual beats for a StoryboardScene using narrative context and statement flow."""
        scene_duration_ms = max(int(scene.estimated_duration_seconds * 1000), 1500)
        statements = self._resolve_statements(scene, script_version)
        section_role = self._resolve_section_role(scene, script_version, narrative_plan)

        # Segment statements into informational clusters
        clusters = self._cluster_statements(statements, scene.narration_excerpt)

        # Distribute scene duration across clusters proportionally to word count
        total_words = sum(max(c["word_count"], 1) for c in clusters)
        allocated_beats: list[VisualBeat] = []
        current_offset = 0

        for idx, cluster in enumerate(clusters):
            word_ratio = cluster["word_count"] / max(total_words, 1)
            raw_duration = int(scene_duration_ms * word_ratio)

            # Ensure last beat covers remainder exactly
            if idx == len(clusters) - 1:
                beat_duration = max(scene_duration_ms - current_offset, 500)
            else:
                beat_duration = max(raw_duration, 1000)

            end_offset = current_offset + beat_duration

            # Determine visual role & preferred asset type
            role, asset_type = InformationVisualMapper.map_goal_to_role(
                cluster["text"],
                section_role=section_role,
                strategy=scene.visual_strategy,
            )

            # Derive continuity group / motif identifier
            continuity_group = self._derive_continuity_group(cluster["text"], scene)

            # Determine text overlay intent
            overlay = self._extract_overlay_intent(cluster["text"], role, scene)

            beat = VisualBeat(
                scene_id=str(scene.section_id or f"scene_{scene.sequence_index}"),
                parent_scene_index=scene.sequence_index,
                beat_index=idx,
                start_offset_ms=current_offset,
                end_offset_ms=end_offset,
                duration_ms=beat_duration,
                narrative_section_role=section_role,
                script_statement_ids=cluster["statement_ids"],
                narration_text=cluster["text"],
                visual_intent=f"Show {role.value.lower()} visual for: {cluster['text'][:70]}...",
                information_goal=f"Convey: {cluster['text'][:90]}",
                visual_role=role,
                continuity_group_id=continuity_group,
                preferred_asset_type=asset_type,
                asset_reuse_policy=(
                    AssetReusePolicy.CONTINUITY_ANCHOR
                    if idx == 0 and continuity_group
                    else AssetReusePolicy.NEW_ACQUISITION
                ),
                asset_query_hint=scene.asset_query_hint or scene.visual_brief,
                text_overlay_intent=overlay,
                grounding_references=scene.citations if hasattr(scene, "citations") else [],
                importance=scene.importance,
            )
            allocated_beats.append(beat)
            current_offset = end_offset

        timing_findings = self.duration_policy.evaluate_beats(allocated_beats)

        return VisualBeatSequence(
            parent_scene_index=scene.sequence_index,
            scene_id=str(scene.section_id or f"scene_{scene.sequence_index}"),
            beats=allocated_beats,
            total_duration_ms=scene_duration_ms,
            continuity_findings=timing_findings,
        )

    def _resolve_statements(self, scene: StoryboardScene, script_version: Any | None) -> list[dict[str, Any]]:
        """Resolves source statements for the scene from ScriptVersion or fallback narration."""
        resolved: list[dict[str, Any]] = []
        if script_version is not None:
            # Check if script_version is dict with sections
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
                                "text": stmt.get("statement_text", "") if isinstance(stmt, dict) else getattr(stmt, "statement_text", ""),
                                "citations": stmt.get("citations", []) if isinstance(stmt, dict) else getattr(stmt, "citations", []),
                            }
                        )

        if not resolved:
            # Fallback: treat narration excerpt as single source statement
            resolved.append(
                {
                    "id": scene.source_statement_references[0] if scene.source_statement_references else 1,
                    "text": scene.narration_excerpt,
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

        # Heuristic fallback based on scene index or purpose
        if scene.sequence_index == 1:
            return NarrativeSectionRole.HOOK
        return NarrativeSectionRole.DEVELOPMENT

    def _cluster_statements(self, statements: list[dict[str, Any]], narration_fallback: str) -> list[dict[str, Any]]:
        """Splits or clusters statements into editorial beat units. Avoids mechanical sentence cuts."""
        clusters: list[dict[str, Any]] = []

        if len(statements) <= 1:
            text = statements[0]["text"] if statements else narration_fallback
            sentences = self._split_clean_sentences(text)
            if len(sentences) <= 1 or len(text.split()) < 18:
                return [{"statement_ids": [statements[0]["id"]] if statements else [1], "text": text, "word_count": max(len(text.split()), 1)}]
            
            # Meaningful mid-sentence split on contrast, causal, or evidence
            split_points = self._identify_editorial_splits(sentences)
            for split_group in split_points:
                combined_text = " ".join(split_group).strip()
                clusters.append(
                    {
                        "statement_ids": [statements[0]["id"]] if statements else [1],
                        "text": combined_text,
                        "word_count": max(len(combined_text.split()), 1),
                    }
                )
            return clusters

        # Multi-statement scene: group tight statements or split on significant factual boundaries
        curr_text = []
        curr_ids = []
        curr_words = 0

        for stmt in statements:
            s_text = stmt["text"].strip()
            s_words = len(s_text.split())

            # Detect if this statement introduces a distinct factual claim, comparison, or evidence
            has_contrast = any(w in s_text.lower() for w in ("however", "in contrast", "vs", "versus", "instead"))
            has_data = bool(re.search(r"\b(\d+(?:\.\d+)?(?:%|x|k|M|m|s|ms))\b", s_text))

            if curr_words >= 14 or (curr_words >= 8 and (has_contrast or has_data)):
                clusters.append(
                    {
                        "statement_ids": list(curr_ids),
                        "text": " ".join(curr_text),
                        "word_count": curr_words,
                    }
                )
                curr_text = [s_text]
                curr_ids = [stmt["id"]]
                curr_words = s_words
            else:
                curr_text.append(s_text)
                curr_ids.append(stmt["id"])
                curr_words += s_words

        if curr_text:
            clusters.append(
                {
                    "statement_ids": list(curr_ids),
                    "text": " ".join(curr_text),
                    "word_count": max(curr_words, 1),
                }
            )

        return clusters

    def _split_clean_sentences(self, text: str) -> list[str]:
        pattern = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'“\(\[])')
        parts = pattern.split(text.strip())
        return [p.strip() for p in parts if p.strip()]

    def _identify_editorial_splits(self, sentences: list[str]) -> list[list[str]]:
        groups: list[list[str]] = []
        current: list[str] = []
        current_words = 0

        for s in sentences:
            words = len(s.split())
            if current and (current_words >= 12 or words >= 12):
                groups.append(current)
                current = [s]
                current_words = words
            else:
                current.append(s)
                current_words += words

        if current:
            groups.append(current)
        return groups if groups else [[s] for s in sentences]

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
            # Same motif continued
            if curr_beat.visual_role == prev_beat.visual_role:
                return ContinuityDecisionType.REFRAME_LATER, AssetReusePolicy.INTENTIONAL_REUSE
            return ContinuityDecisionType.REUSE, AssetReusePolicy.CONCEPTUAL_VARIATION

        if curr_beat.continuity_group_id and curr_beat.continuity_group_id in self.continuity_groups:
            # Returning to a previously established motif
            return ContinuityDecisionType.RETURN_TO_MOTIF, AssetReusePolicy.CALLBACK_VISUAL

        # 4. Same subject/query without explicit motif
        if curr_beat.asset_query_hint and curr_beat.asset_query_hint == prev_beat.asset_query_hint:
            if curr_beat.visual_role == prev_beat.visual_role:
                # Same asset query repeated without motif declaration -> Accidental repeat risk
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

        # Determine stage
        stage = DocumentProgressStage.OVERVIEW
        if "page" in lower or "section" in lower or "table" in lower:
            stage = DocumentProgressStage.SECTION
        elif "quote" in lower or "specifically" in lower or "clause" in lower or "line" in lower:
            stage = DocumentProgressStage.DETAIL
        elif "in context" in lower or "overall" in lower:
            stage = DocumentProgressStage.RETURN_TO_CONTEXT

        if doc_id not in self.document_states:
            # First encounter with document
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
            prev_stage = self.document_states[doc_id].current_stage
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
        # Parse comparison sides if indicated in narration or overlay
        text = beat.narration_text
        match = re.search(r"(\w+)\s+(?:vs|versus|compared to)\s+(\w+)", text, re.IGNORECASE)
        if match:
            ent_a, ent_b = match.group(1).lower(), match.group(2).lower()
            if comp_id in self.comparison_states:
                state = self.comparison_states[comp_id]
                # Check if sides were swapped
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

        # Asset requirements based on role & asset_reuse_policy
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
                    purpose=f"Visual asset for beat {beat.beat_index} ({beat.visual_role.value})",
                    required=True,
                )
            )

        metadata: dict[str, Any] = {
            "beat_id": str(beat.id),
            "beat_index": beat.beat_index,
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


def verify_beat_lineage(
    beat: VisualBeat,
    scene: StoryboardScene,
    script_version: Any | None = None,
    narrative_plan: Any | None = None,
) -> bool:
    """Verifies complete lineage: VisualBeat -> StoryboardScene -> ScriptVersion -> NarrativePlan."""
    # 1. Beat belongs to scene
    if beat.parent_scene_index != scene.sequence_index:
        return False

    # 2. Scene source statements match beat script statement IDs
    if beat.script_statement_ids:
        if not any(sid in scene.source_statement_references for sid in beat.script_statement_ids):
            return False

    # 3. ScriptVersion matches StoryboardPlan if available
    if script_version is not None and narrative_plan is not None:
        s_np_id = getattr(script_version, "narrative_plan_id", None)
        np_id = getattr(narrative_plan, "id", None)
        if s_np_id and np_id and str(s_np_id) != str(np_id):
            return False

    return True
