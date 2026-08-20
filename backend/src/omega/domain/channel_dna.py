"""Channel DNA domain model and strongly-validated schemas.

Defines the strategic identity components: Audience, Brand Voice, Visual Style,
Content Strategy, Structured Publishing Preferences, Typed Goals & KPIs, and Constraints.
This is the domain layer — zero infrastructure dependencies.
"""

from __future__ import annotations

import enum
import re

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
