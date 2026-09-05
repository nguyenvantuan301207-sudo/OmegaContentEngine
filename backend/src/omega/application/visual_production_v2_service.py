import hashlib
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application import content_service
from omega.application.ffmpeg_renderer import FFmpegRenderer
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import NarrationProvider
from omega.application.storyboard_engine import (
    StoryboardEngine,
    StoryboardPlan,
    StoryboardScene,
    VisualStrategy,
)
from omega.application.template_payload_resolver import TemplatePayloadResolver
from omega.application.visual_asset_binding import BoundBrollAsset
from omega.application.visual_asset_engine import VisualAssetEngine, VisualAssetRequest
from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator
from omega.application.visual_direction import VisualAssetKind, VisualDirector
from omega.application.visual_template_renderer import VisualTemplateRenderer
from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime
from omega.infrastructure.models import (
    ContentGenerationRequest,
    MissionExecution,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
)
from omega.infrastructure.visual_asset_materializer import VisualAssetMaterializer
from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderer

_NON_ALPHANUM_REGEX = re.compile(r"[^\w\s-]")

_WHITESPACE_REGEX = re.compile(r"\s+")


class VerticalSliceError(Exception):
    pass


class VerticalSliceSceneResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence_index: int
    original_strategy: str
    effective_strategy: str
    template_id: str
    asset_kind: str | None
    asset_provider: str | None
    asset_id: str | None
    duration_seconds: float
    content_sha256: str
    audio_content_sha256: str | None = None
    audio_duration_seconds: float | None = None


class VerticalSliceRenderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    mission_execution_id: UUID
    content_request_id: UUID
    script_version_id: UUID

    scene_count: int
    template_scene_count: int
    image_scene_count: int
    broll_scene_count: int

    duration_seconds: float
    width: int
    height: int
    fps: int

    output_path: Path
    content_sha256: str
    run_fingerprint: str


def _clean_query_words(text: str, max_words: int = 6) -> str:
    cleaned = _NON_ALPHANUM_REGEX.sub(" ", text)
    words = [w for w in _WHITESPACE_REGEX.split(cleaned.strip()) if w]
    return " ".join(words[:max_words])


class ScriptStoryboardAdapter:
    @staticmethod
    def to_script_dict(script_version: ScriptVersion) -> dict[str, Any]:
        if not script_version.sections:
            raise VerticalSliceError("ScriptVersion has no sections")

        sorted_sections = sorted(script_version.sections, key=lambda s: s.section_order)

        sections_data: list[dict[str, Any]] = []
        total_statements = 0

        for sec in sorted_sections:
            sorted_statements = sorted(sec.statements, key=lambda st: st.statement_order)
            total_statements += len(sorted_statements)

            stmts_data: list[dict[str, Any]] = []
            for st in sorted_statements:
                citations_data = [
                    {
                        "claim_id": str(cit.claim_id) if cit.claim_id else None,
                        "evidence_id": str(cit.evidence_id) if cit.evidence_id else None,
                        "source_id": str(cit.source_id) if cit.source_id else None,
                    }
                    for cit in st.citations
                ]
                stmts_data.append({
                    "statement_order": st.statement_order,
                    "statement_text": st.statement_text,
                    "statement_type": st.statement_type,

                    "citations": citations_data,
                })

            sections_data.append({
                "section_order": sec.section_order,
                "heading": sec.heading,
                "narration_text": sec.narration_text,
                "estimated_duration_seconds": sec.estimated_duration_seconds,
                "statements": stmts_data,
            })

        if total_statements == 0:
            raise VerticalSliceError("ScriptVersion has no statements")

        return {
            "title": script_version.title,
            "estimated_duration_seconds": float(script_version.estimated_duration_seconds),
            "sections": sections_data,
        }


