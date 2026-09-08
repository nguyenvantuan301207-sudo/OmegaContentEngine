"""Read-only channel context service for OMEGA-013 / P13 autonomous production loop.

Provides a stable, bounded, channel-scoped read contract returning:
- RECENT_PERFORMANCE: latest finalized/revised learning input snapshots per content item.
- ACTIVE_KNOWLEDGE: current active knowledge claims for the channel.

No mutations are performed by this service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.learning import KnowledgeStatus
from omega.infrastructure.models import (
    LearningInputLatestPointer,
    LearningInputSnapshot,
    LearningKnowledgeItem,
    LearningKnowledgeLatestPointer,
)

# Bounded result caps — P13 must not run unbounded channel history queries.
RECENT_PERFORMANCE_DEFAULT_LIMIT: int = 50
ACTIVE_KNOWLEDGE_DEFAULT_LIMIT: int = 200


@dataclass(frozen=True)
class RecentPerformanceRecord:
    """One finalized/revised analytics observation window ingested into Learning.

    All IDs are preserved to allow P13 to trace each record back to durable evidence:
    snapshot → input observation → analytics window → publish intent → content.
    """

    # Snapshot identity
    snapshot_id: UUID
    observation_id: UUID
    revision_sequence: int
    payload_checksum: str

    # Content / publish lineage
    publish_intent_id: UUID
    provider_video_id: str
    media_artifact_id: UUID
    channel_dna_revision_id: UUID | None

    # Temporal
    published_at_utc: datetime
    window_type: str
    window_state: str
    window_start_utc: datetime
    window_end_utc: datetime

    # Metrics (may contain None values — no zero fabrication)
    raw_metrics: dict[str, Any]
    metric_qualities: dict[str, Any]
    classifications: dict[str, Any]

    # Data quality
    is_fully_finalized: bool
    quality_flags: list[str]

    # Ingestion timestamp for ordering tie-breaking
    ingested_at: datetime


@dataclass(frozen=True)
class ActiveKnowledgeRecord:
    """Current active knowledge claim for the channel.

    All source IDs are preserved to allow P13 to trace each claim back to its
    hypothesis, evaluation, and input evidence.
    """

    # Family / pointer identity
    knowledge_family_id: UUID
    current_status: str
    current_revision_number: int
    status_reason: str | None
    pointer_updated_at: datetime

    # Immutable knowledge item fields
    knowledge_item_id: UUID
    knowledge_type: str
    structured_claim: dict[str, Any]
    human_readable_summary: str
    evidence_type: str
    confidence_class: str
    effect_size_absolute: float
    effect_size_relative_percent: float | None
    cliffs_delta: float
    sample_size_treatment: int
    sample_size_control: int

    # Audit source IDs — for P13 traceability
    source_hypothesis_id: UUID
    source_evaluation_id: UUID

    revision_number: int
    supersedes_id: UUID | None


@dataclass(frozen=True)
class ChannelContext:
    """Complete read-only context snapshot for one channel, ready for P13 consumption."""

    channel_id: UUID
    recent_performance: list[RecentPerformanceRecord] = field(default_factory=list)
    active_knowledge: list[ActiveKnowledgeRecord] = field(default_factory=list)


class ChannelContextService:
    """Read-only service providing P13 with bounded, channel-scoped evidence context.

    RECENT_PERFORMANCE: Latest finalized/revised LearningInputSnapshot per observation,
    ordered by publication recency (most recent first), bounded.

    ACTIVE_KNOWLEDGE: Current ACTIVE LearningKnowledgeLatestPointer records for the
    channel, joined to the current immutable LearningKnowledgeItem revision.
    Superseded, stale, weakened, and retracted knowledge are excluded.

    No mutations are performed.
    """

    @classmethod
    async def get_channel_context(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        recent_performance_limit: int = RECENT_PERFORMANCE_DEFAULT_LIMIT,
        active_knowledge_limit: int = ACTIVE_KNOWLEDGE_DEFAULT_LIMIT,
    ) -> ChannelContext:
        """Return bounded channel context for P13 consumption.

        Args:
            session: Async DB session (read-only usage).
            channel_id: Target channel UUID.
            recent_performance_limit: Max recent performance records to return.
            active_knowledge_limit: Max active knowledge records to return.

        Returns:
            ChannelContext with RECENT_PERFORMANCE and ACTIVE_KNOWLEDGE sections.
        """
        recent_performance = await cls._get_recent_performance(
            session, channel_id, recent_performance_limit
        )
        active_knowledge = await cls._get_active_knowledge(
            session, channel_id, active_knowledge_limit
        )
        return ChannelContext(
            channel_id=channel_id,
            recent_performance=recent_performance,
            active_knowledge=active_knowledge,
        )

    @classmethod
    async def _get_recent_performance(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        limit: int,
    ) -> list[RecentPerformanceRecord]:
        """Return the latest LearningInputSnapshot per observation/window_type,
        ordered by published_at_utc DESC then ingested_at DESC, bounded.

        Fetches current snapshot IDs from LearningInputLatestPointer (which always
        tracks the latest revision), then hydrates the actual snapshot rows.
        """
        # 1. Select latest pointer IDs for this channel, ordered by publication time.
        stmt_ptrs = (
            select(LearningInputLatestPointer)
            .where(LearningInputLatestPointer.channel_id == channel_id)
            .order_by(
                LearningInputLatestPointer.updated_at.desc(),
            )
            .limit(limit)
        )
        pointers = (await session.execute(stmt_ptrs)).scalars().all()

        if not pointers:
            return []

        snapshot_ids = [p.current_input_snapshot_id for p in pointers]

        # 2. Hydrate snapshots in one IN query, then re-order by published_at DESC.
        stmt_snaps = (
            select(LearningInputSnapshot)
            .where(LearningInputSnapshot.id.in_(snapshot_ids))
            .order_by(
                LearningInputSnapshot.published_at_utc.desc(),
                LearningInputSnapshot.ingested_at.desc(),
            )
        )
        snapshots = (await session.execute(stmt_snaps)).scalars().all()

        return [
            RecentPerformanceRecord(
                snapshot_id=snap.id,
                observation_id=snap.observation_id,
                revision_sequence=snap.revision_sequence,
                payload_checksum=snap.payload_checksum,
                publish_intent_id=snap.publish_intent_id,
                provider_video_id=snap.provider_video_id,
                media_artifact_id=snap.media_artifact_id,
                channel_dna_revision_id=snap.channel_dna_revision_id,
                published_at_utc=snap.published_at_utc,
                window_type=snap.window_type,
                window_state=snap.window_state,
                window_start_utc=snap.window_start_utc,
                window_end_utc=snap.window_end_utc,
                raw_metrics=snap.raw_metrics,
                metric_qualities=snap.metric_qualities,
                classifications=snap.classifications,
                is_fully_finalized=snap.is_fully_finalized,
                quality_flags=snap.quality_flags or [],
                ingested_at=snap.ingested_at,
            )
            for snap in snapshots
        ]

    @classmethod
    async def _get_active_knowledge(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        limit: int,
    ) -> list[ActiveKnowledgeRecord]:
        """Return current ACTIVE LearningKnowledgeLatestPointer records for the channel,
        joined to the current immutable LearningKnowledgeItem revision.

        Superseded, stale, weakened, and retracted knowledge are excluded.
        """
        stmt = (
            select(LearningKnowledgeLatestPointer, LearningKnowledgeItem)
            .join(
                LearningKnowledgeItem,
                LearningKnowledgeItem.id
                == LearningKnowledgeLatestPointer.current_knowledge_item_id,
            )
            .where(
                LearningKnowledgeLatestPointer.channel_id == channel_id,
                LearningKnowledgeLatestPointer.current_status == KnowledgeStatus.ACTIVE.value,
            )
            .order_by(LearningKnowledgeLatestPointer.updated_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()

        return [
            ActiveKnowledgeRecord(
                knowledge_family_id=pointer.knowledge_family_id,
                current_status=pointer.current_status,
                current_revision_number=pointer.current_revision_number,
                status_reason=pointer.status_reason,
                pointer_updated_at=pointer.updated_at,
                knowledge_item_id=item.id,
                knowledge_type=item.knowledge_type,
                structured_claim=item.structured_claim,
                human_readable_summary=item.human_readable_summary,
                evidence_type=item.evidence_type,
                confidence_class=item.confidence_class,
                effect_size_absolute=item.effect_size_absolute,
                effect_size_relative_percent=item.effect_size_relative_percent,
                cliffs_delta=item.cliffs_delta,
                sample_size_treatment=item.sample_size_treatment,
                sample_size_control=item.sample_size_control,
                source_hypothesis_id=item.source_hypothesis_id,
                source_evaluation_id=item.source_evaluation_id,
                revision_number=item.revision_number,
                supersedes_id=item.supersedes_id,
            )
            for pointer, item in rows
        ]
