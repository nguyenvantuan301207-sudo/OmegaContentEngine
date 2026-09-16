"""Immutable artifact-scoped production runtime truth contracts and builder."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator, Mapping
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, model_validator
from pydantic_core import core_schema

from omega.application.production_contract import CanonicalProductionContract
from omega.domain.attribution_delivery import (
    AttributionDeliveryChannel,
    AttributionDeliveryState,
    AttributionObligation,
    canonicalize_attribution_obligations,
    create_attribution_obligation,
)
from omega.domain.production import LicenseStatus

RUNTIME_TRUTH_SCHEMA_VERSION = 3
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_MARKERS = ("token", "secret", "signature", "credential", "apikey", "api_key")


class FrozenJsonObject(Mapping[str, Any]):
    """Deeply immutable, strictly JSON-compatible mapping."""

    __slots__ = ("_data", "_hash")

    def __init__(self, value: Mapping[str, Any] | None = None) -> None:
        object.__setattr__(
            self,
            "_data",
            {str(key): self._freeze(item) for key, item in dict(value or {}).items()},
        )
        object.__setattr__(self, "_hash", None)

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Runtime truth JSON cannot contain non-finite floats")
            return value
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise TypeError("Runtime truth JSON object keys must be strings")
            return FrozenJsonObject(value)
        if isinstance(value, (list, tuple)):
            return tuple(cls._freeze(item) for item in value)
        raise TypeError(
            f"Runtime truth JSON cannot contain {type(value).__name__} objects"
        )

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("FrozenJsonObject is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("FrozenJsonObject is immutable")

    def __hash__(self) -> int:
        current = self._hash
        if current is None:
            current = hash(self.canonical_json())
            object.__setattr__(self, "_hash", current)
        return current

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenJsonObject):
            return self.to_dict() == other.to_dict()
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other)
        return NotImplemented

    @classmethod
    def _thaw(cls, value: Any) -> Any:
        if isinstance(value, FrozenJsonObject):
            return {key: cls._thaw(value[key]) for key in sorted(value)}
        if isinstance(value, tuple):
            return [cls._thaw(item) for item in value]
        return value

    def to_dict(self) -> dict[str, Any]:
        return self._thaw(self)

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ):
        def validate(value: Any) -> FrozenJsonObject:
            if isinstance(value, cls):
                return value
            if isinstance(value, Mapping):
                return cls(value)
            raise TypeError("Expected a JSON object")

        return core_schema.no_info_after_validator_function(
            validate,
            core_schema.any_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: value.to_dict(),
                return_schema=core_schema.dict_schema(),
            ),
        )


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class RuntimeTruthLineage(_FrozenModel):
    channel_id: UUID
    production_request_id: UUID
    content_request_id: UUID
    script_version_id: UUID
    channel_dna_revision_id: UUID
    render_plan_id: UUID
    render_job_id: UUID
    media_artifact_id: UUID
    artifact_version: int = Field(ge=1)
    mission_id: UUID | None = None
    mission_execution_id: UUID | None = None
    task_id: UUID | None = None


class RuntimeSceneTruth(_FrozenModel):
    sequence_index: int = Field(ge=1)
    source_section_id: str | None = None
    source_statement_references: tuple[int, ...] = ()
    narration_text: str | None = None
    original_strategy: str
    effective_strategy: str
    template_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    scene_content_sha256: str
    visual_origin: Literal["EXPLICIT", "TEMPLATE", "PROVIDER"]
    visual_index: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_timing_and_hash(self) -> RuntimeSceneTruth:
        if self.end_ms - self.start_ms != self.duration_ms:
            raise ValueError("Runtime scene duration must equal end_ms - start_ms")
        if not _SHA256.fullmatch(self.scene_content_sha256):
            raise ValueError("Runtime scene hash must be lowercase SHA-256")
        return self


class RuntimeNarrationTruth(_FrozenModel):
    sequence_index: int = Field(ge=1)
    scene_index: int = Field(ge=1)
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    audio_asset_id: str | None = None
    storage_reference: str | None = None
    audio_content_sha256: str
    provider: str | None = None
    model: str | None = None
    voice: str | None = None
    voice_profile: FrozenJsonObject = Field(default_factory=FrozenJsonObject)
    quality: str | None = None
    license_status: str | None = None
    source_reference: str | None = None
    attribution: str | None = None

    @model_validator(mode="after")
    def validate_timing_and_hash(self) -> RuntimeNarrationTruth:
        if self.end_ms - self.start_ms != self.duration_ms:
            raise ValueError("Runtime narration duration must equal end_ms - start_ms")
        if not _SHA256.fullmatch(self.audio_content_sha256):
            raise ValueError("Runtime narration hash must be lowercase SHA-256")
        return self


class RuntimeSubtitleCueTruth(_FrozenModel):
    scene_index: int = Field(ge=1)
    cue_order: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def validate_timing(self) -> RuntimeSubtitleCueTruth:
        if self.end_ms <= self.start_ms:
            raise ValueError("Runtime subtitle cue must have positive duration")
        return self


class RuntimeSubtitleArtifactTruth(_FrozenModel):
    scene_index: int = Field(ge=1)
    artifact_kind: Literal["ASS"] = "ASS"
    content_sha256: str

    @model_validator(mode="after")
    def validate_hash(self) -> RuntimeSubtitleArtifactTruth:
        if not _SHA256.fullmatch(self.content_sha256):
            raise ValueError("Runtime subtitle artifact hash must be lowercase SHA-256")
        return self


class RuntimeSubtitleTruth(_FrozenModel):
    requested_mode: Literal["OFF", "STANDARD", "KARAOKE"]
    effective_mode: Literal["OFF", "STANDARD", "KARAOKE"]
    fallback_applied: bool
    fallback_reason: str | None = None
    timing_source: str
    semantics_version: int = Field(ge=1)
    burn_applied: bool
    style_applied: FrozenJsonObject | None = None
    cues: tuple[RuntimeSubtitleCueTruth, ...] = ()
    artifacts: tuple[RuntimeSubtitleArtifactTruth, ...] = ()

    @model_validator(mode="after")
    def validate_off_truth(self) -> RuntimeSubtitleTruth:
        if self.effective_mode == "OFF" and (
            self.cues
            or self.artifacts
            or self.burn_applied
            or self.style_applied is not None
            or self.timing_source != "NONE"
        ):
            raise ValueError("OFF runtime subtitle truth must be physically empty")
        if self.effective_mode != "OFF" and not self.burn_applied:
            raise ValueError("Enabled runtime subtitle truth must record a physical burn")
        return self


class RuntimeVisualTruth(_FrozenModel):
    scene_index: int = Field(ge=1)
    origin: Literal["EXPLICIT", "TEMPLATE", "PROVIDER"]
    visual_mode: str
    kind: str | None = None
    template_id: str | None = None
    provider: str | None = None
    provider_asset_id: str | None = None
    license_status: LicenseStatus
    source_url: str | None = None
    source_page_url: str | None = None
    license_name: str | None = None
    license_url: str | None = None
    attribution: str | None = None
    allowed_attribution_channels: tuple[AttributionDeliveryChannel, ...] = ()
    query: str | None = None
    storage_reference: str | None = None
    content_sha256: str | None = None
    mime_type: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    provider_metadata: FrozenJsonObject = Field(default_factory=FrozenJsonObject)

    @model_validator(mode="after")
    def validate_hash(self) -> RuntimeVisualTruth:
        if self.content_sha256 is not None and not _SHA256.fullmatch(
            self.content_sha256
        ):
            raise ValueError("Runtime visual hash must be lowercase SHA-256")
        if self.origin == "PROVIDER" and not self.provider:
            raise ValueError("Provider visual truth requires provider identity")
        if self.origin == "TEMPLATE" and any(
            (
                self.provider,
                self.provider_asset_id,
                self.source_url,
                self.source_page_url,
                self.license_name,
                self.license_url,
                self.attribution,
            )
        ):
            raise ValueError("Template visual truth cannot claim provider provenance")
        expected_channels = tuple(
            sorted(set(self.allowed_attribution_channels), key=str)
        )
        if self.allowed_attribution_channels != expected_channels:
            raise ValueError(
                "Runtime visual attribution channels must be unique and canonically ordered"
            )
        return self


class RuntimeBrandAssetTruth(_FrozenModel):
    role: Literal["CHANNEL_BUG", "INTRO", "OUTRO"]
    applied: bool
    reference: str | None = None
    content_sha256: str | None = None
    mime_type: str | None = None

    @model_validator(mode="after")
    def validate_applied_asset(self) -> RuntimeBrandAssetTruth:
        if self.applied and (
            not self.reference
            or not self.content_sha256
            or not _SHA256.fullmatch(self.content_sha256)
        ):
            raise ValueError("Applied brand assets require a safe reference and SHA-256")
        return self


class RuntimeBrandingTruth(_FrozenModel):
    policy_source: str
    assets: tuple[RuntimeBrandAssetTruth, ...] = ()


class RuntimeRenderTargetTruth(_FrozenModel):
    duration_ms: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float | None = Field(default=None, gt=0)
    fps_mode: str | None = None
    video_codec: str
    audio_codec: str | None = None
    has_audio: bool
    container: str
    file_size_bytes: int = Field(gt=0)
    content_sha256: str

    @model_validator(mode="after")
    def validate_hash(self) -> RuntimeRenderTargetTruth:
        if not _SHA256.fullmatch(self.content_sha256):
            raise ValueError("Runtime artifact hash must be lowercase SHA-256")
        return self


class RuntimeFingerprints(_FrozenModel):
    canonical_contract: str
    manifest_run: str
    subtitle_semantics_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_hashes(self) -> RuntimeFingerprints:
        if not _SHA256.fullmatch(self.canonical_contract):
            raise ValueError("Canonical contract fingerprint must be lowercase SHA-256")
        if not _SHA256.fullmatch(self.manifest_run):
            raise ValueError("Manifest run fingerprint must be lowercase SHA-256")
        return self


def _build_attribution_obligations(
    *,
    artifact_id: UUID,
    artifact_sha256: str,
    visuals: tuple[RuntimeVisualTruth, ...] | list[RuntimeVisualTruth],
) -> tuple[AttributionObligation, ...]:
    obligations = [
        create_attribution_obligation(
            artifact_id=artifact_id,
            artifact_sha256=artifact_sha256,
            scene_index=visual.scene_index,
            attribution_text=visual.attribution,
            provider=visual.provider,
            provider_asset_id=visual.provider_asset_id,
            visual_content_sha256=visual.content_sha256,
            template_id=visual.template_id,
            source_reference=visual.license_url or visual.source_page_url,
            allowed_channels=visual.allowed_attribution_channels,
        )
        for visual in visuals
        if visual.license_status == LicenseStatus.ATTRIBUTION_REQUIRED
        and visual.attribution
        and visual.attribution.strip()
    ]
    return canonicalize_attribution_obligations(obligations)


class ProductionRuntimeTruthSnapshot(_FrozenModel):
    schema_version: Literal[3] = RUNTIME_TRUTH_SCHEMA_VERSION
    lineage: RuntimeTruthLineage
    scenes: tuple[RuntimeSceneTruth, ...]
    narration: tuple[RuntimeNarrationTruth, ...]
    subtitles: RuntimeSubtitleTruth
    visuals: tuple[RuntimeVisualTruth, ...]
    branding: RuntimeBrandingTruth
    audio_mix: FrozenJsonObject
    render_target: RuntimeRenderTargetTruth
    probe: FrozenJsonObject
    artifact: FrozenJsonObject
    fingerprints: RuntimeFingerprints
    attribution_obligations: tuple[AttributionObligation, ...] = ()
    attribution_delivery_state: Literal[AttributionDeliveryState.UNKNOWN] = (
        AttributionDeliveryState.UNKNOWN
    )

    @model_validator(mode="after")
    def validate_runtime_coherence(self) -> ProductionRuntimeTruthSnapshot:
        if len(self.scenes) != len(self.visuals):
            raise ValueError("Every runtime scene must have one visual truth entry")
        expected_start = 0
        previous_sequence_index = 0
        for index, scene in enumerate(self.scenes):
            if scene.sequence_index <= previous_sequence_index:
                raise ValueError("Runtime scenes must use strictly increasing order")
            if scene.start_ms != expected_start:
                raise ValueError("Runtime scenes must form one contiguous timeline")
            if scene.visual_index != index:
                raise ValueError("Runtime scene visual linkage is invalid")
            visual = self.visuals[index]
            if visual.scene_index != scene.sequence_index:
                raise ValueError("Runtime scene and visual indices must match")
            expected_start = scene.end_ms
            previous_sequence_index = scene.sequence_index

        scene_by_index = {scene.sequence_index: scene for scene in self.scenes}
        for segment in self.narration:
            scene = scene_by_index.get(segment.scene_index)
            if scene is None or (
                segment.start_ms,
                segment.end_ms,
                segment.duration_ms,
            ) != (scene.start_ms, scene.end_ms, scene.duration_ms):
                raise ValueError("Runtime narration timing must match its rendered scene")

        previous_end = 0
        for cue in self.subtitles.cues:
            if cue.scene_index not in scene_by_index:
                raise ValueError("Runtime subtitle cue references an unknown scene")
            if not cue.text.strip():
                raise ValueError("Runtime subtitle cue text must be non-empty")
            if cue.start_ms < previous_end or cue.end_ms > expected_start:
                raise ValueError("Runtime subtitle cues must be ordered and in bounds")
            previous_end = cue.end_ms

        artifact_scenes: set[int] = set()
        for artifact in self.subtitles.artifacts:
            if artifact.scene_index not in scene_by_index:
                raise ValueError("Runtime subtitle artifact references an unknown scene")
            if artifact.scene_index in artifact_scenes:
                raise ValueError("Only one ASS artifact is allowed per runtime scene")
            artifact_scenes.add(artifact.scene_index)

        transition = (
            self.subtitles.requested_mode,
            self.subtitles.effective_mode,
        )
        if transition[0] != transition[1] and transition != ("KARAOKE", "STANDARD"):
            raise ValueError("Only KARAOKE to STANDARD fallback is permitted")
        if self.subtitles.fallback_applied != (transition[0] != transition[1]):
            raise ValueError("Subtitle fallback provenance is inconsistent")
        if self.subtitles.fallback_applied != bool(self.subtitles.fallback_reason):
            raise ValueError("Subtitle fallback reason provenance is inconsistent")
        if self.subtitles.effective_mode != "OFF" and (
            not self.subtitles.cues or not self.subtitles.artifacts
        ):
            raise ValueError("Enabled subtitles require rendered cues and artifacts")

        expected_obligations = _build_attribution_obligations(
            artifact_id=self.lineage.media_artifact_id,
            artifact_sha256=self.render_target.content_sha256,
            visuals=self.visuals,
        )
        if self.attribution_obligations != expected_obligations:
            raise ValueError(
                "Attribution obligations do not match rendered runtime visual truth"
            )
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def content_fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _read(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Expected runtime mapping, got {type(value).__name__}")


def sanitize_runtime_reference(value: Any) -> str | None:
    """Return a stable, credential-free runtime reference or fail closed."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme in ("http", "https"):
        if parsed.username or parsed.password:
            raise ValueError("Runtime reference must not contain URL credentials")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if parsed.scheme == "brand" and not parsed.query and not parsed.fragment:
        return raw
    lower = raw.lower()
    if any(marker in lower for marker in _SECRET_MARKERS):
        raise ValueError("Runtime reference contains a secret-bearing marker")
    if parsed.scheme:
        raise ValueError("Runtime reference uses an unsupported URI scheme")
    return raw.replace("\\", "/")


