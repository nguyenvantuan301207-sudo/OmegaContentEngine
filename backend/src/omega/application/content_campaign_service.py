"""Canonical content campaign planning service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.domain.channel import ChannelState
from omega.domain.content import ContentType
from omega.domain.content_campaign import (
    ContentCampaignCreate,
    ContentCampaignItemResponse,
    ContentCampaignResponse,
    ContentCampaignStatus,
    compute_campaign_plan_checksum,
    recompute_persisted_campaign_plan_checksum,
)
from omega.domain.content_selection import ContentSelectionMode, ContentSelectionStatus
from omega.infrastructure.models import (
    Channel,
    ContentCampaign,
    ContentCampaignItem,
    ContentSelectionRun,
)


class ContentCampaignConflictError(Exception):
    """Raised when an idempotency key or selection run conflict occurs."""


class ContentCampaignValidationError(ValueError):
    """Raised when campaign planning validation fails closed."""


class ContentCampaignNotFoundError(Exception):
    """Raised when a requested campaign is not found."""


class ContentCampaignIntegrityError(Exception):
    """Raised when persisted campaign historical lineage or invariant check fails closed."""


async def _load_campaign(
    session: AsyncSession, campaign_id: UUID
) -> ContentCampaign | None:
    stmt = (
        select(ContentCampaign)
        .options(
            selectinload(ContentCampaign.items).selectinload(
                ContentCampaignItem.selection_run
            ),
            selectinload(ContentCampaign.items).selectinload(
                ContentCampaignItem.selection_decision
            ),
        )
        .where(ContentCampaign.id == campaign_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _to_response(campaign: ContentCampaign) -> ContentCampaignResponse:
    # 1. Historical read invariant: item_count matches persisted items count
    if campaign.item_count != len(campaign.items):
        raise ContentCampaignIntegrityError(
            f"Campaign '{campaign.id}' item_count ({campaign.item_count}) does not match persisted items ({len(campaign.items)})."
        )

    # 2. Items ordered by position asc
    sorted_items = sorted(campaign.items, key=lambda it: it.position)

    # 3. Positions are exactly 1..item_count with no gap and no duplicate
    expected_positions = list(range(1, campaign.item_count + 1))
    actual_positions = [it.position for it in sorted_items]
    if actual_positions != expected_positions:
        raise ContentCampaignIntegrityError(
            f"Campaign '{campaign.id}' item positions {actual_positions} do not match expected 1..{campaign.item_count}."
        )

    # 4. Checksum integrity: verify recomputed plan checksum matches persisted authority
    recomputed_checksum = recompute_persisted_campaign_plan_checksum(campaign)
    if recomputed_checksum != campaign.plan_checksum:
        raise ContentCampaignIntegrityError(
            f"Campaign '{campaign.id}' persisted plan_checksum '{campaign.plan_checksum}' does not match recomputed checksum '{recomputed_checksum}'."
        )

    items_response: list[ContentCampaignItemResponse] = []
    for it in sorted_items:
        # Check parent campaign link
        if it.campaign_id != campaign.id:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' belongs to campaign '{it.campaign_id}', not '{campaign.id}'."
            )
        # Check relations populated
        if it.selection_decision is None or it.selection_run is None:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' has unpopulated selection run or decision."
            )
        # Selection run channel coherence
        if it.selection_run.channel_id != campaign.channel_id:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' selection run '{it.selection_run.id}' belongs to channel '{it.selection_run.channel_id}', not '{campaign.channel_id}'."
            )
        # Selection run DNA coherence
        if it.selection_run.channel_dna_revision_id != campaign.channel_dna_revision_id:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' selection run '{it.selection_run.id}' pins DNA revision '{it.selection_run.channel_dna_revision_id}', not '{campaign.channel_dna_revision_id}'."
            )
        # Selection run must be SELECTED
        if it.selection_run.status != ContentSelectionStatus.SELECTED.value:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' selection run '{it.selection_run.id}' is not SELECTED (status={it.selection_run.status})."
            )
        # Selection run selected candidate matches item topic candidate
        if it.selection_run.selected_candidate_id != it.topic_candidate_id:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' selection run selected candidate '{it.selection_run.selected_candidate_id}' does not match item candidate '{it.topic_candidate_id}'."
            )
        # Selection decision must match item selection_decision_id, selection_run_id, and topic_candidate_id
        if it.selection_decision.id != it.selection_decision_id:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' decision id '{it.selection_decision.id}' does not match item '{it.selection_decision_id}'."
            )
        if it.selection_decision.selection_run_id != it.selection_run_id:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' decision run '{it.selection_decision.selection_run_id}' does not match item run '{it.selection_run_id}'."
            )
        if it.selection_decision.candidate_id != it.topic_candidate_id:
            raise ContentCampaignIntegrityError(
                f"Campaign item '{it.id}' decision candidate '{it.selection_decision.candidate_id}' does not match item candidate '{it.topic_candidate_id}'."
            )

        items_response.append(
            ContentCampaignItemResponse(
                id=it.id,
                campaign_id=it.campaign_id,
                position=it.position,
                selection_run_id=it.selection_run_id,
                selection_decision_id=it.selection_decision_id,
                topic_candidate_id=it.topic_candidate_id,
                candidate_title_snapshot=it.selection_decision.candidate_title_snapshot,
                selection_mode=ContentSelectionMode(it.selection_run.selection_mode),
                target_content_type=ContentType(it.target_content_type),
                planned_release_at=it.planned_release_at,
                created_at=it.created_at,
            )
        )

    return ContentCampaignResponse(
        id=campaign.id,
        channel_id=campaign.channel_id,
        channel_dna_revision_id=campaign.channel_dna_revision_id,
        title=campaign.title,
        objective=campaign.objective,
        priority=campaign.priority,
        status=ContentCampaignStatus(campaign.status),
        idempotency_key=campaign.idempotency_key,
        plan_checksum=campaign.plan_checksum,
        item_count=campaign.item_count,
        created_by=campaign.created_by,
        created_at=campaign.created_at,
        items=items_response,
    )


async def create_campaign(
    session: AsyncSession,
    channel_id: UUID,
    request: ContentCampaignCreate,
) -> ContentCampaignResponse:
    """Create an immutable, bounded content campaign plan.

    Enforces:
    - Channel validity and non-archival
    - Finalized (SELECTED) selection runs belonging to target channel
    - Strict shared pinned ChannelDNARevision across all runs
    - Direct lineage resolution of exact selected decision and candidate
    - Rejection of duplicate candidates within the campaign
    - Global exclusivity: each selection run authorizes at most one campaign item
    - Deterministic order-sensitive plan checksum
    - Idempotency reconciliation and concurrency safety
    """
    # 1. Check channel
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise ContentCampaignNotFoundError(f"Channel '{channel_id}' not found.")
    if channel.state == ChannelState.ARCHIVED.value:
        raise ContentCampaignValidationError("Cannot plan campaigns for an archived channel.")

    # 2. Check existing campaign with (channel_id, idempotency_key)
    existing = (
        await session.execute(
            select(ContentCampaign).where(
                ContentCampaign.channel_id == channel_id,
                ContentCampaign.idempotency_key == request.idempotency_key,
            )
        )
    ).scalar_one_or_none()

    # 3. Load all requested selection runs
    run_ids = [item_in.selection_run_id for item_in in request.items]
    runs = list(
        (
            await session.execute(
                select(ContentSelectionRun)
                .options(selectinload(ContentSelectionRun.decisions))
                .where(ContentSelectionRun.id.in_(run_ids))
            )
        )
        .scalars()
        .all()
    )
    run_by_id = {run.id: run for run in runs}

    # Validate each run
    if len(run_by_id) != len(run_ids):
        missing_ids = set(run_ids) - set(run_by_id.keys())
        raise ContentCampaignValidationError(
            f"ContentSelectionRun(s) not found: {sorted(str(m) for m in missing_ids)}"
        )

    for run in runs:
        if run.channel_id != channel_id:
            raise ContentCampaignValidationError(
                f"Selection run '{run.id}' belongs to channel '{run.channel_id}', not '{channel_id}'."
            )
        if run.status != ContentSelectionStatus.SELECTED.value:
            raise ContentCampaignValidationError(
                f"Selection run '{run.id}' is not finalized (status={run.status})."
            )
        if run.selected_candidate_id is None:
            raise ContentCampaignValidationError(
                f"Selection run '{run.id}' has no selected candidate."
            )

    # 4. Enforce DNA coherence: all runs must pin the exact same ChannelDNARevision
    dna_revision_ids = {run.channel_dna_revision_id for run in runs}
    if len(dna_revision_ids) != 1:
        raise ContentCampaignConflictError(
            f"Campaign selection runs pin multiple DNA revisions: {sorted(str(d) for d in dna_revision_ids)}. All runs must share the same pinned revision."
        )
    shared_dna_revision_id = dna_revision_ids.pop()

    # 5. Resolve candidate & decision lineage and check candidate uniqueness
    resolved_items_metadata: list[dict[str, Any]] = []
    seen_candidates: set[UUID] = set()

    for idx, item_in in enumerate(request.items):
        pos = idx + 1
        run = run_by_id[item_in.selection_run_id]
        cand_id = run.selected_candidate_id
        assert cand_id is not None

        if cand_id in seen_candidates:
            raise ContentCampaignValidationError(
                f"Topic candidate '{cand_id}' is selected more than once in the campaign."
            )
        seen_candidates.add(cand_id)

        # Match exact decision
        matching_decisions = [
            d
            for d in run.decisions
            if d.selection_run_id == run.id and d.candidate_id == cand_id
        ]
        if len(matching_decisions) != 1:
            raise ContentCampaignValidationError(
                f"Selection run '{run.id}' has {len(matching_decisions)} decisions matching winner '{cand_id}' (expected exactly 1)."
            )
        decision = matching_decisions[0]

        resolved_items_metadata.append(
            {
                "position": pos,
                "selection_run_id": run.id,
                "selection_decision_id": decision.id,
                "topic_candidate_id": cand_id,
                "target_content_type": item_in.target_content_type.value,
                "planned_release_at": item_in.planned_release_at,
            }
        )

    # 6. Compute plan checksum
    calculated_checksum = compute_campaign_plan_checksum(
        channel_id=channel_id,
        channel_dna_revision_id=shared_dna_revision_id,
        title=request.title,
        objective=request.objective,
        priority=request.priority,
        items=resolved_items_metadata,
    )

    # 7. Idempotent replay check: if (channel_id, idempotency_key) already exists,
    # reconcile before checking selection run uniqueness against other campaigns.
    if existing is not None:
        if existing.plan_checksum != calculated_checksum:
            raise ContentCampaignConflictError(
                "idempotency_key is already bound to a different canonical campaign plan"
            )
        loaded = await _load_campaign(session, existing.id)
        assert loaded is not None
        return _to_response(loaded)

    # 8. Check global selection-run exclusivity
    already_used = list(
        (
            await session.execute(
                select(ContentCampaignItem.selection_run_id).where(
                    ContentCampaignItem.selection_run_id.in_(run_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    if already_used:
        raise ContentCampaignConflictError(
            f"Selection run '{already_used[0]}' is already committed to another campaign."
        )

    # 9. Construct and persist campaign atomically
    campaign = ContentCampaign(
        channel_id=channel_id,
        channel_dna_revision_id=shared_dna_revision_id,
        title=request.title,
        objective=request.objective,
        priority=request.priority,
        status=ContentCampaignStatus.READY.value,
        idempotency_key=request.idempotency_key,
        plan_checksum=calculated_checksum,
        item_count=len(resolved_items_metadata),
        created_by=request.created_by,
    )

    for item_meta in resolved_items_metadata:
        campaign_item = ContentCampaignItem(
            position=item_meta["position"],
            selection_run_id=item_meta["selection_run_id"],
            selection_decision_id=item_meta["selection_decision_id"],
            topic_candidate_id=item_meta["topic_candidate_id"],
            target_content_type=item_meta["target_content_type"],
            planned_release_at=item_meta["planned_release_at"],
        )
        campaign.items.append(campaign_item)

    try:
        session.add(campaign)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Handle concurrency: check if winner with same idempotency key exists
        winner = (
            await session.execute(
                select(ContentCampaign).where(
                    ContentCampaign.channel_id == channel_id,
                    ContentCampaign.idempotency_key == request.idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if winner is not None:
            if winner.plan_checksum != calculated_checksum:
                raise ContentCampaignConflictError(
                    "idempotency_key is already bound to a different canonical campaign plan"
                ) from exc
            loaded_winner = await _load_campaign(session, winner.id)
            assert loaded_winner is not None
            return _to_response(loaded_winner)

        # If winner with same idempotency key does not exist, check if selection run was taken concurrently
        concurrent_used = list(
            (
                await session.execute(
                    select(ContentCampaignItem.selection_run_id).where(
                        ContentCampaignItem.selection_run_id.in_(run_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if concurrent_used or "uq_content_campaign_items_selection_run" in str(exc):
            conflict_id = concurrent_used[0] if concurrent_used else run_ids[0]
            raise ContentCampaignConflictError(
                f"Selection run '{conflict_id}' is already committed to another campaign."
            ) from exc
        raise

    loaded = await _load_campaign(session, campaign.id)
    assert loaded is not None
    return _to_response(loaded)


async def get_campaign(
    session: AsyncSession,
    channel_id: UUID,
    campaign_id: UUID,
) -> ContentCampaignResponse | None:
    """Retrieve full campaign detail for a channel."""
    campaign = await _load_campaign(session, campaign_id)
    if campaign is None or campaign.channel_id != channel_id:
        return None
    return _to_response(campaign)


async def list_campaigns(
    session: AsyncSession,
    channel_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[ContentCampaignResponse]:
    """List campaigns for a channel, ordered by created_at DESC, id DESC."""
    stmt = (
        select(ContentCampaign)
        .options(
            selectinload(ContentCampaign.items).selectinload(
                ContentCampaignItem.selection_run
            ),
            selectinload(ContentCampaign.items).selectinload(
                ContentCampaignItem.selection_decision
            ),
        )
        .where(ContentCampaign.channel_id == channel_id)
        .order_by(ContentCampaign.created_at.desc(), ContentCampaign.id.desc())
        .limit(limit)
        .offset(offset)
    )
    campaigns = list((await session.execute(stmt)).scalars().all())
    return [_to_response(c) for c in campaigns]
