"""P19-B canonical content-selection API and concurrency contracts."""

from __future__ import annotations

import asyncio
import copy
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentSelectionDecision,
    ContentSelectionRun,
    Mission,
    MissionExecution,
    TopicCandidate,
)
from omega.main import app


async def _create_channel_and_candidates(
    client: AsyncClient, count: int = 3
) -> tuple[str, list[str]]:
    channel_response = await client.post(
        "/api/v1/channels",
        json={
            "name": "P19 Selection Channel",
            "slug": f"p19-selection-{uuid.uuid4().hex[:10]}",
            "platform": "YOUTUBE",
            "primary_language": "en",
            "target_region": "US",
        },
    )
    assert channel_response.status_code == 201
    channel_id = channel_response.json()["id"]

    candidate_ids: list[str] = []
    titles = [
        "AI Technology Industry News Analysis",
        "Machine Learning Workflow Automation Tutorial",
        "Gardening Tools for Small Balconies",
    ]
    for index in range(count):
        candidate_response = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": titles[index],
                "summary": f"Bounded selection candidate {index}",
                "keywords": titles[index].split(),
                "source_type": "MANUAL",
                "source_name": "p19-test",
                "source_ref": f"test://candidate/{index}",
            },
        )
        assert candidate_response.status_code == 201
        candidate_ids.append(candidate_response.json()["id"])
    return channel_id, candidate_ids


@pytest.mark.asyncio
async def test_selection_run_snapshot_determinism_override_and_idempotency(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, candidate_ids = await _create_channel_and_candidates(client)
        request = {
            "candidate_ids": list(reversed(candidate_ids)),
            "idempotency_key": f"selection-{uuid.uuid4().hex}",
        }
        created = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs", json=request
        )
        assert created.status_code == 201, created.text
        run = created.json()
        assert run["status"] == "READY"
        assert run["considered_count"] == 3
        assert [decision["rank"] for decision in run["decisions"]] == [1, 2, 3]
        assert run["recommended_candidate_id"] == run["decisions"][0]["candidate_id"]
        assert all(
            decision["evidence_snapshot"]["historical_performance_evidence_authority"]
            == "NULL_PROVIDER"
            for decision in run["decisions"]
        )
        assert all(not decision["evidence_snapshot"]["analytics_evidence_ids"] for decision in run["decisions"])

        independent = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": candidate_ids, "idempotency_key": uuid.uuid4().hex},
        )
        assert independent.status_code == 201
        independent_run = independent.json()
        assert independent_run["policy_checksum"] == run["policy_checksum"]
        assert independent_run["candidate_set_checksum"] == run["candidate_set_checksum"]
        assert [
            (item["candidate_id"], item["rank"], item["final_score"], item["reasons"])
            for item in independent_run["decisions"]
        ] == [
            (item["candidate_id"], item["rank"], item["final_score"], item["reasons"])
            for item in run["decisions"]
        ]

        replay = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={**request, "candidate_ids": candidate_ids},
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == run["id"]

        conflict = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": candidate_ids[:2], "idempotency_key": request["idempotency_key"]},
        )
        assert conflict.status_code == 409

        decisions_before = copy.deepcopy(run["decisions"])
        mutated_id = run["decisions"][0]["candidate_id"]
        updated = await client.patch(
            f"/api/v1/channels/{channel_id}/topics/candidates/{mutated_id}",
            json={"title": "A later mutable title"},
        )
        assert updated.status_code == 200
        detail = await client.get(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}"
        )
        assert detail.status_code == 200
        assert detail.json()["decisions"] == decisions_before

        override_id = run["decisions"][1]["candidate_id"]
        finalized = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}/finalize",
            json={
                "selected_candidate_id": override_id,
                "actor": "editor@example.test",
                "override_reason": "Better fit for this editorial window",
            },
        )
        assert finalized.status_code == 200, finalized.text
        selected = finalized.json()
        assert selected["recommended_candidate_id"] == run["recommended_candidate_id"]
        assert selected["selected_candidate_id"] == override_id
        assert selected["selection_mode"] == "OVERRIDE"
        assert selected["selection_reason"] == "Better fit for this editorial window"
        assert selected["decisions"] == decisions_before

        replay_finalize = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}/finalize",
            json={"selected_candidate_id": override_id, "actor": "retrying-editor"},
        )
        assert replay_finalize.status_code == 200

        memory_response = await client.get(f"/api/v1/channels/{channel_id}/topics/memory")
        selected_fingerprint = next(
            decision["topic_fingerprint_snapshot"]
            for decision in run["decisions"]
            if decision["candidate_id"] == override_id
        )
        memory = next(
            item for item in memory_response.json() if item["topic_fingerprint"] == selected_fingerprint
        )
        assert memory["times_selected"] == 1

        candidates = {
            item["id"]: item
            for item in (
                await client.get(f"/api/v1/channels/{channel_id}/topics/candidates")
            ).json()
        }
        assert candidates[override_id]["status"] == "SELECTED"
        assert candidates[run["recommended_candidate_id"]]["status"] != "SELECTED"


