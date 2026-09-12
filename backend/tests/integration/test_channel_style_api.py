"""Integration tests for Channel Style Profile REST API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.main import app


@pytest.mark.asyncio
async def test_channel_style_profile_api_flow(db_session: AsyncSession) -> None:
    slug = f"style-chan-{uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Channel
        res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Style Test Channel",
                "slug": slug,
                "description": "Channel for style profile integration test",
                "platform": "YOUTUBE",
                "primary_language": "en",
                "target_region": "US",
                "timezone": "UTC",
            },
        )
        assert res.status_code == 201
        channel_id = res.json()["id"]

        # 2. Get initial default style profile
        res = await client.get(f"/api/v1/channels/{channel_id}/style-profile")
        assert res.status_code == 200
        data = res.json()
        assert data["channel_id"] == channel_id
        assert data["preset_id"] == "default"
        assert data["custom_subtitle_style"] is None
        assert data["effective_subtitle_style"]["font_family"] == "Arial"
        assert data["effective_subtitle_style"]["alignment"] == 2

        # 3. Update style profile to use a preset
        res = await client.put(
            f"/api/v1/channels/{channel_id}/style-profile",
            json={
                "preset_id": "bold_yellow",
                "notes": "Testing bold yellow preset",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["preset_id"] == "bold_yellow"
        assert data["notes"] == "Testing bold yellow preset"
        assert data["effective_subtitle_style"]["bold"] is True
        assert data["effective_subtitle_style"]["primary_color"] == "#FFD400"

        # 4. Verify GET returns the updated profile
        res = await client.get(f"/api/v1/channels/{channel_id}/style-profile")
        assert res.status_code == 200
        assert res.json()["preset_id"] == "bold_yellow"

        # 5. Non-existent channel returns 404
        bad_id = uuid.uuid4()
        res = await client.get(f"/api/v1/channels/{bad_id}/style-profile")
        assert res.status_code == 404
