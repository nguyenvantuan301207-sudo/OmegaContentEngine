"""Observation service capturing immutable subsystem world states for OMEGA-014."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.guardian_context import build_canonical_guardian_context
from omega.infrastructure.models import (
    AnalyticsWindow,
    AutonomyLoop,
    AutonomyObservationSnapshot,
    ChannelDNARevision,
    ContentGenerationRequest,
    LearningKnowledgeLatestPointer,
    MediaArtifact,
    Mission,
    MissionExecution,
    ProductionRequest,
    PublishIntent,
    ResearchBrief,
    TopicCandidate,
)


class AutonomyObservationService:
    """Service capturing immutable, replayable observation snapshots across subsystems."""

    @classmethod
    async def capture_snapshot(
        cls,
        session: AsyncSession,
        loop_id: UUID,
    ) -> AutonomyObservationSnapshot:
        """Capture an immutable observation snapshot without storing secrets or tokens."""
        stmt_loop = select(AutonomyLoop).where(AutonomyLoop.id == loop_id)
        loop = (await session.execute(stmt_loop)).scalar_one_or_none()
        if not loop:
            raise ValueError(f"AutonomyLoop '{loop_id}' not found.")

        # 1. Mission and Latest Execution
        stmt_m = select(Mission).where(Mission.id == loop.mission_id)
        mission = (await session.execute(stmt_m)).scalar_one()

        stmt_exec = (
            select(MissionExecution)
            .where(MissionExecution.mission_id == loop.mission_id)
            .order_by(desc(MissionExecution.created_at))
            .limit(1)
        )
        latest_exec = (await session.execute(stmt_exec)).scalar_one_or_none()

        # 2. Canonical Guardian Context
        guardian_ctx, guardian_checksum = await build_canonical_guardian_context(
            session=session,
            mission_id=loop.mission_id,
        )

        # 3. Pinned ChannelDNARevision
        stmt_dna = (
            select(ChannelDNARevision)
            .where(ChannelDNARevision.channel_id == loop.channel_id)
            .order_by(desc(ChannelDNARevision.version))
            .limit(1)
        )
        dna_rev = (await session.execute(stmt_dna)).scalar_one_or_none()
        if not dna_rev:
            raise ValueError(f"Channel '{loop.channel_id}' has no ChannelDNARevision records.")

        # 4. Pipeline Inventory (IDs and Statuses only; ZERO secrets)
        stmt_topics = (
            select(TopicCandidate.id)
            .where(
                TopicCandidate.channel_id == loop.channel_id,
                TopicCandidate.status.in_(["APPROVED", "RECOMMENDED", "SELECTED"]),
            )
            .limit(20)
        )
        topic_ids = [str(x) for x in (await session.execute(stmt_topics)).scalars().all()]

        stmt_briefs = (
            select(ResearchBrief.id)
            .where(
                ResearchBrief.channel_id == loop.channel_id,
                ResearchBrief.is_current.is_(True),
            )
            .limit(20)
        )
        brief_ids = [str(x) for x in (await session.execute(stmt_briefs)).scalars().all()]

        stmt_content = (
            select(ContentGenerationRequest.id)
            .where(
                ContentGenerationRequest.channel_id == loop.channel_id,
                ContentGenerationRequest.status.in_(["DRAFT", "RUNNING"]),
            )
            .limit(20)
        )
        content_req_ids = [str(x) for x in (await session.execute(stmt_content)).scalars().all()]

        stmt_prod = (
            select(ProductionRequest.id)
            .where(
                ProductionRequest.channel_id == loop.channel_id,
                ProductionRequest.status.in_(["DRAFT", "PREPARING", "RENDERING"]),
            )
            .limit(20)
        )
        prod_req_ids = [str(x) for x in (await session.execute(stmt_prod)).scalars().all()]

        stmt_art = (
            select(MediaArtifact.id)
            .join(ProductionRequest, MediaArtifact.production_request_id == ProductionRequest.id)
            .where(
                ProductionRequest.channel_id == loop.channel_id,
                MediaArtifact.is_current.is_(True),
            )
            .limit(20)
        )
        artifact_ids = [str(x) for x in (await session.execute(stmt_art)).scalars().all()]

        stmt_pub = (
            select(PublishIntent.id)
            .where(
                PublishIntent.channel_id == loop.channel_id,
                PublishIntent.state.in_(["DRAFT", "APPROVED"]),
            )
            .limit(20)
        )
        publish_intent_ids = [str(x) for x in (await session.execute(stmt_pub)).scalars().all()]

        # 5. Analytics & Learning Telemetry
        stmt_unfinalized = select(func.count(AnalyticsWindow.id)).where(
            AnalyticsWindow.window_state != "FINALIZED"
        )
        unfinalized_windows = (await session.execute(stmt_unfinalized)).scalar() or 0

        stmt_know = (
            select(LearningKnowledgeLatestPointer.current_knowledge_item_id)
            .where(
                LearningKnowledgeLatestPointer.channel_id == loop.channel_id,
                LearningKnowledgeLatestPointer.current_status == "ACTIVE",
            )
            .limit(20)
        )
        learning_item_ids = [str(x) for x in (await session.execute(stmt_know)).scalars().all()]

        # Construct raw observation payload
        raw_obs = {
            "mission_id": str(mission.id),
            "mission_state": mission.state,
            "mission_execution_id": str(latest_exec.id) if latest_exec else None,
            "guardian_epoch": guardian_ctx.guardian_epoch,
            "guardian_gate_state": guardian_ctx.overall_gate_state,
            "guardian_context_checksum": guardian_checksum,
            "dna_revision_id": str(dna_rev.id),
            "dna_version": dna_rev.version,
            "approved_topic_candidate_ids": sorted(topic_ids),
            "current_brief_ids": sorted(brief_ids),
            "pending_content_request_ids": sorted(content_req_ids),
            "active_production_request_ids": sorted(prod_req_ids),
            "ready_media_artifact_ids": sorted(artifact_ids),
            "pending_publish_intent_ids": sorted(publish_intent_ids),
            "unfinalized_analytics_windows": unfinalized_windows,
            "supported_learning_knowledge_ids": sorted(learning_item_ids),
        }

        canonical_json = json.dumps(raw_obs, sort_keys=True, separators=(",", ":"))
        obs_checksum = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        snapshot = AutonomyObservationSnapshot(
            id=uuid4(),
            loop_id=loop.id,
            guardian_context_checksum=guardian_checksum,
            guardian_epoch=guardian_ctx.guardian_epoch,
            dna_revision_id=dna_rev.id,
            raw_observation=raw_obs,
            observation_checksum=obs_checksum,
            captured_at=db_now,
        )
        session.add(snapshot)
        await session.flush()
        return snapshot
