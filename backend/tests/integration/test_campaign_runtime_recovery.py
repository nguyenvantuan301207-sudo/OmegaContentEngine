"""P19-CB2.1 real-PostgreSQL recovery and concurrency acceptance."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application import campaign_admission_service as runtime
from omega.config import Settings
from omega.domain.content_campaign import ContentCampaignItemAdmissionState as ItemState
from omega.domain.content_campaign import ContentCampaignStatus as CampaignState
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    ContentCampaign,
    ContentCampaignExecution,
    ContentCampaignItem,
    ContentCampaignItemExecution,
    DecisionLog,
    DurableDispatchIntent,
    Mission,
    MissionExecution,
    Task,
    TaskDependency,
)
from omega.main import app


def _settings(*, enabled: bool = True, channel_cap: int = 1) -> Settings:
    return Settings(
        _env_file=None,
        campaign_orchestration_enabled=enabled,
        campaign_channel_max_active_missions=channel_cap,
        campaign_default_concurrency=1,
        campaign_max_concurrency=10,
        campaign_materialization_max_attempts=3,
        campaign_reconciliation_batch_size=25,
    )


async def _create_campaign(
    client: AsyncClient,
    *,
    item_count: int,
    max_concurrent: int = 1,
    channel_id: str | None = None,
    title: str = "CB2.1 Campaign",
    priority: int = 2,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    if channel_id is None:
        response = await client.post(
            "/api/v1/channels",
            json={
                "name": "CB2.1 Runtime Channel",
                "slug": f"cb21-{uuid.uuid4().hex[:12]}",
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        assert response.status_code == 201, response.text
        channel_id = response.json()["id"]

    run_ids: list[str] = []
    for position in range(item_count):
        candidate = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": f"CB2.1 candidate {position} {uuid.uuid4().hex[:8]}",
                "summary": "PostgreSQL campaign recovery fixture",
                "keywords": ["campaign", "recovery"],
                "source_type": "MANUAL",
                "source_name": "cb21",
                "source_ref": f"test://cb21/{uuid.uuid4()}",
            },
        )
        assert candidate.status_code == 201, candidate.text
        run = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={
                "candidate_ids": [candidate.json()["id"]],
                "idempotency_key": f"cb21-run-{uuid.uuid4()}",
            },
        )
        assert run.status_code == 201, run.text
        finalized = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run.json()['id']}/finalize",
            json={"actor": "cb21-test"},
        )
        assert finalized.status_code == 200, finalized.text
        run_ids.append(run.json()["id"])

    created = await client.post(
        f"/api/v1/channels/{channel_id}/campaigns",
        json={
            "title": title,
            "objective": "Exercise durable campaign admission",
            "priority": priority,
            "idempotency_key": f"cb21-campaign-{uuid.uuid4()}",
            "created_by": "cb21-test",
            "max_concurrent_missions": max_concurrent,
            "items": [
                {
                    "selection_run_id": run_id,
                    "target_content_type": "YOUTUBE_LONGFORM",
                    "planned_release_at": None,
                }
                for run_id in run_ids
            ],
        },
    )
    assert created.status_code == 201, created.text
    return (
        uuid.UUID(channel_id),
        uuid.UUID(created.json()["id"]),
        [uuid.UUID(item["id"]) for item in created.json()["items"]],
    )


async def _start(channel_id: uuid.UUID, campaign_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        await runtime.start_campaign(session, channel_id, campaign_id, "cb21-test")


async def _admit_one(channel_id: uuid.UUID, campaign_id: uuid.UUID) -> uuid.UUID | None:
    async with AsyncSessionLocal() as session:
        await runtime._lock_channel(session, channel_id)
        campaign = await runtime._locked_campaign(session, channel_id, campaign_id)
        item = await runtime.reserve_one(session, campaign)
        return item.id if item else None


def _subprocess(script: str, *, enabled: bool = True, channel_cap: int = 1) -> str:
    env = os.environ.copy()
    env["CAMPAIGN_ORCHESTRATION_ENABLED"] = "true" if enabled else "false"
    env["CAMPAIGN_CHANNEL_MAX_ACTIVE_MISSIONS"] = str(channel_cap)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd="/app" if os.name != "nt" else os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_start_reconcile_lock_boundary_has_one_authority_without_deadlock(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start and reconcile serialize advisory-before-row across real DB sessions."""
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, _ = await _create_campaign(client, item_count=1)

    original_lock_channel = runtime._lock_channel
    original_locked_campaign = runtime._locked_campaign
    starter_before_row = asyncio.Event()
    reconciler_attempting_advisory = asyncio.Event()
    release_starter = asyncio.Event()

    async def observed_lock_channel(session: AsyncSession, channel: uuid.UUID) -> None:
        if asyncio.current_task().get_name() == "reconciler":
            reconciler_attempting_advisory.set()
        await original_lock_channel(session, channel)

    async def controlled_locked_campaign(
        session: AsyncSession, channel: uuid.UUID, campaign: uuid.UUID
    ) -> ContentCampaign:
        if asyncio.current_task().get_name() == "starter":
            starter_before_row.set()
            await asyncio.wait_for(release_starter.wait(), timeout=5)
        return await original_locked_campaign(session, channel, campaign)

    monkeypatch.setattr(runtime, "_lock_channel", observed_lock_channel)
    monkeypatch.setattr(runtime, "_locked_campaign", controlled_locked_campaign)

    async def start() -> None:
        async with AsyncSessionLocal() as session:
            await runtime.start_campaign(session, channel_id, campaign_id, "lock-order-test")

    async def reconcile() -> None:
        async with AsyncSessionLocal() as session:
            campaign = await session.get(ContentCampaign, campaign_id)
            assert campaign is not None
            await runtime.reconcile_campaign(session, campaign)

    starter = asyncio.create_task(start(), name="starter")
    await asyncio.wait_for(starter_before_row.wait(), timeout=5)
    reconciler = asyncio.create_task(reconcile(), name="reconciler")
    await asyncio.wait_for(reconciler_attempting_advisory.wait(), timeout=5)
    release_starter.set()
    await asyncio.wait_for(asyncio.gather(starter, reconciler), timeout=10)

    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ContentCampaignExecution)
                .where(ContentCampaignExecution.campaign_id == campaign_id)
            )
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(ContentCampaignItemExecution)) == 1
        assert await session.scalar(select(func.count()).select_from(Mission)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DurableDispatchIntent)
                .where(DurableDispatchIntent.purpose == "MISSION_START_EVALUATION")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_two_reconcilers_serialize_without_duplicate_materialization(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, _ = await _create_campaign(client, item_count=2)
    await _start(channel_id, campaign_id)

    release = asyncio.Event()
    ready = 0
    ready_lock = asyncio.Lock()

    async def reconcile() -> None:
        nonlocal ready
        async with AsyncSessionLocal() as session:
            campaign = await session.get(ContentCampaign, campaign_id)
            assert campaign is not None
            async with ready_lock:
                ready += 1
                if ready == 2:
                    release.set()
            await release.wait()
            await runtime.reconcile_campaign(session, campaign)

    await asyncio.wait_for(asyncio.gather(reconcile(), reconcile()), timeout=10)

    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ContentCampaignItemExecution)) == 1
        assert await session.scalar(select(func.count()).select_from(Mission)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ContentCampaignExecution)
                .where(ContentCampaignExecution.campaign_id == campaign_id)
            )
            == 1
        )
        campaign = await session.get(ContentCampaign, campaign_id)
        assert campaign is not None
        campaign_active, _ = await runtime._active_counts(session, campaign)
        assert campaign_active == 1


