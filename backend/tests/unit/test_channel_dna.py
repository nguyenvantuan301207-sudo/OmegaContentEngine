"""Unit tests for Channel DNA schemas, KPI targets, and publishing preferences."""

from __future__ import annotations

import pytest

from omega.domain.channel_dna import (
    BrandAssetReference,
    BrandFormat,
    BrandPackage,
    ChannelBugPolicy,
    ChannelDNA,
    ContentStrategy,
    FrequencyPeriod,
    KPIMetricType,
    KPITarget,
    LongFormBrandPolicy,
    LongFormRuntimeProfile,
    PublishingFrequency,
    PublishingPreferences,
    ShortFormBrandPolicy,
    resolve_production_brand_spec,
)


def test_kpi_target_bounds_validation() -> None:
    """Test bounds validation on typed KPI targets."""
    # Valid rate metrics (0 - 100%)
    target_ctr = KPITarget(metric=KPIMetricType.CTR, target_value=7.5)
    assert target_ctr.target_value == 7.5

    target_retention = KPITarget(metric=KPIMetricType.RETENTION, target_value=60.0)
    assert target_retention.target_value == 60.0

    # Invalid rate metrics
    with pytest.raises(ValueError, match="between 0.0 and 100.0"):
        KPITarget(metric=KPIMetricType.CTR, target_value=120.0)

    with pytest.raises(ValueError, match="between 0.0 and 100.0"):
        KPITarget(metric=KPIMetricType.RETENTION, target_value=-5.0)

    # Valid count / revenue metrics (>= 0)
    target_views = KPITarget(metric=KPIMetricType.VIEWS, target_value=100000.0)
    assert target_views.target_value == 100000.0

    target_rev = KPITarget(metric=KPIMetricType.REVENUE, target_value=0.0)
    assert target_rev.target_value == 0.0

    # Invalid count metrics
    with pytest.raises(ValueError, match="must be non-negative"):
        KPITarget(metric=KPIMetricType.VIEWS, target_value=-1.0)


def test_structured_publishing_frequency() -> None:
    """Test structured publishing frequency validation."""
    freq = PublishingFrequency(count=3, period=FrequencyPeriod.WEEK)
    assert freq.count == 3
    assert freq.period == FrequencyPeriod.WEEK

    with pytest.raises(ValueError):
        PublishingFrequency(count=0, period=FrequencyPeriod.DAY)

    with pytest.raises(ValueError):
        PublishingFrequency(count=150, period=FrequencyPeriod.MONTH)


def test_publishing_preferences_time_windows_and_days() -> None:
    """Test publishing window and preferred day validation."""
    # Valid
    prefs = PublishingPreferences(
        target_timezone="UTC",
        preferred_days=["MONDAY", "WEDNESDAY"],
        preferred_time_windows=["14:00-16:00", "18:30-20:00"],
    )
    assert len(prefs.preferred_days) == 2
    assert len(prefs.preferred_time_windows) == 2

    # Invalid time window format
    with pytest.raises(ValueError, match="Invalid time window format"):
        PublishingPreferences(preferred_time_windows=["2:00-4:00"])

    with pytest.raises(ValueError, match="Invalid time window format"):
        PublishingPreferences(preferred_time_windows=["25:00-26:00"])

    # Invalid day
    with pytest.raises(ValueError, match="Invalid day"):
        PublishingPreferences(preferred_days=["FUNDAY"])


def test_content_strategy_duration_and_pillars() -> None:
    """Test content strategy duration range and pillar deduplication."""
    # Valid
    strategy = ContentStrategy(
        niche="Cybersecurity",
        content_pillars=["Threat Analysis", "Tool Reviews"],
        default_duration_min_seconds=300,
        default_duration_max_seconds=900,
    )
    assert strategy.default_duration_min_seconds <= strategy.default_duration_max_seconds

    # Duration min > max
    with pytest.raises(ValueError, match="cannot be greater than default_duration_max_seconds"):
        ContentStrategy(
            niche="Cybersecurity",
            default_duration_min_seconds=1200,
            default_duration_max_seconds=600,
        )

    # Pillar deduplication
    strat_dedup = ContentStrategy(
        niche="Tech",
        content_pillars=["News", "Tutorials", "news", "TUTORIALS"],
    )
    assert strat_dedup.content_pillars == ["News", "Tutorials"]


