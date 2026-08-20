"""Composite Source Independence Clustering Heuristic.

Detects syndicated or duplicate sources to prevent syndication chains
from artificially inflating independent claim confidence.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from omega.application.source_normalizer import normalize_source_text


def _tokenize(text: str) -> set[str]:
    """Tokenize normalized text into unique alphanumeric words."""
    norm = normalize_source_text(text)
    tokens = re.findall(r"\b[a-z0-9_]{2,}\b", norm)
    return set(tokens)


def _dice_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Sørensen-Dice coefficient between two token sets."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    return (2.0 * intersection) / (len(set_a) + len(set_b))


def _extract_domain(url: str | None) -> str:
    """Extract lowercase netloc domain from URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        return parsed.netloc.lower()
    except Exception:
        return ""


def cluster_source_independence(sources: list[dict[str, Any]]) -> dict[UUID, str]:
    """Cluster sources based on composite similarity.

    Sources in the same cluster share identical content, same normalized publisher/domain,
    or near-identical excerpt text (Dice overlap >= 0.70).

    Returns a mapping of source_id -> cluster_id (SHA-256 digest string).
    """
    if not sources:
        return {}

    # Initial tokenization and prep
    prepared: list[dict[str, Any]] = []
    for s in sources:
        s_id = s["id"] if isinstance(s["id"], UUID) else UUID(str(s["id"]))
        norm_title = normalize_source_text(s.get("title", ""))
        norm_pub = normalize_source_text(s.get("publisher", ""))
        domain = _extract_domain(s.get("url"))
        excerpt = s.get("content_excerpt", "")
        tokens = _tokenize(excerpt)
        content_hash = s.get("content_hash", "")

        prepared.append(
            {
                "id": s_id,
                "norm_title": norm_title,
                "norm_pub": norm_pub,
                "domain": domain,
                "tokens": tokens,
                "content_hash": content_hash,
                "cluster_idx": None,
            }
        )

    cluster_count = 0
    for i, s1 in enumerate(prepared):
        if s1["cluster_idx"] is not None:
            continue
        # Start a new cluster
        s1["cluster_idx"] = cluster_count
        for j in range(i + 1, len(prepared)):
            s2 = prepared[j]
            if s2["cluster_idx"] is not None:
                continue

            # Check matching signals
            same_hash = s1["content_hash"] and s1["content_hash"] == s2["content_hash"]
            same_pub = s1["norm_pub"] and s1["norm_pub"] == s2["norm_pub"]
            same_domain = s1["domain"] and s1["domain"] == s2["domain"]
            excerpt_sim = _dice_similarity(s1["tokens"], s2["tokens"])

            # Cluster if identical content, or (same publisher/domain AND excerpt_sim >= 0.50), or high excerpt_sim >= 0.70
            if (
                same_hash
                or (same_pub and excerpt_sim >= 0.50)
                or (same_domain and excerpt_sim >= 0.50)
                or excerpt_sim >= 0.70
            ):
                s2["cluster_idx"] = cluster_count

        cluster_count += 1

    # Map cluster index to deterministic cluster hash
    cluster_hashes: dict[int, str] = {}
    for c_idx in range(cluster_count):
        items = [str(s["id"]) for s in prepared if s["cluster_idx"] == c_idx]
        items.sort()
        c_hash = hashlib.sha256(f"cluster:{':'.join(items)}".encode()).hexdigest()
        cluster_hashes[c_idx] = c_hash

    return {s["id"]: cluster_hashes[s["cluster_idx"]] for s in prepared}
