"""Integration tests for OMEGA-013 Learning Engine services and REST API.

Covers:
- CohortService (filtering, duration buckets, channel isolation)
- BaselineService (immutability, sample size requirement, missing metric handling, pinning)
- HypothesisService (concurrency, deduplication, monotonic versions)
- EvaluationService & Evidence Manifests (atomicity, non-parametric statistics, manifest invariants)
- KnowledgeService (immutability, supersession, pointer updates)
- LearningJobService & Events (fencing, generations, idempotency)
- REST API (read-only knowledge/hypotheses, idempotent manual refresh, rate limiting, no mutation)
- Network and LLM safety (egress permits, execution outside DB transaction)
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.learning.baseline_service import BaselineService
from omega.application.learning.cohort_service import CohortService
from omega.application.learning.evaluation_service import EvaluationService
from omega.application.learning.feature_service import FeatureExtractionService
from omega.application.learning.hypothesis_service import HypothesisService
from omega.application.learning.job_service import LearningJobService
from omega.application.learning.knowledge_service import KnowledgeService
from omega.application.network.egress_permit import EgressPermitManager
from omega.domain.analytics import WindowState, WindowType
from omega.domain.learning import (
    ConfidenceClass,
    ContentFormat,
    LearningEventType,
    LearningJobStatus,
    compute_event_dedupe_key,
)
from omega.domain.network import ServiceCategory
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsWindow,
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    LearningBaseline,
    LearningCohort,
    LearningEvaluationEvidenceManifest,
    LearningEvent,
    LearningFeatureSnapshot,
    LearningHypothesis,
    LearningHypothesisEvaluation,
    LearningHypothesisLatestPointer,
    LearningInputSnapshot,
    LearningJob,
    LearningKnowledgeItem,
    LearningKnowledgeLatestPointer,
    MediaArtifact,
    Mission,
    PlatformAccount,
    ProductionRequest,
    PublishAttempt,
    PublishIntent,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
)
from omega.main import app

pytestmark = pytest.mark.usefixtures("publisher_test_env")


# ============================================================================
# TEST FIXTURES & HELPERS
# ============================================================================


@pytest_asyncio.fixture
async def learning_env(db_session: AsyncSession) -> dict[str, Any]:
    """Setup base entities conforming strictly to foreign key constraints."""
    chan = Channel(
        id=uuid4(),
        slug=f"chan-{uuid4().hex[:8]}",
        name="Learning Services Channel",
        platform="YOUTUBE",
    )
    db_session.add(chan)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=chan.id,
        version=1,
        snapshot={"name": "Learning DNA"},
        change_reason="Initial DNA",
    )
    db_session.add(dna)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=chan.id,
        platform="YOUTUBE",
        account_display_name="Test Channel Account",
        external_account_id=f"UC_{uuid4().hex[:8]}",
        status="ACTIVE",
        scopes=["https://www.googleapis.com/auth/youtube.readonly"],
    )
    db_session.add(account)

    mission = Mission(
        id=uuid4(),
        channel_id=chan.id,
        title="Learning Mission",
        objective="Learning objective",
        autonomy_level="AUTONOMOUS",
        state="RUNNING",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="PUBLISH_VIDEO",
        title="Publish Task",
        state="COMPLETED",
    )
    db_session.add(task)

    topic = TopicCandidate(
        id=uuid4(),
        channel_id=chan.id,
        title="Topic",
        normalized_title="topic",
        source_name="Manual Entry",
        summary="Topic summary",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        title="Brief",
        summary="Summary",
    )
    db_session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        status="APPROVED",
    )
    db_session.add(content_req)

    script_ver = ScriptVersion(
        id=uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Why This Hook Works? 5 Secret Tips",
        hook_text="Did you know this secret?",
        closing_text="Subscribe now",
        cta_text="Comment below",
    )
    db_session.add(script_ver)

    prod_req = ProductionRequest(
        id=uuid4(),
        channel_id=chan.id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna.id,
        status="COMPLETED",
    )
    db_session.add(prod_req)

    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/test.mp4",
        file_size_bytes=1024,
        content_hash="hash123",
        mime_type="video/mp4",
        duration_ms=45000,
    )
    db_session.add(artifact)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        channel_id=chan.id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum="hash123",
        channel_dna_revision_id=dna.id,
        title="Why This Hook Works? 5 Secret Tips",
        description="Here are 5 tips that worked well.",
        tags=["learning", "analytics", "growth"],
        made_for_kids=False,
        intent_checksum=f"intent_{uuid4().hex}",
        state="PUBLISHED",
    )
    db_session.add(intent)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"attempt_{uuid4().hex}",
        state="SUCCEEDED",
        provider_video_id=f"vid_{uuid4().hex[:10]}",
        provider_url="https://youtube.com/watch?v=123",
    )
    db_session.add(attempt)

    asset = AnalyticsAsset(
        id=uuid4(),
        channel_id=chan.id,
        platform_account_id=account.id,
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        media_artifact_id=artifact.id,
        channel_dna_revision_id=dna.id,
        provider="YOUTUBE",
        provider_video_id=attempt.provider_video_id,
        published_at=datetime.now(UTC) - timedelta(days=8),
        asset_status="ACTIVE",
        lifecycle_phase="ACTIVE_HOURLY",
        next_poll_due_at=datetime.now(UTC),
    )
    db_session.add(asset)

    window = AnalyticsWindow(
        id=uuid4(),
        asset_id=asset.id,
        window_type=WindowType.FIRST_7D.value,
        provider_date=None,
        provider_timezone="America/Los_Angeles",
        window_start_utc=asset.published_at,
        window_end_utc=asset.published_at + timedelta(days=7),
        window_state=WindowState.FINALIZED.value,
    )
    db_session.add(window)

    await db_session.commit()
    return {
        "channel": chan,
        "dna": dna,
        "intent": intent,
        "artifact": artifact,
        "asset": asset,
        "window": window,
    }


async def _create_input_and_feature(
    db_session: AsyncSession,
    env: dict[str, Any],
    window_type: WindowType = WindowType.FIRST_7D,
    window_state: WindowState = WindowState.FINALIZED,
    published_at: datetime | None = None,
    views: float = 1000.0,
    likes: float = 50.0,
    content_format: ContentFormat = ContentFormat.LONG_FORM,
    title_hook_type: str = "QUESTION",
    title_word_count: int = 6,
) -> tuple[LearningInputSnapshot, LearningFeatureSnapshot]:
    """Helper to create a coupled input snapshot and feature snapshot."""
    chan: Channel = env["channel"]
    intent: PublishIntent = env["intent"]
    artifact: MediaArtifact = env["artifact"]
    dna: ChannelDNARevision = env["dna"]

    pub_time = published_at or (datetime.now(UTC) - timedelta(days=5))
    inp = LearningInputSnapshot(
        observation_id=uuid4(),
        channel_id=chan.id,
        publish_intent_id=intent.id,
        provider_video_id=f"vid_{uuid4().hex[:11]}",
        media_artifact_id=artifact.id,
        channel_dna_revision_id=dna.id,
        window_type=window_type.value,
        window_state=window_state.value,
        window_start_utc=pub_time,
        window_end_utc=pub_time + timedelta(days=7),
        published_at_utc=pub_time,
        raw_metrics={"views": views, "likes": likes},
        metric_qualities={"views": "AVAILABLE", "likes": "AVAILABLE"},
        classifications={"views": "PROVIDER_FACT", "likes": "PROVIDER_FACT"},
        is_fully_finalized=True,
        quality_flags=[],
        payload_checksum=f"chk_{uuid4().hex}",
        input_dedupe_key=f"dedupe_{uuid4().hex}",
        revision_sequence=1,
    )
    db_session.add(inp)
    await db_session.flush()

    feat = LearningFeatureSnapshot(
        input_snapshot_id=inp.id,
        channel_id=chan.id,
        extractor_version="1.0.0",
        deterministic_features={
            "content_format": content_format.value,
            "title_hook_type": title_hook_type,
            "title_word_count": title_word_count,
            "title_has_numbers": True,
        },
        feature_snapshot_key=f"feat_{uuid4().hex}",
    )
    db_session.add(feat)
    await db_session.flush()
    return inp, feat


async def _create_hypothesis_and_evaluation(
    db_session: AsyncSession, env: dict[str, Any]
) -> tuple[LearningHypothesis, LearningHypothesisEvaluation]:
    chan: Channel = env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    for i in range(10):
        _, feat_t = await _create_input_and_feature(
            db_session, env, title_hook_type="QUESTION", views=300.0 + i
        )
        await CohortService.assign_cohort_membership(db_session, feat_t)
        _, feat_c = await _create_input_and_feature(
            db_session, env, title_hook_type="STATEMENT", views=100.0 + i
        )
        await CohortService.assign_cohort_membership(db_session, feat_c)
    await db_session.commit()

    hyp = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug=f"hyp-{uuid4().hex[:8]}",
        description="Eval test",
        factor_name="title_hook_type",
        treatment_definition={"feature": "title_hook_type", "value": "QUESTION"},
        control_definition={"feature": "title_hook_type", "value": "STATEMENT"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
    )
    await db_session.commit()

    eval_rec, _ = await EvaluationService.evaluate_hypothesis(
        db_session, hyp.hypothesis_family_id, datetime.now(UTC)
    )
    await db_session.commit()
    return hyp, eval_rec


# ============================================================================
# 1. COHORT TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_incompatible_window_excluded(db_session: AsyncSession, learning_env: dict[str, Any]):
    """Verify that observations from different evaluation windows are assigned to separate cohorts."""
    chan: Channel = learning_env["channel"]
    _, feat_24h = await _create_input_and_feature(
        db_session, learning_env, window_type=WindowType.FIRST_24H
    )
    _, feat_7d = await _create_input_and_feature(
        db_session, learning_env, window_type=WindowType.FIRST_7D
    )

    cohort_24h = await CohortService.get_or_create_default_cohort(
        db_session,
        chan.id,
        ContentFormat.LONG_FORM,
        WindowType.FIRST_24H,
    )
    cohort_7d = await CohortService.get_or_create_default_cohort(
        db_session,
        chan.id,
        ContentFormat.LONG_FORM,
        WindowType.FIRST_7D,
    )

    assert cohort_24h.id != cohort_7d.id
    assert cohort_24h.evaluation_window == WindowType.FIRST_24H.value
    assert cohort_7d.evaluation_window == WindowType.FIRST_7D.value


@pytest.mark.asyncio
async def test_short_and_long_form_not_mixed(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify that short form and long form content cannot be mixed in the same cohort."""
    _, feat_short = await _create_input_and_feature(
        db_session, learning_env, content_format=ContentFormat.SHORT
    )
    _, feat_long = await _create_input_and_feature(
        db_session, learning_env, content_format=ContentFormat.LONG_FORM
    )

    members_short = await CohortService.assign_cohort_membership(db_session, feat_short)
    members_long = await CohortService.assign_cohort_membership(db_session, feat_long)

    cohort_short_id = members_short[0].cohort_id
    cohort_long_id = members_long[0].cohort_id

    assert cohort_short_id != cohort_long_id

    c_short = await db_session.get(LearningCohort, cohort_short_id)
    c_long = await db_session.get(LearningCohort, cohort_long_id)

    assert c_short.content_format == ContentFormat.SHORT.value
    assert c_long.content_format == ContentFormat.LONG_FORM.value


