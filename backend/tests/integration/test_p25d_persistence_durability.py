"""Migration 029: database-only recovery, pinned history, and approval boundaries.

Uses real isolated PostgreSQL rows and the canonical service, with no provider calls.
The interrupted synthetic result helpers are retained; their results are now persisted
with complete experiment ancestry before the learning ingestion boundary is exercised.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from omega.application.learning.learning_repository import LearningRepository
from omega.application.learning.p25d_learning_service import LearningService, P25DLearningService
from omega.application.learning.p25d_query_service import P25DQueryService
from omega.domain.experimentation import (
    AttributionClassification,
    AttributionResult,
    DataMaturityState,
    StatisticalInferenceResult,
)
from omega.domain.learning_loop import (
    AdaptationStatus,
    CausalityStatus,
    LearningHypothesis,
    LearningPolicy,
    RecommendationAction,
)
from omega.domain.performance_analytics import (
    CanonicalMetrics,
    DataFreshnessStatus,
    PerformanceSnapshot,
)
from omega.infrastructure.models import (
    AnalyticsProviderSnapshot,
    Channel,
    ExperimentAttributionResultModel,
    ExperimentRevision,
    ExperimentRoot,
    ExperimentVariantModel,
    LearningCandidateAdaptationModel,
    LearningEvaluationModel,
    LearningInsightModel,
    LearningLoopEvidenceModel,
    LearningRecommendationModel,
    PlatformAccount,
)
from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


def create_fake_attribution_result(
    classification: AttributionClassification = AttributionClassification.TREATMENT_BETTER,
    relative_lift: float = 0.24,
    maturity: DataMaturityState = DataMaturityState.MATURE,
    metric: str = "impressions_ctr",
) -> AttributionResult:
    """Helper to construct synthetic AttributionResult with complete fields."""
    return AttributionResult(
        result_id=uuid4(),
        experiment_id=uuid4(),
        control_variant_id=uuid4(),
        treatment_variant_id=uuid4(),
        primary_metric=metric,
        analysis_window="24h",
        input_lineage_fingerprint="fp-" + uuid4().hex[:12],
        control_value=0.05,
        treatment_value=0.062 if relative_lift > 0 else 0.04,
        absolute_difference=0.012 if relative_lift > 0 else -0.01,
        relative_lift=relative_lift,
        sample_basis={"control": 5000, "treatment": 5000},
        statistical_inference=StatisticalInferenceResult(
            is_statistically_significant=True,
            p_value=0.002,
        ),
        data_maturity=maturity,
        classification=classification,
        findings=["Statistically significant treatment lift"],
        evaluated_at=datetime.now(UTC),
    )


def create_fake_performance_snapshot(
    channel_id: UUID,
    views: int = 1200,
    impressions_ctr: float = 0.06,
) -> PerformanceSnapshot:
    """Helper to construct synthetic PerformanceSnapshot."""
    now = datetime.now(UTC)
    return PerformanceSnapshot(
        snapshot_id=uuid4(),
        receipt_id=uuid4(),
        provider="YOUTUBE",
        external_media_id="ext-vid-001",
        observed_at=now,
        freshness=DataFreshnessStatus.FRESH,
        metrics=CanonicalMetrics(
            views=views,
            impressions_ctr=impressions_ctr,
        ),
    )


async def make_context(session):
    channel = Channel(
        id=uuid4(),
        slug="p25d-" + uuid4().hex[:10],
        name="Learning repair",
        platform="YOUTUBE",
        state="ACTIVE",
    )
    session.add(channel)
    await session.flush()
    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform="YOUTUBE",
        account_display_name="Learning test",
        external_account_id=uuid4().hex,
        status="ACTIVE",
    )
    session.add(account)
    artifact = await create_artifact_with_ancestry(session, channel.id, "a" * 64)
    await session.flush()
    return {"channel": str(channel.id), "account": str(account.id), "artifact": str(artifact.id)}


async def durable_result(session, ids, lift=0.24, metric="impressions_ctr", classification=None):
    result = create_fake_attribution_result(
        relative_lift=lift,
        metric=metric,
        classification=classification
        or (
            AttributionClassification.TREATMENT_BETTER
            if lift > 0
            else AttributionClassification.CONTROL_BETTER
        ),
    )
    root = ExperimentRoot(
        id=result.experiment_id,
        channel_id=UUID(ids["channel"]),
        target_scope_type="MEDIA_ARTIFACT",
        target_scope_id=ids["artifact"],
        status="COMPLETED",
    )
    session.add(root)
    await session.flush()
    revision = ExperimentRevision(
        id=uuid4(),
        experiment_root_id=root.id,
        revision_number=1,
        experiment_type="TITLE",
        hypothesis="A question title improves engagement",
        primary_metric=metric,
        experiment_unit="IMPRESSION",
        minimum_sample_size=1000,
        analysis_window_hours=24,
    )
    session.add(revision)
    await session.flush()
    for variant_id, role in (
        (result.control_variant_id, "CONTROL"),
        (result.treatment_variant_id, "TREATMENT"),
    ):
        session.add(
            ExperimentVariantModel(
                id=variant_id,
                experiment_revision_id=revision.id,
                role=role,
                change_dimension="TITLE",
                media_artifact_id=UUID(ids["artifact"]),
                is_accepted_p24=True,
                creative_qa_status="PASS",
                variant_snapshot_hash=variant_id.hex * 2,
            )
        )
    await session.flush()
    session.add(
        ExperimentAttributionResultModel(
            id=result.result_id,
            experiment_revision_id=revision.id,
            control_variant_id=result.control_variant_id,
            treatment_variant_id=result.treatment_variant_id,
            metric=metric,
            analysis_window=result.analysis_window,
            input_lineage_fingerprint=result.input_lineage_fingerprint,
            control_value=result.control_value,
            treatment_value=result.treatment_value,
            absolute_difference=result.absolute_difference,
            relative_lift=result.relative_lift,
            sample_basis=result.sample_basis,
            statistical_inference=result.statistical_inference.model_dump(mode="json"),
            data_maturity=result.data_maturity.value,
            classification=result.classification.value,
            findings=result.findings,
            evaluated_at=result.evaluated_at,
        )
    )
    await session.flush()
    return result


async def descriptive(session, service, ids, metric="views", delta=0.1):
    snapshot = create_fake_performance_snapshot(UUID(ids["channel"]))
    raw = AnalyticsProviderSnapshot(
        id=snapshot.snapshot_id,
        channel_id=UUID(ids["channel"]),
        platform_account_id=UUID(ids["account"]),
        api_endpoint="fake.analytics",
        request_params={"metric": metric},
        raw_payload=snapshot.metrics.model_dump(mode="json"),
        payload_checksum="b" * 64,
        logical_query_key=uuid4().hex,
        poll_execution_key=uuid4().hex,
        snapshot_dedupe_key=uuid4().hex,
        retrieval_timestamp=snapshot.observed_at,
        http_status=200,
    )
    session.add(raw)
    await session.flush()
    return await service.async_ingest_descriptive_snapshot(
        session, snapshot, metric, delta, UUID(ids["channel"]), ["TITLE"]
    )


async def seed_learning(session, *, observational=False, tradeoff=False, recommend=True):
    # All model/service objects are local to this scope; only primitives escape it.
    ids = await make_context(session)
    repository = LearningRepository()
    service = P25DLearningService(repository)
    policy = LearningPolicy()
    _, policy_revision = await repository.get_or_create_policy(session, policy=policy)
    evidence = []
    if not observational:
        for lift in (0.24, 0.22):
            result = await durable_result(session, ids, lift)
            evidence.append(
                await service.async_ingest_causal_experiment_result(
                    session, result, UUID(ids["channel"]), ["TITLE"]
                )
            )
    evidence.append(
        await descriptive(
            session, service, ids, metric="impressions_ctr" if observational else "views"
        )
    )
    if tradeoff:
        result = await durable_result(session, ids, -0.18, "average_view_duration_seconds")
        evidence.append(
            await service.async_ingest_causal_experiment_result(
                session, result, UUID(ids["channel"]), ["TITLE"]
            )
        )
    hypothesis = LearningHypothesis(
        channel_id=UUID(ids["channel"]),
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Question title improves click through rate",
        content_pillar="SCIENCE",
        content_format="TUTORIAL",
    )
    root, revision = await repository.get_or_create_hypothesis(session, hypothesis)
    insight = await service.evaluate_revision(
        session, revision.id, [e.evidence_id for e in evidence], policy_revision.id
    )
    recommendations = (
        await service.async_generate_recommendations(session, insight.insight_id)
        if recommend
        else []
    )
    ids.update(
        {
            "hypothesis": str(root.id),
            "hypothesis_revision": str(revision.id),
            "policy_revision": str(policy_revision.id),
            "insight": str(insight.insight_id),
            "evaluation": str(insight.evaluation_id),
            "recommendation": str(recommendations[0].recommendation_id)
            if recommendations
            else None,
            "evidence": [str(e.evidence_id) for e in evidence],
            "candidate": str(recommendations[0].candidate_adaptation.adaptation_id)
            if recommendations and recommendations[0].candidate_adaptation
            else None,
        }
    )
    await session.commit()
    return ids


async def counts(session):
    return [
        await session.scalar(select(func.count()).select_from(model))
        for model in (
            LearningEvaluationModel,
            LearningInsightModel,
            LearningRecommendationModel,
            LearningCandidateAdaptationModel,
        )
    ]


async def restart(session):
    engine = session.bind
    session.expunge_all()
    await session.close()
    LearningService.reset_storage()
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_p25d_database_only_restart_recovery(db_session):
    ids = await seed_learning(db_session)
    # JSON string is a primitive snapshot for equality, never an input domain object.
    before = json.dumps(
        await P25DQueryService().async_get_evidence_trace(db_session, UUID(ids["hypothesis"])),
        sort_keys=True,
    )
    initial_counts = await counts(db_session)
    factory = await restart(db_session)
    async with factory() as fresh:
        repository = LearningRepository()
        service = P25DLearningService(repository)
        query = P25DQueryService(repository)
        after = await query.async_get_evidence_trace(fresh, UUID(ids["hypothesis"]))
        assert json.dumps(after, sort_keys=True) == before
        evaluation = after["evaluations"][0]
        assert evaluation["policy_revision_id"] == ids["policy_revision"]
        assert evaluation["hypothesis_revision_id"] == ids["hypothesis_revision"]
        assert evaluation["confidence"] == "HIGH"
        assert {m["role"] for m in evaluation["memberships"]} == {"SUPPORTING", "CONTEXT"}
        assert all(
            m["source_ids"] and m["provenance"]["causal_classification_reason"]
            for m in evaluation["memberships"]
        )
        for membership in evaluation["memberships"]:
            if membership["source_type"] == "P25-C":
                assert membership["attribution_result_id"] in membership["source_ids"]
                assert membership["provenance"]["experiment_revision_id"]
            else:
                assert membership["snapshot_id"] in membership["source_ids"]
        # Only persisted primitive IDs are passed into the restarted evaluation.
        replay = await service.evaluate_revision(
            fresh,
            UUID(ids["hypothesis_revision"]),
            [UUID(e) for e in ids["evidence"]],
            UUID(ids["policy_revision"]),
            datetime.now(UTC) + timedelta(days=30),
        )
        recs = await service.async_generate_recommendations(fresh, replay.insight_id)
        await fresh.commit()
        assert str(replay.evaluation_id) == ids["evaluation"]
        assert str(replay.insight_id) == ids["insight"]
        assert str(recs[0].recommendation_id) == ids["recommendation"]
        assert str(recs[0].candidate_adaptation.adaptation_id) == ids["candidate"]
        assert await counts(fresh) == initial_counts
        assert (
            json.dumps(
                await query.async_get_evidence_trace(fresh, UUID(ids["hypothesis"])), sort_keys=True
            )
            == before
        )


@pytest.mark.asyncio
async def test_historical_recommendation_lineage_and_append(db_session):
    ids = await seed_learning(db_session)
    repository = LearningRepository()
    service = P25DLearningService(repository)
    original = (await repository.get_evidence_trace(db_session, UUID(ids["hypothesis"])))[
        "evaluations"
    ][0]
    result = await durable_result(db_session, ids, -0.9)
    new_evidence = await service.async_ingest_causal_experiment_result(
        db_session, result, UUID(ids["channel"]), ["TITLE"]
    )
    new_ids = [UUID(e) for e in ids["evidence"]] + [new_evidence.evidence_id]
    _, policy = await repository.get_or_create_policy(
        db_session,
        policy=LearningPolicy(policy_version="same-label", min_causal_replications_for_high=3),
    )
    second = await service.evaluate_revision(
        db_session, UUID(ids["hypothesis_revision"]), new_ids, policy.id
    )
    assert str(second.evaluation_id) != ids["evaluation"]
    await service.async_generate_recommendations(db_session, second.insight_id)
    # Delete R1/C1 from this setup is forbidden; generate from old I1 after E2.
    historical = await service.async_generate_recommendations(db_session, UUID(ids["insight"]))
    assert str(historical[0].recommendation_id) == ids["recommendation"]
    await db_session.commit()
    factory = await restart(db_session)
    async with factory() as fresh:
        trace = await P25DQueryService().async_get_evidence_trace(fresh, UUID(ids["hypothesis"]))
        old = next(e for e in trace["evaluations"] if e["evaluation_id"] == ids["evaluation"])
        assert old == original
        assert len(trace["evaluations"]) == 2
        rec = old["insights"][0]["recommendations"][0]
        assert rec["evaluation_id"] == ids["evaluation"]
        assert rec["policy_revision_id"] == ids["policy_revision"]
        assert rec["candidates"][0]["candidate_id"] == ids["candidate"]


@pytest.mark.asyncio
async def test_policy_same_label_content_identity(db_session):
    ids = await seed_learning(db_session)
    repository = LearningRepository()
    _, first = await repository.get_or_create_policy(db_session, policy=LearningPolicy())
    _, repeated = await repository.get_or_create_policy(db_session, policy=LearningPolicy())
    _, changed = await repository.get_or_create_policy(
        db_session, policy=LearningPolicy(min_causal_replications_for_high=3)
    )
    assert first.id == repeated.id == UUID(ids["policy_revision"])
    assert changed.id != first.id
    assert changed.configuration_fingerprint != first.configuration_fingerprint
    service = P25DLearningService(repository)
    second = await service.evaluate_revision(
        db_session, UUID(ids["hypothesis_revision"]), [UUID(e) for e in ids["evidence"]], changed.id
    )
    assert second.confidence.value == "MODERATE"
    assert str(second.evaluation_id) != ids["evaluation"]
    await db_session.commit()
    factory = await restart(db_session)
    async with factory() as fresh:
        trace = await P25DQueryService().async_get_evidence_trace(fresh, UUID(ids["hypothesis"]))
        assert {e["policy_revision_number"] for e in trace["evaluations"]} == {1, 2}
        assert {e["confidence"] for e in trace["evaluations"]} == {"HIGH", "MODERATE"}
        old = next(e for e in trace["evaluations"] if e["evaluation_id"] == ids["evaluation"])
        assert old["policy_revision_id"] == ids["policy_revision"]


@pytest.mark.asyncio
async def test_tradeoff_restart(db_session):
    ids = await seed_learning(db_session, tradeoff=True)
    factory = await restart(db_session)
    async with factory() as fresh:
        trace = await P25DQueryService().async_get_evidence_trace(fresh, UUID(ids["hypothesis"]))
        ev = trace["evaluations"][0]
        assert ev["tradeoffs"] and ev["confidence"] == "LOW"
        assert any(
            m["role"] == "TRADEOFF" and m["metric"] == "average_view_duration_seconds"
            for m in ev["memberships"]
        )
        rec = await P25DQueryService().get_recommendation(fresh, UUID(ids["recommendation"]))
        assert rec.action == RecommendationAction.RUN_FOLLOWUP_EXPERIMENT
        assert rec.candidate_adaptation is None


@pytest.mark.asyncio
async def test_observational_guard_restart(db_session):
    ids = await seed_learning(db_session, observational=True)
    factory = await restart(db_session)
    async with factory() as fresh:
        trace = await P25DQueryService().async_get_evidence_trace(fresh, UUID(ids["hypothesis"]))
        evaluation = trace["evaluations"][0]
        assert evaluation["causal_status"] != "CAUSAL"
        assert all(m["causal_status"] != "CAUSAL" for m in evaluation["memberships"])
        assert evaluation["confidence"] in {"LOW", "VERY_LOW"}
        rec = await P25DQueryService().get_recommendation(fresh, UUID(ids["recommendation"]))
        assert rec.action == RecommendationAction.RUN_FOLLOWUP_EXPERIMENT
        assert rec.candidate_adaptation is None


async def creative_authority_snapshot(session):
    # DNA/Narrative are durable tables; CreativeStyle/Packaging are derived authorities.
    # Include every persistent production request/artifact payload, not just row counts.
    tables = ("channel_dna_revisions", "narrative_plans", "production_requests", "media_artifacts")
    return {
        table: (
            await session.execute(
                text(
                    f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id), '[]'::jsonb) FROM {table} t"
                )
            )
        ).scalar_one()
        for table in tables
    }


@pytest.mark.asyncio
async def test_approval_boundary_and_restart(db_session):
    ids = await seed_learning(db_session)
    before = await creative_authority_snapshot(db_session)
    await P25DLearningService().async_transition_candidate_adaptation(
        db_session,
        UUID(ids["candidate"]),
        AdaptationStatus.APPROVED,
        "producer",
        "Approved proposal only",
    )
    await db_session.commit()
    assert await creative_authority_snapshot(db_session) == before
    factory = await restart(db_session)
    async with factory() as fresh:
        query = P25DQueryService()
        candidate = await query.async_get_candidate_adaptation(fresh, UUID(ids["candidate"]))
        assert candidate.status == AdaptationStatus.APPROVED
        assert candidate.supporting_evidence_ids
        trace = await query.async_get_evidence_trace(fresh, UUID(ids["hypothesis"]))
        history = trace["evaluations"][0]["insights"][0]["recommendations"][0]["candidates"][0][
            "history"
        ]
        assert [(h["from_status"], h["to_status"]) for h in history] == [
            ("NONE", "PROPOSED"),
            ("PROPOSED", "APPROVED"),
        ]
        assert await creative_authority_snapshot(fresh) == before
        with pytest.raises(ValueError, match="Invalid candidate transition"):
            await P25DLearningService().async_transition_candidate_adaptation(
                fresh,
                UUID(ids["candidate"]),
                AdaptationStatus.PROPOSED,
                "producer",
                "Cannot reopen",
            )


@pytest.mark.asyncio
async def test_hypothesis_revision_and_scope_dedup(db_session):
    ids = await seed_learning(db_session)
    repository = LearningRepository()
    hyp = await repository.load_hypothesis_revision(db_session, UUID(ids["hypothesis_revision"]))
    root, same = await repository.get_or_create_hypothesis(
        db_session, hyp.model_copy(update={"hypothesis_id": uuid4()})
    )
    assert str(root.id) == ids["hypothesis"] and str(same.id) == ids["hypothesis_revision"]
    root, new = await repository.get_or_create_hypothesis(
        db_session,
        hyp.model_copy(
            update={"statement": "A new bounded claim about question title performance"}
        ),
    )
    assert new.revision_number == 2 and new.id != same.id
    other, _ = await repository.get_or_create_hypothesis(
        db_session,
        hyp.model_copy(update={"hypothesis_id": uuid4(), "target_scope_id": "other-artifact"}),
    )
    assert other.id != root.id
    service = P25DLearningService(repository)
    revised = await service.evaluate_revision(
        db_session, new.id, [UUID(e) for e in ids["evidence"]], UUID(ids["policy_revision"])
    )
    assert str(revised.evaluation_id) != ids["evaluation"]
    assert (
        await repository.load_insight(db_session, UUID(ids["insight"]))
    ).hypothesis_revision_id == same.id


@pytest.mark.asyncio
async def test_source_reingestion_and_context_guard(db_session):
    ids = await make_context(db_session)
    service = P25DLearningService()
    result = await durable_result(db_session, ids)
    first = await service.async_ingest_causal_experiment_result(
        db_session, result, UUID(ids["channel"]), ["TITLE"]
    )
    second = await service.async_ingest_causal_experiment_result(
        db_session, result, UUID(ids["channel"]), ["TITLE"]
    )
    assert first.evidence_id == second.evidence_id
    assert await db_session.scalar(select(func.count()).select_from(LearningLoopEvidenceModel)) == 1
    with pytest.raises(ValueError, match="source mismatch"):
        await service.async_ingest_causal_experiment_result(
            db_session,
            result.model_copy(update={"relative_lift": 100.0}),
            UUID(ids["channel"]),
            ["TITLE"],
        )
    hyp = LearningHypothesis(
        channel_id=UUID(ids["channel"]),
        creative_dimension="THUMBNAIL",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Thumbnail improves click through rate",
    )
    _, rev = await LearningRepository.get_or_create_hypothesis(db_session, hyp)
    _, pol = await LearningRepository.get_or_create_policy(db_session)
    with pytest.raises(ValueError, match="outside"):
        await service.evaluate_revision(db_session, rev.id, [first.evidence_id], pol.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "classification",
    [
        AttributionClassification.INCONCLUSIVE,
        AttributionClassification.INVALID_EXPERIMENT,
        AttributionClassification.INSUFFICIENT_DATA,
    ],
)
async def test_invalid_or_inconclusive_never_causal(db_session, classification):
    ids = await make_context(db_session)
    result = await durable_result(db_session, ids, classification=classification)
    assert (
        await P25DLearningService().async_ingest_causal_experiment_result(
            db_session, result, UUID(ids["channel"]), ["TITLE"]
        )
        is None
    )
    assert await db_session.scalar(select(func.count()).select_from(LearningLoopEvidenceModel)) == 0


@pytest.mark.asyncio
async def test_database_history_rejects_mutation(db_session):
    ids = await seed_learning(db_session)
    for table, column, value in (
        ("learning_evaluations", "confidence", "LOW"),
        ("learning_policy_revisions", "configuration_fingerprint", "x" * 64),
        ("learning_insights", "summary", "overwritten"),
        ("learning_recommendations", "confidence", "LOW"),
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(f"UPDATE {table} SET {column} = :value"), {"value": value}
                )
    with pytest.raises(DBAPIError, match="append-only"):
        async with db_session.begin_nested():
            await db_session.execute(text("DELETE FROM learning_evaluation_evidence_memberships"))
    assert (
        await LearningRepository.load_insight(db_session, UUID(ids["insight"]))
    ).confidence.value == "HIGH"


@pytest.mark.asyncio
async def test_first_recommendation_for_old_insight_after_new_evaluation(db_session):
    ids = await seed_learning(db_session, recommend=False)
    service = P25DLearningService()
    result = await durable_result(db_session, ids, -0.18, "average_view_duration_seconds")
    tradeoff = await service.async_ingest_causal_experiment_result(
        db_session, result, UUID(ids["channel"]), ["TITLE"]
    )
    _, changed_policy = await LearningRepository.get_or_create_policy(
        db_session, policy=LearningPolicy(min_causal_replications_for_high=3)
    )
    newer = await service.evaluate_revision(
        db_session,
        UUID(ids["hypothesis_revision"]),
        [UUID(e) for e in ids["evidence"]] + [tradeoff.evidence_id],
        changed_policy.id,
    )
    assert newer.confidence.value == "LOW" and newer.tradeoffs
    await service.async_generate_recommendations(db_session, newer.insight_id)
    old_recs = await service.async_generate_recommendations(db_session, UUID(ids["insight"]))
    assert old_recs[0].confidence.value == "HIGH"
    assert old_recs[0].candidate_adaptation is not None
    await db_session.commit()
    factory = await restart(db_session)
    async with factory() as fresh:
        trace = await P25DQueryService().async_get_evidence_trace(fresh, UUID(ids["hypothesis"]))
        old = next(e for e in trace["evaluations"] if e["evaluation_id"] == ids["evaluation"])
        rec = old["insights"][0]["recommendations"][0]
        assert rec["evaluation_id"] == ids["evaluation"]
        assert rec["policy_revision_id"] == ids["policy_revision"]
        assert rec["recommendation"]["confidence"] == "HIGH"
        assert rec["candidates"][0]["status"] == "PROPOSED"


@pytest.mark.asyncio
async def test_database_rejects_false_lineage_and_unaudited_approval(db_session):
    ids = await seed_learning(db_session)
    _, other_policy = await LearningRepository.get_or_create_policy(
        db_session, policy=LearningPolicy(min_causal_replications_for_high=3)
    )
    row = (
        await db_session.execute(
            select(LearningRecommendationModel).where(
                LearningRecommendationModel.id == UUID(ids["recommendation"])
            )
        )
    ).scalar_one()
    with pytest.raises(DBAPIError, match="fk_learning_recommendation_lineage"):
        async with db_session.begin_nested():
            db_session.add(
                LearningRecommendationModel(
                    id=uuid4(),
                    insight_id=row.insight_id,
                    evaluation_id=row.evaluation_id,
                    policy_revision_id=other_policy.id,
                    action=row.action,
                    target_authority=row.target_authority,
                    target_dimension=row.target_dimension,
                    scope_context=row.scope_context,
                    confidence=row.confidence,
                    expected_metric_effect=row.expected_metric_effect,
                    replay_fingerprint="x" * 64,
                    domain_snapshot=row.domain_snapshot,
                )
            )
            await db_session.flush()
    with pytest.raises(DBAPIError, match="approval history"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE learning_candidate_adaptations SET status = 'APPROVED'")
            )
    with pytest.raises(DBAPIError, match="proposal is immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE learning_candidate_adaptations SET proposed_value = '{}'::jsonb")
            )


@pytest.mark.asyncio
async def test_replication_counts_independent_experiments_and_policy_samples(db_session):
    ids = await make_context(db_session)
    service = P25DLearningService()
    result = await durable_result(db_session, ids)
    first = await service.async_ingest_causal_experiment_result(
        db_session, result, UUID(ids["channel"]), ["TITLE"]
    )
    same_experiment = await service.async_ingest_causal_experiment_result(
        db_session, result, UUID(ids["channel"]), ["TITLE", "THUMBNAIL"]
    )
    hypothesis = LearningHypothesis(
        channel_id=UUID(ids["channel"]),
        creative_dimension="TITLE",
        predicted_metric="impressions_ctr",
        predicted_direction="POSITIVE",
        statement="Question title improves click through rate",
    )
    _, revision = await LearningRepository.get_or_create_hypothesis(db_session, hypothesis)
    _, policy = await LearningRepository.get_or_create_policy(db_session)
    evidence_ids = [first.evidence_id, same_experiment.evidence_id]
    evaluation = await service.evaluate_revision(db_session, revision.id, evidence_ids, policy.id)
    assert evaluation.confidence.value == "MODERATE"
    assert evaluation.provenance["causal_replications"] == 1
    _, restrictive = await LearningRepository.get_or_create_policy(
        db_session, policy=LearningPolicy(min_sample_size_per_evidence=10000)
    )
    bounded = await service.evaluate_revision(db_session, revision.id, evidence_ids, restrictive.id)
    assert bounded.confidence.value == "LOW"
    assert bounded.causal_status != CausalityStatus.CAUSAL
