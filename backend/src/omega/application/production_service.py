"""Production Service orchestrating production requests, preparation, and version allocation."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.asset_provider import LocalAssetProvider
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import PlaceholderNarrationProvider
from omega.application.production_timeline import align_production_timeline
from omega.application.scene_planner import plan_scenes_from_script
from omega.application.subtitle_engine import SubtitleEngine
from omega.domain.production import (
    ProductionMode,
    ProductionRequestCreate,
    ProductionRequestStatus,
    RenderJobState,
)
from omega.infrastructure.models import (
    AssetRequirement,
    ContentGenerationRequest,
    NarrationSegment,
    ProductionAsset,
    ProductionRenderJob,
    ProductionRequest,
    ProductionScene,
    RenderPlan,
    ScriptSection,
    ScriptVersion,
    SubtitleCue,
)
from omega.logging import get_logger

logger = get_logger("omega-production-service")


class ProductionLineageError(ValueError):
    """Raised when request lineage validation fails."""

    pass


class ProductionStateError(RuntimeError):
    """Raised when an illegal lifecycle transition is attempted."""

    pass


class ProductionService:
    """Service managing Production Engine lifecycle, preparation, and render job allocation."""

    def __init__(self, storage: LocalMediaStorageProvider | None = None) -> None:
        self.storage = storage or LocalMediaStorageProvider()
        self.asset_provider = LocalAssetProvider(self.storage)
        self.narration_provider = PlaceholderNarrationProvider(self.storage)
        self.subtitle_engine = SubtitleEngine(self.storage)

    async def create_production_request(
        self,
        session: AsyncSession,
        channel_id: uuid.UUID,
        payload: ProductionRequestCreate,
        idempotency_key: str | None = None,
    ) -> ProductionRequest:
        """Create a new ProductionRequest and validate strict upstream lineage."""
        if idempotency_key:
            stmt_existing = select(ProductionRequest).where(
                ProductionRequest.idempotency_key == idempotency_key
            )
            existing = (await session.execute(stmt_existing)).scalar_one_or_none()
            if existing:
                return existing

        # 1. Load ScriptVersion with ContentGenerationRequest
        script_stmt = (
            select(ScriptVersion)
            .where(ScriptVersion.id == payload.script_version_id)
            .options(
                selectinload(ScriptVersion.content_request).selectinload(
                    ContentGenerationRequest.channel
                ),
                selectinload(ScriptVersion.content_request).selectinload(
                    ContentGenerationRequest.channel_dna_revision
                ),
            )
        )
        script_res = await session.execute(script_stmt)
        script = script_res.scalar_one_or_none()

        if not script:
            raise ProductionLineageError(f"ScriptVersion {payload.script_version_id} not found.")

        content_req = script.content_request
        if not content_req:
            raise ProductionLineageError(
                f"ScriptVersion {script.id} has no associated ContentGenerationRequest."
            )

        # 2. Validate Channel Match (Cross-Channel Isolation)
        if content_req.channel_id != channel_id:
            raise ProductionLineageError(
                f"Channel mismatch: Script belongs to channel {content_req.channel_id}, not {channel_id}."
            )

        # 3. Derive pinned ChannelDNARevision from ContentGenerationRequest
        pinned_dna_revision_id = content_req.channel_dna_revision_id
        if not pinned_dna_revision_id:
            raise ProductionLineageError(
                "ContentGenerationRequest has no pinned ChannelDNARevision."
            )

        # 4. Mode determination
        mode = (
            ProductionMode.MISSION_EXECUTION
            if payload.mission_execution_id
            else ProductionMode.INTERACTIVE
        )

        # 5. Persist ProductionRequest
        prod_request = ProductionRequest(
            id=uuid.uuid4(),
            channel_id=channel_id,
            script_version_id=script.id,
            content_request_id=content_req.id,
            channel_dna_revision_id=pinned_dna_revision_id,
            mission_execution_id=payload.mission_execution_id,
            idempotency_key=idempotency_key,
            mode=mode.value,
            status=ProductionRequestStatus.DRAFT.value,
            outcome=None,
            target_width=payload.target_width,
            target_height=payload.target_height,
            fps=payload.fps,
            video_codec=payload.video_codec,
            audio_codec=payload.audio_codec,
            container_format=payload.container_format,
            voice_profile=payload.voice_profile.model_dump(),
            metadata_=payload.metadata,
        )
        try:
            session.add(prod_request)
            await session.commit()
            await session.refresh(prod_request)
        except IntegrityError:
            await session.rollback()
            if idempotency_key:
                stmt_existing = select(ProductionRequest).where(
                    ProductionRequest.idempotency_key == idempotency_key
                )
                existing = (await session.execute(stmt_existing)).scalar_one_or_none()
                if existing:
                    return existing
            raise
        return prod_request

    async def prepare_production(
        self,
        session: AsyncSession,
        channel_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> ProductionRequest:
        """Run Scene Planner, generate placeholder visual assets, narration, and RenderPlan v1."""
        # 1. Load request and script
        req_stmt = (
            select(ProductionRequest)
            .where(ProductionRequest.id == request_id, ProductionRequest.channel_id == channel_id)
            .options(
                selectinload(ProductionRequest.script_version)
                .selectinload(ScriptVersion.sections)
                .selectinload(ScriptSection.statements),
                selectinload(ProductionRequest.scenes),
                selectinload(ProductionRequest.assets),
            )
        )
        res = await session.execute(req_stmt)
        req = res.scalar_one_or_none()
        if not req:
            raise ProductionLineageError(
                f"ProductionRequest {request_id} not found on channel {channel_id}."
            )

        script = req.script_version
        script_dict = {
            "id": script.id,
            "title": script.title,
            "hook_text": script.hook_text,
            "closing_text": script.closing_text,
            "cta_text": script.cta_text,
            "sections": [
                {
                    "id": s.id,
                    "section_order": s.section_order,
                    "heading": s.heading,
                    "statements": [
                        {
                            "id": st.id,
                            "statement_order": st.statement_order,
                            "statement_text": st.statement_text,
                            "statement_type": st.statement_type,
                        }
                        for st in s.statements
                    ],
                }
                for s in script.sections
            ],
        }

        # 2. Deterministic Scene Planning
        planned_scenes = plan_scenes_from_script(req.id, script_dict)

        # Clear any existing draft scenes and assets
        for sc in list(req.scenes):
            await session.delete(sc)
        for a in list(req.assets):
            await session.delete(a)
        await session.flush()

        # 3. Create Scene & Asset Requirement records
        db_scenes: list[ProductionScene] = []
        db_requirements: list[AssetRequirement] = []
        for s_data in planned_scenes:
            scene_obj = ProductionScene(
                id=s_data["id"],
                production_request_id=req.id,
                scene_order=s_data["scene_order"],
                script_section_id=s_data["script_section_id"],
                start_statement_id=s_data["start_statement_id"],
                end_statement_id=s_data["end_statement_id"],
                scene_type=s_data["scene_type"],
                narration_text=s_data["narration_text"],
                estimated_duration_ms=s_data["estimated_duration_ms"],
                visual_intent=s_data["visual_intent"],
                transition_in=s_data["transition_in"],
                transition_out=s_data["transition_out"],
            )
            session.add(scene_obj)
            db_scenes.append(scene_obj)

            for r_data in s_data.get("asset_requirements", []):
                req_obj = AssetRequirement(
                    id=r_data["id"],
                    scene_id=scene_obj.id,
                    asset_type=r_data["asset_type"],
                    purpose=r_data["purpose"],
                    query_hint=r_data.get("query_hint"),
                    required=r_data.get("required", True),
                    status=r_data["status"],
                    license_requirement=r_data["license_requirement"],
                )
                session.add(req_obj)
                db_requirements.append(req_obj)

        await session.flush()

        # 4. Resolve Visual Placeholder Assets
        db_assets: list[ProductionAsset] = []
        for req_obj in db_requirements:
            asset_dict = await self.asset_provider.resolve_asset_requirement(
                channel_id=channel_id,
                request_id=req.id,
                requirement={"id": req_obj.id, "asset_type": req_obj.asset_type},
                width=req.target_width,
                height=req.target_height,
            )
            req_obj.status = "RESOLVED"
            asset_record = ProductionAsset(
                id=asset_dict["id"],
                channel_id=channel_id,
                production_request_id=req.id,
                asset_requirement_id=req_obj.id,
                asset_type=asset_dict["asset_type"],
                provider_type=asset_dict["provider_type"],
                storage_uri=asset_dict["storage_uri"],
                content_hash=asset_dict["content_hash"],
                mime_type=asset_dict["mime_type"],
                width=asset_dict["width"],
                height=asset_dict["height"],
                duration_ms=asset_dict["duration_ms"],
                license_status=asset_dict["license_status"],
                source_ref=asset_dict["source_ref"],
                attribution=asset_dict["attribution"],
            )
            session.add(asset_record)
            db_assets.append(asset_record)

        # 5. Timeline Alignment, Narration Audio, and Subtitles
        narration_plans, subtitle_plans, total_dur_ms = align_production_timeline(planned_scenes)

        for n_plan in narration_plans:
            audio_dict = await self.narration_provider.synthesize_segment_audio(
                channel_id=channel_id,
                request_id=req.id,
                segment=n_plan,
            )
            audio_asset = ProductionAsset(
                id=audio_dict["id"],
                channel_id=channel_id,
                production_request_id=req.id,
                asset_requirement_id=None,
                asset_type=audio_dict["asset_type"],
                provider_type=audio_dict["provider_type"],
                storage_uri=audio_dict["storage_uri"],
                content_hash=audio_dict["content_hash"],
                mime_type=audio_dict["mime_type"],
                width=None,
                height=None,
                duration_ms=audio_dict["duration_ms"],
                license_status=audio_dict["license_status"],
                source_ref=audio_dict["source_ref"],
                attribution=audio_dict["attribution"],
            )
            session.add(audio_asset)

            narr_seg = NarrationSegment(
                id=uuid.uuid4(),
                production_request_id=req.id,
                scene_id=n_plan["scene_id"],
                audio_asset_id=audio_asset.id,
                text=n_plan["text"],
                start_ms=n_plan["start_ms"],
                end_ms=n_plan["end_ms"],
                duration_ms=n_plan["duration_ms"],
            )
            session.add(narr_seg)

        for s_plan in subtitle_plans:
            sub_cue = SubtitleCue(
                id=uuid.uuid4(),
                production_request_id=req.id,
                scene_id=s_plan["scene_id"],
                cue_order=s_plan["cue_order"],
                start_ms=s_plan["start_ms"],
                end_ms=s_plan["end_ms"],
                text=s_plan["text"],
            )
            session.add(sub_cue)

        # Export SRT file
        srt_asset_dict = self.subtitle_engine.export_srt_file(
            channel_id=channel_id,
            request_id=req.id,
            subtitle_cues=subtitle_plans,
        )
        srt_asset = ProductionAsset(
            id=srt_asset_dict["id"],
            channel_id=channel_id,
            production_request_id=req.id,
            asset_requirement_id=None,
            asset_type=srt_asset_dict["asset_type"],
            provider_type=srt_asset_dict["provider_type"],
            storage_uri=srt_asset_dict["storage_uri"],
            content_hash=srt_asset_dict["content_hash"],
            mime_type=srt_asset_dict["mime_type"],
            license_status=srt_asset_dict["license_status"],
            source_ref=srt_asset_dict["source_ref"],
            attribution=srt_asset_dict["attribution"],
        )
        session.add(srt_asset)

        # 6. Create Initial RenderPlan (v1)
        render_plan = RenderPlan(
            id=uuid.uuid4(),
            production_request_id=req.id,
            version=1,
            width=req.target_width,
            height=req.target_height,
            fps=req.fps,
            video_codec=req.video_codec,
            audio_codec=req.audio_codec,
            container=req.container_format,
            total_duration_ms=total_dur_ms,
            scene_manifest=[
                {
                    "scene_order": s["scene_order"],
                    "type": s["scene_type"],
                    "duration_ms": s["estimated_duration_ms"],
                }
                for s in planned_scenes
            ],
            audio_manifest=[
                {
                    "scene_id": str(n["scene_id"]),
                    "text": n["text"],
                    "start_ms": n["start_ms"],
                    "end_ms": n["end_ms"],
                    "duration_ms": n["duration_ms"],
                }
                for n in narration_plans
            ],
            subtitle_manifest=[
                {
                    "scene_id": str(s["scene_id"]),
                    "cue_order": s["cue_order"],
                    "start_ms": s["start_ms"],
                    "end_ms": s["end_ms"],
                    "text": s["text"],
                }
                for s in subtitle_plans
            ],
        )
        session.add(render_plan)

        req.status = ProductionRequestStatus.READY.value
        await session.commit()
        await session.refresh(req)
        return req

    async def allocate_render_job(
        self,
        session: AsyncSession,
        channel_id: uuid.UUID,
        request_id: uuid.UUID,
        idempotency_key: str,
        is_rerender: bool = False,
    ) -> tuple[ProductionRenderJob, RenderPlan, bool]:
        """Atomically allocate a ProductionRenderJob inside a short row lock.

        Returns:
            (job, render_plan, is_new_job)
        """
        # Lock ProductionRequest with for_update to prevent version allocation races
        req_stmt = (
            select(ProductionRequest)
            .where(ProductionRequest.id == request_id, ProductionRequest.channel_id == channel_id)
            .with_for_update()
        )
        res = await session.execute(req_stmt)
        req = res.scalar_one_or_none()
        if not req:
            raise ProductionLineageError(f"ProductionRequest {request_id} not found.")

        # Check for existing job with same idempotency_key
        existing_job_stmt = select(ProductionRenderJob).where(
            ProductionRenderJob.production_request_id == req.id,
            ProductionRenderJob.idempotency_key == idempotency_key,
        )
        existing_job_res = await session.execute(existing_job_stmt)
        existing_job = existing_job_res.scalar_one_or_none()

        if existing_job:
            # Reusing existing job (Idempotent replay)
            plan_stmt = select(RenderPlan).where(RenderPlan.id == existing_job.render_plan_id)
            plan_res = await session.execute(plan_stmt)
            plan = plan_res.scalar_one()
            return existing_job, plan, False

        # If rerender, ensure all prior jobs are terminal
        if is_rerender:
            active_jobs_stmt = select(ProductionRenderJob).where(
                ProductionRenderJob.production_request_id == req.id,
                ProductionRenderJob.state.in_(
                    [
                        RenderJobState.PENDING.value,
                        RenderJobState.QUEUED.value,
                        RenderJobState.RUNNING.value,
                    ]
                ),
            )
            active_jobs_res = await session.execute(active_jobs_stmt)
            if active_jobs_res.scalars().all():
                raise ProductionStateError(
                    "Cannot rerender while a render job is currently running."
                )

        # Determine next RenderPlan version
        plans_stmt = (
            select(RenderPlan)
            .where(RenderPlan.production_request_id == req.id)
            .order_by(RenderPlan.version.desc())
        )
        plans_res = await session.execute(plans_stmt)
        latest_plan = plans_res.scalars().first()

        if not latest_plan:
            raise ProductionStateError("No RenderPlan exists. Call /prepare first.")

        if is_rerender:
            new_version = latest_plan.version + 1
            render_plan = RenderPlan(
                id=uuid.uuid4(),
                production_request_id=req.id,
                version=new_version,
                width=latest_plan.width,
                height=latest_plan.height,
                fps=latest_plan.fps,
                video_codec=latest_plan.video_codec,
                audio_codec=latest_plan.audio_codec,
                container=latest_plan.container,
                total_duration_ms=latest_plan.total_duration_ms,
                scene_manifest=latest_plan.scene_manifest,
                audio_manifest=latest_plan.audio_manifest,
                subtitle_manifest=latest_plan.subtitle_manifest,
            )
            session.add(render_plan)
            await session.flush()
        else:
            render_plan = latest_plan

        # Create new RenderJob
        job = ProductionRenderJob(
            id=uuid.uuid4(),
            production_request_id=req.id,
            render_plan_id=render_plan.id,
            idempotency_key=idempotency_key,
            state=RenderJobState.QUEUED.value,
            attempt=1,
            max_attempts=3,
        )
        session.add(job)
        req.status = ProductionRequestStatus.RUNNING.value
        await session.commit()
        await session.refresh(job)
        return job, render_plan, True
