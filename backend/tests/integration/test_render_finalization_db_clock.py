"""PostgreSQL regression coverage for long render finalization timestamps."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.render_service import _db_wall_clock
from omega.domain.production import (
    MediaArtifactType,
    ProductionOutcome,
    ProductionRequestStatus,
    RenderJobState,
)
from omega.infrastructure.models import (
    MediaArtifact,
    ProductionRenderJob,
    ProductionRequest,
    ProductionRuntimeTruth,
)
from tests.integration.test_dispatch_stall_recovery import _create_test_lineage


@pytest.mark.asyncio
async def test_render_finalization_uses_wall_clock_after_transaction_delay(
    db_session: AsyncSession,
) -> None:
    _, request, job, _ = await _create_test_lineage(db_session)

    transaction_started_at = await db_session.scalar(select(func.now()))
    wall_clock_before_delay = await db_session.scalar(select(func.clock_timestamp()))

    await db_session.execute(select(func.pg_sleep(1)))
    completion_db_time = await _db_wall_clock(db_session)

    artifact = MediaArtifact(
        production_request_id=request.id,
        render_job_id=job.id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        storage_uri="artifacts/or2-a1-regression.mp4",
        content_hash="a" * 64,
        file_size_bytes=1,
        mime_type="video/mp4",
        width=1920,
        height=1080,
        duration_ms=1000,
        created_at=completion_db_time,
    )
    db_session.add(artifact)
    await db_session.flush()
    runtime_truth = ProductionRuntimeTruth(
        artifact_id=artifact.id,
        schema_version=4,
        manifest_run_fingerprint="b" * 64,
        payload={"render_semantics_version": 6},
        created_at=completion_db_time,
    )
    db_session.add(runtime_truth)
    await db_session.execute(
        update(ProductionRequest)
        .where(ProductionRequest.id == request.id)
        .values(
            status=ProductionRequestStatus.SUCCEEDED.value,
            outcome=ProductionOutcome.RENDERED.value,
            completed_at=completion_db_time,
        )
    )
    await db_session.execute(
        update(ProductionRenderJob)
        .where(ProductionRenderJob.id == job.id)
        .values(
            state=RenderJobState.SUCCEEDED.value,
            completed_at=completion_db_time,
        )
    )
    await db_session.flush()
    await db_session.refresh(request)
    await db_session.refresh(job)
    await db_session.refresh(artifact)
    await db_session.refresh(runtime_truth)

    assert isinstance(transaction_started_at, datetime)
    assert isinstance(wall_clock_before_delay, datetime)
    assert request.status == ProductionRequestStatus.SUCCEEDED.value
    assert job.state == RenderJobState.SUCCEEDED.value
    assert transaction_started_at < request.completed_at
    assert transaction_started_at < job.completed_at
    assert transaction_started_at < artifact.created_at
    assert wall_clock_before_delay < job.completed_at
    assert request.completed_at == job.completed_at == artifact.created_at
    assert runtime_truth.created_at == job.completed_at
    assert artifact.production_request_id == request.id
    assert artifact.render_job_id == job.id
    assert runtime_truth.artifact_id == artifact.id
    assert runtime_truth.schema_version == 4
    assert runtime_truth.payload["render_semantics_version"] == 6
