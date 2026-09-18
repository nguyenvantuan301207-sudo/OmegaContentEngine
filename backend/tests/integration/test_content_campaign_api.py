"""Integration tests for canonical content campaign planning API and contracts."""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.infrastructure.models import (
    ChannelDNARevision,
    ContentCampaign,
    ContentCampaignItem,
    ContentSelectionRun,
)
from omega.main import app


async def _create_channel_and_finalized_runs(
    client: AsyncClient,
    count: int = 2,
    *,
    override_second: bool = True,
) -> tuple[str, list[dict]]:
    channel_response = await client.post(
        "/api/v1/channels",
        json={
            "name": "P19-C Campaign Channel",
            "slug": f"p19c-channel-{uuid.uuid4().hex[:10]}",
            "platform": "YOUTUBE",
            "primary_language": "en",
            "target_region": "US",
        },
    )
    assert channel_response.status_code == 201, channel_response.text
    channel_id = channel_response.json()["id"]

    runs: list[dict] = []
    for run_idx in range(count):
        cand_ids: list[str] = []
        for cand_idx in range(3):
            cand_resp = await client.post(
                f"/api/v1/channels/{channel_id}/topics/candidates",
                json={
                    "title": f"Campaign Candidate {run_idx}_{cand_idx}_{uuid.uuid4().hex[:6]}",
                    "summary": f"Summary for run {run_idx} candidate {cand_idx}",
                    "keywords": ["campaign", f"topic{cand_idx}"],
                    "source_type": "MANUAL",
                    "source_name": "p19c-test",
                    "source_ref": f"test://p19c/{run_idx}/{cand_idx}",
                },
            )
            assert cand_resp.status_code == 201, cand_resp.text
            cand_ids.append(cand_resp.json()["id"])

        create_run_resp = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": f"p19c-run-{uuid.uuid4().hex}",
            },
        )
        assert create_run_resp.status_code == 201, create_run_resp.text
        run_data = create_run_resp.json()
        run_id = run_data["id"]

        finalize_url = f"/api/v1/channels/{channel_id}/topics/selection-runs/{run_id}/finalize"
        if run_idx == 1 and override_second:
            override_cand = next(
                c for c in cand_ids if c != run_data["recommended_candidate_id"]
            )
            fin_resp = await client.post(
                finalize_url,
                json={
                    "selected_candidate_id": override_cand,
                    "actor": "lead-editor",
                    "override_reason": "Editorial priority for this cycle",
                },
            )
        else:
            fin_resp = await client.post(
                finalize_url,
                json={"actor": "policy-system"},
            )
        assert fin_resp.status_code == 200, fin_resp.text
        runs.append(fin_resp.json())

    return channel_id, runs


@pytest.mark.asyncio
async def test_campaign_schema_constraints_and_foreign_keys(
    db_session: AsyncSession,
) -> None:
    connection = await db_session.connection()

    campaign_uniques = await connection.run_sync(
        lambda conn: inspect(conn).get_unique_constraints("content_campaigns")
    )
    campaign_checks = await connection.run_sync(
        lambda conn: inspect(conn).get_check_constraints("content_campaigns")
    )
    item_uniques = await connection.run_sync(
        lambda conn: inspect(conn).get_unique_constraints("content_campaign_items")
    )
    item_checks = await connection.run_sync(
        lambda conn: inspect(conn).get_check_constraints("content_campaign_items")
    )
    item_fks = await connection.run_sync(
        lambda conn: inspect(conn).get_foreign_keys("content_campaign_items")
    )

    assert {item["name"] for item in campaign_uniques} >= {
        "uq_content_campaigns_channel_idempotency"
    }
    assert {item["name"] for item in campaign_checks} >= {
        "ck_content_campaigns_status",
        "ck_content_campaigns_item_count_range",
        "ck_content_campaigns_priority_positive",
    }
    assert {item["name"] for item in item_uniques} >= {
        "uq_content_campaign_items_campaign_position",
        "uq_content_campaign_items_selection_run",
        "uq_content_campaign_items_campaign_candidate",
    }
    assert {item["name"] for item in item_checks} >= {
        "ck_content_campaign_items_position_positive",
        "ck_content_campaign_items_content_type",
    }

    # Verify ON DELETE RESTRICT on all foreign keys
    fk_by_col = {fk["constrained_columns"][0]: fk for fk in item_fks}
    assert fk_by_col["campaign_id"]["options"].get("ondelete") == "RESTRICT"
    assert fk_by_col["selection_run_id"]["options"].get("ondelete") == "RESTRICT"
    assert fk_by_col["selection_decision_id"]["options"].get("ondelete") == "RESTRICT"
    assert fk_by_col["topic_candidate_id"]["options"].get("ondelete") == "RESTRICT"


