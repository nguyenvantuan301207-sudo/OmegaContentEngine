"""Unit tests for OMEGA-009 SSRF defense and IP safety validation."""

import pytest

from omega.domain.network import SSRFValidationError, validate_ip_address_safety
from omega.infrastructure.network.dns_resolver import SafeDNSResolver


@pytest.mark.parametrize(
    "forbidden_ip",
    [
        "127.0.0.1",  # Loopback
        "127.0.0.254",
        "10.0.0.1",  # Private Class A
        "172.16.0.1",  # Private Class B
        "172.31.255.255",
        "192.168.1.1",  # Private Class C
        "169.254.169.254",  # Cloud metadata AWS/GCP/Azure
        "169.254.1.1",  # Link-local
        "0.0.0.0",
        "255.255.255.255",
        "::1",  # IPv6 Loopback
        "fe80::1",  # IPv6 Link-Local
        "fc00::1",  # IPv6 ULA
        "fd12:3456:789a:1::1",  # IPv6 ULA
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 Loopback
        "::ffff:169.254.169.254",  # IPv4-mapped IPv6 Metadata
        "::ffff:10.0.0.1",  # IPv4-mapped IPv6 Private
    ],
)
def test_validate_ip_safety_rejects_forbidden_ranges(forbidden_ip: str):
    """Test rejection of all forbidden private, link-local, loopback, and metadata IPs."""
    with pytest.raises(SSRFValidationError, match="SSRF violation"):
        validate_ip_address_safety(forbidden_ip)


@pytest.mark.parametrize(
    "valid_public_ip",
    [
        "8.8.8.8",  # Google Public DNS
        "1.1.1.1",  # Cloudflare DNS
        "93.184.216.34",  # example.com
        "142.250.190.46",  # google.com
        "2001:4860:4860::8888",  # Google IPv6 DNS
        "2606:4700:4700::1111",  # Cloudflare IPv6 DNS
    ],
)
def test_validate_ip_safety_accepts_valid_public_ips(valid_public_ip: str):
    """Test acceptance of routable, safe public IPs."""
    res = validate_ip_address_safety(valid_public_ip)
    assert res is not None


@pytest.mark.asyncio
async def test_dns_resolver_rejects_localhost(monkeypatch):
    """Test that SafeDNSResolver rejects localhost and loopback domain targets."""
    with pytest.raises(SSRFValidationError):
        await SafeDNSResolver.resolve_and_validate("localhost")


@pytest.mark.asyncio
async def test_dns_resolver_rejects_mixed_answers(monkeypatch):
    """Test that SafeDNSResolver rejects responses containing any private address."""

    async def mock_getaddrinfo(host, port, family=0, type=0):
        return [
            (2, 1, 6, "", ("93.184.216.34", 0)),  # Public
            (2, 1, 6, "", ("10.0.0.5", 0)),  # Private
        ]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", mock_getaddrinfo)

    with pytest.raises(SSRFValidationError, match="SSRF violation"):
        await SafeDNSResolver.resolve_and_validate("evil-mixed-domain.com")
