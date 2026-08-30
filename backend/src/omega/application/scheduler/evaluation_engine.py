"""Authoritative Schedule Evaluation Engine for OMEGA-010.

Evaluates work eligibility, channel preferences, DST policies, capacity constraints,
and priority scoring to produce deterministic ScheduleDecision responses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.scheduler.policy_service import SchedulePolicyService
from omega.application.scheduler.priority_engine import PriorityEngine
from omega.application.scheduler.slot_allocator import SlotAllocator
from omega.domain.mission import MissionState
from omega.domain.scheduler import (
    ReservationState,
    ScheduleAction,
    ScheduleDecisionResponse,
    SchedulePolicyConfig,
    ScheduleRequest,
    compute_channel_windows_utc,
    compute_policy_checksum,
    compute_schedule_idempotency_key,
)
from omega.infrastructure.models import (
    ChannelDNARevision,
    Mission,
    ScheduleDecision,
    ScheduleReservation,
)
from omega.logging import get_logger

logger = get_logger(service="omega-scheduler-evaluation")


class ScheduleEvaluationEngine:
    """Evaluates scheduling eligibility, constraints, and slot allocations."""

    @staticmethod
    async def evaluate_schedule(
        session: AsyncSession,
        request: ScheduleRequest,
        *,
        now: datetime | None = None,
    ) -> ScheduleDecisionResponse:
        """Evaluate a schedule request and produce an authoritative decision."""
        if now is None:
            now = datetime.now(UTC)

        # 1. Load Mission
        mission_res = await session.execute(select(Mission).where(Mission.id == request.mission_id))
        mission = mission_res.scalar_one_or_none()
        if not mission:
            raise ValueError(f"Mission {request.mission_id} not found")

        # 2. Check Mission state gates
        if mission.state == MissionState.PAUSED.value:
            return ScheduleDecisionResponse(
                id=UUID("00000000-0000-0000-0000-000000000000"),
                idempotency_key=f"paused:{str(request.mission_id)}",
                action=ScheduleAction.WAITING_GUARDIAN,
                reason="Mission is in PAUSED state",
                policy_id=UUID("00000000-0000-0000-0000-000000000000"),
                policy_version="0.0.0",
                policy_checksum="",
                guardian_epoch=mission.guardian_epoch,
                evaluated_at=now,
            )

        if mission.state in (MissionState.FAILED.value, MissionState.CANCELLED.value):
            return ScheduleDecisionResponse(
                id=UUID("00000000-0000-0000-0000-000000000000"),
                idempotency_key=f"terminal:{str(request.mission_id)}",
                action=ScheduleAction.BLOCKED_SCHEDULE,
                reason=f"Mission is in terminal state ({mission.state})",
                policy_id=UUID("00000000-0000-0000-0000-000000000000"),
                policy_version="0.0.0",
                policy_checksum="",
                guardian_epoch=mission.guardian_epoch,
                evaluated_at=now,
            )

        # 3. Resolve active SchedulePolicy
        policy = await SchedulePolicyService.get_active_policy(
            session, request.workload_category.value
        )
        if not policy:
            logger.warning(
                "no_active_schedule_policy",
                workload_category=request.workload_category.value,
            )
            return ScheduleDecisionResponse(
                id=UUID("00000000-0000-0000-0000-000000000000"),
                idempotency_key=f"no-policy:{request.workload_category.value}",
                action=ScheduleAction.BLOCKED_SCHEDULE,
                reason=f"No active schedule policy found for category {request.workload_category.value}",
                policy_id=UUID("00000000-0000-0000-0000-000000000000"),
                policy_version="0.0.0",
                policy_checksum="",
                guardian_epoch=mission.guardian_epoch,
                evaluated_at=now,
            )

        policy_config = SchedulePolicyConfig(**policy.policy_config)
        policy_checksum = compute_policy_checksum(policy_config)

        # 4. Resolve Channel DNA revision (if channel-scoped)
        channel_dna_revision_id = request.channel_dna_revision_id
        target_timezone = "UTC"
        preferred_days = [
            "MONDAY",
            "TUESDAY",
            "WEDNESDAY",
            "THURSDAY",
            "FRIDAY",
            "SATURDAY",
            "SUNDAY",
        ]
        preferred_time_windows = ["00:00-23:59"]

        if request.channel_id:
            dna_res = await session.execute(
                select(ChannelDNARevision)
                .where(ChannelDNARevision.channel_id == request.channel_id)
                .order_by(ChannelDNARevision.version.desc())
                .limit(1)
            )
            latest_dna = dna_res.scalar_one_or_none()
            if latest_dna:
                channel_dna_revision_id = latest_dna.id
                snapshot = latest_dna.snapshot or {}
                prefs = snapshot.get("publishing_preferences", {})
                target_timezone = prefs.get("target_timezone", "UTC")
                if prefs.get("preferred_days"):
                    preferred_days = prefs["preferred_days"]
                if prefs.get("preferred_time_windows"):
                    preferred_time_windows = prefs["preferred_time_windows"]

        # 5. Check deadline feasibility
        if request.deadline_at and request.deadline_at <= now:
            return ScheduleDecisionResponse(
                id=UUID("00000000-0000-0000-0000-000000000000"),
                idempotency_key=f"deadline-expired:{str(request.target_id)}",
                action=ScheduleAction.BLOCKED_SCHEDULE,
                reason=f"Deadline expired at {request.deadline_at.isoformat()}",
                policy_id=policy.id,
                policy_version=policy.version,
                policy_checksum=policy_checksum,
                guardian_epoch=mission.guardian_epoch,
                evaluated_at=now,
            )

        if (
            request.earliest_start_at
            and request.latest_start_at
            and request.earliest_start_at > request.latest_start_at
        ):
            return ScheduleDecisionResponse(
                id=UUID("00000000-0000-0000-0000-000000000000"),
                idempotency_key=f"deadline-unfeasible:{str(request.target_id)}",
                action=ScheduleAction.BLOCKED_SCHEDULE,
                reason="Earliest start time cannot be after latest start time",
                policy_id=policy.id,
                policy_version=policy.version,
                policy_checksum=policy_checksum,
                guardian_epoch=mission.guardian_epoch,
                evaluated_at=now,
            )

        # 6. Check existing idempotent decision
        base_idempotency_key = compute_schedule_idempotency_key(
            target_type=request.target_type.value,
            target_id=request.target_id,
            mission_id=request.mission_id,
            task_id=request.task_id,
            workload_category=request.workload_category.value,
            channel_id=request.channel_id,
            policy_id=policy.id,
            policy_version=policy.version,
            policy_checksum=policy_checksum,
            channel_dna_revision_id=channel_dna_revision_id,
            guardian_epoch=mission.guardian_epoch,
            earliest_start_at=request.earliest_start_at,
            latest_start_at=request.latest_start_at,
            deadline_at=request.deadline_at,
            estimated_duration_seconds=request.estimated_duration_seconds,
            priority=request.priority,
            caller_key=request.caller_key,
        )

        existing_res = await session.execute(
            select(ScheduleDecision).where(ScheduleDecision.idempotency_key == base_idempotency_key)
        )
        existing_decision = existing_res.scalar_one_or_none()
        if existing_decision and existing_decision.reservation_id:
            res_check = await session.execute(
                select(ScheduleReservation).where(
                    ScheduleReservation.id == existing_decision.reservation_id
                )
            )
            existing_reservation = res_check.scalar_one_or_none()
            if existing_reservation and existing_reservation.state in (
                ReservationState.ACTIVE.value,
                ReservationState.DISPATCHING.value,
                ReservationState.CONSUMED.value,
            ):
                return ScheduleDecisionResponse.model_validate(existing_decision)

        # 7. Compute candidate execution window
        min_start = max(
            now + timedelta(seconds=policy_config.min_lead_time_seconds),
            request.earliest_start_at or now,
        )
        duration_seconds = max(60, request.estimated_duration_seconds or 300)

        # Compute candidate windows from channel preferences
        candidate_windows = compute_channel_windows_utc(
            target_timezone=target_timezone,
            preferred_days=preferred_days,
            preferred_time_windows=preferred_time_windows,
            start_date=min_start,
            horizon_days=policy_config.max_schedule_horizon_days,
            dst_config=policy_config.dst_config,
        )

        chosen_start: datetime | None = None
        chosen_end: datetime | None = None
        dst_evidence: dict[str, Any] | None = None

        for w_start, _w_end, evidence in candidate_windows:
            if w_start >= min_start:
                chosen_start = w_start
                chosen_end = w_start + timedelta(seconds=duration_seconds)
                dst_evidence = evidence
                break

        if not chosen_start:
            # Fallback to direct time window if no channel preference match
            chosen_start = min_start
            chosen_end = min_start + timedelta(seconds=duration_seconds)

        # 8. Compute priority score
        created_at_dt = mission.created_at if hasattr(mission, "created_at") else now
        priority_breakdown = PriorityEngine.calculate_priority(
            mission_priority=request.priority,
            deadline_at=request.deadline_at,
            created_at=created_at_dt,
            now=now,
            fairness=policy_config.fairness,
            max_urgency_bonus=policy_config.max_urgency_bonus,
            target_id=request.target_id,
        )

        diagnostic_context = {
            "priority_breakdown": priority_breakdown.to_dict(),
            "target_timezone": target_timezone,
        }
        if dst_evidence:
            diagnostic_context["dst_resolution"] = dst_evidence

        # 9. Allocate slot via SlotAllocator
        decision, reservation = await SlotAllocator.allocate_slot(
            session,
            policy=policy,
            policy_config=policy_config,
            mission_id=request.mission_id,
            task_id=request.task_id,
            target_type=request.target_type.value,
            target_id=request.target_id,
            workload_category=request.workload_category.value,
            channel_id=request.channel_id,
            channel_dna_revision_id=channel_dna_revision_id,
            guardian_epoch=mission.guardian_epoch,
            candidate_start=chosen_start,
            candidate_end=chosen_end,
            priority=request.priority,
            estimated_duration_seconds=duration_seconds,
            deadline_at=request.deadline_at,
            earliest_start_at=request.earliest_start_at,
            latest_start_at=request.latest_start_at,
            caller_key=request.caller_key,
            priority_score=priority_breakdown.total_score,
            diagnostic_context=diagnostic_context,
        )

        await session.commit()
        return ScheduleDecisionResponse.model_validate(decision)
