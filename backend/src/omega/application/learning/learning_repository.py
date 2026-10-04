"""Durable database repository for learning evidence, hypotheses, evaluations, insights, recommendations, and candidate adaptations.

Provides the single canonical database repository for P25-D learning loop persistence.
Ensures zero process-local authoritative state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.domain.learning_loop import (
    AdaptationStatus,
    CandidateAdaptation,
    CausalityStatus,
    LearningConfidence,
    LearningEvidence,
    LearningHypothesis,
    LearningHypothesisStatus,
    LearningInsight,
    LearningPolicy,
    LearningRecommendation,
)
from omega.infrastructure.models import (
    AnalyticsProviderSnapshot,
    ExperimentAttributionResultModel,
    ExperimentRevision,
    ExperimentRoot,
    LearningAdaptationApprovalHistoryModel,
    LearningCandidateAdaptationModel,
    LearningEvaluationEvidenceMembershipModel,
    LearningEvaluationModel,
    LearningHypothesisRevisionModel,
    LearningHypothesisRootModel,
    LearningInsightModel,
    LearningLoopEvidenceModel,
    LearningPolicyRevisionModel,
    LearningPolicyRootModel,
    LearningRecommendationModel,
)


class LearningRepository:
    """Canonical repository managing durable persistence for P25-D learning loop."""

    # =========================================================================
    # 1. Policies
    # =========================================================================

    @staticmethod
    def fingerprint(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    @classmethod
    async def get_or_create_policy(
        cls,
        session: AsyncSession,
        policy_name: str = "DEFAULT_LEARNING_POLICY",
        policy_version: str = "1.0.0",
        recency_half_life_days: float = 90.0,
        replication_threshold: int = 2,
        policy: LearningPolicy | None = None,
    ) -> tuple[LearningPolicyRootModel, LearningPolicyRevisionModel]:
        pol = policy or LearningPolicy(
            policy_version=policy_version,
            recency_half_life_days=recency_half_life_days,
            min_causal_replications_for_high=replication_threshold,
        )
        if pol.algorithm_version != "p25d-evaluation-v1":
            raise ValueError("Unsupported immutable learning algorithm version")
        config = pol.model_dump(mode="json")
        fp = cls.fingerprint(config)
        # Serialize creation/revision numbering even for concurrent first writers.
        from sqlalchemy import text

        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "p25d-policy:" + policy_name},
        )
        root = (
            await session.execute(
                select(LearningPolicyRootModel).where(
                    LearningPolicyRootModel.policy_name == policy_name
                )
            )
        ).scalar_one_or_none()
        if root is None:
            root = LearningPolicyRootModel(
                id=uuid4(),
                policy_name=policy_name,
                description="Content-addressed P25-D policy",
                current_revision_id=None,
            )
            session.add(root)
            await session.flush()
        revisions = list(
            (
                await session.execute(
                    select(LearningPolicyRevisionModel).where(
                        LearningPolicyRevisionModel.policy_root_id == root.id
                    )
                )
            ).scalars()
        )
        for rev in revisions:
            if rev.configuration_fingerprint == fp:
                return root, rev
        rev = LearningPolicyRevisionModel(
            id=uuid4(),
            policy_root_id=root.id,
            revision_number=max((r.revision_number for r in revisions), default=0) + 1,
            configuration=config,
            configuration_fingerprint=fp,
            evidence_tier_weights={"causal": pol.causal_weight, "other": 1.0},
            recency_half_life_days=pol.recency_half_life_days,
            replication_threshold=pol.min_causal_replications_for_high,
            confidence_thresholds={
                "support_ratio": pol.support_ratio,
                "minimum_sample_size": pol.min_sample_size_per_evidence,
            },
            generalization_policy={
                "min_replications": pol.generalization_min_replications,
                "scope": "LOCAL",
            },
            ranking_policy={
                "followup": pol.followup_rank,
                "high": pol.high_rank,
                "moderate": pol.moderate_rank,
                "lift_weight": pol.lift_rank_weight,
            },
            goodhart_rules={
                "bound_tradeoffs": True,
                "observational_max_confidence": "LOW",
                "auto_apply": False,
            },
            tradeoff_rules={"negative_threshold_pct": pol.tradeoff_negative_threshold_pct},
            brand_safety_policy_version=pol.brand_safety_policy_version,
        )
        session.add(rev)
        await session.flush()
        root.current_revision_id = rev.id
        await session.flush()
        return root, rev

    # =========================================================================
    # 2. Evidence
    # =========================================================================

    @classmethod
    async def persist_evidence(
        cls,
        session: AsyncSession,
        evidence: LearningEvidence,
    ) -> LearningLoopEvidenceModel:
        """Persist a single durable LearningEvidence item idempotently."""
        # Compute source fingerprint
        fp_payload = evidence.model_dump(mode="json", exclude={"evidence_id"})
        fp = cls.fingerprint(fp_payload)
        from sqlalchemy import text

        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "p25d-evidence:" + fp},
        )

        # Check existing
        stmt = select(LearningLoopEvidenceModel).where(
            and_(
                LearningLoopEvidenceModel.channel_id == evidence.channel_id,
                LearningLoopEvidenceModel.source_fingerprint == fp,
            )
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            return existing

        attr_id = None
        snap_id = None
        provenance = dict(evidence.provenance)
        if evidence.source_phase == "P25-C":
            attr_id = UUID(evidence.source_ids[0])
            attr = await session.get(ExperimentAttributionResultModel, attr_id)
            if attr is None:
                raise ValueError(
                    "P25-C learning evidence requires an exact durable AttributionResult"
                )
            rev = await session.get(ExperimentRevision, attr.experiment_revision_id)
            root = await session.get(ExperimentRoot, rev.experiment_root_id)
            if (
                root.channel_id != evidence.channel_id
                or attr.metric != evidence.metric
                or attr.relative_lift != evidence.effect_magnitude
                or attr.sample_basis != evidence.sample_basis
                or attr.evaluated_at != evidence.observed_at
            ):
                raise ValueError("Evidence does not match its durable attribution source")
            if evidence.causal_status == CausalityStatus.CAUSAL and (
                attr.data_maturity != "MATURE"
                or attr.classification
                not in ("TREATMENT_BETTER", "CONTROL_BETTER", "NO_MEANINGFUL_DIFFERENCE")
                or not attr.statistical_inference
                or not attr.statistical_inference.get("is_statistically_significant")
            ):
                raise ValueError(
                    "Invalid/inconclusive attribution cannot authorize CAUSAL evidence"
                )
            provenance.update(
                {
                    "experiment_root_id": str(root.id),
                    "experiment_revision_id": str(rev.id),
                    "control_variant_id": str(attr.control_variant_id),
                    "treatment_variant_id": str(attr.treatment_variant_id),
                    "attribution_snapshot": {
                        "classification": attr.classification,
                        "data_maturity": attr.data_maturity,
                        "input_lineage_fingerprint": attr.input_lineage_fingerprint,
                        "sample_basis": attr.sample_basis,
                        "statistical_inference": attr.statistical_inference,
                        "findings": attr.findings,
                    },
                }
            )
        elif evidence.source_phase == "P25-B":
            if evidence.causal_status == CausalityStatus.CAUSAL:
                raise ValueError("P25-B is descriptive only")
            snap_id = UUID(evidence.source_ids[0])
            snap = await session.get(AnalyticsProviderSnapshot, snap_id)
            if snap is None or snap.channel_id != evidence.channel_id:
                raise ValueError(
                    "P25-B evidence requires an exact durable provider snapshot for its channel"
                )
            provenance.update(
                {
                    "provider_payload_checksum": snap.payload_checksum,
                    "snapshot_dedupe_key": snap.snapshot_dedupe_key,
                }
            )
        elif evidence.causal_status == CausalityStatus.CAUSAL:
            raise ValueError("Only durable P25-C results authorize causal evidence")
        provenance["causal_classification_reason"] = (
            "Valid mature significant P25-C attribution"
            if evidence.causal_status == CausalityStatus.CAUSAL
            else "Observational or insufficient source; no causal claim"
        )
        evidence = evidence.model_copy(update={"provenance": provenance})

        scope_ctx = {
            "target_scope_type": evidence.target_scope_type,
            "target_scope_id": evidence.target_scope_id,
            "content_format": evidence.content_format,
            "content_pillar": evidence.content_pillar,
        }

        tier_str = evidence.evidence_tier.name

        model = LearningLoopEvidenceModel(
            id=evidence.evidence_id,
            domain_snapshot=evidence.model_dump(mode="json"),
            evidence_type=f"{evidence.source_phase}_EVIDENCE",
            source_phase=evidence.source_phase,
            source_id=",".join(evidence.source_ids),
            attribution_result_id=attr_id,
            snapshot_id=snap_id,
            channel_id=evidence.channel_id,
            scope_context=scope_ctx,
            creative_dimensions=evidence.creative_dimensions,
            metric=evidence.metric,
            effect_direction=evidence.effect_direction,
            effect_magnitude=evidence.effect_magnitude,
            sample_basis=evidence.sample_basis,
            data_maturity="MATURE"
            if evidence.causal_status == CausalityStatus.CAUSAL
            else "OBSERVATIONAL",
            causal_status=evidence.causal_status.value,
            evidence_tier=tier_str,
            quality_flags={"quality_score": evidence.quality_score},
            source_fingerprint=fp,
            observed_at=evidence.observed_at,
            provenance=evidence.provenance,
        )
        session.add(model)
        await session.flush()
        return model

    @classmethod
    async def find_evidence_by_id(
        cls,
        session: AsyncSession,
        evidence_id: UUID,
    ) -> LearningLoopEvidenceModel | None:
        """Find evidence by its primary key ID."""
        stmt = select(LearningLoopEvidenceModel).where(LearningLoopEvidenceModel.id == evidence_id)
        return (await session.execute(stmt)).scalars().first()

    # =========================================================================
    # 3. Hypotheses
    # =========================================================================

    @classmethod
    async def get_or_create_hypothesis(
        cls,
        session: AsyncSession,
        hypothesis: LearningHypothesis,
    ) -> tuple[LearningHypothesisRootModel, LearningHypothesisRevisionModel]:
        """Find existing hypothesis by deduplication fingerprint or create new root + revision."""
        dedup_fp = hypothesis.compute_dedupe_key()
        definition = hypothesis.model_dump(
            mode="json",
            exclude={
                "hypothesis_id",
                "status",
                "revision_number",
                "supersedes_hypothesis_id",
                "created_at",
                "provenance",
            },
        )
        definition_fp = cls.fingerprint(definition)
        from sqlalchemy import text

        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "p25d-hypothesis:" + dedup_fp},
        )

        stmt = (
            select(LearningHypothesisRootModel)
            .where(
                and_(
                    LearningHypothesisRootModel.channel_id == hypothesis.channel_id,
                    LearningHypothesisRootModel.dedup_fingerprint == dedup_fp,
                )
            )
            .options(selectinload(LearningHypothesisRootModel.revisions))
            .execution_options(populate_existing=True)
        )
        root = (await session.execute(stmt)).scalars().first()

        scope_ctx = {
            "target_scope_type": hypothesis.target_scope_type,
            "target_scope_id": hypothesis.target_scope_id,
            "content_pillar": hypothesis.content_pillar,
            "content_format": hypothesis.content_format,
        }

        if root:
            for rev in root.revisions:
                if rev.definition_fingerprint == definition_fp:
                    return root, rev
            number = max(r.revision_number for r in root.revisions) + 1
            snapshot = hypothesis.model_copy(
                update={"hypothesis_id": root.id, "revision_number": number}
            )
            rev = LearningHypothesisRevisionModel(
                id=uuid4(),
                hypothesis_root_id=root.id,
                revision_number=number,
                definition_fingerprint=definition_fp,
                domain_snapshot=snapshot.model_dump(mode="json"),
                claim=hypothesis.statement,
                target_dimension=hypothesis.creative_dimension,
                target_metric=hypothesis.predicted_metric,
                predicted_direction=hypothesis.predicted_direction,
                scope_context=scope_ctx,
                generalization_scope="LOCAL",
                evidence_requirements=hypothesis.evidence_requirements,
                provenance=hypothesis.provenance,
            )
            session.add(rev)
            await session.flush()
            root.current_revision_id = rev.id
            await session.flush()
            return root, rev

        # Create new root and revision
        root_id = hypothesis.hypothesis_id
        rev_id = uuid4()
        rev = LearningHypothesisRevisionModel(
            id=rev_id,
            hypothesis_root_id=root_id,
            revision_number=1,
            definition_fingerprint=definition_fp,
            domain_snapshot=hypothesis.model_copy(update={"revision_number": 1}).model_dump(
                mode="json"
            ),
            claim=hypothesis.statement,
            target_dimension=hypothesis.creative_dimension,
            target_metric=hypothesis.predicted_metric,
            predicted_direction=hypothesis.predicted_direction,
            scope_context=scope_ctx,
            generalization_scope="LOCAL",
            evidence_requirements=hypothesis.evidence_requirements,
            provenance=hypothesis.provenance,
        )
        root = LearningHypothesisRootModel(
            id=root_id,
            channel_id=hypothesis.channel_id,
            dedup_fingerprint=dedup_fp,
            target_dimension=hypothesis.creative_dimension,
            target_metric=hypothesis.predicted_metric,
            status=hypothesis.status.value,
            current_revision_id=None,
        )
        session.add(root)
        await session.flush()

        session.add(rev)
        await session.flush()

        root.current_revision_id = rev_id
        await session.flush()
        return root, rev

    @classmethod
    async def find_hypothesis_by_id(
        cls,
        session: AsyncSession,
        hypothesis_id: UUID,
    ) -> LearningHypothesisRootModel | None:
        """Find hypothesis root by ID with revisions."""
        stmt = (
            select(LearningHypothesisRootModel)
            .where(LearningHypothesisRootModel.id == hypothesis_id)
            .options(selectinload(LearningHypothesisRootModel.revisions))
            .execution_options(populate_existing=True)
        )
        return (await session.execute(stmt)).scalars().first()

    # =========================================================================
    # 4. Evaluations & Memberships
    # =========================================================================

    @classmethod
    async def persist_evaluation(
        cls,
        session: AsyncSession,
        hypothesis_revision_id: UUID,
        policy_revision_id: UUID,
        evidence_set_fingerprint: str,
        causal_status: CausalityStatus,
        resulting_status: LearningHypothesisStatus,
        confidence: LearningConfidence,
        net_effect_magnitude: float | None,
        tradeoffs: list[str],
        limitations: list[str],
        provenance: dict[str, Any],
        evaluated_at: datetime,
        memberships: list[tuple[UUID, str, float]],  # (evidence_id, role, weight)
    ) -> tuple[LearningEvaluationModel, bool]:
        """Persist evaluation idempotently. Returns (evaluation_model, is_new)."""
        stmt = (
            select(LearningEvaluationModel)
            .where(
                and_(
                    LearningEvaluationModel.hypothesis_revision_id == hypothesis_revision_id,
                    LearningEvaluationModel.policy_revision_id == policy_revision_id,
                    LearningEvaluationModel.evidence_set_fingerprint == evidence_set_fingerprint,
                )
            )
            .options(
                selectinload(LearningEvaluationModel.memberships),
                selectinload(LearningEvaluationModel.insights),
            )
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            return existing, False

        eval_id = uuid4()
        eval_model = LearningEvaluationModel(
            id=eval_id,
            hypothesis_revision_id=hypothesis_revision_id,
            policy_revision_id=policy_revision_id,
            evidence_set_fingerprint=evidence_set_fingerprint,
            causal_status=causal_status.value,
            resulting_status=resulting_status.value,
            confidence=confidence.value,
            net_effect_magnitude=net_effect_magnitude,
            tradeoffs_detected=tradeoffs,
            limitations=limitations,
            provenance=provenance,
            evaluated_at=evaluated_at,
        )
        session.add(eval_model)
        await session.flush()

        for ev_id, role, weight in memberships:
            m = LearningEvaluationEvidenceMembershipModel(
                id=uuid4(),
                evaluation_id=eval_id,
                evidence_id=ev_id,
                role=role,
                weight=weight,
            )
            session.add(m)

        # Update root status
        rev_stmt = select(LearningHypothesisRevisionModel).where(
            LearningHypothesisRevisionModel.id == hypothesis_revision_id
        )
        rev = (await session.execute(rev_stmt)).scalars().first()
        if rev:
            await session.execute(
                update(LearningHypothesisRootModel)
                .where(LearningHypothesisRootModel.id == rev.hypothesis_root_id)
                .values(status=resulting_status.value, updated_at=datetime.now(UTC))
            )

        await session.flush()
        return eval_model, True

    # =========================================================================
    # 5. Insights
    # =========================================================================

    @classmethod
    async def persist_insight(
        cls,
        session: AsyncSession,
        insight: LearningInsight,
        evaluation_id: UUID,
        hypothesis_revision_id: UUID,
        policy_revision_id: UUID,
    ) -> LearningInsightModel:
        """Persist LearningInsight linked to evaluation and revisions."""
        stmt = select(LearningInsightModel).where(
            (LearningInsightModel.id == insight.insight_id)
            | (LearningInsightModel.evaluation_id == evaluation_id)
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            return existing

        model = LearningInsightModel(
            id=insight.insight_id,
            domain_snapshot=insight.model_dump(mode="json"),
            generated_at=insight.generated_at,
            evaluation_id=evaluation_id,
            hypothesis_revision_id=hypothesis_revision_id,
            policy_revision_id=policy_revision_id,
            scope_context={"scope": insight.scope},
            summary=insight.summary,
            causal_status=insight.causal_status.value,
            confidence=insight.confidence.value,
            metric_impact=insight.metric_impacts,
            tradeoffs=insight.tradeoffs,
            limitations=insight.limitations,
            provenance=insight.provenance,
        )
        session.add(model)
        await session.flush()
        return model

    # =========================================================================
    # 6. Recommendations & Candidate Adaptations
    # =========================================================================

    @classmethod
    async def persist_recommendation(
        cls,
        session: AsyncSession,
        recommendation: LearningRecommendation,
        insight_id: UUID,
        evaluation_id: UUID,
        policy_revision_id: UUID,
    ) -> tuple[LearningRecommendationModel, bool]:
        """Persist recommendation and its candidate adaptation idempotently."""
        pinned = await session.get(LearningInsightModel, insight_id)
        if (
            pinned is None
            or pinned.evaluation_id != evaluation_id
            or pinned.policy_revision_id != policy_revision_id
        ):
            raise ValueError("Recommendation lineage must match the exact persisted insight")
        replay_fp = hashlib.sha256(
            json.dumps(
                {
                    "insight_id": str(insight_id),
                    "evaluation_id": str(evaluation_id),
                    "policy_revision_id": str(policy_revision_id),
                    "action": recommendation.action.value,
                    "target_authority": recommendation.target_authority,
                    "proposed_change": recommendation.proposed_change,
                    "scope": recommendation.scope,
                    "expected_metrics": recommendation.expected_metrics,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        target_dim = (
            recommendation.candidate_adaptation.target_dimension
            if recommendation.candidate_adaptation
            else recommendation.target_authority
        )

        stmt = (
            select(LearningRecommendationModel)
            .where(
                and_(
                    LearningRecommendationModel.evaluation_id == evaluation_id,
                    LearningRecommendationModel.action == recommendation.action.value,
                    LearningRecommendationModel.target_dimension == target_dim,
                    LearningRecommendationModel.replay_fingerprint == replay_fp,
                )
            )
            .options(selectinload(LearningRecommendationModel.adaptations))
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            return existing, False

        target_dim = (
            recommendation.candidate_adaptation.target_dimension
            if recommendation.candidate_adaptation
            else recommendation.target_authority
        )
        risk_lvl = (
            recommendation.risk_assessment.split(":")[0]
            if ":" in recommendation.risk_assessment
            else recommendation.risk_assessment
        ).strip()[:32]
        metric_effect_str = str(recommendation.expected_metrics)[:64]
        safety_snap = {
            "goodhart_result": "BOUNDED" if recommendation.tradeoffs else "NO_DETECTED_TRADEOFF",
            "brand_safety_result": "REQUIRES_PRODUCER_REVIEW",
            "risk_assessment": recommendation.risk_assessment,
        }

        rec_model = LearningRecommendationModel(
            id=recommendation.recommendation_id,
            domain_snapshot=recommendation.model_dump(mode="json"),
            insight_id=insight_id,
            evaluation_id=evaluation_id,
            policy_revision_id=policy_revision_id,
            action=recommendation.action.value,
            target_authority=recommendation.target_authority,
            target_dimension=target_dim,
            scope_context={"scope": recommendation.scope},
            confidence=recommendation.confidence.value,
            ranking_score=recommendation.ranking_score,
            risk_level=risk_lvl,
            expected_metric_effect=metric_effect_str,
            safety_snapshot=safety_snap,
            validation_requirements=["producer_signoff"],
            explanation=recommendation.explanation,
            replay_fingerprint=replay_fp,
        )
        session.add(rec_model)

        if recommendation.candidate_adaptation:
            cand = recommendation.candidate_adaptation
            cand_model = LearningCandidateAdaptationModel(
                id=cand.adaptation_id,
                domain_snapshot=cand.model_dump(mode="json"),
                recommendation_id=recommendation.recommendation_id,
                target_authority=cand.target_authority,
                target_field=cand.target_dimension,
                current_value_context={"value": cand.current_value},
                proposed_value={"value": cand.proposed_value},
                scope_context={"scope": cand.scope},
                confidence=cand.confidence.value,
                status=cand.status.value,
                validation_requirement=cand.validation_requirement,
            )
            session.add(cand_model)

            # Record initial PROPOSED history
            hist = LearningAdaptationApprovalHistoryModel(
                id=uuid4(),
                candidate_adaptation_id=cand.adaptation_id,
                from_status="NONE",
                to_status=AdaptationStatus.PROPOSED.value,
                transition_reason="Generated by evidence-based learning loop recommendation",
                actor="P25D_LEARNING_ENGINE",
                evidence_context={"recommendation_id": str(recommendation.recommendation_id)},
            )
            await session.flush()
            session.add(hist)

        await session.flush()
        return rec_model, True

    @classmethod
    async def transition_candidate_adaptation(
        cls,
        session: AsyncSession,
        candidate_id: UUID,
        to_status: AdaptationStatus,
        actor: str,
        reason: str,
    ) -> LearningCandidateAdaptationModel:
        """Explicitly transition candidate adaptation status and record immutable audit history."""
        stmt = (
            select(LearningCandidateAdaptationModel)
            .where(LearningCandidateAdaptationModel.id == candidate_id)
            .options(selectinload(LearningCandidateAdaptationModel.approval_history))
            .with_for_update()
        )
        cand = (await session.execute(stmt)).scalars().first()
        if not cand:
            raise ValueError(f"CandidateAdaptation {candidate_id} not found.")

        if not actor.strip() or not reason.strip():
            raise ValueError("Approval history requires an actor and reason")
        allowed = {
            "PROPOSED": {"APPROVED", "REJECTED", "EXPIRED", "SUPERSEDED"},
            "APPROVED": {"EXPIRED", "SUPERSEDED"},
            "REJECTED": set(),
            "EXPIRED": set(),
            "SUPERSEDED": set(),
        }
        if to_status.value not in allowed[cand.status]:
            raise ValueError(f"Invalid candidate transition {cand.status} -> {to_status.value}")
        from_status = cand.status

        hist = LearningAdaptationApprovalHistoryModel(
            id=uuid4(),
            candidate_adaptation_id=cand.id,
            from_status=from_status,
            to_status=to_status.value,
            transition_reason=reason,
            actor=actor,
            evidence_context={},
        )
        session.add(hist)
        cand.approval_history.append(hist)
        await session.flush()
        cand.status = to_status.value
        cand.updated_at = datetime.now(UTC)
        await session.flush()
        return cand

    @classmethod
    async def get_candidate_adaptation(
        cls,
        session: AsyncSession,
        candidate_id: UUID,
    ) -> LearningCandidateAdaptationModel | None:
        """Get candidate adaptation with its approval history."""
        stmt = (
            select(LearningCandidateAdaptationModel)
            .where(LearningCandidateAdaptationModel.id == candidate_id)
            .options(selectinload(LearningCandidateAdaptationModel.approval_history))
        )
        return (await session.execute(stmt)).scalars().first()

    # =========================================================================
    # 7. Queries
    # =========================================================================

    @classmethod
    async def list_hypotheses(
        cls,
        session: AsyncSession,
        channel_id: UUID,
    ) -> list[LearningHypothesisRootModel]:
        """List all hypotheses for a channel."""
        stmt = (
            select(LearningHypothesisRootModel)
            .where(LearningHypothesisRootModel.channel_id == channel_id)
            .options(selectinload(LearningHypothesisRootModel.revisions))
            .execution_options(populate_existing=True)
            .order_by(desc(LearningHypothesisRootModel.created_at))
        )
        return list((await session.execute(stmt)).scalars().all())

    @classmethod
    async def list_recommendations(
        cls,
        session: AsyncSession,
    ) -> list[LearningRecommendationModel]:
        """List all recommendations ordered by ranking score."""
        stmt = (
            select(LearningRecommendationModel)
            .options(
                selectinload(LearningRecommendationModel.insight),
                selectinload(LearningRecommendationModel.adaptations),
            )
            .order_by(desc(LearningRecommendationModel.ranking_score))
        )
        return list((await session.execute(stmt)).scalars().all())

    @classmethod
    async def load_evidence(cls, session: AsyncSession, evidence_id: UUID) -> LearningEvidence:
        row = await session.get(LearningLoopEvidenceModel, evidence_id)
        if row is None:
            raise ValueError("Learning evidence not found")
        return LearningEvidence.model_validate(row.domain_snapshot)

    @classmethod
    async def load_hypothesis_revision(
        cls, session: AsyncSession, revision_id: UUID
    ) -> LearningHypothesis:
        row = await session.get(LearningHypothesisRevisionModel, revision_id)
        if row is None:
            raise ValueError("Hypothesis revision not found")
        return LearningHypothesis.model_validate(row.domain_snapshot)

    @classmethod
    async def load_policy_revision(cls, session: AsyncSession, revision_id: UUID) -> LearningPolicy:
        row = await session.get(LearningPolicyRevisionModel, revision_id)
        if row is None:
            raise ValueError("Policy revision not found")
        if cls.fingerprint(row.configuration) != row.configuration_fingerprint:
            raise ValueError("Policy fingerprint mismatch")
        return LearningPolicy.model_validate(row.configuration)

    @classmethod
    async def load_insight(cls, session: AsyncSession, insight_id: UUID) -> LearningInsight:
        row = await session.get(LearningInsightModel, insight_id)
        if row is None:
            raise ValueError("Insight not found")
        return LearningInsight.model_validate(row.domain_snapshot)

    @classmethod
    async def load_candidate(cls, session: AsyncSession, candidate_id: UUID) -> CandidateAdaptation:
        row = await cls.get_candidate_adaptation(session, candidate_id)
        if row is None:
            raise ValueError("Candidate not found")
        original = CandidateAdaptation.model_validate(row.domain_snapshot)
        history = sorted(row.approval_history, key=lambda h: (h.transitioned_at, str(h.id)))
        last = history[-1]
        return original.model_copy(
            update={
                "status": AdaptationStatus(row.status),
                "decision_at": last.transitioned_at if row.status != "PROPOSED" else None,
                "decision_reason": last.transition_reason if row.status != "PROPOSED" else None,
            }
        )

    @classmethod
    async def load_recommendation(
        cls, session: AsyncSession, recommendation_id: UUID
    ) -> LearningRecommendation:
        row = await session.get(LearningRecommendationModel, recommendation_id)
        if row is None:
            raise ValueError("Recommendation not found")
        return LearningRecommendation.model_validate(row.domain_snapshot)

    @classmethod
    async def get_evidence_trace(cls, session: AsyncSession, hypothesis_id: UUID) -> dict[str, Any]:
        root = await cls.find_hypothesis_by_id(session, hypothesis_id)
        if root is None:
            return {"error": "Hypothesis not found"}
        evaluations = list(
            (
                await session.execute(
                    select(LearningEvaluationModel)
                    .join(LearningHypothesisRevisionModel)
                    .where(LearningHypothesisRevisionModel.hypothesis_root_id == hypothesis_id)
                    .order_by(LearningEvaluationModel.evaluated_at, LearningEvaluationModel.id)
                )
            ).scalars()
        )
        trace = {
            "hypothesis_id": str(root.id),
            "channel_id": str(root.channel_id),
            "status": root.status,
            "revisions_count": len(root.revisions),
            "evaluations": [],
        }
        for ev in evaluations:
            rev = await session.get(LearningHypothesisRevisionModel, ev.hypothesis_revision_id)
            policy = await session.get(LearningPolicyRevisionModel, ev.policy_revision_id)
            memberships = list(
                (
                    await session.execute(
                        select(LearningEvaluationEvidenceMembershipModel)
                        .where(LearningEvaluationEvidenceMembershipModel.evaluation_id == ev.id)
                        .order_by(
                            LearningEvaluationEvidenceMembershipModel.evidence_id,
                            LearningEvaluationEvidenceMembershipModel.role,
                        )
                    )
                ).scalars()
            )
            items = []
            for membership in memberships:
                evidence = await session.get(LearningLoopEvidenceModel, membership.evidence_id)
                items.append(
                    {
                        "evidence_id": str(evidence.id),
                        "role": membership.role,
                        "weight": membership.weight,
                        "tier": evidence.evidence_tier,
                        "metric": evidence.metric,
                        "magnitude": evidence.effect_magnitude,
                        "source_type": evidence.source_phase,
                        "source_ids": evidence.domain_snapshot["source_ids"],
                        "attribution_result_id": str(evidence.attribution_result_id)
                        if evidence.attribution_result_id
                        else None,
                        "snapshot_id": str(evidence.snapshot_id) if evidence.snapshot_id else None,
                        "causal_status": evidence.causal_status,
                        "source_fingerprint": evidence.source_fingerprint,
                        "provenance": evidence.provenance,
                        "evidence": evidence.domain_snapshot,
                    }
                )
            insights = list(
                (
                    await session.execute(
                        select(LearningInsightModel).where(
                            LearningInsightModel.evaluation_id == ev.id
                        )
                    )
                ).scalars()
            )
            insight_items = []
            for insight in insights:
                recs = list(
                    (
                        await session.execute(
                            select(LearningRecommendationModel)
                            .where(LearningRecommendationModel.insight_id == insight.id)
                            .order_by(LearningRecommendationModel.id)
                        )
                    ).scalars()
                )
                recommendations = []
                for rec in recs:
                    candidates = list(
                        (
                            await session.execute(
                                select(LearningCandidateAdaptationModel).where(
                                    LearningCandidateAdaptationModel.recommendation_id == rec.id
                                )
                            )
                        ).scalars()
                    )
                    candidate_items = []
                    for candidate in candidates:
                        history = list(
                            (
                                await session.execute(
                                    select(LearningAdaptationApprovalHistoryModel)
                                    .where(
                                        LearningAdaptationApprovalHistoryModel.candidate_adaptation_id
                                        == candidate.id
                                    )
                                    .order_by(
                                        LearningAdaptationApprovalHistoryModel.transitioned_at,
                                        LearningAdaptationApprovalHistoryModel.id,
                                    )
                                )
                            ).scalars()
                        )
                        candidate_items.append(
                            {
                                "candidate_id": str(candidate.id),
                                "status": candidate.status,
                                "proposal": candidate.domain_snapshot,
                                "history": [
                                    {
                                        "id": str(h.id),
                                        "from_status": h.from_status,
                                        "to_status": h.to_status,
                                        "actor": h.actor,
                                        "reason": h.transition_reason,
                                        "transitioned_at": h.transitioned_at.isoformat(),
                                        "evidence_context": h.evidence_context,
                                    }
                                    for h in history
                                ],
                            }
                        )
                    recommendations.append(
                        {
                            "recommendation_id": str(rec.id),
                            "evaluation_id": str(rec.evaluation_id),
                            "policy_revision_id": str(rec.policy_revision_id),
                            "fingerprint": rec.replay_fingerprint,
                            "recommendation": rec.domain_snapshot,
                            "safety_snapshot": rec.safety_snapshot,
                            "candidates": candidate_items,
                        }
                    )
                insight_items.append(
                    {
                        "insight_id": str(insight.id),
                        "evaluation_id": str(insight.evaluation_id),
                        "insight": insight.domain_snapshot,
                        "recommendations": recommendations,
                    }
                )
            trace["evaluations"].append(
                {
                    "evaluation_id": str(ev.id),
                    "hypothesis_revision_id": str(rev.id),
                    "hypothesis_revision_number": rev.revision_number,
                    "hypothesis": rev.domain_snapshot,
                    "policy_revision_id": str(policy.id),
                    "policy_revision_number": policy.revision_number,
                    "policy_fingerprint": policy.configuration_fingerprint,
                    "policy": policy.configuration,
                    "evidence_set_fingerprint": ev.evidence_set_fingerprint,
                    "evaluated_at": ev.evaluated_at.isoformat(),
                    "resulting_status": ev.resulting_status,
                    "causal_status": ev.causal_status,
                    "confidence": ev.confidence,
                    "tradeoffs": ev.tradeoffs_detected,
                    "limitations": ev.limitations,
                    "provenance": ev.provenance,
                    "memberships": items,
                    "insights": insight_items,
                }
            )
        return trace
