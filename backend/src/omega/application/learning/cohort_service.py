"""Cohort management and membership service for OMEGA-013 Learning Engine."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.analytics import WindowType
from omega.domain.learning import (
    ContentFormat,
    LearningEventType,
    compute_cohort_member_key,
    compute_event_dedupe_key,
)
from omega.infrastructure.models import (
    LearningCohort,
    LearningCohortMember,
    LearningEvent,
    LearningFeatureSnapshot,
)


class CohortService:
    """Manages channel-local comparison cohorts and video membership."""

    @classmethod
    async def get_or_create_default_cohort(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        content_format: ContentFormat,
        evaluation_window: WindowType = WindowType.FIRST_7D,
    ) -> LearningCohort:
        """Ensure a default channel-local cohort exists for the format and window."""
        slug = f"{content_format.value.lower()}_{evaluation_window.value.lower()}"
        duration_bucket = "<1M" if content_format == ContentFormat.SHORT else "1M-5M"

        stmt = select(LearningCohort).where(
            LearningCohort.channel_id == channel_id,
            LearningCohort.cohort_slug == slug,
        )
        cohort = (await session.execute(stmt)).scalar_one_or_none()
        if cohort is not None:
            return cohort

        cohort = LearningCohort(
            channel_id=channel_id,
            cohort_slug=slug,
            content_format=content_format.value,
            duration_bucket=duration_bucket,
            dna_compatibility_mode="STRICT_REVISION",
            evaluation_window=evaluation_window.value,
            inclusion_criteria={
                "channel_id": str(channel_id),
                "content_format": content_format.value,
                "evaluation_window": evaluation_window.value,
            },
            is_active=True,
        )
        session.add(cohort)
        await session.flush()
        return cohort

    @classmethod
    async def assign_cohort_membership(
        cls, session: AsyncSession, feature_snapshot: LearningFeatureSnapshot
    ) -> list[LearningCohortMember]:
        """Admit a feature snapshot into compatible channel-local cohorts."""
        fmt_str = feature_snapshot.deterministic_features.get("content_format", "LONG_FORM")
        fmt = ContentFormat(fmt_str)

        # Get default cohort
        cohort = await cls.get_or_create_default_cohort(
            session=session,
            channel_id=feature_snapshot.channel_id,
            content_format=fmt,
        )

        member_key = compute_cohort_member_key(cohort.id, feature_snapshot.id)
        stmt_mem = select(LearningCohortMember).where(
            LearningCohortMember.cohort_member_key == member_key
        )
        existing = (await session.execute(stmt_mem)).scalar_one_or_none()
        if existing is not None:
            return [existing]

        member = LearningCohortMember(
            cohort_id=cohort.id,
            feature_snapshot_id=feature_snapshot.id,
            cohort_member_key=member_key,
        )
        session.add(member)
        await session.flush()

        event_dedupe = compute_event_dedupe_key(
            LearningEventType.COHORT_REBUILT.value, "COHORT", cohort.id, member_key
        )
        event = LearningEvent(
            event_type=LearningEventType.COHORT_REBUILT.value,
            aggregate_type="COHORT",
            aggregate_id=cohort.id,
            channel_id=cohort.channel_id,
            source_ids=[str(feature_snapshot.id)],
            actor="CohortService",
            payload={"cohort_slug": cohort.cohort_slug, "admitted_member": str(member.id)},
            event_dedupe_key=event_dedupe,
            occurred_at=member.admitted_at,
        )
        session.add(event)
        await session.flush()

        return [member]
