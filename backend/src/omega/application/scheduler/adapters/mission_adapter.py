"""Target adapter for Standalone Mission recurring schedule execution."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.mission_service import (
    _create_mission_in_transaction,
    _plan_mission_in_transaction,
    _start_mission_in_transaction,
)
from omega.domain.mission import AutonomyLevel, MissionCreate
from omega.domain.recurring_schedule import (
    RecurringOccurrenceStatus,
    StandaloneMissionPayload,
    validate_target_payload,
)
from omega.logging import get_logger
from omega.infrastructure.models import (
    Mission,
    RecurringScheduleMissionBinding,
    RecurringScheduleOccurrence,
    RecurringScheduleVersion,
)

logger = get_logger(service="omega-scheduler-mission-adapter")


class MissionScheduleTargetAdapter:
    """Dispatches standalone Mission creation and planning atomically for an occurrence."""

    @staticmethod
    async def dispatch(
        session: AsyncSession,
        occurrence: RecurringScheduleOccurrence,
        version: RecurringScheduleVersion,
    ) -> tuple[Mission | None, str]:
        """Atomically create, plan, and start mission, binding to the occurrence.

        Returns (Mission, status_reason).
        """
        # 1. Check existing binding
        existing_binding = (
            await session.execute(
                select(RecurringScheduleMissionBinding).where(
                    RecurringScheduleMissionBinding.occurrence_id == occurrence.id
                )
            )
        ).scalar_one_or_none()
        if existing_binding is not None:
            existing_mission = (
                await session.execute(
                    select(Mission).where(Mission.id == existing_binding.mission_id)
                )
            ).scalar_one_or_none()
            return existing_mission, "ALREADY_BOUND"

        # 2. Parse typed payload
        validated_payload = validate_target_payload(version.target_type, version.payload_template)
        payload = StandaloneMissionPayload.model_validate(validated_payload)

        # 3. Create MissionCreate domain input
        # Note: version.target_id represents the linked Channel ID for standalone missions
        mission_in = MissionCreate(
            title=payload.title,
            objective=payload.objective,
            channel_id=version.target_id,
            description=payload.description,
            autonomy_level=AutonomyLevel(payload.autonomy_level),
            priority=payload.priority,
            metadata={"origin_occurrence_id": str(occurrence.id)},
        )

        try:
            # 4. Atomic transaction: create -> plan -> start -> binding -> occurrence DISPATCHED
            mission = await _create_mission_in_transaction(session, mission_in)
            mission, execution = await _plan_mission_in_transaction(session, mission)
            await _start_mission_in_transaction(session, mission, actor="RECURRING_SCHEDULER")

            # 5. Insert atomic binding
            binding = RecurringScheduleMissionBinding(
                occurrence_id=occurrence.id,
                mission_id=mission.id,
            )
            session.add(binding)

            # 6. Update occurrence
            occ_locked = (
                await session.execute(
                    select(RecurringScheduleOccurrence)
                    .where(RecurringScheduleOccurrence.id == occurrence.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if occ_locked is not None:
                occ_locked.status = RecurringOccurrenceStatus.DISPATCHED.value
                occ_locked.downstream_target_id = mission.id

            await session.commit()
            logger.info(
                "Dispatched standalone mission for occurrence",
                occurrence_id=str(occurrence.id),
                mission_id=str(mission.id),
            )
            return mission, "DISPATCHED"

        except Exception as exc:
            await session.rollback()
            logger.error(
                "Failed to dispatch standalone mission for occurrence",
                occurrence_id=str(occurrence.id),
                error=str(exc),
            )
            raise
