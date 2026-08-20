"""Integration tests for Versioned Immutable ResearchBrief revisions."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_versioned_immutable_brief_revisions(db_session: AsyncSession) -> None:
    """Test that rerun creates version vN+1 while preserving historical vN immutably."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup Channel & Evaluated Topic
        slug = f"revisions-chan-{uuid.uuid4().hex[:8]}"
        chan_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Revision Test Channel",
                "slug": slug,
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
            },
        )
        channel_id = chan_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={
                "title": "Async SQLAlchemy 2.0 with PostgreSQL In-Depth",
                "keywords": ["SQLAlchemy", "Async", "PostgreSQL"],
            },
        )
        cand_id = cand_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )

        # 2. Create Research Request
        req_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": cand_id},
        )
        req_id = req_res.json()["id"]

        # Add Source 1
        src1_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/sources",
            json={
                "title": "SQLAlchemy 2.0 AsyncIO Documentation",
                "publisher": "SQLAlchemy Authors",
                "primary_source_status": "CONFIRMED",
                "content_excerpt": "SQLAlchemy 2.0 introduces native AsyncEngine with greenlet integration.",
            },
        )
        assert src1_res.status_code == 201
        assert "id" in src1_res.json()

        # 3. Initial Run -> Brief v1
        run1_res = await client.post(f"/api/v1/channels/{channel_id}/research/{req_id}/run")
        assert run1_res.status_code == 200
        brief_v1 = run1_res.json()
        assert brief_v1["version"] == 1
        assert brief_v1["is_current"] is True
        assert brief_v1["supersedes_brief_id"] is None
        v1_id = brief_v1["id"]
        v1_summary = brief_v1["summary"]

        # 4. Add Source 2
        await client.post(
            f"/api/v1/channels/{channel_id}/research/{req_id}/sources",
            json={
                "title": "Production AsyncIO Patterns",
                "publisher": "Engineering Review",
                "primary_source_status": "CLAIMED",
                "content_excerpt": "AsyncSession pooling provides resilient database transaction lifecycles.",
            },
        )

        # 5. Rerun Research -> Brief v2
        run2_res = await client.post(f"/api/v1/channels/{channel_id}/research/{req_id}/run")
        assert run2_res.status_code == 200
        brief_v2 = run2_res.json()
        assert brief_v2["version"] == 2
        assert brief_v2["is_current"] is True
        assert brief_v2["supersedes_brief_id"] == v1_id

        # 6. Verify Brief v1 is preserved immutably
        get_v1_res = await client.get(
            f"/api/v1/channels/{channel_id}/research/{req_id}/brief?version=1"
        )
        assert get_v1_res.status_code == 200
        v1_persisted = get_v1_res.json()
        assert v1_persisted["version"] == 1
        assert v1_persisted["is_current"] is False
        assert v1_persisted["summary"] == v1_summary  # Unchanged!

        # 7. Verify GET /brief returns v2 as current
        get_curr_res = await client.get(f"/api/v1/channels/{channel_id}/research/{req_id}/brief")
        assert get_curr_res.status_code == 200
        assert get_curr_res.json()["version"] == 2

        # 8. Verify GET /briefs lists both revisions
        list_briefs_res = await client.get(
            f"/api/v1/channels/{channel_id}/research/{req_id}/briefs"
        )
        assert list_briefs_res.status_code == 200
        briefs_list = list_briefs_res.json()
        assert len(briefs_list) == 2
        assert briefs_list[0]["version"] == 2
        assert briefs_list[1]["version"] == 1
