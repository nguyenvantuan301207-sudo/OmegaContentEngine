"""Computed metrics service for OMEGA-012 Analytics Engine.

Calculates DETERMINISTIC_DERIVED and HEURISTIC_FEATURE metrics from immutable source observations.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from uuid import UUID

from omega.domain.analytics import (
    MetricClassification,
    MetricQuality,
    compute_computation_dedupe_key,
)
from omega.infrastructure.models import AnalyticsComputedMetric, AnalyticsMetricObservation


class ComputedMetricsService:
    """Computes reproducible derived and heuristic metrics from stored observations."""

    @staticmethod
    def _compute_source_checksum(obs_ids: list[UUID]) -> str:
        """Deterministically hash the set of source observation IDs."""
        sorted_ids = sorted(str(uid) for uid in obs_ids)
        return hashlib.sha256(",".join(sorted_ids).encode("utf-8")).hexdigest()

    @classmethod
    def compute_view_velocity(
        cls,
        asset_id: UUID,
        window_id: UUID,
        obs_earlier: AnalyticsMetricObservation,
        obs_later: AnalyticsMetricObservation,
    ) -> AnalyticsComputedMetric | None:
        """Compute view_velocity_per_hour between two point-in-time view observations."""
        if (
            obs_earlier.metric_name != "views"
            or obs_later.metric_name != "views"
            or obs_earlier.integer_value is None
            or obs_later.integer_value is None
        ):
            return None

        t1 = obs_earlier.observation_timestamp
        t2 = obs_later.observation_timestamp
        delta_seconds = (t2 - t1).total_seconds()
        if delta_seconds < 60.0:
            return None

        delta_views = obs_later.integer_value - obs_earlier.integer_value
        hours = delta_seconds / 3600.0
        velocity = delta_views / max(hours, 0.001)

        source_ids = [obs_earlier.id, obs_later.id]
        source_chk = cls._compute_source_checksum(source_ids)
        formula_id = "FORMULA_VIEW_VELOCITY_V1"
        formula_ver = "1"
        dedupe_key = compute_computation_dedupe_key(
            window_id, "view_velocity_per_hour", formula_id, formula_ver, source_chk
        )

        return AnalyticsComputedMetric(
            asset_id=asset_id,
            window_id=window_id,
            metric_name="view_velocity_per_hour",
            classification=MetricClassification.DETERMINISTIC_DERIVED,
            formula_identifier=formula_id,
            formula_version=formula_ver,
            source_observation_ids=[str(uid) for uid in source_ids],
            source_computed_metric_ids=[],
            source_checksum=source_chk,
            computation_dedupe_key=dedupe_key,
            numeric_value=round(velocity, 4),
            metric_quality=MetricQuality.AVAILABLE,
            computation_revision_sequence=1,
            computed_at=datetime.now(UTC),
        )

    @classmethod
    def compute_window_derived_metrics(
        cls,
        asset_id: UUID,
        window_id: UUID,
        observations: dict[str, AnalyticsMetricObservation],
    ) -> list[AnalyticsComputedMetric]:
        """Compute standard derived ratios and scores from a set of window observations."""
        computed_list: list[AnalyticsComputedMetric] = []
        views_obs = observations.get("views")
        views = views_obs.integer_value if views_obs and views_obs.integer_value is not None else 0

        # 1. Like Ratio (%)
        likes_obs = observations.get("likes")
        if likes_obs and likes_obs.integer_value is not None and views_obs and views_obs.id:
            ratio = (likes_obs.integer_value / max(views, 1)) * 100.0
            source_ids = [likes_obs.id, views_obs.id]
            source_chk = cls._compute_source_checksum(source_ids)
            f_id = "FORMULA_LIKE_RATIO_V1"
            f_ver = "1"
            computed_list.append(
                AnalyticsComputedMetric(
                    asset_id=asset_id,
                    window_id=window_id,
                    metric_name="like_ratio_percent",
                    classification=MetricClassification.DETERMINISTIC_DERIVED,
                    formula_identifier=f_id,
                    formula_version=f_ver,
                    source_observation_ids=[str(uid) for uid in source_ids],
                    source_computed_metric_ids=[],
                    source_checksum=source_chk,
                    computation_dedupe_key=compute_computation_dedupe_key(
                        window_id, "like_ratio_percent", f_id, f_ver, source_chk
                    ),
                    numeric_value=round(ratio, 4),
                    metric_quality=MetricQuality.AVAILABLE,
                    computation_revision_sequence=1,
                    computed_at=datetime.now(UTC),
                )
            )

        # 2. Comment Ratio (%)
        com_obs = observations.get("comments")
        if com_obs and com_obs.integer_value is not None and views_obs and views_obs.id:
            ratio = (com_obs.integer_value / max(views, 1)) * 100.0
            source_ids = [com_obs.id, views_obs.id]
            source_chk = cls._compute_source_checksum(source_ids)
            f_id = "FORMULA_COMMENT_RATIO_V1"
            f_ver = "1"
            computed_list.append(
                AnalyticsComputedMetric(
                    asset_id=asset_id,
                    window_id=window_id,
                    metric_name="comment_ratio_percent",
                    classification=MetricClassification.DETERMINISTIC_DERIVED,
                    formula_identifier=f_id,
                    formula_version=f_ver,
                    source_observation_ids=[str(uid) for uid in source_ids],
                    source_computed_metric_ids=[],
                    source_checksum=source_chk,
                    computation_dedupe_key=compute_computation_dedupe_key(
                        window_id, "comment_ratio_percent", f_id, f_ver, source_chk
                    ),
                    numeric_value=round(ratio, 4),
                    metric_quality=MetricQuality.AVAILABLE,
                    computation_revision_sequence=1,
                    computed_at=datetime.now(UTC),
                )
            )

        # 3. Engagement Rate (%)
        if views_obs and views_obs.id and views >= 10:
            likes = (
                likes_obs.integer_value if likes_obs and likes_obs.integer_value is not None else 0
            )
            comments = com_obs.integer_value if com_obs and com_obs.integer_value is not None else 0
            shares_obs = observations.get("shares")
            shares = (
                shares_obs.integer_value
                if shares_obs and shares_obs.integer_value is not None
                else 0
            )

            source_ids = [views_obs.id]
            if likes_obs:
                source_ids.append(likes_obs.id)
            if com_obs:
                source_ids.append(com_obs.id)
            if shares_obs:
                source_ids.append(shares_obs.id)

            engagement = ((likes + comments + shares) / views) * 100.0
            source_chk = cls._compute_source_checksum(source_ids)
            f_id = "FORMULA_ENGAGEMENT_RATE_V1"
            f_ver = "1"
            computed_list.append(
                AnalyticsComputedMetric(
                    asset_id=asset_id,
                    window_id=window_id,
                    metric_name="engagement_rate_percent",
                    classification=MetricClassification.DETERMINISTIC_DERIVED,
                    formula_identifier=f_id,
                    formula_version=f_ver,
                    source_observation_ids=[str(uid) for uid in source_ids],
                    source_computed_metric_ids=[],
                    source_checksum=source_chk,
                    computation_dedupe_key=compute_computation_dedupe_key(
                        window_id, "engagement_rate_percent", f_id, f_ver, source_chk
                    ),
                    numeric_value=round(engagement, 4),
                    metric_quality=MetricQuality.AVAILABLE,
                    computation_revision_sequence=1,
                    computed_at=datetime.now(UTC),
                )
            )

        # 4. Net Subscribers
        sub_gain = observations.get("subscribers_gained")
        sub_lost = observations.get("subscribers_lost")
        if (
            sub_gain
            and sub_gain.integer_value is not None
            and sub_lost
            and sub_lost.integer_value is not None
        ):
            net = sub_gain.integer_value - sub_lost.integer_value
            source_ids = [sub_gain.id, sub_lost.id]
            source_chk = cls._compute_source_checksum(source_ids)
            f_id = "FORMULA_NET_SUBSCRIBERS_V1"
            f_ver = "1"
            computed_list.append(
                AnalyticsComputedMetric(
                    asset_id=asset_id,
                    window_id=window_id,
                    metric_name="net_subscribers",
                    classification=MetricClassification.DETERMINISTIC_DERIVED,
                    formula_identifier=f_id,
                    formula_version=f_ver,
                    source_observation_ids=[str(uid) for uid in source_ids],
                    source_computed_metric_ids=[],
                    source_checksum=source_chk,
                    computation_dedupe_key=compute_computation_dedupe_key(
                        window_id, "net_subscribers", f_id, f_ver, source_chk
                    ),
                    numeric_value=float(net),
                    metric_quality=MetricQuality.AVAILABLE,
                    computation_revision_sequence=1,
                    computed_at=datetime.now(UTC),
                )
            )

        # 5. Retention Efficiency Score (HEURISTIC_FEATURE ONLY)
        pct_viewed_obs = observations.get("average_percentage_viewed")
        if (
            pct_viewed_obs
            and pct_viewed_obs.numeric_value is not None
            and views_obs
            and views_obs.id
        ):
            pct = pct_viewed_obs.numeric_value
            # Formula: (P_view / 100.0) * (1.0 - exp(-views / 100.0))
            score = (pct / 100.0) * (1.0 - math.exp(-views / 100.0))
            score = max(0.0, min(1.0, score))
            source_ids = [pct_viewed_obs.id, views_obs.id]
            source_chk = cls._compute_source_checksum(source_ids)
            f_id = "HEURISTIC_RETENTION_SCORE_V1"
            f_ver = "1"
            computed_list.append(
                AnalyticsComputedMetric(
                    asset_id=asset_id,
                    window_id=window_id,
                    metric_name="retention_efficiency_score",
                    classification=MetricClassification.HEURISTIC_FEATURE,
                    formula_identifier=f_id,
                    formula_version=f_ver,
                    source_observation_ids=[str(uid) for uid in source_ids],
                    source_computed_metric_ids=[],
                    source_checksum=source_chk,
                    computation_dedupe_key=compute_computation_dedupe_key(
                        window_id, "retention_efficiency_score", f_id, f_ver, source_chk
                    ),
                    numeric_value=round(score, 4),
                    metric_quality=MetricQuality.AVAILABLE,
                    computation_revision_sequence=1,
                    computed_at=datetime.now(UTC),
                )
            )

        return computed_list
