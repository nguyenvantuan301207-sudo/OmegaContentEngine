"""Query service for P25-C controlled experiments and attribution results.

Provides read-only access to experiments, variant comparisons, and historical results.
No provider mutations or creative adjustments are ever triggered.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from omega.application.experiments.attribution_service import AttributionService
from omega.domain.experimentation import AttributionResult, ExperimentDefinition


class ExperimentQueryService:
    """Read-only seam for experiment inspection."""

    @classmethod
    def get_experiment(cls, experiment_id: UUID) -> ExperimentDefinition | None:
        """Fetch experiment definition by ID."""
        return AttributionService.get_experiment(experiment_id)

    @classmethod
    def list_experiments(cls, channel_id: UUID) -> list[ExperimentDefinition]:
        """List all experiments for a channel."""
        return AttributionService.list_experiments_for_channel(channel_id)

    @classmethod
    def get_experiment_result(cls, experiment_id: UUID) -> AttributionResult | None:
        """Fetch final or interim attribution result for an experiment."""
        return AttributionService.get_experiment_result(experiment_id)

    @classmethod
    def get_variant_comparison(cls, experiment_id: UUID) -> dict[str, Any] | None:
        """Fetch structured comparison between control and treatment variants."""
        res = AttributionService.get_experiment_result(experiment_id)
        if not res:
            return None

        return {
            "experiment_id": str(res.experiment_id),
            "primary_metric": res.primary_metric,
            "control_variant_id": str(res.control_variant_id),
            "treatment_variant_id": str(res.treatment_variant_id),
            "control_value": res.control_value,
            "treatment_value": res.treatment_value,
            "absolute_difference": res.absolute_difference,
            "relative_lift": res.relative_lift,
            "classification": res.classification.value,
            "data_maturity": res.data_maturity.value,
            "sample_basis": res.sample_basis,
            "findings": res.findings,
            "evaluated_at": res.evaluated_at.isoformat(),
        }