def _sanitize_json_metadata(value: Any) -> Any:
    """Remove secret-bearing keys and normalize non-finite probe values."""
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(marker in key_text.lower() for marker in _SECRET_MARKERS):
                continue
            clean[key_text] = _sanitize_json_metadata(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_metadata(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https"):
            if parsed.username or parsed.password:
                return None
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if any(marker in value.lower() for marker in _SECRET_MARKERS):
            return None
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TypeError(
        f"Runtime truth JSON cannot contain {type(value).__name__} objects"
    )


def read_attribution_foundation(
    payload: Mapping[str, Any],
) -> tuple[tuple[AttributionObligation, ...], AttributionDeliveryState]:
    """Read Phase-1 attribution fields without rewriting legacy payloads."""
    try:
        schema_version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Runtime truth schema_version is malformed") from exc
    if schema_version in (1, 2):
        return (), AttributionDeliveryState.UNKNOWN
    if schema_version != RUNTIME_TRUTH_SCHEMA_VERSION:
        raise ValueError("Unsupported runtime truth schema_version")
    if "attribution_obligations" not in payload:
        raise ValueError("Runtime truth attribution obligations are missing")
    if payload.get("attribution_delivery_state") != AttributionDeliveryState.UNKNOWN.value:
        raise ValueError("Runtime truth cannot claim attribution delivery success")
    raw_obligations = payload["attribution_obligations"]
    if not isinstance(raw_obligations, (list, tuple)):
        raise ValueError("Runtime truth attribution obligations must be an array")
    obligations = tuple(AttributionObligation.model_validate(item) for item in raw_obligations)
    if obligations != canonicalize_attribution_obligations(obligations):
        raise ValueError("Runtime truth attribution obligations are not canonical")
    try:
        artifact_id = UUID(str(payload["lineage"]["media_artifact_id"]))
        artifact_sha256 = str(payload["render_target"]["content_sha256"])
        visuals = tuple(RuntimeVisualTruth.model_validate(item) for item in payload["visuals"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Runtime truth attribution foundation is incomplete") from exc
    if obligations != _build_attribution_obligations(
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
        visuals=visuals,
    ):
        raise ValueError("Runtime truth attribution obligations do not match visuals")
    return obligations, AttributionDeliveryState.UNKNOWN


def build_production_runtime_truth_snapshot(
    *,
    contract: CanonicalProductionContract,
    render_plan_id: UUID,
    render_job_id: UUID,
    media_artifact_id: UUID,
    artifact_version: int,
    artifact_storage_uri: str,
    artifact_size_bytes: int,
    artifact_sha256: str,
    v2_result: Any,
    probe_summary: Mapping[str, Any],
) -> ProductionRuntimeTruthSnapshot:
    """Build runtime truth without I/O, rendering, provider calls, or replanning."""
    lineage = contract.lineage
    lineage_checks = (
        ("content_request_id", lineage.content_request_id),
        ("script_version_id", lineage.script_version_id),
        ("mission_execution_id", lineage.mission_execution_id),
        ("mission_id", lineage.mission_id),
    )
    for field_name, expected in lineage_checks:
        actual = _read(v2_result, field_name)
        if expected != actual:
            raise ValueError(f"V2 runtime {field_name} does not match canonical lineage")
    if lineage.render_job_id is not None and lineage.render_job_id != render_job_id:
        raise ValueError("Runtime render_job_id does not match canonical lineage")
    if _read(v2_result, "content_sha256") not in (None, artifact_sha256):
        raise ValueError("V2 runtime artifact hash does not match final artifact hash")

    result_scenes = tuple(_read(v2_result, "runtime_scenes", ()) or ())
    result_narration = tuple(
        _read(v2_result, "runtime_narration_segments", ()) or ()
    )
    result_cues = tuple(_read(v2_result, "runtime_subtitle_cues", ()) or ())
    result_subtitle_artifacts = tuple(
        _read(v2_result, "runtime_subtitle_artifacts", ()) or ()
    )

    visuals: list[RuntimeVisualTruth] = []
    scenes: list[RuntimeSceneTruth] = []
    for visual_index, raw_scene in enumerate(result_scenes):
        scene = _as_dict(raw_scene)
        origin = str(scene.get("visual_origin") or "TEMPLATE")
        visual = RuntimeVisualTruth(
            scene_index=scene["sequence_index"],
            origin=origin,
            visual_mode=str(scene.get("visual_mode") or "LOCAL_TEMPLATE_ONLY"),
            kind=scene.get("asset_kind"),
            template_id=scene.get("template_id"),
            provider=scene.get("asset_provider"),
            provider_asset_id=scene.get("asset_id"),
            license_status=scene["asset_license_status"],
            source_url=sanitize_runtime_reference(scene.get("asset_source_url")),
            source_page_url=sanitize_runtime_reference(scene.get("asset_source_page_url")),
            license_name=scene.get("asset_license_name"),
            license_url=sanitize_runtime_reference(scene.get("asset_license_url")),
            attribution=scene.get("asset_attribution"),
            allowed_attribution_channels=tuple(
                scene.get("asset_allowed_attribution_channels") or ()
            ),
            query=scene.get("asset_query"),
            storage_reference=sanitize_runtime_reference(scene.get("asset_storage_reference")),
            content_sha256=scene.get("visual_content_sha256"),
            mime_type=scene.get("visual_mime_type"),
            width=scene.get("visual_width"),
            height=scene.get("visual_height"),
            duration_ms=scene.get("visual_duration_ms"),
            provider_metadata=_sanitize_json_metadata(scene.get("asset_provider_metadata") or {}),
        )
        visuals.append(visual)
        scenes.append(
            RuntimeSceneTruth(
                sequence_index=scene["sequence_index"],
                source_section_id=scene.get("source_section_id"),
                source_statement_references=tuple(
                    scene.get("source_statement_references") or ()
                ),
                narration_text=scene.get("narration_text"),
                original_strategy=scene["original_strategy"],
                effective_strategy=scene["effective_strategy"],
                template_id=scene["template_id"],
                start_ms=scene["start_ms"],
                end_ms=scene["end_ms"],
                duration_ms=scene["duration_ms"],
                scene_content_sha256=scene["content_sha256"],
                visual_origin=origin,
                visual_index=visual_index,
            )
        )

    narration = tuple(
        RuntimeNarrationTruth(
            sequence_index=index,
            scene_index=data["scene_index"],
            text=str(data.get("text") or ""),
            start_ms=data["start_ms"],
            end_ms=data["end_ms"],
            duration_ms=data["duration_ms"],
            audio_asset_id=data.get("audio_asset_id"),
            storage_reference=sanitize_runtime_reference(data.get("storage_reference")),
            audio_content_sha256=data["audio_content_sha256"],
            provider=data.get("provider"),
            model=data.get("model"),
            voice=data.get("voice"),
            voice_profile=data.get("voice_profile") or {},
            quality=data.get("quality"),
            license_status=data.get("license_status"),
            source_reference=sanitize_runtime_reference(data.get("source_reference")),
            attribution=data.get("attribution"),
        )
        for index, data in enumerate((_as_dict(item) for item in result_narration), 1)
    )

    requested_mode = str(_read(v2_result, "requested_subtitle_mode", "STANDARD"))
    effective_mode = str(_read(v2_result, "effective_subtitle_mode", "STANDARD"))
    subtitle_truth = RuntimeSubtitleTruth(
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        fallback_applied=bool(
            _read(v2_result, "subtitle_fallback_applied", False)
        ),
        fallback_reason=_read(v2_result, "subtitle_fallback_reason"),
        timing_source=str(_read(v2_result, "subtitle_timing_source", "NONE")),
        semantics_version=int(_read(v2_result, "subtitle_semantics_version")),
        burn_applied=bool(_read(v2_result, "subtitle_burn_applied", False)),
        style_applied=(
            _as_dict(_read(v2_result, "subtitle_style_applied"))
            if _read(v2_result, "subtitle_style_applied") is not None
            else None
        ),
        cues=tuple(RuntimeSubtitleCueTruth(**_as_dict(item)) for item in result_cues),
        artifacts=tuple(
            RuntimeSubtitleArtifactTruth(**_as_dict(item))
            for item in result_subtitle_artifacts
        ),
    )

    probe = _sanitize_json_metadata(dict(probe_summary))
    width = int(probe.get("width") or _read(v2_result, "width"))
    height = int(probe.get("height") or _read(v2_result, "height"))
    duration_ms = int(probe.get("duration_ms") or 0)
    if duration_ms <= 0:
        raise ValueError("Final probe duration must be positive")
    video_codec = str(probe.get("video_codec") or contract.policy.video_codec)
    audio_codec = probe.get("audio_codec") or contract.policy.audio_codec
    fps = probe.get("fps") or _read(v2_result, "fps")

    runtime_branding = _read(v2_result, "runtime_branding", {}) or {}
    branding_data = _as_dict(runtime_branding)
    branding = RuntimeBrandingTruth(
        policy_source=str(
            branding_data.get("policy_source") or "PINNED_CHANNEL_DNA_REVISION"
        ),
        assets=tuple(
            RuntimeBrandAssetTruth(
                role=item["role"],
                applied=bool(item["applied"]),
                reference=sanitize_runtime_reference(item.get("reference")),
                content_sha256=item.get("content_sha256"),
                mime_type=item.get("mime_type"),
            )
            for item in branding_data.get("assets", ())
        ),
    )

    return ProductionRuntimeTruthSnapshot(
        lineage=RuntimeTruthLineage(
            channel_id=lineage.channel_id,
            production_request_id=lineage.production_request_id,
            content_request_id=lineage.content_request_id,
            script_version_id=lineage.script_version_id,
            channel_dna_revision_id=lineage.channel_dna_revision_id,
            render_plan_id=render_plan_id,
            render_job_id=render_job_id,
            media_artifact_id=media_artifact_id,
            artifact_version=artifact_version,
            mission_id=lineage.mission_id,
            mission_execution_id=lineage.mission_execution_id,
            task_id=lineage.task_id,
        ),
        scenes=tuple(scenes),
        narration=narration,
        subtitles=subtitle_truth,
        visuals=tuple(visuals),
        branding=branding,
        audio_mix=_sanitize_json_metadata(
            _read(v2_result, "runtime_audio_mix", {}) or {}
        ),
        render_target=RuntimeRenderTargetTruth(
            duration_ms=duration_ms,
            width=width,
            height=height,
            fps=float(fps) if fps is not None else None,
            fps_mode=_read(v2_result, "effective_fps_mode"),
            video_codec=video_codec,
            audio_codec=str(audio_codec) if audio_codec else None,
            has_audio=bool(probe.get("has_audio", False)),
            container=contract.policy.container_format,
            file_size_bytes=artifact_size_bytes,
            content_sha256=artifact_sha256,
        ),
        probe=probe,
        artifact={
            "artifact_id": str(media_artifact_id),
            "storage_uri": sanitize_runtime_reference(artifact_storage_uri),
            "mime_type": "video/mp4",
            "version": artifact_version,
        },
        fingerprints=RuntimeFingerprints(
            canonical_contract=contract.canonical_fingerprint(),
            manifest_run=str(_read(v2_result, "run_fingerprint")),
            subtitle_semantics_version=int(
                _read(v2_result, "subtitle_semantics_version")
            ),
        ),
        attribution_obligations=_build_attribution_obligations(
            artifact_id=media_artifact_id,
            artifact_sha256=artifact_sha256,
            visuals=visuals,
        ),
        attribution_delivery_state=AttributionDeliveryState.UNKNOWN,
    )


__all__ = [
    "ProductionRuntimeTruthSnapshot",
    "RUNTIME_TRUTH_SCHEMA_VERSION",
    "build_production_runtime_truth_snapshot",
    "read_attribution_foundation",
    "sanitize_runtime_reference",
]
