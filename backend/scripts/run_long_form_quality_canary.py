"""One-shot real canary for the sealed P8 long-form production contract.

This module is inert when imported. P8-E2 owns the single explicit execution.
"""

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import LocalTTSNarrationProvider
from omega.application.production_render_factory import build_production_render_service
from omega.application.production_service import ProductionService
from omega.domain.production import ProductionRequestCreate
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    GuardianCheck,
    MediaArtifact,
    Mission,
    MissionExecution,
    ProductionQAResult,
    ProductionRenderJob,
    ProductionRequest,
    ResearchBrief,
    ResearchRequest,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
    TopicCandidate,
)

TARGET_DURATION_SECONDS = 60
MINIMUM_ACCEPTED_DURATION_MS = 45_000
EXPECTED_STRATEGIES = (
    "TITLE_MOTION",
    "BROLL",
    "IMAGE",
    "DIAGRAM",
    "INFOGRAPHIC",
    "CTA",
)
SECTION_FIXTURES = (
    (
        "Intro Hook",
        "Welcome to this video where we discuss very interesting things about "
        "everything in the world today.",
    ),
    (
        "Broll Section",
        "The environment is full of action and motion as people walk around the "
        "city building today.",
    ),
    (
        "Image Section",
        "A completely normal statement that has enough words to avoid kinetic "
        "text and should trigger images.",
    ),
    (
        "Diagram Section",
        "The system architecture and workflow pipeline consists of many different "
        "components processing the various data stages.",
    ),
    (
        "Infographic Section",
        "A quick overview and comparison of the tradeoff between multiple "
        "multi-factor options in our summary.",
    ),
    (
        "CTA Section",
        "Thank you for watching please subscribe to the channel and leave a "
        "comment below today.",
    ),
)


def _fail(message: str) -> None:
    print("OMEGA P8-E LONG-FORM ONE-SHOT REAL CANARY")
    print("CANARY_RERUN_ALLOWED: NO")
    print("P8_E_RESULT: BLOCKED")
    print(f"BLOCKER: {message}")
    raise SystemExit(1)


