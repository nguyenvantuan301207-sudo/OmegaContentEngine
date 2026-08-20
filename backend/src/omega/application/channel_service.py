"""Channel Application Service.

Encapsulates channel lifecycle management, strongly validated DNA updates,
atomic monotonic revision incrementing, and ChannelContext projection.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.channel import (
    SUPPORTED_PLATFORMS,
    ChannelCreate,
    ChannelDNARevisionResponse,
    ChannelResponse,
    ChannelState,
    ChannelUpdate,
    Platform,
    validate_channel_transition,
    validate_slug,
)
from omega.domain.channel_context import ChannelContext
from omega.domain.channel_dna import ChannelDNA
from omega.infrastructure.models import Channel, ChannelDNARevision
from omega.logging import get_logger

logger = get_logger(service="omega-channel-service")


def _generate_slug_from_name(name: str) -> str:
    """Generate a clean URL-safe slug from a channel name."""
    clean = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", clean).strip("-")
    if not slug:
        slug = f"channel-{uuid.uuid4().hex[:8]}"
    elif len(slug) < 3:
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    return slug[:100]


async def create_channel(session: AsyncSession, create_in: ChannelCreate) -> ChannelResponse:
    """Create a new Channel in DRAFT state with initial DNA and Revision 1."""
    # 1. Platform validation (strictly YOUTUBE for OMEGA-003)
    if create_in.platform not in SUPPORTED_PLATFORMS:
        raise ValueError(
            f"Platform '{create_in.platform.value}' is reserved and not supported in OMEGA-003. Only 'YOUTUBE' is supported."
        )

    # 2. Slug handling & uniqueness
    slug = create_in.slug
    if not slug:
        slug = _generate_slug_from_name(create_in.name)
    slug = validate_slug(slug)

    existing_slug = await session.execute(select(Channel).where(Channel.slug == slug))
    if existing_slug.scalar_one_or_none():
        raise ValueError(f"Channel slug '{slug}' is already in use. Please choose a unique slug.")

    # 3. DNA initialization
    dna = create_in.dna
    if dna is None:
        dna = ChannelDNA.create_default(
            niche="General",
            language=create_in.primary_language,
            region=create_in.target_region,
        )

    dna_dict = dna.model_dump()

    # 4. Create Channel entity
    channel_id = uuid.uuid4()
    channel = Channel(
        id=channel_id,
        name=create_in.name,
        slug=slug,
        description=create_in.description,
        state=ChannelState.DRAFT.value,
        platform=create_in.platform.value,
        platform_channel_id=create_in.platform_channel_id,
        primary_language=create_in.primary_language,
        target_region=create_in.target_region,
        timezone=create_in.timezone,
        dna=dna_dict,
        metadata_=create_in.metadata,
    )
    session.add(channel)

    # 5. Create Initial Revision (Version 1)
    rev = ChannelDNARevision(
        id=uuid.uuid4(),
        channel_id=channel_id,
        version=1,
        snapshot=dna_dict,
        change_reason="Initial channel creation",
        actor="USER",
    )
    session.add(rev)

    await session.commit()
    logger.info("Channel created", channel_id=str(channel.id), slug=channel.slug)

    fresh_res = await session.execute(select(Channel).where(Channel.id == channel.id))
    return ChannelResponse.model_validate(fresh_res.scalar_one())


async def get_channel(session: AsyncSession, channel_id: UUID) -> ChannelResponse | None:
    """Retrieve channel by ID."""
    res = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = res.scalar_one_or_none()
    if not channel:
        return None
    return ChannelResponse.model_validate(channel)


async def get_channel_by_slug(session: AsyncSession, slug: str) -> ChannelResponse | None:
    """Retrieve channel by unique slug."""
    res = await session.execute(select(Channel).where(Channel.slug == slug))
    channel = res.scalar_one_or_none()
    if not channel:
        return None
    return ChannelResponse.model_validate(channel)


async def list_channels(
    session: AsyncSession,
    state: str | None = None,
    platform: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ChannelResponse]:
    """List channels with optional filtering and pagination."""
    query = select(Channel).order_by(Channel.created_at.desc()).limit(limit).offset(offset)
    if state:
        query = query.where(Channel.state == state)
    if platform:
        query = query.where(Channel.platform == platform)

    res = await session.execute(query)
    channels = res.scalars().all()
    return [ChannelResponse.model_validate(c) for c in channels]


async def update_channel(
    session: AsyncSession, channel_id: UUID, update_in: ChannelUpdate
) -> ChannelResponse | None:
    """Update mutable identity attributes of a channel (slug is strictly immutable)."""
    res = await session.execute(select(Channel).where(Channel.id == channel_id).with_for_update())
    channel = res.scalar_one_or_none()
    if not channel:
        return None

    if channel.state == ChannelState.ARCHIVED.value:
        raise ValueError("Cannot update an archived channel.")

    if update_in.name is not None:
        channel.name = update_in.name
    if update_in.description is not None:
        channel.description = update_in.description
    if update_in.platform_channel_id is not None:
        channel.platform_channel_id = update_in.platform_channel_id
    if update_in.primary_language is not None:
        channel.primary_language = update_in.primary_language
    if update_in.target_region is not None:
        channel.target_region = update_in.target_region
    if update_in.timezone is not None:
        channel.timezone = update_in.timezone
    if update_in.metadata is not None:
        channel.metadata_ = update_in.metadata

    channel.updated_at = datetime.now(UTC)
    await session.commit()
    logger.info("Channel updated", channel_id=str(channel.id))

    fresh_res = await session.execute(select(Channel).where(Channel.id == channel_id))
    return ChannelResponse.model_validate(fresh_res.scalar_one())


async def activate_channel(session: AsyncSession, channel_id: UUID) -> ChannelResponse | None:
    """Activate a channel (transitions DRAFT or PAUSED -> ACTIVE)."""
    res = await session.execute(select(Channel).where(Channel.id == channel_id).with_for_update())
    channel = res.scalar_one_or_none()
    if not channel:
        return None

    validate_channel_transition(ChannelState(channel.state), ChannelState.ACTIVE)
    old_state = channel.state
    channel.state = ChannelState.ACTIVE.value
    channel.updated_at = datetime.now(UTC)

    await session.commit()
    logger.info(
        "Channel activated",
        channel_id=str(channel.id),
        old_state=old_state,
        new_state=channel.state,
    )

    fresh_res = await session.execute(select(Channel).where(Channel.id == channel_id))
    return ChannelResponse.model_validate(fresh_res.scalar_one())


async def pause_channel(session: AsyncSession, channel_id: UUID) -> ChannelResponse | None:
    """Pause an active channel (transitions ACTIVE -> PAUSED)."""
    res = await session.execute(select(Channel).where(Channel.id == channel_id).with_for_update())
    channel = res.scalar_one_or_none()
    if not channel:
        return None

    validate_channel_transition(ChannelState(channel.state), ChannelState.PAUSED)
    old_state = channel.state
    channel.state = ChannelState.PAUSED.value
    channel.updated_at = datetime.now(UTC)

    await session.commit()
    logger.info(
        "Channel paused",
        channel_id=str(channel.id),
        old_state=old_state,
        new_state=channel.state,
    )

    fresh_res = await session.execute(select(Channel).where(Channel.id == channel_id))
    return ChannelResponse.model_validate(fresh_res.scalar_one())


async def archive_channel(session: AsyncSession, channel_id: UUID) -> ChannelResponse | None:
    """Archive a channel (transitions DRAFT, ACTIVE, or PAUSED -> ARCHIVED)."""
    res = await session.execute(select(Channel).where(Channel.id == channel_id).with_for_update())
    channel = res.scalar_one_or_none()
    if not channel:
        return None

    validate_channel_transition(ChannelState(channel.state), ChannelState.ARCHIVED)
    now = datetime.now(UTC)
    old_state = channel.state
    channel.state = ChannelState.ARCHIVED.value
    channel.archived_at = now
    channel.updated_at = now

    await session.commit()
    logger.info(
        "Channel archived",
        channel_id=str(channel.id),
        old_state=old_state,
        new_state=channel.state,
    )

    fresh_res = await session.execute(select(Channel).where(Channel.id == channel_id))
    return ChannelResponse.model_validate(fresh_res.scalar_one())


async def get_channel_dna(session: AsyncSession, channel_id: UUID) -> ChannelDNA | None:
    """Retrieve active validated Channel DNA."""
    res = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = res.scalar_one_or_none()
    if not channel:
        return None
    return ChannelDNA.model_validate(channel.dna)


async def update_channel_dna(
    session: AsyncSession,
    channel_id: UUID,
    new_dna: ChannelDNA,
    change_reason: str,
    actor: str = "USER",
) -> ChannelDNA | None:
    """Update Channel DNA, atomically incrementing revision version under row lock."""
    # 1. Lock channel row
    res = await session.execute(select(Channel).where(Channel.id == channel_id).with_for_update())
    channel = res.scalar_one_or_none()
    if not channel:
        return None

    if channel.state == ChannelState.ARCHIVED.value:
        raise ValueError("Cannot update DNA for an archived channel.")

    if not change_reason or len(change_reason.strip()) < 3:
        raise ValueError("A change reason of at least 3 characters is mandatory for DNA updates.")

    # 2. Atomically calculate next version
    max_ver_res = await session.execute(
        select(func.coalesce(func.max(ChannelDNARevision.version), 0)).where(
            ChannelDNARevision.channel_id == channel_id
        )
    )
    current_max = max_ver_res.scalar_one()
    next_version = current_max + 1

    dna_dict = new_dna.model_dump()

    # 3. Update Channel active DNA
    channel.dna = dna_dict
    channel.updated_at = datetime.now(UTC)

    # 4. Insert immutable revision
    rev = ChannelDNARevision(
        id=uuid.uuid4(),
        channel_id=channel_id,
        version=next_version,
        snapshot=dna_dict,
        change_reason=change_reason.strip(),
        actor=actor,
    )
    session.add(rev)

    await session.commit()
    logger.info(
        "Channel DNA updated",
        channel_id=str(channel_id),
        version=next_version,
        change_reason=change_reason,
        actor=actor,
    )

    return new_dna


async def list_dna_revisions(
    session: AsyncSession, channel_id: UUID
) -> list[ChannelDNARevisionResponse]:
    """List all historical DNA revisions for a channel ordered by version descending."""
    res = await session.execute(
        select(ChannelDNARevision)
        .where(ChannelDNARevision.channel_id == channel_id)
        .order_by(ChannelDNARevision.version.desc())
    )
    revisions = res.scalars().all()
    return [ChannelDNARevisionResponse.model_validate(r) for r in revisions]


async def get_channel_context(session: AsyncSession, channel_id: UUID) -> ChannelContext | None:
    """Resolve unified ChannelContext with active DNA and latest revision version."""
    res = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = res.scalar_one_or_none()
    if not channel:
        return None

    ver_res = await session.execute(
        select(func.coalesce(func.max(ChannelDNARevision.version), 1)).where(
            ChannelDNARevision.channel_id == channel_id
        )
    )
    active_version = ver_res.scalar_one()

    return ChannelContext(
        channel_id=channel.id,
        name=channel.name,
        slug=channel.slug,
        platform=Platform(channel.platform),
        state=ChannelState(channel.state),
        primary_language=channel.primary_language,
        target_region=channel.target_region,
        timezone=channel.timezone,
        dna=ChannelDNA.model_validate(channel.dna),
        active_dna_version=active_version,
    )
