import asyncio
import json
import os
import subprocess
import sys
import uuid

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


async def main():
    if not os.environ.get("PEXELS_API_KEY"):
        print("BLOCKER: PEXELS_API_KEY required")
        sys.exit(1)
    if not os.environ.get("GEMINI_API_KEY"):
        print("BLOCKER: GEMINI_API_KEY required")
        sys.exit(1)

    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True)
        subprocess.run(["ffprobe", "-version"], check=True, capture_output=True)
    except Exception:
        print("BLOCKER: ffmpeg or ffprobe not available")
        sys.exit(1)

    storage = LocalMediaStorageProvider()

    # IDs
    channel_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    research_req_id = uuid.uuid4()
    brief_id = uuid.uuid4()
    content_req_id = uuid.uuid4()
    script_id = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        # Lineage
        channel = Channel(
            id=channel_id, slug=f"canary-{channel_id.hex[:6]}", name="QA Canary",
            platform="YOUTUBE", dna={"brand_voice": {"tone": ["AUTHORITATIVE"]}}
        )
        session.add(channel)

        dna = ChannelDNARevision(
            id=dna_id, channel_id=channel_id, version=1, snapshot={}, change_reason="init"
        )
        session.add(dna)

        topic = TopicCandidate(
            id=topic_id, channel_id=channel_id, title="Canary Topic",
            normalized_title="canary topic", source_type="MANUAL",
            topic_fingerprint=topic_id.hex
        )
        session.add(topic)

        mission = Mission(
            id=mission_id, channel_id=channel_id, title="Canary",
            objective="Validate QA V3 real production truth"
        )
        session.add(mission)

        execution = MissionExecution(
            id=execution_id, mission_id=mission_id, channel_dna_revision_id=dna_id,
            state="PLANNED", trigger_type="MANUAL"
        )
        session.add(execution)

        res_req = ResearchRequest(
            id=research_req_id, channel_id=channel_id, topic_candidate_id=topic_id,
            mission_execution_id=execution_id
        )
        session.add(res_req)

        brief = ResearchBrief(
            id=brief_id, research_request_id=research_req_id, channel_id=channel_id,
            topic_candidate_id=topic_id, version=1, title="Canary Research Brief",
            summary="Minimal research fixture for the P7-E QA V3 canary."
        )
        session.add(brief)

        content_req = ContentGenerationRequest(
            id=content_req_id, channel_id=channel_id, topic_candidate_id=topic_id,
            research_brief_id=brief_id, channel_dna_revision_id=dna_id,
            mission_execution_id=execution_id, target_duration_seconds=15
        )
        session.add(content_req)

        script = ScriptVersion(
            id=script_id, content_request_id=content_req_id, version=1,
            is_current=True, title="Canary Script", hook_text="A concise real-world QA V3 production overview.", closing_text="", cta_text="",
            estimated_word_count=50, estimated_duration_seconds=15
        )
        session.add(script)

        # Scenes: 3 sections
        texts = [
            "Welcome to the real-world overview.",
            "In the modern environment, many people navigate the bustling city while relying on nature to ground their daily motion.",
            "Subscribe for more insights."
        ]
        for i, text in enumerate(texts):
            sec = ScriptSection(
                id=uuid.uuid4(), script_version_id=script_id, section_order=i,
                heading=f"Section {i}", narration_text=text,
                estimated_duration_seconds=5
            )
            session.add(sec)
            stmt = ScriptStatement(
                id=uuid.uuid4(), script_section_id=sec.id, statement_order=0,
                statement_text=text, statement_type="FACT"
            )
            session.add(stmt)

        await session.commit()

    async with AsyncSessionLocal() as session:
        # Create
        prod_service = ProductionService(storage=storage)

        payload = ProductionRequestCreate(
            script_version_id=script_id,
            mission_execution_id=execution_id,
            target_width=1920,
            target_height=1080,
            fps=30,
            video_codec="h264",
            audio_codec="aac",
            container_format="mp4"
        )
        req = await prod_service.create_production_request(
            session, channel_id, payload, idempotency_key=f"p7-create-{uuid.uuid4()}"
        )
        assert req.mode == "MISSION_EXECUTION"
        assert req.mission_execution_id == execution_id

        # Prepare
        prod_service.narration_provider = LocalTTSNarrationProvider(storage)
        await prod_service.prepare_production(session, channel_id, req.id)

        # Allocate
        job, plan, is_new = await prod_service.allocate_render_job(
            session, channel_id, req.id, idempotency_key=f"p7-render-{uuid.uuid4()}"
        )
        assert is_new is True
        assert job.production_request_id == req.id
        assert plan.production_request_id == req.id
        await session.commit()

    os.environ["TTS_PROVIDER"] = "gemini"
    render_service = build_production_render_service(storage)

    render_attempted = False

    async with AsyncSessionLocal() as session:
        assert render_attempted is False
        render_attempted = True

        try:
            art, qa_status = await render_service.execute_render_job(
                session, channel_id, req.id, job.id
            )
            await session.commit()
        except Exception as e:
            err_msg = str(e)
            print("OMEGA P7-E ONE-SHOT QA REAL CANARY")
            print("CANARY_ATTEMPTED: YES")
            print("CANARY_RERUN_ALLOWED: NO")
            print(f"MISSION_ID: {mission_id}")
            print(f"MISSION_EXECUTION_ID: {execution_id}")
            print(f"CONTENT_REQUEST_ID: {content_req_id}")
            print(f"SCRIPT_VERSION_ID: {script_id}")
            print(f"PRODUCTION_REQUEST_ID: {req.id}")
            print(f"RENDER_JOB_ID: {job.id}")
            print("P7_E_RESULT: BLOCKED")
            print(f"BLOCKER: execute_render_job failed: {err_msg}")
            sys.exit(1)

    # Post run queries
    async with AsyncSessionLocal() as session:
        # Reload Job and Request
        final_request = (await session.execute(
            select(ProductionRequest).where(ProductionRequest.id == req.id)
        )).scalar_one_or_none()
        final_job = (await session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.id == job.id)
        )).scalar_one_or_none()

        # QA
        qa_res = (await session.execute(
            select(ProductionQAResult)
            .where(ProductionQAResult.production_request_id == req.id)
        )).scalar_one_or_none()

        local_rule_codes = set()
        if qa_res and qa_res.findings:
            for f in qa_res.findings:
                c = f.get("rule_code")
                if c:
                    local_rule_codes.add(c)

        # Guardian
        g_check = (await session.execute(
            select(GuardianCheck)
            .where(
                GuardianCheck.production_request_id == req.id,
                GuardianCheck.checkpoint == "POST_RENDER"
            )
            .options(selectinload(GuardianCheck.decision), selectinload(GuardianCheck.findings))
        )).scalar_one_or_none()

        guardian_media_rule_codes = set()
        if g_check and g_check.findings:
            for f in g_check.findings:
                if f.detector_type == "MEDIA_INTEGRITY":
                    guardian_media_rule_codes.add(f.rule_id)

        # Artifact
        artifact = (await session.execute(
            select(MediaArtifact)
            .where(
                MediaArtifact.production_request_id == req.id,
                MediaArtifact.is_current,
                MediaArtifact.artifact_type == "VIDEO"
            )
        )).scalars().first()

    blockers = []

    finding_parity_exact = (local_rule_codes == guardian_media_rule_codes)
    if not finding_parity_exact:
        blockers.append("FINDING_PARITY failed")

    if not artifact:
        blockers.append("No final artifact")
    else:
        if len(artifact.content_hash) != 64 or not all(c in "0123456789abcdefABCDEF" for c in artifact.content_hash):
            blockers.append("Artifact content_hash invalid")
        if artifact.file_size_bytes <= 0:
            blockers.append("Artifact file_size_bytes <= 0")
        if artifact.width != 1920:
            blockers.append("Artifact width != 1920")
        if artifact.height != 1080:
            blockers.append("Artifact height != 1080")
        if not artifact.duration_ms or artifact.duration_ms <= 0:
            blockers.append("Artifact duration_ms <= 0")

    run_dir = storage.base_root / "visual_v2" / str(execution_id)
    manifest_path = None
    run_fingerprint = None
    if run_dir.exists():
        dirs = [d for d in run_dir.iterdir() if d.is_dir() and len(d.name) == 64 and all(c in "0123456789abcdefABCDEF" for c in d.name)]
        if len(dirs) == 1:
            run_fingerprint = dirs[0].name
            manifest_path = dirs[0] / "manifest.json"
            final_mp4 = dirs[0] / "final.mp4"
            if not final_mp4.exists() or final_mp4.stat().st_size == 0:
                blockers.append("final.mp4 missing or empty")

    if not manifest_path or not manifest_path.exists():
        blockers.append("manifest.json missing")

    manifest = {}
    if manifest_path and manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)

        if manifest.get("run_fingerprint") != run_fingerprint:
            blockers.append("manifest run_fingerprint mismatch")
        if artifact and manifest.get("content_sha256") != artifact.content_hash:
            blockers.append("manifest content_sha256 mismatch")
        if manifest.get("width") != 1920:
            blockers.append("manifest width mismatch")
        if manifest.get("height") != 1080:
            blockers.append("manifest height mismatch")
        if manifest.get("fps") != 30:
            blockers.append("manifest fps mismatch")

        if manifest.get("narration_quality") != "NEURAL_PRODUCTION":
            blockers.append("narration_quality != NEURAL_PRODUCTION")
        if not manifest.get("narration_source_refs"):
            blockers.append("narration_source_refs empty")
        if not manifest.get("runtime_timeline_duration_ms") or manifest.get("runtime_timeline_duration_ms") <= 0:
            blockers.append("runtime_timeline_duration_ms invalid")
        if not manifest.get("runtime_narration_segments"):
            blockers.append("runtime_narration_segments empty")
        if not isinstance(manifest.get("runtime_subtitle_cues"), list):
            blockers.append("runtime_subtitle_cues missing or not list")

    v2_selected = manifest_path is not None
    real_visual_provider = False
    real_visual_scenes = 0

    for s in manifest.get("scenes", []):
        if s.get("asset_provider") == "pexels" and s.get("asset_id"):
            real_visual_provider = True
            real_visual_scenes += 1

    if not real_visual_provider:
        blockers.append("No real Pexels scene proof")

    diag = g_check.diagnostic_context if g_check else {}
    if diag.get("narration_quality") != manifest.get("narration_quality"):
        blockers.append("Guardian diag narration_quality mismatch")
    if diag.get("narration_source_refs") != manifest.get("narration_source_refs"):
        blockers.append("Guardian diag narration_source_refs mismatch")
    if diag.get("runtime_timeline_duration_ms") != manifest.get("runtime_timeline_duration_ms"):
        blockers.append("Guardian diag runtime_timeline_duration_ms mismatch")

    diag_narr = diag.get("runtime_narration_segments", [])
    man_narr = manifest.get("runtime_narration_segments", [])
    if len(diag_narr) != len(man_narr):
        blockers.append("Guardian diag narration_segments length mismatch")
    else:
        for dn, mn in zip(diag_narr, man_narr, strict=True):
            if (dn.get("scene_index") != mn.get("scene_index") or
                dn.get("start_ms") != mn.get("start_ms") or
                dn.get("end_ms") != mn.get("end_ms") or
                dn.get("duration_ms") != mn.get("duration_ms")):
                blockers.append("Guardian diag narration_segments content mismatch")
                break

    diag_sub = diag.get("runtime_subtitle_cues", [])
    man_sub = manifest.get("runtime_subtitle_cues", [])
    if len(diag_sub) != len(man_sub):
        blockers.append("Guardian diag subtitle_cues length mismatch")
    else:
        for ds, ms in zip(diag_sub, man_sub, strict=True):
            if (ds.get("scene_index") != ms.get("scene_index") or
                ds.get("cue_order") != ms.get("cue_order") or
                ds.get("start_ms") != ms.get("start_ms") or
                ds.get("end_ms") != ms.get("end_ms") or
                ds.get("text") != ms.get("text")):
                blockers.append("Guardian diag subtitle_cues content mismatch")
                break

    fallback_tts = False
    subtitle_out = False

    is_neural = manifest.get("narration_quality") == "NEURAL_PRODUCTION"
    if ("ROBOTIC_FALLBACK_TTS" in local_rule_codes or "ROBOTIC_FALLBACK_TTS" in guardian_media_rule_codes) and is_neural:
        fallback_tts = True
        blockers.append("ROBOTIC_FALLBACK_TTS false positive")

    if "SUBTITLE_OUT_OF_RANGE" in local_rule_codes or "SUBTITLE_OUT_OF_RANGE" in guardian_media_rule_codes:
        max_sub = 0
        for ms in man_sub:
            if ms.get("end_ms", 0) > max_sub:
                max_sub = ms.get("end_ms", 0)
        actual_dur = artifact.duration_ms if artifact and artifact.duration_ms else 0
        if max_sub <= actual_dur + 500:
            subtitle_out = True
            blockers.append("SUBTITLE_OUT_OF_RANGE false positive")

    print("OMEGA P7-E ONE-SHOT QA REAL CANARY")
    print("CANARY_ATTEMPTED: YES")
    print("CANARY_RERUN_ALLOWED: NO")
    print(f"MISSION_ID: {mission_id}")
    print(f"MISSION_EXECUTION_ID: {execution_id}")
    print(f"CONTENT_REQUEST_ID: {content_req_id}")
    print(f"SCRIPT_VERSION_ID: {script_id}")
    print(f"PRODUCTION_REQUEST_ID: {req.id}")
    print(f"RENDER_JOB_ID: {job.id}")
    print(f"V2_SELECTED: {v2_selected}")
    print(f"REAL_VISUAL_PROVIDER: {real_visual_provider}")
    print(f"REAL_VISUAL_SCENES: {real_visual_scenes}")
    print(f"NARRATION_QUALITY: {manifest.get('narration_quality')}")
    print(f"NARRATION_SOURCE_REFS: {manifest.get('narration_source_refs')}")
    print(f"RUNTIME_TIMELINE_DURATION_MS: {manifest.get('runtime_timeline_duration_ms')}")
    print(f"RUNTIME_NARRATION_SEGMENTS: {len(man_narr)}")
    print(f"RUNTIME_SUBTITLE_CUES: {len(man_sub)}")
    print(f"QA_STATUS: {qa_res.status if qa_res else 'NONE'}")
    print(f"QA_RULE_CODES: {list(local_rule_codes)}")
    print(f"GUARDIAN_CHECK_ID: {g_check.id if g_check else 'NONE'}")
    print(f"GUARDIAN_MEDIA_RULE_CODES: {list(guardian_media_rule_codes)}")
    print(f"GUARDIAN_ACTION: {g_check.decision.action if g_check and g_check.decision else 'NONE'}")
    print(f"GUARDIAN_GATE_STATE: {g_check.decision.resulting_gate_state if g_check and g_check.decision else 'NONE'}")
    print(f"FINDING_PARITY: {'EXACT' if finding_parity_exact else 'MISMATCH'}")
    print(f"ROBOTIC_FALLBACK_FALSE_POSITIVE: {fallback_tts}")
    print(f"SUBTITLE_OUT_OF_RANGE_FALSE_POSITIVE: {subtitle_out}")
    print(f"ARTIFACT_ID: {artifact.id if artifact else 'NONE'}")
    print(f"ARTIFACT_URI: {artifact.storage_uri if artifact else 'NONE'}")
    print(f"ARTIFACT_SHA256: {artifact.content_hash if artifact else 'NONE'}")
    print(f"ARTIFACT_BYTES: {artifact.file_size_bytes if artifact else 'NONE'}")
    print(f"ARTIFACT_WIDTH: {artifact.width if artifact else 'NONE'}")
    print(f"ARTIFACT_HEIGHT: {artifact.height if artifact else 'NONE'}")
    print(f"ARTIFACT_DURATION_MS: {artifact.duration_ms if artifact else 'NONE'}")
    print(f"MANIFEST_PATH: {manifest_path}")
    print(f"RUN_FINGERPRINT: {run_fingerprint}")
    print(f"PRODUCTION_STATUS: {final_request.status if final_request else 'NONE'}")
    print(f"PRODUCTION_OUTCOME: {final_request.outcome if final_request else 'NONE'}")
    print(f"RENDER_JOB_STATE: {final_job.state if final_job else 'NONE'}")
    print(f"RENDER_JOB_ERROR_CODE: {final_job.error_code if final_job else 'NONE'}")
    print(f"RENDER_JOB_SANITIZED_ERROR: {final_job.sanitized_error if final_job else 'NONE'}")

    action = g_check.decision.action if g_check and g_check.decision else None
    if action not in ("ALLOW", "ALLOW_WITH_WARNING"):
        blockers.append(f"Guardian action blocked canary: {action or 'NONE'}")

    p7_res = "PASS" if not blockers and action in ("ALLOW", "ALLOW_WITH_WARNING") else "BLOCKED"

    print(f"P7_E_RESULT: {p7_res}")
    print(f"BLOCKER: {', '.join(blockers) if blockers else 'NONE'}")

    if p7_res == "BLOCKED":
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())