@pytest.mark.asyncio
async def test_campaign_creation_mixed_formats_and_selection_modes(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=2, override_second=True)

        assert runs[0]["selection_mode"] == "POLICY"
        assert runs[1]["selection_mode"] == "OVERRIDE"

        t1 = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=datetime.UTC)
        t2 = datetime.datetime(2026, 9, 21, 10, 0, 0, tzinfo=datetime.UTC)

        payload = {
            "title": "Summer Growth Campaign",
            "objective": "Accelerate subscriber growth with mixed content types",
            "priority": 2,
            "idempotency_key": f"camp-create-{uuid.uuid4().hex}",
            "created_by": "strategy-planner",
            "items": [
                {
                    "selection_run_id": runs[0]["id"],
                    "target_content_type": "YOUTUBE_LONGFORM",
                    "planned_release_at": t1.isoformat(),
                },
                {
                    "selection_run_id": runs[1]["id"],
                    "target_content_type": "YOUTUBE_SHORT",
                    "planned_release_at": t2.isoformat(),
                },
            ],
        }

        resp = await client.post(f"/api/v1/channels/{channel_id}/campaigns", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()

        assert data["channel_id"] == channel_id
        assert data["channel_dna_revision_id"] == runs[0]["channel_dna_revision_id"]
        assert data["status"] == "READY"
        assert data["item_count"] == 2
        assert len(data["plan_checksum"]) == 64

        items = data["items"]
        assert len(items) == 2

        # Item 1: POLICY, YOUTUBE_LONGFORM, position 1
        assert items[0]["position"] == 1
        assert items[0]["selection_run_id"] == runs[0]["id"]
        assert items[0]["target_content_type"] == "YOUTUBE_LONGFORM"
        assert items[0]["selection_mode"] == "POLICY"
        assert items[0]["candidate_title_snapshot"] != ""
        assert items[0]["planned_release_at"] in ("2026-09-20T10:00:00Z", "2026-09-20T10:00:00+00:00")

        # Item 2: OVERRIDE, YOUTUBE_SHORT, position 2
        assert items[1]["position"] == 2
        assert items[1]["selection_run_id"] == runs[1]["id"]
        assert items[1]["target_content_type"] == "YOUTUBE_SHORT"
        assert items[1]["selection_mode"] == "OVERRIDE"
        assert items[1]["candidate_title_snapshot"] != ""
        assert items[1]["planned_release_at"] in ("2026-09-21T10:00:00Z", "2026-09-21T10:00:00+00:00")


@pytest.mark.asyncio
async def test_campaign_idempotent_replay_and_conflict(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=2)
        idempotency_key = f"camp-idemp-{uuid.uuid4().hex}"

        payload = {
            "title": "Idempotent Campaign",
            "priority": 1,
            "idempotency_key": idempotency_key,
            "created_by": "planner",
            "items": [
                {
                    "selection_run_id": runs[0]["id"],
                    "target_content_type": "YOUTUBE_LONGFORM",
                },
                {
                    "selection_run_id": runs[1]["id"],
                    "target_content_type": "YOUTUBE_SHORT",
                },
            ],
        }

        # First creation
        resp1 = await client.post(f"/api/v1/channels/{channel_id}/campaigns", json=payload)
        assert resp1.status_code == 201, resp1.text
        data1 = resp1.json()

        # Replay same payload with same idempotency key -> identical campaign returned
        resp2 = await client.post(f"/api/v1/channels/{channel_id}/campaigns", json=payload)
        assert resp2.status_code == 201, resp2.text
        data2 = resp2.json()
        assert data1["id"] == data2["id"]
        assert data1["plan_checksum"] == data2["plan_checksum"]

        # Conflict: same idempotency key with different plan
        conflicting_payload = {
            **payload,
            "title": "Different Campaign Title",
        }
        resp3 = await client.post(f"/api/v1/channels/{channel_id}/campaigns", json=conflicting_payload)
        assert resp3.status_code == 409, resp3.text


@pytest.mark.asyncio
async def test_campaign_global_selection_run_exclusivity(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=2)

        # Campaign 1 takes run 0
        resp1 = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Campaign 1",
                "idempotency_key": f"c1-{uuid.uuid4().hex}",
                "created_by": "user1",
                "items": [
                    {
                        "selection_run_id": runs[0]["id"],
                        "target_content_type": "YOUTUBE_LONGFORM",
                    }
                ],
            },
        )
        assert resp1.status_code == 201, resp1.text

        # Campaign 2 attempts to use run 0 with a different idempotency key -> 409
        resp2 = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Campaign 2",
                "idempotency_key": f"c2-{uuid.uuid4().hex}",
                "created_by": "user2",
                "items": [
                    {
                        "selection_run_id": runs[0]["id"],
                        "target_content_type": "YOUTUBE_SHORT",
                    }
                ],
            },
        )
        assert resp2.status_code == 409, resp2.text
        assert "already committed" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_campaign_concurrency_race_selection_run_exclusivity(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=1)
        shared_run_id = runs[0]["id"]

        req_a = {
            "title": "Campaign Race A",
            "idempotency_key": f"race-a-{uuid.uuid4().hex}",
            "created_by": "racer-a",
            "items": [
                {
                    "selection_run_id": shared_run_id,
                    "target_content_type": "YOUTUBE_LONGFORM",
                }
            ],
        }
        req_b = {
            "title": "Campaign Race B",
            "idempotency_key": f"race-b-{uuid.uuid4().hex}",
            "created_by": "racer-b",
            "items": [
                {
                    "selection_run_id": shared_run_id,
                    "target_content_type": "YOUTUBE_SHORT",
                }
            ],
        }

        # Send concurrent requests with different idempotency keys for the same selection run
        results = await asyncio.gather(
            client.post(f"/api/v1/channels/{channel_id}/campaigns", json=req_a),
            client.post(f"/api/v1/channels/{channel_id}/campaigns", json=req_b),
            return_exceptions=False,
        )

        status_codes = sorted([r.status_code for r in results])
        # Exactly one must succeed with 201, the other must get 409 conflict
        assert status_codes == [201, 409], f"Unexpected status codes: {status_codes}"