@pytest.mark.asyncio
async def test_default_finalization_is_policy_and_exactly_once(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, candidate_ids = await _create_channel_and_candidates(client, count=2)
        created = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": candidate_ids, "idempotency_key": uuid.uuid4().hex},
        )
        run = created.json()
        finalize_url = (
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}/finalize"
        )
        first = await client.post(finalize_url, json={"actor": "policy-reviewer"})
        second = await client.post(finalize_url, json={"actor": "policy-reviewer-retry"})
        assert first.status_code == second.status_code == 200
        assert first.json()["selection_mode"] == "POLICY"
        assert first.json()["selected_candidate_id"] == run["recommended_candidate_id"]

        other_candidate_id = next(
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id != run["recommended_candidate_id"]
        )
        conflicting_replay = await client.post(
            finalize_url,
            json={"selected_candidate_id": other_candidate_id, "actor": "policy-reviewer"},
        )
        assert conflicting_replay.status_code == 409

        memory = (await client.get(f"/api/v1/channels/{channel_id}/topics/memory")).json()
        selected_fingerprint = next(
            decision["topic_fingerprint_snapshot"]
            for decision in run["decisions"]
            if decision["candidate_id"] == run["recommended_candidate_id"]
        )
        assert next(
            item["times_selected"]
            for item in memory
            if item["topic_fingerprint"] == selected_fingerprint
        ) == 1


