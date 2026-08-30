"""Domain models, enums, schemas, and deterministic helpers for OMEGA-009 Network Manager.

Defines network route types, service categories, probe types, circuit states,
health states, destination normalization, SSRF validation, and canonical idempotency keys.
This is the domain layer — zero infrastructure/database dependencies.
"""

from __future__ import annotations

import enum
import hashlib
import ipaddress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RouteType(enum.StrEnum):
    """Allowed route types in Core v1."""

    DIRECT = "DIRECT"
    HTTPS_CONNECT_PROXY = "HTTPS_CONNECT_PROXY"
    SOCKS5 = "SOCKS5"
    SYSTEM_DEFAULT = "SYSTEM_DEFAULT"


class ServiceCategory(enum.StrEnum):
    """Categorical network service classifications."""

    GENERAL_HTTP = "GENERAL_HTTP"
    AI_PROVIDER = "AI_PROVIDER"
    STORAGE = "STORAGE"
    YOUTUBE_API = "YOUTUBE_API"
    EXTERNAL_PUBLISH = "EXTERNAL_PUBLISH"
    WEBHOOK = "WEBHOOK"
    INTERNAL_SERVICE = "INTERNAL_SERVICE"


class ProbeType(enum.StrEnum):
    """Network probe execution steps."""

    DNS_RESOLUTION = "DNS_RESOLUTION"
    TCP_CONNECT = "TCP_CONNECT"
    TLS_HANDSHAKE = "TLS_HANDSHAKE"
    HTTPS_HEAD = "HTTPS_HEAD"
    HTTPS_GET_SMALL = "HTTPS_GET_SMALL"
    PROXY_CONNECTIVITY = "PROXY_CONNECTIVITY"


class ProbeStatus(enum.StrEnum):
    """Result status of an individual probe step."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    SKIPPED = "SKIPPED"


class HealthState(enum.StrEnum):
    """Observational health classification derived from probe history."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class CircuitState(enum.StrEnum):
    """Authoritative traffic control state for a route/service pair."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class NetworkAction(enum.StrEnum):
    """Deterministic actions emitted by the Network Preflight Decision Engine."""

    ALLOW = "ALLOW"
    ALLOW_DEGRADED = "ALLOW_DEGRADED"
    WAITING_NETWORK = "WAITING_NETWORK"
    BLOCKED_NETWORK = "BLOCKED_NETWORK"


class NetworkPolicyStatus(enum.StrEnum):
    """Lifecycle status of a versioned NetworkPolicy."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class NetworkFailureCategory(enum.StrEnum):
    """Categorical classification of network failures for retry/policy evaluation."""

    DNS_FAILURE = "DNS_FAILURE"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    CONNECT_REFUSED = "CONNECT_REFUSED"
    TLS_ERROR = "TLS_ERROR"
    PROXY_ERROR = "PROXY_ERROR"
    HTTP_5XX = "HTTP_5XX"
    HTTP_4XX = "HTTP_4XX"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    STALE_ROUTE_CONFIG = "STALE_ROUTE_CONFIG"
    POLICY_REJECTED = "POLICY_REJECTED"
    UNKNOWN = "UNKNOWN"


# ── SSRF Disallowed IP Networks ──
DISALLOWED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # Carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),  # IPv4 Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # IPv4 Link-Local / Cloud Metadata
    ipaddress.ip_network("172.16.0.0/12"),  # Private RFC 1918
    ipaddress.ip_network("192.0.0.0/24"),  # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),  # Private RFC 1918
    ipaddress.ip_network("198.18.0.0/15"),  # Network benchmark tests
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved / Future use
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6 Disallowed
    ipaddress.ip_network("::1/128"),  # IPv6 Loopback
    ipaddress.ip_network("::/128"),  # Unspecified
    ipaddress.ip_network("fe80::/10"),  # IPv6 Link-Local
    ipaddress.ip_network("fc00::/7"),  # IPv6 Unique Local (ULA)
    ipaddress.ip_network("ff00::/8"),  # IPv6 Multicast
    ipaddress.ip_network("2001:db8::/32"),  # Documentation
]


class SSRFValidationError(ValueError):
    """Raised when a destination or resolved IP violates SSRF egress safety rules."""


class CanonicalDestination(BaseModel):
    """Normalized, canonical representation of a network destination."""

    scheme: str
    normalized_host: str
    effective_port: int
    path_prefix: str | None = None

    @property
    def canonical_string(self) -> str:
        """Return the standard canonical destination string."""
        path = self.path_prefix or ""
        if path and not path.startswith("/"):
            path = f"/{path}"
        return f"{self.scheme}://{self.normalized_host}:{self.effective_port}{path}"


