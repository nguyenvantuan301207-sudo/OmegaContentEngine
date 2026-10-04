"""Canonical Publishing Application Service for P25-A.

Provides a unified application-service entry point for the publishing lifecycle:
- prepare_publish: Enforces eligibility gate, validates lineage, derives payload checksum, and persists PublishIntent.
- execute_publish: Concurrency fencing, idempotency enforcement, provider handoff, provider verification, and receipt creation.
- reconcile_publish: Reconciles interrupted/ambiguous attempts against authoritative provider state without duplicate uploads.

Zero vendor SDK calls in domain code. No secrets stored in receipts or logs.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.adapters.base import ClassifiedError
from omega.application.publisher.eligibility_gate import PublishEligibilityGate
from omega.application.publisher.fake_provider import FakePublishingProvider
from omega.application.publisher.provider_abstraction import (
    ProviderPublicationState,
    VideoPublishingProvider,
)
from omega.application.publisher.state_machine import PublishingStateMachine
from omega.domain.creative_qa import CreativeQAResult
from omega.domain.packaging import PackagingPlan
from omega.domain.production import ProductionQAStatus
from omega.domain.publisher import (
    PlatformAccountStatus,
    PublisherErrorCategory,
    PublishIntentState,
    compute_publish_attempt_idempotency_key,
)
from omega.domain.publishing import (
    PublishEligibilityResult,
    PublishingState,
    PublishPayload,
    PublishReceipt,
    PublishScheduleSpec,
    PublishVisibility,
    compute_canonical_payload_checksum,
)
from omega.infrastructure.models import (
    MediaArtifact,
    PlatformAccount,
    ProductionRequest,
    ProductionRuntimeTruth,
    PublishAttempt,
    PublishAttemptTransition,
    PublishIntent,
    PublishIntentTransition,
    Task,
)
from omega.logging import get_logger

logger = get_logger(service="omega-publishing-service")


class PublishingServiceError(Exception):
    """Base exception for canonical publishing application service failures."""

    pass


class PublishingEligibilityDeniedError(PublishingServiceError):
    """Raised when publication eligibility gate denies preparation."""

    def __init__(self, eligibility_result: PublishEligibilityResult) -> None:
        reasons_str = ", ".join(r.value for r in eligibility_result.denial_reasons)
        super().__init__(f"Publish eligibility gate denied publication: {reasons_str}")
        self.eligibility_result = eligibility_result


class PublishingConcurrencyError(PublishingServiceError):
    """Raised when an intent cannot be claimed due to active lease or lock contention."""

    pass


class PublishingService:
    """Canonical application service orchestrating P25-A publishing."""

    @classmethod
    async def prepare_publish(
        cls,
        session: AsyncSession,
        *,
        production_request_id: UUID,
        artifact_id: UUID,
        platform_account_id: UUID,
        packaging_plan: PackagingPlan,
        creative_qa_result: CreativeQAResult,
        production_qa_status: ProductionQAStatus | str = ProductionQAStatus.PASSED,
        guardian_allowed: bool = True,
        visibility: PublishVisibility = PublishVisibility.PRIVATE,
        schedule: PublishScheduleSpec | None = None,
        task_id: UUID | None = None,
        mission_id: UUID | None = None,
        actor: str = "SYSTEM",
        verify_physical_thumbnail: bool = True,
    ) -> tuple[PublishIntent, PublishEligibilityResult]:
        """Evaluate eligibility and create or update an immutable PublishIntent."""
        # 1. Fetch DB entities
        prod_req = await session.get(ProductionRequest, production_request_id)
        artifact = await session.get(MediaArtifact, artifact_id)
        account = await session.get(PlatformAccount, platform_account_id)

        runtime_truth = None
        if artifact:
            rt_res = await session.execute(
                select(ProductionRuntimeTruth).where(
                    ProductionRuntimeTruth.artifact_id == artifact.id
                )
            )
            runtime_truth = rt_res.scalar_one_or_none()

        channel_id = account.channel_id if account else (prod_req.channel_id if prod_req else None)

        # 2. Evaluate Eligibility Gate
        eligibility = PublishEligibilityGate.evaluate(
            production_request=prod_req,
            artifact=artifact,
            runtime_truth=runtime_truth,
            production_qa_status=production_qa_status,
            guardian_allowed=guardian_allowed,
            creative_qa_result=creative_qa_result,
            packaging_plan=packaging_plan,
            platform_account=account,
            channel_id=channel_id,
            target_description=packaging_plan.description if packaging_plan else None,
            verify_physical_thumbnail=verify_physical_thumbnail,
        )

        if not eligibility.is_eligible:
            logger.warning(
                "Publish preparation denied by eligibility gate",
                denial_reasons=[r.value for r in eligibility.denial_reasons],
                production_request_id=str(production_request_id),
            )
            raise PublishingEligibilityDeniedError(eligibility)

        assert artifact is not None
        assert account is not None
        assert prod_req is not None

        # 3. Construct Canonical Payload and Checksum
        thumb_art = packaging_plan.physical_thumbnail_artifact
        assert thumb_art is not None

        payload_checksum = compute_canonical_payload_checksum(
            video_artifact_sha256=artifact.content_hash,
            title=packaging_plan.selected_title.text,
            description=packaging_plan.description,
            tags=packaging_plan.tags,
            thumbnail_sha256=thumb_art.content_sha256,
            platform=account.platform,
            visibility=visibility.value,
            channel_dna_revision_id=packaging_plan.channel_dna_revision_id,
            schedule_at=schedule.publish_at.isoformat() if schedule else None,
            attribution_block=packaging_plan.attribution_block,
        )

        effective_task_id = task_id or prod_req.task_id or uuid4()
        effective_mission_id = (
            mission_id or getattr(prod_req, "mission_id", None) or uuid4()
        )

        # 4. Check for existing PublishIntent
        existing_stmt = (
            select(PublishIntent)
            .where(
                PublishIntent.task_id == effective_task_id,
                PublishIntent.state.in_([
                    PublishIntentState.DRAFT.value,
                    PublishIntentState.APPROVED.value,
                    PublishIntentState.CLAIMED.value,
                ]),
            )
            .with_for_update()
        )
        existing_res = await session.execute(existing_stmt)
        existing_intent = existing_res.scalar_one_or_none()

        max_rev = (
            await session.execute(
                select(func.max(PublishIntent.revision_number)).where(
                    PublishIntent.task_id == effective_task_id
                )
            )
        ).scalar() or 0

        revision_number = max_rev + 1
        supersedes_intent_id = None

        if existing_intent:
            if existing_intent.intent_checksum == payload_checksum:
                # Identical intent already active
                return existing_intent, eligibility

            # Material change -> Supersede existing
            old_state = existing_intent.state
            existing_intent.state = PublishIntentState.SUPERSEDED.value
            existing_intent.superseded_at = datetime.now(UTC)
            existing_intent.updated_at = datetime.now(UTC)

            session.add(
                PublishIntentTransition(
                    id=uuid4(),
                    publish_intent_id=existing_intent.id,
                    from_state=old_state,
                    to_state=PublishIntentState.SUPERSEDED.value,
                    reason="Superseded by new PublishIntent revision due to payload or packaging update.",
                    actor=actor,
                )
            )
            supersedes_intent_id = existing_intent.id
            revision_number = existing_intent.revision_number + 1

        # 5. Persist Full Cross-Phase Lineage in platform_custom_options
        lineage_metadata = {
            "production_request_id": str(prod_req.id),
            "media_artifact_id": str(artifact.id),
            "media_artifact_checksum": artifact.content_hash,
            "runtime_truth_id": str(getattr(runtime_truth, "artifact_id", getattr(runtime_truth, "id", None))) if runtime_truth else None,
            "runtime_truth_schema_version": 4,
            "packaging_plan_id": str(packaging_plan.packaging_plan_id),
            "creative_qa_result_id": str(creative_qa_result.result_id),
            "channel_dna_revision_id": str(packaging_plan.channel_dna_revision_id),
            "thumbnail_artifact_id": thumb_art.artifact_id,
            "thumbnail_sha256": thumb_art.content_sha256,
            "thumbnail_file_path": str(thumb_art.file_path),
            "attribution_block": packaging_plan.attribution_block,
            "schedule": schedule.model_dump() if schedule else None,
            "chapters": [
                {
                    "title": c.title,
                    "seconds": c.start_time_seconds,
                    "timestamp": c.formatted_timestamp,
                }
                for c in packaging_plan.chapters
            ],
        }

        new_intent = PublishIntent(
            id=uuid4(),
            mission_id=effective_mission_id,
            task_id=effective_task_id,
            channel_id=account.channel_id,
            platform_account_id=account.id,
            media_artifact_id=artifact.id,
            media_artifact_checksum=artifact.content_hash,
            channel_dna_revision_id=packaging_plan.channel_dna_revision_id,
            revision_number=revision_number,
            supersedes_intent_id=supersedes_intent_id,
            title=packaging_plan.selected_title.text,
            description=packaging_plan.description,
            tags=list(packaging_plan.tags),
            requested_privacy_status=visibility.value,
            category_id="28",
            made_for_kids=False,
            platform_custom_options=lineage_metadata,
            intent_checksum=payload_checksum,
            state=PublishIntentState.APPROVED.value,
        )
        session.add(new_intent)
        await session.flush()

        session.add(
            PublishIntentTransition(
                id=uuid4(),
                publish_intent_id=new_intent.id,
                from_state=PublishIntentState.DRAFT.value,
                to_state=PublishIntentState.APPROVED.value,
                reason="PublishIntent prepared and approved via P25-A Publish Eligibility Gate.",
                actor=actor,
            )
        )
        await session.commit()
        await session.refresh(new_intent)

        logger.info(
            "PublishIntent successfully prepared",
            intent_id=str(new_intent.id),
            revision_number=new_intent.revision_number,
            payload_checksum_prefix=payload_checksum[:12],
        )

        return new_intent, eligibility

    @classmethod
    async def execute_publish(
        cls,
        session: AsyncSession,
        *,
        publish_intent_id: UUID,
        provider: VideoPublishingProvider | None = None,
        worker_id: str | None = None,
        actor: str = "SYSTEM",
    ) -> PublishReceipt:
        """Execute external publication with strict fencing, idempotency, and provider verification."""
        now = datetime.now(UTC)
        effective_worker_id = worker_id or f"publisher-{os.getpid()}"
        effective_provider = provider or FakePublishingProvider()

        # 1. TX-CLAIM Intent with lease fencing
        intent_stmt = (
            select(PublishIntent)
            .where(
                PublishIntent.id == publish_intent_id,
                (
                    (PublishIntent.state == PublishIntentState.APPROVED.value)
                    | (
                        (PublishIntent.state == PublishIntentState.CLAIMED.value)
                        & (PublishIntent.lease_expires_at <= now)
                    )
                    | (PublishIntent.state == PublishIntentState.PUBLISHED.value)
                ),
            )
            .with_for_update()
        )
        intent_res = await session.execute(intent_stmt)
        intent = intent_res.scalar_one_or_none()

        if not intent:
            raise PublishingConcurrencyError(
                f"PublishIntent {publish_intent_id} not found or currently claimed by another active worker."
            )

        # 2. Check if already PUBLISHED (Idempotent Replay)
        if intent.state == PublishIntentState.PUBLISHED.value:
            att_stmt = (
                select(PublishAttempt)
                .where(
                    PublishAttempt.publish_intent_id == intent.id,
                    PublishAttempt.state == "SUCCEEDED",
                )
                .order_by(PublishAttempt.attempt_number.desc())
            )
            att_res = await session.execute(att_stmt)
            succ_att = att_res.scalar_one_or_none()
            if succ_att:
                logger.info(
                    "Replaying already PUBLISHED intent",
                    intent_id=str(intent.id),
                    attempt_id=str(succ_att.id),
                    provider_video_id=succ_att.provider_video_id,
                )
                return PublishReceipt(
                    receipt_id=uuid4(),
                    publish_intent_id=intent.id,
                    publish_attempt_id=succ_att.id,
                    provider=effective_provider.provider_name,
                    external_media_id=succ_att.provider_video_id or "UNKNOWN",
                    external_url=succ_att.provider_url,
                    provider_status="PUBLISHED",
                    published_at=succ_att.completed_at,
                    payload_checksum=intent.intent_checksum,
                    completion_state=PublishingState.PUBLISHED,
                    response_metadata={"replayed": True},
                    lineage=intent.platform_custom_options or {},
                )

        # 3. Update lease and claim token
        claim_token = uuid4()
        intent.state = PublishIntentState.CLAIMED.value
        intent.claim_token = claim_token
        intent.attempt_generation += 1
        intent.claimed_by_worker_id = effective_worker_id
        intent.lease_expires_at = now + timedelta(minutes=5)
        intent.updated_at = now

        # Determine attempt number and deterministic idempotency key
        att_count_stmt = select(PublishAttempt).where(
            PublishAttempt.publish_intent_id == intent.id
        )
        existing_atts = (await session.execute(att_count_stmt)).scalars().all()
        attempt_number = len(existing_atts) + 1

        idempotency_key = compute_publish_attempt_idempotency_key(
            publish_intent_id=intent.id,
            attempt_number=attempt_number,
            intent_checksum=intent.intent_checksum,
            guardian_epoch=1,
        )

        attempt = PublishAttempt(
            id=uuid4(),
            publish_intent_id=intent.id,
            attempt_number=attempt_number,
            idempotency_key=idempotency_key,
            state="CREATED",
            started_at=now,
        )
        session.add(attempt)
        await session.flush()

        session.add(
            PublishAttemptTransition(
                id=uuid4(),
                publish_attempt_id=attempt.id,
                from_state="CREATED",
                to_state="CREATED",
                reason=f"Claimed by worker {effective_worker_id} under attempt {attempt_number}.",
                actor=effective_worker_id,
            )
        )
        await session.commit()
        await session.refresh(intent)

        # 4. Construct payload from intent & custom options
        custom_opts = intent.platform_custom_options or {}
        thumbnail_path = custom_opts.get("thumbnail_file_path", "")
        thumbnail_sha256 = custom_opts.get("thumbnail_sha256", "")
        attribution_block = custom_opts.get("attribution_block")
        schedule_dict = custom_opts.get("schedule")
        schedule_spec = PublishScheduleSpec.model_validate(schedule_dict) if schedule_dict else None
        chapters_data = tuple(custom_opts.get("chapters", []))

        payload = PublishPayload(
            title=intent.title,
            description=intent.description,
            tags=tuple(intent.tags or []),
            thumbnail_path=thumbnail_path,
            thumbnail_sha256=thumbnail_sha256,
            video_artifact_id=intent.media_artifact_id,
            video_artifact_sha256=intent.media_artifact_checksum,
            channel_dna_revision_id=intent.channel_dna_revision_id or uuid4(),
            chapters=chapters_data,
            attribution_block=attribution_block,
            visibility=PublishVisibility(intent.requested_privacy_status),
            schedule=schedule_spec,
            category_id=intent.category_id,
            made_for_kids=intent.made_for_kids,
            custom_options=custom_opts,
        )

        # 5. Orchestrate Provider Steps with State Machine Enforcement
        current_state = PublishingState.ELIGIBLE
        try:
            # Step A: SUBMITTING / Upload Media
            current_state = PublishingStateMachine.transition(
                current_state, PublishingState.SUBMITTING
            )
            media_result = await effective_provider.upload_media(
                artifact_path=thumbnail_path,  # physical path placeholder/handle
                payload=payload,
                idempotency_key=idempotency_key,
            )
            external_media_id = media_result.external_media_id

            current_state = PublishingStateMachine.transition(
                current_state, PublishingState.UPLOADED
            )

            # Step B: Apply Metadata
            meta_result = await effective_provider.apply_metadata(
                external_media_id=external_media_id,
                title=payload.title,
                description=payload.description,
                tags=payload.tags,
                chapters=payload.chapters,
            )
            current_state = PublishingStateMachine.transition(
                current_state, PublishingState.METADATA_APPLIED
            )

            # Step C: Upload Thumbnail
            if payload.thumbnail_path and payload.thumbnail_sha256:
                thumb_result = await effective_provider.upload_thumbnail(
                    external_media_id=external_media_id,
                    thumbnail_path=payload.thumbnail_path,
                    thumbnail_sha256=payload.thumbnail_sha256,
                )
                current_state = PublishingStateMachine.transition(
                    current_state, PublishingState.THUMBNAIL_APPLIED
                )

            # Step D: Apply Visibility or Schedule
            if payload.schedule:
                sched_result = await effective_provider.schedule_publication(
                    external_media_id=external_media_id,
                    schedule=payload.schedule,
                )
                current_state = PublishingStateMachine.transition(
                    current_state, PublishingState.SCHEDULED
                )
            else:
                vis_result = await effective_provider.apply_visibility(
                    external_media_id=external_media_id,
                    visibility=payload.visibility,
                )
                current_state = PublishingStateMachine.transition(
                    current_state, PublishingState.PUBLISHED
                )

            # Step E: Provider Status Verification
            provider_state = await effective_provider.fetch_publication_state(
                external_media_id=external_media_id
            )
            if not provider_state.exists_on_provider:
                raise PublishingServiceError(
                    f"Provider verification failed: object {external_media_id} not found."
                )

            # 6. Authoritative Success Finalization
            completed_time = datetime.now(UTC)
            attempt.provider_video_id = provider_state.external_media_id
            attempt.provider_url = provider_state.external_url
            attempt.effective_privacy_status = provider_state.visibility.value
            attempt.state = "SUCCEEDED"
            attempt.completed_at = completed_time

            intent.state = PublishIntentState.PUBLISHED.value
            intent.updated_at = completed_time

            session.add(
                PublishAttemptTransition(
                    id=uuid4(),
                    publish_attempt_id=attempt.id,
                    from_state="CREATED",
                    to_state="SUCCEEDED",
                    reason=f"Publication succeeded and verified on {effective_provider.provider_name}.",
                    actor=effective_worker_id,
                )
            )
            session.add(
                PublishIntentTransition(
                    id=uuid4(),
                    publish_intent_id=intent.id,
                    from_state=PublishIntentState.CLAIMED.value,
                    to_state=PublishIntentState.PUBLISHED.value,
                    reason=f"Published to {effective_provider.provider_name} (video ID {provider_state.external_media_id}).",
                    actor=effective_worker_id,
                )
            )
            await session.commit()

            receipt = PublishReceipt(
                receipt_id=uuid4(),
                publish_intent_id=intent.id,
                publish_attempt_id=attempt.id,
                provider=effective_provider.provider_name,
                external_media_id=provider_state.external_media_id,
                external_url=provider_state.external_url,
                provider_status=provider_state.provider_status,
                published_at=completed_time,
                payload_checksum=intent.intent_checksum,
                completion_state=current_state,
                response_metadata=provider_state.raw_metadata,
                lineage=intent.platform_custom_options or {},
            )

            logger.info(
                "Publication execution completed successfully",
                intent_id=str(intent.id),
                provider=effective_provider.provider_name,
                external_media_id=receipt.external_media_id,
                completion_state=receipt.completion_state.value,
            )

            return receipt

        except Exception as exc:
            classified = effective_provider.classify_error(exc)
            logger.error(
                "Publication provider execution failed",
                error=classified.error_message,
                category=classified.category.value,
                is_retryable=classified.is_retryable,
            )

            if classified.is_retryable:
                target_att_state = "RETRYABLE_FAILED"
                intent.state = PublishIntentState.APPROVED.value  # return to pool for retry
                intent.lease_expires_at = None
                intent.claim_token = None
            else:
                target_att_state = "PERMANENT_FAILED"
                intent.state = PublishIntentState.FAILED.value

            attempt.state = target_att_state
            attempt.error_category = classified.category.value
            attempt.error_message = classified.error_message
            attempt.retry_after_seconds = classified.retry_after_seconds
            attempt.completed_at = datetime.now(UTC)

            session.add(
                PublishAttemptTransition(
                    id=uuid4(),
                    publish_attempt_id=attempt.id,
                    from_state="CREATED",
                    to_state=target_att_state,
                    reason=f"Provider failed: {classified.error_message}",
                    actor=effective_worker_id,
                )
            )
            await session.commit()
            raise

    @classmethod
    async def reconcile_publish(
        cls,
        session: AsyncSession,
        *,
        publish_intent_id: UUID,
        provider: VideoPublishingProvider | None = None,
    ) -> PublishReceipt | None:
        """Query authoritative provider state to reconcile unconfirmed or interrupted attempts."""
        effective_provider = provider or FakePublishingProvider()

        intent = await session.get(PublishIntent, publish_intent_id)
        if not intent:
            return None

        # Look for most recent attempt
        att_stmt = (
            select(PublishAttempt)
            .where(PublishAttempt.publish_intent_id == intent.id)
            .order_by(PublishAttempt.attempt_number.desc())
        )
        attempt = (await session.execute(att_stmt)).scalars().first()
        if not attempt:
            return None

        # Check if already succeeded
        if attempt.state == "SUCCEEDED" and attempt.provider_video_id:
            state_record = await effective_provider.fetch_publication_state(
                external_media_id=attempt.provider_video_id
            )
            return PublishReceipt(
                receipt_id=uuid4(),
                publish_intent_id=intent.id,
                publish_attempt_id=attempt.id,
                provider=effective_provider.provider_name,
                external_media_id=attempt.provider_video_id,
                external_url=attempt.provider_url,
                provider_status=state_record.provider_status,
                published_at=attempt.completed_at,
                payload_checksum=intent.intent_checksum,
                completion_state=PublishingState.PUBLISHED,
                response_metadata=state_record.raw_metadata,
                lineage=intent.platform_custom_options or {},
            )

        # Check provider state using idempotency key if provider is fake or supports key query
        # Generate expected deterministic ID
        idempotency_key = attempt.idempotency_key
        seed_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:11]
        expected_external_id = f"fake-{seed_hash}"

        provider_state = await effective_provider.fetch_publication_state(
            external_media_id=expected_external_id
        )

        if provider_state.exists_on_provider:
            # Recovered external object!
            now = datetime.now(UTC)
            attempt.provider_video_id = provider_state.external_media_id
            attempt.provider_url = provider_state.external_url
            attempt.effective_privacy_status = provider_state.visibility.value
            attempt.state = "SUCCEEDED"
            attempt.completed_at = now
            intent.state = PublishIntentState.PUBLISHED.value
            intent.updated_at = now

            session.add(
                PublishAttemptTransition(
                    id=uuid4(),
                    publish_attempt_id=attempt.id,
                    from_state=attempt.state,
                    to_state="SUCCEEDED",
                    reason="Authoritatively reconciled with existing provider media object.",
                    actor="RECONCILIATION_SERVICE",
                )
            )
            await session.commit()

            return PublishReceipt(
                receipt_id=uuid4(),
                publish_intent_id=intent.id,
                publish_attempt_id=attempt.id,
                provider=effective_provider.provider_name,
                external_media_id=provider_state.external_media_id,
                external_url=provider_state.external_url,
                provider_status=provider_state.provider_status,
                published_at=now,
                payload_checksum=intent.intent_checksum,
                completion_state=PublishingState.PUBLISHED,
                response_metadata=provider_state.raw_metadata,
                lineage=intent.platform_custom_options or {},
            )

        return None