@pytest.mark.asyncio
async def test_concurrent_same_run_persists_once(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, candidate_ids = await _create_channel_and_candidates(client, count=2)
        payload = {"candidate_ids": candidate_ids, "idempotency_key": uuid.uuid4().hex}

        async def create() -> tuple[int, str]:
            response = await client.post(
                f"/api/v1/channels/{channel_id}/topics/selection-runs", json=payload
            )
            return response.status_code, response.json().get("id", response.text)

        results = await asyncio.gather(create(), create())
        assert all(status == 201 for status, _ in results)
        assert results[0][1] == results[1][1]

        run_count = await db_session.scalar(select(func.count()).select_from(ContentSelectionRun))
        decision_count = await db_session.scalar(
            select(func.count()).select_from(ContentSelectionDecision)
        )
        selected_count = await db_session.scalar(
            select(func.count()).select_from(TopicCandidate).where(TopicCandidate.status == "SELECTED")
        )
        assert run_count == 1
        assert decision_count == 2
        assert selected_count == 0


@pytest.mark.asyncio
async def test_active_dna_not_latest_and_replay_after_activation_is_stable(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, candidate_ids = await _create_channel_and_candidates(client, count=2)
        revision_n = (
            await db_session.execute(
                select(ChannelDNARevision)
                .where(ChannelDNARevision.channel_id == uuid.UUID(channel_id))
                .order_by(ChannelDNARevision.version.desc())
            )
        ).scalars().first()
        assert revision_n is not None

        revision_n_plus_1_snapshot = copy.deepcopy(revision_n.snapshot)
        revision_n_plus_1_snapshot["content_strategy"]["niche"] = "Inactive next niche"
        revision_n_plus_1 = ChannelDNARevision(
            id=uuid.uuid4(),
            channel_id=uuid.UUID(channel_id),
            version=revision_n.version + 1,
            snapshot=revision_n_plus_1_snapshot,
            change_reason="Create inactive future revision",
            actor="TEST",
        )
        db_session.add(revision_n_plus_1)
        await db_session.commit()

        interactive_key = uuid.uuid4().hex
        active_n_response = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": candidate_ids, "idempotency_key": interactive_key},
        )
        assert active_n_response.status_code == 201, active_n_response.text
        active_n_run = active_n_response.json()
        assert active_n_run["channel_dna_revision_id"] == str(revision_n.id)

        mission = Mission(
            id=uuid.uuid4(),
            channel_id=uuid.UUID(channel_id),
            title="Pinned selection mission",
            objective="Verify pinned DNA selection",
            state="RUNNING",
            autonomy_level="SUPERVISED",
            priority=1,
            metadata_={},
        )
        execution = MissionExecution(
            id=uuid.uuid4(),
            mission_id=mission.id,
            channel_dna_revision_id=revision_n.id,
            state="RUNNING",
            trigger_type="MANUAL",
        )
        db_session.add_all([mission, execution])
        await db_session.commit()

        mission_run_response = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={
                "candidate_ids": candidate_ids,
                "idempotency_key": uuid.uuid4().hex,
                "mission_execution_id": str(execution.id),
            },
        )
        assert mission_run_response.status_code == 201, mission_run_response.text
        mission_run = mission_run_response.json()
        assert mission_run["channel_dna_revision_id"] == str(revision_n.id)

        channel = await db_session.get(Channel, uuid.UUID(channel_id))
        assert channel is not None
        channel.dna = revision_n_plus_1_snapshot
        await db_session.commit()

        replay_after_activation = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": list(reversed(candidate_ids)), "idempotency_key": interactive_key},
        )
        assert replay_after_activation.status_code == 201
        assert replay_after_activation.json()["id"] == active_n_run["id"]
        assert replay_after_activation.json()["channel_dna_revision_id"] == str(revision_n.id)
        assert replay_after_activation.json()["decisions"] == active_n_run["decisions"]

        original_detail = await client.get(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{mission_run['id']}"
        )
        assert original_detail.json()["channel_dna_revision_id"] == str(revision_n.id)
        assert original_detail.json()["decisions"] == mission_run["decisions"]

        interactive_response = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": candidate_ids, "idempotency_key": uuid.uuid4().hex},
        )
        assert interactive_response.status_code == 201
        assert interactive_response.json()["channel_dna_revision_id"] == str(
            revision_n_plus_1.id
        )


@pytest.mark.asyncio
async def test_ready_run_rejects_candidate_selected_by_legacy_authority(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, candidate_ids = await _create_channel_and_candidates(client, count=1)
        candidate_id = candidate_ids[0]
        created = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": candidate_ids, "idempotency_key": uuid.uuid4().hex},
        )
        run = created.json()

        legacy = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{candidate_id}/select"
        )
        assert legacy.status_code == 200
        stale_finalize = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}/finalize",
            json={"actor": "stale-reviewer"},
        )
        assert stale_finalize.status_code == 409

        detail = await client.get(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}"
        )
        assert detail.json()["status"] == "READY"
        memory = (await client.get(f"/api/v1/channels/{channel_id}/topics/memory")).json()
        assert memory[0]["times_selected"] == 1


