"""Celery worker tasks.

Uses SYNCHRONOUS SQLAlchemy (psycopg2) for database access.
Never uses async/asyncio inside Celery tasks.

See docs/decisions/001-foundation-architecture.md for rationale.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from omega.infrastructure.celery_app import celery_app
from omega.logging import get_logger

logger = get_logger(service="omega-worker")


def _sanitize_task_error(error: Exception) -> str:
    """Return a sanitized error message safe for database persistence.

    Never persists raw tracebacks or secret-containing exception details.
    """
    error_type = type(error).__name__
    message = str(error).split("\n")[0]
    for pattern in ["://", "password", "secret", "token", "key=", "apikey"]:
        if pattern.lower() in message.lower():
            return f"{error_type}: Task execution error (details in logs)"
    if len(message) > 500:
        message = message[:500] + "..."
    return f"{error_type}: {message}"


async def _record_render_bootstrap_failure(
    session: Any,
    request_id: str,
    job_id: str,
    sanitized_error: str,
) -> bool:
    """Fail one exactly-correlated render job and durably wake Mission evaluation."""
    from sqlalchemy import select

    from omega.application.durable_dispatch import DurableDispatchService
    from omega.domain.production import RenderErrorCode, RenderJobState
    from omega.infrastructure.models import MissionExecution, ProductionRenderJob, ProductionRequest

    try:
        parsed_request_id = uuid.UUID(str(request_id))
        parsed_job_id = uuid.UUID(str(job_id))
    except (TypeError, ValueError, AttributeError):
        await session.rollback()
        return False

    job = (
        await session.execute(
            select(ProductionRenderJob)
            .where(
                ProductionRenderJob.id == parsed_job_id,
                ProductionRenderJob.production_request_id == parsed_request_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        await session.rollback()
        return False
    if job.state == RenderJobState.FAILED.value:
        await session.rollback()
        return True
    if job.state not in (
        RenderJobState.PENDING.value,
        RenderJobState.QUEUED.value,
        RenderJobState.RETRY.value,
    ):
        await session.rollback()
        return False

    request = await session.get(ProductionRequest, parsed_request_id)
    if request is None or request.id != job.production_request_id:
        await session.rollback()
        return False

    job.state = RenderJobState.FAILED.value
    job.error_code = RenderErrorCode.UNKNOWN.value
    job.sanitized_error = sanitized_error[:1000]
    job.completed_at = datetime.now(UTC)

    execution_id = request.mission_execution_id
    if execution_id is not None:
        mission_id = (
            await session.execute(
                select(MissionExecution.mission_id).where(MissionExecution.id == execution_id)
            )
        ).scalar_one_or_none()
        if mission_id is not None:
            await DurableDispatchService.enqueue_async(
                session,
                idempotency_key=(
                    f"render-terminal-evaluation:{parsed_request_id}:{parsed_job_id}"
                ),
                task_name="omega.orchestrator.evaluate",
                args=[str(mission_id), str(execution_id)],
                purpose="RENDER_TERMINAL_EVALUATION",
                mission_id=mission_id,
                mission_execution_id=execution_id,
                production_request_id=parsed_request_id,
                render_job_id=parsed_job_id,
            )
    await session.commit()
    return True


def _load_dependency_outputs(session: Any, task: Any) -> dict[str, dict]:
    """Load and validate canonical outputs from a task's direct dependencies."""
    from omega.domain.task import TaskState
    from omega.infrastructure.models import Task, TaskDependency

    dependency_rows = (
        session.query(TaskDependency, Task)
        .outerjoin(Task, Task.id == TaskDependency.depends_on_task_id)
        .filter(TaskDependency.task_id == task.id)
        .order_by(Task.task_type, Task.id)
        .all()
    )

    dependency_outputs: dict[str, dict] = {}
    for dependency, upstream_task in dependency_rows:
        if upstream_task is None:
            raise RuntimeError("Direct dependency references a missing upstream task")
        if dependency.mission_id != task.mission_id or upstream_task.mission_id != task.mission_id:
            raise RuntimeError("Direct dependency belongs to a different mission")
        if upstream_task.execution_id != task.execution_id:
            raise RuntimeError("Direct dependency belongs to a different mission execution")
        if upstream_task.state != TaskState.SUCCEEDED.value:
            raise RuntimeError("Direct dependency is not SUCCEEDED")
        if not isinstance(upstream_task.task_type, str) or not upstream_task.task_type.strip():
            raise RuntimeError("Direct dependency has no deterministic task_type identity")
        if not isinstance(upstream_task.output, dict):
            raise RuntimeError("Direct dependency output is malformed")
        if upstream_task.task_type in dependency_outputs:
            raise RuntimeError("Duplicate direct dependency task_type is ambiguous")

        dependency_outputs[upstream_task.task_type] = upstream_task.output

    return dependency_outputs


class _PendingPublishResult(dict):
    """Marker for a publish task whose provider lifecycle is now durably dispatched."""


class _PendingProductionResult(dict):
    """Correlation payload plus private, process-local render dispatch instructions."""

    def __init__(
        self,
        correlation: dict[str, str] | None = None,
        *,
        dispatch_required: bool = False,
        channel_id: str | None = None,
        production_request_id: str | None = None,
        render_job_id: str | None = None,
        **correlation_ids: str,
    ) -> None:
        super().__init__(correlation or correlation_ids)
        self.dispatch_required = dispatch_required
        self.channel_id = channel_id
        self.production_request_id = production_request_id
        self.render_job_id = render_job_id