@pytest.mark.asyncio
async def test_campaign_dna_coherence_enforcement(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=2)

        # Mutate the second run in database to have a different channel_dna_revision_id
        diff_dna_id = uuid.uuid4()
        diff_dna = ChannelDNARevision(
            id=diff_dna_id,
            channel_id=uuid.UUID(channel_id),
            version=999,
            snapshot={"niche": "Tech", "preferred_formats": ["EXPLAINER"]},
            change_reason="Different revision for testing",
            actor="tester",
        )
        db_session.add(diff_dna)
        await db_session.flush()

        run2 = await db_session.get(ContentSelectionRun, uuid.UUID(runs[1]["id"]))
        assert run2 is not None
        run2.channel_dna_revision_id = diff_dna_id
        await db_session.commit()

        # Attempt to create campaign with runs spanning two different DNA revisions
        resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Mismatched DNA Campaign",
                "idempotency_key": f"dna-mis-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": runs[0]["id"], "target_content_type": "YOUTUBE_LONGFORM"},
                    {"selection_run_id": runs[1]["id"], "target_content_type": "YOUTUBE_SHORT"},
                ],
            },
        )
        assert resp.status_code == 409, resp.text
        assert "multiple DNA revisions" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_campaign_channel_coherence_enforcement(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_a, runs_a = await _create_channel_and_finalized_runs(client, count=1)
        channel_b, _ = await _create_channel_and_finalized_runs(client, count=1)

        # Attempt to use run from channel A in channel B campaign
        resp = await client.post(
            f"/api/v1/channels/{channel_b}/campaigns",
            json={
                "title": "Cross Channel Campaign",
                "idempotency_key": f"cross-ch-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": runs_a[0]["id"], "target_content_type": "YOUTUBE_LONGFORM"}
                ],
            },
        )
        assert resp.status_code == 400, resp.text
        assert "belongs to channel" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_campaign_unfinalized_run_rejection(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_resp = await client.post(
            "/api/v1/channels",
            json={
                "name": "Unfinalized Channel",
                "slug": f"unfin-{uuid.uuid4().hex[:10]}",
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        channel_id = channel_resp.json()["id"]

        cand_resp = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Topic for Unfinalized Run",
                "keywords": ["test"],
                "source_type": "MANUAL",
                "source_name": "test",
                "source_ref": "test://unfin",
            },
        )
        cand_id = cand_resp.json()["id"]

        run_resp = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={
                "candidate_ids": [cand_id],
                "idempotency_key": f"run-unfin-{uuid.uuid4().hex}",
            },
        )
        run_data = run_resp.json()
        assert run_data["status"] == "READY"

        # Attempt to create campaign with unfinalized run
        camp_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Unfinalized Run Campaign",
                "idempotency_key": f"camp-unfin-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": run_data["id"], "target_content_type": "YOUTUBE_LONGFORM"}
                ],
            },
        )
        assert camp_resp.status_code == 400, camp_resp.text
        assert "not finalized" in camp_resp.json()["detail"]


