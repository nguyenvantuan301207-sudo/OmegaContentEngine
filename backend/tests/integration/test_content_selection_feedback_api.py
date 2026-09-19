"""Integration tests for P19-E: Versioned Analytics/Learning Historical-Performance Feedback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.content_selection_service import (
    CONTENT_SELECTION_POLICY_NAME,
    candidate_set_checksum,
    policy_checksum,
)
from omega.domain.analytics import MetricClassification, MetricQuality, WindowState, WindowType
from omega.domain.historical_performance import (
    HISTORICAL_PERFORMANCE_POLICY_NAME,
    HistoricalPerformanceStatus,
    compute_historical_performance_policy_checksum,
)
from omega.infrastructure.models import (
    ChannelDNARevision,
    ContentGenerationRequest,
    ContentSelectionDecision,
    ContentSelectionRun,
    LearningInputLatestPointer,
    LearningInputSnapshot,
    MediaArtifact,
    Mission,
    PlatformAccount,
    ProductionRequest,
    PublishIntent,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
)
from omega.main import app


async def _ensure_test_environment_for_channel(
    session: AsyncSession, channel_id: uuid.UUID
) -> dict[str, Any]:
    """Ensure complete valid publishing environment exists for the channel."""
    # 1. Platform Account
    res_acc = await session.execute(
        select(PlatformAccount).where(PlatformAccount.channel_id == channel_id)
    )
    account = res_acc.scalars().first()
    if not account:
        account = PlatformAccount(
            id=uuid.uuid4(),
            channel_id=channel_id,
            platform="YOUTUBE",
            account_display_name="Test Account",
            external_account_id=f"UC_{uuid.uuid4().hex[:8]}",
            status="ACTIVE",
            scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        )
        session.add(account)

    # 2. Media Artifact with production chain
    res_art = await session.execute(
        select(MediaArtifact)
        .join(ProductionRequest, MediaArtifact.production_request_id == ProductionRequest.id)
        .where(ProductionRequest.channel_id == channel_id)
    )
    artifact = res_art.scalars().first()
    if not artifact:
        dna_rev_res = await session.execute(
            select(ChannelDNARevision).where(ChannelDNARevision.channel_id == channel_id)
        )
        dna_rev = dna_rev_res.scalars().first()
        assert dna_rev is not None

        topic = TopicCandidate(
            id=uuid.uuid4(),
            channel_id=channel_id,
            title="Artifact Seeding Topic",
            normalized_title="artifact seeding topic",
            topic_fingerprint=f"fp_art_{uuid.uuid4().hex[:10]}",
            status="DISCOVERED",
        )
        session.add(topic)

        r_req = ResearchRequest(
            id=uuid.uuid4(),
            channel_id=channel_id,
            topic_candidate_id=topic.id,
            status="COMPLETED",
        )
        session.add(r_req)

        brief = ResearchBrief(
            id=uuid.uuid4(),
            research_request_id=r_req.id,
            channel_id=channel_id,
            topic_candidate_id=topic.id,
            title="Brief",
            summary="Summary",
        )
        session.add(brief)

        content_req = ContentGenerationRequest(
            id=uuid.uuid4(),
            channel_id=channel_id,
            topic_candidate_id=topic.id,
            research_brief_id=brief.id,
            channel_dna_revision_id=dna_rev.id,
            status="APPROVED",
        )
        session.add(content_req)

        script_ver = ScriptVersion(
            id=uuid.uuid4(),
            content_request_id=content_req.id,
            version=1,
            title="Script",
            hook_text="Hook",
            closing_text="Close",
            cta_text="CTA",
        )
        session.add(script_ver)

        prod_req = ProductionRequest(
            id=uuid.uuid4(),
            channel_id=channel_id,
            script_version_id=script_ver.id,
            content_request_id=content_req.id,
            channel_dna_revision_id=dna_rev.id,
            status="COMPLETED",
        )
        session.add(prod_req)

        artifact = MediaArtifact(
            id=uuid.uuid4(),
            production_request_id=prod_req.id,
            artifact_type="VIDEO",
            version=1,
            is_current=True,
            storage_uri=f"videos/test-{uuid.uuid4().hex[:8]}.mp4",
            file_size_bytes=1024,
            content_hash=f"hash_{uuid.uuid4().hex[:8]}",
            mime_type="video/mp4",
            duration_ms=45000,
        )
        session.add(artifact)

    # 3. Mission
    res_mis = await session.execute(
        select(Mission).where(Mission.channel_id == channel_id)
    )
    mission = res_mis.scalars().first()
    if not mission:
        mission = Mission(
            id=uuid.uuid4(),
            channel_id=channel_id,
            title="Seeding Mission",
            objective="Seeding Objective",
            autonomy_level="AUTONOMOUS",
            state="RUNNING",
        )
        session.add(mission)

    # 4. Task
    res_task = await session.execute(
        select(Task).where(Task.mission_id == mission.id)
    )
    task = res_task.scalars().first()
    if not task:
        task = Task(
            id=uuid.uuid4(),
            mission_id=mission.id,
            task_type="PUBLISH_VIDEO",
            title="Publish Task",
            state="COMPLETED",
        )
        session.add(task)

    await session.flush()
    return {
        "account": account,
        "artifact": artifact,
        "mission": mission,
        "task": task,
    }




async def _create_channel_and_candidates(
    client: AsyncClient,
    count: int = 2,
    titles: list[str] | None = None,
    keywords_list: list[list[str]] | None = None,
) -> tuple[str, list[str]]:
    channel_res = await client.post(
        "/api/v1/channels",
        json={
            "name": f"P19E Channel {uuid.uuid4().hex[:6]}",
            "slug": f"p19e-chan-{uuid.uuid4().hex[:10]}",
            "platform": "YOUTUBE",
            "primary_language": "en",
            "target_region": "US",
        },
    )
    assert channel_res.status_code == 201
    channel_id = channel_res.json()["id"]

    default_titles = [
        "Quantum Computing Advanced Algorithms Guide",
        "Home Gardening Sourdough Baking Basics",
    ]
    candidate_ids: list[str] = []
    for i in range(count):
        t = titles[i] if titles and i < len(titles) else default_titles[i % len(default_titles)]
        kw = keywords_list[i] if keywords_list and i < len(keywords_list) else t.lower().split()
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": t,
                "summary": f"Summary for {t}",
                "keywords": kw,
                "source_type": "MANUAL",
                "source_name": "p19e-test",
                "source_ref": f"test://cand/{i}",
            },
        )
        assert cand_res.status_code == 201
        candidate_ids.append(cand_res.json()["id"])

    return channel_id, candidate_ids


async def _seed_learning_evidence(
    session: AsyncSession,
    *,
    channel_id: uuid.UUID,
    count: int,
    title_prefix: str,
    keywords: list[str],
    tags: list[str],
    views_values: list[float],
    window_state: str = WindowState.FINALIZED.value,
    is_fully_finalized: bool = True,
    metric_quality: str = MetricQuality.AVAILABLE.value,
    metric_classification: str = MetricClassification.PROVIDER_FACT.value,
    revision_sequence: int = 1,
) -> list[tuple[LearningInputSnapshot, LearningInputLatestPointer]]:
    created: list[tuple[LearningInputSnapshot, LearningInputLatestPointer]] = []
    now = datetime.now(UTC)
    env = await _ensure_test_environment_for_channel(session, channel_id)
    artifact = env["artifact"]

    # 1. Add and flush all PublishIntents first
    intents: list[PublishIntent] = []
    for i in range(count):
        task = Task(
            id=uuid.uuid4(),
            mission_id=env["mission"].id,
            task_type="PUBLISH_VIDEO",
            title=f"Publish Task {uuid.uuid4().hex[:8]}",
            state="COMPLETED",
        )
        session.add(task)
        intent = PublishIntent(
            id=uuid.uuid4(),
            mission_id=env["mission"].id,
            task_id=task.id,
            channel_id=channel_id,
            platform_account_id=env["account"].id,
            media_artifact_id=artifact.id,
            media_artifact_checksum="hash123",
            title=f"{title_prefix} Part {i+1}",
            tags=tags,
            description="Historical content intent",
            made_for_kids=False,
            intent_checksum=f"intent_{uuid.uuid4().hex}",
            state="PUBLISHED",
        )
        session.add(intent)
        intents.append(intent)
    await session.flush()

    # 2. Add and flush all LearningInputSnapshots
    snapshots: list[tuple[LearningInputSnapshot, uuid.UUID]] = []
    for i, intent in enumerate(intents):
        obs_id = uuid.uuid4()
        snap_id = uuid.uuid4()
        raw_views = views_values[i % len(views_values)]
        raw_metrics = {
            "views": raw_views,
            "watch_time_seconds": raw_views * 60.0,
            "average_percentage_viewed": 65.0,
            "impression_ctr_percent": 8.5,
            "subscribers_gained": 25.0,
        }
        metric_qualities = {m: metric_quality for m in raw_metrics}
        classifications = {m: metric_classification for m in raw_metrics}
        payload_checksum = hashlib.sha256(f"payload-{snap_id}".encode()).hexdigest()

        snapshot = LearningInputSnapshot(
            id=snap_id,
            observation_id=obs_id,
            channel_id=channel_id,
            publish_intent_id=intent.id,
            provider_video_id=f"vid_{uuid.uuid4().hex[:11]}",
            media_artifact_id=artifact.id,
            published_at_utc=now - timedelta(days=count - i),
            window_type=WindowType.FIRST_7D.value,
            window_state=window_state,
            window_start_utc=now - timedelta(days=count - i),
            window_end_utc=now - timedelta(days=count - i - 7),
            raw_metrics=raw_metrics,
            metric_qualities=metric_qualities,
            classifications=classifications,
            is_fully_finalized=is_fully_finalized,
            quality_flags=[],
            payload_checksum=payload_checksum,
            input_dedupe_key=f"dedupe_{uuid.uuid4().hex}",
            revision_sequence=revision_sequence,
        )
        session.add(snapshot)
        snapshots.append((snapshot, obs_id))
    await session.flush()

    # 3. Add pointers and commit
    for snapshot, obs_id in snapshots:
        pointer = LearningInputLatestPointer(
            observation_id=obs_id,
            window_type=WindowType.FIRST_7D.value,
            channel_id=channel_id,
            current_input_snapshot_id=snapshot.id,
            current_revision_sequence=revision_sequence,
            current_payload_checksum=snapshot.payload_checksum,
            updated_at=now,
        )
        session.add(pointer)
        created.append((snapshot, pointer))

    await session.commit()
    return created



@pytest.mark.asyncio
async def test_new_selection_run_uses_policy_v2_and_schema_v2(
    db_session: AsyncSession,
) -> None:
    """New selection run must be policy version 2 and evidence_snapshot schema_version 2."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id_str, cand_ids = await _create_channel_and_candidates(client, count=2)
        resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": f"p19e-v2-{uuid.uuid4().hex}",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["policy_version"] == 2
        assert data["policy_name"] == CONTENT_SELECTION_POLICY_NAME
        assert data["policy_checksum"] == policy_checksum()

        for decision in data["decisions"]:
            ev = decision["evidence_snapshot"]
            assert ev["schema_version"] == 2
            assert (
                ev["historical_performance_evidence_authority"]
                == "OMEGA_LEARNING_HISTORICAL_PERFORMANCE"
            )
            assert ev["historical_performance_policy_name"] == HISTORICAL_PERFORMANCE_POLICY_NAME
            assert ev["historical_performance_policy_version"] == 1
            assert (
                ev["historical_performance_policy_checksum"]
                == compute_historical_performance_policy_checksum()
            )
            # Empty channel -> INSUFFICIENT_CORPUS fallback
            assert ev["historical_performance_status"] == HistoricalPerformanceStatus.INSUFFICIENT_CORPUS.value
            assert ev["historical_performance_score"] == 50.0
            assert decision["score_breakdown"]["historical_performance"] == 50.0
            assert ev["analytics_evidence_authority"] == "ANALYTICS_WINDOW"
            assert ev["analytics_evidence_ids"] == []
            assert ev["learning_evidence_ids"] == []
            assert ev["active_knowledge_authority"] == "NOT_APPLIED_V1"
            assert ev["active_learning_knowledge_ids"] == []


