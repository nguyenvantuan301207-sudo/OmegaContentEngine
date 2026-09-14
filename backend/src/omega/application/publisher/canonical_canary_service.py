"""Canonical construction and identity gates for the private publisher canary.

This module deliberately composes the production Mission, Publisher, and
Scheduler services.  It does not create an alternative canary-only task graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.mission_service import create_mission, plan_mission
from omega.application.publisher.calendar_service import PublishCalendarService
from omega.application.publisher.intent_service import PublishIntentService
from omega.domain.mission import MissionCreate
from omega.domain.publisher import PrivacyStatus, PublishIntentCreate, PublishIntentState
from omega.domain.scheduler import ScheduleTargetType
from omega.infrastructure.models import (
    Channel,
    MediaArtifact,
    Mission,
    MissionExecution,
    PlatformAccount,
    ProductionRequest,
    PublishAttempt,
    PublishIntent,
    ScheduleDecision,
    SchedulerDispatchOutbox,
    ScheduleReservation,
    Task,
    UploadSession,
)

OMEGA_CANARY_CHANNEL_ID = UUID("fa8813c9-9e7b-43d0-b76e-1bb323ad5a7a")
OMEGA_CANARY_YOUTUBE_CHANNEL_ID = "UCuOLELkr9sco11X2QgNSN1Q"
OMEGA_CANARY_DISPLAY_NAME = "DmYTB"
OMEGA_CANARY_PLATFORM_ACCOUNT_ID = UUID("ad2fe73d-4214-43a7-a3f1-3a1255467009")
CANONICAL_CANARY_MARKER = "p17e4_canonical_private_canary"


class CanonicalCanaryError(ValueError):
    """Raised when canonical private-canary construction or identity is invalid."""


@dataclass(frozen=True)
class CanonicalCanaryGraph:
    """Identifiers produced by the canonical production service composition."""

    mission_id: UUID
    execution_id: UUID
    task_id: UUID
    publish_intent_id: UUID
    schedule_decision_id: UUID
    reservation_id: UUID


class CanonicalCanaryService:
    """Compose and validate one scheduled private canary for the OMEGA channel."""

    @classmethod
    async def construct(
        cls,
        session: AsyncSession,
        *,
        media_artifact_id: UUID,
        scheduled_start_at: datetime,
        title: str,
        description: str = "",
        made_for_kids: bool = False,
        actor: str = "CANONICAL_CANARY",
    ) -> CanonicalCanaryGraph:
        """Create Mission through reservation using existing production services."""
        channel = await session.get(Channel, OMEGA_CANARY_CHANNEL_ID)
        if channel is None:
            raise CanonicalCanaryError("Canonical OMEGA canary channel does not exist.")

        account = await session.get(PlatformAccount, OMEGA_CANARY_PLATFORM_ACCOUNT_ID)
        if account is None:
            raise CanonicalCanaryError("Canonical OMEGA YouTube account does not exist.")
        if (
            account.channel_id != channel.id
            or account.platform.upper() != "YOUTUBE"
            or account.external_account_id != OMEGA_CANARY_YOUTUBE_CHANNEL_ID
            or account.account_display_name != OMEGA_CANARY_DISPLAY_NAME
            or account.status != "ACTIVE"
        ):
            raise CanonicalCanaryError("Canonical OMEGA YouTube account identity is invalid.")

        artifact = await session.get(MediaArtifact, media_artifact_id)
        if artifact is None:
            raise CanonicalCanaryError("Canonical canary MediaArtifact does not exist.")
        production_request = await session.get(ProductionRequest, artifact.production_request_id)
        if production_request is None or production_request.channel_id != channel.id:
            raise CanonicalCanaryError(
                "Canonical canary MediaArtifact does not belong to the OMEGA channel."
            )

        mission_response = await create_mission(
            session,
            MissionCreate(
                title=title,
                objective="Execute one canonical scheduled private YouTube canary.",
                channel_id=channel.id,
                metadata={
                    CANONICAL_CANARY_MARKER: True,
                    "canonical_inputs": {
                        "publish": {
                            "platform_account_id": str(account.id),
                            "title": title,
                            "description": description,
                            "tags": ["omega", "canonical-canary"],
                            "requested_privacy_status": PrivacyStatus.PRIVATE.value,
                            "category_id": "28",
                            "made_for_kids": made_for_kids,
                        }
                    },
                },
            ),
        )
        await plan_mission(session, mission_response.id)

        executions = list(
            (
                await session.execute(
                    select(MissionExecution).where(
                        MissionExecution.mission_id == mission_response.id
                    )
                )
            )
            .scalars()
            .all()
        )
        publish_tasks = list(
            (
                await session.execute(
                    select(Task).where(
                        Task.mission_id == mission_response.id,
                        Task.task_type == "publish",
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(executions) != 1 or len(publish_tasks) != 1:
            raise CanonicalCanaryError(
                "Canonical mission planning must create one execution and one publish task."
            )
        execution = executions[0]
        task = publish_tasks[0]
        if task.execution_id != execution.id or execution.mission_id != task.mission_id:
            raise CanonicalCanaryError("Canonical publish task MissionExecution link is invalid.")

        intent = await PublishIntentService.create_publish_intent(
            session,
            PublishIntentCreate(
                mission_id=mission_response.id,
                task_id=task.id,
                channel_id=channel.id,
                platform_account_id=account.id,
                media_artifact_id=artifact.id,
                media_artifact_checksum=artifact.content_hash,
                channel_dna_revision_id=execution.channel_dna_revision_id,
                title=title,
                description=description,
                tags=["omega", "canonical-canary"],
                requested_privacy_status=PrivacyStatus.PRIVATE,
                category_id="28",
                made_for_kids=made_for_kids,
            ),
            actor=actor,
            initial_state=PublishIntentState.APPROVED,
            commit=False,
        )
        decision, reservation = await PublishCalendarService.schedule_intent(
            session,
            intent_id=intent.id,
            scheduled_start_at=scheduled_start_at,
            actor=actor,
            reason="Canonical private publisher canary",
            now=scheduled_start_at,
        )
        await session.commit()

        return CanonicalCanaryGraph(
            mission_id=mission_response.id,
            execution_id=execution.id,
            task_id=task.id,
            publish_intent_id=intent.id,
            schedule_decision_id=decision.id,
            reservation_id=reservation.id,
        )

    @classmethod
    async def validate_pre_provider_identity(
        cls,
        session: AsyncSession,
        *,
        intent: PublishIntent,
        task: Task,
        mission: Mission,
    ) -> None:
        """Fail closed on identity inconsistency before any external network work."""
        execution = (
            await session.get(MissionExecution, task.execution_id)
            if task.execution_id is not None
            else None
        )
        if (
            task.execution_id is None
            or execution is None
            or task.mission_id != mission.id
            or execution.mission_id != mission.id
            or intent.task_id != task.id
            or intent.mission_id != mission.id
        ):
            raise CanonicalCanaryError(
                "Publish Mission/MissionExecution/Task/PublishIntent identity graph is invalid."
            )

        marker = (mission.metadata_ or {}).get(CANONICAL_CANARY_MARKER)
        if marker is not True:
            return

        if (
            mission.channel_id != OMEGA_CANARY_CHANNEL_ID
            or intent.channel_id != OMEGA_CANARY_CHANNEL_ID
            or intent.platform_account_id != OMEGA_CANARY_PLATFORM_ACCOUNT_ID
            or intent.requested_privacy_status != PrivacyStatus.PRIVATE.value
        ):
            raise CanonicalCanaryError("Canonical canary target or privacy identity is invalid.")

        intents = list(
            (
                await session.execute(
                    select(PublishIntent.id).where(PublishIntent.task_id == task.id)
                )
            )
            .scalars()
            .all()
        )
        if intents != [intent.id]:
            raise CanonicalCanaryError(
                "Canonical canary requires exactly one PublishIntent for its task."
            )

        account = await session.get(PlatformAccount, intent.platform_account_id)
        if (
            account is None
            or account.channel_id != OMEGA_CANARY_CHANNEL_ID
            or account.platform.upper() != "YOUTUBE"
            or account.external_account_id != OMEGA_CANARY_YOUTUBE_CHANNEL_ID
            or account.account_display_name != OMEGA_CANARY_DISPLAY_NAME
        ):
            raise CanonicalCanaryError("Canonical canary provider account identity is invalid.")

        reservations = list(
            (
                await session.execute(
                    select(ScheduleReservation).where(
                        ScheduleReservation.target_type
                        == ScheduleTargetType.PUBLISH_INTENT.value,
                        ScheduleReservation.target_id == intent.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(reservations) != 1:
            raise CanonicalCanaryError(
                "Canonical canary requires exactly one ScheduleReservation."
            )
        reservation = reservations[0]
        decision = await session.get(ScheduleDecision, reservation.decision_id)
        if (
            decision is None
            or decision.id != reservation.decision_id
            or decision.reservation_id != reservation.id
            or decision.mission_id != mission.id
            or decision.task_id != task.id
            or decision.target_type != ScheduleTargetType.PUBLISH_INTENT.value
            or decision.target_id != intent.id
            or reservation.mission_id != mission.id
            or reservation.channel_id != intent.channel_id
        ):
            raise CanonicalCanaryError("Canonical canary schedule identity graph is invalid.")

        outboxes = list(
            (
                await session.execute(
                    select(SchedulerDispatchOutbox).where(
                        SchedulerDispatchOutbox.reservation_id == reservation.id
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(outboxes) != 1:
            raise CanonicalCanaryError(
                "Canonical canary requires exactly one SchedulerDispatchOutbox."
            )
        outbox = outboxes[0]
        if (
            outbox.task_id != task.id
            or outbox.mission_id != mission.id
            or outbox.celery_task_name != "omega.publisher.execute_publish"
            or outbox.celery_args != {"args": [str(task.id)]}
        ):
            raise CanonicalCanaryError("Canonical canary scheduler outbox identity is invalid.")

        attempts = list(
            (
                await session.execute(
                    select(PublishAttempt.id).where(
                        PublishAttempt.publish_intent_id == intent.id
                    )
                )
            )
            .scalars()
            .all()
        )
        upload_sessions = list(
            (
                await session.execute(
                    select(UploadSession.id)
                    .join(
                        PublishAttempt,
                        UploadSession.publish_attempt_id == PublishAttempt.id,
                    )
                    .where(PublishAttempt.publish_intent_id == intent.id)
                )
            )
            .scalars()
            .all()
        )
        if attempts or upload_sessions:
            raise CanonicalCanaryError(
                "Canonical canary already has publisher execution evidence."
            )
