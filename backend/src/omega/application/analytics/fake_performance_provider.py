"""Deterministic Fake Provider for P25-B Performance Analytics.

Supports deterministic test simulations with zero network operations and zero real provider mutation.
Simulates:
- Initial snapshot (T0)
- Metric growth over time (T1)
- No-data state
- Partial metric availability
- Provider lag (delayed analytics)
- Transient fetch failures with retry recovery
- Terminal authorization / permission failures
- Counter resets and anomalies
- Monotonic retention curves
- Isolated channel context
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from omega.application.analytics.performance_provider import (
    AnalyticsErrorClassification,
    PerformanceAnalyticsProvider,
    RetryableAnalyticsError,
    TerminalAnalyticsError,
)
from omega.domain.performance_analytics import (
    RawProviderMetricsPayload,
    RetentionCurve,
    RetentionPoint,
)


class FakePerformanceAnalyticsProvider(PerformanceAnalyticsProvider):
    """Deterministic, read-only analytics provider for testing and canaries."""

    def __init__(self, provider_name: str = "FAKE_YOUTUBE") -> None:
        self.provider_name = provider_name
        self._media_store: dict[str, list[RawProviderMetricsPayload]] = {}
        self._retention_store: dict[str, RetentionCurve] = {}
        self._channel_store: dict[str, dict[str, Any]] = {}
        self._availability_store: dict[str, str] = {}
        self._transient_failure_budget: dict[str, int] = {}
        self._terminal_failure_media: set[str] = set()
        self._lagged_media: set[str] = set()
        self.fetch_call_count: int = 0
        self.retry_recovery_count: int = 0

    # ── Simulation Configuration Helpers ─────────────────────────────────────

    def configure_timeline_metrics(
        self,
        external_media_id: str,
        snapshots: list[tuple[datetime, dict[str, Any]]],
    ) -> None:
        """Register a series of time-ordered metric payloads for an external media ID."""
        payloads: list[RawProviderMetricsPayload] = []
        for ts, data in snapshots:
            payloads.append(
                RawProviderMetricsPayload(
                    provider=self.provider_name,
                    external_media_id=external_media_id,
                    provider_timestamp=ts,
                    observed_at=ts,
                    raw_data=data,
                    schema_version="1.0",
                    ingestion_provenance={"source": "fake_timeline_simulation"},
                )
            )
        self._media_store[external_media_id] = payloads
        self._availability_store[external_media_id] = "AVAILABLE"

    def configure_retention_curve(
        self,
        external_media_id: str,
        points: list[tuple[float, float]],
    ) -> None:
        """Register a retention curve of (relative_position, retention_ratio) pairs."""
        curve = RetentionCurve(
            points=[RetentionPoint(relative_position=p, retention_ratio=r) for p, r in points]
        )
        self._retention_store[external_media_id] = curve

    def configure_transient_failure(self, external_media_id: str, failures_before_success: int) -> None:
        """Configure transient failures that succeed after a certain number of attempts."""
        self._transient_failure_budget[external_media_id] = failures_before_success

    def configure_terminal_failure(self, external_media_id: str) -> None:
        """Configure permanent terminal failure (e.g. invalid credentials/permissions)."""
        self._terminal_failure_media.add(external_media_id)

    def configure_lagged_media(self, external_media_id: str) -> None:
        """Mark media as experiencing provider analytics processing lag."""
        self._lagged_media.add(external_media_id)

    def configure_availability(self, external_media_id: str, status: str) -> None:
        """Set media availability status: AVAILABLE, PRIVATE, DELETED, or UNAVAILABLE."""
        self._availability_store[external_media_id] = status

    def configure_channel_context(self, channel_id: str, context: dict[str, Any]) -> None:
        """Register isolated channel-level aggregate metrics."""
        self._channel_store[channel_id] = context

    # ── PerformanceAnalyticsProvider Implementation ──────────────────────────

    async def fetch_media_metrics(
        self,
        external_media_id: str,
        as_of: datetime | None = None,
    ) -> RawProviderMetricsPayload:
        self.fetch_call_count += 1

        # Check terminal failure
        if external_media_id in self._terminal_failure_media:
            raise TerminalAnalyticsError(
                message=f"Terminal authorization failure for media {external_media_id}",
                classification=AnalyticsErrorClassification.INVALID_CREDENTIALS,
            )

        # Check transient failure budget
        if self._transient_failure_budget.get(external_media_id, 0) > 0:
            self._transient_failure_budget[external_media_id] -= 1
            self.retry_recovery_count += 1
            raise RetryableAnalyticsError(
                message=f"Transient provider 503 error for media {external_media_id}",
                classification=AnalyticsErrorClassification.PROVIDER_5XX,
                retry_after_seconds=1,
            )

        # Check provider lag
        if external_media_id in self._lagged_media:
            raise RetryableAnalyticsError(
                message=f"Provider analytics pipeline delayed for media {external_media_id}",
                classification=AnalyticsErrorClassification.TEMPORARY_LAG,
                retry_after_seconds=60,
            )

        # Check media presence
        timeline = self._media_store.get(external_media_id)
        if not timeline:
            raise TerminalAnalyticsError(
                message=f"No provider data found for external media {external_media_id}",
                classification=AnalyticsErrorClassification.INVALID_MEDIA_ID,
            )

        if as_of is None:
            return timeline[-1]

        # Find latest snapshot at or before as_of
        as_of_utc = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
        matching = [s for s in timeline if s.provider_timestamp <= as_of_utc]
        if not matching:
            return timeline[0]
        return matching[-1]

    async def fetch_retention_curve(
        self,
        external_media_id: str,
    ) -> RetentionCurve | None:
        return self._retention_store.get(external_media_id)

    async def fetch_channel_context(
        self,
        channel_account_id: str,
    ) -> dict[str, Any] | None:
        return self._channel_store.get(channel_account_id)

    async def check_media_availability(
        self,
        external_media_id: str,
    ) -> str:
        if external_media_id in self._terminal_failure_media:
            return "UNAVAILABLE"
        return self._availability_store.get(external_media_id, "AVAILABLE")
