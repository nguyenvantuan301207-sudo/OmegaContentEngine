"""Signal provider abstractions for Trend, Historical Performance, Cost Feasibility, and Revenue.

Provides protocol interfaces and local/neutral default implementations for OMEGA-004.
"""

from __future__ import annotations

from typing import Protocol


class TrendSignalProvider(Protocol):
    """Protocol for trend score evaluation."""

    def get_trend_score(
        self, title: str, keywords: list[str], manual_score: float | None = None
    ) -> float: ...


class NullTrendSignalProvider:
    """Default neutral trend provider returning manual score or baseline 50.0."""

    def get_trend_score(
        self, title: str, keywords: list[str], manual_score: float | None = None
    ) -> float:
        if manual_score is not None:
            return float(manual_score)
        return 50.0


class HistoricalPerformanceProvider(Protocol):
    """Protocol for historical content performance baseline."""

    def get_performance_score(
        self, topic_title: str, keywords: list[str], manual_score: float | None = None
    ) -> float: ...


class NullPerformanceProvider:
    """Default neutral performance provider returning manual score or baseline 50.0."""

    def get_performance_score(
        self, topic_title: str, keywords: list[str], manual_score: float | None = None
    ) -> float:
        if manual_score is not None:
            return float(manual_score)
        return 50.0


class ProductionCostProvider(Protocol):
    """Protocol for cost efficiency / production feasibility scoring."""

    def get_cost_efficiency_score(self, title: str, manual_score: float | None = None) -> float: ...


class DefaultCostProvider:
    """Default cost feasibility provider returning manual score or baseline 70.0."""

    def get_cost_efficiency_score(self, title: str, manual_score: float | None = None) -> float:
        if manual_score is not None:
            return float(manual_score)
        return 70.0


class RevenuePotentialProvider(Protocol):
    """Protocol for commercial / revenue potential scoring."""

    def get_revenue_score(
        self, niche: str, keywords: list[str], manual_score: float | None = None
    ) -> float: ...


class DefaultRevenueProvider:
    """Default revenue potential provider returning manual score or baseline 50.0."""

    def get_revenue_score(
        self, niche: str, keywords: list[str], manual_score: float | None = None
    ) -> float:
        if manual_score is not None:
            return float(manual_score)
        return 50.0
