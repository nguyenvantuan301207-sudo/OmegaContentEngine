"""Render service implementing decoupled 3-phase execution, staging, and atomic finalization."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.ffmpeg_renderer import FFmpegRenderer
from omega.application.media_probe import MediaProbe
from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.application.production_qa import ProductionQAEngine
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
    MediaArtifact,
    ProductionQAResult,
    ProductionRenderJob,
    ProductionRequest,
    ProductionScene,
)
from omega.logging import get_logger

logger = get_logger(service="omega-render-service")


class ProductionRenderService:
    """Orchestrates non-transactional media rendering, ffprobe inspection, and atomic DB finalization."""

    def __init__(self, storage: LocalMediaStorageProvider | None = None) -> None:
        self.storage = storage or LocalMediaStorageProvider()
        self.renderer = FFmpegRenderer()
        self.probe = MediaProbe()
        self.qa_engine = ProductionQAEngine()

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

        # Check for duplicate execution / idempotent no-op
        if job.state == RenderJobState.SUCCEEDED.value:
            # Check existing artifact
            art_stmt = select(MediaArtifact).where(MediaArtifact.render_job_id == job.id)
            art_res = await session.execute(art_stmt)
            art = art_res.scalar_one_or_none()
            return art, ProductionQAStatus.PASSED

        prod_req = job.production_request
        mission_id = None
        if prod_req.mission_execution and prod_req.mission_execution.mission_id:
            mission_id = prod_req.mission_execution.mission_id
        elif (
            prod_req.content_request
            and prod_req.content_request.mission_execution
            and prod_req.content_request.mission_execution.mission_id
        ):
            mission_id = prod_req.content_request.mission_execution.mission_id

        # PRE_RENDER Guardian gate check
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
                if pre_check.decision and pre_check.decision.action not in (
                    GuardianAction.ALLOW,
                    GuardianAction.ALLOW_WITH_WARNING,
                ):
                    await self._record_job_failure(
                        session,
                        job_id,
                        RenderErrorCode.VALIDATION_FAILED,
                        f"Guardian PRE_RENDER held: {pre_check.decision.reason}",
                    )
                    await session.commit()
                    return None, ProductionQAStatus.BLOCKED
            except Exception as exc:
                logger.error("Guardian PRE_RENDER evaluation failed", error=str(exc))

        job.state = RenderJobState.RUNNING.value
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
        script_data = {"id": req.script_version_id}
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

        try:
            # 1. Render individual scene clips in staging
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
            await self._record_job_failure(session, job_id, RenderErrorCode.FFMPEG_FAILED, str(exc))
            raise

        # ══════════════════════════════════════════════════════════════════
        # PHASE 3: SHORT ATOMIC DB FINALIZATION & PRODUCTION QA
        # ══════════════════════════════════════════════════════════════════
        try:
            # 1. Run local 17-rule Production QA
            qa_status, qa_findings = self.qa_engine.evaluate(
                request_data=req_data,
                script_version_data=script_data,
                content_request_data=content_req_data,
                assets_data=assets_list,
                requirements_data=reqs_list,
                narration_segments=narr_list,
                subtitle_cues=subs_list,
                media_probe_summary=probe_summary,
                artifact_file_path=final_artifact_path,
                expected_hash=content_hash,
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

            # POST_RENDER Guardian Check
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
                    post_check = await guardian_engine.execute_check(
                        GuardianCheckCreate(
                            mission_id=mission_id,
                            production_request_id=request_id,
                            media_artifact_id=media_art.id,
                            checkpoint=GuardianCheckpoint.POST_RENDER,
                            trigger_type=CheckTriggerType.POST_RENDER,
                            diagnostic_context={
                                "media_probe_summary": probe_summary,
                                "artifact_id": str(media_art.id),
                                "expected_hash": content_hash,
                                "artifact_file_path": str(final_artifact_path)
                                if final_artifact_path
                                else None,
                            },
                        )
                    )
                    if post_check.decision and post_check.decision.action not in (
                        GuardianAction.ALLOW,
                        GuardianAction.ALLOW_WITH_WARNING,
                    ):
                        qa_status = ProductionQAStatus.BLOCKED
                except Exception as exc:
                    logger.error("Guardian POST_RENDER evaluation failed", error=str(exc))

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

            # 6. Update RenderJob to SUCCEEDED
            await session.execute(
                update(ProductionRenderJob)
                .where(ProductionRenderJob.id == job_id)
                .values(state=RenderJobState.SUCCEEDED.value)
            )

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

    async def _record_job_failure(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        error_code: RenderErrorCode,
        error_msg: str,
    ) -> None:
        """Record failed render job state in a short transaction."""
        try:
            await session.execute(
                update(ProductionRenderJob)
                .where(ProductionRenderJob.id == job_id)
                .values(
                    state=RenderJobState.FAILED.value,
                    error_code=error_code.value,
                    sanitized_error=error_msg[:1000],
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
