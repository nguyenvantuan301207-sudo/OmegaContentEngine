"""Canonical Guardian context construction and fail-closed validation for OMEGA-014."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.autonomy import CanonicalGuardianContext
from omega.domain.guardian import GuardianResolutionType
from omega.infrastructure.models import (
    GuardianCheck,
    GuardianException,
    GuardianFinding,
    GuardianResolutionEvent,
    GuardianRuleSet,
    GuardianStateTransition,
    Mission,
)


class GuardianConfigurationError(Exception):
    """Raised when Guardian safety rulesets are invalid or fail closed."""

    pass


async def build_canonical_guardian_context(
    session: AsyncSession,
    mission_id: UUID,
) -> tuple[CanonicalGuardianContext, str]:
    """Build canonical GuardianContext from authoritative safety sources.

    Fails closed if:
    - Zero active rulesets exist.
    - Multiple active rulesets exist.
    - Mission is not found.
    - No GuardianStateTransition exists (defaults to WAITING_GUARDIAN).
    """
    # 1. Load Mission for guardian_epoch
    stmt_m = select(Mission).where(Mission.id == mission_id)
    mission = (await session.execute(stmt_m)).scalar_one_or_none()
    if not mission:
        raise ValueError(f"Mission '{mission_id}' not found.")
    guardian_epoch = getattr(mission, "guardian_epoch", 1)

    # 2. Query Active Ruleset (Must be EXACTLY one active)
    stmt_rules = select(GuardianRuleSet).where(GuardianRuleSet.status == "ACTIVE")
    active_rulesets = (await session.execute(stmt_rules)).scalars().all()
    if len(active_rulesets) == 0:
        raise GuardianConfigurationError("Zero active Guardian rulesets found; fail closed.")
    if len(active_rulesets) > 1:
        raise GuardianConfigurationError(
            f"Multiple ({len(active_rulesets)}) active Guardian rulesets found; fail closed."
        )
    active_rule = active_rulesets[0]

    # 3. Overall Gate State (From latest GuardianStateTransition)
    stmt_trans = (
        select(GuardianStateTransition)
        .where(GuardianStateTransition.mission_id == mission_id)
        .order_by(desc(GuardianStateTransition.created_at))
        .limit(1)
    )
    latest_trans = (await session.execute(stmt_trans)).scalar_one_or_none()
    # FAIL CLOSED: If no transition ever occurred, gate state is WAITING_GUARDIAN
    overall_gate_state = latest_trans.to_gate_state if latest_trans else "WAITING_GUARDIAN"

    # 4. Active Exceptions
    # Evaluated against DB func.now()
    stmt_exc = (
        select(GuardianException)
        .where(
            GuardianException.mission_id == mission_id,
            GuardianException.expires_at > func.now(),
        )
        .order_by(GuardianException.id.asc())
    )
    exceptions = (await session.execute(stmt_exc)).scalars().all()
    active_exceptions_list = sorted(
        [
            f"{exc.id}:{exc.rule_id}:{exc.expires_at.isoformat() if exc.expires_at else 'NONE'}"
            for exc in exceptions
        ]
    )

    # 5. Unresolved Safety-Relevant Findings (severity IN ('HIGH', 'CRITICAL'))
    # Joined to GuardianCheck for mission scoping
    # Uses real GuardianResolutionEvent semantics: only terminal resolution types resolve a finding.
    final_resolution_types = [
        GuardianResolutionType.OVERRIDE_APPROVED.value,
        GuardianResolutionType.EXCEPTION_APPLIED.value,
        GuardianResolutionType.FALSE_POSITIVE_DISMISSED.value,
        GuardianResolutionType.MITIGATED.value,
        GuardianResolutionType.TERMINAL_ACCEPTED.value,
    ]
    # Subquery for the latest resolution event per finding
    stmt_latest_res = (
        select(
            GuardianResolutionEvent.finding_id,
            GuardianResolutionEvent.resolution_type,
        )
        .where(GuardianResolutionEvent.finding_id.is_not(None))
        .distinct(GuardianResolutionEvent.finding_id)
        .order_by(
            GuardianResolutionEvent.finding_id,
            desc(GuardianResolutionEvent.created_at),
            desc(GuardianResolutionEvent.id),
        )
        .subquery()
    )
    stmt_resolved_ids = select(stmt_latest_res.c.finding_id).where(
        stmt_latest_res.c.resolution_type.in_(final_resolution_types)
    )

    stmt_findings = (
        select(GuardianFinding)
        .join(GuardianCheck, GuardianFinding.guardian_check_id == GuardianCheck.id)
        .where(
            GuardianCheck.mission_id == mission_id,
            GuardianFinding.severity.in_(["HIGH", "CRITICAL"]),
            GuardianFinding.id.not_in(stmt_resolved_ids),
        )
        .order_by(GuardianFinding.id.asc())
    )
    findings = (await session.execute(stmt_findings)).scalars().all()
    unresolved_findings_list = sorted(
        [f"{f.id}:{f.guardian_check_id}:{f.rule_id}:{f.severity}" for f in findings]
    )

    # 6. Form Canonical Contract
    context = CanonicalGuardianContext(
        active_exception_identities=active_exceptions_list,
        active_ruleset_checksum=active_rule.checksum,
        active_ruleset_id=str(active_rule.id),
        active_ruleset_version=active_rule.version,
        guardian_epoch=guardian_epoch,
        guardian_policy_config_version=active_rule.version,
        overall_gate_state=overall_gate_state,
        unresolved_blocking_finding_identities=unresolved_findings_list,
    )
    checksum = context.compute_checksum()
    return context, checksum
