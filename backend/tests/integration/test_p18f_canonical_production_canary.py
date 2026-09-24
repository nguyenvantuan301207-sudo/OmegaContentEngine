"""P18-F canonical physical production canary.

This intentionally exercises the real production, V2/Chromium/FFmpeg, QA,
Guardian, RuntimeTruth, and attribution-sidecar paths.  Only uncontrolled
network providers are replaced with deterministic local implementations.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import socket
import struct
import wave
import zlib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.api.production import (
    export_artifact_attribution_sidecar,
    get_artifact_qa_result,
    get_artifact_runtime_truth,
)
from omega.application.guardian.detectors.base import (
    BaseDetector,
    GuardianEvaluationContext,
)
from omega.application.guardian.detectors.media_integrity import MediaIntegrityDetector
from omega.application.guardian.engine import GuardianEngine as RealGuardianEngine
from omega.application.media_probe import MediaProbe
from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.application.production_runtime_truth import (
    RUNTIME_TRUTH_SCHEMA_VERSION,
    ProductionRuntimeTruthSnapshot,
    RuntimeBeatVisualTruth,
    read_attribution_foundation,
)
from omega.application.production_service import ProductionService
from omega.application.render_service import ProductionRenderService
from omega.application.visual_asset_engine import (
    ResolvedVisualAsset,
    VisualAssetCandidate,
    VisualAssetEngine,
    VisualAssetRequest,
)
from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator
from omega.application.visual_production_v2_service import VisualProductionV2Service
from omega.domain.attribution_sidecar import parse_attribution_sidecar
from omega.domain.attribution_delivery import AttributionDeliveryChannel
from omega.domain.guardian import (
    CheckTriggerType,
    DetectorFailurePolicy,
    GuardianAction,
    GuardianCheckCreate,
    GuardianCheckpoint,
    GuardianFindingData,
    GuardianRiskType,
    GuardianSeverity,
)
from omega.domain.production import (
    LicenseStatus,
    ProductionQARuleCode,
    ProductionQAStatus,
    ProductionRequestCreate,
)
from omega.infrastructure.models import (
    AssetRequirement,
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    GuardianCheck,
    MediaArtifact,
    Mission,
    MissionExecution,
    ProductionQAResult,
    ProductionRequest,
    ProductionRuntimeTruth,
    ProductionScene,
    ResearchBrief,
    ResearchRequest,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
    TopicCandidate,
)
from omega.infrastructure.database import AsyncSessionLocal


ATTRIBUTION_TEXT = "P18-F deterministic canary image by OMEGA QA"
PROVIDER_NAME = "p18f-local-provider"
PROVIDER_ASSET_ID = "p18f-attributed-image-v1"
MIDDLE_STATEMENT = (
    "Careful makers combine clear evidence with practical examples so every viewer "
    "can understand useful lessons without confusion or needless jargon today."
)


def _write_rgb_png(path: Path, *, width: int = 1920, height: int = 1080) -> None:
    """Write a deterministic, dependency-free RGB PNG with real dimensions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    row = b"\x00" + (b"\x14\x5a\x96" * width)
    raw = row * height
    path.write_bytes(
        signature
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, level=9))
        + chunk(b"IEND", b"")
    )


