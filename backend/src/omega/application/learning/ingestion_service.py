"""Ingestion service for OMEGA-013 Learning Engine."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.learning_service import LearningExportService
from omega.application.learning.advisory_locks import acquire_advisory_lock
from omega.domain.analytics import (
    LearningObservation,
    WindowState,
    compute_learning_observation_checksum,
)
from omega.domain.learning import (
    LearningEventType,
    compute_event_dedupe_key,
    compute_input_dedupe_key,
)
from omega.infrastructure.models import (
    LearningEvent,
    LearningIngestionCursor,
    LearningInputLatestPointer,
    LearningInputSnapshot,
)


class LearningIngestionService:
    """Consumes and persists finalized OMEGA-012 learning observations with concurrency safety."""

    @classmethod
    async def ingest_observation(
        cls, session: AsyncSession, observation: LearningObservation
    ) -> tuple[LearningInputSnapshot, bool]:
        """Ingest a single LearningObservation into an immutable LearningInputSnapshot.

        Returns (snapshot, is_new).
        """
        if observation.observation_schema_version != 1:
            raise ValueError(
                f"Unsupported observation schema version: {observation.observation_schema_version}"
            )

        payload_checksum = compute_learning_observation_checksum(observation)

        # 1. Acquire transaction-level advisory lock on (observation_id, window_type)
        await acquire_advisory_lock(
            session,
            "learning_input",
            str(observation.observation_id),
            observation.window_type.value,
        )

        # 2. Select latest pointer FOR UPDATE
        stmt = (
            select(LearningInputLatestPointer)
            .where(
                LearningInputLatestPointer.observation_id == observation.observation_id,
                LearningInputLatestPointer.window_type == observation.window_type.value,
            )
            .with_for_update()
        )
        pointer = (await session.execute(stmt)).scalar_one_or_none()

        # 3. Check for exact duplicate delivery
        if pointer is not None and pointer.current_payload_checksum == payload_checksum:
            existing = await session.get(LearningInputSnapshot, pointer.current_input_snapshot_id)
            if existing is not None:
                return existing, False

        # 4. Allocate revision sequence deterministically
        next_rev = 1 if pointer is None else pointer.current_revision_sequence + 1
        preceding_id = None if pointer is None else pointer.current_input_snapshot_id

        # 5. Insert immutable snapshot
        dedupe_key = compute_input_dedupe_key(
            observation.observation_id, observation.window_type, next_rev
        )
        snapshot = LearningInputSnapshot(
            observation_id=observation.observation_id,
            channel_id=observation.channel_id,
            publish_intent_id=observation.publish_intent_id,
            provider_video_id=observation.provider_video_id,
            media_artifact_id=observation.media_artifact_id,
            channel_dna_revision_id=observation.channel_dna_revision_id,
            window_type=observation.window_type.value,
            window_state=observation.window_state.value,
            window_start_utc=observation.window_start_utc,
            window_end_utc=observation.window_end_utc,
            published_at_utc=observation.published_at_utc,
            raw_metrics=observation.metrics,
            metric_qualities={k: v.value for k, v in observation.metric_qualities.items()},
            classifications={k: v.value for k, v in observation.classifications.items()},
            is_fully_finalized=observation.is_fully_finalized,
            quality_flags=observation.quality_flags,
            payload_checksum=payload_checksum,
            input_dedupe_key=dedupe_key,
            revision_sequence=next_rev,
            preceding_snapshot_id=preceding_id,
        )
        session.add(snapshot)
        await session.flush()

        now_utc = datetime.now(UTC)

        # 6. Insert or update latest pointer
        if pointer is None:
            pointer = LearningInputLatestPointer(
                observation_id=observation.observation_id,
                window_type=observation.window_type.value,
                channel_id=observation.channel_id,
                current_input_snapshot_id=snapshot.id,
                current_revision_sequence=1,
                current_payload_checksum=payload_checksum,
                updated_at=now_utc,
            )
            session.add(pointer)
        else:
            pointer.current_input_snapshot_id = snapshot.id
            pointer.current_revision_sequence = next_rev
            pointer.current_payload_checksum = payload_checksum
            pointer.updated_at = now_utc

        await session.flush()

        # 7. Append immutable event
        event_type = (
            LearningEventType.OBSERVATION_INGESTED.value
            if next_rev == 1
            else LearningEventType.INPUT_SUPERSEDED.value
        )
        event_dedupe = compute_event_dedupe_key(
            event_type, "OBSERVATION", snapshot.id, str(next_rev)
        )
        event = LearningEvent(
            event_type=event_type,
            aggregate_type="OBSERVATION",
            aggregate_id=snapshot.id,
            channel_id=snapshot.channel_id,
            source_ids=[str(observation.observation_id)],
            actor="LearningIngestionService",
            payload={
                "revision_sequence": next_rev,
                "window_type": snapshot.window_type,
                "window_state": snapshot.window_state,
                "payload_checksum": payload_checksum,
            },
            event_dedupe_key=event_dedupe,
            occurred_at=now_utc,
        )
        session.add(event)
        await session.flush()

        return snapshot, True

    @classmethod
    async def sweep_and_ingest(
        cls,
        session: AsyncSession,
        consumer_id: str = "omega_learning_default",
        batch_size: int = 50,
    ) -> int:
        """Sweep newly finalized/revised analytics windows and advance cursor high-water mark."""
        # 1. Acquire advisory lock on cursor to serialize first creation and advancements
        await acquire_advisory_lock(session, "learning_cursor", consumer_id)

        # 2. Select cursor row FOR UPDATE
        stmt_cursor = (
            select(LearningIngestionCursor)
            .where(LearningIngestionCursor.consumer_id == consumer_id)
            .with_for_update()
        )
        cursor = (await session.execute(stmt_cursor)).scalar_one_or_none()

        now_utc = datetime.now(UTC)

        # 3. Create cursor row if missing
        if cursor is None:
            cursor = LearningIngestionCursor(
                consumer_id=consumer_id,
                cursor_updated_at_utc=datetime(1970, 1, 1, tzinfo=UTC),
                cursor_window_id=UUID(int=0),
                high_water_mark_sequence=0,
                last_success_at=now_utc,
                updated_at=now_utc,
            )
            session.add(cursor)
            await session.flush()

        # 4. Enumerate candidates using keyset pagination
        candidates = await LearningExportService.enumerate_export_candidates(
            session=session,
            after_updated_at=cursor.cursor_updated_at_utc,
            after_window_id=cursor.cursor_window_id,
            limit=batch_size,
        )

        ingested_count = 0
        for candidate in candidates:
            obs = await LearningExportService.export_observation_contract(
                session=session,
                asset_id=candidate.asset_id,
                window_type=candidate.window_type,
            )
            if obs is not None and obs.window_state in (
                WindowState.FINALIZED,
                WindowState.REVISED,
            ):
                await cls.ingest_observation(session, obs)
                ingested_count += 1

        # 5. Advance cursor atomically
        if candidates:
            last_item = candidates[-1]
            cursor.cursor_updated_at_utc = last_item.updated_at
            cursor.cursor_window_id = last_item.window_id
            cursor.high_water_mark_sequence += len(candidates)
            cursor.last_success_at = now_utc
            cursor.updated_at = now_utc
            await session.flush()

        return ingested_count