@pytest.mark.asyncio
async def test_reconcile_and_finalize_complete_without_deadlock(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, _ = await _create_campaign(client, item_count=1)
    await _start(channel_id, campaign_id)
    async with AsyncSessionLocal() as session:
        campaign = await session.get(ContentCampaign, campaign_id)
        assert campaign is not None
        await runtime.reconcile_campaign(session, campaign)
        await session.execute(update(Mission).values(state="FAILED"))
        await session.commit()

    async def reconcile() -> None:
        async with AsyncSessionLocal() as session:
            campaign = await session.get(ContentCampaign, campaign_id)
            assert campaign is not None
            await runtime.reconcile_campaign(session, campaign)

    async def finalize() -> None:
        async with AsyncSessionLocal() as session:
            await runtime.finalize_campaign(session, campaign_id)

    await asyncio.wait_for(asyncio.gather(reconcile(), finalize()), timeout=10)

    async with AsyncSessionLocal() as session:
        campaign = await session.get(ContentCampaign, campaign_id)
        assert campaign is not None and campaign.status == CampaignState.FAILED.value
        assert await session.scalar(select(func.count()).select_from(ContentCampaignItemExecution)) == 1
        assert await session.scalar(select(func.count()).select_from(Mission)) == 1


@pytest.mark.asyncio
async def test_admitted_reservation_survives_process_exit_and_fresh_reconciler(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, item_ids = await _create_campaign(client, item_count=10)
    await _start(channel_id, campaign_id)

    reserve_script = f"""
import asyncio, uuid
from sqlalchemy import select
from omega.application import campaign_admission_service as r
from omega.infrastructure.database import AsyncWorkerSessionLocal
from omega.infrastructure.models import ContentCampaign
async def main():
    async with AsyncWorkerSessionLocal() as s:
        c=(await s.execute(select(ContentCampaign).where(ContentCampaign.id==uuid.UUID('{campaign_id}')))).scalar_one()
        await r._lock_channel(s, c.channel_id)
        c=await r._locked_campaign(s, c.channel_id, c.id)
        i=await r.reserve_one(s,c)
        print(i.id if i else 'NONE')
asyncio.run(main())
"""
    assert _subprocess(reserve_script) == str(item_ids[0])

    async with AsyncSessionLocal() as session:
        item = await session.get(ContentCampaignItem, item_ids[0])
        assert item is not None and item.admission_state == ItemState.ADMITTED.value
        assert await session.scalar(select(func.count()).select_from(Mission)) == 0

    reconcile_script = f"""
import asyncio, uuid
from sqlalchemy import select
from omega.application import campaign_admission_service as r
from omega.infrastructure.database import AsyncWorkerSessionLocal
from omega.infrastructure.models import ContentCampaign
async def main():
    async with AsyncWorkerSessionLocal() as s:
        c=(await s.execute(select(ContentCampaign).where(ContentCampaign.id==uuid.UUID('{campaign_id}')))).scalar_one()
        print(await r.reconcile_campaign(s,c))
asyncio.run(main())
"""
    _subprocess(reconcile_script)
    _subprocess(reconcile_script)

    async with AsyncSessionLocal() as session:
        item = await session.get(ContentCampaignItem, item_ids[0])
        bindings = list((await session.execute(select(ContentCampaignItemExecution))).scalars())
        assert item is not None and item.admission_state == ItemState.MATERIALIZED.value
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ContentCampaignItem)
                .where(ContentCampaignItem.campaign_id == campaign_id)
            )
            == 10
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ContentCampaignItem)
                .where(
                    ContentCampaignItem.campaign_id == campaign_id,
                    ContentCampaignItem.admission_state == ItemState.PENDING.value,
                )
            )
            == 9
        )
        assert len(bindings) == 1
        assert await session.scalar(select(func.count()).select_from(Mission)) == 1
        assert await session.scalar(select(func.count()).select_from(MissionExecution)) == 1
        assert await session.scalar(select(func.count()).select_from(Task)) == 7
        assert await session.scalar(select(func.count()).select_from(TaskDependency)) == 6
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DecisionLog)
                .where(DecisionLog.decision_type == "MISSION_START")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DurableDispatchIntent)
                .where(DurableDispatchIntent.purpose == "MISSION_START_EVALUATION")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_concurrent_materializers_commit_one_complete_lineage(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, item_ids = await _create_campaign(client, item_count=1)
    await _start(channel_id, campaign_id)
    assert await _admit_one(channel_id, campaign_id) == item_ids[0]

    async def materialize():
        async with AsyncSessionLocal() as session:
            return await runtime.materialize_admitted_item(session, campaign_id, item_ids[0])

    first, second = await asyncio.gather(materialize(), materialize())
    assert sum(bool(value) for value in (first, second)) == 1
    async with AsyncSessionLocal() as session:
        bindings = list((await session.execute(select(ContentCampaignItemExecution))).scalars())
        assert len(bindings) == 1
        assert await session.scalar(select(func.count()).select_from(Mission)) == 1
        assert await session.scalar(select(func.count()).select_from(MissionExecution)) == 1
        assert await session.scalar(select(func.count()).select_from(Task)) == 7
        assert await session.scalar(select(func.count()).select_from(TaskDependency)) == 6


@pytest.mark.asyncio
async def test_materialization_rollback_then_recovery_is_atomic(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, item_ids = await _create_campaign(client, item_count=1)
    await _start(channel_id, campaign_id)
    await _admit_one(channel_id, campaign_id)

    authoritative_binding = runtime.ContentCampaignItemExecution
    monkeypatch.setattr(
        runtime,
        "ContentCampaignItemExecution",
        lambda **_: (_ for _ in ()).throw(RuntimeError("crash before binding")),
    )
    async with AsyncSessionLocal() as session:
        with pytest.raises(RuntimeError, match="crash before binding"):
            await runtime.materialize_admitted_item(session, campaign_id, item_ids[0])
        await session.rollback()
    monkeypatch.setattr(runtime, "ContentCampaignItemExecution", authoritative_binding)

    async with AsyncSessionLocal() as session:
        item = await session.get(ContentCampaignItem, item_ids[0])
        assert item is not None and item.admission_state == ItemState.ADMITTED.value
        for model in (
            Mission,
            MissionExecution,
            Task,
            TaskDependency,
            ContentCampaignItemExecution,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        campaign = await session.get(ContentCampaign, campaign_id)
        assert campaign is not None
        await runtime.reconcile_campaign(session, campaign)

    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Mission)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(ContentCampaignItemExecution))
            == 1
        )


