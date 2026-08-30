"""Polling orchestration and lease fencing service for OMEGA-012 Analytics Engine.

Manages scheduled sweeps, worker leases, manual refreshes, and catch-up execution.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.adapters.youtube import (
    YOUTUBE_DATA_API_VIDEOS_URL,
    YouTubeAnalyticsAdapter,
)
from omega.application.analytics.ingestion_service import AnalyticsIngestionService
from omega.application.analytics.quota_service import QuotaService
from omega.application.network.preflight import NetworkPreflightService
from omega.domain.analytics import (
    AnalyticsPollJobStatus,
    WindowType,
    compute_logical_query_key,
    compute_poll_execution_key,
)
from omega.domain.network import NetworkPreflightRequest, ServiceCategory
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsPollJob,
    AnalyticsWindow,
    CredentialVault,
)
from omega.infrastructure.vault import get_credential_vault
from omega.logging import get_logger

logger = get_logger(service="omega-analytics-poll-service")


class AnalyticsPollService:
    """Coordinates asset polling sweeps and worker claims."""

    @staticmethod
    async def create_or_get_active_window(
        session: AsyncSession, asset: AnalyticsAsset, window_type: WindowType = WindowType.LIFETIME
    ) -> AnalyticsWindow:
        """Retrieve or create standard evaluation window for an asset."""
        stmt = select(AnalyticsWindow).where(
            AnalyticsWindow.asset_id == asset.id,
            AnalyticsWindow.window_type == window_type.value,
        )
        res = await session.execute(stmt)
        win = res.scalar_one_or_none()
        if win is None:
            now_utc = datetime.now(UTC)
            win = AnalyticsWindow(
                asset_id=asset.id,
                window_type=window_type.value,
                provider_date=None,
                provider_timezone="America/Los_Angeles",
                window_start_utc=asset.published_at,
                window_end_utc=now_utc + timedelta(days=3650),
                window_state="PROVISIONAL",
            )
            session.add(win)
            await session.flush()
        return win

    @staticmethod
    async def claim_poll_job(
        session: AsyncSession,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> AnalyticsPollJob | None:
        """Claim a pending or expired poll job with fenced lease increment."""
        stmt = (
            select(AnalyticsPollJob)
            .where(
                AnalyticsPollJob.id == job_id,
                (AnalyticsPollJob.status == AnalyticsPollJobStatus.PENDING)
                | (
                    (AnalyticsPollJob.status == AnalyticsPollJobStatus.CLAIMED)
                    & (AnalyticsPollJob.lease_expires_at < func.now())
                ),
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        if job is None:
            return None

        now_utc = datetime.now(UTC)
        job.status = AnalyticsPollJobStatus.CLAIMED
        job.claim_token = uuid4()
        job.claim_generation += 1
        job.claimed_by_worker_id = worker_id
        job.lease_expires_at = now_utc + timedelta(seconds=lease_seconds)
        job.started_at = now_utc
        await session.flush()
        return job

    @staticmethod
    async def execute_asset_poll(
        session: AsyncSession,
        asset_id: UUID,
        worker_id: str = "worker-1",
        manual_refresh_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Execute a full poll cycle for a single asset with preflight and lease fencing."""
        stmt = select(AnalyticsAsset).where(AnalyticsAsset.id == asset_id)
        res = await session.execute(stmt)
        asset = res.scalar_one_or_none()
        if asset is None:
            return {"status": "error", "message": f"Asset {asset_id} not found"}

        now_utc = datetime.now(UTC)
        if manual_refresh_id is not None:
            exec_ctx = f"manual:{manual_refresh_id}"
        else:
            slot_iso = now_utc.strftime("%Y-%m-%dT%H:00:00Z")
            exec_ctx = f"slot:{slot_iso}"

        poll_exec_key = compute_poll_execution_key(asset.id, exec_ctx)

        # 1. Create and Claim Poll Job
        job = AnalyticsPollJob(
            job_type="VIDEO_POLL",
            status=AnalyticsPollJobStatus.PENDING,
            poll_execution_key=poll_exec_key,
            target_asset_ids=[str(asset.id)],
        )
        session.add(job)
        await session.flush()

        claimed_job = await AnalyticsPollService.claim_poll_job(session, job.id, worker_id)
        if claimed_job is None:
            return {"status": "error", "message": "Failed to claim poll job"}

        token = claimed_job.claim_token
        gen = claimed_job.claim_generation
        window = await AnalyticsPollService.create_or_get_active_window(
            session, asset, WindowType.LIFETIME
        )
        window_id = window.id

        # 2. Get Access Token via CredentialVault
        vault = get_credential_vault()
        vault_res = await session.execute(
            select(CredentialVault).where(
                CredentialVault.platform_account_id == asset.platform_account_id
            )
        )
        vault_entry = vault_res.scalar_one_or_none()
        if not vault_entry:
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = "CredentialVault entry missing for platform account."
            await session.flush()
            return {"status": "error", "category": "AUTH_REVOKED", "message": "No vault entry"}

        try:
            access_token = vault.decrypt(
                vault_entry.encrypted_access_token, vault_entry.key_version
            )
        except Exception as exc:
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = f"Auth token failed: {exc}"
            await session.flush()
            return {"status": "error", "category": "AUTH_REVOKED", "message": str(exc)}

        # 3. Network Preflight via OMEGA-009
        preflight_service = NetworkPreflightService(lambda: session)
        _, permit = await preflight_service.preflight(
            NetworkPreflightRequest(
                destination_url=YOUTUBE_DATA_API_VIDEOS_URL,
                service_category=ServiceCategory.YOUTUBE_API,
                caller_key="analytics_asset_poll",
            )
        )
        if permit and not permit.is_permitted:
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = f"Network blocked: {permit.rejection_reason}"
            await session.flush()
            return {"status": "error", "category": "NETWORK_BLOCKED"}

        # 4. Quota Check
        quota_bucket = await QuotaService.get_or_create_bucket(
            session, "YOUTUBE", "DATA_API_V3", "VIDEOS_LIST"
        )
        if not QuotaService.is_quota_available(quota_bucket, 1):
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = "Quota exhausted."
            await session.flush()
            return {"status": "deferred", "category": "QUOTA_EXHAUSTED"}

        # 5. Execute HTTP OUTSIDE Database Transaction
        adapter = YouTubeAnalyticsAdapter()
        try:
            fetch_result = await adapter.fetch_video_batch(
                access_token=access_token,
                provider_video_ids=[asset.provider_video_id],
                permit=permit,
            )
        except Exception as exc:
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = str(exc)
            await session.flush()
            return {"status": "error", "message": str(exc)}

        # Record quota consumption
        await QuotaService.record_usage(session, "YOUTUBE", "DATA_API_V3", "VIDEOS_LIST", units=1)

        # 6. Atomic Post-Network Persistence
        log_query_key = compute_logical_query_key(
            provider="YOUTUBE",
            platform_account_id=asset.platform_account_id,
            provider_video_id=asset.provider_video_id,
            api_source="DATA_API_V3",
            canonical_params_hash="videos_list_standard",
            provider_window_or_date="LIFETIME",
            dimensions_hash="",
        )

        snapshot = await AnalyticsIngestionService.persist_video_poll_result(
            session=session,
            poll_job_id=job.id,
            claim_token=token,
            claim_generation=gen,
            asset_id=asset.id,
            window_id=window_id,
            fetch_result=fetch_result,
            poll_execution_key=poll_exec_key,
            logical_query_key=log_query_key,
        )

        return {
            "status": "success",
            "snapshot_id": str(snapshot.id),
            "asset_id": str(asset.id),
            "job_id": str(job.id),
        }