@pytest.mark.asyncio
async def test_campaign_get_and_list_endpoints(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=2)

        resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Listable Campaign",
                "idempotency_key": f"list-camp-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": runs[0]["id"], "target_content_type": "YOUTUBE_LONGFORM"}
                ],
            },
        )
        assert resp.status_code == 201
        created_camp = resp.json()
        camp_id = created_camp["id"]

        # Detail GET
        detail_resp = await client.get(f"/api/v1/channels/{channel_id}/campaigns/{camp_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["id"] == camp_id

        # Other channel GET -> 404
        other_ch = uuid.uuid4()
        not_found_resp = await client.get(f"/api/v1/channels/{other_ch}/campaigns/{camp_id}")
        assert not_found_resp.status_code == 404

        # List GET
        list_resp = await client.get(f"/api/v1/channels/{channel_id}/campaigns")
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) >= 1
        assert any(c["id"] == camp_id for c in items)


@pytest.mark.asyncio
async def test_campaign_historical_deletion_restricted(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=1)

        resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Durable Campaign",
                "idempotency_key": f"durable-camp-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": runs[0]["id"], "target_content_type": "YOUTUBE_LONGFORM"}
                ],
            },
        )
        assert resp.status_code == 201
        camp_id = uuid.UUID(resp.json()["id"])

        # Attempt to delete the campaign row directly via SQL/session
        with pytest.raises(IntegrityError):
            await db_session.execute(
                delete(ContentCampaign).where(ContentCampaign.id == camp_id)
            )
            await db_session.commit()
        await db_session.rollback()


