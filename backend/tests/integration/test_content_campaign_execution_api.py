"""Integration tests for P19-D: Bounded Campaign-to-Mission Fan-Out and Durable Lineage."""

from __future__ import annotations

import datetime
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application import mission_service
from omega.application.mission_service import (
    _create_mission_in_transaction,
    _plan_mission_in_transaction,
)
from omega.domain.content_campaign_execution import (
    compute_fanout_policy_checksum,
)
from omega.domain.mission import MissionCreate, MissionTriggerType
from omega.infrastructure.models import (
    ChannelDNARevision,
    ContentCampaignExecution,
    ContentCampaignItemExecution,
    ContentGenerationRequest,
    DecisionLog,
    DurableDispatchIntent,
    Mission,
    MissionExecution,
    ProductionRenderJob,
    ProductionRequest,
    PublishIntent,
    ResearchRequest,
    ScheduleDecision,
    ScheduleReservation,
    Task,
    TaskDependency,
    TopicCandidate,
)
from omega.main import app


async def _create_channel_and_campaign(
    client: AsyncClient,
    item_count: int = 2,
    priority: int = 2,
) -> tuple[str, str, str, list[str]]:
    """Helper to create channel, DNA revision, finalized selection runs, and campaign."""
    # 1. Create channel
    chan_resp = await client.post(
        "/api/v1/channels",
        json={
            "name": "P19-D Fanout Channel",
            "slug": f"p19d-channel-{uuid.uuid4().hex[:10]}",
            "platform": "YOUTUBE",
            "primary_language": "en",
            "target_region": "US",
        },
    )
    assert chan_resp.status_code == 201, chan_resp.text
    channel_id = chan_resp.json()["id"]

    # 2. Get initial DNA revision
    dna_resp = await client.get(f"/api/v1/channels/{channel_id}/dna/revisions")
    assert dna_resp.status_code == 200, dna_resp.text
    dna_revisions = dna_resp.json()
    dna_revision_id = dna_revisions[0]["id"]

    # 3. Create candidates and finalized selection runs
    run_ids: list[str] = []
    candidate_ids: list[str] = []
    for run_idx in range(item_count):
        cand_resp = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": f"Candidate Subject {run_idx}_{uuid.uuid4().hex[:6]}",
                "summary": f"Summary for candidate {run_idx}",
                "keywords": ["fanout", f"topic_{run_idx}"],
                "source_type": "MANUAL",
                "source_name": "p19d-test",
                "source_ref": f"test://p19d/{run_idx}",
            },
        )
        assert cand_resp.status_code == 201, cand_resp.text
        cand_id = cand_resp.json()["id"]
        candidate_ids.append(cand_id)

        # Create selection run with candidate
        run_resp = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={
                "candidate_ids": [cand_id],
                "idempotency_key": f"p19d-run-{uuid.uuid4().hex}",
            },
        )
        assert run_resp.status_code == 201, run_resp.text
        run_id = run_resp.json()["id"]

        # Finalize run
        fin_resp = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run_id}/finalize",
            json={"actor": "p19d-test-runner"},
        )
        assert fin_resp.status_code == 200, fin_resp.text
        run_ids.append(run_id)

    # 4. Create campaign
    items_payload = []
    for idx, r_id in enumerate(run_ids):
        content_type = "YOUTUBE_LONGFORM" if idx % 2 == 0 else "YOUTUBE_SHORT"
        rel_at = (
            datetime.datetime(2026, 10, 1, 12, 0, 0, tzinfo=datetime.UTC)
            if idx == 0
            else None
        )
        items_payload.append(
            {
                "selection_run_id": r_id,
                "target_content_type": content_type,
                "planned_release_at": rel_at.isoformat() if rel_at else None,
            }
        )

    camp_resp = await client.post(
        f"/api/v1/channels/{channel_id}/campaigns",
        json={
            "title": f"P19-D Campaign {uuid.uuid4().hex[:6]}",
            "objective": "Verify bounded mission fanout and execution lineage",
            "priority": priority,
            "idempotency_key": f"camp-{uuid.uuid4().hex}",
            "created_by": "p19d-lead",
            "items": items_payload,
        },
    )
    assert camp_resp.status_code == 201, camp_resp.text
    campaign_id = camp_resp.json()["id"]

    return channel_id, dna_revision_id, campaign_id, candidate_ids