@pytest.mark.asyncio
async def test_deterministic_failure_releases_slot_and_transient_retry_is_bounded(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, item_ids = await _create_campaign(client, item_count=3)
    await _start(channel_id, campaign_id)
    await _admit_one(channel_id, campaign_id)

    original_planner = runtime._plan_mission_in_transaction

    async def deterministic_failure(*args, **kwargs):
        raise ValueError("deterministic planning invariant")

    monkeypatch.setattr(runtime, "_plan_mission_in_transaction", deterministic_failure)
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError):
            await runtime.materialize_admitted_item(session, campaign_id, item_ids[0])
        await session.rollback()
        assert await runtime.persist_materialization_failure(
            session, campaign_id, item_ids[0], ValueError("deterministic planning invariant")
        )
    monkeypatch.setattr(runtime, "_plan_mission_in_transaction", original_planner)

    assert await _admit_one(channel_id, campaign_id) == item_ids[1]
    async with AsyncSessionLocal() as session:
        failed = await session.get(ContentCampaignItem, item_ids[0])
        assert failed is not None
        assert failed.admission_state == ItemState.FAILED.value
        assert failed.materialization_attempts == 1
        assert failed.materialization_error_code == "MATERIALIZATION_DETERMINISTIC"
        assert "Traceback" not in (failed.sanitized_materialization_error or "")
        assert await session.scalar(select(func.count()).select_from(Mission)) == 0

    transient_item = item_ids[1]
    transient = OperationalError("statement", {}, SimpleNamespace(sqlstate="40001"))
    for expected_attempt in (1, 2, 3):
        async with AsyncSessionLocal() as session:
            terminal = await runtime.persist_materialization_failure(
                session, campaign_id, transient_item, transient
            )
            item = await session.get(ContentCampaignItem, transient_item)
            assert item is not None and item.materialization_attempts == expected_attempt
            if expected_attempt < 3:
                assert not terminal and item.admission_state == ItemState.ADMITTED.value
                assert item.next_materialization_attempt_at is not None
                await session.execute(
                    update(ContentCampaignItem)
                    .where(ContentCampaignItem.id == transient_item)
                    .values(next_materialization_attempt_at=func.now())
                )
                await session.commit()
            else:
                assert terminal and item.admission_state == ItemState.FAILED.value


