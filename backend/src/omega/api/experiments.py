"""Read-only REST API endpoints for P25-C Experiments & Causal Attribution."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from omega.application.experiments.query_service import ExperimentQueryService
from omega.logging import get_logger

logger = get_logger(service="omega-experiments-api")

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


class ExperimentResponse(BaseModel):
    experiment_id: UUID
    channel_id: UUID
    experiment_type: str
    hypothesis: str
    primary_metric: str
    status: str
    control_variant_id: UUID
    treatment_variant_ids: list[UUID]


class AttributionResultResponse(BaseModel):
    experiment_id: UUID
    primary_metric: str
    classification: str
    data_maturity: str
    control_value: float | None = None
    treatment_value: float | None = None
    absolute_difference: float | None = None
    relative_lift: float | None = None
    sample_basis: dict[str, int] = {}
    findings: list[str] = []


@router.get("/{experiment_id}")
async def get_experiment(experiment_id: UUID) -> ExperimentResponse:
    """Read-only inspection of an experiment definition."""
    exp = ExperimentQueryService.get_experiment(experiment_id)
    if not exp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Experiment {experiment_id} not found",
        )

    return ExperimentResponse(
        experiment_id=exp.experiment_id,
        channel_id=exp.channel_id,
        experiment_type=exp.experiment_type.value,
        hypothesis=exp.hypothesis,
        primary_metric=exp.primary_metric,
        status=exp.status.value,
        control_variant_id=exp.control_variant_id,
        treatment_variant_ids=exp.treatment_variant_ids,
    )


@router.get("/{experiment_id}/result")
async def get_experiment_result(experiment_id: UUID) -> AttributionResultResponse:
    """Read-only retrieval of causal attribution result."""
    res = ExperimentQueryService.get_experiment_result(experiment_id)
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attribution result found for experiment {experiment_id}",
        )

    return AttributionResultResponse(
        experiment_id=res.experiment_id,
        primary_metric=res.primary_metric,
        classification=res.classification.value,
        data_maturity=res.data_maturity.value,
        control_value=res.control_value,
        treatment_value=res.treatment_value,
        absolute_difference=res.absolute_difference,
        relative_lift=res.relative_lift,
        sample_basis=res.sample_basis,
        findings=res.findings,
    )


@router.get("/{experiment_id}/comparison")
async def get_variant_comparison(experiment_id: UUID) -> dict[str, Any]:
    """Read-only retrieval of variant comparison."""
    comp = ExperimentQueryService.get_variant_comparison(experiment_id)
    if not comp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No variant comparison found for experiment {experiment_id}",
        )
    return comp