@pytest.mark.asyncio
async def test_campaign_execution_schema_constraints_and_foreign_keys(
    db_session: AsyncSession,
) -> None:
    """Verify migration 021 schema, check constraints, unique constraints, and foreign keys."""
    connection = await db_session.connection()

    exec_uniques = await connection.run_sync(
        lambda conn: inspect(conn).get_unique_constraints("content_campaign_executions")
    )
    exec_checks = await connection.run_sync(
        lambda conn: inspect(conn).get_check_constraints("content_campaign_executions")
    )
    exec_fks = await connection.run_sync(
        lambda conn: inspect(conn).get_foreign_keys("content_campaign_executions")
    )

    assert {item["name"] for item in exec_uniques} >= {
        "uq_content_campaign_executions_campaign_id"
    }
    assert {item["name"] for item in exec_checks} >= {
        "ck_content_campaign_executions_status",
        "ck_content_campaign_executions_item_count_range",
    }
    fk_by_col = {fk["constrained_columns"][0]: fk for fk in exec_fks}
    assert fk_by_col["campaign_id"]["options"].get("ondelete") == "RESTRICT"
    assert fk_by_col["channel_id"]["options"].get("ondelete") == "RESTRICT"
    assert fk_by_col["channel_dna_revision_id"]["options"].get("ondelete") == "RESTRICT"

    item_exec_uniques = await connection.run_sync(
        lambda conn: inspect(conn).get_unique_constraints("content_campaign_item_executions")
    )
    item_exec_checks = await connection.run_sync(
        lambda conn: inspect(conn).get_check_constraints("content_campaign_item_executions")
    )
    item_exec_fks = await connection.run_sync(
        lambda conn: inspect(conn).get_foreign_keys("content_campaign_item_executions")
    )

    assert {item["name"] for item in item_exec_uniques} >= {
        "uq_content_campaign_item_executions_item_id",
        "uq_content_campaign_item_executions_mission_id",
        "uq_content_campaign_item_executions_mission_execution_id",
        "uq_content_campaign_item_executions_exec_pos",
    }
    assert {item["name"] for item in item_exec_checks} >= {
        "ck_content_campaign_item_executions_position_positive",
    }
    item_fk_by_col = {fk["constrained_columns"][0]: fk for fk in item_exec_fks}
    assert item_fk_by_col["campaign_execution_id"]["options"].get("ondelete") == "RESTRICT"
    assert item_fk_by_col["campaign_item_id"]["options"].get("ondelete") == "RESTRICT"
    assert item_fk_by_col["mission_id"]["options"].get("ondelete") == "RESTRICT"
    assert item_fk_by_col["mission_execution_id"]["options"].get("ondelete") == "RESTRICT"


