import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.audio_mix_policy import (
    LicenseStatus,
    build_audio_mix_plan,
    build_background_music_plan,
    build_sfx_event_plan,
)
from omega.application.beat_asset_executor import BeatAssetExecutor
from omega.application.beat_clip_assembler import BeatClipAssembler
from omega.application.beat_visual_renderer import (
    BeatVisualRenderer,
    quantize_parent_frame_counts,
)
from omega.application.brand_asset_resolver import (
    BrandAssetResolutionError,
    BrandAssetResolver,
    BrandMediaKind,
    ResolvedBrandAsset,
)
from omega.application.canonical_beat_preparation import CanonicalBeatPreparationService
from omega.application.ffmpeg_renderer import (
    FINAL_MASTER_SAMPLE_RATE_HZ,
    FFmpegRenderer,
)
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import NarrationProvider
from omega.application.production_contract import (
    CanonicalProductionContract,
    resolve_canonical_production_contract,
)
from omega.application.production_runtime_truth import RUNTIME_TRUTH_SCHEMA_VERSION
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
from omega.application.visual_asset_engine import VisualAssetEngine
from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator
from omega.application.visual_direction import VisualAssetKind, VisualDirector
from omega.application.visual_template_renderer import VisualTemplateRenderer
from omega.domain.attribution_delivery import AttributionDeliveryChannel
from omega.domain.channel_dna import BrandFormat, resolve_production_brand_spec
from omega.domain.channel_style import ChannelStyleProfile, extract_channel_style_profile
from omega.domain.production import (
    SubtitleFallbackPolicy,
    SubtitleMode,
    SubtitleModeDecision,
    evaluate_subtitle_mode_decision,
)
from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime
from omega.infrastructure.models import (
    Channel,
    MissionExecution,
    ProductionRequest,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
)
from omega.infrastructure.visual_asset_materializer import VisualAssetMaterializer
from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderer

_NON_ALPHANUM_REGEX = re.compile(r"[^\w\s-]")
_WHITESPACE_REGEX = re.compile(r"\s+")
_SENSITIVE_METADATA_MARKERS = (
    "token",
    "secret",
    "signature",
    "credential",
    "apikey",
    "api_key",
    "authorization",
    "oauth",
    "password",
    "resumable",
)
_AUTHORIZATION_VALUE = re.compile(r"^\s*(?:bearer|basic)\s+\S+", re.IGNORECASE)
_DROP_METADATA_VALUE = object()


