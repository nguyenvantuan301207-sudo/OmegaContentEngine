"""Integration tests verifying strict citation and provenance invariants."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application import content_service
from omega.domain.content import ContentGenerationRequestCreate
from omega.main import app


class ForeignBriefLeakingProvider:
    """Malicious/faulty mock provider that attempts to inject citations from a foreign brief."""

    def __init__(self, foreign_brief_id: uuid.UUID) -> None:
        self.foreign_brief_id = foreign_brief_id

    def generate_intent(self, *args, **kwargs):
        return {
            "primary_goal": "Goal",
            "audience_intent": "Intent",
            "viewer_promise": "Promise",
            "central_question": "Question",
            "core_takeaway": "Takeaway",
            "tone": "AUTHORITATIVE",
            "pace": "MODERATE",
            "complexity": "INTERMEDIATE",
            "desired_emotion": "Excited",
            "call_to_action_type": "SUBSCRIBE",
        }

    def generate_hooks(self, *args, **kwargs):
        return [
            {
                "hook_variant_index": 0,
                "text": "Hook text",
                "hook_type": "QUESTION",
                "score": 90.0,
                "reason_codes": [],
                "selected": True,
                "citations": [],
            }
        ]

    def generate_outline(self, *args, **kwargs):
        return {
            "opening_description": "Opening",
            "sections": [
                {
                    "section_id": "sec_1",
                    "title": "Sec 1",
                    "objective": "Obj",
                    "key_points": [],
                    "claim_refs": [],
                    "estimated_duration_seconds": 60,
                    "transition": "Trans",
                    "retention_goal": "Goal",
                }
            ],
            "closing_description": "Closing",
        }

    def generate_script(self, *args, **kwargs):
        return {
            "title": "Leaking Script",
            "hook_id": None,
            "hook_text": "Hook text",
            "closing_text": "Closing text",
            "cta_text": "CTA text",
            "estimated_word_count": 100,
            "estimated_duration_seconds": 60,
            "style_snapshot": {},
            "sections": [
                {
                    "section_order": 1,
                    "heading": "Sec 1",
                    "narration_text": "Leaked statement text.",
                    "estimated_duration_seconds": 60,
                    "statements": [
                        {
                            "statement_order": 1,
                            "statement_text": "Factual statement citing foreign research brief.",
                            "statement_type": "FACTUAL",
                            "qualification_note": None,
                            "citations": [
                                {
                                    "research_brief_id": str(
                                        self.foreign_brief_id
                                    ),  # Foreign Brief!
                                    "claim_id": str(uuid.uuid4()),
                                    "evidence_id": str(uuid.uuid4()),
                                    "source_id": str(uuid.uuid4()),
                                }
                            ],
                        }
                    ],
                }
            ],
        }


@pytest.mark.asyncio
async def test_cross_brief_citation_rejection(db_session: AsyncSession) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup Channel, Topic, and Genuine Brief
        slug = f"cit-leak-{uuid.uuid4().hex[:8]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={"name": "Citation Invariants Channel", "slug": slug, "platform": "YOUTUBE"},
        )
        channel_id = uuid.UUID(c_res.json()["id"])
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Invariants Topic", "summary": "Citation invariants"},
        )
        cand_id = uuid.UUID(cand_res.json()["id"])
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        r_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": str(cand_id), "research_question": "Question"},
        )
        research_req_id = r_res.json()["id"]

        run_res = await client.post(f"/api/v1/channels/{channel_id}/research/{research_req_id}/run")
        genuine_brief_id = uuid.UUID(run_res.json()["id"])

        # 2. Create Content Request
        req_in = ContentGenerationRequestCreate(
            topic_candidate_id=cand_id,
            research_brief_id=genuine_brief_id,
            content_type="YOUTUBE_LONGFORM",
        )
        content_req = await content_service.create_request(db_session, channel_id, req_in)

        # 3. Attempt Generation with Malicious Provider Injecting a Foreign Brief Citation
        foreign_brief_id = uuid.uuid4()
        leaking_provider = ForeignBriefLeakingProvider(foreign_brief_id)

        with pytest.raises(ValueError, match="Cross-brief citation rejected"):
            await content_service.generate_content(
                session=db_session,
                channel_id=channel_id,
                request_id=content_req.id,
                provider=leaking_provider,
            )


@pytest.mark.asyncio
async def test_cross_channel_request_creation_rejection(db_session: AsyncSession) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Channel 1
        c1_res = await client.post(
            "/api/v1/channels",
            json={"name": "Chan 1", "slug": f"c1-{uuid.uuid4().hex[:6]}", "platform": "YOUTUBE"},
        )
        chan1_id = c1_res.json()["id"]
        await client.post(f"/api/v1/channels/{chan1_id}/activate")

        # Channel 2
        c2_res = await client.post(
            "/api/v1/channels",
            json={"name": "Chan 2", "slug": f"c2-{uuid.uuid4().hex[:6]}", "platform": "YOUTUBE"},
        )
        chan2_id = c2_res.json()["id"]
        await client.post(f"/api/v1/channels/{chan2_id}/activate")

        # Create topic & brief on Channel 1
        t_res = await client.post(
            f"/api/v1/channels/{chan1_id}/topics/candidates",
            json={"title": "Chan1 Topic"},
        )
        t_id = t_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{chan1_id}/topics/candidates/{t_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        r_res = await client.post(
            f"/api/v1/channels/{chan1_id}/research",
            json={"topic_candidate_id": t_id, "research_question": "Q1"},
        )
        r_id = r_res.json()["id"]
        b_res = await client.post(f"/api/v1/channels/{chan1_id}/research/{r_id}/run")
        brief1_id = b_res.json()["id"]

        # Attempt to create content request on Channel 2 using Channel 1's Topic & Brief
        bad_req_res = await client.post(
            f"/api/v1/channels/{chan2_id}/content",
            json={
                "topic_candidate_id": t_id,
                "research_brief_id": brief1_id,
            },
        )
        assert bad_req_res.status_code == 400
        assert "does not exist for this channel" in bad_req_res.json()["detail"]