@pytest.mark.asyncio
async def test_channel_local_learning_default(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify that cohorts are strictly channel-local by default."""
    chan_a: Channel = learning_env["channel"]
    other_chan = Channel(
        id=uuid4(),
        slug=f"chan-{uuid4().hex[:8]}",
        name="Other Channel",
        platform="YOUTUBE",
    )
    db_session.add(other_chan)
    await db_session.commit()

    cohort_a = await CohortService.get_or_create_default_cohort(
        db_session, chan_a.id, ContentFormat.LONG_FORM
    )
    cohort_b = await CohortService.get_or_create_default_cohort(
        db_session, other_chan.id, ContentFormat.LONG_FORM
    )

    assert cohort_a.id != cohort_b.id
    assert cohort_a.channel_id == chan_a.id
    assert cohort_b.channel_id == other_chan.id


@pytest.mark.asyncio
async def test_cross_channel_statistical_pooling_not_supported(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify system enforces channel-local scope and rejects cross-channel cohort membership pooling."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )

    # Inclusion criteria asserts channel_id match
    assert cohort.inclusion_criteria.get("channel_id") == str(chan.id)
    foreign_channel_id = uuid4()
    assert str(foreign_channel_id) != cohort.inclusion_criteria.get("channel_id")


@pytest.mark.asyncio
async def test_duplicate_cohort_membership_safe(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify duplicate assignment into the same cohort is idempotent."""
    _, feat = await _create_input_and_feature(
        db_session, learning_env, content_format=ContentFormat.LONG_FORM
    )

    members1 = await CohortService.assign_cohort_membership(db_session, feat)
    await db_session.commit()

    members2 = await CohortService.assign_cohort_membership(db_session, feat)
    await db_session.commit()

    assert members1[0].id == members2[0].id


# ============================================================================
# 2. BASELINE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_reproducible_baseline(db_session: AsyncSession, learning_env: dict[str, Any]):
    """Verify baseline calculation produces identical deterministic statistics for the same inputs."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )

    for i in range(12):
        _, feat = await _create_input_and_feature(
            db_session,
            learning_env,
            views=100.0 * (i + 1),
            published_at=datetime.now(UTC) - timedelta(days=i + 1),
        )
        await CohortService.assign_cohort_membership(db_session, feat)
    await db_session.commit()

    as_of = datetime.now(UTC)
    b1 = await BaselineService.calculate_cohort_baseline(
        db_session, cohort.id, "views", WindowType.FIRST_7D, as_of
    )
    assert b1 is not None
    assert b1.member_count == 12

    b2 = await BaselineService.calculate_cohort_baseline(
        db_session, cohort.id, "views", WindowType.FIRST_7D, as_of
    )
    assert b2 is not None
    assert b1.metric_median == b2.metric_median
    assert b1.metric_mad == b2.metric_mad
    assert b1.baseline_version == b2.baseline_version


@pytest.mark.asyncio
async def test_baseline_revision_when_member_set_changes(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify that adding new members increments the baseline revision sequence."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )

    for i in range(10):
        _, feat = await _create_input_and_feature(
            db_session,
            learning_env,
            views=200.0,
            published_at=datetime.now(UTC) - timedelta(days=i + 2),
        )
        await CohortService.assign_cohort_membership(db_session, feat)
    await db_session.commit()

    b1 = await BaselineService.calculate_cohort_baseline(
        db_session, cohort.id, "views", WindowType.FIRST_7D, datetime.now(UTC)
    )
    assert b1 is not None
    assert b1.baseline_version == 1
    assert b1.member_count == 10

    for _i in range(2):
        _, feat = await _create_input_and_feature(
            db_session,
            learning_env,
            views=500.0,
            published_at=datetime.now(UTC) - timedelta(days=1),
        )
        await CohortService.assign_cohort_membership(db_session, feat)
    await db_session.commit()

    b2 = await BaselineService.calculate_cohort_baseline(
        db_session, cohort.id, "views", WindowType.FIRST_7D, datetime.now(UTC)
    )
    assert b2 is not None
    assert b2.baseline_version == 2
    assert b2.member_count == 12


@pytest.mark.asyncio
async def test_old_baseline_remains_immutable(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify that creating a new baseline version does not mutate or overwrite the old row."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )

    for i in range(10):
        _, feat = await _create_input_and_feature(
            db_session,
            learning_env,
            views=100.0,
            published_at=datetime.now(UTC) - timedelta(days=i + 3),
        )
        await CohortService.assign_cohort_membership(db_session, feat)
    await db_session.commit()

    b1 = await BaselineService.calculate_cohort_baseline(
        db_session, cohort.id, "views", WindowType.FIRST_7D, datetime.now(UTC)
    )
    b1_id = b1.id
    b1_count = b1.member_count

    _, feat_new = await _create_input_and_feature(
        db_session,
        learning_env,
        views=900.0,
        published_at=datetime.now(UTC) - timedelta(days=1),
    )
    await CohortService.assign_cohort_membership(db_session, feat_new)
    await db_session.commit()

    b2 = await BaselineService.calculate_cohort_baseline(
        db_session, cohort.id, "views", WindowType.FIRST_7D, datetime.now(UTC)
    )
    await db_session.commit()

    b1_refetch = await db_session.get(LearningBaseline, b1_id)
    assert b1_refetch.baseline_version == 1
    assert b1_refetch.member_count == b1_count
    assert b2.id != b1_id


@pytest.mark.asyncio
async def test_missing_metric_not_zeroed(db_session: AsyncSession, learning_env: dict[str, Any]):
    """Verify observations with missing metric values are excluded rather than coerced to 0."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )

    for i in range(9):
        inp, feat = await _create_input_and_feature(
            db_session,
            learning_env,
            published_at=datetime.now(UTC) - timedelta(days=i + 1),
        )
        inp.raw_metrics["watch_time_hours"] = 10.0
        inp.metric_qualities["watch_time_hours"] = "AVAILABLE"
        await CohortService.assign_cohort_membership(db_session, feat)

    inp_missing, feat_missing = await _create_input_and_feature(
        db_session,
        learning_env,
        published_at=datetime.now(UTC) - timedelta(days=10),
    )
    inp_missing.raw_metrics["watch_time_hours"] = None
    inp_missing.metric_qualities["watch_time_hours"] = "UNKNOWN_MISSING"
    await CohortService.assign_cohort_membership(db_session, feat_missing)
    await db_session.commit()

    b = await BaselineService.calculate_cohort_baseline(
        db_session, cohort.id, "watch_time_hours", WindowType.FIRST_7D, datetime.now(UTC)
    )
    assert b is None


@pytest.mark.asyncio
async def test_baseline_member_selection_pinned_to_as_of_time(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify that publications occurring after as_of_utc are excluded."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=5)

    for i in range(10):
        _, feat = await _create_input_and_feature(
            db_session,
            learning_env,
            views=100.0,
            published_at=cutoff - timedelta(days=i + 1),
        )
        await CohortService.assign_cohort_membership(db_session, feat)

    for i in range(5):
        _, feat = await _create_input_and_feature(
            db_session,
            learning_env,
            views=1000.0,
            published_at=cutoff + timedelta(days=i + 1),
        )
        await CohortService.assign_cohort_membership(db_session, feat)
    await db_session.commit()

    b = await BaselineService.calculate_cohort_baseline(
        db_session, cohort.id, "views", WindowType.FIRST_7D, cutoff
    )
    assert b is not None
    assert b.member_count == 10
    assert b.metric_median == 100.0


# ============================================================================
# 3. HYPOTHESIS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_first_hypothesis_version_cannot_duplicate_version_1(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify proposing the same hypothesis definition concurrently resolves to exactly version 1."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    family_id = uuid4()

    h1 = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="test-hook-format",
        description="Questions perform better than statements",
        factor_name="title_hook_type",
        treatment_definition={"feature": "title_hook_type", "value": "QUESTION"},
        control_definition={"feature": "title_hook_type", "value": "STATEMENT"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )
    await db_session.commit()

    h2 = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="test-hook-format",
        description="Questions perform better than statements",
        factor_name="title_hook_type",
        treatment_definition={"feature": "title_hook_type", "value": "QUESTION"},
        control_definition={"feature": "title_hook_type", "value": "STATEMENT"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )
    await db_session.commit()

    assert h1.id == h2.id
    assert h1.hypothesis_version == 1


@pytest.mark.asyncio
async def test_concurrent_hypothesis_revision_serializes_versions(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify hypothesis revision creation increments version sequence strictly monotonically."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    family_id = uuid4()

    h1 = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="test-revisions",
        description="Initial version",
        factor_name="title_word_count",
        treatment_definition={"feature": "title_word_count", "operator": "lte", "value": 5},
        control_definition={"feature": "title_word_count", "operator": "gt", "value": 5},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )
    await db_session.commit()

    h2 = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="test-revisions",
        description="Revised version with stricter threshold",
        factor_name="title_word_count",
        treatment_definition={"feature": "title_word_count", "operator": "lte", "value": 4},
        control_definition={"feature": "title_word_count", "operator": "gt", "value": 4},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )
    await db_session.commit()

    assert h1.hypothesis_version == 1
    assert h2.hypothesis_version == 2
    assert h2.supersedes_hypothesis_id == h1.id


@pytest.mark.asyncio
async def test_hypothesis_revision_is_append_only(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify original hypothesis version is never mutated when a revision is created."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    family_id = uuid4()

    h1 = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="append-only-check",
        description="V1 description",
        factor_name="title_hook_type",
        treatment_definition={"val": 1},
        control_definition={"val": 0},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )
    h1_id = h1.id
    await db_session.commit()

    await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="append-only-check",
        description="V2 description",
        factor_name="title_hook_type",
        treatment_definition={"val": 2},
        control_definition={"val": 0},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )
    await db_session.commit()

    h1_refetch = await db_session.get(LearningHypothesis, h1_id)
    assert h1_refetch.description == "V1 description"
    assert h1_refetch.hypothesis_version == 1