def _write_audible_wav(path: Path, *, duration_ms: int = 900) -> None:
    """Write deterministic mono PCM containing an audible two-tone signal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16_000
    frame_count = round(sample_rate * duration_ms / 1000)
    frames = bytearray()
    for index in range(frame_count):
        t = index / sample_rate
        sample = int(
            7_000 * math.sin(2 * math.pi * 440 * t)
            + 2_000 * math.sin(2 * math.pi * 660 * t)
        )
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)


class _DeterministicNarrationProvider:
    model = "p18f-local-audible-v1"
    default_voice = "p18f-fixed-voice"

    def __init__(self, storage: LocalMediaStorageProvider) -> None:
        self.storage = storage
        self.calls = 0

    async def synthesize_segment_audio(
        self,
        channel_id: UUID,
        request_id: UUID,
        segment: dict[str, Any],
        voice_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del voice_profile
        self.calls += 1
        text_value = " ".join(str(segment["text"]).split())
        identity = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
        target = self.storage.get_narration_dir(channel_id, request_id) / f"{identity}.wav"
        if not target.exists():
            _write_audible_wav(target)
        return {
            "id": f"p18f-audio-{identity[:16]}",
            "storage_uri": self.storage.to_relative_uri(channel_id, request_id, target),
            "duration_ms": 900,
            "content_hash": compute_sha256(target),
            "source_ref": "p18f-deterministic-local-audio",
            "narration_quality": "NEURAL_PRODUCTION",
            "license_status": LicenseStatus.GENERATED.value,
            "voice": self.default_voice,
        }


class _DeterministicVisualProvider:
    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path
        self.searches = 0
        self.fetches = 0

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

    async def search(
        self,
        request: VisualAssetRequest,
        limit: int = 5,
    ) -> list[VisualAssetCandidate]:
        del limit
        self.searches += 1
        return [
            VisualAssetCandidate(
                provider_id=PROVIDER_ASSET_ID,
                kind=request.kind,
                provider=PROVIDER_NAME,
                source_url="https://canary.invalid/assets/p18f-attributed-image.png",
                source_page_url="https://canary.invalid/p18f-attribution",
                mime_type="image/png",
                width=1920,
                height=1080,
                duration_seconds=None,
                license_status=LicenseStatus.ATTRIBUTION_REQUIRED,
                license_name="P18-F deterministic attribution license",
                license_url="https://canary.invalid/licenses/p18f",
                attribution_text=ATTRIBUTION_TEXT,
                metadata={"fixture": "p18f", "network_fetched": False},
            )
        ]

    async def fetch(self, candidate: VisualAssetCandidate) -> ResolvedVisualAsset:
        self.fetches += 1
        assert candidate.provider_id == PROVIDER_ASSET_ID
        return ResolvedVisualAsset(
            asset_id=candidate.provider_id,
            kind=candidate.kind,
            provider=PROVIDER_NAME,
            source_url=candidate.source_url,
            source_page_url=candidate.source_page_url,
            local_path=self.image_path,
            mime_type="image/png",
            width=1920,
            height=1080,
            duration_seconds=None,
            content_sha256=compute_sha256(self.image_path),
            license_status=LicenseStatus.ATTRIBUTION_REQUIRED,
            license_name=candidate.license_name,
            license_url=candidate.license_url,
            attribution_text=ATTRIBUTION_TEXT,
            allowed_attribution_channels=(
                AttributionDeliveryChannel.EXPORT_SIDECAR,
            ),
            query="p18f deterministic attributed production image",
            metadata={"fixture": "p18f", "network_fetched": False},
        )


class _BlockPhysicalV3Detector(BaseDetector):
    """Real Guardian detector that blocks only the rendered v3 candidate."""

    detector_type = "P18F_BLOCK_PHYSICAL_V3"
    detector_version = "1.0.0"
    supported_checkpoints = {GuardianCheckpoint.POST_RENDER}
    failure_policy = DetectorFailurePolicy.FAIL_CLOSED

    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        del session_factory
        artifact_path = str(context.diagnostic_context.get("artifact_file_path") or "")
        if "video_v3_" not in Path(artifact_path).name:
            return []
        assert Path(artifact_path).is_file(), "v3 must be physically rendered before Guardian blocks"
        return [
            GuardianFindingData(
                rule_id="P18F_DETERMINISTIC_V3_BLOCK",
                severity=GuardianSeverity.HIGH,
                risk_type=GuardianRiskType.MEDIA_CORRUPTION,
                confidence=1.0,
                evidence={
                    "artifact_id": str(context.media_artifact_id),
                    "physical_candidate": True,
                    "version": 3,
                },
                location_reference={"artifact_file": Path(artifact_path).name},
                message="P18-F deterministically blocks the real physical v3 candidate.",
            )
        ]


def _install_network_guard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Deny uncontrolled network while allowing the isolated test PostgreSQL host."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    db_host = urlsplit(os.environ["DATABASE_URL"]).hostname
    if db_host:
        allowed_hosts.add(db_host.lower())
        for item in socket.getaddrinfo(db_host, None):
            allowed_hosts.add(str(item[4][0]).lower())
    blocked: list[str] = []

    def is_allowed(address: object) -> bool:
        if isinstance(address, str):
            return True  # Unix-domain socket or Windows named-pipe equivalent.
        if not isinstance(address, tuple) or not address:
            return False
        host = str(address[0]).strip("[]").lower()
        if host in allowed_hosts:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def guarded_connect(sock: socket.socket, address: object):
        if not is_allowed(address):
            blocked.append(repr(address))
            raise AssertionError(f"P18-F blocked uncontrolled network connection: {address!r}")
        return original_connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: object):
        if not is_allowed(address):
            blocked.append(repr(address))
            raise AssertionError(f"P18-F blocked uncontrolled network connection: {address!r}")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    return blocked


async def _seed_lineage(db_session: AsyncSession) -> dict[str, Any]:
    channel = Channel(
        id=uuid4(),
        name="P18-F Canonical Canary",
        slug=f"p18f-{uuid4().hex[:12]}",
        state="ACTIVE",
        platform="YOUTUBE",
        dna={},
        metadata_={"visual_asset_mode": "PEXELS", "narration_provider": "NEURAL"},
    )
    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=channel.id,
        version=1,
        snapshot={},
        change_reason="P18-F deterministic canary",
        actor="SYSTEM",
    )
    topic = TopicCandidate(
        id=uuid4(),
        channel_id=channel.id,
        title="Canonical production authority",
        normalized_title="canonical production authority",
        summary="P18-F deterministic canary",
        source_type="MANUAL",
        source_name="p18f",
        topic_fingerprint=hashlib.sha256(b"p18f-topic").hexdigest(),
        status="SELECTED",
    )
    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="P18-F Mission",
        objective="Prove canonical physical production authority",
        state="RUNNING",
        autonomy_level="SUPERVISED",
        guardian_epoch=1,
    )
    mission_execution = MissionExecution(
        id=uuid4(),
        mission_id=mission.id,
        channel_dna_revision_id=dna.id,
        state="RUNNING",
        trigger_type="MANUAL",
    )
    db_session.add_all([channel, dna, topic, mission, mission_execution])
    await db_session.flush()

    research_request = ResearchRequest(
        id=uuid4(),
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        mode="INTERACTIVE",
        status="COMPLETED",
        outcome="SUFFICIENT",
    )
    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=research_request.id,
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        version=1,
        is_current=True,
        outcome="SUFFICIENT",
        overall_confidence=100.0,
        title="P18-F canonical production brief",
        summary="Deterministic local canary evidence.",
    )
    db_session.add_all([research_request, brief])
    await db_session.flush()

    content_requests: dict[str, ContentGenerationRequest] = {}
    scripts: dict[str, ScriptVersion] = {}
    statements = (
        "A concise opening establishes the canonical production contract clearly.",
        MIDDLE_STATEMENT,
        "A concise closing confirms exact artifact authority and attribution delivery.",
    )
    headings = ("Opening", "Evidence", "Outro")
    for role, mission_id in (
        ("interactive", None),
        ("mission", mission_execution.id),
    ):
        content_request = ContentGenerationRequest(
            id=uuid4(),
            channel_id=channel.id,
            topic_candidate_id=topic.id,
            research_brief_id=brief.id,
            channel_dna_revision_id=dna.id,
            mission_execution_id=mission_id,
            idempotency_key=f"p18f-content-{role}-{uuid4().hex}",
            mode="MISSION_EXECUTION" if mission_id else "INTERACTIVE",
            status="SUCCEEDED",
            outcome="GENERATED",
            content_type="YOUTUBE_LONGFORM",
            target_duration_seconds=3,
            target_word_count=50,
            language="en",
            region="US",
        )
        script = ScriptVersion(
            id=uuid4(),
            content_request_id=content_request.id,
            version=1,
            is_current=True,
            title="P18-F Canonical Production Canary",
            hook_text=statements[0],
            closing_text=statements[2],
            cta_text="Verify the exact artifact evidence.",
            estimated_word_count=sum(len(item.split()) for item in statements),
            estimated_duration_seconds=3,
            qa_status="PASSED",
            style_snapshot={},
        )
        db_session.add_all([content_request, script])
        await db_session.flush()
        for index, (heading, statement_text) in enumerate(
            zip(headings, statements, strict=True), start=1
        ):
            section = ScriptSection(
                id=uuid4(),
                script_version_id=script.id,
                section_order=index,
                heading=heading,
                narration_text=statement_text,
                estimated_duration_seconds=1,
            )
            db_session.add(section)
            await db_session.flush()
            db_session.add(
                ScriptStatement(
                    id=uuid4(),
                    script_section_id=section.id,
                    statement_order=1,
                    statement_text=statement_text,
                    statement_type="NARRATIVE",
                )
            )
        content_requests[role] = content_request
        scripts[role] = script
    await db_session.commit()
    return {
        "channel": channel,
        "dna": dna,
        "mission": mission,
        "mission_execution": mission_execution,
        "content_requests": content_requests,
        "scripts": scripts,
    }


async def _create_prepared_request(
    db_session: AsyncSession,
    *,
    channel_id: UUID,
    script_id: UUID,
    mission_execution_id: UUID | None,
) -> ProductionRequest:
    service = ProductionService()
    request = await service.create_production_request(
        db_session,
        channel_id,
        ProductionRequestCreate(
            script_version_id=script_id,
            target_width=1920,
            target_height=1080,
            fps=15,
            video_codec="h264",
            audio_codec="aac",
            container_format="mp4",
            mission_execution_id=mission_execution_id,
            metadata={
                "render_settings": {
                    "visual_asset_mode": "PEXELS",
                    "narration_provider": "NEURAL",
                    "subtitle_mode": "STANDARD",
                    "channel_bug_enabled": False,
                    "intro_enabled": False,
                    "outro_enabled": False,
                }
            },
        ),
        idempotency_key=f"p18f-production-{uuid4().hex}",
    )
    return await service.prepare_production(db_session, channel_id, request.id)


async def _render_version(
    db_session: AsyncSession,
    production_service: ProductionService,
    render_service: ProductionRenderService,
    request: ProductionRequest,
    *,
    role: str,
    rerender: bool,
) -> tuple[MediaArtifact, ProductionQAStatus]:
    job, plan, is_new = await production_service.allocate_render_job(
        db_session,
        request.channel_id,
        request.id,
        idempotency_key=f"p18f-{role}-{uuid4().hex}",
        is_rerender=rerender,
    )
    assert is_new is True
    expected_version = int(role.removeprefix("v")) if role.startswith("v") else 1
    assert plan.version == expected_version
    artifact, status = await render_service.execute_render_job(
        db_session,
        request.channel_id,
        request.id,
        job.id,
    )
    assert artifact is not None
    assert artifact.version == expected_version
    return artifact, status


def _normalized_semantics(
    snapshot: ProductionRuntimeTruthSnapshot,
    qa_status: str,
) -> dict[str, Any]:
    return {
        "scenes": [
            {
                "sequence": item.sequence_index,
                "original": item.original_strategy,
                "effective": item.effective_strategy,
                "template": item.template_id,
                "duration_ms": item.duration_ms,
                "narration": item.narration_text,
                "visual_origin": item.visual_origin,
            }
            for item in snapshot.scenes
        ],
        "visuals": [
            {
                "scene": item.parent_scene_index,
                "beat": item.materialized_beat_index,
                "source_beat": item.source_editorial_beat_index,
                "origin": item.visual_origin,
                "kind": item.asset_kind,
                "template": item.template_id,
                "provider": item.provider,
                "provider_asset": item.provider_asset_id,
                "license": item.license_status.value,
                "attribution": item.attribution,
                "provider_asset_sha256": item.provider_asset_content_sha256,
                "rendered_beat_sha256": item.rendered_beat_clip_sha256,
            }
            for item in snapshot.visual_beats
        ],
        "narration": [
            {
                "scene": item.scene_index,
                "text": item.text,
                "duration_ms": item.duration_ms,
                "provider": item.provider,
                    "model": item.model,
                    "voice": item.voice,
                    "quality": item.quality,
                    "license": (
                        item.license_status.value
                        if hasattr(item.license_status, "value")
                        else item.license_status
                    ),
                    "source": item.source_reference,
            }
            for item in snapshot.narration
        ],
        "subtitles": {
            "requested": snapshot.subtitles.requested_mode,
            "effective": snapshot.subtitles.effective_mode,
            "timing": snapshot.subtitles.timing_source,
                "burned": snapshot.subtitles.burn_applied,
                "cues": [
                    (
                        item.scene_index,
                        item.end_ms - item.start_ms,
                        item.text,
                    )
                    for item in snapshot.subtitles.cues
                ],
            "artifacts": [
                (item.scene_index, item.content_sha256)
                for item in snapshot.subtitles.artifacts
            ],
        },
        "render_target": {
            "duration_ms": snapshot.render_target.duration_ms,
            "width": snapshot.render_target.width,
            "height": snapshot.render_target.height,
            "fps": snapshot.render_target.fps,
            "fps_mode": snapshot.render_target.fps_mode,
            "video_codec": snapshot.render_target.video_codec,
            "audio_codec": snapshot.render_target.audio_codec,
            "has_audio": snapshot.render_target.has_audio,
            "container": snapshot.render_target.container,
        },
        "attribution": [
            {
                "scene": item.scene_index,
                "provider": item.provider,
                "provider_asset": item.provider_asset_id,
                "visual_sha256": item.visual_content_sha256,
                "text": item.attribution_text,
                "channels": tuple(channel.value for channel in item.allowed_channels),
            }
            for item in snapshot.attribution_obligations
        ],
        "qa_authority": qa_status,
        "combined_authority": "ACCEPTED",
    }


async def _exact_authority(
    db_session: AsyncSession,
    *,
    channel_id: UUID,
    request_id: UUID,
    artifact: MediaArtifact,
    require_guardian: bool,
) -> tuple[ProductionRuntimeTruthSnapshot, ProductionQAResult, GuardianCheck | None]:
    runtime_response = await get_artifact_runtime_truth(
        channel_id, request_id, artifact.id, db_session
    )
    qa = await get_artifact_qa_result(channel_id, request_id, artifact.id, db_session)
    snapshot = ProductionRuntimeTruthSnapshot.model_validate(
        runtime_response.runtime_snapshot
    )
    assert runtime_response.artifact_id == artifact.id
    assert runtime_response.render_version == artifact.version
    assert snapshot.schema_version == RUNTIME_TRUTH_SCHEMA_VERSION
    assert snapshot.lineage.media_artifact_id == artifact.id
    assert snapshot.lineage.production_request_id == request_id
    assert snapshot.lineage.channel_id == channel_id
    assert snapshot.lineage.artifact_version == artifact.version
    assert qa.artifact_id == artifact.id
    assert qa.production_request_id == request_id

    check = (
        await db_session.execute(
            select(GuardianCheck)
            .where(
                GuardianCheck.media_artifact_id == artifact.id,
                GuardianCheck.production_request_id == request_id,
                GuardianCheck.checkpoint == GuardianCheckpoint.POST_RENDER.value,
            )
            .options(selectinload(GuardianCheck.decision))
        )
    ).scalar_one_or_none()
    if require_guardian:
        assert check is not None
        assert check.media_artifact_id == artifact.id
        assert check.production_request_id == request_id
        assert check.decision is not None
    else:
        assert check is None
    return snapshot, qa, check


async def _physical_evidence(
    storage: LocalMediaStorageProvider,
    probe: MediaProbe,
    *,
    channel_id: UUID,
    request_id: UUID,
    artifact: MediaArtifact,
    snapshot: ProductionRuntimeTruthSnapshot,
) -> dict[str, Any]:
    path = storage.resolve_artifact_path(channel_id, request_id, artifact.storage_uri)
    assert path.is_file() and path.stat().st_size > 0
    independent_probe = await probe.probe_file(path)
    independent_hash = compute_sha256(path)
    assert independent_hash == artifact.content_hash
    assert independent_hash == snapshot.render_target.content_sha256
    assert independent_probe["width"] == artifact.width == snapshot.render_target.width == 1920
    assert independent_probe["height"] == artifact.height == snapshot.render_target.height == 1080
    assert independent_probe["duration_ms"] > 0
    assert abs(independent_probe["duration_ms"] - artifact.duration_ms) <= 100
    assert abs(independent_probe["duration_ms"] - snapshot.render_target.duration_ms) <= 100
    assert str(independent_probe["video_codec"]).lower() in {"h264", "avc1", "libx264"}
    assert independent_probe["has_audio"] is True
    return {
        "version": artifact.version,
        "artifact_id": str(artifact.id),
        "path": path.name,
        "width": independent_probe["width"],
        "height": independent_probe["height"],
        "duration_ms": independent_probe["duration_ms"],
        "video_codec": independent_probe["video_codec"],
        "audio_codec": independent_probe.get("audio_codec"),
        "has_audio": independent_probe["has_audio"],
        "sha256": independent_hash,
        "current": artifact.is_current,
        "runtime_truth_schema": snapshot.schema_version,
    }


@pytest.mark.asyncio
async def test_post_render_guardian_borrows_candidate_transaction(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """Guardian sees flushed artifacts and leaves commit/rollback to its caller."""
    del tmp_path
    lineage = await _seed_lineage(db_session)
    channel: Channel = lineage["channel"]
    mission: Mission = lineage["mission"]
    request = await _create_prepared_request(
        db_session,
        channel_id=channel.id,
        script_id=lineage["scripts"]["mission"].id,
        mission_execution_id=lineage["mission_execution"].id,
    )
    engine = RealGuardianEngine(
        session_factory=AsyncSessionLocal,
        detectors=[_BlockPhysicalV3Detector()],
    )

    async def persist_candidate(version: int) -> tuple[UUID, UUID]:
        artifact_id = uuid4()
        artifact = MediaArtifact(
            id=artifact_id,
            production_request_id=request.id,
            render_job_id=None,
            artifact_type="VIDEO",
            version=version,
            is_current=False,
            storage_uri=f"artifacts/focused-v{version}.mp4",
            content_hash=hashlib.sha256(f"focused-v{version}".encode()).hexdigest(),
            file_size_bytes=1,
            mime_type="video/mp4",
            width=1920,
            height=1080,
            duration_ms=1000,
        )
        db_session.add(artifact)
        await db_session.flush()
        response = await engine.execute_check(
            GuardianCheckCreate(
                mission_id=mission.id,
                production_request_id=request.id,
                media_artifact_id=artifact_id,
                checkpoint=GuardianCheckpoint.POST_RENDER,
                trigger_type=CheckTriggerType.POST_RENDER,
                diagnostic_context={"artifact_id": str(artifact_id)},
            ),
            session=db_session,
        )
        assert response.media_artifact_id == artifact_id
        assert response.decision is not None
        assert response.decision.action == GuardianAction.ALLOW
        return artifact_id, response.id

    committed_artifact_id, committed_check_id = await persist_candidate(1)
    async with AsyncSessionLocal() as observer:
        assert await observer.get(MediaArtifact, committed_artifact_id) is None
        assert await observer.get(GuardianCheck, committed_check_id) is None

    await db_session.commit()
    async with AsyncSessionLocal() as observer:
        committed_artifact = await observer.get(MediaArtifact, committed_artifact_id)
        committed_check = await observer.get(GuardianCheck, committed_check_id)
        assert committed_artifact is not None
        assert committed_check is not None
        assert committed_check.media_artifact_id == committed_artifact.id

    rolled_back_artifact_id, rolled_back_check_id = await persist_candidate(2)
    await db_session.rollback()
    async with AsyncSessionLocal() as observer:
        assert await observer.get(MediaArtifact, rolled_back_artifact_id) is None
        assert await observer.get(GuardianCheck, rolled_back_check_id) is None


@pytest.mark.asyncio
async def test_guardian_qa_preserves_persisted_requirement_scene_identity(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guardian QA matches requirements to the persisted scene order."""
    lineage = await _seed_lineage(db_session)
    channel: Channel = lineage["channel"]
    request = await _create_prepared_request(
        db_session,
        channel_id=channel.id,
        script_id=lineage["scripts"]["mission"].id,
        mission_execution_id=lineage["mission_execution"].id,
    )
    loaded_request = (
        await db_session.execute(
            select(ProductionRequest)
            .where(ProductionRequest.id == request.id)
            .options(
                selectinload(ProductionRequest.scenes).selectinload(
                    ProductionScene.asset_requirements
                )
            )
        )
    ).scalar_one()
    required_scene = next(
        scene for scene in loaded_request.scenes if scene.scene_order == 1
    )
    for scene in loaded_request.scenes:
        for candidate in scene.asset_requirements:
            candidate.required = False
    requirement = AssetRequirement(
        id=uuid4(),
        scene_id=required_scene.id,
        asset_type="IMAGE",
        purpose="P18-F scene identity regression",
        query_hint="deterministic scene identity",
        required=True,
        status="PENDING",
        license_requirement="COMMERCIAL_ALLOWED",
    )
    db_session.add(requirement)
    await db_session.commit()

    assert required_scene.scene_order == 1
    assert requirement.scene_id == required_scene.id

    def runtime_snapshot(scene_index: int) -> SimpleNamespace:
        return SimpleNamespace(
            subtitles=SimpleNamespace(
                effective_mode="OFF",
                burn_applied=False,
                cues=(),
                artifacts=(),
            ),
            scenes=(
                SimpleNamespace(
                    sequence_index=scene_index,
                    duration_ms=1000,
                    scene_content_sha256="a" * 64,
                    template_id="kinetic_text",
                ),
            ),
            visual_beats=(
                RuntimeBeatVisualTruth(
                    parent_scene_index=scene_index,
                    materialized_beat_index=0,
                    source_editorial_beat_index=0,
                    semantic_role="PRIMARY",
                    start_offset_ms=0,
                    end_offset_ms=1000,
                    duration_ms=1000,
                    template_id="kinetic_text",
                    camera_motion_intent="STATIC",
                    transition_intent="CUT",
                    asset_action="GENERATE_TEMPLATE",
                    visual_origin="TEMPLATE",
                    license_status=LicenseStatus.GENERATED,
                    rendered_beat_clip_sha256="b" * 64,
                ),
            ),
        )

    selected_snapshot = runtime_snapshot(1)
    monkeypatch.setattr(
        "omega.application.guardian.detectors.media_integrity._runtime_truth_snapshot",
        lambda *args, **kwargs: selected_snapshot,
    )
    detector = MediaIntegrityDetector()
    context = GuardianEvaluationContext(
        mission_id=lineage["mission"].id,
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=request.id,
        diagnostic_context={
            "runtime_truth_snapshot": {"schema_version": RUNTIME_TRUTH_SCHEMA_VERSION}
        },
    )

    matching_findings = await detector.evaluate(context, AsyncSessionLocal)
    assert ProductionQARuleCode.MISSING_REQUIRED_ASSET.value not in {
        finding.rule_id for finding in matching_findings
    }

    selected_snapshot = runtime_snapshot(2)
    mismatched_findings = await detector.evaluate(context, AsyncSessionLocal)
    assert ProductionQARuleCode.MISSING_REQUIRED_ASSET.value in {
        finding.rule_id for finding in mismatched_findings
    }


