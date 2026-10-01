"""Integration tests proving PostgreSQL concurrency, atomic downstream target dispatch, and crash recovery invariants."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application import campaign_admission_service as runtime
from omega.application.scheduler.adapters.campaign_adapter import CampaignScheduleTargetAdapter
from omega.application.scheduler.adapters.mission_adapter import MissionScheduleTargetAdapter
from omega.application.scheduler.recurring_scheduler_service import RecurringSchedulerService
from omega.application.scheduler.recurring_sweep_service import RecurringSweepService
from omega.config import Settings
from omega.domain.content_campaign import (
    ContentCampaignItemAdmissionState,
    ContentCampaignOrchestrationMode,
    ContentCampaignStatus,
)
from omega.domain.recurring_schedule import (
    CatchUpPolicy,
    DSTAmbiguousStrategy,
    DSTNonexistentStrategy,
    RecurringOccurrenceStatus,
    RecurringScheduleCreate,
    RecurringScheduleStatus,
    RecurringScheduleTargetType,
)
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    Channel,
    ContentCampaign,
    ContentCampaignExecution,
    ContentCampaignItem,
    Mission,
    RecurringSchedule,
    RecurringScheduleCampaignBinding,
    RecurringScheduleMissionBinding,
    RecurringScheduleOccurrence,
    RecurringScheduleVersion,
)
from omega.main import app


async def _create_canonical_campaign(
    client: AsyncClient,
    *,
    item_count: int = 2,
    mode: str = ContentCampaignOrchestrationMode.LAZY_ADMISSION_V1.value,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a fully integrated valid channel, topic selection run, and campaign."""
    ch_slug = f"camp-chan-{uuid.uuid4().hex[:8]}"
    ch_res = await client.post(
        "/api/v1/channels",
        json={
            "name": f"Channel {ch_slug}",
            "slug": ch_slug,
            "platform": "YOUTUBE",
            "primary_language": "en",
            "target_region": "US",
        },
    )
    assert ch_res.status_code == 201, ch_res.text
    channel_id = ch_res.json()["id"]

    run_ids: list[str] = []
    for pos in range(item_count):
        cand = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": f"Candidate {pos} {uuid.uuid4().hex[:6]}",
                "summary": "Summary",
                "keywords": ["test"],
                "source_type": "MANUAL",
                "source_name": "test",
                "source_ref": f"test://{uuid.uuid4()}",
            },
        )
        assert cand.status_code == 201, cand.text
        run = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={
                "candidate_ids": [cand.json()["id"]],
                "idempotency_key": f"run-{uuid.uuid4()}",
            },
        )
        assert run.status_code == 201, run.text
        fin = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run.json()['id']}/finalize",
            json={"actor": "test"},
        )
        assert fin.status_code == 200, fin.text
        run_ids.append(run.json()["id"])

    created = await client.post(
        f"/api/v1/channels/{channel_id}/campaigns",
        json={
            "title": f"Campaign {uuid.uuid4().hex[:6]}",
            "objective": "Test recurring target",
            "priority": 1,
            "idempotency_key": f"camp-{uuid.uuid4()}",
            "created_by": "test",
            "max_concurrent_missions": 1,
            "items": [
                {
                    "selection_run_id": rid,
                    "target_content_type": "YOUTUBE_LONGFORM",
                    "planned_release_at": None,
                }
                for rid in run_ids
            ],
        },
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]

    async with AsyncSessionLocal() as session:
        if mode != ContentCampaignOrchestrationMode.LAZY_ADMISSION_V1.value:
            await session.execute(
                update(ContentCampaign)
                .where(ContentCampaign.id == uuid.UUID(campaign_id))
                .values(orchestration_mode=mode)
            )
            await session.commit()
        else:
            await runtime.start_campaign(session, uuid.UUID(channel_id), uuid.UUID(campaign_id), "test")

    return uuid.UUID(channel_id), uuid.UUID(campaign_id)


