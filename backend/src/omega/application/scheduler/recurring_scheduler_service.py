"""Recurring schedule application service governing lifecycle, editing, and version snapshots."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.config import get_settings
from omega.domain.recurring_schedule import (
    CatchUpPolicy,
    DSTAmbiguousStrategy,
    DSTNonexistentStrategy,
    RecurringScheduleCreate,
    RecurringScheduleResponse,
    RecurringScheduleStatus,
    RecurringScheduleTargetType,
    RecurringScheduleUpdate,
    RecurringScheduleVersionResponse,
    TimelinePreviewRequest,
    TimelinePreviewResponse,
    calculate_next_occurrence,
    validate_target_payload,
)
from omega.logging import get_logger
from omega.infrastructure.models import (
    Channel,
    ContentCampaign,
    RecurringSchedule,
    RecurringScheduleVersion,
)

logger = get_logger(service="omega-scheduler-recurring-service")


class SchedulerRuntimeError(ValueError):
    """Base error for recurring scheduler runtime violations."""


class SchedulerFeatureDisabledError(SchedulerRuntimeError):
    """Raised when an operation requires the master execution gate to be enabled."""


class RecurringSchedulerService:
    """Manages the lifecycle and immutable version definitions of recurring schedules."""

    @staticmethod
    async def _db_now(session: AsyncSession) -> datetime:
        """Fetch authoritative wall-clock timestamp from PostgreSQL."""
        res = await session.execute(select(func.clock_timestamp()))
        now_dt = res.scalar_one()
        if now_dt.tzinfo is None:
            return now_dt.replace(tzinfo=timezone.utc)
        return now_dt

    @classmethod
    async def create_draft_schedule(
        cls, session: AsyncSession, data: RecurringScheduleCreate
    ) -> RecurringScheduleResponse:
        """Create a new schedule definition in safe DRAFT status."""
        # Validate target existence before insertion
        await cls._validate_target_exists(session, data.target_type, data.target_id)

        # Validate typed payload
        validated_payload = validate_target_payload(data.target_type, data.payload_template)

        schedule_id = uuid4()
        version_id = uuid4()

        # DRAFT schedule: next_run_at is strictly NULL (enforced by DB check chk_schedule_draft_no_run)
        schedule = RecurringSchedule(
            id=schedule_id,
            name=data.name,
            description=data.description,
            status=RecurringScheduleStatus.DRAFT.value,
            current_version_id=version_id,
            start_time=data.start_time,
            end_time=data.end_time,
            last_run_at=None,
            next_run_at=None,
        )
        session.add(schedule)

        version = RecurringScheduleVersion(
            id=version_id,
            schedule_id=schedule_id,
            version_number=1,
            cron_expression=data.cron_expression,
            interval_seconds=data.interval_seconds,
            timezone=data.timezone,
            dst_ambiguous_strategy=data.dst_ambiguous_strategy.value,
            dst_nonexistent_strategy=data.dst_nonexistent_strategy.value,
            catch_up_policy=data.catch_up_policy.value,
            max_catch_up_occurrences=data.max_catch_up_occurrences,
            target_type=data.target_type.value,
            target_id=data.target_id,
            payload_template=validated_payload,
        )
        session.add(version)
        await session.commit()

        logger.info("Created draft recurring schedule", schedule_id=str(schedule_id), name=data.name)
        return await cls.get_schedule(session, schedule_id)  # type: ignore

    @classmethod
    async def activate_schedule(cls, session: AsyncSession, schedule_id: UUID) -> RecurringScheduleResponse:
        """Explicitly activate a DRAFT or PAUSED schedule, computing initial next_run_at."""
        settings = get_settings()
        if not settings.recurring_scheduler_enabled:
            raise SchedulerFeatureDisabledError(
                "Recurring scheduler execution is disabled by RECURRING_SCHEDULER_ENABLED=false."
            )

        schedule = (
            await session.execute(
                select(RecurringSchedule)
                .where(RecurringSchedule.id == schedule_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if schedule is None:
            raise SchedulerRuntimeError(f"Schedule '{schedule_id}' not found.")

        if schedule.status not in (RecurringScheduleStatus.DRAFT.value, RecurringScheduleStatus.PAUSED.value):
            raise SchedulerRuntimeError(f"Cannot activate schedule from status '{schedule.status}'.")

        version = (
            await session.execute(
                select(RecurringScheduleVersion).where(
                    RecurringScheduleVersion.id == schedule.current_version_id
                )
            )
        ).scalar_one()

        # Validate target is still eligible
        await cls._validate_target_exists(session, version.target_type, version.target_id)

        now_utc = await cls._db_now(session)

        # Calculate initial next_run_at starting from now_utc
        next_run = calculate_next_occurrence(
            cron_expression=version.cron_expression,
            interval_seconds=version.interval_seconds,
            timezone_str=version.timezone,
            reference_time_utc=now_utc,
            dst_ambiguous=DSTAmbiguousStrategy(version.dst_ambiguous_strategy),
            dst_nonexistent=DSTNonexistentStrategy(version.dst_nonexistent_strategy),
            start_time_utc=schedule.start_time,
            end_time_utc=schedule.end_time,
        )

        schedule.status = RecurringScheduleStatus.ACTIVE.value
        schedule.next_run_at = next_run
        await session.commit()

        logger.info("Activated recurring schedule", schedule_id=str(schedule_id), next_run_at=str(next_run))
        return await cls.get_schedule(session, schedule_id)  # type: ignore

    @classmethod
    async def pause_schedule(cls, session: AsyncSession, schedule_id: UUID) -> RecurringScheduleResponse:
        """Pause an active schedule. Paused time does not generate occurrences."""
        schedule = (
            await session.execute(
                select(RecurringSchedule)
                .where(RecurringSchedule.id == schedule_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if schedule is None:
            raise SchedulerRuntimeError(f"Schedule '{schedule_id}' not found.")

        if schedule.status != RecurringScheduleStatus.ACTIVE.value:
            raise SchedulerRuntimeError(f"Cannot pause schedule from status '{schedule.status}'.")

        schedule.status = RecurringScheduleStatus.PAUSED.value
        # next_run_at is cleared so non-active schedule retains no executable pointer
        schedule.next_run_at = None
        await session.commit()

        logger.info("Paused recurring schedule", schedule_id=str(schedule_id))
        return await cls.get_schedule(session, schedule_id)  # type: ignore

    @classmethod
    async def resume_schedule(cls, session: AsyncSession, schedule_id: UUID) -> RecurringScheduleResponse:
        """Resume a paused schedule strictly calculating next_run_at forward from resume time."""
        settings = get_settings()
        if not settings.recurring_scheduler_enabled:
            raise SchedulerFeatureDisabledError(
                "Recurring scheduler execution is disabled by RECURRING_SCHEDULER_ENABLED=false."
            )

        schedule = (
            await session.execute(
                select(RecurringSchedule)
                .where(RecurringSchedule.id == schedule_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if schedule is None:
            raise SchedulerRuntimeError(f"Schedule '{schedule_id}' not found.")

        if schedule.status != RecurringScheduleStatus.PAUSED.value:
            raise SchedulerRuntimeError(f"Cannot resume schedule from status '{schedule.status}'.")

        version = (
            await session.execute(
                select(RecurringScheduleVersion).where(
                    RecurringScheduleVersion.id == schedule.current_version_id
                )
            )
        ).scalar_one()

        now_utc = await cls._db_now(session)

        # Paused time is not downtime: next_run_at is computed forward from now_utc
        next_run = calculate_next_occurrence(
            cron_expression=version.cron_expression,
            interval_seconds=version.interval_seconds,
            timezone_str=version.timezone,
            reference_time_utc=now_utc,
            dst_ambiguous=DSTAmbiguousStrategy(version.dst_ambiguous_strategy),
            dst_nonexistent=DSTNonexistentStrategy(version.dst_nonexistent_strategy),
            start_time_utc=schedule.start_time,
            end_time_utc=schedule.end_time,
        )

        schedule.status = RecurringScheduleStatus.ACTIVE.value
        schedule.next_run_at = next_run
        await session.commit()

        logger.info("Resumed recurring schedule", schedule_id=str(schedule_id), next_run_at=str(next_run))
        return await cls.get_schedule(session, schedule_id)  # type: ignore

    @classmethod
    async def cancel_schedule(cls, session: AsyncSession, schedule_id: UUID) -> RecurringScheduleResponse:
        """Permanently cancel a recurring schedule."""
        schedule = (
            await session.execute(
                select(RecurringSchedule)
                .where(RecurringSchedule.id == schedule_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if schedule is None:
            raise SchedulerRuntimeError(f"Schedule '{schedule_id}' not found.")

        schedule.status = RecurringScheduleStatus.CANCELLED.value
        schedule.next_run_at = None
        await session.commit()

        logger.info("Cancelled recurring schedule", schedule_id=str(schedule_id))
        return await cls.get_schedule(session, schedule_id)  # type: ignore

    @classmethod
    async def edit_schedule(
        cls, session: AsyncSession, schedule_id: UUID, data: RecurringScheduleUpdate
    ) -> RecurringScheduleResponse:
        """Edit schedule definition by inserting a new immutable version snapshot."""
        schedule = (
            await session.execute(
                select(RecurringSchedule)
                .where(RecurringSchedule.id == schedule_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if schedule is None:
            raise SchedulerRuntimeError(f"Schedule '{schedule_id}' not found.")

        if schedule.status in (
            RecurringScheduleStatus.COMPLETED.value,
            RecurringScheduleStatus.CANCELLED.value,
            RecurringScheduleStatus.ARCHIVED.value,
        ):
            raise SchedulerRuntimeError(f"Cannot edit terminal schedule in status '{schedule.status}'.")

        cur_version = (
            await session.execute(
                select(RecurringScheduleVersion).where(
                    RecurringScheduleVersion.id == schedule.current_version_id
                )
            )
        ).scalar_one()

        # Update top-level attributes
        if data.name is not None:
            schedule.name = data.name
        if data.description is not None:
            schedule.description = data.description
        if data.end_time is not None:
            schedule.end_time = data.end_time

        # Check if version definition changed
        target_type = data.target_type or RecurringScheduleTargetType(cur_version.target_type)
        target_id = data.target_id or cur_version.target_id

        # Validate target
        await cls._validate_target_exists(session, target_type, target_id)

        raw_payload = data.payload_template if data.payload_template is not None else cur_version.payload_template
        validated_payload = validate_target_payload(target_type, raw_payload)

        cron_expr = data.cron_expression if data.cron_expression is not None else (
            cur_version.cron_expression if data.interval_seconds is None else None
        )
        interval_secs = data.interval_seconds if data.interval_seconds is not None else (
            cur_version.interval_seconds if data.cron_expression is None else None
        )

        new_version_id = uuid4()
        new_version = RecurringScheduleVersion(
            id=new_version_id,
            schedule_id=schedule.id,
            version_number=cur_version.version_number + 1,
            cron_expression=cron_expr,
            interval_seconds=interval_secs,
            timezone=data.timezone or cur_version.timezone,
            dst_ambiguous_strategy=(data.dst_ambiguous_strategy.value if data.dst_ambiguous_strategy else cur_version.dst_ambiguous_strategy),
            dst_nonexistent_strategy=(data.dst_nonexistent_strategy.value if data.dst_nonexistent_strategy else cur_version.dst_nonexistent_strategy),
            catch_up_policy=(data.catch_up_policy.value if data.catch_up_policy else cur_version.catch_up_policy),
            max_catch_up_occurrences=(data.max_catch_up_occurrences or cur_version.max_catch_up_occurrences),
            target_type=target_type.value,
            target_id=target_id,
            payload_template=validated_payload,
        )
        session.add(new_version)
        schedule.current_version_id = new_version_id

        # If active, recompute next_run_at strictly forward from now_utc
        if schedule.status == RecurringScheduleStatus.ACTIVE.value:
            now_utc = await cls._db_now(session)
            schedule.next_run_at = calculate_next_occurrence(
                cron_expression=new_version.cron_expression,
                interval_seconds=new_version.interval_seconds,
                timezone_str=new_version.timezone,
                reference_time_utc=now_utc,
                dst_ambiguous=DSTAmbiguousStrategy(new_version.dst_ambiguous_strategy),
                dst_nonexistent=DSTNonexistentStrategy(new_version.dst_nonexistent_strategy),
                start_time_utc=schedule.start_time,
                end_time_utc=schedule.end_time,
            )

        await session.commit()
        logger.info(
            "Updated recurring schedule with new version",
            schedule_id=str(schedule_id),
            version=new_version.version_number,
        )
        return await cls.get_schedule(session, schedule_id)  # type: ignore

    @classmethod
    async def get_schedule(cls, session: AsyncSession, schedule_id: UUID) -> RecurringScheduleResponse | None:
        """Fetch recurring schedule with its current version."""
        res = await session.execute(
            select(RecurringSchedule)
            .options(selectinload(RecurringSchedule.current_version))
            .where(RecurringSchedule.id == schedule_id)
        )
        schedule = res.scalar_one_or_none()
        if schedule is None:
            return None

        v_resp = None
        if schedule.current_version:
            v_resp = RecurringScheduleVersionResponse(
                id=schedule.current_version.id,
                schedule_id=schedule.current_version.schedule_id,
                version_number=schedule.current_version.version_number,
                cron_expression=schedule.current_version.cron_expression,
                interval_seconds=schedule.current_version.interval_seconds,
                timezone=schedule.current_version.timezone,
                dst_ambiguous_strategy=schedule.current_version.dst_ambiguous_strategy,
                dst_nonexistent_strategy=schedule.current_version.dst_nonexistent_strategy,
                catch_up_policy=schedule.current_version.catch_up_policy,
                max_catch_up_occurrences=schedule.current_version.max_catch_up_occurrences,
                target_type=schedule.current_version.target_type,
                target_id=schedule.current_version.target_id,
                payload_template=schedule.current_version.payload_template,
                created_at=schedule.current_version.created_at,
            )

        return RecurringScheduleResponse(
            id=schedule.id,
            name=schedule.name,
            description=schedule.description,
            status=schedule.status,
            current_version_id=schedule.current_version_id,
            start_time=schedule.start_time,
            end_time=schedule.end_time,
            last_run_at=schedule.last_run_at,
            next_run_at=schedule.next_run_at,
            created_at=schedule.created_at,
            updated_at=schedule.updated_at,
            current_version=v_resp,
        )

    @classmethod
    async def list_schedules(
        cls, session: AsyncSession, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[RecurringScheduleResponse]:
        """List recurring schedules."""
        query = select(RecurringSchedule).options(selectinload(RecurringSchedule.current_version))
        if status is not None:
            query = query.where(RecurringSchedule.status == status)
        query = query.order_by(RecurringSchedule.created_at.desc()).limit(limit).offset(offset)
        rows = (await session.execute(query)).scalars().all()
        results: list[RecurringScheduleResponse] = []
        for s in rows:
            v_resp = None
            if s.current_version:
                v_resp = RecurringScheduleVersionResponse(
                    id=s.current_version.id,
                    schedule_id=s.current_version.schedule_id,
                    version_number=s.current_version.version_number,
                    cron_expression=s.current_version.cron_expression,
                    interval_seconds=s.current_version.interval_seconds,
                    timezone=s.current_version.timezone,
                    dst_ambiguous_strategy=s.current_version.dst_ambiguous_strategy,
                    dst_nonexistent_strategy=s.current_version.dst_nonexistent_strategy,
                    catch_up_policy=s.current_version.catch_up_policy,
                    max_catch_up_occurrences=s.current_version.max_catch_up_occurrences,
                    target_type=s.current_version.target_type,
                    target_id=s.current_version.target_id,
                    payload_template=s.current_version.payload_template,
                    created_at=s.current_version.created_at,
                )
            results.append(
                RecurringScheduleResponse(
                    id=s.id,
                    name=s.name,
                    description=s.description,
                    status=s.status,
                    current_version_id=s.current_version_id,
                    start_time=s.start_time,
                    end_time=s.end_time,
                    last_run_at=s.last_run_at,
                    next_run_at=s.next_run_at,
                    created_at=s.created_at,
                    updated_at=s.updated_at,
                    current_version=v_resp,
                )
            )
        return results

    @staticmethod
    def preview_timeline(request: TimelinePreviewRequest) -> TimelinePreviewResponse:
        """Pure calculation preview of next N occurrences without persisting anything."""
        occurrences: list[datetime] = []
        ref = request.start_time
        for _ in range(request.count):
            nxt = calculate_next_occurrence(
                cron_expression=request.cron_expression,
                interval_seconds=request.interval_seconds,
                timezone_str=request.timezone,
                reference_time_utc=ref,
                dst_ambiguous=request.dst_ambiguous_strategy,
                dst_nonexistent=request.dst_nonexistent_strategy,
                start_time_utc=request.start_time,
                end_time_utc=request.end_time,
            )
            if nxt is None:
                break
            occurrences.append(nxt)
            ref = nxt
        return TimelinePreviewResponse(occurrences_utc=occurrences, count=len(occurrences))

    @staticmethod
    async def _validate_target_exists(
        session: AsyncSession, target_type: RecurringScheduleTargetType | str, target_id: UUID
    ) -> None:
        """Validate target downstream authority existence and eligibility."""
        t_type = RecurringScheduleTargetType(str(target_type))
        if t_type == RecurringScheduleTargetType.STANDALONE_MISSION:
            # target_id must be an existing, non-archived Channel
            chan = (
                await session.execute(select(Channel).where(Channel.id == target_id))
            ).scalar_one_or_none()
            if chan is None:
                raise SchedulerRuntimeError(f"Target Channel '{target_id}' does not exist.")
            if chan.state == "ARCHIVED":
                raise SchedulerRuntimeError(f"Target Channel '{target_id}' is ARCHIVED.")

        elif t_type == RecurringScheduleTargetType.CAMPAIGN_ADMISSION:
            # target_id must be a LAZY_ADMISSION_V1 Campaign in eligible state
            camp = (
                await session.execute(
                    select(ContentCampaign).where(ContentCampaign.id == target_id)
                )
            ).scalar_one_or_none()
            if camp is None:
                raise SchedulerRuntimeError(f"Target ContentCampaign '{target_id}' does not exist.")
            if camp.orchestration_mode != "LAZY_ADMISSION_V1":
                raise SchedulerRuntimeError(
                    f"Target ContentCampaign '{target_id}' has mode '{camp.orchestration_mode}'. "
                    f"Only 'LAZY_ADMISSION_V1' is supported; 'LEGACY_UPFRONT' is rejected."
                )
            if camp.status not in ("READY", "RUNNING", "PAUSED"):
                raise SchedulerRuntimeError(
                    f"Target ContentCampaign '{target_id}' has ineligible status '{camp.status}'."
                )
