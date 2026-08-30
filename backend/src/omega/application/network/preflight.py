"""Authoritative Network Preflight Service with 3-Phase Decoupled Execution.

Implements short DB transaction boundaries, connection-level SSRF defense,
route configuration fencing, authoritative circuit breaker updates, and egress permit issuance.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.network.circuit_breaker import RouteCircuitBreaker
from omega.application.network.egress_permit import EgressPermitManager
from omega.application.network.health_evaluator import HealthEvaluator
from omega.application.network.probe_engine import ProbeEngine
from omega.application.network.route_selector import RouteSelector
from omega.domain.network import (
    DestinationRule,
    HealthState,
    NetworkAction,
    NetworkCheckResponse,
    NetworkDecisionResponse,
    NetworkEgressPermit,
    NetworkFailureCategory,
    NetworkPolicyStatus,
    NetworkPreflightRequest,
    NetworkProbeResultData,
    ProbeStatus,
    ProbeType,
    RouteType,
    ServiceCategory,
    compute_config_checksum,
    compute_preflight_idempotency_key,
    normalize_destination,
)
from omega.infrastructure.models import (
    NetworkCheck,
    NetworkDecision,
    NetworkPolicy,
    NetworkProbeRun,
    NetworkRoute,
)
from omega.logging import get_logger

logger = get_logger(service="omega-network-preflight")


DEFAULT_POLICY_CONFIG: dict[str, Any] = {
    "health_evaluation": {
        "min_samples": 1,
        "window_size": 10,
        "max_probe_staleness_seconds": 60,
        "unhealthy_consecutive_failure_threshold": 3,
        "degraded_min_success_ratio": 0.80,
        "max_degraded_latency_ms": 2000.0,
    },
    "circuit_breaker": {
        "failure_threshold": 5,
        "cooldown_seconds": 30,
        "half_open_success_threshold": 2,
    },
    "destination_rules": [
        {
            "scheme": "https",
            "exact_host": None,
            "domain_suffix": None,
            "allowed_ports": [443, 80],
            "path_prefix": None,
        }
    ],
}


class NetworkPreflightService:
    """Executes authoritative network preflight checks."""

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self.session_factory = session_factory

    async def preflight(
        self,
        request: NetworkPreflightRequest,
    ) -> tuple[NetworkCheckResponse, NetworkEgressPermit | None]:
        """Execute authoritative preflight check and return result + optional egress permit."""
        canon_dest = normalize_destination(request.destination_url)
        now = datetime.now(UTC)

        # ── TX 1: Resolve Pinned State, Idempotency & Claim Check ──
        async with self.session_factory() as session:
            # 1. Resolve Active Policy
            policy_stmt = (
                select(NetworkPolicy)
                .where(
                    NetworkPolicy.service_category == request.service_category.value,
                    NetworkPolicy.status == NetworkPolicyStatus.ACTIVE.value,
                    NetworkPolicy.effective_at <= now,
                )
                .order_by(NetworkPolicy.effective_at.desc(), NetworkPolicy.version.desc())
            )
            pol_res = await session.execute(policy_stmt)
            policy = pol_res.scalars().first()

            if not policy:
                # Initialize default active policy if none exists
                policy_id = uuid4()
                pol_checksum = compute_config_checksum(DEFAULT_POLICY_CONFIG)
                policy = NetworkPolicy(
                    id=policy_id,
                    service_category=request.service_category.value,
                    version="1.0.0",
                    status=NetworkPolicyStatus.ACTIVE.value,
                    effective_at=now,
                    policy_config=DEFAULT_POLICY_CONFIG,
                    checksum=pol_checksum,
                    created_at=now,
                )
                session.add(policy)
                await session.flush()

            # 2. Validate Destination Rules in Policy
            rules_data = policy.policy_config.get("destination_rules", [])
            destination_allowed = True
            if rules_data:
                rules = [DestinationRule(**r) for r in rules_data]
                destination_allowed = any(r.matches(canon_dest) for r in rules)

            # 3. Resolve Candidate Route
            route = await RouteSelector.select_route_for_service(
                session=session,
                service_category=request.service_category,
            )

            if not route:
                logger.warning(
                    "Preflight blocked: no healthy route available",
                    category=request.service_category.value,
                )
                # Create synthetic check and return WAITING_NETWORK
                check_id = uuid4()
                idempotency_key = compute_preflight_idempotency_key(
                    mission_id=request.mission_id,
                    task_id=request.task_id,
                    service_category=request.service_category,
                    canonical_destination=canon_dest.canonical_string,
                    route_id=uuid4(),
                    route_config_version=1,
                    route_config_checksum="none",
                    policy_id=policy.id,
                    policy_version=policy.version,
                    policy_checksum=policy.checksum,
                    caller_key=request.caller_key,
                )
                synthetic_resp = NetworkCheckResponse(
                    id=check_id,
                    mission_id=request.mission_id,
                    task_id=request.task_id,
                    route_id=uuid4(),
                    route_config_version=1,
                    route_config_checksum="none",
                    policy_id=policy.id,
                    policy_version=policy.version,
                    policy_checksum=policy.checksum,
                    service_category=request.service_category,
                    canonical_destination=canon_dest.canonical_string,
                    idempotency_key=idempotency_key,
                    status="COMPLETED",
                    started_at=now,
                    completed_at=now,
                    created_at=now,
                    decision=NetworkDecisionResponse(
                        id=uuid4(),
                        network_check_id=check_id,
                        action=NetworkAction.WAITING_NETWORK,
                        resulting_health_state=HealthState.UNHEALTHY,
                        reason="No active or healthy network route available for service category.",
                        actor="NETWORK_MANAGER",
                        created_at=now,
                    ),
                    probe_runs=[],
                )
                return synthetic_resp, None

            # 4. Compute Idempotency Key
            idempotency_key = compute_preflight_idempotency_key(
                mission_id=request.mission_id,
                task_id=request.task_id,
                service_category=request.service_category,
                canonical_destination=canon_dest.canonical_string,
                route_id=route.id,
                route_config_version=route.config_version,
                route_config_checksum=route.config_checksum,
                policy_id=policy.id,
                policy_version=policy.version,
                policy_checksum=policy.checksum,
                caller_key=request.caller_key,
            )

            # Check existing check by idempotency key
            if not request.force_refresh:
                exist_stmt = (
                    select(NetworkCheck)
                    .where(NetworkCheck.idempotency_key == idempotency_key)
                    .options(
                        selectinload(NetworkCheck.decision),
                        selectinload(NetworkCheck.probe_runs),
                    )
                )
                exist_res = await session.execute(exist_stmt)
                existing_check = exist_res.scalar_one_or_none()
                if (
                    existing_check
                    and existing_check.status == "COMPLETED"
                    and existing_check.decision
                ):
                    logger.info(
                        "Returning existing authoritative preflight decision",
                        check_id=str(existing_check.id),
                    )
                    resp = self._build_check_response(existing_check)
                    permit = None
                    if existing_check.decision.action in (
                        NetworkAction.ALLOW.value,
                        NetworkAction.ALLOW_DEGRADED.value,
                    ):
                        permit = EgressPermitManager.issue_permit(
                            network_check_id=existing_check.id,
                            route_id=existing_check.route_id,
                            route_config_version=existing_check.route_config_version,
                            canonical_destination=existing_check.canonical_destination,
                            service_category=request.service_category,
                        )
                    return resp, permit

            # 5. Insert IN_PROGRESS Check
            check_id = uuid4()
            network_check = NetworkCheck(
                id=check_id,
                mission_id=request.mission_id,
                task_id=request.task_id,
                route_id=route.id,
                route_config_version=route.config_version,
                route_config_checksum=route.config_checksum,
                policy_id=policy.id,
                policy_version=policy.version,
                policy_checksum=policy.checksum,
                service_category=request.service_category.value,
                canonical_destination=canon_dest.canonical_string,
                idempotency_key=idempotency_key,
                status="IN_PROGRESS",
                started_at=now,
                created_at=now,
            )
            session.add(network_check)
            await session.commit()

            pinned_route_id = route.id
            pinned_route_type = RouteType(route.route_type)
            pinned_endpoint_url = route.endpoint_url
            pinned_connect_timeout = route.connect_timeout_seconds
            pinned_read_timeout = route.read_timeout_seconds
            pinned_policy_config = policy.policy_config

        # ── Non-TX: Execute Probes ──
        if not destination_allowed:
            probe_results = [
                NetworkProbeResultData(
                    probe_type=ProbeType.HTTPS_HEAD,
                    status=ProbeStatus.BLOCKED_POLICY,
                    latency_ms=0.0,
                    evidence={"destination": canon_dest.canonical_string},
                    error_category=NetworkFailureCategory.POLICY_REJECTED,
                    error_message="Destination does not match active NetworkPolicy destination rules.",
                )
            ]
        else:
            probe_results = await ProbeEngine.run_probes(
                canonical_dest=canon_dest,
                route_type=pinned_route_type,
                proxy_endpoint_url=pinned_endpoint_url,
                connect_timeout=pinned_connect_timeout,
                read_timeout=pinned_read_timeout,
            )

        # ── TX 2: Persist Probe Runs ──
        async with self.session_factory() as session:
            for p in probe_results:
                session.add(
                    NetworkProbeRun(
                        id=uuid4(),
                        check_id=check_id,
                        probe_type=p.probe_type.value,
                        status=p.status.value,
                        latency_ms=p.latency_ms,
                        evidence=p.evidence,
                        created_at=datetime.now(UTC),
                    )
                )
            await session.commit()

        # ── TX 3: Revalidate & Finalize Decision ──
        async with self.session_factory() as session:
            # Lock route to check config fencing
            route_res = await session.execute(
                select(NetworkRoute).where(NetworkRoute.id == pinned_route_id).with_for_update()
            )
            locked_route = route_res.scalar_one()

            # Fencing check: verify route enabled & config version unmodified
            is_stale_config = (
                not locked_route.is_enabled
                or locked_route.config_version != network_check.route_config_version
                or locked_route.config_checksum != network_check.route_config_checksum
            )

            if is_stale_config:
                action = NetworkAction.WAITING_NETWORK
                health_state = HealthState.UNHEALTHY
                reason = "Route configuration changed or route disabled during probe evaluation."
            elif not destination_allowed:
                action = NetworkAction.BLOCKED_NETWORK
                health_state = HealthState.UNHEALTHY
                reason = "Destination blocked by active NetworkPolicy destination rules."
            else:
                # Evaluate observational health
                health_state, health_reason = HealthEvaluator.evaluate_health_from_probes(
                    probe_runs=probe_results,
                    policy_config=pinned_policy_config,
                )

                cb_cfg = pinned_policy_config.get("circuit_breaker", {})
                fail_thresh = cb_cfg.get("failure_threshold", 5)
                cooldown_sec = cb_cfg.get("cooldown_seconds", 30)
                ho_thresh = cb_cfg.get("half_open_success_threshold", 2)

                # Check for SSRF failures
                ssrf_fails = [
                    p
                    for p in probe_results
                    if p.error_category == NetworkFailureCategory.SSRF_BLOCKED
                ]
                if ssrf_fails:
                    action = NetworkAction.BLOCKED_NETWORK
                    reason = f"SSRF violation: {ssrf_fails[0].error_message}"
                    await RouteCircuitBreaker.record_probe_outcome(
                        session=session,
                        route_id=pinned_route_id,
                        service_category=request.service_category,
                        is_success=False,
                        failure_threshold=fail_thresh,
                        cooldown_seconds=cooldown_sec,
                        trigger_reason="SSRF violation detected.",
                    )
                elif health_state == HealthState.HEALTHY:
                    action = NetworkAction.ALLOW
                    reason = health_reason
                    await RouteCircuitBreaker.record_probe_outcome(
                        session=session,
                        route_id=pinned_route_id,
                        service_category=request.service_category,
                        is_success=True,
                        half_open_success_threshold=ho_thresh,
                    )
                elif health_state == HealthState.DEGRADED:
                    action = NetworkAction.ALLOW_DEGRADED
                    reason = health_reason
                    await RouteCircuitBreaker.record_probe_outcome(
                        session=session,
                        route_id=pinned_route_id,
                        service_category=request.service_category,
                        is_success=True,
                        half_open_success_threshold=ho_thresh,
                    )
                else:
                    action = NetworkAction.WAITING_NETWORK
                    reason = health_reason
                    await RouteCircuitBreaker.record_probe_outcome(
                        session=session,
                        route_id=pinned_route_id,
                        service_category=request.service_category,
                        is_success=False,
                        failure_threshold=fail_thresh,
                        cooldown_seconds=cooldown_sec,
                        trigger_reason=health_reason,
                    )

            # Insert Decision
            decision = NetworkDecision(
                id=uuid4(),
                network_check_id=check_id,
                action=action.value,
                resulting_health_state=health_state.value,
                reason=reason,
                actor="NETWORK_MANAGER",
                created_at=datetime.now(UTC),
            )
            session.add(decision)

            # Update Check to COMPLETED
            check_record = await session.execute(
                select(NetworkCheck).where(NetworkCheck.id == check_id).with_for_update()
            )
            locked_check = check_record.scalar_one()
            locked_check.status = "COMPLETED"
            locked_check.completed_at = datetime.now(UTC)

            await session.commit()

        # Reload complete record
        async with self.session_factory() as session:
            final_res = await session.execute(
                select(NetworkCheck)
                .where(NetworkCheck.id == check_id)
                .options(
                    selectinload(NetworkCheck.decision),
                    selectinload(NetworkCheck.probe_runs),
                )
            )
            completed_check = final_res.scalar_one()
            resp = self._build_check_response(completed_check)

            permit = None
            if action in (NetworkAction.ALLOW, NetworkAction.ALLOW_DEGRADED):
                permit = EgressPermitManager.issue_permit(
                    network_check_id=check_id,
                    route_id=pinned_route_id,
                    route_config_version=locked_route.config_version,
                    canonical_destination=canon_dest.canonical_string,
                    service_category=request.service_category,
                )

            return resp, permit

    def _build_check_response(self, check: NetworkCheck) -> NetworkCheckResponse:
        """Convert ORM NetworkCheck to Pydantic response."""
        dec_resp = None
        if check.decision:
            dec_resp = NetworkDecisionResponse(
                id=check.decision.id,
                network_check_id=check.decision.network_check_id,
                action=NetworkAction(check.decision.action),
                resulting_health_state=HealthState(check.decision.resulting_health_state),
                reason=check.decision.reason,
                actor=check.decision.actor,
                created_at=check.decision.created_at,
            )

        probe_runs_data = [
            NetworkProbeResultData(
                probe_type=p.probe_type,
                status=p.status,
                latency_ms=p.latency_ms,
                evidence=p.evidence,
            )
            for p in check.probe_runs
        ]

        return NetworkCheckResponse(
            id=check.id,
            mission_id=check.mission_id,
            task_id=check.task_id,
            route_id=check.route_id,
            route_config_version=check.route_config_version,
            route_config_checksum=check.route_config_checksum,
            policy_id=check.policy_id,
            policy_version=check.policy_version,
            policy_checksum=check.policy_checksum,
            service_category=ServiceCategory(check.service_category),
            canonical_destination=check.canonical_destination,
            idempotency_key=check.idempotency_key,
            status=check.status,
            started_at=check.started_at,
            completed_at=check.completed_at,
            created_at=check.created_at,
            decision=dec_resp,
            probe_runs=probe_runs_data,
        )