@pytest.mark.asyncio
async def test_two_sweeps_one_occurrence_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two concurrent sweep workers discovering the same due schedule materialize exactly ONE occurrence."""
    monkeypatch.setattr(
        "omega.application.scheduler.recurring_sweep_service.get_settings",
        lambda: Settings(recurring_scheduler_enabled=True),
    )

    sched_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    past_due = now - timedelta(seconds=120)

    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE TABLE recurring_schedules CASCADE"))
        await session.commit()
        sched = RecurringSchedule(
            id=sched_id,
            name="Due Sched",
            status=RecurringScheduleStatus.ACTIVE.value,
            current_version_id=ver_id,
            start_time=past_due - timedelta(minutes=10),
            next_run_at=past_due,
        )
        ver = RecurringScheduleVersion(
            id=ver_id,
            schedule_id=sched_id,
            version_number=1,
            interval_seconds=300,
            timezone="UTC",
            dst_ambiguous_strategy="FIRST",
            dst_nonexistent_strategy="NEXT_VALID",
            catch_up_policy=CatchUpPolicy.RUN_LATEST_ONLY.value,
            max_catch_up_occurrences=3,
            target_type=RecurringScheduleTargetType.STANDALONE_MISSION.value,
            target_id=uuid.uuid4(),
            payload_template={"title": "Test Mission", "objective": "Test Objective"},
        )
        session.add(sched)
        session.add(ver)
        await session.commit()

    # Run two concurrent sweeps on separate DB sessions
    async def _sweep_worker() -> int:
        async with AsyncSessionLocal() as s:
            return await RecurringSweepService._materialize_due_schedules(s, batch_size=10)

    count_1, count_2 = await asyncio.gather(_sweep_worker(), _sweep_worker())

    # Verify occurrence ledger contains exactly 1 occurrence for this schedule
    async with AsyncSessionLocal() as session:
        occurrences = (
            await session.execute(
                select(RecurringScheduleOccurrence).where(
                    RecurringScheduleOccurrence.schedule_id == sched_id
                )
            )
        ).scalars().all()
        assert len(occurrences) == 1
        assert occurrences[0].status == RecurringOccurrenceStatus.PENDING.value

        # Schedule next_run_at must have advanced
        refreshed_sched = (
            await session.execute(
                select(RecurringSchedule).where(RecurringSchedule.id == sched_id)
            )
        ).scalar_one()
        assert refreshed_sched.next_run_at > past_due


@pytest.mark.asyncio
async def test_two_mission_occurrence_dispatchers_one_mission_concurrency() -> None:
    """Two concurrent workers dispatching the same PENDING occurrence start exactly ONE Mission and ONE binding."""
    sched_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    occ_id = uuid.uuid4()
    ch_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        ch = Channel(
            id=ch_id,
            name=f"Channel-{ch_id.hex[:6]}",
            slug=f"channel-{ch_id.hex[:6]}",
            platform="YOUTUBE",
            state="ACTIVE",
        )
        session.add(ch)

        sched = RecurringSchedule(
            id=sched_id,
            name="Mission Sched",
            status=RecurringScheduleStatus.ACTIVE.value,
            current_version_id=ver_id,
            start_time=now,
            next_run_at=now + timedelta(hours=1),
        )
        ver = RecurringScheduleVersion(
            id=ver_id,
            schedule_id=sched_id,
            version_number=1,
            interval_seconds=300,
            timezone="UTC",
            dst_ambiguous_strategy="FIRST",
            dst_nonexistent_strategy="NEXT_VALID",
            catch_up_policy=CatchUpPolicy.RUN_LATEST_ONLY.value,
            max_catch_up_occurrences=3,
            target_type=RecurringScheduleTargetType.STANDALONE_MISSION.value,
            target_id=ch_id,
            payload_template={"title": "Concurrent Mission", "objective": "Concurrency Test"},
        )
        session.add(sched)
        session.add(ver)
        await session.flush()

        occ = RecurringScheduleOccurrence(
            id=occ_id,
            schedule_id=sched_id,
            schedule_version_id=ver_id,
            occurrence_at=now,
            status=RecurringOccurrenceStatus.PENDING.value,
            idempotency_key=f"idem-{occ_id}",
            downstream_target_type=RecurringScheduleTargetType.STANDALONE_MISSION.value,
        )
        session.add(occ)
        await session.commit()

    # Compete to dispatch the occurrence using target adapter
    async def _dispatch_attempt() -> bool:
        async with AsyncSessionLocal() as s:
            occ_locked = (
                await s.execute(
                    select(RecurringScheduleOccurrence)
                    .where(
                        RecurringScheduleOccurrence.id == occ_id,
                        RecurringScheduleOccurrence.status == RecurringOccurrenceStatus.PENDING.value,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if occ_locked is None:
                return False

            v = (
                await s.execute(
                    select(RecurringScheduleVersion).where(
                        RecurringScheduleVersion.id == occ_locked.schedule_version_id
                    )
                )
            ).scalar_one()

            mission, reason = await MissionScheduleTargetAdapter.dispatch(
                session=s,
                occurrence=occ_locked,
                version=v,
            )
            return mission is not None and reason == "DISPATCHED"

    res_1, res_2 = await asyncio.gather(_dispatch_attempt(), _dispatch_attempt())
    # Exactly one dispatch should have succeeded and claimed the row
    assert (res_1, res_2).count(True) == 1

    # Authoritative verification
    async with AsyncSessionLocal() as session:
        occ_final = (
            await session.execute(
                select(RecurringScheduleOccurrence).where(RecurringScheduleOccurrence.id == occ_id)
            )
        ).scalar_one()
        assert occ_final.status == RecurringOccurrenceStatus.DISPATCHED.value

        bindings = (
            await session.execute(
                select(RecurringScheduleMissionBinding).where(
                    RecurringScheduleMissionBinding.occurrence_id == occ_id
                )
            )
        ).scalars().all()
        assert len(bindings) == 1

        # Exactly 1 mission created
        mission = (
            await session.execute(
                select(Mission).where(Mission.id == bindings[0].mission_id)
            )
        ).scalar_one()
        assert mission.title == "Concurrent Mission"
        assert mission.state in ("PLANNING", "QUEUED", "RUNNING")


@pytest.mark.asyncio
async def test_campaign_target_rejects_legacy_upfront() -> None:
    """Campaign target adapter strictly rejects LEGACY_UPFRONT campaigns."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ch_id, camp_id = await _create_canonical_campaign(
            client,
            item_count=1,
            mode=ContentCampaignOrchestrationMode.LEGACY_UPFRONT.value,
        )

    sched_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    occ_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        sched = RecurringSchedule(
            id=sched_id,
            name="Legacy Sched",
            status=RecurringScheduleStatus.ACTIVE.value,
            current_version_id=ver_id,
            start_time=now,
            next_run_at=now,
        )
        ver = RecurringScheduleVersion(
            id=ver_id,
            schedule_id=sched_id,
            version_number=1,
            interval_seconds=300,
            timezone="UTC",
            dst_ambiguous_strategy="FIRST",
            dst_nonexistent_strategy="NEXT_VALID",
            catch_up_policy=CatchUpPolicy.RUN_LATEST_ONLY.value,
            max_catch_up_occurrences=3,
            target_type=RecurringScheduleTargetType.CAMPAIGN_ADMISSION.value,
            target_id=camp_id,
            payload_template={},
        )
        session.add(sched)
        session.add(ver)
        await session.flush()

        occ = RecurringScheduleOccurrence(
            id=occ_id,
            schedule_id=sched_id,
            schedule_version_id=ver_id,
            occurrence_at=now,
            status=RecurringOccurrenceStatus.PENDING.value,
            idempotency_key=f"idem-{occ_id}",
            downstream_target_type=RecurringScheduleTargetType.CAMPAIGN_ADMISSION.value,
        )
        session.add(occ)
        await session.commit()

    # 1. Test rejection of LEGACY_UPFRONT target (checked before status)
    async with AsyncSessionLocal() as session:
        occ_refreshed = (
            await session.execute(
                select(RecurringScheduleOccurrence).where(RecurringScheduleOccurrence.id == occ_id)
            )
        ).scalar_one()
        ver_refreshed = (
            await session.execute(
                select(RecurringScheduleVersion).where(RecurringScheduleVersion.id == ver_id)
            )
        ).scalar_one()

        item, reason = await CampaignScheduleTargetAdapter.dispatch(
            session=session,
            occurrence=occ_refreshed,
            version=ver_refreshed,
        )
        assert item is None
        assert "LEGACY_UPFRONT" in reason

    # 2. Also test target validation in recurring_scheduler_service
    from omega.application.scheduler.recurring_scheduler_service import (
        RecurringSchedulerService,
        SchedulerRuntimeError,
    )
    async with AsyncSessionLocal() as session:
        with pytest.raises(SchedulerRuntimeError, match="LEGACY_UPFRONT"):
            await RecurringSchedulerService._validate_target_exists(
                session,
                RecurringScheduleTargetType.CAMPAIGN_ADMISSION.value,
                camp_id,
            )


