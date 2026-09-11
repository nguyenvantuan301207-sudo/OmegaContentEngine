"""Render service implementing decoupled 3-phase execution, staging, and atomic finalization."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.durable_dispatch import DurableDispatchService
from omega.application.ffmpeg_renderer import FFmpegExecutionError, FFmpegRenderer
from omega.application.media_probe import MediaProbe
from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.application.production_qa import ProductionQAEngine
from omega.application.template_payload_resolver import TemplatePayloadError
from omega.domain.production import (
    AssetType,
    MediaArtifactType,
    ProductionOutcome,
    ProductionQAStatus,
    ProductionRequestStatus,
    RenderErrorCode,
    RenderJobState,
)
from omega.infrastructure.models import (
    ContentGenerationRequest,
    MediaArtifact,
    MissionExecution,
    ProductionQAResult,
    ProductionRenderJob,
    ProductionRequest,
    ProductionScene,
    ScriptVersion,
)
from omega.logging import get_logger

logger = get_logger(service="omega-render-service")


def _classify_phase2_error(exc: Exception) -> RenderErrorCode:
    """Classify known Phase 2 failures without treating input defects as FFmpeg errors."""
    if isinstance(exc, TemplatePayloadError):
        return RenderErrorCode.INPUT_INVALID
    if isinstance(exc, FFmpegExecutionError):
        return RenderErrorCode.FFMPEG_FAILED
    return RenderErrorCode.FFMPEG_FAILED


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
    ) -> tuple[MediaArtifact | None, ProductionQAStatus]:
        """Execute the 3-phase render workflow for a render job."""
        # ══════════════════════════════════════════════════════════════════
        # PHASE 1: SHORT DB TRANSACTION (State transition to RUNNING)
        # ══════════════════════════════════════════════════════════════════
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
                ).selectinload(ScriptVersion.sections),
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
            await session.rollback()
            return None, ProductionQAStatus.PENDING
        if job.state in (RenderJobState.FAILED.value, RenderJobState.CANCELLED.value):
            await session.rollback()
            return None, ProductionQAStatus.BLOCKED
        if job.state not in (RenderJobState.QUEUED.value, RenderJobState.RETRY.value):
            await session.rollback()
            return None, ProductionQAStatus.PENDING

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
                from omega.infrastructure.database import AsyncSessionLocal

                guardian_engine = GuardianEngine(session_factory=AsyncSessionLocal)
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
                    await self._record_job_failure(
                        session,
                        job_id,
                        RenderErrorCode.INPUT_INVALID,
                        f"Guardian PRE_RENDER held: {reason}",
                    )
                    return None, ProductionQAStatus.BLOCKED
            except Exception as exc:
                logger.error("Guardian PRE_RENDER evaluation failed", error=str(exc))
                await self._record_job_failure(
                    session,
                    job_id,
                    RenderErrorCode.INPUT_INVALID,
                    f"Guardian PRE_RENDER evaluation failed: {str(exc)}"
                )
                return None, ProductionQAStatus.BLOCKED

        # All pre-render checks passed — now persist RUNNING before entering render phase.
        job.state = RenderJobState.RUNNING.value
        job.started_at = job.started_at or datetime.now(UTC)
        await session.commit()

        # Extract snapshot data for phase 2 execution
        plan = job.render_plan
        req = job.production_request
        version = plan.version
        width = plan.width
        height = plan.height
        fps = plan.fps
        video_codec = plan.video_codec
        audio_codec = plan.audio_codec

        # Collect scene asset and audio pairings
        assets_by_req_id = {a.asset_requirement_id: a for a in req.assets if a.asset_requirement_id}
        narration_by_scene_id = {n.scene_id: n for n in req.narration_segments}
        scenes_data = sorted(req.scenes, key=lambda s: s.scene_order)

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
            {"id": r.id, "purpose": r.purpose, "required": r.required}
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

        try:
            # Check if we should use V2
            use_v2 = self._should_use_v2(req)

            if use_v2:
                # V2 Route
                (
                    runtime_quality,
                    runtime_refs,
                    runtime_timeline_duration_ms,
                    runtime_narration_segments,
                    runtime_subtitle_cues,
                    runtime_scenes,
                ) = await self._render_v2_staging(
                    session=session,
                    req=req,
                    fps=fps,
                    target_width=width,
                    target_height=height,
                    container_format=plan.container if hasattr(plan, "container") else req.container_format,
                    video_codec=video_codec,
                    staging_output_path=staging_output_path,
                )
            else:
                # Legacy Route
                scene_clips: list[Path] = []
                for scene in scenes_data:
                    # Find image asset
                    req_id = scene.asset_requirements[0].id if scene.asset_requirements else None
                    img_asset = assets_by_req_id.get(req_id)
                    img_path = (
                        self.storage.resolve_stored_uri(channel_id, request_id, img_asset.storage_uri)
                        if img_asset
                        else None
                    )

                    # Find audio asset
                    narr_seg = narration_by_scene_id.get(scene.id)
                    audio_asset = (
                        next((a for a in req.assets if a.id == narr_seg.audio_asset_id), None)
                        if narr_seg
                        else None
                    )
                    audio_path = (
                        self.storage.resolve_stored_uri(channel_id, request_id, audio_asset.storage_uri)
                        if audio_asset
                        else None
                    )

                    clip_path = staging_dir / f"scene_{scene.scene_order}.mp4"
                    dur_sec = (
                        scene.estimated_duration_ms / 1000.0 if scene.estimated_duration_ms > 0 else 3.0
                    )
                    motion_effect = "SLOW_ZOOM_IN" if str(scene.scene_type) in ("TITLE", "TITLE_MOTION", "DIAGRAM", "INFOGRAPHIC", "STATISTIC", "CTA") else "NONE"

                    if img_path and audio_path and img_path.exists() and audio_path.exists():
                        await self.renderer.render_scene_clip(
                            image_path=img_path,
                            audio_path=audio_path,
                            output_path=clip_path,
                            width=width,
                            height=height,
                            fps=fps,
                            video_codec=video_codec,
                            audio_codec=audio_codec,
                            duration_sec=dur_sec,
                            motion_effect=motion_effect,
                        )
                    else:
                        # Synthetic placeholder fallback clip
                        await self._render_synthetic_clip(clip_path, width, height, fps, dur_sec)

                    scene_clips.append(clip_path)

                # Find subtitle asset if generated
                sub_asset = next(
                    (
                        a
                        for a in req.assets
                        if a.asset_type in ("SUBTITLE", AssetType.SUBTITLE.value)
                        or (a.mime_type and "subrip" in a.mime_type)
                    ),
                    None,
                )
                srt_path = (
                    self.storage.resolve_stored_uri(channel_id, request_id, sub_asset.storage_uri)
                    if sub_asset and sub_asset.storage_uri
                    else None
                )

                # 2. Concatenate scene clips into final staging video
                if len(scene_clips) == 1 and (not srt_path or not srt_path.exists()):
                    if staging_output_path.exists():
                        staging_output_path.unlink()
                    scene_clips[0].rename(staging_output_path)
                else:
                    await self.renderer.concatenate_clips(
                        scene_clips, staging_output_path, srt_path=srt_path
                    )

            # 3. Media probe validation via ffprobe
            probe_summary = await self.probe.probe_file(staging_output_path)

            # 4. Calculate deterministic SHA-256 hash
            content_hash = compute_sha256(staging_output_path)
            file_size = staging_output_path.stat().st_size

            # 5. Atomic Move/Rename to final immutable artifacts path
            final_file_name = f"video_v{version}_{content_hash[:12]}.mp4"
            final_artifact_path = artifacts_dir / final_file_name
            os.replace(staging_output_path, final_artifact_path)
            rel_uri = self.storage.to_relative_uri(channel_id, request_id, final_artifact_path)

        except Exception as exc:
            # Clean staging directory on failure
            self.storage.cleanup_directory(staging_dir)
            # Update job state in DB as FAILED
            await self._record_job_failure(session, job_id, _classify_phase2_error(exc), str(exc))
            raise

        # ══════════════════════════════════════════════════════════════════
        # PHASE 3: SHORT ATOMIC DB FINALIZATION & PRODUCTION QA
        # ══════════════════════════════════════════════════════════════════
        try:
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

            if use_v2:
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
            else:
                canonical_scenes_data = []
                for s in scenes_data:
                    canonical_scenes_data.append({
                        "sequence_index": s.scene_order,
                        "original_strategy": s.scene_type,
                        "effective_strategy": s.scene_type,
                        "duration_seconds": (s.estimated_duration_ms / 1000.0) if s.estimated_duration_ms else 0.0,
                        "asset_kind": None,
                        "asset_provider": None,
                        "asset_id": None,
                    })

            # 1. Run local 17-rule Production QA
            qa_status, qa_findings = self.qa_engine.evaluate(
                request_data=req_data,
                script_version_data=script_data,
                content_request_data=content_req_data,
                assets_data=assets_list,
                requirements_data=reqs_list,
                narration_segments=qa_narr_list,
                subtitle_cues=qa_subs_list,
                media_probe_summary=probe_summary,
                artifact_file_path=final_artifact_path,
                expected_hash=content_hash,
                scenes_data=canonical_scenes_data,
            )

            # 2. Atomic rollover of current pointer
            # Mark prior videos as is_current = False
            await session.execute(
                update(MediaArtifact)
                .where(
                    MediaArtifact.production_request_id == request_id,
                    MediaArtifact.artifact_type == MediaArtifactType.VIDEO.value,
                )
                .values(is_current=False)
            )
            await session.flush()

            # 3. Insert new MediaArtifact (vN)
            media_art = MediaArtifact(
                id=uuid.uuid4(),
                production_request_id=request_id,
                render_job_id=job_id,
                artifact_type=MediaArtifactType.VIDEO.value,
                version=version,
                is_current=True,
                storage_uri=rel_uri,
                content_hash=content_hash,
                file_size_bytes=file_size,
                mime_type="video/mp4",
                width=probe_summary.get("width", width),
                height=probe_summary.get("height", height),
                duration_ms=probe_summary.get("duration_ms", 0),
            )
            session.add(media_art)
            await session.flush()

            # 4. Insert ProductionQAResult
            qa_record = ProductionQAResult(
                id=uuid.uuid4(),
                production_request_id=request_id,
                artifact_id=media_art.id,
                status=qa_status.value,
                findings=[f.model_dump() for f in qa_findings],
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
                qa_status = await self._evaluate_post_render_guardian(
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
                    runtime_scenes=runtime_scenes if use_v2 else (),
                )

            # 5. Update ProductionRequest status and outcome
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
                )
            )

            # 6. Update RenderJob to SUCCEEDED and atomically persist its evaluator wake-up.
            await session.execute(
                update(ProductionRenderJob)
                .where(ProductionRenderJob.id == job_id)
                .values(state=RenderJobState.SUCCEEDED.value)
            )
            await self._enqueue_terminal_evaluation(session, prod_req, job_id)

            await session.commit()
            await session.refresh(media_art)
            return media_art, qa_status

        except Exception as exc:
            await session.rollback()
            # If DB finalization failed, clean up uncommitted final physical file
            if final_artifact_path and final_artifact_path.exists():
                self.storage.cleanup_file(final_artifact_path)
            await self._record_job_failure(
                session, job_id, RenderErrorCode.STORAGE_FAILED, str(exc)
            )
            raise
        finally:
            # Clean staging directory
            self.storage.cleanup_directory(staging_dir)

    def _should_use_v2(self, req: ProductionRequest) -> bool:
        mode = getattr(req, "mode", None)
        # Handle both string and Enum representations
        is_mission = mode == "MISSION_EXECUTION" or (hasattr(mode, "value") and mode.value == "MISSION_EXECUTION")
        return is_mission and self.visual_production_service is not None

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
            post_check = await guardian_engine.execute_check(
                GuardianCheckCreate(
                    mission_id=mission_id,
                    production_request_id=request_id,
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
                    },
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
    ) -> tuple[
        str | None,
        tuple[str, ...],
        int | None,
        tuple[object, ...],
        tuple[object, ...],
        tuple[object, ...],
    ]:
        import shutil

        # Lineage check
        if not req.mission_execution_id or not req.content_request_id:
            raise ValueError("Mission execution lineage missing for V2 render")

        # Target contract check
        if target_width != 1920 or target_height != 1080:
            raise ValueError(f"V2 unsupported resolution: {target_width}x{target_height}")
        if container_format.lower() != "mp4":
            raise ValueError(f"V2 unsupported container: {container_format}")
        if video_codec.lower() not in ("h264", "libx264"):
            raise ValueError(f"V2 unsupported codec: {video_codec}")

        # V2 execution
        result = await self.visual_production_service.render_mission_execution(
            session,
            req.mission_execution_id,
            req.content_request_id,
            fps=fps,
            voice_profile=req.voice_profile,
            subtitle_enabled=True,
        )

        # Validate V2 Result Lineage
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
        shutil.copy2(out_path, staging_output_path)

        # Verify Copied SHA
        copied_sha = compute_sha256(staging_output_path)
        if copied_sha != source_sha:
            raise ValueError("V2 copied SHA mismatch")

        return (
            getattr(result, "narration_quality", None),
            tuple(getattr(result, "narration_source_refs", ()) or ()),
            getattr(result, "runtime_timeline_duration_ms", None),
            tuple(getattr(result, "runtime_narration_segments", ()) or ()),
            tuple(getattr(result, "runtime_subtitle_cues", ()) or ()),
            tuple(getattr(result, "runtime_scenes", ()) or ()),
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
    ) -> None:
        """Record failed render job state in a short transaction."""
        try:
            job = (
                await session.execute(
                    select(ProductionRenderJob)
                    .where(ProductionRenderJob.id == job_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None or job.state not in (
                RenderJobState.PENDING.value,
                RenderJobState.QUEUED.value,
                RenderJobState.RUNNING.value,
                RenderJobState.RETRY.value,
            ):
                await session.rollback()
                return

            job.state = RenderJobState.FAILED.value
            job.error_code = error_code.value
            job.sanitized_error = error_msg[:1000]
            job.completed_at = datetime.now(UTC)

            request = await session.get(ProductionRequest, job.production_request_id)
            if request is not None:
                await self._enqueue_terminal_evaluation(session, request, job_id)
            await session.commit()
        except Exception:
            await session.rollback()
