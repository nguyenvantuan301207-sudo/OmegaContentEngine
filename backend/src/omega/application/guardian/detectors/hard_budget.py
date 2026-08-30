"""Hard Budget Detector.

Enforces strict dollar ceilings on mission execution costs.
Failure policy is FAIL_CLOSED.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.guardian.detectors.base import BaseDetector, GuardianEvaluationContext
from omega.domain.guardian import (
    DetectorFailurePolicy,
    GuardianCheckpoint,
    GuardianFindingData,
    GuardianRiskType,
    GuardianSeverity,
)
from omega.infrastructure.models import CostRecord


class HardBudgetDetector(BaseDetector):
    """Detects when mission costs breach strict budget ceilings."""

    detector_type = "HARD_BUDGET"
    detector_version = "1.0.0"
    supported_checkpoints = {GuardianCheckpoint.PRE_TASK_DISPATCH, GuardianCheckpoint.PRE_RENDER}
    failure_policy = DetectorFailurePolicy.FAIL_CLOSED

    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        findings: list[GuardianFindingData] = []
        rules = context.rules_config or {}
        max_budget = Decimal(str(rules.get("max_mission_budget_usd", "50.00")))
        estimated_action_cost = Decimal(
            str(context.diagnostic_context.get("estimated_cost_usd", "0.00"))
        )

        async with session_factory() as session:
            stmt = select(func.coalesce(func.sum(CostRecord.amount_usd), Decimal("0.00"))).where(
                CostRecord.mission_id == context.mission_id
            )
            res = await session.execute(stmt)
            accumulated_cost = res.scalar_one() or Decimal("0.00")

        projected_total = accumulated_cost + estimated_action_cost
        if projected_total > max_budget:
            findings.append(
                GuardianFindingData(
                    rule_id="HARD_BUDGET_CEILING_EXCEEDED",
                    severity=GuardianSeverity.CRITICAL,
                    risk_type=GuardianRiskType.HARD_BUDGET_OVERRUN,
                    confidence=1.0,
                    evidence={
                        "max_budget_usd": float(max_budget),
                        "accumulated_cost_usd": float(accumulated_cost),
                        "estimated_action_cost_usd": float(estimated_action_cost),
                        "projected_total_usd": float(projected_total),
                    },
                    location_reference={"mission_id": str(context.mission_id)},
                    message=(
                        f"Projected total cost (${projected_total:.2f}) exceeds hard budget ceiling "
                        f"(${max_budget:.2f}). Action blocked."
                    ),
                )
            )

        return findings
