"""Unit tests for Channel DNA schemas, KPI targets, and publishing preferences."""

from __future__ import annotations

import pytest

from omega.domain.channel_dna import (
    ChannelDNA,
    ContentStrategy,
    FrequencyPeriod,
    KPIMetricType,
    KPITarget,
    PublishingFrequency,
    PublishingPreferences,
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
