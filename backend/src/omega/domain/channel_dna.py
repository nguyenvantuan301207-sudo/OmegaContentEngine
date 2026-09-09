"""Channel DNA domain model and strongly-validated schemas.

Defines the strategic identity components: Audience, Brand Voice, Visual Style,
Content Strategy, Structured Publishing Preferences, Typed Goals & KPIs, and Constraints.
This is the domain layer — zero infrastructure dependencies.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class KnowledgeLevel(enum.StrEnum):
    """Audience knowledge level."""

    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"
    ALL_LEVELS = "ALL_LEVELS"


class AudienceProfile(BaseModel):
    """Defines target audience demographics, knowledge level, and intent."""

    age_range: str = Field(default="18-34", min_length=1, max_length=50)
    interests: list[str] = Field(
        default_factory=lambda: ["technology", "productivity"], min_length=1, max_length=20
    )
    knowledge_level: KnowledgeLevel = KnowledgeLevel.ALL_LEVELS
    viewer_intent: list[str] = Field(
        default_factory=lambda: ["EDUCATION", "ANALYSIS"], min_length=1
    )
    preferred_content_length: str = Field(default="8-15 min", min_length=1, max_length=50)
    preferred_style: list[str] = Field(default_factory=lambda: ["CLEAR", "STRUCTURED"])
    geographic_focus: list[str] = Field(default_factory=lambda: ["US", "GLOBAL"])

    @field_validator("interests")
    @classmethod
    def unique_interests(cls, v: list[str]) -> list[str]:
        seen = set()
        deduped = []
        for item in v:
            clean = item.strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                deduped.append(clean)
        if not deduped:
            raise ValueError("At least one interest must be specified.")
        return deduped


class BrandVoice(BaseModel):
    """Defines tone, pacing, vocabulary, and narration style."""

    tone: list[str] = Field(
        default_factory=lambda: ["AUTHORITATIVE", "CONVERSATIONAL", "OBJECTIVE"],
        min_length=1,
    )
    pace: str = Field(default="MODERATE", min_length=1, max_length=50)
    complexity: str = Field(default="ACCESSIBLE", min_length=1, max_length=50)
    humor_level: str = Field(default="SUBTLE", min_length=1, max_length=50)
    formality: str = Field(default="SEMI_FORMAL", min_length=1, max_length=50)
    narration_style: str = Field(default="THIRD_PERSON", min_length=1, max_length=50)
    preferred_vocabulary: list[str] = Field(default_factory=list)
    avoid_vocabulary: list[str] = Field(default_factory=list)


class VisualStyle(BaseModel):
    """Defines aesthetic guidelines, thumbnail style, and editing rules."""

    visual_theme: str = Field(default="DARK_MINIMAL", min_length=1, max_length=100)
    thumbnail_style: str = Field(default="BOLD_TEXT_HIGH_CONTRAST", min_length=1, max_length=100)
    color_preferences: list[str] = Field(default_factory=lambda: ["#0F172A", "#38BDF8", "#F8FAFC"])
    font_preferences: list[str] = Field(
        default_factory=lambda: ["Inter", "Montserrat", "JetBrains Mono"]
    )
    editing_style: str = Field(default="DYNAMIC_JUMP_CUT", min_length=1, max_length=100)
    b_roll_style: str = Field(default="TECH_SCREENCAST", min_length=1, max_length=100)
    caption_style: str = Field(default="ANIMATED_WORD", min_length=1, max_length=100)


class BrandAssetReference(BaseModel):
    """Stable reusable brand asset identity stored in a Channel DNA snapshot."""

    model_config = ConfigDict(frozen=True)

    reference: str = Field(min_length=1, max_length=2048)
    content_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    mime_type: str = Field(min_length=3, max_length=255)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    variant_name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("reference")
    @classmethod
    def validate_storage_reference(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Brand asset reference fields must not be blank.")
        if not re.fullmatch(r"brand://channel/[A-Za-z0-9][A-Za-z0-9._/-]*", clean):
            raise ValueError("Brand asset reference must use the brand://channel/<storage-key> scheme.")
        if any(part in ("", ".", "..") for part in clean.removeprefix("brand://channel/").split("/")):
            raise ValueError("Brand asset reference contains an unsafe storage key.")
        return clean

    @field_validator("mime_type")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Brand asset reference fields must not be blank.")
        return clean

    @field_validator("content_hash")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        return value.lower()


class ChannelBugPosition(enum.StrEnum):
    TOP_LEFT = "TOP_LEFT"
    TOP_RIGHT = "TOP_RIGHT"
    BOTTOM_LEFT = "BOTTOM_LEFT"
    BOTTOM_RIGHT = "BOTTOM_RIGHT"


class ChannelBugTimingPolicy(enum.StrEnum):
    ALWAYS = "ALWAYS"
    AFTER_INTRO = "AFTER_INTRO"
    MAIN_CONTENT_ONLY = "MAIN_CONTENT_ONLY"


class ChannelBugPolicy(BaseModel):
    """Normalized renderer-independent channel logo overlay intent."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    logo_variant: str | None = Field(default=None, min_length=1, max_length=100)
    position: ChannelBugPosition = ChannelBugPosition.TOP_RIGHT
    safe_margin_x: float = Field(default=0.03, ge=0.0, le=0.25)
    safe_margin_y: float = Field(default=0.03, ge=0.0, le=0.25)
    scale: float = Field(default=0.1, gt=0.0, le=1.0)
    opacity: float = Field(default=0.7, ge=0.0, le=1.0)
    timing_policy: ChannelBugTimingPolicy = ChannelBugTimingPolicy.MAIN_CONTENT_ONLY
    subtitle_safe: bool = True