@pytest.mark.asyncio
async def test_campaign_occurrence_dispatch_and_binding_atomicity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid lazy admission campaign occurrence dispatch binds admitted item atomically."""
    monkeypatch.setattr(
        "omega.application.campaign_admission_service.get_settings",
        lambda: Settings(campaign_orchestration_enabled=True, recurring_scheduler_enabled=True),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ch_id, camp_id = await _create_canonical_campaign(
            client,
            item_count=2,
            mode=ContentCampaignOrchestrationMode.LAZY_ADMISSION_V1.value,
        )

    sched_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    occ_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        sched = RecurringSchedule(
            id=sched_id,
            name="Campaign Sched",
            status=RecurringScheduleStatus.ACTIVE.value,
            current_version_id=ver_id,
            start_time=now,
            next_run_at=now,
        )
        ver = RecurringScheduleVersion(
            id=ver_id,
            schedule_id=sched_id,
            version_number=1,
            interval_seconds=300,
            timezone="UTC",
            dst_ambiguous_strategy="FIRST",
            dst_nonexistent_strategy="NEXT_VALID",
            catch_up_policy=CatchUpPolicy.RUN_LATEST_ONLY.value,
            max_catch_up_occurrences=3,
            target_type=RecurringScheduleTargetType.CAMPAIGN_ADMISSION.value,
            target_id=camp_id,
            payload_template={},
        )
        session.add(sched)
        session.add(ver)
        await session.flush()

        occ = RecurringScheduleOccurrence(
            id=occ_id,
            schedule_id=sched_id,
            schedule_version_id=ver_id,
            occurrence_at=now,
            status=RecurringOccurrenceStatus.PENDING.value,
            idempotency_key=f"idem-{occ_id}",
            downstream_target_type=RecurringScheduleTargetType.CAMPAIGN_ADMISSION.value,
        )
        session.add(occ)
        await session.commit()

    async with AsyncSessionLocal() as session:
        occ_refreshed = (
            await session.execute(
                select(RecurringScheduleOccurrence).where(RecurringScheduleOccurrence.id == occ_id)
            )
        ).scalar_one()
        ver_refreshed = (
            await session.execute(
                select(RecurringScheduleVersion).where(RecurringScheduleVersion.id == ver_id)
            )
        ).scalar_one()

        item, reason = await CampaignScheduleTargetAdapter.dispatch(
            session=session,
            occurrence=occ_refreshed,
            version=ver_refreshed,
        )
        assert item is not None
        assert reason in ("ADMITTED", "DISPATCHED")

    # Authoritative verification
    async with AsyncSessionLocal() as session:
        occ_final = (
            await session.execute(
                select(RecurringScheduleOccurrence).where(RecurringScheduleOccurrence.id == occ_id)
            )
        ).scalar_one()
        assert occ_final.status == RecurringOccurrenceStatus.DISPATCHED.value

        # Verify campaign binding row exists
        c_binding = (
            await session.execute(
                select(RecurringScheduleCampaignBinding).where(
                    RecurringScheduleCampaignBinding.occurrence_id == occ_id
                )
            )
        ).scalar_one()
        assert c_binding.campaign_id == camp_id
        assert c_binding.campaign_item_id is not None

        # Verify campaign item was admitted
        item_refreshed = (
            await session.execute(
                select(ContentCampaignItem).where(
                    ContentCampaignItem.id == c_binding.campaign_item_id
                )
            )
        ).scalar_one()
        assert item_refreshed.admission_state in (ContentCampaignItemAdmissionState.ADMITTED.value, ContentCampaignItemAdmissionState.MATERIALIZED.value)


@pytest.mark.asyncio
async def test_stale_dispatching_recovery_with_existing_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale DISPATCHING occurrence recovers to DISPATCHED if authoritative binding exists."""
    monkeypatch.setattr(
        "omega.application.scheduler.recurring_sweep_service.get_settings",
        lambda: Settings(recurring_scheduler_enabled=True),
    )

    sched_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    occ_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(minutes=10)

    async with AsyncSessionLocal() as session:
        sched = RecurringSchedule(
            id=sched_id,
            name="Crash Sched",
            status=RecurringScheduleStatus.ACTIVE.value,
            current_version_id=ver_id,
            start_time=old_time,
            next_run_at=now,
        )
        ver = RecurringScheduleVersion(
            id=ver_id,
            schedule_id=sched_id,
            version_number=1,
            interval_seconds=300,
            timezone="UTC",
            dst_ambiguous_strategy="FIRST",
            dst_nonexistent_strategy="NEXT_VALID",
            catch_up_policy=CatchUpPolicy.RUN_LATEST_ONLY.value,
            max_catch_up_occurrences=3,
            target_type=RecurringScheduleTargetType.STANDALONE_MISSION.value,
            target_id=uuid.uuid4(),
            payload_template={"title": "Mission", "objective": "Test"},
        )
        session.add(sched)
        session.add(ver)
        await session.flush()

        # Occurrence is stuck in DISPATCHING from worker crash
        occ = RecurringScheduleOccurrence(
            id=occ_id,
            schedule_id=sched_id,
            schedule_version_id=ver_id,
            occurrence_at=old_time,
            status=RecurringOccurrenceStatus.DISPATCHING.value,
            idempotency_key=f"idem-{occ_id}",
            downstream_target_type=RecurringScheduleTargetType.STANDALONE_MISSION.value,
            updated_at=old_time,
        )
        # But mission and binding were already durably committed
        mission = Mission(
            id=mission_id,
            title="Durable Mission",
            objective="Objective",
            autonomy_level="SUPERVISED",
            state="DRAFT",
        )
        session.add(occ)
        session.add(mission)
        await session.flush()

        binding = RecurringScheduleMissionBinding(
            id=uuid.uuid4(),
            occurrence_id=occ_id,
            mission_id=mission_id,
        )
        session.add(binding)
        await session.commit()

    # Reconciler runs
    async with AsyncSessionLocal() as session:
        res = await RecurringSweepService.reconcile_stale_occurrences(session)
        assert res["recovered"] >= 1

    # Verify occurrence converged to DISPATCHED
    async with AsyncSessionLocal() as session:
        occ_final = (
            await session.execute(
                select(RecurringScheduleOccurrence).where(RecurringScheduleOccurrence.id == occ_id)
            )
        ).scalar_one()
        assert occ_final.status == RecurringOccurrenceStatus.DISPATCHED.value
        assert occ_final.downstream_target_id == mission_id
