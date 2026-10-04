"""Research sufficiency is enforced on creation and exact historical generation pins."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application import content_service
from omega.application.content_provider import TemplateContentProvider
from omega.domain.content import ContentGenerationRequestCreate
from omega.infrastructure.models import (
    ChannelDNARevision,
    ContentGenerationRequest,
    ResearchBrief,
    ScriptVersion,
)
from omega.main import app
from tests.research_fixtures import create_research_case


def _brief(channel, topic, request, outcome, **kwargs):
    return ResearchBrief(
        id=uuid.uuid4(),
        channel_id=uuid.UUID(channel),
        topic_candidate_id=uuid.UUID(topic),
        research_request_id=uuid.UUID(request),
        title="Pinned research",
        summary="Immutable research evidence",
        outcome=outcome,
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["SUFFICIENT", "PARTIAL", "INSUFFICIENT"])
async def test_create_content_requires_sufficient_research(db_session: AsyncSession, outcome):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel, topic, request = await create_research_case(client)
        brief = _brief(channel, topic, request, outcome)
        db_session.add(brief)
        await db_session.commit()
        response = await client.post(
            f"/api/v1/channels/{channel}/content",
            json={
                "topic_candidate_id": topic,
                "research_brief_id": str(brief.id),
            },
        )
        if outcome == "SUFFICIENT":
            assert response.status_code == 201, response.text
            assert response.json()["research_brief_id"] == str(brief.id)
        else:
            assert response.status_code == 400, response.text
            assert "requires a SUFFICIENT ResearchBrief" in response.json()["detail"]
            assert outcome in response.json()["detail"]
            assert (
                await db_session.scalar(select(func.count()).select_from(ContentGenerationRequest))
            ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["PARTIAL", "INSUFFICIENT"])
async def test_historical_non_sufficient_pin_cannot_generate_regenerate_or_replay(
    db_session: AsyncSession, outcome
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel, topic, request = await create_research_case(client)
        pinned = _brief(channel, topic, request, outcome, version=1, is_current=False)
        newer = _brief(
            channel, topic, request, "SUFFICIENT", version=2, supersedes_brief_id=pinned.id
        )
        db_session.add(pinned)
        await db_session.flush()
        db_session.add(newer)
        revision = (
            await db_session.scalars(
                select(ChannelDNARevision).where(
                    ChannelDNARevision.channel_id == uuid.UUID(channel)
                )
            )
        ).one()
        historical = ContentGenerationRequest(
            id=uuid.uuid4(),
            channel_id=uuid.UUID(channel),
            topic_candidate_id=uuid.UUID(topic),
            research_brief_id=pinned.id,
            channel_dna_revision_id=revision.id,
            mode="INTERACTIVE",
            status="DRAFT",
            idempotency_key=f"historical-{uuid.uuid4()}",
        )
        db_session.add(historical)
        await db_session.commit()
        provider = Mock(spec=TemplateContentProvider)
        with pytest.raises(ValueError, match="requires a SUFFICIENT ResearchBrief"):
            await content_service.generate_content(
                db_session, uuid.UUID(channel), historical.id, provider=provider
            )
        assert provider.mock_calls == []
        for action in ("generate", "regenerate"):
            response = await client.post(
                f"/api/v1/channels/{channel}/content/{historical.id}/{action}"
            )
            assert response.status_code == 400, response.text
            assert str(pinned.id) in response.json()["detail"]
            assert outcome in response.json()["detail"]
        # Idempotency cannot bypass the guard or replace the historical pin with the newer brief.
        with pytest.raises(ValueError, match="requires a SUFFICIENT ResearchBrief"):
            await content_service.create_request(
                db_session,
                uuid.UUID(channel),
                ContentGenerationRequestCreate(
                    topic_candidate_id=uuid.UUID(topic), research_brief_id=newer.id
                ),
                idempotency_key=historical.idempotency_key,
            )
        await db_session.refresh(historical)
        assert historical.research_brief_id == pinned.id
        assert historical.status == "DRAFT"
        assert (await db_session.scalar(select(func.count()).select_from(ScriptVersion))) == 0


@pytest.mark.asyncio
async def test_historical_sufficient_brief_remains_authoritative_even_if_newer_brief_is_insufficient(
    db_session: AsyncSession,
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel, topic, request = await create_research_case(client)
        pinned = _brief(channel, topic, request, "SUFFICIENT", version=1, is_current=False)
        newer = _brief(
            channel, topic, request, "INSUFFICIENT", version=2, supersedes_brief_id=pinned.id
        )
        db_session.add(pinned)
        await db_session.flush()
        db_session.add(newer)
        await db_session.commit()
        response = await client.post(
            f"/api/v1/channels/{channel}/content",
            json={
                "topic_candidate_id": topic,
                "research_brief_id": str(pinned.id),
            },
        )
        assert response.status_code == 201, response.text
        content = response.json()
        for action in ("generate", "regenerate"):
            result = await client.post(
                f"/api/v1/channels/{channel}/content/{content['id']}/{action}"
            )
            assert result.status_code == 200, result.text
        persisted = (await client.get(f"/api/v1/channels/{channel}/content/{content['id']}")).json()
        assert persisted["research_brief_id"] == str(pinned.id)
