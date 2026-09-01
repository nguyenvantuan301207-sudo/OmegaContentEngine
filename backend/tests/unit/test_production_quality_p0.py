"""Unit tests for OMEGA Product Quality P0 components: Audio, Subtitles, Visuals, and QA."""

import uuid
from pathlib import Path

import pytest

from omega.application.asset_provider import LocalAssetProvider, generate_scene_card_svg
from omega.application.ffmpeg_renderer import FFmpegRenderer
from omega.application.media_probe import MediaProbe
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import LocalTTSNarrationProvider, clean_text_for_flite
from omega.domain.production import (
    AssetProviderType,
    AssetType,
    SceneType,
)


@pytest.mark.asyncio
async def test_p0_audio_narration_synthesis(tmp_path: Path):
    """Test LocalTTSNarrationProvider generates audible, non-silent speech."""
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    provider = LocalTTSNarrationProvider(storage)
    channel_id = uuid.uuid4()
    request_id = uuid.uuid4()

    segment = {
        "text": "FastAPI 2026 High-Throughput Microservices architecture breakdown.",
        "duration_ms": 3000,
    }

    asset = await provider.synthesize_segment_audio(channel_id, request_id, segment)

    assert asset["id"] is not None
    assert asset["asset_type"] == AssetType.AUDIO.value
    assert asset["provider_type"] == AssetProviderType.SYSTEM.value
    assert "Local TTS" in asset["source_ref"]
    assert asset["content_hash"] is not None

    file_path = storage.resolve_stored_uri(channel_id, request_id, asset["storage_uri"])
    assert file_path.is_file()
    assert file_path.stat().st_size > 0

    # Probe volume
    probe = MediaProbe()
    vol = await probe.detect_audio_volume(file_path)
    if vol.get("mean_volume_db") is not None:
        assert vol["mean_volume_db"] > -50.0  # Not digital silence


def test_p0_clean_text_for_flite():
    """Test text cleaning for flite filter."""
    text = "Hello world! This is a 100% test with special chars: @#$% & *."
    clean = clean_text_for_flite(text)
    assert "Hello world" in clean
    assert "@" not in clean
    assert "$" not in clean


@pytest.mark.asyncio
async def test_p0_visual_scene_card_generation(tmp_path: Path):
    """Test LocalAssetProvider generates rich visual scene cards for various scene types."""
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    provider = LocalAssetProvider(storage)
    channel_id = uuid.uuid4()
    request_id = uuid.uuid4()

    # Test TITLE scene
    req_title = {
        "id": uuid.uuid4(),
        "purpose": "Title Hook",
        "scene_type": SceneType.TITLE.value,
        "title": "FastAPI Architecture 2026",
        "heading": "SYSTEM DESIGN",
        "narration_text": "Is FastAPI the right architectural choice?",
        "visual_intent": "High impact title card",
    }
    asset_title = await provider.resolve_asset_requirement(channel_id, request_id, req_title)
    assert asset_title["provider_type"] == AssetProviderType.SYSTEM.value
    assert "Scene Card" in asset_title["source_ref"]

    title_path = storage.resolve_stored_uri(channel_id, request_id, asset_title["storage_uri"])
    assert title_path.is_file()
    assert title_path.stat().st_size > 1000  # Non-trivial PNG

    # Test STATISTIC scene
    req_stat = {
        "id": uuid.uuid4(),
        "purpose": "Metric Benchmark",
        "scene_type": SceneType.STATISTIC.value,
        "title": "FastAPI Architecture 2026",
        "heading": "BENCHMARKS",
        "narration_text": "Microservices achieve 50k req/s using asyncpg.",
        "visual_intent": "Infographic benchmark display",
    }
    asset_stat = await provider.resolve_asset_requirement(channel_id, request_id, req_stat)
    assert asset_stat["provider_type"] == AssetProviderType.SYSTEM.value

    # Test CTA scene
    req_cta = {
        "id": uuid.uuid4(),
        "purpose": "Closing CTA",
        "scene_type": SceneType.CTA.value,
        "title": "FastAPI Architecture 2026",
        "heading": "SUMMARY",
        "narration_text": "Subscribe for weekly architecture deep dives.",
        "visual_intent": "Outro summary and CTA",
    }
    asset_cta = await provider.resolve_asset_requirement(channel_id, request_id, req_cta)
    assert asset_cta["provider_type"] == AssetProviderType.SYSTEM.value


def test_p0_generate_scene_card_svg_markup():
    """Test SVG markup contains expected tags and text."""
    svg = generate_scene_card_svg(
        scene_type="STATISTIC",
        title="FastAPI Masterclass",
        heading="BENCHMARKS",
        text="FastAPI services hit 50k req/s throughput.",
        visual_intent="Empirical data display",
        theme_idx=0,
    )
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "BENCHMARKS" in svg
    assert "50k req/s" in svg or "FastAPI" in svg


@pytest.mark.asyncio
async def test_p0_subtitle_burnin_render(tmp_path: Path):
    """Test FFmpegRenderer burns in subtitles when srt_path is provided."""
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    asset_provider = LocalAssetProvider(storage)
    narr_provider = LocalTTSNarrationProvider(storage)
    renderer = FFmpegRenderer()

    channel_id = uuid.uuid4()
    request_id = uuid.uuid4()

    # Generate scene 1 image and audio
    req = {
        "id": uuid.uuid4(),
        "purpose": "Title Hook",
        "scene_type": "TITLE",
        "title": "FastAPI 2026",
        "heading": "OVERVIEW",
        "narration_text": "FastAPI 2026 High-Throughput Microservices",
        "visual_intent": "Title card",
    }
    img_asset = await asset_provider.resolve_asset_requirement(channel_id, request_id, req)
    img_path = storage.resolve_stored_uri(channel_id, request_id, img_asset["storage_uri"])

    aud_asset = await narr_provider.synthesize_segment_audio(
        channel_id, request_id, {"text": "FastAPI 2026 High-Throughput Microservices", "duration_ms": 2500}
    )
    aud_path = storage.resolve_stored_uri(channel_id, request_id, aud_asset["storage_uri"])

    # Render scene clip
    scene_clip = tmp_path / "scene_1.mp4"
    await renderer.render_scene_clip(
        image_path=img_path,
        audio_path=aud_path,
        output_path=scene_clip,
        duration_sec=2.5,
    )
    assert scene_clip.is_file()

    # Generate SRT file
    srt_path = tmp_path / "subs.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,500\nFastAPI 2026 High-Throughput Microservices\n",
        encoding="utf-8",
    )

    # Concatenate / burn in subtitles
    final_output = tmp_path / "final_burned.mp4"
    await renderer.concatenate_clips(
        clip_paths=[scene_clip],
        output_path=final_output,
        srt_path=srt_path,
    )
    assert final_output.is_file()
    assert final_output.stat().st_size > 0

    # Probe final output
    probe = MediaProbe()
    summary = await probe.probe_file(final_output)
    assert summary["has_video"] is True
    assert summary["has_audio"] is True
    assert summary["duration_ms"] > 0
    if summary.get("mean_volume_db") is not None:
        assert summary["mean_volume_db"] > -50.0
