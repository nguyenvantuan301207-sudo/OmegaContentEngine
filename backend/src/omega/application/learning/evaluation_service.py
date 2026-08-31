"""Evaluation service for OMEGA-013 Learning Engine."""

from __future__ import annotations

import hashlib
import statistics
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.learning.baseline_service import BaselineService
from omega.application.learning.knowledge_service import KnowledgeService
from omega.domain.analytics import WindowState, WindowType
from omega.domain.learning import (
    CONFIDENCE_POLICY_ID,
    CONFIDENCE_POLICY_VERSION,
    EFFECT_POLICY_ID,
    EFFECT_POLICY_VERSION,
    OMEGA_EFFECT_POLICY_V1,
    ConfidenceClass,
    HypothesisStatus,
    KnowledgeStatus,
    LearningEventType,
    adjust_p_values_benjamini_hochberg,
    compute_cliffs_delta,
    compute_confidence,
    compute_evaluation_key,
    compute_event_dedupe_key,
    compute_manifest_checksum,
    compute_mann_whitney_u,
)
from omega.infrastructure.models import (
    LearningBaseline,
    LearningCohortMember,
    LearningEvaluationEvidenceManifest,
    LearningEvent,
    LearningFeatureSnapshot,
    LearningHypothesis,
    LearningHypothesisEvaluation,
    LearningHypothesisLatestPointer,
    LearningInputSnapshot,
)

ANALYSIS_VERSION: str = "1.0.0"
CORRECTION_METHOD: str = "BENJAMINI_HOCHBERG"
CORRECTION_METHOD_VERSION: int = 1


