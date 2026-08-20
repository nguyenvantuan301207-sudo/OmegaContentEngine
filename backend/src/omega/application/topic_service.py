"""Topic Intelligence application service.

Orchestrates candidate ingestion, batch imports, dual-context evaluation,
recommendations, idempotent selection/rejection, and TopicMemory persistence.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application import channel_service
from omega.application.duplicate_detector import (
    classify_topic_similarity,
    compute_topic_fingerprint,
    normalize_text,
)
from omega.application.topic_scorer import calculate_topic_scores
from omega.domain.channel import ChannelState
from omega.domain.channel_context import ChannelContext
from omega.domain.channel_dna import ChannelDNA
from omega.domain.topic import (
    DuplicateStatus,
    EvaluationContextMode,
    TopicAngleResponse,
    TopicCandidateBatchImport,
    TopicCandidateCreate,
    TopicCandidateResponse,
    TopicCandidateUpdate,
    TopicMemoryResponse,
    TopicStatus,
)
from omega.domain.topic_scoring import (
    DEFAULT_SCORING_PROFILE,
    DEFAULT_SIMILARITY_PROFILE,
    SimilarityProfile,
    TopicScoringProfile,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    MissionExecution,
    TopicAngle,
    TopicCandidate,
    TopicMemory,
)
from omega.logging import get_logger

logger = get_logger(service="omega-topic-service")


async def _reload_candidate(session: AsyncSession, candidate_id: UUID) -> TopicCandidate:
    """Reload candidate with preloaded angles to avoid expired attribute access."""
    res = await session.execute(
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(TopicCandidate.id == candidate_id)
    )
    return res.scalars().one()


async def ingest_candidate(
    session: AsyncSession,
    channel_id: UUID,
    candidate_in: TopicCandidateCreate,
) -> TopicCandidateResponse:
    """Ingest a single topic candidate and update TopicMemory discovery count."""
    # 1. Verify channel exists and is not ARCHIVED
    chan_res = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = chan_res.scalar_one_or_none()
    if not channel:
        raise ValueError(f"Channel with ID '{channel_id}' not found.")
    if channel.state == ChannelState.ARCHIVED.value:
        raise ValueError(f"Cannot ingest topics for ARCHIVED channel '{channel_id}'.")

    # 2. Check Idempotency Key
    if candidate_in.idempotency_key:
        existing_res = await session.execute(
            select(TopicCandidate)
            .options(selectinload(TopicCandidate.angles))
            .where(
                TopicCandidate.channel_id == channel_id,
                TopicCandidate.idempotency_key == candidate_in.idempotency_key,
            )
        )
        existing_cand = existing_res.scalar_one_or_none()
        if existing_cand:
            logger.info("Candidate already exists with idempotency key", idempotency_key=candidate_in.idempotency_key)
            return _to_candidate_response(existing_cand)

    # 3. Compute normalized title and versioned fingerprint
    norm_title = normalize_text(candidate_in.title)
    if not norm_title:
        raise ValueError("Candidate title must contain alphanumeric characters.")

    fingerprint = compute_topic_fingerprint(
        normalized_title=norm_title,
        language=candidate_in.language,
        entities=candidate_in.entities,
        keywords=candidate_in.keywords,
    )

    # 4. Create TopicCandidate model
    candidate_id = uuid.uuid4()
    candidate = TopicCandidate(
        id=candidate_id,
        channel_id=channel_id,
        title=candidate_in.title.strip(),
        normalized_title=norm_title,
        summary=candidate_in.summary.strip() if candidate_in.summary else None,
        source_type=candidate_in.source_type.value,
        source_name=candidate_in.source_name,
        source_ref=candidate_in.source_ref,
        language=candidate_in.language,
        region=candidate_in.region,
        entities=candidate_in.entities,
        keywords=candidate_in.keywords,
        tags=candidate_in.tags,
        topic_fingerprint=fingerprint,
        status=TopicStatus.DISCOVERED.value,
        duplicate_status=DuplicateStatus.FRESH_TOPIC.value,
        reasons=[],
        score_breakdown={},
        idempotency_key=candidate_in.idempotency_key,
        metadata_=candidate_in.metadata,
    )

    # Add angles
    for angle_in in candidate_in.angles:
        norm_ang = normalize_text(angle_in.angle)
        candidate.angles.append(
            TopicAngle(
                id=uuid.uuid4(),
                candidate_id=candidate_id,
                angle=angle_in.angle.strip(),
                normalized_angle=norm_ang,
                hook=angle_in.hook.strip() if angle_in.hook else None,
                audience_intent=angle_in.audience_intent.strip() if angle_in.audience_intent else None,
                format_hint=angle_in.format_hint.strip() if angle_in.format_hint else None,
            )
        )

    session.add(candidate)

    # 5. TopicMemory Discovery Update
    mem_res = await session.execute(
        select(TopicMemory).where(
            TopicMemory.channel_id == channel_id,
            TopicMemory.topic_fingerprint == fingerprint,
        )
    )
    existing_mem = mem_res.scalar_one_or_none()
    if existing_mem:
        # Increment discovery count for a genuinely new candidate discovery event
        existing_mem.times_discovered += 1
        existing_mem.last_seen_at = datetime.now(UTC)
    else:
        # Create initial TopicMemory record
        memory_id = uuid.uuid4()
        new_mem = TopicMemory(
            id=memory_id,
            channel_id=channel_id,
            canonical_topic=candidate_in.title.strip(),
            normalized_topic=norm_title,
            topic_fingerprint=fingerprint,
            entities=candidate_in.entities,
            keywords=candidate_in.keywords,
            semantic_tags=candidate_in.tags,
            times_discovered=1,
            times_selected=0,
            times_produced=0,
            times_rejected=0,
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            metadata_={},
        )
        session.add(new_mem)

    await session.flush()
    await session.commit()

    loaded_cand = await _reload_candidate(session, candidate_id)

    logger.info(
        "Candidate ingested successfully",
        candidate_id=str(candidate_id),
        channel_id=str(channel_id),
        title=candidate_in.title,
    )
    return _to_candidate_response(loaded_cand)


async def batch_ingest_candidates(
    session: AsyncSession,
    channel_id: UUID,
    batch_in: TopicCandidateBatchImport,
) -> list[TopicCandidateResponse]:
    """Batch ingest multiple candidates in sequence within an outer transaction."""
    results: list[TopicCandidateResponse] = []
    for cand_in in batch_in.candidates:
        resp = await ingest_candidate(session, channel_id, cand_in)
        results.append(resp)
    return results


async def get_candidate(
    session: AsyncSession,
    candidate_id: UUID,
) -> TopicCandidateResponse | None:
    """Retrieve a single topic candidate by ID."""
    res = await session.execute(
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(TopicCandidate.id == candidate_id)
    )
    cand = res.scalar_one_or_none()
    if not cand:
        return None
    return _to_candidate_response(cand)


async def list_candidates(
    session: AsyncSession,
    channel_id: UUID,
    status: str | None = None,
    source_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TopicCandidateResponse]:
    """List topic candidates with filtering and pagination."""
    stmt = (
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(TopicCandidate.channel_id == channel_id)
        .order_by(TopicCandidate.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(TopicCandidate.status == status)
    if source_type:
        stmt = stmt.where(TopicCandidate.source_type == source_type)

    res = await session.execute(stmt)
    return [_to_candidate_response(c) for c in res.scalars().all()]


async def update_candidate(
    session: AsyncSession,
    candidate_id: UUID,
    update_in: TopicCandidateUpdate,
) -> TopicCandidateResponse | None:
    """Update mutable fields of a topic candidate."""
    res = await session.execute(
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(TopicCandidate.id == candidate_id)
    )
    cand = res.scalar_one_or_none()
    if not cand:
        return None

    if cand.status in (TopicStatus.SELECTED.value, TopicStatus.ARCHIVED.value):
        raise ValueError(f"Cannot update candidate in '{cand.status}' state.")

    if update_in.title is not None:
        cand.title = update_in.title.strip()
        cand.normalized_title = normalize_text(update_in.title)
        cand.topic_fingerprint = compute_topic_fingerprint(
            normalized_title=cand.normalized_title,
            language=cand.language,
            entities=cand.entities,
            keywords=cand.keywords,
        )

    if update_in.summary is not None:
        cand.summary = update_in.summary.strip() if update_in.summary else None
    if update_in.entities is not None:
        cand.entities = update_in.entities
    if update_in.keywords is not None:
        cand.keywords = update_in.keywords
    if update_in.tags is not None:
        cand.tags = update_in.tags
    if update_in.metadata is not None:
        cand.metadata_ = update_in.metadata

    await session.commit()
    loaded_cand = await _reload_candidate(session, candidate_id)
    return _to_candidate_response(loaded_cand)


async def archive_candidate(
    session: AsyncSession,
    candidate_id: UUID,
) -> TopicCandidateResponse | None:
    """Soft-archive a candidate without deleting historical records."""
    res = await session.execute(
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(TopicCandidate.id == candidate_id)
    )
    cand = res.scalar_one_or_none()
    if not cand:
        return None

    cand.status = TopicStatus.ARCHIVED.value
    cand.archived_at = datetime.now(UTC)
    await session.commit()
    logger.info("Candidate soft-archived", candidate_id=str(candidate_id))
    loaded_cand = await _reload_candidate(session, candidate_id)
    return _to_candidate_response(loaded_cand)


async def _resolve_dna_context(
    session: AsyncSession,
    channel_id: UUID,
    mode: EvaluationContextMode,
    mission_execution_id: UUID | None = None,
) -> ChannelContext:
    """Resolve ChannelContext according to strict context modes."""
    if mode == EvaluationContextMode.MISSION_EXECUTION:
        if not mission_execution_id:
            raise ValueError("mission_execution_id is required when mode is MISSION_EXECUTION.")

        exec_res = await session.execute(
            select(MissionExecution)
            .options(selectinload(MissionExecution.mission))
            .where(MissionExecution.id == mission_execution_id)
        )
        execution = exec_res.scalar_one_or_none()
        if not execution:
            raise ValueError(f"MissionExecution '{mission_execution_id}' not found.")

        if execution.mission.channel_id != channel_id:
            raise ValueError(
                f"MissionExecution '{mission_execution_id}' belongs to channel '{execution.mission.channel_id}', not target channel '{channel_id}'."
            )

        if not execution.channel_dna_revision_id:
            raise ValueError(
                f"MissionExecution '{mission_execution_id}' does not have a pinned ChannelDNARevision."
            )

        rev_res = await session.execute(
            select(ChannelDNARevision).where(ChannelDNARevision.id == execution.channel_dna_revision_id)
        )
        revision = rev_res.scalar_one_or_none()
        if not revision:
            raise ValueError(f"Pinned ChannelDNARevision '{execution.channel_dna_revision_id}' not found.")

        chan_res = await session.execute(select(Channel).where(Channel.id == channel_id))
        channel = chan_res.scalar_one_or_none()
        if not channel:
            raise ValueError(f"Channel '{channel_id}' not found.")

        parsed_dna = ChannelDNA.model_validate(revision.snapshot)
        return ChannelContext(
            channel_id=channel.id,
            name=channel.name,
            slug=channel.slug,
            platform=channel.platform,  # type: ignore[arg-type]
            state=channel.state,  # type: ignore[arg-type]
            primary_language=channel.primary_language,
            target_region=channel.target_region,
            timezone=channel.timezone,
            dna=parsed_dna,
            active_dna_version=revision.version,
        )

    # INTERACTIVE mode: resolve current active ChannelContext
    ctx = await channel_service.get_channel_context(session, channel_id)
    if not ctx:
        raise ValueError(f"Channel '{channel_id}' not found.")
    return ctx


async def evaluate_candidate(
    session: AsyncSession,
    candidate_id: UUID,
    mode: EvaluationContextMode = EvaluationContextMode.INTERACTIVE,
    mission_execution_id: UUID | None = None,
    profile: TopicScoringProfile = DEFAULT_SCORING_PROFILE,
    sim_profile: SimilarityProfile = DEFAULT_SIMILARITY_PROFILE,
) -> TopicCandidateResponse:
    """Evaluate a single topic candidate against Channel DNA and TopicMemory."""
    # 1. Fetch Candidate
    res = await session.execute(
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(TopicCandidate.id == candidate_id)
    )
    cand = res.scalar_one_or_none()
    if not cand:
        raise ValueError(f"Candidate with ID '{candidate_id}' not found.")

    if cand.status == TopicStatus.ARCHIVED.value:
        raise ValueError(f"Cannot evaluate ARCHIVED candidate '{candidate_id}'.")

    # 2. Resolve Context
    ctx = await _resolve_dna_context(
        session=session,
        channel_id=cand.channel_id,
        mode=mode,
        mission_execution_id=mission_execution_id,
    )

    # 3. Load all TopicMemory records for this channel
    mem_res = await session.execute(
        select(TopicMemory)
        .options(selectinload(TopicMemory.angles))
        .where(TopicMemory.channel_id == cand.channel_id)
    )
    memory_records = mem_res.scalars().all()

    # 4. Find most similar TopicMemory record
    best_status = DuplicateStatus.FRESH_TOPIC
    best_sim = 0.0
    matched_memory_id: UUID | None = None
    matched_memory: TopicMemory | None = None

    for mem in memory_records:
        # Ignore self-memory if this memory record was created solely by this candidate
        # and has never been discovered by another source or selected/produced
        if (
            mem.topic_fingerprint == cand.topic_fingerprint
            and mem.times_discovered <= 1
            and mem.times_selected == 0
            and mem.times_produced == 0
        ):
            continue

        mem_angles_list = [
            {"angle": a.angle, "normalized_angle": a.normalized_angle}
            for a in mem.angles
        ]
        cand_angles_list = [
            {"angle": a.angle, "normalized_angle": a.normalized_angle}
            for a in cand.angles
        ]

        status, sim = classify_topic_similarity(
            candidate_norm_title=cand.normalized_title,
            candidate_entities=cand.entities,
            candidate_keywords=cand.keywords,
            candidate_fingerprint=cand.topic_fingerprint,
            candidate_angles=cand_angles_list,
            memory_norm_title=mem.normalized_topic,
            memory_entities=mem.entities,
            memory_keywords=mem.keywords,
            memory_fingerprint=mem.topic_fingerprint,
            memory_angles=mem_angles_list,
            profile=sim_profile,
        )

        if sim > best_sim:
            best_sim = sim
            best_status = status
            matched_memory_id = mem.id
            matched_memory = mem

    cand.duplicate_status = best_status.value
    cand.similarity_score = round(best_sim, 3) if best_sim > 0 else None
    cand.similar_memory_id = matched_memory_id

    # 5. Compute Explainable Scores
    scores = calculate_topic_scores(
        candidate=cand,
        channel_context=ctx,
        duplicate_status=best_status,
        similar_memory=matched_memory,
        memory_records=memory_records,
        profile=profile,
    )

    cand.audience_fit_score = scores["audience_fit_score"]
    cand.strategic_fit_score = scores["strategic_fit_score"]
    cand.trend_score = scores["trend_score"]
    cand.novelty_score = scores["novelty_score"]
    cand.content_gap_score = scores["content_gap_score"]
    cand.historical_performance_score = scores["historical_performance_score"]
    cand.cost_efficiency_score = scores["cost_efficiency_score"]
    cand.revenue_potential_score = scores["revenue_potential_score"]
    cand.final_score = scores["final_score"]
    cand.score_breakdown = scores["score_breakdown"]
    cand.reasons = scores["reasons"]
    cand.evaluated_at = datetime.now(UTC)

    # 6. Status transition
    if cand.final_score >= profile.minimum_recommendation_score and cand.status not in (
        TopicStatus.SELECTED.value,
        TopicStatus.REJECTED.value,
    ):
        cand.status = TopicStatus.RECOMMENDED.value
    elif cand.status not in (TopicStatus.SELECTED.value, TopicStatus.REJECTED.value):
        cand.status = TopicStatus.EVALUATED.value

    # Update memory last_evaluation_score if matched
    if matched_memory:
        matched_memory.last_evaluation_score = cand.final_score

    await session.commit()

    loaded_cand = await _reload_candidate(session, candidate_id)

    logger.info(
        "Candidate evaluated",
        candidate_id=str(candidate_id),
        final_score=loaded_cand.final_score,
        status=loaded_cand.status,
        duplicate_status=loaded_cand.duplicate_status,
    )
    return _to_candidate_response(loaded_cand)


async def evaluate_batch(
    session: AsyncSession,
    channel_id: UUID,
    mode: EvaluationContextMode = EvaluationContextMode.INTERACTIVE,
    mission_execution_id: UUID | None = None,
    profile: TopicScoringProfile = DEFAULT_SCORING_PROFILE,
) -> list[TopicCandidateResponse]:
    """Evaluate all pending DISCOVERED topic candidates for a channel."""
    res = await session.execute(
        select(TopicCandidate.id).where(
            TopicCandidate.channel_id == channel_id,
            TopicCandidate.status == TopicStatus.DISCOVERED.value,
        )
    )
    candidate_ids = res.scalars().all()
    results: list[TopicCandidateResponse] = []
    for cid in candidate_ids:
        eval_resp = await evaluate_candidate(
            session=session,
            candidate_id=cid,
            mode=mode,
            mission_execution_id=mission_execution_id,
            profile=profile,
        )
        results.append(eval_resp)
    return results


async def list_recommendations(
    session: AsyncSession,
    channel_id: UUID,
    min_score: float = 60.0,
    limit: int = 10,
) -> list[TopicCandidateResponse]:
    """Retrieve top-ranked recommended topic candidates."""
    res = await session.execute(
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(
            TopicCandidate.channel_id == channel_id,
            TopicCandidate.status.in_([TopicStatus.RECOMMENDED.value, TopicStatus.EVALUATED.value]),
            TopicCandidate.final_score >= min_score,
        )
        .order_by(TopicCandidate.final_score.desc())
        .limit(limit)
    )
    return [_to_candidate_response(c) for c in res.scalars().all()]


async def select_candidate(
    session: AsyncSession,
    candidate_id: UUID,
) -> TopicCandidateResponse:
    """Select a candidate for production, updating TopicMemory times_selected exactly once."""
    res = await session.execute(
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(TopicCandidate.id == candidate_id)
        .with_for_update()
    )
    cand = res.scalar_one_or_none()
    if not cand:
        raise ValueError(f"Candidate with ID '{candidate_id}' not found.")

    if cand.status == TopicStatus.SELECTED.value:
        # Idempotent no-op
        return _to_candidate_response(cand)

    if cand.status in (TopicStatus.ARCHIVED.value, TopicStatus.REJECTED.value):
        raise ValueError(f"Cannot select candidate in '{cand.status}' state.")

    cand.status = TopicStatus.SELECTED.value

    # Update TopicMemory times_selected
    mem_res = await session.execute(
        select(TopicMemory)
        .where(
            TopicMemory.channel_id == cand.channel_id,
            TopicMemory.topic_fingerprint == cand.topic_fingerprint,
        )
        .with_for_update()
    )
    mem = mem_res.scalar_one_or_none()
    if mem:
        mem.times_selected += 1
        mem.last_selected_at = datetime.now(UTC)
    else:
        new_mem = TopicMemory(
            id=uuid.uuid4(),
            channel_id=cand.channel_id,
            canonical_topic=cand.title,
            normalized_topic=cand.normalized_title,
            topic_fingerprint=cand.topic_fingerprint,
            entities=cand.entities,
            keywords=cand.keywords,
            semantic_tags=cand.tags,
            times_discovered=1,
            times_selected=1,
            times_produced=0,
            times_rejected=0,
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            last_selected_at=datetime.now(UTC),
            last_evaluation_score=cand.final_score,
            metadata_={},
        )
        session.add(new_mem)

    await session.commit()
    loaded_cand = await _reload_candidate(session, candidate_id)
    logger.info("Candidate selected for production", candidate_id=str(candidate_id))
    return _to_candidate_response(loaded_cand)


async def reject_candidate(
    session: AsyncSession,
    candidate_id: UUID,
    reason: str,
) -> TopicCandidateResponse:
    """Reject a candidate, updating TopicMemory times_rejected exactly once."""
    res = await session.execute(
        select(TopicCandidate)
        .options(selectinload(TopicCandidate.angles))
        .where(TopicCandidate.id == candidate_id)
        .with_for_update()
    )
    cand = res.scalar_one_or_none()
    if not cand:
        raise ValueError(f"Candidate with ID '{candidate_id}' not found.")

    if cand.status == TopicStatus.REJECTED.value:
        # Idempotent no-op
        return _to_candidate_response(cand)

    if cand.status == TopicStatus.ARCHIVED.value:
        raise ValueError("Cannot reject ARCHIVED candidate.")

    cand.status = TopicStatus.REJECTED.value
    cand.reasons = list(cand.reasons) + [f"REJECTED: {reason}"]

    # Update TopicMemory times_rejected
    mem_res = await session.execute(
        select(TopicMemory)
        .where(
            TopicMemory.channel_id == cand.channel_id,
            TopicMemory.topic_fingerprint == cand.topic_fingerprint,
        )
        .with_for_update()
    )
    mem = mem_res.scalar_one_or_none()
    if mem:
        mem.times_rejected += 1
        mem.last_rejected_at = datetime.now(UTC)
    else:
        new_mem = TopicMemory(
            id=uuid.uuid4(),
            channel_id=cand.channel_id,
            canonical_topic=cand.title,
            normalized_topic=cand.normalized_title,
            topic_fingerprint=cand.topic_fingerprint,
            entities=cand.entities,
            keywords=cand.keywords,
            semantic_tags=cand.tags,
            times_discovered=1,
            times_selected=0,
            times_produced=0,
            times_rejected=1,
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            last_rejected_at=datetime.now(UTC),
            last_evaluation_score=cand.final_score,
            metadata_={},
        )
        session.add(new_mem)

    await session.commit()
    loaded_cand = await _reload_candidate(session, candidate_id)
    logger.info("Candidate rejected", candidate_id=str(candidate_id), reason=reason)
    return _to_candidate_response(loaded_cand)


async def list_topic_memory(
    session: AsyncSession,
    channel_id: UUID,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TopicMemoryResponse]:
    """List TopicMemory records for a Channel."""
    stmt = (
        select(TopicMemory)
        .options(selectinload(TopicMemory.angles))
        .where(TopicMemory.channel_id == channel_id)
        .order_by(TopicMemory.last_seen_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if search:
        s_norm = f"%{normalize_text(search)}%"
        stmt = stmt.where(TopicMemory.normalized_topic.like(s_norm))

    res = await session.execute(stmt)
    return [_to_memory_response(m) for m in res.scalars().all()]


async def get_topic_memory(
    session: AsyncSession,
    memory_id: UUID,
) -> TopicMemoryResponse | None:
    """Get single TopicMemory record by ID."""
    res = await session.execute(
        select(TopicMemory)
        .options(selectinload(TopicMemory.angles))
        .where(TopicMemory.id == memory_id)
    )
    mem = res.scalar_one_or_none()
    if not mem:
        return None
    return _to_memory_response(mem)


def _to_candidate_response(cand: TopicCandidate) -> TopicCandidateResponse:
    """Convert TopicCandidate ORM to Pydantic Response."""
    angles_resp = [
        TopicAngleResponse(
            id=a.id,
            candidate_id=a.candidate_id,
            memory_id=a.memory_id,
            angle=a.angle,
            normalized_angle=a.normalized_angle,
            hook=a.hook,
            audience_intent=a.audience_intent,
            format_hint=a.format_hint,
            created_at=a.created_at,
        )
        for a in (cand.angles or [])
    ]

    return TopicCandidateResponse(
        id=cand.id,
        channel_id=cand.channel_id,
        title=cand.title,
        normalized_title=cand.normalized_title,
        summary=cand.summary,
        source_type=cand.source_type,  # type: ignore[arg-type]
        source_name=cand.source_name,
        source_ref=cand.source_ref,
        language=cand.language,
        region=cand.region,
        entities=list(cand.entities or []),
        keywords=list(cand.keywords or []),
        tags=list(cand.tags or []),
        topic_fingerprint=cand.topic_fingerprint,
        audience_fit_score=cand.audience_fit_score,
        strategic_fit_score=cand.strategic_fit_score,
        trend_score=cand.trend_score,
        novelty_score=cand.novelty_score,
        content_gap_score=cand.content_gap_score,
        historical_performance_score=cand.historical_performance_score,
        cost_efficiency_score=cand.cost_efficiency_score,
        revenue_potential_score=cand.revenue_potential_score,
        final_score=cand.final_score,
        duplicate_status=cand.duplicate_status,  # type: ignore[arg-type]
        similar_memory_id=cand.similar_memory_id,
        similarity_score=cand.similarity_score,
        status=cand.status,  # type: ignore[arg-type]
        reasons=list(cand.reasons or []),
        score_breakdown=dict(cand.score_breakdown or {}),
        angles=angles_resp,
        idempotency_key=cand.idempotency_key,
        metadata_=dict(cand.metadata_ or {}),
        created_at=cand.created_at,
        updated_at=cand.updated_at,
        evaluated_at=cand.evaluated_at,
        archived_at=cand.archived_at,
    )


def _to_memory_response(mem: TopicMemory) -> TopicMemoryResponse:
    """Convert TopicMemory ORM to Pydantic Response."""
    angles_resp = [
        TopicAngleResponse(
            id=a.id,
            candidate_id=a.candidate_id,
            memory_id=a.memory_id,
            angle=a.angle,
            normalized_angle=a.normalized_angle,
            hook=a.hook,
            audience_intent=a.audience_intent,
            format_hint=a.format_hint,
            created_at=a.created_at,
        )
        for a in (mem.angles or [])
    ]

    return TopicMemoryResponse(
        id=mem.id,
        channel_id=mem.channel_id,
        canonical_topic=mem.canonical_topic,
        normalized_topic=mem.normalized_topic,
        topic_fingerprint=mem.topic_fingerprint,
        entities=list(mem.entities or []),
        keywords=list(mem.keywords or []),
        semantic_tags=list(mem.semantic_tags or []),
        first_seen_at=mem.first_seen_at,
        last_seen_at=mem.last_seen_at,
        times_discovered=mem.times_discovered,
        times_selected=mem.times_selected,
        times_produced=mem.times_produced,
        times_rejected=mem.times_rejected,
        last_selected_at=mem.last_selected_at,
        last_produced_at=mem.last_produced_at,
        last_rejected_at=mem.last_rejected_at,
        last_evaluation_score=mem.last_evaluation_score,
        angles=angles_resp,
        metadata_=dict(mem.metadata_ or {}),
    )
