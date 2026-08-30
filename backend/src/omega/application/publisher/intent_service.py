"""Publish Intent Management Service for OMEGA-011 Publisher.

Manages the upstream construction, checksumming, revisioning, and superseding
of immutable PublishIntent contracts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.publisher import (
    PlatformAccountStatus,
    PublishIntentCreate,
    PublishIntentState,
    compute_publish_intent_checksum,
)
from omega.infrastructure.models import (
    MediaArtifact,
    PlatformAccount,
    PublishIntent,
    PublishIntentTransition,
    Task,
)
from omega.logging import get_logger

logger = get_logger(service="omega-publisher-intent-service")


class PublishIntentServiceError(Exception):
    """Base exception for PublishIntent creation and lifecycle failures."""

    pass


class PublishIntentService:
    """Creates, validates, revisions, and audits immutable publication intents."""

    @classmethod
    async def create_publish_intent(
        cls,
        session: AsyncSession,
        payload: PublishIntentCreate,
        *,
        actor: str = "SYSTEM",
    ) -> PublishIntent:
        """Construct or revision an approved PublishIntent snapshot before scheduling."""
        # 1. Validate mandatory audience compliance
        if payload.made_for_kids is None:
            raise PublishIntentServiceError(
                "made_for_kids is mandatory and cannot be None (COPPA compliance)."
            )

        # 2. Verify Task exists
        task_res = await session.execute(select(Task).where(Task.id == payload.task_id))
        task = task_res.scalar_one_or_none()
        if not task:
            raise PublishIntentServiceError(f"Task {payload.task_id} not found.")

        # 3. Verify MediaArtifact exists & checksum matches
        art_res = await session.execute(
            select(MediaArtifact).where(MediaArtifact.id == payload.media_artifact_id)
        )
        artifact = art_res.scalar_one_or_none()
        if not artifact:
            raise PublishIntentServiceError(f"MediaArtifact {payload.media_artifact_id} not found.")
        if artifact.content_hash.lower() != payload.media_artifact_checksum.lower():
            raise PublishIntentServiceError(
                f"MediaArtifact checksum mismatch: DB has '{artifact.content_hash}', payload provided '{payload.media_artifact_checksum}'."
            )

        # 4. Verify PlatformAccount is ACTIVE
        acct_res = await session.execute(
            select(PlatformAccount).where(PlatformAccount.id == payload.platform_account_id)
        )
        account = acct_res.scalar_one_or_none()
        if not account:
            raise PublishIntentServiceError(
                f"PlatformAccount {payload.platform_account_id} not found."
            )
        if account.status != PlatformAccountStatus.ACTIVE.value:
            raise PublishIntentServiceError(
                f"PlatformAccount {account.id} is not ACTIVE (current status: {account.status})."
            )

        # 5. Compute deterministic intent checksum
        intent_checksum = compute_publish_intent_checksum(
            task_id=payload.task_id,
            media_artifact_checksum=payload.media_artifact_checksum,
            channel_dna_revision_id=payload.channel_dna_revision_id,
            platform=account.platform,
            title=payload.title,
            description=payload.description,
            tags=payload.tags,
            requested_privacy_status=payload.requested_privacy_status.value,
            category_id=payload.category_id,
            made_for_kids=payload.made_for_kids,
            platform_custom_options=payload.platform_custom_options,
        )

        # 6. Check for existing active intent on this task
        stmt = (
            select(PublishIntent)
            .where(
                PublishIntent.task_id == payload.task_id,
                PublishIntent.state.in_(
                    [PublishIntentState.APPROVED.value, PublishIntentState.CLAIMED.value]
                ),
            )
            .with_for_update()
        )
        existing_res = await session.execute(stmt)
        existing_intent = existing_res.scalar_one_or_none()

        revision_number = 1
        supersedes_intent_id = None

        if existing_intent:
            if existing_intent.intent_checksum == intent_checksum:
                # Identical intent already active
                return existing_intent

            # Metadata or artifact changed -> Supersede existing intent
            existing_intent.state = PublishIntentState.SUPERSEDED.value
            existing_intent.superseded_at = datetime.now(UTC)
            existing_intent.updated_at = datetime.now(UTC)

            # Record transition for superseded intent
            trans_old = PublishIntentTransition(
                id=uuid4(),
                publish_intent_id=existing_intent.id,
                from_state=PublishIntentState.APPROVED.value,
                to_state=PublishIntentState.SUPERSEDED.value,
                reason="Superseded by new PublishIntent revision due to metadata or artifact change.",
                actor=actor,
            )
            session.add(trans_old)

            revision_number = existing_intent.revision_number + 1
            supersedes_intent_id = existing_intent.id

        # 7. Create new PublishIntent in APPROVED state
        new_intent = PublishIntent(
            id=uuid4(),
            mission_id=payload.mission_id,
            task_id=payload.task_id,
            channel_id=payload.channel_id,
            platform_account_id=payload.platform_account_id,
            media_artifact_id=payload.media_artifact_id,
            media_artifact_checksum=payload.media_artifact_checksum,
            channel_dna_revision_id=payload.channel_dna_revision_id,
            revision_number=revision_number,
            supersedes_intent_id=supersedes_intent_id,
            title=payload.title,
            description=payload.description,
            tags=payload.tags,
            requested_privacy_status=payload.requested_privacy_status.value,
            category_id=payload.category_id,
            made_for_kids=payload.made_for_kids,
            platform_custom_options=payload.platform_custom_options,
            intent_checksum=intent_checksum,
            state=PublishIntentState.APPROVED.value,
        )
        session.add(new_intent)
        await session.flush()

        # 8. Record initial transition
        trans_new = PublishIntentTransition(
            id=uuid4(),
            publish_intent_id=new_intent.id,
            from_state=PublishIntentState.DRAFT.value,
            to_state=PublishIntentState.APPROVED.value,
            reason="PublishIntent created and approved for scheduling.",
            actor=actor,
        )
        session.add(trans_new)

        await session.commit()
        await session.refresh(new_intent)
        logger.info(
            "PublishIntent created and approved",
            intent_id=str(new_intent.id),
            task_id=str(new_intent.task_id),
            revision_number=new_intent.revision_number,
        )
        return new_intent

    @classmethod
    async def cancel_intent(
        cls,
        session: AsyncSession,
        intent_id: UUID,
        reason: str = "User cancelled intent",
        actor: str = "USER",
    ) -> PublishIntent:
        """Cancel an active or approved PublishIntent."""
        stmt = select(PublishIntent).where(PublishIntent.id == intent_id).with_for_update()
        res = await session.execute(stmt)
        intent = res.scalar_one_or_none()
        if not intent:
            raise PublishIntentServiceError(f"PublishIntent {intent_id} not found.")

        old_state = intent.state
        intent.state = PublishIntentState.CANCELLED.value
        intent.updated_at = datetime.now(UTC)

        trans = PublishIntentTransition(
            id=uuid4(),
            publish_intent_id=intent.id,
            from_state=old_state,
            to_state=PublishIntentState.CANCELLED.value,
            reason=reason,
            actor=actor,
        )
        session.add(trans)
        await session.commit()
        await session.refresh(intent)
        return intent