@pytest.mark.asyncio
async def test_campaign_zero_downstream_side_effects(
    db_session: AsyncSession,
) -> None:
    """Prove that creating a ContentCampaign causes ZERO downstream side effects.

    Downstream tables must show exactly zero new rows created.
    """
    downstream_tables = [
        "missions",
        "mission_executions",
        "tasks",
        "schedule_decisions",
        "schedule_reservations",
        "content_generation_requests",
        "production_requests",
        "publish_intents",
    ]

    async def _get_table_counts() -> dict[str, int]:
        counts: dict[str, int] = {}
        for tbl in downstream_tables:
            result = await db_session.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
            counts[tbl] = int(result.scalar_one())
        return counts

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=2)

        counts_before = await _get_table_counts()

        # Create campaign
        resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Strict Zero Side Effects Campaign",
                "objective": "Verify pure planning isolation",
                "priority": 1,
                "idempotency_key": f"pure-plan-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": runs[0]["id"], "target_content_type": "YOUTUBE_LONGFORM"},
                    {"selection_run_id": runs[1]["id"], "target_content_type": "YOUTUBE_SHORT"},
                ],
            },
        )
        assert resp.status_code == 201, resp.text

        counts_after = await _get_table_counts()

        for tbl in downstream_tables:
            delta = counts_after[tbl] - counts_before[tbl]
            assert delta == 0, f"Table '{tbl}' had delta {delta} (expected 0 side effects)"


@pytest.mark.asyncio
async def test_campaign_topic_candidate_title_mutation_historical_snapshot_immutable(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=1)
        original_run = runs[0]

        camp_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Historical Immutability Campaign",
                "idempotency_key": f"hist-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": original_run["id"], "target_content_type": "YOUTUBE_LONGFORM"}
                ],
            },
        )
        assert camp_resp.status_code == 201
        camp_data = camp_resp.json()
        camp_id = camp_data["id"]
        original_title_snapshot = camp_data["items"][0]["candidate_title_snapshot"]

        # Mutate the underlying topic candidate title directly in DB
        cand_id = camp_data["items"][0]["topic_candidate_id"]
        await db_session.execute(
            text(f"UPDATE topic_candidates SET title = 'A Drastically Mutated Candidate Title' WHERE id = '{cand_id}'")
        )
        await db_session.commit()

        # GET campaign must preserve historical snapshot
        get_resp = await client.get(f"/api/v1/channels/{channel_id}/campaigns/{camp_id}")
        assert get_resp.status_code == 200
        reloaded_snapshot = get_resp.json()["items"][0]["candidate_title_snapshot"]
        assert reloaded_snapshot == original_title_snapshot
        assert "Mutated" not in reloaded_snapshot


@pytest.mark.asyncio
async def test_campaign_concurrency_race_same_idempotency_key(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=2)
        shared_key = f"same-key-{uuid.uuid4().hex}"

        req = {
            "title": "Same Key Concurrent Campaign",
            "idempotency_key": shared_key,
            "created_by": "concurrent-tester",
            "items": [
                {"selection_run_id": runs[0]["id"], "target_content_type": "YOUTUBE_LONGFORM"},
                {"selection_run_id": runs[1]["id"], "target_content_type": "YOUTUBE_SHORT"},
            ],
        }

        # Concurrently issue two requests with same idempotency key and same canonical plan
        results = await asyncio.gather(
            client.post(f"/api/v1/channels/{channel_id}/campaigns", json=req),
            client.post(f"/api/v1/channels/{channel_id}/campaigns", json=req),
            return_exceptions=False,
        )

        assert results[0].status_code == 201
        assert results[1].status_code == 201
        data0 = results[0].json()
        data1 = results[1].json()

        # Both callers must resolve successfully to the same campaign ID and checksum
        assert data0["id"] == data1["id"]
        assert data0["plan_checksum"] == data1["plan_checksum"]

        # Exactly 1 ContentCampaign row in database
        camp_count = (
            await db_session.execute(
                select(func.count()).select_from(ContentCampaign).where(
                    ContentCampaign.channel_id == uuid.UUID(channel_id),
                    ContentCampaign.idempotency_key == shared_key,
                )
            )
        ).scalar_one()
        assert camp_count == 1

        # Exactly 2 ContentCampaignItem rows in database for this campaign
        item_count = (
            await db_session.execute(
                select(func.count()).select_from(ContentCampaignItem).where(
                    ContentCampaignItem.campaign_id == uuid.UUID(data0["id"])
                )
            )
        ).scalar_one()
        assert item_count == 2