class EndScreenLayoutType(enum.StrEnum):
    LOGO_ONLY = "LOGO_ONLY"
    SUBSCRIBE = "SUBSCRIBE"
    NEXT_VIDEO = "NEXT_VIDEO"
    SUBSCRIBE_AND_NEXT_VIDEO = "SUBSCRIBE_AND_NEXT_VIDEO"


class EndScreenLayout(BaseModel):
    """Generic deterministic end-screen layout intent."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    layout: EndScreenLayoutType = EndScreenLayoutType.LOGO_ONLY
    show_tagline: bool = False
    show_subscribe: bool = False
    next_video_slots: int = Field(default=0, ge=0, le=4)


class BrandFormat(enum.StrEnum):
    LONG_FORM = "LONG_FORM"
    SHORT_FORM = "SHORT_FORM"


class LongFormBrandPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    hook_before_intro: Literal[True] = True
    micro_intro_enabled: bool = False
    channel_bug_enabled: bool = False
    branded_outro_enabled: bool = False
    end_screen_enabled: bool = False


class ShortFormBrandPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    inherit_long_form_micro_intro: Literal[False] = False
    lightweight_logo_motif_enabled: bool = False
    short_ending_enabled: bool = False


class BrandPackage(BaseModel):
    """Optional reusable channel brand package and format-specific policy."""

    model_config = ConfigDict(frozen=True)

    logo_asset: BrandAssetReference | None = None
    logo_variant: str | None = Field(default=None, min_length=1, max_length=100)
    intro_asset: BrandAssetReference | None = None
    outro_asset: BrandAssetReference | None = None
    channel_bug: ChannelBugPolicy = Field(default_factory=ChannelBugPolicy)
    tagline: str | None = Field(default=None, min_length=1, max_length=300)
    sonic_logo: BrandAssetReference | None = None
    end_screen_layout: EndScreenLayout = Field(default_factory=EndScreenLayout)
    long_form: LongFormBrandPolicy = Field(default_factory=LongFormBrandPolicy)
    short_form: ShortFormBrandPolicy = Field(default_factory=ShortFormBrandPolicy)

    @model_validator(mode="after")
    def validate_asset_roles_and_dependencies(self) -> BrandPackage:
        expected_types = (
            ("logo_asset", self.logo_asset, "image/"),
            ("intro_asset", self.intro_asset, "video/"),
            ("outro_asset", self.outro_asset, "video/"),
            ("sonic_logo", self.sonic_logo, "audio/"),
        )
        for field_name, asset, mime_prefix in expected_types:
            if asset is not None and not asset.mime_type.lower().startswith(mime_prefix):
                raise ValueError(f"{field_name} must use a {mime_prefix[:-1]} MIME type.")
        if self.intro_asset and self.intro_asset.duration_seconds is not None and not 1.5 <= self.intro_asset.duration_seconds <= 3.0:
            raise ValueError("intro_asset duration must be between 1.5 and 3 seconds.")
        if self.outro_asset and self.outro_asset.duration_seconds is not None and not 5.0 <= self.outro_asset.duration_seconds <= 8.0:
            raise ValueError("outro_asset duration must be between 5 and 8 seconds.")
        if self.channel_bug.enabled and self.logo_asset is None:
            raise ValueError("Enabled channel_bug requires logo_asset.")
        return self


class ResolvedProductionBrandSpec(BaseModel):
    """Immutable render-relevant brand values resolved from one pinned DNA revision."""

    model_config = ConfigDict(frozen=True)

    format: BrandFormat
    source_channel_dna_revision_id: UUID
    logo_asset: BrandAssetReference | None = None
    logo_variant: str | None = None
    intro_asset: BrandAssetReference | None = None
    outro_asset: BrandAssetReference | None = None
    channel_bug: ChannelBugPolicy | None = None
    tagline: str | None = None
    sonic_logo: BrandAssetReference | None = None
    end_screen_layout: EndScreenLayout | None = None
    sequencing_policy: LongFormBrandPolicy | ShortFormBrandPolicy

    @property
    def has_render_effect(self) -> bool:
        return any((
            self.logo_asset is not None,
            self.intro_asset is not None,
            self.outro_asset is not None,
            self.sonic_logo is not None,
            self.channel_bug is not None and self.channel_bug.enabled,
            self.end_screen_layout is not None and self.end_screen_layout.enabled,
            self.tagline is not None,
        ))

    def canonical_json(self) -> str:
        render_values = self.model_dump(
            mode="json",
            exclude={"source_channel_dna_revision_id"},
        )
        return json.dumps(render_values, sort_keys=True, separators=(",", ":"))

    @property
    def identity(self) -> str | None:
        if not self.has_render_effect:
            return None
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def resolve_production_brand_spec(
    *,
    channel_dna_snapshot: dict,
    channel_dna_revision_id: UUID,
    production_format: BrandFormat,
) -> ResolvedProductionBrandSpec:
    """Resolve deterministic brand input exclusively from a pinned DNA snapshot."""
    dna = ChannelDNA.model_validate(channel_dna_snapshot)
    package = dna.brand_package
    if package is None:
        return ResolvedProductionBrandSpec(
            format=production_format,
            source_channel_dna_revision_id=channel_dna_revision_id,
            sequencing_policy=(
                LongFormBrandPolicy()
                if production_format == BrandFormat.LONG_FORM
                else ShortFormBrandPolicy()
            ),
        )

    is_long_form = production_format == BrandFormat.LONG_FORM
    policy = package.long_form if is_long_form else package.short_form
    return ResolvedProductionBrandSpec(
        format=production_format,
        source_channel_dna_revision_id=channel_dna_revision_id,
        logo_asset=package.logo_asset,
        logo_variant=package.logo_variant,
        intro_asset=package.intro_asset if is_long_form and policy.micro_intro_enabled else None,
        outro_asset=package.outro_asset if is_long_form and policy.branded_outro_enabled else None,
        channel_bug=package.channel_bug if is_long_form and policy.channel_bug_enabled else None,
        tagline=package.tagline,
        sonic_logo=package.sonic_logo,
        end_screen_layout=package.end_screen_layout,
        sequencing_policy=policy,
    )


class LongFormRuntimeProfile(BaseModel):
    """Validated runtime bounds and recommendation for long-form productions."""

    minimum_seconds: int = Field(default=480, ge=30, le=3600)
    preferred_minimum_seconds: int = Field(default=720, ge=30, le=3600)
    default_recommendation_seconds: int = Field(default=840, ge=30, le=3600)
    preferred_maximum_seconds: int = Field(default=960, ge=30, le=3600)
    maximum_seconds: int = Field(default=1320, ge=30, le=3600)

    @model_validator(mode="after")
    def validate_ordered_bounds(self) -> LongFormRuntimeProfile:
        bounds = (
            self.minimum_seconds,
            self.preferred_minimum_seconds,
            self.default_recommendation_seconds,
            self.preferred_maximum_seconds,
            self.maximum_seconds,
        )
        if bounds != tuple(sorted(bounds)):
            raise ValueError(
                "Long-form runtime bounds must be ordered: minimum <= preferred minimum "
                "<= default recommendation <= preferred maximum <= maximum."
            )
        return self


class ContentStrategy(BaseModel):
    """Defines content niche, ordered pillars, and duration targets."""

    niche: str = Field(default="AI & Technology", min_length=2, max_length=100)
    subniches: list[str] = Field(
        default_factory=lambda: ["Machine Learning", "Workflow Automation"]
    )
    content_pillars: list[str] = Field(
        default_factory=lambda: [
            "Industry News & Analysis",
            "Tool Breakdowns & Tutorials",
            "Deep Dive Case Studies",
        ],
        min_length=1,
    )
    preferred_formats: list[str] = Field(
        default_factory=lambda: ["EXPLAINER", "NEWS_ROUNDUP", "DEEP_DIVE"]
    )
    default_duration_min_seconds: int = Field(default=300, ge=10, le=86400)
    default_duration_max_seconds: int = Field(default=900, ge=10, le=86400)
    long_form_runtime: LongFormRuntimeProfile = Field(default_factory=LongFormRuntimeProfile)
    evergreen_ratio: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("subniches", "content_pillars")
    @classmethod
    def unique_items(cls, v: list[str]) -> list[str]:
        seen = set()
        deduped = []
        for item in v:
            clean = item.strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                deduped.append(clean)
        return deduped

    @model_validator(mode="after")
    def validate_duration_range(self) -> ContentStrategy:
        if self.default_duration_min_seconds > self.default_duration_max_seconds:
            raise ValueError(
                f"default_duration_min_seconds ({self.default_duration_min_seconds}) "
                f"cannot be greater than default_duration_max_seconds ({self.default_duration_max_seconds})."
            )
        if not self.content_pillars:
            raise ValueError("At least one content pillar is required.")
        return self


TIME_WINDOW_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


class FrequencyPeriod(enum.StrEnum):
    """Frequency period options."""

    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"


class PublishingFrequency(BaseModel):
    """Structured publishing target frequency."""

    count: int = Field(default=3, ge=1, le=100)
    period: FrequencyPeriod = FrequencyPeriod.WEEK


class PublishingPreferences(BaseModel):
    """Defines publishing windows, days, and approval gates."""

    target_timezone: str = Field(default="UTC", min_length=1, max_length=50)
    preferred_days: list[str] = Field(default_factory=lambda: ["MONDAY", "WEDNESDAY", "FRIDAY"])
    preferred_time_windows: list[str] = Field(
        default_factory=lambda: ["14:00-16:00", "18:00-20:00"]
    )
    frequency_target: PublishingFrequency = Field(default_factory=PublishingFrequency)
    approval_required_before_publish: bool = True

    @field_validator("preferred_time_windows")
    @classmethod
    def validate_time_windows(cls, v: list[str]) -> list[str]:
        for window in v:
            clean = window.strip()
            if not TIME_WINDOW_REGEX.match(clean):
                raise ValueError(
                    f"Invalid time window format '{window}'. Must be 'HH:MM-HH:MM' (24-hour format, e.g. '14:00-16:00')."
                )
        return v

    @field_validator("preferred_days")
    @classmethod
    def validate_days(cls, v: list[str]) -> list[str]:
        valid_days = {
            "MONDAY",
            "TUESDAY",
            "WEDNESDAY",
            "THURSDAY",
            "FRIDAY",
            "SATURDAY",
            "SUNDAY",
        }
        deduped = []
        seen = set()
        for day in v:
            clean = day.strip().upper()
            if clean not in valid_days:
                raise ValueError(f"Invalid day '{day}'. Must be one of {sorted(valid_days)}.")
            if clean not in seen:
                seen.add(clean)
                deduped.append(clean)
        return deduped


class PrimaryGoal(enum.StrEnum):
    """Primary strategic goal."""

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


class KPITarget(BaseModel):
    """Strongly-typed KPI target benchmark."""

    metric: KPIMetricType
    target_value: float
    timeframe_days: int | None = Field(default=None, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_bounds(self) -> KPITarget:
        # Rate metrics (percentages) must be 0-100
        if self.metric in (
            KPIMetricType.CTR,
            KPIMetricType.RETENTION,
            KPIMetricType.ENGAGEMENT_RATE,
        ):
            if not (0.0 <= self.target_value <= 100.0):
                raise ValueError(
                    f"Target value for percentage metric '{self.metric.value}' must be between 0.0 and 100.0 (got {self.target_value})."
                )
        else:
            # Count and revenue metrics must be non-negative
            if self.target_value < 0.0:
                raise ValueError(
                    f"Target value for metric '{self.metric.value}' must be non-negative (got {self.target_value})."
                )
        return self


class GoalsAndKPIs(BaseModel):
    """Strategic goals and strongly-typed KPI targets."""

    primary_goal: PrimaryGoal = PrimaryGoal.GROWTH
    secondary_goals: list[PrimaryGoal] = Field(default_factory=lambda: [PrimaryGoal.ENGAGEMENT])
    target_kpis: list[KPITarget] = Field(
        default_factory=lambda: [
            KPITarget(metric=KPIMetricType.VIEWS, target_value=50000.0, timeframe_days=30),
            KPITarget(metric=KPIMetricType.RETENTION, target_value=50.0, timeframe_days=30),
            KPITarget(metric=KPIMetricType.CTR, target_value=8.0, timeframe_days=30),
        ]
    )


class Constraints(BaseModel):
    """Operational constraints, safety rules, and limits."""

    max_daily_videos: int = Field(default=1, ge=1, le=10)
    forbidden_topics: list[str] = Field(default_factory=list)
    content_safety_level: str = Field(default="STANDARD", min_length=1, max_length=50)
    guidelines: list[str] = Field(default_factory=list)


class ChannelDNA(BaseModel):
    """Root Channel DNA model encapsulating full strategic and operational identity."""

    audience: AudienceProfile = Field(default_factory=AudienceProfile)
    brand_voice: BrandVoice = Field(default_factory=BrandVoice)
    visual_style: VisualStyle = Field(default_factory=VisualStyle)
    brand_package: BrandPackage | None = None
    content_strategy: ContentStrategy = Field(default_factory=ContentStrategy)
    publishing_preferences: PublishingPreferences = Field(default_factory=PublishingPreferences)
    goals_and_kpis: GoalsAndKPIs = Field(default_factory=GoalsAndKPIs)
    constraints: Constraints = Field(default_factory=Constraints)

    model_config = ConfigDict(extra="ignore")

    @classmethod
    def create_default(
        cls,
        niche: str = "AI & Technology",
        language: str = "en",
        region: str = "US",
    ) -> ChannelDNA:
        """Helper to construct a fully-populated default Channel DNA."""
        return cls(
            audience=AudienceProfile(geographic_focus=[region]),
            content_strategy=ContentStrategy(niche=niche),
            publishing_preferences=PublishingPreferences(target_timezone="UTC"),
        )