def test_long_form_runtime_profile_defaults_and_ordering() -> None:
    profile = LongFormRuntimeProfile()
    assert (
        profile.minimum_seconds,
        profile.preferred_minimum_seconds,
        profile.default_recommendation_seconds,
        profile.preferred_maximum_seconds,
        profile.maximum_seconds,
    ) == (480, 720, 840, 960, 1320)

    with pytest.raises(ValueError, match="must be ordered"):
        LongFormRuntimeProfile(default_recommendation_seconds=1000)


def test_existing_content_strategy_snapshot_gets_runtime_defaults() -> None:
    strategy = ContentStrategy.model_validate({"niche": "Existing channel"})
    assert strategy.long_form_runtime == LongFormRuntimeProfile()


def test_legacy_and_empty_brand_packages_are_valid() -> None:
    assert ChannelDNA.model_validate({}).brand_package is None
    assert ChannelDNA(brand_package=BrandPackage()).brand_package == BrandPackage()


def test_brand_asset_roles_and_channel_bug_validation() -> None:
    logo = BrandAssetReference(
        reference="brand://channel/logo-primary",
        content_hash="a" * 64,
        mime_type="image/png",
        width=512,
        height=512,
    )
    intro = BrandAssetReference(
        reference="brand://channel/intro",
        content_hash="b" * 64,
        mime_type="video/mp4",
        duration_seconds=2.0,
    )
    outro = BrandAssetReference(
        reference="brand://channel/outro",
        content_hash="c" * 64,
        mime_type="video/mp4",
        duration_seconds=6.0,
    )
    sonic = BrandAssetReference(
        reference="brand://channel/sonic",
        content_hash="d" * 64,
        mime_type="audio/wav",
    )
    package = BrandPackage(
        logo_asset=logo,
        intro_asset=intro,
        outro_asset=outro,
        sonic_logo=sonic,
        channel_bug=ChannelBugPolicy(enabled=True),
    )
    assert package.logo_asset.mime_type == "image/png"
    assert package.intro_asset.mime_type == "video/mp4"
    assert package.outro_asset.mime_type == "video/mp4"
    assert package.sonic_logo.mime_type == "audio/wav"

    with pytest.raises(ValueError, match="logo_asset must use a image MIME type"):
        BrandPackage(logo_asset=intro)
    with pytest.raises(ValueError):
        ChannelBugPolicy(opacity=1.1)
    with pytest.raises(ValueError):
        ChannelBugPolicy(scale=0)
    with pytest.raises(ValueError):
        ChannelBugPolicy(position="CENTER")


def test_brand_format_policies_encode_sequence_invariants() -> None:
    assert LongFormBrandPolicy().hook_before_intro is True
    assert ShortFormBrandPolicy().inherit_long_form_micro_intro is False


