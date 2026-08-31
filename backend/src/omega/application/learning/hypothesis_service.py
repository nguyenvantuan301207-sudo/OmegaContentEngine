"""Hypothesis management service for OMEGA-013 Learning Engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.learning.advisory_locks import acquire_advisory_lock
from omega.domain.analytics import WindowType
from omega.domain.learning import (
    HypothesisStatus,
    LearningEventType,
    compute_event_dedupe_key,
    compute_hypothesis_definition_checksum,
)
from omega.infrastructure.models import (
    LearningEvent,
    LearningHypothesis,
    LearningHypothesisLatestPointer,
)


class HypothesisService:
    """Manages immutable hypothesis definition versions and mutable lifecycle pointers."""

    @classmethod
    async def propose_or_get_hypothesis(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        cohort_id: UUID,
        hypothesis_slug: str,
        description: str,
        factor_name: str,
        treatment_definition: dict[str, Any],
        control_definition: dict[str, Any],
        target_outcome_metric: str,
        target_evaluation_window: WindowType,
        hypothesis_family_id: UUID | None = None,
    ) -> LearningHypothesis:
        """Create or advance a hypothesis definition with concurrency and revision safety."""
        family_id = hypothesis_family_id or uuid.uuid4()

        # 1. Acquire transaction advisory lock on family identity
        await acquire_advisory_lock(session, "learning_hypothesis", str(family_id))

        def_checksum = compute_hypothesis_definition_checksum(
            channel_id=channel_id,
            cohort_id=cohort_id,
            factor_name=factor_name,
            treatment_definition=treatment_definition,
            control_definition=control_definition,
            target_outcome_metric=target_outcome_metric,
            target_evaluation_window=target_evaluation_window,
        )

        # 2. Select latest pointer FOR UPDATE
        stmt = (
            select(LearningHypothesisLatestPointer)
            .where(LearningHypothesisLatestPointer.hypothesis_family_id == family_id)
            .with_for_update()
        )
        pointer = (await session.execute(stmt)).scalar_one_or_none()

        # 3. Check for identical definition duplicate
        if pointer is not None:
            curr_hyp = await session.get(LearningHypothesis, pointer.current_hypothesis_id)
            if curr_hyp is not None and curr_hyp.definition_checksum == def_checksum:
                return curr_hyp

        # 4. Allocate version
        next_ver = 1 if pointer is None else pointer.current_version + 1
        supersedes_id = None if pointer is None else pointer.current_hypothesis_id

        # 5. Insert immutable hypothesis version
        hyp = LearningHypothesis(
            hypothesis_family_id=family_id,
            hypothesis_version=next_ver,
            channel_id=channel_id,
            cohort_id=cohort_id,
            hypothesis_slug=hypothesis_slug,
            description=description,
            factor_name=factor_name,
            treatment_definition=treatment_definition,
            control_definition=control_definition,
            target_outcome_metric=target_outcome_metric,
            target_evaluation_window=target_evaluation_window.value,
            definition_checksum=def_checksum,
            supersedes_hypothesis_id=supersedes_id,
        )
        session.add(hyp)
        await session.flush()

        now_utc = datetime.now(UTC)

        # 6. Insert or update pointer
        if pointer is None:
            pointer = LearningHypothesisLatestPointer(
                hypothesis_family_id=family_id,
                channel_id=channel_id,
                current_hypothesis_id=hyp.id,
                current_version=1,
                current_status=HypothesisStatus.DRAFT.value,
                updated_at=now_utc,
            )
            session.add(pointer)
        else:
            pointer.current_hypothesis_id = hyp.id
            pointer.current_version = next_ver
            pointer.updated_at = now_utc

        await session.flush()

        # 7. Append immutable event
        event_type = (
            LearningEventType.HYPOTHESIS_PROPOSED.value
            if next_ver == 1
            else LearningEventType.HYPOTHESIS_REVISED.value
        )
        event_dedupe = compute_event_dedupe_key(event_type, "HYPOTHESIS", hyp.id, str(next_ver))
        event = LearningEvent(
            event_type=event_type,
            aggregate_type="HYPOTHESIS",
            aggregate_id=hyp.id,
            channel_id=channel_id,
            source_ids=[str(supersedes_id)] if supersedes_id else [],
            actor="HypothesisService",
            payload={
                "slug": hypothesis_slug,
                "version": next_ver,
                "factor": factor_name,
            },
            event_dedupe_key=event_dedupe,
            occurred_at=now_utc,
        )
        session.add(event)
        await session.flush()

        return hyp