def _canonical_production_pair(context: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    dependency_outputs = context.get("dependency_outputs")
    if not isinstance(dependency_outputs, dict):
        raise ValueError("dependency_outputs must be an object")
    content_output = dependency_outputs.get("content_generation")
    if not isinstance(content_output, dict):
        raise ValueError("content_generation dependency output is required")
    if "content_request_id" not in content_output or "script_version_id" not in content_output:
        raise ValueError("content_generation dependency must supply both canonical correlation IDs")
    try:
        return (
            uuid.UUID(str(content_output["content_request_id"])),
            uuid.UUID(str(content_output["script_version_id"])),
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("content_generation dependency contains malformed UUID correlation") from exc


def _execute_canonical_production(
    task_id: uuid.UUID, context: dict[str, Any]
) -> dict[str, str]:
    """Create/observe canonical production and dispatch only newly allocated render work."""
    content_request_id, script_version_id = _canonical_production_pair(context)
    try:
        mission_id = uuid.UUID(str(context["mission_id"]))
        execution_id = uuid.UUID(str(context["execution_id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("canonical production requires valid mission and execution IDs") from exc

    async def run() -> dict[str, str]:
        from omega.application.production_service import ProductionService
        from omega.domain.content import (
            ContentOutcome,
            ContentRequestStatus,
            ScriptQAStatus,
        )
        from omega.domain.production import (
            MediaArtifactType,
            ProductionMode,
            ProductionRequestCreate,
            ProductionRequestStatus,
            RenderJobState,
        )
        from omega.infrastructure.database import AsyncWorkerSessionLocal
        from omega.infrastructure.models import (
            ContentGenerationRequest,
            MediaArtifact,
            Mission,
            MissionExecution,
            ProductionRenderJob,
            ProductionRequest,
            ScriptVersion,
            Task,
        )

        async with AsyncWorkerSessionLocal() as async_session:
            task = await async_session.get(Task, task_id)
            execution = await async_session.get(MissionExecution, execution_id)
            mission = await async_session.get(Mission, mission_id)
            content_request = await async_session.get(ContentGenerationRequest, content_request_id)
            script = await async_session.get(ScriptVersion, script_version_id)
            if task is None or task.execution_id != execution_id or task.mission_id != mission_id:
                raise ValueError("production Task MissionExecution lineage is invalid")
            if execution is None or execution.mission_id != mission_id:
                raise ValueError("production MissionExecution lineage is invalid")
            if mission is None or mission.channel_id is None:
                raise ValueError("production Mission channel lineage is invalid")
            if content_request is None:
                raise ValueError("ContentGenerationRequest not found")
            if script is None or script.content_request_id != content_request.id:
                raise ValueError("ScriptVersion does not belong to ContentGenerationRequest")
            if content_request.mission_execution_id != execution_id:
                raise ValueError("ContentGenerationRequest MissionExecution lineage is invalid")
            if content_request.channel_id != mission.channel_id:
                raise ValueError("ContentGenerationRequest channel lineage is invalid")
            if (
                execution.channel_dna_revision_id is None
                or content_request.channel_dna_revision_id != execution.channel_dna_revision_id
            ):
                raise ValueError("ContentGenerationRequest pinned DNA lineage is invalid")
            if (
                content_request.status != ContentRequestStatus.SUCCEEDED.value
                or content_request.outcome != ContentOutcome.GENERATED.value
                or not script.is_current
                or script.qa_status
                not in (
                    ScriptQAStatus.PASSED.value,
                    ScriptQAStatus.PASSED_WITH_WARNINGS.value,
                )
            ):
                raise ValueError("ScriptVersion is not accepted under content request semantics")

            request_key = hashlib.sha256(
                f"mission-production:{execution_id}:{task_id}".encode()
            ).hexdigest()
            service = ProductionService()
            request = await service.create_production_request(
                async_session,
                mission.channel_id,
                ProductionRequestCreate(
                    script_version_id=script_version_id,
                    mission_execution_id=execution_id,
                ),
                idempotency_key=request_key,
            )
            persisted = await async_session.get(ProductionRequest, request.id)
            if (
                persisted is None
                or persisted.mission_execution_id != execution_id
                or persisted.channel_id != mission.channel_id
                or persisted.script_version_id != script_version_id
                or persisted.content_request_id != content_request_id
                or persisted.channel_dna_revision_id != execution.channel_dna_revision_id
                or persisted.mode != ProductionMode.MISSION_EXECUTION.value
            ):
                raise ValueError("ProductionRequest canonical lineage is invalid")

            if persisted.status == ProductionRequestStatus.DRAFT.value:
                persisted = await service.prepare_production(
                    async_session, mission.channel_id, persisted.id
                )

            render_key = hashlib.sha256(
                f"mission-render:{persisted.id}:{task_id}".encode()
            ).hexdigest()
            if persisted.status == ProductionRequestStatus.READY.value:
                job, _plan, is_new = await service.allocate_render_job(
                    async_session, mission.channel_id, persisted.id, render_key
                )
            elif persisted.status in (
                ProductionRequestStatus.RUNNING.value,
                ProductionRequestStatus.SUCCEEDED.value,
                ProductionRequestStatus.FAILED.value,
                ProductionRequestStatus.CANCELLED.value,
            ):
                from sqlalchemy import select

                job = (
                    await async_session.execute(
                        select(ProductionRenderJob).where(
                            ProductionRenderJob.production_request_id == persisted.id,
                            ProductionRenderJob.idempotency_key == render_key,
                        )
                    )
                ).scalar_one_or_none()
                if job is None:
                    raise ValueError(
                        "non-READY ProductionRequest has no deterministic RenderJob"
                    )
                is_new = False
            else:
                raise ValueError(f"unsupported ProductionRequest status: {persisted.status}")
            output = {
                "production_request_id": str(persisted.id),
                "render_job_id": str(job.id),
            }
            if job.state in (
                RenderJobState.PENDING.value,
                RenderJobState.QUEUED.value,
                RenderJobState.RUNNING.value,
                RenderJobState.RETRY.value,
            ):
                return _PendingProductionResult(
                    output,
                    dispatch_required=is_new,
                    channel_id=str(mission.channel_id),
                    production_request_id=str(persisted.id),
                    render_job_id=str(job.id),
                )
            if job.state == RenderJobState.SUCCEEDED.value:
                from sqlalchemy import select

                artifact = (
                    await async_session.execute(
                        select(MediaArtifact).where(MediaArtifact.render_job_id == job.id)
                    )
                ).scalar_one_or_none()
                if (
                    artifact is None
                    or artifact.production_request_id != persisted.id
                    or artifact.render_job_id != job.id
                    or artifact.artifact_type != MediaArtifactType.VIDEO.value
                ):
                    raise ValueError("successful render job has no valid MediaArtifact")
                output["media_artifact_id"] = str(artifact.id)
                return output
            if job.state in (RenderJobState.FAILED.value, RenderJobState.CANCELLED.value):
                raise RuntimeError(f"render job terminal failure: {job.state}")
            raise ValueError(f"unsupported render job state: {job.state}")

    return asyncio.run(run())


def _canonical_qa_correlation(
    context: dict[str, Any],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Parse the exact canonical production dependency for Mission QA."""
    dependency_outputs = context.get("dependency_outputs")
    if not isinstance(dependency_outputs, dict):
        raise ValueError("dependency_outputs must be an object")
    production_output = dependency_outputs.get("production")
    if not isinstance(production_output, dict):
        raise ValueError("production dependency output is required")
    required = ("production_request_id", "render_job_id", "media_artifact_id")
    if any(key not in production_output for key in required):
        raise ValueError("production dependency must supply all canonical correlation IDs")
    try:
        return tuple(uuid.UUID(str(production_output[key])) for key in required)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("production dependency contains malformed UUID correlation") from exc


def _execute_canonical_qa(task_id: uuid.UUID, context: dict[str, Any]) -> dict[str, str]:
    """Observe persisted production QA and final Guardian-derived usability truth."""
    request_id, render_job_id, artifact_id = _canonical_qa_correlation(context)
    try:
        mission_id = uuid.UUID(str(context["mission_id"]))
        execution_id = uuid.UUID(str(context["execution_id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("canonical QA requires valid mission and execution IDs") from exc

    async def run() -> dict[str, str]:
        from sqlalchemy import select

        from omega.domain.production import (
            MediaArtifactType,
            ProductionOutcome,
            ProductionQAStatus,
            ProductionRequestStatus,
            RenderJobState,
        )
        from omega.infrastructure.database import AsyncWorkerSessionLocal
        from omega.infrastructure.models import (
            MediaArtifact,
            Mission,
            MissionExecution,
            ProductionQAResult,
            ProductionRenderJob,
            ProductionRequest,
            Task,
        )

        async with AsyncWorkerSessionLocal() as async_session:
            task = await async_session.get(Task, task_id)
            execution = await async_session.get(MissionExecution, execution_id)
            mission = await async_session.get(Mission, mission_id)
            request = await async_session.get(ProductionRequest, request_id)
            render_job = await async_session.get(ProductionRenderJob, render_job_id)
            artifact = await async_session.get(MediaArtifact, artifact_id)

            if task is None or task.execution_id != execution_id or task.mission_id != mission_id:
                raise ValueError("QA Task MissionExecution lineage is invalid")
            if execution is None or execution.mission_id != mission_id:
                raise ValueError("QA MissionExecution lineage is invalid")
            if mission is None or mission.channel_id is None:
                raise ValueError("QA Mission channel lineage is invalid")
            if request is None or request.mission_execution_id != execution_id:
                raise ValueError("ProductionRequest MissionExecution lineage is invalid")
            if request.channel_id != mission.channel_id:
                raise ValueError("ProductionRequest channel lineage is invalid")
            if (
                execution.channel_dna_revision_id is None
                or request.channel_dna_revision_id != execution.channel_dna_revision_id
            ):
                raise ValueError("ProductionRequest pinned DNA lineage is invalid")
            if render_job is None or render_job.production_request_id != request_id:
                raise ValueError("ProductionRenderJob lineage is invalid")
            if (
                artifact is None
                or artifact.production_request_id != request_id
                or artifact.render_job_id != render_job_id
                or artifact.artifact_type != MediaArtifactType.VIDEO.value
                or not artifact.is_current
            ):
                raise ValueError("MediaArtifact lineage is invalid")
            if request.status != ProductionRequestStatus.SUCCEEDED.value:
                raise ValueError("ProductionRequest is not mechanically successful")
            if render_job.state != RenderJobState.SUCCEEDED.value:
                raise ValueError("ProductionRenderJob is not mechanically successful")

            qa_result = (
                await async_session.execute(
                    select(ProductionQAResult).where(
                        ProductionQAResult.production_request_id == request_id,
                        ProductionQAResult.artifact_id == artifact_id,
                    )
                )
            ).scalar_one_or_none()
            if qa_result is None:
                raise ValueError("exact ProductionQAResult not found")
            if request.outcome == ProductionOutcome.BLOCKED.value:
                raise RuntimeError("production usability is BLOCKED")
            if request.outcome != ProductionOutcome.RENDERED.value:
                raise ValueError("ProductionRequest has no accepted final usability outcome")
            if qa_result.status not in (
                ProductionQAStatus.PASSED.value,
                ProductionQAStatus.PASSED_WITH_WARNINGS.value,
            ):
                raise RuntimeError(f"production QA is not accepted: {qa_result.status}")
            return {
                "production_request_id": str(request_id),
                "media_artifact_id": str(artifact_id),
                "production_qa_result_id": str(qa_result.id),
            }

    return asyncio.run(run())


def _canonical_publish_artifact_id(context: dict[str, Any]) -> uuid.UUID:
    """Parse the exact media artifact correlation from the direct QA dependency."""
    dependency_outputs = context.get("dependency_outputs")
    if not isinstance(dependency_outputs, dict):
        raise ValueError("dependency_outputs must be an object")
    qa_output = dependency_outputs.get("qa")
    if not isinstance(qa_output, dict) or "media_artifact_id" not in qa_output:
        raise ValueError("QA dependency must supply canonical media_artifact_id")
    try:
        return uuid.UUID(str(qa_output["media_artifact_id"]))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("QA dependency contains malformed media_artifact_id") from exc


def _execute_canonical_publish(
    task_id: uuid.UUID, task_input: dict[str, Any] | None, context: dict[str, Any]
) -> dict[str, Any] | _PendingPublishResult:
    """Atomically prepare an approved intent, task correlation, and durable dispatch."""
    if not isinstance(task_input, dict):
        raise ValueError("canonical publish task input must be an object")
    canonical_publish = task_input.get("canonical_publish")
    if not isinstance(canonical_publish, dict):
        raise ValueError("canonical publish authority is required")
    required_authority = ("platform_account_id", "title", "made_for_kids")
    if any(key not in canonical_publish for key in required_authority):
        raise ValueError("canonical publish authority is malformed")
    execution_mode = canonical_publish.get("execution_mode", "EXTERNAL_DISPATCH")
    if execution_mode not in ("EXTERNAL_DISPATCH", "INTERNAL_READINESS_ONLY"):
        raise ValueError(f"unsupported canonical publish execution_mode: {execution_mode}")

    artifact_id = _canonical_publish_artifact_id(context)
    try:
        mission_id = uuid.UUID(str(context["mission_id"]))
        execution_id = uuid.UUID(str(context["execution_id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("canonical publish requires valid mission and execution IDs") from exc

    async def run() -> dict[str, Any] | _PendingPublishResult:
        from sqlalchemy import select

        from omega.application.durable_dispatch import DurableDispatchService
        from omega.application.publisher.intent_service import PublishIntentService
        from omega.application.publisher.publish_service import PublishExecutionService
        from omega.domain.mission import MissionState
        from omega.domain.publisher import PublishIntentCreate, PublishIntentState
        from omega.domain.task import TaskState
        from omega.infrastructure.database import AsyncWorkerSessionLocal
        from omega.infrastructure.models import MediaArtifact, Mission, MissionExecution, Task

        async with AsyncWorkerSessionLocal() as async_session:
            mission = (
                await async_session.execute(
                    select(Mission).where(Mission.id == mission_id).with_for_update()
                )
            ).scalar_one_or_none()
            task = (
                await async_session.execute(
                    select(Task).where(Task.id == task_id).with_for_update()
                )
            ).scalar_one_or_none()
            execution = await async_session.get(MissionExecution, execution_id)
            artifact = await async_session.get(MediaArtifact, artifact_id)

            current = (
                mission is not None
                and mission.state == MissionState.RUNNING.value
                and mission.channel_id is not None
                and task is not None
                and task.mission_id == mission.id
                and task.task_type == "publish"
                and task.state == TaskState.RUNNING.value
                and task.execution_id == execution_id
                and (task.dispatched_epoch is None or task.dispatched_epoch == mission.guardian_epoch)
                and execution is not None
                and execution.mission_id == mission.id
                and artifact is not None
            )
            if not current:
                raise ValueError("canonical publish current-state or QA artifact fence failed")

            payload = PublishIntentCreate(
                mission_id=mission.id,
                task_id=task.id,
                channel_id=mission.channel_id,
                platform_account_id=canonical_publish["platform_account_id"],
                media_artifact_id=artifact.id,
                media_artifact_checksum=artifact.content_hash,
                channel_dna_revision_id=execution.channel_dna_revision_id,
                title=canonical_publish["title"],
                description=canonical_publish.get("description", ""),
                tags=canonical_publish.get("tags", []),
                requested_privacy_status=canonical_publish.get(
                    "requested_privacy_status", "PRIVATE"
                ),
                category_id=canonical_publish.get("category_id", "28"),
                made_for_kids=canonical_publish["made_for_kids"],
                platform_custom_options=canonical_publish.get("platform_custom_options", {}),
            )
            intent = await PublishIntentService.create_publish_intent(
                async_session,
                payload,
                actor="MISSION_WORKER",
                initial_state=PublishIntentState.APPROVED,
                commit=False,
            )
            if intent.state != PublishIntentState.APPROVED.value:
                raise ValueError(
                    f"canonical PublishIntent is not execution eligible: {intent.state}"
                )

            task.output = {
                "publish_intent_id": str(intent.id),
                "media_artifact_id": str(artifact.id),
            }
            task.updated_at = datetime.now(UTC)
            if execution_mode == "INTERNAL_READINESS_ONLY":
                await async_session.commit()
                readiness = await PublishExecutionService.validate_internal_publish_readiness(
                    async_session,
                    guardian_session_factory=AsyncWorkerSessionLocal,
                    task_id=task.id,
                    mission_id=mission.id,
                    execution_id=execution.id,
                    artifact_id=artifact.id,
                    intent_id=intent.id,
                )
                task.output["internal_readiness"] = readiness
                if readiness["status"] != "INTERNAL_READY":
                    await async_session.commit()
                    raise ValueError(
                        "canonical internal publish readiness failed: "
                        + "; ".join(readiness["validation_errors"])
                    )
                await async_session.commit()
                return task.output

            await DurableDispatchService.enqueue_async(
                async_session,
                idempotency_key=f"publish-dispatch:{intent.id}",
                task_name="omega.publisher.execute_publish",
                args=[str(task.id)],
                purpose="PUBLISH_EXECUTION_DISPATCH",
                mission_id=task.mission_id,
                mission_execution_id=task.execution_id,
                mission_task_id=task.id,
            )
            await async_session.commit()
            return _PendingPublishResult(task.output)

    return asyncio.run(run())


def _canonical_topic_id(task_input: dict[str, Any] | None) -> uuid.UUID:
    """Parse the explicit persisted TopicCandidate authority for a canonical topic task."""
    if not isinstance(task_input, dict):
        raise ValueError("canonical topic input must be an object")
    if "topic_candidate_id" not in task_input:
        raise ValueError("canonical topic requires explicit persisted topic_candidate_id authority")
    try:
        return uuid.UUID(str(task_input["topic_candidate_id"]))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("canonical topic contains malformed topic_candidate_id") from exc


def _canonical_research_topic_id(context: dict[str, Any]) -> uuid.UUID:
    """Parse the exact direct topic_discovery dependency for canonical research."""
    dependency_outputs = context.get("dependency_outputs")
    if not isinstance(dependency_outputs, dict):
        raise ValueError("dependency_outputs must be an object")
    topic_output = dependency_outputs.get("topic_discovery")
    if not isinstance(topic_output, dict) or "topic_candidate_id" not in topic_output:
        raise ValueError("topic_discovery dependency with topic_candidate_id is required")
    try:
        return uuid.UUID(str(topic_output["topic_candidate_id"]))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("topic_discovery dependency contains malformed topic_candidate_id") from exc


def _execute_canonical_topic(
    task_id: uuid.UUID, task_input: dict[str, Any] | None, context: dict[str, Any]
) -> dict[str, str]:
    """Evaluate and select an explicit persisted topic using pinned Mission context."""
    topic_id = _canonical_topic_id(task_input)
    try:
        mission_id = uuid.UUID(str(context["mission_id"]))
        execution_id = uuid.UUID(str(context["execution_id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("canonical topic requires valid mission and execution IDs") from exc

    async def run() -> dict[str, str]:
        from omega.application import topic_service
        from omega.domain.topic import EvaluationContextMode, TopicStatus
        from omega.infrastructure.database import AsyncWorkerSessionLocal
        from omega.infrastructure.models import Mission, MissionExecution, Task, TopicCandidate

        async with AsyncWorkerSessionLocal() as async_session:
            task = await async_session.get(Task, task_id)
            execution = await async_session.get(MissionExecution, execution_id)
            mission = await async_session.get(Mission, mission_id)
            candidate = await async_session.get(TopicCandidate, topic_id)
            if task is None or task.mission_id != mission_id or task.execution_id != execution_id:
                raise ValueError("topic Task MissionExecution lineage is invalid")
            if execution is None or execution.mission_id != mission_id:
                raise ValueError("topic MissionExecution lineage is invalid")
            if mission is None or mission.channel_id is None:
                raise ValueError("topic Mission channel lineage is invalid")
            if execution.channel_dna_revision_id is None:
                raise ValueError("topic MissionExecution is missing pinned ChannelDNARevision")
            if candidate is None or candidate.channel_id != mission.channel_id:
                raise ValueError("TopicCandidate channel lineage is invalid")

            if candidate.status != TopicStatus.SELECTED.value:
                await topic_service.evaluate_candidate(
                    async_session,
                    topic_id,
                    mode=EvaluationContextMode.MISSION_EXECUTION,
                    mission_execution_id=execution_id,
                )
                await topic_service.select_candidate(async_session, topic_id)

            persisted = await async_session.get(TopicCandidate, topic_id)
            if (
                persisted is None
                or persisted.channel_id != mission.channel_id
                or persisted.status != TopicStatus.SELECTED.value
            ):
                raise ValueError("TopicCandidate did not reach authoritative SELECTED state")
            return {"topic_candidate_id": str(persisted.id)}

    return asyncio.run(run())


def _execute_canonical_research(
    task_id: uuid.UUID,
    task_input: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, str]:
    """Create/reuse and run canonical research through the persisted ResearchService lifecycle."""
    topic_id = _canonical_research_topic_id(context)
    try:
        mission_id = uuid.UUID(str(context["mission_id"]))
        execution_id = uuid.UUID(str(context["execution_id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("canonical research requires valid mission and execution IDs") from exc

    async def run() -> dict[str, str]:
        from sqlalchemy import select

        from omega.application import research_service
        from omega.domain.research import (
            ResearchOutcome,
            ResearchRequestCreate,
            ResearchRequestStatus,
            ResearchSourceBatchCreate,
        )
        from omega.domain.topic import TopicStatus
        from omega.infrastructure.database import AsyncWorkerSessionLocal
        from omega.infrastructure.models import (
            Mission,
            MissionExecution,
            ResearchBrief,
            ResearchRequest,
            Task,
            TopicCandidate,
        )

        async with AsyncWorkerSessionLocal() as async_session:
            if task_input is not None and not isinstance(task_input, dict):
                raise ValueError("canonical research task input must be an object")
            research_authority = (
                task_input.get("canonical_research")
                if isinstance(task_input, dict)
                else None
            )
            source_batch = (
                ResearchSourceBatchCreate.model_validate(research_authority)
                if research_authority is not None
                else None
            )
            task = await async_session.get(Task, task_id)
            execution = await async_session.get(MissionExecution, execution_id)
            mission = await async_session.get(Mission, mission_id)
            topic = await async_session.get(TopicCandidate, topic_id)
            if task is None or task.mission_id != mission_id or task.execution_id != execution_id:
                raise ValueError("research Task MissionExecution lineage is invalid")
            if execution is None or execution.mission_id != mission_id:
                raise ValueError("research MissionExecution lineage is invalid")
            if mission is None or mission.channel_id is None:
                raise ValueError("research Mission channel lineage is invalid")
            if execution.channel_dna_revision_id is None:
                raise ValueError("research MissionExecution is missing pinned ChannelDNARevision")
            if (
                topic is None
                or topic.channel_id != mission.channel_id
                or topic.status != TopicStatus.SELECTED.value
            ):
                raise ValueError("research TopicCandidate lineage is invalid")

            identity = f"mission-research:{execution_id}:{task_id}"
            request = (
                await async_session.execute(
                    select(ResearchRequest).where(
                        ResearchRequest.mission_execution_id == execution_id,
                        ResearchRequest.topic_candidate_id == topic_id,
                        ResearchRequest.channel_id == mission.channel_id,
                        ResearchRequest.metadata_["canonical_task_identity"].as_string() == identity,
                    )
                )
            ).scalar_one_or_none()
            if request is None:
                created = await research_service.create_research_request(
                    async_session,
                    mission.channel_id,
                    ResearchRequestCreate(
                        topic_candidate_id=topic_id,
                        mission_execution_id=execution_id,
                        metadata={"canonical_task_identity": identity},
                    ),
                )
                request = await async_session.get(ResearchRequest, created.id)

            if (
                request is None
                or request.topic_candidate_id != topic_id
                or request.mission_execution_id != execution_id
                or request.channel_id != mission.channel_id
                or dict(request.metadata_ or {}).get("canonical_task_identity") != identity
            ):
                raise ValueError("ResearchRequest canonical lineage is invalid")

            brief = None
            if request.status == ResearchRequestStatus.SUCCEEDED.value:
                brief = (
                    await async_session.execute(
                        select(ResearchBrief).where(
                            ResearchBrief.research_request_id == request.id,
                            ResearchBrief.is_current.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if brief is None:
                    raise ValueError("successful ResearchRequest has no authoritative current brief")
            elif request.status in (
                ResearchRequestStatus.FAILED.value,
                ResearchRequestStatus.CANCELLED.value,
                ResearchRequestStatus.RUNNING.value,
            ):
                raise ValueError(f"ResearchRequest cannot run from state '{request.status}'")
            else:
                if source_batch is not None:
                    await research_service.batch_add_sources(
                        async_session,
                        request.id,
                        source_batch,
                    )
                generated = await research_service.run_research(async_session, request.id)
                brief = await async_session.get(ResearchBrief, generated.id)

            if (
                brief is None
                or brief.research_request_id != request.id
                or brief.topic_candidate_id != topic_id
                or brief.channel_id != mission.channel_id
            ):
                raise ValueError("ResearchBrief canonical lineage is invalid")
            if (
                request.status != ResearchRequestStatus.SUCCEEDED.value
                or request.outcome != ResearchOutcome.SUFFICIENT.value
                or brief.outcome != ResearchOutcome.SUFFICIENT.value
            ):
                raise ValueError("canonical research did not produce SUFFICIENT authority")
            return {
                "topic_candidate_id": str(topic_id),
                "research_request_id": str(request.id),
                "research_brief_id": str(brief.id),
            }

    return asyncio.run(run())


def _canonical_content_pair(
    task_input: dict[str, Any] | None, context: dict[str, Any]
) -> tuple[uuid.UUID, uuid.UUID]:
    """Resolve and normalize the canonical topic/research-brief handoff."""

    def parse_source(source: Any, label: str, *, absent_is_empty: bool) -> tuple[uuid.UUID, uuid.UUID] | None:
        if source is None and absent_is_empty:
            return None
        if not isinstance(source, dict):
            raise ValueError(f"{label} must be an object")
        present = {key for key in ("topic_candidate_id", "research_brief_id") if key in source}
        if not present:
            if absent_is_empty:
                return None
            raise ValueError(f"{label} must supply both canonical correlation IDs")
        if len(present) != 2:
            raise ValueError(f"{label} must supply both canonical correlation IDs")
        try:
            return (
                uuid.UUID(str(source["topic_candidate_id"])),
                uuid.UUID(str(source["research_brief_id"])),
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"{label} contains malformed UUID correlation") from exc

    dependency_outputs = context.get("dependency_outputs")
    if not isinstance(dependency_outputs, dict):
        raise ValueError("dependency_outputs must be an object")
    dependency_pair = parse_source(
        dependency_outputs.get("research"), "research dependency output", absent_is_empty=True
    )

    if task_input is not None and not isinstance(task_input, dict):
        raise ValueError("task_input must be an object")
    seed_present = isinstance(task_input, dict) and "canonical_seed" in task_input
    seed_pair = parse_source(
        task_input.get("canonical_seed") if isinstance(task_input, dict) else None,
        "canonical_seed",
        absent_is_empty=not seed_present,
    )

    if dependency_pair and seed_pair and dependency_pair != seed_pair:
        raise ValueError("research dependency output and canonical_seed correlation mismatch")
    pair = dependency_pair or seed_pair
    if pair is None:
        raise ValueError("canonical content correlation pair is required")
    return pair


def _execute_canonical_content(
    task_id: uuid.UUID, task_input: dict[str, Any] | None, context: dict[str, Any]
) -> dict[str, str]:
    """Run canonical Mission content through the existing async ContentService lifecycle."""
    topic_id, brief_id = _canonical_content_pair(task_input, context)
    try:
        mission_id = uuid.UUID(str(context["mission_id"]))
        execution_id = uuid.UUID(str(context["execution_id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("canonical content requires valid mission and execution IDs") from exc

    async def run() -> dict[str, str]:
        from sqlalchemy import select

        from omega.application import content_service
        from omega.domain.content import (
            ContentGenerationRequestCreate,
            ContentOutcome,
            ContentRequestStatus,
            ScriptQAStatus,
        )
        from omega.domain.research import ResearchOutcome, ResearchRequestStatus
        from omega.domain.topic import TopicStatus
        from omega.infrastructure.database import AsyncWorkerSessionLocal
        from omega.infrastructure.models import (
            ContentGenerationRequest,
            Mission,
            MissionExecution,
            ResearchBrief,
            ResearchRequest,
            ScriptVersion,
            TopicCandidate,
        )

        async with AsyncWorkerSessionLocal() as async_session:
            execution = await async_session.get(MissionExecution, execution_id)
            if execution is None or execution.mission_id != mission_id:
                raise ValueError("task MissionExecution lineage is invalid")
            mission = await async_session.get(Mission, mission_id)
            if mission is None or mission.channel_id is None:
                raise ValueError("Mission channel lineage is invalid")
            if execution.channel_dna_revision_id is None:
                raise ValueError("MissionExecution is missing pinned ChannelDNARevision")

            topic = await async_session.get(TopicCandidate, topic_id)
            if (
                topic is None
                or topic.channel_id != mission.channel_id
                or topic.status != TopicStatus.SELECTED.value
            ):
                raise ValueError("TopicCandidate is not valid for Mission content generation")

            brief = await async_session.get(ResearchBrief, brief_id)
            if (
                brief is None
                or brief.topic_candidate_id != topic_id
                or brief.channel_id != mission.channel_id
            ):
                raise ValueError("ResearchBrief does not match the canonical topic/channel")
            research_request = await async_session.get(ResearchRequest, brief.research_request_id)
            if (
                research_request is None
                or research_request.topic_candidate_id != topic_id
                or research_request.channel_id != mission.channel_id
                or research_request.mission_execution_id != execution_id
            ):
                raise ValueError("ResearchBrief parent request MissionExecution lineage is invalid")
            if (
                research_request.status != ResearchRequestStatus.SUCCEEDED.value
                or research_request.outcome != ResearchOutcome.SUFFICIENT.value
                or brief.outcome != ResearchOutcome.SUFFICIENT.value
            ):
                raise ValueError("ResearchBrief is not SUFFICIENT canonical authority")

            if task_input is not None and not isinstance(task_input, dict):
                raise ValueError("task_input must be an object")
            content_authority = (
                task_input.get("canonical_content")
                if isinstance(task_input, dict)
                else None
            )
            if content_authority is not None and not isinstance(content_authority, dict):
                raise ValueError("canonical_content must be an object")

            idempotency_key = hashlib.sha256(
                f"mission-content:{execution_id}:{task_id}".encode()
            ).hexdigest()
            request_in = ContentGenerationRequestCreate(
                topic_candidate_id=topic_id,
                research_brief_id=brief_id,
                mission_execution_id=execution_id,
                **(content_authority or {}),
            )
            request = await content_service.create_request(
                async_session, mission.channel_id, request_in, idempotency_key=idempotency_key
            )
            persisted_request = await async_session.get(ContentGenerationRequest, request.id)
            if (
                persisted_request is None
                or persisted_request.mission_execution_id != execution_id
                or persisted_request.channel_id != mission.channel_id
                or persisted_request.topic_candidate_id != topic_id
                or persisted_request.research_brief_id != brief_id
                or persisted_request.channel_dna_revision_id != execution.channel_dna_revision_id
            ):
                raise ValueError("ContentGenerationRequest lineage is invalid")

            script = None
            if persisted_request.status == ContentRequestStatus.SUCCEEDED.value:
                script = (
                    await async_session.execute(
                        select(ScriptVersion).where(
                            ScriptVersion.content_request_id == persisted_request.id,
                            ScriptVersion.is_current.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if script is None:
                    raise ValueError("successful content request has no authoritative current script")
            elif persisted_request.status in (
                ContentRequestStatus.FAILED.value,
                ContentRequestStatus.CANCELLED.value,
            ):
                raise ValueError(f"content request is terminal: {persisted_request.status}")
            else:
                generated = await content_service.generate_content(
                    async_session, mission.channel_id, persisted_request.id
                )
                script = await async_session.get(ScriptVersion, generated.id)

            if script is None or script.content_request_id != persisted_request.id:
                raise ValueError("ScriptVersion does not belong to ContentGenerationRequest")
            if (
                persisted_request.outcome != ContentOutcome.GENERATED.value
                or script.qa_status
                not in (
                    ScriptQAStatus.PASSED.value,
                    ScriptQAStatus.PASSED_WITH_WARNINGS.value,
                )
            ):
                raise ValueError("canonical content did not produce an accepted script")
            return {
                "content_request_id": str(persisted_request.id),
                "script_version_id": str(script.id),
                "topic_candidate_id": str(topic_id),
                "research_brief_id": str(brief_id),
            }

    return asyncio.run(run())


# ── OMEGA-001 Foundation Task (Preserved) ──


@celery_app.task(bind=True, name="omega.worker.tasks.run_test_job")
def run_test_job(self, job_id: str) -> dict:
    """Execute a test job (OMEGA-001 Foundation).

    Updates job state: RUNNING → SUCCEEDED (or FAILED on error).
    Uses synchronous DB access via psycopg2.
    """
    from omega.infrastructure.database_sync import SyncSessionLocal
    from omega.infrastructure.models import Job

    logger.info("Test job started", job_id=job_id)

    session = SyncSessionLocal()
    try:
        parsed_id = uuid.UUID(str(job_id))
        job = session.query(Job).filter(Job.id == parsed_id).first()
        if not job:
            logger.error("Job not found in database", job_id=job_id)
            return {"status": "error", "message": "Job not found"}

        # Mark as RUNNING
        job.state = "RUNNING"
        job.started_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        session.commit()

        # Simulate work
        time.sleep(2)

        # Mark as SUCCEEDED
        job.state = "SUCCEEDED"
        job.result = {"message": "Test job completed successfully"}
        job.completed_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        session.commit()

        logger.info("Test job completed", job_id=job_id)
        return {"status": "success", "job_id": job_id}

    except Exception as exc:
        session.rollback()
        logger.error(
            "Test job failed",
            job_id=job_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        try:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job:
                job.state = "FAILED"
                job.error = _sanitize_task_error(exc)
                job.completed_at = datetime.now(UTC)
                job.updated_at = datetime.now(UTC)
                session.commit()
        except Exception:
            logger.error("Failed to update job state after error", job_id=job_id, exc_info=True)
        return {"status": "error", "job_id": job_id}
    finally:
        session.close()


# ── OMEGA-002 Mission Engine Worker Tasks ──


@celery_app.task(bind=True, name="omega.tasks.execute")
def execute_task(self, task_id: str) -> dict:
    """Execute a mission task using registered TaskExecutor.

    Atomic transitions:
    QUEUED → RUNNING → SUCCEEDED (or FAILED / READY on retry).
    Uses synchronous psycopg2 session.
    """
    from omega.application.executor import default_executor_registry
    from omega.domain.decision import Actor, DecisionType
    from omega.domain.mission import MissionState
    from omega.domain.task import TaskState
    from omega.infrastructure.database_sync import SyncSessionLocal
    from omega.infrastructure.models import DecisionLog, Mission, Task

    logger.info("Executing task", task_id=task_id)
    session = SyncSessionLocal()
    parsed_id = uuid.UUID(str(task_id))

    try:
        # 1. Inspect mission_id for lock hierarchy
        initial_task = session.query(Task.mission_id).filter(Task.id == parsed_id).first()
        if not initial_task:
            logger.error("Task not found in database", task_id=task_id)
            return {"status": "error", "message": "Task not found"}

        mission_id = initial_task.mission_id

        # 2. Lock Mission FIRST, Task SECOND
        mission = session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
        task = session.query(Task).filter(Task.id == parsed_id).with_for_update().first()

        if not mission or not task:
            logger.error("Mission or Task disappeared under lock", task_id=task_id)
            return {"status": "error", "message": "Record missing under lock"}

        if mission.state != MissionState.RUNNING.value:
            logger.warning(
                "Mission not in RUNNING state, refusing execution",
                task_id=task_id,
                mission_state=mission.state,
            )
            return {"status": "skipped", "reason": "mission_not_running"}

        # Validate fencing token
        if task.dispatched_epoch is not None and task.dispatched_epoch != mission.guardian_epoch:
            logger.warning(
                "Stale task epoch detected. Refusing execution without side effects.",
                task_id=task_id,
                dispatched_epoch=task.dispatched_epoch,
                mission_epoch=mission.guardian_epoch,
            )
            session.add(
                DecisionLog(
                    mission_id=mission.id,
                    execution_id=task.execution_id,
                    task_id=task.id,
                    decision_type=DecisionType.TASK_FAILED.value,
                    decision=f"Worker refused stale task '{task.title}'",
                    reason=f"Epoch mismatch: dispatched={task.dispatched_epoch}, current={mission.guardian_epoch}",
                    actor=Actor.WORKER.value,
                )
            )
            session.commit()
            return {
                "status": "stale_epoch_refused",
                "dispatched_epoch": task.dispatched_epoch,
                "current_epoch": mission.guardian_epoch,
            }

        if task.state != TaskState.QUEUED.value:
            logger.warning(
                "Task not in QUEUED state, skipping execution",
                task_id=task_id,
                current_state=task.state,
            )
            return {"status": "skipped", "task_state": task.state}

        now = datetime.now(UTC)
        task.state = TaskState.RUNNING.value
        task.started_at = now
        task.updated_at = now
        session.commit()

        # Resolve executor and hydrate canonical direct-dependency correlation outputs.
        context = {
            "mission_id": str(task.mission_id),
            "execution_id": str(task.execution_id) if task.execution_id else None,
            "dependency_outputs": _load_dependency_outputs(session, task),
        }

        # Execute canonical service-backed stages outside the placeholder registry.
        if task.task_type == "topic_discovery":
            result_output = _execute_canonical_topic(task.id, task.input, context)
        elif task.task_type == "research":
            result_output = _execute_canonical_research(task.id, task.input, context)
        elif task.task_type == "content_generation":
            result_output = _execute_canonical_content(task.id, task.input, context)
        elif task.task_type == "production":
            result_output = _execute_canonical_production(task.id, context)
        elif task.task_type == "qa":
            result_output = _execute_canonical_qa(task.id, context)
        elif task.task_type == "publish":
            result_output = _execute_canonical_publish(task.id, task.input, context)
        else:
            executor = default_executor_registry.get(task.task_type)
            result_output = executor.execute(
                task_id=task.id,
                task_type=task.task_type,
                task_input=task.input,
                context=context,
            )

        if isinstance(result_output, _PendingPublishResult):
            # Preparation committed all correlation and dispatch state. Do not race the publisher.
            return {"status": "pending", "task_id": task_id}

        if isinstance(result_output, _PendingProductionResult):
            # Re-enter the worker lock hierarchy before crossing the external dispatch boundary.
            mission = (
                session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
            )
            task = session.query(Task).filter(Task.id == parsed_id).with_for_update().first()
            pending_is_current = (
                mission is not None
                and task is not None
                and mission.state == MissionState.RUNNING.value
                and task.mission_id == mission.id
                and task.execution_id == uuid.UUID(str(context["execution_id"]))
                and task.task_type == "production"
                and task.state == TaskState.RUNNING.value
                and (
                    task.dispatched_epoch is None
                    or task.dispatched_epoch == mission.guardian_epoch
                )
            )
            if not pending_is_current:
                session.rollback()
                logger.warning(
                    "Refusing stale pending production correlation and render dispatch",
                    task_id=task_id,
                )
                return {"status": "skipped", "reason": "stale_pending_production"}

            task.output = dict(result_output)
            task.updated_at = datetime.now(UTC)
            if result_output.dispatch_required:
                from omega.application.durable_dispatch import DurableDispatchService

                DurableDispatchService.enqueue(
                    session,
                    idempotency_key=(
                        "render-dispatch:"
                        f"{result_output.production_request_id}:{result_output.render_job_id}"
                    ),
                    task_name="omega.production.render",
                    args=[
                        result_output.channel_id,
                        result_output.production_request_id,
                        result_output.render_job_id,
                    ],
                    purpose="PRODUCTION_RENDER_DISPATCH",
                    mission_id=task.mission_id,
                    mission_execution_id=task.execution_id,
                    mission_task_id=task.id,
                    production_request_id=uuid.UUID(result_output.production_request_id),
                    render_job_id=uuid.UUID(result_output.render_job_id),
                )
            session.commit()
            return {"status": "pending", "task_id": task_id}

        # Mark SUCCEEDED under lock
        now = datetime.now(UTC)
        mission = session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
        task = session.query(Task).filter(Task.id == parsed_id).with_for_update().first()
        if task:
            task.state = TaskState.SUCCEEDED.value
            task.output = result_output
            task.completed_at = now
            task.updated_at = now

            decision = DecisionLog(
                mission_id=task.mission_id,
                execution_id=task.execution_id,
                task_id=task.id,
                decision_type=DecisionType.TASK_SUCCEEDED.value,
                decision=f"Task '{task.title}' succeeded",
                reason="Executor returned successfully",
                actor=Actor.WORKER.value,
            )
            session.add(decision)
            session.flush()
            from omega.application.durable_dispatch import DurableDispatchService

            DurableDispatchService.enqueue(
                session,
                idempotency_key=f"mission-evaluation:{task.execution_id}:{decision.id}",
                task_name="omega.orchestrator.evaluate",
                args=[str(task.mission_id), str(task.execution_id) if task.execution_id else ""],
                purpose="MISSION_TASK_TERMINAL_EVALUATION",
                mission_id=task.mission_id,
                mission_execution_id=task.execution_id,
                mission_task_id=task.id,
            )
            session.commit()

            logger.info("Task completed successfully", task_id=task_id)

        return {"status": "success", "task_id": task_id}

    except Exception as exc:
        session.rollback()
        sanitized_err = _sanitize_task_error(exc)
        logger.error(
            "Task execution failed",
            task_id=task_id,
            error=sanitized_err,
            exc_info=True,
        )

        try:
            now = datetime.now(UTC)
            mission = (
                session.query(Mission).filter(Mission.id == mission_id).with_for_update().first()
            )
            task = session.query(Task).filter(Task.id == parsed_id).with_for_update().first()
            if task:
                if task.retry_count < task.max_retries:
                    # Increment retry atomically and transition back to READY
                    task.retry_count += 1
                    task.state = TaskState.READY.value
                    task.error = f"Retry {task.retry_count}/{task.max_retries}: {sanitized_err}"
                    task.updated_at = now

                    session.add(
                        DecisionLog(
                            mission_id=task.mission_id,
                            execution_id=task.execution_id,
                            task_id=task.id,
                            decision_type=DecisionType.TASK_RETRY.value,
                            decision=f"Schedule retry {task.retry_count}/{task.max_retries} for task '{task.title}'",
                            reason=sanitized_err,
                            actor=Actor.WORKER.value,
                        )
                    )
                else:
                    # Retries exhausted -> FAILED
                    task.state = TaskState.FAILED.value
                    task.error = sanitized_err
                    task.completed_at = now
                    task.updated_at = now

                    session.add(
                        DecisionLog(
                            mission_id=task.mission_id,
                            execution_id=task.execution_id,
                            task_id=task.id,
                            decision_type=DecisionType.TASK_FAILED.value,
                            decision=f"Task '{task.title}' permanently failed",
                            reason=f"Max retries exhausted ({task.max_retries}). Error: {sanitized_err}",
                            actor=Actor.WORKER.value,
                        )
                    )

                from omega.application.durable_dispatch import DurableDispatchService

                DurableDispatchService.enqueue(
                    session,
                    idempotency_key=(
                        f"mission-evaluation:{task.execution_id}:{task.id}:"
                        f"{task.state}:{task.retry_count}"
                    ),
                    task_name="omega.orchestrator.evaluate",
                    args=[str(task.mission_id), str(task.execution_id) if task.execution_id else ""],
                    purpose="MISSION_TASK_FAILURE_EVALUATION",
                    mission_id=task.mission_id,
                    mission_execution_id=task.execution_id,
                    mission_task_id=task.id,
                )
                session.commit()

        except Exception:
            logger.error("Failed to persist task error state", task_id=task_id, exc_info=True)

        return {"status": "failed", "task_id": task_id, "error": sanitized_err}
    finally:
        session.close()


@celery_app.task(bind=True, name="omega.orchestrator.evaluate")
def evaluate_mission_task(self, mission_id: str, execution_id: str = "") -> dict:
    """Trigger synchronous Orchestrator evaluation from Celery worker queue."""
    from omega.application.orchestrator import evaluate_mission_sync
    from omega.infrastructure.database_sync import SyncSessionLocal

    session = SyncSessionLocal()
    try:
        m_id = uuid.UUID(str(mission_id))
        e_id = uuid.UUID(str(execution_id)) if execution_id else None
        res = evaluate_mission_sync(session, m_id, e_id)
        return res
    except Exception as exc:
        logger.error(
            "Orchestrator sync evaluation task failed", mission_id=mission_id, exc_info=True
        )
        return {"status": "error", "message": _sanitize_task_error(exc)}
    finally:
        session.close()


@celery_app.task(bind=True, name="omega.production.render")
def execute_production_render_task(
    self,
    channel_id: str,
    request_id: str,
    job_id: str,
) -> dict:
    """Asynchronous background rendering task using ProductionRenderService."""
    import asyncio

    from omega.infrastructure.database import AsyncWorkerSessionLocal

    logger.info(
        "Starting background render task",
        channel_id=channel_id,
        request_id=request_id,
        job_id=job_id,
    )

    async def _run():
        async with AsyncWorkerSessionLocal() as session:
            try:
                r_id = uuid.UUID(str(request_id))
                j_id = uuid.UUID(str(job_id))
                c_id = uuid.UUID(str(channel_id))
                from omega.application.production_render_factory import (
                    build_production_render_service,
                )

                service = build_production_render_service()
            except Exception as exc:
                sanitized_error = _sanitize_task_error(exc)
                logger.error(
                    "Background render bootstrap failed",
                    job_id=job_id,
                    error=sanitized_error,
                    exc_info=True,
                )
                persisted = await _record_render_bootstrap_failure(
                    session,
                    request_id,
                    job_id,
                    sanitized_error,
                )
                return {
                    "status": "failed",
                    "error": sanitized_error,
                    "failure_persisted": persisted,
                }

            try:
                art, qa_status = await service.execute_render_job(session, c_id, r_id, j_id)
                return {
                    "status": "success",
                    "artifact_id": str(art.id) if art else None,
                    "qa_status": str(qa_status.value),
                }
            except Exception as exc:
                logger.error("Background render task failed", job_id=job_id, exc_info=True)
                return {
                    "status": "failed",
                    "error": _sanitize_task_error(exc),
                }

    return asyncio.run(_run())


@celery_app.task(name="omega.dispatch.relay")
def durable_dispatch_relay_task() -> dict[str, int | str]:
    """Publish one bounded batch of persisted generic dispatch intents."""
    from omega.application.durable_dispatch import DurableDispatchService
    from omega.infrastructure.database_sync import SyncSessionLocal

    session = SyncSessionLocal()
    try:
        return {"status": "success", **DurableDispatchService.relay_batch(session, publisher=celery_app)}
    except Exception:
        session.rollback()
        logger.error("Durable dispatch relay failed", exc_info=True)
        return {"status": "error", "claimed": 0, "sent": 0, "retried": 0, "dead_letter": 0}
    finally:
        session.close()


@celery_app.task(name="omega.guardian.process_alert_outbox")
def process_guardian_alert_outbox() -> dict[str, int]:
    """Process pending alerts from the transactional guardian outbox."""
    import asyncio

    from omega.application.guardian.outbox import AlertOutboxService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            return await AlertOutboxService.process_outbox_items(session)

    try:
        processed = asyncio.run(_run())
        return {"status": "success", "processed": processed}
    except Exception as exc:
        logger.error("Failed to process guardian alert outbox", error=str(exc), exc_info=True)
        return {"status": "error", "processed": 0}


# ── OMEGA-010 Scheduler Background Worker Tasks ──


@celery_app.task(name="omega.scheduler.dispatch_sweep")
def schedule_dispatch_sweep_task() -> dict[str, int]:
    """Execute periodic dispatch sweep for due ACTIVE reservations."""
    import asyncio

    from omega.application.scheduler.sweep_service import SchedulerSweepService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> dict[str, int]:
        async with AsyncSessionLocal() as session:
            return await SchedulerSweepService.run_dispatch_sweep(session)

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Schedule dispatch sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "claimed": 0, "dispatched": 0, "rejected": 0}


@celery_app.task(name="omega.scheduler.outbox_relay")
def schedule_outbox_relay_task() -> dict[str, int]:
    """Execute periodic outbox relay for pending/retry dispatch outbox items."""
    import asyncio

    from omega.application.scheduler.outbox_relay import OutboxRelayService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> dict[str, int]:
        async with AsyncSessionLocal() as session:
            return await OutboxRelayService.process_outbox_batch(session)

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Schedule outbox relay failed", error=str(exc), exc_info=True)
        return {"status": "error", "claimed": 0, "sent": 0, "retried": 0, "dead_letter": 0}


@celery_app.task(name="omega.scheduler.expiration_sweep")
def schedule_expiration_sweep_task() -> dict[str, int]:
    """Execute periodic expiration sweep for expired ACTIVE reservations."""
    import asyncio

    from omega.application.scheduler.sweep_service import SchedulerSweepService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            return await SchedulerSweepService.run_expiration_sweep(session)

    try:
        expired = asyncio.run(_run())
        return {"status": "success", "expired": expired}
    except Exception as exc:
        logger.error("Schedule expiration sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "expired": 0}


@celery_app.task(name="omega.scheduler.stale_dispatching_sweep")
def schedule_stale_dispatching_sweep_task() -> dict[str, int]:
    """Execute periodic stale DISPATCHING recovery sweep."""
    import asyncio

    from omega.application.scheduler.sweep_service import SchedulerSweepService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> dict[str, int]:
        async with AsyncSessionLocal() as session:
            return await SchedulerSweepService.run_stale_dispatching_recovery_sweep(session)

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Schedule stale dispatch recovery failed", error=str(exc), exc_info=True)
        return {"status": "error", "recovered": 0, "consumed": 0, "released": 0, "requeued": 0}


@celery_app.task(name="omega.publisher.execute_publish")
def execute_publish_task(task_id: str) -> dict[str, Any]:
    """Execute external publication for an approved PublishIntent."""
    import asyncio
    from uuid import UUID

    from omega.application.publisher.publish_service import PublishExecutionService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            attempt = await PublishExecutionService.execute_publish(session, UUID(task_id))
            return {
                "attempt_id": str(attempt.id),
                "state": attempt.state,
                "provider_video_id": attempt.provider_video_id,
            }

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error(
            "Publisher task execution failed", task_id=task_id, error=str(exc), exc_info=True
        )
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="omega.publisher.handoff_sweep")
def publisher_handoff_sweep_task() -> dict[str, int]:
    """Periodic sweep delivering retry handoffs to OMEGA-010 Scheduler."""
    import asyncio

    from omega.application.publisher.handoff_relay import HandoffRelayService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            return await HandoffRelayService.process_pending_handoffs(session)

    try:
        delivered = asyncio.run(_run())
        return {"status": "success", "delivered": delivered}
    except Exception as exc:
        logger.error("Publisher handoff sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "delivered": 0}


@celery_app.task(name="omega.publisher.reconciliation_sweep")
def publisher_reconciliation_sweep_task() -> dict[str, int]:
    """Periodic sweep reconciling ambiguous UNKNOWN publish attempts."""
    import asyncio

    from omega.application.publisher.reconciliation_service import ReconciliationService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            return await ReconciliationService.reconcile_pending_attempts_sweep(session)

    try:
        reconciled = asyncio.run(_run())
        return {"status": "success", "reconciled": reconciled}
    except Exception as exc:
        logger.error("Publisher reconciliation sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "reconciled": 0}


# ── OMEGA-012 Analytics Engine Tasks ──────────────────────────────────────────


@celery_app.task(name="omega.analytics.poll_sweep")
def analytics_poll_sweep_task() -> dict[str, Any]:
    """Periodic sweep scanning due analytics assets and enqueuing poll batches."""
    import asyncio

    from sqlalchemy import func, select

    from omega.application.analytics.poll_service import AnalyticsPollService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal
    from omega.infrastructure.models import AnalyticsAsset

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            recovered = await AnalyticsPollService.recover_published_assets(session)
            stmt = (
                select(AnalyticsAsset.id)
                .where(
                    AnalyticsAsset.asset_status == "ACTIVE",
                    AnalyticsAsset.next_poll_due_at <= func.now(),
                )
                .limit(50)
            )
            res = await session.execute(stmt)
            due_ids = res.scalars().all()

            processed = 0
            for asset_id in due_ids:
                try:
                    await AnalyticsPollService.execute_asset_poll(
                        session=session,
                        asset_id=asset_id,
                        worker_id="celery-poll-worker",
                    )
                    processed += 1
                except Exception as poll_exc:
                    logger.error("Asset poll failed", asset_id=str(asset_id), error=str(poll_exc))

            await session.commit()
            return {"recovered": len(recovered), "due": len(due_ids), "processed": processed}

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Analytics poll sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "recovered": 0, "due": 0, "processed": 0}


@celery_app.task(name="omega.analytics.fetch_video_batch")
def analytics_fetch_video_batch_task(asset_id: str) -> dict[str, Any]:
    """Execute video asset analytics poll for a single asset."""
    import asyncio
    from uuid import UUID

    from omega.application.analytics.poll_service import AnalyticsPollService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            res = await AnalyticsPollService.execute_asset_poll(
                session=session,
                asset_id=UUID(asset_id),
                worker_id="celery-batch-worker",
            )
            await session.commit()
            return res

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error(
            "Analytics fetch video batch failed", asset_id=asset_id, error=str(exc), exc_info=True
        )
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="omega.analytics.fetch_analytics_report")
def analytics_fetch_analytics_report_task(asset_id: str) -> dict[str, Any]:
    """Fetch a deep Analytics API report for a video asset."""
    import asyncio
    from uuid import UUID

    from omega.application.analytics.poll_service import AnalyticsPollService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            result = await AnalyticsPollService.execute_asset_report(
                session=session,
                asset_id=UUID(asset_id),
                worker_id="celery-report-worker",
            )
            await session.commit()
            return result

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error(
            "Analytics report fetch failed", asset_id=asset_id, error=str(exc), exc_info=True
        )
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="omega.analytics.daily_reconciliation_sweep")
def analytics_daily_reconciliation_sweep_task() -> dict[str, Any]:
    """Finalize elapsed FIRST_24H and FIRST_7D analytics windows."""
    import asyncio

    from omega.application.analytics.poll_service import AnalyticsPollService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            result = await AnalyticsPollService.reconcile_windows(session)
            await session.commit()
            return result

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Analytics daily reconciliation sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "checked_windows": 0, "finalized_windows": 0}


@celery_app.task(name="omega.learning.ingest_observations_sweep")
def learning_ingest_observations_sweep_task() -> dict[str, Any]:
    """Periodic sweep ingesting finalized/revised analytics observations into learning."""
    import asyncio

    from omega.application.learning.ingestion_service import LearningIngestionService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            count = await LearningIngestionService.sweep_and_ingest(session)
            await session.commit()
            return {"ingested_count": count}

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Learning ingest sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "ingested_count": 0}


@celery_app.task(name="omega.learning.evaluate_hypotheses_sweep")
def learning_evaluate_hypotheses_sweep_task() -> dict[str, Any]:
    """Periodic sweep evaluating active hypotheses across channels."""
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy import select

    from omega.application.learning.evaluation_service import EvaluationService
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal
    from omega.infrastructure.models import LearningHypothesisLatestPointer

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(LearningHypothesisLatestPointer.hypothesis_family_id)
                .where(
                    LearningHypothesisLatestPointer.current_status.in_(
                        ["DRAFT", "ACTIVE", "SUPPORTED", "WEAKENED"]
                    )
                )
                .limit(10)
            )
            family_ids = (await session.execute(stmt)).scalars().all()
            now_utc = datetime.now(UTC)
            evaluated = 0
            for family_id in family_ids:
                try:
                    await EvaluationService.evaluate_hypothesis(
                        session=session,
                        hypothesis_family_id=family_id,
                        as_of_utc=now_utc,
                    )
                    evaluated += 1
                except Exception as eval_exc:
                    logger.warning(
                        "Hypothesis evaluation failed",
                        family_id=str(family_id),
                        error=str(eval_exc),
                    )
            await session.commit()
            return {"evaluated_count": evaluated}

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Learning evaluate sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "evaluated_count": 0}


# ── OMEGA-014 Autonomous Loop Worker Tasks ──


@celery_app.task(name="omega.autonomy.tick_sweep")
def autonomy_tick_sweep_task() -> dict[str, Any]:
    """Periodic sweep executing autonomous loop iterations across active loops."""
    import asyncio

    from sqlalchemy import select

    from omega.application.autonomy.dispatch_service import AutonomyDispatchService
    from omega.application.autonomy.loop_service import AutonomyLoopService
    from omega.application.autonomy.observation_service import AutonomyObservationService
    from omega.application.autonomy.plan_service import AutonomyPlanService
    from omega.domain.autonomy import AutonomyLoopState
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal
    from omega.infrastructure.models import (
        AutonomyLoopLatestPointer,
        AutonomyPolicySnapshot,
    )

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            # Query loops in IDLE eligible for a new iteration
            stmt = (
                select(AutonomyLoopLatestPointer.loop_id)
                .where(AutonomyLoopLatestPointer.operational_state == AutonomyLoopState.IDLE.value)
                .limit(5)
            )
            loop_ids = (await session.execute(stmt)).scalars().all()
            ticks_processed = 0

            for target_loop_id in loop_ids:
                try:
                    # 1. Capture Observation
                    obs = await AutonomyObservationService.capture_snapshot(session, target_loop_id)

                    # 2. Query latest policy snapshot
                    stmt_pol = (
                        select(AutonomyPolicySnapshot.id)
                        .where(AutonomyPolicySnapshot.loop_id == target_loop_id)
                        .order_by(AutonomyPolicySnapshot.policy_version.desc())
                        .limit(1)
                    )
                    pol_id = (await session.execute(stmt_pol)).scalar_one()

                    # 3. Allocate Iteration
                    iteration = await AutonomyLoopService.allocate_iteration(
                        session=session,
                        loop_id=target_loop_id,
                        observation_snapshot_id=obs.id,
                        policy_snapshot_id=pol_id,
                    )

                    # 4. Create Action Plan
                    plan = await AutonomyPlanService.create_plan(
                        session=session,
                        iteration_id=iteration.id,
                    )
                    plan_id = plan.id
                    requires_approval = plan.requires_approval

                    await AutonomyLoopService.update_operational_state(
                        session=session,
                        loop_id=target_loop_id,
                        new_state=AutonomyLoopState.PLANNING,
                    )

                    # 5. Dispatch if autonomous
                    if not requires_approval:
                        await AutonomyDispatchService.prepare_and_dispatch(
                            session=session,
                            action_plan_id=plan_id,
                        )
                    else:
                        await AutonomyLoopService.update_operational_state(
                            session=session,
                            loop_id=target_loop_id,
                            new_state=AutonomyLoopState.WAITING_APPROVAL,
                        )

                    await session.commit()
                    ticks_processed += 1
                except Exception as loop_exc:
                    await session.rollback()
                    logger.warning(
                        "Autonomy loop tick iteration skipped or failed",
                        loop_id=str(target_loop_id),
                        error=str(loop_exc),
                    )
            return {"ticks_processed": ticks_processed}

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Autonomy tick sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "ticks_processed": 0}


@celery_app.task(name="omega.autonomy.reconciliation_sweep")
def autonomy_reconciliation_sweep_task() -> dict[str, Any]:
    """Periodic sweep reconciling unconfirmed or timed-out action attempts."""
    import asyncio

    from sqlalchemy import select

    from omega.application.autonomy.reconciliation_service import AutonomyReconciliationService
    from omega.domain.autonomy import ActionAttemptOutcomeStatus
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal
    from omega.infrastructure.models import AutonomyActionAttemptOutcome

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(AutonomyActionAttemptOutcome.action_attempt_id)
                .where(
                    AutonomyActionAttemptOutcome.status
                    == ActionAttemptOutcomeStatus.RECONCILIATION_REQUIRED.value
                )
                .distinct()
                .limit(10)
            )
            attempt_ids = (await session.execute(stmt)).scalars().all()
            reconciled_count = 0

            for att_id in attempt_ids:
                try:
                    await AutonomyReconciliationService.reconcile_attempt(session, att_id)
                    reconciled_count += 1
                except Exception as rec_exc:
                    await session.rollback()
                    logger.warning(
                        "Reconciliation attempt failed", attempt_id=str(att_id), error=str(rec_exc)
                    )

            return {"reconciled_count": reconciled_count}

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Autonomy reconciliation sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "reconciled_count": 0}


@celery_app.task(name="omega.autonomy.approval_expiry_sweep")
def autonomy_approval_expiry_sweep_task() -> dict[str, Any]:
    """Periodic sweep expiring timed-out pending approvals."""
    import asyncio

    from sqlalchemy import func, select

    from omega.application.autonomy.approval_service import AutonomyApprovalService
    from omega.domain.autonomy import ApprovalActorType, ApprovalDecisionValue
    from omega.infrastructure.database import AsyncWorkerSessionLocal as AsyncSessionLocal
    from omega.infrastructure.models import (
        AutonomyApprovalDecision,
        AutonomyApprovalRequest,
    )

    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            # Query requests that expired where no decision exists
            decided_ids = select(AutonomyApprovalDecision.approval_request_id)
            stmt = (
                select(AutonomyApprovalRequest.id)
                .where(
                    AutonomyApprovalRequest.expires_at <= func.now(),
                    AutonomyApprovalRequest.id.not_in(decided_ids),
                )
                .limit(10)
            )
            expired_request_ids = (await session.execute(stmt)).scalars().all()
            expired_count = 0

            for req_id in expired_request_ids:
                try:
                    await AutonomyApprovalService.decide_request(
                        session=session,
                        approval_request_id=req_id,
                        decision=ApprovalDecisionValue.EXPIRED,
                        actor_type=ApprovalActorType.SYSTEM,
                        reviewer_user_id=None,
                        review_reason="Automatically expired by approval expiry sweep.",
                    )
                    await session.commit()
                    expired_count += 1
                except Exception as exp_exc:
                    await session.rollback()
                    logger.warning(
                        "Approval expiry failed", request_id=str(req_id), error=str(exp_exc)
                    )

            return {"expired_count": expired_count}

    try:
        res = asyncio.run(_run())
        return {"status": "success", **res}
    except Exception as exc:
        logger.error("Autonomy approval expiry sweep failed", error=str(exc), exc_info=True)
        return {"status": "error", "expired_count": 0}
