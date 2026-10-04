"""Deterministic Fake Provider for P25-C Controlled Experimentation.

Supports deterministic test simulations without network calls and without real provider mutation.
Simulates:
- Configurable variant metrics (CTR, impressions, watch time)
- Provider data lag (delayed analytics)
- Unequal and small sample sizes
- Inconclusive results
- Treatment lift
- Control win
- Exposure counts
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from omega.application.experiments.experiment_provider import ExperimentProvider


class FakeExperimentProvider(ExperimentProvider):
    """Deterministic, read-only experiment provider for tests and canaries."""

    def __init__(self, provider_name: str = "FAKE_EXPERIMENT_PROVIDER") -> None:
        self.provider_name = provider_name
        self._variant_metrics: dict[tuple[UUID, UUID], dict[str, Any]] = {}
        self._exposure_counts: dict[UUID, dict[UUID, int]] = {}
        self._freshness_status: dict[UUID, str] = {}
        self.fetch_count: int = 0

    # ── Simulation Configuration Helpers ─────────────────────────────────────

    def configure_variant_metrics(
        self,
        experiment_id: UUID,
        variant_id: UUID,
        metrics: dict[str, Any],
        sample_count: int = 1000,
    ) -> None:
        """Register deterministic metrics and exposure count for a specific variant."""
        self._variant_metrics[(experiment_id, variant_id)] = metrics
        if experiment_id not in self._exposure_counts:
            self._exposure_counts[experiment_id] = {}
        self._exposure_counts[experiment_id][variant_id] = sample_count
        if experiment_id not in self._freshness_status:
            self._freshness_status[experiment_id] = "FRESH"

    def configure_freshness(self, experiment_id: UUID, status: str) -> None:
        """Set telemetry freshness status (e.g. FRESH, DELAYED, STALE, UNAVAILABLE)."""
        self._freshness_status[experiment_id] = status

    # ── ExperimentProvider Interface Implementation ──────────────────────────

    async def fetch_variant_performance(
        self,
        experiment_id: UUID,
        variant_id: UUID,
        window_hours: int = 24,
    ) -> dict[str, Any]:
        self.fetch_count += 1
        key = (experiment_id, variant_id)
        if key not in self._variant_metrics:
            return {}
        return self._variant_metrics[key]

    async def fetch_exposure_counts(
        self,
        experiment_id: UUID,
    ) -> dict[UUID, int]:
        return self._exposure_counts.get(experiment_id, {})

    async def check_experiment_freshness(
        self,
        experiment_id: UUID,
    ) -> str:
        return self._freshness_status.get(experiment_id, "FRESH")
