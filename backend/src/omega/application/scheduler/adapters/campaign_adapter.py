"""Target adapter for Campaign admission recurring schedule execution."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.campaign_admission_service import admit_schedule_occurrence
from omega.logging import get_logger
from omega.infrastructure.models import (
    ContentCampaign,
    ContentCampaignItem,
    RecurringScheduleOccurrence,
    RecurringScheduleVersion,
)

logger = get_logger(service="omega-scheduler-campaign-adapter")


class CampaignScheduleTargetAdapter:
    """Dispatches Campaign item admission atomically for an occurrence."""

    @staticmethod
    async def dispatch(
        session: AsyncSession,
        occurrence: RecurringScheduleOccurrence,
        version: RecurringScheduleVersion,
    ) -> tuple[ContentCampaignItem | None, str]:
        """Admit next item into active Campaign under canonical lock hierarchy.

        Returns (ContentCampaignItem, status_reason).
        """
        # Look up channel_id from target campaign
        campaign_id = version.target_id
        campaign = (
            await session.execute(
                select(ContentCampaign).where(ContentCampaign.id == campaign_id)
            )
        ).scalar_one_or_none()
        if campaign is None:
            return None, "CAMPAIGN_NOT_FOUND"

        channel_id = campaign.channel_id

        # Delegate directly to the canonical admission service which owns locks and single commit
        item, reason = await admit_schedule_occurrence(
            session=session,
            channel_id=channel_id,
            campaign_id=campaign_id,
            occurrence_id=occurrence.id,
        )
        return item, reason
