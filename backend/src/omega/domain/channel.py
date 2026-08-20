"""Channel domain model and schemas.

Defines Channel states, platforms, goals, metrics, state transitions,
and Pydantic schemas for channel management.
This is the domain layer — zero infrastructure dependencies.
"""

from __future__ import annotations

import enum
import re
import zoneinfo
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from omega.domain.channel_dna import ChannelDNA


class ChannelState(enum.StrEnum):
    """All possible states for a Channel."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class Platform(enum.StrEnum):
    """Supported and reserved platforms.

    Only YOUTUBE is active in OMEGA-003. Other platforms are reserved.
    """

    YOUTUBE = "YOUTUBE"
    # Reserved for future phases
    TIKTOK = "TIKTOK"
    INSTAGRAM = "INSTAGRAM"
    X_TWITTER = "X_TWITTER"


# Supported active platforms for OMEGA-003
SUPPORTED_PLATFORMS: set[Platform] = {Platform.YOUTUBE}


class PrimaryGoal(enum.StrEnum):
    """Primary strategic goal for a channel."""

    GROWTH = "GROWTH"
    ENGAGEMENT = "ENGAGEMENT"
    WATCH_TIME = "WATCH_TIME"
    REVENUE = "REVENUE"
    AUTHORITY = "AUTHORITY"
    LEAD_GENERATION = "LEAD_GENERATION"


class KPIMetricType(enum.StrEnum):
    """Supported metric types for KPI targets."""

    SUBSCRIBER_GROWTH = "SUBSCRIBER_GROWTH"
    VIEWS = "VIEWS"
    WATCH_TIME = "WATCH_TIME"
    CTR = "CTR"
    RETENTION = "RETENTION"
    ENGAGEMENT_RATE = "ENGAGEMENT_RATE"
    REVENUE = "REVENUE"


class FrequencyPeriod(enum.StrEnum):
    """Time unit for structured publishing frequency."""

    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"


# State transition graph for Channel
VALID_CHANNEL_TRANSITIONS: dict[ChannelState, set[ChannelState]] = {
    ChannelState.DRAFT: {ChannelState.ACTIVE, ChannelState.ARCHIVED},
    ChannelState.ACTIVE: {ChannelState.PAUSED, ChannelState.ARCHIVED},
    ChannelState.PAUSED: {ChannelState.ACTIVE, ChannelState.ARCHIVED},
    # ARCHIVED is terminal for MVP
    ChannelState.ARCHIVED: set(),
}


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: str, target: str, entity: str = "Channel") -> None:
        self.current = current
        self.target = target
        self.entity = entity
        super().__init__(f"Invalid {entity} state transition: {current} -> {target}")


def validate_channel_transition(current: ChannelState, target: ChannelState) -> None:
    """Validate that a channel transition from current to target is permissible."""
    if target not in VALID_CHANNEL_TRANSITIONS.get(current, set()):
        raise InvalidStateTransitionError(
            current=current.value, target=target.value, entity="Channel"
        )


# Slug format: lowercase alphanumeric with single hyphens, 3 to 100 chars
SLUG_REGEX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Language tag format (normalized BCP-47 subset: en, vi, en-US, pt-BR, es-419, zh-Hans)
LANGUAGE_TAG_REGEX = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2}|-[A-Z][a-z]{3}|-[0-9]{3})?$")

# Region tag format (ISO-3166-1 alpha-2)
REGION_TAG_REGEX = re.compile(r"^[A-Z]{2}$")


def validate_slug(slug: str) -> str:
    """Validate channel slug format."""
    normalized = slug.strip().lower()
    if not (3 <= len(normalized) <= 100):
        raise ValueError("Slug must be between 3 and 100 characters long.")
    if not SLUG_REGEX.match(normalized):
        raise ValueError(
            "Slug must contain only lowercase alphanumeric characters separated by single hyphens."
        )
    return normalized


def validate_language_tag(lang: str) -> str:
    """Validate language tag against supported BCP-47 normalized subset."""
    normalized = lang.strip()
    if not LANGUAGE_TAG_REGEX.match(normalized):
        raise ValueError(
            f"Invalid language tag '{lang}'. Must be normalized BCP-47 (e.g., 'en', 'vi', 'en-US', 'pt-BR', 'zh-Hans')."
        )
    return normalized


def validate_region_tag(region: str) -> str:
    """Validate region tag against ISO-3166-1 alpha-2."""
    normalized = region.strip().upper()
    if not REGION_TAG_REGEX.match(normalized):
        raise ValueError(f"Invalid region code '{region}'. Must be a 2-letter uppercase ISO country code (e.g., 'US', 'VN').")
    return normalized


def validate_timezone(tz_name: str) -> str:
    """Validate timezone name against standard IANA timezones."""
    normalized = tz_name.strip()
    if normalized not in zoneinfo.available_timezones() and normalized != "UTC":
        raise ValueError(f"Invalid IANA timezone '{tz_name}'. Example valid timezones: 'UTC', 'America/New_York', 'Asia/Ho_Chi_Minh'.")
    return normalized


# ── Pydantic Schemas ──


class ChannelCreate(BaseModel):
    """Schema for creating a new Channel."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=3, max_length=100)
    description: str | None = None
    platform: Platform = Platform.YOUTUBE
    platform_channel_id: str | None = Field(default=None, max_length=255)
    primary_language: str = Field(default="en", min_length=2, max_length=20)
    target_region: str = Field(default="US", min_length=2, max_length=10)
    timezone: str = Field(default="UTC", min_length=1, max_length=50)
    dna: ChannelDNA | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def check_slug(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_slug(v)
        return None

    @field_validator("primary_language")
    @classmethod
    def check_language(cls, v: str) -> str:
        return validate_language_tag(v)

    @field_validator("target_region")
    @classmethod
    def check_region(cls, v: str) -> str:
        return validate_region_tag(v)

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, v: str) -> str:
        return validate_timezone(v)

    @field_validator("platform")
    @classmethod
    def check_platform(cls, v: Platform) -> Platform:
        if v not in SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Platform '{v.value}' is reserved and not supported in OMEGA-003. Only 'YOUTUBE' is supported."
            )
        return v