class EvaluationService:
    """Performs non-parametric statistical hypothesis evaluations and persists immutable evidence manifests."""

    @classmethod
    def _matches_condition(cls, value: Any, condition: dict[str, Any]) -> bool:
        """Evaluate a simple feature match condition (e.g. {'operator': 'gt', 'value': 5})."""
        op = condition.get("operator", "eq")
        target = condition.get("value")
        if op == "eq":
            return value == target
        elif op == "ne":
            return value != target
        elif op == "gt":
            return float(value) > float(target)
        elif op == "gte":
            return float(value) >= float(target)
        elif op == "lt":
            return float(value) < float(target)
        elif op == "lte":
            return float(value) <= float(target)
        elif op == "in":
            return value in target
        return False

    @classmethod
    async def evaluate_hypothesis(
        cls,
        session: AsyncSession,
        hypothesis_family_id: UUID,
        as_of_utc: datetime,
    ) -> tuple[LearningHypothesisEvaluation, LearningEvaluationEvidenceManifest]:
        """Execute a formal hypothesis evaluation atomically with an immutable evidence manifest."""
        # 1. Lock latest pointer FOR UPDATE
        stmt_ptr = (
            select(LearningHypothesisLatestPointer)
            .where(LearningHypothesisLatestPointer.hypothesis_family_id == hypothesis_family_id)
            .with_for_update()
        )
        pointer = (await session.execute(stmt_ptr)).scalar_one_or_none()
        if pointer is None:
            raise ValueError(f"Hypothesis family {hypothesis_family_id} does not exist.")

        hypothesis = await session.get(LearningHypothesis, pointer.current_hypothesis_id)
        if hypothesis is None:
            raise ValueError(f"Current hypothesis {pointer.current_hypothesis_id} not found.")

        window_type = WindowType(hypothesis.target_evaluation_window)
        metric_name = hypothesis.target_outcome_metric

        # 2. Get baseline
        baseline = await BaselineService.calculate_cohort_baseline(
            session=session,
            cohort_id=hypothesis.cohort_id,
            outcome_metric=metric_name,
            evaluation_window=window_type,
            as_of_utc=as_of_utc,
        )
        if baseline is None:
            # Create minimal placeholder baseline if sample < 10
            base_key = f"empty_{hypothesis.cohort_id}_{metric_name}_{as_of_utc.isoformat()}"
            baseline = LearningBaseline(
                cohort_id=hypothesis.cohort_id,
                outcome_metric=metric_name,
                evaluation_window=window_type.value,
                baseline_version=1,
                member_count=0,
                metric_median=0.0,
                metric_mean=0.0,
                metric_stddev=0.0,
                metric_iqr=0.0,
                metric_mad=0.0,
                member_ids_checksum=hashlib.sha256(b"").hexdigest(),
                evaluation_as_of_utc=as_of_utc,
                selection_policy_version=1,
                baseline_key=hashlib.sha256(base_key.encode("utf-8")).hexdigest(),
                calculated_at=datetime.now(UTC),
            )
            session.add(baseline)
            await session.flush()

        # 3. Query all cohort feature snapshots and input observations
        stmt_members = (
            select(LearningFeatureSnapshot, LearningInputSnapshot)
            .join(
                LearningCohortMember,
                LearningCohortMember.feature_snapshot_id == LearningFeatureSnapshot.id,
            )
            .join(
                LearningInputSnapshot,
                LearningInputSnapshot.id == LearningFeatureSnapshot.input_snapshot_id,
            )
            .where(
                LearningCohortMember.cohort_id == hypothesis.cohort_id,
                LearningInputSnapshot.window_type == window_type.value,
                LearningInputSnapshot.window_state.in_(
                    [WindowState.FINALIZED.value, WindowState.REVISED.value]
                ),
                LearningInputSnapshot.published_at_utc <= as_of_utc,
            )
        )
        rows = (await session.execute(stmt_members)).all()

        treatment_snapshots: list[tuple[UUID, UUID, UUID, float]] = []
        control_snapshots: list[tuple[UUID, UUID, UUID, float]] = []

        factor_name = hypothesis.factor_name
        treatment_cond = hypothesis.treatment_definition
        control_cond = hypothesis.control_definition

        for feat, inp in rows:
            feat_val = feat.deterministic_features.get(factor_name) or feat.model_features.get(
                factor_name
            )
            raw_metric_val = inp.raw_metrics.get(metric_name)
            quality = inp.metric_qualities.get(metric_name, "")

            if raw_metric_val is None or quality in (
                "UNKNOWN_MISSING",
                "SUPPRESSED",
                "PROVIDER_ERROR",
                "PERMISSION_DENIED",
            ):
                continue

            num_val = float(raw_metric_val)
            if feat_val is not None and cls._matches_condition(feat_val, treatment_cond):
                treatment_snapshots.append((inp.id, inp.observation_id, feat.id, num_val))
            elif feat_val is not None and cls._matches_condition(feat_val, control_cond):
                control_snapshots.append((inp.id, inp.observation_id, feat.id, num_val))

        n_t = len(treatment_snapshots)
        n_c = len(control_snapshots)

        treatment_vals = [x[3] for x in treatment_snapshots]
        control_vals = [x[3] for x in control_snapshots]

        t_snap_ids = [x[0] for x in treatment_snapshots]
        c_snap_ids = [x[0] for x in control_snapshots]
        all_snap_ids = sorted(str(u) for u in t_snap_ids + c_snap_ids)
        member_set_chk = hashlib.sha256(",".join(all_snap_ids).encode("utf-8")).hexdigest()

        all_obs_ids = sorted(list(set(str(x[1]) for x in treatment_snapshots + control_snapshots)))
        all_feat_ids = sorted(list(set(str(x[2]) for x in treatment_snapshots + control_snapshots)))

        # Fallback values for small sample
        if n_t < 10 or n_c < 10:
            resulting_status = HypothesisStatus.INCONCLUSIVE
            t_med = statistics.median(treatment_vals) if treatment_vals else 0.0
            c_med = statistics.median(control_vals) if control_vals else 0.0
            abs_delta = t_med - c_med
            rel_delta = ((t_med - c_med) / c_med) * 100.0 if c_med > 0 else None
            cliffs_d = compute_cliffs_delta(treatment_vals, control_vals)
            p_raw = 1.0
            p_adj = 1.0
            confidence = ConfidenceClass.VERY_LOW
        else:
            t_med = statistics.median(treatment_vals)
            c_med = statistics.median(control_vals)
            abs_delta = t_med - c_med
            rel_delta = ((t_med - c_med) / c_med) * 100.0 if c_med > 0 else None

            _, p_raw = compute_mann_whitney_u(treatment_vals, control_vals)
            adjusted_list = adjust_p_values_benjamini_hochberg([p_raw])
            p_adj = adjusted_list[0] if adjusted_list else 1.0

            cliffs_d = compute_cliffs_delta(treatment_vals, control_vals)

            policy = OMEGA_EFFECT_POLICY_V1.get(metric_name, {})
            min_cliffs = policy.get("min_cliffs_delta", 0.28)
            min_rel = policy.get("min_relative_delta_percent")
            min_abs = policy.get("min_absolute_delta", 0.0)

            effect_met = abs(cliffs_d) >= min_cliffs and abs(abs_delta) >= min_abs
            if min_rel is not None and rel_delta is not None:
                effect_met = effect_met and abs(rel_delta) >= min_rel

            if p_adj <= 0.05 and effect_met:
                if cliffs_d > 0 and abs_delta > 0:
                    resulting_status = HypothesisStatus.SUPPORTED
                else:
                    resulting_status = HypothesisStatus.CONTRADICTED
            elif pointer.current_status == HypothesisStatus.SUPPORTED.value:
                resulting_status = HypothesisStatus.WEAKENED
            else:
                resulting_status = HypothesisStatus.INCONCLUSIVE

            confidence = compute_confidence(
                n_treatment=n_t,
                n_control=n_c,
                p_adjusted=p_adj,
                cliffs_delta=cliffs_d,
                min_cliffs_delta=min_cliffs,
            )

        eval_version = 1
        eval_key = compute_evaluation_key(hypothesis.id, eval_version, member_set_chk)

        # 4. Insert Evaluation & Evidence Manifest atomically in a savepoint
        async with session.begin_nested():
            evaluation = LearningHypothesisEvaluation(
                hypothesis_id=hypothesis.id,
                hypothesis_family_id=hypothesis.hypothesis_family_id,
                baseline_id=baseline.id,
                evaluation_version=eval_version,
                sample_size_treatment=n_t,
                sample_size_control=n_c,
                treatment_median=t_med,
                control_median=c_med,
                effect_size_absolute=abs_delta,
                effect_size_relative_percent=rel_delta,
                cliffs_delta=cliffs_d,
                p_value_raw=p_raw,
                p_value_adjusted=p_adj,
                confidence_class=confidence.value,
                resulting_status=resulting_status.value,
                evaluation_key=eval_key,
                evaluated_at=datetime.now(UTC),
            )
            session.add(evaluation)
            await session.flush()

            # 5. Insert Evidence Manifest (Mandatory & Atomic)
            manifest_chk = compute_manifest_checksum(
                evaluation_id=evaluation.id,
                treatment_snapshot_ids=t_snap_ids,
                control_snapshot_ids=c_snap_ids,
                treatment_vals=treatment_vals,
                control_vals=control_vals,
                member_set_checksum=member_set_chk,
            )
            family_fdr_id = f"{hypothesis.channel_id}:{hypothesis.cohort_id}:{metric_name}"

            manifest = LearningEvaluationEvidenceManifest(
                evaluation_id=evaluation.id,
                treatment_input_snapshot_ids=[str(u) for u in t_snap_ids],
                control_input_snapshot_ids=[str(u) for u in c_snap_ids],
                source_learning_observation_ids=all_obs_ids,
                source_feature_snapshot_ids=all_feat_ids,
                baseline_id=baseline.id,
                treatment_values=treatment_vals,
                control_values=control_vals,
                member_set_checksum=member_set_chk,
                analysis_version=ANALYSIS_VERSION,
                effect_policy_id=EFFECT_POLICY_ID,
                effect_policy_version=EFFECT_POLICY_VERSION,
                confidence_policy_id=CONFIDENCE_POLICY_ID,
                confidence_policy_version=CONFIDENCE_POLICY_VERSION,
                multiple_comparison_family_id=family_fdr_id,
                correction_method=CORRECTION_METHOD,
                correction_method_version=CORRECTION_METHOD_VERSION,
                evaluation_as_of_utc=as_of_utc,
                manifest_checksum=manifest_chk,
            )
            session.add(manifest)
            await session.flush()

            # 6. Update Hypothesis Latest Pointer
            pointer.latest_evaluation_id = evaluation.id
            pointer.current_status = resulting_status.value
            pointer.updated_at = datetime.now(UTC)
            await session.flush()

        # 7. Promote to Knowledge Memory if SUPPORTED or WEAKENED
        if resulting_status in (HypothesisStatus.SUPPORTED, HypothesisStatus.WEAKENED):
            claim_text = (
                f"Within {hypothesis.hypothesis_slug}, {factor_name} is associated with "
                f"{abs_delta:+.1f} delta in {metric_name} ({confidence.value} confidence, "
                f"Nt={n_t}, Nc={n_c})."
            )
            k_status = (
                KnowledgeStatus.ACTIVE
                if resulting_status == HypothesisStatus.SUPPORTED
                else KnowledgeStatus.WEAKENED
            )
            await KnowledgeService.create_or_advance_knowledge(
                session=session,
                channel_id=hypothesis.channel_id,
                knowledge_type=factor_name.upper(),
                structured_claim={
                    "factor": factor_name,
                    "target_metric": metric_name,
                    "median_difference": abs_delta,
                    "relative_percent": rel_delta,
                },
                human_readable_summary=claim_text,
                confidence_class=confidence,
                effect_size_absolute=abs_delta,
                effect_size_relative_percent=rel_delta,
                cliffs_delta=cliffs_d,
                sample_size_treatment=n_t,
                sample_size_control=n_c,
                source_hypothesis_id=hypothesis.id,
                source_evaluation_id=evaluation.id,
                resulting_status=k_status,
            )

        # 8. Append Learning Event
        event_dedupe = compute_event_dedupe_key(
            LearningEventType.HYPOTHESIS_EVALUATED.value,
            "HYPOTHESIS",
            hypothesis.id,
            eval_key,
        )
        event = LearningEvent(
            event_type=LearningEventType.HYPOTHESIS_EVALUATED.value,
            aggregate_type="HYPOTHESIS",
            aggregate_id=hypothesis.id,
            channel_id=hypothesis.channel_id,
            source_ids=[str(evaluation.id)],
            actor="EvaluationService",
            payload={
                "status": resulting_status.value,
                "confidence": confidence.value,
                "p_adj": p_adj,
                "effect": abs_delta,
            },
            event_dedupe_key=event_dedupe,
            occurred_at=datetime.now(UTC),
        )
        session.add(event)
        await session.flush()

        return evaluation, manifest
