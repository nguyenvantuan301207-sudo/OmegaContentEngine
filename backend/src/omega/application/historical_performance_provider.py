"""Application provider and detached corpus builder for historical performance feedback."""

from __future__ import annotations

import contextlib
import math
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.duplicate_detector import normalize_text
from omega.domain.historical_performance import (
    HISTORICAL_ALLOWED_CLASSIFICATIONS,
    HISTORICAL_ALLOWED_QUALITIES,
    HISTORICAL_ALLOWED_WINDOW_STATES,
    HISTORICAL_ELIGIBLE_METRICS,
    HISTORICAL_EVALUATION_WINDOW,
    HISTORICAL_MAX_CORPUS_SIZE,
    HISTORICAL_MAX_MATCHES,
    HISTORICAL_MIN_CORPUS_SIZE,
    HISTORICAL_MIN_MATCHED_ITEMS,
    HISTORICAL_MIN_RELEVANCE,
    HISTORICAL_NEUTRAL_SCORE,
    HISTORICAL_PERFORMANCE_POLICY_NAME,
    HISTORICAL_PERFORMANCE_POLICY_VERSION,
    ContentIdentityAuthority,
    HistoricalContentIdentity,
    HistoricalPerformanceIntegrityError,
    HistoricalPerformanceSignal,
    HistoricalPerformanceStatus,
    HistoricalRecord,
    compute_content_identity_checksum,
    compute_corpus_checksum,
    compute_historical_performance_policy_checksum,
    compute_lexical_relevance,
    compute_percentile_midranks,
)
from omega.infrastructure.models import (
    ContentSelectionDecision,
    LearningInputLatestPointer,
    LearningInputSnapshot,
    Mission,
    PublishIntent,
    TopicCandidate,
)


