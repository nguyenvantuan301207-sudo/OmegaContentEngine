"""Deterministic priority scoring and fairness engine for OMEGA-010.

Calculates explainable priority scores based on mission priority, deadline urgency,
waiting age bonuses, and workload category weights.
Zero randomness, zero non-deterministic ML.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from omega.domain.scheduler import FairnessConfig


@dataclass(frozen=True)
class PriorityBreakdown:
    """Structured evidence explaining the calculated priority score."""

    base_score: float
    urgency_score: float
    age_bonus_score: float
    category_weight: float
    total_score: float
    waiting_seconds: float
    is_starving: bool
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_score": self.base_score,
            "urgency_score": self.urgency_score,
            "age_bonus_score": self.age_bonus_score,
            "category_weight": self.category_weight,
            "total_score": self.total_score,
            "waiting_seconds": self.waiting_seconds,
            "is_starving": self.is_starving,
            "details": self.details,
        }


class PriorityEngine:
    """Computes deterministic, explainable scheduling priority."""

    @staticmethod
    def calculate_priority(
        *,
        mission_priority: int,
        deadline_at: datetime | None,
        created_at: datetime,
        now: datetime | None = None,
        fairness: FairnessConfig | None = None,
        max_urgency_bonus: int = 50,
        category_weight: int = 0,
        target_id: UUID | None = None,
    ) -> PriorityBreakdown:
        """Calculate structured priority score with fairness and tie-break metrics."""
        if now is None:
            now = datetime.now(UTC)
        if fairness is None:
            fairness = FairnessConfig()

        # 1. Base score from mission priority
        base_score = float(mission_priority * 100)

        # 2. Deadline urgency score
        urgency_score = 0.0
        time_to_deadline_seconds: float | None = None
        if deadline_at is not None:
            horizon_seconds = timedelta(days=7).total_seconds()
            time_to_deadline_seconds = (deadline_at - now).total_seconds()
            if time_to_deadline_seconds <= 0:
                urgency_score = float(max_urgency_bonus)
            elif time_to_deadline_seconds < horizon_seconds:
                ratio = (horizon_seconds - time_to_deadline_seconds) / horizon_seconds
                urgency_score = round(ratio * max_urgency_bonus, 2)

        # 3. Age bonus score
        waiting_seconds = max(0.0, (now - created_at).total_seconds())
        intervals = int(waiting_seconds / fairness.age_bonus_interval_seconds)
        raw_age_bonus = intervals * fairness.age_bonus_per_interval
        age_bonus_score = float(min(fairness.age_bonus_cap, raw_age_bonus))

        # 4. Starvation detection
        is_starving = (
            fairness.starvation_threshold_seconds > 0
            and waiting_seconds >= fairness.starvation_threshold_seconds
        )

        # 5. Total composite score
        total_score = base_score + urgency_score + age_bonus_score + float(category_weight)

        details = {
            "mission_priority": mission_priority,
            "deadline_at": deadline_at.isoformat() if deadline_at else None,
            "time_to_deadline_seconds": time_to_deadline_seconds,
            "created_at": created_at.isoformat(),
            "now": now.isoformat(),
            "intervals_waited": intervals,
            "raw_age_bonus": raw_age_bonus,
            "age_bonus_cap": fairness.age_bonus_cap,
            "starvation_threshold_seconds": fairness.starvation_threshold_seconds,
            "target_id": str(target_id) if target_id else None,
        }

        return PriorityBreakdown(
            base_score=base_score,
            urgency_score=urgency_score,
            age_bonus_score=age_bonus_score,
            category_weight=float(category_weight),
            total_score=round(total_score, 2),
            waiting_seconds=round(waiting_seconds, 2),
            is_starving=is_starving,
            details=details,
        )