@pytest.mark.asyncio
async def test_materialize_campaign_success_and_dag_topology(
    db_session: AsyncSession,
) -> None:
    """Test successful campaign materialization, 1:1 item-to-mission mapping, and 7-stage DAG topology."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, dna_rev_id, campaign_id, candidate_ids = await _create_channel_and_campaign(
            client, item_count=2, priority=25
        )

        mat_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "operator-alice"},
        )
        assert mat_resp.status_code == 201, mat_resp.text
        data = mat_resp.json()

        assert data["campaign_id"] == campaign_id
        assert data["channel_id"] == channel_id
        assert data["channel_dna_revision_id"] == dna_rev_id
        assert data["status"] == "MATERIALIZED"
        assert data["item_count"] == 2
        assert data["materialized_by"] == "operator-alice"
        assert data["fanout_policy_checksum"] == compute_fanout_policy_checksum()
        assert len(data["items"]) == 2

        for idx, item in enumerate(data["items"]):
            assert item["position"] == idx + 1
            assert item["mission_state"] == "READY"
            assert item["mission_execution_state"] == "PLANNED"

            # Check DB state for Mission and MissionExecution
            mission_id = uuid.UUID(item["mission_id"])
            exec_id = uuid.UUID(item["mission_execution_id"])

            m_res = await db_session.execute(select(Mission).where(Mission.id == mission_id))
            mission = m_res.scalar_one()
            assert mission.state == "READY"
            assert mission.started_at is None
            # Priority clamped 1..10 from campaign's 25 -> 10
            assert mission.priority == 10
            assert mission.autonomy_level == "SUPERVISED"

            lineage = mission.metadata_["campaign_lineage"]
            assert lineage["campaign_id"] == campaign_id
            assert lineage["position"] == idx + 1
            if idx == 0:
                assert lineage["planned_release_at"] == "2026-10-01T12:00:00+00:00"
            else:
                assert lineage["planned_release_at"] is None

            # Verify DAG tasks and dependencies
            tasks_res = await db_session.execute(
                select(Task).where(Task.execution_id == exec_id).order_by(Task.priority.asc())
            )
            tasks = list(tasks_res.scalars().all())
            assert len(tasks) == 7
            task_types = [t.task_type for t in tasks]
            assert task_types == [
                "strategy",
                "topic_discovery",
                "research",
                "content_generation",
                "production",
                "qa",
                "publish",
            ]

            qa_task = next(t for t in tasks if t.task_type == "qa")
            assert qa_task.requires_approval is True

            # Verify planned task seeds
            td_task = next(t for t in tasks if t.task_type == "topic_discovery")
            assert td_task.input["topic_candidate_id"] == candidate_ids[idx]

            cg_task = next(t for t in tasks if t.task_type == "content_generation")
            expected_type = "YOUTUBE_LONGFORM" if idx == 0 else "YOUTUBE_SHORT"
            assert cg_task.input["canonical_content"]["content_type"] == expected_type

            # Verify dependencies
            deps_res = await db_session.execute(
                select(TaskDependency).where(TaskDependency.mission_id == mission_id)
            )
            deps = list(deps_res.scalars().all())
            assert len(deps) == 6


@pytest.mark.asyncio
async def test_explicit_dna_override_validation_fail_closed(
    db_session: AsyncSession,
) -> None:
    """Explicit DNA override validation: if DNA revision belongs to another channel, fail closed with zero partial persistence."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        chan1_resp = await client.post(
            "/api/v1/channels",
            json={
                "name": "Channel 1",
                "slug": f"c1-{uuid.uuid4().hex[:10]}",
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        assert chan1_resp.status_code == 201
        c1_id = uuid.UUID(chan1_resp.json()["id"])

        chan2_resp = await client.post(
            "/api/v1/channels",
            json={
                "name": "Channel 2",
                "slug": f"c2-{uuid.uuid4().hex[:10]}",
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        assert chan2_resp.status_code == 201
        c2_id = uuid.UUID(chan2_resp.json()["id"])

        dna2_resp = await client.get(f"/api/v1/channels/{c2_id}/dna/revisions")
        assert dna2_resp.status_code == 200
        c2_dna_rev_id = uuid.UUID(dna2_resp.json()[0]["id"])

        # Create draft mission for channel 1
        m_in = MissionCreate(
            title="Channel 1 Mission",
            objective="Test DNA Override",
            channel_id=c1_id,
        )
        mission = await _create_mission_in_transaction(db_session, m_in)

        # Count tasks and executions before call
        tasks_before = (await db_session.execute(select(func.count(Task.id)))).scalar_one()
        execs_before = (await db_session.execute(select(func.count(MissionExecution.id)))).scalar_one()

        # Call _plan_mission_in_transaction with channel 2's DNA revision
        with pytest.raises(ValueError, match="belongs to channel"):
            await _plan_mission_in_transaction(
                db_session,
                mission,
                channel_dna_revision_id=c2_dna_rev_id,
                trigger_type=MissionTriggerType.API,
            )

        # Roll back transaction
        await db_session.rollback()

        tasks_after = (await db_session.execute(select(func.count(Task.id)))).scalar_one()
        execs_after = (await db_session.execute(select(func.count(MissionExecution.id)))).scalar_one()
        assert tasks_after == tasks_before
        assert execs_after == execs_before


@pytest.mark.asyncio
async def test_exact_dna_pinning_ignores_newer_channel_revisions(
    db_session: AsyncSession,
) -> None:
    """Campaign materialization must strictly pin ContentCampaign.channel_dna_revision_id, ignoring newer revisions."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, original_dna_rev_id, campaign_id, _ = await _create_channel_and_campaign(
            client, item_count=1
        )

        # Create a newer DNA revision on this channel
        new_dna_rev_id = uuid.uuid4()
        db_session.add(
            ChannelDNARevision(
                id=new_dna_rev_id,
                channel_id=uuid.UUID(channel_id),
                version=2,
                snapshot={"tone": "NEW_TONE", "style": "NEW_STYLE"},
                change_reason="Updated DNA test",
                actor="dna-tester",
            )
        )
        await db_session.commit()

        # Materialize campaign
        mat_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "dna-tester"},
        )
        assert mat_resp.status_code == 201, mat_resp.text
        data = mat_resp.json()

        # The campaign execution MUST pin original_dna_rev_id, NOT new_dna_rev_id
        assert data["channel_dna_revision_id"] == original_dna_rev_id
        mission_exec_id = uuid.UUID(data["items"][0]["mission_execution_id"])

        m_exec = (
            await db_session.execute(
                select(MissionExecution).where(MissionExecution.id == mission_exec_id)
            )
        ).scalar_one()
        assert str(m_exec.channel_dna_revision_id) == original_dna_rev_id
        assert str(m_exec.channel_dna_revision_id) != new_dna_rev_id


@pytest.mark.asyncio
async def test_candidate_display_authority_uses_immutable_snapshot(
    db_session: AsyncSession,
) -> None:
    """Mission title/objective must use ContentSelectionDecision.candidate_title_snapshot, not mutated TopicCandidate.title."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, _, campaign_id, candidate_ids = await _create_channel_and_campaign(
            client, item_count=1
        )
        target_cand_id = uuid.UUID(candidate_ids[0])

        # Mutate TopicCandidate.title in the DB
        cand = (
            await db_session.execute(
                select(TopicCandidate).where(TopicCandidate.id == target_cand_id)
            )
        ).scalar_one()
        original_title = cand.title
        cand.title = "MUTATED_TOPIC_CANDIDATE_TITLE_AFTER_PLANNING"
        await db_session.commit()

        # Materialize campaign
        mat_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "snapshot-tester"},
        )
        assert mat_resp.status_code == 201, mat_resp.text
        data = mat_resp.json()

        mission_id = uuid.UUID(data["items"][0]["mission_id"])
        mission = (
            await db_session.execute(select(Mission).where(Mission.id == mission_id))
        ).scalar_one()

        # Must contain original title from snapshot, NOT mutated title
        assert original_title in mission.title
        assert "MUTATED_TOPIC_CANDIDATE_TITLE_AFTER_PLANNING" not in mission.title
        assert original_title in mission.objective