@pytest.mark.asyncio
async def test_campaign_persisted_checksum_exact_match(
    db_session: AsyncSession,
) -> None:
    from omega.domain.content_campaign import recompute_persisted_campaign_plan_checksum

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=2)

        resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Checksum Integrity Campaign",
                "objective": "Verify exact checksum equality",
                "priority": 3,
                "idempotency_key": f"sum-match-{uuid.uuid4().hex}",
                "created_by": "verifier",
                "items": [
                    {"selection_run_id": runs[0]["id"], "target_content_type": "YOUTUBE_LONGFORM"},
                    {"selection_run_id": runs[1]["id"], "target_content_type": "YOUTUBE_SHORT"},
                ],
            },
        )
        assert resp.status_code == 201
        camp_id = uuid.UUID(resp.json()["id"])

        # Load campaign directly from DB
        stmt = (
            select(ContentCampaign)
            .options(
                selectinload(ContentCampaign.items).selectinload(ContentCampaignItem.selection_run),
                selectinload(ContentCampaign.items).selectinload(ContentCampaignItem.selection_decision),
            )
            .where(ContentCampaign.id == camp_id)
        )
        persisted = (await db_session.execute(stmt)).scalar_one()

        # Recompute checksum from persisted authority
        recomputed = recompute_persisted_campaign_plan_checksum(persisted)
        assert recomputed == persisted.plan_checksum
        assert len(recomputed) == 64


@pytest.mark.asyncio
async def test_campaign_newer_dna_activation_does_not_mutate_historical_plan(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=1)
        original_run = runs[0]

        camp_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "DNA Invariance Campaign",
                "idempotency_key": f"dna-inv-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": original_run["id"], "target_content_type": "YOUTUBE_LONGFORM"}
                ],
            },
        )
        assert camp_resp.status_code == 201
        original_campaign = camp_resp.json()

        # Insert newer DNA revision for the channel
        newer_dna_id = uuid.uuid4()
        newer_dna = ChannelDNARevision(
            id=newer_dna_id,
            channel_id=uuid.UUID(channel_id),
            version=99,
            snapshot={"niche": "Updated Niche", "preferred_formats": ["DEEP_DIVE"]},
            change_reason="Brand refresh",
            actor="strategy-director",
        )
        db_session.add(newer_dna)
        await db_session.commit()

        # GET campaign must preserve the original pinned DNA revision and checksum
        get_resp = await client.get(
            f"/api/v1/channels/{channel_id}/campaigns/{original_campaign['id']}"
        )
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["channel_dna_revision_id"] == original_campaign["channel_dna_revision_id"]
        assert data["channel_dna_revision_id"] != str(newer_dna_id)
        assert data["plan_checksum"] == original_campaign["plan_checksum"]


@pytest.mark.asyncio
async def test_campaign_historical_read_integrity_violation_fails_closed(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, runs = await _create_channel_and_finalized_runs(client, count=1)

        camp_resp = await client.post(
            f"/api/v1/channels/{channel_id}/campaigns",
            json={
                "title": "Integrity Failure Campaign",
                "idempotency_key": f"fail-closed-{uuid.uuid4().hex}",
                "created_by": "tester",
                "items": [
                    {"selection_run_id": runs[0]["id"], "target_content_type": "YOUTUBE_LONGFORM"}
                ],
            },
        )
        assert camp_resp.status_code == 201
        camp_id = uuid.UUID(camp_resp.json()["id"])

        # Intentionally corrupt the persisted item's target_content_type in DB
        await db_session.execute(
            text(f"UPDATE content_campaign_items SET target_content_type = 'YOUTUBE_SHORT' WHERE campaign_id = '{camp_id}'")
        )
        await db_session.commit()

        # GET campaign must fail closed with 500 integrity error, NOT return corrupted data
        get_resp = await client.get(f"/api/v1/channels/{channel_id}/campaigns/{camp_id}")
        assert get_resp.status_code == 500
        assert "integrity violation" in get_resp.json()["detail"].lower()
