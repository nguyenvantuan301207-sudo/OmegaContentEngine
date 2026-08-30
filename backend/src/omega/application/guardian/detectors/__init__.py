"""Guardian Detectors package."""

from omega.application.guardian.detectors.base import BaseDetector, GuardianEvaluationContext
from omega.application.guardian.detectors.content_quality import ContentQualityDetector
from omega.application.guardian.detectors.cost_anomaly import CostAnomalyDetector
from omega.application.guardian.detectors.critical_capacity import CriticalCapacityDetector
from omega.application.guardian.detectors.hard_budget import HardBudgetDetector
from omega.application.guardian.detectors.media_integrity import MediaIntegrityDetector
from omega.application.guardian.detectors.pipeline_anomaly import PipelineAnomalyDetector
from omega.application.guardian.detectors.policy_risk import PolicyRiskDetector
from omega.application.guardian.detectors.system_health import SystemHealthTelemetryDetector

ALL_DETECTORS: list[BaseDetector] = [
    HardBudgetDetector(),
    CostAnomalyDetector(),
    CriticalCapacityDetector(),
    SystemHealthTelemetryDetector(),
    ContentQualityDetector(),
    PolicyRiskDetector(),
    MediaIntegrityDetector(),
    PipelineAnomalyDetector(),
]

__all__ = [
    "ALL_DETECTORS",
    "BaseDetector",
    "ContentQualityDetector",
    "CostAnomalyDetector",
    "CriticalCapacityDetector",
    "GuardianEvaluationContext",
    "HardBudgetDetector",
    "MediaIntegrityDetector",
    "PipelineAnomalyDetector",
    "PolicyRiskDetector",
    "SystemHealthTelemetryDetector",
]
