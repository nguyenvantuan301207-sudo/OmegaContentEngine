"""Observational route health evaluator based on versioned policy parameters.

Computes sample-based health classifications without mutating control state.
"""

from __future__ import annotations

from typing import Any

from omega.domain.network import HealthState, NetworkProbeResultData, ProbeStatus
from omega.logging import get_logger

logger = get_logger(service="omega-health-evaluator")


class HealthEvaluator:
    """Evaluates observational health of network routes from probe evidence."""

    @staticmethod
    def evaluate_health_from_probes(
        probe_runs: list[NetworkProbeResultData],
        policy_config: dict[str, Any],
    ) -> tuple[HealthState, str]:
        """Compute HealthState and diagnostic reason from probe results and policy rules."""
        health_cfg = policy_config.get("health_evaluation", {})
        min_samples = health_cfg.get("min_samples", 1)
        max_degraded_latency = health_cfg.get("max_degraded_latency_ms", 2000.0)
        degraded_min_success_ratio = health_cfg.get("degraded_min_success_ratio", 0.80)

        if not probe_runs or len(probe_runs) < min_samples:
            return (
                HealthState.UNKNOWN,
                f"Insufficient probe samples ({len(probe_runs)} < {min_samples}).",
            )

        # Check for probe failures and success ratio
        failed_probes = [p for p in probe_runs if p.status != ProbeStatus.SUCCESS]
        success_ratio = (len(probe_runs) - len(failed_probes)) / len(probe_runs)
        if failed_probes and success_ratio < degraded_min_success_ratio:
            first_fail = failed_probes[0]
            err_msg = first_fail.error_message or first_fail.error_category or "Probe failed"
            return (
                HealthState.UNHEALTHY,
                f"Critical probe failure in {first_fail.probe_type.value}: {err_msg}",
            )

        # Evaluate Latency
        latencies = [p.latency_ms for p in probe_runs if p.status == ProbeStatus.SUCCESS]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        if avg_latency > max_degraded_latency:
            return (
                HealthState.DEGRADED,
                f"Elevated latency ({avg_latency:.1f}ms > {max_degraded_latency:.1f}ms).",
            )

        return HealthState.HEALTHY, f"All probes healthy. Average latency: {avg_latency:.1f}ms."
