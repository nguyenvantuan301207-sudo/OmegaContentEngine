"""Policy Risk Detector.

Enforces explainable, rule-based policy checks and copyright licensing facts.
Failure policy is FAIL_CLOSED.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
from omega.infrastructure.models import ProductionAsset


class PolicyRiskDetector(BaseDetector):
    """Detects copyright provenance flaws and policy risks using metadata and license facts."""

    detector_type = "POLICY_RISK"
    detector_version = "1.0.0"
    supported_checkpoints = {
        GuardianCheckpoint.PRE_TASK_DISPATCH,
        GuardianCheckpoint.PRE_RENDER,
        GuardianCheckpoint.PRE_EXTERNAL_SIDE_EFFECT,
    }
    failure_policy = DetectorFailurePolicy.FAIL_CLOSED

    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        findings: list[GuardianFindingData] = []
        diag = context.diagnostic_context or {}

        # 1. Direct assets payload from context if provided
        assets_list: list[dict[str, Any]] = diag.get("assets", [])

        # 2. If not in diagnostic context and production_request_id is present, query DB
        if not assets_list and context.production_request_id:
            async with session_factory() as session:
                stmt = select(ProductionAsset).where(
                    ProductionAsset.production_request_id == context.production_request_id
                )
                res = await session.execute(stmt)
                db_assets = res.scalars().all()
                assets_list = [
                    {
                        "id": str(a.id),
                        "source_ref": a.source_ref,
                        "license_status": a.license_status,
                        "asset_requirement_id": str(a.asset_requirement_id)
                        if a.asset_requirement_id
                        else None,
                        "commercial_use_allowed": (a.metadata_ or {}).get(
                            "commercial_use_allowed", True
                        ),
                    }
                    for a in db_assets
                ]

        for asset in assets_list:
            asset_id = str(asset.get("id", "unknown"))
            license_status = str(asset.get("license_status", "")).upper()
            source_ref = asset.get("source_ref")
            comm_allowed = asset.get("commercial_use_allowed", True)

            # Fact 1: Missing provenance
            if not source_ref or not str(source_ref).strip():
                findings.append(
                    GuardianFindingData(
                        rule_id="MISSING_ASSET_PROVENANCE",
                        severity=GuardianSeverity.CRITICAL,
                        risk_type=GuardianRiskType.COPYRIGHT_LICENSE,
                        confidence=1.0,
                        evidence={"asset_id": asset_id, "source_ref": source_ref},
                        location_reference={"asset_id": asset_id},
                        message=f"Production asset '{asset_id}' lacks provenance citation or source reference.",
                    )
                )

            # Fact 2: Prohibited / Blocked license
            if license_status == "BLOCKED":
                findings.append(
                    GuardianFindingData(
                        rule_id="PROHIBITED_LICENSE_STATUS",
                        severity=GuardianSeverity.CRITICAL,
                        risk_type=GuardianRiskType.COPYRIGHT_LICENSE,
                        confidence=1.0,
                        evidence={"asset_id": asset_id, "license_status": license_status},
                        location_reference={"asset_id": asset_id},
                        message=f"Production asset '{asset_id}' has prohibited BLOCKED license status.",
                    )
                )

            # Fact 3: Unknown license for required asset
            if license_status in ("UNKNOWN", "") and asset.get("asset_requirement_id"):
                findings.append(
                    GuardianFindingData(
                        rule_id="UNRESOLVED_ASSET_LICENSE",
                        severity=GuardianSeverity.HIGH,
                        risk_type=GuardianRiskType.COPYRIGHT_LICENSE,
                        confidence=1.0,
                        evidence={"asset_id": asset_id, "license_status": license_status},
                        location_reference={"asset_id": asset_id},
                        message=f"Required asset '{asset_id}' has UNRESOLVED / UNKNOWN license rights.",
                    )
                )

            # Fact 4: Commercial-use incompatibility
            if comm_allowed is False:
                findings.append(
                    GuardianFindingData(
                        rule_id="COMMERCIAL_USE_INCOMPATIBLE",
                        severity=GuardianSeverity.HIGH,
                        risk_type=GuardianRiskType.COPYRIGHT_LICENSE,
                        confidence=0.95,
                        evidence={"asset_id": asset_id, "commercial_use_allowed": False},
                        location_reference={"asset_id": asset_id},
                        message=f"Asset '{asset_id}' prohibits commercial use distribution.",
                    )
                )

        return findings
