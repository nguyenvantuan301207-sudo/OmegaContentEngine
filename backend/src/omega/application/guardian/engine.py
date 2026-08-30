"""Guardian Core Engine.

Orchestrates the decoupled four-phase check evaluation lifecycle:
Phase 1: Short claim transaction (locks Mission first, sets up check + detector runs, commits).
Phase 2: Detectors evaluate outside open DB transactions.
Phase 3: Short transaction per detector persisting detector run terminal state + findings.
Phase 4: Final short transaction locking Mission first, evaluating exceptions, computing pure
         decision, stamping single decision, state transition, alert outbox, and selective epoch update.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.guardian.decision_engine import GuardianDecisionEngine
from omega.application.guardian.detectors import ALL_DETECTORS, BaseDetector
from omega.application.guardian.detectors.base import GuardianEvaluationContext
from omega.application.guardian.exceptions import GuardianExceptionManager
from omega.application.guardian.outbox import AlertOutboxService
from omega.domain.guardian import (
    AlertChannel,
    CheckTriggerType,
    DetectorFailurePolicy,
    DetectorRunStatus,
    GuardianAction,
    GuardianCheckCreate,
    GuardianCheckpoint,
    GuardianCheckResponse,
    GuardianDecisionResponse,
    GuardianDetectorRunResponse,
    GuardianFindingData,
    GuardianFindingResponse,
    GuardianGateState,
    GuardianTargetType,
    RuleSetStatus,
    compute_canonical_check_key,
    compute_rules_config_checksum,
)
from omega.domain.mission import MissionState
from omega.infrastructure.models import (
    GuardianCheck,
    GuardianDecision,
    GuardianDecisionFinding,
    GuardianDetectorRun,
    GuardianFinding,
    GuardianRuleSet,
    GuardianStateTransition,
    Mission,
    Task,
)
from omega.logging import get_logger

logger = get_logger(service="omega-guardian-engine")

DEFAULT_RULESET_VERSION = "1.0.0"
DEFAULT_RULES_CONFIG: dict[str, Any] = {
    "max_mission_budget_usd": 50.0,
    "anomaly_min_sample_size": 10,
    "anomaly_threshold_multiplier": 2.0,
    "min_free_disk_bytes": 200 * 1024 * 1024,
    "max_tasks_per_mission": 50,
    "max_rerenders_per_production_request": 2,
    "max_mission_runtime_seconds": 7200.0,
}


class GuardianEngine:
    """Centralized safety, policy, cost, and pipeline-risk control plane."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        detectors: list[BaseDetector] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.detectors = detectors or ALL_DETECTORS

    async def get_or_create_default_ruleset(self, session: AsyncSession) -> GuardianRuleSet:
        """Retrieve active ruleset or lazily initialize default v1.0.0."""
        stmt = (
            select(GuardianRuleSet)
            .where(GuardianRuleSet.status == RuleSetStatus.ACTIVE.value)
            .order_by(GuardianRuleSet.created_at.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        ruleset = res.scalar_one_or_none()
        if not ruleset:
            checksum = compute_rules_config_checksum(DEFAULT_RULES_CONFIG)
            ruleset = GuardianRuleSet(
                id=uuid4(),
                version=DEFAULT_RULESET_VERSION,
                status=RuleSetStatus.ACTIVE.value,
                effective_at=datetime.now(UTC),
                checksum=checksum,
                rules_config=DEFAULT_RULES_CONFIG,
            )
            session.add(ruleset)
            await session.flush()
        return ruleset

    async def execute_check(self, payload: GuardianCheckCreate) -> GuardianCheckResponse:
        """Execute the decoupled four-phase evaluation lifecycle."""
        # ══════════════════════════════════════════════════════════════════
        # PHASE 1: SHORT CLAIM TRANSACTION
        # ══════════════════════════════════════════════════════════════════
        async with self.session_factory() as session:
            # 1. Lock Mission FIRST
            mission_res = await session.execute(
                select(Mission).where(Mission.id == payload.mission_id).with_for_update()
            )
            mission = mission_res.scalar_one_or_none()
            if not mission:
                raise ValueError(f"Mission '{payload.mission_id}' not found for Guardian check.")

            current_epoch = mission.guardian_epoch
            ruleset = await self.get_or_create_default_ruleset(session)

            # Determine canonical target type and ID
            target_type = GuardianTargetType.MISSION
            target_id = mission.id
            if payload.media_artifact_id:
                target_type = GuardianTargetType.MEDIA_ARTIFACT
                target_id = payload.media_artifact_id
            elif payload.production_request_id:
                target_type = GuardianTargetType.PRODUCTION_REQUEST
                target_id = payload.production_request_id
            elif payload.task_id:
                target_type = GuardianTargetType.TASK
                target_id = payload.task_id

            idempotency_key = compute_canonical_check_key(
                target_type=target_type,
                target_id=target_id,
                checkpoint=payload.checkpoint,
                trigger_type=payload.trigger_type,
                guardian_epoch=current_epoch,
                ruleset_version=ruleset.version,
                caller_key=payload.caller_key,
            )

            # Check if a completed check already exists
            existing_check_stmt = (
                select(GuardianCheck)
                .where(
                    GuardianCheck.mission_id == payload.mission_id,
                    GuardianCheck.checkpoint == payload.checkpoint.value,
                    GuardianCheck.idempotency_key == idempotency_key,
                )
                .options(
                    selectinload(GuardianCheck.decision),
                    selectinload(GuardianCheck.findings),
                    selectinload(GuardianCheck.detector_runs),
                )
            )
            existing_res = await session.execute(existing_check_stmt)
            existing_check = existing_res.scalar_one_or_none()
            if existing_check and existing_check.status == "COMPLETED":
                return self._build_check_response(existing_check)

            # Filter detectors applicable for this checkpoint
            applicable_detectors = [
                d for d in self.detectors if payload.checkpoint in d.supported_checkpoints
            ]

            check_id = existing_check.id if existing_check else uuid4()
            now = datetime.now(UTC)

            if not existing_check:
                new_check = GuardianCheck(
                    id=check_id,
                    mission_id=payload.mission_id,
                    task_id=payload.task_id,
                    production_request_id=payload.production_request_id,
                    media_artifact_id=payload.media_artifact_id,
                    trigger_type=payload.trigger_type.value,
                    checkpoint=payload.checkpoint.value,
                    ruleset_id=ruleset.id,
                    ruleset_version=ruleset.version,
                    ruleset_checksum=ruleset.checksum,
                    status="RUNNING",
                    idempotency_key=idempotency_key,
                    guardian_epoch=current_epoch,
                    diagnostic_context=payload.diagnostic_context,
                    started_at=now,
                    created_at=now,
                )
                session.add(new_check)
                await session.flush()

            # Insert or retrieve GuardianDetectorRun rows
            detector_runs_map: dict[str, GuardianDetectorRun] = {}
            for det in applicable_detectors:
                run_stmt = select(GuardianDetectorRun).where(
                    GuardianDetectorRun.guardian_check_id == check_id,
                    GuardianDetectorRun.detector_type == det.detector_type,
                    GuardianDetectorRun.detector_version == det.detector_version,
                )
                run_res = await session.execute(run_stmt)
                det_run = run_res.scalar_one_or_none()
                if not det_run:
                    det_run = GuardianDetectorRun(
                        id=uuid4(),
                        guardian_check_id=check_id,
                        detector_type=det.detector_type,
                        detector_version=det.detector_version,
                        status=DetectorRunStatus.PENDING.value,
                        failure_policy=det.failure_policy.value,
                        attempt=1,
                        max_attempts=3,
                        idempotency_key=f"{idempotency_key}:{det.detector_type}:{det.detector_version}",
                        started_at=now,
                        created_at=now,
                    )
                    session.add(det_run)
                detector_runs_map[det.detector_type] = det_run

            await session.commit()

        # ══════════════════════════════════════════════════════════════════
        # PHASE 2: EVALUATE DETECTORS OUTSIDE OPEN DB TRANSACTION
        # ══════════════════════════════════════════════════════════════════
        context = GuardianEvaluationContext(
            mission_id=payload.mission_id,
            checkpoint=payload.checkpoint,
            trigger_type=payload.trigger_type,
            task_id=payload.task_id,
            production_request_id=payload.production_request_id,
            media_artifact_id=payload.media_artifact_id,
            guardian_epoch=current_epoch,
            rules_config=ruleset.rules_config,
            diagnostic_context=payload.diagnostic_context,
        )

        detector_results: list[
            tuple[BaseDetector, list[GuardianFindingData], Exception | None]
        ] = []
        for det in applicable_detectors:
            try:
                findings_data = await det.evaluate(context, self.session_factory)
                detector_results.append((det, findings_data, None))
            except Exception as exc:
                logger.error(
                    "Guardian detector execution failed",
                    detector=det.detector_type,
                    check_id=str(check_id),
                    error=str(exc),
                    exc_info=True,
                )
                detector_results.append((det, [], exc))

        # ══════════════════════════════════════════════════════════════════
        # PHASE 3: SHORT PERSIST TRANSACTION PER DETECTOR
        # ══════════════════════════════════════════════════════════════════
        persisted_findings: list[tuple[GuardianFindingData, UUID]] = []
        detector_failures: list[tuple[str, DetectorFailurePolicy, str]] = []

        for det, findings_data, exc in detector_results:
            async with self.session_factory() as session:
                run_stmt = (
                    select(GuardianDetectorRun)
                    .where(
                        GuardianDetectorRun.guardian_check_id == check_id,
                        GuardianDetectorRun.detector_type == det.detector_type,
                        GuardianDetectorRun.detector_version == det.detector_version,
                    )
                    .with_for_update()
                )
                run_res = await session.execute(run_stmt)
                det_run = run_res.scalar_one()

                now = datetime.now(UTC)
                det_run.completed_at = now

                if exc is not None:
                    det_run.status = DetectorRunStatus.FAILED.value
                    det_run.error_data = {"error": str(exc), "type": type(exc).__name__}
                    detector_failures.append((det.detector_type, det.failure_policy, str(exc)))
                else:
                    det_run.status = DetectorRunStatus.COMPLETED.value
                    for f in findings_data:
                        finding_row = GuardianFinding(
                            id=uuid4(),
                            guardian_check_id=check_id,
                            detector_run_id=det_run.id,
                            detector_type=det.detector_type,
                            detector_version=det.detector_version,
                            rule_id=f.rule_id,
                            severity=f.severity.value,
                            risk_type=f.risk_type.value,
                            confidence=f.confidence,
                            evidence=f.evidence,
                            location_reference=f.location_reference,
                            message=f.message,
                            created_at=now,
                        )
                        session.add(finding_row)
                        await session.flush()
                        persisted_findings.append((f, finding_row.id))

                await session.commit()

        # ══════════════════════════════════════════════════════════════════
        # PHASE 4: FINAL SHORT DECISION TRANSACTION
        # ══════════════════════════════════════════════════════════════════
        async with self.session_factory() as session:
            # 1. Lock Mission FIRST
            mission_res = await session.execute(
                select(Mission).where(Mission.id == payload.mission_id).with_for_update()
            )
            mission = mission_res.scalar_one()

            # 2. Lock Task SECOND if task_id is present
            if payload.task_id:
                await session.execute(
                    select(Task).where(Task.id == payload.task_id).with_for_update()
                )

            # Match active exceptions for each finding
            findings_with_exceptions: list[tuple[GuardianFindingData, UUID | None]] = []
            finding_exception_pairs: list[tuple[UUID, UUID | None]] = []

            for f_data, f_id in persisted_findings:
                matched_exc_id = await GuardianExceptionManager.find_matching_exception(
                    session=session,
                    finding=f_data,
                    mission_id=mission.id,
                    channel_id=mission.channel_id,
                )
                findings_with_exceptions.append((f_data, matched_exc_id))
                finding_exception_pairs.append((f_id, matched_exc_id))

            # Compute pure decision
            action, gate_state, reason = GuardianDecisionEngine.compute_decision(
                checkpoint=payload.checkpoint,
                findings_with_exceptions=findings_with_exceptions,
                detector_failures=detector_failures,
            )

            # Insert exactly ONE GuardianDecision
            decision_id = uuid4()
            now = datetime.now(UTC)
            decision = GuardianDecision(
                id=decision_id,
                guardian_check_id=check_id,
                action=action.value,
                reason=reason,
                resulting_gate_state=gate_state.value,
                actor="GUARDIAN_ENGINE",
                created_at=now,
            )
            session.add(decision)
            await session.flush()

            # Link decision to findings and applied exceptions
            for f_id, exc_id in finding_exception_pairs:
                session.add(
                    GuardianDecisionFinding(
                        decision_id=decision_id,
                        finding_id=f_id,
                        applied_exception_id=exc_id,
                    )
                )

            # Update GuardianCheck to COMPLETED
            check_res = await session.execute(
                select(GuardianCheck).where(GuardianCheck.id == check_id).with_for_update()
            )
            completed_check = check_res.scalar_one()
            completed_check.status = "COMPLETED"
            completed_check.completed_at = now

            # Apply selective epoch and mission state transition
            if action == GuardianAction.FORCE_FAIL:
                mission.guardian_epoch += 1
                mission.state = MissionState.FAILED.value
                mission.completed_at = now
                mission.updated_at = now
            elif action == GuardianAction.PAUSE or gate_state == GuardianGateState.BLOCKED:
                mission.guardian_epoch += 1
                mission.state = MissionState.PAUSED.value
                mission.paused_at = now
                mission.updated_at = now
            # Harmless transitions (e.g. OPEN -> RESTRICTED) do NOT increment guardian_epoch

            # Audit state transition
            session.add(
                GuardianStateTransition(
                    id=uuid4(),
                    mission_id=mission.id,
                    checkpoint=payload.checkpoint.value,
                    from_gate_state="WAITING_GUARDIAN",
                    to_gate_state=gate_state.value,
                    decision_id=decision_id,
                    guardian_epoch=mission.guardian_epoch,
                    reason=reason,
                    created_at=now,
                )
            )

            # Stage alert outbox if non-ALLOW
            if action != GuardianAction.ALLOW:
                AlertOutboxService.stage_alert(
                    session=session,
                    guardian_check_id=check_id,
                    decision_id=decision_id,
                    channel=AlertChannel.IN_APP,
                    destination=f"mission:{mission.id}",
                    payload={
                        "checkpoint": payload.checkpoint.value,
                        "action": action.value,
                        "gate_state": gate_state.value,
                        "reason": reason,
                    },
                )

            await session.commit()

        # Reload complete record for response
        async with self.session_factory() as session:
            final_res = await session.execute(
                select(GuardianCheck)
                .where(GuardianCheck.id == check_id)
                .options(
                    selectinload(GuardianCheck.decision),
                    selectinload(GuardianCheck.findings),
                    selectinload(GuardianCheck.detector_runs),
                )
            )
            return self._build_check_response(final_res.scalar_one())

    def _build_check_response(self, check: GuardianCheck) -> GuardianCheckResponse:
        """Convert ORM GuardianCheck to Pydantic response."""
        dec_resp = None
        if check.decision:
            dec_resp = GuardianDecisionResponse(
                id=check.decision.id,
                guardian_check_id=check.decision.guardian_check_id,
                action=GuardianAction(check.decision.action),
                reason=check.decision.reason,
                resulting_gate_state=GuardianGateState(check.decision.resulting_gate_state),
                actor=check.decision.actor,
                created_at=check.decision.created_at,
            )

        findings_resp = [
            GuardianFindingResponse(
                id=f.id,
                guardian_check_id=f.guardian_check_id,
                detector_run_id=f.detector_run_id,
                detector_type=f.detector_type,
                detector_version=f.detector_version,
                rule_id=f.rule_id,
                severity=f.severity,
                risk_type=f.risk_type,
                confidence=f.confidence,
                evidence=f.evidence,
                location_reference=f.location_reference,
                message=f.message,
                created_at=f.created_at,
            )
            for f in check.findings
        ]

        runs_resp = [
            GuardianDetectorRunResponse(
                id=r.id,
                guardian_check_id=r.guardian_check_id,
                detector_type=r.detector_type,
                detector_version=r.detector_version,
                status=r.status,
                failure_policy=r.failure_policy,
                attempt=r.attempt,
                max_attempts=r.max_attempts,
                idempotency_key=r.idempotency_key,
                error_data=r.error_data,
                started_at=r.started_at,
                completed_at=r.completed_at,
                created_at=r.created_at,
            )
            for r in check.detector_runs
        ]

        return GuardianCheckResponse(
            id=check.id,
            mission_id=check.mission_id,
            task_id=check.task_id,
            production_request_id=check.production_request_id,
            media_artifact_id=check.media_artifact_id,
            trigger_type=CheckTriggerType(check.trigger_type),
            checkpoint=GuardianCheckpoint(check.checkpoint),
            ruleset_id=check.ruleset_id,
            ruleset_version=check.ruleset_version,
            ruleset_checksum=check.ruleset_checksum,
            status=check.status,
            idempotency_key=check.idempotency_key,
            guardian_epoch=check.guardian_epoch,
            diagnostic_context=check.diagnostic_context,
            started_at=check.started_at,
            completed_at=check.completed_at,
            created_at=check.created_at,
            decision=dec_resp,
            findings=findings_resp,
            detector_runs=runs_resp,
        )