class LearningHistoricalPerformanceProvider:
    """Detached, in-memory historical performance evaluator built once per selection run."""

    def __init__(
        self,
        *,
        channel_id: UUID,
        policy_checksum: str,
        corpus_checksum: str,
        pinned_snapshots: list[LearningInputSnapshot],
        records: list[HistoricalRecord],
    ) -> None:
        self.channel_id = channel_id
        self.policy_checksum = policy_checksum
        self.corpus_checksum = corpus_checksum
        self.pinned_snapshots = pinned_snapshots
        self.all_records = records
        self.performance_records = [r for r in records if r.historical_item_performance is not None]

    @property
    def pinned_snapshot_count(self) -> int:
        return len(self.pinned_snapshots)

    @property
    def performance_record_count(self) -> int:
        return len(self.performance_records)

    @classmethod
    async def build(
        cls,
        session: AsyncSession,
        channel_id: UUID,
    ) -> LearningHistoricalPerformanceProvider:
        """Build a detached immutable historical performance provider for the channel."""
        policy_checksum = compute_historical_performance_policy_checksum()

        # 1. Pinned Pointer + Snapshot query
        # Join LearningInputLatestPointer to LearningInputSnapshot, ordered by publication recency
        query = (
            select(LearningInputLatestPointer, LearningInputSnapshot)
            .join(
                LearningInputSnapshot,
                LearningInputLatestPointer.current_input_snapshot_id == LearningInputSnapshot.id,
            )
            .where(
                LearningInputLatestPointer.channel_id == channel_id,
                LearningInputLatestPointer.window_type == HISTORICAL_EVALUATION_WINDOW,
                LearningInputSnapshot.channel_id == channel_id,
                LearningInputSnapshot.window_type == HISTORICAL_EVALUATION_WINDOW,
                LearningInputSnapshot.window_state.in_(HISTORICAL_ALLOWED_WINDOW_STATES),
                LearningInputSnapshot.is_fully_finalized.is_(True),
            )
            .order_by(
                LearningInputSnapshot.published_at_utc.desc(),
                LearningInputSnapshot.id.asc(),
            )
            .limit(HISTORICAL_MAX_CORPUS_SIZE)
        )

        rows = (await session.execute(query)).all()
        pinned_pointers: list[LearningInputLatestPointer] = []
        pinned_snapshots: list[LearningInputSnapshot] = []

        for pointer, snapshot in rows:
            # Amendment 2: Validate latest-pointer integrity
            if (
                pointer.current_input_snapshot_id != snapshot.id
                or pointer.observation_id != snapshot.observation_id
                or pointer.window_type != snapshot.window_type
                or pointer.channel_id != snapshot.channel_id
                or pointer.current_revision_sequence != snapshot.revision_sequence
                or pointer.current_payload_checksum != snapshot.payload_checksum
                or snapshot.channel_id != channel_id
                or snapshot.window_type != HISTORICAL_EVALUATION_WINDOW
            ):
                raise HistoricalPerformanceIntegrityError(
                    f"LearningInputLatestPointer for observation {pointer.observation_id} "
                    f"is internally inconsistent with current snapshot {snapshot.id}."
                )
            pinned_pointers.append(pointer)
            pinned_snapshots.append(snapshot)

        # 2. Batch-resolve content identities (Amendment 4 & 11)
        identities_by_snapshot_id = await cls._batch_resolve_identities(
            session=session,
            channel_id=channel_id,
            snapshots=pinned_snapshots,
        )

        # 3. Extract and filter metric values per snapshot
        raw_metrics_by_snapshot: dict[UUID, dict[str, float]] = {}
        for snapshot in pinned_snapshots:
            valid_metrics: dict[str, float] = {}
            raw_metrics = snapshot.raw_metrics or {}
            classifications = snapshot.classifications or {}
            qualities = snapshot.metric_qualities or {}

            for metric_name in HISTORICAL_ELIGIBLE_METRICS:
                classification = classifications.get(metric_name)
                quality = qualities.get(metric_name)
                if classification not in HISTORICAL_ALLOWED_CLASSIFICATIONS:
                    continue
                if quality not in HISTORICAL_ALLOWED_QUALITIES:
                    continue

                if quality == "ZERO_CONFIRMED":
                    valid_metrics[metric_name] = 0.0
                elif metric_name in raw_metrics and raw_metrics[metric_name] is not None:
                    try:
                        val = float(raw_metrics[metric_name])
                        if not math.isnan(val) and not math.isinf(val):
                            valid_metrics[metric_name] = val
                    except (ValueError, TypeError):
                        pass

            raw_metrics_by_snapshot[snapshot.id] = valid_metrics

        # 4. Normalize metrics using PERCENTILE_MIDRANK_V1 (Amendment 5)
        normalized_by_snapshot: dict[UUID, dict[str, float]] = {s.id: {} for s in pinned_snapshots}
        for metric_name in HISTORICAL_ELIGIBLE_METRICS:
            observations: list[tuple[UUID, float]] = [
                (s.id, raw_metrics_by_snapshot[s.id][metric_name])
                for s in pinned_snapshots
                if metric_name in raw_metrics_by_snapshot[s.id]
            ]
            if len(observations) >= HISTORICAL_MIN_CORPUS_SIZE:
                snapshot_ids = [obs[0] for obs in observations]
                values = [obs[1] for obs in observations]
                percentile_scores = compute_percentile_midranks(values)
                for sid, score in zip(snapshot_ids, percentile_scores, strict=True):
                    normalized_by_snapshot[sid][metric_name] = score

        # 5. Build detached HistoricalRecord instances
        records: list[HistoricalRecord] = []
        corpus_checksum_snapshots: list[dict] = []

        for snapshot in pinned_snapshots:
            identity = identities_by_snapshot_id[snapshot.id]
            norm_metrics = normalized_by_snapshot[snapshot.id]
            item_perf: float | None = None
            if norm_metrics:
                item_perf = sum(norm_metrics.values()) / len(norm_metrics)

            record = HistoricalRecord(
                snapshot_id=snapshot.id,
                observation_id=snapshot.observation_id,
                publish_intent_id=snapshot.publish_intent_id,
                published_at_utc=snapshot.published_at_utc,
                revision_sequence=snapshot.revision_sequence,
                payload_checksum=snapshot.payload_checksum,
                window_type=snapshot.window_type,
                window_state=snapshot.window_state,
                identity=identity,
                metric_values=raw_metrics_by_snapshot[snapshot.id],
                normalized_metrics=norm_metrics,
                historical_item_performance=item_perf,
            )
            records.append(record)

            corpus_checksum_snapshots.append(
                {
                    "learning_snapshot_id": snapshot.id,
                    "observation_id": snapshot.observation_id,
                    "payload_checksum": snapshot.payload_checksum,
                    "revision_sequence": snapshot.revision_sequence,
                    "window_type": snapshot.window_type,
                    "window_state": snapshot.window_state,
                    "content_identity_checksum": identity.identity_checksum,
                }
            )

        # 6. Compute corpus checksum covering all score-affecting inputs (Amendment 3)
        corpus_checksum = compute_corpus_checksum(
            snapshots=corpus_checksum_snapshots,
            policy_checksum=policy_checksum,
        )

        return cls(
            channel_id=channel_id,
            policy_checksum=policy_checksum,
            corpus_checksum=corpus_checksum,
            pinned_snapshots=pinned_snapshots,
            records=records,
        )

    @classmethod
    async def _batch_resolve_identities(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        snapshots: list[LearningInputSnapshot],
    ) -> dict[UUID, HistoricalContentIdentity]:
        """Batch load PublishIntent, Mission, Decision, and TopicCandidate to resolve identities."""
        publish_intent_ids = list(
            {s.publish_intent_id for s in snapshots if s.publish_intent_id is not None}
        )
        intents_by_id: dict[UUID, PublishIntent] = {}
        if publish_intent_ids:
            res_intents = await session.execute(
                select(PublishIntent).where(PublishIntent.id.in_(publish_intent_ids))
            )
            intents_by_id = {intent.id: intent for intent in res_intents.scalars().all()}

        mission_ids = list(
            {intent.mission_id for intent in intents_by_id.values() if intent.mission_id}
        )
        missions_by_id: dict[UUID, Mission] = {}
        if mission_ids:
            res_missions = await session.execute(
                select(Mission).where(Mission.id.in_(mission_ids))
            )
            missions_by_id = {m.id: m for m in res_missions.scalars().all()}

        # Identify candidate IDs and decision IDs from mission metadata
        decision_ids: set[UUID] = set()
        candidate_ids: set[UUID] = set()
        for mission in missions_by_id.values():
            meta = mission.metadata_ or {}
            campaign_lineage = meta.get("campaign_lineage", {})
            canonical_inputs = meta.get("canonical_inputs", {})

            # Decision ID
            dec_id_str = campaign_lineage.get("selection_decision_id") or meta.get(
                "selection_decision_id"
            )
            if dec_id_str:
                with contextlib.suppress(ValueError, TypeError):
                    decision_ids.add(UUID(str(dec_id_str)))

            # TopicCandidate ID
            tc_id_str = canonical_inputs.get("topic_candidate_id") or campaign_lineage.get(
                "topic_candidate_id"
            )
            if tc_id_str:
                with contextlib.suppress(ValueError, TypeError):
                    candidate_ids.add(UUID(str(tc_id_str)))

        decisions_by_id: dict[UUID, ContentSelectionDecision] = {}
        if decision_ids:
            res_dec = await session.execute(
                select(ContentSelectionDecision).where(
                    ContentSelectionDecision.id.in_(list(decision_ids))
                )
            )
            decisions_by_id = {d.id: d for d in res_dec.scalars().all()}

        candidates_by_id: dict[UUID, TopicCandidate] = {}
        if candidate_ids:
            res_cand = await session.execute(
                select(TopicCandidate).where(TopicCandidate.id.in_(list(candidate_ids)))
            )
            candidates_by_id = {c.id: c for c in res_cand.scalars().all()}

        # Build identities
        result: dict[UUID, HistoricalContentIdentity] = {}
        for snapshot in snapshots:
            intent = intents_by_id.get(snapshot.publish_intent_id) if snapshot.publish_intent_id else None
            mission = missions_by_id.get(intent.mission_id) if intent and intent.mission_id else None

            authority = ContentIdentityAuthority.UNRESOLVED
            title = ""
            keywords: list[str] = []
            tags: list[str] = []
            source_authority_ids: dict[str, str | None] = {
                "learning_snapshot_id": str(snapshot.id),
                "publish_intent_id": str(intent.id) if intent else None,
                "mission_id": str(mission.id) if mission else None,
            }

            # Precedence A: SELECTION_DECISION_SNAPSHOT
            if mission is not None:
                meta = mission.metadata_ or {}
                campaign_lineage = meta.get("campaign_lineage", {})
                dec_id_str = campaign_lineage.get("selection_decision_id") or meta.get(
                    "selection_decision_id"
                )
                if dec_id_str:
                    try:
                        dec_uuid = UUID(str(dec_id_str))
                        decision = decisions_by_id.get(dec_uuid)
                        if decision and decision.candidate_snapshot:
                            cand_snap = decision.candidate_snapshot
                            authority = ContentIdentityAuthority.SELECTION_DECISION_SNAPSHOT
                            title = str(cand_snap.get("title", ""))
                            keywords = list(cand_snap.get("keywords", []))
                            tags = list(cand_snap.get("tags", []))
                            source_authority_ids["selection_decision_id"] = str(decision.id)
                    except (ValueError, TypeError):
                        pass

            # Precedence B: MISSION_CANONICAL_TOPIC
            if authority == ContentIdentityAuthority.UNRESOLVED and mission is not None:
                meta = mission.metadata_ or {}
                canonical_inputs = meta.get("canonical_inputs", {})
                campaign_lineage = meta.get("campaign_lineage", {})
                tc_id_str = canonical_inputs.get("topic_candidate_id") or campaign_lineage.get(
                    "topic_candidate_id"
                )
                if tc_id_str:
                    try:
                        tc_uuid = UUID(str(tc_id_str))
                        candidate = candidates_by_id.get(tc_uuid)
                        if candidate and candidate.channel_id == channel_id:
                            authority = ContentIdentityAuthority.MISSION_CANONICAL_TOPIC
                            title = candidate.title
                            keywords = list(candidate.keywords)
                            tags = list(candidate.tags)
                            source_authority_ids["topic_candidate_id"] = str(candidate.id)
                    except (ValueError, TypeError):
                        pass

            # Precedence C: PUBLISH_INTENT_METADATA
            if authority == ContentIdentityAuthority.UNRESOLVED and intent is not None:
                authority = ContentIdentityAuthority.PUBLISH_INTENT_METADATA
                title = intent.title
                keywords = []
                tags = list(intent.tags)

            norm_title = normalize_text(title)
            norm_keywords = [normalize_text(k) for k in keywords if normalize_text(k)]
            norm_tags = [normalize_text(t) for t in tags if normalize_text(t)]

            identity_checksum = compute_content_identity_checksum(
                identity_authority=authority.value,
                source_authority_ids=source_authority_ids,
                normalized_title=norm_title,
                normalized_keywords=norm_keywords,
                normalized_tags=norm_tags,
            )

            result[snapshot.id] = HistoricalContentIdentity(
                identity_authority=authority,
                title=title,
                keywords=keywords,
                tags=tags,
                normalized_title=norm_title,
                normalized_keywords=norm_keywords,
                normalized_tags=norm_tags,
                identity_checksum=identity_checksum,
                source_authority_ids=source_authority_ids,
            )

        return result

    def evaluate(
        self,
        topic_title: str,
        keywords: list[str],
    ) -> HistoricalPerformanceSignal:
        """Pure, synchronous candidate evaluation over the detached in-memory corpus."""
        # Minimum corpus fallback (Amendment 6 & 8)
        if self.performance_record_count < HISTORICAL_MIN_CORPUS_SIZE:
            return HistoricalPerformanceSignal(
                score=HISTORICAL_NEUTRAL_SCORE,
                status=HistoricalPerformanceStatus.INSUFFICIENT_CORPUS,
                policy_name=HISTORICAL_PERFORMANCE_POLICY_NAME,
                policy_version=HISTORICAL_PERFORMANCE_POLICY_VERSION,
                policy_checksum=self.policy_checksum,
                corpus_checksum=self.corpus_checksum,
                pinned_snapshot_count=self.pinned_snapshot_count,
                performance_record_count=self.performance_record_count,
                match_count=0,
                learning_evidence_ids=[],
                analytics_evidence_ids=[],
                source_publish_intent_ids=[],
                metric_names_used=[],
                matched_evidence=[],
            )

        # Candidate relevance matching across performance-eligible records (Amendment 7 & 8)
        evaluated_matches: list[tuple[float, float, float, HistoricalRecord]] = []
        for record in self.performance_records:
            relevance, token_jaccard, keyword_coverage = compute_lexical_relevance(
                candidate_title=topic_title,
                candidate_keywords=keywords,
                historical_title=record.identity.title,
                historical_keywords=record.identity.keywords,
                historical_tags=record.identity.tags,
            )
            if relevance >= HISTORICAL_MIN_RELEVANCE:
                evaluated_matches.append((relevance, token_jaccard, keyword_coverage, record))

        # Deterministic sort: relevance DESC, published_at_utc DESC, snapshot_id ASC
        evaluated_matches.sort(
            key=lambda item: (-item[0], -item[3].published_at_utc.timestamp(), str(item[3].snapshot_id))
        )
        matched_slice = evaluated_matches[:HISTORICAL_MAX_MATCHES]

        # Minimum matched items fallback
        if len(matched_slice) < HISTORICAL_MIN_MATCHED_ITEMS:
            return HistoricalPerformanceSignal(
                score=HISTORICAL_NEUTRAL_SCORE,
                status=HistoricalPerformanceStatus.INSUFFICIENT_RELEVANT_HISTORY,
                policy_name=HISTORICAL_PERFORMANCE_POLICY_NAME,
                policy_version=HISTORICAL_PERFORMANCE_POLICY_VERSION,
                policy_checksum=self.policy_checksum,
                corpus_checksum=self.corpus_checksum,
                pinned_snapshot_count=self.pinned_snapshot_count,
                performance_record_count=self.performance_record_count,
                match_count=len(matched_slice),
                learning_evidence_ids=[],
                analytics_evidence_ids=[],
                source_publish_intent_ids=[],
                metric_names_used=[],
                matched_evidence=[],
            )

        # Weighted mean calculation: score = sum(perf * weight) / sum(weight)
        weighted_sum = sum(
            rec.historical_item_performance * rel  # type: ignore[operator]
            for rel, _, _, rec in matched_slice
        )
        total_weight = sum(rel for rel, _, _, _ in matched_slice)
        raw_score = weighted_sum / total_weight
        final_score = round(max(0.0, min(100.0, raw_score)), 1)

        matched_evidence = [
            {
                "learning_snapshot_id": str(rec.snapshot_id),
                "analytics_observation_id": str(rec.observation_id),
                "publish_intent_id": str(rec.publish_intent_id) if rec.publish_intent_id else None,
                "identity_authority": rec.identity.identity_authority.value,
                "relevance": round(rel, 4),
                "token_jaccard": round(tj, 4),
                "keyword_coverage": round(kc, 4),
                "historical_item_performance": round(rec.historical_item_performance, 1),  # type: ignore[arg-type]
                "metrics_used": sorted(list(rec.normalized_metrics.keys())),
            }
            for rel, tj, kc, rec in matched_slice
        ]

        learning_evidence_ids = [str(rec.snapshot_id) for _, _, _, rec in matched_slice]
        analytics_evidence_ids = [str(rec.observation_id) for _, _, _, rec in matched_slice]
        source_publish_intent_ids = list(
            dict.fromkeys(
                str(rec.publish_intent_id)
                for _, _, _, rec in matched_slice
                if rec.publish_intent_id is not None
            )
        )
        metric_names_used = sorted(
            list(set(m for _, _, _, rec in matched_slice for m in rec.normalized_metrics))
        )

        return HistoricalPerformanceSignal(
            score=final_score,
            status=HistoricalPerformanceStatus.APPLIED,
            policy_name=HISTORICAL_PERFORMANCE_POLICY_NAME,
            policy_version=HISTORICAL_PERFORMANCE_POLICY_VERSION,
            policy_checksum=self.policy_checksum,
            corpus_checksum=self.corpus_checksum,
            pinned_snapshot_count=self.pinned_snapshot_count,
            performance_record_count=self.performance_record_count,
            match_count=len(matched_slice),
            learning_evidence_ids=learning_evidence_ids,
            analytics_evidence_ids=analytics_evidence_ids,
            source_publish_intent_ids=source_publish_intent_ids,
            metric_names_used=metric_names_used,
            matched_evidence=matched_evidence,
        )