class VisualProductionV2Service:
    def __init__(
        self,
        asset_orchestrator: VisualAssetOrchestrator,
        output_root: Path,
        browser_runtime_factory: Callable[[], Any] | None = None,
        video_renderer: VisualV2VideoRenderer | None = None,
        ffmpeg_renderer: FFmpegRenderer | None = None,
        narration_provider: NarrationProvider | None = None,
        narration_storage: LocalMediaStorageProvider | None = None,
    ):
        self._orchestrator = asset_orchestrator
        self._output_root = output_root
        self._browser_runtime_factory = browser_runtime_factory or BrowserCaptureRuntime
        self._video_renderer = video_renderer or VisualV2VideoRenderer()
        self._ffmpeg_renderer = ffmpeg_renderer or FFmpegRenderer()
        self._template_renderer = VisualTemplateRenderer()
        self._template_resolver = TemplatePayloadResolver()
        self._visual_director = VisualDirector()
        self._visual_asset_engine = VisualAssetEngine()
        self._storyboard_engine = StoryboardEngine()
        self._narration_provider = narration_provider
        self._narration_storage = narration_storage
        if self._narration_provider and not self._narration_storage:
            raise ValueError("narration_storage is required when narration_provider is supplied")

    async def render_mission_execution(
        self,
        session: AsyncSession,
        mission_execution_id: UUID,
        content_request_id: UUID,
        *,
        fps: int = 12,
        voice_profile: dict[str, Any] | None = None,
    ) -> VerticalSliceRenderResult:
        if fps <= 0 or fps > 60:
            raise VerticalSliceError(f"Invalid fps: {fps}. Must be > 0 and <= 60.")

        # 1. True Mission Lineage Validation
        exec_stmt = (
            select(MissionExecution)
            .where(MissionExecution.id == mission_execution_id)
            .options(selectinload(MissionExecution.mission))
        )
        exec_res = await session.execute(exec_stmt)
        mission_exec = exec_res.scalar_one_or_none()
        if not mission_exec:
            raise VerticalSliceError(f"MissionExecution '{mission_execution_id}' not found")

        mission = mission_exec.mission
        if not mission:
            raise VerticalSliceError(f"Mission for execution '{mission_execution_id}' not found")

        if not mission.channel_id:
            raise VerticalSliceError(f"Mission '{mission.id}' has no channel_id")

        req_stmt = (
            select(ContentGenerationRequest)
            .where(ContentGenerationRequest.id == content_request_id)
            .options(
                selectinload(ContentGenerationRequest.scripts)
                .selectinload(ScriptVersion.sections)
                .selectinload(ScriptSection.statements)
                .selectinload(ScriptStatement.citations)
            )
        )
        req_res = await session.execute(req_stmt)
        content_req = req_res.scalar_one_or_none()
        if not content_req:
            raise VerticalSliceError(f"ContentGenerationRequest '{content_request_id}' not found")

        if content_req.mission_execution_id != mission_execution_id:
            raise VerticalSliceError(
                f"ContentGenerationRequest.mission_execution_id ({content_req.mission_execution_id}) "
                f"does not match supplied mission_execution_id ({mission_execution_id})"
            )

        if content_req.channel_id != mission.channel_id:
            raise VerticalSliceError(
                f"ContentGenerationRequest.channel_id ({content_req.channel_id}) "
                f"does not match Mission.channel_id ({mission.channel_id})"
            )

        if content_req.channel_dna_revision_id != mission_exec.channel_dna_revision_id:
            raise VerticalSliceError(
                f"ContentGenerationRequest.channel_dna_revision_id ({content_req.channel_dna_revision_id}) "
                f"does not match MissionExecution.channel_dna_revision_id ({mission_exec.channel_dna_revision_id})"
            )

        # 2. Resolve or Generate ScriptVersion
        script_version: ScriptVersion | None = None
        if content_req.scripts:
            # Deterministically reuse latest version
            sorted_scripts = sorted(content_req.scripts, key=lambda s: s.version, reverse=True)
            script_version = sorted_scripts[0]
        else:
            # Invoke real content service
            try:
                await content_service.generate_content(
                    session=session,
                    channel_id=content_req.channel_id,
                    request_id=content_req.id,
                )
            except Exception as e:
                raise VerticalSliceError(f"Content generation failed: {self._sanitize_error(e)}") from e

            # Reload with eager associations
            reload_stmt = (
                select(ScriptVersion)
                .where(ScriptVersion.content_request_id == content_req.id)
                .order_by(desc(ScriptVersion.version))
                .options(
                    selectinload(ScriptVersion.sections)
                    .selectinload(ScriptSection.statements)
                    .selectinload(ScriptStatement.citations)
                )
            )
            reload_res = await session.execute(reload_stmt)
            script_version = reload_res.scalars().first()

        if not script_version:
            raise VerticalSliceError(f"No ScriptVersion available for request '{content_request_id}'")

        # 3. Deterministic Run Fingerprint & Idempotency Check
        fingerprint_input = (
            f"omega-vertical-slice-v0:{mission_execution_id}:"
            f"{content_request_id}:{script_version.id}:{fps}"
        )
        if self._narration_provider:
            fingerprint_input += ":narrated"
            provider_cls = self._narration_provider.__class__.__name__
            model = getattr(self._narration_provider, "model", None)
            voice = getattr(self._narration_provider, "default_voice", None)
            fingerprint_input += f":{provider_cls}:{model}:{voice}"
            if voice_profile:
                normalized_vp = json.dumps(voice_profile, sort_keys=True)
                fingerprint_input += f":{normalized_vp}"

        run_fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()

        run_dir = self._output_root / str(mission_execution_id) / run_fingerprint
        final_mp4_path = run_dir / "final.mp4"
        manifest_path = run_dir / "manifest.json"

        final_exists = final_mp4_path.is_file()
        manifest_exists = manifest_path.is_file()

        if final_exists and manifest_exists:
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest_data = json.load(f)


                if manifest_data.get("run_fingerprint") != run_fingerprint:
                    raise VerticalSliceError("Manifest run_fingerprint mismatch")

                expected_sha = manifest_data.get("content_sha256")
                actual_sha = self._compute_streaming_sha(final_mp4_path)
                if actual_sha != expected_sha:
                    raise VerticalSliceError("Existing final.mp4 SHA256 mismatch with manifest")

                with open(final_mp4_path, "rb") as f:
                    hdr = f.read(4096)
                    if b"ftyp" not in hdr:
                        raise VerticalSliceError("Existing final.mp4 missing ftyp header")

                return VerticalSliceRenderResult(
                    mission_id=mission.id,
                    mission_execution_id=mission_execution_id,
                    content_request_id=content_request_id,
                    script_version_id=script_version.id,
                    scene_count=manifest_data["scene_count"],
                    template_scene_count=manifest_data["template_scene_count"],
                    image_scene_count=manifest_data["image_scene_count"],
                    broll_scene_count=manifest_data["broll_scene_count"],
                    duration_seconds=manifest_data["duration_seconds"],
                    width=manifest_data["width"],
                    height=manifest_data["height"],
                    fps=manifest_data["fps"],
                    output_path=final_mp4_path,
                    content_sha256=actual_sha,
                    run_fingerprint=run_fingerprint,
                )
            except Exception as e:
                raise VerticalSliceError(f"Incomplete or corrupt prior run: {e}") from e

        if final_exists or manifest_exists:
            raise VerticalSliceError("Incomplete prior run detected (found partial final/manifest)")

        # 4. Script -> Storyboard
        script_dict = ScriptStoryboardAdapter.to_script_dict(script_version)
        storyboard: StoryboardPlan = self._storyboard_engine.generate_storyboard(script_dict)

        if not storyboard.scenes:
            raise VerticalSliceError("StoryboardEngine produced 0 scenes")

        # 5. Work Directory Setup
        work_dir = run_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 6. Process Scenes

            ordered_scene_paths: list[Path] = []
            scene_results: list[VerticalSliceSceneResult] = []
            total_duration = 0.0

            template_scenes = 0
            image_scenes = 0
            broll_scenes = 0

            indices = set()
            for scene in storyboard.scenes:
                if scene.sequence_index in indices:
                    raise VerticalSliceError(f"Duplicate sequence_index: {scene.sequence_index}")
                indices.add(scene.sequence_index)

            sorted_scenes = sorted(storyboard.scenes, key=lambda s: s.sequence_index)

            async with self._browser_runtime_factory() as browser:
                for scene in sorted_scenes:
                    if scene.estimated_duration_seconds <= 0:
                        raise VerticalSliceError(
                            f"Scene {scene.sequence_index} duration {scene.estimated_duration_seconds} <= 0"
                        )

                    original_strategy = scene.visual_strategy
                    effective_scene = self._apply_v0_compatibility(scene)
                    effective_strategy = effective_scene.visual_strategy

                    audio_sha: str | None = None
                    audio_duration_sec: float | None = None
                    actual_scene_duration_seconds = effective_scene.estimated_duration_seconds
                    audio_path: Path | None = None

                    if self._narration_provider:
                        if not scene.narration_excerpt:
                            raise VerticalSliceError(f"Empty narration text for scene {scene.sequence_index}")

                        try:
                            audio_asset = await self._narration_provider.synthesize_segment_audio(
                                channel_id=mission.channel_id,
                                request_id=content_request_id,
                                segment={"text": scene.narration_excerpt},
                                voice_profile=voice_profile,
                            )
                        except Exception as e:
                            raise VerticalSliceError(f"Narration provider failed: {self._sanitize_error(e)}") from e

                        rel_uri = audio_asset.get("storage_uri")
                        if not rel_uri:
                            raise VerticalSliceError("Audio asset missing storage_uri")

                        audio_path = self._narration_storage.resolve_stored_uri(
                            mission.channel_id, content_request_id, rel_uri
                        )

                        if not audio_path.exists() or audio_path.stat().st_size <= 0:
                            raise VerticalSliceError("Audio file missing or empty")

                        duration_ms = audio_asset.get("duration_ms")
                        if not duration_ms or duration_ms <= 0:
                            raise VerticalSliceError("Audio duration missing or zero")

                        if not audio_asset.get("content_hash"):
                            raise VerticalSliceError("Audio asset missing content_hash")

                        audio_duration_sec = duration_ms / 1000.0
                        actual_scene_duration_seconds = audio_duration_sec
                        audio_sha = audio_asset["content_hash"]

                    # Meaningful query fallback for IMAGE and BROLL
                    if effective_strategy in (VisualStrategy.IMAGE, VisualStrategy.BROLL):
                        self._ensure_meaningful_query(effective_scene)

                    direction = self._visual_director.resolve(effective_scene)
                    payload = self._template_resolver.resolve(effective_scene, direction)

                    assets: tuple[Any, ...] = ()
                    broll_asset: BoundBrollAsset | None = None
                    asset_kind_str: str | None = None
                    asset_provider_str: str | None = None
                    asset_id_str: str | None = None

                    if direction.asset_requirements:
                        req_spec = direction.asset_requirements[0]
                        asset_kind_str = req_spec.kind.value
                        asset_request: VisualAssetRequest = self._visual_asset_engine.build_request(
                            scene_index=effective_scene.sequence_index,
                            requirement=req_spec,
                        )
                        if asset_request is None:
                            raise VerticalSliceError("Could not build required visual asset request")

                        try:
                            resolved_asset = await self._orchestrator.resolve(asset_request)
                        except Exception as e:
                            raise VerticalSliceError(f"Asset orchestrator failed for scene {scene.sequence_index}: {self._sanitize_error(e)}") from e

                        asset_provider_str = resolved_asset.provider
                        asset_id_str = resolved_asset.asset_id

                        if req_spec.kind == VisualAssetKind.IMAGE:
                            try:
                                bound_img = VisualAssetMaterializer.materialize(resolved_asset)
                            except Exception as e:
                                raise VerticalSliceError(f"Image materializer failed: {self._sanitize_error(e)}") from e
                            assets = (bound_img,)
                            image_scenes += 1
                        elif req_spec.kind == VisualAssetKind.BROLL:
                            try:
                                bound_broll = VisualAssetMaterializer.materialize_broll(resolved_asset)
                            except Exception as e:
                                raise VerticalSliceError(f"Broll materializer failed: {self._sanitize_error(e)}") from e
                            assets = (bound_broll,)
                            broll_asset = bound_broll
                            broll_scenes += 1
                        else:
                            raise VerticalSliceError(f"Unsupported asset requirement kind: {req_spec.kind}")
                    else:
                        template_scenes += 1

                    try:
                        document = self._template_renderer.render(payload, assets=assets)
                    except Exception as e:
                        raise VerticalSliceError(f"Template renderer failed for scene {scene.sequence_index}: {self._sanitize_error(e)}") from e

                    scene_out_path = work_dir / f"scene_{scene.sequence_index:03d}.mp4"
                    scene_visual_out_path = work_dir / f"scene_{scene.sequence_index:03d}_visual.mp4" if self._narration_provider else scene_out_path

                    try:
                        render_res = await self._video_renderer.render_clip(
                            document=document,
                            motion_profile=direction.motion_profile,
                            duration_seconds=actual_scene_duration_seconds,
                            output_path=scene_visual_out_path,
                            browser_runtime=browser,
                            fps=fps,
                            broll_asset=broll_asset,
                        )
                    except Exception as e:
                        raise VerticalSliceError(f"Scene video render failed for scene {scene.sequence_index}: {self._sanitize_error(e)}") from e

                    final_scene_sha = render_res.video_sha256

                    if self._narration_provider:
                        try:
                            await self._ffmpeg_renderer.mux_video_audio(
                                video_path=scene_visual_out_path,
                                audio_path=audio_path,
                                output_path=scene_out_path,
                            )
                        except Exception as e:
                            raise VerticalSliceError(f"Mux failed for scene {scene.sequence_index}: {self._sanitize_error(e)}") from e

                        if not scene_out_path.exists() or scene_out_path.stat().st_size <= 0:
                            raise VerticalSliceError(f"Mux output missing or empty for scene {scene.sequence_index}")

                        final_scene_sha = self._compute_streaming_sha(scene_out_path)

                    ordered_scene_paths.append(scene_out_path)
                    total_duration += actual_scene_duration_seconds

                    scene_results.append(
                        VerticalSliceSceneResult(
                            sequence_index=scene.sequence_index,
                            original_strategy=original_strategy.value,
                            effective_strategy=effective_strategy.value,
                            template_id=document.template_id.value,
                            asset_kind=asset_kind_str,
                            asset_provider=asset_provider_str,
                            asset_id=asset_id_str,
                            duration_seconds=actual_scene_duration_seconds,
                            content_sha256=final_scene_sha,
                            audio_content_sha256=audio_sha,
                            audio_duration_seconds=audio_duration_sec,
                        )
                    )

            # 7. Final Concatenation
            final_temp_mp4 = work_dir / "final_temp.mp4"
            try:
                await self._ffmpeg_renderer.concatenate_clips(
                    clip_paths=ordered_scene_paths,
                    output_path=final_temp_mp4,
                    srt_path=None,
                )
            except Exception as e:
                raise VerticalSliceError(f"Final video concatenation failed: {self._sanitize_error(e)}") from e

            if not final_temp_mp4.is_file() or final_temp_mp4.stat().st_size <= 0:
                raise VerticalSliceError("Final concatenated MP4 is missing or empty")

            with open(final_temp_mp4, "rb") as f:
                hdr = f.read(4096)
                if b"ftyp" not in hdr:
                    raise VerticalSliceError("Final concatenated MP4 missing ftyp header")

            final_sha = self._compute_streaming_sha(final_temp_mp4)

            # 8. Atomic Publish to Run Root
            final_temp_mp4.replace(final_mp4_path)

            manifest_content = {
                "run_fingerprint": run_fingerprint,
                "mission_id": str(mission.id),
                "mission_execution_id": str(mission_execution_id),
                "content_request_id": str(content_request_id),
                "script_version_id": str(script_version.id),
                "scene_count": len(ordered_scene_paths),
                "template_scene_count": template_scenes,
                "image_scene_count": image_scenes,
                "broll_scene_count": broll_scenes,
                "duration_seconds": round(total_duration, 2),
                "width": 1920,
                "height": 1080,
                "fps": fps,
                "content_sha256": final_sha,
                "narration_enabled": bool(self._narration_provider),
                "narration_provider": self._narration_provider.__class__.__name__ if self._narration_provider else None,
                "narration_model": getattr(self._narration_provider, "model", None) if self._narration_provider else None,
                "narration_voice": getattr(self._narration_provider, "default_voice", None) if self._narration_provider else None,
                "scenes": [s.model_dump() for s in scene_results],
            }

            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest_content, f, indent=2)

        finally:
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

        return VerticalSliceRenderResult(
            mission_id=mission.id,
            mission_execution_id=mission_execution_id,
            content_request_id=content_request_id,
            script_version_id=script_version.id,
            scene_count=len(ordered_scene_paths),
            template_scene_count=template_scenes,
            image_scene_count=image_scenes,
            broll_scene_count=broll_scenes,
            duration_seconds=round(total_duration, 2),
            width=1920,
            height=1080,
            fps=fps,
            output_path=final_mp4_path,
            content_sha256=final_sha,
            run_fingerprint=run_fingerprint,
        )

    def _apply_v0_compatibility(self, scene: StoryboardScene) -> StoryboardScene:
        strat = scene.visual_strategy
        if strat == VisualStrategy.SCREENSHOT:
            raise VerticalSliceError("SCREENSHOT strategy is not supported by Vertical Slice V0")

        if strat == VisualStrategy.KINETIC_TEXT:
            return scene.model_copy(update={"visual_strategy": VisualStrategy.TITLE_MOTION})

        if strat == VisualStrategy.INFOGRAPHIC:
            return scene.model_copy(update={"visual_strategy": VisualStrategy.IMAGE})

        if strat == VisualStrategy.CTA:
            text = scene.on_screen_text or scene.narration_excerpt[:40]
            return scene.model_copy(update={
                "visual_strategy": VisualStrategy.TITLE_MOTION,
                "on_screen_text": text,
            })

        return scene

    def _ensure_meaningful_query(self, scene: StoryboardScene) -> None:
        raw_hint = scene.asset_query_hint or ""
        clean = raw_hint.strip().lower()

        is_meaningless = (
            not clean
            or clean in ("placeholder", "generic")
            or "abstract technology background" in clean
        )

        if is_meaningless:
            heading_words = _clean_query_words(scene.section_id, max_words=3)
            narration_words = _clean_query_words(scene.narration_excerpt, max_words=4)
            derived = f"{heading_words} {narration_words}".strip()
            if not derived:
                raise VerticalSliceError("Could not derive meaningful asset query")
            scene.asset_query_hint = derived

    def _sanitize_error(self, e: Exception) -> str:
        msg = str(e)
        lower_msg = msg.lower()
        sensitive_patterns = ["://", "password", "secret", "token", "key=", "apikey", "authorization"]
        for pat in sensitive_patterns:
            if pat in lower_msg:
                return "[REDACTED]"
        return msg

    def _compute_streaming_sha(self, file_path: Path) -> str:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()
