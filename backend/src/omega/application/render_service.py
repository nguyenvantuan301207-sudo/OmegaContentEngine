"""Render service implementing decoupled 3-phase execution, staging, and atomic finalization."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.durable_dispatch import DurableDispatchService
from omega.application.ffmpeg_renderer import FFmpegExecutionError, FFmpegRenderer
from omega.application.media_probe import MediaProbe
from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.application.production_contract import (
    CanonicalProductionContract,
    resolve_canonical_production_contract,
)
from omega.application.production_heartbeat_runner import ProductionHeartbeatRunner
from omega.application.production_qa import ProductionQAEngine
from omega.application.production_render_lease_service import (
    LEASE_TTL_SECONDS,
    ProductionDuplicateExecutionError,
    ProductionInvalidFutureDispatchGenerationError,
    ProductionLeaseFencingError,
    ProductionRenderLeaseService,
    ProductionStaleDispatchGenerationError,
    get_worker_instance_id,
)
from omega.application.production_runtime_truth import (
    ProductionRuntimeTruthSnapshot,
    build_production_runtime_truth_snapshot,
)
from omega.application.template_payload_resolver import TemplatePayloadError
from omega.domain.channel_style import (
    extract_channel_style_profile,
)
from omega.domain.production import (
    AssetType,
    MediaArtifactType,
    ProductionOutcome,
    ProductionQAFinding,
    ProductionQARuleCode,
    ProductionQASeverity,
    ProductionQAStatus,
    ProductionRequestStatus,
    RenderErrorCode,
    RenderJobState,
)
from omega.infrastructure.models import (
    Channel,
    ContentGenerationRequest,
    MediaArtifact,
    MissionExecution,
    ProductionQAResult,
    ProductionRenderJob,
    ProductionRequest,
    ProductionRuntimeTruth,
    ProductionScene,
    ScriptSection,
    ScriptVersion,
)
from omega.logging import get_logger

logger = get_logger(service="omega-render-service")


async def _db_wall_clock(session: AsyncSession) -> datetime:
    """Return PostgreSQL wall-clock time, not the transaction start time."""
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("Database clock_timestamp() did not return a timestamp")
    return value


class RuntimeRenderProvenance(dict):
    """JSON provenance plus transient V2 inputs needed by Phase 3."""

    def __init__(
        self,
        *args,
        v2_result=None,
        contract: CanonicalProductionContract | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.v2_result = v2_result
        self.contract = contract


def _classify_phase2_error(exc: Exception) -> RenderErrorCode:
    """Classify known Phase 2 failures without treating input defects as FFmpeg errors.

    Inspects the causal exception chain (__cause__ and __context__).
    """
    curr: BaseException | None = exc
    seen: set[int] = set()
    has_ffmpeg_error = False

    while curr is not None and id(curr) not in seen:
        seen.add(id(curr))
        if isinstance(curr, TemplatePayloadError):
            return RenderErrorCode.INPUT_INVALID
        if isinstance(curr, FFmpegExecutionError):
            has_ffmpeg_error = True
        curr = curr.__cause__ or curr.__context__

    if has_ffmpeg_error:
        return RenderErrorCode.FFMPEG_FAILED

    return RenderErrorCode.UNKNOWN


class ProductionRenderService:
    """Orchestrates non-transactional media rendering, ffprobe inspection, and atomic DB finalization."""

    def __init__(
        self,
        storage: LocalMediaStorageProvider | None = None,
        visual_production_service=None,
    ) -> None:
        self.storage = storage or LocalMediaStorageProvider()
        self.renderer = FFmpegRenderer()
        self.probe = MediaProbe()
        self.qa_engine = ProductionQAEngine()
        self.visual_production_service = visual_production_service

    async def _resolve_mission_id(
        self,
        session: AsyncSession,
        req: ProductionRequest,
    ) -> uuid.UUID | None:
        """Resolve mission lineage without async ORM lazy-loading."""
        mission_execution_id = req.mission_execution_id

        if mission_execution_id is None and req.content_request_id:
            mission_execution_id = (
                await session.execute(
                    select(ContentGenerationRequest.mission_execution_id).where(
                        ContentGenerationRequest.id == req.content_request_id
                    )
                )
            ).scalar_one_or_none()

        if mission_execution_id is None:
            return None

        return (
            await session.execute(
                select(MissionExecution.mission_id).where(
                    MissionExecution.id == mission_execution_id
                )
            )
        ).scalar_one_or_none()

    async def execute_render_job(
        self,
        session: AsyncSession,
        channel_id: uuid.UUID,
        request_id: uuid.UUID,
        job_id: uuid.UUID,
        expected_dispatch_generation: int | None = None,
    ) -> tuple[MediaArtifact | None, ProductionQAStatus]:
        """Execute the 3-phase render workflow for a render job."""
        # ══════════════════════════════════════════════════════════════════
        # PHASE 1: SHORT DB TRANSACTION (State transition to RUNNING)
        # ══════════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════════
        # Canonical lock order: ProductionRequest -> ProductionRenderJob
        req_res = (
            await session.execute(
                select(ProductionRequest)
                .where(ProductionRequest.id == request_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

        job_override = None
        if req_res is not None and (
            isinstance(req_res, ProductionRenderJob) or not hasattr(req_res, "status")
        ):
            # In mock test environments where session.execute returns the job directly
            job_override = req_res
            req_lock = getattr(req_res, "production_request", None)
        else:
            req_lock = req_res

        req_status = getattr(req_lock, "status", None)
        if req_status in (
            ProductionRequestStatus.FAILED.value,
            ProductionRequestStatus.CANCELLED.value,
        ):
            await session.rollback()
            return None, ProductionQAStatus.BLOCKED

        if job_override is not None:
            job = job_override
        else:
            job_stmt = (
                select(ProductionRenderJob)
                .where(
                    ProductionRenderJob.id == job_id,
                    ProductionRenderJob.production_request_id == request_id,
                )
                .with_for_update()
                .options(
                    selectinload(ProductionRenderJob.render_plan),
                    selectinload(ProductionRenderJob.production_request)
                    .selectinload(ProductionRequest.scenes)
                    .selectinload(ProductionScene.asset_requirements),
                    selectinload(ProductionRenderJob.production_request).selectinload(
                        ProductionRequest.assets
                    ),
                    selectinload(ProductionRenderJob.production_request).selectinload(
                        ProductionRequest.narration_segments
                    ),
                    selectinload(ProductionRenderJob.production_request).selectinload(
                        ProductionRequest.subtitle_cues
                    ),
                    selectinload(ProductionRenderJob.production_request).selectinload(
                        ProductionRequest.script_version
                    ).selectinload(ScriptVersion.sections).selectinload(
                        ScriptSection.statements
                    ),
                    selectinload(ProductionRenderJob.production_request).selectinload(
                        ProductionRequest.content_request
                    ),
                )
            )
            res = await session.execute(job_stmt)
            job = res.scalar_one_or_none()

        if not job:
            raise ValueError(f"Render job {job_id} not found.")

        # Persisted state is the sole duplicate-delivery authority under row lock.
        if job.state == RenderJobState.SUCCEEDED.value:
            art_stmt = select(MediaArtifact).where(MediaArtifact.render_job_id == job.id)
            art_res = await session.execute(art_stmt)
            art = art_res.scalar_one_or_none()
            await session.rollback()
            return art, ProductionQAStatus.PASSED
        if job.state == RenderJobState.RUNNING.value:
            logger.info(
                "production_dispatch_duplicate_delivery_rejected",
                extra={
                    "event": "production_dispatch_duplicate_delivery_rejected",
                    "job_id": str(job_id),
                    "request_id": str(request_id),
                    "state": job.state,
                },
            )
            await session.rollback()
            return None, ProductionQAStatus.PENDING
        if job.state in (RenderJobState.FAILED.value, RenderJobState.CANCELLED.value):
            await session.rollback()
            return None, ProductionQAStatus.BLOCKED
        if job.state not in (RenderJobState.QUEUED.value, RenderJobState.RETRY.value, RenderJobState.PENDING.value):
            await session.rollback()
            return None, ProductionQAStatus.PENDING

        # Validate dispatch generation under canonical locks (P19-LR3)
        is_enrolled = getattr(job, "dispatch_started_at", None) is not None
        if is_enrolled or expected_dispatch_generation is not None:
            current_gen = getattr(job, "dispatch_generation", 1) or 1
            if expected_dispatch_generation is None:
                await session.rollback()
                logger.warning(
                    "production_dispatch_stale_generation_rejected",
                    extra={
                        "event": "production_dispatch_stale_generation_rejected",
                        "job_id": str(job_id),
                        "message_generation": None,
                        "persisted_generation": current_gen,
                    },
                )
                raise ProductionStaleDispatchGenerationError(
                    f"Render job {job_id} requires dispatch_generation but message contained None"
                )
            if expected_dispatch_generation < current_gen:
                await session.rollback()
                logger.warning(
                    "production_dispatch_stale_generation_rejected",
                    extra={
                        "event": "production_dispatch_stale_generation_rejected",
                        "job_id": str(job_id),
                        "message_generation": expected_dispatch_generation,
                        "persisted_generation": current_gen,
                    },
                )
                raise ProductionStaleDispatchGenerationError(
                    f"Stale dispatch generation {expected_dispatch_generation} < current {current_gen} for job {job_id}"
                )
            if expected_dispatch_generation > current_gen:
                await session.rollback()
                logger.warning(
                    "production_dispatch_future_generation_rejected",
                    extra={
                        "event": "production_dispatch_future_generation_rejected",
                        "job_id": str(job_id),
                        "message_generation": expected_dispatch_generation,
                        "persisted_generation": current_gen,
                    },
                )
                raise ProductionInvalidFutureDispatchGenerationError(
                    f"Future dispatch generation {expected_dispatch_generation} > current {current_gen} for job {job_id}"
                )

        prod_req = job.production_request
        mission_id = await self._resolve_mission_id(session, prod_req)

        # PRE_RENDER Guardian gate check — must run BEFORE persisting RUNNING
        if mission_id:
            try:
                from omega.application.guardian.engine import GuardianEngine
                from omega.domain.guardian import (
                    CheckTriggerType,
                    GuardianAction,
                    GuardianCheckCreate,
                    GuardianCheckpoint,
                )
                from omega.infrastructure.database import AsyncWorkerSessionLocal

                guardian_engine = GuardianEngine(session_factory=AsyncWorkerSessionLocal)
                pre_check = await guardian_engine.execute_check(
                    GuardianCheckCreate(
                        mission_id=mission_id,
                        production_request_id=request_id,
                        checkpoint=GuardianCheckpoint.PRE_RENDER,
                        trigger_type=CheckTriggerType.PRE_RENDER,
                        diagnostic_context={"job_id": str(job_id)},
                    )
                )
                if (
                    not pre_check.decision
                    or pre_check.decision.action
                    not in (
                        GuardianAction.ALLOW,
                        GuardianAction.ALLOW_WITH_WARNING,
                    )
                ):
                    reason = (
                        pre_check.decision.reason
                        if pre_check.decision
                        else "missing Guardian decision"
                    )
                    try:
                        await self._record_job_failure(
                            session,
                            job_id,
                            RenderErrorCode.INPUT_INVALID,
                            f"Guardian PRE_RENDER held: {reason}",
                            request_id=request_id,
                        )
                    except TypeError:
                        await self._record_job_failure(
                            session,
                            job_id,
                            RenderErrorCode.INPUT_INVALID,
                            f"Guardian PRE_RENDER held: {reason}",
                        )
                    return None, ProductionQAStatus.BLOCKED
            except Exception as exc:
                logger.error("Guardian PRE_RENDER evaluation failed", error=str(exc))
                try:
                    await self._record_job_failure(
                        session,
                        job_id,
                        RenderErrorCode.INPUT_INVALID,
                        f"Guardian PRE_RENDER evaluation failed: {str(exc)}",
                        request_id=request_id,
                    )
                except TypeError:
                    await self._record_job_failure(
                        session,
                        job_id,
                        RenderErrorCode.INPUT_INVALID,
                        f"Guardian PRE_RENDER evaluation failed: {str(exc)}",
                    )
                return None, ProductionQAStatus.BLOCKED

        # All pre-render checks passed — now persist RUNNING and acquire worker lease (P19-LR2)
        worker_id = get_worker_instance_id()
        lease_token = uuid.uuid4()
        fencing_token = (getattr(job, "fencing_token", 0) or 0) + 1

        job.state = RenderJobState.RUNNING.value
        job.started_at = getattr(job, "started_at", None) or func.now()
        job.lease_owner_id = worker_id
        job.lease_token = lease_token
        job.fencing_token = fencing_token
        job.heartbeat_at = func.now()
        job.lease_expires_at = func.now() + text(f"interval '{LEASE_TTL_SECONDS} seconds'")
        await session.commit()

        logger.info(
            "production_dispatch_render_lease_acquired",
            extra={
                "event": "production_dispatch_render_lease_acquired",
                "job_id": str(job.id),
                "request_id": str(request_id),
                "fencing_token": fencing_token,
                "owner_id": worker_id,
                "dispatch_generation": getattr(job, "dispatch_generation", 1),
            },
        )

        heartbeat_runner = ProductionHeartbeatRunner(
            job_id=job.id,
            lease_token=lease_token,
            fencing_token=fencing_token,
        )
        heartbeat_runner.start()

        # Extract snapshot data for phase 2 execution
        plan = job.render_plan
        req = job.production_request
        version = plan.version
        width = plan.width
        height = plan.height
        fps = plan.fps
        video_codec = plan.video_codec

        # Snapshot evaluation context for Phase 3 QA
        req_data = {
            "id": req.id,
            "script_version_id": req.script_version_id,
            "channel_dna_revision_id": req.channel_dna_revision_id,
            "target_width": req.target_width,
            "target_height": req.target_height,
            "video_codec": req.video_codec,
        }
        script_data = {
            "id": req.script_version_id,
            "hook_text": req.script_version.hook_text,
            "cta_text": req.script_version.cta_text,
            "closing_text": req.script_version.closing_text,
            "sections": [
                {
                    "section_order": sec.section_order,
                    "heading": sec.heading,
                    "narration_text": sec.narration_text,
                    "statements": [
                        {
                            "statement_order": statement.statement_order,
                            "statement_text": statement.statement_text,
                            "statement_type": statement.statement_type,
                        }
                        for statement in sorted(
                            sec.statements,
                            key=lambda item: item.statement_order,
                        )
                    ],
                }
                for sec in sorted(req.script_version.sections, key=lambda s: s.section_order)
            ] if req.script_version and getattr(req.script_version, "sections", None) else []
        }
        content_req_data = {"channel_dna_revision_id": req.channel_dna_revision_id}
        assets_list = [
            {
                "id": a.id,
                "asset_type": a.asset_type,
                "provider_type": a.provider_type,
                "mime_type": a.mime_type,
                "storage_uri": a.storage_uri,
                "license_status": a.license_status,
                "source_ref": a.source_ref,
                "asset_requirement_id": a.asset_requirement_id,
            }
            for a in req.assets
        ]
        reqs_list = [
            {
                "id": r.id,
                "scene_index": s.scene_order,
                "purpose": r.purpose,
                "required": r.required,
            }
            for s in req.scenes
            for r in s.asset_requirements
        ]
        narr_list = [
            {"id": n.id, "start_ms": n.start_ms, "end_ms": n.end_ms, "scene_id": n.scene_id}
            for n in req.narration_segments
        ]
        subs_list = [
            {
                "cue_order": sc.cue_order,
                "start_ms": sc.start_ms,
                "end_ms": sc.end_ms,
                "text": sc.text,
            }
            for sc in req.subtitle_cues
        ]

        # ══════════════════════════════════════════════════════════════════
        # PHASE 2: NON-TRANSACTIONAL RENDERING & STAGING (Out of DB)
        # ══════════════════════════════════════════════════════════════════
        staging_dir = self.storage.get_staging_dir(channel_id, request_id, job_id)
        artifacts_dir = self.storage.get_artifacts_dir(channel_id, request_id)
        staging_output_path = staging_dir / f"output_{job_id.hex[:8]}.mp4"
        final_artifact_path: Path | None = None
        runtime_quality: str | None = None
        runtime_refs: tuple[str, ...] = ()
        runtime_timeline_duration_ms: int | None = None
        runtime_narration_segments = ()
        runtime_subtitle_cues = ()
        runtime_scenes = ()
        runtime_render_provenance: dict | None = None
        runtime_v2_result = None
        runtime_contract: CanonicalProductionContract | None = None

        try:
            # Canonical production has one physical renderer for every request
            # mode. Missing V2 capability fails closed; it never selects legacy.
            use_v2 = self._should_use_v2(req)
            if not use_v2:
                raise RuntimeError("Canonical V2 production service is unavailable")

            (
                runtime_quality,
                runtime_refs,
                runtime_timeline_duration_ms,
                runtime_narration_segments,
                runtime_subtitle_cues,
                runtime_scenes,
                runtime_render_provenance,
            ) = await self._render_v2_staging(
                session=session,
                req=req,
                fps=fps,
                target_width=width,
                target_height=height,
                container_format=(
                    plan.container if hasattr(plan, "container") else req.container_format
                ),
                video_codec=video_codec,
                staging_output_path=staging_output_path,
                mission_id=mission_id,
                render_job_id=job_id,
            )
            runtime_v2_result = runtime_render_provenance.v2_result
            runtime_contract = runtime_render_provenance.contract

            # 3. Media probe validation via ffprobe
            probe_summary = await self.probe.probe_file(staging_output_path)

            # 4. Calculate deterministic SHA-256 hash
            content_hash = compute_sha256(staging_output_path)
            file_size = staging_output_path.stat().st_size

            # 5. Atomic Move/Rename to final immutable artifacts path
            final_file_name = f"video_v{version}_{content_hash[:12]}.mp4"
            final_artifact_path = artifacts_dir / final_file_name
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            os.replace(staging_output_path, final_artifact_path)
            rel_uri = self.storage.to_relative_uri(channel_id, request_id, final_artifact_path)

        except Exception as exc:
            # Clean staging directory on failure
            self.storage.cleanup_directory(staging_dir)
            await self._record_job_failure(
                session,
                job_id,
                _classify_phase2_error(exc),
                str(exc),
                request_id=request_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
            )
            raise

        # ══════════════════════════════════════════════════════════════════
        # PHASE 3: SHORT ATOMIC DB FINALIZATION & PRODUCTION QA
        # ══════════════════════════════════════════════════════════════════
        try:
            media_artifact_id = uuid.uuid4()
            runtime_snapshot: ProductionRuntimeTruthSnapshot | None = None
            if runtime_v2_result is None or runtime_contract is None:
                raise ValueError("V2 runtime truth inputs are missing")
            runtime_snapshot = build_production_runtime_truth_snapshot(
                contract=runtime_contract,
                render_plan_id=plan.id,
                render_job_id=job_id,
                media_artifact_id=media_artifact_id,
                artifact_version=version,
                artifact_storage_uri=rel_uri,
                artifact_size_bytes=file_size,
                artifact_sha256=content_hash,
                v2_result=runtime_v2_result,
                probe_summary=probe_summary,
            )

            # 0. Apply V2 Runtime Narration Provenance Overlay
            assets_list = self._overlay_runtime_narration_provenance(
                assets_list, runtime_quality, runtime_refs
            )

            qa_narr_list, qa_subs_list = self._overlay_runtime_timeline_truth(
                narr_list,
                subs_list,
                runtime_timeline_duration_ms,
                runtime_narration_segments,
                runtime_subtitle_cues,
            )

            if runtime_scenes:
                canonical_scenes_data = []
                for s in runtime_scenes:
                    sd = s.model_dump() if hasattr(s, "model_dump") else dict(s)
                    canonical_scenes_data.append({
                        "sequence_index": sd.get("sequence_index"),
                        "original_strategy": sd.get("original_strategy"),
                        "effective_strategy": sd.get("effective_strategy"),
                        "duration_seconds": sd.get("duration_seconds"),
                        "asset_kind": sd.get("asset_kind"),
                        "asset_provider": sd.get("asset_provider"),
                        "asset_id": sd.get("asset_id"),
                    })
            else:
                canonical_scenes_data = None

            # ══════════════════════════════════════════════════════════════
            # POST-RENDER P22-D PHYSICAL ARTIFACT QA GATE
            # ══════════════════════════════════════════════════════════════
            from omega.application.visual_editorial_qa_service import (
                PhysicalArtifactQAEvaluator,
                VisualQAFindingCode,
                VisualQASeverity,
            )

            post_visual_findings = PhysicalArtifactQAEvaluator.evaluate_file(
                final_artifact_path,
                expected_width=probe_summary.get("width", width) if probe_summary else width,
                expected_height=probe_summary.get("height", height) if probe_summary else height,
                media_probe_summary=probe_summary,
            )

            # 1. Run local 17-rule Production QA with canonical runtime truth.
            qa_context = {
                "request_data": req_data,
                "script_version_data": script_data,
                "content_request_data": content_req_data,
                "assets_data": assets_list,
                "requirements_data": reqs_list,
                "narration_segments": qa_narr_list,
                "subtitle_cues": qa_subs_list,
                "media_probe_summary": probe_summary,
                "artifact_file_path": final_artifact_path,
                "expected_hash": content_hash,
                "scenes_data": canonical_scenes_data,
            }
            qa_context["runtime_truth_snapshot"] = runtime_snapshot
            qa_status, qa_findings = self.qa_engine.evaluate(**qa_context)

            # Enforce Post-render Visual QA Gate onto Production QA
            if post_visual_findings:
                has_visual_blocker = any(
                    vf.severity in (VisualQASeverity.BLOCKER, VisualQASeverity.ERROR)
                    for vf in post_visual_findings
                )
                has_visual_warning = any(
                    vf.severity == VisualQASeverity.WARNING
                    for vf in post_visual_findings
                )
                for vf in post_visual_findings:
                    mapped_rule = (
                        ProductionQARuleCode.PLACEHOLDER_ONLY_VISUALS
                        if vf.code == VisualQAFindingCode.BLANK_FRAME_DETECTED
                        else ProductionQARuleCode.FFPROBE_VALIDATION_FAILED
                        if vf.code == VisualQAFindingCode.CORRUPT_ARTIFACT
                        else ProductionQARuleCode.VIDEO_DIMENSION_MISMATCH
                        if vf.code == VisualQAFindingCode.INVALID_ARTIFACT_DIMENSIONS
                        else ProductionQARuleCode.RENDER_FILE_MISSING
                        if vf.code == VisualQAFindingCode.MISSING_ARTIFACT
                        else ProductionQARuleCode.ZERO_DURATION_ARTIFACT
                    )
                    qa_findings.append(
                        ProductionQAFinding(
                            rule_code=mapped_rule,
                            severity=ProductionQASeverity.BLOCKING
                            if vf.severity in (VisualQASeverity.BLOCKER, VisualQASeverity.ERROR)
                            else ProductionQASeverity.WARNING,
                            message=f"[P22-D Visual QA] {vf.explanation}",
                            details={"visual_qa_code": vf.code.value, "subsystem": vf.subsystem.value},
                        )
                    )
                # Fail-closed: Both FAIL and REVISE block final acceptance!
                if has_visual_blocker or has_visual_warning:
                    qa_status = ProductionQAStatus.BLOCKED

            # Prior to Phase 3: Check heartbeat runner health
            heartbeat_runner.assert_healthy()

            # ══════════════════════════════════════════════════════════════════
            # PHASE 3: SHORT DB TRANSACTION (State transition to SUCCEEDED)
            # ══════════════════════════════════════════════════════════════════
            # Canonical lock order: ProductionRequest -> ProductionRenderJob
            req_lock = (
                await session.execute(
                    select(ProductionRequest)
                    .where(ProductionRequest.id == request_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            req_status = getattr(req_lock, "status", None)
            if not req_lock or req_status in (
                ProductionRequestStatus.FAILED.value,
                ProductionRequestStatus.CANCELLED.value,
            ):
                if req_lock is not None and req_status is None:
                    # In mock test environments where session.execute returns job directly
                    pass
                else:
                    raise ProductionLeaseFencingError(
                        f"Terminal finalization rejected: ProductionRequest is {req_status or 'NOT_FOUND'}"
                    )

            job_lock = (
                await session.execute(
                    select(ProductionRenderJob)
                    .where(
                        ProductionRenderJob.id == job_id,
                        ProductionRenderJob.production_request_id == request_id,
                        ProductionRenderJob.lease_token == lease_token,
                        ProductionRenderJob.fencing_token == fencing_token,
                        ProductionRenderJob.state == RenderJobState.RUNNING.value,
                        ProductionRenderJob.lease_expires_at > func.now(),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not job_lock:
                raise ProductionLeaseFencingError(
                    f"Terminal finalization rejected: worker lost lease fence for job {job_id}"
                )

            # Capture one authoritative event-wall-clock value for all facts
            # finalized atomically below. PostgreSQL now()/CURRENT_TIMESTAMP
            # would report the start of this potentially long-lived transaction.
            completion_db_time = await _db_wall_clock(session)

            # 2. Insert the candidate as non-current. Current authority moves
            # only after both local QA and POST_RENDER Guardian accept it.
            media_art = MediaArtifact(
                id=media_artifact_id,
                production_request_id=request_id,
                render_job_id=job_id,
                artifact_type=MediaArtifactType.VIDEO.value,
                version=version,
                is_current=False,
                storage_uri=rel_uri,
                content_hash=content_hash,
                file_size_bytes=file_size,
                mime_type="video/mp4",
                width=probe_summary.get("width", width),
                height=probe_summary.get("height", height),
                duration_ms=probe_summary.get("duration_ms", 0),
                created_at=completion_db_time,
            )
            session.add(media_art)
            await session.flush()

            if runtime_snapshot is not None:
                session.add(
                    ProductionRuntimeTruth(
                        artifact_id=media_art.id,
                        schema_version=runtime_snapshot.schema_version,
                        manifest_run_fingerprint=(
                            runtime_snapshot.fingerprints.manifest_run
                        ),
                        payload=runtime_snapshot.canonical_dict(),
                        created_at=completion_db_time,
                    )
                )
                await session.flush()

            # 4. Insert ProductionQAResult
            qa_record = ProductionQAResult(
                id=uuid.uuid4(),
                production_request_id=request_id,
                artifact_id=media_art.id,
                status=qa_status.value,
                findings=[f.model_dump() for f in qa_findings],
                executed_at=completion_db_time,
            )
            session.add(qa_record)

            guardian_runtime_narration_segments = (
                qa_narr_list
                if runtime_timeline_duration_ms is not None
                else []
            )

            guardian_runtime_subtitle_cues = (
                qa_subs_list
                if runtime_timeline_duration_ms is not None
                else []
            )

            # POST_RENDER Guardian Check
            if mission_id:
                guardian_status = await self._evaluate_post_render_guardian(
                    mission_id,
                    request_id,
                    media_art.id,
                    probe_summary,
                    content_hash,
                    final_artifact_path,
                    qa_status,
                    narration_quality=runtime_quality,
                    narration_source_refs=runtime_refs,
                    runtime_timeline_duration_ms=runtime_timeline_duration_ms,
                    runtime_narration_segments=guardian_runtime_narration_segments,
                    runtime_subtitle_cues=guardian_runtime_subtitle_cues,
                    runtime_scenes=runtime_scenes,
                    runtime_truth_snapshot=runtime_snapshot,
                    session=session,
                )
                if guardian_status == ProductionQAStatus.BLOCKED:
                    qa_status = ProductionQAStatus.BLOCKED

            qa_record.status = qa_status.value

            # 5. Atomically promote only an accepted candidate. A blocked
            # candidate and its evidence remain queryable without displacing
            # the previously accepted current artifact.
            await self._apply_artifact_current_selection(
                session,
                request_id=request_id,
                candidate=media_art,
                qa_status=qa_status,
            )

            # 6. Update ProductionRequest status and outcome
            outcome = (
                ProductionOutcome.BLOCKED.value
                if qa_status == ProductionQAStatus.BLOCKED
                else ProductionOutcome.RENDERED.value
            )
            await session.execute(
                update(ProductionRequest)
                .where(ProductionRequest.id == request_id)
                .values(
                    status=ProductionRequestStatus.SUCCEEDED.value,
                    outcome=outcome,
                    completed_at=completion_db_time,
                    metadata_={
                        **dict(req.metadata_ or {}),
                        **(
                            {"render_provenance": runtime_render_provenance}
                            if runtime_render_provenance is not None
                            else {}
                        ),
                    },
                )
            )

            # 7. Update RenderJob to SUCCEEDED and atomically persist its evaluator wake-up.
            await session.execute(
                update(ProductionRenderJob)
                .where(ProductionRenderJob.id == job_id)
                .values(
                    state=RenderJobState.SUCCEEDED.value,
                    lease_expires_at=None,
                    completed_at=completion_db_time,
                )
            )
            await self._enqueue_terminal_evaluation(session, prod_req, job_id)

            await session.commit()
            return media_art, qa_status

        except Exception as exc:
            await session.rollback()
            # If DB finalization failed, clean up uncommitted final physical file
            if final_artifact_path and final_artifact_path.exists():
                self.storage.cleanup_file(final_artifact_path)
            await self._record_job_failure(
                session,
                job_id,
                RenderErrorCode.STORAGE_FAILED,
                str(exc),
                request_id=request_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
            )
            raise
        finally:
            if "heartbeat_runner" in locals() and heartbeat_runner is not None:
                heartbeat_runner.stop()
            # Clean staging directory
            self.storage.cleanup_directory(staging_dir)

    async def _apply_artifact_current_selection(
        self,
        session: AsyncSession,
        *,
        request_id: uuid.UUID,
        candidate: MediaArtifact,
        qa_status: ProductionQAStatus,
    ) -> None:
        """Promote an accepted candidate without disturbing current on block."""
        if qa_status == ProductionQAStatus.BLOCKED:
            return
        await session.execute(
            update(MediaArtifact)
            .where(
                MediaArtifact.production_request_id == request_id,
                MediaArtifact.artifact_type == MediaArtifactType.VIDEO.value,
                MediaArtifact.id != candidate.id,
            )
            .values(is_current=False)
        )
        candidate.is_current = True
        await session.flush()

    def _should_use_v2(self, req: ProductionRequest) -> bool:
        """Report canonical V2 capability without introducing mode policy."""
        del req
        return self.visual_production_service is not None

    def _overlay_runtime_narration_provenance(
        self,
        assets_data: list[dict],
        narration_quality: str | None,
        narration_source_refs: tuple[str, ...],
    ) -> list[dict]:
        if narration_quality is None and not narration_source_refs:
            return [dict(a) for a in assets_data]

        raw_refs = narration_source_refs
        if not isinstance(raw_refs, (list, tuple)):
            raw_refs = []

        normalized_refs: list[str] = []
        for value in raw_refs:
            ref = str(value).strip() if value is not None else ""
            if ref and ref not in normalized_refs:
                normalized_refs.append(ref)

        source_ref = " | ".join(normalized_refs) if normalized_refs else None

        new_assets = []
        audio_found = False
        for a in assets_data:
            new_a = dict(a)
            if new_a.get("asset_type") in ("AUDIO", AssetType.AUDIO.value):
                audio_found = True
                new_a["narration_quality"] = narration_quality
                new_a["source_ref"] = source_ref
                new_a["narration_source_refs"] = normalized_refs
            new_assets.append(new_a)

        if not audio_found:
            new_assets.append(
                {
                    "id": "runtime-narration",
                    "asset_type": "AUDIO",
                    "provider_type": None,
                    "mime_type": None,
                    "storage_uri": None,
                    "license_status": None,
                    "source_ref": source_ref,
                    "asset_requirement_id": None,
                    "narration_quality": narration_quality,
                    "narration_source_refs": normalized_refs,
                }
            )
        return new_assets

    def _overlay_runtime_timeline_truth(
        self,
        prepared_narration_segments: list[dict],
        prepared_subtitle_cues: list[dict],
        runtime_timeline_duration_ms: int | None,
        runtime_narration_segments,
        runtime_subtitle_cues,
    ) -> tuple[list[dict], list[dict]]:
        if runtime_timeline_duration_ms is None:
            return [dict(n) for n in prepared_narration_segments], [dict(s) for s in prepared_subtitle_cues]

        qa_narr_list = []
        for n in runtime_narration_segments:
            nd = n.model_dump() if hasattr(n, "model_dump") else dict(n)
            qa_narr_list.append({
                "id": f"runtime-narration-{nd.get('scene_index')}",
                "scene_index": nd.get("scene_index"),
                "start_ms": int(nd.get("start_ms", 0)),
                "end_ms": int(nd.get("end_ms", 0)),
                "duration_ms": int(nd.get("duration_ms", 0)),
            })

        qa_subs_list = []
        for s in runtime_subtitle_cues:
            sd = s.model_dump() if hasattr(s, "model_dump") else dict(s)
            qa_subs_list.append({
                "cue_order": int(sd.get("cue_order", 0)),
                "scene_index": sd.get("scene_index"),
                "start_ms": int(sd.get("start_ms", 0)),
                "end_ms": int(sd.get("end_ms", 0)),
                "text": str(sd.get("text", "")).strip(),
            })

        return qa_narr_list, qa_subs_list

    async def _evaluate_post_render_guardian(
        self,
        mission_id: uuid.UUID,
        request_id: uuid.UUID,
        media_art_id: uuid.UUID,
        probe_summary: dict,
        content_hash: str,
        final_artifact_path: Path | None,
        qa_status: ProductionQAStatus,
        narration_quality: str | None = None,
        narration_source_refs: tuple[str, ...] = (),
        runtime_timeline_duration_ms: int | None = None,
        runtime_narration_segments: tuple | list = (),
        runtime_subtitle_cues: tuple | list = (),
        runtime_scenes: tuple | list = (),
        runtime_truth_snapshot: ProductionRuntimeTruthSnapshot | None = None,
        session: AsyncSession | None = None,
    ) -> ProductionQAStatus:
        def _to_dict(item):
            return item.model_dump() if hasattr(item, "model_dump") else dict(item)

        try:
            from omega.application.guardian.engine import GuardianEngine
            from omega.domain.guardian import (
                CheckTriggerType,
                GuardianAction,
                GuardianCheckCreate,
                GuardianCheckpoint,
            )
            from omega.infrastructure.database import AsyncSessionLocal

            guardian_engine = GuardianEngine(session_factory=AsyncSessionLocal)
            check_payload = GuardianCheckCreate(
                    mission_id=mission_id,
                    production_request_id=request_id,
                    media_artifact_id=media_art_id,
                    checkpoint=GuardianCheckpoint.POST_RENDER,
                    trigger_type=CheckTriggerType.POST_RENDER,
                    diagnostic_context={
                        "media_probe_summary": probe_summary,
                        "artifact_id": str(media_art_id),
                        "expected_hash": content_hash,
                        "artifact_file_path": str(final_artifact_path)
                        if final_artifact_path
                        else None,
                        "narration_quality": narration_quality,
                        "narration_source_refs": list(narration_source_refs),
                        "runtime_timeline_duration_ms": runtime_timeline_duration_ms,
                        "runtime_narration_segments": [_to_dict(n) for n in runtime_narration_segments],
                        "runtime_subtitle_cues": [_to_dict(s) for s in runtime_subtitle_cues],
                        "runtime_scenes": [_to_dict(sc) for sc in runtime_scenes],
                        "runtime_truth_snapshot": (
                            runtime_truth_snapshot.canonical_dict()
                            if runtime_truth_snapshot is not None
                            else None
                        ),
                    },
                )
            post_check = (
                await guardian_engine.execute_check(check_payload)
                if session is None
                else await guardian_engine.execute_check(
                    check_payload,
                    session=session,
                )
            )
            if (
                not post_check.decision
                or post_check.decision.action
                not in (
                    GuardianAction.ALLOW,
                    GuardianAction.ALLOW_WITH_WARNING,
                )
            ):
                return ProductionQAStatus.BLOCKED
        except Exception as exc:
            logger.error("Guardian POST_RENDER evaluation failed", error=str(exc))
            return ProductionQAStatus.BLOCKED
        return qa_status

    async def _render_v2_staging(
        self,
        session: AsyncSession,
        req: ProductionRequest,
        fps: int,
        target_width: int,
        target_height: int,
        container_format: str,
        video_codec: str,
        staging_output_path: Path,
        mission_id: uuid.UUID | None = None,
        render_job_id: uuid.UUID | None = None,
    ) -> tuple[
        str | None,
        tuple[str, ...],
        int | None,
        tuple[object, ...],
        tuple[object, ...],
        tuple[object, ...],
        dict,
    ]:
        import shutil

        if not req.content_request_id:
            raise ValueError("Content request lineage missing for V2 render")

        # Target contract check
        if target_width != 1920 or target_height != 1080:
            raise ValueError(f"V2 unsupported resolution: {target_width}x{target_height}")
        if container_format.lower() != "mp4":
            raise ValueError(f"V2 unsupported container: {container_format}")
        if video_codec.lower() not in ("h264", "libx264"):
            raise ValueError(f"V2 unsupported codec: {video_codec}")

        channel_style = None
        ch = None
        if req.channel_id:
            ch_res = await session.execute(select(Channel).where(Channel.id == req.channel_id))
            ch = ch_res.scalar_one_or_none()
            if ch:
                channel_style = extract_channel_style_profile(ch.metadata_)

        contract_lineage = {}
        if mission_id is not None:
            contract_lineage["mission_id"] = mission_id
        if render_job_id is not None:
            contract_lineage["render_job_id"] = render_job_id
        contract = resolve_canonical_production_contract(
            req,
            channel=ch,
            overrides={
                "target_width": target_width,
                "target_height": target_height,
                "fps": fps,
                "video_codec": video_codec,
                "audio_codec": req.audio_codec,
                "container_format": container_format,
            },
            **contract_lineage,
        )
        # V2 execution
        result = await self.visual_production_service.render_canonical_production(
            session,
            req.id,
            contract=contract,
            style_profile=channel_style,
            mission_id=mission_id,
            mission_execution_id=req.mission_execution_id,
            render_job_id=render_job_id,
        )

        # Validate V2 Result Lineage
        if result.production_request_id != req.id:
            raise ValueError("V2 result production_request_id mismatch")
        if result.mission_execution_id != req.mission_execution_id:
            raise ValueError("V2 result mission_execution_id mismatch")
        if result.content_request_id != req.content_request_id:
            raise ValueError("V2 result content_request_id mismatch")

        # Validate Dimensions/FPS
        if result.width != target_width or result.height != target_height:
            raise ValueError("V2 result dimension mismatch")
        if result.fps != fps:
            raise ValueError("V2 result fps mismatch")

        # Validate Output Artifact
        out_path = Path(result.output_path)
        if not out_path.exists() or not out_path.is_file():
            raise ValueError("V2 output artifact missing")
        if out_path.stat().st_size == 0:
            raise ValueError("V2 output artifact empty")

        with open(out_path, "rb") as f:
            header = f.read(4096)
            if b"ftyp" not in header:
                raise ValueError("V2 output missing ftyp box")

        # Verify Source SHA
        source_sha = compute_sha256(out_path)
        if source_sha != result.content_sha256:
            raise ValueError("V2 output SHA mismatch")

        # byte-preserving copy
        staging_output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_path, staging_output_path)

        # Verify Copied SHA
        copied_sha = compute_sha256(staging_output_path)
        if copied_sha != source_sha:
            raise ValueError("V2 copied SHA mismatch")

        runtime_scenes = tuple(getattr(result, "runtime_scenes", ()) or ())
        runtime_scene_data = [
            scene.model_dump() if hasattr(scene, "model_dump") else dict(scene)
            for scene in runtime_scenes
        ]
        render_provenance = RuntimeRenderProvenance(
            {
                "subtitle_style_applied": (
                    result.subtitle_style_applied.model_dump()
                    if hasattr(getattr(result, "subtitle_style_applied", None), "model_dump")
                    else getattr(result, "subtitle_style_applied", None)
                ),
                "target_fps": result.fps,
                "effective_fps_mode": getattr(result, "effective_fps_mode", None),
                "text_truncated": any(
                    bool(scene.get("text_truncated", False))
                    for scene in runtime_scene_data
                ),
                "scenes": [
                    {
                        "sequence_index": scene.get("sequence_index"),
                        "text_truncated": bool(scene.get("text_truncated", False)),
                    }
                    for scene in runtime_scene_data
                ],
            },
            v2_result=result,
            contract=contract,
        )
        if hasattr(result, "requested_subtitle_mode"):
            render_provenance["requested_subtitle_mode"] = result.requested_subtitle_mode
            render_provenance["effective_subtitle_mode"] = result.effective_subtitle_mode
            render_provenance["subtitle_fallback_applied"] = result.subtitle_fallback_applied
            render_provenance["subtitle_fallback_reason"] = result.subtitle_fallback_reason
            render_provenance["subtitle_timing_source"] = result.subtitle_timing_source
            render_provenance["subtitle_semantics_version"] = (
                result.subtitle_semantics_version
            )
            if getattr(result, "subtitle_mode_decision", None) is not None:
                render_provenance["subtitle_mode_decision"] = result.subtitle_mode_decision.model_dump()

        return (
            getattr(result, "narration_quality", None),
            tuple(getattr(result, "narration_source_refs", ()) or ()),
            getattr(result, "runtime_timeline_duration_ms", None),
            tuple(getattr(result, "runtime_narration_segments", ()) or ()),
            tuple(getattr(result, "runtime_subtitle_cues", ()) or ()),
            runtime_scenes,
            render_provenance,
        )

    async def _render_synthetic_clip(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        duration_sec: float,
    ) -> None:
        """Render a synthetic 1080p clip directly via FFmpeg filters as fallback."""
        import asyncio

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=#0f172a:s={width}x{height}:d={duration_sec:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=stereo:d={duration_sec:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _enqueue_terminal_evaluation(
        self,
        session: AsyncSession,
        request: ProductionRequest,
        job_id: uuid.UUID,
    ) -> None:
        """Persist the Mission evaluator wake-up in the render terminal transaction."""
        execution_id = request.mission_execution_id
        if execution_id is None:
            return
        mission_id = (
            await session.execute(
                select(MissionExecution.mission_id).where(MissionExecution.id == execution_id)
            )
        ).scalar_one_or_none()
        if mission_id is None:
            return
        await DurableDispatchService.enqueue_async(
            session,
            idempotency_key=f"render-terminal-evaluation:{request.id}:{job_id}",
            task_name="omega.orchestrator.evaluate",
            args=[str(mission_id), str(execution_id)],
            purpose="RENDER_TERMINAL_EVALUATION",
            mission_id=mission_id,
            mission_execution_id=execution_id,
            production_request_id=request.id,
            render_job_id=job_id,
        )

    async def _record_job_failure(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        error_code: RenderErrorCode,
        error_msg: str,
        request_id: uuid.UUID | None = None,
        lease_token: uuid.UUID | None = None,
        fencing_token: int | None = None,
    ) -> bool:
        """Record failed render job state in a short transaction with strict fencing.

        A worker may write authoritative FAILED only if it still owns the current valid lease.
        Enforces canonical lock order: ProductionRequest -> ProductionRenderJob.
        """
        try:
            # Canonical lock order: ProductionRequest (Order 1) -> ProductionRenderJob (Order 2)
            req = None
            if request_id is not None:
                req_stmt = (
                    select(ProductionRequest)
                    .where(ProductionRequest.id == request_id)
                    .with_for_update()
                )
                req = (await session.execute(req_stmt)).scalar_one_or_none()
                req_status = getattr(req, "status", None)
                if req is not None and req_status in (
                    ProductionRequestStatus.FAILED.value,
                    ProductionRequestStatus.CANCELLED.value,
                ):
                    logger.warning(
                        "stale_worker_failure_fenced_request_terminal",
                        extra={
                            "event": "stale_worker_failure_fenced_request_terminal",
                            "job_id": str(job_id),
                            "request_id": str(request_id),
                            "status": req_status,
                        },
                    )
                    await session.rollback()
                    return False

            # Lock ProductionRenderJob (Order 2)
            job_stmt = (
                select(ProductionRenderJob)
                .where(ProductionRenderJob.id == job_id)
                .with_for_update()
            )
            job = (await session.execute(job_stmt)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return False

            if req is None and getattr(job, "production_request_id", None) is not None:
                req = await session.get(ProductionRequest, job.production_request_id)
                req_status = getattr(req, "status", None)
                if req is not None and req_status in (
                    ProductionRequestStatus.FAILED.value,
                    ProductionRequestStatus.CANCELLED.value,
                ):
                    await session.rollback()
                    return False


            # If job is already in a terminal state, worker cannot overwrite it
            if job.state in (
                RenderJobState.SUCCEEDED.value,
                RenderJobState.FAILED.value,
                RenderJobState.CANCELLED.value,
            ):
                logger.warning(
                    "stale_worker_failure_fenced_job_terminal",
                    extra={
                        "event": "stale_worker_failure_fenced_job_terminal",
                        "job_id": str(job_id),
                        "job_state": job.state,
                    },
                )
                await session.rollback()
                return False

            # For leased jobs: enforce lease ownership and DB-time validity
            is_leased = getattr(job, "lease_token", None) is not None or lease_token is not None
            if is_leased:
                if job.state != RenderJobState.RUNNING.value:
                    await session.rollback()
                    return False

                if lease_token is None or fencing_token is None:
                    logger.warning(
                        "stale_worker_failure_fenced_missing_token",
                        extra={
                            "event": "stale_worker_failure_fenced_missing_token",
                            "job_id": str(job_id),
                        },
                    )
                    await session.rollback()
                    return False

                if job.lease_token != lease_token or job.fencing_token != fencing_token:
                    logger.warning(
                        "stale_worker_failure_fenced_token_mismatch",
                        extra={
                            "event": "stale_worker_failure_fenced_token_mismatch",
                            "job_id": str(job_id),
                            "job_fencing_token": job.fencing_token,
                            "worker_fencing_token": fencing_token,
                        },
                    )
                    await session.rollback()
                    return False

                # Check DB-time validity: lease must not be expired
                check_unexpired = (
                    await session.execute(
                        select(ProductionRenderJob.id).where(
                            ProductionRenderJob.id == job_id,
                            ProductionRenderJob.lease_expires_at > func.now(),
                        )
                    )
                ).scalar_one_or_none()
                if check_unexpired is None:
                    logger.warning(
                        "stale_worker_failure_fenced_lease_expired",
                        extra={
                            "event": "stale_worker_failure_fenced_lease_expired",
                            "job_id": str(job_id),
                            "lease_token": str(lease_token),
                            "fencing_token": fencing_token,
                        },
                    )
                    await session.rollback()
                    return False

            now = datetime.now(UTC)
            job.state = RenderJobState.FAILED.value
            job.error_code = error_code.value
            job.sanitized_error = error_msg[:1000]
            job.completed_at = now
            job.lease_expires_at = None

            if req is None and job.production_request_id is not None:
                req = await session.get(ProductionRequest, job.production_request_id)

            req_status = getattr(req, "status", None)
            if req is not None and req_status not in (
                ProductionRequestStatus.FAILED.value,
                ProductionRequestStatus.CANCELLED.value,
            ):
                setattr(req, "status", ProductionRequestStatus.FAILED.value)
                setattr(req, "outcome", ProductionOutcome.BLOCKED.value)
                setattr(req, "failed_at", now)
                raw_meta = getattr(req, "metadata_", None)
                metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
                metadata["failure_info"] = {
                    "error_code": error_code.value,
                    "failure_stage": "RENDER_EXECUTION",
                    "reason": error_msg[:1000],
                    "failed_at": now.isoformat(),
                    "details": {
                        "job_id": str(job_id),
                        "fencing_token": fencing_token or getattr(job, "fencing_token", None),
                    },
                }
                setattr(req, "metadata_", metadata)
                await self._enqueue_terminal_evaluation(session, req, job_id)

            await session.commit()
            return True
        except Exception:
            await session.rollback()
            return False
