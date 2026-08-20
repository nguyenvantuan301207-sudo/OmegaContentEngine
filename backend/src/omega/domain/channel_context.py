"""ChannelContext projection.

Provides a unified, read-only operational context combining channel identity,
active Channel DNA, and revision metadata for downstream consumers.
This is the domain layer — zero infrastructure dependencies.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from omega.domain.channel import ChannelState, Platform
from omega.domain.channel_dna import ChannelDNA


class ChannelContext(BaseModel):
    """Unified operational context consumed by planners, executors, and downstream engines."""

    channel_id: UUID
    name: str
    slug: str
    platform: Platform
    state: ChannelState
    primary_language: str
    target_region: str
    timezone: str
    dna: ChannelDNA
    active_dna_version: int

    model_config = ConfigDict(from_attributes=True)
