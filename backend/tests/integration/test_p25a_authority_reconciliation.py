"""Integration tests for P25-A Publishing Authority Reconciliation.

Proves:
1. Multi-entrypoint idempotency: API-style, worker-style, and reconciliation invocations
   for the SAME logical PublishIntent converge on ONE provider object (count = 1).
2. Legacy entrypoint (PublishExecutionService.execute_publish) fail-closed behavior:
   blocks execution when CreativeQA is not PASS, artifact is non-current, or RuntimeTruth is missing.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.fake_provider import FakePublishingProvider
from omega.application.publisher.publish_service import (
    PublishExecutionError,
    PublishExecutionService,
)
from omega.application.publisher.publishing_service import PublishingService
from omega.domain.creative_qa import CreativeQAResult, CreativeQAStatus
from omega.domain.production import ProductionQAStatus
from omega.domain.publishing import PublishingState
from omega.infrastructure.models import (
    MediaArtifact,
    ProductionRuntimeTruth,
    PublishIntent,
    Task,
)
from tests.integration.test_p25a_publishing_canary import realistic_publishing_production

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest.mark.asyncio
async def test_multi_entrypoint_idempotency_converges_on_single_provider_object(
    db_session: AsyncSession, realistic_publishing_production
):
    """Proves Section 12: API, Worker, and Reconciliation invocations converge on ONE provider object."""
    p = realistic_publishing_production
    prod_req = p["prod_req"]
    artifact = p["artifact"]
    account = p["account"]
    pkg_plan = p["pkg_plan"]
    cqa_result = p["cqa_result"]
    task = p["task"]

    mission = p["mission"]

    # 1. Prepare intent via canonical PublishingService
    intent, elig = await PublishingService.prepare_publish(
        session=db_session,
        production_request_id=prod_req.id,
        artifact_id=artifact.id,
        platform_account_id=account.id,
        packaging_plan=pkg_plan,
        creative_qa_result=cqa_result,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        task_id=task.id,
        mission_id=mission.id,
        verify_physical_thumbnail=True,
    )
    assert elig.is_eligible is True
    assert intent is not None

    # Single shared fake provider
    provider = FakePublishingProvider()

    # ── ENTRYPOINT 1: API-Style Invocation ──
    receipt1 = await PublishingService.execute_publish(
        session=db_session,
        publish_intent_id=intent.id,
        provider=provider,
        worker_id="api-worker-01",
    )
    assert receipt1.completion_state == PublishingState.PUBLISHED
    assert provider.total_media_count == 1
    media_id_1 = receipt1.external_media_id

    # ── ENTRYPOINT 2: Worker-Style Invocation ──
    # Delegates through PublishExecutionService.execute_publish
    attempt2 = await PublishExecutionService.execute_publish(
        session=db_session,
        task_id=task.id,
        worker_id="celery-worker-02",
        provider=provider,
    )
    assert attempt2 is not None
    # CRITICAL: Media count MUST remain 1 (no duplicate video object!)
    assert provider.total_media_count == 1

    # ── ENTRYPOINT 3: Reconciliation Invocation ──
    receipt3 = await PublishingService.reconcile_publish(
        session=db_session,
        publish_intent_id=intent.id,
        provider=provider,
    )
    assert receipt3 is not None
    assert receipt3.external_media_id == media_id_1
    # CRITICAL: Media count MUST STILL be 1!
    assert provider.total_media_count == 1

    print("\n--- MULTI-ENTRYPOINT IDEMPOTENCY PASS ---")
    print(f"API receipt external ID: {receipt1.external_media_id}")
    print(f"Worker attempt ID: {attempt2.id}")
    print(f"Reconciliation receipt external ID: {receipt3.external_media_id}")
    print(f"Final Fake Provider Media Count: {provider.total_media_count}")
    print("------------------------------------------")


@pytest.mark.asyncio
async def test_legacy_entrypoint_fail_closed_on_creative_qa_fail(
    db_session: AsyncSession, realistic_publishing_production
):
    """Proves Section 13: Legacy entrypoint blocks execution when CreativeQA is FAIL."""
    p = realistic_publishing_production
    prod_req = p["prod_req"]
    artifact = p["artifact"]
    account = p["account"]
    task = p["task"]
    mission = p["mission"]

    # Prepare intent with failing creative QA options
    intent = PublishIntent(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        channel_id=prod_req.channel_id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum=artifact.content_hash,
        revision_number=1,
        title="Unaccepted Creative Title",
        description="Fails QA",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        platform_custom_options={
            "creative_qa_status": "FAIL",
            "creative_qa_accepted": False,
        },
        intent_checksum="c" * 64,
        state="APPROVED",
    )
    db_session.add(intent)
    await db_session.commit()

    with pytest.raises(PublishExecutionError, match="CREATIVE_QA_NOT_PASS"):
        await PublishExecutionService.execute_publish(
            session=db_session,
            task_id=task.id,
            worker_id="legacy-worker",
        )


@pytest.mark.asyncio
async def test_legacy_entrypoint_fail_closed_on_non_current_artifact(
    db_session: AsyncSession, realistic_publishing_production
):
    """Proves Section 13: Legacy entrypoint blocks execution when artifact is non-current / stale."""
    p = realistic_publishing_production
    prod_req = p["prod_req"]
    artifact = p["artifact"]
    account = p["account"]
    task = p["task"]
    mission = p["mission"]

    # Mark artifact non-current
    artifact.is_current = False
    artifact.state = "IS_SUPERSEDED"
    await db_session.commit()

    intent = PublishIntent(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        channel_id=prod_req.channel_id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum=artifact.content_hash,
        revision_number=1,
        title="Stale Artifact Title",
        description="Stale artifact",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        intent_checksum="d" * 64,
        state="APPROVED",
    )
    db_session.add(intent)
    await db_session.commit()

    with pytest.raises(PublishExecutionError, match="STALE_ARTIFACT"):
        await PublishExecutionService.execute_publish(
            session=db_session,
            task_id=task.id,
            worker_id="legacy-worker",
        )


@pytest.mark.asyncio
async def test_legacy_entrypoint_fail_closed_on_missing_runtime_truth(
    db_session: AsyncSession, realistic_publishing_production
):
    """Proves Section 13: Legacy entrypoint blocks execution when RuntimeTruth is missing."""
    p = realistic_publishing_production
    prod_req = p["prod_req"]
    artifact = p["artifact"]
    account = p["account"]
    task = p["task"]
    mission = p["mission"]

    # Delete runtime truth for artifact
    rt = await db_session.get(ProductionRuntimeTruth, artifact.id)
    if rt:
        await db_session.delete(rt)
        await db_session.commit()

    intent = PublishIntent(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        channel_id=prod_req.channel_id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum=artifact.content_hash,
        revision_number=1,
        title="Missing Truth Title",
        description="Missing truth",
        tags=[],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        intent_checksum="e" * 64,
        state="APPROVED",
    )
    db_session.add(intent)
    await db_session.commit()

    with pytest.raises(PublishExecutionError, match="RUNTIME_TRUTH_MISSING"):
        await PublishExecutionService.execute_publish(
            session=db_session,
            task_id=task.id,
            worker_id="legacy-worker",
        )