def test_pinned_brand_resolution_is_value_like_and_deterministic() -> None:
    from uuid import UUID

    revision_id = UUID("12345678-1234-5678-1234-567812345678")
    snapshot = {
        "brand_package": {
            "logo_asset": {
                "reference": "brand://channel/logo-primary",
                "content_hash": "a" * 64,
                "mime_type": "image/png",
            },
            "intro_asset": {
                "reference": "brand://channel/intro",
                "content_hash": "b" * 64,
                "mime_type": "video/mp4",
                "duration_seconds": 2.0,
            },
            "long_form": {"micro_intro_enabled": True},
        }
    }
    first = resolve_production_brand_spec(
        channel_dna_snapshot=snapshot,
        channel_dna_revision_id=revision_id,
        production_format=BrandFormat.LONG_FORM,
    )
    replay = resolve_production_brand_spec(
        channel_dna_snapshot=snapshot,
        channel_dna_revision_id=revision_id,
        production_format=BrandFormat.LONG_FORM,
    )
    different_revision = resolve_production_brand_spec(
        channel_dna_snapshot=snapshot,
        channel_dna_revision_id=UUID("87654321-4321-8765-4321-876543218765"),
        production_format=BrandFormat.LONG_FORM,
    )
    changed_logo = resolve_production_brand_spec(
        channel_dna_snapshot={
            "brand_package": {
                **snapshot["brand_package"],
                "logo_asset": {
                    **snapshot["brand_package"]["logo_asset"],
                    "content_hash": "e" * 64,
                },
            }
        },
        channel_dna_revision_id=revision_id,
        production_format=BrandFormat.LONG_FORM,
    )
    changed_intro = resolve_production_brand_spec(
        channel_dna_snapshot={
            "brand_package": {
                **snapshot["brand_package"],
                "intro_asset": {
                    **snapshot["brand_package"]["intro_asset"],
                    "content_hash": "f" * 64,
                },
            }
        },
        channel_dna_revision_id=revision_id,
        production_format=BrandFormat.LONG_FORM,
    )

    assert first == replay
    assert first.identity == replay.identity
    assert first.identity == different_revision.identity
    assert first.source_channel_dna_revision_id == revision_id
    assert first.source_channel_dna_revision_id != different_revision.source_channel_dna_revision_id
    assert first.identity != changed_logo.identity
    assert first.identity != changed_intro.identity
    with pytest.raises(ValueError):
        first.logo_variant = "mutable"


def test_outro_policy_controls_effective_spec_and_identity() -> None:
    from uuid import uuid4

    outro = {
        "reference": "brand://channel/outro",
        "content_hash": "c" * 64,
        "mime_type": "video/mp4",
        "duration_seconds": 6.0,
    }
    disabled = resolve_production_brand_spec(
        channel_dna_snapshot={"brand_package": {"outro_asset": outro}},
        channel_dna_revision_id=uuid4(),
        production_format=BrandFormat.LONG_FORM,
    )
    dormant_changed = resolve_production_brand_spec(
        channel_dna_snapshot={
            "brand_package": {"outro_asset": {**outro, "content_hash": "d" * 64}}
        },
        channel_dna_revision_id=uuid4(),
        production_format=BrandFormat.LONG_FORM,
    )
    enabled = resolve_production_brand_spec(
        channel_dna_snapshot={
            "brand_package": {
                "outro_asset": outro,
                "long_form": {"branded_outro_enabled": True},
            }
        },
        channel_dna_revision_id=uuid4(),
        production_format=BrandFormat.LONG_FORM,
    )

    assert disabled.outro_asset is None
    assert disabled.identity == dormant_changed.identity
    assert enabled.outro_asset is not None
    assert enabled.outro_asset.content_hash == "c" * 64
    assert enabled.identity != disabled.identity


def test_no_brand_resolution_has_no_external_substitution() -> None:
    from uuid import uuid4

    resolved = resolve_production_brand_spec(
        channel_dna_snapshot={},
        channel_dna_revision_id=uuid4(),
        production_format=BrandFormat.SHORT_FORM,
    )
    assert resolved.logo_asset is None
    assert resolved.intro_asset is None
    assert resolved.outro_asset is None
    assert resolved.sonic_logo is None
    assert resolved.sequencing_policy.inherit_long_form_micro_intro is False
    assert resolved.has_render_effect is False
    assert resolved.identity is None


def test_channel_dna_default_builder() -> None:
    """Test ChannelDNA default helper constructor."""
    dna = ChannelDNA.create_default(
        niche="AI Research",
        language="vi",
        region="VN",
    )
    assert dna.content_strategy.niche == "AI Research"
    assert dna.audience.geographic_focus == ["VN"]
    assert dna.publishing_preferences.frequency_target.count == 3
    assert len(dna.goals_and_kpis.target_kpis) >= 1