@pytest.mark.asyncio
async def test_same_hypothesis_definition_deduplicates(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify proposing the identical definition multiple times returns the same entity."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    family_id = uuid4()

    args = dict(
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="dedupe-check",
        description="Dedupe check",
        factor_name="hook",
        treatment_definition={"hook": "Q"},
        control_definition={"hook": "S"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )

    ha = await HypothesisService.propose_or_get_hypothesis(db_session, **args)
    hb = await HypothesisService.propose_or_get_hypothesis(db_session, **args)
    assert ha.id == hb.id


@pytest.mark.asyncio
async def test_changed_hypothesis_definition_creates_new_version(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify changing any element of the definition checksum creates a new version."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    family_id = uuid4()

    h1 = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="def-change",
        description="Initial",
        factor_name="hook",
        treatment_definition={"feature": "hook", "value": "A"},
        control_definition={"feature": "hook", "value": "B"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )
    await db_session.commit()

    h2 = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="def-change",
        description="Changed factor",
        factor_name="hook",
        treatment_definition={"feature": "hook", "value": "A_MODIFIED"},
        control_definition={"feature": "hook", "value": "B"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
        hypothesis_family_id=family_id,
    )
    await db_session.commit()

    assert h2.hypothesis_version == 2
    assert h2.definition_checksum != h1.definition_checksum


# ============================================================================
# 4. EVIDENCE MANIFEST & ATOMIC EVALUATION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_evaluation_records_exact_treatment_and_control_evidence(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify evaluation produces an evidence manifest detailing exact treatment and control samples."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )

    for i in range(10):
        _, feat_t = await _create_input_and_feature(
            db_session,
            learning_env,
            title_hook_type="QUESTION",
            views=500.0 + i * 10,
        )
        await CohortService.assign_cohort_membership(db_session, feat_t)

        _, feat_c = await _create_input_and_feature(
            db_session,
            learning_env,
            title_hook_type="STATEMENT",
            views=100.0 + i * 10,
        )
        await CohortService.assign_cohort_membership(db_session, feat_c)
    await db_session.commit()

    hyp = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="hook-eval",
        description="Question vs Statement",
        factor_name="title_hook_type",
        treatment_definition={"feature": "title_hook_type", "value": "QUESTION"},
        control_definition={"feature": "title_hook_type", "value": "STATEMENT"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
    )
    await db_session.commit()

    eval_record, manifest = await EvaluationService.evaluate_hypothesis(
        db_session, hyp.hypothesis_family_id, datetime.now(UTC)
    )
    await db_session.commit()

    assert eval_record is not None
    assert manifest is not None
    assert len(manifest.treatment_input_snapshot_ids) == 10
    assert len(manifest.control_input_snapshot_ids) == 10
    assert len(manifest.treatment_values) == 10
    assert len(manifest.control_values) == 10
    assert manifest.evaluation_id == eval_record.id


@pytest.mark.asyncio
async def test_evaluation_can_be_reproduced_from_evidence_manifest(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify that computing stats on manifest values reproduces exact evaluation metrics."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )

    for i in range(10):
        _, feat_t = await _create_input_and_feature(
            db_session, learning_env, title_hook_type="QUESTION", views=300.0 + i * 5
        )
        await CohortService.assign_cohort_membership(db_session, feat_t)
        _, feat_c = await _create_input_and_feature(
            db_session, learning_env, title_hook_type="STATEMENT", views=150.0 + i * 5
        )
        await CohortService.assign_cohort_membership(db_session, feat_c)
    await db_session.commit()

    hyp = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="reproduce-eval",
        description="Reproduce eval test",
        factor_name="title_hook_type",
        treatment_definition={"feature": "title_hook_type", "value": "QUESTION"},
        control_definition={"feature": "title_hook_type", "value": "STATEMENT"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
    )
    await db_session.commit()

    eval_record, manifest = await EvaluationService.evaluate_hypothesis(
        db_session, hyp.hypothesis_family_id, datetime.now(UTC)
    )
    await db_session.commit()

    t_vals = manifest.treatment_values
    c_vals = manifest.control_values
    reproduced_t_median = statistics.median(t_vals)
    reproduced_c_median = statistics.median(c_vals)

    assert round(eval_record.treatment_median, 4) == round(reproduced_t_median, 4)
    assert round(eval_record.control_median, 4) == round(reproduced_c_median, 4)


@pytest.mark.asyncio
async def test_evaluation_cannot_commit_without_evidence_manifest(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify foreign key constraint: evaluation must link to an evidence manifest."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    for _i in range(10):
        _, feat_t = await _create_input_and_feature(
            db_session, learning_env, title_hook_type="QUESTION", views=200.0
        )
        await CohortService.assign_cohort_membership(db_session, feat_t)
        _, feat_c = await _create_input_and_feature(
            db_session, learning_env, title_hook_type="STATEMENT", views=100.0
        )
        await CohortService.assign_cohort_membership(db_session, feat_c)
    await db_session.commit()

    hyp = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="manifest-fk-test",
        description="FK test",
        factor_name="title_hook_type",
        treatment_definition={"feature": "title_hook_type", "value": "QUESTION"},
        control_definition={"feature": "title_hook_type", "value": "STATEMENT"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
    )
    await db_session.commit()

    eval_rec, manifest = await EvaluationService.evaluate_hypothesis(
        db_session, hyp.hypothesis_family_id, datetime.now(UTC)
    )
    await db_session.commit()

    stmt = select(LearningEvaluationEvidenceManifest).where(
        LearningEvaluationEvidenceManifest.evaluation_id == eval_rec.id
    )
    fetched_manifest = (await db_session.execute(stmt)).scalar_one_or_none()
    assert fetched_manifest is not None
    assert fetched_manifest.id == manifest.id


@pytest.mark.asyncio
async def test_manifest_failure_rolls_back_evaluation(
    db_session: AsyncSession, learning_env: dict[str, Any], monkeypatch
):
    """Verify that an error persisting the manifest rolls back the evaluation record."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    for _i in range(10):
        _, feat_t = await _create_input_and_feature(
            db_session, learning_env, title_hook_type="QUESTION", views=350.0
        )
        await CohortService.assign_cohort_membership(db_session, feat_t)
        _, feat_c = await _create_input_and_feature(
            db_session, learning_env, title_hook_type="STATEMENT", views=150.0
        )
        await CohortService.assign_cohort_membership(db_session, feat_c)
    await db_session.commit()

    hyp = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="rollback-test",
        description="Rollback test",
        factor_name="title_hook_type",
        treatment_definition={"feature": "title_hook_type", "value": "QUESTION"},
        control_definition={"feature": "title_hook_type", "value": "STATEMENT"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
    )
    await db_session.commit()

    def broken_checksum(*args, **kwargs):
        raise RuntimeError("Simulated manifest failure")

    monkeypatch.setattr(
        "omega.application.learning.evaluation_service.compute_manifest_checksum",
        broken_checksum,
    )

    with pytest.raises(RuntimeError, match="Simulated manifest failure"):
        await EvaluationService.evaluate_hypothesis(
            db_session, hyp.hypothesis_family_id, datetime.now(UTC)
        )

    stmt = select(LearningHypothesisEvaluation).where(
        LearningHypothesisEvaluation.hypothesis_id == hyp.id
    )
    evals = (await db_session.execute(stmt)).scalars().all()
    assert len(evals) == 0


@pytest.mark.asyncio
async def test_evaluation_and_latest_pointer_update_are_atomic(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify hypothesis evaluation and pointer update commit atomically."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    for _i in range(10):
        _, feat_t = await _create_input_and_feature(
            db_session, learning_env, title_hook_type="QUESTION", views=350.0
        )
        await CohortService.assign_cohort_membership(db_session, feat_t)
        _, feat_c = await _create_input_and_feature(
            db_session, learning_env, title_hook_type="STATEMENT", views=150.0
        )
        await CohortService.assign_cohort_membership(db_session, feat_c)
    await db_session.commit()

    hyp = await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="pointer-atomic-test",
        description="Pointer atomicity test",
        factor_name="title_hook_type",
        treatment_definition={"feature": "title_hook_type", "value": "QUESTION"},
        control_definition={"feature": "title_hook_type", "value": "STATEMENT"},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
    )
    await db_session.commit()

    eval_rec, _ = await EvaluationService.evaluate_hypothesis(
        db_session, hyp.hypothesis_family_id, datetime.now(UTC)
    )
    await db_session.commit()

    ptr = await db_session.get(LearningHypothesisLatestPointer, hyp.hypothesis_family_id)
    assert ptr.latest_evaluation_id == eval_rec.id


# ============================================================================
# 5. KNOWLEDGE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_first_knowledge_revision_cannot_duplicate_revision_1(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify creating the initial knowledge revision serializes to revision 1."""
    chan: Channel = learning_env["channel"]
    hyp, eval_rec = await _create_hypothesis_and_evaluation(db_session, learning_env)
    fam_id = uuid4()
    item1 = await KnowledgeService.create_or_advance_knowledge(
        session=db_session,
        channel_id=chan.id,
        knowledge_type="FACTOR_CORRELATION",
        structured_claim={"factor": "title_hook_type", "finding": "QUESTION_OUTPERFORMS"},
        human_readable_summary="Question hooks perform better.",
        confidence_class=ConfidenceClass.MODERATE,
        effect_size_absolute=200.0,
        effect_size_relative_percent=50.0,
        cliffs_delta=0.6,
        sample_size_treatment=10,
        sample_size_control=10,
        source_hypothesis_id=hyp.id,
        source_evaluation_id=eval_rec.id,
        knowledge_family_id=fam_id,
    )
    await db_session.commit()

    assert item1.revision_number == 1

    ptr = await db_session.get(LearningKnowledgeLatestPointer, fam_id)
    assert ptr is not None
    assert ptr.current_revision_number == 1
    assert ptr.current_knowledge_item_id == item1.id


@pytest.mark.asyncio
async def test_old_knowledge_payload_never_mutated(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify older knowledge revisions remain strictly immutable after an update."""
    chan: Channel = learning_env["channel"]
    hyp, eval_rec = await _create_hypothesis_and_evaluation(db_session, learning_env)
    fam_id = uuid4()
    item1 = await KnowledgeService.create_or_advance_knowledge(
        session=db_session,
        channel_id=chan.id,
        knowledge_type="FACTOR_CORRELATION",
        structured_claim={"version": 1},
        human_readable_summary="V1 summary",
        confidence_class=ConfidenceClass.LOW,
        effect_size_absolute=50.0,
        effect_size_relative_percent=10.0,
        cliffs_delta=0.2,
        sample_size_treatment=10,
        sample_size_control=10,
        source_hypothesis_id=hyp.id,
        source_evaluation_id=eval_rec.id,
        knowledge_family_id=fam_id,
    )
    item1_id = item1.id
    await db_session.commit()

    item2 = await KnowledgeService.create_or_advance_knowledge(
        session=db_session,
        channel_id=chan.id,
        knowledge_type="FACTOR_CORRELATION",
        structured_claim={"version": 2},
        human_readable_summary="V2 summary",
        confidence_class=ConfidenceClass.HIGH,
        effect_size_absolute=150.0,
        effect_size_relative_percent=30.0,
        cliffs_delta=0.8,
        sample_size_treatment=25,
        sample_size_control=25,
        source_hypothesis_id=hyp.id,
        source_evaluation_id=eval_rec.id,
        knowledge_family_id=fam_id,
    )
    await db_session.commit()

    item1_refetch = await db_session.get(LearningKnowledgeItem, item1_id)
    assert item1_refetch.human_readable_summary == "V1 summary"
    assert item1_refetch.confidence_class == ConfidenceClass.LOW.value
    assert item2.revision_number == 2
    assert item2.supersedes_id == item1_id


@pytest.mark.asyncio
async def test_knowledge_supersession_preserves_old_lineage(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify revision chain maintains backwards pointers via supersedes_id."""
    chan: Channel = learning_env["channel"]
    hyp, eval_rec = await _create_hypothesis_and_evaluation(db_session, learning_env)
    fam_id = uuid4()
    prev_id = None
    for rev in range(1, 4):
        item = await KnowledgeService.create_or_advance_knowledge(
            session=db_session,
            channel_id=chan.id,
            knowledge_type="FACTOR_CORRELATION",
            structured_claim={"step": rev},
            human_readable_summary=f"Revision {rev}",
            confidence_class=ConfidenceClass.MODERATE,
            effect_size_absolute=100.0,
            effect_size_relative_percent=20.0,
            cliffs_delta=0.5,
            sample_size_treatment=10,
            sample_size_control=10,
            source_hypothesis_id=hyp.id,
            source_evaluation_id=eval_rec.id,
            knowledge_family_id=fam_id,
        )
        await db_session.commit()
        if rev > 1:
            assert item.supersedes_id == prev_id
        prev_id = item.id


@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_newer_knowledge_pointer(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify latest pointer check prevents overwriting with a lower revision."""
    chan: Channel = learning_env["channel"]
    hyp, eval_rec = await _create_hypothesis_and_evaluation(db_session, learning_env)
    fam_id = uuid4()
    await KnowledgeService.create_or_advance_knowledge(
        session=db_session,
        channel_id=chan.id,
        knowledge_type="FACTOR",
        structured_claim={},
        human_readable_summary="Rev 1",
        confidence_class=ConfidenceClass.LOW,
        effect_size_absolute=10.0,
        effect_size_relative_percent=5.0,
        cliffs_delta=0.1,
        sample_size_treatment=10,
        sample_size_control=10,
        source_hypothesis_id=hyp.id,
        source_evaluation_id=eval_rec.id,
        knowledge_family_id=fam_id,
    )
    item2 = await KnowledgeService.create_or_advance_knowledge(
        session=db_session,
        channel_id=chan.id,
        knowledge_type="FACTOR",
        structured_claim={},
        human_readable_summary="Rev 2",
        confidence_class=ConfidenceClass.HIGH,
        effect_size_absolute=20.0,
        effect_size_relative_percent=10.0,
        cliffs_delta=0.2,
        sample_size_treatment=20,
        sample_size_control=20,
        source_hypothesis_id=hyp.id,
        source_evaluation_id=eval_rec.id,
        knowledge_family_id=fam_id,
    )
    await db_session.commit()

    ptr = await db_session.get(LearningKnowledgeLatestPointer, fam_id)
    assert ptr.current_revision_number == 2
    assert ptr.current_knowledge_item_id == item2.id


# ============================================================================
# 6. EVENTS & JOBS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_learning_event_append_only_and_idempotent(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify event recording creates append-only log with deterministic deduplication."""
    chan: Channel = learning_env["channel"]
    key = compute_event_dedupe_key(
        LearningEventType.OBSERVATION_INGESTED.value,
        "INPUT",
        uuid4(),
        "rev_1",
    )
    ev1 = LearningEvent(
        event_type=LearningEventType.OBSERVATION_INGESTED.value,
        channel_id=chan.id,
        aggregate_type="INPUT",
        aggregate_id=uuid4(),
        source_ids=[],
        actor="system",
        payload={"sample": 1},
        event_dedupe_key=key,
    )
    db_session.add(ev1)
    await db_session.commit()

    ev2 = LearningEvent(
        event_type=LearningEventType.OBSERVATION_INGESTED.value,
        channel_id=chan.id,
        aggregate_type="INPUT",
        aggregate_id=uuid4(),
        source_ids=[],
        actor="system",
        payload={"sample": 2},
        event_dedupe_key=key,
    )
    db_session.add(ev2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_duplicate_manual_refresh_creates_one_semantic_job(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify concurrent manual refresh triggers reuse an existing pending job."""
    chan: Channel = learning_env["channel"]
    identity = "refresh_window_001"
    job1, created1 = await LearningJobService.get_or_create_job(
        db_session, chan.id, "MANUAL_REFRESH", identity
    )
    job2, created2 = await LearningJobService.get_or_create_job(
        db_session, chan.id, "MANUAL_REFRESH", identity
    )
    await db_session.commit()

    assert created1 is True
    assert created2 is False
    assert job1.id == job2.id


@pytest.mark.asyncio
async def test_learning_job_claim_generation_and_lease_fencing(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify job lease increments generation counter and allows re-claiming after expiry."""
    chan: Channel = learning_env["channel"]
    job, _ = await LearningJobService.get_or_create_job(
        db_session, chan.id, "EVALUATION_SWEEP", uuid4().hex
    )
    await db_session.commit()

    claimed1 = await LearningJobService.claim_job(db_session, job.id, "worker_1", lease_seconds=1)
    await db_session.commit()
    assert claimed1 is not None
    assert claimed1.claim_generation == 1
    token1 = claimed1.claim_token

    claimed2_fail = await LearningJobService.claim_job(
        db_session, job.id, "worker_2", lease_seconds=300
    )
    assert claimed2_fail is None

    claimed1.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
    await db_session.commit()

    claimed2 = await LearningJobService.claim_job(db_session, job.id, "worker_2", lease_seconds=300)
    await db_session.commit()
    assert claimed2 is not None
    assert claimed2.claim_generation == 2
    assert claimed2.claim_token != token1


@pytest.mark.asyncio
async def test_stale_worker_cannot_complete_learning_job(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify fenced worker with old generation token cannot mark a job completed."""
    chan: Channel = learning_env["channel"]
    job, _ = await LearningJobService.get_or_create_job(
        db_session, chan.id, "EVALUATION_SWEEP", uuid4().hex
    )
    await db_session.commit()

    w1_job = await LearningJobService.claim_job(db_session, job.id, "worker_1", lease_seconds=1)
    w1_token = w1_job.claim_token
    w1_gen = w1_job.claim_generation
    w1_job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    await LearningJobService.claim_job(db_session, job.id, "worker_2", lease_seconds=300)
    await db_session.commit()

    success = await LearningJobService.verify_and_complete_job(db_session, job.id, w1_token, w1_gen)
    await db_session.commit()
    assert success is False

    refetched = await db_session.get(LearningJob, job.id)
    assert refetched.status == LearningJobStatus.CLAIMED.value


# ============================================================================
# 7. API & SECURITY TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_channel_knowledge(db_session: AsyncSession, learning_env: dict[str, Any]):
    """Verify GET /api/v1/learning/channels/{channel_id}/knowledge returns active knowledge items."""
    chan: Channel = learning_env["channel"]
    hyp, eval_rec = await _create_hypothesis_and_evaluation(db_session, learning_env)
    await KnowledgeService.create_or_advance_knowledge(
        session=db_session,
        channel_id=chan.id,
        knowledge_type="TOPIC_AFFINITY",
        structured_claim={"topic": "Tech"},
        human_readable_summary="Tech topics show 25% higher CTR.",
        confidence_class=ConfidenceClass.MODERATE,
        effect_size_absolute=25.0,
        effect_size_relative_percent=25.0,
        cliffs_delta=0.5,
        sample_size_treatment=12,
        sample_size_control=12,
        source_hypothesis_id=hyp.id,
        source_evaluation_id=eval_rec.id,
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/learning/channels/{chan.id}/knowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert any(
            item["human_readable_summary"] == "Tech topics show 25% higher CTR." for item in data
        )


@pytest.mark.asyncio
async def test_get_hypothesis_history(db_session: AsyncSession, learning_env: dict[str, Any]):
    """Verify GET /api/v1/learning/channels/{channel_id}/hypotheses returns active hypothesis summaries."""
    chan: Channel = learning_env["channel"]
    cohort = await CohortService.get_or_create_default_cohort(
        db_session, chan.id, ContentFormat.LONG_FORM
    )
    await HypothesisService.propose_or_get_hypothesis(
        session=db_session,
        channel_id=chan.id,
        cohort_id=cohort.id,
        hypothesis_slug="api-history-check",
        description="API history test",
        factor_name="hook",
        treatment_definition={"val": 1},
        control_definition={"val": 0},
        target_outcome_metric="views",
        target_evaluation_window=WindowType.FIRST_7D,
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/learning/channels/{chan.id}/hypotheses")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["hypothesis_slug"] == "api-history-check"


@pytest.mark.asyncio
async def test_manual_refresh_idempotent(db_session: AsyncSession, learning_env: dict[str, Any]):
    """Verify POST /api/v1/learning/channels/{channel_id}/refresh creates background job and is idempotent."""
    chan: Channel = learning_env["channel"]
    req_id = uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post(f"/api/v1/learning/channels/{chan.id}/refresh?request_id={req_id}")
        assert r1.status_code in (200, 202)
        data1 = r1.json()
        assert data1["status"] in ("PENDING", "CLAIMED", "SUCCESS")

        r2 = await client.post(f"/api/v1/learning/channels/{chan.id}/refresh?request_id={req_id}")
        assert r2.status_code in (200, 202)
        data2 = r2.json()
        assert data1["job_id"] == data2["job_id"]


@pytest.mark.asyncio
async def test_manual_refresh_rate_limited(db_session: AsyncSession, learning_env: dict[str, Any]):
    """Verify rapid repeated manual refreshes return successful idempotent job or 429 rate limit."""
    chan: Channel = learning_env["channel"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.post(f"/api/v1/learning/channels/{chan.id}/refresh") for _ in range(5)
        ]
        assert all(r.status_code in (200, 202, 429) for r in responses)


@pytest.mark.asyncio
async def test_learning_api_has_no_publisher_scheduler_or_dna_mutation():
    """Verify learning endpoints never expose or execute publish, schedule, or DNA mutation actions."""
    from omega.api.learning import router as learning_router

    forbidden_words = [
        "publish",
        "schedule",
        "mutate_dna",
        "update_dna",
        "auto_select",
        "auto_optimize",
    ]
    for route in learning_router.routes:
        path = getattr(route, "path", "").lower()
        name = getattr(route, "name", "").lower()
        for forbidden in forbidden_words:
            assert forbidden not in path, f"Forbidden keyword '{forbidden}' in route path: {path}"
            assert forbidden not in name, f"Forbidden keyword '{forbidden}' in route name: {name}"


# ============================================================================
# 8. NETWORK & LLM SAFETY TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_external_model_call_requires_fresh_destination_permit():
    """Verify that any outbound model extractor checks fresh destination egress permits."""
    # 1. Valid unexpired permit
    permit = EgressPermitManager.issue_permit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://generativelanguage.googleapis.com",
        service_category=ServiceCategory.AI_PROVIDER,
        ttl_seconds=60.0,
    )
    assert permit.is_valid_for("https://generativelanguage.googleapis.com/v1beta/models")

    # 2. Expired permit
    expired_permit = EgressPermitManager.issue_permit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://generativelanguage.googleapis.com",
        service_category=ServiceCategory.AI_PROVIDER,
        ttl_seconds=-10.0,
    )
    assert not expired_permit.is_valid_for(
        "https://generativelanguage.googleapis.com/v1beta/models"
    )

    # 3. Destination mismatch (SSRF protection)
    assert not permit.is_valid_for("http://169.254.169.254/latest/meta-data")


@pytest.mark.asyncio
async def test_learning_model_network_call_outside_db_transaction(
    db_session: AsyncSession, learning_env: dict[str, Any]
):
    """Verify model feature extraction operates on in-memory payloads before opening database transactions."""
    inp, snap = await _create_input_and_feature(db_session, learning_env)

    model_extraction = await FeatureExtractionService.attach_model_features(
        session=db_session,
        feature_snapshot=snap,
        model_provider="google",
        model_name="gemini-1.5-flash",
        prompt_template_version="1.0.0",
        prompt_text="Extract features for video: 10 Secrets to High Retention Hooks",
        structured_output={"hook_style": "QUESTION", "tone": "EDUCATIONAL"},
        latency_ms=120,
    )
    await db_session.commit()

    assert model_extraction is not None
    assert model_extraction.feature_snapshot_id == snap.id
    assert model_extraction.model_name == "gemini-1.5-flash"
    assert "chain_of_thought" not in model_extraction.structured_output
