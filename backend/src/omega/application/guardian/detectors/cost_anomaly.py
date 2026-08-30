"""Cost Anomaly Detector.

Calculates rolling median metrics for task and mission costs.
Avoids bootstrap false positives by requiring sample size >= 10.
Failure policy is REQUIRE_REVIEW.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from decimal import Decimal

from sqlalchemy import select
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


class CostAnomalyDetector(BaseDetector):
    """Detects statistically significant cost anomalies using rolling median."""

    detector_type = "COST_ANOMALY"
    detector_version = "1.0.0"
    supported_checkpoints = {
        GuardianCheckpoint.PRE_TASK_DISPATCH,
        GuardianCheckpoint.PRE_RENDER,
        GuardianCheckpoint.MISSION_TERMINAL,
    }
    failure_policy = DetectorFailurePolicy.REQUIRE_REVIEW

    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        findings: list[GuardianFindingData] = []
        rules = context.rules_config or {}
        min_samples = int(rules.get("anomaly_min_sample_size", 10))
        multiplier_threshold = float(rules.get("anomaly_threshold_multiplier", 2.0))

        estimated_action_cost = Decimal(
            str(context.diagnostic_context.get("estimated_cost_usd", "0.00"))
        )
        if estimated_action_cost <= Decimal("0.00"):
            return findings

        cost_type = context.diagnostic_context.get("cost_type")

        async with session_factory() as session:
            stmt = select(CostRecord.amount_usd).order_by(CostRecord.recorded_at.desc()).limit(100)
            if cost_type:
                stmt = stmt.where(CostRecord.cost_type == str(cost_type))

            res = await session.execute(stmt)
            amounts = [float(a) for a in res.scalars().all()]

        # Avoid bootstrap false positives: requires sample size >= min_samples
        if len(amounts) < min_samples:
            return findings

        median_val = statistics.median(amounts)
        if median_val <= 0.0:
            return findings

        ratio = float(estimated_action_cost) / median_val
        if ratio > multiplier_threshold:
            findings.append(
                GuardianFindingData(
                    rule_id="COST_ANOMALY_SPIKE",
                    severity=GuardianSeverity.HIGH,
                    risk_type=GuardianRiskType.COST_ANOMALY,
                    confidence=0.90,
                    evidence={
                        "estimated_cost_usd": float(estimated_action_cost),
                        "historical_median_usd": median_val,
                        "ratio": ratio,
                        "threshold_multiplier": multiplier_threshold,
                        "sample_size": len(amounts),
                    },
                    location_reference={"mission_id": str(context.mission_id)},
                    message=(
                        f"Estimated cost (${float(estimated_action_cost):.2f}) is {ratio:.1f}x higher "
                        f"than historical rolling median (${median_val:.2f}, n={len(amounts)}). "
                        f"Requires operational review."
                    ),
                )
            )

        return findings
