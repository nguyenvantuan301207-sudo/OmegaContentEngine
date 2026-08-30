"""DNS resolution infrastructure with strict SSRF IP validation.

Resolves hostnames to IP addresses asynchronously and validates every returned IP
against disallowed ranges before any connection attempt.
"""

from __future__ import annotations

import asyncio
import socket

from omega.domain.network import SSRFValidationError, validate_ip_address_safety
from omega.logging import get_logger

logger = get_logger(service="omega-dns-resolver")


class SafeDNSResolver:
    """Performs async DNS resolution with mandatory SSRF safety checks."""

    @staticmethod
    async def resolve_and_validate(hostname: str) -> list[str]:
        """Resolve a hostname to IP addresses and validate EVERY returned IP.

        Raises SSRFValidationError if ANY resolved IP belongs to a disallowed network.
        Returns a list of validated IP strings.
        """
        loop = asyncio.get_running_loop()
        try:
            addr_info = await loop.getaddrinfo(
                hostname,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            logger.warning("DNS resolution failed", hostname=hostname, error=str(exc))
            raise ValueError(f"DNS resolution failed for hostname '{hostname}': {exc}") from exc

        if not addr_info:
            raise ValueError(f"DNS resolution returned no address records for '{hostname}'.")

        resolved_ips: list[str] = []
        for info in addr_info:
            sockaddr = info[4]
            ip_str = sockaddr[0]
            if ip_str not in resolved_ips:
                resolved_ips.append(ip_str)

        # Validate EVERY resolved IP against SSRF blocklists
        for ip_str in resolved_ips:
            try:
                validate_ip_address_safety(ip_str)
            except SSRFValidationError as exc:
                logger.error(
                    "SSRF violation detected during DNS resolution",
                    hostname=hostname,
                    resolved_ip=ip_str,
                    error=str(exc),
                )
                raise

        return resolved_ips