@pytest.mark.asyncio
async def test_campaign_and_channel_capacity_races_and_cross_process_lock(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings(channel_cap=2))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_a, _ = await _create_campaign(
            client, item_count=5, max_concurrent=2, title="A"
        )
        _, campaign_b, _ = await _create_campaign(
            client,
            item_count=3,
            max_concurrent=2,
            channel_id=str(channel_id),
            title="B",
        )
    await _start(channel_id, campaign_a)
    await _start(channel_id, campaign_b)

    await asyncio.gather(*[_admit_one(channel_id, campaign_a) for _ in range(4)])
    async with AsyncSessionLocal() as session:
        active_a, _ = await runtime._active_counts(
            session, await session.get(ContentCampaign, campaign_a)
        )
        assert active_a == 2

    monkeypatch.setattr(runtime, "get_settings", lambda: _settings(channel_cap=1))
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ContentCampaignItem)
            .where(ContentCampaignItem.campaign_id == campaign_a)
            .values(admission_state=ItemState.PENDING.value, admitted_at=None)
        )
        await session.commit()
    first, second = await asyncio.gather(
        _admit_one(channel_id, campaign_a), _admit_one(channel_id, campaign_b)
    )
    assert sum(value is not None for value in (first, second)) == 1
    async with AsyncSessionLocal() as session:
        _, channel_active = await runtime._active_counts(
            session, await session.get(ContentCampaign, campaign_a)
        )
        assert channel_active == 1

    script = (
        "import uuid; from omega.application.campaign_admission_service import "
        f"channel_admission_lock_key; print(channel_admission_lock_key(uuid.UUID('{channel_id}')))"
    )
    assert (
        _subprocess(script)
        == _subprocess(script)
        == str(runtime.channel_admission_lock_key(channel_id))
    )


