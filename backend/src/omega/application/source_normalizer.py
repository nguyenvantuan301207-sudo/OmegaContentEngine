"""Source text normalization, excerpt bounding, and SHA-256 digest computation.

Ensures deterministic source content hashing and bounds raw excerpt storage.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import urlparse

MAX_EXCERPT_LENGTH = 5000


def normalize_source_text(text: str) -> str:
    """Normalize text by stripping diacritics, lowercase, removing excess whitespace."""
    if not text:
        return ""
    # Normalize unicode to NFKD and strip combining characters
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Convert to lowercase and clean non-alphanumeric chars
    cleaned = re.sub(r"[^\w\s\.\,\-\:\;]", " ", stripped.lower())
    # Collapse multiple whitespace characters
    return re.sub(r"\s+", " ", cleaned).strip()


def bound_excerpt(text: str, max_chars: int = MAX_EXCERPT_LENGTH) -> str:
    """Ensure excerpt does not exceed MAX_EXCERPT_LENGTH."""
    text_clean = text.strip()
    if len(text_clean) > max_chars:
        return text_clean[:max_chars]
    return text_clean


def compute_source_content_hash(normalized_text: str) -> str:
    """Compute deterministic SHA-256 content hash with version prefix."""
    payload = f"omega-source-content:v1:{normalized_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_url(url: str | None) -> str | None:
    """Normalize URL by stripping tracking query params and fragments."""
    if not url:
        return None
    url_clean = url.strip()
    if not url_clean:
        return None
    try:
        parsed = urlparse(url_clean)
        scheme = parsed.scheme.lower() if parsed.scheme else "https"
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        return f"{scheme}://{netloc}{path}"
    except Exception:
        return url_clean.lower().rstrip("/")
