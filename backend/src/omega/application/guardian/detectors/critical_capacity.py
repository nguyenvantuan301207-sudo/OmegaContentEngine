"""Critical Capacity Detector.

Verifies critical system disk storage space and primary filesystem availability.
Failure policy is FAIL_CLOSED.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.guardian.detectors.base import BaseDetector, GuardianEvaluationContext
from omega.domain.guardian import (
    DetectorFailurePolicy,
    GuardianCheckpoint,
    GuardianFindingData,
    GuardianRiskType,
    GuardianSeverity,
)


class CriticalCapacityDetector(BaseDetector):
    """Detects when storage capacity falls below critical operational thresholds."""

    detector_type = "CRITICAL_CAPACITY"
    detector_version = "1.0.0"
    supported_checkpoints = {
        GuardianCheckpoint.PRE_TASK_DISPATCH,
        GuardianCheckpoint.PRE_RENDER,
        GuardianCheckpoint.POST_RENDER,
        GuardianCheckpoint.PRE_EXTERNAL_SIDE_EFFECT,
        GuardianCheckpoint.MISSION_TERMINAL,
    }
    failure_policy = DetectorFailurePolicy.FAIL_CLOSED

    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        findings: list[GuardianFindingData] = []
        rules = context.rules_config or {}
        # Default minimum 200 MB free space required
        min_free_bytes = int(rules.get("min_free_disk_bytes", 200 * 1024 * 1024))

        target_dir = Path("/tmp")
        if not target_dir.exists():
            target_dir = Path(".")

        total, used, free = shutil.disk_usage(target_dir)

        if free < min_free_bytes:
            findings.append(
                GuardianFindingData(
                    rule_id="CRITICAL_DISK_STORAGE_EXHAUSTED",
                    severity=GuardianSeverity.CRITICAL,
                    risk_type=GuardianRiskType.SYSTEM_CAPACITY,
                    confidence=1.0,
                    evidence={
                        "free_bytes": free,
                        "min_required_bytes": min_free_bytes,
                        "total_bytes": total,
                        "used_bytes": used,
                        "path": str(target_dir),
                    },
                    location_reference={"storage_path": str(target_dir)},
                    message=(
                        f"Critical disk storage threshold breached: only {free / (1024 * 1024):.1f}MB free "
                        f"(minimum required: {min_free_bytes / (1024 * 1024):.1f}MB). All pipeline mutations blocked."
                    ),
                )
            )

        return findings
