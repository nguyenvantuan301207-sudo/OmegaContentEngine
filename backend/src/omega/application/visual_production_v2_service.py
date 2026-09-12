import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application import content_service
from omega.application.audio_mix_policy import (
    LicenseStatus,
    build_audio_mix_plan,
    build_background_music_plan,
    build_sfx_event_plan,
)
from omega.application.brand_asset_resolver import (
    BrandAssetResolutionError,
    BrandAssetResolver,
    BrandMediaKind,
    ResolvedBrandAsset,
)
from omega.application.ffmpeg_renderer import FFmpegRenderer
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import NarrationProvider
from omega.application.storyboard_engine import (
    StoryboardEngine,
    StoryboardScene,
    VisualStrategy,
)
from omega.application.subtitle_engine import (
    SubtitleRenderStyle,
    generate_karaoke_ass_document,
    generate_karaoke_cues,
)
from omega.application.template_payload_resolver import TemplatePayloadResolver
from omega.application.visual_asset_binding import BoundBrollAsset
from omega.application.visual_asset_engine import VisualAssetEngine, VisualAssetRequest
from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator
from omega.application.visual_direction import VisualAssetKind, VisualDirector
from omega.application.visual_template_renderer import VisualTemplateRenderer
from omega.domain.channel_dna import BrandFormat, resolve_production_brand_spec
from omega.domain.channel_style import ChannelStyleProfile, extract_channel_style_profile
from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
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


KARAOKE_SUBTITLE_VERSION = "v1"
KARAOKE_MAX_WORDS_PER_CUE = 5
KARAOKE_MAX_CHARS_PER_CUE = 36
SENTENCE_MAX_WORDS_PER_CUE = 16
SENTENCE_MAX_CHARS_PER_CUE = 80

VISUAL_DIRECTOR_VERSION = "v2"


class VerticalSliceRuntimeNarrationSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_index: int
    start_ms: int
    end_ms: int
    duration_ms: int


class VerticalSliceRuntimeSubtitleCue(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_index: int
    cue_order: int
    start_ms: int
    end_ms: int
    text: str


class VerticalSliceSceneResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence_index: int
    original_strategy: str
    effective_strategy: str
    template_id: str
    asset_kind: str | None
    asset_provider: str | None
    asset_id: str | None
    asset_query: str | None = None
    duration_seconds: float
    content_sha256: str
    audio_content_sha256: str | None = None
    audio_duration_seconds: float | None = None
    subtitle_cue_count: int | None = None
    subtitle_text_truncated: bool | None = None
    text_fitting: tuple[dict[str, Any], ...] = ()
    text_truncated: bool = False


@dataclass(frozen=True)
class VerticalSliceBackgroundMusicInput:
    audio_path: Path | str
    duration_ms: int
    license_status: str
    gain_db: float = -20.0
    fade_in_ms: int = 1000
    fade_out_ms: int = 1500


@dataclass(frozen=True)
class VerticalSliceSFXInput:
    event_id: str
    audio_path: Path | str
    start_ms: int
    duration_ms: int
    license_status: str
    gain_db: float


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
    narration_quality: str | None = None
    narration_source_refs: tuple[str, ...] = ()
    runtime_timeline_duration_ms: int | None = None
    runtime_narration_segments: tuple[VerticalSliceRuntimeNarrationSegment, ...] = ()
    runtime_subtitle_cues: tuple[VerticalSliceRuntimeSubtitleCue, ...] = ()
    runtime_scenes: tuple[VerticalSliceSceneResult, ...] = ()
    subtitle_style_applied: SubtitleRenderStyle | None = None
    style_profile_applied: ChannelStyleProfile | None = None
    effective_fps_mode: str = "CFR"
    subtitle_enabled: bool = False
    karaoke_subtitles_enabled: bool = False
    subtitle_mode: str = "sentence"


_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "we", "you", "they", "this", "that",
    "is", "are", "was", "were", "be", "been", "being",
    "it", "of", "in", "on", "for", "to", "with", "as", "by", "at", "from"
})