def _policy_checksum_v1() -> str:
    from omega.domain.topic_scoring import DEFAULT_SCORING_PROFILE, DEFAULT_SIMILARITY_PROFILE

    payload = {
        "policy_name": CONTENT_SELECTION_POLICY_NAME,
        "policy_version": 1,
        "topic_scoring_profile": DEFAULT_SCORING_PROFILE.model_dump(mode="json"),
        "similarity_profile": DEFAULT_SIMILARITY_PROFILE.model_dump(mode="json"),
        "eligible_candidate_states": ["DISCOVERED", "EVALUATED", "RECOMMENDED"],
        "ranking": {"primary": "FINAL_SCORE_DESC", "tie_break": "CANDIDATE_UUID_ASC"},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.asyncio
async def test_old_v1_run_replay_compatibility(
    db_session: AsyncSession,
) -> None:
    """Replaying an existing v1 selection run returns original v1 record without re-scoring."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id_str, cand_ids = await _create_channel_and_candidates(client, count=2)
        channel_uuid = uuid.UUID(channel_id_str)
        cand_uuids = [uuid.UUID(cid) for cid in cand_ids]

        # Fetch pinned DNA revision
        dna_rev_res = await db_session.execute(
            select(ChannelDNARevision).where(ChannelDNARevision.channel_id == channel_uuid)
        )
        dna_rev = dna_rev_res.scalars().first()
        assert dna_rev is not None

        v1_policy_cs = _policy_checksum_v1()
        v1_cand_set_cs = candidate_set_checksum(
            candidate_ids=cand_uuids,
            channel_dna_revision_id=dna_rev.id,
            selection_policy_checksum=v1_policy_cs,
            mission_execution_id=None,
        )

        # Seed exact historical v1 run
        v1_run_id = uuid.uuid4()
        idempotency_key = f"v1-historical-key-{uuid.uuid4().hex}"
        v1_run = ContentSelectionRun(
            id=v1_run_id,
            channel_id=channel_uuid,
            channel_dna_revision_id=dna_rev.id,
            mission_execution_id=None,
            status="READY",
            policy_name="OMEGA_CONTENT_SELECTION",
            policy_version=1,
            policy_checksum=v1_policy_cs,
            candidate_set_checksum=v1_cand_set_cs,
            idempotency_key=idempotency_key,
            recommended_candidate_id=cand_uuids[0],
            considered_count=2,
            completed_at=datetime.now(UTC),
        )
        v1_run.decisions.append(
            ContentSelectionDecision(
                id=uuid.uuid4(),
                selection_run_id=v1_run_id,
                candidate_id=cand_uuids[0],
                rank=1,
                final_score=75.0,
                score_breakdown={"historical_performance": 50.0},
                reasons=["FRESH_TOPIC"],
                duplicate_status="FRESH_TOPIC",
                candidate_title_snapshot="Quantum v1",
                topic_fingerprint_snapshot="fp1",
                candidate_snapshot={"id": str(cand_uuids[0]), "title": "Quantum v1"},
                evidence_snapshot={
                    "schema_version": 1,
                    "historical_performance_evidence_authority": "NULL_PROVIDER",
                    "analytics_evidence_ids": [],
                    "learning_evidence_ids": [],
                },
            )
        )
        v1_run.decisions.append(
            ContentSelectionDecision(
                id=uuid.uuid4(),
                selection_run_id=v1_run_id,
                candidate_id=cand_uuids[1],
                rank=2,
                final_score=60.0,
                score_breakdown={"historical_performance": 50.0},
                reasons=["FRESH_TOPIC"],
                duplicate_status="FRESH_TOPIC",
                candidate_title_snapshot="Baking v1",
                topic_fingerprint_snapshot="fp2",
                candidate_snapshot={"id": str(cand_uuids[1]), "title": "Baking v1"},
                evidence_snapshot={
                    "schema_version": 1,
                    "historical_performance_evidence_authority": "NULL_PROVIDER",
                    "analytics_evidence_ids": [],
                    "learning_evidence_ids": [],
                },
            )
        )
        db_session.add(v1_run)
        await db_session.commit()

        # Replay the SAME idempotency key and request
        replay_resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": idempotency_key,
            },
        )
        assert replay_resp.status_code == 201, replay_resp.text
        data = replay_resp.json()
        assert data["id"] == str(v1_run_id)
        assert data["policy_version"] == 1
        assert data["policy_checksum"] == v1_policy_cs
        assert data["decisions"][0]["evidence_snapshot"]["schema_version"] == 1
        assert (
            data["decisions"][0]["evidence_snapshot"]["historical_performance_evidence_authority"]
            == "NULL_PROVIDER"
        )


@pytest.mark.asyncio
async def test_learning_evidence_applied_and_score_evidence_equality(
    db_session: AsyncSession,
) -> None:
    """Finalized Learning evidence is applied, candidate score matches evidence snapshot."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        titles = [
            "Quantum Physics Computing Mechanics",
            "Culinary Sourdough Bread Recipe",
        ]
        channel_id_str, cand_ids = await _create_channel_and_candidates(
            client, count=2, titles=titles
        )
        channel_uuid = uuid.UUID(channel_id_str)

        # Seed 6 finalized records relevant to Quantum
        views = [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]
        await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=6,
            title_prefix="Quantum Physics In Depth",
            keywords=["quantum", "physics", "mechanics"],
            tags=["quantum", "physics"],
            views_values=views,
        )

        resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": f"p19e-applied-{uuid.uuid4().hex}",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()

        # Check candidate 0 (Quantum)
        q_decision = next(d for d in data["decisions"] if d["candidate_id"] == cand_ids[0])
        q_ev = q_decision["evidence_snapshot"]
        assert q_ev["historical_performance_status"] == HistoricalPerformanceStatus.APPLIED.value
        assert q_ev["historical_performance_match_count"] >= 2
        assert len(q_ev["learning_evidence_ids"]) == q_ev["historical_performance_match_count"]
        assert len(q_ev["analytics_evidence_ids"]) == q_ev["historical_performance_match_count"]
        assert len(q_ev["matched_evidence"]) == q_ev["historical_performance_match_count"]

        # Score equality invariant
        hist_score = q_ev["historical_performance_score"]
        assert q_decision["score_breakdown"]["historical_performance"] == hist_score
        assert "HISTORICAL_PERFORMANCE_EVIDENCE_APPLIED" in q_decision["reasons"]

        # Check candidate 1 (Sourdough - unrelated to Quantum)
        s_decision = next(d for d in data["decisions"] if d["candidate_id"] == cand_ids[1])
        s_ev = s_decision["evidence_snapshot"]
        assert s_ev["historical_performance_status"] == HistoricalPerformanceStatus.INSUFFICIENT_RELEVANT_HISTORY.value
        assert s_ev["historical_performance_score"] == 50.0
        assert s_decision["score_breakdown"]["historical_performance"] == 50.0
        assert "HISTORICAL_PERFORMANCE_INSUFFICIENT_RELEVANT_HISTORY" in s_decision["reasons"]

        # Corpus consistency across all candidates in the run
        assert q_ev["historical_performance_corpus_checksum"] == s_ev["historical_performance_corpus_checksum"]
        assert q_ev["historical_performance_corpus_count"] == s_ev["historical_performance_corpus_count"] == 6
        assert q_ev["historical_performance_pinned_snapshot_count"] == s_ev["historical_performance_pinned_snapshot_count"] == 6


