"""P25-C Integration Persistence & Durability Test Suite.

Verifies:
- Section 18: Restart Durability Test
- Section 19: Historical Immutability Test
- Section 20: Versioning Test
- Section 21: Overlap Restart Test
- Section 22: Exposure Privacy Test
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omega.application.experiments.attribution_service import AttributionService
from omega.application.experiments.experiment_repository import ExperimentRepository
from omega.application.experiments.fake_experiment_provider import FakeExperimentProvider
from omega.domain.channel import ChannelState, Platform
from omega.domain.creative_qa import CreativeQAStatus
from omega.domain.experimentation import (
    AttributionClassification,
    ChangeDimension,
    DataMaturityState,
    ExperimentStatus,
    ExperimentType,
    ExperimentUnit,
    ExperimentVariant,
    VariantRole,
)
from omega.infrastructure.models import (
    AnalyticsProviderSnapshot,
    Channel,
    ExperimentAttributionResultModel,
    ExperimentExposureModel,
    ExperimentRevision,
    ExperimentRoot,
    ExperimentVariantModel,
    MediaArtifact,
    PlatformAccount,
)
from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest.fixture(autouse=True)
def clean_attribution_storage():
    """Ensure in-memory attribution cache is wiped clean before and after each test."""
    AttributionService.reset_storage()
    yield
    AttributionService.reset_storage()


@pytest_asyncio.fixture
async def experiment_fixtures(db_session: AsyncSession):
    """Sets up minimal durable Channel, PlatformAccount, and MediaArtifacts for experiments."""
    channel = Channel(
        id=uuid4(),
        slug=f"p25c-chan-{uuid4().hex[:8]}",
        name="P25-C Persistence Test Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="P25-C Test Account",
        external_account_id=f"yt-{uuid4().hex[:12]}",
        status="ACTIVE",
    )
    db_session.add(account)

    art_hash_a = "a" * 64
    art_hash_b = "b" * 64
    artifact_a = await create_artifact_with_ancestry(db_session, channel.id, art_hash_a)
    artifact_a.is_current = True
    artifact_a.artifact_type = "VIDEO"

    artifact_b = MediaArtifact(
        id=uuid4(),
        production_request_id=artifact_a.production_request_id,
        artifact_type="VIDEO",
        version=2,
        is_current=False,
        storage_uri="videos/test_render_b.mp4",
        file_size_bytes=2048,
        content_hash=art_hash_b,
        mime_type="video/mp4",
    )
    db_session.add(artifact_b)

    now_utc = datetime.now(UTC)
    raw_snapshot = AnalyticsProviderSnapshot(
        id=uuid4(),
        channel_id=channel.id,
        platform_account_id=account.id,
        api_endpoint="youtube.analytics.reports.query",
        request_params={"metrics": "views,estimatedMinutesWatched"},
        raw_payload={"rows": [[100, 4000]]},
        payload_checksum="c" * 64,
        logical_query_key="query-key-001",
        poll_execution_key=f"poll-{uuid4().hex[:12]}",
        snapshot_dedupe_key=f"dedupe-{uuid4().hex[:16]}",
        retrieval_timestamp=now_utc,
        http_status=200,
    )
    db_session.add(raw_snapshot)

    await db_session.commit()

    return {
        "channel": channel,
        "account": account,
        "artifact_a": artifact_a,
        "artifact_b": artifact_b,
        "raw_snapshot": raw_snapshot,
    }


@pytest.mark.asyncio
async def test_restart_durability(db_session: AsyncSession, experiment_fixtures: dict):
    """Section 18: Restart Durability Test.

    1. create experiment
    2. persist variants
    3. start experiment
    4. record exposure / aggregate truth
    5. analyze
    6. persist attribution result
    7. destroy service + repository objects
    8. close DB session
    9. construct fresh session/repository/service
    10. reload
    11. re-analyze identical inputs
    Verify:
    - same experiment lineage
    - same variant snapshots
    - same status
    - same attribution identity
    - zero duplicate result rows
    """
    channel = experiment_fixtures["channel"]
    artifact_a = experiment_fixtures["artifact_a"]
    raw_snapshot = experiment_fixtures["raw_snapshot"]

    control_var = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Declarative Video Title",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
        provenance={"p24_gate": "PASSED"},
    )

    treatment_var = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Question Video Title?",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
        provenance={"p24_gate": "PASSED"},
    )

    # 1 & 2. Create experiment and persist variants via service with DB session
    exp_def, bound_variants = await AttributionService.create_experiment(
        channel_id=channel.id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Question title outperforms declarative title",
        primary_metric="impressions_ctr",
        secondary_metrics=["average_view_duration_seconds", "views"],
        control_variant=control_var,
        treatment_variants=[treatment_var],
        experiment_unit=ExperimentUnit.IMPRESSION,
        minimum_sample_size=1000,
        analysis_window_hours=24,
        target_scope_type="MEDIA_ARTIFACT",
        target_scope_id=str(artifact_a.id),
        session=db_session,
    )
    bound_ctrl = bound_variants[0]
    bound_trt = bound_variants[1]

    # Validate experiment
    validated_exp = await AttributionService.validate_experiment(
        exp_def.experiment_id, session=db_session
    )
    assert validated_exp.status == ExperimentStatus.READY

    # 3. Start experiment -> RUNNING
    started_exp = await AttributionService.start_experiment(
        exp_def.experiment_id, session=db_session
    )
    assert started_exp.status == ExperimentStatus.RUNNING

    # 4. Record exposure / aggregate truth
    exposure = await AttributionService.record_exposure(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_trt.variant_id,
        subject_id="agg_window_bucket_001",
        sample_count=1500,
        exposure_mode="AGGREGATE",
        session=db_session,
    )
    assert exposure.aggregate_sample_count == 1500

    # 5. Configure deterministic fake experiment provider
    fake_provider = FakeExperimentProvider(provider_name="FAKE")
    fake_provider.configure_freshness(exp_def.experiment_id, "FRESH")
    fake_provider.configure_variant_metrics(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_ctrl.variant_id,
        metrics={"impressions": 10000, "clicks": 500, "impressions_ctr": 0.05, "views": 490},
        sample_count=10000,
    )
    fake_provider.configure_variant_metrics(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_trt.variant_id,
        metrics={"impressions": 10000, "clicks": 620, "impressions_ctr": 0.062, "views": 610},
        sample_count=10000,
    )

    as_of_time = datetime.now(UTC) + timedelta(hours=24)
    analysis_input_list = [
        {"snapshot_id": str(raw_snapshot.id), "endpoint": "youtube.analytics.reports.query"}
    ]

    # 6. Analyze and persist attribution result
    result_1 = await AttributionService.analyze_experiment(
        experiment_id=exp_def.experiment_id,
        provider=fake_provider,
        as_of=as_of_time,
        analysis_inputs=analysis_input_list,
        session=db_session,
    )
    assert result_1.classification == AttributionClassification.TREATMENT_BETTER
    assert result_1.control_value == 0.05
    assert result_1.treatment_value == 0.062

    # Commit db_session so all rows are durably written
    await db_session.commit()

    # 7. Destroy service cache & close DB session
    AttributionService.reset_storage()
    session_maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await db_session.close()

    # 8 & 9. Construct fresh session/repository/service
    async with session_maker() as fresh_session:
        # 10. Reload from database
        loaded_root = await ExperimentRepository.get_root(fresh_session, exp_def.experiment_id)
        assert loaded_root is not None
        assert loaded_root.id == exp_def.experiment_id
        assert loaded_root.status == "RUNNING"
        assert loaded_root.target_scope_type == "MEDIA_ARTIFACT"
        assert loaded_root.target_scope_id == str(artifact_a.id)

        v_models = await ExperimentRepository.get_variants_for_revision(
            fresh_session, loaded_root.current_revision_id
        )
        assert len(v_models) == 2
        ctrl_loaded = next(v for v in v_models if v.role == "CONTROL")
        treat_loaded = next(v for v in v_models if v.role == "TREATMENT")

        assert ctrl_loaded.title == "Declarative Video Title"
        assert treat_loaded.title == "Question Video Title?"
        assert ctrl_loaded.variant_snapshot_hash == bound_ctrl.get_snapshot_hash()

        # 11. Re-analyze identical inputs with fresh session
        result_2 = await AttributionService.analyze_experiment(
            experiment_id=exp_def.experiment_id,
            provider=fake_provider,
            as_of=as_of_time,
            analysis_inputs=analysis_input_list,
            session=fresh_session,
        )

        # Verify:
        # - same attribution identity
        assert result_2.result_id == result_1.result_id
        assert result_2.control_value == result_1.control_value
        assert result_2.treatment_value == result_1.treatment_value
        assert result_2.relative_lift == result_1.relative_lift

        # - zero duplicate result rows in DB
        stmt = select(func.count(ExperimentAttributionResultModel.id)).where(
            ExperimentAttributionResultModel.experiment_revision_id == loaded_root.current_revision_id
        )
        count = (await fresh_session.execute(stmt)).scalar_one()
        assert count == 1, f"Expected exactly 1 attribution result row, found {count}"


@pytest.mark.asyncio
async def test_historical_immutability(db_session: AsyncSession, experiment_fixtures: dict):
    """Section 19: Historical Immutability Test.

    After experiment completion:
    - create newer packaging/title/thumbnail revisions.
    - load the completed experiment.
    - verify historical variant content, artifact identity, P24 acceptance evidence,
      input snapshots, and AttributionResult are unchanged.
    """
    channel = experiment_fixtures["channel"]
    artifact_a = experiment_fixtures["artifact_a"]
    raw_snapshot = experiment_fixtures["raw_snapshot"]

    c_var = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Historical Title 19A",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
        provenance={"signature": "p24-evidence-19a"},
    )
    t_var = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Historical Title 19B",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
        provenance={"signature": "p24-evidence-19b"},
    )

    exp_def, bound_variants = await AttributionService.create_experiment(
        channel_id=channel.id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Curiosity title lift",
        primary_metric="impressions_ctr",
        control_variant=c_var,
        treatment_variants=[t_var],
        analysis_window_hours=24,
        target_scope_type="MEDIA_ARTIFACT",
        target_scope_id=str(artifact_a.id),
        session=db_session,
    )
    await AttributionService.validate_experiment(exp_def.experiment_id, session=db_session)
    await AttributionService.start_experiment(exp_def.experiment_id, session=db_session)

    fake_provider = FakeExperimentProvider(provider_name="FAKE")
    fake_provider.configure_freshness(exp_def.experiment_id, "FRESH")
    fake_provider.configure_variant_metrics(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_variants[0].variant_id,
        metrics={"impressions": 10000, "clicks": 500, "impressions_ctr": 0.05},
        sample_count=10000,
    )
    fake_provider.configure_variant_metrics(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_variants[1].variant_id,
        metrics={"impressions": 10000, "clicks": 650, "impressions_ctr": 0.065},
        sample_count=10000,
    )

    as_of = datetime.now(UTC) + timedelta(hours=24)
    analysis_inputs = [{"snapshot_id": str(raw_snapshot.id)}]

    original_result = await AttributionService.analyze_experiment(
        experiment_id=exp_def.experiment_id,
        provider=fake_provider,
        as_of=as_of,
        analysis_inputs=analysis_inputs,
        session=db_session,
    )
    completed_exp = await AttributionService.complete_experiment(
        exp_def.experiment_id, session=db_session
    )
    assert completed_exp.status == ExperimentStatus.COMPLETED
    await db_session.commit()

    # Now simulate newer packaging/title modifications outside this experiment
    artifact_a.storage_uri = "videos/mutated_path.mp4"
    await db_session.commit()

    # Clear cache and reload from fresh session
    AttributionService.reset_storage()
    session_maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with session_maker() as fresh_session:
        loaded_root = await ExperimentRepository.get_root(fresh_session, exp_def.experiment_id)
        assert loaded_root.status == "COMPLETED"

        v_models = await ExperimentRepository.get_variants_for_revision(
            fresh_session, loaded_root.current_revision_id
        )
        ctrl_db = next(v for v in v_models if v.role == "CONTROL")
        trt_db = next(v for v in v_models if v.role == "TREATMENT")

        # Historical variant content, hashes, and P24 evidence remain pinned
        assert ctrl_db.title == "Historical Title 19A"
        assert ctrl_db.provenance == {"signature": "p24-evidence-19a"}
        assert ctrl_db.variant_snapshot_hash == bound_variants[0].get_snapshot_hash()

        assert trt_db.title == "Historical Title 19B"
        assert trt_db.provenance == {"signature": "p24-evidence-19b"}
        assert trt_db.variant_snapshot_hash == bound_variants[1].get_snapshot_hash()

        # Historical AttributionResult remains immutable
        persisted_res = await ExperimentRepository.get_attribution_result(
            fresh_session, original_result.result_id
        )
        assert persisted_res is not None
        assert persisted_res.id == original_result.result_id
        assert persisted_res.relative_lift == original_result.relative_lift
        assert persisted_res.classification == original_result.classification.value


@pytest.mark.asyncio
async def test_experiment_versioning(db_session: AsyncSession, experiment_fixtures: dict):
    """Section 20: Versioning Test.

    Attempt to materially change primary metric / hypothesis / window after READY/RUNNING.
    Verify:
    - New revision created (revision_number = 2, supersedes_revision_id = rev1).
    - Original revision (rev1) remains queryable and intact in DB.
    """
    channel = experiment_fixtures["channel"]
    artifact_a = experiment_fixtures["artifact_a"]

    c_var = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Title A",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
    )
    t_var = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Title B",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
    )

    exp_def, bound_variants = await AttributionService.create_experiment(
        channel_id=channel.id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Initial hypothesis v1",
        primary_metric="impressions_ctr",
        control_variant=c_var,
        treatment_variants=[t_var],
        analysis_window_hours=24,
        target_scope_type="MEDIA_ARTIFACT",
        target_scope_id=str(artifact_a.id),
        session=db_session,
    )
    await AttributionService.validate_experiment(exp_def.experiment_id, session=db_session)
    await AttributionService.start_experiment(exp_def.experiment_id, session=db_session)
    await db_session.commit()

    # Now create revision 2 with changed metric and hypothesis
    rev2, v2_models = await ExperimentRepository.create_revision(
        session=db_session,
        root_id=exp_def.experiment_id,
        experiment_type="TITLE",
        hypothesis="Updated hypothesis v2",
        primary_metric="average_view_duration_seconds",
        secondary_metrics=["views"],
        assignment_policy={"randomization_seed": "seed2", "control_allocation_ratio": 0.5},
        experiment_unit="IMPRESSION",
        minimum_sample_size=2000,
        analysis_window_hours=48,
        dimensions=["TITLE"],
        variants_data=[
            {
                "variant_id": uuid4(),
                "role": "CONTROL",
                "change_dimension": "TITLE",
                "media_artifact_id": artifact_a.id,
                "title": "Title A",
                "is_accepted_p24": True,
                "creative_qa_status": "PASS",
            },
            {
                "variant_id": uuid4(),
                "role": "TREATMENT",
                "change_dimension": "TITLE",
                "media_artifact_id": artifact_a.id,
                "title": "Title B2",
                "is_accepted_p24": True,
                "creative_qa_status": "PASS",
            },
        ],
    )
    await db_session.commit()

    # Verify both revisions exist and are queryable
    root = await ExperimentRepository.get_root(db_session, exp_def.experiment_id)
    rev1_db = await ExperimentRepository.get_revision(db_session, rev2.supersedes_revision_id)
    rev2_db = await ExperimentRepository.get_revision(db_session, rev2.id)

    assert rev1_db is not None
    assert rev1_db.revision_number == 1
    assert rev1_db.hypothesis == "Initial hypothesis v1"
    assert rev1_db.primary_metric == "impressions_ctr"

    assert rev2_db is not None
    assert rev2_db.revision_number == 2
    assert rev2_db.supersedes_revision_id == rev1_db.id
    assert rev2_db.hypothesis == "Updated hypothesis v2"
    assert rev2_db.primary_metric == "average_view_duration_seconds"


@pytest.mark.asyncio
async def test_overlap_restart_guard(db_session: AsyncSession, experiment_fixtures: dict):
    """Section 21: Overlap Restart Test.

    1. Create RUNNING experiment on target scope X with dimension TITLE.
    2. Destroy application state, fresh session.
    3. Attempt conflicting experiment on same target scope X and same changed dimension TITLE.
       Expected: blocked from durable DB state.
    4. Then attempt experiment on different independent target scope Y:
       Expected: allowed.
    """
    channel = experiment_fixtures["channel"]
    artifact_a = experiment_fixtures["artifact_a"]
    artifact_b = experiment_fixtures["artifact_b"]

    c1 = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Scope X Control",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
    )
    t1 = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Scope X Treatment",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
    )

    exp_1, _ = await AttributionService.create_experiment(
        channel_id=channel.id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Scope X test 1",
        primary_metric="impressions_ctr",
        control_variant=c1,
        treatment_variants=[t1],
        target_scope_type="MEDIA_ARTIFACT",
        target_scope_id=str(artifact_a.id),
        session=db_session,
    )
    await AttributionService.validate_experiment(exp_1.experiment_id, session=db_session)
    await AttributionService.start_experiment(exp_1.experiment_id, session=db_session)
    await db_session.commit()

    # Destroy state and open fresh session
    AttributionService.reset_storage()
    session_maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await db_session.close()

    async with session_maker() as fresh_session:
        # Attempt conflicting experiment on same target scope and dimension (TITLE_AND_THUMBNAIL conflicts on TITLE)
        c_conf = ExperimentVariant(
            variant_id=uuid4(),
            experiment_id=uuid4(),
            role=VariantRole.CONTROL,
            change_dimension=ChangeDimension.TITLE_AND_THUMBNAIL,
            media_artifact_id=artifact_a.id,
            title="Scope X Conflict Control",
            creative_qa_status=CreativeQAStatus.PASS,
            is_accepted_p24=True,
        )
        t_conf = ExperimentVariant(
            variant_id=uuid4(),
            experiment_id=uuid4(),
            role=VariantRole.TREATMENT,
            change_dimension=ChangeDimension.TITLE_AND_THUMBNAIL,
            media_artifact_id=artifact_a.id,
            title="Scope X Conflict Treatment",
            creative_qa_status=CreativeQAStatus.PASS,
            is_accepted_p24=True,
        )

        with pytest.raises(ValueError, match="Overlapping experiment detected"):
            await AttributionService.create_experiment(
                channel_id=channel.id,
                experiment_type=ExperimentType.TITLE_AND_THUMBNAIL,
                hypothesis="Scope X conflict test",
                primary_metric="impressions_ctr",
                control_variant=c_conf,
                treatment_variants=[t_conf],
                target_scope_type="MEDIA_ARTIFACT",
                target_scope_id=str(artifact_a.id),  # Same scope!
                session=fresh_session,
            )

        # Attempt experiment on different independent target scope Y
        c_ind = ExperimentVariant(
            variant_id=uuid4(),
            experiment_id=uuid4(),
            role=VariantRole.CONTROL,
            change_dimension=ChangeDimension.TITLE,
            media_artifact_id=artifact_b.id,
            title="Scope Y Control",
            creative_qa_status=CreativeQAStatus.PASS,
            is_accepted_p24=True,
        )
        t_ind = ExperimentVariant(
            variant_id=uuid4(),
            experiment_id=uuid4(),
            role=VariantRole.TREATMENT,
            change_dimension=ChangeDimension.TITLE,
            media_artifact_id=artifact_b.id,
            title="Scope Y Treatment",
            creative_qa_status=CreativeQAStatus.PASS,
            is_accepted_p24=True,
        )

        created_independent, _ = await AttributionService.create_experiment(
            channel_id=channel.id,
            experiment_type=ExperimentType.TITLE,
            hypothesis="Scope Y independent test",
            primary_metric="impressions_ctr",
            control_variant=c_ind,
            treatment_variants=[t_ind],
            target_scope_type="MEDIA_ARTIFACT",
            target_scope_id=str(artifact_b.id),  # Different scope!
            session=fresh_session,
        )
        assert created_independent.target_scope_id == str(artifact_b.id)
        await fresh_session.commit()


@pytest.mark.asyncio
async def test_exposure_privacy_modes(db_session: AsyncSession, experiment_fixtures: dict):
    """Section 22: Exposure Privacy Test.

    Verify:
    - Aggregate/provider-native experiments do not require invented subjects (subject_key is None or aggregate).
    - Individual exposure identity is pseudonymous without raw PII.
    """
    channel = experiment_fixtures["channel"]
    artifact_a = experiment_fixtures["artifact_a"]

    c_var = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.CONTROL,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Title A",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
    )
    t_var = ExperimentVariant(
        variant_id=uuid4(),
        experiment_id=uuid4(),
        role=VariantRole.TREATMENT,
        change_dimension=ChangeDimension.TITLE,
        media_artifact_id=artifact_a.id,
        title="Title B",
        creative_qa_status=CreativeQAStatus.PASS,
        is_accepted_p24=True,
    )

    exp_def, bound_variants = await AttributionService.create_experiment(
        channel_id=channel.id,
        experiment_type=ExperimentType.TITLE,
        hypothesis="Privacy test",
        primary_metric="impressions_ctr",
        control_variant=c_var,
        treatment_variants=[t_var],
        target_scope_type="MEDIA_ARTIFACT",
        target_scope_id=str(artifact_a.id),
        session=db_session,
    )
    await AttributionService.validate_experiment(exp_def.experiment_id, session=db_session)
    await AttributionService.start_experiment(exp_def.experiment_id, session=db_session)

    # 1. Aggregate exposure: no invented viewer subject
    agg_exposure = await AttributionService.record_exposure(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_variants[1].variant_id,
        subject_id="aggregate_bucket_daily",
        sample_count=5000,
        exposure_mode="AGGREGATE",
        subject_key=None,
        session=db_session,
    )
    assert agg_exposure.exposure_mode == "AGGREGATE"
    assert agg_exposure.aggregate_sample_count == 5000
    assert agg_exposure.subject_key is None

    # 2. Individual exposure: pseudonymous subject key (SHA-256 hash), no PII
    pseudonymous_key = hashlib.sha256(b"viewer_secure_salt_user_98765").hexdigest()
    ind_exposure = await AttributionService.record_exposure(
        experiment_id=exp_def.experiment_id,
        variant_id=bound_variants[1].variant_id,
        subject_id=pseudonymous_key,
        sample_count=1,
        exposure_mode="INDIVIDUAL",
        subject_key=pseudonymous_key,
        session=db_session,
    )
    assert ind_exposure.exposure_mode == "INDIVIDUAL"
    assert ind_exposure.subject_key == pseudonymous_key
    assert "@" not in ind_exposure.subject_key  # No raw email/PII

    await db_session.commit()

    # Verify rows in DB
    root = await ExperimentRepository.get_root(db_session, exp_def.experiment_id)
    exp_models = await ExperimentRepository.get_exposures_for_revision(
        db_session, root.current_revision_id
    )
    assert len(exp_models) == 2