def normalize_destination(raw_url: str) -> CanonicalDestination:
    """Normalize a target URL into a canonical destination object.

    Enforces lowercase hostname, trailing dot removal, IDNA/punycode encoding,
    and standard default port resolution (80 for http, 443 for https).
    """
    url_trimmed = raw_url.strip()
    if not url_trimmed:
        raise ValueError("Destination URL cannot be empty.")

    parsed = urlparse(url_trimmed) if "://" in url_trimmed else urlparse(f"https://{url_trimmed}")

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme '{scheme}'. Only http and https are allowed.")

    host_raw = parsed.hostname
    if not host_raw:
        raise ValueError(f"Could not extract valid hostname from '{raw_url}'.")

    # Lowercase, strip trailing dots, strip whitespace
    host_clean = host_raw.lower().rstrip(".").strip()

    # Apply IDNA/punycode encoding
    try:
        host_idna = host_clean.encode("idna").decode("ascii")
    except Exception as exc:
        raise ValueError(f"Invalid internationalized domain name '{host_clean}': {exc}") from exc

    # Port normalization
    effective_port = parsed.port if parsed.port is not None else (443 if scheme == "https" else 80)

    path_prefix = parsed.path.rstrip("/") if parsed.path and parsed.path != "/" else None

    return CanonicalDestination(
        scheme=scheme,
        normalized_host=host_idna,
        effective_port=effective_port,
        path_prefix=path_prefix,
    )


