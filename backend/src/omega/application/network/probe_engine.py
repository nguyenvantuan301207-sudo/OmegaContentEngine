"""Bounded probe execution engine for OMEGA-009 Network Manager.

Executes isolated probe steps (DNS, TCP, TLS, HTTPS, Proxy) outside database transactions.
Captures structured evidence, latency, and failure classifications.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from urllib.parse import urlparse

import httpx

from omega.domain.network import (
    CanonicalDestination,
    NetworkFailureCategory,
    NetworkProbeResultData,
    ProbeStatus,
    ProbeType,
    RouteType,
    SSRFValidationError,
)
from omega.infrastructure.network.dns_resolver import SafeDNSResolver
from omega.infrastructure.network.pinned_transport import PinnedAsyncHTTPTransport
from omega.logging import get_logger

logger = get_logger(service="omega-probe-engine")


class ProbeEngine:
    """Executes deterministic, bounded network probes."""

    @staticmethod
    async def run_probes(
        canonical_dest: CanonicalDestination,
        route_type: RouteType = RouteType.DIRECT,
        proxy_endpoint_url: str | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
    ) -> list[NetworkProbeResultData]:
        """Execute standard probe suite against destination."""
        probe_results: list[NetworkProbeResultData] = []
        host = canonical_dest.normalized_host
        port = canonical_dest.effective_port
        scheme = canonical_dest.scheme

        # ── Step 1: DNS Resolution Probe ──
        dns_start = time.perf_counter()
        resolved_ips: list[str] = []
        try:
            resolved_ips = await SafeDNSResolver.resolve_and_validate(host)
            dns_latency = (time.perf_counter() - dns_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.DNS_RESOLUTION,
                    status=ProbeStatus.SUCCESS,
                    latency_ms=round(dns_latency, 2),
                    evidence={"resolved_ips": resolved_ips, "hostname": host},
                )
            )
        except SSRFValidationError as exc:
            dns_latency = (time.perf_counter() - dns_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.DNS_RESOLUTION,
                    status=ProbeStatus.BLOCKED_POLICY,
                    latency_ms=round(dns_latency, 2),
                    evidence={"hostname": host, "error": str(exc)},
                    error_category=NetworkFailureCategory.SSRF_BLOCKED,
                    error_message=str(exc),
                )
            )
            return probe_results
        except Exception as exc:
            dns_latency = (time.perf_counter() - dns_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.DNS_RESOLUTION,
                    status=ProbeStatus.FAILURE,
                    latency_ms=round(dns_latency, 2),
                    evidence={"hostname": host, "error": str(exc)},
                    error_category=NetworkFailureCategory.DNS_FAILURE,
                    error_message=str(exc),
                )
            )
            return probe_results

        selected_ip = resolved_ips[0]

        # ── Step 2: TCP Connect Probe (DIRECT or proxy gateway) ──
        tcp_start = time.perf_counter()
        target_connect_host = selected_ip if route_type == RouteType.DIRECT else host
        target_connect_port = port

        if route_type in (RouteType.HTTPS_CONNECT_PROXY, RouteType.SOCKS5) and proxy_endpoint_url:
            parsed_proxy = urlparse(proxy_endpoint_url)
            proxy_host = parsed_proxy.hostname or "127.0.0.1"
            proxy_port = parsed_proxy.port or (443 if parsed_proxy.scheme == "https" else 8080)
            target_connect_host = proxy_host
            target_connect_port = proxy_port

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(target_connect_host, target_connect_port),
                timeout=connect_timeout,
            )
            writer.close()
            await writer.wait_closed()
            tcp_latency = (time.perf_counter() - tcp_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.TCP_CONNECT,
                    status=ProbeStatus.SUCCESS,
                    latency_ms=round(tcp_latency, 2),
                    evidence={"connected_host": target_connect_host, "port": target_connect_port},
                )
            )
        except TimeoutError:
            tcp_latency = (time.perf_counter() - tcp_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.TCP_CONNECT,
                    status=ProbeStatus.TIMEOUT,
                    latency_ms=round(tcp_latency, 2),
                    evidence={"connected_host": target_connect_host, "port": target_connect_port},
                    error_category=NetworkFailureCategory.CONNECT_TIMEOUT,
                    error_message="TCP connection timed out.",
                )
            )
            return probe_results
        except Exception as exc:
            tcp_latency = (time.perf_counter() - tcp_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.TCP_CONNECT,
                    status=ProbeStatus.FAILURE,
                    latency_ms=round(tcp_latency, 2),
                    evidence={
                        "connected_host": target_connect_host,
                        "port": target_connect_port,
                        "error": str(exc),
                    },
                    error_category=NetworkFailureCategory.CONNECT_REFUSED,
                    error_message=str(exc),
                )
            )
            return probe_results

        # ── Step 3: TLS Handshake Probe (if DIRECT & HTTPS) ──
        if route_type == RouteType.DIRECT and scheme == "https":
            tls_start = time.perf_counter()
            try:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = True
                ssl_ctx.verify_mode = ssl.CERT_REQUIRED

                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        selected_ip,
                        port,
                        ssl=ssl_ctx,
                        server_hostname=host,
                    ),
                    timeout=connect_timeout,
                )
                writer.close()
                await writer.wait_closed()
                tls_latency = (time.perf_counter() - tls_start) * 1000.0
                probe_results.append(
                    NetworkProbeResultData(
                        probe_type=ProbeType.TLS_HANDSHAKE,
                        status=ProbeStatus.SUCCESS,
                        latency_ms=round(tls_latency, 2),
                        evidence={
                            "server_hostname": host,
                            "pinned_ip": selected_ip,
                            "tls_verified": True,
                        },
                    )
                )
            except Exception as exc:
                tls_latency = (time.perf_counter() - tls_start) * 1000.0
                probe_results.append(
                    NetworkProbeResultData(
                        probe_type=ProbeType.TLS_HANDSHAKE,
                        status=ProbeStatus.FAILURE,
                        latency_ms=round(tls_latency, 2),
                        evidence={
                            "server_hostname": host,
                            "pinned_ip": selected_ip,
                            "error": str(exc),
                        },
                        error_category=NetworkFailureCategory.TLS_ERROR,
                        error_message=f"TLS handshake verification failed: {exc}",
                    )
                )
                return probe_results

        # ── Step 4: HTTPS HEAD Probe via Pinned Transport ──
        http_start = time.perf_counter()
        transport = PinnedAsyncHTTPTransport(
            route_type=route_type,
            proxy_endpoint_url=proxy_endpoint_url,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            tls_verify=True,
        )
        try:
            async with httpx.AsyncClient(
                transport=transport, timeout=connect_timeout + read_timeout
            ) as client:
                target_url = canonical_dest.canonical_string
                resp = await client.head(target_url, headers={"user-agent": "omega-probe/1.0"})
                http_latency = (time.perf_counter() - http_start) * 1000.0
                status_cat = (
                    NetworkFailureCategory.HTTP_5XX
                    if resp.status_code >= 500
                    else (NetworkFailureCategory.HTTP_4XX if resp.status_code >= 400 else None)
                )
                probe_results.append(
                    NetworkProbeResultData(
                        probe_type=ProbeType.HTTPS_HEAD,
                        status=ProbeStatus.SUCCESS
                        if resp.status_code < 500
                        else ProbeStatus.FAILURE,
                        latency_ms=round(http_latency, 2),
                        evidence={"status_code": resp.status_code, "headers": dict(resp.headers)},
                        error_category=status_cat,
                    )
                )
        except SSRFValidationError as exc:
            http_latency = (time.perf_counter() - http_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.HTTPS_HEAD,
                    status=ProbeStatus.BLOCKED_POLICY,
                    latency_ms=round(http_latency, 2),
                    evidence={"error": str(exc)},
                    error_category=NetworkFailureCategory.SSRF_BLOCKED,
                    error_message=str(exc),
                )
            )
        except TimeoutError:
            http_latency = (time.perf_counter() - http_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.HTTPS_HEAD,
                    status=ProbeStatus.TIMEOUT,
                    latency_ms=round(http_latency, 2),
                    evidence={"error": "HTTP probe request timed out"},
                    error_category=NetworkFailureCategory.CONNECT_TIMEOUT,
                    error_message="HTTP probe request timed out.",
                )
            )
        except Exception as exc:
            http_latency = (time.perf_counter() - http_start) * 1000.0
            probe_results.append(
                NetworkProbeResultData(
                    probe_type=ProbeType.HTTPS_HEAD,
                    status=ProbeStatus.FAILURE,
                    latency_ms=round(http_latency, 2),
                    evidence={"error": str(exc)},
                    error_category=NetworkFailureCategory.UNKNOWN,
                    error_message=str(exc),
                )
            )

        return probe_results
