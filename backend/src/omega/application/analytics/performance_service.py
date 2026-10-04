"""Canonical Performance Analytics Service for P25-B.

Coordinates provider ingestion, normalization, delta/velocity derivation,
counter anomaly detection, retention curve handling, and idempotent persistence.

Boundaries:
- P25-B: Published-content performance analytics attached to PublishReceipt.
- Does NOT perform causal attribution (P25-C).
- Does NOT perform learning or creative recommendations (P25-D).
- Does NOT mutate external provider accounts.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.performance_normalizer import PerformanceNormalizer
from omega.application.analytics.performance_provider import (
    AnalyticsErrorClassification,
    PerformanceAnalyticsError,
    PerformanceAnalyticsProvider,
    RetryableAnalyticsError,
    TerminalAnalyticsError,
)
from omega.domain.analytics import (
    AnalyticsAssetStatus,
    AnalyticsLifecyclePhase,
    MetricClassification,
    MetricQuality,
    WindowState,
    WindowType,
    compute_observation_dedupe_key,
    compute_payload_checksum,
)
from omega.domain.performance_analytics import (
    CanonicalMetrics,
    CounterAnomalyRecord,
    DataFreshnessStatus,
    DeltaVelocityMetrics,
    DerivedRetentionMetrics,
    PerformanceSnapshot,
    PerformanceSummary,
    RawProviderMetricsPayload,
    RetentionCurve,
)
from omega.domain.publishing import PublishReceipt
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsChannelSnapshot,
    AnalyticsComputedMetric,
    AnalyticsMetricLatestPointer,
    AnalyticsMetricObservation,
    AnalyticsProviderSnapshot,
    AnalyticsWindow,
    PublishIntent,
)
from omega.logging import get_logger

logger = get_logger(service="omega-performance-analytics")


class PerformanceAnalyticsService:
    """Authoritative service for published production performance analytics."""

    # ── Snapshot Ingestion ───────────────────────────────────────────────────

    @classmethod
    async def ingest_performance_snapshot(
        cls,
        session: AsyncSession,
        receipt: PublishReceipt,
        provider: PerformanceAnalyticsProvider,
        as_of: datetime | None = None,
        sync_checkpoint: str | None = None,
        max_retries: int = 3,
    ) -> PerformanceSnapshot:
        """Fetch, normalize, derive, and persist an authoritative performance snapshot.

        Guarantees:
        - Bounded retry for retryable provider failures.
        - Fail-closed terminal error handling.
        - Ingestion idempotency (replaying identical snapshot does not duplicate).
        - Explicit freshness modeling (delayed data is DELAYED, never zero).
        - Counter anomaly detection.
        - Retention curve integration with linear interpolation.
        - Strictly read-only towards external providers.
        """
        now_utc = datetime.now(UTC)
        obs_time = as_of or now_utc
        if obs_time.tzinfo is None:
            obs_time = obs_time.replace(tzinfo=UTC)

        ext_id = receipt.external_media_id
        prov_name = receipt.provider

        logger.info(
            "analytics.sync.started",
            receipt_id=str(receipt.receipt_id),
            provider=prov_name,
            external_media_id=ext_id,
            as_of=obs_time.isoformat(),
        )

        # 1. Check Media Availability
        media_avail = await provider.check_media_availability(ext_id)
        if media_avail == "DELAYED":
            logger.info("analytics.provider.lag", external_media_id=ext_id)
            freshness = DataFreshnessStatus.DELAYED
        elif media_avail in ("UNAVAILABLE", "DELETED"):
            freshness = DataFreshnessStatus.UNAVAILABLE
        else:
            freshness = DataFreshnessStatus.FRESH

        # 2. Fetch Raw Provider Metrics with Bounded Retry
        raw_payload: RawProviderMetricsPayload | None = None
        attempt_count = 0
        last_error: Exception | None = None

        while attempt_count < max_retries:
            attempt_count += 1
            try:
                raw_payload = await provider.fetch_media_metrics(ext_id, as_of=obs_time)
                break
            except RetryableAnalyticsError as err:
                last_error = err
                logger.warning(
                    "analytics.provider.error",
                    error=str(err),
                    classification=err.classification.value,
                    attempt=attempt_count,
                    retryable=True,
                )
                if err.classification == AnalyticsErrorClassification.TEMPORARY_LAG:
                    logger.info("analytics.provider.lag", external_media_id=ext_id)
                    freshness = DataFreshnessStatus.DELAYED
                    # Provider analytics pipeline lagging: do not treat missing data as zero!
                    break
                if attempt_count < max_retries:
                    await asyncio.sleep(0.01 * (2 ** (attempt_count - 1)))
            except TerminalAnalyticsError as err:
                logger.error(
                    "analytics.provider.error",
                    error=str(err),
                    classification=err.classification.value,
                    retryable=False,
                )
                raise

        if raw_payload is None:
            if freshness == DataFreshnessStatus.DELAYED:
                # Return a valid DELAYED snapshot with empty metrics (not zeroes!)
                empty_metrics = CanonicalMetrics(
                    views=None,
                    impressions=None,
                    watch_time_seconds=None,
                    average_view_duration_seconds=None,
                    average_percentage_viewed=None,
                    impressions_ctr=None,
                    metric_qualities={"all": MetricQuality.NOT_READY.value},
                )
                return PerformanceSnapshot(
                    snapshot_id=uuid4(),
                    receipt_id=receipt.receipt_id,
                    provider=prov_name,
                    external_media_id=ext_id,
                    observed_at=obs_time,
                    freshness=DataFreshnessStatus.DELAYED,
                    metrics=empty_metrics,
                    lineage=receipt.lineage,
                    sync_checkpoint=sync_checkpoint,
                )
            if last_error:
                raise last_error
            raise TerminalAnalyticsError(
                f"Failed to fetch metrics for media {ext_id} after {max_retries} attempts",
                AnalyticsErrorClassification.INVALID_MEDIA_ID,
            )

        # 3. Compute Deduplication Key and Check Idempotency
        payload_checksum = raw_payload.compute_checksum()
        logical_key = hashlib.sha256(f"{prov_name}:{ext_id}:{raw_payload.provider_timestamp.isoformat()}".encode("utf-8")).hexdigest()
        dedupe_key = hashlib.sha256(f"snap:{prov_name}:{ext_id}:{payload_checksum}".encode("utf-8")).hexdigest()

        # 4. Resolve or Create AnalyticsAsset Anchor
        asset = await cls._ensure_analytics_asset(session, receipt)
        poll_key = hashlib.sha256(f"sync:{asset.id}:{obs_time.isoformat()}".encode("utf-8")).hexdigest()

        # Check existing snapshot
        stmt_existing = select(AnalyticsProviderSnapshot).where(
            AnalyticsProviderSnapshot.snapshot_dedupe_key == dedupe_key
        )
        res_existing = await session.execute(stmt_existing)
        existing_snap_row = res_existing.scalar_one_or_none()

        if existing_snap_row is not None:
            logger.info(
                "analytics.snapshot.duplicate_skipped",
                dedupe_key=dedupe_key,
                snapshot_id=str(existing_snap_row.id),
            )
            # Reconstruct and return existing snapshot without duplicating records
            existing_snapshot = await cls._reconstruct_snapshot(
                session, asset, receipt, existing_snap_row, sync_checkpoint
            )
            return existing_snapshot

        # 5. Normalize Canonical Metrics
        canonical_metrics = PerformanceNormalizer.normalize(raw_payload)

        # 6. Fetch Retention Curve & Derive Retention Metrics
        retention_curve = await provider.fetch_retention_curve(ext_id)
        if retention_curve:
            duration = canonical_metrics.watch_time_seconds or canonical_metrics.average_view_duration_seconds
            derived_retention = DerivedRetentionMetrics.from_curve(retention_curve, duration)
        else:
            derived_retention = None
            logger.info("analytics.retention.unavailable", external_media_id=ext_id)

        # 7. Previous Snapshot & Anomaly / Delta Derivation
        previous_snapshot = await cls._get_latest_snapshot_for_asset(session, asset, receipt)
        anomalies: list[CounterAnomalyRecord] = []
        if previous_snapshot:
            anomalies = PerformanceNormalizer.detect_counter_anomalies(
                canonical_metrics, previous_snapshot.metrics
            )
            if anomalies:
                logger.warning(
                    "analytics.counter.anomaly",
                    count=len(anomalies),
                    external_media_id=ext_id,
                )

        deltas = DeltaVelocityMetrics.compute(
            current=canonical_metrics,
            current_time=raw_payload.observed_at,
            previous=previous_snapshot.metrics if previous_snapshot else None,
            previous_time=previous_snapshot.observed_at if previous_snapshot else None,
        )

        # 8. Persist Raw Snapshot to Database
        provider_snapshot_row = AnalyticsProviderSnapshot(
            asset_id=asset.id,
            channel_id=asset.channel_id,
            platform_account_id=asset.platform_account_id,
            api_endpoint="PROVIDER_PERFORMANCE_METRICS",
            request_params={"external_media_id": ext_id},
            raw_payload=raw_payload.raw_data,
            payload_checksum=payload_checksum,
            logical_query_key=logical_key,
            poll_execution_key=poll_key,
            snapshot_dedupe_key=dedupe_key,
            retrieval_timestamp=raw_payload.observed_at,
            http_status=200,
        )
        session.add(provider_snapshot_row)
        await session.flush()

        # 9. Ensure / Update Lifetime Evaluation Window
        window = await cls._ensure_window(session, asset, obs_time)

        # 10. Persist Metric Observations
        await cls._persist_metric_observations(
            session, asset, window, provider_snapshot_row, canonical_metrics, raw_payload.observed_at
        )

        # 11. Persist Computed Metrics (Deltas & Velocity)
        if deltas.is_valid:
            await cls._persist_computed_metrics(session, asset, window, deltas, raw_payload.observed_at)

        # 12. Update Asset Poll State & Status
        asset.last_polled_at = now_utc
        if media_avail in ("UNAVAILABLE", "DELETED"):
            asset.asset_status = (
                AnalyticsAssetStatus.PROVIDER_DELETED.value
                if media_avail == "DELETED"
                else AnalyticsAssetStatus.UNAVAILABLE.value
            )
        await session.flush()

        # 13. Construct and Return Canonical PerformanceSnapshot
        snapshot = PerformanceSnapshot(
            snapshot_id=uuid4(),
            receipt_id=receipt.receipt_id,
            provider=prov_name,
            external_media_id=ext_id,
            observed_at=raw_payload.observed_at,
            freshness=freshness,
            metrics=canonical_metrics,
            retention_curve=retention_curve,
            derived_retention=derived_retention,
            deltas=deltas if deltas.is_valid else None,
            anomalies=anomalies,
            raw_snapshot_id=provider_snapshot_row.id,
            raw_payload_checksum=payload_checksum,
            lineage=receipt.lineage,
            sync_checkpoint=sync_checkpoint,
        )

        logger.info(
            "analytics.snapshot.ingested",
            snapshot_id=str(snapshot.snapshot_id),
            views=snapshot.metrics.views,
            external_media_id=ext_id,
        )
        logger.info("analytics.sync.completed", snapshot_id=str(snapshot.snapshot_id))

        return snapshot

    # ── Query Seam ───────────────────────────────────────────────────────────

    @classmethod
    async def get_latest_performance(
        cls,
        session: AsyncSession,
        receipt: PublishReceipt,
    ) -> PerformanceSnapshot | None:
        """Query authoritative latest performance snapshot for a published video."""
        asset = await cls._find_asset(session, receipt)
        if not asset:
            return None
        return await cls._get_latest_snapshot_for_asset(session, asset, receipt)

    @classmethod
    async def get_performance_history(
        cls,
        session: AsyncSession,
        receipt: PublishReceipt,
    ) -> list[PerformanceSnapshot]:
        """Query time-series sequence of all recorded snapshots for a published video."""
        asset = await cls._find_asset(session, receipt)
        if not asset:
            return []

        stmt = (
            select(AnalyticsProviderSnapshot)
            .where(AnalyticsProviderSnapshot.asset_id == asset.id)
            .order_by(AnalyticsProviderSnapshot.retrieval_timestamp.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        snapshots: list[PerformanceSnapshot] = []
        prev_snap: PerformanceSnapshot | None = None

        for row in rows:
            snap = await cls._reconstruct_snapshot(session, asset, receipt, row, None, prev_snap)
            snapshots.append(snap)
            prev_snap = snap

        return snapshots

    @classmethod
    async def get_performance_summary(
        cls,
        session: AsyncSession,
        receipt: PublishReceipt,
    ) -> PerformanceSummary | None:
        """Query deterministic summary of published performance."""
        latest = await cls.get_latest_performance(session, receipt)
        if not latest:
            return None

        history = await cls.get_performance_history(session, receipt)
        count = len(history)

        m = latest.metrics
        views = m.views or 0
        engagement = None
        if views > 0:
            total_eng = (m.likes or 0) + (m.comments or 0) + (m.shares or 0)
            engagement = round(total_eng / views, 4)

        sub_impact = None
        if m.subscribers_gained is not None:
            sub_impact = m.subscribers_gained - (m.subscribers_lost or 0)

        ret_50 = latest.derived_retention.retention_at_50_percent if latest.derived_retention else None

        age_hours = 0.0
        if receipt.published_at:
            pub_at = receipt.published_at if receipt.published_at.tzinfo else receipt.published_at.replace(tzinfo=UTC)
            age_hours = round(max(0.0, (latest.observed_at - pub_at).total_seconds() / 3600.0), 2)

        has_anom = any(len(s.anomalies) > 0 for s in history)

        return PerformanceSummary(
            external_media_id=receipt.external_media_id,
            receipt_id=receipt.receipt_id,
            provider=receipt.provider,
            latest_observed_at=latest.observed_at,
            freshness=latest.freshness,
            views=m.views,
            watch_time_seconds=m.watch_time_seconds,
            impressions_ctr=m.impressions_ctr,
            average_view_duration_seconds=m.average_view_duration_seconds,
            average_percentage_viewed=m.average_percentage_viewed,
            engagement_score=engagement,
            subscriber_impact=sub_impact,
            retention_at_50_percent=ret_50,
            snapshot_count=count,
            age_hours=age_hours,
            has_anomalies=has_anom,
        )

    # ── Isolated Channel Context ─────────────────────────────────────────────

    @classmethod
    async def capture_channel_context(
        cls,
        session: AsyncSession,
        channel_account_id: str,
        provider: PerformanceAnalyticsProvider,
    ) -> dict[str, Any] | None:
        """Fetch and return channel-level context. Strictly isolated from single-video metrics."""
        ctx = await provider.fetch_channel_context(channel_account_id)
        if ctx:
            logger.info("analytics.channel_context.captured", channel_account_id=channel_account_id)
        return ctx

    # ── Internal Storage & Helper Methods ────────────────────────────────────

    @classmethod
    async def _find_asset(
        cls,
        session: AsyncSession,
        receipt: PublishReceipt,
    ) -> AnalyticsAsset | None:
        stmt = select(AnalyticsAsset).where(AnalyticsAsset.publish_intent_id == receipt.publish_intent_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def _ensure_analytics_asset(
        cls,
        session: AsyncSession,
        receipt: PublishReceipt,
    ) -> AnalyticsAsset:
        asset = await cls._find_asset(session, receipt)
        if asset is not None:
            return asset

        # Look up parent PublishIntent for lineage details
        stmt_intent = select(PublishIntent).where(PublishIntent.id == receipt.publish_intent_id)
        res_intent = await session.execute(stmt_intent)
        intent = res_intent.scalar_one_or_none()

        if intent is None:
            raise ValueError(f"PublishIntent {receipt.publish_intent_id} not found in database.")

        pub_at = receipt.published_at or datetime.now(UTC)
        if pub_at.tzinfo is None:
            pub_at = pub_at.replace(tzinfo=UTC)

        asset = AnalyticsAsset(
            channel_id=intent.channel_id,
            platform_account_id=intent.platform_account_id,
            publish_intent_id=intent.id,
            publish_attempt_id=receipt.publish_attempt_id,
            media_artifact_id=intent.media_artifact_id,
            channel_dna_revision_id=intent.channel_dna_revision_id,
            provider=receipt.provider,
            provider_video_id=receipt.external_media_id,
            published_at=pub_at,
            asset_status=AnalyticsAssetStatus.ACTIVE.value,
            lifecycle_phase=AnalyticsLifecyclePhase.ACTIVE_HOURLY.value,
            next_poll_due_at=pub_at + timedelta(hours=1),
        )
        session.add(asset)
        await session.flush()
        return asset

    @classmethod
    async def _ensure_window(
        cls,
        session: AsyncSession,
        asset: AnalyticsAsset,
        now_utc: datetime,
    ) -> AnalyticsWindow:
        pub_at = asset.published_at if asset.published_at.tzinfo else asset.published_at.replace(tzinfo=UTC)
        stmt = select(AnalyticsWindow).where(
            AnalyticsWindow.asset_id == asset.id,
            AnalyticsWindow.window_type == WindowType.LIFETIME.value,
        )
        res = await session.execute(stmt)
        window = res.scalar_one_or_none()

        if window is None:
            window = AnalyticsWindow(
                asset_id=asset.id,
                window_type=WindowType.LIFETIME.value,
                provider_timezone="UTC",
                window_start_utc=pub_at,
                window_end_utc=now_utc,
                window_state=WindowState.PROVISIONAL.value,
            )
            session.add(window)
            await session.flush()
        else:
            window.window_end_utc = now_utc
            await session.flush()

        return window

    @classmethod
    async def _persist_metric_observations(
        cls,
        session: AsyncSession,
        asset: AnalyticsAsset,
        window: AnalyticsWindow,
        snapshot_row: AnalyticsProviderSnapshot,
        metrics: CanonicalMetrics,
        obs_time: datetime,
    ) -> None:
        metric_items = [
            ("views", metrics.views, float(metrics.views) if metrics.views is not None else None),
            ("impressions", metrics.impressions, float(metrics.impressions) if metrics.impressions is not None else None),
            ("watch_time_seconds", None, metrics.watch_time_seconds),
            ("average_view_duration_seconds", None, metrics.average_view_duration_seconds),
            ("average_percentage_viewed", None, metrics.average_percentage_viewed),
            ("impressions_ctr", None, metrics.impressions_ctr),
            ("likes", metrics.likes, float(metrics.likes) if metrics.likes is not None else None),
            ("comments", metrics.comments, float(metrics.comments) if metrics.comments is not None else None),
            ("shares", metrics.shares, float(metrics.shares) if metrics.shares is not None else None),
            ("subscribers_gained", metrics.subscribers_gained, float(metrics.subscribers_gained) if metrics.subscribers_gained is not None else None),
            ("subscribers_lost", metrics.subscribers_lost, float(metrics.subscribers_lost) if metrics.subscribers_lost is not None else None),
            ("net_subscribers", metrics.net_subscribers, float(metrics.net_subscribers) if metrics.net_subscribers is not None else None),
        ]

        for m_name, int_val, num_val in metric_items:
            quality = metrics.metric_qualities.get(m_name, MetricQuality.AVAILABLE.value)
            if int_val is None and num_val is None:
                quality = MetricQuality.UNKNOWN_MISSING.value

            stmt_ptr = (
                select(AnalyticsMetricLatestPointer)
                .where(
                    AnalyticsMetricLatestPointer.asset_id == asset.id,
                    AnalyticsMetricLatestPointer.window_id == window.id,
                    AnalyticsMetricLatestPointer.metric_name == m_name,
                    AnalyticsMetricLatestPointer.dimensions_hash == "",
                )
                .with_for_update()
            )
            res_ptr = await session.execute(stmt_ptr)
            pointer = res_ptr.scalar_one_or_none()

            next_rev = (pointer.current_revision_sequence + 1) if pointer else 1
            preceding_id = pointer.current_observation_id if pointer else None

            dedupe_key = compute_observation_dedupe_key(
                snapshot_row.id, window.id, m_name, "", 1
            )

            obs = AnalyticsMetricObservation(
                asset_id=asset.id,
                window_id=window.id,
                snapshot_id=snapshot_row.id,
                metric_name=m_name,
                classification=MetricClassification.PROVIDER_FACT.value,
                metric_quality=quality,
                numeric_value=num_val,
                integer_value=int_val,
                dimensions={},
                dimensions_hash="",
                normalization_version=1,
                observation_dedupe_key=dedupe_key,
                revision_sequence=next_rev,
                preceding_observation_id=preceding_id,
                observation_timestamp=obs_time,
            )
            session.add(obs)
            await session.flush()

            if pointer is None:
                pointer = AnalyticsMetricLatestPointer(
                    asset_id=asset.id,
                    window_id=window.id,
                    metric_name=m_name,
                    dimensions_hash="",
                    current_observation_id=obs.id,
                    current_revision_sequence=next_rev,
                )
                session.add(pointer)
            else:
                pointer.current_observation_id = obs.id
                pointer.current_revision_sequence = next_rev
            await session.flush()

    @classmethod
    async def _persist_computed_metrics(
        cls,
        session: AsyncSession,
        asset: AnalyticsAsset,
        window: AnalyticsWindow,
        deltas: DeltaVelocityMetrics,
        now_utc: datetime,
    ) -> None:
        computed_items = [
            ("views_delta", deltas.views_delta, "FORMULA_VIEWS_DELTA_V1"),
            ("views_per_hour", deltas.views_per_hour, "FORMULA_VIEWS_PER_HOUR_V1"),
            ("watch_time_delta_seconds", deltas.watch_time_delta_seconds, "FORMULA_WT_DELTA_V1"),
            ("watch_time_per_hour_seconds", deltas.watch_time_per_hour_seconds, "FORMULA_WT_PER_HOUR_V1"),
        ]

        for m_name, val, formula_id in computed_items:
            if val is not None:
                stmt_max_rev = select(func.max(AnalyticsComputedMetric.computation_revision_sequence)).where(
                    AnalyticsComputedMetric.window_id == window.id,
                    AnalyticsComputedMetric.metric_name == m_name,
                    AnalyticsComputedMetric.formula_version == "1",
                )
                res_max = await session.execute(stmt_max_rev)
                curr_max = res_max.scalar() or 0
                next_rev = curr_max + 1

                dedupe_key = hashlib.sha256(f"comp:{window.id}:{m_name}:{formula_id}:{now_utc.isoformat()}".encode("utf-8")).hexdigest()
                comp = AnalyticsComputedMetric(
                    asset_id=asset.id,
                    window_id=window.id,
                    metric_name=m_name,
                    classification=MetricClassification.DETERMINISTIC_DERIVED.value,
                    formula_identifier=formula_id,
                    formula_version="1",
                    source_observation_ids=[],
                    source_computed_metric_ids=[],
                    source_checksum=dedupe_key,
                    computation_dedupe_key=dedupe_key,
                    numeric_value=float(val),
                    metric_quality=MetricQuality.AVAILABLE.value,
                    computation_revision_sequence=next_rev,
                    computed_at=now_utc,
                )
                session.add(comp)
        await session.flush()

    @classmethod
    async def _get_latest_snapshot_for_asset(
        cls,
        session: AsyncSession,
        asset: AnalyticsAsset,
        receipt: PublishReceipt,
    ) -> PerformanceSnapshot | None:
        stmt = (
            select(AnalyticsProviderSnapshot)
            .where(AnalyticsProviderSnapshot.asset_id == asset.id)
            .order_by(AnalyticsProviderSnapshot.retrieval_timestamp.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        snap_row = res.scalar_one_or_none()
        if not snap_row:
            return None
        return await cls._reconstruct_snapshot(session, asset, receipt, snap_row, None)

    @classmethod
    async def _reconstruct_snapshot(
        cls,
        session: AsyncSession,
        asset: AnalyticsAsset,
        receipt: PublishReceipt,
        snap_row: AnalyticsProviderSnapshot,
        sync_checkpoint: str | None = None,
        previous_snapshot: PerformanceSnapshot | None = None,
    ) -> PerformanceSnapshot:
        # Load observations for this snapshot
        stmt_obs = select(AnalyticsMetricObservation).where(
            AnalyticsMetricObservation.snapshot_id == snap_row.id
        )
        obs_rows = (await session.execute(stmt_obs)).scalars().all()

        obs_dict: dict[str, Any] = {}
        qualities: dict[str, str] = {}
        for r in obs_rows:
            obs_dict[r.metric_name] = r.integer_value if r.integer_value is not None else r.numeric_value
            qualities[r.metric_name] = r.metric_quality

        metrics = CanonicalMetrics(
            views=obs_dict.get("views"),
            impressions=obs_dict.get("impressions"),
            watch_time_seconds=obs_dict.get("watch_time_seconds"),
            average_view_duration_seconds=obs_dict.get("average_view_duration_seconds"),
            average_percentage_viewed=obs_dict.get("average_percentage_viewed"),
            impressions_ctr=obs_dict.get("impressions_ctr"),
            likes=obs_dict.get("likes"),
            comments=obs_dict.get("comments"),
            shares=obs_dict.get("shares"),
            subscribers_gained=obs_dict.get("subscribers_gained"),
            subscribers_lost=obs_dict.get("subscribers_lost"),
            net_subscribers=obs_dict.get("net_subscribers"),
            metric_qualities=qualities,
        )

        anomalies: list[CounterAnomalyRecord] = []
        deltas: DeltaVelocityMetrics | None = None
        if previous_snapshot:
            anomalies = PerformanceNormalizer.detect_counter_anomalies(metrics, previous_snapshot.metrics)
            deltas = DeltaVelocityMetrics.compute(
                current=metrics,
                current_time=snap_row.retrieval_timestamp,
                previous=previous_snapshot.metrics,
                previous_time=previous_snapshot.observed_at,
            )

        freshness = DataFreshnessStatus.FRESH
        if asset.asset_status in (
            AnalyticsAssetStatus.PROVIDER_DELETED.value,
            AnalyticsAssetStatus.UNAVAILABLE.value,
        ):
            freshness = DataFreshnessStatus.UNAVAILABLE

        return PerformanceSnapshot(
            snapshot_id=uuid4(),
            receipt_id=receipt.receipt_id,
            provider=receipt.provider,
            external_media_id=receipt.external_media_id,
            observed_at=snap_row.retrieval_timestamp,
            freshness=freshness,
            metrics=metrics,
            deltas=deltas if deltas and deltas.is_valid else None,
            anomalies=anomalies,
            raw_snapshot_id=snap_row.id,
            raw_payload_checksum=snap_row.payload_checksum,
            lineage=receipt.lineage,
            sync_checkpoint=sync_checkpoint,
        )
