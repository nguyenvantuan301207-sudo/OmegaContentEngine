"""Two-phase scheduler sweep service and stale occurrence reconciler."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.scheduler.adapters.campaign_adapter import CampaignScheduleTargetAdapter
from omega.application.scheduler.adapters.mission_adapter import MissionScheduleTargetAdapter
from omega.config import get_settings
from omega.domain.recurring_schedule import (
    CatchUpPolicy,
    DSTAmbiguousStrategy,
    DSTNonexistentStrategy,
    RecurringOccurrenceStatus,
    RecurringScheduleStatus,
    RecurringScheduleTargetType,
    calculate_next_occurrence,
    partition_catch_up_occurrences,
)
from omega.infrastructure.models import (
    RecurringSchedule,
    RecurringScheduleCampaignBinding,
    RecurringScheduleMissionBinding,
    RecurringScheduleOccurrence,
    RecurringScheduleVersion,
)

logger = logging.getLogger("omega.scheduler.recurring_sweep")


class RecurringSweepService:
    """Executes atomic occurrence materialization (Phase 1) and isolated downstream dispatch (Phase 2)."""

    @staticmethod
    async def _db_now(session: AsyncSession) -> datetime:
        """Fetch authoritative wall-clock timestamp from PostgreSQL."""
        res = await session.execute(select(func.clock_timestamp()))
        now_dt = res.scalar_one()
        if now_dt.tzinfo is None:
            return now_dt.replace(tzinfo=timezone.utc)
        return now_dt

    @classmethod
    async def execute_due_sweep(cls, session: AsyncSession) -> dict[str, int]:
        """Execute Phase 1 (materialize due schedules) and Phase 2 (dispatch pending occurrences)."""
        settings = get_settings()
        if not settings.recurring_scheduler_enabled:
            return {"status": "DISABLED", "materialized": 0, "dispatched": 0}

        # ── Phase 1: Discover and materialize due schedules atomically ──
        materialized_count = await cls._materialize_due_schedules(session, settings.scheduler_sweep_batch_size)

        # ── Phase 2: Dispatch pending occurrences with row locking ──
        dispatched_count = await cls._dispatch_pending_occurrences(session, settings.scheduler_sweep_batch_size)

        return {
            "status": "SUCCESS",
            "materialized": materialized_count,
            "dispatched": dispatched_count,
        }

    @classmethod
    async def _materialize_due_schedules(cls, session: AsyncSession, batch_size: int) -> int:
        """Phase 1: Discover due schedules using clock_timestamp() and materialize occurrences."""
        now_utc = await cls._db_now(session)

        # Query due active schedules with row locks, skipping already locked
        due_schedules = list(
            (
                await session.execute(
                    select(RecurringSchedule)
                    .options(selectinload(RecurringSchedule.current_version))
                    .where(
                        RecurringSchedule.status == RecurringScheduleStatus.ACTIVE.value,
                        RecurringSchedule.next_run_at <= func.clock_timestamp(),
                    )
                    .with_for_update(skip_locked=True)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

        if not due_schedules:
            return 0

        total_materialized = 0

        for schedule in due_schedules:
            version = schedule.current_version
            if version is None and schedule.current_version_id is not None:
                version = (
                    await session.execute(
                        select(RecurringScheduleVersion).where(
                            RecurringScheduleVersion.id == schedule.current_version_id
                        )
                    )
                ).scalar_one_or_none()
            if version is None:
                continue

            scheduled_at = schedule.next_run_at
            if scheduled_at is None:
                continue

            # Check if multiple occurrences were missed while due
            missed_ticks = cls._collect_missed_ticks(
                schedule=schedule,
                version=version,
                from_utc=scheduled_at,
                up_to_utc=now_utc,
            )

            # Evaluate catch-up policy
            to_dispatch, to_skip = partition_catch_up_occurrences(
                missed_occurrences=missed_ticks,
                policy=CatchUpPolicy(version.catch_up_policy),
                max_catch_up=version.max_catch_up_occurrences,
            )

            # Record skipped occurrences
            for skip_dt in to_skip:
                idempotency_key = f"rec-occ:{schedule.id}:{skip_dt.isoformat()}"
                occ = RecurringScheduleOccurrence(
                    schedule_id=schedule.id,
                    schedule_version_id=version.id,
                    occurrence_at=skip_dt,
                    status=RecurringOccurrenceStatus.SKIPPED.value,
                    idempotency_key=idempotency_key,
                    downstream_target_type=version.target_type,
                    downstream_target_id=None,
                    error_message=f"Skipped per catch-up policy '{version.catch_up_policy}'",
                )
                session.add(occ)

            # Record dispatchable occurrences
            latest_occurrence_at = scheduled_at
            for dispatch_dt in to_dispatch:
                idempotency_key = f"rec-occ:{schedule.id}:{dispatch_dt.isoformat()}"
                occ = RecurringScheduleOccurrence(
                    schedule_id=schedule.id,
                    schedule_version_id=version.id,
                    occurrence_at=dispatch_dt,
                    status=RecurringOccurrenceStatus.PENDING.value,
                    idempotency_key=idempotency_key,
                    downstream_target_type=version.target_type,
                    downstream_target_id=None,
                )
                session.add(occ)
                total_materialized += 1
                latest_occurrence_at = dispatch_dt

            # Advance schedule timeline
            schedule.last_run_at = latest_occurrence_at
            next_due = calculate_next_occurrence(
                cron_expression=version.cron_expression,
                interval_seconds=version.interval_seconds,
                timezone_str=version.timezone,
                reference_time_utc=max(latest_occurrence_at, now_utc),
                dst_ambiguous=DSTAmbiguousStrategy(version.dst_ambiguous_strategy),
                dst_nonexistent=DSTNonexistentStrategy(version.dst_nonexistent_strategy),
                start_time_utc=schedule.start_time,
                end_time_utc=schedule.end_time,
            )
            schedule.next_run_at = next_due

            # If schedule has no further runs, transition to COMPLETED
            if next_due is None:
                schedule.status = RecurringScheduleStatus.COMPLETED.value

        # Commit Phase 1: Timeline advancement and occurrence rows are 100% durable
        await session.commit()
        return total_materialized

    @classmethod
    def _collect_missed_ticks(
        cls,
        schedule: RecurringSchedule,
        version: RecurringScheduleVersion,
        from_utc: datetime,
        up_to_utc: datetime,
    ) -> list[datetime]:
        """Collect all mathematical occurrence timestamps between from_utc and up_to_utc."""
        ticks: list[datetime] = [from_utc]
        ref = from_utc
        max_loop = 50  # Cap search to prevent unbounded calculation
        while len(ticks) < max_loop:
            nxt = calculate_next_occurrence(
                cron_expression=version.cron_expression,
                interval_seconds=version.interval_seconds,
                timezone_str=version.timezone,
                reference_time_utc=ref,
                dst_ambiguous=DSTAmbiguousStrategy(version.dst_ambiguous_strategy),
                dst_nonexistent=DSTNonexistentStrategy(version.dst_nonexistent_strategy),
                start_time_utc=schedule.start_time,
                end_time_utc=schedule.end_time,
            )
            if nxt is None or nxt > up_to_utc:
                break
            ticks.append(nxt)
            ref = nxt
        return ticks

    @classmethod
    async def _dispatch_pending_occurrences(cls, session: AsyncSession, batch_size: int) -> int:
        """Phase 2: Claim PENDING occurrences and dispatch to canonical target adapters."""
        settings = get_settings()

        pending_rows = list(
            (
                await session.execute(
                    select(RecurringScheduleOccurrence)
                    .where(RecurringScheduleOccurrence.status == RecurringOccurrenceStatus.PENDING.value)
                    .order_by(RecurringScheduleOccurrence.occurrence_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

        if not pending_rows:
            return 0

        dispatched_count = 0

        for occ in pending_rows:
            version = (
                await session.execute(
                    select(RecurringScheduleVersion).where(
                        RecurringScheduleVersion.id == occ.schedule_version_id
                    )
                )
            ).scalar_one_or_none()
            if version is None:
                continue

            # Transition to DISPATCHING before invoking adapter
            occ.status = RecurringOccurrenceStatus.DISPATCHING.value
            occ.attempt_count += 1
            occ.last_attempt_at = await cls._db_now(session)
            await session.commit()

            target_type = RecurringScheduleTargetType(occ.downstream_target_type)

            try:
                if target_type == RecurringScheduleTargetType.STANDALONE_MISSION:
                    mission, reason = await MissionScheduleTargetAdapter.dispatch(session, occ, version)
                    if reason in ("DISPATCHED", "ALREADY_BOUND"):
                        dispatched_count += 1

                elif target_type == RecurringScheduleTargetType.CAMPAIGN_ADMISSION:
                    item, reason = await CampaignScheduleTargetAdapter.dispatch(session, occ, version)
                    if reason in ("ADMITTED", "ALREADY_BOUND"):
                        dispatched_count += 1
                    elif reason == "CAPACITY_FULL":
                        # Transition to WAITING with deadline
                        now = await cls._db_now(session)
                        occ.status = RecurringOccurrenceStatus.WAITING.value
                        occ.wait_deadline_at = now + timedelta(seconds=settings.scheduler_wait_timeout_seconds)
                        await session.commit()
                    elif reason in ("CAMPAIGN_NOT_RUNNING", "NO_PENDING_ITEMS"):
                        occ.status = RecurringOccurrenceStatus.SKIPPED.value
                        occ.error_message = reason
                        await session.commit()

            except Exception as exc:
                logger.error("Exception during occurrence dispatch occurrence_id=%s: %s", occ.id, exc)
                await session.rollback()
                # Re-mark as PENDING with error if attempts remain, else FAILED
                occ_err = (
                    await session.execute(
                        select(RecurringScheduleOccurrence)
                        .where(RecurringScheduleOccurrence.id == occ.id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if occ_err is not None:
                    if occ_err.attempt_count >= settings.scheduler_max_dispatch_attempts:
                        occ_err.status = RecurringOccurrenceStatus.FAILED.value
                    else:
                        occ_err.status = RecurringOccurrenceStatus.PENDING.value
                    occ_err.error_message = str(exc)
                    await session.commit()

        return dispatched_count

    @classmethod
    async def reconcile_stale_occurrences(cls, session: AsyncSession) -> dict[str, int]:
        """PostgreSQL-backed reconciliation for stale PENDING, WAITING, and DISPATCHING occurrences."""
        settings = get_settings()
        if not settings.recurring_scheduler_enabled:
            return {"status": "DISABLED", "recovered": 0, "skipped": 0, "failed": 0}

        now_utc = await cls._db_now(session)
        recovered = 0
        skipped = 0
        failed = 0

        # 1. Reconcile WAITING occurrences where wait deadline expired
        expired_waiting = list(
            (
                await session.execute(
                    select(RecurringScheduleOccurrence)
                    .where(
                        RecurringScheduleOccurrence.status == RecurringOccurrenceStatus.WAITING.value,
                        RecurringScheduleOccurrence.wait_deadline_at <= func.clock_timestamp(),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for occ in expired_waiting:
            occ.status = RecurringOccurrenceStatus.SKIPPED.value
            occ.error_message = "Wait deadline expired while awaiting capacity"
            skipped += 1
        await session.commit()

        # 2. Reconcile stale DISPATCHING occurrences (stuck longer than timeout)
        stale_dispatch_cutoff = now_utc - timedelta(seconds=settings.scheduler_dispatch_timeout_seconds)
        stale_dispatching = list(
            (
                await session.execute(
                    select(RecurringScheduleOccurrence)
                    .where(
                        RecurringScheduleOccurrence.status == RecurringOccurrenceStatus.DISPATCHING.value,
                        RecurringScheduleOccurrence.updated_at <= stale_dispatch_cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )

        for occ in stale_dispatching:
            target_type = RecurringScheduleTargetType(occ.downstream_target_type)

            # Check authoritative binding table
            has_binding = False
            if target_type == RecurringScheduleTargetType.STANDALONE_MISSION:
                b_mission = (
                    await session.execute(
                        select(RecurringScheduleMissionBinding).where(
                            RecurringScheduleMissionBinding.occurrence_id == occ.id
                        )
                    )
                ).scalar_one_or_none()
                if b_mission is not None:
                    has_binding = True
                    occ.downstream_target_id = b_mission.mission_id

            elif target_type == RecurringScheduleTargetType.CAMPAIGN_ADMISSION:
                b_camp = (
                    await session.execute(
                        select(RecurringScheduleCampaignBinding).where(
                            RecurringScheduleCampaignBinding.occurrence_id == occ.id
                        )
                    )
                ).scalar_one_or_none()
                if b_camp is not None:
                    has_binding = True
                    occ.downstream_target_id = b_camp.campaign_item_id

            if has_binding:
                # Downstream authority was committed before crash: converge to DISPATCHED
                occ.status = RecurringOccurrenceStatus.DISPATCHED.value
                recovered += 1
            else:
                # No downstream authority exists: reset to PENDING if attempts remain
                if occ.attempt_count >= settings.scheduler_max_dispatch_attempts:
                    occ.status = RecurringOccurrenceStatus.FAILED.value
                    occ.error_message = "Stale dispatch exceeded maximum attempts"
                    failed += 1
                else:
                    occ.status = RecurringOccurrenceStatus.PENDING.value
                    recovered += 1

        await session.commit()

        return {
            "status": "SUCCESS",
            "recovered": recovered,
            "skipped": skipped,
            "failed": failed,
        }
