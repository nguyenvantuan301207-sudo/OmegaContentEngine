"""Canonical research must filter authority while preserving stored evidence/history."""

from __future__ import annotations

import copy
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.models import ClaimEvidence, ResearchRequest, ResearchSource
from omega.main import app
from tests.research_fixtures import create_research_case


async def _sources(client, session, base, qualities, *, syndicated=False):
    excerpts = [
        "Laboratory calibration records preserve instrument serial identifiers and sample measurements.",
        "Ocean current observations document seasonal tides near the eastern harbor entrance.",
        "Historical archive custodians retain catalog references beside original publication dates.",
    ]
    ids = []
    for index, quality in enumerate(qualities):
        response = await client.post(
            f"{base}/sources",
            json={
                "title": f"Record {index}",
                "publisher": f"Publisher {index}",
                "url": f"https://record-{index}.example/report",
                "primary_source_status": "CONFIRMED",
                "content_excerpt": (
                    excerpts[0] + f" Record variant {index}." if syndicated else excerpts[index]
                ),
                "metadata": {
                    "claims": [
                        {"text": f"Automatic claim from record {index}.", "strength_score": 100.0}
                    ]
                },
            },
        )
        assert response.status_code == 201, response.text
        source_id = uuid.UUID(response.json()["id"])
        source = await session.get(ResearchSource, source_id)
        source.quality_score = (
            quality  # Exact persisted boundary fixtures, not a production rewrite.
        )
        ids.append(source_id)
    await session.commit()
    return ids


async def _claims(client, base, supporting_ids, contradicting_id=None):
    for text in [
        "Original identifiers remain traceable.",
        "Original publication dates remain traceable.",
    ]:
        evidence = [
            {"source_id": str(source_id), "excerpt": text, "strength_score": 95.0}
            for source_id in supporting_ids
        ]
        if contradicting_id:
            evidence.append(
                {
                    "source_id": str(contradicting_id),
                    "excerpt": "Contradictory record disputes this fact.",
                    "strength_score": 100.0,
                    "support_direction": "CONTRADICTS",
                }
            )
        response = await client.post(
            f"{base}/claims", json={"claim_text": text, "evidence": evidence}
        )
        assert response.status_code == 201, response.text


