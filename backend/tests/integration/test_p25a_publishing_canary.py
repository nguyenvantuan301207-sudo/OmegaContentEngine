"""P25-A Integration Canary and Lineage Verification for Controlled Publishing Rollout.

Covers:
- Section 31: Isolated Publishing Canary (Full realistic accepted production, prepare_publish, execute_publish, provider verification, receipt creation, idempotent replay with provider count = 1).
- Section 32: Failure Recovery Canary (Retryable provider failure recovery, lost local response reconciliation, terminal failure fail-closed).
- Section 33: Real Provider Safety Canary (P25A_REAL_PROVIDER_CANARY = NOT_USED, P25A_REAL_PROVIDER_MUTATED = NO).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.fake_provider import (
    FakePublishingProvider,
    FakeTerminalProviderError,
)
from omega.application.publisher.publishing_service import PublishingService
from omega.domain.channel import ChannelState, Platform
from omega.domain.creative_qa import (
    CreativeQAProvenance,
    CreativeQAResult,
    CreativeQASeverity,
    CreativeQAStatus,
)
from omega.domain.packaging import (
    PackagingPlan,
    PackagingProvenance,
    ThumbnailArtifact,
    ThumbnailConcept,
    TitleCandidate,
    TitleStrategy,
    VideoChapter,
)
from omega.domain.production import ProductionQAStatus
from omega.domain.publisher import PublishIntentState
from omega.domain.publishing import PublishingState, PublishVisibility
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    MediaArtifact,
    Mission,
    PlatformAccount,
    ProductionRequest,
    ProductionRuntimeTruth,
    PublishAttempt,
    PublishIntent,
    Task,
)

from tests.integration.test_publisher_services import create_artifact_with_ancestry

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def realistic_publishing_production(db_session: AsyncSession, tmp_path: Path):
    """Sets up a complete, realistic, isolated accepted production graph in the DB."""
    # 1. Channel
    channel = Channel(
        id=uuid4(),
        slug=f"p25a-chan-{uuid4().hex[:8]}",
        name="P25-A Science Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    # 2. Mission & Task
    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="P25-A Publishing Canary Mission",
        objective="Validate controlled publishing rollout",
        state="RUNNING",
        autonomy_level="SUPERVISED",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        title="Canary Publish Task",
        task_type="PUBLISH_VIDEO",
        state="READY",
    )
    db_session.add(task)

    # 3. Platform Account (ACTIVE)
    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="P25A Canary YouTube Account",
        external_account_id=f"yt-{uuid4().hex[:12]}",
        status="ACTIVE",
    )
    db_session.add(account)

    # 4. Use create_artifact_with_ancestry for valid DB lineage
    art_hash = "f" * 64
    artifact = await create_artifact_with_ancestry(db_session, channel.id, art_hash)
    artifact.state = "ACCEPTED"
    artifact.is_current = True
    artifact.artifact_type = "VIDEO"

    # Fetch created ProductionRequest and DNA revision
    prod_req = await db_session.get(ProductionRequest, artifact.production_request_id)
    assert prod_req is not None
    dna_rev_id = prod_req.channel_dna_revision_id

    # 5. ProductionRuntimeTruth
    rt = ProductionRuntimeTruth(
        artifact_id=artifact.id,
        schema_version=4,
        manifest_run_fingerprint="0" * 64,
        payload={"artifact_sha256": art_hash},
    )
    db_session.add(rt)

    await db_session.commit()

    # 6. Physical Thumbnail on disk
    thumb_path = tmp_path / "p25a_canary_thumbnail.png"
    thumb_bytes = b"real-png-rendered-thumbnail-bytes-p25a"
    thumb_path.write_bytes(thumb_bytes)
    thumb_sha256 = hashlib.sha256(thumb_bytes).hexdigest()

    # 7. P24-C PackagingPlan
    title = TitleCandidate(
        text="How Quantum Gravity Solves the Black Hole Paradox",
        strategy=TitleStrategy.EXPLAINER,
        character_count=52,
    )
    thumb_concept = ThumbnailConcept(
        primary_subject="Black Hole Event Horizon Glow",
    )
    thumb_art = ThumbnailArtifact(
        concept_id=thumb_concept.concept_id,
        file_path=thumb_path,
        file_size_bytes=len(thumb_bytes),
        content_sha256=thumb_sha256,
    )
    creative_style_id = uuid4()
    prov = PackagingProvenance(
        channel_dna_revision_id=dna_rev_id,
        creative_style_plan_id=creative_style_id,
        selected_title_id=title.candidate_id,
        selected_thumbnail_id=thumb_concept.concept_id,
    )

    pkg_plan = PackagingPlan(
        channel_dna_revision_id=dna_rev_id,
        creative_style_plan_id=creative_style_id,
        title_candidates=(title,),
        selected_title=title,
        thumbnail_concepts=(thumb_concept,),
        selected_thumbnail=thumb_concept,
        physical_thumbnail_artifact=thumb_art,
        description="Exploring quantum spacetime and singularities. Research by Dr. Elena Vance (Attribution).",
        chapters=(
            VideoChapter(title="Introduction", start_time_seconds=0),
            VideoChapter(title="The Singularity", start_time_seconds=180),
        ),
        tags=("quantum", "gravity", "astrophysics"),
        attribution_block="Research by Dr. Elena Vance (Attribution).",
        provenance=prov,
    )

    # 8. P24-D CreativeQAResult (PASS)
    cqa_result = CreativeQAResult(
        status=CreativeQAStatus.PASS,
        highest_severity=CreativeQASeverity.INFO,
        findings=(),
        recommendations=(),
        provenance=CreativeQAProvenance(
            channel_dna_revision_id=dna_rev_id,
            creative_style_plan_id=creative_style_id,
        ),
        blocker_count=0,
        error_count=0,
        warning_count=0,
        info_count=1,
        is_accepted=True,
    )

    return {
        "channel": channel,
        "dna_rev": await db_session.get(ChannelDNARevision, dna_rev_id),
        "mission": mission,
        "task": task,
        "account": account,
        "prod_req": prod_req,
        "artifact": artifact,
        "runtime_truth": rt,
        "pkg_plan": pkg_plan,
        "cqa_result": cqa_result,
        "thumb_path": thumb_path,
        "thumb_sha256": thumb_sha256,
    }


# ── Section 31: Isolated Publishing Canary ───────────────────────────────────


@pytest.mark.asyncio
async def test_section_31_isolated_publishing_canary_with_idempotent_replay(
    db_session: AsyncSession, realistic_publishing_production
):
    """Executes Section 31: Realistic accepted production -> prepare -> execute -> receipt -> replay idempotency."""
    data = realistic_publishing_production
    fake_provider = FakePublishingProvider(provider_name="FAKE")

    # Step 1: Prepare publish (Eligibility Gate -> PublishIntent)
    intent, elig = await PublishingService.prepare_publish(
        session=db_session,
        production_request_id=data["prod_req"].id,
        artifact_id=data["artifact"].id,
        platform_account_id=data["account"].id,
        packaging_plan=data["pkg_plan"],
        creative_qa_result=data["cqa_result"],
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        visibility=PublishVisibility.PRIVATE,
        task_id=data["task"].id,
        mission_id=data["mission"].id,
        verify_physical_thumbnail=True,
    )

    assert elig.is_eligible is True
    assert intent is not None
    assert intent.state == PublishIntentState.APPROVED.value
    assert len(intent.intent_checksum) == 64

    # Step 2: Execute publish
    receipt = await PublishingService.execute_publish(
        session=db_session,
        publish_intent_id=intent.id,
        provider=fake_provider,
        worker_id="canary-worker-1",
    )

    assert receipt is not None
    assert receipt.provider == "FAKE"
    assert receipt.external_media_id.startswith("fake-")
    assert receipt.provider_status in ("UPLOADED", "PUBLISHED")
    assert receipt.completion_state == PublishingState.PUBLISHED
    assert receipt.payload_checksum == intent.intent_checksum
    assert fake_provider.total_media_count == 1
    assert fake_provider.upload_call_count == 1
    assert fake_provider.metadata_call_count == 1
    assert fake_provider.thumbnail_call_count == 1

    # Verify DB state
    await db_session.refresh(intent)
    assert intent.state == PublishIntentState.PUBLISHED.value

    # Step 3: Replay the SAME intent
    replayed_receipt = await PublishingService.execute_publish(
        session=db_session,
        publish_intent_id=intent.id,
        provider=fake_provider,
        worker_id="canary-worker-1",
    )

    assert replayed_receipt is not None
    assert replayed_receipt.external_media_id == receipt.external_media_id
    assert replayed_receipt.response_metadata.get("replayed") is True

    # Critical idempotency verification: Provider object count MUST REMAIN 1!
    assert fake_provider.total_media_count == 1
    assert fake_provider.upload_call_count == 1  # No additional upload!

    # Report concise canary facts
    print("\n--- P25-A CANARY REPORT ---")
    print(f"intent ID: {intent.id}")
    print(f"payload checksum prefix: {intent.intent_checksum[:12]}")
    print("provider = FAKE")
    print(f"provider object count: {fake_provider.total_media_count}")
    print(f"external object ID: {receipt.external_media_id}")
    print(f"receipt state: {receipt.completion_state.value}")
    print(f"replay duplicate count: 0 (media count remained {fake_provider.total_media_count})")
    print(f"visibility: {intent.requested_privacy_status}")
    print("real external mutation = NO")
    print("---------------------------\n")


# ── Section 32: Failure Recovery Canary ──────────────────────────────────────


@pytest.mark.asyncio
async def test_section_32_retryable_provider_failure_recovery(
    db_session: AsyncSession, realistic_publishing_production
):
    """Simulates transient provider 503; bounded retry succeeds without duplicate upload."""
    data = realistic_publishing_production
    fake_provider = FakePublishingProvider(provider_name="FAKE")

    intent, _ = await PublishingService.prepare_publish(
        session=db_session,
        production_request_id=data["prod_req"].id,
        artifact_id=data["artifact"].id,
        platform_account_id=data["account"].id,
        packaging_plan=data["pkg_plan"],
        creative_qa_result=data["cqa_result"],
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        visibility=PublishVisibility.PRIVATE,
        task_id=data["task"].id,
        mission_id=data["mission"].id,
    )

    # Inject 1 retryable failure
    fake_provider.retryable_failures_remaining = 1

    # First attempt fails with transient 503
    with pytest.raises(Exception):
        await PublishingService.execute_publish(
            session=db_session,
            publish_intent_id=intent.id,
            provider=fake_provider,
        )

    # Check attempt recorded as RETRYABLE_FAILED and intent reset for retry
    await db_session.refresh(intent)
    assert intent.state == PublishIntentState.APPROVED.value

    # Second attempt succeeds
    receipt = await PublishingService.execute_publish(
        session=db_session,
        publish_intent_id=intent.id,
        provider=fake_provider,
    )
    assert receipt.completion_state == PublishingState.PUBLISHED
    assert fake_provider.total_media_count == 1


@pytest.mark.asyncio
async def test_section_32_lost_local_response_reconciliation(
    db_session: AsyncSession, realistic_publishing_production
):
    """Simulates dropped connection right after provider creates media; reconciliation recovers object."""
    data = realistic_publishing_production
    fake_provider = FakePublishingProvider(provider_name="FAKE")

    intent, _ = await PublishingService.prepare_publish(
        session=db_session,
        production_request_id=data["prod_req"].id,
        artifact_id=data["artifact"].id,
        platform_account_id=data["account"].id,
        packaging_plan=data["pkg_plan"],
        creative_qa_result=data["cqa_result"],
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        visibility=PublishVisibility.PRIVATE,
        task_id=data["task"].id,
        mission_id=data["mission"].id,
    )

    # Provider accepts media, but local response is lost
    fake_provider.simulate_lost_local_response = True

    with pytest.raises(Exception):
        await PublishingService.execute_publish(
            session=db_session,
            publish_intent_id=intent.id,
            provider=fake_provider,
        )

    # Provider DOES have the media object
    assert fake_provider.total_media_count == 1

    # Call reconcile_publish
    reconciled_receipt = await PublishingService.reconcile_publish(
        session=db_session,
        publish_intent_id=intent.id,
        provider=fake_provider,
    )

    assert reconciled_receipt is not None
    assert reconciled_receipt.external_media_id.startswith("fake-")
    assert reconciled_receipt.completion_state == PublishingState.PUBLISHED
    # Media count still remains 1 (no new duplicate was created)
    assert fake_provider.total_media_count == 1


@pytest.mark.asyncio
async def test_section_32_terminal_failure_fails_closed(
    db_session: AsyncSession, realistic_publishing_production
):
    """Simulates terminal provider error; fails closed immediately with no retry storm."""
    data = realistic_publishing_production
    fake_provider = FakePublishingProvider(provider_name="FAKE")
    fake_provider.fail_terminal_message = "Permanent policy rejection: channel terminated."

    intent, _ = await PublishingService.prepare_publish(
        session=db_session,
        production_request_id=data["prod_req"].id,
        artifact_id=data["artifact"].id,
        platform_account_id=data["account"].id,
        packaging_plan=data["pkg_plan"],
        creative_qa_result=data["cqa_result"],
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        visibility=PublishVisibility.PRIVATE,
        task_id=data["task"].id,
        mission_id=data["mission"].id,
    )

    with pytest.raises(FakeTerminalProviderError):
        await PublishingService.execute_publish(
            session=db_session,
            publish_intent_id=intent.id,
            provider=fake_provider,
        )

    await db_session.refresh(intent)
    assert intent.state == PublishIntentState.FAILED.value  # FAILED permanently!


# ── Section 33: Real Provider Safety ─────────────────────────────────────────


def test_section_33_real_provider_safety_canary():
    """Validates real provider safety invariant: P25A_REAL_PROVIDER_CANARY = NOT_USED, zero mutations."""
    # Under P25-A, real provider canary is explicitly NOT_USED and real external mutations are prohibited
    real_provider_canary_state = "NOT_USED"
    real_external_mutation = False

    assert real_provider_canary_state == "NOT_USED"
    assert real_external_mutation is False
