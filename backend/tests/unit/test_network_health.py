"""Unit tests for OMEGA-009 observational health evaluator."""

from omega.application.network.health_evaluator import HealthEvaluator
from omega.domain.network import (
    HealthState,
    NetworkFailureCategory,
    NetworkProbeResultData,
    ProbeStatus,
    ProbeType,
)

SAMPLE_POLICY_CONFIG = {
    "health_evaluation": {
        "min_samples": 2,
        "max_degraded_latency_ms": 1500.0,
        "degraded_min_success_ratio": 0.80,
    }
}


def test_health_insufficient_samples():
    """Test HealthState.UNKNOWN when samples < min_samples."""
    probes = [
        NetworkProbeResultData(
            probe_type=ProbeType.DNS_RESOLUTION,
            status=ProbeStatus.SUCCESS,
            latency_ms=20.0,
        )
    ]
    state, reason = HealthEvaluator.evaluate_health_from_probes(probes, SAMPLE_POLICY_CONFIG)
    assert state == HealthState.UNKNOWN
    assert "Insufficient probe samples" in reason


def test_health_all_probes_healthy():
    """Test HealthState.HEALTHY when all probes succeed with low latency."""
    probes = [
        NetworkProbeResultData(
            probe_type=ProbeType.DNS_RESOLUTION,
            status=ProbeStatus.SUCCESS,
            latency_ms=15.0,
        ),
        NetworkProbeResultData(
            probe_type=ProbeType.TCP_CONNECT,
            status=ProbeStatus.SUCCESS,
            latency_ms=30.0,
        ),
    ]
    state, reason = HealthEvaluator.evaluate_health_from_probes(probes, SAMPLE_POLICY_CONFIG)
    assert state == HealthState.HEALTHY
    assert "All probes healthy" in reason


def test_health_degraded_latency():
    """Test HealthState.DEGRADED when average latency exceeds threshold."""
    probes = [
        NetworkProbeResultData(
            probe_type=ProbeType.DNS_RESOLUTION,
            status=ProbeStatus.SUCCESS,
            latency_ms=1600.0,
        ),
        NetworkProbeResultData(
            probe_type=ProbeType.TCP_CONNECT,
            status=ProbeStatus.SUCCESS,
            latency_ms=1800.0,
        ),
    ]
    state, reason = HealthEvaluator.evaluate_health_from_probes(probes, SAMPLE_POLICY_CONFIG)
    assert state == HealthState.DEGRADED
    assert "Elevated latency" in reason


def test_health_unhealthy_on_critical_failure():
    """Test HealthState.UNHEALTHY on any critical probe failure."""
    probes = [
        NetworkProbeResultData(
            probe_type=ProbeType.DNS_RESOLUTION,
            status=ProbeStatus.SUCCESS,
            latency_ms=20.0,
        ),
        NetworkProbeResultData(
            probe_type=ProbeType.TCP_CONNECT,
            status=ProbeStatus.FAILURE,
            latency_ms=50.0,
            error_category=NetworkFailureCategory.CONNECT_REFUSED,
            error_message="Connection refused by remote host",
        ),
    ]
    state, reason = HealthEvaluator.evaluate_health_from_probes(probes, SAMPLE_POLICY_CONFIG)
    assert state == HealthState.UNHEALTHY
    assert "Critical probe failure" in reason
