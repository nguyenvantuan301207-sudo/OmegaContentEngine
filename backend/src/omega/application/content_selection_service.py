"""Canonical deterministic content-selection execution service."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application import topic_service
from omega.domain.channel import ChannelState, Platform
from omega.domain.channel_context import ChannelContext
from omega.domain.channel_dna import ChannelDNA
from omega.domain.content_selection import (
    ContentSelectionFinalize,
    ContentSelectionMode,
    ContentSelectionRunCreate,
    ContentSelectionRunResponse,
    ContentSelectionStatus,
)
from omega.domain.topic import TopicStatus
from omega.domain.topic_scoring import DEFAULT_SCORING_PROFILE, DEFAULT_SIMILARITY_PROFILE
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentSelectionDecision,
    ContentSelectionRun,
    MissionExecution,
    TopicCandidate,
    TopicMemory,
)

CONTENT_SELECTION_POLICY_NAME = "OMEGA_CONTENT_SELECTION"
CONTENT_SELECTION_POLICY_VERSION = 1
ELIGIBLE_CANDIDATE_STATES = (
    TopicStatus.DISCOVERED.value,
    TopicStatus.EVALUATED.value,
    TopicStatus.RECOMMENDED.value,
)
RANKING_TIE_BREAK = "CANDIDATE_UUID_ASC"


class ContentSelectionConflictError(ValueError):
    """Raised when an idempotency key or finalized selection conflicts."""


class ContentSelectionNotFoundError(ValueError):
    """Raised when a channel-scoped selection run does not exist."""


def _canonical_checksum(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def policy_checksum() -> str:
    """Hash stable configuration; version is the compatibility gate for code semantics.

    A scoring implementation change that is not represented by profile data must bump
    CONTENT_SELECTION_POLICY_VERSION. Runtime IDs and timestamps are excluded.
    """
    return _canonical_checksum(
        {
            "policy_name": CONTENT_SELECTION_POLICY_NAME,
            "policy_version": CONTENT_SELECTION_POLICY_VERSION,
            "topic_scoring_profile": DEFAULT_SCORING_PROFILE.model_dump(mode="json"),
            "similarity_profile": DEFAULT_SIMILARITY_PROFILE.model_dump(mode="json"),
            "eligible_candidate_states": sorted(ELIGIBLE_CANDIDATE_STATES),
            "ranking": {"primary": "FINAL_SCORE_DESC", "tie_break": RANKING_TIE_BREAK},
        }
    )


def candidate_set_checksum(
    *,
    candidate_ids: list[UUID],
    channel_dna_revision_id: UUID,
    selection_policy_checksum: str,
    mission_execution_id: UUID | None,
) -> str:
    """Hash the unordered explicit set plus pinned policy, DNA, and trigger identity."""
    return _canonical_checksum(
        {
            "candidate_ids": sorted(str(candidate_id) for candidate_id in candidate_ids),
            "channel_dna_revision_id": str(channel_dna_revision_id),
            "policy_checksum": selection_policy_checksum,
            "trigger": {
                "mode": "MISSION_EXECUTION" if mission_execution_id else "INTERACTIVE",
                "mission_execution_id": str(mission_execution_id) if mission_execution_id else None,
            },
        }
    )


async def _load_run(
    session: AsyncSession,
    run_id: UUID,
    *,
    for_update: bool = False,
) -> ContentSelectionRun | None:
    statement = (
        select(ContentSelectionRun)
        .options(selectinload(ContentSelectionRun.decisions))
        .where(ContentSelectionRun.id == run_id)
    )
    if for_update:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


def _response(run: ContentSelectionRun) -> ContentSelectionRunResponse:
    decision_candidate_ids = [decision.candidate_id for decision in run.decisions]
    if len(decision_candidate_ids) != run.considered_count:
        raise RuntimeError("Selection run considered_count does not match persisted decisions.")
    if decision_candidate_ids.count(run.recommended_candidate_id) != 1:
        raise RuntimeError("Selection run recommendation is not exactly one persisted decision.")
    if (
        run.selected_candidate_id is not None
        and decision_candidate_ids.count(run.selected_candidate_id) != 1
    ):
        raise RuntimeError("Selection run winner is not exactly one persisted decision.")
    return ContentSelectionRunResponse.model_validate(run)


async def _resolve_pinned_context(
    session: AsyncSession,
    channel_id: UUID,
    mission_execution_id: UUID | None,
) -> tuple[ChannelDNARevision, ChannelContext]:
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise ValueError(f"Channel '{channel_id}' not found.")
    if channel.state == ChannelState.ARCHIVED.value:
        raise ValueError("Cannot execute content selection for an archived channel.")

    if mission_execution_id is not None:
        execution = (
            await session.execute(
                select(MissionExecution)
                .options(selectinload(MissionExecution.mission))
                .where(MissionExecution.id == mission_execution_id)
            )
        ).scalar_one_or_none()
        if execution is None:
            raise ValueError(f"MissionExecution '{mission_execution_id}' not found.")
        if execution.mission.channel_id != channel_id:
            raise ValueError("MissionExecution does not belong to the target channel.")
        if execution.channel_dna_revision_id is None:
            raise ValueError("MissionExecution has no pinned ChannelDNARevision.")
        revision = await session.get(ChannelDNARevision, execution.channel_dna_revision_id)
    else:
        revisions = (
            await session.execute(
                select(ChannelDNARevision)
                .where(ChannelDNARevision.channel_id == channel_id)
                .order_by(ChannelDNARevision.version.desc())
            )
        ).scalars().all()
        active_dna = ChannelDNA.model_validate(channel.dna).model_dump(mode="json")
        revision = next(
            (
                candidate_revision
                for candidate_revision in revisions
                if ChannelDNA.model_validate(candidate_revision.snapshot).model_dump(mode="json")
                == active_dna
            ),
            None,
        )

    if revision is None:
        raise ValueError("Pinned ChannelDNARevision not found.")
    if revision.channel_id != channel_id:
        raise ValueError("Pinned ChannelDNARevision does not belong to the target channel.")

    context = ChannelContext(
        channel_id=channel.id,
        name=channel.name,
        slug=channel.slug,
        platform=Platform(channel.platform),
        state=ChannelState(channel.state),
        primary_language=channel.primary_language,
        target_region=channel.target_region,
        timezone=channel.timezone,
        dna=ChannelDNA.model_validate(revision.snapshot),
        active_dna_version=revision.version,
    )
    return revision, context


async def _return_existing_or_conflict(
    session: AsyncSession,
    existing: ContentSelectionRun,
    request: ContentSelectionRunCreate,
) -> ContentSelectionRunResponse:
    expected = candidate_set_checksum(
        candidate_ids=request.candidate_ids,
        channel_dna_revision_id=existing.channel_dna_revision_id,
        selection_policy_checksum=existing.policy_checksum,
        mission_execution_id=request.mission_execution_id,
    )
    if (
        existing.candidate_set_checksum != expected
        or existing.mission_execution_id != request.mission_execution_id
    ):
        raise ContentSelectionConflictError(
            "idempotency_key is already bound to a different canonical selection request"
        )
    loaded = await _load_run(session, existing.id)
    assert loaded is not None
    return _response(loaded)


def _candidate_snapshot(candidate: TopicCandidate) -> dict:
    return {
        "id": str(candidate.id),
        "title": candidate.title,
        "normalized_title": candidate.normalized_title,
        "summary": candidate.summary,
        "source_type": candidate.source_type,
        "source_name": candidate.source_name,
        "source_ref": candidate.source_ref,
        "language": candidate.language,
        "region": candidate.region,
        "entities": list(candidate.entities),
        "keywords": list(candidate.keywords),
        "tags": list(candidate.tags),
        "topic_fingerprint": candidate.topic_fingerprint,
    }


async def create_selection_run(
    session: AsyncSession,
    channel_id: UUID,
    request: ContentSelectionRunCreate,
) -> ContentSelectionRunResponse:
    """Score and snapshot a bounded explicit candidate set without selecting it."""
    existing = (
        await session.execute(
            select(ContentSelectionRun).where(
                ContentSelectionRun.channel_id == channel_id,
                ContentSelectionRun.idempotency_key == request.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return await _return_existing_or_conflict(session, existing, request)

    revision, context = await _resolve_pinned_context(
        session, channel_id, request.mission_execution_id
    )
    current_policy_checksum = policy_checksum()
    request_checksum = candidate_set_checksum(
        candidate_ids=request.candidate_ids,
        channel_dna_revision_id=revision.id,
        selection_policy_checksum=current_policy_checksum,
        mission_execution_id=request.mission_execution_id,
    )

    candidates = list(
        (
            await session.execute(
                select(TopicCandidate)
                .options(selectinload(TopicCandidate.angles))
                .where(TopicCandidate.id.in_(request.candidate_ids))
                .order_by(TopicCandidate.id.asc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if len(candidates) != len(request.candidate_ids):
        raise ValueError("Every requested TopicCandidate must exist.")
    for candidate in candidates:
        if candidate.channel_id != channel_id:
            raise ValueError(f"Candidate '{candidate.id}' does not belong to the target channel.")
        if candidate.status not in ELIGIBLE_CANDIDATE_STATES:
            raise ValueError(
                f"Candidate '{candidate.id}' is in ineligible state '{candidate.status}'."
            )

    memory_records = list(
        (
            await session.execute(
                select(TopicMemory)
                .options(selectinload(TopicMemory.angles))
                .where(TopicMemory.channel_id == channel_id)
                .order_by(TopicMemory.id.asc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )

    evaluated: list[tuple[TopicCandidate, dict]] = [
        (
            candidate,
            topic_service.evaluate_candidate_inputs(candidate, context, memory_records),
        )
        for candidate in candidates
    ]
    evaluated.sort(key=lambda item: (-item[1]["final_score"], str(item[0].id)))

    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    run = ContentSelectionRun(
        id=run_id,
        channel_id=channel_id,
        channel_dna_revision_id=revision.id,
        mission_execution_id=request.mission_execution_id,
        status=ContentSelectionStatus.READY.value,
        policy_name=CONTENT_SELECTION_POLICY_NAME,
        policy_version=CONTENT_SELECTION_POLICY_VERSION,
        policy_checksum=current_policy_checksum,
        candidate_set_checksum=request_checksum,
        idempotency_key=request.idempotency_key,
        recommended_candidate_id=evaluated[0][0].id,
        considered_count=len(evaluated),
        completed_at=now,
    )
    similarity_checksum = _canonical_checksum(
        DEFAULT_SIMILARITY_PROFILE.model_dump(mode="json")
    )
    for rank, (candidate, evaluation) in enumerate(evaluated, start=1):
        run.decisions.append(
            ContentSelectionDecision(
                id=uuid.uuid4(),
                selection_run_id=run_id,
                candidate_id=candidate.id,
                rank=rank,
                final_score=evaluation["final_score"],
                score_breakdown=evaluation["score_breakdown"],
                reasons=evaluation["reasons"],
                duplicate_status=evaluation["duplicate_status"].value,
                similar_memory_id=evaluation["similar_memory_id"],
                similarity_score=evaluation["similarity_score"],
                candidate_title_snapshot=candidate.title,
                topic_fingerprint_snapshot=candidate.topic_fingerprint,
                candidate_snapshot=_candidate_snapshot(candidate),
                evidence_snapshot={
                    "schema_version": 1,
                    "channel_dna_revision_id": str(revision.id),
                    "matched_topic_memory_id": (
                        str(evaluation["similar_memory_id"])
                        if evaluation["similar_memory_id"]
                        else None
                    ),
                    "duplicate_status": evaluation["duplicate_status"].value,
                    "similarity_score": evaluation["similarity_score"],
                    "scoring_profile": {
                        "name": CONTENT_SELECTION_POLICY_NAME,
                        "version": CONTENT_SELECTION_POLICY_VERSION,
                        "policy_checksum": current_policy_checksum,
                    },
                    "similarity_profile_checksum": similarity_checksum,
                    "trend_evidence_authority": "NULL_PROVIDER",
                    "historical_performance_evidence_authority": "NULL_PROVIDER",
                    "cost_evidence_authority": "DEFAULT_PROVIDER",
                    "revenue_evidence_authority": "DEFAULT_PROVIDER",
                    "analytics_evidence_ids": [],
                    "learning_evidence_ids": [],
                },
            )
        )

    try:
        session.add(run)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = (
            await session.execute(
                select(ContentSelectionRun).where(
                    ContentSelectionRun.channel_id == channel_id,
                    ContentSelectionRun.idempotency_key == request.idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if winner is None:
            raise
        return await _return_existing_or_conflict(session, winner, request)

    loaded = await _load_run(session, run_id)
    assert loaded is not None
    return _response(loaded)


async def get_selection_run(
    session: AsyncSession, channel_id: UUID, run_id: UUID
) -> ContentSelectionRunResponse | None:
    run = await _load_run(session, run_id)
    if run is None or run.channel_id != channel_id:
        return None
    return _response(run)


async def list_selection_runs(
    session: AsyncSession, channel_id: UUID, *, limit: int = 50, offset: int = 0
) -> list[ContentSelectionRunResponse]:
    runs = (
        (
            await session.execute(
                select(ContentSelectionRun)
                .options(selectinload(ContentSelectionRun.decisions))
                .where(ContentSelectionRun.channel_id == channel_id)
                .order_by(ContentSelectionRun.created_at.desc(), ContentSelectionRun.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [_response(run) for run in runs]


async def finalize_selection_run(
    session: AsyncSession,
    channel_id: UUID,
    run_id: UUID,
    request: ContentSelectionFinalize,
) -> ContentSelectionRunResponse:
    """Atomically finalize the run and apply existing TopicMemory selection semantics."""
    run = await _load_run(session, run_id, for_update=True)
    if run is None or run.channel_id != channel_id:
        raise ContentSelectionNotFoundError("ContentSelectionRun not found.")

    selected_candidate_id = request.selected_candidate_id or run.recommended_candidate_id
    if run.status == ContentSelectionStatus.SELECTED.value:
        if run.selected_candidate_id == selected_candidate_id:
            return _response(run)
        raise ContentSelectionConflictError("A finalized selection winner cannot be changed.")

    considered_ids = {decision.candidate_id for decision in run.decisions}
    if selected_candidate_id not in considered_ids:
        raise ValueError("Selected candidate must belong to this run's considered set.")

    if selected_candidate_id == run.recommended_candidate_id:
        mode = ContentSelectionMode.POLICY
        reason = "POLICY_RECOMMENDATION_ACCEPTED"
    else:
        if not request.override_reason:
            raise ValueError("override_reason is required when overriding the recommendation.")
        mode = ContentSelectionMode.OVERRIDE
        reason = request.override_reason

    try:
        await topic_service.select_candidate_in_transaction(
            session,
            selected_candidate_id,
            fail_if_already_selected=True,
            expected_channel_id=run.channel_id,
        )
    except topic_service.TopicSelectionConflictError as exc:
        raise ContentSelectionConflictError(str(exc)) from exc
    now = datetime.now(UTC)
    run.selected_candidate_id = selected_candidate_id
    run.selection_mode = mode.value
    run.selected_by = request.actor
    run.selection_reason = reason
    run.selected_at = now
    run.status = ContentSelectionStatus.SELECTED.value
    await session.commit()

    loaded = await _load_run(session, run_id)
    assert loaded is not None
    return _response(loaded)
