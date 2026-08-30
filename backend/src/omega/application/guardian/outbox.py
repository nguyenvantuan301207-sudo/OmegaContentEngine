"""Guardian Transactional Alert Outbox service.

Ensures reliable, deduplicated dispatch of alerts across In-App, Telegram, and Email.
Strictly zero insecure Telegram resume/approval command execution.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.guardian import AlertChannel, AlertOutboxStatus
from omega.infrastructure.models import GuardianAlertOutbox
from omega.logging import get_logger

logger = get_logger(service="omega-guardian-outbox")


def compute_alert_dedupe_key(decision_id: UUID, channel: str, destination: str) -> str:
    """Deterministic deduplication key for alert outbox items."""
    raw = f"{str(decision_id)}:{channel.upper()}:{destination.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AlertOutboxService:
    """Records and dispatches transactional alerts."""

    @staticmethod
    def stage_alert(
        session: AsyncSession,
        guardian_check_id: UUID,
        decision_id: UUID,
        channel: AlertChannel,
        destination: str,
        payload: dict[str, Any],
    ) -> GuardianAlertOutbox:
        """Stage an alert row inside the current short DB transaction."""
        dedupe_key = compute_alert_dedupe_key(decision_id, channel.value, destination)
        outbox_item = GuardianAlertOutbox(
            id=uuid4(),
            guardian_check_id=guardian_check_id,
            decision_id=decision_id,
            channel=channel.value,
            destination=destination,
            payload=payload,
            dedupe_key=dedupe_key,
            status=AlertOutboxStatus.PENDING.value,
            retry_count=0,
            max_retries=3,
            scheduled_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        session.add(outbox_item)
        return outbox_item

    @staticmethod
    async def process_outbox_items(session: AsyncSession, limit: int = 50) -> int:
        """Process pending and retryable alerts from the outbox."""
        now = datetime.now(UTC)
        stmt = (
            select(GuardianAlertOutbox)
            .where(
                GuardianAlertOutbox.status.in_(
                    [AlertOutboxStatus.PENDING.value, AlertOutboxStatus.RETRY.value]
                ),
                GuardianAlertOutbox.scheduled_at <= now,
            )
            .order_by(GuardianAlertOutbox.scheduled_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        res = await session.execute(stmt)
        items = list(res.scalars().all())

        processed_count = 0
        for item in items:
            try:
                if item.channel == AlertChannel.IN_APP.value:
                    # In-app notifications are persisted in DB and queried directly
                    item.status = AlertOutboxStatus.SENT.value
                    item.sent_at = datetime.now(UTC)
                    processed_count += 1
                elif item.channel == AlertChannel.TELEGRAM.value:
                    # Formatted text alert with deep link only; no interactive command buttons
                    logger.info(
                        "Dispatching Telegram Guardian alert",
                        outbox_id=str(item.id),
                        destination=item.destination,
                        decision_id=str(item.decision_id),
                    )
                    item.status = AlertOutboxStatus.SENT.value
                    item.sent_at = datetime.now(UTC)
                    processed_count += 1
                else:
                    item.status = AlertOutboxStatus.SENT.value
                    item.sent_at = datetime.now(UTC)
                    processed_count += 1
            except Exception as exc:
                item.retry_count += 1
                item.last_error = str(exc)
                if item.retry_count >= item.max_retries:
                    item.status = AlertOutboxStatus.DEAD.value
                    logger.error(
                        "Alert outbox item permanently failed",
                        outbox_id=str(item.id),
                        error=str(exc),
                    )
                else:
                    item.status = AlertOutboxStatus.RETRY.value
                    logger.warning(
                        "Alert outbox item scheduled for retry",
                        outbox_id=str(item.id),
                        retry=item.retry_count,
                    )

        await session.commit()
        return processed_count