@pytest.mark.asyncio
async def test_revision_pointer_semantics(
    db_session: AsyncSession,
) -> None:
    """Selector consumes latest revision via pointer and ignores superseded revision 1."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        titles = ["Advanced Cloud Architecture", "Cooking Pastries"]
        channel_id_str, cand_ids = await _create_channel_and_candidates(
            client, count=2, titles=titles
        )
        channel_uuid = uuid.UUID(channel_id_str)

        # Seed 5 records rev 1
        created = await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=5,
            title_prefix="Advanced Cloud Systems",
            keywords=["cloud", "architecture"],
            tags=["cloud"],
            views_values=[1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
            revision_sequence=1,
        )

        # 1. Create run K using current pointer revision 1
        key_k = f"p19e-pointer-k-{uuid.uuid4().hex}"
        resp_k = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": key_k,
            },
        )
        assert resp_k.status_code == 201
        data_k = resp_k.json()
        run_k_id = data_k["id"]
        dec_k = next(d for d in data_k["decisions"] if d["candidate_id"] == cand_ids[0])
        ev_k = dec_k["evidence_snapshot"]
        orig_snap, pointer = created[0]
        assert str(orig_snap.id) in ev_k["learning_evidence_ids"]
        old_corpus_checksum = ev_k["historical_performance_corpus_checksum"]
        old_score = ev_k["historical_performance_score"]

        # 2. Advance pointer of record 0 to revision 2 with higher metrics
        rev2_snap_id = uuid.uuid4()
        now = datetime.now(UTC)
        rev2_checksum = hashlib.sha256(f"rev2-{rev2_snap_id}".encode()).hexdigest()

        rev2_snapshot = LearningInputSnapshot(
            id=rev2_snap_id,
            observation_id=orig_snap.observation_id,
            channel_id=channel_uuid,
            publish_intent_id=orig_snap.publish_intent_id,
            provider_video_id=orig_snap.provider_video_id,
            media_artifact_id=orig_snap.media_artifact_id,
            published_at_utc=orig_snap.published_at_utc,
            window_type=orig_snap.window_type,
            window_state=WindowState.REVISED.value,
            window_start_utc=orig_snap.window_start_utc,
            window_end_utc=orig_snap.window_end_utc,
            raw_metrics={"views": 50000.0, "watch_time_seconds": 3000000.0},
            metric_qualities={"views": MetricQuality.REVISED.value, "watch_time_seconds": MetricQuality.REVISED.value},
            classifications={"views": MetricClassification.PROVIDER_FACT.value, "watch_time_seconds": MetricClassification.PROVIDER_FACT.value},
            is_fully_finalized=True,
            quality_flags=[],
            payload_checksum=rev2_checksum,
            input_dedupe_key=f"dedupe_{uuid.uuid4().hex}",
            revision_sequence=2,
        )
        db_session.add(rev2_snapshot)

        # Update pointer to revision 2
        pointer.current_input_snapshot_id = rev2_snap_id
        pointer.current_revision_sequence = 2
        pointer.current_payload_checksum = rev2_checksum
        pointer.updated_at = now
        await db_session.commit()

        # 3. Replay SAME idempotency key K: same run ID, same old corpus checksum, same old evidence IDs
        replay_resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": key_k,
            },
        )
        assert replay_resp.status_code == 201
        replay_data = replay_resp.json()
        assert replay_data["id"] == run_k_id
        replay_dec = next(d for d in replay_data["decisions"] if d["candidate_id"] == cand_ids[0])
        replay_ev = replay_dec["evidence_snapshot"]
        assert replay_ev["historical_performance_corpus_checksum"] == old_corpus_checksum
        assert replay_ev["historical_performance_score"] == old_score
        assert str(orig_snap.id) in replay_ev["learning_evidence_ids"]
        assert str(rev2_snap_id) not in replay_ev["learning_evidence_ids"]

        # 4. Create NEW selection run with NEW idempotency key: uses new pointer revision
        new_key = f"p19e-new-key-{uuid.uuid4().hex}"
        new_resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": new_key,
            },
        )
        assert new_resp.status_code == 201
        new_data = new_resp.json()
        assert new_data["id"] != run_k_id
        new_dec = next(d for d in new_data["decisions"] if d["candidate_id"] == cand_ids[0])
        new_ev = new_dec["evidence_snapshot"]
        assert str(rev2_snap_id) in new_ev["learning_evidence_ids"]
        assert str(orig_snap.id) not in new_ev["learning_evidence_ids"]
        assert new_ev["historical_performance_corpus_checksum"] != old_corpus_checksum


@pytest.mark.asyncio
async def test_provisional_data_excluded(
    db_session: AsyncSession,
) -> None:
    """PROVISIONAL snapshots are excluded from corpus and do not affect score."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        titles = ["Database Indexing Strategies", "Baking Pies"]
        channel_id_str, cand_ids = await _create_channel_and_candidates(
            client, count=2, titles=titles
        )
        channel_uuid = uuid.UUID(channel_id_str)

        # Seed 5 finalized records and 3 provisional records (total 8 snapshots)
        await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=5,
            title_prefix="Database Indexing In Depth",
            keywords=["database", "indexing"],
            tags=["database"],
            views_values=[1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
            window_state=WindowState.FINALIZED.value,
            is_fully_finalized=True,
        )
        provisional_records = await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=3,
            title_prefix="Database Indexing High Volume",
            keywords=["database", "indexing"],
            tags=["database"],
            views_values=[99999.0, 99999.0, 99999.0],
            window_state=WindowState.PROVISIONAL.value,
            is_fully_finalized=False,
        )

        resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": f"p19e-prov-{uuid.uuid4().hex}",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        dec = next(d for d in data["decisions"] if d["candidate_id"] == cand_ids[0])
        ev = dec["evidence_snapshot"]

        # 3 provisional records are excluded: exactly 5 finalized snapshots were pinned and scored
        assert ev["historical_performance_pinned_snapshot_count"] == 5
        assert ev["historical_performance_corpus_count"] == 5
        assert ev["historical_performance_status"] == HistoricalPerformanceStatus.APPLIED.value
        prov_snap_ids = {str(snap.id) for snap, _ in provisional_records}
        for used_id in ev["learning_evidence_ids"]:
            assert used_id not in prov_snap_ids