@pytest.mark.asyncio
async def test_materialization_replay_idempotency_and_integrity_validation(
    db_session: AsyncSession,
) -> None:
    """Replay must return exact historical execution without creating any new entities. Corrupt lineage must fail closed."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, _, campaign_id, _ = await _create_channel_and_campaign(
            client, item_count=2
        )

        # Initial materialization
        mat1 = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "first-actor"},
        )
        assert mat1.status_code == 201
        data1 = mat1.json()

        # Count all entities in DB
        counts_before = {
            "missions": (await db_session.execute(select(func.count(Mission.id)))).scalar_one(),
            "executions": (await db_session.execute(select(func.count(MissionExecution.id)))).scalar_one(),
            "tasks": (await db_session.execute(select(func.count(Task.id)))).scalar_one(),
            "deps": (await db_session.execute(select(func.count(TaskDependency.id)))).scalar_one(),
            "decision_logs": (await db_session.execute(select(func.count(DecisionLog.id)))).scalar_one(),
            "bindings": (await db_session.execute(select(func.count(ContentCampaignItemExecution.id)))).scalar_one(),
            "campaign_executions": (await db_session.execute(select(func.count(ContentCampaignExecution.id)))).scalar_one(),
        }

        # Replay with DIFFERENT actor
        mat2 = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "different-actor"},
        )
        assert mat2.status_code == 200
        data2 = mat2.json()

        assert data2["id"] == data1["id"]
        # Preserves original actor
        assert data2["materialized_by"] == "first-actor"
        assert len(data2["items"]) == len(data1["items"])
        for it1, it2 in zip(data1["items"], data2["items"], strict=True):
            assert it1["id"] == it2["id"]
            assert it1["mission_id"] == it2["mission_id"]

        # Verify zero new entity rows were created
        counts_after = {
            "missions": (await db_session.execute(select(func.count(Mission.id)))).scalar_one(),
            "executions": (await db_session.execute(select(func.count(MissionExecution.id)))).scalar_one(),
            "tasks": (await db_session.execute(select(func.count(Task.id)))).scalar_one(),
            "deps": (await db_session.execute(select(func.count(TaskDependency.id)))).scalar_one(),
            "decision_logs": (await db_session.execute(select(func.count(DecisionLog.id)))).scalar_one(),
            "bindings": (await db_session.execute(select(func.count(ContentCampaignItemExecution.id)))).scalar_one(),
            "campaign_executions": (await db_session.execute(select(func.count(ContentCampaignExecution.id)))).scalar_one(),
        }
        assert counts_after == counts_before

        # Corrupt mission metadata in DB to test fail-closed integrity
        m_id = uuid.UUID(data1["items"][0]["mission_id"])
        m = (await db_session.execute(select(Mission).where(Mission.id == m_id))).scalar_one()
        m.metadata_ = {"campaign_lineage": {"corrupted": True}}
        await db_session.commit()

        # Replay and GET must fail with 500 integrity error
        replay_fail = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "any-actor"},
        )
        assert replay_fail.status_code == 500
        assert "integrity violation" in replay_fail.text

        get_fail = await client.get(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/execution"
        )
        assert get_fail.status_code == 500
        assert "integrity violation" in get_fail.text


@pytest.mark.asyncio
async def test_historical_read_allows_future_mission_lifecycle_progression(
    db_session: AsyncSession,
) -> None:
    """Historical read integrity allows legitimate Mission lifecycle progression (e.g. CANCELLED) and exposes current state."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, _, campaign_id, _ = await _create_channel_and_campaign(
            client, item_count=2
        )

        mat = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "lifecycle-tester"},
        )
        assert mat.status_code == 201
        data = mat.json()

        mission_a_id = uuid.UUID(data["items"][0]["mission_id"])

        # Legitimate lifecycle transition: cancel Mission A
        cancel_resp = await client.post(f"/api/v1/missions/{mission_a_id}/cancel")
        assert cancel_resp.status_code == 200, cancel_resp.text

        # GET /campaigns/{campaign_id}/execution
        get_resp = await client.get(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/execution"
        )
        assert get_resp.status_code == 200, get_resp.text
        get_data = get_resp.json()

        assert get_data["status"] == "MATERIALIZED"
        assert get_data["items"][0]["mission_state"] == "CANCELLED"
        assert get_data["items"][1]["mission_state"] == "READY"
        assert get_data["items"][0]["mission_id"] == str(mission_a_id)

        # Replay materialization after legitimate cancellation must return same campaign execution without replacing missions
        replay_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "lifecycle-tester"},
        )
        assert replay_resp.status_code == 200, replay_resp.text
        assert replay_resp.json()["items"][0]["mission_state"] == "CANCELLED"
        assert replay_resp.json()["items"][0]["mission_id"] == str(mission_a_id)


