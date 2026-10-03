"""Narrative QA & Acceptance Engine.

Implements the final editorial quality gate for NarrativePlan before script generation.
Aggregates structural validation, grounding quality, promise/payoff fidelity,
hook quality, narrative coherence, retention pacing, and channel fit.
Provides deterministic finding deduplication, severity mapping, actionable
remediation recommendations, and script generation gate enforcement.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.application.retention_pacing_engine import RetentionPacingService
from omega.domain.channel_dna import ChannelDNA
from omega.domain.narrative_pacing import PacingFindingCode, PacingFindingSeverity, PacingPlan
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanFinding,
    NarrativeRuleCode,
    NarrativeSection,
    NarrativeSectionRole,
    NarrativeSeverity,
)
from omega.domain.narrative_qa import (
    NarrativeQAFinding,
    NarrativeQAFindingCode,
    NarrativeQAGateError,
    NarrativeQARecommendation,
    NarrativeQARecommendationAction,
    NarrativeQAResult,
    NarrativeQASeverity,
    NarrativeQAStatus,
    NarrativeQASubsystem,
)

logger = logging.getLogger(__name__)


# ── Model Reviewer Protocol & Fake ──────────────────────────────────────────


class NarrativeEditorialModelReviewer(Protocol):
    """Protocol for optional model-assisted subjective editorial review."""

    def review(
        self,
        plan: NarrativePlan,
        research_brief: dict[str, Any] | None = None,
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
    ) -> list[NarrativeQAFinding]:
        """Perform subjective review and emit structured QA findings."""
        ...


class FakeEditorialModelReviewer:
    """Deterministic fake model reviewer for testing."""

    def __init__(self, findings: list[NarrativeQAFinding] | None = None) -> None:
        self._findings = findings or []

    def review(
        self,
        plan: NarrativePlan,
        research_brief: dict[str, Any] | None = None,
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
    ) -> list[NarrativeQAFinding]:
        return list(self._findings)


# ── Subsystem Evaluators ────────────────────────────────────────────────────


class StructuralQAEvaluator:
    """Evaluates structural integrity by adapting P21-A NarrativePlanValidator."""

    def __init__(self, validator: NarrativePlanValidator | None = None) -> None:
        self.validator = validator or NarrativePlanValidator(strict_grounding=False)

    def evaluate(self, plan: NarrativePlan) -> list[NarrativeQAFinding]:
        val_result = self.validator.validate(plan)
        findings: list[NarrativeQAFinding] = []

        code_map: dict[NarrativeRuleCode, tuple[NarrativeQAFindingCode, NarrativeQASeverity]] = {
            NarrativeRuleCode.EMPTY_PLAN: (
                NarrativeQAFindingCode.EMPTY_PLAN,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.MISSING_REQUIRED_ROLE: (
                NarrativeQAFindingCode.MISSING_REQUIRED_ROLE,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.MISSING_REQUIRED_HOOK: (
                NarrativeQAFindingCode.MISSING_REQUIRED_ROLE,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.INVALID_ROLE_COMBINATION: (
                NarrativeQAFindingCode.INVALID_ROLE_ORDER,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.ORPHAN_PAYOFF: (
                NarrativeQAFindingCode.ORPHAN_PAYOFF,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.UNRESOLVED_PROMISE: (
                NarrativeQAFindingCode.UNRESOLVED_PROMISE,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.PAYOFF_BEFORE_PROMISE: (
                NarrativeQAFindingCode.ORPHAN_PAYOFF,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.DURATION_OUT_OF_BOUNDS: (
                NarrativeQAFindingCode.INVALID_DURATION,
                NarrativeQASeverity.ERROR,
            ),
            NarrativeRuleCode.DURATION_SUM_MISMATCH: (
                NarrativeQAFindingCode.INVALID_DURATION,
                NarrativeQASeverity.ERROR,
            ),
            NarrativeRuleCode.INVALID_DURATION_ALLOCATION: (
                NarrativeQAFindingCode.INVALID_DURATION,
                NarrativeQASeverity.ERROR,
            ),
            NarrativeRuleCode.MISSING_GROUNDING: (
                NarrativeQAFindingCode.INVALID_GROUNDING,
                NarrativeQASeverity.ERROR,
            ),
            NarrativeRuleCode.INVALID_CTA_PLACEMENT: (
                NarrativeQAFindingCode.INVALID_CTA_PLACEMENT,
                NarrativeQASeverity.ERROR,
            ),
            NarrativeRuleCode.EXCESSIVE_CTA_COUNT: (
                NarrativeQAFindingCode.INVALID_CTA_PLACEMENT,
                NarrativeQASeverity.ERROR,
            ),
            NarrativeRuleCode.HOOK_NOT_FIRST: (
                NarrativeQAFindingCode.INVALID_ROLE_ORDER,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.HOOK_DURATION_EXCEEDED: (
                NarrativeQAFindingCode.OVERLONG_HOOK,
                NarrativeQASeverity.WARNING,
            ),
            NarrativeRuleCode.DUPLICATE_SECTION_ORDER: (
                NarrativeQAFindingCode.INVALID_ROLE_ORDER,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.ORDERING_GAP: (
                NarrativeQAFindingCode.INVALID_ROLE_ORDER,
                NarrativeQASeverity.BLOCKER,
            ),
            NarrativeRuleCode.SECTION_COUNT_OUT_OF_BOUNDS: (
                NarrativeQAFindingCode.INVALID_ROLE_ORDER,
                NarrativeQASeverity.ERROR,
            ),
        }

        for vf in val_result.findings:
            mapping = code_map.get(
                vf.rule_code,
                (NarrativeQAFindingCode.INVALID_ROLE_ORDER, NarrativeQASeverity.ERROR),
            )
            qa_code, default_sev = mapping
            sev = (
                NarrativeQASeverity.BLOCKER
                if vf.severity == NarrativeSeverity.BLOCKING
                else default_sev
            )

            orders = [vf.section_order] if vf.section_order is not None else []
            remediation = self._get_structural_remediation(qa_code, vf.message)

            findings.append(
                NarrativeQAFinding(
                    code=qa_code,
                    severity=sev,
                    subsystem=NarrativeQASubsystem.STRUCTURE,
                    explanation=vf.message,
                    affected_section_orders=orders,
                    contributing_sources=["NarrativePlanValidator"],
                    recommended_remediation=remediation,
                )
            )

        return findings

    @staticmethod
    def _get_structural_remediation(code: NarrativeQAFindingCode, msg: str) -> str:
        if code == NarrativeQAFindingCode.ORPHAN_PAYOFF:
            return "Ensure every payoff section references an established open-loop promise_id."
        if code == NarrativeQAFindingCode.UNRESOLVED_PROMISE:
            return "Ensure every opened promise_id is paired with a resolving payoff section."
        if code == NarrativeQAFindingCode.MISSING_REQUIRED_ROLE:
            return "Add required semantic roles matching the format profile specifications."
        if code == NarrativeQAFindingCode.INVALID_DURATION:
            return "Rebalance section durations so total duration matches the target duration."
        if code == NarrativeQAFindingCode.INVALID_CTA_PLACEMENT:
            return "Position Call-to-Action (CTA) at or near the conclusion after payoff delivery."
        return "Correct structural ordering and roles according to format profile rules."


class GroundingQAEvaluator:
    """Evaluates research grounding quality, citation depth, and evidence consistency."""

    def evaluate(
        self,
        plan: NarrativePlan,
        research_brief: dict[str, Any] | None,
    ) -> list[NarrativeQAFinding]:
        findings: list[NarrativeQAFinding] = []
        if not research_brief:
            return findings

        verified_claims = research_brief.get("verified_claims", []) or []
        verified_claim_ids = {
            str(c.get("claim_id")) for c in verified_claims if c.get("claim_id")
        }
        uncertain_claims = research_brief.get("uncertain_claims", []) or []
        uncertain_claim_ids = {
            str(c.get("claim_id")) for c in uncertain_claims if c.get("claim_id")
        }
        contradictions = research_brief.get("contradictions", []) or []
        contradicted_claim_ids = {
            str(c.get("claim_id")) for c in contradictions if c.get("claim_id")
        }

        # Track citation usage across sections
        citation_counts: dict[str, int] = {}
        for section in plan.sections:
            cids = [str(c) for c in section.get_grounding_claim_ids()]
            for cid in cids:
                citation_counts[cid] = citation_counts.get(cid, 0) + 1

                # Check uncertain claim misuse
                if cid in uncertain_claim_ids:
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.UNCERTAIN_CLAIM_PROMOTED,
                            severity=NarrativeQASeverity.BLOCKER,
                            subsystem=NarrativeQASubsystem.GROUNDING,
                            explanation=(
                                f"Section {section.section_order} cites uncertain claim '{cid}' "
                                f"as verified fact."
                            ),
                            affected_section_orders=[section.section_order],
                            affected_section_ids=[section.id],
                            contributing_sources=["GroundingQAEvaluator"],
                            recommended_remediation="Remove uncertain claim or replace with a verified claim.",
                        )
                    )

                # Check contradiction citation
                if cid in contradicted_claim_ids:
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.INCONSISTENT_RESEARCH_EVIDENCE,
                            severity=NarrativeQASeverity.BLOCKER,
                            subsystem=NarrativeQASubsystem.GROUNDING,
                            explanation=(
                                f"Section {section.section_order} cites contradicted claim '{cid}'."
                            ),
                            affected_section_orders=[section.section_order],
                            affected_section_ids=[section.id],
                            contributing_sources=["GroundingQAEvaluator"],
                            recommended_remediation="Replace contradictory claim with verified research evidence.",
                        )
                    )

            # Check important factual sections for weak grounding coverage
            if section.role in (
                NarrativeSectionRole.DEVELOPMENT,
                NarrativeSectionRole.ESCALATION,
            ):
                if len(cids) == 0 and len(verified_claim_ids) > 0:
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.WEAK_GROUNDING_COVERAGE,
                            severity=NarrativeQASeverity.ERROR,
                            subsystem=NarrativeQASubsystem.GROUNDING,
                            explanation=(
                                f"Core factual section {section.section_order} ({section.role.value}) "
                                f"has 0 claim citations."
                            ),
                            affected_section_orders=[section.section_order],
                            affected_section_ids=[section.id],
                            contributing_sources=["GroundingQAEvaluator"],
                            recommended_remediation="Attach at least one verified claim citation to support this section.",
                        )
                    )

            # Check payoff section evidence support
            if section.role == NarrativeSectionRole.PAYOFF:
                if len(cids) == 0 and len(verified_claim_ids) > 0:
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.PAYOFF_LACKING_EVIDENCE,
                            severity=NarrativeQASeverity.ERROR,
                            subsystem=NarrativeQASubsystem.GROUNDING,
                            explanation=(
                                f"Payoff section {section.section_order} lacks verified research "
                                f"citations to substantiate resolution."
                            ),
                            affected_section_orders=[section.section_order],
                            affected_section_ids=[section.id],
                            contributing_sources=["GroundingQAEvaluator"],
                            recommended_remediation="Substantiate payoff resolution with verified factual citations.",
                        )
                    )

            # Check unsupported key information
            if len(section.key_information) >= 3 and len(cids) == 0:
                findings.append(
                    NarrativeQAFinding(
                        code=NarrativeQAFindingCode.UNSUPPORTED_KEY_INFO,
                        severity=NarrativeQASeverity.WARNING,
                        subsystem=NarrativeQASubsystem.GROUNDING,
                        explanation=(
                            f"Section {section.section_order} contains {len(section.key_information)} "
                            f"key information items with no grounding citations."
                        ),
                        affected_section_orders=[section.section_order],
                        affected_section_ids=[section.id],
                        contributing_sources=["GroundingQAEvaluator"],
                        recommended_remediation="Add research claim citations to ground key informational points.",
                    )
                )

        # Check excessive citation reuse (> 3 sections when > 1 verified claim available)
        if len(verified_claim_ids) > 1:
            for cid, count in citation_counts.items():
                if count >= 4:
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.EXCESSIVE_CITATION_REUSE,
                            severity=NarrativeQASeverity.WARNING,
                            subsystem=NarrativeQASubsystem.GROUNDING,
                            explanation=(
                                f"Claim '{cid}' is cited in {count} separate sections, risking "
                                f"narrative monotony."
                            ),
                            affected_section_orders=[],
                            contributing_sources=["GroundingQAEvaluator"],
                            recommended_remediation="Diversify citations across available verified claims.",
                        )
                    )

        return findings


class PromisePayoffQAEvaluator:
    """Evaluates narrative promise clarity, payoff completeness, and conceptual alignment."""

    def evaluate(self, plan: NarrativePlan) -> list[NarrativeQAFinding]:
        findings: list[NarrativeQAFinding] = []
        promises: dict[str, NarrativeSection] = {}
        payoffs: dict[str, NarrativeSection] = {}

        for s in plan.sections:
            if s.promise_id:
                if s.promise_id in promises:
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.MULTIPLE_REDUNDANT_PROMISES,
                            severity=NarrativeQASeverity.ERROR,
                            subsystem=NarrativeQASubsystem.EDITORIAL,
                            explanation=f"Duplicate promise_id '{s.promise_id}' declared across multiple sections.",
                            affected_section_orders=[
                                promises[s.promise_id].section_order,
                                s.section_order,
                            ],
                            contributing_sources=["PromisePayoffQAEvaluator"],
                            recommended_remediation="Use distinct promise IDs or consolidate promises.",
                        )
                    )
                promises[s.promise_id] = s

                # Evaluate promise quality
                if len(s.objective.strip()) < 15:
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.WEAK_PROMISE,
                            severity=NarrativeQASeverity.WARNING,
                            subsystem=NarrativeQASubsystem.EDITORIAL,
                            explanation=(
                                f"Promise in section {s.section_order} has very short objective "
                                f"('{s.objective}')."
                            ),
                            affected_section_orders=[s.section_order],
                            affected_section_ids=[s.id],
                            contributing_sources=["PromisePayoffQAEvaluator"],
                            recommended_remediation="Deepen promise stakes and clarify question being posed.",
                        )
                    )

            if s.payoff_reference:
                payoffs[s.payoff_reference] = s

        # Evaluate promise-payoff alignment
        for pid, p_sec in promises.items():
            pay_sec = payoffs.get(pid)
            if not pay_sec:
                continue

            # Payoff duration check
            min_payoff_dur = 10 if plan.format_profile != NarrativeFormatProfile.SHORT else 5
            if pay_sec.target_duration_seconds < min_payoff_dur:
                findings.append(
                    NarrativeQAFinding(
                        code=NarrativeQAFindingCode.PAYOFF_INCOMPLETE,
                        severity=NarrativeQASeverity.WARNING,
                        subsystem=NarrativeQASubsystem.EDITORIAL,
                        explanation=(
                            f"Payoff section {pay_sec.section_order} duration "
                            f"({pay_sec.target_duration_seconds}s) is too short to adequately "
                            f"resolve promise '{pid}'."
                        ),
                        affected_section_orders=[pay_sec.section_order],
                        affected_section_ids=[pay_sec.id],
                        contributing_sources=["PromisePayoffQAEvaluator"],
                        recommended_remediation="Allocate additional duration to ensure thorough payoff resolution.",
                    )
                )

            # Conceptual alignment check
            p_words = set(re.findall(r"\w+", p_sec.objective.lower()))
            pay_words = set(re.findall(r"\w+", pay_sec.objective.lower()))
            common_words = {
                w
                for w in (p_words & pay_words)
                if len(w) > 3
                and w not in {"what", "that", "this", "from", "with", "about", "your", "their"}
            }

            if not common_words and len(p_words) > 5 and len(pay_words) > 5:
                # If neither objective nor key points share substantive concepts, flag mismatch
                pay_key_words = set(
                    re.findall(r"\w+", " ".join(pay_sec.key_information).lower())
                )
                if not (p_words & pay_key_words):
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.PROMISE_PAYOFF_MISMATCH,
                            severity=NarrativeQASeverity.ERROR,
                            subsystem=NarrativeQASubsystem.EDITORIAL,
                            explanation=(
                                f"Promise in section {p_sec.section_order} has low thematic alignment "
                                f"with payoff in section {pay_sec.section_order}."
                            ),
                            affected_section_orders=[p_sec.section_order, pay_sec.section_order],
                            contributing_sources=["PromisePayoffQAEvaluator"],
                            recommended_remediation="Align payoff explanation directly with the question posed in the promise.",
                        )
                    )

        return findings


class HookQAEvaluator:
    """Evaluates hook relevance, curiosity, length, and format alignment."""

    def evaluate(self, plan: NarrativePlan) -> list[NarrativeQAFinding]:
        findings: list[NarrativeQAFinding] = []
        hook_sections = plan.get_sections_by_role(NarrativeSectionRole.HOOK)
        if not hook_sections:
            return findings

        hook = hook_sections[0]

        # Duration bounds by format
        max_hook_dur = {
            NarrativeFormatProfile.SHORT: 10,
            NarrativeFormatProfile.MEDIUM: 30,
            NarrativeFormatProfile.LONG: 45,
        }.get(plan.format_profile, 30)

        if hook.target_duration_seconds > max_hook_dur:
            findings.append(
                NarrativeQAFinding(
                    code=NarrativeQAFindingCode.OVERLONG_HOOK,
                    severity=NarrativeQASeverity.WARNING,
                    subsystem=NarrativeQASubsystem.EDITORIAL,
                    explanation=(
                        f"Hook duration ({hook.target_duration_seconds}s) exceeds recommended "
                        f"threshold for {plan.format_profile.value} ({max_hook_dur}s)."
                    ),
                    affected_section_orders=[hook.section_order],
                    affected_section_ids=[hook.id],
                    contributing_sources=["HookQAEvaluator"],
                    recommended_remediation=f"Tighten hook to under {max_hook_dur} seconds.",
                )
            )

        # Hook substantive quality
        hook_text = (hook.objective + " " + " ".join(hook.key_information)).strip()
        weak_starters = ("welcome", "hello", "hi guys", "in this video", "today we", "intro")
        obj_clean = hook.objective.lower().strip()
        is_weak_text = (
            len(hook_text) < 20
            or any(obj_clean.startswith(ws) for ws in weak_starters)
            or obj_clean in {"intro", "introduction", "hook", "welcome", "hello"}
        )
        if is_weak_text:
            findings.append(
                NarrativeQAFinding(
                    code=NarrativeQAFindingCode.WEAK_HOOK,
                    severity=NarrativeQASeverity.WARNING,
                    subsystem=NarrativeQASubsystem.EDITORIAL,
                    explanation="Hook lacks clear curiosity, stakes, or substantive objective.",
                    affected_section_orders=[hook.section_order],
                    affected_section_ids=[hook.id],
                    contributing_sources=["HookQAEvaluator"],
                    recommended_remediation="Reframe hook around a compelling question, surprising fact, or immediate stakes.",
                )
            )

        # Downstream topic alignment
        downstream_text = " ".join(
            s.objective + " " + " ".join(s.key_information)
            for s in plan.sections
            if s.section_order != hook.section_order
        ).lower()

        hook_words = {
            w
            for w in re.findall(r"\w+", hook.objective.lower())
            if len(w) > 4
            and w not in {"video", "today", "explore", "discover", "learn", "watch", "about"}
        }

        # If hook mentions multiple specific nouns completely absent downstream, flag misleading hook
        unsupported_hook_words = [w for w in hook_words if w not in downstream_text]
        if len(hook_words) >= 4 and len(unsupported_hook_words) >= (len(hook_words) * 0.85):
            findings.append(
                NarrativeQAFinding(
                    code=NarrativeQAFindingCode.MISLEADING_HOOK,
                    severity=NarrativeQASeverity.WARNING,
                    subsystem=NarrativeQASubsystem.EDITORIAL,
                    explanation="Hook introduces concepts and terminology not explored in subsequent narrative sections.",
                    affected_section_orders=[hook.section_order],
                    affected_section_ids=[hook.id],
                    contributing_sources=["HookQAEvaluator"],
                    recommended_remediation="Ensure hook promises only topics and outcomes delivered by the narrative.",
                )
            )

        return findings


class CoherenceQAEvaluator:
    """Evaluates narrative progression, repetition, and semantic bridges between sections."""

    def evaluate(self, plan: NarrativePlan) -> list[NarrativeQAFinding]:
        findings: list[NarrativeQAFinding] = []
        sections = sorted(plan.sections, key=lambda s: s.section_order)

        for i in range(len(sections) - 1):
            s1 = sections[i]
            s2 = sections[i + 1]

            # Repetition check (objective tokens)
            w1 = set(re.findall(r"\w+", s1.objective.lower()))
            w2 = set(re.findall(r"\w+", s2.objective.lower()))
            if len(w1) > 3 and len(w2) > 3:
                overlap = len(w1 & w2) / max(len(w1), len(w2))
                if overlap > 0.8:
                    findings.append(
                        NarrativeQAFinding(
                            code=NarrativeQAFindingCode.NARRATIVE_REPETITION,
                            severity=NarrativeQASeverity.WARNING,
                            subsystem=NarrativeQASubsystem.EDITORIAL,
                            explanation=(
                                f"High objective repetition between adjacent sections "
                                f"{s1.section_order} and {s2.section_order} ({overlap:.1%})."
                            ),
                            affected_section_orders=[s1.section_order, s2.section_order],
                            contributing_sources=["CoherenceQAEvaluator"],
                            recommended_remediation="Merge or differentiate repetitive section objectives.",
                        )
                    )

            # Circular development check (s[i] repeats concepts of s[i-2])
            if i >= 2:
                s0 = sections[i - 1]
                s_prev = sections[i - 2]
                if s1.role == s_prev.role == NarrativeSectionRole.DEVELOPMENT:
                    shared_prev = set(s1.key_information) & set(s_prev.key_information)
                    if shared_prev:
                        findings.append(
                            NarrativeQAFinding(
                                code=NarrativeQAFindingCode.CIRCULAR_DEVELOPMENT,
                                severity=NarrativeQASeverity.WARNING,
                                subsystem=NarrativeQASubsystem.EDITORIAL,
                                explanation=(
                                    f"Section {s1.section_order} circularly repeats key information "
                                    f"from section {s_prev.section_order} without advancement."
                                ),
                                affected_section_orders=[s_prev.section_order, s1.section_order],
                                contributing_sources=["CoherenceQAEvaluator"],
                                recommended_remediation="Ensure sequential development sections progressively introduce new information.",
                            )
                        )

        return findings


class PacingQAEvaluator:
    """Adapts and promotes P21-C RetentionPacingService diagnostics into QA findings."""

    def __init__(self, pacing_service: RetentionPacingService | None = None) -> None:
        self.pacing_service = pacing_service or RetentionPacingService()

    def evaluate(
        self,
        plan: NarrativePlan,
        pacing_plan: PacingPlan | None = None,
    ) -> list[NarrativeQAFinding]:
        findings: list[NarrativeQAFinding] = []
        if not pacing_plan:
            try:
                pacing_plan = self.pacing_service.audit_plan(plan)
            except Exception as e:
                logger.warning("Pacing audit failed in QA evaluator: %s", e)
                return findings

        code_map: dict[PacingFindingCode, tuple[NarrativeQAFindingCode, NarrativeQASeverity]] = {
            PacingFindingCode.DEAD_SECTION: (
                NarrativeQAFindingCode.DEAD_ZONE,
                NarrativeQASeverity.ERROR,
            ),
            PacingFindingCode.LOW_INFORMATION_PROGRESS: (
                NarrativeQAFindingCode.DEAD_ZONE,
                NarrativeQASeverity.WARNING,
            ),
            PacingFindingCode.DENSITY_OVERLOADED: (
                NarrativeQAFindingCode.OVERLOADED_SECTION,
                NarrativeQASeverity.WARNING,
            ),
            PacingFindingCode.DENSITY_UNDERLOADED: (
                NarrativeQAFindingCode.UNDERLOADED_SECTION,
                NarrativeQASeverity.WARNING,
            ),
            PacingFindingCode.LOOP_HELD_TOO_LONG: (
                NarrativeQAFindingCode.OPEN_LOOP_TOO_LONG,
                NarrativeQASeverity.WARNING,
            ),
            PacingFindingCode.LOOP_RESOLVED_TOO_QUICKLY: (
                NarrativeQAFindingCode.PAYOFF_TOO_EARLY,
                NarrativeQASeverity.WARNING,
            ),
            PacingFindingCode.OVERLONG_CONTEXT: (
                NarrativeQAFindingCode.OVERLONG_CONTEXT,
                NarrativeQASeverity.WARNING,
            ),
            PacingFindingCode.FLAT_ESCALATION: (
                NarrativeQAFindingCode.FLAT_ESCALATION,
                NarrativeQASeverity.WARNING,
            ),
        }

        for pf in pacing_plan.findings:
            mapping = code_map.get(pf.code)
            if not mapping:
                continue

            qa_code, default_sev = mapping
            sev = (
                NarrativeQASeverity.ERROR
                if pf.severity == PacingFindingSeverity.BLOCKING
                else default_sev
            )

            orders = [pf.section_order] if pf.section_order is not None else []
            remediation = self._get_pacing_remediation(qa_code)

            findings.append(
                NarrativeQAFinding(
                    code=qa_code,
                    severity=sev,
                    subsystem=NarrativeQASubsystem.PACING,
                    explanation=pf.message,
                    affected_section_orders=orders,
                    contributing_sources=["RetentionPacingService"],
                    recommended_remediation=remediation,
                )
            )

        return findings

    @staticmethod
    def _get_pacing_remediation(code: NarrativeQAFindingCode) -> str:
        if code == NarrativeQAFindingCode.DEAD_ZONE:
            return "Inject factual claims, advance the open loop, or compress the section duration."
        if code == NarrativeQAFindingCode.OVERLOADED_SECTION:
            return "Split claims across multiple sections or reduce target information density."
        if code == NarrativeQAFindingCode.UNDERLOADED_SECTION:
            return "Shorten section duration or add key explanatory points."
        if code == NarrativeQAFindingCode.OPEN_LOOP_TOO_LONG:
            return "Advance payoff timing or insert interim partial reveals."
        if code == NarrativeQAFindingCode.PAYOFF_TOO_EARLY:
            return "Delay payoff to allow audience curiosity and evidence progression to build."
        if code == NarrativeQAFindingCode.OVERLONG_CONTEXT:
            return "Compress context duration to leave budget for main development."
        if code == NarrativeQAFindingCode.FLAT_ESCALATION:
            return "Structure narrative progression so later developments escalate in significance."
        return "Apply pacing optimization recommendations."


class ChannelFitQAEvaluator:
    """Evaluates narrative alignment with channel DNA tone, audience, and pace."""

    def evaluate(
        self,
        plan: NarrativePlan,
        channel_dna: ChannelDNA | dict[str, Any] | None,
    ) -> list[NarrativeQAFinding]:
        findings: list[NarrativeQAFinding] = []
        if not channel_dna:
            return findings

        dna_dict = (
            channel_dna.model_dump()
            if isinstance(channel_dna, ChannelDNA)
            else dict(channel_dna)
        )
        brand_voice = dna_dict.get("brand_voice") or {}
        audience = dna_dict.get("audience") or {}

        # Tone check
        tones = [str(t).upper() for t in brand_voice.get("tone", [])]
        if "ACADEMIC" in tones or "AUTHORITATIVE" in tones:
            # Plan must have grounded sections
            grounded_count = sum(1 for s in plan.sections if len(s.get_grounding_claim_ids()) > 0)
            if grounded_count < (len(plan.sections) // 2):
                findings.append(
                    NarrativeQAFinding(
                        code=NarrativeQAFindingCode.CHANNEL_TONE_MISMATCH,
                        severity=NarrativeQASeverity.WARNING,
                        subsystem=NarrativeQASubsystem.CHANNEL_FIT,
                        explanation=(
                            "Channel DNA specifies AUTHORITATIVE/ACADEMIC tone, but fewer than "
                            "half of sections contain factual claim citations."
                        ),
                        contributing_sources=["ChannelFitQAEvaluator"],
                        recommended_remediation="Strengthen grounding coverage to uphold authoritative brand voice.",
                    )
                )

        # Audience knowledge level check
        knowledge_level = str(audience.get("knowledge_level") or "").upper()
        if knowledge_level == "BEGINNER":
            # Check for excessive context or overcomplicated density
            context_sections = plan.get_sections_by_role(NarrativeSectionRole.CONTEXT)
            if not context_sections and plan.format_profile != NarrativeFormatProfile.SHORT:
                findings.append(
                    NarrativeQAFinding(
                        code=NarrativeQAFindingCode.AUDIENCE_DEPTH_MISMATCH,
                        severity=NarrativeQASeverity.WARNING,
                        subsystem=NarrativeQASubsystem.CHANNEL_FIT,
                        explanation=(
                            "Channel audience is BEGINNER, but narrative contains no introductory "
                            "CONTEXT section to establish foundational concepts."
                        ),
                        contributing_sources=["ChannelFitQAEvaluator"],
                        recommended_remediation="Add a bounded CONTEXT section before technical development.",
                    )
                )

        # Pacing style check
        expected_pace = str(brand_voice.get("pace") or "").upper()
        if expected_pace == "FAST":
            context_dur = sum(
                s.target_duration_seconds
                for s in plan.get_sections_by_role(NarrativeSectionRole.CONTEXT)
            )
            if context_dur > (plan.target_duration_seconds * 0.20):
                findings.append(
                    NarrativeQAFinding(
                        code=NarrativeQAFindingCode.PACING_STYLE_MISMATCH,
                        severity=NarrativeQASeverity.WARNING,
                        subsystem=NarrativeQASubsystem.CHANNEL_FIT,
                        explanation=(
                            f"Channel DNA requests FAST pace, but context accounts for "
                            f"{context_dur}s (>{plan.target_duration_seconds * 0.20:.0f}s)."
                        ),
                        contributing_sources=["ChannelFitQAEvaluator"],
                        recommended_remediation="Compress context to align with fast-paced channel style.",
                    )
                )

        # CTA check
        cta_sections = plan.get_sections_by_role(NarrativeSectionRole.CTA)
        if len(cta_sections) > 1:
            findings.append(
                NarrativeQAFinding(
                    code=NarrativeQAFindingCode.CTA_STYLE_MISMATCH,
                    severity=NarrativeQASeverity.WARNING,
                    subsystem=NarrativeQASubsystem.CHANNEL_FIT,
                    explanation=f"Multiple separate CTA sections ({len(cta_sections)}) dilute viewer focus.",
                    affected_section_orders=[s.section_order for s in cta_sections],
                    contributing_sources=["ChannelFitQAEvaluator"],
                    recommended_remediation="Consolidate calls-to-action into a single concise closing section.",
                )
            )

        return findings


class EditorialCompletenessEvaluator:
    """Evaluates narrative wholeness, contextual sufficiency, and ending closure."""

    def evaluate(self, plan: NarrativePlan) -> list[NarrativeQAFinding]:
        findings: list[NarrativeQAFinding] = []

        dev_sections = plan.get_sections_by_role(NarrativeSectionRole.DEVELOPMENT)
        payoff_sections = plan.get_sections_by_role(NarrativeSectionRole.PAYOFF)
        closing_sections = plan.get_sections_by_role(NarrativeSectionRole.CLOSING)

        # Missing context check: if payoff exists without development
        if payoff_sections and not dev_sections:
            findings.append(
                NarrativeQAFinding(
                    code=NarrativeQAFindingCode.MISSING_CONTEXT,
                    severity=NarrativeQASeverity.ERROR,
                    subsystem=NarrativeQASubsystem.EDITORIAL,
                    explanation="Payoff provided without substantive DEVELOPMENT sections to build understanding.",
                    affected_section_orders=[s.section_order for s in payoff_sections],
                    contributing_sources=["EditorialCompletenessEvaluator"],
                    recommended_remediation="Insert DEVELOPMENT section(s) before PAYOFF.",
                )
            )

        # Development too thin check (< 20% of total duration for non-short formats)
        if plan.format_profile != NarrativeFormatProfile.SHORT and dev_sections:
            dev_dur = sum(s.target_duration_seconds for s in dev_sections)
            if dev_dur < (plan.target_duration_seconds * 0.20):
                findings.append(
                    NarrativeQAFinding(
                        code=NarrativeQAFindingCode.DEVELOPMENT_TOO_THIN,
                        severity=NarrativeQASeverity.WARNING,
                        subsystem=NarrativeQASubsystem.EDITORIAL,
                        explanation=(
                            f"Development duration ({dev_dur}s) is under 20% of total plan duration "
                            f"({plan.target_duration_seconds}s)."
                        ),
                        affected_section_orders=[s.section_order for s in dev_sections],
                        contributing_sources=["EditorialCompletenessEvaluator"],
                        recommended_remediation="Expand explanatory development budget.",
                    )
                )

        # Abrupt closing check (< 5 seconds)
        for c in closing_sections:
            if c.target_duration_seconds < 5:
                findings.append(
                    NarrativeQAFinding(
                        code=NarrativeQAFindingCode.CLOSING_ABRUPT,
                        severity=NarrativeQASeverity.WARNING,
                        subsystem=NarrativeQASubsystem.EDITORIAL,
                        explanation=f"Closing section {c.section_order} is abruptly short ({c.target_duration_seconds}s).",
                        affected_section_orders=[c.section_order],
                        affected_section_ids=[c.id],
                        contributing_sources=["EditorialCompletenessEvaluator"],
                        recommended_remediation="Allocate at least 5-10s for closing remarks.",
                    )
                )

        return findings


# ── Finding Deduplication & Severity Policy ──────────────────────────────────


class FindingDeduplicator:
    """Collapses overlapping findings across subsystems into canonical findings."""

    @staticmethod
    def deduplicate(findings: list[NarrativeQAFinding]) -> list[NarrativeQAFinding]:
        deduped: dict[tuple[NarrativeQAFindingCode, tuple[int, ...]], NarrativeQAFinding] = {}

        for f in findings:
            key = (f.code, tuple(sorted(f.affected_section_orders)))
            if key not in deduped:
                deduped[key] = f
            else:
                existing = deduped[key]
                # Merge sources
                all_sources = sorted(
                    list(set(existing.contributing_sources + f.contributing_sources))
                )
                # Keep highest severity
                highest_sev = max(existing.severity, f.severity)
                # Merge section IDs
                all_ids = list({str(x): x for x in (existing.affected_section_ids + f.affected_section_ids)}.values())
                # Prefer more detailed explanation or combine
                explanation = (
                    existing.explanation
                    if len(existing.explanation) >= len(f.explanation)
                    else f.explanation
                )
                remediation = existing.recommended_remediation or f.recommended_remediation

                deduped[key] = NarrativeQAFinding(
                    code=existing.code,
                    severity=highest_sev,
                    subsystem=existing.subsystem,
                    explanation=explanation,
                    affected_section_orders=existing.affected_section_orders,
                    affected_section_ids=all_ids,
                    contributing_sources=all_sources,
                    recommended_remediation=remediation,
                )

        return list(deduped.values())


class SeverityAndAcceptancePolicy:
    """Evaluates final acceptance status and builds actionable recommendations."""

    @staticmethod
    def evaluate_status(
        findings: list[NarrativeQAFinding],
    ) -> tuple[NarrativeQAStatus, NarrativeQASeverity]:
        if not findings:
            return NarrativeQAStatus.PASS, NarrativeQASeverity.INFO

        severities = [f.severity for f in findings]
        highest = max(severities)

        # Any BLOCKER causes immediate rejection
        if any(s == NarrativeQASeverity.BLOCKER for s in severities):
            return NarrativeQAStatus.FAIL, NarrativeQASeverity.BLOCKER

        # Any ERROR requires plan revision
        if any(s == NarrativeQASeverity.ERROR for s in severities):
            return NarrativeQAStatus.REVISE, NarrativeQASeverity.ERROR

        # If only WARNINGs exist, determine if revision is strictly required
        actionable_warning_codes = {
            NarrativeQAFindingCode.DEAD_ZONE,
            NarrativeQAFindingCode.OVERLONG_CONTEXT,
            NarrativeQAFindingCode.OPEN_LOOP_TOO_LONG,
            NarrativeQAFindingCode.PAYOFF_INCOMPLETE,
            NarrativeQAFindingCode.NARRATIVE_REPETITION,
            NarrativeQAFindingCode.WEAK_GROUNDING_COVERAGE,
        }
        if any(f.code in actionable_warning_codes for f in findings):
            return NarrativeQAStatus.REVISE, NarrativeQASeverity.WARNING

        # Minor advisory warnings or INFO pass
        return NarrativeQAStatus.PASS, highest

    @staticmethod
    def build_recommendations(
        findings: list[NarrativeQAFinding],
    ) -> list[NarrativeQARecommendation]:
        recommendations: list[NarrativeQARecommendation] = []
        action_map: dict[NarrativeQAFindingCode, NarrativeQARecommendationAction] = {
            NarrativeQAFindingCode.OVERLONG_CONTEXT: NarrativeQARecommendationAction.SHORTEN_CONTEXT,
            NarrativeQAFindingCode.PAYOFF_LACKING_EVIDENCE: NarrativeQARecommendationAction.STRENGTHEN_PAYOFF_EVIDENCE,
            NarrativeQAFindingCode.WEAK_GROUNDING_COVERAGE: NarrativeQARecommendationAction.ADD_GROUNDING,
            NarrativeQAFindingCode.PAYOFF_NOT_SUPPORTED: NarrativeQARecommendationAction.STRENGTHEN_PAYOFF_EVIDENCE,
            NarrativeQAFindingCode.NARRATIVE_REPETITION: NarrativeQARecommendationAction.MERGE_SECTIONS,
            NarrativeQAFindingCode.OPEN_LOOP_TOO_LONG: NarrativeQARecommendationAction.ADVANCE_PAYOFF,
            NarrativeQAFindingCode.PAYOFF_TOO_EARLY: NarrativeQARecommendationAction.DELAY_PAYOFF,
            NarrativeQAFindingCode.OVERLONG_HOOK: NarrativeQARecommendationAction.TIGHTEN_HOOK,
            NarrativeQAFindingCode.WEAK_HOOK: NarrativeQARecommendationAction.TIGHTEN_HOOK,
            NarrativeQAFindingCode.CTA_STYLE_MISMATCH: NarrativeQARecommendationAction.REMOVE_REDUNDANT_CTA,
            NarrativeQAFindingCode.INVALID_DURATION: NarrativeQARecommendationAction.REBALANCE_DURATION,
            NarrativeQAFindingCode.DEVELOPMENT_TOO_THIN: NarrativeQARecommendationAction.EXPAND_DEVELOPMENT,
            NarrativeQAFindingCode.OVERLOADED_SECTION: NarrativeQARecommendationAction.SPLIT_OVERLOADED_SECTION,
        }

        for f in findings:
            action = action_map.get(f.code)
            if action and f.recommended_remediation:
                recommendations.append(
                    NarrativeQARecommendation(
                        action=action,
                        affected_section_orders=f.affected_section_orders,
                        remediation=f.recommended_remediation,
                    )
                )

        return recommendations


# ── Primary Application Service ─────────────────────────────────────────────


class NarrativeQAService:
    """Primary Narrative QA & Acceptance service."""

    def __init__(
        self,
        structural_evaluator: StructuralQAEvaluator | None = None,
        grounding_evaluator: GroundingQAEvaluator | None = None,
        promise_evaluator: PromisePayoffQAEvaluator | None = None,
        hook_evaluator: HookQAEvaluator | None = None,
        coherence_evaluator: CoherenceQAEvaluator | None = None,
        pacing_evaluator: PacingQAEvaluator | None = None,
        channel_evaluator: ChannelFitQAEvaluator | None = None,
        completeness_evaluator: EditorialCompletenessEvaluator | None = None,
    ) -> None:
        self.structural_evaluator = structural_evaluator or StructuralQAEvaluator()
        self.grounding_evaluator = grounding_evaluator or GroundingQAEvaluator()
        self.promise_evaluator = promise_evaluator or PromisePayoffQAEvaluator()
        self.hook_evaluator = hook_evaluator or HookQAEvaluator()
        self.coherence_evaluator = coherence_evaluator or CoherenceQAEvaluator()
        self.pacing_evaluator = pacing_evaluator or PacingQAEvaluator()
        self.channel_evaluator = channel_evaluator or ChannelFitQAEvaluator()
        self.completeness_evaluator = (
            completeness_evaluator or EditorialCompletenessEvaluator()
        )

    def evaluate_plan(
        self,
        plan: NarrativePlan,
        research_brief: dict[str, Any] | None = None,
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
        pacing_plan: PacingPlan | None = None,
        model_reviewer: NarrativeEditorialModelReviewer | None = None,
    ) -> NarrativeQAResult:
        """Run comprehensive editorial QA evaluation across all subsystems."""
        raw_findings: list[NarrativeQAFinding] = []

        # 1. Structural evaluation
        raw_findings.extend(self.structural_evaluator.evaluate(plan))

        # 2. Grounding quality
        raw_findings.extend(
            self.grounding_evaluator.evaluate(plan, research_brief=research_brief)
        )

        # 3. Promise / payoff quality
        raw_findings.extend(self.promise_evaluator.evaluate(plan))

        # 4. Hook quality
        raw_findings.extend(self.hook_evaluator.evaluate(plan))

        # 5. Coherence and repetition
        raw_findings.extend(self.coherence_evaluator.evaluate(plan))

        # 6. Pacing and retention
        raw_findings.extend(
            self.pacing_evaluator.evaluate(plan, pacing_plan=pacing_plan)
        )

        # 7. Channel DNA fit
        raw_findings.extend(
            self.channel_evaluator.evaluate(plan, channel_dna=channel_dna)
        )

        # 8. Editorial completeness
        raw_findings.extend(self.completeness_evaluator.evaluate(plan))

        # 9. Optional Model-assisted review
        reviewer_id = "NONE"
        if model_reviewer:
            try:
                model_findings = model_reviewer.review(
                    plan,
                    research_brief=research_brief,
                    channel_dna=channel_dna,
                )
                raw_findings.extend(model_findings)
                reviewer_id = type(model_reviewer).__name__
            except Exception as e:
                logger.warning("Optional model editorial review failed: %s", e)

        # 10. Deduplication
        deduped_findings = FindingDeduplicator.deduplicate(raw_findings)

        # 11. Acceptance & Severity Policy
        status, highest_sev = SeverityAndAcceptancePolicy.evaluate_status(
            deduped_findings
        )
        recommendations = SeverityAndAcceptancePolicy.build_recommendations(
            deduped_findings
        )

        # 12. Provenance recording
        provenance = {
            "narrative_plan_id": str(plan.id),
            "narrative_plan_version": plan.version,
            "pacing_plan_id": str(pacing_plan.id) if pacing_plan else None,
            "qa_engine_version": "1.0.0-p21d",
            "model_reviewer": reviewer_id,
            "total_findings": len(deduped_findings),
            "blocker_count": sum(
                1 for f in deduped_findings if f.severity == NarrativeQASeverity.BLOCKER
            ),
            "error_count": sum(
                1 for f in deduped_findings if f.severity == NarrativeQASeverity.ERROR
            ),
            "warning_count": sum(
                1 for f in deduped_findings if f.severity == NarrativeQASeverity.WARNING
            ),
            "info_count": sum(
                1 for f in deduped_findings if f.severity == NarrativeQASeverity.INFO
            ),
            "status": status.value,
            "evaluated_at": datetime.now(UTC).isoformat(),
        }

        # Store durable provenance in NarrativePlan metadata without schema changes
        plan.metadata["p21d_qa"] = provenance

        return NarrativeQAResult(
            plan_id=plan.id,
            plan_version=plan.version,
            status=status,
            highest_severity=highest_sev,
            findings=deduped_findings,
            recommendations=recommendations,
            pacing_plan_id=pacing_plan.id if pacing_plan else None,
            provenance=provenance,
            evaluated_at=datetime.now(UTC),
        )

    def enforce_script_gate(
        self,
        plan: NarrativePlan,
        qa_result: NarrativeQAResult | None = None,
        research_brief: dict[str, Any] | None = None,
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
        pacing_plan: PacingPlan | None = None,
    ) -> NarrativeQAResult:
        """Enforce the script generation acceptance gate.

        Raises NarrativeQAGateError if status is REVISE or FAIL.
        """
        result = qa_result or self.evaluate_plan(
            plan=plan,
            research_brief=research_brief,
            channel_dna=channel_dna,
            pacing_plan=pacing_plan,
        )

        if result.status != NarrativeQAStatus.PASS:
            raise NarrativeQAGateError(
                f"NarrativePlan {plan.id} (v{plan.version}) blocked by script generation gate: "
                f"status={result.status.value}, blockers={result.blocker_count}, errors={result.error_count}",
                qa_result=result,
            )

        return result