def _preflight() -> None:
    missing = [
        name
        for name in ("PEXELS_API_KEY", "GEMINI_API_KEY")
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        _fail(f"required configuration missing: {', '.join(missing)}")

    for executable in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run(
                [executable, "-version"],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            _fail(f"required executable unavailable: {executable}")


def _find_manifest(
    storage: LocalMediaStorageProvider,
    execution_id: uuid.UUID,
) -> tuple[Path | None, str | None]:
    run_dir = storage.base_root / "visual_v2" / str(execution_id)
    if not run_dir.exists():
        return None, None

    run_dirs = [
        path
        for path in run_dir.iterdir()
        if path.is_dir()
        and len(path.name) == 64
        and all(char in "0123456789abcdefABCDEF" for char in path.name)
    ]
    if len(run_dirs) != 1:
        return None, None
    return run_dirs[0] / "manifest.json", run_dirs[0].name


async def _seed_long_form_lineage() -> dict[str, uuid.UUID]:
    ids = {
        "channel": uuid.uuid4(),
        "dna": uuid.uuid4(),
        "topic": uuid.uuid4(),
        "mission": uuid.uuid4(),
        "execution": uuid.uuid4(),
        "research_request": uuid.uuid4(),
        "brief": uuid.uuid4(),
        "content_request": uuid.uuid4(),
        "script": uuid.uuid4(),
    }

    async with AsyncSessionLocal() as session:
        session.add(
            Channel(
                id=ids["channel"],
                slug=f"long-form-canary-{ids['channel'].hex[:6]}",
                name="P8 Long-Form Quality Canary",
                platform="YOUTUBE",
                dna={"brand_voice": {"tone": ["AUTHORITATIVE"]}},
            )
        )
        session.add(
            ChannelDNARevision(
                id=ids["dna"],
                channel_id=ids["channel"],
                version=1,
                snapshot={"default_duration_min_seconds": TARGET_DURATION_SECONDS},
                change_reason="P8 long-form canary",
            )
        )
        session.add(
            TopicCandidate(
                id=ids["topic"],
                channel_id=ids["channel"],
                title="Long-Form Quality Canary",
                normalized_title="long-form quality canary",
                source_type="MANUAL",
                topic_fingerprint=ids["topic"].hex,
            )
        )
        session.add(
            Mission(
                id=ids["mission"],
                channel_id=ids["channel"],
                title="P8 Long-Form Quality Canary",
                objective="Validate the sealed P8 long-form production contract",
            )
        )
        session.add(
            MissionExecution(
                id=ids["execution"],
                mission_id=ids["mission"],
                channel_dna_revision_id=ids["dna"],
                state="PLANNED",
                trigger_type="MANUAL",
            )
        )
        session.add(
            ResearchRequest(
                id=ids["research_request"],
                channel_id=ids["channel"],
                topic_candidate_id=ids["topic"],
                mission_execution_id=ids["execution"],
            )
        )
        session.add(
            ResearchBrief(
                id=ids["brief"],
                research_request_id=ids["research_request"],
                channel_id=ids["channel"],
                topic_candidate_id=ids["topic"],
                version=1,
                title="P8 Long-Form Canary Research Brief",
                summary="Canonical six-section fixture for real long-form evidence.",
            )
        )
        session.add(
            ContentGenerationRequest(
                id=ids["content_request"],
                channel_id=ids["channel"],
                topic_candidate_id=ids["topic"],
                research_brief_id=ids["brief"],
                channel_dna_revision_id=ids["dna"],
                mission_execution_id=ids["execution"],
                target_duration_seconds=TARGET_DURATION_SECONDS,
            )
        )
        session.add(
            ScriptVersion(
                id=ids["script"],
                content_request_id=ids["content_request"],
                version=1,
                is_current=True,
                title="Long Form Canary Fixture",
                hook_text=SECTION_FIXTURES[0][1],
                closing_text="",
                cta_text=SECTION_FIXTURES[-1][1],
                estimated_word_count=sum(
                    len(text.split()) for _heading, text in SECTION_FIXTURES
                ),
                estimated_duration_seconds=TARGET_DURATION_SECONDS,
            )
        )

        for section_order, (heading, text) in enumerate(SECTION_FIXTURES, start=1):
            section_id = uuid.uuid4()
            session.add(
                ScriptSection(
                    id=section_id,
                    script_version_id=ids["script"],
                    section_order=section_order,
                    heading=heading,
                    narration_text=text,
                    estimated_duration_seconds=10,
                )
            )
            session.add(
                ScriptStatement(
                    id=uuid.uuid4(),
                    script_section_id=section_id,
                    statement_order=section_order,
                    statement_text=text,
                    statement_type="NARRATION",
                )
            )

        await session.commit()
    return ids


async def main() -> None:
    _preflight()
    ids = await _seed_long_form_lineage()
    storage = LocalMediaStorageProvider()

    async with AsyncSessionLocal() as session:
        production_service = ProductionService(storage=storage)
        payload = ProductionRequestCreate(
            script_version_id=ids["script"],
            mission_execution_id=ids["execution"],
            target_width=1920,
            target_height=1080,
            fps=30,
            video_codec="h264",
            audio_codec="aac",
            container_format="mp4",
        )
        request = await production_service.create_production_request(
            session,
            ids["channel"],
            payload,
            idempotency_key=f"p8-long-form-create-{uuid.uuid4()}",
        )
        if request.mode != "MISSION_EXECUTION":
            _fail(f"unexpected production mode: {request.mode}")

        production_service.narration_provider = LocalTTSNarrationProvider(storage)
        await production_service.prepare_production(
            session,
            ids["channel"],
            request.id,
        )
        job, plan, is_new = await production_service.allocate_render_job(
            session,
            ids["channel"],
            request.id,
            idempotency_key=f"p8-long-form-render-{uuid.uuid4()}",
        )
        if not is_new or job.production_request_id != request.id:
            _fail("render job allocation was not new or had incorrect lineage")
        if plan.production_request_id != request.id:
            _fail("render plan had incorrect production request lineage")
        await session.commit()

    os.environ["TTS_PROVIDER"] = "gemini"
    render_service = build_production_render_service(storage)
    render_attempted = False

    async with AsyncSessionLocal() as session:
        if render_attempted:
            _fail("second render execution was requested")
        render_attempted = True
        try:
            _artifact, render_qa_status = await render_service.execute_render_job(
                session,
                ids["channel"],
                request.id,
                job.id,
            )
            await session.commit()
        except Exception as exc:
            _fail(f"execute_render_job failed: {exc}")

    async with AsyncSessionLocal() as session:
        final_request = (
            await session.execute(
                select(ProductionRequest).where(ProductionRequest.id == request.id)
            )
        ).scalar_one_or_none()
        final_job = (
            await session.execute(
                select(ProductionRenderJob).where(ProductionRenderJob.id == job.id)
            )
        ).scalar_one_or_none()
        qa_result = (
            await session.execute(
                select(ProductionQAResult).where(
                    ProductionQAResult.production_request_id == request.id
                )
            )
        ).scalar_one_or_none()
        guardian_check = (
            await session.execute(
                select(GuardianCheck)
                .where(
                    GuardianCheck.production_request_id == request.id,
                    GuardianCheck.checkpoint == "POST_RENDER",
                )
                .options(
                    selectinload(GuardianCheck.decision),
                    selectinload(GuardianCheck.findings),
                )
            )
        ).scalar_one_or_none()
        artifact = (
            await session.execute(
                select(MediaArtifact).where(
                    MediaArtifact.production_request_id == request.id,
                    MediaArtifact.is_current,
                    MediaArtifact.artifact_type == "VIDEO",
                )
            )
        ).scalars().first()

    manifest_path, run_fingerprint = _find_manifest(storage, ids["execution"])
    manifest = {}
    if manifest_path and manifest_path.is_file():
        with manifest_path.open(encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)

    qa_rule_codes = {
        finding.get("rule_code")
        for finding in (qa_result.findings if qa_result else [])
        if finding.get("rule_code")
    }
    guardian_rule_codes = {
        finding.rule_id
        for finding in (guardian_check.findings if guardian_check else [])
        if finding.detector_type == "MEDIA_INTEGRITY"
    }
    guardian_diagnostic_context = (
        guardian_check.diagnostic_context if guardian_check else {}
    ) or {}
    runtime_scenes = guardian_diagnostic_context.get("runtime_scenes")
    runtime_strategies = [
        scene.get("effective_strategy") for scene in (runtime_scenes or [])
    ]
    scenes = manifest.get("scenes", [])
    strategies = [scene.get("effective_strategy") for scene in scenes]
    narration_segments = manifest.get("runtime_narration_segments", [])
    subtitle_cues = manifest.get("runtime_subtitle_cues", [])
    external_visual_scenes = [
        scene
        for scene in scenes
        if scene.get("effective_strategy") in {"BROLL", "IMAGE"}
        and str(scene.get("asset_provider", "")).lower() == "pexels"
        and scene.get("asset_id")
    ]
    artifact_path = (
        storage.resolve_artifact_path(
            ids["channel"],
            request.id,
            artifact.storage_uri,
        )
        if artifact
        else None
    )

    blockers = []
    if not artifact:
        blockers.append("final video artifact missing")
    elif (
        artifact.duration_ms is None
        or artifact.duration_ms < MINIMUM_ACCEPTED_DURATION_MS
    ):
        blockers.append("final video artifact duration is under 45000ms")
    manifest_duration_ms = manifest.get("runtime_timeline_duration_ms")
    if (
        manifest_duration_ms is not None
        and manifest_duration_ms < MINIMUM_ACCEPTED_DURATION_MS
    ):
        blockers.append("manifest runtime timeline duration is under 45000ms")
    if not artifact_path or not artifact_path.is_file():
        blockers.append("physical final video file missing")
    elif artifact_path.stat().st_size <= 0:
        blockers.append("physical final video file is empty")
    if not manifest_path or not manifest_path.is_file():
        blockers.append("manifest.json missing")
    if manifest.get("run_fingerprint") != run_fingerprint:
        blockers.append("manifest run fingerprint mismatch")
    if artifact and manifest.get("content_sha256") != artifact.content_hash:
        blockers.append("manifest and artifact hashes differ")
    if len(scenes) != len(EXPECTED_STRATEGIES):
        blockers.append("runtime scene count is not 6")
    if strategies != list(EXPECTED_STRATEGIES):
        blockers.append("runtime strategy sequence differs from sealed contract")
    if runtime_scenes is None:
        blockers.append("Guardian diagnostic runtime_scenes missing")
    elif len(runtime_scenes) != len(EXPECTED_STRATEGIES):
        blockers.append("Guardian diagnostic runtime scene count is not 6")
    if runtime_strategies != list(EXPECTED_STRATEGIES):
        blockers.append("Guardian diagnostic runtime strategy sequence differs")
    if scenes and runtime_scenes != scenes:
        blockers.append("Guardian diagnostic runtime scenes differ from manifest")
    if len(external_visual_scenes) != 2:
        blockers.append("real Pexels evidence missing for BROLL or IMAGE")
    if manifest.get("narration_quality") != "NEURAL_PRODUCTION":
        blockers.append("narration quality is not NEURAL_PRODUCTION")
    if not manifest.get("narration_source_refs"):
        blockers.append("narration source references missing")
    if len(narration_segments) != len(EXPECTED_STRATEGIES):
        blockers.append("runtime narration segment count is not 6")
    if len(subtitle_cues) <= len(EXPECTED_STRATEGIES):
        blockers.append("runtime subtitle cue scale is not long-form")
    if not qa_result:
        blockers.append("ProductionQA result missing")
    elif qa_result.status != "PASSED":
        blockers.append(f"ProductionQA status is not PASSED: {qa_result.status}")
    if qa_rule_codes:
        blockers.append("ProductionQA rule-code set is not empty")
    if not guardian_check or not guardian_check.decision:
        blockers.append("POST_RENDER Guardian decision missing")
    if guardian_rule_codes:
        blockers.append("Guardian media ProductionQA-derived rule-code set is not empty")

    guardian_action = (
        guardian_check.decision.action
        if guardian_check and guardian_check.decision
        else "NONE"
    )
    if guardian_action not in ("ALLOW", "ALLOW_WITH_WARNING"):
        blockers.append(f"Guardian action blocked canary: {guardian_action}")

    print("OMEGA P8-E LONG-FORM ONE-SHOT REAL CANARY")
    print("CANARY_ATTEMPTED: YES")
    print("CANARY_RERUN_ALLOWED: NO")
    print(f"MISSION_ID: {ids['mission']}")
    print(f"MISSION_EXECUTION_ID: {ids['execution']}")
    print(f"CONTENT_REQUEST_ID: {ids['content_request']}")
    print(f"SCRIPT_VERSION_ID: {ids['script']}")
    print(f"PRODUCTION_REQUEST_ID: {request.id}")
    print(f"RENDER_JOB_ID: {job.id}")
    print(f"TARGET_DURATION_SECONDS: {TARGET_DURATION_SECONDS}")
    print(f"SCENE_COUNT: {len(scenes)}")
    print(f"STRATEGY_SEQUENCE: {strategies}")
    print(f"GUARDIAN_RUNTIME_SCENE_COUNT: {len(runtime_scenes or [])}")
    print(f"GUARDIAN_RUNTIME_STRATEGY_SEQUENCE: {runtime_strategies}")
    print(f"REAL_VISUAL_SCENES: {len(external_visual_scenes)}")
    print(f"NARRATION_QUALITY: {manifest.get('narration_quality')}")
    print(f"RUNTIME_NARRATION_SEGMENTS: {len(narration_segments)}")
    print(f"RUNTIME_SUBTITLE_CUES: {len(subtitle_cues)}")
    print(f"QA_STATUS: {qa_result.status if qa_result else 'NONE'}")
    print(f"RENDER_QA_STATUS: {render_qa_status}")
    print(f"QA_RULE_CODES: {sorted(qa_rule_codes)}")
    print(f"GUARDIAN_CHECK_ID: {guardian_check.id if guardian_check else 'NONE'}")
    print(f"GUARDIAN_MEDIA_RULE_CODES: {sorted(guardian_rule_codes)}")
    print(f"GUARDIAN_ACTION: {guardian_action}")
    print(f"ARTIFACT_URI: {artifact.storage_uri if artifact else 'NONE'}")
    print(f"ARTIFACT_PATH: {artifact_path}")
    print(
        "ARTIFACT_FILE_SIZE: "
        f"{artifact_path.stat().st_size if artifact_path and artifact_path.is_file() else 'NONE'}"
    )
    print(f"ARTIFACT_DURATION_MS: {artifact.duration_ms if artifact else 'NONE'}")
    print(f"MANIFEST_RUNTIME_TIMELINE_DURATION_MS: {manifest_duration_ms}")
    print(f"MANIFEST_PATH: {manifest_path}")
    print(f"PRODUCTION_STATUS: {final_request.status if final_request else 'NONE'}")
    print(f"PRODUCTION_OUTCOME: {final_request.outcome if final_request else 'NONE'}")
    print(f"RENDER_JOB_STATE: {final_job.state if final_job else 'NONE'}")
    print(f"P8_E_RESULT: {'PASS' if not blockers else 'BLOCKED'}")
    print(f"BLOCKER: {', '.join(blockers) if blockers else 'NONE'}")

    if blockers:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
