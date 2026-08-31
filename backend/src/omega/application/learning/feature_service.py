"""Feature extraction service for OMEGA-013 Learning Engine."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.learning import (
    LearningEventType,
    compute_event_dedupe_key,
    compute_feature_snapshot_key,
)
from omega.infrastructure.models import (
    LearningEvent,
    LearningFeatureSnapshot,
    LearningInputSnapshot,
    LearningModelExtraction,
    MediaArtifact,
    PublishIntent,
)

FEATURE_SCHEMA_VERSION: int = 1
DETERMINISTIC_EXTRACTOR_VERSION: str = "1.0.0"


class FeatureExtractionService:
    """Extracts immutable, reproducible content features from published creative assets."""

    @classmethod
    async def extract_deterministic_features(
        cls,
        session: AsyncSession,
        input_snapshot: LearningInputSnapshot,
        extractor_version: str = DETERMINISTIC_EXTRACTOR_VERSION,
    ) -> LearningFeatureSnapshot:
        """Extract deterministic features for an input snapshot.

        Idempotent: Re-extracting with same input snapshot and extractor version returns existing snapshot.
        """
        key = compute_feature_snapshot_key(
            input_snapshot.id, FEATURE_SCHEMA_VERSION, extractor_version
        )

        stmt = select(LearningFeatureSnapshot).where(
            LearningFeatureSnapshot.feature_snapshot_key == key
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

        # Fetch underlying PublishIntent and MediaArtifact
        intent = await session.get(PublishIntent, input_snapshot.publish_intent_id)
        if intent is None:
            title = "Untitled"
            desc = ""
            tags = []
        else:
            title = intent.title
            desc = intent.description
            tags = intent.tags or []

        artifact = await session.get(MediaArtifact, input_snapshot.media_artifact_id)
        if artifact and artifact.duration_ms is not None:
            duration_seconds = float(artifact.duration_ms) / 1000.0
        else:
            duration_seconds = 60.0
        content_format = "SHORT" if duration_seconds < 60.0 else "LONG_FORM"

        features: dict[str, Any] = {
            "title_length_chars": len(title),
            "title_word_count": len(title.split()),
            "title_has_question": "?" in title,
            "title_has_number": any(ch.isdigit() for ch in title),
            "description_length": len(desc),
            "tag_count": len(tags),
            "duration_seconds": duration_seconds,
            "content_format": content_format,
            "publish_day_of_week": input_snapshot.published_at_utc.weekday(),
            "publish_hour_utc": input_snapshot.published_at_utc.hour,
            "channel_dna_revision_id": str(input_snapshot.channel_dna_revision_id)
            if input_snapshot.channel_dna_revision_id
            else None,
        }

        snapshot = LearningFeatureSnapshot(
            input_snapshot_id=input_snapshot.id,
            channel_id=input_snapshot.channel_id,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            extractor_version=extractor_version,
            deterministic_features=features,
            model_features={},
            feature_snapshot_key=key,
        )
        session.add(snapshot)
        await session.flush()

        # Log event
        event_dedupe = compute_event_dedupe_key(
            LearningEventType.FEATURE_CREATED.value, "FEATURE", snapshot.id, key
        )
        event = LearningEvent(
            event_type=LearningEventType.FEATURE_CREATED.value,
            aggregate_type="FEATURE",
            aggregate_id=snapshot.id,
            channel_id=snapshot.channel_id,
            source_ids=[str(input_snapshot.id)],
            actor="FeatureExtractionService",
            payload={"extractor_version": extractor_version, "format": content_format},
            event_dedupe_key=event_dedupe,
            occurred_at=snapshot.created_at,
        )
        session.add(event)
        await session.flush()

        return snapshot

    @classmethod
    async def attach_model_features(
        cls,
        session: AsyncSession,
        feature_snapshot: LearningFeatureSnapshot,
        model_provider: str,
        model_name: str,
        prompt_template_version: str,
        prompt_text: str,
        structured_output: dict[str, Any],
        latency_ms: int,
    ) -> LearningModelExtraction:
        """Persist structured model extraction provenance and attach to feature snapshot.

        No conversational chain-of-thought is persisted.
        """
        prompt_chk = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        resp_chk = hashlib.sha256(
            json.dumps(structured_output, sort_keys=True).encode("utf-8")
        ).hexdigest()

        extraction = LearningModelExtraction(
            feature_snapshot_id=feature_snapshot.id,
            model_provider=model_provider,
            model_name=model_name,
            prompt_template_version=prompt_template_version,
            prompt_checksum=prompt_chk,
            response_checksum=resp_chk,
            structured_output=structured_output,
            latency_ms=latency_ms,
        )
        session.add(extraction)

        # Update model features dictionary
        merged = dict(feature_snapshot.model_features or {})
        merged.update(structured_output)
        feature_snapshot.model_features = merged
        await session.flush()

        return extraction