@pytest.mark.asyncio
async def test_zero_confirmed_is_numeric_zero_in_real_provider(
    db_session: AsyncSession,
) -> None:
    """The provider normalizes a confirmed zero, not the stale raw metric value."""
    from omega.application.historical_performance_provider import (
        LearningHistoricalPerformanceProvider,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id_str, _ = await _create_channel_and_candidates(client, count=1)
        channel_id = uuid.UUID(channel_id_str)
        created = await _seed_learning_evidence(
            db_session,
            channel_id=channel_id,
            count=5,
            title_prefix="Confirmed Zero Series",
            keywords=["confirmed", "zero"],
            tags=["series"],
            views_values=[999.0, 10.0, 20.0, 30.0, 40.0],
        )
        for index, (snapshot, _) in enumerate(created):
            snapshot.classifications = {
                name: (
                    MetricClassification.PROVIDER_FACT.value
                    if name == "views"
                    else MetricClassification.HEURISTIC_FEATURE.value
                )
                for name in snapshot.raw_metrics
            }
            if index == 0:
                snapshot.metric_qualities = {
                    **snapshot.metric_qualities,
                    "views": MetricQuality.ZERO_CONFIRMED.value,
                }
        await db_session.commit()

        provider = await LearningHistoricalPerformanceProvider.build(db_session, channel_id)
        assert provider.pinned_snapshot_count == 5
        assert provider.performance_record_count == 5
        records = {record.snapshot_id: record for record in provider.performance_records}
        zero_record = records[created[0][0].id]
        assert zero_record.metric_values == {"views": 0.0}
        assert zero_record.normalized_metrics == {"views": 0.0}
        assert zero_record.historical_item_performance == 0.0


@pytest.mark.asyncio
async def test_heuristic_and_invalid_qualities_excluded(
    db_session: AsyncSession,
) -> None:
    """HEURISTIC_FEATURE and invalid qualities (SUPPRESSED, etc.) are excluded."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        titles = ["Robotics Kinematics", "Baking Croissants"]
        channel_id_str, cand_ids = await _create_channel_and_candidates(
            client, count=2, titles=titles
        )
        channel_uuid = uuid.UUID(channel_id_str)

        # Seed 5 records with HEURISTIC_FEATURE classification
        await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=5,
            title_prefix="Robotics Kinematics Series",
            keywords=["robotics", "kinematics"],
            tags=["robotics"],
            views_values=[1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
            metric_classification=MetricClassification.HEURISTIC_FEATURE.value,
        )

        resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": f"p19e-heur-{uuid.uuid4().hex}",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        ev = data["decisions"][0]["evidence_snapshot"]

        # Pinned 5 snapshots, but 0 have eligible metrics -> performance_record_count == 0 < 5 -> INSUFFICIENT_CORPUS
        assert ev["historical_performance_status"] == HistoricalPerformanceStatus.INSUFFICIENT_CORPUS.value
        assert ev["historical_performance_score"] == 50.0
        assert ev["historical_performance_corpus_count"] == 0
        assert ev["historical_performance_pinned_snapshot_count"] == 5


@pytest.mark.asyncio
async def test_high_vs_low_topic_history_demonstrates_separation(
    db_session: AsyncSession,
) -> None:
    """High-performing history yields higher score than low-performing history."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        titles = [
            "High Performing Quantum Architecture",
            "Low Performing Simple Scripting",
        ]
        channel_id_str, cand_ids = await _create_channel_and_candidates(
            client, count=2, titles=titles
        )
        channel_uuid = uuid.UUID(channel_id_str)

        # Seed 5 high-performing Quantum records
        await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=5,
            title_prefix="Quantum Architecture Research",
            keywords=["quantum", "architecture"],
            tags=["quantum"],
            views_values=[80000.0, 85000.0, 90000.0, 95000.0, 100000.0],
        )

        # Seed 5 low-performing Scripting records
        await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=5,
            title_prefix="Simple Scripting Snippets",
            keywords=["simple", "scripting"],
            tags=["scripting"],
            views_values=[10.0, 20.0, 30.0, 40.0, 50.0],
        )

        resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": f"p19e-high-low-{uuid.uuid4().hex}",
            },
        )
        assert resp.status_code == 201
        data = resp.json()

        high_dec = next(d for d in data["decisions"] if d["candidate_id"] == cand_ids[0])
        low_dec = next(d for d in data["decisions"] if d["candidate_id"] == cand_ids[1])

        high_score = high_dec["evidence_snapshot"]["historical_performance_score"]
        low_score = low_dec["evidence_snapshot"]["historical_performance_score"]

        assert high_score > low_score
        assert high_dec["score_breakdown"]["historical_performance"] == high_score
        assert low_dec["score_breakdown"]["historical_performance"] == low_score


