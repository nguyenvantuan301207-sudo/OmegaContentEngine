"""Real qualifying research fixtures for tests that exercise the content boundary."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def create_research_case(
    client: AsyncClient, minimum_quality: float = 50.0
) -> tuple[str, str, str]:
    channel = await client.post(
        "/api/v1/channels",
        json={
            "name": "Research Authority Test",
            "slug": f"research-gate-{uuid.uuid4().hex[:8]}",
            "platform": "YOUTUBE",
        },
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    assert (await client.post(f"/api/v1/channels/{channel_id}/activate")).status_code == 200
    topic = await client.post(
        f"/api/v1/channels/{channel_id}/topics/candidates",
        json={
            "title": "Research Authority",
            "summary": "Qualifying evidence and immutable brief pinning",
        },
    )
    assert topic.status_code == 201, topic.text
    topic_id = topic.json()["id"]
    assert (
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{topic_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
    ).status_code == 200
    request = await client.post(
        f"/api/v1/channels/{channel_id}/research",
        json={"topic_candidate_id": topic_id, "minimum_source_quality": minimum_quality},
    )
    assert request.status_code == 201, request.text
    return channel_id, topic_id, request.json()["id"]


async def seed_sufficient_research(client: AsyncClient, channel_id: str, request_id: str) -> None:
    """Add two independent, qualifying sources supporting two verifiable facts."""
    base = f"/api/v1/channels/{channel_id}/research/{request_id}"
    source_ids = []
    for index, excerpt in enumerate(
        [
            "Measured laboratory samples retain their original identifiers throughout instrument calibration.",
            "Archive custodians document publication dates alongside catalog references in quarterly registers.",
        ]
    ):
        response = await client.post(
            f"{base}/sources",
            json={
                "title": f"Independent primary record {index}",
                "publisher": f"Independent research publisher {index}",
                "url": f"https://research-fixture-{index}.example/record",
                "primary_source_status": "CONFIRMED",
                "content_excerpt": excerpt,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["quality_score"] >= 50.0
        source_ids.append(response.json()["id"])
    for text in [
        "Records preserve original sample identifiers.",
        "Records document original publication dates.",
    ]:
        response = await client.post(
            f"{base}/claims",
            json={
                "claim_text": text,
                "claim_type": "FACT",
                "evidence": [
                    {"source_id": source_id, "excerpt": text, "strength_score": 95.0}
                    for source_id in source_ids
                ],
            },
        )
        assert response.status_code == 201, response.text
