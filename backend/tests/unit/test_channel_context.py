"""Unit tests for ChannelContext projection."""

from __future__ import annotations

import uuid

from omega.domain.channel import ChannelState, Platform
from omega.domain.channel_context import ChannelContext
from omega.domain.channel_dna import ChannelDNA


def test_channel_context_construction() -> None:
    """Test ChannelContext model construction and serialization."""
    channel_id = uuid.uuid4()
    dna = ChannelDNA.create_default(niche="Data Science", language="en", region="US")

    context = ChannelContext(
        channel_id=channel_id,
        name="Data Science Daily",
        slug="data-science-daily",
        platform=Platform.YOUTUBE,
        state=ChannelState.ACTIVE,
        primary_language="en",
        target_region="US",
        timezone="UTC",
        dna=dna,
        active_dna_version=2,
    )

    assert context.channel_id == channel_id
    assert context.active_dna_version == 2
    assert context.dna.content_strategy.niche == "Data Science"
    assert context.state == ChannelState.ACTIVE