@pytest.mark.asyncio
async def test_atomic_rollback_all_seven_authorities(
    db_session: AsyncSession,
) -> None:
    """If failure occurs while materializing item 2, rollback must leave zero delta across all seven authorities."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, _, campaign_id, _ = await _create_channel_and_campaign(
            client, item_count=2
        )

        # Count baseline rows across all 7 authorities
        c_camp_execs = (await db_session.execute(select(func.count(ContentCampaignExecution.id)))).scalar_one()
        c_item_execs = (await db_session.execute(select(func.count(ContentCampaignItemExecution.id)))).scalar_one()
        c_missions = (await db_session.execute(select(func.count(Mission.id)))).scalar_one()
        c_mission_execs = (await db_session.execute(select(func.count(MissionExecution.id)))).scalar_one()
        c_tasks = (await db_session.execute(select(func.count(Task.id)))).scalar_one()
        c_deps = (await db_session.execute(select(func.count(TaskDependency.id)))).scalar_one()
        c_decisions = (await db_session.execute(select(func.count(DecisionLog.id)))).scalar_one()

        # Monkeypatch _plan_mission_in_transaction to fail on the second call
        original_plan = mission_service._plan_mission_in_transaction
        call_count = 0

        async def failing_plan(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated failure during item 2 materialization")
            return await original_plan(*args, **kwargs)

        with (
            patch(
                "omega.application.content_campaign_execution_service._plan_mission_in_transaction",
                side_effect=failing_plan,
            ),
            pytest.raises(RuntimeError, match="Simulated failure during item 2 materialization"),
        ):
            await client.post(
                f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
                json={"actor": "atomic-tester"},
            )

        # Verify delta = 0 for all 7 authorities
        assert (await db_session.execute(select(func.count(ContentCampaignExecution.id)))).scalar_one() == c_camp_execs
        assert (await db_session.execute(select(func.count(ContentCampaignItemExecution.id)))).scalar_one() == c_item_execs
        assert (await db_session.execute(select(func.count(Mission.id)))).scalar_one() == c_missions
        assert (await db_session.execute(select(func.count(MissionExecution.id)))).scalar_one() == c_mission_execs
        assert (await db_session.execute(select(func.count(Task.id)))).scalar_one() == c_tasks
        assert (await db_session.execute(select(func.count(TaskDependency.id)))).scalar_one() == c_deps
        assert (await db_session.execute(select(func.count(DecisionLog.id)))).scalar_one() == c_decisions


@pytest.mark.asyncio
async def test_zero_downstream_dispatch_side_effects(
    db_session: AsyncSession,
) -> None:
    """Materialization must create zero execution-side-effect rows across all durable outbox tables."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, _, campaign_id, _ = await _create_channel_and_campaign(
            client, item_count=2
        )

        # Baseline outbox counts
        c_sched_dec = (await db_session.execute(select(func.count(ScheduleDecision.id)))).scalar_one()
        c_sched_res = (await db_session.execute(select(func.count(ScheduleReservation.id)))).scalar_one()
        c_dispatch = (await db_session.execute(select(func.count(DurableDispatchIntent.id)))).scalar_one()
        c_research = (await db_session.execute(select(func.count(ResearchRequest.id)))).scalar_one()
        c_content = (await db_session.execute(select(func.count(ContentGenerationRequest.id)))).scalar_one()
        c_prod_req = (await db_session.execute(select(func.count(ProductionRequest.id)))).scalar_one()
        c_prod_render = (await db_session.execute(select(func.count(ProductionRenderJob.id)))).scalar_one()
        c_publish = (await db_session.execute(select(func.count(PublishIntent.id)))).scalar_one()

        mat_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
            json={"actor": "outbox-tester"},
        )
        assert mat_resp.status_code == 201

        # Assert delta = 0 for all 8 tables
        assert (await db_session.execute(select(func.count(ScheduleDecision.id)))).scalar_one() == c_sched_dec
        assert (await db_session.execute(select(func.count(ScheduleReservation.id)))).scalar_one() == c_sched_res
        assert (await db_session.execute(select(func.count(DurableDispatchIntent.id)))).scalar_one() == c_dispatch
        assert (await db_session.execute(select(func.count(ResearchRequest.id)))).scalar_one() == c_research
        assert (await db_session.execute(select(func.count(ContentGenerationRequest.id)))).scalar_one() == c_content
        assert (await db_session.execute(select(func.count(ProductionRequest.id)))).scalar_one() == c_prod_req
        assert (await db_session.execute(select(func.count(ProductionRenderJob.id)))).scalar_one() == c_prod_render
        assert (await db_session.execute(select(func.count(PublishIntent.id)))).scalar_one() == c_publish