def _safe_external_reference(value: str | None) -> str | None:
    """Strip query/fragment/credentials from provider references."""
    if not value:
        return None
    parsed = urlsplit(str(value).strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    hostname = parsed.hostname
    safe_host = f"[{hostname}]" if ":" in hostname else hostname
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None:
        safe_host = f"{safe_host}:{port}"
    return urlunsplit((parsed.scheme.lower(), safe_host, parsed.path, "", ""))


def _safe_provider_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() in ("http", "https"):
            return _safe_external_reference(value)
        if parsed.username or parsed.password or _AUTHORIZATION_VALUE.match(value):
            return None
        return value
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(
                marker in key_text.lower()
                for marker in _SENSITIVE_METADATA_MARKERS
            ):
                continue
            safe_item = _safe_provider_metadata_value(item)
            if safe_item is not _DROP_METADATA_VALUE:
                safe[key_text] = safe_item
        return safe
    if isinstance(value, (list, tuple)):
        safe_items = []
        for item in value:
            safe_item = _safe_provider_metadata_value(item)
            if safe_item is not _DROP_METADATA_VALUE:
                safe_items.append(safe_item)
        return safe_items
    return _DROP_METADATA_VALUE


def _safe_provider_metadata(value: Any) -> dict[str, Any]:
    """Recursively copy JSON-native provider metadata without secrets."""
    if not isinstance(value, dict):
        return {}
    safe = _safe_provider_metadata_value(value)
    return safe if isinstance(safe, dict) else {}


class VerticalSliceError(Exception):
    pass


KARAOKE_SUBTITLE_VERSION = "v1"
SUBTITLE_SEMANTICS_VERSION = 3
CANONICAL_RENDER_SEMANTICS_VERSION = 5
FINAL_MASTER_TARGET_I = -16.0
FINAL_MASTER_TARGET_TP = -1.5
FINAL_MASTER_TARGET_LRA = 7.0
KARAOKE_MAX_WORDS_PER_CUE = 5
KARAOKE_MAX_CHARS_PER_CUE = 36
SENTENCE_MAX_WORDS_PER_CUE = 12
SENTENCE_MAX_CHARS_PER_CUE = 50

VISUAL_DIRECTOR_VERSION = "v2"


def _canonical_render_semantics_identity(
    *,
    render_semantics_version: int | None = None,
    target_i: float | None = None,
    target_tp: float | None = None,
    target_lra: float | None = None,
    sample_rate_hz: int | None = None,
) -> str:
    """Return deterministic physical-output semantics included in cache identity."""
    render_semantics_version = (
        CANONICAL_RENDER_SEMANTICS_VERSION
        if render_semantics_version is None
        else render_semantics_version
    )
    target_i = FINAL_MASTER_TARGET_I if target_i is None else target_i
    target_tp = FINAL_MASTER_TARGET_TP if target_tp is None else target_tp
    target_lra = FINAL_MASTER_TARGET_LRA if target_lra is None else target_lra
    sample_rate_hz = (
        FINAL_MASTER_SAMPLE_RATE_HZ
        if sample_rate_hz is None
        else sample_rate_hz
    )
    return (
        f"canonical-render-semantics-v{render_semantics_version}:"
        f"mastering-policy-v1:i={target_i:.1f}:tp={target_tp:.1f}:"
        f"lra={target_lra:.1f}:sample_rate={sample_rate_hz}"
    )


def _has_current_subtitle_semantics_version(manifest: dict[str, Any]) -> bool:
    """Return whether a cached render uses the current physical subtitle semantics."""
    value = manifest.get("subtitle_semantics_version")
    return type(value) is int and value == SUBTITLE_SEMANTICS_VERSION


def _validate_cached_subtitle_semantics(
    *,
    manifest: dict[str, Any],
    canonical_requested_mode: SubtitleMode,
    fallback_policy: SubtitleFallbackPolicy,
) -> bool:
    """Validate cached subtitle provenance against current render semantics."""
    runtime_truth_version = manifest.get("runtime_truth_schema_version")
    if (
        type(runtime_truth_version) is not int
        or runtime_truth_version != RUNTIME_TRUTH_SCHEMA_VERSION
    ):
        return False
    if not _has_current_subtitle_semantics_version(manifest):
        return False

    try:
        requested_mode = SubtitleMode(manifest["requested_subtitle_mode"])
        cached_decision = SubtitleModeDecision.model_validate(
            manifest["subtitle_mode_decision"]
        )
    except (KeyError, TypeError, ValueError):
        return False

    if requested_mode != canonical_requested_mode:
        return False

    capability_error: str | None = None
    if requested_mode == SubtitleMode.KARAOKE:
        scenes = manifest.get("scenes")
        narration_segments = manifest.get("runtime_narration_segments")
        subtitle_cues = manifest.get("runtime_subtitle_cues")
        if not all(isinstance(items, list) for items in (scenes, narration_segments, subtitle_cues)):
            return False

        try:
            scene_indices = [scene["sequence_index"] for scene in scenes]
            if (
                not scene_indices
                or any(type(index) is not int or index <= 0 for index in scene_indices)
                or len(set(scene_indices)) != len(scene_indices)
            ):
                return False

            segment_by_scene: dict[int, dict[str, Any]] = {}
            for segment in narration_segments:
                scene_index = segment["scene_index"]
                if scene_index in segment_by_scene:
                    return False
                segment_by_scene[scene_index] = segment

            cues_by_scene: dict[int, list[dict[str, Any]]] = {}
            for cue in subtitle_cues:
                cues_by_scene.setdefault(cue["scene_index"], []).append(cue)

            scene_by_index = {scene["sequence_index"]: scene for scene in scenes}
            for scene_index in sorted(scene_indices):
                segment = segment_by_scene.get(scene_index)
                scene_cues = cues_by_scene.get(scene_index)
                if segment is None or not scene_cues:
                    return False
                if scene_by_index[scene_index].get("subtitle_cue_count") != len(scene_cues):
                    return False
                ordered_cues = sorted(scene_cues, key=lambda cue: cue["cue_order"])
                text = " ".join(str(cue["text"]).strip() for cue in ordered_cues)
                timing_error = _derived_karaoke_timing_error(
                    text, segment.get("duration_ms")
                )
                if timing_error is not None:
                    capability_error = f"scene_{scene_index}:{timing_error}"
                    break
        except (KeyError, TypeError, ValueError):
            return False

    expected_decision = evaluate_subtitle_mode_decision(
        requested_mode=requested_mode,
        fallback_policy=fallback_policy,
        has_word_timing=False,
        timing_available=capability_error is None,
        timing_error_reason=capability_error,
        segment_timing_available=True,
    )
    expected_enabled = expected_decision.effective_mode != SubtitleMode.OFF
    expected_karaoke = expected_decision.effective_mode == SubtitleMode.KARAOKE
    expected_subtitle_mode = "karaoke" if expected_karaoke else "sentence"

    return (
        cached_decision == expected_decision
        and manifest.get("effective_subtitle_mode")
        == expected_decision.effective_mode.value
        and manifest.get("subtitle_fallback_applied")
        is expected_decision.fallback_applied
        and manifest.get("subtitle_fallback_reason")
        == expected_decision.fallback_reason
        and manifest.get("subtitle_timing_source")
        == expected_decision.timing_source.value
        and manifest.get("subtitle_enabled") is expected_enabled
        and manifest.get("karaoke_subtitles_enabled") is expected_karaoke
        and manifest.get("subtitle_mode") == expected_subtitle_mode
        and (
            requested_mode != SubtitleMode.OFF
            or (
                manifest.get("runtime_subtitle_cues") == []
                and manifest.get("runtime_subtitle_artifacts") == []
                and manifest.get("subtitle_burn_applied") is False
            )
        )
    )


def _validate_cached_render_semantics(
    *,
    manifest: dict[str, Any],
    canonical_requested_mode: SubtitleMode,
    fallback_policy: SubtitleFallbackPolicy,
) -> bool:
    """Validate all versioned physical-output semantics for cache reuse."""
    render_version = manifest.get("canonical_render_semantics_version")
    if (
        type(render_version) is not int
        or render_version != CANONICAL_RENDER_SEMANTICS_VERSION
    ):
        return False
    sample_rate_hz = manifest.get("final_master_sample_rate_hz")
    if (
        type(sample_rate_hz) is not int
        or sample_rate_hz != FINAL_MASTER_SAMPLE_RATE_HZ
    ):
        return False
    return _validate_cached_subtitle_semantics(
        manifest=manifest,
        canonical_requested_mode=canonical_requested_mode,
        fallback_policy=fallback_policy,
    )


def _derived_karaoke_timing_error(text: str, duration_ms: Any) -> str | None:
    """Return a deterministic capability error for derived karaoke timing."""
    normalized_text = " ".join(str(text).split())
    if not normalized_text:
        return "blank_subtitle_text"
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        return "missing_or_invalid_segment_duration"
    if duration_ms <= 0:
        return "non_positive_segment_duration"

    try:
        cues = generate_karaoke_cues(
            [{"text": normalized_text, "start_ms": 0, "duration_ms": duration_ms}],
            max_words_per_cue=KARAOKE_MAX_WORDS_PER_CUE,
            max_chars_per_cue=KARAOKE_MAX_CHARS_PER_CUE,
            sentence_mode=False,
        )
    except (TypeError, ValueError):
        return "derived_karaoke_generation_failed"

    if not cues:
        return "no_derived_karaoke_cues"

    previous_end_ms = 0
    allocated_duration_ms = 0
    for cue in cues:
        start_ms = cue.get("start_ms")
        end_ms = cue.get("end_ms")
        if (
            not isinstance(start_ms, int)
            or not isinstance(end_ms, int)
            or start_ms != previous_end_ms
            or end_ms <= start_ms
            or end_ms > duration_ms
        ):
            return "non_monotonic_derived_karaoke_cues"
        words = cue.get("words") or []
        if not words:
            return "derived_karaoke_cue_without_words"
        cue_word_duration_ms = 0
        for word in words:
            word_duration_ms = word.get("duration_ms")
            if (
                isinstance(word_duration_ms, bool)
                or not isinstance(word_duration_ms, int)
                or word_duration_ms < 10
            ):
                return "zero_centisecond_karaoke_unit"
            cue_word_duration_ms += word_duration_ms
        if cue_word_duration_ms != end_ms - start_ms:
            return "derived_karaoke_cue_duration_mismatch"
        allocated_duration_ms += cue_word_duration_ms
        previous_end_ms = end_ms

    if previous_end_ms != duration_ms or allocated_duration_ms != duration_ms:
        return "derived_karaoke_duration_not_conserved"
    return None


class VerticalSliceRuntimeNarrationSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_index: int
    start_ms: int
    end_ms: int
    duration_ms: int
    text: str = ""
    audio_asset_id: str | None = None
    storage_reference: str | None = None
    audio_content_sha256: str | None = None
    provider: str | None = None
    model: str | None = None
    voice: str | None = None
    voice_profile: dict[str, Any] = Field(default_factory=dict)
    quality: str | None = None
    license_status: str | None = None
    source_reference: str | None = None
    attribution: str | None = None


class VerticalSliceRuntimeSubtitleCue(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_index: int
    cue_order: int
    start_ms: int
    end_ms: int
    text: str


class VerticalSliceRuntimeSubtitleArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_index: int
    artifact_kind: str = "ASS"
    content_sha256: str


class VerticalSliceSceneResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence_index: int
    source_section_id: str | None = None
    source_statement_references: tuple[int, ...] = ()
    narration_text: str | None = None
    original_strategy: str
    effective_strategy: str
    template_id: str | None
    execution_mode: str = "LEGACY_SINGLE_SCENE"
    asset_kind: str | None
    asset_provider: str | None
    asset_id: str | None
    asset_query: str | None = None
    duration_seconds: float
    start_ms: int | None = None
    end_ms: int | None = None
    duration_ms: int | None = None
    content_sha256: str
    audio_content_sha256: str | None = None
    audio_duration_seconds: float | None = None
    subtitle_cue_count: int | None = None
    subtitle_text_truncated: bool | None = None
    text_fitting: tuple[dict[str, Any], ...] = ()
    text_truncated: bool = False
    visual_origin: str | None = "TEMPLATE"
    visual_mode: str = "LOCAL_TEMPLATE_ONLY"
    asset_source_url: str | None = None
    asset_source_page_url: str | None = None
    asset_license_status: LicenseStatus | None
    asset_license_name: str | None = None
    asset_license_url: str | None = None
    asset_attribution: str | None = None
    asset_allowed_attribution_channels: tuple[AttributionDeliveryChannel, ...] = ()
    asset_storage_reference: str | None = None
    visual_content_sha256: str | None = None
    visual_mime_type: str | None = None
    visual_width: int | None = None
    visual_height: int | None = None
    visual_duration_ms: int | None = None
    asset_provider_metadata: dict[str, Any] = Field(default_factory=dict)


class VerticalSliceRuntimeBeatVisual(BaseModel):
    model_config = ConfigDict(frozen=True)

    parent_scene_index: int
    materialized_beat_index: int
    source_editorial_beat_index: int
    source_statement_references: tuple[int, ...] = ()
    semantic_role: str
    start_offset_ms: int
    end_offset_ms: int
    duration_ms: int
    template_id: str
    camera_motion_intent: str
    transition_intent: str
    asset_action: str
    reuse_from_beat_index: int | None = None
    visual_origin: str
    asset_kind: str | None = None
    query: str | None = None
    provider: str | None = None
    provider_asset_id: str | None = None
    source_url: str | None = None
    source_page_url: str | None = None
    license_status: LicenseStatus
    license_name: str | None = None
    license_url: str | None = None
    attribution: str | None = None
    allowed_attribution_channels: tuple[AttributionDeliveryChannel, ...] = ()
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    provider_asset_content_sha256: str | None = None
    rendered_beat_clip_sha256: str


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

    mission_id: UUID | None
    mission_execution_id: UUID | None
    content_request_id: UUID
    script_version_id: UUID
    production_request_id: UUID | None = None
    channel_id: UUID | None = None
    channel_dna_revision_id: UUID | None = None

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
    subtitle_semantics_version: int
    narration_quality: str | None = None
    narration_source_refs: tuple[str, ...] = ()
    runtime_timeline_duration_ms: int | None = None
    runtime_narration_segments: tuple[VerticalSliceRuntimeNarrationSegment, ...] = ()
    runtime_subtitle_cues: tuple[VerticalSliceRuntimeSubtitleCue, ...] = ()
    runtime_subtitle_artifacts: tuple[VerticalSliceRuntimeSubtitleArtifact, ...] = ()
    runtime_scenes: tuple[VerticalSliceSceneResult, ...] = ()
    runtime_visual_beats: tuple[VerticalSliceRuntimeBeatVisual, ...] = ()
    runtime_branding: dict[str, Any] = Field(default_factory=dict)
    runtime_audio_mix: dict[str, Any] = Field(default_factory=dict)
    subtitle_style_applied: SubtitleRenderStyle | None = None
    style_profile_applied: ChannelStyleProfile | None = None
    effective_fps_mode: str = "CFR"
    subtitle_enabled: bool = False
    karaoke_subtitles_enabled: bool = False
    subtitle_mode: str = "sentence"
    requested_subtitle_mode: str = "STANDARD"
    effective_subtitle_mode: str = "STANDARD"
    subtitle_fallback_applied: bool = False
    subtitle_fallback_reason: str | None = None
    subtitle_timing_source: str = "DERIVED_SEGMENT_TIMING"
    subtitle_mode_decision: SubtitleModeDecision | None = None
    subtitle_burn_applied: bool = False


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

        hook_text = str(getattr(script_version, "hook_text", None) or "").strip()
        closing_text = str(
            getattr(script_version, "closing_text", None) or ""
        ).strip()
        cta_text = str(getattr(script_version, "cta_text", None) or "").strip()
        if hook_text:
            hook_heading = str(getattr(script_version, "title", None) or "").strip() or "Introduction"
            sections_data.insert(
                0,
                {
                    "section_order": -1,
                    "heading": hook_heading,
                    "narration_text": hook_text,
                    "estimated_duration_seconds": None,
                    "statements": [
                        {
                            "statement_order": 0,
                            "statement_text": hook_text,
                            "statement_type": "HOOK",
                            "citations": [],
                        }
                    ],
                },
            )
        closing_statements = []
        if closing_text:
            closing_statements.append(
                {
                    "statement_order": 1,
                    "statement_text": closing_text,
                    "statement_type": "CLOSING",
                    "citations": [],
                }
            )
        if cta_text:
            closing_statements.append(
                {
                    "statement_order": 2,
                    "statement_text": cta_text,
                    "statement_type": "CTA",
                    "citations": [],
                }
            )
        if closing_statements:
            sections_data.append(
                {
                    "section_order": len(sections_data) + 1,
                    "heading": "Closing",
                    "narration_text": " ".join(
                        statement["statement_text"]
                        for statement in closing_statements
                    ),
                    "estimated_duration_seconds": None,
                    "statements": closing_statements,
                }
            )

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
        beat_preparation_service: Any | None = None,
        beat_asset_executor: BeatAssetExecutor | None = None,
        beat_visual_renderer: BeatVisualRenderer | None = None,
        beat_clip_assembler: BeatClipAssembler | None = None,
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
        self._beat_preparation_service = (
            beat_preparation_service or CanonicalBeatPreparationService
        )
        self._beat_asset_executor = beat_asset_executor or BeatAssetExecutor(
            resolver=self._orchestrator
        )
        self._beat_visual_renderer = beat_visual_renderer or BeatVisualRenderer(
            payload_resolver=self._template_resolver,
            template_renderer=self._template_renderer,
            video_renderer=self._video_renderer,
        )
        self._beat_clip_assembler = beat_clip_assembler or BeatClipAssembler(
            self._ffmpeg_renderer
        )
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
        subtitle_mode: SubtitleMode | str | None = None,
        subtitle_fallback_policy: SubtitleFallbackPolicy | str | None = None,
        contract: CanonicalProductionContract | None = None,
        production_request_id: UUID | None = None,
    ) -> VerticalSliceRenderResult:
        """Validate Mission lineage, then delegate to the public canonical path."""
        exec_stmt = (
            select(MissionExecution)
            .where(MissionExecution.id == mission_execution_id)
            .options(selectinload(MissionExecution.mission))
        )
        exec_res = await session.execute(exec_stmt)
        mission_exec = exec_res.scalar_one_or_none()
        if mission_exec is None:
            raise VerticalSliceError(
                f"MissionExecution '{mission_execution_id}' not found"
            )
        mission = mission_exec.mission
        if mission is None:
            raise VerticalSliceError(
                f"Mission for execution '{mission_execution_id}' not found"
            )
        if not mission.channel_id:
            raise VerticalSliceError(f"Mission '{mission.id}' has no channel_id")

        request_stmt = select(ProductionRequest).where(
            ProductionRequest.mission_execution_id == mission_execution_id,
            ProductionRequest.content_request_id == content_request_id,
        )
        if production_request_id is not None:
            request_stmt = request_stmt.where(
                ProductionRequest.id == production_request_id
            )
        request_stmt = request_stmt.options(
            selectinload(ProductionRequest.content_request),
            selectinload(ProductionRequest.channel),
        )
        request_res = await session.execute(request_stmt)
        production_request = request_res.scalar_one_or_none()
        if production_request is None:
            raise VerticalSliceError(
                "Associated ProductionRequest not found for MissionExecution and "
                "ContentGenerationRequest"
            )
        if production_request.mission_execution_id != mission_execution_id:
            raise VerticalSliceError(
                "ProductionRequest.mission_execution_id does not match supplied "
                "mission_execution_id"
            )
        if production_request.content_request_id != content_request_id:
            raise VerticalSliceError(
                "ProductionRequest.content_request_id does not match supplied "
                "content_request_id"
            )
        if production_request.channel_id != mission.channel_id:
            raise VerticalSliceError(
                "ProductionRequest.channel_id does not match Mission.channel_id"
            )
        content_request = production_request.content_request
        if content_request is None:
            raise VerticalSliceError("ProductionRequest pinned content request is missing")
        if content_request.mission_execution_id != mission_execution_id:
            raise VerticalSliceError(
                "ContentGenerationRequest.mission_execution_id does not match supplied "
                "mission_execution_id"
            )
        if content_request.channel_id != production_request.channel_id:
            raise VerticalSliceError(
                "ContentGenerationRequest.channel_id does not match ProductionRequest.channel_id"
            )

        canonical_contract = contract
        if canonical_contract is None:
            overrides: dict[str, Any] = {
                "fps": fps,
                "voice_profile": dict(voice_profile or {}),
                "visual_asset_mode": self._visual_asset_mode,
            }
            if subtitle_mode is not None:
                overrides["subtitle_mode"] = subtitle_mode
            else:
                overrides["subtitle_enabled"] = subtitle_enabled
            if subtitle_fallback_policy is not None:
                overrides["subtitle_fallback_policy"] = subtitle_fallback_policy
            if subtitle_style is not None:
                overrides["subtitle_style"] = subtitle_style
            canonical_contract = resolve_canonical_production_contract(
                production_request,
                channel=production_request.channel,
                mission_id=mission.id,
                overrides=overrides,
            )

        return await self.render_canonical_production(
            session=session,
            production_request_id=production_request.id,
            contract=canonical_contract,
            mission_id=mission.id,
            mission_execution_id=mission_execution_id,
            background_music=background_music,
            sfx_inputs=sfx_inputs,
            audio_mix_enabled=audio_mix_enabled,
            style_profile=style_profile,
        )

    async def render_canonical_production(
        self,
        session: AsyncSession,
        production_request_id: UUID,
        contract: CanonicalProductionContract,
        *,
        background_music: VerticalSliceBackgroundMusicInput | None = None,
        sfx_inputs: list[VerticalSliceSFXInput] | None = None,
        audio_mix_enabled: bool = False,
        style_profile: ChannelStyleProfile | None = None,
        mission_id: UUID | None = None,
        mission_execution_id: UUID | None = None,
        task_id: UUID | None = None,
        render_job_id: UUID | None = None,
    ) -> VerticalSliceRenderResult:
        """Render one exact ProductionRequest without requiring Mission lineage."""
        if contract.lineage.production_request_id != production_request_id:
            raise VerticalSliceError(
                "Canonical contract production_request_id does not match request"
            )

        return await self._render_canonical_production_core(
            session=session,
            production_request_id=production_request_id,
            contract=contract,
            mission_id=mission_id,
            mission_execution_id=mission_execution_id,
            task_id=task_id,
            render_job_id=render_job_id,
            background_music=background_music,
            sfx_inputs=sfx_inputs,
            audio_mix_enabled=audio_mix_enabled,
            style_profile=style_profile,
        )

    @staticmethod
    def _validate_canonical_target(contract: CanonicalProductionContract) -> None:
        policy = contract.policy
        if policy.target_width != 1920 or policy.target_height != 1080:
            raise VerticalSliceError(
                f"V2 unsupported resolution: {policy.target_width}x{policy.target_height}"
            )
        if policy.target_fps <= 0 or policy.target_fps > 60:
            raise VerticalSliceError(
                f"Invalid fps: {policy.target_fps}. Must be > 0 and <= 60."
            )
        if policy.fps_mode.upper() != "CFR":
            raise VerticalSliceError(f"V2 unsupported fps mode: {policy.fps_mode}")
        if policy.container_format.lower() != "mp4":
            raise VerticalSliceError(
                f"V2 unsupported container: {policy.container_format}"
            )
        if policy.video_codec.lower() not in ("h264", "libx264"):
            raise VerticalSliceError(f"V2 unsupported codec: {policy.video_codec}")
        if policy.audio_codec.lower() != "aac":
            raise VerticalSliceError(
                f"V2 unsupported audio codec: {policy.audio_codec}"
            )

    async def _render_parent_visual(
        self,
        *,
        script_dict: dict[str, Any],
        scene: StoryboardScene,
        duration_seconds: float,
        canonical_visual_mode: str,
        work_dir: Path,
        browser: Any,
        fps: int,
        style_profile: ChannelStyleProfile | None,
        narration_enabled: bool,
        timeline_start_ms: int = 0,
        frame_count_override: int | None = None,
    ) -> dict[str, Any]:
        """Render one parent visual, committing irreversibly at beat acquisition."""
        duration_ms = int(round(duration_seconds * 1000))
        scene_out_path = work_dir / f"scene_{scene.sequence_index:03d}.mp4"
        scene_visual_out_path = (
            work_dir / f"scene_{scene.sequence_index:03d}_visual.mp4"
            if narration_enabled
            else scene_out_path
        )
        try:
            prep = self._beat_preparation_service.prepare_from_script_dict(
                script_dict=script_dict,
                scene=scene,
                scene_duration_ms=duration_ms,
                visual_asset_mode=canonical_visual_mode,
            )
        except Exception:
            prep = None

        if (
            prep is not None
            and prep.eligible
            and prep.render_plan is not None
            and len(prep.render_plan.units) >= 2
        ):
            plan = prep.render_plan
            try:
                execution = await self._beat_asset_executor.execute_plan(
                    render_plan=plan,
                    provider_acquisition_allowed=(canonical_visual_mode == "PEXELS"),
                )
                rendered = await self._beat_visual_renderer.render_plan(
                    render_plan=plan,
                    asset_execution=execution,
                    output_dir=work_dir / "beats" / f"scene_{scene.sequence_index:03d}",
                    browser_runtime=browser,
                    fps=fps,
                    accent_color=style_profile.accent_color if style_profile else None,
                    bg_color=style_profile.bg_color if style_profile else None,
                    timeline_start_ms=timeline_start_ms,
                )
                assembly = await self._beat_clip_assembler.assemble(
                    rendered.clips,
                    output_path=scene_visual_out_path,
                    fps=fps,
                )
            except Exception as exc:
                raise VerticalSliceError(
                    f"Committed multi-beat render failed for scene "
                    f"{scene.sequence_index}: {self._sanitize_error(exc)}"
                ) from exc
            if assembly.parent_scene_index != scene.sequence_index:
                raise VerticalSliceError("Multi-beat assembly scene mismatch")
            if assembly.expected_duration_ms != duration_ms:
                raise VerticalSliceError("Multi-beat assembly duration mismatch")

            beat_truth: list[VerticalSliceRuntimeBeatVisual] = []
            for unit, asset, metadata in zip(
                plan.units, execution.assets, rendered.beat_metadata, strict=True
            ):
                provider_asset = asset.resolved_asset
                beat_truth.append(
                    VerticalSliceRuntimeBeatVisual(
                        parent_scene_index=scene.sequence_index,
                        materialized_beat_index=unit.materialized_index,
                        source_editorial_beat_index=unit.source_beat_index,
                        source_statement_references=tuple(
                            unit.scene_view.source_statement_references
                        ),
                        semantic_role=str(unit.direction_view.metadata["semantic_role"]),
                        start_offset_ms=unit.start_ms,
                        end_offset_ms=unit.end_ms,
                        duration_ms=unit.duration_ms,
                        template_id=metadata.template_id.value,
                        camera_motion_intent=unit.camera_motion_intent.value,
                        transition_intent=unit.transition_intent.value,
                        asset_action=asset.action.value,
                        reuse_from_beat_index=asset.reuse_from_beat_index,
                        visual_origin="PROVIDER" if provider_asset else "TEMPLATE",
                        asset_kind=(
                            asset.required_kind.value if asset.required_kind else None
                        ),
                        query=unit.asset_decision.query_hint,
                        provider=provider_asset.provider if provider_asset else None,
                        provider_asset_id=(
                            provider_asset.asset_id if provider_asset else None
                        ),
                        source_url=(
                            _safe_external_reference(provider_asset.source_url)
                            if provider_asset else None
                        ),
                        source_page_url=(
                            _safe_external_reference(provider_asset.source_page_url)
                            if provider_asset else None
                        ),
                        license_status=(
                            provider_asset.license_status
                            if provider_asset else LicenseStatus.GENERATED
                        ),
                        license_name=(
                            provider_asset.license_name if provider_asset else None
                        ),
                        license_url=(
                            _safe_external_reference(provider_asset.license_url)
                            if provider_asset else None
                        ),
                        attribution=(
                            provider_asset.attribution_text if provider_asset else None
                        ),
                        allowed_attribution_channels=(
                            tuple(
                                sorted(
                                    set(provider_asset.allowed_attribution_channels),
                                    key=str,
                                )
                            )
                            if provider_asset else ()
                        ),
                        provider_metadata=(
                            _safe_provider_metadata(provider_asset.metadata)
                            if provider_asset else {}
                        ),
                        provider_asset_content_sha256=(
                            provider_asset.content_sha256 if provider_asset else None
                        ),
                        rendered_beat_clip_sha256=metadata.video_sha256,
                    )
                )
            return {
                "execution_mode": "MULTI_BEAT",
                "scene_out_path": scene_out_path,
                "scene_visual_out_path": scene_visual_out_path,
                "visual_sha256": assembly.content_sha256
                or self._compute_streaming_sha(scene_visual_out_path),
                "runtime_beats": tuple(beat_truth),
                "template_id": None,
                "resolved_asset": None,
                "asset_kind": None,
                "asset_provider": None,
                "asset_id": None,
                "asset_query": None,
                "visual_origin": None,
                "visual_content_sha256": None,
                "visual_mime_type": None,
                "visual_width": None,
                "visual_height": None,
                "visual_duration_ms": duration_ms,
                "text_fitting": (),
                "text_truncated": False,
            }

        if scene.visual_strategy in (VisualStrategy.IMAGE, VisualStrategy.BROLL):
            self._ensure_meaningful_query(scene)
        direction = self._visual_director.resolve(scene)
        if canonical_visual_mode == "LOCAL_TEMPLATE_ONLY" and direction.asset_requirements:
            raise VerticalSliceError(
                "LOCAL_TEMPLATE_ONLY scene produced an external asset requirement"
            )
        payload = self._template_resolver.resolve(scene, direction)
        assets: tuple[Any, ...] = ()
        broll_asset: BoundBrollAsset | None = None
        asset_kind: str | None = None
        asset_query: str | None = None
        resolved_asset = None
        if direction.asset_requirements:
            if self._orchestrator is None:
                raise VerticalSliceError("Asset resolution requires an asset orchestrator")
            requirement = direction.asset_requirements[0]
            asset_kind = requirement.kind.value
            request = self._visual_asset_engine.build_request(
                scene_index=scene.sequence_index, requirement=requirement
            )
            if request is None:
                raise VerticalSliceError("Could not build required visual asset request")
            asset_query = request.query
            try:
                resolved_asset = await self._orchestrator.resolve(request)
                if requirement.kind == VisualAssetKind.IMAGE:
                    assets = (VisualAssetMaterializer.materialize(resolved_asset),)
                elif requirement.kind == VisualAssetKind.BROLL:
                    broll_asset = VisualAssetMaterializer.materialize_broll(resolved_asset)
                    assets = (broll_asset,)
                else:
                    raise VerticalSliceError(
                        f"Unsupported asset requirement kind: {requirement.kind}"
                    )
            except Exception as exc:
                raise VerticalSliceError(
                    f"Asset orchestrator failed for scene {scene.sequence_index}: "
                    f"{self._sanitize_error(exc)}"
                ) from exc
        try:
            document = self._template_renderer.render(
                payload,
                assets=assets,
                accent_color=style_profile.accent_color if style_profile else None,
                bg_color=style_profile.bg_color if style_profile else None,
            )
            render_result = await self._video_renderer.render_clip(
                document=document,
                motion_profile=direction.motion_profile,
                duration_seconds=duration_seconds,
                output_path=scene_visual_out_path,
                browser_runtime=browser,
                fps=fps,
                broll_asset=broll_asset,
                frame_count_override=frame_count_override,
            )
        except Exception as exc:
            raise VerticalSliceError(
                f"Scene video render failed for scene {scene.sequence_index}: "
                f"{self._sanitize_error(exc)}"
            ) from exc
        origin = "PROVIDER" if resolved_asset else "TEMPLATE"
        beat = VerticalSliceRuntimeBeatVisual(
            parent_scene_index=scene.sequence_index,
            materialized_beat_index=0,
            source_editorial_beat_index=0,
            source_statement_references=tuple(scene.source_statement_references),
            semantic_role="LEGACY_PARENT_SCENE",
            start_offset_ms=0,
            end_offset_ms=duration_ms,
            duration_ms=duration_ms,
            template_id=document.template_id.value,
            camera_motion_intent="LEGACY",
            transition_intent="HARD_CUT",
            asset_action="ACQUIRE_IF_NEEDED" if resolved_asset else "LOCAL_TEMPLATE",
            visual_origin=origin,
            asset_kind=asset_kind,
            query=asset_query,
            provider=resolved_asset.provider if resolved_asset else None,
            provider_asset_id=resolved_asset.asset_id if resolved_asset else None,
            source_url=(
                _safe_external_reference(resolved_asset.source_url)
                if resolved_asset else None
            ),
            source_page_url=(
                _safe_external_reference(resolved_asset.source_page_url)
                if resolved_asset else None
            ),
            license_status=(
                resolved_asset.license_status if resolved_asset else LicenseStatus.GENERATED
            ),
            license_name=resolved_asset.license_name if resolved_asset else None,
            license_url=(
                _safe_external_reference(resolved_asset.license_url)
                if resolved_asset else None
            ),
            attribution=resolved_asset.attribution_text if resolved_asset else None,
            allowed_attribution_channels=(
                tuple(sorted(set(resolved_asset.allowed_attribution_channels), key=str))
                if resolved_asset else ()
            ),
            provider_metadata=(
                _safe_provider_metadata(resolved_asset.metadata) if resolved_asset else {}
            ),
            provider_asset_content_sha256=(
                resolved_asset.content_sha256 if resolved_asset else None
            ),
            rendered_beat_clip_sha256=render_result.video_sha256,
        )
        return {
            "execution_mode": "LEGACY_SINGLE_SCENE",
            "scene_out_path": scene_out_path,
            "scene_visual_out_path": scene_visual_out_path,
            "visual_sha256": render_result.video_sha256,
            "runtime_beats": (beat,),
            "template_id": document.template_id.value,
            "resolved_asset": resolved_asset,
            "asset_kind": asset_kind,
            "asset_provider": resolved_asset.provider if resolved_asset else None,
            "asset_id": resolved_asset.asset_id if resolved_asset else None,
            "asset_query": asset_query,
            "visual_origin": origin,
            "visual_content_sha256": (
                resolved_asset.content_sha256 if resolved_asset else render_result.video_sha256
            ),
            "visual_mime_type": resolved_asset.mime_type if resolved_asset else "video/mp4",
            "visual_width": resolved_asset.width if resolved_asset else render_result.width,
            "visual_height": resolved_asset.height if resolved_asset else render_result.height,
            "visual_duration_ms": (
                int(round(resolved_asset.duration_seconds * 1000))
                if resolved_asset and resolved_asset.duration_seconds is not None
                else duration_ms
            ),
            "text_fitting": tuple(item.model_dump() for item in document.text_fitting),
            "text_truncated": any(item.text_truncated for item in document.text_fitting),
        }

    async def _finalize_narrated_parent_scene(
        self,
        *,
        scene_index: int,
        scene_visual_path: Path,
        scene_output_path: Path,
        audio_path: Path,
        ass_path: Path | None,
        subtitle_enabled: bool,
        work_dir: Path,
    ) -> str:
        """Burn parent subtitles once, then mux parent narration once."""
        mux_video_input = scene_visual_path
        if subtitle_enabled and ass_path is not None:
            subtitled_path = work_dir / f"scene_{scene_index:03d}_subtitled.mp4"
            try:
                await self._ffmpeg_renderer.burn_ass_subtitles(
                    video_path=scene_visual_path,
                    ass_path=ass_path,
                    output_path=subtitled_path,
                )
            except Exception as exc:
                raise VerticalSliceError(
                    f"ASS burn failed for scene {scene_index}: {self._sanitize_error(exc)}"
                ) from exc
            if not subtitled_path.exists() or subtitled_path.stat().st_size <= 0:
                raise VerticalSliceError(
                    f"Subtitle burned output missing or empty for scene {scene_index}"
                )
            mux_video_input = subtitled_path
        try:
            await self._ffmpeg_renderer.mux_video_audio(
                video_path=mux_video_input,
                audio_path=audio_path,
                output_path=scene_output_path,
                preserve_video_duration=True,
            )
        except Exception as exc:
            raise VerticalSliceError(
                f"Mux failed for scene {scene_index}: {self._sanitize_error(exc)}"
            ) from exc
        if not scene_output_path.exists() or scene_output_path.stat().st_size <= 0:
            raise VerticalSliceError(f"Mux output missing or empty for scene {scene_index}")
        return self._compute_streaming_sha(scene_output_path)

    async def _render_canonical_production_core(
        self,
        session: AsyncSession,
        production_request_id: UUID,
        contract: CanonicalProductionContract,
        *,
        mission_id: UUID | None = None,
        mission_execution_id: UUID | None = None,
        task_id: UUID | None = None,
        render_job_id: UUID | None = None,
        background_music: VerticalSliceBackgroundMusicInput | None = None,
        sfx_inputs: list[VerticalSliceSFXInput] | None = None,
        audio_mix_enabled: bool = False,
        style_profile: ChannelStyleProfile | None = None,
    ) -> VerticalSliceRenderResult:
        audio_mix_enabled = background_music is not None or bool(sfx_inputs)
        self._validate_canonical_target(contract)
        fps = contract.policy.target_fps
        voice_profile = contract.policy.voice_profile.to_dict()

        if audio_mix_enabled and not self._narration_provider:
            raise VerticalSliceError("narration_provider MUST be configured when audio mix is enabled")


        if fps <= 0 or fps > 60:
            raise VerticalSliceError(f"Invalid fps: {fps}. Must be > 0 and <= 60.")

        prod_stmt = (
            select(ProductionRequest)
            .where(ProductionRequest.id == production_request_id)
            .options(
                selectinload(ProductionRequest.script_version)
                .selectinload(ScriptVersion.sections)
                .selectinload(ScriptSection.statements)
                .selectinload(ScriptStatement.citations),
                selectinload(ProductionRequest.content_request),
                selectinload(ProductionRequest.channel_dna_revision),
                selectinload(ProductionRequest.channel),
            )
        )
        prod_res = await session.execute(prod_stmt)
        production_request = prod_res.scalar_one_or_none()
        if production_request is None:
            raise VerticalSliceError(
                f"ProductionRequest '{production_request_id}' not found"
            )

        exact_pins = {
            "production_request_id": production_request.id,
            "channel_id": production_request.channel_id,
            "content_request_id": production_request.content_request_id,
            "script_version_id": production_request.script_version_id,
            "channel_dna_revision_id": production_request.channel_dna_revision_id,
            "mission_execution_id": production_request.mission_execution_id,
        }
        for field_name, actual in exact_pins.items():
            if getattr(contract.lineage, field_name) != actual:
                raise VerticalSliceError(
                    f"ProductionRequest {field_name} does not match canonical contract"
                )

        content_request_id = production_request.content_request_id
        content_req = production_request.content_request
        script_version = production_request.script_version
        pinned_dna_revision = production_request.channel_dna_revision
        channel_id = production_request.channel_id
        if content_req is None or content_req.id != content_request_id:
            raise VerticalSliceError("ProductionRequest pinned content request is missing")
        if script_version is None or script_version.id != contract.lineage.script_version_id:
            raise VerticalSliceError("ProductionRequest pinned ScriptVersion is missing")
        if (
            pinned_dna_revision is None
            or pinned_dna_revision.id != contract.lineage.channel_dna_revision_id
        ):
            raise VerticalSliceError(
                "ProductionRequest pinned ChannelDNARevision is missing"
            )
        channel = production_request.channel

        if contract.policy.brand_spec_reference:
            raise VerticalSliceError(
                "Canonical brand_spec_reference is unsupported by the V2 brand resolver"
            )

        resolved_brand = resolve_production_brand_spec(
            channel_dna_snapshot=pinned_dna_revision.snapshot,
            channel_dna_revision_id=pinned_dna_revision.id,
            production_format=BrandFormat.LONG_FORM,
        )
        try:
            resolved_logo, resolved_intro, resolved_outro = (
                self._resolve_visual_brand_assets(
                    channel_id,
                    resolved_brand.logo_asset,
                    resolved_brand.intro_asset,
                    resolved_brand.outro_asset,
                )
            )
        except BrandAssetResolutionError as exc:
            raise VerticalSliceError(
                f"Brand asset resolution failed: {self._sanitize_error(exc)}"
            ) from exc

        if style_profile is None and channel_id:
            try:
                if channel is None:
                    ch_stmt = select(Channel).where(Channel.id == channel_id)
                    ch_res = await session.execute(ch_stmt)
                    channel = ch_res.scalar_one_or_none()
                if channel:
                    style_profile = extract_channel_style_profile(channel.metadata_)
                else:
                    style_profile = ChannelStyleProfile()
            except Exception:
                style_profile = ChannelStyleProfile()
        elif style_profile is None:
            style_profile = ChannelStyleProfile()

        # Resolve canonical subtitle mode and policy
        canonical_sub_mode = contract.policy.subtitle_mode
        canonical_sub_fallback = contract.policy.subtitle_fallback_policy
        subtitle_style = contract.policy.subtitle_style

        if subtitle_style is not None:
            resolved_subtitle_style = subtitle_style
        elif style_profile is not None:
            resolved_subtitle_style = style_profile.resolve_effective_style()
        else:
            resolved_subtitle_style = SubtitleRenderStyle()

        # Check narration prerequisite if subtitles are requested
        if canonical_sub_mode != SubtitleMode.OFF and not self._narration_provider:
            raise VerticalSliceError("Subtitles require narration")

        # Evaluate preliminary subtitle mode decision
        subtitle_decision = evaluate_subtitle_mode_decision(
            requested_mode=canonical_sub_mode,
            fallback_policy=canonical_sub_fallback,
            has_word_timing=False,
            timing_available=True,
        )

        effective_subtitle_enabled = (subtitle_decision.effective_mode != SubtitleMode.OFF)

        # 3. Deterministic Run Fingerprint & Idempotency Check
        canonical_visual_mode = contract.policy.visual_asset_mode.value
        if canonical_visual_mode != self._visual_asset_mode:
            raise VerticalSliceError(
                "Configured visual capability does not match canonical visual_asset_mode"
            )
        cache_owner_id = production_request_id
        canonical_render_data = contract.to_provenance_dict()
        optional_lineage = canonical_render_data["lineage"]
        for field_name in (
            "mission_id",
            "mission_execution_id",
            "task_id",
            "render_job_id",
        ):
            optional_lineage[field_name] = None
        canonical_render_fingerprint = hashlib.sha256(
            json.dumps(canonical_render_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        fingerprint_input = (
            f"omega-canonical-production-v1:{cache_owner_id}:"
            f"{channel_id}:{content_request_id}:{script_version.id}:"
            f"{pinned_dna_revision.id}:{canonical_render_fingerprint}:{fps}:"
            f"visual-director-{VISUAL_DIRECTOR_VERSION}:"
            "visual-asset-selection-v2:"
            f"visual-asset-mode:{canonical_visual_mode}:"
            f"subtitle-semantics-v{SUBTITLE_SEMANTICS_VERSION}:"
            f"runtime-truth-v{RUNTIME_TRUTH_SCHEMA_VERSION}"
        )
        fingerprint_input += f":{_canonical_render_semantics_identity()}"
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
            if effective_subtitle_enabled:
                subtitle_marker = f"karaoke:{KARAOKE_SUBTITLE_VERSION}:words={KARAOKE_MAX_WORDS_PER_CUE}:chars={KARAOKE_MAX_CHARS_PER_CUE}"
                fingerprint_input += f":{subtitle_marker}"
                fingerprint_input += ":style=" + resolved_subtitle_style.model_dump_json()
                fingerprint_input += f":submode={subtitle_decision.effective_mode.value}"
        normalized_sfx = []
        bgm_sha: str | None = None
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

        # Subtitle semantics affect the physical burned output.  Never reinterpret,
        # rewrite, or delete an incompatible cached render.  If an incompatible
        # manifest occupies the current deterministic slot, advance to another
        # deterministic slot and perform a fresh render there.
        cache_slot = 0
        while True:
            cache_fingerprint_input = fingerprint_input
            if cache_slot:
                cache_fingerprint_input += f":incompatible-cache-slot={cache_slot}"
            run_fingerprint = hashlib.sha256(
                cache_fingerprint_input.encode("utf-8")
            ).hexdigest()
            run_dir = self._output_root / str(cache_owner_id) / run_fingerprint
            final_mp4_path = run_dir / "final.mp4"
            manifest_path = run_dir / "manifest.json"

            final_exists = final_mp4_path.is_file()
            manifest_exists = manifest_path.is_file()
            if not manifest_exists:
                break
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    cached_manifest = json.load(f)
            except (OSError, json.JSONDecodeError):
                break
            if _validate_cached_render_semantics(
                manifest=cached_manifest,
                canonical_requested_mode=canonical_sub_mode,
                fallback_policy=canonical_sub_fallback,
            ):
                break
            cache_slot += 1

        if final_exists and manifest_exists:
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest_data = json.load(f)


                if manifest_data.get("run_fingerprint") != run_fingerprint:
                    raise VerticalSliceError("Manifest run_fingerprint mismatch")

                expected_lineage = {
                    "production_request_id": str(production_request_id),
                    "channel_id": str(channel_id),
                    "channel_dna_revision_id": str(pinned_dna_revision.id),
                    "content_request_id": str(content_request_id),
                    "script_version_id": str(script_version.id),
                }
                for field_name, expected in expected_lineage.items():
                    if manifest_data.get(field_name) != expected:
                        raise VerticalSliceError(
                            f"Manifest {field_name} does not match canonical lineage"
                        )

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
                    mission_id=mission_id,
                    mission_execution_id=mission_execution_id,
                    content_request_id=content_request_id,
                    script_version_id=script_version.id,
                    production_request_id=production_request_id,
                    channel_id=channel_id,
                    channel_dna_revision_id=pinned_dna_revision.id,
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
                    subtitle_semantics_version=manifest_data[
                        "subtitle_semantics_version"
                    ],
                    narration_quality=manifest_data.get("narration_quality"),
                    narration_source_refs=tuple(manifest_data.get("narration_source_refs", [])),
                    runtime_timeline_duration_ms=manifest_data.get("runtime_timeline_duration_ms"),
                    runtime_narration_segments=tuple(
                        VerticalSliceRuntimeNarrationSegment(**s) for s in manifest_data.get("runtime_narration_segments", [])
                    ),
                    runtime_subtitle_cues=tuple(
                        VerticalSliceRuntimeSubtitleCue(**c) for c in manifest_data.get("runtime_subtitle_cues", [])
                    ),
                    runtime_subtitle_artifacts=tuple(
                        VerticalSliceRuntimeSubtitleArtifact(**item)
                        for item in manifest_data.get("runtime_subtitle_artifacts", [])
                    ),
                    runtime_scenes=tuple(
                        VerticalSliceSceneResult(**s) for s in manifest_data.get("scenes", [])
                    ),
                    runtime_visual_beats=tuple(
                        VerticalSliceRuntimeBeatVisual(**item)
                        for item in manifest_data.get("runtime_visual_beats", [])
                    ),
                    runtime_branding=manifest_data.get("runtime_branding", {}),
                    runtime_audio_mix=manifest_data.get("runtime_audio_mix", {}),
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
                    requested_subtitle_mode=manifest_data.get(
                        "requested_subtitle_mode",
                        "KARAOKE" if manifest_data.get("karaoke_subtitles_enabled") else ("STANDARD" if manifest_data.get("subtitle_enabled", True) else "OFF"),
                    ),
                    effective_subtitle_mode=manifest_data.get(
                        "effective_subtitle_mode",
                        "KARAOKE" if manifest_data.get("karaoke_subtitles_enabled") else ("STANDARD" if manifest_data.get("subtitle_enabled", True) else "OFF"),
                    ),
                    subtitle_fallback_applied=bool(manifest_data.get("subtitle_fallback_applied", False)),
                    subtitle_fallback_reason=manifest_data.get("subtitle_fallback_reason"),
                    subtitle_timing_source=manifest_data.get(
                        "subtitle_timing_source",
                        "DERIVED_SEGMENT_TIMING" if manifest_data.get("subtitle_enabled", True) else "NONE",
                    ),
                    subtitle_mode_decision=(
                        SubtitleModeDecision(**manifest_data["subtitle_mode_decision"])
                        if manifest_data.get("subtitle_mode_decision")
                        else None
                    ),
                    subtitle_burn_applied=bool(
                        manifest_data.get("subtitle_burn_applied", False)
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
            runtime_subtitle_artifacts = []
            runtime_visual_beats: list[VerticalSliceRuntimeBeatVisual] = []


            indices = set()
            for scene in storyboard.scenes:
                if scene.sequence_index in indices:
                    raise VerticalSliceError(f"Duplicate sequence_index: {scene.sequence_index}")
                indices.add(scene.sequence_index)

            sorted_scenes = sorted(storyboard.scenes, key=lambda s: s.sequence_index)

            # Resolve narration timing for every scene before any visual render or
            # subtitle burn.  SubtitleModeDecision is production-render state and
            # must not change as individual scenes are processed.
            prepared_narration: dict[int, dict[str, Any]] = {}
            for scene in sorted_scenes:
                if scene.estimated_duration_seconds <= 0:
                    raise VerticalSliceError(
                        f"Scene {scene.sequence_index} duration {scene.estimated_duration_seconds} <= 0"
                    )
                if not self._narration_provider:
                    continue

                narration_text = " ".join(str(scene.narration_excerpt or "").split())
                if not narration_text:
                    raise VerticalSliceError(
                        f"Empty narration text for scene {scene.sequence_index}"
                    )
                try:
                    audio_asset = await self._narration_provider.synthesize_segment_audio(
                        channel_id=channel_id,
                        request_id=content_request_id,
                        segment={"text": narration_text},
                        voice_profile=voice_profile,
                    )
                except Exception as e:
                    raise VerticalSliceError(
                        f"Narration provider failed: {self._sanitize_error(e)}"
                    ) from e

                rel_uri = audio_asset.get("storage_uri")
                if not rel_uri:
                    raise VerticalSliceError("Audio asset missing storage_uri")
                audio_path = self._narration_storage.resolve_stored_uri(
                    channel_id, content_request_id, rel_uri
                )
                if not audio_path.exists() or audio_path.stat().st_size <= 0:
                    raise VerticalSliceError("Audio file missing or empty")

                duration_ms = audio_asset.get("duration_ms")
                if (
                    isinstance(duration_ms, bool)
                    or not isinstance(duration_ms, int)
                    or duration_ms <= 0
                ):
                    raise VerticalSliceError("Audio duration missing or zero")
                if not audio_asset.get("content_hash"):
                    raise VerticalSliceError("Audio asset missing content_hash")

                scene_start_ms = runtime_cursor_ms
                scene_end_ms = scene_start_ms + duration_ms
                runtime_narration_segments.append(
                    VerticalSliceRuntimeNarrationSegment(
                        scene_index=scene.sequence_index,
                        start_ms=scene_start_ms,
                        end_ms=scene_end_ms,
                        duration_ms=duration_ms,
                        text=narration_text,
                        audio_asset_id=(
                            str(audio_asset.get("id"))
                            if audio_asset.get("id") is not None
                            else None
                        ),
                        storage_reference=str(rel_uri),
                        audio_content_sha256=str(audio_asset["content_hash"]),
                        provider=self._narration_provider.__class__.__name__,
                        model=getattr(self._narration_provider, "model", None),
                        voice=(
                            audio_asset.get("voice")
                            or getattr(self._narration_provider, "default_voice", None)
                        ),
                        voice_profile=dict(voice_profile or {}),
                        quality=audio_asset.get("narration_quality"),
                        license_status=audio_asset.get("license_status"),
                        source_reference=audio_asset.get("source_ref"),
                        attribution=audio_asset.get("attribution"),
                    )
                )
                runtime_cursor_ms = scene_end_ms
                narration_total_duration_ms += duration_ms

                narration_quality = audio_asset.get("narration_quality")
                if narration_quality:
                    narration_qualities.append(narration_quality)
                narration_ref = audio_asset.get("source_ref")
                if narration_ref and narration_ref not in narration_source_refs_list:
                    narration_source_refs_list.append(narration_ref)

                prepared_narration[scene.sequence_index] = {
                    "asset": audio_asset,
                    "path": audio_path,
                    "duration_ms": duration_ms,
                    "start_ms": scene_start_ms,
                    "end_ms": scene_end_ms,
                    "text": narration_text,
                }

            if canonical_sub_mode == SubtitleMode.KARAOKE:
                capability_error = None
                for scene in sorted_scenes:
                    prepared = prepared_narration.get(scene.sequence_index)
                    if prepared is None:
                        capability_error = (
                            f"scene_{scene.sequence_index}:missing_narration_timing"
                        )
                        break
                    timing_error = _derived_karaoke_timing_error(
                        prepared["text"], prepared["duration_ms"]
                    )
                    if timing_error is not None:
                        capability_error = f"scene_{scene.sequence_index}:{timing_error}"
                        break
                subtitle_decision = evaluate_subtitle_mode_decision(
                    requested_mode=canonical_sub_mode,
                    fallback_policy=canonical_sub_fallback,
                    has_word_timing=False,
                    timing_available=capability_error is None,
                    timing_error_reason=capability_error,
                    segment_timing_available=True,
                )

            effective_subtitle_enabled = (
                subtitle_decision.effective_mode != SubtitleMode.OFF
            )

            if self._narration_provider:
                parent_intervals = [
                    (
                        prepared_narration[s.sequence_index]["start_ms"],
                        prepared_narration[s.sequence_index]["end_ms"],
                    )
                    for s in sorted_scenes
                ]
                global_logical_duration_ms = runtime_cursor_ms
            else:
                parent_intervals = []
                curr_ms = 0
                for s in sorted_scenes:
                    dur_ms = max(1, int(round(s.estimated_duration_seconds * 1000)))
                    parent_intervals.append((curr_ms, curr_ms + dur_ms))
                    curr_ms += dur_ms
                global_logical_duration_ms = curr_ms

            parent_frame_budgets = quantize_parent_frame_counts(
                parent_intervals, global_logical_duration_ms, fps
            )
            scene_frame_budget_map = {
                s.sequence_index: fb
                for s, fb in zip(sorted_scenes, parent_frame_budgets, strict=True)
            }
            global_canonical_frame_budget = sum(parent_frame_budgets)

            async with self._browser_runtime_factory() as browser:
                for scene in sorted_scenes:
                    original_strategy = scene.visual_strategy
                    effective_scene = self._apply_v0_compatibility(scene)
                    effective_scene = self._apply_visual_asset_mode_policy(effective_scene)
                    effective_strategy = effective_scene.visual_strategy

                    audio_sha: str | None = None
                    audio_duration_sec: float | None = None
                    actual_scene_duration_seconds = effective_scene.estimated_duration_seconds
                    audio_path: Path | None = None
                    scene_subtitle_cues = 0
                    scene_subtitle_text_truncated = False
                    ass_path = None

                    if self._narration_provider:
                        prepared = prepared_narration[scene.sequence_index]
                        audio_asset = prepared["asset"]
                        audio_path = prepared["path"]
                        duration_ms = prepared["duration_ms"]
                        scene_start_ms = prepared["start_ms"]
                        audio_duration_sec = duration_ms / 1000.0
                        actual_scene_duration_seconds = audio_duration_sec
                        audio_sha = audio_asset["content_hash"]
                        if effective_subtitle_enabled:
                            effective_mode = subtitle_decision.effective_mode
                            use_karaoke_highlight = (effective_mode == SubtitleMode.KARAOKE)
                            segment = {
                                "text": prepared["text"],
                                "start_ms": 0,
                                "duration_ms": duration_ms,
                            }
                            try:
                                scene_cues = generate_karaoke_cues(
                                    [segment],
                                    max_words_per_cue=KARAOKE_MAX_WORDS_PER_CUE if use_karaoke_highlight else SENTENCE_MAX_WORDS_PER_CUE,
                                    max_chars_per_cue=KARAOKE_MAX_CHARS_PER_CUE if use_karaoke_highlight else SENTENCE_MAX_CHARS_PER_CUE,
                                    sentence_mode=not use_karaoke_highlight,
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

                                # Build effective style with karaoke flag matching effective mode
                                effective_style = resolved_subtitle_style.model_copy(update={"karaoke": use_karaoke_highlight})

                                ass_document = generate_karaoke_ass_document(
                                    scene_cues,
                                    width=1920,
                                    height=1080,
                                    style=effective_style,
                                )
                                ass_content = ass_document.content
                                runtime_subtitle_artifacts.append(
                                    VerticalSliceRuntimeSubtitleArtifact(
                                        scene_index=scene.sequence_index,
                                        content_sha256=hashlib.sha256(
                                            ass_content.encode("utf-8")
                                        ).hexdigest(),
                                    )
                                )
                                scene_subtitle_text_truncated = any(
                                    item.text_truncated for item in ass_document.layout
                                )
                                ass_path = work_dir / f"scene_{scene.sequence_index:03d}.ass"
                                with open(ass_path, "w", encoding="utf-8") as f:
                                    f.write(ass_content)
                                if not ass_path.exists() or ass_path.stat().st_size <= 0:
                                    raise VerticalSliceError(f"ASS file missing or empty for scene {scene.sequence_index}")
                            except Exception as e:
                                raise VerticalSliceError(f"Subtitle generation failed: {self._sanitize_error(e)}") from e

                    scene_timeline_start_ms = (
                        prepared_narration[scene.sequence_index]["start_ms"]
                        if self._narration_provider
                        else parent_intervals[sorted_scenes.index(scene)][0]
                    )
                    scene_frame_budget = scene_frame_budget_map[scene.sequence_index]

                    visual = await self._render_parent_visual(
                        script_dict=script_dict,
                        scene=effective_scene,
                        duration_seconds=actual_scene_duration_seconds,
                        canonical_visual_mode=canonical_visual_mode,
                        work_dir=work_dir,
                        browser=browser,
                        fps=fps,
                        style_profile=style_profile,
                        narration_enabled=bool(self._narration_provider),
                        timeline_start_ms=scene_timeline_start_ms,
                        frame_count_override=scene_frame_budget,
                    )
                    scene_out_path = visual["scene_out_path"]
                    scene_visual_out_path = visual["scene_visual_out_path"]
                    final_scene_sha = visual["visual_sha256"]
                    runtime_visual_beats.extend(visual["runtime_beats"])
                    resolved_asset = visual["resolved_asset"]
                    asset_kind_str = visual["asset_kind"]
                    asset_provider_str = visual["asset_provider"]
                    asset_id_str = visual["asset_id"]
                    asset_query_str = visual["asset_query"]
                    if effective_strategy == VisualStrategy.IMAGE:
                        image_scenes += 1
                    elif effective_strategy == VisualStrategy.BROLL:
                        broll_scenes += 1
                    else:
                        template_scenes += 1
                    if self._narration_provider:
                        if audio_path is None:
                            raise VerticalSliceError(
                                f"Narration audio missing for scene {scene.sequence_index}"
                            )
                        final_scene_sha = await self._finalize_narrated_parent_scene(
                            scene_index=scene.sequence_index,
                            scene_visual_path=scene_visual_out_path,
                            scene_output_path=scene_out_path,
                            audio_path=audio_path,
                            ass_path=ass_path,
                            subtitle_enabled=effective_subtitle_enabled,
                            work_dir=work_dir,
                        )
                    ordered_scene_paths.append(scene_out_path)
                    total_duration += actual_scene_duration_seconds

                    if self._narration_provider:
                        actual_start_ms = prepared_narration[scene.sequence_index]["start_ms"]
                        actual_end_ms = prepared_narration[scene.sequence_index]["end_ms"]
                    else:
                        actual_start_ms = sum(
                            existing.duration_ms or 0 for existing in scene_results
                        )
                        actual_duration_ms = int(round(actual_scene_duration_seconds * 1000))
                        actual_end_ms = actual_start_ms + actual_duration_ms
                    actual_duration_ms = actual_end_ms - actual_start_ms

                    scene_results.append(
                        VerticalSliceSceneResult(
                            sequence_index=scene.sequence_index,
                            source_section_id=scene.section_id,
                            source_statement_references=tuple(
                                scene.source_statement_references
                            ),
                            narration_text=scene.narration_excerpt,
                            original_strategy=original_strategy.value,
                            effective_strategy=effective_strategy.value,
                            template_id=visual["template_id"],
                            execution_mode=visual["execution_mode"],
                            asset_kind=asset_kind_str,
                            asset_provider=asset_provider_str,
                            asset_id=asset_id_str,
                            asset_query=asset_query_str,
                            duration_seconds=actual_scene_duration_seconds,
                            start_ms=actual_start_ms,
                            end_ms=actual_end_ms,
                            duration_ms=actual_duration_ms,
                            content_sha256=final_scene_sha,
                            audio_content_sha256=audio_sha,
                            audio_duration_seconds=audio_duration_sec,
                            subtitle_cue_count=scene_subtitle_cues if effective_subtitle_enabled else None,
                            subtitle_text_truncated=scene_subtitle_text_truncated if effective_subtitle_enabled else None,
                            text_fitting=visual["text_fitting"],
                            text_truncated=visual["text_truncated"],
                            visual_origin=visual["visual_origin"],
                            visual_mode=canonical_visual_mode,
                            asset_source_url=(
                                _safe_external_reference(resolved_asset.source_url)
                                if resolved_asset is not None
                                else None
                            ),
                            asset_source_page_url=(
                                _safe_external_reference(resolved_asset.source_page_url)
                                if resolved_asset is not None
                                else None
                            ),
                            asset_license_status=(
                                resolved_asset.license_status
                                if resolved_asset is not None
                                else (
                                    LicenseStatus.GENERATED
                                    if visual["execution_mode"] == "LEGACY_SINGLE_SCENE"
                                    else None
                                )
                            ),
                            asset_license_name=(
                                resolved_asset.license_name
                                if resolved_asset is not None
                                else None
                            ),
                            asset_license_url=(
                                _safe_external_reference(resolved_asset.license_url)
                                if resolved_asset is not None
                                else None
                            ),
                            asset_attribution=(
                                resolved_asset.attribution_text
                                if resolved_asset is not None
                                else None
                            ),
                            asset_allowed_attribution_channels=(
                                tuple(
                                    sorted(
                                        set(resolved_asset.allowed_attribution_channels),
                                        key=str,
                                    )
                                )
                                if resolved_asset is not None
                                else ()
                            ),
                            visual_content_sha256=visual["visual_content_sha256"],
                            visual_mime_type=visual["visual_mime_type"],
                            visual_width=visual["visual_width"],
                            visual_height=visual["visual_height"],
                            visual_duration_ms=visual["visual_duration_ms"],
                            asset_provider_metadata=(
                                _safe_provider_metadata(resolved_asset.metadata)
                                if resolved_asset is not None
                                else {}
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
                runtime_subtitle_artifacts = []

            # 8. Deterministic brand composition and final concatenation
            runtime_branding = {
                "policy_source": str(resolved_brand.source_channel_dna_revision_id),
                "canonical_policy": {
                    "channel_bug_enabled": contract.policy.channel_bug_enabled,
                    "intro_enabled": contract.policy.intro_enabled,
                    "outro_enabled": contract.policy.outro_enabled,
                    "brand_spec_reference": contract.policy.brand_spec_reference,
                },
                "assets": [
                    {
                        "role": role,
                        "applied": applied,
                        "reference": asset.reference,
                        "content_sha256": asset.content_hash,
                        "mime_type": asset.mime_type,
                    }
                    for role, asset, applied in (
                        (
                            "CHANNEL_BUG",
                            resolved_logo,
                            resolved_logo is not None
                            and resolved_brand.channel_bug is not None
                            and contract.policy.channel_bug_enabled,
                        ),
                        (
                            "INTRO",
                            resolved_intro,
                            resolved_intro is not None
                            and contract.policy.intro_enabled,
                        ),
                        (
                            "OUTRO",
                            resolved_outro,
                            resolved_outro is not None
                            and contract.policy.outro_enabled,
                        ),
                    )
                    if asset is not None
                ],
            }
            branded_content_paths = ordered_scene_paths
            if (
                resolved_logo is not None
                and resolved_brand.channel_bug is not None
                and contract.policy.channel_bug_enabled
            ):
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
                    exact_video_frame_count=global_canonical_frame_budget,
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
            runtime_audio_mix = {
                "enabled": False,
                "narration_applied": bool(self._narration_provider),
                "target_duration_ms": narration_total_duration_ms,
                "background_music": None,
                "sfx_events": [],
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
                runtime_audio_mix = {
                    "enabled": True,
                    "narration_applied": bool(self._narration_provider),
                    "target_duration_ms": audio_mix_target_duration_ms,
                    "background_music": (
                        {
                            "content_sha256": bgm_sha,
                            "duration_ms": background_music.duration_ms,
                            "license_status": str(background_music.license_status),
                            "gain_db": mix_plan.background_music.gain_db,
                            "loop_required": mix_plan.background_music.loop_required,
                            "fade_in_ms": mix_plan.background_music.fade_in_ms,
                            "fade_out_ms": mix_plan.background_music.fade_out_ms,
                            "attribution_required": mix_plan.background_music.attribution_required,
                        }
                        if mix_plan.background_music is not None
                        else None
                    ),
                    "sfx_events": [
                        {
                            "event_id": event.event_id,
                            "content_sha256": normalized_sfx[index][1],
                            "start_ms": event.start_ms,
                            "duration_ms": event.duration_ms,
                            "license_status": str(normalized_sfx[index][0].license_status),
                            "gain_db": event.gain_db,
                            "attribution_required": event.attribution_required,
                        }
                        for index, event in enumerate(mix_plan.sfx_events)
                    ],
                }

            enabled_intro = resolved_intro if contract.policy.intro_enabled else None
            enabled_outro = resolved_outro if contract.policy.outro_enabled else None
            if enabled_intro is not None or enabled_outro is not None:
                final_clip_paths = self._brand_clip_paths(
                    [working_content_mp4],
                    enabled_intro,
                    enabled_outro,
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

            # Master audio loudness normalization (P18-G1)
            # Normalized EXACTLY ONCE on final master after scene concatenation
            # and optional audio mixing/branding, but before artifact hashing.
            if self._narration_provider or audio_mix_enabled:
                normalized_master_mp4 = work_dir / "final_normalized.mp4"
                try:
                    await self._ffmpeg_renderer.normalize_master_audio(
                        video_path=working_final_mp4,
                        output_path=normalized_master_mp4,
                        target_i=FINAL_MASTER_TARGET_I,
                        target_tp=FINAL_MASTER_TARGET_TP,
                        target_lra=FINAL_MASTER_TARGET_LRA,
                        sample_rate_hz=FINAL_MASTER_SAMPLE_RATE_HZ,
                    )
                except Exception as e:
                    raise VerticalSliceError(f"Master audio normalization failed: {self._sanitize_error(e)}") from e
                if normalized_master_mp4.is_file() and normalized_master_mp4.stat().st_size > 0:
                    working_final_mp4 = normalized_master_mp4

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
                "runtime_truth_schema_version": RUNTIME_TRUTH_SCHEMA_VERSION,
                "subtitle_semantics_version": SUBTITLE_SEMANTICS_VERSION,
                "canonical_render_semantics_version": (
                    CANONICAL_RENDER_SEMANTICS_VERSION
                ),
                "final_master_sample_rate_hz": FINAL_MASTER_SAMPLE_RATE_HZ,
                "visual_asset_mode": canonical_visual_mode,
                "scene_artifacts_version": "v1",
                "resolved_brand_spec": resolved_brand.model_dump(mode="json"),
                "resolved_brand_identity": resolved_brand.identity,
                "resolved_brand_asset_identity": resolved_brand_identity,
                "production_request_id": (
                    str(production_request_id) if production_request_id else None
                ),
                "channel_id": str(channel_id),
                "channel_dna_revision_id": str(pinned_dna_revision.id),
                "mission_id": str(mission_id) if mission_id else None,
                "mission_execution_id": (
                    str(mission_execution_id) if mission_execution_id else None
                ),
                "task_id": str(task_id) if task_id else None,
                "render_job_id": str(render_job_id) if render_job_id else None,
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
                "subtitle_enabled": effective_subtitle_enabled,
                "karaoke_subtitles_enabled": bool(
                    effective_subtitle_enabled
                    and subtitle_decision.effective_mode == SubtitleMode.KARAOKE
                ),
                "subtitle_mode": (
                    "karaoke"
                    if subtitle_decision.effective_mode == SubtitleMode.KARAOKE
                    else "sentence"
                ),
                "requested_subtitle_mode": subtitle_decision.requested_mode.value,
                "effective_subtitle_mode": subtitle_decision.effective_mode.value,
                "subtitle_fallback_applied": subtitle_decision.fallback_applied,
                "subtitle_fallback_reason": subtitle_decision.fallback_reason,
                "subtitle_timing_source": subtitle_decision.timing_source.value,
                "subtitle_mode_decision": subtitle_decision.model_dump(),
                "subtitle_style_applied": (
                    resolved_subtitle_style.model_copy(update={"karaoke": subtitle_decision.effective_mode == SubtitleMode.KARAOKE}).model_dump()
                    if effective_subtitle_enabled
                    else None
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
                "runtime_subtitle_artifacts": [
                    item.model_dump() for item in runtime_subtitle_artifacts
                ],
                "subtitle_burn_applied": bool(
                    effective_subtitle_enabled and runtime_subtitle_artifacts
                ),
                "runtime_branding": runtime_branding,
                "runtime_audio_mix": runtime_audio_mix,
                "runtime_visual_beats": [
                    item.model_dump(mode="json") for item in runtime_visual_beats
                ],
                "scenes": [s.model_dump(mode="json") for s in scene_results],
            }

            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest_content, f, indent=2)

        finally:
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

        return VerticalSliceRenderResult(
            mission_id=mission_id,
            mission_execution_id=mission_execution_id,
            content_request_id=content_request_id,
            script_version_id=script_version.id,
            production_request_id=production_request_id,
            channel_id=channel_id,
            channel_dna_revision_id=pinned_dna_revision.id,
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
            subtitle_semantics_version=SUBTITLE_SEMANTICS_VERSION,
            narration_quality=final_narration_quality,
            narration_source_refs=final_narration_source_refs,
            runtime_timeline_duration_ms=runtime_timeline_duration_ms,
            runtime_narration_segments=tuple(runtime_narration_segments),
            runtime_subtitle_cues=tuple(runtime_subtitle_cues),
            runtime_subtitle_artifacts=tuple(runtime_subtitle_artifacts),
            runtime_scenes=tuple(scene_results),
            runtime_visual_beats=tuple(runtime_visual_beats),
            runtime_branding=runtime_branding,
            runtime_audio_mix=runtime_audio_mix,
            subtitle_style_applied=(
                resolved_subtitle_style.model_copy(update={"karaoke": subtitle_decision.effective_mode == SubtitleMode.KARAOKE})
                if effective_subtitle_enabled
                else None
            ),
            style_profile_applied=style_profile,
            effective_fps_mode="CFR",
            subtitle_enabled=effective_subtitle_enabled,
            karaoke_subtitles_enabled=bool(
                effective_subtitle_enabled
                and subtitle_decision.effective_mode == SubtitleMode.KARAOKE
            ),
            subtitle_mode=(
                "karaoke"
                if subtitle_decision.effective_mode == SubtitleMode.KARAOKE
                else "sentence"
            ),
            requested_subtitle_mode=subtitle_decision.requested_mode.value,
            effective_subtitle_mode=subtitle_decision.effective_mode.value,
            subtitle_fallback_applied=subtitle_decision.fallback_applied,
            subtitle_fallback_reason=subtitle_decision.fallback_reason,
            subtitle_timing_source=subtitle_decision.timing_source.value,
            subtitle_mode_decision=subtitle_decision,
            subtitle_burn_applied=bool(
                effective_subtitle_enabled and runtime_subtitle_artifacts
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

        if manifest.get("canonical_render_semantics_version", 0) >= 3:
            raise VerticalSliceError(
                "Scene regeneration is unavailable for multi-beat render semantics"
            )

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
                subtitle_semantics_version=rev_manifest[
                    "subtitle_semantics_version"
                ],
                narration_quality=rev_manifest.get("narration_quality"),
                narration_source_refs=tuple(rev_manifest.get("narration_source_refs", [])),
                runtime_timeline_duration_ms=rev_manifest.get("runtime_timeline_duration_ms"),
                runtime_narration_segments=tuple(
                    VerticalSliceRuntimeNarrationSegment(**s) for s in rev_manifest.get("runtime_narration_segments", [])
                ),
                runtime_subtitle_cues=tuple(
                    VerticalSliceRuntimeSubtitleCue(**c) for c in rev_manifest.get("runtime_subtitle_cues", [])
                ),
                runtime_subtitle_artifacts=tuple(
                    VerticalSliceRuntimeSubtitleArtifact(**item)
                    for item in rev_manifest.get("runtime_subtitle_artifacts", [])
                ),
                runtime_scenes=tuple(
                    VerticalSliceSceneResult(**s) for s in rev_manifest.get("scenes", [])
                ),
                runtime_branding=rev_manifest.get("runtime_branding", {}),
                runtime_audio_mix=rev_manifest.get("runtime_audio_mix", {}),
                subtitle_style_applied=(
                    SubtitleRenderStyle(**rev_manifest["subtitle_style_applied"])
                    if rev_manifest.get("subtitle_style_applied")
                    else None
                ),
                requested_subtitle_mode=rev_manifest.get(
                    "requested_subtitle_mode", "OFF"
                ),
                effective_subtitle_mode=rev_manifest.get(
                    "effective_subtitle_mode", "OFF"
                ),
                subtitle_fallback_applied=bool(
                    rev_manifest.get("subtitle_fallback_applied", False)
                ),
                subtitle_fallback_reason=rev_manifest.get(
                    "subtitle_fallback_reason"
                ),
                subtitle_timing_source=rev_manifest.get(
                    "subtitle_timing_source", "NONE"
                ),
                subtitle_burn_applied=bool(
                    rev_manifest.get("subtitle_burn_applied", False)
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
                subtitle_semantics_version=rev_manifest[
                    "subtitle_semantics_version"
                ],
                narration_quality=rev_manifest.get("narration_quality"),
                narration_source_refs=tuple(rev_manifest.get("narration_source_refs", [])),
                runtime_timeline_duration_ms=rev_manifest.get("runtime_timeline_duration_ms"),
                runtime_narration_segments=tuple(
                    VerticalSliceRuntimeNarrationSegment(**s) for s in rev_manifest.get("runtime_narration_segments", [])
                ),
                runtime_subtitle_cues=tuple(
                    VerticalSliceRuntimeSubtitleCue(**c) for c in rev_manifest.get("runtime_subtitle_cues", [])
                ),
                runtime_subtitle_artifacts=tuple(
                    VerticalSliceRuntimeSubtitleArtifact(**item)
                    for item in rev_manifest.get("runtime_subtitle_artifacts", [])
                ),
                runtime_scenes=tuple(
                    VerticalSliceSceneResult(**s) for s in rev_manifest.get("scenes", [])
                ),
                runtime_branding=rev_manifest.get("runtime_branding", {}),
                runtime_audio_mix=rev_manifest.get("runtime_audio_mix", {}),
                subtitle_style_applied=(
                    SubtitleRenderStyle(**rev_manifest["subtitle_style_applied"])
                    if rev_manifest.get("subtitle_style_applied")
                    else None
                ),
                requested_subtitle_mode=rev_manifest.get(
                    "requested_subtitle_mode", "OFF"
                ),
                effective_subtitle_mode=rev_manifest.get(
                    "effective_subtitle_mode", "OFF"
                ),
                subtitle_fallback_applied=bool(
                    rev_manifest.get("subtitle_fallback_applied", False)
                ),
                subtitle_fallback_reason=rev_manifest.get(
                    "subtitle_fallback_reason"
                ),
                subtitle_timing_source=rev_manifest.get(
                    "subtitle_timing_source", "NONE"
                ),
                subtitle_burn_applied=bool(
                    rev_manifest.get("subtitle_burn_applied", False)
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
