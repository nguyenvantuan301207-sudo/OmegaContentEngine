"""Canonical normalization service for OMEGA-012 Analytics Engine.

Transforms raw provider payloads into strongly typed, immutable PROVIDER_FACT observations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from omega.domain.analytics import (
    MetricClassification,
    MetricQuality,
    compute_observation_dedupe_key,
)

NORMALIZATION_VERSION = 1


@dataclass(frozen=True)
class NormalizedObservationFact:
    """In-memory representation of a single normalized provider fact observation."""

    metric_name: str
    classification: MetricClassification
    metric_quality: MetricQuality
    numeric_value: float | None
    integer_value: int | None
    string_value: str | None
    dimensions: dict[str, Any]
    dimensions_hash: str
    normalization_version: int
    observation_dedupe_key: str
    observation_timestamp: datetime


class AnalyticsNormalizer:
    """Deterministic normalizer for provider facts."""

    @staticmethod
    def _compute_dimensions_hash(dimensions: dict[str, Any]) -> str:
        """Deterministically hash dimension map."""
        if not dimensions:
            return ""
        encoded = json.dumps(dimensions, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def normalize_data_api_video_item(
        cls,
        item: dict[str, Any],
        snapshot_id: UUID,
        window_id: UUID,
        observation_timestamp: datetime,
    ) -> list[NormalizedObservationFact]:
        """Normalize a single YouTube Data API v3 video item (statistics, status)."""
        statistics = item.get("statistics", {})
        dimensions: dict[str, Any] = {}
        dimensions_hash = cls._compute_dimensions_hash(dimensions)

        facts: list[NormalizedObservationFact] = []

        # views
        views_raw = statistics.get("viewCount")
        if views_raw is not None:
            val_int = int(views_raw)
            quality = MetricQuality.ZERO_CONFIRMED if val_int == 0 else MetricQuality.AVAILABLE
            facts.append(
                NormalizedObservationFact(
                    metric_name="views",
                    classification=MetricClassification.PROVIDER_FACT,
                    metric_quality=quality,
                    numeric_value=float(val_int),
                    integer_value=val_int,
                    string_value=None,
                    dimensions=dimensions,
                    dimensions_hash=dimensions_hash,
                    normalization_version=NORMALIZATION_VERSION,
                    observation_dedupe_key=compute_observation_dedupe_key(
                        snapshot_id, window_id, "views", dimensions_hash, NORMALIZATION_VERSION
                    ),
                    observation_timestamp=observation_timestamp,
                )
            )
        else:
            facts.append(
                NormalizedObservationFact(
                    metric_name="views",
                    classification=MetricClassification.PROVIDER_FACT,
                    metric_quality=MetricQuality.UNKNOWN_MISSING,
                    numeric_value=None,
                    integer_value=None,
                    string_value=None,
                    dimensions=dimensions,
                    dimensions_hash=dimensions_hash,
                    normalization_version=NORMALIZATION_VERSION,
                    observation_dedupe_key=compute_observation_dedupe_key(
                        snapshot_id, window_id, "views", dimensions_hash, NORMALIZATION_VERSION
                    ),
                    observation_timestamp=observation_timestamp,
                )
            )

        # likes
        likes_raw = statistics.get("likeCount")
        if likes_raw is not None:
            val_int = int(likes_raw)
            quality = MetricQuality.ZERO_CONFIRMED if val_int == 0 else MetricQuality.AVAILABLE
            facts.append(
                NormalizedObservationFact(
                    metric_name="likes",
                    classification=MetricClassification.PROVIDER_FACT,
                    metric_quality=quality,
                    numeric_value=float(val_int),
                    integer_value=val_int,
                    string_value=None,
                    dimensions=dimensions,
                    dimensions_hash=dimensions_hash,
                    normalization_version=NORMALIZATION_VERSION,
                    observation_dedupe_key=compute_observation_dedupe_key(
                        snapshot_id, window_id, "likes", dimensions_hash, NORMALIZATION_VERSION
                    ),
                    observation_timestamp=observation_timestamp,
                )
            )
        else:
            facts.append(
                NormalizedObservationFact(
                    metric_name="likes",
                    classification=MetricClassification.PROVIDER_FACT,
                    metric_quality=MetricQuality.UNKNOWN_MISSING,
                    numeric_value=None,
                    integer_value=None,
                    string_value=None,
                    dimensions=dimensions,
                    dimensions_hash=dimensions_hash,
                    normalization_version=NORMALIZATION_VERSION,
                    observation_dedupe_key=compute_observation_dedupe_key(
                        snapshot_id, window_id, "likes", dimensions_hash, NORMALIZATION_VERSION
                    ),
                    observation_timestamp=observation_timestamp,
                )
            )

        # comments
        comments_raw = statistics.get("commentCount")
        if comments_raw is not None:
            val_int = int(comments_raw)
            quality = MetricQuality.ZERO_CONFIRMED if val_int == 0 else MetricQuality.AVAILABLE
            facts.append(
                NormalizedObservationFact(
                    metric_name="comments",
                    classification=MetricClassification.PROVIDER_FACT,
                    metric_quality=quality,
                    numeric_value=float(val_int),
                    integer_value=val_int,
                    string_value=None,
                    dimensions=dimensions,
                    dimensions_hash=dimensions_hash,
                    normalization_version=NORMALIZATION_VERSION,
                    observation_dedupe_key=compute_observation_dedupe_key(
                        snapshot_id, window_id, "comments", dimensions_hash, NORMALIZATION_VERSION
                    ),
                    observation_timestamp=observation_timestamp,
                )
            )
        else:
            facts.append(
                NormalizedObservationFact(
                    metric_name="comments",
                    classification=MetricClassification.PROVIDER_FACT,
                    metric_quality=MetricQuality.UNKNOWN_MISSING,
                    numeric_value=None,
                    integer_value=None,
                    string_value=None,
                    dimensions=dimensions,
                    dimensions_hash=dimensions_hash,
                    normalization_version=NORMALIZATION_VERSION,
                    observation_dedupe_key=compute_observation_dedupe_key(
                        snapshot_id, window_id, "comments", dimensions_hash, NORMALIZATION_VERSION
                    ),
                    observation_timestamp=observation_timestamp,
                )
            )

        return facts

    @classmethod
    def normalize_analytics_report_payload(
        cls,
        payload: dict[str, Any],
        snapshot_id: UUID,
        window_id: UUID,
        observation_timestamp: datetime,
    ) -> list[NormalizedObservationFact]:
        """Normalize tabular YouTube Analytics API report payload into observations."""
        headers = [col.get("name") for col in payload.get("columnHeaders", [])]
        rows = payload.get("rows", [])

        facts: list[NormalizedObservationFact] = []
        if not rows or not headers:
            return facts

        metric_name_map = {
            "views": "views",
            "estimatedMinutesWatched": "watch_time_seconds",
            "averageViewDuration": "average_view_duration_seconds",
            "averageViewPercentage": "average_percentage_viewed",
            "subscribersGained": "subscribers_gained",
            "subscribersLost": "subscribers_lost",
            "shares": "shares",
            "impressions": "impressions",
            "impressionClickThroughRate": "impression_ctr_percent",
        }

        # For single-row reports or sum across rows
        for row in rows:
            row_dict = dict(zip(headers, row, strict=False))
            # Extract dimension columns if any
            dims: dict[str, Any] = {}
            if "day" in row_dict:
                dims["day"] = row_dict["day"]
            if "insightTrafficSourceType" in row_dict:
                dims["trafficSource"] = row_dict["insightTrafficSourceType"]

            dim_hash = cls._compute_dimensions_hash(dims)

            for prov_name, canonical_name in metric_name_map.items():
                if prov_name in row_dict:
                    val = row_dict[prov_name]
                    if val is None:
                        quality = MetricQuality.UNKNOWN_MISSING
                        num_val = None
                        int_val = None
                    else:
                        if canonical_name == "watch_time_seconds":
                            # estimatedMinutesWatched -> seconds
                            num_val = float(val) * 60.0
                            int_val = int(num_val)
                            quality = (
                                MetricQuality.ZERO_CONFIRMED
                                if num_val == 0.0
                                else MetricQuality.AVAILABLE
                            )
                        elif canonical_name in (
                            "average_percentage_viewed",
                            "impression_ctr_percent",
                            "average_view_duration_seconds",
                        ):
                            num_val = float(val)
                            int_val = None
                            quality = (
                                MetricQuality.ZERO_CONFIRMED
                                if num_val == 0.0
                                else MetricQuality.AVAILABLE
                            )
                        else:
                            int_val = int(val)
                            num_val = float(int_val)
                            quality = (
                                MetricQuality.ZERO_CONFIRMED
                                if int_val == 0
                                else MetricQuality.AVAILABLE
                            )

                    facts.append(
                        NormalizedObservationFact(
                            metric_name=canonical_name,
                            classification=MetricClassification.PROVIDER_FACT,
                            metric_quality=quality,
                            numeric_value=num_val,
                            integer_value=int_val,
                            string_value=None,
                            dimensions=dims,
                            dimensions_hash=dim_hash,
                            normalization_version=NORMALIZATION_VERSION,
                            observation_dedupe_key=compute_observation_dedupe_key(
                                snapshot_id,
                                window_id,
                                canonical_name,
                                dim_hash,
                                NORMALIZATION_VERSION,
                            ),
                            observation_timestamp=observation_timestamp,
                        )
                    )

        return facts