@pytest.mark.asyncio
async def test_real_same_campaign_concurrency(
    db_session: AsyncSession,
) -> None:
    """Two concurrent materialization requests serialize cleanly on the campaign row lock, returning the same execution."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, _, campaign_id, _ = await _create_channel_and_campaign(
            client, item_count=2
        )

        import asyncio

        res1, res2 = await asyncio.gather(
            client.post(
                f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
                json={"actor": "actor-1"},
            ),
            client.post(
                f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
                json={"actor": "actor-2"},
            ),
        )

        assert res1.status_code in (200, 201), res1.text
        assert res2.status_code in (200, 201), res2.text
        data1 = res1.json()
        data2 = res2.json()

        # Both return the exact same execution ID
        assert data1["id"] == data2["id"]
        assert data1["status"] == "MATERIALIZED"
        assert data2["status"] == "MATERIALIZED"

        # Verify exact counts in DB
        c_id = uuid.UUID(campaign_id)
        exec_count = (
            await db_session.execute(
                select(func.count(ContentCampaignExecution.id)).where(
                    ContentCampaignExecution.campaign_id == c_id
                )
            )
        ).scalar_one()
        assert exec_count == 1

        item_exec_count = (
            await db_session.execute(
                select(func.count(ContentCampaignItemExecution.id)).where(
                    ContentCampaignItemExecution.campaign_execution_id == uuid.UUID(data1["id"])
                )
            )
        ).scalar_one()
        assert item_exec_count == 2

        mission_ids = [uuid.UUID(it["mission_id"]) for it in data1["items"]]
        missions_in_db = (
            await db_session.execute(
                select(func.count(Mission.id)).where(Mission.id.in_(mission_ids))
            )
        ).scalar_one()
        assert missions_in_db == 2

        exec_ids = [uuid.UUID(it["mission_execution_id"]) for it in data1["items"]]
        mission_execs_in_db = (
            await db_session.execute(
                select(func.count(MissionExecution.id)).where(MissionExecution.id.in_(exec_ids))
            )
        ).scalar_one()
        assert mission_execs_in_db == 2

        tasks_in_db = (
            await db_session.execute(
                select(func.count(Task.id)).where(Task.execution_id.in_(exec_ids))
            )
        ).scalar_one()
        assert tasks_in_db == 14  # 7 * 2

        deps_in_db = (
            await db_session.execute(
                select(func.count(TaskDependency.id)).where(TaskDependency.mission_id.in_(mission_ids))
            )
        ).scalar_one()
        assert deps_in_db == 12  # 6 * 2


@pytest.mark.asyncio
async def test_integrityerror_reconciliation_reloads_validated_winner(
    db_session: AsyncSession,
) -> None:
    """When commit raises an IntegrityError (e.g. race condition), the production handler rolls back, reloads the durable winner, validates integrity, and returns it."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, _, campaign_id, _ = await _create_channel_and_campaign(
            client, item_count=1
        )

        from unittest.mock import patch

        from sqlalchemy.exc import IntegrityError

        orig_commit = AsyncSession.commit
        raised = False

        async def commit_then_raise_once(self: AsyncSession) -> None:
            nonlocal raised
            await orig_commit(self)
            if not raised:
                raised = True
                raise IntegrityError(
                    "duplicate key value violates unique constraint 'uq_content_campaign_executions_campaign_id'",
                    params={},
                    orig=Exception("synthetic race integrity error"),
                )

        with patch.object(AsyncSession, "commit", commit_then_raise_once):
            resp = await client.post(
                f"/api/v1/channels/{channel_id}/campaigns/{campaign_id}/materialize",
                json={"actor": "reconciled-actor"},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["campaign_id"] == campaign_id
        assert data["status"] == "MATERIALIZED"
        assert len(data["items"]) == 1

        # Verify exactly one physical materialization exists
        c_id = uuid.UUID(campaign_id)
        exec_count = (
            await db_session.execute(
                select(func.count(ContentCampaignExecution.id)).where(
                    ContentCampaignExecution.campaign_id == c_id
                )
            )
        ).scalar_one()
        assert exec_count == 1

        # No duplicate missions
        m_id = uuid.UUID(data["items"][0]["mission_id"])
        missions = (
            await db_session.execute(
                select(func.count(Mission.id)).where(Mission.id == m_id)
            )
        ).scalar_one()
        assert missions == 1
