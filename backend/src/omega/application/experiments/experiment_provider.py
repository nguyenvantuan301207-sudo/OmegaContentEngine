"""Provider Experiment Abstraction for P25-C.

Read-only interface wrapping platform-native or aggregate experiment telemetry.
No domain service calls vendor SDK directly.
"""

from __future__ import annotations

import abc
from typing import Any
from uuid import UUID


class ExperimentProvider(abc.ABC):
    """Narrow read-only interface for experiment telemetry."""

    @abc.abstractmethod
    async def fetch_variant_performance(
        self,
        experiment_id: UUID,
        variant_id: UUID,
        window_hours: int = 24,
    ) -> dict[str, Any]:
        """Fetch aggregate performance facts for an individual experiment variant."""
        pass

    @abc.abstractmethod
    async def fetch_exposure_counts(
        self,
        experiment_id: UUID,
    ) -> dict[UUID, int]:
        """Fetch total recorded exposures per variant ID."""
        pass

    @abc.abstractmethod
    async def check_experiment_freshness(
        self,
        experiment_id: UUID,
    ) -> str:
        """Check provider telemetry processing status: FRESH, DELAYED, STALE, UNAVAILABLE."""
        pass
