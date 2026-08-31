"""Baseline computation service for OMEGA-013 Learning Engine."""

from __future__ import annotations

import hashlib
import statistics
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.analytics import WindowState, WindowType
from omega.domain.learning import (
    SELECTION_POLICY_VERSION,
    LearningEventType,
    compute_baseline_key,
    compute_event_dedupe_key,
)
from omega.infrastructure.models import (
    LearningBaseline,
    LearningCohort,
    LearningCohortMember,
    LearningEvent,
    LearningFeatureSnapshot,
    LearningInputSnapshot,
)


class BaselineService:
    """Computes immutable, reproducible performance baselines for comparison cohorts."""

    @classmethod
    async def calculate_cohort_baseline(
        cls,
        session: AsyncSession,
        cohort_id: UUID,
        outcome_metric: str,
        evaluation_window: WindowType,
        as_of_utc: datetime,
    ) -> LearningBaseline | None:
        """Compute rolling baseline for a cohort over up to 30 eligible videos in 90 days.

        Requires minimum N >= 10 eligible samples. Missing metrics are excluded, not zeroed.
        """
        cohort = await session.get(LearningCohort, cohort_id)
        if cohort is None:
            return None

        earliest_pub = as_of_utc - timedelta(days=90)

        # Query eligible cohort members within 90-day horizon on or before as_of_utc
        stmt = (
            select(LearningInputSnapshot)
            .join(
                LearningFeatureSnapshot,
                LearningFeatureSnapshot.input_snapshot_id == LearningInputSnapshot.id,
            )
            .join(
                LearningCohortMember,
                LearningCohortMember.feature_snapshot_id == LearningFeatureSnapshot.id,
            )
            .where(
                LearningCohortMember.cohort_id == cohort.id,
                LearningInputSnapshot.window_type == evaluation_window.value,
                LearningInputSnapshot.window_state.in_(
                    [WindowState.FINALIZED.value, WindowState.REVISED.value]
                ),
                LearningInputSnapshot.published_at_utc <= as_of_utc,
                LearningInputSnapshot.published_at_utc >= earliest_pub,
            )
            .order_by(LearningInputSnapshot.published_at_utc.desc())
            .limit(30)
        )
        snapshots = (await session.execute(stmt)).scalars().all()

        # Filter to snapshots with valid metric values
        valid_items: list[tuple[UUID, float]] = []
        for snap in snapshots:
            val = snap.raw_metrics.get(outcome_metric)
            quality = snap.metric_qualities.get(outcome_metric, "")
            if val is not None and quality not in (
                "UNKNOWN_MISSING",
                "SUPPRESSED",
                "PROVIDER_ERROR",
                "PERMISSION_DENIED",
            ):
                valid_items.append((snap.id, float(val)))

        if len(valid_items) < 10:
            return None

        values = [item[1] for item in valid_items]
        member_ids = sorted(str(item[0]) for item in valid_items)
        member_ids_chk = hashlib.sha256(",".join(member_ids).encode("utf-8")).hexdigest()

        # Check existing latest baseline
        stmt_latest = (
            select(LearningBaseline)
            .where(
                LearningBaseline.cohort_id == cohort.id,
                LearningBaseline.outcome_metric == outcome_metric,
                LearningBaseline.evaluation_window == evaluation_window.value,
            )
            .order_by(LearningBaseline.baseline_version.desc())
            .limit(1)
        )
        latest = (await session.execute(stmt_latest)).scalar_one_or_none()

        if latest is not None and latest.member_ids_checksum == member_ids_chk:
            return latest

        next_ver = (latest.baseline_version + 1) if latest is not None else 1

        med = statistics.median(values)
        mean_val = statistics.mean(values)
        std_val = statistics.stdev(values) if len(values) > 1 else 0.0

        sorted_vals = sorted(values)
        n = len(sorted_vals)
        q1 = sorted_vals[int(0.25 * (n - 1))]
        q3 = sorted_vals[int(0.75 * (n - 1))]
        iqr_val = q3 - q1

        abs_devs = [abs(x - med) for x in values]
        mad_val = statistics.median(abs_devs)

        base_key = compute_baseline_key(
            cohort.id, outcome_metric, evaluation_window, next_ver, member_ids_chk
        )

        baseline = LearningBaseline(
            cohort_id=cohort.id,
            outcome_metric=outcome_metric,
            evaluation_window=evaluation_window.value,
            baseline_version=next_ver,
            member_count=len(values),
            metric_median=med,
            metric_mean=mean_val,
            metric_stddev=std_val,
            metric_iqr=iqr_val,
            metric_mad=mad_val,
            member_ids_checksum=member_ids_chk,
            evaluation_as_of_utc=as_of_utc,
            selection_policy_version=SELECTION_POLICY_VERSION,
            baseline_key=base_key,
            calculated_at=datetime.now(UTC),
        )
        session.add(baseline)
        await session.flush()

        event_dedupe = compute_event_dedupe_key(
            LearningEventType.BASELINE_CREATED.value, "BASELINE", baseline.id, base_key
        )
        event = LearningEvent(
            event_type=LearningEventType.BASELINE_CREATED.value,
            aggregate_type="BASELINE",
            aggregate_id=baseline.id,
            channel_id=cohort.channel_id,
            source_ids=[str(item[0]) for item in valid_items],
            actor="BaselineService",
            payload={
                "outcome_metric": outcome_metric,
                "baseline_version": next_ver,
                "median": med,
                "mad": mad_val,
            },
            event_dedupe_key=event_dedupe,
            occurred_at=baseline.calculated_at,
        )
        session.add(event)
        await session.flush()

        return baseline
