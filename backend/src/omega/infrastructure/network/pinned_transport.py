"""Pinned Async HTTP Transport for connection-level SSRF defense.

Binds socket connections directly to pre-validated IP addresses while preserving
the original hostname for TLS SNI, certificate hostname validation, and HTTP Host header.
Enforces bounded response bodies (max 4KB) and strict redirect validation.
"""

from __future__ import annotations

import io
import ssl
from urllib.parse import urljoin

import httpx

from omega.domain.network import (
    CanonicalDestination,
    RouteType,
    SSRFValidationError,
    normalize_destination,
)
from omega.infrastructure.network.dns_resolver import SafeDNSResolver
from omega.logging import get_logger

logger = get_logger(service="omega-pinned-transport")

MAX_PROBE_BODY_BYTES = 4096  # 4KB max response payload
MAX_REDIRECT_HOPS = 2


class PinnedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """Async transport that connects socket to a validated IP and bounds responses."""

    def __init__(
        self,
        route_type: RouteType = RouteType.DIRECT,
        proxy_endpoint_url: str | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        tls_verify: bool = True,
    ) -> None:
        super().__init__()
        self.route_type = route_type
        self.proxy_endpoint_url = proxy_endpoint_url
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.tls_verify = tls_verify

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Execute request with connection-level IP pinning and manual redirect inspection."""
        current_url = str(request.url)
        method = request.method
        headers = dict(request.headers)

        for hop in range(MAX_REDIRECT_HOPS + 1):
            canon_dest = normalize_destination(current_url)

            # 1. Resolve and validate all target IPs
            resolved_ips = await SafeDNSResolver.resolve_and_validate(canon_dest.normalized_host)
            selected_ip = resolved_ips[0]

            # 2. Build single-use transport connected to pinned IP
            response = await self._execute_pinned_request(
                method=method,
                canonical_dest=canon_dest,
                pinned_ip=selected_ip,
                headers=headers,
                content=request.content,
            )

            # 3. Handle 3xx Redirects
            if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                if hop >= MAX_REDIRECT_HOPS:
                    raise ValueError(
                        f"Exceeded maximum allowed redirect hops ({MAX_REDIRECT_HOPS})."
                    )

                redirect_url = urljoin(current_url, response.headers["location"])
                logger.info(
                    "Intercepting redirect for re-validation",
                    from_url=current_url,
                    to_url=redirect_url,
                    hop=hop + 1,
                )
                current_url = redirect_url
                method = "GET" if response.status_code == 303 else method
                continue

            return response

        raise ValueError("Unexpected redirect loop termination.")

    async def _execute_pinned_request(
        self,
        method: str,
        canonical_dest: CanonicalDestination,
        pinned_ip: str,
        headers: dict[str, str],
        content: bytes | None,
    ) -> httpx.Response:
        """Execute HTTP request against pinned IP using direct connection or trusted proxy."""
        # Enforce Host header
        headers["host"] = canonical_dest.normalized_host
        headers.setdefault("range", "bytes=0-1024")

        # Configure SSL Context
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = True
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED

        # Target URL replaces hostname with pinned IP for socket connection while preserving port & path
        path = canonical_dest.path_prefix or "/"
        target_pinned_url = (
            f"{canonical_dest.scheme}://{pinned_ip}:{canonical_dest.effective_port}{path}"
        )

        if self.route_type == RouteType.DIRECT:
            # DIRECT: connect to pinned IP with custom AsyncHTTPTransport
            # SNI set via verify / server_hostname extension in httpx
            transport = httpx.AsyncHTTPTransport(
                verify=ssl_ctx if canonical_dest.scheme == "https" else True,
                http2=False,
            )
            # Use extensions to enforce server_hostname on pinned IP URL
            async with httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(
                    connect=self.connect_timeout,
                    read=self.read_timeout,
                    write=5.0,
                    pool=5.0,
                ),
            ) as client:
                req = client.build_request(
                    method=method,
                    url=target_pinned_url,
                    headers=headers,
                    content=content,
                    extensions={"sni_hostname": canonical_dest.normalized_host},
                )
                resp = await client.send(req, stream=True)
                body_bytes = await self._read_bounded_response(resp)
                return httpx.Response(
                    status_code=resp.status_code,
                    headers=resp.headers,
                    content=body_bytes,
                    request=req,
                )

        elif self.route_type == RouteType.HTTPS_CONNECT_PROXY:
            if not self.proxy_endpoint_url:
                raise ValueError("HTTPS_CONNECT_PROXY route missing proxy_endpoint_url.")

            # Proxy connects tunnel to pinned IP:port
            proxy_mount = httpx.AsyncHTTPTransport(
                proxy=self.proxy_endpoint_url,
                verify=ssl_ctx if canonical_dest.scheme == "https" else True,
                http2=False,
            )
            async with httpx.AsyncClient(
                transport=proxy_mount,
                timeout=httpx.Timeout(connect=self.connect_timeout, read=self.read_timeout),
            ) as client:
                req = client.build_request(
                    method=method,
                    url=target_pinned_url,
                    headers=headers,
                    content=content,
                    extensions={"sni_hostname": canonical_dest.normalized_host},
                )
                resp = await client.send(req, stream=True)
                body_bytes = await self._read_bounded_response(resp)
                return httpx.Response(
                    status_code=resp.status_code,
                    headers=resp.headers,
                    content=body_bytes,
                    request=req,
                )

        elif self.route_type == RouteType.SOCKS5:
            if not self.proxy_endpoint_url:
                raise ValueError("SOCKS5 route missing proxy_endpoint_url.")
            proxy_mount = httpx.AsyncHTTPTransport(
                proxy=self.proxy_endpoint_url,
                verify=ssl_ctx if canonical_dest.scheme == "https" else True,
            )
            async with httpx.AsyncClient(
                transport=proxy_mount,
                timeout=httpx.Timeout(connect=self.connect_timeout, read=self.read_timeout),
            ) as client:
                req = client.build_request(
                    method=method,
                    url=target_pinned_url,
                    headers=headers,
                    content=content,
                    extensions={"sni_hostname": canonical_dest.normalized_host},
                )
                resp = await client.send(req, stream=True)
                body_bytes = await self._read_bounded_response(resp)
                return httpx.Response(
                    status_code=resp.status_code,
                    headers=resp.headers,
                    content=body_bytes,
                    request=req,
                )

        else:
            raise SSRFValidationError(
                f"Route type '{self.route_type}' is unsupported or deferred in Core v1."
            )

    async def _read_bounded_response(self, response: httpx.Response) -> bytes:
        """Read response body up to MAX_PROBE_BODY_BYTES (4KB)."""
        buffer = io.BytesIO()
        total_read = 0
        async for chunk in response.aiter_bytes():
            buffer.write(chunk)
            total_read += len(chunk)
            if total_read >= MAX_PROBE_BODY_BYTES:
                break
        await response.aclose()
        return buffer.getvalue()