def _clean_query_words(text: str, max_words: int = 8) -> list[str]:
    cleaned = _NON_ALPHANUM_REGEX.sub(" ", text).lower()
    words = [w for w in _WHITESPACE_REGEX.split(cleaned.strip()) if w]
    meaningful = []
    seen = set()
    for w in words:
        if w not in _STOPWORDS and w not in seen:
            meaningful.append(w)
            seen.add(w)
            if len(meaningful) >= max_words:
                break
    return meaningful


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
        asset_orchestrator: VisualAssetOrchestrator | None,
        output_root: Path,
        browser_runtime_factory: Callable[[], Any] | None = None,
        video_renderer: VisualV2VideoRenderer | None = None,
        ffmpeg_renderer: FFmpegRenderer | None = None,
        narration_provider: NarrationProvider | None = None,
        narration_storage: LocalMediaStorageProvider | None = None,
        brand_asset_resolver: BrandAssetResolver | None = None,
        visual_asset_mode: str = "PEXELS",
    ):
        if visual_asset_mode not in ("PEXELS", "LOCAL_TEMPLATE_ONLY"):
            raise ValueError(f"Unsupported visual_asset_mode: {visual_asset_mode}")
        if visual_asset_mode == "PEXELS" and asset_orchestrator is None:
            raise ValueError("asset_orchestrator is required in PEXELS mode")
        self._orchestrator = asset_orchestrator
        self._visual_asset_mode = visual_asset_mode
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
        self._brand_asset_resolver = brand_asset_resolver
        if self._narration_provider and not self._narration_storage:
            raise ValueError("narration_storage is required when narration_provider is supplied")

    async def render_mission_execution(
        self,
        session: AsyncSession,
        mission_execution_id: UUID,
        content_request_id: UUID,
        *,
        fps: int = 24,
        voice_profile: dict[str, Any] | None = None,
        background_music: VerticalSliceBackgroundMusicInput | None = None,
        sfx_inputs: list[VerticalSliceSFXInput] | None = None,
        audio_mix_enabled: bool = False,
        subtitle_enabled: bool = False,
        subtitle_style: SubtitleRenderStyle | None = None,
        style_profile: ChannelStyleProfile | None = None,
    ) -> VerticalSliceRenderResult:
        audio_mix_enabled = background_music is not None or bool(sfx_inputs)

        if audio_mix_enabled and not self._narration_provider:
            raise VerticalSliceError("narration_provider MUST be configured when audio mix is enabled")


        if fps <= 0 or fps > 60:
            raise VerticalSliceError(f"Invalid fps: {fps}. Must be > 0 and <= 60.")

        # 1. True Mission Lineage Validation
        exec_stmt = (
            select(MissionExecution)
            .where(MissionExecution.id == mission_execution_id)
            .options(
                selectinload(MissionExecution.mission),
                selectinload(MissionExecution.channel_dna_revision),
            )
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
        pinned_dna_revision: ChannelDNARevision | None = mission_exec.channel_dna_revision
        if pinned_dna_revision is None:
            raise VerticalSliceError("MissionExecution is missing pinned ChannelDNARevision")
        resolved_brand = resolve_production_brand_spec(
            channel_dna_snapshot=pinned_dna_revision.snapshot,
            channel_dna_revision_id=pinned_dna_revision.id,
            production_format=BrandFormat.LONG_FORM,
        )
        try:
            resolved_logo, resolved_intro, resolved_outro = self._resolve_visual_brand_assets(
                mission.channel_id,
                resolved_brand.logo_asset,
                resolved_brand.intro_asset,
                resolved_brand.outro_asset,
            )
        except BrandAssetResolutionError as exc:
            raise VerticalSliceError(f"Brand asset resolution failed: {self._sanitize_error(exc)}") from exc

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

        if style_profile is None and mission.channel_id:
            try:
                ch_stmt = select(Channel).where(Channel.id == mission.channel_id)
                ch_res = await session.execute(ch_stmt)
                ch = ch_res.scalar_one_or_none()
                if ch:
                    style_profile = extract_channel_style_profile(ch.metadata_)
                else:
                    style_profile = ChannelStyleProfile()
            except Exception:
                style_profile = ChannelStyleProfile()
        elif style_profile is None:
            style_profile = ChannelStyleProfile()

        if subtitle_enabled and not self._narration_provider:
            raise VerticalSliceError("Subtitles require narration")
        if subtitle_style is not None:
            resolved_subtitle_style = subtitle_style
        elif style_profile is not None:
            resolved_subtitle_style = style_profile.resolve_effective_style()
        else:
            resolved_subtitle_style = SubtitleRenderStyle()

        # 3. Deterministic Run Fingerprint & Idempotency Check
        fingerprint_input = (
            f"omega-vertical-slice-v0:{mission_execution_id}:"
            f"{content_request_id}:{script_version.id}:{fps}:"
            f"visual-director-{VISUAL_DIRECTOR_VERSION}:visual-asset-selection-v2:"
            f"visual-asset-mode:{self._visual_asset_mode}"
        )
        if style_profile:
            fingerprint_input += f":style-profile-v1:{style_profile.model_dump_json()}"
        if resolved_brand.identity is not None:
            fingerprint_input += f":brand-spec-v1:{resolved_brand.identity}"
        resolved_brand_identity = self._resolved_brand_asset_identity(
            resolved_logo,
            resolved_intro,
            resolved_outro,
        )
        if resolved_brand_identity is not None:
            fingerprint_input += f":brand-render-assets-v1:{resolved_brand_identity}"
        if self._narration_provider:
            fingerprint_input += ":narrated"
            provider_cls = self._narration_provider.__class__.__name__
            model = getattr(self._narration_provider, "model", None)
            voice = getattr(self._narration_provider, "default_voice", None)
            fingerprint_input += f":{provider_cls}:{model}:{voice}"
            if voice_profile:
                normalized_vp = json.dumps(voice_profile, sort_keys=True)
                fingerprint_input += f":{normalized_vp}"
            if subtitle_enabled:
                subtitle_marker = f"karaoke:{KARAOKE_SUBTITLE_VERSION}:words={KARAOKE_MAX_WORDS_PER_CUE}:chars={KARAOKE_MAX_CHARS_PER_CUE}"
                fingerprint_input += f":{subtitle_marker}"
                fingerprint_input += ":style=" + resolved_subtitle_style.model_dump_json()
        normalized_sfx = []
        if audio_mix_enabled:
            audio_mix_fp = {}
            if background_music:
                p = Path(background_music.audio_path).resolve()
                if not p.is_file() or p.stat().st_size == 0:
                    raise VerticalSliceError("Background music missing or empty")
                bgm_sha = self._compute_streaming_sha(p)
                audio_mix_fp["background_music"] = {
                    "source_sha256": bgm_sha,
                    "duration_ms": background_music.duration_ms,
                    "license_status": str(background_music.license_status),
                    "gain_db": background_music.gain_db,
                    "fade_in_ms": background_music.fade_in_ms,
                    "fade_out_ms": background_music.fade_out_ms,
                }

            if sfx_inputs:
                raw_sfx = []
                for sfx in sfx_inputs:
                    p = Path(sfx.audio_path).resolve()
                    if not p.is_file() or p.stat().st_size == 0:
                        raise VerticalSliceError("SFX file missing or empty")
                    s_sha = self._compute_streaming_sha(p)
                    raw_sfx.append((sfx, s_sha))

                # Sort by (start_ms, event_id, source_sha256)
                raw_sfx.sort(key=lambda x: (x[0].start_ms, x[0].event_id, x[1]))
                sfx_fp_list = []
                for sfx, s_sha in raw_sfx:
                    normalized_sfx.append((sfx, s_sha, Path(sfx.audio_path).resolve()))
                    sfx_fp_list.append({
                        "source_sha256": s_sha,
                        "event_id": sfx.event_id,
                        "start_ms": sfx.start_ms,
                        "duration_ms": sfx.duration_ms,
                        "license_status": str(sfx.license_status),
                        "gain_db": sfx.gain_db,
                    })
                audio_mix_fp["sfx_events"] = sfx_fp_list

            fingerprint_input += ":audio-mix-v1:" + json.dumps(audio_mix_fp, sort_keys=True)

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

                if manifest_data.get("scene_artifacts_version") == "v1":
                    scenes_dir = run_dir / "scenes"
                    for scene_data in manifest_data.get("scenes", []):
                        seq_idx = scene_data.get("sequence_index")
                        if seq_idx is None or seq_idx <= 0:
                            raise VerticalSliceError("Invalid sequence index in manifest")
                        scene_mp4 = scenes_dir / f"scene_{seq_idx:03d}.mp4"
                        if not scene_mp4.is_file() or scene_mp4.stat().st_size <= 0:
                            raise VerticalSliceError("Persisted scene missing or empty")
                        with open(scene_mp4, "rb") as f:
                            s_hdr = f.read(4096)
                            if b"ftyp" not in s_hdr:
                                raise VerticalSliceError("Persisted scene missing ftyp header")
                        if self._compute_streaming_sha(scene_mp4) != scene_data.get("content_sha256"):
                            raise VerticalSliceError("Persisted scene SHA256 mismatch")

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
                    narration_quality=manifest_data.get("narration_quality"),
                    narration_source_refs=tuple(manifest_data.get("narration_source_refs", [])),
                    runtime_timeline_duration_ms=manifest_data.get("runtime_timeline_duration_ms"),
                    runtime_narration_segments=tuple(
                        VerticalSliceRuntimeNarrationSegment(**s) for s in manifest_data.get("runtime_narration_segments", [])
                    ),
                    runtime_subtitle_cues=tuple(
                        VerticalSliceRuntimeSubtitleCue(**c) for c in manifest_data.get("runtime_subtitle_cues", [])
                    ),
                    runtime_scenes=tuple(
                        VerticalSliceSceneResult(**s) for s in manifest_data.get("scenes", [])
                    ),
                    subtitle_style_applied=(
                        SubtitleRenderStyle(**manifest_data["subtitle_style_applied"])
                        if manifest_data.get("subtitle_style_applied")
                        else None
                    ),
                    style_profile_applied=(
                        ChannelStyleProfile(**manifest_data["style_profile_applied"])
                        if manifest_data.get("style_profile_applied")
                        else None
                    ),
                    effective_fps_mode=manifest_data.get("effective_fps_mode", "CFR"),
                    subtitle_enabled=bool(
                        manifest_data.get("subtitle_enabled", manifest_data.get("karaoke_subtitles_enabled", False))
                    ),
                    karaoke_subtitles_enabled=bool(
                        manifest_data.get("karaoke_subtitles_enabled", False)
                        if "subtitle_enabled" in manifest_data
                        else (
                            manifest_data.get("subtitle_style_applied", {}).get("karaoke", False)
                            if manifest_data.get("subtitle_style_applied")
                            else manifest_data.get("karaoke_subtitles_enabled", False)
                        )
                    ),
                    subtitle_mode=str(
                        manifest_data.get(
                            "subtitle_mode",
                            "karaoke" if (
                                manifest_data.get("karaoke_subtitles_enabled")
                                and (
                                    "subtitle_enabled" not in manifest_data
                                    or manifest_data.get("subtitle_style_applied", {}).get("karaoke", False)
                                )
                            ) else "sentence"
                        )
                    ),
                )
            except Exception as e:
                raise VerticalSliceError(f"Incomplete or corrupt prior run: {e}") from e

        if final_exists or manifest_exists:
            raise VerticalSliceError("Incomplete prior run detected (found partial final/manifest)")

        # 4. Script -> Storyboard
        script_dict = ScriptStoryboardAdapter.to_script_dict(script_version)
        pacing = style_profile.pacing if style_profile else "BALANCED"
        try:
            storyboard = self._storyboard_engine.generate_storyboard(script_dict, pacing=pacing)
        except TypeError:
            storyboard = self._storyboard_engine.generate_storyboard(script_dict)

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
            narration_total_duration_ms = 0

            template_scenes = 0
            image_scenes = 0
            broll_scenes = 0

            narration_qualities: list[str] = []
            narration_source_refs_list: list[str] = []

            runtime_cursor_ms = 0
            runtime_cue_order = 1
            runtime_narration_segments = []
            runtime_subtitle_cues = []


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
                    effective_scene = self._apply_visual_asset_mode_policy(effective_scene)
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
                        narration_total_duration_ms += duration_ms

                        scene_start_ms = runtime_cursor_ms
                        scene_end_ms = scene_start_ms + duration_ms

                        runtime_narration_segments.append(
                            VerticalSliceRuntimeNarrationSegment(
                                scene_index=scene.sequence_index,
                                start_ms=scene_start_ms,
                                end_ms=scene_end_ms,
                                duration_ms=duration_ms,
                            )
                        )
                        runtime_cursor_ms = scene_end_ms

                        n_qual = audio_asset.get("narration_quality")
                        if n_qual:
                            narration_qualities.append(n_qual)
                        n_ref = audio_asset.get("source_ref")
                        if n_ref and n_ref not in narration_source_refs_list:
                            narration_source_refs_list.append(n_ref)

                        scene_subtitle_cues = 0
                        scene_subtitle_text_truncated = False
                        if subtitle_enabled:
                            segment = {
                                "text": scene.narration_excerpt,
                                "start_ms": 0,
                                "duration_ms": duration_ms,
                            }
                            try:
                                scene_cues = generate_karaoke_cues(
                                    [segment],
                                    max_words_per_cue=KARAOKE_MAX_WORDS_PER_CUE if resolved_subtitle_style.karaoke else SENTENCE_MAX_WORDS_PER_CUE,
                                    max_chars_per_cue=KARAOKE_MAX_CHARS_PER_CUE if resolved_subtitle_style.karaoke else SENTENCE_MAX_CHARS_PER_CUE,
                                    sentence_mode=not resolved_subtitle_style.karaoke,
                                )
                                scene_subtitle_cues = len(scene_cues)

                                for scene_cue in scene_cues:
                                    local_start = scene_cue["start_ms"]
                                    local_end = scene_cue["end_ms"]
                                    if not (local_start >= 0 and local_end > local_start and local_end <= duration_ms):
                                        raise VerticalSliceError("Malformed runtime karaoke cue")

                                    absolute_start = scene_start_ms + local_start
                                    absolute_end = scene_start_ms + local_end

                                    runtime_subtitle_cues.append(
                                        VerticalSliceRuntimeSubtitleCue(
                                            scene_index=scene.sequence_index,
                                            cue_order=runtime_cue_order,
                                            start_ms=absolute_start,
                                            end_ms=absolute_end,
                                            text=str(scene_cue["text"]).strip(),
                                        )
                                    )
                                    runtime_cue_order += 1

                                ass_document = generate_karaoke_ass_document(
                                    scene_cues,
                                    width=1920,
                                    height=1080,
                                    style=resolved_subtitle_style,
                                )
                                ass_content = ass_document.content
                                scene_subtitle_text_truncated = any(
                                    item.text_truncated for item in ass_document.layout
                                )
                                ass_path = work_dir / f"scene_{scene.sequence_index:03d}.ass"
                                with open(ass_path, "w", encoding="utf-8") as f:
                                    f.write(ass_content)
                                if not ass_path.exists() or ass_path.stat().st_size <= 0:
                                    raise VerticalSliceError(f"ASS file missing or empty for scene {scene.sequence_index}")
                            except Exception as e:
                                raise VerticalSliceError(f"Karaoke generation failed: {self._sanitize_error(e)}") from e

                    # Meaningful query fallback for IMAGE and BROLL
                    if effective_strategy in (VisualStrategy.IMAGE, VisualStrategy.BROLL):
                        self._ensure_meaningful_query(effective_scene)

                    direction = self._visual_director.resolve(effective_scene)
                    if (
                        self._visual_asset_mode == "LOCAL_TEMPLATE_ONLY"
                        and direction.asset_requirements
                    ):
                        raise VerticalSliceError(
                            "LOCAL_TEMPLATE_ONLY scene produced an external asset requirement"
                        )
                    payload = self._template_resolver.resolve(effective_scene, direction)

                    assets: tuple[Any, ...] = ()
                    broll_asset: BoundBrollAsset | None = None
                    asset_kind_str: str | None = None
                    asset_provider_str: str | None = None
                    asset_id_str: str | None = None
                    asset_query_str: str | None = None

                    if direction.asset_requirements:
                        if self._orchestrator is None:
                            raise VerticalSliceError(
                                "Asset resolution requires an asset orchestrator"
                            )
                        req_spec = direction.asset_requirements[0]
                        asset_kind_str = req_spec.kind.value
                        asset_request: VisualAssetRequest = self._visual_asset_engine.build_request(
                            scene_index=effective_scene.sequence_index,
                            requirement=req_spec,
                        )
                        if asset_request is None:
                            raise VerticalSliceError("Could not build required visual asset request")
                        asset_query_str = asset_request.query

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
                        document = self._template_renderer.render(
                            payload,
                            assets=assets,
                            accent_color=style_profile.accent_color if style_profile else None,
                            bg_color=style_profile.bg_color if style_profile else None,
                        )
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
                        mux_video_input = scene_visual_out_path
                        if subtitle_enabled:
                            scene_subtitled_visual_path = work_dir / f"scene_{scene.sequence_index:03d}_subtitled.mp4"
                            try:
                                await self._ffmpeg_renderer.burn_ass_subtitles(
                                    video_path=scene_visual_out_path,
                                    ass_path=ass_path,
                                    output_path=scene_subtitled_visual_path,
                                )
                            except Exception as e:
                                raise VerticalSliceError(f"ASS burn failed for scene {scene.sequence_index}: {self._sanitize_error(e)}") from e
                            if not scene_subtitled_visual_path.exists() or scene_subtitled_visual_path.stat().st_size <= 0:
                                raise VerticalSliceError(f"Subtitle burned output missing or empty for scene {scene.sequence_index}")
                            mux_video_input = scene_subtitled_visual_path

                        try:
                            await self._ffmpeg_renderer.mux_video_audio(
                                video_path=mux_video_input,
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
                            asset_query=asset_query_str,
                            duration_seconds=actual_scene_duration_seconds,
                            content_sha256=final_scene_sha,
                            audio_content_sha256=audio_sha,
                            audio_duration_seconds=audio_duration_sec,
                            subtitle_cue_count=scene_subtitle_cues if subtitle_enabled else None,
                            subtitle_text_truncated=scene_subtitle_text_truncated if subtitle_enabled else None,
                            text_fitting=tuple(
                                decision.model_dump() for decision in document.text_fitting
                            ),
                            text_truncated=any(
                                decision.text_truncated for decision in document.text_fitting
                            ),
                        )
                    )

            if self._narration_provider:
                runtime_timeline_duration_ms = runtime_cursor_ms
                if runtime_timeline_duration_ms != narration_total_duration_ms:
                    raise VerticalSliceError("Timeline duration mismatch")

                for i in range(1, len(runtime_narration_segments)):
                    if runtime_narration_segments[i].start_ms != runtime_narration_segments[i-1].end_ms:
                        raise VerticalSliceError("Narration segments not contiguous")

                for seg in runtime_narration_segments:
                    if not (0 <= seg.start_ms < seg.end_ms <= runtime_timeline_duration_ms):
                        raise VerticalSliceError("Narration segment out of bounds")

                for cue in runtime_subtitle_cues:
                    if not (0 <= cue.start_ms < cue.end_ms <= runtime_timeline_duration_ms):
                        raise VerticalSliceError("Subtitle cue out of bounds")

                for i in range(1, len(runtime_subtitle_cues)):
                    if runtime_subtitle_cues[i].start_ms < runtime_subtitle_cues[i-1].end_ms:
                        raise VerticalSliceError("Subtitle cues overlap")
            else:
                runtime_timeline_duration_ms = None
                runtime_narration_segments = []
                runtime_subtitle_cues = []

            # 8. Deterministic brand composition and final concatenation
            branded_content_paths = ordered_scene_paths
            if resolved_logo is not None and resolved_brand.channel_bug is not None:
                logo_policy = resolved_brand.channel_bug
                branded_content_paths = []
                for scene_path in ordered_scene_paths:
                    branded_path = work_dir / f"{scene_path.stem}_branded.mp4"
                    try:
                        await self._ffmpeg_renderer.overlay_logo(
                            video_path=scene_path,
                            logo_path=resolved_logo.local_path,
                            output_path=branded_path,
                            scale=logo_policy.scale,
                            opacity=logo_policy.opacity,
                            position=logo_policy.position.value,
                            safe_margin_x=logo_policy.safe_margin_x,
                            safe_margin_y=logo_policy.safe_margin_y,
                        )
                    except Exception as e:
                        raise VerticalSliceError(f"Logo overlay failed: {self._sanitize_error(e)}") from e
                    branded_content_paths.append(branded_path)

            generated_content_mp4 = work_dir / "generated_content.mp4"
            try:
                await self._ffmpeg_renderer.concatenate_clips(
                    clip_paths=branded_content_paths,
                    output_path=generated_content_mp4,
                    srt_path=None,
                    target_fps=fps,
                )
            except Exception as e:
                raise VerticalSliceError(f"Generated content concatenation failed: {self._sanitize_error(e)}") from e

            if not generated_content_mp4.is_file() or generated_content_mp4.stat().st_size <= 0:
                raise VerticalSliceError("Generated content MP4 is missing or empty")

            with open(generated_content_mp4, "rb") as f:
                hdr = f.read(4096)
                if b"ftyp" not in hdr:
                    raise VerticalSliceError("Generated content MP4 missing ftyp header")

            # 8.5 Master Audio Mix
            working_content_mp4 = generated_content_mp4
            audio_mix_manifest = {
                "audio_mix_enabled": False,
                "background_music_enabled": False,
                "background_music_attribution_required": False,
                "sfx_event_count": 0,
                "sfx_attribution_required_count": 0,
                "audio_mix_target_duration_ms": 0,
            }

            if audio_mix_enabled:
                audio_mix_target_duration_ms = narration_total_duration_ms
                if audio_mix_target_duration_ms <= 0:
                    raise VerticalSliceError("Audio mix enabled but narration total duration is <= 0")

                bgm_plan = None
                if background_music:
                    try:
                        bgm_plan = build_background_music_plan(
                            video_duration_ms=audio_mix_target_duration_ms,
                            music_duration_ms=background_music.duration_ms,
                            license_status=LicenseStatus(background_music.license_status),
                            gain_db=background_music.gain_db,
                            fade_in_ms=background_music.fade_in_ms,
                            fade_out_ms=background_music.fade_out_ms,
                        )
                    except Exception as e:
                        raise VerticalSliceError(f"Background music plan failed: {self._sanitize_error(e)}") from e

                sfx_plans = []
                for sfx, _s_sha, _p in normalized_sfx:
                    try:
                        plan = build_sfx_event_plan(
                            event_id=sfx.event_id,
                            start_ms=sfx.start_ms,
                            duration_ms=sfx.duration_ms,
                            video_duration_ms=audio_mix_target_duration_ms,
                            license_status=LicenseStatus(sfx.license_status),
                            gain_db=sfx.gain_db,
                        )
                        sfx_plans.append(plan)
                    except Exception as e:
                        raise VerticalSliceError(f"SFX plan failed for {sfx.event_id}: {self._sanitize_error(e)}") from e

                try:
                    mix_plan = build_audio_mix_plan(
                        video_duration_ms=audio_mix_target_duration_ms,
                        background_music=bgm_plan,
                        sfx_events=sfx_plans,
                    )
                except Exception as e:
                    raise VerticalSliceError(f"Audio mix plan failed: {self._sanitize_error(e)}") from e

                bgm_path = None
                bgm_gain = 0.0
                bgm_loop = False
                bgm_fade_in = 0
                bgm_fade_out = 0

                if mix_plan.background_music:
                    bgm_path = Path(background_music.audio_path).resolve()
                    bgm_gain = mix_plan.background_music.gain_db
                    bgm_loop = mix_plan.background_music.loop_required
                    bgm_fade_in = mix_plan.background_music.fade_in_ms
                    bgm_fade_out = mix_plan.background_music.fade_out_ms

                sfx_mix_inputs = []
                for i, p_event in enumerate(mix_plan.sfx_events):
                    sfx_mix_inputs.append(
                        FFmpegRenderer.SFXMixInput(
                            audio_path=normalized_sfx[i][2],
                            start_ms=p_event.start_ms,
                            duration_ms=p_event.duration_ms,
                            gain_db=p_event.gain_db,
                        )
                    )

                mixed_content_mp4 = work_dir / "mixed_content.mp4"
                try:
                    await self._ffmpeg_renderer.mix_master_audio(
                        video_path=generated_content_mp4,
                        output_path=mixed_content_mp4,
                        target_duration_ms=audio_mix_target_duration_ms,
                        background_music_path=bgm_path,
                        background_music_gain_db=bgm_gain,
                        background_music_loop_required=bgm_loop,
                        background_music_fade_in_ms=bgm_fade_in,
                        background_music_fade_out_ms=bgm_fade_out,
                        sfx_inputs=sfx_mix_inputs,
                    )
                except Exception as e:
                    raise VerticalSliceError(f"Master audio mix failed: {self._sanitize_error(e)}") from e

                if not mixed_content_mp4.is_file() or mixed_content_mp4.stat().st_size <= 0:
                    raise VerticalSliceError("Mixed MP4 missing or empty")

                with open(mixed_content_mp4, "rb") as f:
                    hdr = f.read(4096)
                    if b"ftyp" not in hdr:
                        raise VerticalSliceError("Mixed MP4 missing ftyp header")

                working_content_mp4 = mixed_content_mp4

                audio_mix_manifest = {
                    "audio_mix_enabled": True,
                    "background_music_enabled": mix_plan.background_music is not None,
                    "background_music_attribution_required": mix_plan.background_music.attribution_required if mix_plan.background_music else False,
                    "sfx_event_count": len(mix_plan.sfx_events),
                    "sfx_attribution_required_count": sum(1 for e in mix_plan.sfx_events if e.attribution_required),
                    "audio_mix_target_duration_ms": audio_mix_target_duration_ms,
                }

            if resolved_intro is not None or resolved_outro is not None:
                final_clip_paths = self._brand_clip_paths(
                    [working_content_mp4],
                    resolved_intro,
                    resolved_outro,
                )
                final_branded_mp4 = work_dir / "final_branded.mp4"
                try:
                    await self._ffmpeg_renderer.concatenate_clips(
                        clip_paths=final_clip_paths,
                        output_path=final_branded_mp4,
                        srt_path=None,
                        target_fps=fps,
                    )
                except Exception as e:
                    raise VerticalSliceError(f"Final brand concatenation failed: {self._sanitize_error(e)}") from e
                if not final_branded_mp4.is_file() or final_branded_mp4.stat().st_size <= 0:
                    raise VerticalSliceError("Final branded MP4 is missing or empty")
                working_final_mp4 = final_branded_mp4
            else:
                working_final_mp4 = working_content_mp4

            final_sha = self._compute_streaming_sha(working_final_mp4)
            working_final_mp4.replace(final_mp4_path)

            scenes_dir = run_dir / "scenes"
            scenes_dir.mkdir(parents=True, exist_ok=True)
            for scene_res, scene_path in zip(scene_results, ordered_scene_paths, strict=True):
                if not scene_path.is_file() or scene_path.stat().st_size <= 0:
                    raise VerticalSliceError(f"Scene {scene_res.sequence_index} artifact missing or empty")
                with open(scene_path, "rb") as f:
                    if b"ftyp" not in f.read(4096):
                        raise VerticalSliceError(f"Scene {scene_res.sequence_index} artifact missing ftyp header")
                c_sha = self._compute_streaming_sha(scene_path)
                if c_sha != scene_res.content_sha256:
                    print(f'MISMATCH: c_sha={c_sha} vs content_sha={scene_res.content_sha256} path={scene_path}')
                    raise VerticalSliceError(f"Scene {scene_res.sequence_index} artifact SHA mismatch")
                final_scene_path = scenes_dir / f"scene_{scene_res.sequence_index:03d}.mp4"
                scene_path.replace(final_scene_path)

            final_narration_quality = None
            if "DEVELOPMENT_FALLBACK" in narration_qualities:
                final_narration_quality = "DEVELOPMENT_FALLBACK"
            elif "NEURAL_PRODUCTION" in narration_qualities:
                final_narration_quality = "NEURAL_PRODUCTION"

            final_narration_source_refs = tuple(narration_source_refs_list)

            manifest_content = {
                "run_fingerprint": run_fingerprint,
                "visual_asset_mode": self._visual_asset_mode,
                "scene_artifacts_version": "v1",
                "resolved_brand_spec": resolved_brand.model_dump(mode="json"),
                "resolved_brand_identity": resolved_brand.identity,
                "resolved_brand_asset_identity": resolved_brand_identity,
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
                "target_fps": fps,
                "effective_fps_mode": "CFR",
                "content_sha256": final_sha,
                **audio_mix_manifest,
                "narration_enabled": bool(self._narration_provider),
                "subtitle_enabled": subtitle_enabled,
                "karaoke_subtitles_enabled": bool(
                    subtitle_enabled
                    and resolved_subtitle_style is not None
                    and getattr(resolved_subtitle_style, "karaoke", False)
                ),
                "subtitle_mode": (
                    "karaoke"
                    if (resolved_subtitle_style is not None and getattr(resolved_subtitle_style, "karaoke", False))
                    else "sentence"
                ),
                "subtitle_style_applied": (
                    resolved_subtitle_style.model_dump() if subtitle_enabled else None
                ),
                "style_profile_applied": (
                    style_profile.model_dump() if style_profile else None
                ),
                "narration_provider": self._narration_provider.__class__.__name__ if self._narration_provider else None,
                "narration_model": getattr(self._narration_provider, "model", None) if self._narration_provider else None,
                "narration_voice": getattr(self._narration_provider, "default_voice", None) if self._narration_provider else None,
                "narration_quality": final_narration_quality,
                "narration_source_refs": list(final_narration_source_refs),
                "runtime_timeline_duration_ms": runtime_timeline_duration_ms,
                "runtime_narration_segments": [s.model_dump() for s in runtime_narration_segments],
                "runtime_subtitle_cues": [c.model_dump() for c in runtime_subtitle_cues],
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
            narration_quality=final_narration_quality,
            narration_source_refs=final_narration_source_refs,
            runtime_timeline_duration_ms=runtime_timeline_duration_ms,
            runtime_narration_segments=tuple(runtime_narration_segments),
            runtime_subtitle_cues=tuple(runtime_subtitle_cues),
            runtime_scenes=tuple(scene_results),
            subtitle_style_applied=resolved_subtitle_style if subtitle_enabled else None,
            style_profile_applied=style_profile,
            effective_fps_mode="CFR",
            subtitle_enabled=subtitle_enabled,
            karaoke_subtitles_enabled=bool(
                subtitle_enabled
                and resolved_subtitle_style is not None
                and getattr(resolved_subtitle_style, "karaoke", False)
            ),
            subtitle_mode=(
                "karaoke"
                if (resolved_subtitle_style is not None and getattr(resolved_subtitle_style, "karaoke", False))
                else "sentence"
            ),
        )


    async def regenerate_scene(
        self,
        session: AsyncSession,
        mission_execution_id: UUID,
        content_request_id: UUID,
        base_run_fingerprint: str,
        scene_index: int,
        visual_strategy_override: VisualStrategy | None = None,
        asset_query_override: str | None = None,
    ):
        if not re.match(r"^[0-9a-f]{64}$", base_run_fingerprint):
            raise VerticalSliceError("Invalid run_fingerprint format")

        base_dir = self._output_root / str(mission_execution_id) / base_run_fingerprint
        manifest_path = base_dir / "manifest.json"

        if not manifest_path.exists():
            raise VerticalSliceError("Base manifest not found")

        final_mp4 = base_dir / "final.mp4"
        if not final_mp4.is_file() or final_mp4.stat().st_size <= 0:
            raise VerticalSliceError("Base preview requires valid final.mp4")

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        if manifest.get("run_fingerprint") != base_run_fingerprint:
            raise VerticalSliceError("Base manifest run_fingerprint mismatch")

        if manifest.get("scene_artifacts_version") != "v1":
            raise VerticalSliceError("Scene preview unavailable for legacy run")

        if manifest.get("narration_enabled") or manifest.get("karaoke_subtitles_enabled") or manifest.get("subtitle_enabled") or manifest.get("audio_mix_enabled"):
            raise VerticalSliceError("V1 regeneration unsupported for audio/subtitle enabled base runs")

        base_scenes = manifest.get("scenes", [])
        scene_info = next((s for s in base_scenes if s.get("sequence_index") == scene_index), None)
        if not scene_info:
            raise VerticalSliceError(f"Scene {scene_index} not found in manifest")

        for s_info in base_scenes:
            s_idx = s_info["sequence_index"]
            s_mp4 = base_dir / "scenes" / f"scene_{s_idx:03d}.mp4"
            if not s_mp4.is_file() or s_mp4.stat().st_size <= 0:
                raise VerticalSliceError(f"Base scene {s_idx} missing or empty")
            with open(s_mp4, "rb") as f:
                if b"ftyp" not in f.read(4096):
                    raise VerticalSliceError(f"Base scene {s_idx} missing ftyp header")
            if self._compute_streaming_sha(s_mp4) != s_info.get("content_sha256"):
                raise VerticalSliceError(f"Base scene {s_idx} SHA256 mismatch")

        if visual_strategy_override == VisualStrategy.SCREENSHOT:
            raise VerticalSliceError("SCREENSHOT materialization is not yet implemented")

        norm_strat = visual_strategy_override.value if visual_strategy_override else ""
        norm_query = asset_query_override.strip() if asset_query_override else ""

        fingerprint_input = f"scene-regenerate-v1:{base_run_fingerprint}:{scene_index}:{norm_strat}:{norm_query}"
        rev_fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()

        rev_dir = self._output_root / str(mission_execution_id) / rev_fingerprint
        if rev_dir.exists():
            rev_manifest_path = rev_dir / "manifest.json"
            rev_final_mp4 = rev_dir / "final.mp4"
            if not rev_manifest_path.exists() or not rev_final_mp4.is_file() or rev_final_mp4.stat().st_size <= 0:
                raise VerticalSliceError("Existing revision directory is partial or corrupt")
            with open(rev_manifest_path, encoding="utf-8") as f:
                rev_manifest = json.load(f)
            if rev_manifest.get("run_fingerprint") != rev_fingerprint:
                raise VerticalSliceError("Existing revision manifest fingerprint mismatch")
            if rev_manifest.get("base_run_fingerprint") != base_run_fingerprint:
                raise VerticalSliceError("Existing revision base fingerprint mismatch")
            if rev_manifest.get("regenerated_scene_indices") != [scene_index]:
                raise VerticalSliceError("Existing revision indices mismatch")
            with open(rev_final_mp4, "rb") as f:
                if b"ftyp" not in f.read(4096):
                    raise VerticalSliceError("Existing revision final.mp4 missing ftyp header")
            if self._compute_streaming_sha(rev_final_mp4) != rev_manifest.get("content_sha256"):
                raise VerticalSliceError("Existing revision final.mp4 SHA mismatch")

            for s_info in rev_manifest.get("scenes", []):
                s_idx = s_info["sequence_index"]
                s_mp4 = rev_dir / "scenes" / f"scene_{s_idx:03d}.mp4"
                if not s_mp4.is_file() or s_mp4.stat().st_size <= 0:
                    raise VerticalSliceError(f"Existing revision scene {s_idx} missing or empty")
                with open(s_mp4, "rb") as f:
                    if b"ftyp" not in f.read(4096):
                        raise VerticalSliceError(f"Existing revision scene {s_idx} missing ftyp header")
                if self._compute_streaming_sha(s_mp4) != s_info.get("content_sha256"):
                    raise VerticalSliceError(f"Existing revision scene {s_idx} SHA mismatch")

            return VerticalSliceRenderResult(
                mission_id=UUID(rev_manifest["mission_id"]),
                mission_execution_id=mission_execution_id,
                content_request_id=content_request_id,
                script_version_id=UUID(rev_manifest["script_version_id"]),
                scene_count=rev_manifest["scene_count"],
                template_scene_count=rev_manifest.get("template_scene_count", 0),
                image_scene_count=rev_manifest.get("image_scene_count", 0),
                broll_scene_count=rev_manifest.get("broll_scene_count", 0),
                duration_seconds=rev_manifest["duration_seconds"],
                width=rev_manifest["width"],
                height=rev_manifest["height"],
                fps=rev_manifest["fps"],
                output_path=rev_final_mp4,
                content_sha256=rev_manifest["content_sha256"],
                run_fingerprint=rev_fingerprint,
                narration_quality=rev_manifest.get("narration_quality"),
                narration_source_refs=tuple(rev_manifest.get("narration_source_refs", [])),
                runtime_timeline_duration_ms=rev_manifest.get("runtime_timeline_duration_ms"),
                runtime_narration_segments=tuple(
                    VerticalSliceRuntimeNarrationSegment(**s) for s in rev_manifest.get("runtime_narration_segments", [])
                ),
                runtime_subtitle_cues=tuple(
                    VerticalSliceRuntimeSubtitleCue(**c) for c in rev_manifest.get("runtime_subtitle_cues", [])
                ),
                runtime_scenes=tuple(
                    VerticalSliceSceneResult(**s) for s in rev_manifest.get("scenes", [])
                ),
            )

        work_dir = self._output_root / "work" / rev_fingerprint
        if work_dir.exists():
            shutil.rmtree(work_dir)

        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            script_version_id = UUID(manifest["script_version_id"])
            script_version = await session.get(ScriptVersion, script_version_id)
            if not script_version:
                raise VerticalSliceError("Script version not found")

            sdict = ScriptStoryboardAdapter.to_script_dict(script_version)
            plan = self._storyboard_engine.generate_storyboard(sdict)

            target_scene = next((s for s in plan.scenes if s.sequence_index == scene_index), None)
            if not target_scene:
                raise VerticalSliceError("Scene not found in storyboard")

            original_strategy = target_scene.visual_strategy

            if visual_strategy_override:
                target_scene.visual_strategy = visual_strategy_override
            if asset_query_override is not None:
                target_scene.asset_query_hint = asset_query_override

            target_scene = self._apply_v0_compatibility(target_scene)
            target_scene = self._apply_visual_asset_mode_policy(target_scene)
            effective_strategy = target_scene.visual_strategy

            if effective_strategy in (VisualStrategy.IMAGE, VisualStrategy.BROLL):
                self._ensure_meaningful_query(target_scene)

            fps = manifest["fps"]
            actual_duration = scene_info["duration_seconds"]
            broll_asset = None
            asset_kind_str = None
            asset_provider_str = None
            asset_id_str = None
            asset_query_str = None

            direction = self._visual_director.resolve(target_scene)
            if (
                self._visual_asset_mode == "LOCAL_TEMPLATE_ONLY"
                and direction.asset_requirements
            ):
                raise VerticalSliceError(
                    "LOCAL_TEMPLATE_ONLY scene produced an external asset requirement"
                )
            payload = self._template_resolver.resolve(target_scene, direction)

            assets = ()
            if direction.asset_requirements:
                if self._orchestrator is None:
                    raise VerticalSliceError(
                        "Asset resolution requires an asset orchestrator"
                    )
                req_spec = direction.asset_requirements[0]
                asset_kind_str = req_spec.kind.value
                asset_request = self._visual_asset_engine.build_request(
                    scene_index=target_scene.sequence_index,
                    requirement=req_spec,
                )
                if asset_request is None:
                    raise VerticalSliceError("Could not build required visual asset request")

                asset_query_str = asset_request.query

                try:
                    resolved_asset = await self._orchestrator.resolve(asset_request)
                except Exception as e:
                    raise VerticalSliceError(f"Asset orchestrator failed: {self._sanitize_error(e)}") from e

                asset_provider_str = resolved_asset.provider
                asset_id_str = resolved_asset.asset_id

                if req_spec.kind == VisualAssetKind.IMAGE:
                    bound_img = VisualAssetMaterializer.materialize(resolved_asset)
                    assets = (bound_img,)
                elif req_spec.kind == VisualAssetKind.BROLL:
                    bound_broll = VisualAssetMaterializer.materialize_broll(resolved_asset)
                    assets = (bound_broll,)
                    broll_asset = bound_broll
                else:
                    raise VerticalSliceError(f"Unsupported asset requirement kind: {req_spec.kind}")

            base_style_profile = manifest.get("style_profile_applied")
            accent_color = base_style_profile.get("accent_color") if base_style_profile else None
            bg_color = base_style_profile.get("bg_color") if base_style_profile else None
            try:
                document = self._template_renderer.render(
                    payload,
                    assets=assets,
                    accent_color=accent_color,
                    bg_color=bg_color,
                )
            except Exception as e:
                raise VerticalSliceError(f"Template renderer failed: {self._sanitize_error(e)}") from e

            async with self._browser_runtime_factory() as browser_ctx:
                render_res = await self._video_renderer.render_clip(
                    document=document,
                    motion_profile=direction.motion_profile,
                    duration_seconds=actual_duration,
                    output_path=work_dir / f"scene_{scene_index:03d}.mp4",
                    browser_runtime=browser_ctx,
                    fps=fps,
                    broll_asset=broll_asset,
                )

            if not render_res.output_path.exists() or render_res.output_path.stat().st_size <= 0:
                raise VerticalSliceError("Rendered output is missing or empty")
            with open(render_res.output_path, "rb") as f:
                if b"ftyp" not in f.read(4096):
                    raise VerticalSliceError("Rendered output missing ftyp header")

            new_sha = self._compute_streaming_sha(render_res.output_path)
            if new_sha != render_res.video_sha256:
                raise VerticalSliceError("Rendered output SHA mismatch")

            ordered_scene_paths = []
            new_scene_results = []

            for s_info in base_scenes:
                s_idx = s_info["sequence_index"]
                work_path = work_dir / f"scene_{s_idx:03d}.mp4"
                if s_idx == scene_index:
                    ordered_scene_paths.append(render_res.output_path)
                    new_scene_info = dict(s_info)
                    new_scene_info["original_strategy"] = original_strategy.value
                    new_scene_info["effective_strategy"] = effective_strategy.value
                    new_scene_info["template_id"] = render_res.template_id
                    new_scene_info["asset_kind"] = asset_kind_str
                    new_scene_info["asset_provider"] = asset_provider_str
                    new_scene_info["asset_id"] = asset_id_str
                    new_scene_info["asset_query"] = asset_query_str
                    new_scene_info["duration_seconds"] = render_res.duration_seconds
                    new_scene_info["content_sha256"] = new_sha

                    if "visual_strategy" in new_scene_info:
                        del new_scene_info["visual_strategy"]
                    if "asset_query_hint" in new_scene_info:
                        del new_scene_info["asset_query_hint"]

                    new_scene_results.append(new_scene_info)
                else:
                    base_s_mp4 = base_dir / "scenes" / f"scene_{s_idx:03d}.mp4"
                    shutil.copy2(base_s_mp4, work_path)
                    if self._compute_streaming_sha(work_path) != s_info["content_sha256"]:
                        raise VerticalSliceError("Copied scene SHA mismatch")
                    ordered_scene_paths.append(work_path)
                    new_scene_results.append(s_info)

            final_temp_mp4 = work_dir / "final_temp.mp4"
            await self._ffmpeg_renderer.concatenate_clips(
                clip_paths=ordered_scene_paths,
                output_path=final_temp_mp4,
                srt_path=None,
                target_fps=fps,
            )

            if not final_temp_mp4.is_file() or final_temp_mp4.stat().st_size <= 0:
                raise VerticalSliceError("Final concatenated MP4 is missing or empty")
            with open(final_temp_mp4, "rb") as f:
                if b"ftyp" not in f.read(4096):
                    raise VerticalSliceError("Final concatenated MP4 missing ftyp header")

            final_sha = self._compute_streaming_sha(final_temp_mp4)

            rev_dir.mkdir(parents=True, exist_ok=True)
            final_mp4_path = rev_dir / "final.mp4"
            final_temp_mp4.replace(final_mp4_path)

            scenes_dir = rev_dir / "scenes"
            scenes_dir.mkdir(parents=True, exist_ok=True)
            for s_idx, spath in zip([s["sequence_index"] for s in new_scene_results], ordered_scene_paths, strict=True):
                spath.replace(scenes_dir / f"scene_{s_idx:03d}.mp4")

            image_scenes = sum(1 for s in new_scene_results if s.get("asset_kind") == "IMAGE")
            broll_scenes = sum(1 for s in new_scene_results if s.get("asset_kind") == "BROLL")
            template_scenes = len(new_scene_results) - image_scenes - broll_scenes

            rev_manifest = dict(manifest)
            rev_manifest["run_fingerprint"] = rev_fingerprint
            rev_manifest["base_run_fingerprint"] = base_run_fingerprint
            rev_manifest["regenerated_scene_indices"] = [scene_index]
            rev_manifest["scenes"] = new_scene_results
            rev_manifest["content_sha256"] = final_sha
            rev_manifest["image_scene_count"] = image_scenes
            rev_manifest["broll_scene_count"] = broll_scenes
            rev_manifest["template_scene_count"] = template_scenes

            with open(rev_dir / "manifest.json", "w", encoding="utf-8") as f:
                json.dump(rev_manifest, f, indent=2)

            return VerticalSliceRenderResult(
                mission_id=UUID(manifest["mission_id"]),
                mission_execution_id=mission_execution_id,
                content_request_id=content_request_id,
                script_version_id=script_version_id,
                scene_count=len(ordered_scene_paths),
                template_scene_count=template_scenes,
                image_scene_count=image_scenes,
                broll_scene_count=broll_scenes,
                duration_seconds=manifest["duration_seconds"],
                width=manifest["width"],
                height=manifest["height"],
                fps=fps,
                output_path=final_mp4_path,
                content_sha256=final_sha,
                run_fingerprint=rev_fingerprint,
                narration_quality=rev_manifest.get("narration_quality"),
                narration_source_refs=tuple(rev_manifest.get("narration_source_refs", [])),
                runtime_timeline_duration_ms=rev_manifest.get("runtime_timeline_duration_ms"),
                runtime_narration_segments=tuple(
                    VerticalSliceRuntimeNarrationSegment(**s) for s in rev_manifest.get("runtime_narration_segments", [])
                ),
                runtime_subtitle_cues=tuple(
                    VerticalSliceRuntimeSubtitleCue(**c) for c in rev_manifest.get("runtime_subtitle_cues", [])
                ),
                runtime_scenes=tuple(
                    VerticalSliceSceneResult(**s) for s in rev_manifest.get("scenes", [])
                ),
            )

        finally:
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    def _resolve_visual_brand_assets(
        self,
        channel_id: UUID,
        logo_asset: Any,
        intro_asset: Any,
        outro_asset: Any,
    ) -> tuple[ResolvedBrandAsset | None, ResolvedBrandAsset | None, ResolvedBrandAsset | None]:
        if logo_asset is None and intro_asset is None and outro_asset is None:
            return None, None, None
        if self._brand_asset_resolver is None:
            raise BrandAssetResolutionError("Brand asset resolver is required for configured visual brand assets.")
        resolved = (
            self._brand_asset_resolver.resolve_optional(channel_id, logo_asset, BrandMediaKind.IMAGE),
            self._brand_asset_resolver.resolve_optional(channel_id, intro_asset, BrandMediaKind.VIDEO),
            self._brand_asset_resolver.resolve_optional(channel_id, outro_asset, BrandMediaKind.VIDEO),
        )
        expected = (BrandMediaKind.IMAGE, BrandMediaKind.VIDEO, BrandMediaKind.VIDEO)
        if any(asset is not None and asset.media_kind != kind for asset, kind in zip(resolved, expected, strict=True)):
            raise BrandAssetResolutionError("Resolved brand asset media kind mismatch.")
        return resolved

    @staticmethod
    def _brand_clip_paths(
        content_paths: list[Path],
        intro: ResolvedBrandAsset | None,
        outro: ResolvedBrandAsset | None,
    ) -> list[Path]:
        return [
            *([intro.local_path] if intro is not None else []),
            *content_paths,
            *([outro.local_path] if outro is not None else []),
        ]

    @staticmethod
    def _resolved_brand_asset_identity(
        logo: ResolvedBrandAsset | None,
        intro: ResolvedBrandAsset | None,
        outro: ResolvedBrandAsset | None,
    ) -> str | None:
        values = {
            role: {
                "reference": asset.reference,
                "media_kind": asset.media_kind.value,
                "content_sha256": asset.content_hash,
            }
            for role, asset in (("logo", logo), ("intro", intro), ("outro", outro))
            if asset is not None
        }
        if not values:
            return None
        canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _apply_visual_asset_mode_policy(
        self, scene: StoryboardScene
    ) -> StoryboardScene:
        if (
            self._visual_asset_mode == "LOCAL_TEMPLATE_ONLY"
            and scene.visual_strategy
            in (VisualStrategy.IMAGE, VisualStrategy.BROLL, VisualStrategy.SCREENSHOT)
        ):
            return scene.model_copy(
                update={"visual_strategy": VisualStrategy.TITLE_MOTION}
            )
        return scene

    def _apply_v0_compatibility(self, scene: StoryboardScene) -> StoryboardScene:
        strat = scene.visual_strategy

        # SCREENSHOT materialization is not yet implemented (blocked by VerticalSliceError in materializer).
        # Safely fall back to IMAGE if there is a meaningful query, else TITLE_MOTION.
        if strat == VisualStrategy.SCREENSHOT:
            raw_hint = scene.asset_query_hint or ""
            clean_hint = raw_hint.strip().lower()
            is_meaningful = clean_hint and clean_hint not in ("placeholder", "generic") and "abstract technology background" not in clean_hint

            fallback_strat = VisualStrategy.IMAGE if is_meaningful else VisualStrategy.TITLE_MOTION
            return scene.model_copy(update={"visual_strategy": fallback_strat})

        return scene

    def _ensure_meaningful_query(self, scene: StoryboardScene) -> None:
        raw_hint = scene.asset_query_hint or ""
        clean = raw_hint.strip().lower()

        is_meaningless = (
            not clean
            or clean in ("placeholder", "generic")
            or "abstract technology background" in clean
        )

        if not is_meaningless:
            tokens = _clean_query_words(raw_hint, max_words=8)
            derived = " ".join(tokens)
            if not derived:
                is_meaningless = True
            else:
                scene.asset_query_hint = derived

        if is_meaningless:
            heading_tokens = _clean_query_words(scene.section_id, max_words=3)
            narration_tokens = _clean_query_words(scene.narration_excerpt, max_words=6)

            combined = []
            seen = set()
            for w in heading_tokens + narration_tokens:
                if w not in seen:
                    combined.append(w)
                    seen.add(w)

            if len(combined) > 8:
                combined = combined[:8]

            derived = " ".join(combined).strip()

            if scene.visual_strategy == VisualStrategy.IMAGE and not derived:
                derived = "concept illustration"
            elif scene.visual_strategy == VisualStrategy.BROLL and not derived:
                derived = "stock footage"

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

    def resolve_scene_preview(self, mission_execution_id: UUID, run_fingerprint: str, scene_index: int) -> Path:
        import re
        if not re.match(r"^[0-9a-f]{64}$", run_fingerprint):
            raise VerticalSliceError("Invalid run_fingerprint format")

        if scene_index <= 0:
            raise VerticalSliceError("Invalid scene index")

        run_dir = self._output_root / str(mission_execution_id) / run_fingerprint
        manifest_path = run_dir / "manifest.json"

        if not manifest_path.exists():
            raise VerticalSliceError("Manifest not found")

        final_mp4 = run_dir / "final.mp4"
        if not final_mp4.is_file() or final_mp4.stat().st_size <= 0:
            raise VerticalSliceError("Preview requires valid final.mp4")

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        if manifest.get("run_fingerprint") != run_fingerprint:
            raise VerticalSliceError("Manifest run_fingerprint mismatch")

        if manifest.get("scene_artifacts_version") != "v1":
            raise VerticalSliceError("Scene preview unavailable for legacy run")

        scene_info = next((s for s in manifest.get("scenes", []) if s.get("sequence_index") == scene_index), None)
        if not scene_info:
            raise VerticalSliceError(f"Scene {scene_index} not found in manifest")

        scene_mp4 = run_dir / "scenes" / f"scene_{scene_index:03d}.mp4"
        if not scene_mp4.is_file() or scene_mp4.stat().st_size <= 0:
            raise VerticalSliceError("Persisted scene missing or empty")

        with open(scene_mp4, "rb") as f:
            if b"ftyp" not in f.read(4096):
                raise VerticalSliceError("Persisted scene missing ftyp header")

        if self._compute_streaming_sha(scene_mp4) != scene_info.get("content_sha256"):
            raise VerticalSliceError("Persisted scene SHA256 mismatch")

        return scene_mp4
