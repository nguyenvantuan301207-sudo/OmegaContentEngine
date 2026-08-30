"""Atomic post-network ingestion and persistence service for OMEGA-012 Analytics Engine.

Enforces poll job fencing, window row locking, immutable revision lineage, and zero-leak rollback.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.adapters.base import AnalyticsAdapterError, ProviderFetchResult
from omega.application.analytics.computed_service import ComputedMetricsService
from omega.application.analytics.normalizer import AnalyticsNormalizer, NormalizedObservationFact
from omega.domain.analytics import (
    AnalyticsErrorCategory,
    AnalyticsLifecyclePhase,
    AnalyticsPollJobStatus,
    MetricClassification,
    compute_snapshot_dedupe_key,
)
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsChannelLatestPointer,
    AnalyticsChannelSnapshot,
    AnalyticsComputedMetric,
    AnalyticsMetricLatestPointer,
    AnalyticsMetricObservation,
    AnalyticsPollJob,
    AnalyticsProviderSnapshot,
    AnalyticsWindow,
)
from omega.logging import get_logger

logger = get_logger(service="omega-analytics-ingestion")


class AnalyticsIngestionService:
    """Coordinates post-network atomic persistence and revision management."""

    @staticmethod
    async def persist_video_poll_result(
        session: AsyncSession,
        poll_job_id: UUID,
        claim_token: UUID,
        claim_generation: int,
        asset_id: UUID,
        window_id: UUID,
        fetch_result: ProviderFetchResult,
        poll_execution_key: str,
        logical_query_key: str,
    ) -> AnalyticsProviderSnapshot:
        """Execute atomic post-network persistence for a video asset poll."""
        now_utc = datetime.now(UTC)

        # 1. Revalidate Poll Job Lease Fencing
        stmt_job = (
            select(AnalyticsPollJob)
            .where(
                AnalyticsPollJob.id == poll_job_id,
                AnalyticsPollJob.claim_token == claim_token,
                AnalyticsPollJob.claim_generation == claim_generation,
                AnalyticsPollJob.lease_expires_at > func.now(),
            )
            .with_for_update()
        )
        res_job = await session.execute(stmt_job)
        poll_job = res_job.scalar_one_or_none()
        if poll_job is None:
            raise AnalyticsAdapterError(
                message=f"Poll job {poll_job_id} lease expired or generation superseded. Fenced out.",
                category=AnalyticsErrorCategory.FENCING_REJECTED,
                retryable=False,
            )

        # 2. Lock Evaluation Window Anchor FOR UPDATE
        stmt_win = select(AnalyticsWindow).where(AnalyticsWindow.id == window_id).with_for_update()
        res_win = await session.execute(stmt_win)
        window = res_win.scalar_one_or_none()
        if window is None:
            raise ValueError(f"AnalyticsWindow {window_id} not found.")

        # 3. Deduplicate / Insert Raw Provider Snapshot
        dedupe_key = compute_snapshot_dedupe_key(poll_execution_key, fetch_result.payload_checksum)
        stmt_snap = select(AnalyticsProviderSnapshot).where(
            AnalyticsProviderSnapshot.snapshot_dedupe_key == dedupe_key
        )
        res_snap = await session.execute(stmt_snap)
        snapshot = res_snap.scalar_one_or_none()

        stmt_asset = select(AnalyticsAsset).where(AnalyticsAsset.id == asset_id)
        res_asset = await session.execute(stmt_asset)
        asset = res_asset.scalar_one()

        is_new_snapshot = False
        if snapshot is None:
            snapshot = AnalyticsProviderSnapshot(
                asset_id=asset_id,
                channel_id=asset.channel_id,
                platform_account_id=asset.platform_account_id,
                api_endpoint=fetch_result.api_endpoint,
                request_params=fetch_result.request_params,
                raw_payload=fetch_result.raw_payload,
                payload_checksum=fetch_result.payload_checksum,
                logical_query_key=logical_query_key,
                poll_execution_key=poll_execution_key,
                snapshot_dedupe_key=dedupe_key,
                retrieval_timestamp=fetch_result.retrieval_timestamp,
                http_status=fetch_result.http_status,
            )
            session.add(snapshot)
            await session.flush()
            is_new_snapshot = True

        # 4. Check if Observations Already Exist for this Snapshot
        stmt_check_obs = select(AnalyticsMetricObservation).where(
            AnalyticsMetricObservation.snapshot_id == snapshot.id,
            AnalyticsMetricObservation.window_id == window_id,
        )
        existing_obs = (await session.execute(stmt_check_obs)).scalars().all()
        if existing_obs and not is_new_snapshot:
            # Same snapshot already normalized. Complete poll job without fake revision.
            poll_job.status = AnalyticsPollJobStatus.COMPLETED
            poll_job.completed_at = now_utc
            await session.flush()
            return snapshot

        # 5. Normalize Observations
        normalized_facts: list[NormalizedObservationFact] = []
        if fetch_result.api_endpoint == "YOUTUBE_DATA_API_VIDEOS":
            items = (
                fetch_result.raw_payload.get("items", [])
                if isinstance(fetch_result.raw_payload, dict)
                else []
            )
            if items:
                normalized_facts = AnalyticsNormalizer.normalize_data_api_video_item(
                    items[0], snapshot.id, window_id, fetch_result.retrieval_timestamp
                )
        elif fetch_result.api_endpoint == "YOUTUBE_ANALYTICS_REPORTS":
            if isinstance(fetch_result.raw_payload, dict):
                normalized_facts = AnalyticsNormalizer.normalize_analytics_report_payload(
                    fetch_result.raw_payload,
                    snapshot.id,
                    window_id,
                    fetch_result.retrieval_timestamp,
                )

        # 6. Allocate Revisions and Update Latest Pointers
        obs_map: dict[str, AnalyticsMetricObservation] = {}
        for fact in normalized_facts:
            stmt_ptr = (
                select(AnalyticsMetricLatestPointer)
                .where(
                    AnalyticsMetricLatestPointer.asset_id == asset_id,
                    AnalyticsMetricLatestPointer.window_id == window_id,
                    AnalyticsMetricLatestPointer.metric_name == fact.metric_name,
                    AnalyticsMetricLatestPointer.dimensions_hash == fact.dimensions_hash,
                )
                .with_for_update()
            )
            res_ptr = await session.execute(stmt_ptr)
            pointer = res_ptr.scalar_one_or_none()

            if pointer is None:
                next_rev = 1
                preceding_id = None
            else:
                next_rev = pointer.current_revision_sequence + 1
                preceding_id = pointer.current_observation_id

            obs = AnalyticsMetricObservation(
                asset_id=asset_id,
                window_id=window_id,
                snapshot_id=snapshot.id,
                metric_name=fact.metric_name,
                classification=MetricClassification.PROVIDER_FACT,
                metric_quality=fact.metric_quality,
                numeric_value=fact.numeric_value,
                integer_value=fact.integer_value,
                string_value=fact.string_value,
                dimensions=fact.dimensions,
                dimensions_hash=fact.dimensions_hash,
                normalization_version=fact.normalization_version,
                observation_dedupe_key=fact.observation_dedupe_key,
                revision_sequence=next_rev,
                preceding_observation_id=preceding_id,
                observation_timestamp=fact.observation_timestamp,
            )
            session.add(obs)
            await session.flush()
            obs_map[fact.metric_name] = obs

            # Update or create pointer
            if pointer is None:
                pointer = AnalyticsMetricLatestPointer(
                    asset_id=asset_id,
                    window_id=window_id,
                    metric_name=fact.metric_name,
                    dimensions_hash=fact.dimensions_hash,
                    current_observation_id=obs.id,
                    current_revision_sequence=next_rev,
                    updated_at=now_utc,
                )
                session.add(pointer)
            else:
                pointer.current_observation_id = obs.id
                pointer.current_revision_sequence = next_rev
                pointer.updated_at = now_utc
            await session.flush()

        # 7. Compute Derived Metrics (Multi-source, Deduplicated)
        computed_metrics = ComputedMetricsService.compute_window_derived_metrics(
            asset_id, window_id, obs_map
        )
        for comp in computed_metrics:
            # Check if identical computation already exists
            stmt_comp = select(AnalyticsComputedMetric).where(
                AnalyticsComputedMetric.computation_dedupe_key == comp.computation_dedupe_key
            )
            existing_comp = (await session.execute(stmt_comp)).scalar_one_or_none()
            if existing_comp is None:
                stmt_max_rev = select(
                    func.max(AnalyticsComputedMetric.computation_revision_sequence)
                ).where(
                    AnalyticsComputedMetric.window_id == window_id,
                    AnalyticsComputedMetric.metric_name == comp.metric_name,
                    AnalyticsComputedMetric.formula_version == comp.formula_version,
                )
                max_rev = (await session.execute(stmt_max_rev)).scalar() or 0
                comp.computation_revision_sequence = max_rev + 1
                session.add(comp)
        await session.flush()

        # 8. Advance Asset State & Next Poll Due
        asset.last_polled_at = now_utc
        asset.poll_error_count = 0
        asset.last_error_category = None

        # Determine next poll cadence based on age
        age = now_utc - asset.published_at
        if age.total_seconds() < 86400 * 3:
            asset.lifecycle_phase = AnalyticsLifecyclePhase.ACTIVE_HOURLY
            asset.next_poll_due_at = now_utc + func.make_interval(0, 0, 0, 0, 1, 0, 0)
        elif age.total_seconds() < 86400 * 14:
            asset.lifecycle_phase = AnalyticsLifecyclePhase.MATURE_DAILY
            asset.next_poll_due_at = now_utc + func.make_interval(0, 0, 0, 1, 0, 0, 0)
        else:
            asset.lifecycle_phase = AnalyticsLifecyclePhase.STABLE_WEEKLY
            asset.next_poll_due_at = now_utc + func.make_interval(0, 0, 0, 7, 0, 0, 0)

        # 9. Complete Poll Job
        poll_job.status = AnalyticsPollJobStatus.COMPLETED
        poll_job.completed_at = now_utc
        await session.flush()

        return snapshot

    @staticmethod
    async def persist_channel_poll_result(
        session: AsyncSession,
        poll_job_id: UUID,
        claim_token: UUID,
        claim_generation: int,
        channel_id: UUID,
        platform_account_id: UUID,
        snapshot_date: date,
        fetch_result: ProviderFetchResult,
        poll_execution_key: str,
        logical_query_key: str,
    ) -> AnalyticsChannelSnapshot:
        """Execute atomic post-network persistence for channel statistics."""
        now_utc = datetime.now(UTC)

        # 1. Revalidate Poll Job Fencing
        stmt_job = (
            select(AnalyticsPollJob)
            .where(
                AnalyticsPollJob.id == poll_job_id,
                AnalyticsPollJob.claim_token == claim_token,
                AnalyticsPollJob.claim_generation == claim_generation,
                AnalyticsPollJob.lease_expires_at > func.now(),
            )
            .with_for_update()
        )
        res_job = await session.execute(stmt_job)
        poll_job = res_job.scalar_one_or_none()
        if poll_job is None:
            raise AnalyticsAdapterError(
                message=f"Channel poll job {poll_job_id} lease expired or fenced out.",
                category=AnalyticsErrorCategory.FENCING_REJECTED,
                retryable=False,
            )

        # 2. Deduplicate / Insert Raw Provider Snapshot (Single Source of Raw Truth)
        dedupe_key = compute_snapshot_dedupe_key(poll_execution_key, fetch_result.payload_checksum)
        stmt_snap = select(AnalyticsProviderSnapshot).where(
            AnalyticsProviderSnapshot.snapshot_dedupe_key == dedupe_key
        )
        res_snap = await session.execute(stmt_snap)
        snapshot = res_snap.scalar_one_or_none()

        if snapshot is None:
            snapshot = AnalyticsProviderSnapshot(
                asset_id=None,
                channel_id=channel_id,
                platform_account_id=platform_account_id,
                api_endpoint=fetch_result.api_endpoint,
                request_params=fetch_result.request_params,
                raw_payload=fetch_result.raw_payload,
                payload_checksum=fetch_result.payload_checksum,
                logical_query_key=logical_query_key,
                poll_execution_key=poll_execution_key,
                snapshot_dedupe_key=dedupe_key,
                retrieval_timestamp=fetch_result.retrieval_timestamp,
                http_status=fetch_result.http_status,
            )
            session.add(snapshot)
            await session.flush()

        # 3. Parse Channel Stats
        items = (
            fetch_result.raw_payload.get("items", [])
            if isinstance(fetch_result.raw_payload, dict)
            else []
        )
        stats = items[0].get("statistics", {}) if items else {}
        total_views = int(stats.get("viewCount", 0))
        sub_count = int(stats.get("subscriberCount", 0))
        video_count = int(stats.get("videoCount", 0))

        # 4. Check Pointer & Allocate Channel Snapshot Revision
        stmt_ptr = (
            select(AnalyticsChannelLatestPointer)
            .where(
                AnalyticsChannelLatestPointer.channel_id == channel_id,
                AnalyticsChannelLatestPointer.snapshot_date == snapshot_date,
            )
            .with_for_update()
        )
        res_ptr = await session.execute(stmt_ptr)
        pointer = res_ptr.scalar_one_or_none()

        if pointer is None:
            next_rev = 1
            preceding_id = None
        else:
            next_rev = pointer.current_revision_sequence + 1
            preceding_id = pointer.current_channel_snapshot_id

        # 5. Insert Immutable Channel Snapshot (References Raw Snapshot)
        from omega.domain.analytics import compute_pacific_calendar_day_utc_range

        win_start, win_end = compute_pacific_calendar_day_utc_range(snapshot_date)

        channel_snap = AnalyticsChannelSnapshot(
            channel_id=channel_id,
            platform_account_id=platform_account_id,
            snapshot_id=snapshot.id,
            snapshot_date=snapshot_date,
            provider_timezone="America/Los_Angeles",
            window_start_utc=win_start,
            window_end_utc=win_end,
            total_views=total_views,
            subscriber_count=sub_count,
            video_count=video_count,
            aggregate_watch_time_seconds=None,
            revision_sequence=next_rev,
            preceding_snapshot_id=preceding_id,
        )
        session.add(channel_snap)
        await session.flush()

        # Update Pointer
        if pointer is None:
            pointer = AnalyticsChannelLatestPointer(
                channel_id=channel_id,
                snapshot_date=snapshot_date,
                current_channel_snapshot_id=channel_snap.id,
                current_revision_sequence=next_rev,
                updated_at=now_utc,
            )
            session.add(pointer)
        else:
            pointer.current_channel_snapshot_id = channel_snap.id
            pointer.current_revision_sequence = next_rev
            pointer.updated_at = now_utc
        await session.flush()

        # 6. Complete Poll Job
        poll_job.status = AnalyticsPollJobStatus.COMPLETED
        poll_job.completed_at = now_utc
        await session.flush()

        return channel_snap
