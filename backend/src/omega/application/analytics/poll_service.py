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
    YOUTUBE_ANALYTICS_REPORTS_URL,
    YOUTUBE_DATA_API_VIDEOS_URL,
    YouTubeAnalyticsAdapter,
)
from omega.application.analytics.capabilities import MetricCapabilityRegistry
from omega.application.analytics.ingestion_service import AnalyticsIngestionService
from omega.application.analytics.quota_service import QuotaService
from omega.application.network.preflight import NetworkPreflightService
from omega.domain.analytics import (
    AnalyticsPollJobStatus,
    MetricQuality,
    WindowState,
    WindowType,
    compute_logical_query_key,
    compute_poll_execution_key,
    compute_relative_window_utc_range,
)
from omega.domain.network import NetworkPreflightRequest, ServiceCategory
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsPollJob,
    AnalyticsWindow,
    CredentialVault,
    PlatformAccount,
    PublishAttempt,
    PublishIntent,
)
from omega.infrastructure.vault import get_credential_vault
from omega.logging import get_logger

logger = get_logger(service="omega-analytics-poll-service")


class AnalyticsLineageConflictError(ValueError):
    """A candidate's provider identity conflicts with an existing asset's authoritative lineage."""


class AnalyticsPollService:
    """Coordinates asset polling sweeps and worker claims."""

    RECOVERY_BATCH_SIZE = 50
    RECONCILIATION_BATCH_SIZE = 20
    DEEP_REPORT_METRICS = (
        "watch_time_seconds",
        "average_view_duration_seconds",
        "average_percentage_viewed",
        "subscribers_gained",
        "subscribers_lost",
        "shares",
    )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @classmethod
    async def create_or_get_active_window(
        cls, session: AsyncSession, asset: AnalyticsAsset, window_type: WindowType = WindowType.LIFETIME
    ) -> AnalyticsWindow:
        """Retrieve or create a deterministic evaluation window for an asset."""
        published_at = cls._as_utc(asset.published_at)
        stmt = select(AnalyticsWindow).where(
            AnalyticsWindow.asset_id == asset.id,
            AnalyticsWindow.window_type == window_type.value,
            AnalyticsWindow.window_start_utc == published_at,
        )
        win = (await session.execute(stmt)).scalar_one_or_none()
        if win is None:
            if window_type == WindowType.LIFETIME:
                window_end = published_at + timedelta(days=3650)
            else:
                _, window_end = compute_relative_window_utc_range(published_at, window_type)
            win = AnalyticsWindow(
                asset_id=asset.id,
                window_type=window_type.value,
                provider_date=None,
                provider_timezone="America/Los_Angeles",
                window_start_utc=published_at,
                window_end_utc=window_end,
                window_state=WindowState.PROVISIONAL.value,
            )
            session.add(win)
            await session.flush()
        return win

    @classmethod
    async def recover_published_assets(
        cls, session: AsyncSession, limit: int = RECOVERY_BATCH_SIZE
    ) -> list[AnalyticsAsset]:
        """Discover a bounded batch of authoritative publish successes missed by Analytics."""
        stmt = (
            select(PublishAttempt, PublishIntent)
            .join(PublishIntent, PublishIntent.id == PublishAttempt.publish_intent_id)
            .where(
                PublishAttempt.state == "SUCCEEDED",
                PublishAttempt.provider_video_id.is_not(None),
                PublishAttempt.completed_at.is_not(None),
                PublishIntent.state == "PUBLISHED",
                ~select(AnalyticsAsset.id)
                .where(
                    AnalyticsAsset.provider == "YOUTUBE",
                    AnalyticsAsset.provider_video_id == PublishAttempt.provider_video_id,
                    AnalyticsAsset.publish_intent_id == PublishIntent.id,
                    AnalyticsAsset.publish_attempt_id == PublishAttempt.id,
                    AnalyticsAsset.platform_account_id == PublishIntent.platform_account_id,
                    AnalyticsAsset.channel_id == PublishIntent.channel_id,
                    AnalyticsAsset.media_artifact_id == PublishIntent.media_artifact_id,
                    AnalyticsAsset.channel_dna_revision_id.is_not_distinct_from(
                        PublishIntent.channel_dna_revision_id
                    ),
                )
                .exists(),
            )
            .order_by(PublishAttempt.completed_at.asc(), PublishAttempt.id.asc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        recovered: list[AnalyticsAsset] = []
        for attempt, intent in rows:
            try:
                asset, created = await cls._recover_publish_attempt_candidate(
                    session, attempt, intent
                )
            except AnalyticsLineageConflictError:
                logger.error(
                    "recovery_lineage_conflict_skipped",
                    publish_attempt_id=str(attempt.id),
                    publish_intent_id=str(intent.id),
                    provider="YOUTUBE",
                    provider_video_id=attempt.provider_video_id,
                )
                continue
            if created:
                recovered.append(asset)
        return recovered

    @classmethod
    async def _recover_publish_attempt_candidate(
        cls, session: AsyncSession, attempt: PublishAttempt, intent: PublishIntent
    ) -> tuple[AnalyticsAsset, bool]:
        """Register one authoritative candidate, rejecting conflicting canonical lineage."""
        provider = "YOUTUBE"
        existing = (
            await session.execute(
                select(AnalyticsAsset).where(
                    AnalyticsAsset.provider == provider,
                    AnalyticsAsset.provider_video_id == attempt.provider_video_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            authoritative_lineage = (
                intent.id,
                attempt.id,
                intent.platform_account_id,
                intent.channel_id,
                intent.media_artifact_id,
                intent.channel_dna_revision_id,
            )
            existing_lineage = (
                existing.publish_intent_id,
                existing.publish_attempt_id,
                existing.platform_account_id,
                existing.channel_id,
                existing.media_artifact_id,
                existing.channel_dna_revision_id,
            )
            if existing_lineage != authoritative_lineage:
                raise AnalyticsLineageConflictError(
                    "AnalyticsAsset provider identity conflicts with authoritative publish lineage: "
                    f"{provider}:{attempt.provider_video_id}"
                )
            asset = existing
        else:
            published_at = cls._as_utc(attempt.completed_at)
            asset = AnalyticsAsset(
                channel_id=intent.channel_id,
                platform_account_id=intent.platform_account_id,
                publish_intent_id=intent.id,
                publish_attempt_id=attempt.id,
                media_artifact_id=intent.media_artifact_id,
                channel_dna_revision_id=intent.channel_dna_revision_id,
                provider=provider,
                provider_video_id=attempt.provider_video_id,
                published_at=published_at,
                asset_status="ACTIVE",
                lifecycle_phase="ACTIVE_HOURLY",
                next_poll_due_at=published_at,
            )
            session.add(asset)
            await session.flush()

        await cls.create_or_get_active_window(session, asset, WindowType.FIRST_24H)
        await cls.create_or_get_active_window(session, asset, WindowType.FIRST_7D)
        return asset, existing is None

    @classmethod
    async def reconcile_windows(
        cls, session: AsyncSession, limit: int = RECONCILIATION_BATCH_SIZE
    ) -> dict[str, int]:
        """Finalize a bounded batch of elapsed P11-A windows without mutating evidence."""
        stmt = (
            select(AnalyticsWindow)
            .where(
                AnalyticsWindow.window_type.in_(
                    [WindowType.FIRST_24H.value, WindowType.FIRST_7D.value]
                ),
                AnalyticsWindow.window_state == WindowState.PROVISIONAL.value,
                AnalyticsWindow.window_end_utc <= func.now(),
            )
            .order_by(AnalyticsWindow.window_end_utc.asc(), AnalyticsWindow.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        windows = (await session.execute(stmt)).scalars().all()
        now_utc = (await session.execute(select(func.now()))).scalar_one()
        finalized = sum(cls._finalize_window_if_eligible(window, now_utc) for window in windows)
        await session.flush()
        return {"checked_windows": len(windows), "finalized_windows": finalized}

    @classmethod
    def _finalize_window_if_eligible(
        cls, window: AnalyticsWindow, now_utc: datetime
    ) -> bool:
        """Apply the elapsed relative-window transition using the database clock."""
        if (
            window.window_type not in (WindowType.FIRST_24H.value, WindowType.FIRST_7D.value)
            or window.window_state != WindowState.PROVISIONAL.value
            or cls._as_utc(window.window_end_utc) > cls._as_utc(now_utc)
        ):
            return False
        window.window_state = WindowState.FINALIZED.value
        window.finalized_at = now_utc
        return True

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

    @classmethod
    async def execute_asset_report(
        cls,
        session: AsyncSession,
        asset_id: UUID,
        worker_id: str = "analytics-report-worker",
    ) -> dict[str, Any]:
        """Execute the real scope-, capability-, quota-, and preflight-gated deep report path."""
        asset = (
            await session.execute(select(AnalyticsAsset).where(AnalyticsAsset.id == asset_id))
        ).scalar_one_or_none()
        if asset is None:
            return {"status": "error", "message": f"Asset {asset_id} not found"}

        account = (
            await session.execute(
                select(PlatformAccount).where(PlatformAccount.id == asset.platform_account_id)
            )
        ).scalar_one()
        provider_metrics: list[str] = []
        for metric_name in cls.DEEP_REPORT_METRICS:
            valid, quality = MetricCapabilityRegistry.validate_metric_request(
                provider=asset.provider,
                canonical_metric_name=metric_name,
                api_source="ANALYTICS_API_V2",
                report_type="BASIC_VIDEO_REPORT",
                dimensions=[],
                account_scopes=account.scopes or [],
            )
            if not valid:
                return {
                    "status": "not_ready",
                    "category": "AUTH_SCOPE_MISSING"
                    if quality == MetricQuality.PERMISSION_DENIED
                    else "CAPABILITY_UNAVAILABLE",
                    "metric_quality": quality.value,
                }
            capability = MetricCapabilityRegistry.get_capability(
                asset.provider, metric_name, "ANALYTICS_API_V2"
            )
            if capability is None:
                return {"status": "not_ready", "category": "CAPABILITY_UNAVAILABLE"}
            provider_metrics.append(capability.provider_metric_name)

        now_utc = datetime.now(UTC)
        window_stmt = (
            select(AnalyticsWindow)
            .where(
                AnalyticsWindow.asset_id == asset.id,
                AnalyticsWindow.window_type.in_(
                    [WindowType.FIRST_24H.value, WindowType.FIRST_7D.value]
                ),
                AnalyticsWindow.window_state.in_(
                    [WindowState.PROVISIONAL.value, WindowState.FINALIZED.value]
                ),
            )
            .order_by(AnalyticsWindow.window_end_utc.asc())
            .limit(1)
        )
        window = (await session.execute(window_stmt)).scalar_one_or_none()
        if window is None:
            window = await cls.create_or_get_active_window(session, asset, WindowType.FIRST_7D)

        exec_ctx = f"report:{window.window_type}:{now_utc.strftime('%Y-%m-%dT%H:00:00Z')}"
        poll_exec_key = compute_poll_execution_key(asset.id, exec_ctx)
        job = AnalyticsPollJob(
            job_type="VIDEO_REPORT",
            status=AnalyticsPollJobStatus.PENDING,
            poll_execution_key=poll_exec_key,
            target_asset_ids=[str(asset.id)],
        )
        session.add(job)
        await session.flush()
        claimed_job = await cls.claim_poll_job(session, job.id, worker_id)
        if claimed_job is None:
            return {"status": "error", "message": "Failed to claim report job"}

        vault_entry = (
            await session.execute(
                select(CredentialVault).where(
                    CredentialVault.platform_account_id == asset.platform_account_id
                )
            )
        ).scalar_one_or_none()
        if vault_entry is None:
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = "CredentialVault entry missing for platform account."
            await session.flush()
            return {"status": "error", "category": "AUTH_REVOKED"}
        try:
            access_token = get_credential_vault().decrypt(
                vault_entry.encrypted_access_token, vault_entry.key_version
            )
        except Exception as exc:
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = f"Auth token failed: {exc}"
            await session.flush()
            return {"status": "error", "category": "AUTH_REVOKED", "message": str(exc)}

        _, permit = await NetworkPreflightService(lambda: session).preflight(
            NetworkPreflightRequest(
                destination_url=YOUTUBE_ANALYTICS_REPORTS_URL,
                service_category=ServiceCategory.YOUTUBE_API,
                caller_key="analytics_video_report",
            )
        )
        if permit is None or not permit.is_valid_for(YOUTUBE_ANALYTICS_REPORTS_URL):
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = "Network preflight did not permit YouTube Analytics report."
            await session.flush()
            return {"status": "error", "category": "NETWORK_BLOCKED"}

        quota_bucket = await QuotaService.get_or_create_bucket(
            session, "YOUTUBE", "ANALYTICS_API_V2", "REPORTS_QUERY"
        )
        if not QuotaService.is_quota_available(quota_bucket, 1):
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = "Quota exhausted."
            await session.flush()
            return {"status": "deferred", "category": "QUOTA_EXHAUSTED"}

        end_at = min(cls._as_utc(window.window_end_utc), now_utc)
        try:
            fetch_result = await YouTubeAnalyticsAdapter().fetch_video_report(
                access_token=access_token,
                provider_video_id=asset.provider_video_id,
                start_date=cls._as_utc(window.window_start_utc).date(),
                end_date=end_at.date(),
                metrics=provider_metrics,
                dimensions=None,
                permit=permit,
            )
        except Exception as exc:
            claimed_job.status = AnalyticsPollJobStatus.FAILED
            claimed_job.error_message = str(exc)
            await session.flush()
            return {"status": "error", "message": str(exc)}

        await QuotaService.record_usage(
            session, "YOUTUBE", "ANALYTICS_API_V2", "REPORTS_QUERY", units=1
        )
        logical_query_key = compute_logical_query_key(
            provider=asset.provider,
            platform_account_id=asset.platform_account_id,
            provider_video_id=asset.provider_video_id,
            api_source="ANALYTICS_API_V2",
            canonical_params_hash="basic_video_report_v1",
            provider_window_or_date=window.window_type,
            dimensions_hash="",
        )
        snapshot = await AnalyticsIngestionService.persist_video_poll_result(
            session=session,
            poll_job_id=job.id,
            claim_token=claimed_job.claim_token,
            claim_generation=claimed_job.claim_generation,
            asset_id=asset.id,
            window_id=window.id,
            fetch_result=fetch_result,
            poll_execution_key=poll_exec_key,
            logical_query_key=logical_query_key,
        )
        return {
            "status": "success",
            "snapshot_id": str(snapshot.id),
            "asset_id": str(asset.id),
            "job_id": str(job.id),
            "window_type": window.window_type,
        }