@pytest.mark.asyncio
async def test_pause_cancel_and_gate_off_race_outcomes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings(channel_cap=2))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, item_ids = await _create_campaign(
            client, item_count=3, max_concurrent=2
        )
    await _start(channel_id, campaign_id)
    await _admit_one(channel_id, campaign_id)

    async with AsyncSessionLocal() as pause_session:
        await runtime.pause_campaign(pause_session, channel_id, campaign_id)
    async with AsyncSessionLocal() as materialize_session:
        assert not await runtime.materialize_admitted_item(
            materialize_session, campaign_id, item_ids[0]
        )
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Mission)) == 0

    async with AsyncSessionLocal() as resume_session:
        await runtime.resume_campaign(resume_session, channel_id, campaign_id)
    await _admit_one(channel_id, campaign_id)
    async with AsyncSessionLocal() as materialize_session:
        binding = await runtime.materialize_admitted_item(
            materialize_session, campaign_id, item_ids[0]
        )
        assert binding is not None
    async with AsyncSessionLocal() as pause_session:
        await runtime.pause_campaign(pause_session, channel_id, campaign_id)
    async with AsyncSessionLocal() as session:
        mission = (await session.execute(select(Mission))).scalar_one()
        assert mission.state == "READY"

    async with AsyncSessionLocal() as resume_session:
        await runtime.resume_campaign(resume_session, channel_id, campaign_id)
    await _admit_one(channel_id, campaign_id)
    async with AsyncSessionLocal() as cancel_session:
        await runtime.cancel_campaign(cancel_session, channel_id, campaign_id)
    async with AsyncSessionLocal() as materialize_session:
        assert not await runtime.materialize_admitted_item(
            materialize_session, campaign_id, item_ids[1]
        )

    monkeypatch.setattr(runtime, "get_settings", lambda: _settings(enabled=False))
    async with AsyncSessionLocal() as session:
        campaign = await session.get(ContentCampaign, campaign_id)
        before = await session.scalar(
            select(func.count())
            .select_from(ContentCampaignItem)
            .where(ContentCampaignItem.admission_state == ItemState.PENDING.value)
        )
        await runtime.reconcile_campaign(session, campaign)
        after = await session.scalar(
            select(func.count())
            .select_from(ContentCampaignItem)
            .where(ContentCampaignItem.admission_state == ItemState.PENDING.value)
        )
        assert before == after
        assert (
            await session.get(ContentCampaign, campaign_id)
        ).status == CampaignState.CANCELLED.value
        orphan_count = await session.scalar(
            select(func.count())
            .select_from(Mission)
            .where(~Mission.id.in_(select(ContentCampaignItemExecution.mission_id)))
        )
        assert orphan_count == 0


