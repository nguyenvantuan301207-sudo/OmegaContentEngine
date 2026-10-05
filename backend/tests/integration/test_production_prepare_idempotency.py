"""Persisted prepare identity and PostgreSQL row-lock concurrency regressions."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.production_service import ProductionService, ProductionStateError
from omega.domain.production import ProductionRequestCreate
from omega.infrastructure.models import (
    AssetRequirement,
    MediaArtifact,
    ProductionRenderJob,
    ProductionRequest,
    ProductionScene,
    RenderPlan,
)
from omega.main import app
from tests.integration.test_p18f_canonical_production_canary import _seed_lineage


async def draft_request(session):
    lineage = await _seed_lineage(session)
    service = ProductionService()
    request = await service.create_production_request(
        session,
        lineage["channel"].id,
        ProductionRequestCreate(script_version_id=lineage["scripts"]["interactive"].id),
    )
    return service, request.channel_id, request.id


async def planning_snapshot(session, request_id):
    scenes = (
        (
            await session.execute(
                select(ProductionScene)
                .where(ProductionScene.production_request_id == request_id)
                .order_by(ProductionScene.scene_order)
            )
        )
        .scalars()
        .all()
    )
    plans = (
        (
            await session.execute(
                select(RenderPlan).where(RenderPlan.production_request_id == request_id)
            )
        )
        .scalars()
        .all()
    )
    requirements = (
        (
            await session.execute(
                select(AssetRequirement)
                .join(ProductionScene)
                .where(ProductionScene.production_request_id == request_id)
            )
        )
        .scalars()
        .all()
    )
    jobs = (
        (
            await session.execute(
                select(ProductionRenderJob).where(
                    ProductionRenderJob.production_request_id == request_id
                )
            )
        )
        .scalars()
        .all()
    )
    artifacts = (
        (
            await session.execute(
                select(MediaArtifact).where(MediaArtifact.production_request_id == request_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(plans) == 1
    assert plans[0].version == 1
    assert scenes
    assert requirements
    assert not jobs
    assert not artifacts
    return (
        plans[0].id,
        tuple(scene.id for scene in scenes),
        frozenset(row.id for row in requirements),
    )


@pytest.mark.asyncio
async def test_http_prepare_twice_preserves_persisted_truth(db_session):
    _, channel_id, request_id = await draft_request(db_session)
    path = f"/api/v1/channels/{channel_id}/production/{request_id}/prepare"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(path)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "READY"
        original = await planning_snapshot(db_session, request_id)
        second = await client.post(path)
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "READY"
        assert second.json()["id"] == first.json()["id"]
        assert await planning_snapshot(db_session, request_id) == original


@pytest.mark.asyncio
async def test_concurrent_prepare_uses_request_row_authority(db_session):
    service, channel_id, request_id = await draft_request(db_session)
    # Independent real transactions compete for the same request. Keep a stale
    # DRAFT identity in each session to verify post-lock refresh as well.
    async with (
        AsyncSession(db_session.bind, expire_on_commit=False) as first,
        AsyncSession(db_session.bind, expire_on_commit=False) as second,
    ):
        stale_first = await first.get(ProductionRequest, request_id)
        stale_second = await second.get(ProductionRequest, request_id)
        assert stale_first.status == stale_second.status == "DRAFT"
        results = await asyncio.gather(
            service.prepare_production(first, channel_id, request_id),
            service.prepare_production(second, channel_id, request_id),
        )
        assert [request.status for request in results] == ["READY", "READY"]
        original = await planning_snapshot(first, request_id)
        assert await planning_snapshot(second, request_id) == original
        await service.prepare_production(second, channel_id, request_id)
        assert await planning_snapshot(second, request_id) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"])
async def test_lifecycle_blocks_persisted_repreparation(db_session, state):
    service, channel_id, request_id = await draft_request(db_session)
    request = await service.prepare_production(db_session, channel_id, request_id)
    original = await planning_snapshot(db_session, request_id)
    request.status = state
    await db_session.commit()
    with pytest.raises(ProductionStateError, match=state):
        await service.prepare_production(db_session, channel_id, request_id)
    await db_session.rollback()
    assert await planning_snapshot(db_session, request_id) == original
