"""Guardian Decision Engine.

Pure domain logic computing deterministic GuardianAction and GuardianGateState
from findings, detector failure policies, applied exceptions, and checkpoint strictness.
"""

from __future__ import annotations

from uuid import UUID

from omega.domain.guardian import (
    DetectorFailurePolicy,
    GuardianAction,
    GuardianCheckpoint,
    GuardianFindingData,
    GuardianGateState,
    GuardianSeverity,
)

UNRECOVERABLE_RULE_IDS: set[str] = {
    "RENDER_HASH_MISMATCH",
    "ZERO_DURATION_ARTIFACT",
    "CORRUPTED_LINEAGE_INVARIANT",
}


class GuardianDecisionEngine:
    """Computes deterministic gate decisions and actions."""

    @classmethod
    def compute_decision(
        cls,
        checkpoint: GuardianCheckpoint,
        findings_with_exceptions: list[tuple[GuardianFindingData, UUID | None]],
        detector_failures: list[tuple[str, DetectorFailurePolicy, str]],
    ) -> tuple[GuardianAction, GuardianGateState, str]:
        """Compute (action, gate_state, reason) from aggregated context."""
        # 1. Check for detector execution failures under declared failure policy
        for det_type, policy, err_msg in detector_failures:
            if policy == DetectorFailurePolicy.FAIL_CLOSED:
                return (
                    GuardianAction.PAUSE,
                    GuardianGateState.BLOCKED,
                    f"Detector '{det_type}' failed under FAIL_CLOSED policy: {err_msg}",
                )
            if policy == DetectorFailurePolicy.REQUIRE_REVIEW:
                return (
                    GuardianAction.REQUIRE_REVIEW,
                    GuardianGateState.BLOCKED,
                    f"Detector '{det_type}' failed under REQUIRE_REVIEW policy: {err_msg}",
                )
            # FAIL_OPEN_WITH_WARNING continues to evaluate remaining findings

        # 2. Filter unhandled findings (findings without an applied exception)
        unhandled: list[GuardianFindingData] = []
        handled_count = 0
        for finding, exc_id in findings_with_exceptions:
            if exc_id is not None:
                handled_count += 1
            else:
                unhandled.append(finding)

        if not unhandled:
            if handled_count > 0:
                return (
                    GuardianAction.ALLOW_WITH_WARNING,
                    GuardianGateState.RESTRICTED,
                    f"All {handled_count} findings permitted under active authorized exceptions.",
                )
            return (
                GuardianAction.ALLOW,
                GuardianGateState.OPEN,
                f"Checkpoint {checkpoint.value} passed all evaluated guardian detectors.",
            )

        # 3. Check for unrecoverable technical invariants requiring FORCE_FAIL
        unrecoverable_findings = [f for f in unhandled if f.rule_id in UNRECOVERABLE_RULE_IDS]
        if unrecoverable_findings:
            rule_names = ", ".join(f.rule_id for f in unrecoverable_findings)
            return (
                GuardianAction.FORCE_FAIL,
                GuardianGateState.BLOCKED,
                f"Certainty of unrecoverable failure: {rule_names}. Terminal failure forced.",
            )

        # 4. Critical severity findings -> PAUSE + REQUIRE_REVIEW (never blind FORCE_FAIL)
        critical_findings = [f for f in unhandled if f.severity == GuardianSeverity.CRITICAL]
        if critical_findings:
            reasons = "; ".join(f"{f.rule_id}: {f.message}" for f in critical_findings[:3])
            return (
                GuardianAction.PAUSE,
                GuardianGateState.BLOCKED,
                f"Critical risk detected at {checkpoint.value}: {reasons}",
            )

        # 5. High severity findings -> REQUIRE_REVIEW (gate BLOCKED)
        high_findings = [f for f in unhandled if f.severity == GuardianSeverity.HIGH]
        if high_findings:
            reasons = "; ".join(f"{f.rule_id}: {f.message}" for f in high_findings[:3])
            return (
                GuardianAction.REQUIRE_REVIEW,
                GuardianGateState.BLOCKED,
                f"High risk detected at {checkpoint.value}: {reasons}",
            )

        # 6. Medium and Low severity findings
        # Stricter tolerance at external side-effect boundary
        if checkpoint == GuardianCheckpoint.PRE_EXTERNAL_SIDE_EFFECT:
            reasons = "; ".join(f"{f.rule_id}: {f.message}" for f in unhandled[:3])
            return (
                GuardianAction.REQUIRE_REVIEW,
                GuardianGateState.BLOCKED,
                f"Protected external boundary requires review for findings: {reasons}",
            )

        # Internal checkpoints allow warnings without bumping epoch
        reasons = "; ".join(f"{f.rule_id}: {f.message}" for f in unhandled[:3])
        return (
            GuardianAction.ALLOW_WITH_WARNING,
            GuardianGateState.RESTRICTED,
            f"Findings permitted with warning monitoring: {reasons}",
        )
