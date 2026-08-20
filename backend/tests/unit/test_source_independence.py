"""Unit tests for Source Independence Clustering Heuristic."""

from __future__ import annotations

import uuid

from omega.application.source_independence import cluster_source_independence
from omega.application.source_normalizer import compute_source_content_hash, normalize_source_text


def test_syndicated_sources_grouped_in_same_cluster() -> None:
    """Test that reposted/syndicated articles from the same publisher share cluster ID."""
    text = "FastAPI 0.110 introduces performance optimizations for async request parsing."
    norm_text = normalize_source_text(text)
    c_hash = compute_source_content_hash(norm_text)

    id1 = uuid.uuid4()
    id2 = uuid.uuid4()
    id3 = uuid.uuid4()

    sources = [
        {
            "id": id1,
            "title": "FastAPI Release Notes 0.110",
            "publisher": "Tech Wire Global",
            "url": "https://techwire.com/fastapi-0110",
            "content_excerpt": text,
            "content_hash": c_hash,
        },
        # Syndicated repost on partner domain with exact same excerpt
        {
            "id": id2,
            "title": "FastAPI Release Notes 0.110 (Repost)",
            "publisher": "Tech Wire Global (Asia)",
            "url": "https://techwire.asia/fastapi-0110",
            "content_excerpt": text,
            "content_hash": c_hash,
        },
        # Independent original review from different publisher
        {
            "id": id3,
            "title": "Benchmarking Modern Python Web Frameworks",
            "publisher": "Engineering Deep Dives",
            "url": "https://engdeepdives.org/benchmarks-2026",
            "content_excerpt": "In our internal stress tests, ASGI throughput showed marked improvements across endpoints.",
            "content_hash": compute_source_content_hash("In our internal stress tests..."),
        },
    ]

    clusters = cluster_source_independence(sources)

    # Source 1 and Source 2 must be clustered together
    assert clusters[id1] == clusters[id2]
    # Source 3 must be in a distinct cluster
    assert clusters[id3] != clusters[id1]
    assert len(set(clusters.values())) == 2
