"""NarrativePlan deterministic validation engine.

Evaluates structural constraints, format profile compliance,
promise/payoff integrity, and research source grounding.
Emits structured findings with explicit rule codes and severities.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from omega.domain.narrative_plan import (
    FORMAT_PROFILE_CONSTRAINTS,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanFinding,
    NarrativePlanValidationResult,
    NarrativeRuleCode,
    NarrativeSectionRole,
    NarrativeSeverity,
    NarrativeValidationStatus,
)


class NarrativePlanValidator:
    """Deterministic validator for NarrativePlan domain entities."""

    def __init__(self, strict_grounding: bool = False):
        self.strict_grounding = strict_grounding

    def validate(
        self,
        plan: NarrativePlan,
        require_grounding: bool | None = None,
    ) -> NarrativePlanValidationResult:
        """Run all deterministic validation rules against the plan."""
        findings: list[NarrativePlanFinding] = []
        should_ground = self.strict_grounding if require_grounding is None else require_grounding

        # Rule 1: Empty Plan
        if not plan.sections:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.EMPTY_PLAN,
                    severity=NarrativeSeverity.BLOCKING,
                    message="NarrativePlan contains no sections.",
                )
            )
            return NarrativePlanValidationResult(
                plan_id=plan.id,
                status=NarrativeValidationStatus.BLOCKED,
                findings=findings,
                evaluated_at=datetime.now(UTC),
            )

        constraints = FORMAT_PROFILE_CONSTRAINTS.get(plan.format_profile)
        if not constraints:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.INVALID_ROLE_COMBINATION,
                    severity=NarrativeSeverity.BLOCKING,
                    message=f"Unknown format profile: {plan.format_profile}",
                )
            )
            return NarrativePlanValidationResult(
                plan_id=plan.id,
                status=NarrativeValidationStatus.BLOCKED,
                findings=findings,
                evaluated_at=datetime.now(UTC),
            )

        # Rule 2: Section Ordering & Uniqueness
        orders = [s.section_order for s in plan.sections]
        if len(orders) != len(set(orders)):
            seen_orders: set[int] = set()
            for s in plan.sections:
                if s.section_order in seen_orders:
                    findings.append(
                        NarrativePlanFinding(
                            rule_code=NarrativeRuleCode.DUPLICATE_SECTION_ORDER,
                            severity=NarrativeSeverity.BLOCKING,
                            message=f"Duplicate section_order {s.section_order} found.",
                            section_order=s.section_order,
                        )
                    )
                seen_orders.add(s.section_order)

        sorted_sections = sorted(plan.sections, key=lambda s: s.section_order)
        expected_orders = list(range(1, len(sorted_sections) + 1))
        actual_orders = [s.section_order for s in sorted_sections]
        if actual_orders != expected_orders:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.ORDERING_GAP,
                    severity=NarrativeSeverity.BLOCKING,
                    message=f"Section order sequence has gaps or does not start at 1: {actual_orders}",
                )
            )

        # Rule 3: Section Count Bounds
        section_count = len(sorted_sections)
        if section_count < constraints.min_sections:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.SECTION_COUNT_OUT_OF_BOUNDS,
                    severity=NarrativeSeverity.BLOCKING,
                    message=(
                        f"Section count {section_count} is below minimum {constraints.min_sections} "
                        f"for profile {plan.format_profile.value}."
                    ),
                    details={"actual": section_count, "min": constraints.min_sections},
                )
            )
        elif section_count > constraints.max_sections:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.SECTION_COUNT_OUT_OF_BOUNDS,
                    severity=NarrativeSeverity.WARNING,
                    message=(
                        f"Section count {section_count} exceeds recommended maximum {constraints.max_sections} "
                        f"for profile {plan.format_profile.value}."
                    ),
                    details={"actual": section_count, "max": constraints.max_sections},
                )
            )

        # Rule 4: Allowed and Required Roles
        present_roles = {s.role for s in sorted_sections}

        for s in sorted_sections:
            if s.role not in constraints.allowed_roles:
                findings.append(
                    NarrativePlanFinding(
                        rule_code=NarrativeRuleCode.INVALID_ROLE_COMBINATION,
                        severity=NarrativeSeverity.BLOCKING,
                        message=f"Role {s.role.value} is not permitted for format profile {plan.format_profile.value}.",
                        section_order=s.section_order,
                        details={"role": s.role.value, "profile": plan.format_profile.value},
                    )
                )

        missing_required = constraints.required_roles - present_roles
        if missing_required:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.MISSING_REQUIRED_ROLE,
                    severity=NarrativeSeverity.BLOCKING,
                    message=(
                        f"Missing required narrative roles for profile {plan.format_profile.value}: "
                        f"{', '.join(r.value for r in sorted(missing_required, key=lambda x: x.value))}"
                    ),
                    details={"missing_roles": [r.value for r in missing_required]},
                )
            )

        # Rule 5: Hook Placement and Timing
        first_section = sorted_sections[0]
        if first_section.role != NarrativeSectionRole.HOOK:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.HOOK_NOT_FIRST,
                    severity=NarrativeSeverity.BLOCKING,
                    message=f"First section must have role HOOK, but found {first_section.role.value}.",
                    section_order=first_section.section_order,
                )
            )
        else:
            if first_section.target_duration_seconds > constraints.max_hook_duration_seconds:
                findings.append(
                    NarrativePlanFinding(
                        rule_code=NarrativeRuleCode.HOOK_DURATION_EXCEEDED,
                        severity=NarrativeSeverity.WARNING,
                        message=(
                            f"Hook duration {first_section.target_duration_seconds}s exceeds "
                            f"recommended limit {constraints.max_hook_duration_seconds}s for profile {plan.format_profile.value}."
                        ),
                        section_order=first_section.section_order,
                        details={
                            "duration": first_section.target_duration_seconds,
                            "max": constraints.max_hook_duration_seconds,
                        },
                    )
                )

        # Rule 6: Promise -> Payoff Integrity
        declared_promises: dict[str, NarrativeSection] = {}
        referenced_payoffs: dict[str, list[NarrativeSection]] = {}

        for s in sorted_sections:
            if s.promise_id:
                pid = s.promise_id.strip()
                if pid in declared_promises:
                    findings.append(
                        NarrativePlanFinding(
                            rule_code=NarrativeRuleCode.DUPLICATE_PROMISE_ID,
                            severity=NarrativeSeverity.BLOCKING,
                            message=f"Duplicate promise_id '{pid}' declared in section {s.section_order}.",
                            section_order=s.section_order,
                            details={"promise_id": pid},
                        )
                    )
                else:
                    declared_promises[pid] = s

            if s.payoff_reference:
                pref = s.payoff_reference.strip()
                referenced_payoffs.setdefault(pref, []).append(s)

        # Verify each declared promise has a matching later payoff
        for pid, p_sec in declared_promises.items():
            payoff_sections = referenced_payoffs.get(pid, [])
            if not payoff_sections:
                findings.append(
                    NarrativePlanFinding(
                        rule_code=NarrativeRuleCode.UNRESOLVED_PROMISE,
                        severity=NarrativeSeverity.BLOCKING,
                        message=f"Promise '{pid}' in section {p_sec.section_order} has no corresponding payoff section.",
                        section_order=p_sec.section_order,
                        details={"promise_id": pid},
                    )
                )
            else:
                for pay_sec in payoff_sections:
                    if pay_sec.section_order <= p_sec.section_order:
                        findings.append(
                            NarrativePlanFinding(
                                rule_code=NarrativeRuleCode.PAYOFF_BEFORE_PROMISE,
                                severity=NarrativeSeverity.BLOCKING,
                                message=(
                                    f"Payoff section {pay_sec.section_order} for promise '{pid}' "
                                    f"appears before or at promise section {p_sec.section_order}."
                                ),
                                section_order=pay_sec.section_order,
                                details={"promise_id": pid, "promise_order": p_sec.section_order},
                            )
                        )

        # Verify each payoff references a valid earlier promise
        for pref, pay_secs in referenced_payoffs.items():
            if pref not in declared_promises:
                for pay_sec in pay_secs:
                    findings.append(
                        NarrativePlanFinding(
                            rule_code=NarrativeRuleCode.ORPHAN_PAYOFF,
                            severity=NarrativeSeverity.BLOCKING,
                            message=f"Payoff section {pay_sec.section_order} references unknown promise '{pref}'.",
                            section_order=pay_sec.section_order,
                            details={"payoff_reference": pref},
                        )
                    )
            elif len(pay_secs) > 1:
                for pay_sec in pay_secs[1:]:
                    findings.append(
                        NarrativePlanFinding(
                            rule_code=NarrativeRuleCode.DUPLICATE_PAYOFF_LINK,
                            severity=NarrativeSeverity.BLOCKING,
                            message=f"Duplicate payoff section {pay_sec.section_order} for promise '{pref}'.",
                            section_order=pay_sec.section_order,
                            details={"payoff_reference": pref},
                        )
                    )

        # Rule 7: Duration Bounds and Allocation
        total_section_dur = sum(s.target_duration_seconds for s in sorted_sections)
        if plan.target_duration_seconds < constraints.min_duration_seconds:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.DURATION_OUT_OF_BOUNDS,
                    severity=NarrativeSeverity.BLOCKING,
                    message=(
                        f"Target duration {plan.target_duration_seconds}s is below minimum "
                        f"{constraints.min_duration_seconds}s for profile {plan.format_profile.value}."
                    ),
                    details={"target": plan.target_duration_seconds, "min": constraints.min_duration_seconds},
                )
            )
        elif plan.target_duration_seconds > constraints.max_duration_seconds:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.DURATION_OUT_OF_BOUNDS,
                    severity=NarrativeSeverity.BLOCKING,
                    message=(
                        f"Target duration {plan.target_duration_seconds}s exceeds maximum "
                        f"{constraints.max_duration_seconds}s for profile {plan.format_profile.value}."
                    ),
                    details={"target": plan.target_duration_seconds, "max": constraints.max_duration_seconds},
                )
            )

        tolerance = constraints.duration_tolerance_pct
        dur_diff = abs(total_section_dur - plan.target_duration_seconds)
        max_allowed_diff = plan.target_duration_seconds * tolerance
        if dur_diff > max_allowed_diff:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.DURATION_SUM_MISMATCH,
                    severity=NarrativeSeverity.WARNING,
                    message=(
                        f"Sum of section durations ({total_section_dur}s) deviates from plan target "
                        f"({plan.target_duration_seconds}s) by {dur_diff}s (tolerance: ±{math.ceil(max_allowed_diff)}s)."
                    ),
                    details={
                        "sum_sections": total_section_dur,
                        "plan_target": plan.target_duration_seconds,
                        "tolerance_pct": tolerance,
                    },
                )
            )

        # Rule 8: CTA Rules
        cta_sections = [s for s in sorted_sections if s.role == NarrativeSectionRole.CTA]
        if len(cta_sections) > constraints.cta_max_count:
            findings.append(
                NarrativePlanFinding(
                    rule_code=NarrativeRuleCode.EXCESSIVE_CTA_COUNT,
                    severity=NarrativeSeverity.BLOCKING,
                    message=(
                        f"Plan contains {len(cta_sections)} CTA sections, maximum allowed is "
                        f"{constraints.cta_max_count} for profile {plan.format_profile.value}."
                    ),
                    details={"actual": len(cta_sections), "max": constraints.cta_max_count},
                )
            )

        for cta_sec in cta_sections:
            if cta_sec.section_order <= 2 and len(sorted_sections) > 3:
                findings.append(
                    NarrativePlanFinding(
                        rule_code=NarrativeRuleCode.INVALID_CTA_PLACEMENT,
                        severity=NarrativeSeverity.WARNING,
                        message=f"CTA appears unusually early in narrative at section {cta_sec.section_order}.",
                        section_order=cta_sec.section_order,
                    )
                )

        # Rule 9: Source Grounding (if required)
        if should_ground:
            factual_roles = {
                NarrativeSectionRole.DEVELOPMENT,
                NarrativeSectionRole.PAYOFF,
                NarrativeSectionRole.CONTEXT,
            }
            for s in sorted_sections:
                if s.role in factual_roles and not s.grounding_references:
                    findings.append(
                        NarrativePlanFinding(
                            rule_code=NarrativeRuleCode.MISSING_GROUNDING,
                            severity=NarrativeSeverity.BLOCKING,
                            message=f"Factual section {s.section_order} ({s.role.value}) requires at least one research grounding reference.",
                            section_order=s.section_order,
                            details={"role": s.role.value},
                        )
                    )

        # Compute Overall Status
        has_blocking = any(
            f.severity in (NarrativeSeverity.BLOCKING, NarrativeSeverity.ERROR)
            for f in findings
        )
        has_warning = any(f.severity == NarrativeSeverity.WARNING for f in findings)

        if has_blocking:
            status = NarrativeValidationStatus.BLOCKED
        elif has_warning:
            status = NarrativeValidationStatus.PASSED_WITH_WARNINGS
        else:
            status = NarrativeValidationStatus.PASSED

        return NarrativePlanValidationResult(
            plan_id=plan.id,
            status=status,
            findings=findings,
            evaluated_at=datetime.now(UTC),
        )
