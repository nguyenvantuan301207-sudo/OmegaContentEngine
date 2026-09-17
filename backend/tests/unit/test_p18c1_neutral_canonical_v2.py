import hashlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.brand_asset_resolver import BrandMediaKind, ResolvedBrandAsset
from omega.application.production_contract import (
    CanonicalProductionContract,
    CanonicalProductionLineage,
    CanonicalProductionPolicy,
)
from omega.application.production_runtime_truth import (
    build_production_runtime_truth_snapshot,
)
from omega.application.storyboard_engine import (
    StoryboardPlan,
    StoryboardScene,
    VisualStrategy,
)
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.application.visual_production_v2_service import (
    SUBTITLE_SEMANTICS_VERSION,
    ScriptStoryboardAdapter,
    VerticalSliceError,
    VisualProductionV2Service,
)
from omega.domain.production import (
    NarrationProviderType,
    ProductionMode,
    SubtitleFallbackPolicy,
    SubtitleMode,
    VisualAssetMode,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    ProductionRequest,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
)
from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderResult

VALID_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def _script(version: int, title: str) -> ScriptVersion:
    script = ScriptVersion(
        id=uuid.uuid4(),
        version=version,
        title=title,
        hook_text="Pinned hook",
        closing_text="Pinned closing",
        cta_text="Pinned CTA",
        estimated_duration_seconds=6,
    )
    section = ScriptSection(
        id=uuid.uuid4(),
        section_order=1,
        heading="Pinned body",
        narration_text="Pinned body statement",
        estimated_duration_seconds=6,
    )
    statement = ScriptStatement(
        id=uuid.uuid4(),
        statement_order=1,
        statement_text="Pinned body statement",
        statement_type="ASSERTION",
    )
    statement.citations = []
    section.statements = [statement]
    script.sections = [section]
    return script


def _interactive_fixture():
    channel = Channel(
        id=uuid.uuid4(),
        name="Neutral channel",
        slug=f"neutral-{uuid.uuid4().hex[:8]}",
        metadata_={},
    )
    dna = ChannelDNARevision(
        id=uuid.uuid4(),
        channel_id=channel.id,
        version=3,
        snapshot={},
        change_reason="pinned",
    )
    pinned = _script(1, "Pinned script")
    latest = _script(2, "Latest script must not render")
    content_request = ContentGenerationRequest(
        id=uuid.uuid4(),
        channel_id=channel.id,
        channel_dna_revision_id=dna.id,
        mission_execution_id=None,
    )
    content_request.scripts = [pinned, latest]
    request = ProductionRequest(
        id=uuid.uuid4(),
        channel_id=channel.id,
        content_request_id=content_request.id,
        script_version_id=pinned.id,
        channel_dna_revision_id=dna.id,
        mission_execution_id=None,
        mode=ProductionMode.INTERACTIVE.value,
        voice_profile={"voice": "canonical-voice"},
    )
    request.channel = channel
    request.channel_dna_revision = dna
    request.content_request = content_request
    request.script_version = pinned
    contract = CanonicalProductionContract(
        mode=ProductionMode.INTERACTIVE,
        lineage=CanonicalProductionLineage(
            channel_id=channel.id,
            production_request_id=request.id,
            content_request_id=content_request.id,
            script_version_id=pinned.id,
            channel_dna_revision_id=dna.id,
            mission_id=None,
            mission_execution_id=None,
            task_id=None,
        ),
        policy=CanonicalProductionPolicy(
            visual_asset_mode=VisualAssetMode.LOCAL_TEMPLATE_ONLY,
            narration_provider=NarrationProviderType.LOCAL_TTS,
            subtitle_mode=SubtitleMode.OFF,
            subtitle_fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
            subtitle_style=SubtitleRenderStyle(),
            target_fps=12,
            voice_profile=request.voice_profile,
        ),
    )
    return request, pinned, latest, contract


def _session_for(request: ProductionRequest):
    session = AsyncMock()

    async def execute(statement):
        result = MagicMock()
        if "production_requests" in str(statement).lower():
            result.scalar_one_or_none.return_value = request
        else:
            result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=execute)
    return session


