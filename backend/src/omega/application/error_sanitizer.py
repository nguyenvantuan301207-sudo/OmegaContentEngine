"""Secret-safe normalization for errors persisted by operational services."""

from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://[^\s\]\[()<>\"']+", re.IGNORECASE)
_AUTH_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+")
_SECRET_RE = re.compile(
    r"(?i)(access_token|refresh_token|client_secret|password|api[_-]?key|token|key)"
    r"(\s*[:=]\s*)([^\s&,;]+)"
)


def sanitize_sensitive_text(value: object, *, limit: int = 500) -> str:
    """Return bounded diagnostic text with URLs and credential-shaped values removed."""
    text = str(value).split("\n", 1)[0]
    text = _AUTH_RE.sub(r"\1[REDACTED]", text)
    text = _SECRET_RE.sub(r"\1\2[REDACTED]", text)
    text = _URL_RE.sub("[REDACTED_URL]", text)
    return text[:limit]


def sanitize_error(error: BaseException) -> str:
    """Return a type-qualified, persistable representation of an exception."""
    return f"{type(error).__name__}: {sanitize_sensitive_text(error)}"