@pytest.mark.asyncio
async def test_fair_batch_order_prefers_never_admitted_then_priority(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings(channel_cap=2))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, low_id, low_items = await _create_campaign(
            client, item_count=1, channel_id=None, title="low", priority=1
        )
        _, high_id, high_items = await _create_campaign(
            client,
            item_count=1,
            channel_id=str(channel_id),
            title="high",
            priority=9,
        )
        _, mid_id, mid_items = await _create_campaign(
            client,
            item_count=1,
            channel_id=str(channel_id),
            title="mid",
            priority=5,
        )
    for campaign_id in (low_id, high_id, mid_id):
        await _start(channel_id, campaign_id)

    async with AsyncSessionLocal() as session:
        result = await runtime.reconcile_batch(session)
        assert result["reserved"] == 2

    async with AsyncSessionLocal() as session:
        states = {
            item_id: (await session.get(ContentCampaignItem, item_id)).admission_state
            for item_id in (low_items[0], high_items[0], mid_items[0])
        }
        assert states[high_items[0]] == ItemState.MATERIALIZED.value
        assert states[mid_items[0]] == ItemState.MATERIALIZED.value
        assert states[low_items[0]] == ItemState.PENDING.value


@pytest.mark.asyncio
async def test_migration_024_preserves_legacy_lineage_and_backfills_runtime_fields(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings(channel_cap=2))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, item_ids = await _create_campaign(
            client, item_count=2, max_concurrent=2
        )
    await _start(channel_id, campaign_id)
    binding_ids: list[uuid.UUID] = []
    mission_ids: list[uuid.UUID] = []
    for item_id in item_ids:
        assert await _admit_one(channel_id, campaign_id) == item_id
        async with AsyncSessionLocal() as session:
            binding = await runtime.materialize_admitted_item(session, campaign_id, item_id)
            assert binding
            binding_ids.append(binding.id)
            mission_ids.append(binding.mission_id)

    def migrate(revision: str) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade" if revision == "head" else "downgrade",
                revision,
            ],
            cwd="/app" if os.name != "nt" else os.path.join(os.getcwd(), "backend"),
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

    try:
        migrate("023")
        check = _subprocess(
            f"""
import asyncio, uuid
from sqlalchemy import text
from omega.infrastructure.database import AsyncWorkerSessionLocal
async def main():
    async with AsyncWorkerSessionLocal() as s:
        values = (await s.execute(text(\"SELECT c.status, e.status, count(b.id) FROM content_campaigns c JOIN content_campaign_executions e ON e.campaign_id=c.id JOIN content_campaign_item_executions b ON b.campaign_execution_id=e.id WHERE c.id=:id GROUP BY c.status,e.status\"), {{'id': uuid.UUID('{campaign_id}')}})).one()
        print('|'.join(map(str, values)))
asyncio.run(main())
"""
        )
        assert check == "READY|MATERIALIZED|2"
    finally:
        migrate("head")

    async with AsyncSessionLocal() as session:
        campaign = await session.get(ContentCampaign, campaign_id)
        assert campaign is not None
        assert campaign.orchestration_mode == "LEGACY_UPFRONT"
        assert campaign.plan_checksum_version == 1
        assert campaign.max_concurrent_missions is None
        for item_id, binding_id, mission_id in zip(item_ids, binding_ids, mission_ids, strict=True):
            item = await session.get(ContentCampaignItem, item_id)
            binding = await session.get(ContentCampaignItemExecution, binding_id)
            assert item is not None and item.item_key == f"selection-run:{item.selection_run_id}"
            assert item.admission_state == ItemState.MATERIALIZED.value
            assert binding is not None and binding.mission_id == mission_id


@pytest.mark.asyncio
async def test_lazy_legacy_authority_exclusion(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "get_settings", lambda: _settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, campaign_id, _ = await _create_campaign(client, item_count=1)
        started, materialized = await asyncio.gather(
            client.post(
                f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/start",
                json={"actor": "cb21"},
            ),
            client.post(
                f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
                json={"actor": "cb21"},
            ),
        )
        assert started.status_code == 200
        assert materialized.status_code == 409
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Mission)) == 0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        legacy_channel_id, legacy_campaign_id, _ = await _create_campaign(client, item_count=1)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ContentCampaign)
                .where(ContentCampaign.id == legacy_campaign_id)
                .values(orchestration_mode="LEGACY_UPFRONT", max_concurrent_missions=None)
            )
            await session.commit()
        lazy_start = await client.post(
            f"/api/v1/channels/{legacy_channel_id}/campaigns/{legacy_campaign_id}/start",
            json={"actor": "cb21"},
        )
        assert lazy_start.status_code == 409