class ChannelUpdate(BaseModel):
    """Schema for updating Channel identity attributes (slug is immutable)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    platform_channel_id: str | None = Field(default=None, max_length=255)
    primary_language: str | None = Field(default=None, min_length=2, max_length=20)
    target_region: str | None = Field(default=None, min_length=2, max_length=10)
    timezone: str | None = Field(default=None, min_length=1, max_length=50)
    metadata: dict[str, Any] | None = None

    @field_validator("primary_language")
    @classmethod
    def check_language(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_language_tag(v)
        return None

    @field_validator("target_region")
    @classmethod
    def check_region(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_region_tag(v)
        return None

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_timezone(v)
        return None


class ChannelDNAUpdateRequest(BaseModel):
    """Schema for updating Channel DNA (creates a new revision)."""

    dna: ChannelDNA
    change_reason: str = Field(..., min_length=3, max_length=500, description="Mandatory rationale for the DNA update")
    actor: str = Field(default="USER", min_length=1, max_length=50)


class ChannelDNARevisionResponse(BaseModel):
    """Schema for returning a Channel DNA Revision snapshot."""

    id: UUID
    channel_id: UUID
    version: int
    snapshot: dict[str, Any]
    change_reason: str
    actor: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChannelResponse(BaseModel):
    """Schema for returning full Channel details."""

    id: UUID
    name: str
    slug: str
    description: str | None = None
    state: ChannelState
    platform: Platform
    platform_channel_id: str | None = None
    primary_language: str
    target_region: str
    timezone: str
    dna: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
