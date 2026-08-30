"""Quota tracking and management service for OMEGA-012 Analytics Engine.

Enforces bucket/method-aware quota limits with dynamic Pacific midnight reset.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.analytics import compute_quota_reset_instants
from omega.infrastructure.models import AnalyticsQuotaBucket


class QuotaService:
    """Manages provider quota buckets and internal budget thresholds."""

    @staticmethod
    async def get_or_create_bucket(
        session: AsyncSession,
        provider: str,
        quota_bucket: str,
        method: str,
        configured_daily_limit: int | None = None,
        internal_budget_limit: int | None = None,
    ) -> AnalyticsQuotaBucket:
        """Get or initialize a quota bucket tracking row."""
        stmt = select(AnalyticsQuotaBucket).where(
            AnalyticsQuotaBucket.provider == provider,
            AnalyticsQuotaBucket.quota_bucket == quota_bucket,
            AnalyticsQuotaBucket.method == method,
        )
        res = await session.execute(stmt)
        bucket = res.scalar_one_or_none()

        now_utc = datetime.now(UTC)
        last_reset_utc, next_reset_utc = compute_quota_reset_instants(now_utc)

        if bucket is None:
            bucket = AnalyticsQuotaBucket(
                provider=provider,
                quota_bucket=quota_bucket,
                method=method,
                configured_daily_limit=configured_daily_limit,
                internal_budget_limit=internal_budget_limit,
                provider_authoritative_usage=None,
                estimated_internal_units=0,
                provider_timezone="America/Los_Angeles",
                last_reset_at_utc=last_reset_utc,
                next_reset_at_utc=next_reset_utc,
                evidence_source="CONFIGURED",
                last_verified_at=now_utc,
            )
            session.add(bucket)
            await session.flush()
        else:
            # Check if reset boundary has passed
            if now_utc >= bucket.next_reset_at_utc:
                bucket.estimated_internal_units = 0
                bucket.provider_authoritative_usage = None
                bucket.last_reset_at_utc = last_reset_utc
                bucket.next_reset_at_utc = next_reset_utc
                await session.flush()

        return bucket

    @staticmethod
    async def record_usage(
        session: AsyncSession,
        provider: str,
        quota_bucket: str,
        method: str,
        units: int,
        authoritative_usage: int | None = None,
    ) -> AnalyticsQuotaBucket:
        """Record estimated units and optional authoritative provider usage."""
        bucket = await QuotaService.get_or_create_bucket(session, provider, quota_bucket, method)
        bucket.estimated_internal_units += units
        if authoritative_usage is not None:
            bucket.provider_authoritative_usage = authoritative_usage
            bucket.last_verified_at = datetime.now(UTC)
            bucket.evidence_source = "PROVIDER_API_HEADER"
        await session.flush()
        return bucket

    @staticmethod
    def get_utilization_percent(bucket: AnalyticsQuotaBucket) -> float | None:
        """Return percentage utilization if configured limit is known, else None.

        Never fabricates percentage utilization if limit is unknown.
        """
        if bucket.configured_daily_limit is None or bucket.configured_daily_limit <= 0:
            return None

        # Prioritize authoritative usage if known, else internal estimate
        usage = (
            bucket.provider_authoritative_usage
            if bucket.provider_authoritative_usage is not None
            else bucket.estimated_internal_units
        )
        return (usage / bucket.configured_daily_limit) * 100.0

    @staticmethod
    def is_quota_available(bucket: AnalyticsQuotaBucket, requested_units: int = 1) -> bool:
        """Check if quota or internal budget permits making a request."""
        now_utc = datetime.now(UTC)
        if now_utc >= bucket.next_reset_at_utc:
            return True

        # Check internal budget if set
        if (
            bucket.internal_budget_limit is not None
            and bucket.estimated_internal_units + requested_units > bucket.internal_budget_limit
        ):
            return False

        # Check configured daily limit if set
        if bucket.configured_daily_limit is not None:
            usage = (
                bucket.provider_authoritative_usage
                if bucket.provider_authoritative_usage is not None
                else bucket.estimated_internal_units
            )
            if usage + requested_units > bucket.configured_daily_limit:
                return False

        return True