async def _stored_snapshot(session):
    session.expire_all()
    result = {}
    for model in (ResearchSource, ClaimEvidence):
        rows = (await session.scalars(select(model).order_by(model.id))).all()
        result[model.__name__] = [
            copy.deepcopy(
                {column.key: getattr(row, column.key) for column in model.__mapper__.column_attrs}
            )
            for row in rows
        ]
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_claims", [False, True])
async def test_no_qualifying_sources_fail_closed_without_rewriting_evidence(
    db_session: AsyncSession, manual_claims
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel, _, request = await create_research_case(client)
        base = f"/api/v1/channels/{channel}/research/{request}"
        ids = await _sources(client, db_session, base, [44.50, 48.50])
        if manual_claims:
            await _claims(client, base, ids)
        before = await _stored_snapshot(db_session)
        response = await client.post(f"{base}/run")
        assert response.status_code == 200, response.text
        brief = response.json()
        assert brief["outcome"] == "INSUFFICIENT"
        assert brief["overall_confidence"] == 0.0
        assert brief["verified_claims"] == []
        assert len(brief["uncertain_claims"]) == (2 if manual_claims else 0)
        assert brief["sources_summary"] == {
            "total_sources": 2,
            "eligible_sources": 0,
            "below_quality_threshold_sources": 2,
            "independent_clusters": 0,
            "high_quality_sources": 0,
            "confirmed_primary_sources": 0,
        }
        claims = (await client.get(f"{base}/claims")).json()
        assert len(claims) == (2 if manual_claims else 0)
        for claim in claims:
            assert not claim["is_verified"]
            assert claim["confidence_score"] == 0.0
            assert claim["supporting_sources_count"] == 0
            assert len(claim["evidence"]) == 2
        assert await _stored_snapshot(db_session) == before
        assert len((await client.get(f"{base}/sources")).json()) == 2


@pytest.mark.asyncio
async def test_threshold_inclusive_and_low_quality_support_and_conflicts_excluded(
    db_session: AsyncSession,
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel, _, request = await create_research_case(client)
        base = f"/api/v1/channels/{channel}/research/{request}"
        ids = await _sources(client, db_session, base, [50.0, 90.0, 49.99])
        await _claims(client, base, ids, contradicting_id=ids[2])
        before = await _stored_snapshot(db_session)
        response = await client.post(f"{base}/run")
        assert response.status_code == 200, response.text
        brief = response.json()
        assert brief["outcome"] == "SUFFICIENT"
        assert brief["overall_confidence"] == 92.5
        assert brief["sources_summary"]["eligible_sources"] == 2
        assert brief["sources_summary"]["below_quality_threshold_sources"] == 1
        assert brief["sources_summary"]["independent_clusters"] == 2
        assert brief["contradictions"] == []
        assert len(brief["verified_claims"]) == 2
        for claim in brief["verified_claims"]:
            assert {citation["source_id"] for citation in claim["citations"]} == {
                str(s) for s in ids[:2]
            }
        for claim in (await client.get(f"{base}/claims")).json():
            assert claim["independent_sources_count"] == 2
            assert claim["supporting_sources_count"] == 2
            assert claim["contradicting_sources_count"] == 0
            assert len(claim["evidence"]) == 4
        assert await _stored_snapshot(db_session) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("syndicated", [False, True])
async def test_outcome_uses_only_eligible_independent_clusters(
    db_session: AsyncSession, syndicated
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel, _, request = await create_research_case(client)
        base = f"/api/v1/channels/{channel}/research/{request}"
        ids = await _sources(
            client,
            db_session,
            base,
            [90.0, 90.0 if syndicated else 49.0, 48.0],
            syndicated=syndicated,
        )
        await _claims(client, base, ids)
        response = await client.post(f"{base}/run")
        assert response.status_code == 200, response.text
        brief = response.json()
        assert brief["outcome"] == "PARTIAL"
        assert len(brief["verified_claims"]) == 2
        assert brief["sources_summary"]["total_sources"] == 3
        assert brief["sources_summary"]["eligible_sources"] == (2 if syndicated else 1)
        assert brief["sources_summary"]["independent_clusters"] == 1
        for claim in (await client.get(f"{base}/claims")).json():
            assert claim["independent_sources_count"] == 1


@pytest.mark.asyncio
async def test_automatic_extraction_uses_only_eligible_sources_and_brief_history_is_immutable(
    db_session: AsyncSession,
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel, _, request = await create_research_case(client)
        base = f"/api/v1/channels/{channel}/research/{request}"
        ids = await _sources(client, db_session, base, [90.0, 90.0, 49.0])
        first_response = await client.post(f"{base}/run")
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        assert first["outcome"] == "SUFFICIENT"
        claims = (await client.get(f"{base}/claims")).json()
        assert len(claims) == 2
        assert {e["source_id"] for c in claims for e in c["evidence"]} == {str(s) for s in ids[:2]}
        before = await _stored_snapshot(db_session)
        req = await db_session.get(ResearchRequest, uuid.UUID(request))
        req.minimum_source_quality = 95.0
        await db_session.commit()
        second_response = await client.post(f"{base}/run")
        assert second_response.status_code == 200, second_response.text
        second = second_response.json()
        assert second["outcome"] == "INSUFFICIENT"
        assert second["version"] == 2
        assert second["supersedes_brief_id"] == first["id"]
        assert second["overall_confidence"] == 0.0
        historical = (await client.get(f"{base}/brief?version=1")).json()
        assert historical == {**first, "is_current": False}
        assert await _stored_snapshot(db_session) == before
