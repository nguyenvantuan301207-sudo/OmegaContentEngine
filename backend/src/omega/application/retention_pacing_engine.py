"""Retention & Pacing Intelligence Engine for P21-C.

Implements deterministic section-duration planning, information density modeling,
open-loop lifetime auditing, reveal progression tracking, dead-zone detection,
repetition analysis, escalation curves, and revision-based pacing optimization.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from omega.domain.channel_dna import ChannelDNA
from omega.domain.narrative_pacing import (
    OpenLoopMetric,
    PacingAdjustment,
    PacingFinding,
    PacingFindingCode,
    PacingFindingSeverity,
    PacingPlan,
    PacingProfile,
    RevealStage,
    SectionTimingDetail,
)
from omega.domain.narrative_plan import (
    FORMAT_PROFILE_CONSTRAINTS,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.narrative_strategy import STRATEGY_CATALOG, NarrativeStrategy
from omega.logging import get_logger

logger = get_logger("omega-retention-pacing")


# ======================================================================
# 1. Pacing Profile Resolver
# ======================================================================

class PacingProfileResolver:
    """Resolves effective PacingProfile from ChannelDNA and editorial hints."""

    @staticmethod
    def resolve_profile(channel_dna: ChannelDNA | dict[str, Any] | None) -> PacingProfile:
        if not channel_dna:
            return PacingProfile.BALANCED

        dna_dict = channel_dna.model_dump() if isinstance(channel_dna, ChannelDNA) else dict(channel_dna)

        # 1. Direct v2 Narrative Preferences check
        narr_prefs = dna_dict.get("narrative_preferences") or {}
        pref_pace = str(narr_prefs.get("preferred_pacing") or "").upper()
        if pref_pace == "FAST":
            return PacingProfile.FAST
        if pref_pace == "DELIBERATE":
            return PacingProfile.DELIBERATE

        # 2. Direct v2 Editorial Voice check
        ed_voice = dna_dict.get("editorial_voice") or {}
        ed_energy = str(ed_voice.get("energy") or "").upper()
        if ed_energy in ("HIGH_ENERGY", "DYNAMIC"):
            return PacingProfile.FAST

        # 3. Fallback to Brand Voice
        brand_voice = dna_dict.get("brand_voice", {})
        pace_str = str(brand_voice.get("pace", "")).upper()
        tone_str = str(brand_voice.get("tone", "")).upper()

        if "FAST" in pace_str or "ENERGETIC" in pace_str:
            return PacingProfile.FAST
        if "MEASURED" in pace_str or "RELAXED" in pace_str or "SLOW" in pace_str or "DELIBERATE" in pace_str:
            return PacingProfile.DELIBERATE
        if "ACADEMIC" in tone_str or "ANALYTICAL" in tone_str:
            return PacingProfile.DELIBERATE

        if pref_pace == "BALANCED":
            return PacingProfile.BALANCED

        return PacingProfile.BALANCED


# ======================================================================
# 2. Section Timing Engine
# ======================================================================

class SectionTimingEngine:
    """Calculates deterministic, format-aware section duration allocations."""

    # Base duration weights by role and pacing profile
    ROLE_WEIGHTS: dict[PacingProfile, dict[NarrativeSectionRole, float]] = {
        PacingProfile.FAST: {
            NarrativeSectionRole.HOOK: 0.15,
            NarrativeSectionRole.PROMISE: 0.08,
            NarrativeSectionRole.CONTEXT: 0.10,
            NarrativeSectionRole.DEVELOPMENT: 0.35,
            NarrativeSectionRole.ESCALATION: 0.15,
            NarrativeSectionRole.PAYOFF: 0.20,
            NarrativeSectionRole.TAKEAWAY: 0.10,
            NarrativeSectionRole.CLOSING: 0.05,
            NarrativeSectionRole.CTA: 0.05,
        },
        PacingProfile.BALANCED: {
            NarrativeSectionRole.HOOK: 0.12,
            NarrativeSectionRole.PROMISE: 0.08,
            NarrativeSectionRole.CONTEXT: 0.18,
            NarrativeSectionRole.DEVELOPMENT: 0.40,
            NarrativeSectionRole.ESCALATION: 0.20,
            NarrativeSectionRole.PAYOFF: 0.22,
            NarrativeSectionRole.TAKEAWAY: 0.12,
            NarrativeSectionRole.CLOSING: 0.06,
            NarrativeSectionRole.CTA: 0.05,
        },
        PacingProfile.DELIBERATE: {
            NarrativeSectionRole.HOOK: 0.10,
            NarrativeSectionRole.PROMISE: 0.07,
            NarrativeSectionRole.CONTEXT: 0.25,
            NarrativeSectionRole.DEVELOPMENT: 0.45,
            NarrativeSectionRole.ESCALATION: 0.22,
            NarrativeSectionRole.PAYOFF: 0.25,
            NarrativeSectionRole.TAKEAWAY: 0.15,
            NarrativeSectionRole.CLOSING: 0.08,
            NarrativeSectionRole.CTA: 0.05,
        },
    }

    @classmethod
    def calculate_durations(
        cls,
        sections: list[NarrativeSection],
        format_profile: NarrativeFormatProfile,
        target_total_duration: int,
        pacing_profile: PacingProfile = PacingProfile.BALANCED,
    ) -> list[int]:
        """Allocate target duration per section so sum approximates target_total_duration."""
        if not sections:
            return []

        constraints = FORMAT_PROFILE_CONSTRAINTS[format_profile]
        weights_map = cls.ROLE_WEIGHTS[pacing_profile]

        # 1. Determine raw weights per section
        raw_weights = [weights_map.get(s.role, 0.20) for s in sections]
        total_raw_weight = sum(raw_weights) or 1.0

        # 2. Initial proportional distribution
        durations: list[int] = []
        for w in raw_weights:
            dur = int(round((w / total_raw_weight) * target_total_duration))
            durations.append(max(dur, 5))

        # 3. Enforce hook bound constraint
        max_hook = constraints.max_hook_duration_seconds
        if durations[0] > max_hook:
            excess = durations[0] - max_hook
            durations[0] = max_hook
            # Redistribute excess to development or payoff
            dev_indices = [i for i, s in enumerate(sections) if s.role in (NarrativeSectionRole.DEVELOPMENT, NarrativeSectionRole.PAYOFF)]
            if dev_indices:
                for idx in dev_indices:
                    durations[idx] += excess // len(dev_indices)

        # 4. Enforce exact sum normalization
        current_sum = sum(durations)
        diff = target_total_duration - current_sum
        if diff != 0:
            # Adjust last non-CTA/closing section or last section
            adj_idx = len(durations) - 1
            for i in range(len(sections) - 1, -1, -1):
                if sections[i].role not in (NarrativeSectionRole.CLOSING, NarrativeSectionRole.CTA):
                    adj_idx = i
                    break
            durations[adj_idx] = max(5, durations[adj_idx] + diff)

        return durations


# ======================================================================
# 3. Information Density Engine
# ======================================================================

class InformationDensityEngine:
    """Evaluates and assigns factual information density per section."""

    @staticmethod
    def evaluate_density(
        section: NarrativeSection,
        format_profile: NarrativeFormatProfile,
        duration: int,
    ) -> tuple[InformationDensity, list[PacingFinding]]:
        findings: list[PacingFinding] = []
        claim_count = len(section.grounding_references)
        key_info_count = len(section.key_information)

        # Density score based on factual density per second
        density_rate = claim_count / max(duration, 1)

        # High concept density
        if claim_count >= 2 or (claim_count >= 1 and duration <= 15):
            density = InformationDensity.HIGH
        elif claim_count == 1 or key_info_count >= 2:
            density = InformationDensity.MEDIUM
        else:
            density = InformationDensity.LOW

        # Detect overload: too many claims in short duration
        if format_profile == NarrativeFormatProfile.SHORT and duration < 12 and claim_count > 2:
            findings.append(
                PacingFinding(
                    code=PacingFindingCode.DENSITY_OVERLOADED,
                    severity=PacingFindingSeverity.WARNING,
                    message=f"Section {section.section_order} has {claim_count} claims packed into {duration}s.",
                    section_order=section.section_order,
                    metric_value=density_rate,
                )
            )
        elif format_profile in (NarrativeFormatProfile.MEDIUM, NarrativeFormatProfile.LONG) and duration < 20 and claim_count > 3:
            findings.append(
                PacingFinding(
                    code=PacingFindingCode.DENSITY_OVERLOADED,
                    severity=PacingFindingSeverity.WARNING,
                    message=f"Section {section.section_order} has {claim_count} claims in {duration}s.",
                    section_order=section.section_order,
                    metric_value=density_rate,
                )
            )

        # Detect underload: long duration with 0 claims or key points
        if section.role in (NarrativeSectionRole.CONTEXT, NarrativeSectionRole.DEVELOPMENT, NarrativeSectionRole.PAYOFF):
            if duration >= 25 and claim_count == 0 and key_info_count <= 1:
                findings.append(
                    PacingFinding(
                        code=PacingFindingCode.DENSITY_UNDERLOADED,
                        severity=PacingFindingSeverity.WARNING,
                        message=f"Section {section.section_order} ({section.role.value}) runs {duration}s without factual progression.",
                        section_order=section.section_order,
                        metric_value=0.0,
                    )
                )

        return density, findings


# ======================================================================
# 4. Open-Loop Timing Engine
# ======================================================================

class OpenLoopTimingEngine:
    """Analyzes curiosity loop lifetimes, payoff delays, and loop coherence."""

    # Format acceptable delay ranges (min_seconds, max_seconds)
    ACCEPTABLE_LOOP_RANGES: dict[NarrativeFormatProfile, tuple[int, int]] = {
        NarrativeFormatProfile.SHORT: (10, 35),
        NarrativeFormatProfile.MEDIUM: (45, 240),
        NarrativeFormatProfile.LONG: (120, 900),
    }

    @classmethod
    def analyze_loops(
        cls,
        sections: list[NarrativeSection],
        format_profile: NarrativeFormatProfile,
    ) -> tuple[list[OpenLoopMetric], list[PacingFinding]]:
        metrics: list[OpenLoopMetric] = []
        findings: list[PacingFinding] = []

        min_range, max_range = cls.ACCEPTABLE_LOOP_RANGES[format_profile]

        # Locate promises
        promises: dict[str, tuple[int, int]] = {}  # promise_id -> (section_order, start_time)
        cum_time = 0
        section_start_times: dict[int, int] = {}
        for s in sections:
            section_start_times[s.section_order] = cum_time
            if s.promise_id:
                if s.promise_id in promises:
                    findings.append(
                        PacingFinding(
                            code=PacingFindingCode.DUPLICATE_PROMISE,
                            severity=PacingFindingSeverity.WARNING,
                            message=f"Duplicate promise_id '{s.promise_id}' opened again in section {s.section_order}.",
                            section_order=s.section_order,
                        )
                    )
                else:
                    promises[s.promise_id] = (s.section_order, cum_time)
            cum_time += s.target_duration_seconds

        # Locate payoffs
        payoffs: dict[str, int] = {}  # promise_id -> section_order
        for s in sections:
            if s.payoff_reference:
                payoffs[s.payoff_reference] = s.section_order

        # Check for multiple simultaneous unresolved loops
        if len(promises) > 2:
            findings.append(
                PacingFinding(
                    code=PacingFindingCode.MULTIPLE_UNRESOLVED_LOOPS,
                    severity=PacingFindingSeverity.WARNING,
                    message=f"NarrativePlan opens {len(promises)} simultaneous promise loops; increases cognitive strain.",
                )
            )

        # Analyze each promise
        for pid, (prom_order, prom_start) in promises.items():
            pay_order = payoffs.get(pid)
            if pay_order is not None:
                pay_start = section_start_times[pay_order]
                elapsed = pay_start - prom_start
                span = pay_order - prom_order

                status = "OPTIMAL"
                if elapsed < min_range:
                    status = "TOO_FAST"
                    findings.append(
                        PacingFinding(
                            code=PacingFindingCode.LOOP_RESOLVED_TOO_QUICKLY,
                            severity=PacingFindingSeverity.WARNING,
                            message=f"Promise loop '{pid}' resolved after only {elapsed}s (< {min_range}s threshold).",
                            section_order=pay_order,
                            metric_value=float(elapsed),
                        )
                    )
                elif elapsed > max_range:
                    status = "TOO_SLOW"
                    findings.append(
                        PacingFinding(
                            code=PacingFindingCode.LOOP_HELD_TOO_LONG,
                            severity=PacingFindingSeverity.WARNING,
                            message=f"Promise loop '{pid}' held for {elapsed}s (> {max_range}s threshold).",
                            section_order=pay_order,
                            metric_value=float(elapsed),
                        )
                    )

                metrics.append(
                    OpenLoopMetric(
                        promise_id=pid,
                        promise_section_order=prom_order,
                        payoff_section_order=pay_order,
                        elapsed_duration_seconds=elapsed,
                        section_span=span,
                        is_resolved=True,
                        acceptable_min_seconds=min_range,
                        acceptable_max_seconds=max_range,
                        status=status,
                    )
                )
            else:
                metrics.append(
                    OpenLoopMetric(
                        promise_id=pid,
                        promise_section_order=prom_order,
                        payoff_section_order=None,
                        elapsed_duration_seconds=cum_time - prom_start,
                        section_span=len(sections) - prom_order,
                        is_resolved=False,
                        acceptable_min_seconds=min_range,
                        acceptable_max_seconds=max_range,
                        status="UNRESOLVED",
                    )
                )

        return metrics, findings


# ======================================================================
# 5. Reveal Timing Engine
# ======================================================================

class RevealTimingEngine:
    """Tracks narrative reveal progression and detects premature or delayed reveals."""

    @classmethod
    def assign_stages(
        cls,
        sections: list[NarrativeSection],
    ) -> tuple[list[RevealStage], list[PacingFinding]]:
        stages: list[RevealStage] = []
        findings: list[PacingFinding] = []

        total_sections = len(sections)
        for idx, s in enumerate(sections, start=1):
            if s.role in (NarrativeSectionRole.HOOK, NarrativeSectionRole.PROMISE):
                st = RevealStage.SETUP
            elif s.role == NarrativeSectionRole.CONTEXT:
                st = RevealStage.SETUP if idx <= 2 else RevealStage.PARTIAL_REVEAL
            elif s.role == NarrativeSectionRole.DEVELOPMENT:
                if idx < total_sections * 0.4:
                    st = RevealStage.PARTIAL_REVEAL
                else:
                    st = RevealStage.EVIDENCE_PROGRESSION
            elif s.role == NarrativeSectionRole.ESCALATION:
                st = RevealStage.MAJOR_REVEAL
            elif s.role in (NarrativeSectionRole.PAYOFF, NarrativeSectionRole.TAKEAWAY):
                st = RevealStage.FINAL_PAYOFF
            else:
                st = RevealStage.FINAL_PAYOFF
            stages.append(st)

        # Detect premature reveal: MAJOR_REVEAL or FINAL_PAYOFF within first 2 sections (unless SHORT)
        for i, (s, st) in enumerate(zip(sections, stages, strict=True), start=1):
            if i <= 2 and st in (RevealStage.MAJOR_REVEAL, RevealStage.FINAL_PAYOFF) and total_sections >= 5:
                findings.append(
                    PacingFinding(
                        code=PacingFindingCode.PREMATURE_REVEAL,
                        severity=PacingFindingSeverity.WARNING,
                        message=f"Premature reveal ({st.value}) occurred too early at section {i}.",
                        section_order=i,
                    )
                )

        # Detect delayed reveal: no MAJOR_REVEAL or FINAL_PAYOFF until very last section
        if total_sections >= 5 and all(st not in (RevealStage.MAJOR_REVEAL, RevealStage.FINAL_PAYOFF) for st in stages[:-1]):
            findings.append(
                PacingFinding(
                    code=PacingFindingCode.DELAYED_REVEAL,
                    severity=PacingFindingSeverity.WARNING,
                    message="Major reveal was delayed until the final section without adequate exploration.",
                    section_order=total_sections,
                )
            )

        return stages, findings


# ======================================================================
# 6. Dead-Zone and Stalled Development Detector
# ======================================================================

class DeadZoneDetector:
    """Identifies narrative intervals lacking information progression or escalation."""

    @classmethod
    def detect_dead_zones(
        cls,
        sections: list[NarrativeSection],
        total_duration: int,
    ) -> list[PacingFinding]:
        findings: list[PacingFinding] = []

        context_duration = sum(s.target_duration_seconds for s in sections if s.role == NarrativeSectionRole.CONTEXT)
        if total_duration > 0 and (context_duration / total_duration) > 0.30:
            findings.append(
                PacingFinding(
                    code=PacingFindingCode.OVERLONG_CONTEXT,
                    severity=PacingFindingSeverity.WARNING,
                    message=f"Context sections occupy {context_duration}s ({context_duration/total_duration:.1%}) of total runtime.",
                    metric_value=context_duration / total_duration,
                )
            )

        for s in sections:
            # Dead section: 0 claims, <=1 key info, no promise or payoff
            if (
                s.role not in (NarrativeSectionRole.HOOK, NarrativeSectionRole.CLOSING, NarrativeSectionRole.CTA)
                and len(s.grounding_references) == 0
                and len(s.key_information) <= 1
                and not s.promise_id
                and not s.payoff_reference
            ):
                findings.append(
                    PacingFinding(
                        code=PacingFindingCode.DEAD_SECTION,
                        severity=PacingFindingSeverity.WARNING,
                        message=f"Section {s.section_order} ({s.role.value}) has zero claims, no curiosity loop, and minimal information progression.",
                        section_order=s.section_order,
                    )
                )

            # Low information progress: long section (>25s) with 0 grounding references
            if (
                s.role in (NarrativeSectionRole.DEVELOPMENT, NarrativeSectionRole.CONTEXT)
                and s.target_duration_seconds >= 25
                and len(s.grounding_references) == 0
            ):
                findings.append(
                    PacingFinding(
                        code=PacingFindingCode.LOW_INFORMATION_PROGRESS,
                        severity=PacingFindingSeverity.WARNING,
                        message=f"Section {s.section_order} duration is {s.target_duration_seconds}s without factual claims.",
                        section_order=s.section_order,
                        metric_value=float(s.target_duration_seconds),
                    )
                )

        # Stalled development: adjacent development sections with zero claims
        for i in range(len(sections) - 1):
            s1 = sections[i]
            s2 = sections[i + 1]
            if (
                s1.role == NarrativeSectionRole.DEVELOPMENT
                and s2.role == NarrativeSectionRole.DEVELOPMENT
                and len(s1.grounding_references) == 0
                and len(s2.grounding_references) == 0
            ):
                findings.append(
                    PacingFinding(
                        code=PacingFindingCode.STALLED_DEVELOPMENT,
                        severity=PacingFindingSeverity.WARNING,
                        message=f"Adjacent development sections {s1.section_order} and {s2.section_order} both lack factual grounding progression.",
                        section_order=s2.section_order,
                    )
                )

        return findings


# ======================================================================
# 7. Repetition & Redundancy Detector
# ======================================================================

class RedundancyDetector:
    """Detects repeated claims, duplicate objectives, and redundant takeaways."""

    @classmethod
    def detect_redundancy(cls, sections: list[NarrativeSection]) -> list[PacingFinding]:
        findings: list[PacingFinding] = []

        seen_claims: dict[UUID, int] = {}
        for s in sections:
            for g in s.grounding_references:
                if g.claim_id:
                    if g.claim_id in seen_claims:
                        prev_order = seen_claims[g.claim_id]
                        findings.append(
                            PacingFinding(
                                code=PacingFindingCode.REPEATED_CLAIM,
                                severity=PacingFindingSeverity.WARNING,
                                message=f"Claim {g.claim_id} was already cited in section {prev_order} and repeated in section {s.section_order}.",
                                section_order=s.section_order,
                            )
                        )
                    else:
                        seen_claims[g.claim_id] = s.section_order

        # Repeated objectives (normalized token similarity)
        objectives: list[tuple[int, str]] = [(s.section_order, s.objective.lower().strip()) for s in sections]
        for i in range(len(objectives)):
            for j in range(i + 1, len(objectives)):
                order1, obj1 = objectives[i]
                order2, obj2 = objectives[j]
                words1 = set(obj1.split())
                words2 = set(obj2.split())
                if words1 and words2:
                    jaccard = len(words1 & words2) / len(words1 | words2)
                    if jaccard > 0.80:
                        findings.append(
                            PacingFinding(
                                code=PacingFindingCode.REPEATED_OBJECTIVE,
                                severity=PacingFindingSeverity.WARNING,
                                message=f"Section {order2} objective heavily duplicates section {order1} (similarity {jaccard:.2f}).",
                                section_order=order2,
                                metric_value=jaccard,
                            )
                        )

        # Repeated takeaway content matching earlier development
        takeaway_sections = [s for s in sections if s.role == NarrativeSectionRole.TAKEAWAY]
        dev_sections = [s for s in sections if s.role == NarrativeSectionRole.DEVELOPMENT]
        for t in takeaway_sections:
            t_words = set(t.objective.lower().split())
            for d in dev_sections:
                d_words = set(d.objective.lower().split())
                if t_words and d_words and (len(t_words & d_words) / len(t_words | d_words)) > 0.85:
                    findings.append(
                        PacingFinding(
                            code=PacingFindingCode.REPEATED_TAKEAWAY,
                            severity=PacingFindingSeverity.WARNING,
                            message=f"Takeaway in section {t.section_order} duplicates development in section {d.section_order}.",
                            section_order=t.section_order,
                        )
                    )

        return findings


# ======================================================================
# 8. Escalation Curve Engine
# ======================================================================

class EscalationCurveEngine:
    """Models and scores narrative intensity progression across sections."""

    BASE_INTENSITY: dict[NarrativeSectionRole, float] = {
        NarrativeSectionRole.HOOK: 0.75,
        NarrativeSectionRole.PROMISE: 0.65,
        NarrativeSectionRole.CONTEXT: 0.40,
        NarrativeSectionRole.DEVELOPMENT: 0.60,
        NarrativeSectionRole.ESCALATION: 0.85,
        NarrativeSectionRole.PAYOFF: 0.95,
        NarrativeSectionRole.TAKEAWAY: 0.55,
        NarrativeSectionRole.CLOSING: 0.30,
        NarrativeSectionRole.CTA: 0.25,
    }

    @classmethod
    def calculate_curve(
        cls,
        sections: list[NarrativeSection],
    ) -> tuple[list[float], list[PacingFinding]]:
        curve: list[float] = []
        findings: list[PacingFinding] = []

        total_secs = len(sections)
        for i, s in enumerate(sections):
            base = cls.BASE_INTENSITY.get(s.role, 0.50)
            # Adjust intensity by information density and progression position
            density_bonus = 0.10 if s.target_information_density == InformationDensity.HIGH else 0.0
            pos_bonus = (i / max(total_secs, 1)) * 0.15 if s.role == NarrativeSectionRole.DEVELOPMENT else 0.0
            score = round(min(1.0, base + density_bonus + pos_bonus), 3)
            curve.append(score)

        # Check for flat development curve
        dev_scores = [curve[i] for i, s in enumerate(sections) if s.role == NarrativeSectionRole.DEVELOPMENT]
        if len(dev_scores) >= 2 and max(dev_scores) - min(dev_scores) < 0.01:
            findings.append(
                PacingFinding(
                    code=PacingFindingCode.FLAT_ESCALATION,
                    severity=PacingFindingSeverity.INFO,
                    message="Development sections have a flat intensity curve without progressive escalation.",
                )
            )

        # Check for reversed progression (context higher intensity than payoff/development)
        context_scores = [curve[i] for i, s in enumerate(sections) if s.role == NarrativeSectionRole.CONTEXT]
        payoff_scores = [curve[i] for i, s in enumerate(sections) if s.role == NarrativeSectionRole.PAYOFF]
        if context_scores and payoff_scores and max(context_scores) > min(payoff_scores):
            findings.append(
                PacingFinding(
                    code=PacingFindingCode.REVERSED_ESCALATION,
                    severity=PacingFindingSeverity.WARNING,
                    message="Context section intensity exceeds payoff intensity, reversing narrative progression.",
                )
            )

        return curve, findings


# ======================================================================
# 9. Retention & Pacing Service
# ======================================================================

class RetentionPacingService:
    """Primary application service for P21-C Retention & Pacing Intelligence."""

    def audit_plan(
        self,
        plan: NarrativePlan,
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
        pacing_profile: PacingProfile | None = None,
    ) -> PacingPlan:
        """Analyze a NarrativePlan and produce a comprehensive PacingPlan."""
        profile = pacing_profile or PacingProfileResolver.resolve_profile(channel_dna)
        all_findings: list[PacingFinding] = []

        # 1. Section timing analysis
        recommended_durations = SectionTimingEngine.calculate_durations(
            sections=plan.sections,
            format_profile=plan.format_profile,
            target_total_duration=plan.target_duration_seconds,
            pacing_profile=profile,
        )

        # 2. Information density analysis
        densities: list[InformationDensity] = []
        for s, dur in zip(plan.sections, recommended_durations, strict=True):
            d, f_list = InformationDensityEngine.evaluate_density(s, plan.format_profile, dur)
            densities.append(d)
            all_findings.extend(f_list)

        # 3. Open-loop timing analysis
        loop_metrics, loop_findings = OpenLoopTimingEngine.analyze_loops(plan.sections, plan.format_profile)
        all_findings.extend(loop_findings)

        # 4. Reveal progression analysis
        reveal_stages, reveal_findings = RevealTimingEngine.assign_stages(plan.sections)
        all_findings.extend(reveal_findings)

        # 5. Dead-zone analysis
        dead_zone_findings = DeadZoneDetector.detect_dead_zones(plan.sections, plan.target_duration_seconds)
        all_findings.extend(dead_zone_findings)

        # 6. Redundancy analysis
        redundancy_findings = RedundancyDetector.detect_redundancy(plan.sections)
        all_findings.extend(redundancy_findings)

        # 7. Escalation curve
        curve, curve_findings = EscalationCurveEngine.calculate_curve(plan.sections)
        all_findings.extend(curve_findings)

        # 8. Compute recommended adjustments
        adjustments: list[PacingAdjustment] = []
        for s, rec_dur in zip(plan.sections, recommended_durations, strict=True):
            delta = rec_dur - s.target_duration_seconds
            if delta != 0:
                adj_type = "EXPAND_DURATION" if delta > 0 else "COMPRESS_DURATION"
                adjustments.append(
                    PacingAdjustment(
                        section_order=s.section_order,
                        adjustment_type=adj_type,
                        duration_delta_seconds=delta,
                        density_override=densities[s.section_order - 1],
                        rationale=f"Rebalance section {s.section_order} under {profile.value} profile.",
                    )
                )

        # Assemble timing details
        timing_details: list[SectionTimingDetail] = []
        for s, rec_dur, d, st, intensity in zip(
            plan.sections, recommended_durations, densities, reveal_stages, curve, strict=True
        ):
            timing_details.append(
                SectionTimingDetail(
                    section_id=s.id,
                    section_order=s.section_order,
                    role=s.role,
                    current_duration_seconds=s.target_duration_seconds,
                    recommended_duration_seconds=rec_dur,
                    target_information_density=d,
                    reveal_stage=st,
                    intensity_score=intensity,
                )
            )

        return PacingPlan(
            narrative_plan_id=plan.id,
            narrative_plan_version=plan.version,
            pacing_profile=profile,
            format_profile=plan.format_profile,
            target_duration_seconds=plan.target_duration_seconds,
            initial_duration_sum_seconds=sum(s.target_duration_seconds for s in plan.sections),
            optimized_duration_sum_seconds=sum(recommended_durations),
            section_timings=timing_details,
            open_loop_metrics=loop_metrics,
            findings=all_findings,
            recommended_adjustments=adjustments,
            escalation_curve=curve,
        )

    def optimize_plan(
        self,
        plan: NarrativePlan,
        plan_service: Any | None = None,
        channel_dna: ChannelDNA | dict[str, Any] | None = None,
        pacing_profile: PacingProfile | None = None,
    ) -> tuple[NarrativePlan, PacingPlan]:
        """Produce an optimized, paced NarrativePlan revision through the P21-A revision authority."""
        pacing_plan = self.audit_plan(plan, channel_dna=channel_dna, pacing_profile=pacing_profile)

        # Build adjusted sections
        timing_map = {t.section_order: t for t in pacing_plan.section_timings}
        new_sections: list[NarrativeSection] = []
        for s in plan.sections:
            timing = timing_map.get(s.section_order)
            new_dur = timing.recommended_duration_seconds if timing else s.target_duration_seconds
            new_density = timing.target_information_density if timing else s.target_information_density

            new_sections.append(
                NarrativeSection(
                    id=uuid.uuid4(),
                    section_order=s.section_order,
                    role=s.role,
                    objective=s.objective,
                    key_information=list(s.key_information),
                    grounding_references=list(s.grounding_references),
                    target_duration_seconds=new_dur,
                    target_information_density=new_density,
                    open_loop_intent=s.open_loop_intent,
                    promise_id=s.promise_id,
                    payoff_reference=s.payoff_reference,
                    notes=s.notes,
                )
            )

        updated_metadata = dict(plan.metadata)
        updated_metadata.update(
            {
                "pacing_profile": pacing_plan.pacing_profile.value,
                "pacing_findings_count": len(pacing_plan.findings),
                "pacing_adjustments_count": len(pacing_plan.recommended_adjustments),
                "pacing_optimized_at": datetime.now(UTC).isoformat(),
            }
        )

        # If NarrativePlanService is available, create durable revision
        if plan_service is not None and hasattr(plan_service, "create_revision"):
            revised_plan, _ = plan_service.create_revision(
                plan_id=plan.id,
                new_sections=new_sections,
                new_target_duration_seconds=pacing_plan.optimized_duration_sum_seconds,
                notes=f"Optimized under {pacing_plan.pacing_profile.value} profile",
                auto_validate=True,
            )
            if hasattr(revised_plan, "metadata") and isinstance(revised_plan.metadata, dict):
                revised_plan.metadata.update(updated_metadata)
            return revised_plan, pacing_plan

        # Otherwise create detached updated revision (e.g. for pure in-memory planning)
        new_version = plan.version + 1
        revised_plan = NarrativePlan(
            id=uuid.uuid4(),
            content_generation_request_id=plan.content_generation_request_id,
            topic_candidate_id=plan.topic_candidate_id,
            research_brief_id=plan.research_brief_id,
            channel_dna_revision_id=plan.channel_dna_revision_id,
            version=new_version,
            status=plan.status,
            format_profile=plan.format_profile,
            target_duration_seconds=pacing_plan.optimized_duration_sum_seconds,
            estimated_duration_seconds=pacing_plan.optimized_duration_sum_seconds,
            is_current=True,
            supersedes_plan_id=plan.id,
            schema_version=plan.schema_version,
            sections=new_sections,
            metadata=updated_metadata,
        )
        return revised_plan, pacing_plan