@pytest.mark.asyncio
async def test_concurrent_runs_can_claim_candidate_only_once(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as setup_client:
        channel_id, candidate_ids = await _create_channel_and_candidates(setup_client, count=1)
        runs = []
        for _ in range(2):
            response = await setup_client.post(
                f"/api/v1/channels/{channel_id}/topics/selection-runs",
                json={"candidate_ids": candidate_ids, "idempotency_key": uuid.uuid4().hex},
            )
            assert response.status_code == 201
            runs.append(response.json())

    async def finalize(run_id: str) -> int:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/channels/{channel_id}/topics/selection-runs/{run_id}/finalize",
                json={"actor": f"reviewer-{run_id}"},
            )
            return response.status_code

    statuses = await asyncio.gather(finalize(runs[0]["id"]), finalize(runs[1]["id"]))
    assert sorted(statuses) == [200, 409]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        states = []
        for run in runs:
            detail = await client.get(
                f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}"
            )
            states.append(detail.json()["status"])
        assert sorted(states) == ["READY", "SELECTED"]
        memory = (await client.get(f"/api/v1/channels/{channel_id}/topics/memory")).json()
        assert memory[0]["times_selected"] == 1


@pytest.mark.asyncio
async def test_override_rejects_stale_candidate_and_finalize_not_found_is_404(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, candidate_ids = await _create_channel_and_candidates(client, count=2)
        created = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": candidate_ids, "idempotency_key": uuid.uuid4().hex},
        )
        run = created.json()
        override_id = run["decisions"][1]["candidate_id"]
        rejected = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{override_id}/reject",
            json={"reason": "No longer eligible for this window"},
        )
        assert rejected.status_code == 200

        stale_override = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}/finalize",
            json={
                "selected_candidate_id": override_id,
                "actor": "editor",
                "override_reason": "Previously preferred",
            },
        )
        assert stale_override.status_code == 409
        detail = await client.get(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{run['id']}"
        )
        assert detail.json()["status"] == "READY"

        missing = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs/{uuid.uuid4()}/finalize",
            json={"actor": "editor"},
        )
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_equal_scores_use_candidate_uuid_tie_break(db_session: AsyncSession) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id, _ = await _create_channel_and_candidates(client, count=1)
        candidate_ids: list[str] = []
        for suffix in ("a", "b"):
            response = await client.post(
                f"/api/v1/channels/{channel_id}/topics/candidates",
                json={
                    "title": "Identical Deterministic Selection Topic",
                    "keywords": ["identical", "selection"],
                    "idempotency_key": f"tie-{suffix}-{uuid.uuid4().hex}",
                },
            )
            candidate_ids.append(response.json()["id"])

        response = await client.post(
            f"/api/v1/channels/{channel_id}/topics/selection-runs",
            json={"candidate_ids": list(reversed(candidate_ids)), "idempotency_key": uuid.uuid4().hex},
        )
        assert response.status_code == 201, response.text
        decisions = response.json()["decisions"]
        assert decisions[0]["final_score"] == decisions[1]["final_score"]
        assert [item["candidate_id"] for item in decisions] == sorted(candidate_ids)


@pytest.mark.asyncio
async def test_selection_schema_constraints_and_cross_channel_rejection(
    db_session: AsyncSession,
) -> None:
    connection = await db_session.connection()
    run_uniques = await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).get_unique_constraints(
            "content_selection_runs"
        )
    )
    decision_uniques = await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).get_unique_constraints(
            "content_selection_decisions"
        )
    )
    run_checks = await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).get_check_constraints(
            "content_selection_runs"
        )
    )
    assert {item["name"] for item in run_uniques} >= {
        "uq_content_selection_runs_channel_idempotency"
    }
    assert {item["name"] for item in decision_uniques} >= {
        "uq_selection_decisions_run_candidate",
        "uq_selection_decisions_run_rank",
    }
    assert {item["name"] for item in run_checks} >= {
        "ck_content_selection_runs_count_positive",
        "ck_content_selection_runs_finalization_fields",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_a, candidate_ids = await _create_channel_and_candidates(client, count=1)
        channel_b, _ = await _create_channel_and_candidates(client, count=1)
        response = await client.post(
            f"/api/v1/channels/{channel_b}/topics/selection-runs",
            json={"candidate_ids": candidate_ids, "idempotency_key": uuid.uuid4().hex},
        )
        assert response.status_code == 400
        assert channel_a != channel_b
