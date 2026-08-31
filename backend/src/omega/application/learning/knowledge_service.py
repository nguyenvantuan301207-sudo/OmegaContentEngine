"""Knowledge memory management service for OMEGA-013 Learning Engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.learning.advisory_locks import acquire_advisory_lock
from omega.domain.learning import (
    ConfidenceClass,
    EvidenceType,
    KnowledgeStatus,
    LearningEventType,
    compute_event_dedupe_key,
    compute_knowledge_key,
)
from omega.infrastructure.models import (
    LearningEvent,
    LearningKnowledgeItem,
    LearningKnowledgeLatestPointer,
)


class KnowledgeService:
    """Manages immutable institutional memory revisions and mutable lifecycle projections."""

    @classmethod
    async def create_or_advance_knowledge(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        knowledge_type: str,
        structured_claim: dict[str, Any],
        human_readable_summary: str,
        confidence_class: ConfidenceClass,
        effect_size_absolute: float,
        effect_size_relative_percent: float | None,
        cliffs_delta: float,
        sample_size_treatment: int,
        sample_size_control: int,
        source_hypothesis_id: UUID,
        source_evaluation_id: UUID,
        resulting_status: KnowledgeStatus = KnowledgeStatus.ACTIVE,
        status_reason: str | None = None,
        knowledge_family_id: UUID | None = None,
        evidence_type: EvidenceType = EvidenceType.OBSERVATIONAL,
    ) -> LearningKnowledgeItem:
        """Persist a new immutable knowledge revision and update the family pointer."""
        family_id = knowledge_family_id or uuid.uuid4()

        # 1. Acquire transaction advisory lock on knowledge family
        await acquire_advisory_lock(session, "learning_knowledge", str(family_id))

        # 2. Select latest pointer FOR UPDATE
        stmt = (
            select(LearningKnowledgeLatestPointer)
            .where(LearningKnowledgeLatestPointer.knowledge_family_id == family_id)
            .with_for_update()
        )
        pointer = (await session.execute(stmt)).scalar_one_or_none()

        next_rev = 1 if pointer is None else pointer.current_revision_number + 1
        supersedes_id = None if pointer is None else pointer.current_knowledge_item_id

        knowledge_key = compute_knowledge_key(family_id, next_rev)

        # 3. Insert immutable knowledge revision
        item = LearningKnowledgeItem(
            knowledge_family_id=family_id,
            channel_id=channel_id,
            knowledge_type=knowledge_type,
            structured_claim=structured_claim,
            human_readable_summary=human_readable_summary,
            evidence_type=evidence_type.value,
            confidence_class=confidence_class.value,
            effect_size_absolute=effect_size_absolute,
            effect_size_relative_percent=effect_size_relative_percent,
            cliffs_delta=cliffs_delta,
            sample_size_treatment=sample_size_treatment,
            sample_size_control=sample_size_control,
            source_hypothesis_id=source_hypothesis_id,
            source_evaluation_id=source_evaluation_id,
            revision_number=next_rev,
            supersedes_id=supersedes_id,
            knowledge_key=knowledge_key,
        )
        session.add(item)
        await session.flush()

        now_utc = datetime.now(UTC)

        # 4. Insert or update pointer
        if pointer is None:
            pointer = LearningKnowledgeLatestPointer(
                knowledge_family_id=family_id,
                channel_id=channel_id,
                current_knowledge_item_id=item.id,
                current_revision_number=1,
                current_status=resulting_status.value,
                status_reason=status_reason,
                updated_at=now_utc,
            )
            session.add(pointer)
        else:
            pointer.current_knowledge_item_id = item.id
            pointer.current_revision_number = next_rev
            pointer.current_status = resulting_status.value
            pointer.status_reason = status_reason
            pointer.updated_at = now_utc

        await session.flush()

        # 5. Append immutable event
        event_type = (
            LearningEventType.KNOWLEDGE_CREATED.value
            if next_rev == 1
            else LearningEventType.KNOWLEDGE_SUPERSEDED.value
        )
        event_dedupe = compute_event_dedupe_key(event_type, "KNOWLEDGE", item.id, str(next_rev))
        event = LearningEvent(
            event_type=event_type,
            aggregate_type="KNOWLEDGE",
            aggregate_id=item.id,
            channel_id=channel_id,
            source_ids=[str(source_evaluation_id)],
            actor="KnowledgeService",
            payload={
                "revision_number": next_rev,
                "confidence": confidence_class.value,
                "status": resulting_status.value,
            },
            event_dedupe_key=event_dedupe,
            occurred_at=now_utc,
        )
        session.add(event)
        await session.flush()

        return item