@pytest.mark.asyncio
async def test_read_only_analytics_and_learning_tables(
    db_session: AsyncSession,
) -> None:
    """Selection execution must perform ZERO mutations to Analytics and Learning tables."""
    res = await db_session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND (table_name LIKE 'analytics_%' OR table_name LIKE 'learning_%')"
        )
    )
    table_names = [r[0] for r in res.fetchall()]
    counts_before = {}
    for tbl in table_names:
        c = (await db_session.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar_one()
        counts_before[tbl] = c

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id_str, cand_ids = await _create_channel_and_candidates(client, count=2)
        resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": f"p19e-ro-{uuid.uuid4().hex}",
            },
        )
        assert resp.status_code == 201

    for tbl, before in counts_before.items():
        after = (await db_session.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar_one()
        assert before == after, f"Table {tbl} mutated! before={before}, after={after}"


@pytest.mark.asyncio
async def test_unrelated_high_performer_excluded_from_candidate_evidence(
    db_session: AsyncSession,
) -> None:
    """Unrelated high-performing historical records are excluded from candidate evidence and do not inflate score."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        titles = [
            "Quantum Computing Hardware Fundamentals",
            "Balcony Gardening Tips",
        ]
        channel_id_str, cand_ids = await _create_channel_and_candidates(
            client, count=2, titles=titles
        )
        channel_uuid = uuid.UUID(channel_id_str)

        # Seed 3 relevant Quantum records with low/moderate views
        rel_records = await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=3,
            title_prefix="Quantum Computing Hardware Guide",
            keywords=["quantum", "hardware", "computing"],
            tags=["quantum"],
            views_values=[1000.0, 2000.0, 3000.0],
        )

        # Seed 2 completely unrelated Gardening records with huge views
        unrel_records = await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=2,
            title_prefix="Indoor Plant Fertilizer Strategies",
            keywords=["plant", "fertilizer", "gardening"],
            tags=["gardening"],
            views_values=[100000.0, 200000.0],
        )

        resp = await client.post(
            f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
            json={
                "candidate_ids": cand_ids,
                "idempotency_key": f"p19e-unrelated-hp-{uuid.uuid4().hex}",
            },
        )
        assert resp.status_code == 201
        data = resp.json()

        q_dec = next(d for d in data["decisions"] if d["candidate_id"] == cand_ids[0])
        ev = q_dec["evidence_snapshot"]

        unrel_snap_ids = {str(snap.id) for snap, _ in unrel_records}
        unrel_obs_ids = {str(snap.observation_id) for snap, _ in unrel_records}

        # Assert unrelated high performers are excluded from candidate's evidence
        for snap_id in ev["learning_evidence_ids"]:
            assert snap_id not in unrel_snap_ids
        for obs_id in ev["analytics_evidence_ids"]:
            assert obs_id not in unrel_obs_ids

        # Matched evidence count equals len(learning_evidence_ids) and all are from rel_records
        rel_snap_ids = {str(snap.id) for snap, _ in rel_records}
        for item in ev["matched_evidence"]:
            assert item["learning_snapshot_id"] in rel_snap_ids
            assert item["learning_snapshot_id"] not in unrel_snap_ids

        # Score is moderate (not inflated to near 100)
        assert ev["historical_performance_score"] < 50.0


@pytest.mark.asyncio
async def test_same_key_concurrent_v2_selection(
    db_session: AsyncSession,
) -> None:
    """Concurrent requests with same key return same run and persist exactly one run row."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id_str, cand_ids = await _create_channel_and_candidates(client, count=2)
        shared_key = f"p19e-concurrent-key-{uuid.uuid4().hex}"

        async def _call() -> any:
            return await client.post(
                f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
                json={
                    "candidate_ids": cand_ids,
                    "idempotency_key": shared_key,
                },
            )

        resp_a, resp_b = await asyncio.gather(_call(), _call())
        assert resp_a.status_code == 201
        assert resp_b.status_code == 201
        data_a = resp_a.json()
        data_b = resp_b.json()

        assert data_a["id"] == data_b["id"]
        assert data_a["policy_checksum"] == data_b["policy_checksum"]
        assert data_a["candidate_set_checksum"] == data_b["candidate_set_checksum"]

        # Exactly 1 ContentSelectionRun row exists
        run_count = (
            await db_session.execute(
                select(func.count())
                .select_from(ContentSelectionRun)
                .where(ContentSelectionRun.idempotency_key == shared_key)
            )
        ).scalar_one()
        assert run_count == 1

        # Exactly 2 decisions exist for this run
        dec_count = (
            await db_session.execute(
                select(func.count())
                .select_from(ContentSelectionDecision)
                .where(ContentSelectionDecision.selection_run_id == uuid.UUID(data_a["id"]))
            )
        ).scalar_one()
        assert dec_count == 2


@pytest.mark.asyncio
async def test_pointer_integrity_fail_closed(
    db_session: AsyncSession,
) -> None:
    """Inconsistent latest pointer/snapshot causes fail-closed HistoricalPerformanceIntegrityError."""
    from omega.application.content_selection_service import create_selection_run
    from omega.domain.content_selection import ContentSelectionRunCreate
    from omega.domain.historical_performance import HistoricalPerformanceIntegrityError

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id_str, cand_ids = await _create_channel_and_candidates(client, count=2)
        channel_uuid = uuid.UUID(channel_id_str)

        # Seed 5 records
        created = await _seed_learning_evidence(
            db_session,
            channel_id=channel_uuid,
            count=5,
            title_prefix="Corrupt Pointer Series",
            keywords=["corrupt", "pointer"],
            tags=["test"],
            views_values=[1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
        )

        # Corrupt the pointer: set current_revision_sequence to 999 (snapshot has 1)
        _, pointer = created[0]
        pointer.current_revision_sequence = 999
        await db_session.commit()

        # Direct service call must fail closed with HistoricalPerformanceIntegrityError
        cand_uuids = [uuid.UUID(cid) for cid in cand_ids]
        req = ContentSelectionRunCreate(
            candidate_ids=cand_uuids,
            idempotency_key=f"fail-closed-key-{uuid.uuid4().hex}",
        )
        with pytest.raises(HistoricalPerformanceIntegrityError):
            await create_selection_run(db_session, channel_uuid, req)
        await db_session.rollback()

        # API call fails closed with HistoricalPerformanceIntegrityError
        api_key = f"fail-closed-api-{uuid.uuid4().hex}"
        with pytest.raises(HistoricalPerformanceIntegrityError):
            await client.post(
                f"/api/v1/channels/{channel_id_str}/topics/selection-runs",
                json={
                    "candidate_ids": cand_ids,
                    "idempotency_key": api_key,
                },
            )

        # No partially persisted run in DB
        persisted = (
            await db_session.execute(
                select(ContentSelectionRun).where(ContentSelectionRun.idempotency_key == api_key)
            )
        ).scalar_one_or_none()
        assert persisted is None


@pytest.mark.asyncio
async def test_content_identity_precedence_in_historical_evidence(
    db_session: AsyncSession,
) -> None:
    """Test 3-tier content identity precedence: SELECTION_DECISION_SNAPSHOT > MISSION_CANONICAL_TOPIC > PUBLISH_INTENT_METADATA."""
    from omega.application.historical_performance_provider import (
        LearningHistoricalPerformanceProvider,
    )
    from omega.domain.historical_performance import ContentIdentityAuthority

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel_id_str, _ = await _create_channel_and_candidates(client, count=1)
        channel_uuid = uuid.UUID(channel_id_str)

        env = await _ensure_test_environment_for_channel(db_session, channel_uuid)
        dna_rev = (
            await db_session.execute(
                select(ChannelDNARevision).where(ChannelDNARevision.channel_id == channel_uuid)
            )
        ).scalars().first()

        # Create a topic candidate with mutated title
        topic = TopicCandidate(
            id=uuid.uuid4(),
            channel_id=channel_uuid,
            title="Mutated Candidate Title",
            normalized_title="mutated candidate title",
            topic_fingerprint=f"fp_prec_{uuid.uuid4().hex[:8]}",
            status="DISCOVERED",
        )
        db_session.add(topic)
        await db_session.flush()

        # Precedence A: Selection decision with immutable snapshot
        run_id = uuid.uuid4()
        sel_run = ContentSelectionRun(
            id=run_id,
            channel_id=channel_uuid,
            channel_dna_revision_id=dna_rev.id,
            idempotency_key=f"prec-run-{uuid.uuid4().hex}",
            policy_name="OMEGA_CONTENT_SELECTION",
            policy_version=2,
            policy_checksum="dummy-cs",
            candidate_set_checksum="dummy-cand-cs",
            status="READY",
            recommended_candidate_id=topic.id,
            considered_count=1,
        )
        db_session.add(sel_run)

        decision_id = uuid.uuid4()
        cand_snap = {
            "id": str(topic.id),
            "title": "Immutable Decision Snapshot Title",
            "keywords": ["decision", "keyword"],
            "tags": ["decision_tag"],
        }
        decision = ContentSelectionDecision(
            id=decision_id,
            selection_run_id=run_id,
            candidate_id=topic.id,
            rank=1,
            final_score=85.0,
            score_breakdown={},
            reasons=[],
            duplicate_status="UNIQUE",
            candidate_title_snapshot=cand_snap["title"],
            topic_fingerprint_snapshot=topic.topic_fingerprint,
            candidate_snapshot=cand_snap,
            evidence_snapshot={},
        )
        db_session.add(decision)

        # Mission with campaign lineage pointing to selection_decision_id
        mission_a = Mission(
            id=uuid.uuid4(),
            channel_id=channel_uuid,
            title="Mission A",
            objective="Objective",
            autonomy_level="AUTONOMOUS",
            state="RUNNING",
            metadata_={
                "campaign_lineage": {"selection_decision_id": str(decision_id)},
                "canonical_inputs": {"topic_candidate_id": str(topic.id)},
            },
        )
        db_session.add(mission_a)

        task_a = Task(
            id=uuid.uuid4(),
            mission_id=mission_a.id,
            task_type="PUBLISH_VIDEO",
            title="Task A",
            state="COMPLETED",
        )
        db_session.add(task_a)

        intent_a = PublishIntent(
            id=uuid.uuid4(),
            mission_id=mission_a.id,
            task_id=task_a.id,
            channel_id=channel_uuid,
            platform_account_id=env["account"].id,
            media_artifact_id=env["artifact"].id,
            media_artifact_checksum="hash_a",
            title="Fallback Intent Title A",
            tags=["intent_tag_a"],
            description="Intent description",
            made_for_kids=False,
            intent_checksum=f"cs_a_{uuid.uuid4().hex}",
            state="PUBLISHED",
        )
        db_session.add(intent_a)

        # Precedence B: Mission with canonical topic_candidate_id, NO decision snapshot
        mission_b = Mission(
            id=uuid.uuid4(),
            channel_id=channel_uuid,
            title="Mission B",
            objective="Objective",
            autonomy_level="AUTONOMOUS",
            state="RUNNING",
            metadata_={
                "canonical_inputs": {"topic_candidate_id": str(topic.id)},
            },
        )
        db_session.add(mission_b)

        task_b = Task(
            id=uuid.uuid4(),
            mission_id=mission_b.id,
            task_type="PUBLISH_VIDEO",
            title="Task B",
            state="COMPLETED",
        )
        db_session.add(task_b)

        intent_b = PublishIntent(
            id=uuid.uuid4(),
            mission_id=mission_b.id,
            task_id=task_b.id,
            channel_id=channel_uuid,
            platform_account_id=env["account"].id,
            media_artifact_id=env["artifact"].id,
            media_artifact_checksum="hash_b",
            title="Fallback Intent Title B",
            tags=["intent_tag_b"],
            description="Intent description",
            made_for_kids=False,
            intent_checksum=f"cs_b_{uuid.uuid4().hex}",
            state="PUBLISHED",
        )
        db_session.add(intent_b)

        # Precedence C: PublishIntent fallback only (no mission metadata)
        task_c = Task(
            id=uuid.uuid4(),
            mission_id=env["mission"].id,
            task_type="PUBLISH_VIDEO",
            title="Task C",
            state="COMPLETED",
        )
        db_session.add(task_c)

        intent_c = PublishIntent(
            id=uuid.uuid4(),
            mission_id=env["mission"].id,
            task_id=task_c.id,
            channel_id=channel_uuid,
            platform_account_id=env["account"].id,
            media_artifact_id=env["artifact"].id,
            media_artifact_checksum="hash_c",
            title="Only Intent Title C",
            tags=["intent_tag_c"],
            description="Intent description",
            made_for_kids=False,
            intent_checksum=f"cs_c_{uuid.uuid4().hex}",
            state="PUBLISHED",
        )
        db_session.add(intent_c)
        await db_session.flush()

        snap_a = LearningInputSnapshot(
            id=uuid.uuid4(),
            observation_id=uuid.uuid4(),
            channel_id=channel_uuid,
            publish_intent_id=intent_a.id,
            provider_video_id=f"vid_a_{uuid.uuid4().hex[:6]}",
            media_artifact_id=env["artifact"].id,
            published_at_utc=datetime.now(UTC),
            window_type=WindowType.FIRST_7D.value,
            window_state=WindowState.FINALIZED.value,
            window_start_utc=datetime.now(UTC),
            window_end_utc=datetime.now(UTC),
            raw_metrics={"views": 5000.0},
            metric_qualities={"views": "AVAILABLE"},
            classifications={"views": "PROVIDER_FACT"},
            is_fully_finalized=True,
            quality_flags=[],
            payload_checksum=f"cs_snap_a_{uuid.uuid4().hex}",
            input_dedupe_key=f"dedupe_a_{uuid.uuid4().hex}",
            revision_sequence=1,
        )
        db_session.add(snap_a)

        snap_b = LearningInputSnapshot(
            id=uuid.uuid4(),
            observation_id=uuid.uuid4(),
            channel_id=channel_uuid,
            publish_intent_id=intent_b.id,
            provider_video_id=f"vid_b_{uuid.uuid4().hex[:6]}",
            media_artifact_id=env["artifact"].id,
            published_at_utc=datetime.now(UTC),
            window_type=WindowType.FIRST_7D.value,
            window_state=WindowState.FINALIZED.value,
            window_start_utc=datetime.now(UTC),
            window_end_utc=datetime.now(UTC),
            raw_metrics={"views": 6000.0},
            metric_qualities={"views": "AVAILABLE"},
            classifications={"views": "PROVIDER_FACT"},
            is_fully_finalized=True,
            quality_flags=[],
            payload_checksum=f"cs_snap_b_{uuid.uuid4().hex}",
            input_dedupe_key=f"dedupe_b_{uuid.uuid4().hex}",
            revision_sequence=1,
        )
        db_session.add(snap_b)

        snap_c = LearningInputSnapshot(
            id=uuid.uuid4(),
            observation_id=uuid.uuid4(),
            channel_id=channel_uuid,
            publish_intent_id=intent_c.id,
            provider_video_id=f"vid_c_{uuid.uuid4().hex[:6]}",
            media_artifact_id=env["artifact"].id,
            published_at_utc=datetime.now(UTC),
            window_type=WindowType.FIRST_7D.value,
            window_state=WindowState.FINALIZED.value,
            window_start_utc=datetime.now(UTC),
            window_end_utc=datetime.now(UTC),
            raw_metrics={"views": 7000.0},
            metric_qualities={"views": "AVAILABLE"},
            classifications={"views": "PROVIDER_FACT"},
            is_fully_finalized=True,
            quality_flags=[],
            payload_checksum=f"cs_snap_c_{uuid.uuid4().hex}",
            input_dedupe_key=f"dedupe_c_{uuid.uuid4().hex}",
            revision_sequence=1,
        )
        db_session.add(snap_c)
        await db_session.commit()

        # Batch resolve identities
        identities = await LearningHistoricalPerformanceProvider._batch_resolve_identities(
            session=db_session,
            channel_id=channel_uuid,
            snapshots=[snap_a, snap_b, snap_c],
        )

        # Verify Precedence A: uses decision snapshot, not mutated candidate title
        ident_a = identities[snap_a.id]
        assert ident_a.identity_authority == ContentIdentityAuthority.SELECTION_DECISION_SNAPSHOT
        assert ident_a.title == "Immutable Decision Snapshot Title"
        assert ident_a.keywords == ["decision", "keyword"]
        assert ident_a.tags == ["decision_tag"]

        # Verify Precedence B: uses candidate title
        ident_b = identities[snap_b.id]
        assert ident_b.identity_authority == ContentIdentityAuthority.MISSION_CANONICAL_TOPIC
        assert ident_b.title == "Mutated Candidate Title"

        # Verify Precedence C: uses intent title
        ident_c = identities[snap_c.id]
        assert ident_c.identity_authority == ContentIdentityAuthority.PUBLISH_INTENT_METADATA
        assert ident_c.title == "Only Intent Title C"
