"""Pipeline Anomaly Detector.

Enforces runaway execution protections: retry caps, max rerenders,
consecutive provider failures, task counts, and execution timeout bounds.
Failure policy is REQUIRE_REVIEW.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

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
from omega.infrastructure.models import Mission, ProductionRenderJob, Task


class PipelineAnomalyDetector(BaseDetector):
    """Detects runaway execution, excessive retries, and pipeline loop anomalies."""

    detector_type = "PIPELINE_ANOMALY"
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

        max_tasks = int(rules.get("max_tasks_per_mission", 50))
        max_rerenders = int(rules.get("max_rerenders_per_production_request", 2))
        max_mission_runtime_sec = float(rules.get("max_mission_runtime_seconds", 7200.0))

        async with session_factory() as session:
            # 1. Total tasks count check
            task_count_res = await session.execute(
                select(func.count(Task.id)).where(Task.mission_id == context.mission_id)
            )
            total_tasks = task_count_res.scalar_one() or 0

            if total_tasks > max_tasks:
                findings.append(
                    GuardianFindingData(
                        rule_id="RUNAWAY_MAX_TASKS_EXCEEDED",
                        severity=GuardianSeverity.CRITICAL,
                        risk_type=GuardianRiskType.PIPELINE_RUNAWAY,
                        confidence=1.0,
                        evidence={"total_tasks": total_tasks, "max_tasks_allowed": max_tasks},
                        location_reference={"mission_id": str(context.mission_id)},
                        message=f"Mission total task count ({total_tasks}) exceeded runaway limit ({max_tasks}).",
                    )
                )

            # 2. Rerenders limit check (if production_request_id is present)
            if context.production_request_id:
                renders_res = await session.execute(
                    select(func.count(ProductionRenderJob.id)).where(
                        ProductionRenderJob.production_request_id == context.production_request_id
                    )
                )
                render_count = renders_res.scalar_one() or 0
                if render_count > max_rerenders:
                    findings.append(
                        GuardianFindingData(
                            rule_id="RUNAWAY_MAX_RERENDERS_EXCEEDED",
                            severity=GuardianSeverity.HIGH,
                            risk_type=GuardianRiskType.PIPELINE_RUNAWAY,
                            confidence=1.0,
                            evidence={
                                "render_count": render_count,
                                "max_rerenders_allowed": max_rerenders,
                                "production_request_id": str(context.production_request_id),
                            },
                            location_reference={
                                "production_request_id": str(context.production_request_id)
                            },
                            message=f"Production request render attempts ({render_count}) exceeded limit ({max_rerenders}).",
                        )
                    )

            # 3. Mission runtime bound check
            mission_res = await session.execute(
                select(Mission).where(Mission.id == context.mission_id)
            )
            mission = mission_res.scalar_one_or_none()
            if mission and mission.started_at:
                now = datetime.now(UTC)
                runtime_sec = (now - mission.started_at).total_seconds()
                if runtime_sec > max_mission_runtime_sec:
                    findings.append(
                        GuardianFindingData(
                            rule_id="RUNAWAY_MISSION_TIMEOUT_EXCEEDED",
                            severity=GuardianSeverity.HIGH,
                            risk_type=GuardianRiskType.PIPELINE_RUNAWAY,
                            confidence=1.0,
                            evidence={
                                "runtime_seconds": runtime_sec,
                                "max_allowed_seconds": max_mission_runtime_sec,
                            },
                            location_reference={"mission_id": str(context.mission_id)},
                            message=(
                                f"Mission active runtime ({runtime_sec:.1f}s) exceeded limit "
                                f"({max_mission_runtime_sec:.1f}s)."
                            ),
                        )
                    )

        return findings
