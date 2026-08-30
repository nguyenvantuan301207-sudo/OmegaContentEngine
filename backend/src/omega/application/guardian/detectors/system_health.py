"""System Health Telemetry Detector.

Gathers advisory health metrics, worker backlog, and latency telemetry.
Failure policy is FAIL_OPEN_WITH_WARNING.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.guardian.detectors.base import BaseDetector, GuardianEvaluationContext
from omega.domain.guardian import (
    DetectorFailurePolicy,
    GuardianCheckpoint,
    GuardianFindingData,
    GuardianRiskType,
    GuardianSeverity,
)


class SystemHealthTelemetryDetector(BaseDetector):
    """Detects advisory latency spikes and database responsiveness degradation."""

    detector_type = "SYSTEM_HEALTH_TELEMETRY"
    detector_version = "1.0.0"
    supported_checkpoints = {
        GuardianCheckpoint.PRE_TASK_DISPATCH,
        GuardianCheckpoint.PRE_RENDER,
        GuardianCheckpoint.POST_RENDER,
        GuardianCheckpoint.PRE_EXTERNAL_SIDE_EFFECT,
        GuardianCheckpoint.MISSION_TERMINAL,
    }
    failure_policy = DetectorFailurePolicy.FAIL_OPEN_WITH_WARNING

    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        findings: list[GuardianFindingData] = []
        rules = context.rules_config or {}
        max_latency_ms = float(rules.get("max_db_ping_latency_ms", 1000.0))

        start = time.perf_counter()
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            latency_ms = (time.perf_counter() - start) * 1000.0

            if latency_ms > max_latency_ms:
                findings.append(
                    GuardianFindingData(
                        rule_id="DB_PING_LATENCY_ELEVATED",
                        severity=GuardianSeverity.LOW,
                        risk_type=GuardianRiskType.SYSTEM_TELEMETRY,
                        confidence=0.75,
                        evidence={
                            "latency_ms": latency_ms,
                            "threshold_ms": max_latency_ms,
                        },
                        location_reference={"service": "postgres"},
                        message=f"Database query latency is elevated: {latency_ms:.1f}ms > {max_latency_ms:.1f}ms.",
                    )
                )
        except Exception as exc:
            findings.append(
                GuardianFindingData(
                    rule_id="DB_PING_FAILED_TELEMETRY",
                    severity=GuardianSeverity.MEDIUM,
                    risk_type=GuardianRiskType.SYSTEM_TELEMETRY,
                    confidence=0.85,
                    evidence={"error": str(exc)},
                    location_reference={"service": "postgres"},
                    message=f"Database ping check threw an exception: {exc}",
                )
            )

        return findings
