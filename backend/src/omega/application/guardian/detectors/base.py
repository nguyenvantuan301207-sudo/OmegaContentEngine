"""Base Detector abstraction for Guardian subsystem.

All detectors inherit from BaseDetector, declare their version, supported
checkpoints, and failure policy, and execute using read-only isolated sessions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.guardian import (
    CheckTriggerType,
    DetectorFailurePolicy,
    GuardianCheckpoint,
    GuardianFindingData,
)


class GuardianEvaluationContext(BaseModel):
    """Contextual payload provided to detectors for evaluation."""

    mission_id: UUID
    checkpoint: GuardianCheckpoint
    trigger_type: CheckTriggerType
    task_id: UUID | None = None
    production_request_id: UUID | None = None
    media_artifact_id: UUID | None = None
    guardian_epoch: int = 1
    rules_config: dict[str, Any] = Field(default_factory=dict)
    diagnostic_context: dict[str, Any] = Field(default_factory=dict)


class BaseDetector(ABC):
    """Abstract base class for all Guardian detectors."""

    detector_type: str
    detector_version: str = "1.0.0"
    supported_checkpoints: set[GuardianCheckpoint]
    failure_policy: DetectorFailurePolicy

    @abstractmethod
    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        """Execute detector logic using isolated read sessions and return findings."""
        pass
