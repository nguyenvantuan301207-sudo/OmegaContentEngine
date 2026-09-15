"""Production Service orchestrating production requests, preparation, and version allocation."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.scene_composition import SceneCompositionEngine
from omega.application.storyboard_engine import StoryboardEngine
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.application.visual_production_v2_service import ScriptStoryboardAdapter
from omega.domain.channel_style import extract_channel_style_profile
from omega.domain.production import (
    ProductionMode,
    ProductionRequestCreate,
    ProductionRequestStatus,
    RenderJobState,
    SubtitleMode,
)
from omega.infrastructure.models import (
    AssetRequirement,
    Channel,
    ContentGenerationRequest,
    ProductionRenderJob,
    ProductionRequest,
    ProductionScene,
    RenderPlan,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
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
        self.storyboard_engine = StoryboardEngine()
        self.scene_composition_engine = SceneCompositionEngine()

    async def update_render_settings(
        self,
        session: AsyncSession,
        channel_id: uuid.UUID,
        request_id: uuid.UUID,
        subtitle_style: SubtitleRenderStyle | None,
        subtitle_mode: SubtitleMode | str | None = None,
    ) -> ProductionRequest:
        """Persist validated V2 render settings in existing request metadata JSON."""
        stmt = select(ProductionRequest).where(
            ProductionRequest.id == request_id,
            ProductionRequest.channel_id == channel_id,
        )
        request = (await session.execute(stmt)).scalar_one_or_none()
        if request is None:
            raise ProductionLineageError(f"ProductionRequest {request_id} not found.")
        if request.status == ProductionRequestStatus.RUNNING.value:
            raise ProductionStateError("Render settings cannot change while a render is running.")

        metadata = dict(request.metadata_ or {})
        render_settings = dict(metadata.get("render_settings") or {})
        if subtitle_style is not None:
            render_settings["subtitle_style"] = subtitle_style.model_dump()
        if subtitle_mode is not None:
            if isinstance(subtitle_mode, SubtitleMode):
                render_settings["subtitle_mode"] = subtitle_mode.value
            else:
                s = str(subtitle_mode).strip().upper()
                if s in ("OFF", "DISABLED", "NONE", "FALSE"):
                    render_settings["subtitle_mode"] = SubtitleMode.OFF.value
                elif s in ("STANDARD", "SENTENCE", "TRUE"):
                    render_settings["subtitle_mode"] = SubtitleMode.STANDARD.value
                elif s in ("KARAOKE",):
                    render_settings["subtitle_mode"] = SubtitleMode.KARAOKE.value
                else:
                    raise ValueError(f"Invalid subtitle_mode: '{subtitle_mode}'")
        metadata["render_settings"] = render_settings
        request.metadata_ = metadata
        await session.commit()
        await session.refresh(request)
        return request

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
        """Persist canonical planned scenes and a RenderPlan without physical media work."""
        # 1. Load request and script
        req_stmt = (
            select(ProductionRequest)
            .where(ProductionRequest.id == request_id, ProductionRequest.channel_id == channel_id)
            .options(
                selectinload(ProductionRequest.script_version)
                .selectinload(ScriptVersion.sections)
                .selectinload(ScriptSection.statements)
                .selectinload(ScriptStatement.citations),
                selectinload(ProductionRequest.scenes),
            )
        )
        res = await session.execute(req_stmt)
        req = res.scalar_one_or_none()
        if not req:
            raise ProductionLineageError(
                f"ProductionRequest {request_id} not found on channel {channel_id}."
            )

        script = req.script_version
        script_dict = ScriptStoryboardAdapter.to_script_dict(script)
        channel = await session.get(Channel, channel_id)
        style_profile = (
            extract_channel_style_profile(channel.metadata_) if channel else None
        )
        pacing = style_profile.pacing if style_profile else "BALANCED"
        storyboard = self.storyboard_engine.generate_storyboard(
            script_dict,
            pacing=pacing,
        )

        # Clear existing planned scenes before replacing the plan. Physical assets
        # are intentionally outside preparation authority and are not touched.
        for sc in list(req.scenes):
            await session.delete(sc)
        await session.flush()

        sections_by_heading = {
            section.heading: section for section in script.sections
        }

        # Persist the canonical storyboard as planned truth only.
        for planned_scene in storyboard.scenes:
            section = sections_by_heading.get(planned_scene.section_id)
            statements_by_order = (
                {statement.statement_order: statement for statement in section.statements}
                if section is not None
                else {}
            )
            referenced_statements = [
                statements_by_order[order]
                for order in planned_scene.source_statement_references
                if order in statements_by_order
            ]
            scene_obj = ProductionScene(
                id=uuid.uuid4(),
                production_request_id=req.id,
                scene_order=planned_scene.sequence_index,
                script_section_id=section.id if section is not None else None,
                start_statement_id=(
                    referenced_statements[0].id if referenced_statements else None
                ),
                end_statement_id=(
                    referenced_statements[-1].id if referenced_statements else None
                ),
                scene_type=planned_scene.visual_strategy.value,
                narration_text=planned_scene.narration_excerpt,
                estimated_duration_ms=round(
                    planned_scene.estimated_duration_seconds * 1000
                ),
                visual_intent=planned_scene.visual_brief,
                transition_in=None,
                transition_out=planned_scene.motion_hint,
            )
            session.add(scene_obj)

            composition = self.scene_composition_engine.compose(planned_scene)
            for planned_requirement in composition.asset_requirements:
                session.add(AssetRequirement(
                    id=uuid.uuid4(),
                    scene_id=scene_obj.id,
                    asset_type=planned_requirement.kind,
                    purpose=planned_requirement.purpose,
                    query_hint=planned_requirement.query,
                    required=True,
                    status="PENDING",
                    license_requirement="COMMERCIAL_ALLOWED",
                ))

        # The RenderPlan is a planning manifest. Runtime narration, subtitles,
        # and artifact truth are produced only by canonical V2 rendering.
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
            total_duration_ms=round(storyboard.estimated_duration_seconds * 1000),
            scene_manifest=[
                {
                    "scene_order": scene.sequence_index,
                    "type": scene.visual_strategy.value,
                    "duration_ms": round(scene.estimated_duration_seconds * 1000),
                }
                for scene in storyboard.scenes
            ],
            audio_manifest=[],
            subtitle_manifest=[],
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
