"""Secret sanitizer for OMEGA structured logging and operational telemetry.

Redacts known credential and secret material from log event dictionaries and strings.
Preserves non-secret operational identifiers (mission_id, render_job_id, fencing_token, etc.).
"""

from __future__ import annotations

import os
import re
from typing import Any

# Regex to detect credentials in connection URLs (e.g. postgresql://user:pass@host:5432/db)
URL_PASSWORD_RE = re.compile(r"://([^:]+):([^@]+)@")

# Regex to detect Bearer tokens in headers or strings
BEARER_TOKEN_RE = re.compile(r"Bearer\s+([A-Za-z0-9\-\._~\+\/]+=*)", re.IGNORECASE)

# Field names whose values must always be redacted
REDACT_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "lease_token",
        "password",
        "secret",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "metrics_auth_token",
        "operator_auth_token",
        "omega_secret_encryption_key",
        "pexels_api_key",
        "gemini_api_key",
        "google_client_secret",
        "encryption_key",
    }
)


def sanitize_string_value(val: str) -> str:
    """Sanitize credential patterns inside a string value."""
    if not val:
        return val

    # Redact URL passwords
    val = URL_PASSWORD_RE.sub(r"://\1:[REDACTED]@", val)

    # Redact Bearer tokens
    val = BEARER_TOKEN_RE.sub("Bearer [REDACTED]", val)

    # Free-text assignments (including JSON formatted by third-party loggers).
    val = re.sub(
        r"(?i)(lease_token|(?:refresh|access)_token|(?:client|jwt)_secret|api_key|password)([\"'\s:=]+)[^\s,;\"'}]+",
        r"\1\2[REDACTED]",
        val,
    )
    for key, secret in os.environ.items():
        if secret and len(secret) >= 8 and any(marker in key.lower() for marker in REDACT_KEYS):
            val = val.replace(secret, "[REDACTED]")
    return val


def sanitize_event_dict(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor to sanitize sensitive keys and strings in log events."""
    sanitized: dict[str, Any] = {}

    for k, v in event_dict.items():
        k_lower = str(k).lower()

        # Check if the key name is sensitive
        if any(needle in k_lower for needle in REDACT_KEYS):
            sanitized[k] = "[REDACTED]"
            continue

        if isinstance(v, str):
            sanitized[k] = sanitize_string_value(v)
        elif isinstance(v, dict):
            # Recursively sanitize nested dictionaries
            sanitized[k] = sanitize_event_dict(logger, method_name, v)
        elif isinstance(v, (list, tuple)):
            sanitized[k] = [
                sanitize_event_dict(logger, method_name, {"value": item})["value"] for item in v
            ]
        else:
            sanitized[k] = v

    return sanitized
