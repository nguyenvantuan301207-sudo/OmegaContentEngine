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


class DesiredDepth(enum.StrEnum):
    """Audience desired content depth."""

    SURFACE = "SURFACE"
    PRACTICAL = "PRACTICAL"
    DEEP_DIVE = "DEEP_DIVE"
    ACADEMIC = "ACADEMIC"


class JargonSensitivity(enum.StrEnum):
    """Audience sensitivity to technical terminology/jargon."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


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

    # v2 Audience Dimensions
    primary_audience: str = Field(
        default="Technical practitioners, developers, and researchers",
        min_length=1,
        max_length=200,
    )
    expected_context: str = Field(
        default="Foundational understanding of software engineering and computing principles",
        min_length=1,
        max_length=300,
    )
    desired_depth: DesiredDepth = DesiredDepth.DEEP_DIVE
    preferred_complexity: str = Field(default="MODERATE_TO_HIGH", min_length=1, max_length=50)
    sensitivity_to_jargon: JargonSensitivity = JargonSensitivity.LOW
    content_expectations: list[str] = Field(
        default_factory=lambda: ["Technical accuracy", "Grounded claims", "Structured reasoning"]
    )

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


# ── P24-A Channel DNA v2 Models ──────────────────────────────────────────────


class ChannelPositioning(BaseModel):
    """Concise strategic positioning defining purpose, promise, and distinctive edge."""

    model_config = ConfigDict(frozen=True)

    channel_purpose: str = Field(
        default="Provide rigorous, factual analysis on core technology developments",
        min_length=1,
        max_length=500,
    )
    content_promise: str = Field(
        default="Clear, evidence-backed breakdowns without sensationalism",
        min_length=1,
        max_length=500,
    )
    distinctive_angle: str = Field(
        default="First-principles architectural depth and empirical verification",
        min_length=1,
        max_length=500,
    )
    primary_subject_domain: str = Field(
        default="AI & Computer Systems", min_length=1, max_length=150
    )
    secondary_subject_domains: tuple[str, ...] = (
        "Distributed Systems",
        "Software Engineering",
    )
    audience_benefit: str = Field(
        default="Gain deep technical comprehension of complex engineering breakthroughs",
        min_length=1,
        max_length=500,
    )


class VoiceFormality(enum.StrEnum):
    FORMAL = "FORMAL"
    SEMI_FORMAL = "SEMI_FORMAL"
    CONVERSATIONAL = "CONVERSATIONAL"
    CASUAL = "CASUAL"


class VoiceDepth(enum.StrEnum):
    CONCISE = "CONCISE"
    EXPLANATORY = "EXPLANATORY"
    EXHAUSTIVE = "EXHAUSTIVE"


class VoiceEnergy(enum.StrEnum):
    CALM = "CALM"
    MODERATE = "MODERATE"
    DYNAMIC = "DYNAMIC"
    HIGH_ENERGY = "HIGH_ENERGY"


class VoiceExpressiveness(enum.StrEnum):
    NEUTRAL = "NEUTRAL"
    OBJECTIVE = "OBJECTIVE"
    EXPRESSIVE = "EXPRESSIVE"
    OPINIONATED = "OPINIONATED"


class VoiceTechnicality(enum.StrEnum):
    ACCESSIBLE = "ACCESSIBLE"
    BALANCED = "BALANCED"
    TECHNICAL = "TECHNICAL"
    SPECIALIZED = "SPECIALIZED"


class VoiceCharacter(enum.StrEnum):
    SERIOUS = "SERIOUS"
    ANALYTICAL = "ANALYTICAL"
    ENGAGING = "ENGAGING"
    PLAYFUL = "PLAYFUL"


class EditorialVoice(BaseModel):
    """Typed, inspectable editorial voice dimensions."""

    model_config = ConfigDict(frozen=True)

    formality: VoiceFormality = VoiceFormality.SEMI_FORMAL
    depth: VoiceDepth = VoiceDepth.EXPLANATORY
    energy: VoiceEnergy = VoiceEnergy.CALM
    expressiveness: VoiceExpressiveness = VoiceExpressiveness.OBJECTIVE
    technicality: VoiceTechnicality = VoiceTechnicality.TECHNICAL
    tone_character: VoiceCharacter = VoiceCharacter.ANALYTICAL
    style_notes: str | None = Field(
        default="Maintain sober, evidence-focused tone; avoid colloquial hype.",
        max_length=1000,
    )


class HardConstraints(BaseModel):
    """Non-negotiable operational and editorial rules that downstream systems CANNOT violate."""

    model_config = ConfigDict(frozen=True)

    no_fabricated_claims: bool = True
    no_misleading_clickbait: bool = True
    no_unsupported_medical_claims: bool = True
    no_profanity: bool = True
    max_daily_videos: int = Field(default=1, ge=1, le=10)
    content_safety_level: str = Field(default="STRICT", min_length=1, max_length=50)
    prohibited_vocabulary: tuple[str, ...] = ()
    required_disclaimers: tuple[str, ...] = ()
    custom_rules: tuple[str, ...] = ()


class SoftPreferences(BaseModel):
    """Flexible guidelines that downstream systems can optimize within constraints."""

    model_config = ConfigDict(frozen=True)

    prefer_cinematic_visuals: bool = True
    prefer_moderate_pacing: bool = True
    prefer_concise_cta: bool = True
    prefer_instrumental_music: bool = True
    prefer_evidence_graphics: bool = True


class NarrativeHookStyle(enum.StrEnum):
    QUESTION = "QUESTION"
    BOLD_STATEMENT = "BOLD_STATEMENT"
    PARADOX = "PARADOX"
    CASE_STUDY = "CASE_STUDY"
    EVIDENCE_REVEAL = "EVIDENCE_REVEAL"


class NarrativeContextDepth(enum.StrEnum):
    MINIMAL = "MINIMAL"
    MODERATE = "MODERATE"
    COMPREHENSIVE = "COMPREHENSIVE"


class NarrativePayoffStyle(enum.StrEnum):
    CONCRETE_EVIDENCE = "CONCRETE_EVIDENCE"
    SYNTHESIS = "SYNTHESIS"
    ACTIONABLE_FRAMEWORK = "ACTIONABLE_FRAMEWORK"


class NarrativeCTAStyle(enum.StrEnum):
    CONCISE = "CONCISE"
    COMMUNITY = "COMMUNITY"
    ENGAGEMENT = "ENGAGEMENT"
    MINIMAL = "MINIMAL"


class NarrativePreferences(BaseModel):
    """Channel preferences guiding P21 Narrative Architecture."""

    model_config = ConfigDict(frozen=True)

    preferred_strategies: tuple[str, ...] = (
        "EVIDENCE_FIRST",
        "PROBLEM_SOLUTION",
        "HOOK_DISCOVERY",
    )
    hook_style: NarrativeHookStyle = NarrativeHookStyle.EVIDENCE_REVEAL
    context_depth: NarrativeContextDepth = NarrativeContextDepth.MODERATE
    preferred_pacing: str = "BALANCED"  # FAST, BALANCED, DELIBERATE
    open_loop_tolerance: str = "BALANCED"  # STRICT, BALANCED, PERMISSIVE
    payoff_style: NarrativePayoffStyle = NarrativePayoffStyle.CONCRETE_EVIDENCE
    educational_depth: str = "PRACTICAL_DEPTH"
    cta_style: NarrativeCTAStyle = NarrativeCTAStyle.CONCISE


class VisualDensityPreference(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CameraMotionIntensity(enum.StrEnum):
    RESTRAINED = "RESTRAINED"
    SMOOTH = "SMOOTH"
    DYNAMIC = "DYNAMIC"


class VisualPreferences(BaseModel):
    """Channel preferences guiding P22 Visual Architecture."""

    model_config = ConfigDict(frozen=True)

    visual_density: VisualDensityPreference = VisualDensityPreference.MEDIUM
    b_roll_usage: str = "TARGETED"
    diagram_frequency: str = "MANDATORY_FOR_COMPLEX_CLAIMS"
    evidence_emphasis: str = "HIGH"
    camera_motion_intensity: CameraMotionIntensity = CameraMotionIntensity.RESTRAINED
    transition_restraint: bool = True
    typography_character: str = "CLEAN_MODERN"
    graphic_complexity: str = "STRUCTURED"
    comparison_treatment: str = "SIDE_BY_SIDE"


class AudioPreferences(BaseModel):
    """Channel preferences guiding P23 Audio Architecture."""

    model_config = ConfigDict(frozen=True)

    music_usage_tendency: str = "SUBTLE_BACKGROUND"  # ALWAYS, SUBTLE_BACKGROUND, KEY_BEATS_ONLY, NONE, MINIMAL
    preferred_energy_min: float = Field(default=0.2, ge=0.0, le=1.0)
    preferred_energy_max: float = Field(default=0.6, ge=0.0, le=1.0)
    vocal_policy: str = "INSTRUMENTAL_ONLY"
    sfx_density: str = "BALANCED"  # SPARSE, BALANCED, DYNAMIC
    audio_restraint: bool = True
    sonic_character: str = "CINEMATIC_AMBIENT"

    @model_validator(mode="after")
    def validate_energy_bounds(self) -> AudioPreferences:
        if self.preferred_energy_min > self.preferred_energy_max:
            raise ValueError("preferred_energy_min cannot be greater than preferred_energy_max")
        return self


class PackagingPreferences(BaseModel):
    """Channel preferences preparing P24-C Packaging Generation seam."""

    model_config = ConfigDict(frozen=True)

    title_tone: str = "FACTUAL_COMPELLING"
    title_length_tendency: str = "CONCISE"
    thumbnail_density: str = "MINIMAL_TO_MODERATE"
    thumbnail_text_policy: str = "MAX_3_WORDS"
    description_style: str = "STRUCTURED_OUTLINE"
    chapter_style: str = "SECTION_BASED"
    metadata_voice: str = "OBJECTIVE"
    clickbait_tolerance: str = "ZERO_TOLERANCE"
    prohibited_packaging_patterns: tuple[str, ...] = (
        "ALL_CAPS",
        "RED_ARROWS_AND_CIRCLES",
        "EMOTIONAL_CLICKBAIT",
        "MISLEADING_PROMISES",
    )


class ResolvedPackagingSpec(BaseModel):
    """Resolved packaging projection consumed by P24-C."""

    model_config = ConfigDict(frozen=True)

    title_tone: str
    title_length_tendency: str
    thumbnail_density: str
    thumbnail_text_policy: str
    description_style: str
    chapter_style: str
    metadata_voice: str
    clickbait_tolerance: str
    prohibited_patterns: tuple[str, ...]
    hard_constraints_binding: bool = True


class ContentPillar(BaseModel):
    """Approved structured content pillar/theme."""

    model_config = ConfigDict(frozen=True)

    pillar_id: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    priority_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    allowed_subtopics: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()


class AvoidPatterns(BaseModel):
    """Explicit typed undesirable channel behaviors."""

    model_config = ConfigDict(frozen=True)

    sensationalized_claims: bool = True
    repetitive_hooks: bool = True
    excessive_memes: bool = True
    overactive_camera_motion: bool = True
    constant_sfx: bool = True
    generic_cta: bool = True
    overloaded_thumbnails: bool = True
    prohibited_phrases: tuple[str, ...] = ()
    custom_avoid_rules: tuple[str, ...] = ()


class FormatOverride(BaseModel):
    """Format-specific override for a target profile (e.g. SHORT, MEDIUM, LONG)."""

    model_config = ConfigDict(frozen=True)

    pacing: str | None = None
    context_depth: NarrativeContextDepth | None = None
    visual_density: VisualDensityPreference | None = None
    cta_style: NarrativeCTAStyle | None = None
    music_tendency: str | None = None
    sfx_density: str | None = None


class ResolvedChannelDNA(BaseModel):
    """Immutable resolved snapshot of Channel DNA for a specific format profile."""

    model_config = ConfigDict(frozen=True)

    format_profile: str
    positioning: ChannelPositioning
    audience: AudienceProfile
    editorial_voice: EditorialVoice
    hard_constraints: HardConstraints
    soft_preferences: SoftPreferences
    narrative_preferences: NarrativePreferences
    visual_preferences: VisualPreferences
    audio_preferences: AudioPreferences
    packaging_preferences: PackagingPreferences
    content_pillars: tuple[ContentPillar, ...]
    avoid_patterns: AvoidPatterns
    packaging_spec: ResolvedPackagingSpec
    channel_dna_revision_id: UUID | None = None
    version: int | None = None


class ChannelDNAFindingCode(enum.StrEnum):
    CONTRADICTORY_CONSTRAINTS = "CONTRADICTORY_CONSTRAINTS"
    INVALID_RANGE_VALUE = "INVALID_RANGE_VALUE"
    EMPTY_REQUIRED_POSITIONING = "EMPTY_REQUIRED_POSITIONING"
    DUPLICATE_CONTENT_PILLAR_ID = "DUPLICATE_CONTENT_PILLAR_ID"
    INVALID_PRIORITY_WEIGHT = "INVALID_PRIORITY_WEIGHT"
    IMPOSSIBLE_FORMAT_OVERRIDE = "IMPOSSIBLE_FORMAT_OVERRIDE"
    HARD_SOFT_POLICY_CONFLICT = "HARD_SOFT_POLICY_CONFLICT"
    UNKNOWN_ENUM_VALUE = "UNKNOWN_ENUM_VALUE"


class ChannelDNAFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ChannelDNAFindingCode
    severity: Literal["INFO", "WARNING", "ERROR", "BLOCKER"]
    field: str
    explanation: str
    recommended_remediation: str


class ChannelDNAValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_valid: bool
    findings: tuple[ChannelDNAFinding, ...] = ()
    error_count: int = 0
    warning_count: int = 0


class ChannelDNAValidator:
    """Validates ChannelDNA integrity, constraints, ranges, and consistency."""

    @classmethod
    def validate(cls, dna: ChannelDNA) -> ChannelDNAValidationResult:
        findings: list[ChannelDNAFinding] = []

        # 1. Contradictory hard constraints / clickbait
        if dna.hard_constraints.no_misleading_clickbait:
            if dna.packaging_preferences.clickbait_tolerance in (
                "HIGH_ENGAGEMENT",
                "AGGRESSIVE_CLICKBAIT",
                "CLICKBAIT_PERMITTED",
            ):
                findings.append(
                    ChannelDNAFinding(
                        code=ChannelDNAFindingCode.CONTRADICTORY_CONSTRAINTS,
                        severity="ERROR",
                        field="packaging_preferences.clickbait_tolerance",
                        explanation="Packaging specifies aggressive clickbait tolerance while hard constraint forbids misleading clickbait.",
                        recommended_remediation="Set clickbait_tolerance to ZERO_TOLERANCE or align with brand safety policy.",
                    )
                )

        # 2. Audio energy range
        if dna.audio_preferences.preferred_energy_min > dna.audio_preferences.preferred_energy_max:
            findings.append(
                ChannelDNAFinding(
                    code=ChannelDNAFindingCode.INVALID_RANGE_VALUE,
                    severity="ERROR",
                    field="audio_preferences.preferred_energy_min",
                    explanation=f"preferred_energy_min ({dna.audio_preferences.preferred_energy_min}) exceeds preferred_energy_max ({dna.audio_preferences.preferred_energy_max}).",
                    recommended_remediation="Ensure minimum energy is less than or equal to maximum energy.",
                )
            )

        # 3. Duplicate content pillar IDs and priority weights
        seen_pillars: set[str] = set()
        for pillar in dna.content_pillars_v2:
            if pillar.pillar_id in seen_pillars:
                findings.append(
                    ChannelDNAFinding(
                        code=ChannelDNAFindingCode.DUPLICATE_CONTENT_PILLAR_ID,
                        severity="ERROR",
                        field="content_pillars_v2",
                        explanation=f"Duplicate content pillar ID '{pillar.pillar_id}' detected.",
                        recommended_remediation="Assign unique pillar IDs across all content pillars.",
                    )
                )
            seen_pillars.add(pillar.pillar_id)

            if not (0.0 <= pillar.priority_weight <= 1.0):
                findings.append(
                    ChannelDNAFinding(
                        code=ChannelDNAFindingCode.INVALID_PRIORITY_WEIGHT,
                        severity="ERROR",
                        field=f"content_pillars_v2.{pillar.pillar_id}.priority_weight",
                        explanation=f"Priority weight {pillar.priority_weight} outside allowed range [0.0, 1.0].",
                        recommended_remediation="Set priority weight between 0.0 and 1.0.",
                    )
                )

        # 4. Positioning validation
        pos = dna.positioning
        if not pos.channel_purpose.strip():
            findings.append(
                ChannelDNAFinding(
                    code=ChannelDNAFindingCode.EMPTY_REQUIRED_POSITIONING,
                    severity="ERROR",
                    field="positioning.channel_purpose",
                    explanation="Channel purpose cannot be empty.",
                    recommended_remediation="Provide a concise statement of channel purpose.",
                )
            )
        if not pos.content_promise.strip():
            findings.append(
                ChannelDNAFinding(
                    code=ChannelDNAFindingCode.EMPTY_REQUIRED_POSITIONING,
                    severity="ERROR",
                    field="positioning.content_promise",
                    explanation="Content promise cannot be empty.",
                    recommended_remediation="Define what content promise is delivered to the audience.",
                )
            )

        # 5. Format overrides sanity
        for fmt, override in dna.format_overrides.items():
            if fmt.upper() not in ("SHORT", "MEDIUM", "LONG", "SHORT_FORM", "LONG_FORM"):
                findings.append(
                    ChannelDNAFinding(
                        code=ChannelDNAFindingCode.IMPOSSIBLE_FORMAT_OVERRIDE,
                        severity="WARNING",
                        field=f"format_overrides.{fmt}",
                        explanation=f"Unrecognized format profile override key '{fmt}'.",
                        recommended_remediation="Use recognized format profile (SHORT, MEDIUM, LONG).",
                    )
                )

        has_errors = any(f.severity in ("ERROR", "BLOCKER") for f in findings)
        return ChannelDNAValidationResult(
            is_valid=not has_errors,
            findings=tuple(findings),
            error_count=sum(1 for f in findings if f.severity in ("ERROR", "BLOCKER")),
            warning_count=sum(1 for f in findings if f.severity == "WARNING"),
        )


class ChannelDNA(BaseModel):
    """Root Channel DNA model encapsulating full strategic and operational identity (v2)."""

    # Existing v1 models (retained for 100% backward compatibility)
    audience: AudienceProfile = Field(default_factory=AudienceProfile)
    brand_voice: BrandVoice = Field(default_factory=BrandVoice)
    visual_style: VisualStyle = Field(default_factory=VisualStyle)
    brand_package: BrandPackage | None = None
    content_strategy: ContentStrategy = Field(default_factory=ContentStrategy)
    publishing_preferences: PublishingPreferences = Field(default_factory=PublishingPreferences)
    goals_and_kpis: GoalsAndKPIs = Field(default_factory=GoalsAndKPIs)
    constraints: Constraints = Field(default_factory=Constraints)

    # Channel DNA v2 core components
    positioning: ChannelPositioning = Field(default_factory=ChannelPositioning)
    editorial_voice: EditorialVoice = Field(default_factory=EditorialVoice)
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    soft_preferences: SoftPreferences = Field(default_factory=SoftPreferences)
    narrative_preferences: NarrativePreferences = Field(default_factory=NarrativePreferences)
    visual_preferences: VisualPreferences = Field(default_factory=VisualPreferences)
    audio_preferences: AudioPreferences = Field(default_factory=AudioPreferences)
    packaging_preferences: PackagingPreferences = Field(default_factory=PackagingPreferences)
    content_pillars_v2: list[ContentPillar] = Field(default_factory=list)
    avoid_patterns: AvoidPatterns = Field(default_factory=AvoidPatterns)
    format_overrides: dict[str, FormatOverride] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")

    def resolve_for_format(
        self,
        format_profile: str = "LONG",
        contextual_overrides: dict | None = None,
        *,
        revision_id: UUID | None = None,
        version: int | None = None,
    ) -> ResolvedChannelDNA:
        """Deterministically resolve effective ChannelDNA values for a format profile."""
        norm_fmt = format_profile.strip().upper()
        override = self.format_overrides.get(norm_fmt) or self.format_overrides.get(format_profile)

        # 1. Hard constraints are strictly binding and immutable across format overrides
        resolved_hard = self.hard_constraints

        # 2. Narrative preferences
        pacing = override.pacing if (override and override.pacing) else self.narrative_preferences.preferred_pacing
        context_depth = override.context_depth if (override and override.context_depth) else self.narrative_preferences.context_depth
        cta_style = override.cta_style if (override and override.cta_style) else self.narrative_preferences.cta_style

        # For SHORT format: defaults to FAST pacing and MINIMAL context if not explicitly set
        if norm_fmt in ("SHORT", "SHORT_FORM") and not (override and override.pacing):
            pacing = "FAST"
        if norm_fmt in ("SHORT", "SHORT_FORM") and not (override and override.context_depth):
            context_depth = NarrativeContextDepth.MINIMAL

        resolved_narrative = self.narrative_preferences.model_copy(update={
            "preferred_pacing": pacing,
            "context_depth": context_depth,
            "cta_style": cta_style,
        })

        # 3. Visual preferences
        vis_density = override.visual_density if (override and override.visual_density) else self.visual_preferences.visual_density
        resolved_visual = self.visual_preferences.model_copy(update={
            "visual_density": vis_density,
        })

        # 4. Audio preferences
        music_tendency = override.music_tendency if (override and override.music_tendency) else self.audio_preferences.music_usage_tendency
        sfx_density = override.sfx_density if (override and override.sfx_density) else self.audio_preferences.sfx_density
        resolved_audio = self.audio_preferences.model_copy(update={
            "music_usage_tendency": music_tendency,
            "sfx_density": sfx_density,
        })

        # 5. Content pillars (if v2 list empty, derive from v1 content_strategy.content_pillars)
        pillars: list[ContentPillar] = list(self.content_pillars_v2)
        if not pillars and self.content_strategy.content_pillars:
            pillars = [
                ContentPillar(
                    pillar_id=f"pillar-{i+1}",
                    name=p,
                    description=f"Strategic content pillar: {p}",
                    priority_weight=1.0,
                )
                for i, p in enumerate(self.content_strategy.content_pillars)
            ]

        # 6. Packaging spec
        pkg_spec = self.to_packaging_spec(format_profile=norm_fmt)

        return ResolvedChannelDNA(
            format_profile=norm_fmt,
            positioning=self.positioning,
            audience=self.audience,
            editorial_voice=self.editorial_voice,
            hard_constraints=resolved_hard,
            soft_preferences=self.soft_preferences,
            narrative_preferences=resolved_narrative,
            visual_preferences=resolved_visual,
            audio_preferences=resolved_audio,
            packaging_preferences=self.packaging_preferences,
            content_pillars=tuple(pillars),
            avoid_patterns=self.avoid_patterns,
            packaging_spec=pkg_spec,
            channel_dna_revision_id=revision_id,
            version=version,
        )

    def to_packaging_spec(self, format_profile: str = "LONG") -> ResolvedPackagingSpec:
        """Derive resolved packaging specification for P24-C."""
        pkg = self.packaging_preferences
        clickbait = "ZERO_TOLERANCE" if self.hard_constraints.no_misleading_clickbait else pkg.clickbait_tolerance
        return ResolvedPackagingSpec(
            title_tone=pkg.title_tone,
            title_length_tendency=pkg.title_length_tendency,
            thumbnail_density=pkg.thumbnail_density,
            thumbnail_text_policy=pkg.thumbnail_text_policy,
            description_style=pkg.description_style,
            chapter_style=pkg.chapter_style,
            metadata_voice=pkg.metadata_voice,
            clickbait_tolerance=clickbait,
            prohibited_patterns=pkg.prohibited_packaging_patterns,
            hard_constraints_binding=True,
        )

    @classmethod
    def create_default(
        cls,
        niche: str = "AI & Technology",
        language: str = "en",
        region: str = "US",
    ) -> ChannelDNA:
        """Helper to construct a fully-populated default Channel DNA with v2 structures."""
        return cls(
            audience=AudienceProfile(geographic_focus=[region]),
            content_strategy=ContentStrategy(niche=niche),
            publishing_preferences=PublishingPreferences(target_timezone="UTC"),
            positioning=ChannelPositioning(
                channel_purpose=f"Provide rigorous, factual analysis on {niche}",
                content_promise=f"Clear, evidence-backed breakdowns of {niche} developments",
                primary_subject_domain=niche,
            ),
        )
