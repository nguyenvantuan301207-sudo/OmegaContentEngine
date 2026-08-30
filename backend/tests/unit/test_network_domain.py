"""Unit tests for OMEGA-009 Network Manager domain models, normalization, and hashing."""

from uuid import uuid4

import pytest

from omega.domain.network import (
    CanonicalDestination,
    DestinationRule,
    ServiceCategory,
    compute_preflight_idempotency_key,
    normalize_destination,
)


def test_normalize_destination_standard():
    """Test standard normalization of scheme, host, and port."""
    dest = normalize_destination("https://API.YouTube.COM:443/v3/videos?foo=bar")
    assert dest.scheme == "https"
    assert dest.normalized_host == "api.youtube.com"
    assert dest.effective_port == 443
    assert dest.path_prefix == "/v3/videos"
    assert dest.canonical_string == "https://api.youtube.com:443/v3/videos"


def test_normalize_destination_default_ports():
    """Test default port assignment (80 for http, 443 for https)."""
    http_dest = normalize_destination("http://example.com")
    assert http_dest.effective_port == 80
    assert http_dest.canonical_string == "http://example.com:80"

    https_dest = normalize_destination("https://example.com")
    assert https_dest.effective_port == 443
    assert https_dest.canonical_string == "https://example.com:443"


def test_normalize_destination_trailing_dot_and_idna():
    """Test trailing dot removal and IDNA/punycode domain encoding."""
    dest = normalize_destination("https://bücher.example.com./api/")
    assert dest.normalized_host == "xn--bcher-kva.example.com"
    assert dest.canonical_string == "https://xn--bcher-kva.example.com:443/api"


def test_normalize_destination_invalid_scheme():
    """Test rejection of unsupported protocols."""
    with pytest.raises(ValueError, match="Unsupported scheme 'ftp'"):
        normalize_destination("ftp://files.example.com")


def test_compute_preflight_idempotency_key_stability_and_fencing():
    """Test idempotency hash stability and variation across config changes."""
    mission_id = uuid4()
    task_id = uuid4()
    route_id = uuid4()
    policy_id = uuid4()

    key1 = compute_preflight_idempotency_key(
        mission_id=mission_id,
        task_id=task_id,
        service_category=ServiceCategory.YOUTUBE_API,
        canonical_destination="https://api.youtube.com:443",
        route_id=route_id,
        route_config_version=1,
        route_config_checksum="chk_v1",
        policy_id=policy_id,
        policy_version="1.0.0",
        policy_checksum="pol_chk1",
        caller_key="test_call",
    )

    # Identical arguments produce identical key
    key2 = compute_preflight_idempotency_key(
        mission_id=mission_id,
        task_id=task_id,
        service_category=ServiceCategory.YOUTUBE_API,
        canonical_destination="https://api.youtube.com:443",
        route_id=route_id,
        route_config_version=1,
        route_config_checksum="chk_v1",
        policy_id=policy_id,
        policy_version="1.0.0",
        policy_checksum="pol_chk1",
        caller_key="test_call",
    )
    assert key1 == key2

    # Mutation in route_config_version produces different key
    key_v2 = compute_preflight_idempotency_key(
        mission_id=mission_id,
        task_id=task_id,
        service_category=ServiceCategory.YOUTUBE_API,
        canonical_destination="https://api.youtube.com:443",
        route_id=route_id,
        route_config_version=2,
        route_config_checksum="chk_v2",
        policy_id=policy_id,
        policy_version="1.0.0",
        policy_checksum="pol_chk1",
        caller_key="test_call",
    )
    assert key1 != key_v2


def test_destination_rule_matching():
    """Test structured destination rule matching."""
    rule_suffix = DestinationRule(
        scheme="https",
        domain_suffix=".googleapis.com",
        allowed_ports=[443],
        path_prefix="/youtube",
    )

    dest_match = normalize_destination("https://youtube.googleapis.com/youtube/v3/videos")
    assert rule_suffix.matches(dest_match) is True

    dest_wrong_port = CanonicalDestination(
        scheme="https",
        normalized_host="youtube.googleapis.com",
        effective_port=8443,
        path_prefix="/youtube/v3",
    )
    assert rule_suffix.matches(dest_wrong_port) is False

    dest_wrong_domain = normalize_destination("https://api.attacker.com/youtube/v3")
    assert rule_suffix.matches(dest_wrong_domain) is False

    dest_wrong_path = normalize_destination("https://youtube.googleapis.com/drive/v3")
    assert rule_suffix.matches(dest_wrong_path) is False