def _local_service(
    tmp_path: Path,
    *,
    with_narration: bool = False,
    brand_asset_resolver=None,
):
    video_renderer = AsyncMock()

    async def render_clip(*args, **kwargs):
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_MP4 + b"neutral-scene")
        return VisualV2VideoRenderResult(
            output_path=output,
            scene_index=1,
            template_id="HERO_TITLE",
            width=1920,
            height=1080,
            fps=12,
            duration_seconds=6.0,
            frame_count=72,
            video_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
            source_html_sha256="a" * 64,
            motion_profile="none",
        )

    video_renderer.render_clip.side_effect = render_clip
    ffmpeg = AsyncMock()

    async def concatenate(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(VALID_MP4 + b"neutral-final")

    ffmpeg.concatenate_clips.side_effect = concatenate

    async def write_muxed(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(VALID_MP4 + b"muxed")

    async def write_subtitled(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(VALID_MP4 + b"subtitled")

    ffmpeg.mux_video_audio.side_effect = write_muxed
    ffmpeg.burn_ass_subtitles.side_effect = write_subtitled
    browser_context = MagicMock()
    browser_context.__aenter__ = AsyncMock(return_value=MagicMock())
    browser_context.__aexit__ = AsyncMock(return_value=None)
    narration_provider = None
    narration_storage = None
    if with_narration:
        audio_path = tmp_path / "narration.wav"
        audio_path.write_bytes(b"fake-wave")
        narration_provider = AsyncMock()
        narration_provider.model = "test-model"
        narration_provider.default_voice = "test-voice"
        narration_provider.synthesize_segment_audio.return_value = {
            "storage_uri": "narration.wav",
            "duration_ms": 6000,
            "content_hash": hashlib.sha256(audio_path.read_bytes()).hexdigest(),
            "source_ref": "local-test",
            "narration_quality": "NEURAL_PRODUCTION",
        }
        narration_storage = MagicMock()
        narration_storage.resolve_stored_uri.return_value = audio_path

    service = VisualProductionV2Service(
        asset_orchestrator=None,
        output_root=tmp_path,
        browser_runtime_factory=lambda: browser_context,
        video_renderer=video_renderer,
        ffmpeg_renderer=ffmpeg,
        narration_provider=narration_provider,
        narration_storage=narration_storage,
        brand_asset_resolver=brand_asset_resolver,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    captured_scripts = []

    def storyboard(script_dict, pacing="BALANCED"):
        captured_scripts.append(script_dict)
        return StoryboardPlan(
            title=script_dict["title"],
            estimated_duration_seconds=6,
            scenes=[
                StoryboardScene(
                    sequence_index=1,
                    section_id="body",
                    purpose="neutral",
                    source_statement_references=[1],
                    narration_excerpt="Pinned body statement",
                    estimated_duration_seconds=6,
                    visual_strategy=VisualStrategy.TITLE_MOTION,
                    visual_brief="neutral",
                )
            ],
        )

    service._storyboard_engine.generate_storyboard = MagicMock(side_effect=storyboard)
    return service, video_renderer, captured_scripts, narration_provider


@pytest.mark.asyncio
async def test_interactive_neutral_render_uses_exact_pins_and_cache_parity(tmp_path):
    request, pinned, latest, contract = _interactive_fixture()
    service, video_renderer, captured_scripts, _ = _local_service(tmp_path)
    session = _session_for(request)

    fresh = await service.render_canonical_production(session, request.id, contract)
    cached = await service.render_canonical_production(session, request.id, contract)

    assert fresh == cached
    assert fresh.production_request_id == request.id
    assert fresh.channel_id == request.channel_id
    assert fresh.channel_dna_revision_id == request.channel_dna_revision_id
    assert fresh.script_version_id == pinned.id
    assert fresh.script_version_id != latest.id
    assert fresh.mission_id is None
    assert fresh.mission_execution_id is None
    assert fresh.subtitle_semantics_version == 3
    assert fresh.requested_subtitle_mode == "OFF"
    assert fresh.runtime_subtitle_cues == ()
    assert captured_scripts[0]["title"] == "Pinned script"
    assert video_renderer.render_clip.await_count == 1

    snapshot = build_production_runtime_truth_snapshot(
        contract=contract,
        render_plan_id=uuid.uuid4(),
        render_job_id=uuid.uuid4(),
        media_artifact_id=uuid.uuid4(),
        artifact_version=1,
        artifact_storage_uri="artifact.mp4",
        artifact_size_bytes=fresh.output_path.stat().st_size,
        artifact_sha256=fresh.content_sha256,
        v2_result=fresh,
        probe_summary={
            "duration_ms": int(fresh.duration_seconds * 1000),
            "width": fresh.width,
            "height": fresh.height,
            "fps": fresh.fps,
            "video_codec": "h264",
            "audio_codec": "aac",
            "has_audio": False,
        },
    )
    assert snapshot.lineage.production_request_id == request.id
    assert snapshot.lineage.mission_id is None
    assert snapshot.lineage.mission_execution_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [SubtitleMode.STANDARD, SubtitleMode.KARAOKE])
async def test_neutral_subtitle_modes_synthesize_once_and_cache(mode, tmp_path):
    request, _, _, contract = _interactive_fixture()
    contract = contract.model_copy(
        update={"policy": contract.policy.model_copy(update={"subtitle_mode": mode})}
    )
    service, _, _, narration_provider = _local_service(tmp_path, with_narration=True)
    session = _session_for(request)

    fresh = await service.render_canonical_production(session, request.id, contract)
    cached = await service.render_canonical_production(session, request.id, contract)

    assert fresh == cached
    assert fresh.requested_subtitle_mode == mode.value
    assert fresh.effective_subtitle_mode == mode.value
    assert fresh.subtitle_timing_source == "DERIVED_SEGMENT_TIMING"
    assert fresh.runtime_subtitle_cues
    assert fresh.subtitle_semantics_version == 3
    narration_provider.synthesize_segment_audio.assert_awaited_once_with(
        channel_id=request.channel_id,
        request_id=request.content_request_id,
        segment={"text": "Pinned body statement"},
        voice_profile={"voice": "canonical-voice"},
    )


def test_storyboard_adapter_preserves_hook_closing_and_cta():
    adapted = ScriptStoryboardAdapter.to_script_dict(_script(1, "Script"))

    statements = [
        statement["statement_text"]
        for section in adapted["sections"]
        for statement in section["statements"]
    ]
    assert statements == [
        "Pinned hook",
        "Pinned body statement",
        "Pinned closing",
        "Pinned CTA",
    ]


@pytest.mark.asyncio
async def test_mission_wrapper_delegates_to_public_neutral_entrypoint(tmp_path):
    service = VisualProductionV2Service(
        asset_orchestrator=None,
        output_root=tmp_path,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    execution_id = uuid.uuid4()
    content_request_id = uuid.uuid4()
    production_request_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    script_version_id = uuid.uuid4()
    dna_revision_id = uuid.uuid4()
    mission = MagicMock(id=uuid.uuid4(), channel_id=channel_id)
    mission_execution = MagicMock(id=execution_id, mission=mission)
    content_request = MagicMock(
        id=content_request_id,
        mission_execution_id=execution_id,
        channel_id=channel_id,
    )
    production_request = MagicMock(
        id=production_request_id,
        mission_execution_id=execution_id,
        content_request_id=content_request_id,
        channel_id=channel_id,
        content_request=content_request,
    )
    contract = CanonicalProductionContract(
        mode=ProductionMode.MISSION_EXECUTION,
        lineage=CanonicalProductionLineage(
            channel_id=channel_id,
            production_request_id=production_request_id,
            content_request_id=content_request_id,
            script_version_id=script_version_id,
            channel_dna_revision_id=dna_revision_id,
            mission_id=mission.id,
            mission_execution_id=execution_id,
        ),
        policy=CanonicalProductionPolicy(
            visual_asset_mode=VisualAssetMode.LOCAL_TEMPLATE_ONLY,
            narration_provider=NarrationProviderType.LOCAL_TTS,
            subtitle_mode=SubtitleMode.OFF,
            subtitle_fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
            subtitle_style=SubtitleRenderStyle(),
        ),
    )
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = mission_execution
    request_result = MagicMock()
    request_result.scalar_one_or_none.return_value = production_request
    session = AsyncMock()
    session.execute.side_effect = [exec_result, request_result]
    service.render_canonical_production = AsyncMock(return_value="result")

    result = await service.render_mission_execution(
        session,
        execution_id,
        content_request_id,
        contract=contract,
        production_request_id=production_request_id,
    )

    assert result == "result"
    service.render_canonical_production.assert_awaited_once_with(
        session=session,
        production_request_id=production_request_id,
        contract=contract,
        mission_id=mission.id,
        mission_execution_id=execution_id,
        background_music=None,
        sfx_inputs=None,
        audio_mix_enabled=False,
        style_profile=None,
    )


def test_unsupported_canonical_target_fails_closed():
    request, _, _, contract = _interactive_fixture()
    unsupported = contract.model_copy(
        update={"policy": contract.policy.model_copy(update={"target_width": 1280})}
    )

    with pytest.raises(VerticalSliceError, match="unsupported resolution"):
        VisualProductionV2Service._validate_canonical_target(unsupported)
    assert request.mode == ProductionMode.INTERACTIVE.value


def test_subtitle_semantics_version_is_three():
    assert SUBTITLE_SEMANTICS_VERSION == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mission_first", [True, False])
async def test_mission_and_neutral_share_pinned_physical_cache(
    tmp_path, mission_first
):
    request, pinned, latest, contract = _interactive_fixture()
    execution_id = uuid.uuid4()
    mission = MagicMock(id=uuid.uuid4(), channel_id=request.channel_id)
    execution_dna = MagicMock(id=uuid.uuid4(), snapshot={"brand_name": "wrong"})
    mission_execution = MagicMock(
        id=execution_id,
        mission=mission,
        channel_dna_revision=execution_dna,
        channel_dna_revision_id=execution_dna.id,
    )
    request.mode = ProductionMode.MISSION_EXECUTION.value
    request.mission_execution_id = execution_id
    request.mission_execution = mission_execution
    request.content_request.mission_execution_id = execution_id
    mission_contract = contract.model_copy(
        update={
            "mode": ProductionMode.MISSION_EXECUTION,
            "lineage": contract.lineage.model_copy(
                update={
                    "mission_id": mission.id,
                    "mission_execution_id": execution_id,
                }
            ),
        }
    )
    session = AsyncMock()

    async def execute(statement):
        result = MagicMock()
        if "mission_executions" in str(statement).lower():
            result.scalar_one_or_none.return_value = mission_execution
        else:
            result.scalar_one_or_none.return_value = request
        return result

    session.execute.side_effect = execute
    service, video_renderer, captured_scripts, _ = _local_service(tmp_path)

    async def render_mission():
        return await service.render_mission_execution(
            session,
            execution_id,
            request.content_request_id,
            contract=mission_contract,
            production_request_id=request.id,
        )

    async def render_neutral():
        return await service.render_canonical_production(
            session,
            request.id,
            mission_contract,
        )

    if mission_first:
        mission_result = await render_mission()
        neutral_result = await render_neutral()
    else:
        neutral_result = await render_neutral()
        mission_result = await render_mission()

    assert mission_result.run_fingerprint == neutral_result.run_fingerprint
    assert mission_result.script_version_id == pinned.id
    assert mission_result.script_version_id != latest.id
    assert mission_result.channel_dna_revision_id == request.channel_dna_revision_id
    assert mission_result.channel_dna_revision_id != execution_dna.id
    assert mission_result.mission_id == mission.id
    assert mission_result.mission_execution_id == execution_id
    assert neutral_result.mission_id is None
    assert neutral_result.mission_execution_id is None
    assert mission_result.runtime_scenes == neutral_result.runtime_scenes
    assert mission_result.runtime_narration_segments == neutral_result.runtime_narration_segments
    assert mission_result.runtime_subtitle_cues == neutral_result.runtime_subtitle_cues
    assert mission_result.runtime_branding == neutral_result.runtime_branding
    assert mission_result.width == neutral_result.width
    assert mission_result.height == neutral_result.height
    assert mission_result.fps == neutral_result.fps
    assert (
        mission_result.subtitle_semantics_version
        == neutral_result.subtitle_semantics_version
        == 3
    )
    assert captured_scripts[0]["title"] == "Pinned script"
    assert video_renderer.render_clip.await_count == 1


@pytest.mark.asyncio
async def test_physical_contract_change_invalidates_neutral_cache(tmp_path):
    request, _, _, contract = _interactive_fixture()
    service, video_renderer, _, narration = _local_service(
        tmp_path, with_narration=True
    )
    session = _session_for(request)

    first = await service.render_canonical_production(session, request.id, contract)
    changed = contract.model_copy(
        update={
            "policy": contract.policy.model_copy(
                update={"subtitle_mode": SubtitleMode.STANDARD}
            )
        }
    )
    second = await service.render_canonical_production(session, request.id, changed)

    assert first.run_fingerprint != second.run_fingerprint
    assert video_renderer.render_clip.await_count == 2
    assert narration.synthesize_segment_audio.await_count == 2


@pytest.mark.asyncio
async def test_canonical_brand_switches_gate_physical_application(tmp_path):
    request, _, _, contract = _interactive_fixture()
    logo_path = tmp_path / "logo.png"
    intro_path = tmp_path / "intro.mp4"
    outro_path = tmp_path / "outro.mp4"
    logo_path.write_bytes(b"logo")
    intro_path.write_bytes(VALID_MP4 + b"intro")
    outro_path.write_bytes(VALID_MP4 + b"outro")
    request.channel_dna_revision.snapshot = {
        "brand_package": {
            "logo_asset": {
                "reference": "brand://channel/logo.png",
                "content_hash": "a" * 64,
                "mime_type": "image/png",
            },
            "intro_asset": {
                "reference": "brand://channel/intro.mp4",
                "content_hash": "b" * 64,
                "mime_type": "video/mp4",
            },
            "outro_asset": {
                "reference": "brand://channel/outro.mp4",
                "content_hash": "c" * 64,
                "mime_type": "video/mp4",
            },
            "channel_bug": {"enabled": True},
            "long_form": {
                "micro_intro_enabled": True,
                "channel_bug_enabled": True,
                "branded_outro_enabled": True,
            },
        }
    }
    resolver = MagicMock()
    resolver.resolve_optional.side_effect = [
        ResolvedBrandAsset(
            local_path=logo_path,
            content_hash="a" * 64,
            media_kind=BrandMediaKind.IMAGE,
            mime_type="image/png",
            reference="brand://channel/logo.png",
        ),
        ResolvedBrandAsset(
            local_path=intro_path,
            content_hash="b" * 64,
            media_kind=BrandMediaKind.VIDEO,
            mime_type="video/mp4",
            reference="brand://channel/intro.mp4",
        ),
        ResolvedBrandAsset(
            local_path=outro_path,
            content_hash="c" * 64,
            media_kind=BrandMediaKind.VIDEO,
            mime_type="video/mp4",
            reference="brand://channel/outro.mp4",
        ),
    ]
    service, _, _, _ = _local_service(
        tmp_path / "disabled", brand_asset_resolver=resolver
    )
    disabled = contract.model_copy(
        update={
            "policy": contract.policy.model_copy(
                update={
                    "channel_bug_enabled": False,
                    "intro_enabled": False,
                    "outro_enabled": False,
                }
            )
        }
    )

    result = await service.render_canonical_production(
        _session_for(request), request.id, disabled
    )

    service._ffmpeg_renderer.overlay_logo.assert_not_awaited()
    assert service._ffmpeg_renderer.concatenate_clips.await_count == 1
    assert {item["role"]: item["applied"] for item in result.runtime_branding["assets"]} == {
        "CHANNEL_BUG": False,
        "INTRO": False,
        "OUTRO": False,
    }
    assert result.runtime_branding["canonical_policy"] == {
        "channel_bug_enabled": False,
        "intro_enabled": False,
        "outro_enabled": False,
        "brand_spec_reference": None,
    }


@pytest.mark.asyncio
async def test_unsupported_brand_spec_reference_fails_closed(tmp_path):
    request, _, _, contract = _interactive_fixture()
    service, video_renderer, _, _ = _local_service(tmp_path)
    unsupported = contract.model_copy(
        update={
            "policy": contract.policy.model_copy(
                update={"brand_spec_reference": "brand-spec://unsupported"}
            )
        }
    )

    with pytest.raises(VerticalSliceError, match="brand_spec_reference is unsupported"):
        await service.render_canonical_production(
            _session_for(request), request.id, unsupported
        )
    video_renderer.render_clip.assert_not_awaited()