def validate_ip_address_safety(ip_str: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Validate that an IP address is a safe, routable public address.

    Rejects loopback, RFC 1918, link-local, cloud metadata, ULA, and IPv4-mapped IPv6.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise SSRFValidationError(f"Invalid IP address format: '{ip_str}'") from exc

    # Check for IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    for network in DISALLOWED_IP_NETWORKS:
        if ip in network:
            raise SSRFValidationError(
                f"Destination IP '{ip}' belongs to disallowed network '{network}' (SSRF violation)."
            )

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise SSRFValidationError(
            f"Destination IP '{ip}' is not a public routable address (SSRF violation)."
        )

    return ip


class DestinationRule(BaseModel):
    """Structured allowlist rule for a NetworkPolicy."""

    scheme: str = "https"
    exact_host: str | None = None
    domain_suffix: str | None = None
    allowed_ports: list[int] = Field(default_factory=lambda: [443])
    path_prefix: str | None = None

    def matches(self, dest: CanonicalDestination) -> bool:
        """Evaluate whether a CanonicalDestination satisfies this rule."""
        if self.scheme and dest.scheme != self.scheme.lower():
            return False

        if self.allowed_ports and dest.effective_port not in self.allowed_ports:
            return False

        host = dest.normalized_host
        if self.exact_host:
            if host != self.exact_host.lower().strip():
                return False
        elif self.domain_suffix:
            suffix = self.domain_suffix.lower().strip()
            if not suffix.startswith("."):
                suffix = f".{suffix}"
            if not (host.endswith(suffix) or host == suffix.lstrip(".")):
                return False

        if self.path_prefix:
            prefix = self.path_prefix.rstrip("/")
            dest_path = dest.path_prefix or ""
            if not dest_path.startswith(prefix):
                return False

        return True


def compute_preflight_idempotency_key(
    mission_id: UUID | None,
    task_id: UUID | None,
    service_category: ServiceCategory,
    canonical_destination: str,
    route_id: UUID,
    route_config_version: int,
    route_config_checksum: str,
    policy_id: UUID,
    policy_version: str,
    policy_checksum: str,
    caller_key: str,
) -> str:
    """Compute the canonical preflight idempotency key binding exact configuration state."""
    raw = (
        f"{str(mission_id or 'none')}:{str(task_id or 'none')}:"
        f"{service_category.value}:{canonical_destination}:"
        f"{str(route_id)}:{route_config_version}:{route_config_checksum}:"
        f"{str(policy_id)}:{policy_version}:{policy_checksum}:{caller_key}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_config_checksum(config: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 checksum for route or policy configuration dict."""
    import json

    serialized = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ── Pydantic Request & Response Schemas ──


class NetworkProfileCreate(BaseModel):
    """Schema for creating a NetworkProfile."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    is_default: bool = False


class NetworkProfileResponse(BaseModel):
    """Schema for returning NetworkProfile details."""

    id: UUID
    name: str
    description: str | None
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NetworkRouteCreate(BaseModel):
    """Schema for creating a NetworkRoute."""

    profile_id: UUID
    name: str = Field(..., min_length=1, max_length=100)
    route_type: RouteType = RouteType.DIRECT
    endpoint_url: str | None = None
    credential_ref: str | None = Field(None, max_length=100)
    allowed_service_categories: list[ServiceCategory] = Field(
        default_factory=lambda: [ServiceCategory.GENERAL_HTTP]
    )
    tls_verify: bool = True
    connect_timeout_seconds: float = Field(5.0, ge=0.5, le=30.0)
    read_timeout_seconds: float = Field(10.0, ge=1.0, le=60.0)
    max_retries: int = Field(3, ge=0, le=5)
    priority_weight: int = Field(100, ge=1, le=1000)
    is_enabled: bool = True

    @field_validator("credential_ref")
    @classmethod
    def validate_credential_ref(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("ENV:"):
            raise ValueError(
                "credential_ref must follow the 'ENV:VARIABLE_NAME' format in Core v1."
            )
        return v

    @field_validator("tls_verify")
    @classmethod
    def validate_tls_verify(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("tls_verify must be True for all operational routes in Core v1.")
        return v


class NetworkRouteResponse(BaseModel):
    """Schema for returning NetworkRoute details."""

    id: UUID
    profile_id: UUID
    name: str
    route_type: RouteType
    endpoint_url: str | None
    credential_ref: str | None
    allowed_service_categories: list[str]
    tls_verify: bool
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_retries: int
    priority_weight: int
    is_enabled: bool
    config_version: int
    config_checksum: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NetworkPolicyCreate(BaseModel):
    """Schema for creating or updating a versioned NetworkPolicy."""

    service_category: ServiceCategory
    version: str = Field(..., min_length=1, max_length=50)
    status: NetworkPolicyStatus = NetworkPolicyStatus.DRAFT
    effective_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    policy_config: dict[str, Any]


class NetworkPolicyResponse(BaseModel):
    """Schema for returning NetworkPolicy details."""

    id: UUID
    service_category: ServiceCategory
    version: str
    status: NetworkPolicyStatus
    effective_at: datetime
    policy_config: dict[str, Any]
    checksum: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NetworkProbeResultData(BaseModel):
    """Structured evidence output from an individual probe execution."""

    probe_type: ProbeType
    status: ProbeStatus
    latency_ms: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    error_category: NetworkFailureCategory | None = None
    error_message: str | None = None


class NetworkPreflightRequest(BaseModel):
    """Request payload for an authoritative network preflight evaluation."""

    destination_url: str = Field(..., min_length=1)
    service_category: ServiceCategory
    mission_id: UUID | None = None
    task_id: UUID | None = None
    caller_key: str = "default"
    force_refresh: bool = False


class NetworkDecisionResponse(BaseModel):
    """Schema for returning an authoritative NetworkDecision."""

    id: UUID
    network_check_id: UUID
    action: NetworkAction
    resulting_health_state: HealthState
    reason: str
    actor: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NetworkCheckResponse(BaseModel):
    """Schema for returning complete NetworkCheck details and findings."""

    id: UUID
    mission_id: UUID | None
    task_id: UUID | None
    route_id: UUID
    route_config_version: int
    route_config_checksum: str
    policy_id: UUID
    policy_version: str
    policy_checksum: str
    service_category: ServiceCategory
    canonical_destination: str
    idempotency_key: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    decision: NetworkDecisionResponse | None = None
    probe_runs: list[NetworkProbeResultData] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# Alias for preflight response result
NetworkPreflightResult = NetworkCheckResponse


class NetworkEgressPermit(BaseModel):
    """Authorized cryptographic or structured permit for outbound execution."""

    permit_id: UUID = Field(default_factory=uuid4)
    network_check_id: UUID
    route_id: UUID
    route_config_version: int
    canonical_destination: str
    service_category: ServiceCategory
    issued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime

    def is_valid_for(self, target_url: str, current_time: datetime | None = None) -> bool:
        """Check whether the permit is unexpired and matches the canonical destination."""
        now = current_time or datetime.now(UTC)
        if now >= self.expires_at:
            return False
        canon = normalize_destination(target_url)
        return canon.canonical_string.startswith(self.canonical_destination)