@pytest.mark.asyncio
async def test_p18f_canonical_production_canary(
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove all ten P18-F requirements in one physical isolated canary."""
    assert RUNTIME_TRUTH_SCHEMA_VERSION == 4

    blocked_network = _install_network_guard(monkeypatch)
    lineage = await _seed_lineage(db_session)
    channel: Channel = lineage["channel"]
    mission_execution: MissionExecution = lineage["mission_execution"]

    storage = LocalMediaStorageProvider(base_root=str(tmp_path / "media"))
    image_path = tmp_path / "providers" / "p18f-attributed.png"
    _write_rgb_png(image_path)
    visual_provider = _DeterministicVisualProvider(image_path)
    narration_provider = _DeterministicNarrationProvider(storage)
    orchestrator = VisualAssetOrchestrator(VisualAssetEngine(), [visual_provider])
    v2 = VisualProductionV2Service(
        asset_orchestrator=orchestrator,
        output_root=tmp_path / "physical-cache",
        narration_provider=narration_provider,
        narration_storage=storage,
        visual_asset_mode="PEXELS",
    )
    render_service = ProductionRenderService(
        storage=storage,
        visual_production_service=v2,
    )
    production_service = ProductionService(storage=storage)
    independent_probe = MediaProbe()

    interactive = await _create_prepared_request(
        db_session,
        channel_id=channel.id,
        script_id=lineage["scripts"]["interactive"].id,
        mission_execution_id=None,
    )
    mission = await _create_prepared_request(
        db_session,
        channel_id=channel.id,
        script_id=lineage["scripts"]["mission"].id,
        mission_execution_id=mission_execution.id,
    )

    real_engine_factory = lambda session_factory: RealGuardianEngine(
        session_factory=session_factory,
        detectors=[MediaIntegrityDetector(), _BlockPhysicalV3Detector()],
    )
    monkeypatch.setattr(
        "omega.application.guardian.engine.GuardianEngine",
        real_engine_factory,
    )

    assert not (tmp_path / "physical-cache" / str(interactive.id)).exists()
    interactive_v1, interactive_status = await _render_version(
        db_session,
        production_service,
        render_service,
        interactive,
        role="interactive-v1",
        rerender=False,
    )
    assert interactive_status in {
        ProductionQAStatus.PASSED,
        ProductionQAStatus.PASSED_WITH_WARNINGS,
    }
    interactive_snapshot, interactive_qa, _ = await _exact_authority(
        db_session,
        channel_id=channel.id,
        request_id=interactive.id,
        artifact=interactive_v1,
        require_guardian=False,
    )
    interactive_physical = await _physical_evidence(
        storage,
        independent_probe,
        channel_id=channel.id,
        request_id=interactive.id,
        artifact=interactive_v1,
        snapshot=interactive_snapshot,
    )

    assert not (tmp_path / "physical-cache" / str(mission.id)).exists()
    mission_v1, mission_v1_status = await _render_version(
        db_session,
        production_service,
        render_service,
        mission,
        role="v1",
        rerender=False,
    )
    assert mission_v1_status in {
        ProductionQAStatus.PASSED,
        ProductionQAStatus.PASSED_WITH_WARNINGS,
    }
    cold_counts = (
        visual_provider.searches,
        visual_provider.fetches,
        narration_provider.calls,
    )
    assert cold_counts[0] >= 2 and cold_counts[1] >= 2 and cold_counts[2] >= 6
    mission_v1_snapshot, mission_v1_qa, mission_v1_guardian = await _exact_authority(
        db_session,
        channel_id=channel.id,
        request_id=mission.id,
        artifact=mission_v1,
        require_guardian=True,
    )
    assert mission_v1_guardian.decision.action in {
        GuardianAction.ALLOW.value,
        GuardianAction.ALLOW_WITH_WARNING.value,
    }
    mission_v1_physical = await _physical_evidence(
        storage,
        independent_probe,
        channel_id=channel.id,
        request_id=mission.id,
        artifact=mission_v1,
        snapshot=mission_v1_snapshot,
    )

    assert _normalized_semantics(
        interactive_snapshot, interactive_qa.status
    ) == _normalized_semantics(mission_v1_snapshot, mission_v1_qa.status)

    mission_v2, mission_v2_status = await _render_version(
        db_session,
        production_service,
        render_service,
        mission,
        role="v2",
        rerender=True,
    )
    assert mission_v2_status in {
        ProductionQAStatus.PASSED,
        ProductionQAStatus.PASSED_WITH_WARNINGS,
    }
    assert (
        visual_provider.searches,
        visual_provider.fetches,
        narration_provider.calls,
    ) == cold_counts, "same pinned mission lineage must replay from the physical cache"
    mission_v2_snapshot, mission_v2_qa, mission_v2_guardian = await _exact_authority(
        db_session,
        channel_id=channel.id,
        request_id=mission.id,
        artifact=mission_v2,
        require_guardian=True,
    )
    assert mission_v2_guardian.decision.action in {
        GuardianAction.ALLOW.value,
        GuardianAction.ALLOW_WITH_WARNING.value,
    }
    assert mission_v2.content_hash == mission_v1.content_hash
    assert mission_v2_snapshot.fingerprints.manifest_run == mission_v1_snapshot.fingerprints.manifest_run
    assert _normalized_semantics(
        mission_v2_snapshot, mission_v2_qa.status
    ) == _normalized_semantics(mission_v1_snapshot, mission_v1_qa.status)
    mission_v2_physical = await _physical_evidence(
        storage,
        independent_probe,
        channel_id=channel.id,
        request_id=mission.id,
        artifact=mission_v2,
        snapshot=mission_v2_snapshot,
    )

    mission_v3, mission_v3_status = await _render_version(
        db_session,
        production_service,
        render_service,
        mission,
        role="v3",
        rerender=True,
    )
    assert mission_v3_status == ProductionQAStatus.BLOCKED
    assert (
        visual_provider.searches,
        visual_provider.fetches,
        narration_provider.calls,
    ) == cold_counts
    mission_v3_snapshot, mission_v3_qa, mission_v3_guardian = await _exact_authority(
        db_session,
        channel_id=channel.id,
        request_id=mission.id,
        artifact=mission_v3,
        require_guardian=True,
    )
    assert mission_v3_qa.status == ProductionQAStatus.BLOCKED.value
    assert mission_v3_guardian.decision.action == GuardianAction.REQUIRE_REVIEW.value
    assert mission_v3.content_hash == mission_v2.content_hash
    mission_v3_physical = await _physical_evidence(
        storage,
        independent_probe,
        channel_id=channel.id,
        request_id=mission.id,
        artifact=mission_v3,
        snapshot=mission_v3_snapshot,
    )

    artifacts = list(
        (
            await db_session.execute(
                select(MediaArtifact)
                .where(MediaArtifact.production_request_id == mission.id)
                .order_by(MediaArtifact.version)
            )
        ).scalars()
    )
    assert [item.id for item in artifacts] == [mission_v1.id, mission_v2.id, mission_v3.id]
    assert [item.is_current for item in artifacts] == [False, True, False]
    assert len({item.id for item in artifacts}) == 3

    for artifact, snapshot, qa, guardian in (
        (mission_v1, mission_v1_snapshot, mission_v1_qa, mission_v1_guardian),
        (mission_v2, mission_v2_snapshot, mission_v2_qa, mission_v2_guardian),
        (mission_v3, mission_v3_snapshot, mission_v3_qa, mission_v3_guardian),
    ):
        assert qa.artifact_id == guardian.media_artifact_id == snapshot.lineage.media_artifact_id == artifact.id
        assert snapshot.lineage.artifact_version == artifact.version
        assert snapshot.lineage.render_job_id == artifact.render_job_id
        attributed = [
            item
            for item in snapshot.visual_beats
            if item.license_status == LicenseStatus.ATTRIBUTION_REQUIRED
        ]
        assert len(attributed) == 1
        assert attributed[0].provider == PROVIDER_NAME
        assert attributed[0].provider_asset_id == PROVIDER_ASSET_ID
        assert attributed[0].attribution == ATTRIBUTION_TEXT
        assert attributed[0].provider_asset_content_sha256 == compute_sha256(image_path)
        obligations, _ = read_attribution_foundation(snapshot.canonical_dict())
        assert len(obligations) == 1
        assert obligations[0].artifact_id == artifact.id
        assert obligations[0].artifact_sha256 == artifact.content_hash
        assert obligations[0].attribution_text == ATTRIBUTION_TEXT
        assert all(
            item["rule_code"]
            != ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION.value
            for item in qa.findings
        )

    first_export = await export_artifact_attribution_sidecar(
        channel.id,
        mission.id,
        mission_v2.id,
        db_session,
        storage,
    )
    second_export = await export_artifact_attribution_sidecar(
        channel.id,
        mission.id,
        mission_v2.id,
        db_session,
        storage,
    )
    assert first_export.package_created is True
    assert first_export.evidence_created is True
    assert second_export.package_created is False
    assert second_export.evidence_created is False
    assert first_export.package_uri == second_export.package_uri
    assert first_export.sidecar_sha256 == second_export.sidecar_sha256
    assert first_export.package_checksum == second_export.package_checksum
    package = storage.resolve_stored_uri(channel.id, mission.id, first_export.package_uri)
    sidecar = parse_attribution_sidecar((package / first_export.sidecar_filename).read_bytes())
    assert sidecar.artifact_id == mission_v2.id
    assert sidecar.artifact_sha256 == mission_v2.content_hash
    assert [item.attribution_text for item in sidecar.obligations] == [ATTRIBUTION_TEXT]
    assert compute_sha256(package / first_export.media_filename) == mission_v2.content_hash
    assert hashlib.sha256(
        (package / first_export.sidecar_filename).read_bytes()
    ).hexdigest() == first_export.sidecar_sha256

    assert blocked_network == []
    evidence = {
        "interactive_v1": {
            **interactive_physical,
            "qa": interactive_qa.status,
            "guardian": "NOT_APPLICABLE",
            "license": LicenseStatus.ATTRIBUTION_REQUIRED.value,
            "attribution_obligations": len(interactive_snapshot.attribution_obligations),
        },
        "mission_v1_cold": {
            **mission_v1_physical,
            "qa": mission_v1_qa.status,
            "guardian": mission_v1_guardian.decision.action,
            "license": LicenseStatus.ATTRIBUTION_REQUIRED.value,
            "attribution_obligations": len(mission_v1_snapshot.attribution_obligations),
        },
        "mission_v2_cache": {
            **mission_v2_physical,
            "qa": mission_v2_qa.status,
            "guardian": mission_v2_guardian.decision.action,
            "license": LicenseStatus.ATTRIBUTION_REQUIRED.value,
            "attribution_obligations": len(mission_v2_snapshot.attribution_obligations),
        },
        "mission_v3_blocked": {
            **mission_v3_physical,
            "qa": mission_v3_qa.status,
            "guardian": mission_v3_guardian.decision.action,
            "license": LicenseStatus.ATTRIBUTION_REQUIRED.value,
            "attribution_obligations": len(mission_v3_snapshot.attribution_obligations),
        },
        "provider_calls_after_cold": {
            "search": cold_counts[0],
            "fetch": cold_counts[1],
            "narration": cold_counts[2],
        },
        "sidecar": {
            "artifact_id": str(sidecar.artifact_id),
            "sidecar_sha256": first_export.sidecar_sha256,
            "package_checksum": first_export.package_checksum,
            "obligation_ids": list(first_export.obligation_ids),
            "idempotent_replay": True,
        },
        "network": {"blocked_attempts": blocked_network, "external_calls": 0},
    }
    print("P18F_CANARY_EVIDENCE=" + json.dumps(evidence, sort_keys=True))
