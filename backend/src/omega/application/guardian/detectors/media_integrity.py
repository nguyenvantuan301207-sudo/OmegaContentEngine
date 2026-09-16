"""Media Integrity Detector.

Wraps ProductionQAAdapter to evaluate media encoding, ffprobe metrics,
timeline alignment, and physical storage artifacts.
Failure policy is REQUIRE_REVIEW.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.guardian.adapters.production_qa_adapter import ProductionQAAdapter
from omega.application.guardian.detectors.base import BaseDetector, GuardianEvaluationContext
from omega.application.production_runtime_truth import ProductionRuntimeTruthSnapshot
from omega.domain.guardian import (
    DetectorFailurePolicy,
    GuardianCheckpoint,
    GuardianFindingData,
)
from omega.domain.production import AssetType
from omega.infrastructure.models import (
    MediaArtifact,
    ProductionRequest,
    ProductionScene,
    ScriptVersion,
)


def _runtime_truth_snapshot(
    payload: object,
    *,
    artifact_id: object | None,
    production_request_id: object | None,
    channel_id: object | None = None,
) -> ProductionRuntimeTruthSnapshot:
    snapshot = ProductionRuntimeTruthSnapshot.model_validate(payload)
    if artifact_id is not None and str(snapshot.lineage.media_artifact_id) != str(artifact_id):
        raise ValueError("Runtime truth does not belong to the requested media artifact")
    if (
        production_request_id is not None
        and str(snapshot.lineage.production_request_id) != str(production_request_id)
    ):
        raise ValueError("Runtime truth does not belong to the requested production request")
    if channel_id is not None and str(snapshot.lineage.channel_id) != str(channel_id):
        raise ValueError("Runtime truth does not belong to the production request channel")
    return snapshot


class MediaIntegrityDetector(BaseDetector):
    """Detects media encoding flaws, corruptions, and format mismatches."""

    detector_type = "MEDIA_INTEGRITY"
    detector_version = "1.0.0"
    supported_checkpoints = {GuardianCheckpoint.POST_RENDER}
    failure_policy = DetectorFailurePolicy.REQUIRE_REVIEW

    def __init__(self) -> None:
        self.adapter = ProductionQAAdapter()

    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        findings: list[GuardianFindingData] = []
        diag = context.diagnostic_context or {}

        # 1. Direct evaluation from diagnostic payload if provided
        if (
            context.media_artifact_id is None
            and "request_data" in diag
            and "script_version_data" in diag
        ):
            runtime_snapshot = None
            if diag.get("runtime_truth_snapshot") is not None:
                runtime_snapshot = _runtime_truth_snapshot(
                    diag["runtime_truth_snapshot"],
                    artifact_id=context.media_artifact_id,
                    production_request_id=context.production_request_id
                    or diag["request_data"].get("id"),
                    channel_id=diag["request_data"].get("channel_id"),
                )
            return self.adapter.evaluate(
                request_data=diag["request_data"],
                script_version_data=diag["script_version_data"],
                content_request_data=diag.get("content_request_data", {}),
                assets_data=diag.get("assets_data", []),
                requirements_data=diag.get("requirements_data", []),
                narration_segments=diag.get("narration_segments", []),
                subtitle_cues=diag.get("subtitle_cues", []),
                media_probe_summary=diag.get("media_probe_summary"),
                artifact_file_path=diag.get("artifact_file_path"),
                expected_hash=diag.get("expected_hash"),
                scenes_data=diag.get("scenes_data"),
                runtime_truth_snapshot=runtime_snapshot,
            )

        # 2. Database resolution via production_request_id or media_artifact_id
        target_prod_id = context.production_request_id
        async with session_factory() as session:
            if not target_prod_id and context.media_artifact_id:
                art_res = await session.execute(
                    select(MediaArtifact).where(MediaArtifact.id == context.media_artifact_id)
                )
                art = art_res.scalar_one_or_none()
                if art:
                    target_prod_id = art.production_request_id

            if not target_prod_id:
                if context.media_artifact_id is not None:
                    raise ValueError("Exact media artifact could not be resolved")
                return findings

            stmt = (
                select(ProductionRequest)
                .where(ProductionRequest.id == target_prod_id)
                .options(
                    selectinload(ProductionRequest.script_version).selectinload(
                        ScriptVersion.sections
                    ),
                    selectinload(ProductionRequest.scenes).selectinload(
                        ProductionScene.asset_requirements
                    ),
                    selectinload(ProductionRequest.assets),
                    selectinload(ProductionRequest.narration_segments),
                    selectinload(ProductionRequest.subtitle_cues),
                    selectinload(ProductionRequest.artifacts).selectinload(
                        MediaArtifact.runtime_truth
                    ),
                )
            )
            res = await session.execute(stmt)
            prod_req = res.scalar_one_or_none()
            if not prod_req:
                if context.media_artifact_id is not None:
                    raise ValueError(
                        "Production request for exact media artifact could not be resolved"
                    )
                return findings

            exact_artifact = None
            if context.media_artifact_id is not None:
                exact_artifact = next(
                    (
                        artifact
                        for artifact in prod_req.artifacts
                        if artifact.id == context.media_artifact_id
                    ),
                    None,
                )
                if exact_artifact is None and diag.get("runtime_truth_snapshot") is None:
                    raise ValueError("Exact media artifact could not be resolved")

            selected_artifact = exact_artifact
            if selected_artifact is None and context.media_artifact_id is None:
                selected_artifact = next(
                    (artifact for artifact in prod_req.artifacts if artifact.is_current),
                    None,
                )
                if selected_artifact is None and prod_req.artifacts:
                    selected_artifact = prod_req.artifacts[0]

            runtime_snapshot = None
            if diag.get("runtime_truth_snapshot") is not None:
                runtime_snapshot = _runtime_truth_snapshot(
                    diag["runtime_truth_snapshot"],
                    artifact_id=context.media_artifact_id,
                    production_request_id=prod_req.id,
                    channel_id=prod_req.channel_id,
                )
            elif selected_artifact is not None and selected_artifact.runtime_truth is not None:
                runtime_snapshot = _runtime_truth_snapshot(
                    selected_artifact.runtime_truth.payload,
                    artifact_id=selected_artifact.id,
                    production_request_id=prod_req.id,
                    channel_id=prod_req.channel_id,
                )

            req_data = {
                "id": str(prod_req.id),
                "script_version_id": str(prod_req.script_version_id),
                "channel_dna_revision_id": str(prod_req.channel_dna_revision_id),
                "target_width": prod_req.target_width,
                "target_height": prod_req.target_height,
                "video_codec": prod_req.video_codec,
            }
            script_data = {
                "id": str(prod_req.script_version_id),
                "hook_text": prod_req.script_version.hook_text if prod_req.script_version else None,
                "cta_text": prod_req.script_version.cta_text if prod_req.script_version else None,
                "closing_text": prod_req.script_version.closing_text if prod_req.script_version else None,
                "sections": [
                    {
                        "section_order": sec.section_order,
                        "heading": sec.heading,
                        "narration_text": sec.narration_text,
                    }
                    for sec in sorted(prod_req.script_version.sections, key=lambda s: s.section_order)
                ] if prod_req.script_version and getattr(prod_req.script_version, "sections", None) else []
            }
            content_req_data = {"channel_dna_revision_id": str(prod_req.channel_dna_revision_id)}

            assets_data = [
                {
                    "id": str(a.id),
                    "asset_type": a.asset_type,
                    "provider_type": a.provider_type,
                    "mime_type": a.mime_type,
                    "storage_uri": a.storage_uri,
                    "license_status": a.license_status,
                    "source_ref": a.source_ref,
                    "asset_requirement_id": str(a.asset_requirement_id)
                    if a.asset_requirement_id
                    else None,
                }
                for a in prod_req.assets
            ]
            reqs_data = [
                {
                    "id": str(r.id),
                    "scene_index": s.scene_order,
                    "purpose": r.purpose,
                    "required": r.required,
                }
                for s in prod_req.scenes
                for r in s.asset_requirements
            ]

            runtime_quality = diag.get("narration_quality")
            raw_refs = diag.get("narration_source_refs", [])
            if not isinstance(raw_refs, (list, tuple)):
                raw_refs = []

            if runtime_quality is not None or raw_refs:
                normalized_refs: list[str] = []
                for value in raw_refs:
                    ref = str(value).strip() if value is not None else ""
                    if ref and ref not in normalized_refs:
                        normalized_refs.append(ref)

                source_ref = " | ".join(normalized_refs) if normalized_refs else None

                audio_found = False
                for a in assets_data:
                    if a.get("asset_type") in ("AUDIO", AssetType.AUDIO.value):
                        audio_found = True
                        a["narration_quality"] = runtime_quality
                        a["source_ref"] = source_ref
                        a["narration_source_refs"] = normalized_refs

                if not audio_found:
                    assets_data.append({
                        "id": "runtime-narration",
                        "asset_type": "AUDIO",
                        "provider_type": None,
                        "mime_type": None,
                        "storage_uri": None,
                        "license_status": None,
                        "source_ref": source_ref,
                        "asset_requirement_id": None,
                        "narration_quality": runtime_quality,
                        "narration_source_refs": normalized_refs,
                    })

            narr_data = [
                {
                    "id": str(n.id),
                    "start_ms": n.start_ms,
                    "end_ms": n.end_ms,
                    "scene_id": str(n.scene_id),
                }
                for n in prod_req.narration_segments
            ]
            subs_data = [
                {
                    "cue_order": sc.cue_order,
                    "start_ms": sc.start_ms,
                    "end_ms": sc.end_ms,
                    "text": sc.text,
                }
                for sc in prod_req.subtitle_cues
            ]

            runtime_duration = diag.get("runtime_timeline_duration_ms")
            if runtime_duration is not None:
                narr_data = []
                for n in diag.get("runtime_narration_segments", []):
                    nd = dict(n)
                    narr_data.append({
                        "id": f"runtime-narration-{nd.get('scene_index')}",
                        "scene_index": nd.get("scene_index"),
                        "start_ms": int(nd.get("start_ms", 0)),
                        "end_ms": int(nd.get("end_ms", 0)),
                        "duration_ms": int(nd.get("duration_ms", 0)),
                    })
                subs_data = []
                for s in diag.get("runtime_subtitle_cues", []):
                    sd = dict(s)
                    subs_data.append({
                        "cue_order": int(sd.get("cue_order", 0)),
                        "scene_index": sd.get("scene_index"),
                        "start_ms": int(sd.get("start_ms", 0)),
                        "end_ms": int(sd.get("end_ms", 0)),
                        "text": str(sd.get("text", "")).strip(),
                    })

            runtime_scenes = diag.get("runtime_scenes")
            if runtime_scenes is not None:
                scenes_data = []
                for s in runtime_scenes:
                    sd = dict(s)
                    scenes_data.append({
                        "sequence_index": sd.get("sequence_index"),
                        "original_strategy": sd.get("original_strategy"),
                        "effective_strategy": sd.get("effective_strategy"),
                        "duration_seconds": sd.get("duration_seconds"),
                        "asset_kind": sd.get("asset_kind"),
                        "asset_provider": sd.get("asset_provider"),
                        "asset_id": sd.get("asset_id"),
                    })
            else:
                scenes_data = []
                for s in prod_req.scenes:
                    scenes_data.append({
                        "sequence_index": s.scene_order,
                        "original_strategy": s.scene_type,
                        "effective_strategy": s.scene_type,
                        "duration_seconds": (s.estimated_duration_ms / 1000.0) if s.estimated_duration_ms else 0.0,
                        "asset_kind": None,
                        "asset_provider": None,
                        "asset_id": None,
                    })

            probe_summary = diag.get("media_probe_summary")
            artifact_path = diag.get("artifact_file_path")
            expected_hash = diag.get("expected_hash")

            if probe_summary is None and selected_artifact:
                probe_summary = {
                    "width": selected_artifact.width,
                    "height": selected_artifact.height,
                    "duration_ms": selected_artifact.duration_ms,
                    "video_codec": prod_req.video_codec,
                    "has_audio": True,
                }
            if artifact_path is None and selected_artifact:
                artifact_path = selected_artifact.storage_uri
            if expected_hash is None and selected_artifact:
                expected_hash = selected_artifact.content_hash

            return self.adapter.evaluate(
                request_data=req_data,
                script_version_data=script_data,
                content_request_data=content_req_data,
                assets_data=assets_data,
                requirements_data=reqs_data,
                narration_segments=narr_data,
                subtitle_cues=subs_data,
                media_probe_summary=probe_summary,
                artifact_file_path=artifact_path,
                expected_hash=expected_hash,
                scenes_data=scenes_data,
                runtime_truth_snapshot=runtime_snapshot,
            )
