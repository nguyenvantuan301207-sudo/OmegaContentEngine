"""Lazy campaign admission, materialization, and lifecycle coordination.

Campaign owns only admission and aggregate lifecycle. Mission remains the execution
authority and DurableDispatchService remains the broker handoff authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.error_sanitizer import sanitize_error
from omega.application.mission_service import (
    _create_mission_in_transaction,
    _plan_mission_in_transaction,
    cancel_mission,
    start_mission,
)
from omega.config import get_settings
from omega.domain.content_campaign import (
    CampaignSummaryResponse,
    normalize_planned_release_at,
)
from omega.domain.content_campaign import (
    ContentCampaignItemAdmissionState as ItemState,
)
from omega.domain.content_campaign import (
    ContentCampaignOrchestrationMode as Mode,
)
from omega.domain.content_campaign import (
    ContentCampaignStatus as CampaignState,
)
from omega.domain.content_campaign_execution import (
    FANOUT_POLICY_NAME,
    FANOUT_POLICY_VERSION,
    ContentCampaignExecutionStatus,
    compute_fanout_policy_checksum,
)
from omega.domain.mission import AutonomyLevel, MissionCreate, MissionState, MissionTriggerType
from omega.infrastructure.models import (
    ContentCampaign,
    ContentCampaignExecution,
    ContentCampaignItem,
    ContentCampaignItemExecution,
    ContentSelectionDecision,
    Mission,
)


class CampaignRuntimeError(ValueError):
    pass


class CampaignFeatureDisabledError(CampaignRuntimeError):
    pass


@dataclass(frozen=True)
class ReconciliationResult:
    campaigns_seen: int = 0
    reserved: int = 0
    materialized: int = 0
    started: int = 0
    failed: int = 0
    cancelled: int = 0
    finalized: int = 0


def channel_admission_lock_key(channel_id: UUID) -> int:
    raw = f"campaign-channel-admission:{channel_id}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


async def _lock_channel(session: AsyncSession, channel_id: UUID) -> None:
    # Canonical Campaign lock hierarchy: channel advisory lock -> Campaign row
    # lock -> child/execution/Mission locks. Callers must end the transaction
    # before beginning a new acquisition sequence in this order.
    await session.execute(
        select(func.pg_advisory_xact_lock(channel_admission_lock_key(channel_id)))
    )


async def _db_now(session: AsyncSession):
    return (await session.execute(select(func.now()))).scalar_one()


async def _locked_campaign(
    session: AsyncSession, channel_id: UUID, campaign_id: UUID
) -> ContentCampaign:
    campaign = (
        await session.execute(
            select(ContentCampaign)
            .where(
                ContentCampaign.id == campaign_id,
                ContentCampaign.channel_id == channel_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise CampaignRuntimeError("Campaign not found for channel.")
    if campaign.orchestration_mode != Mode.LAZY_ADMISSION_V1.value:
        raise CampaignRuntimeError("Legacy campaigns do not support lazy runtime actions.")
    return campaign


async def start_campaign(
    session: AsyncSession, channel_id: UUID, campaign_id: UUID, actor: str
) -> ContentCampaign:
    settings = get_settings()
    if not settings.campaign_orchestration_enabled:
        raise CampaignFeatureDisabledError("Campaign orchestration is disabled.")
    await _lock_channel(session, channel_id)
    campaign = await _locked_campaign(session, channel_id, campaign_id)
    if campaign.status == CampaignState.RUNNING.value:
        return campaign
    if campaign.status != CampaignState.READY.value:
        raise CampaignRuntimeError(f"Campaign cannot start from {campaign.status}.")
    if (
        not campaign.max_concurrent_missions
        or not 1 <= campaign.max_concurrent_missions <= settings.campaign_max_concurrency
    ):
        raise CampaignRuntimeError("Campaign concurrency is missing or outside configured bounds.")
    existing = (
        await session.execute(
            select(ContentCampaignExecution).where(
                ContentCampaignExecution.campaign_id == campaign.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise CampaignRuntimeError("READY lazy campaign already has an execution authority.")
    now = await _db_now(session)
    campaign.status = CampaignState.RUNNING.value
    campaign.started_at = now
    session.add(
        ContentCampaignExecution(
            id=uuid4(),
            campaign_id=campaign.id,
            channel_id=campaign.channel_id,
            channel_dna_revision_id=campaign.channel_dna_revision_id,
            status=ContentCampaignExecutionStatus.ACTIVE.value,
            fanout_policy_name=FANOUT_POLICY_NAME,
            fanout_policy_version=FANOUT_POLICY_VERSION,
            fanout_policy_checksum=compute_fanout_policy_checksum(),
            item_count=campaign.item_count,
            materialized_by=actor,
        )
    )
    await session.commit()
    return campaign


async def pause_campaign(
    session: AsyncSession, channel_id: UUID, campaign_id: UUID
) -> ContentCampaign:
    await _lock_channel(session, channel_id)
    campaign = await _locked_campaign(session, channel_id, campaign_id)
    if campaign.status == CampaignState.PAUSED.value:
        return campaign
    if campaign.status != CampaignState.RUNNING.value:
        raise CampaignRuntimeError(f"Campaign cannot pause from {campaign.status}.")
    now = await _db_now(session)
    campaign.status = CampaignState.PAUSED.value
    campaign.paused_at = now
    await session.execute(
        update(ContentCampaignItem)
        .where(
            ContentCampaignItem.campaign_id == campaign.id,
            ContentCampaignItem.admission_state == ItemState.ADMITTED.value,
            ~ContentCampaignItem.id.in_(select(ContentCampaignItemExecution.campaign_item_id)),
        )
        .values(
            admission_state=ItemState.PENDING.value,
            admitted_at=None,
            next_materialization_attempt_at=None,
        )
    )
    await session.commit()
    return campaign


async def resume_campaign(
    session: AsyncSession, channel_id: UUID, campaign_id: UUID
) -> ContentCampaign:
    if not get_settings().campaign_orchestration_enabled:
        raise CampaignFeatureDisabledError("Campaign orchestration is disabled.")
    await _lock_channel(session, channel_id)
    campaign = await _locked_campaign(session, channel_id, campaign_id)
    if campaign.status != CampaignState.PAUSED.value:
        raise CampaignRuntimeError(f"Campaign cannot resume from {campaign.status}.")
    campaign.status = CampaignState.RUNNING.value
    campaign.paused_at = None
    await session.commit()
    return campaign


async def cancel_campaign(
    session: AsyncSession, channel_id: UUID, campaign_id: UUID
) -> ContentCampaign:
    await _lock_channel(session, channel_id)
    campaign = await _locked_campaign(session, channel_id, campaign_id)
    if campaign.status == CampaignState.CANCELLED.value:
        return campaign
    if campaign.status not in {
        CampaignState.READY.value,
        CampaignState.RUNNING.value,
        CampaignState.PAUSED.value,
        CampaignState.CANCELLING.value,
    }:
        raise CampaignRuntimeError(f"Campaign cannot cancel from {campaign.status}.")
    campaign.status = CampaignState.CANCELLING.value
    await session.execute(
        update(ContentCampaignItem)
        .where(
            ContentCampaignItem.campaign_id == campaign.id,
            ContentCampaignItem.admission_state.in_(
                [ItemState.PENDING.value, ItemState.ADMITTED.value]
            ),
            ~ContentCampaignItem.id.in_(select(ContentCampaignItemExecution.campaign_item_id)),
        )
        .values(
            admission_state=ItemState.CANCELLED.value,
            admitted_at=None,
            next_materialization_attempt_at=None,
        )
    )
    mission_ids = list(
        (
            await session.execute(
                select(ContentCampaignItemExecution.mission_id)
                .join(
                    ContentCampaignItem,
                    ContentCampaignItem.id == ContentCampaignItemExecution.campaign_item_id,
                )
                .where(ContentCampaignItem.campaign_id == campaign.id)
            )
        )
        .scalars()
        .all()
    )
    await session.commit()
    for mission_id in mission_ids:
        state = (
            await session.execute(select(Mission.state).where(Mission.id == mission_id))
        ).scalar_one_or_none()
        if state in {
            MissionState.READY.value,
            MissionState.RUNNING.value,
            MissionState.PAUSED.value,
        }:
            await cancel_mission(session, mission_id)
    await finalize_campaign(session, campaign.id)
    return campaign


async def archive_campaign(
    session: AsyncSession, channel_id: UUID, campaign_id: UUID
) -> ContentCampaign:
    campaign = await _locked_campaign(session, channel_id, campaign_id)
    if campaign.status not in {
        CampaignState.SUCCEEDED.value,
        CampaignState.PARTIAL.value,
        CampaignState.FAILED.value,
        CampaignState.CANCELLED.value,
    }:
        raise CampaignRuntimeError("Only terminal campaigns may be archived.")
    if campaign.archived_at is None:
        campaign.archived_at = await _db_now(session)
        await session.commit()
    return campaign


async def _active_counts(session: AsyncSession, campaign: ContentCampaign) -> tuple[int, int]:
    active_states = [
        MissionState.READY.value,
        MissionState.RUNNING.value,
        MissionState.PAUSED.value,
    ]
    campaign_active = (
        await session.execute(
            select(func.count(func.distinct(ContentCampaignItem.id)))
            .select_from(ContentCampaignItem)
            .outerjoin(
                ContentCampaignItemExecution,
                ContentCampaignItemExecution.campaign_item_id == ContentCampaignItem.id,
            )
            .outerjoin(Mission, Mission.id == ContentCampaignItemExecution.mission_id)
            .where(
                ContentCampaignItem.campaign_id == campaign.id,
                or_(
                    ContentCampaignItem.admission_state == ItemState.ADMITTED.value,
                    Mission.state.in_(active_states),
                ),
            )
        )
    ).scalar_one()
    channel_active = (
        await session.execute(
            select(func.count(func.distinct(ContentCampaignItem.id)))
            .select_from(ContentCampaignItem)
            .join(ContentCampaign, ContentCampaign.id == ContentCampaignItem.campaign_id)
            .outerjoin(
                ContentCampaignItemExecution,
                ContentCampaignItemExecution.campaign_item_id == ContentCampaignItem.id,
            )
            .outerjoin(Mission, Mission.id == ContentCampaignItemExecution.mission_id)
            .where(
                ContentCampaign.channel_id == campaign.channel_id,
                ContentCampaign.orchestration_mode == Mode.LAZY_ADMISSION_V1.value,
                ContentCampaign.status.in_(
                    [
                        CampaignState.RUNNING.value,
                        CampaignState.PAUSED.value,
                        CampaignState.CANCELLING.value,
                    ]
                ),
                or_(
                    ContentCampaignItem.admission_state == ItemState.ADMITTED.value,
                    Mission.state.in_(active_states),
                ),
            )
        )
    ).scalar_one()
    return int(campaign_active), int(channel_active)


async def reserve_one(
    session: AsyncSession, campaign: ContentCampaign
) -> ContentCampaignItem | None:
    settings = get_settings()
    if (
        not settings.campaign_orchestration_enabled
        or campaign.status != CampaignState.RUNNING.value
    ):
        return None
    campaign_active, channel_active = await _active_counts(session, campaign)
    if (
        campaign_active >= int(campaign.max_concurrent_missions or 0)
        or channel_active >= settings.campaign_channel_max_active_missions
    ):
        return None
    item = (
        await session.execute(
            select(ContentCampaignItem)
            .where(
                ContentCampaignItem.campaign_id == campaign.id,
                ContentCampaignItem.admission_state == ItemState.PENDING.value,
            )
            .order_by(ContentCampaignItem.position)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()
    if item is None:
        return None
    now = await _db_now(session)
    item.admission_state = ItemState.ADMITTED.value
    item.admitted_at = now
    item.next_materialization_attempt_at = None
    campaign.last_admitted_at = now
    await session.commit()
    return item


async def materialize_admitted_item(
    session: AsyncSession, campaign_id: UUID, item_id: UUID
) -> ContentCampaignItemExecution | None:
    campaign = (
        await session.execute(
            select(ContentCampaign).where(ContentCampaign.id == campaign_id).with_for_update()
        )
    ).scalar_one()
    item = (
        await session.execute(
            select(ContentCampaignItem).where(ContentCampaignItem.id == item_id).with_for_update()
        )
    ).scalar_one()
    if item.admission_state != ItemState.ADMITTED.value:
        await session.rollback()
        return False
    if (
        campaign.status != CampaignState.RUNNING.value
        or item.admission_state != ItemState.ADMITTED.value
    ):
        return None
    existing = (
        await session.execute(
            select(ContentCampaignItemExecution).where(
                ContentCampaignItemExecution.campaign_item_id == item.id
            )
        )
    ).scalar_one_or_none()
    if existing:
        item.admission_state = ItemState.MATERIALIZED.value
        return existing
    execution = (
        await session.execute(
            select(ContentCampaignExecution).where(
                ContentCampaignExecution.campaign_id == campaign.id
            )
        )
    ).scalar_one()
    decision = (
        await session.execute(
            select(ContentSelectionDecision).where(
                ContentSelectionDecision.id == item.selection_decision_id
            )
        )
    ).scalar_one()
    metadata = {
        "campaign_lineage": {
            "campaign_id": str(campaign.id),
            "campaign_item_id": str(item.id),
            "campaign_execution_id": str(execution.id),
            "position": item.position,
            "selection_run_id": str(item.selection_run_id),
            "selection_decision_id": str(item.selection_decision_id),
            "topic_candidate_id": str(item.topic_candidate_id),
            "target_content_type": item.target_content_type,
            "planned_release_at": normalize_planned_release_at(item.planned_release_at),
        },
        "canonical_inputs": {
            "topic_candidate_id": str(item.topic_candidate_id),
            "content": {"content_type": item.target_content_type},
        },
    }
    mission = await _create_mission_in_transaction(
        session,
        MissionCreate(
            title=f"[{campaign.title}] {decision.candidate_title_snapshot}"[:255],
            objective=f"Execute campaign '{campaign.title}' item #{item.position} targeting {item.target_content_type}."[
                :2000
            ],
            channel_id=campaign.channel_id,
            description=f"Materialized mission for campaign {campaign.id} item #{item.position}",
            autonomy_level=AutonomyLevel.SUPERVISED,
            priority=max(1, min(10, campaign.priority)),
            metadata=metadata,
        ),
    )
    mission, mission_execution = await _plan_mission_in_transaction(
        session,
        mission,
        channel_dna_revision_id=campaign.channel_dna_revision_id,
        trigger_type=MissionTriggerType.API,
    )
    binding = ContentCampaignItemExecution(
        id=uuid4(),
        campaign_execution_id=execution.id,
        campaign_item_id=item.id,
        position=item.position,
        mission_id=mission.id,
        mission_execution_id=mission_execution.id,
    )
    session.add(binding)
    item.admission_state = ItemState.MATERIALIZED.value
    item.materialized_at = await _db_now(session)
    item.next_materialization_attempt_at = None
    await session.commit()
    return binding


def _transient_db_error(exc: BaseException) -> bool:
    if not isinstance(exc, (OperationalError, DBAPIError)):
        return False
    code = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(
        getattr(exc, "orig", None), "pgcode", None
    )
    return code in {"40001", "40P01", "55P03", "08000", "08003", "08006", "08001"}


async def persist_materialization_failure(
    session: AsyncSession, campaign_id: UUID, item_id: UUID, exc: BaseException
) -> bool:
    """Persist a bounded failure after caller rollback; a concurrent binding always wins."""
    settings = get_settings()
    await session.execute(
        select(ContentCampaign.id).where(ContentCampaign.id == campaign_id).with_for_update()
    )
    item = (
        await session.execute(
            select(ContentCampaignItem).where(ContentCampaignItem.id == item_id).with_for_update()
        )
    ).scalar_one()
    winner = (
        await session.execute(
            select(ContentCampaignItemExecution.id).where(
                ContentCampaignItemExecution.campaign_item_id == item.id
            )
        )
    ).scalar_one_or_none()
    if winner is not None:
        item.admission_state = ItemState.MATERIALIZED.value
        await session.commit()
        return False
    item.materialization_attempts += 1
    item.sanitized_materialization_error = sanitize_error(exc)[:500]
    transient = _transient_db_error(exc)
    if transient and item.materialization_attempts < settings.campaign_materialization_max_attempts:
        item.materialization_error_code = "MATERIALIZATION_TRANSIENT_DB"
        delay = 2**item.materialization_attempts
        item.next_materialization_attempt_at = await _db_now(session) + timedelta(seconds=delay)
    else:
        item.admission_state = ItemState.FAILED.value
        item.materialization_error_code = (
            "MATERIALIZATION_RETRIES_EXHAUSTED"
            if transient
            else (
                "MATERIALIZATION_DETERMINISTIC"
                if isinstance(exc, (ValueError, IntegrityError, CampaignRuntimeError))
                else "MATERIALIZATION_UNCLASSIFIED"
            )
        )
        item.next_materialization_attempt_at = None
    await session.commit()
    return item.admission_state == ItemState.FAILED.value


async def finalize_campaign(session: AsyncSession, campaign_id: UUID) -> bool:
    campaign = (
        await session.execute(
            select(ContentCampaign).where(ContentCampaign.id == campaign_id).with_for_update()
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                select(ContentCampaignItem.admission_state, Mission.state)
                .select_from(ContentCampaignItem)
                .outerjoin(
                    ContentCampaignItemExecution,
                    ContentCampaignItemExecution.campaign_item_id == ContentCampaignItem.id,
                )
                .outerjoin(Mission, Mission.id == ContentCampaignItemExecution.mission_id)
                .where(ContentCampaignItem.campaign_id == campaign.id)
            )
        ).all()
    )
    terminal_missions = {
        MissionState.SUCCEEDED.value,
        MissionState.FAILED.value,
        MissionState.CANCELLED.value,
        None,
    }
    resolved_items = {
        ItemState.MATERIALIZED.value,
        ItemState.FAILED.value,
        ItemState.CANCELLED.value,
    }
    if not rows or not all(
        admission in resolved_items and mission_state in terminal_missions
        for admission, mission_state in rows
    ):
        return False
    now = await _db_now(session)
    execution = (
        await session.execute(
            select(ContentCampaignExecution).where(
                ContentCampaignExecution.campaign_id == campaign.id
            )
        )
    ).scalar_one_or_none()
    if execution and execution.status == ContentCampaignExecutionStatus.ACTIVE.value:
        execution.status = ContentCampaignExecutionStatus.MATERIALIZED.value
        execution.materialization_completed_at = now
    if campaign.status == CampaignState.CANCELLING.value:
        campaign.status = CampaignState.CANCELLED.value
        campaign.cancelled_at = now
        if execution:
            execution.status = ContentCampaignExecutionStatus.CANCELLED.value
    else:
        successes = sum(state == MissionState.SUCCEEDED.value for _, state in rows)
        failures = sum(
            adm == ItemState.FAILED.value
            or state in {MissionState.FAILED.value, MissionState.CANCELLED.value}
            for adm, state in rows
        )
        campaign.status = (
            CampaignState.SUCCEEDED.value
            if successes == len(rows)
            else (
                CampaignState.FAILED.value
                if successes == 0 and failures
                else CampaignState.PARTIAL.value
            )
        )
        campaign.completed_at = now
    await session.commit()
    return True


async def reconcile_campaign(
    session: AsyncSession, campaign: ContentCampaign
) -> ReconciliationResult:
    """Run safety convergence always and growth only while the master gate is enabled."""
    # Re-read all mutable authority under the canonical lock order. The
    # Campaign instance supplied by batch discovery is only an identifier and
    # must not authorize lifecycle, gate, or capacity decisions.
    channel_id = campaign.channel_id
    campaign_id = campaign.id
    await _lock_channel(session, channel_id)
    campaign = await _locked_campaign(session, channel_id, campaign_id)
    if campaign.status == CampaignState.CANCELLING.value:
        # cancel_campaign owns the same canonical acquisition sequence. End
        # this read/lock transaction before delegating so it can revalidate.
        await session.commit()
        await cancel_campaign(session, campaign.channel_id, campaign.id)
        return ReconciliationResult(campaigns_seen=1, cancelled=1, finalized=1)
    if campaign.status in {
        CampaignState.RUNNING.value,
        CampaignState.PAUSED.value,
    } and await finalize_campaign(session, campaign.id):
        return ReconciliationResult(campaigns_seen=1, finalized=1)
    if (
        not get_settings().campaign_orchestration_enabled
        or campaign.status != CampaignState.RUNNING.value
    ):
        # finalize_campaign intentionally retains its row lock when there is
        # nothing to finalize. Do not leak that lock into the next batch item.
        await session.commit()
        return ReconciliationResult(campaigns_seen=1)
    admitted = list(
        (
            await session.execute(
                select(ContentCampaignItem)
                .where(
                    ContentCampaignItem.campaign_id == campaign.id,
                    ContentCampaignItem.admission_state == ItemState.ADMITTED.value,
                    or_(
                        ContentCampaignItem.next_materialization_attempt_at.is_(None),
                        ContentCampaignItem.next_materialization_attempt_at <= func.now(),
                    ),
                )
                .order_by(ContentCampaignItem.position)
            )
        )
        .scalars()
        .all()
    )
    reserved = materialized = started = failed = 0
    for item in admitted:
        try:
            binding = await materialize_admitted_item(session, campaign.id, item.id)
            if binding:
                materialized += 1
                if await start_mission(session, binding.mission_id):
                    started += 1
        except Exception as exc:
            await session.rollback()
            failed += int(await persist_materialization_failure(session, campaign.id, item.id, exc))
    ready_bindings = list(
        (
            await session.execute(
                select(ContentCampaignItemExecution)
                .join(Mission, Mission.id == ContentCampaignItemExecution.mission_id)
                .join(
                    ContentCampaignItem,
                    ContentCampaignItem.id == ContentCampaignItemExecution.campaign_item_id,
                )
                .where(
                    ContentCampaignItem.campaign_id == campaign.id,
                    Mission.state == MissionState.READY.value,
                )
            )
        )
        .scalars()
        .all()
    )
    for binding in ready_bindings:
        if await start_mission(session, binding.mission_id):
            started += 1
    item = await reserve_one(session, campaign)
    if item:
        reserved = 1
        try:
            binding = await materialize_admitted_item(session, campaign.id, item.id)
            if binding:
                materialized += 1
                if await start_mission(session, binding.mission_id):
                    started += 1
        except Exception as exc:
            await session.rollback()
            failed += int(await persist_materialization_failure(session, campaign.id, item.id, exc))
    if not await finalize_campaign(session, campaign.id):
        # End the final read/lock transaction before reconcile_batch advances.
        await session.commit()
    return ReconciliationResult(1, reserved, materialized, started, failed)


async def reconcile_batch(session: AsyncSession) -> dict[str, int]:
    settings = get_settings()
    campaigns = list(
        (
            await session.execute(
                select(ContentCampaign)
                .where(
                    ContentCampaign.orchestration_mode == Mode.LAZY_ADMISSION_V1.value,
                    ContentCampaign.status.in_(
                        [
                            CampaignState.RUNNING.value,
                            CampaignState.PAUSED.value,
                            CampaignState.CANCELLING.value,
                        ]
                    ),
                )
                .order_by(
                    ContentCampaign.last_admitted_at.asc().nullsfirst(),
                    ContentCampaign.priority.desc(),
                    ContentCampaign.created_at,
                    ContentCampaign.id,
                )
                .limit(settings.campaign_reconciliation_batch_size)
            )
        )
        .scalars()
        .all()
    )
    total = ReconciliationResult()
    for campaign in campaigns:
        try:
            result = await reconcile_campaign(session, campaign)
        except Exception:
            await session.rollback()
            continue
        total = ReconciliationResult(
            *(
                getattr(total, f) + getattr(result, f)
                for f in ReconciliationResult.__dataclass_fields__
            )
        )
    return total.__dict__


async def campaign_summary(session: AsyncSession, campaign_id: UUID) -> CampaignSummaryResponse:
    rows = list(
        (
            await session.execute(
                select(ContentCampaignItem.admission_state, Mission.state)
                .select_from(ContentCampaignItem)
                .outerjoin(
                    ContentCampaignItemExecution,
                    ContentCampaignItemExecution.campaign_item_id == ContentCampaignItem.id,
                )
                .outerjoin(Mission, Mission.id == ContentCampaignItemExecution.mission_id)
                .where(ContentCampaignItem.campaign_id == campaign_id)
            )
        ).all()
    )
    total = len(rows)
    pending = sum(a == ItemState.PENDING.value for a, _ in rows)
    admitted = sum(a == ItemState.ADMITTED.value for a, _ in rows)
    active = sum(
        s in {MissionState.READY.value, MissionState.RUNNING.value, MissionState.PAUSED.value}
        for _, s in rows
    )
    succeeded = sum(s == MissionState.SUCCEEDED.value for _, s in rows)
    failed = sum(a == ItemState.FAILED.value or s == MissionState.FAILED.value for a, s in rows)
    cancelled = sum(
        a == ItemState.CANCELLED.value or s == MissionState.CANCELLED.value for a, s in rows
    )
    completed = succeeded + failed + cancelled
    return CampaignSummaryResponse(
        total=total,
        pending=pending,
        admitted=admitted,
        active=active,
        succeeded=succeeded,
        failed=failed,
        cancelled=cancelled,
        completed=completed,
        progress=(completed / total if total else 0.0),
    )
