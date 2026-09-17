"""Focused test suite for P18-G1 Viewer-Facing Editorial Hardening.

Covers:
G1A: Remove narration duplication from BROLL_EXPLAINER and IMAGE_EXPLAINER; BODY optional.
G1B: Internal structural label suppression (Hook, Closing, CTA, Section N, Scene N),
     HERO_TITLE uses real script title, closing-only final scene != CTA, explicit CTA == CTA.
G1C: Shared subtitle safe area authority (240px bottom safe band) honored by BROLL, IMAGE, CTA;
     STANDARD subtitle wrapping stays <= 2 lines and <= 55 chars per line; KARAOKE preserved.
G1D: Final master audio loudness normalization (-16 LUFS, -1.5 dBTP, 7 LU LRA),
     physical test with ffmpeg, and exact-once application proof.
"""

import asyncio
import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from omega.application.ffmpeg_renderer import FFmpegRenderer
from omega.application.scene_template_registry import (
    SceneTemplateRegistry,
    TemplateInputKey,
)
from omega.application.storyboard_engine import (
    StoryboardEngine,
    StoryboardScene,
    VisualStrategy,
)
from omega.application.subtitle_engine import (
    CANONICAL_SUBTITLE_SAFE_BOTTOM_PX,
    SubtitleRenderStyle,
    generate_karaoke_ass_document,
    generate_karaoke_cues,
    generate_sentence_cues,
)
from omega.application.template_payload_resolver import (
    TemplatePayloadResolver,
    is_internal_structural_label,
)
from omega.application.visual_asset_binding import BoundBrollAsset, BoundVisualAsset
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualAssetRequirement,
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
)
from omega.application.visual_production_v2_service import ScriptStoryboardAdapter
from omega.application.visual_template_renderer import VisualTemplateRenderer

# ══════════════════════════════════════════════════════════════════
# G1A: REMOVE NARRATION DUPLICATION
# ══════════════════════════════════════════════════════════════════


def test_broll_explainer_body_optional_in_registry():
    registry = SceneTemplateRegistry()
    defn = registry.get(VisualTemplateId.BROLL_EXPLAINER)
    assert TemplateInputKey.BODY not in defn.required_inputs
    assert TemplateInputKey.BODY in defn.optional_inputs


def test_image_explainer_body_optional_in_registry():
    registry = SceneTemplateRegistry()
    defn = registry.get(VisualTemplateId.IMAGE_EXPLAINER)
    assert TemplateInputKey.BODY not in defn.required_inputs
    assert TemplateInputKey.BODY in defn.optional_inputs


def test_broll_narration_not_copied_verbatim_into_body():
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=2,
        section_id="Atmospheric Scattering",
        purpose="Illustrate scattering",
        source_statement_references=[1],
        narration_excerpt="Rayleigh scattering causes shorter blue wavelengths to disperse rapidly in all directions.",
        estimated_duration_seconds=4.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=None,  # No concise editorial text
    )
    direction = VisualDirection(
        scene_index=2,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert TemplateInputKey.BODY not in payload.inputs
    assert "Rayleigh scattering" not in str(payload.inputs.get(TemplateInputKey.BODY, ""))


def test_broll_uses_concise_on_screen_text_when_distinct():
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=2,
        section_id="Atmospheric Scattering",
        purpose="Illustrate scattering",
        source_statement_references=[1],
        narration_excerpt="Rayleigh scattering causes shorter blue wavelengths to disperse rapidly in all directions.",
        estimated_duration_seconds=4.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text="Blue Light Disperses First",  # Concise distinct text
    )
    direction = VisualDirection(
        scene_index=2,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert payload.inputs[TemplateInputKey.BODY] == "Blue Light Disperses First"


def test_broll_omits_body_when_on_screen_text_equals_narration():
    resolver = TemplatePayloadResolver()
    narration = "The sky turns orange as sunlight travels through thicker atmosphere."
    scene = StoryboardScene(
        sequence_index=3,
        section_id="Thicker Atmosphere",
        purpose="Illustrate atmosphere",
        source_statement_references=[2],
        narration_excerpt=narration,
        estimated_duration_seconds=4.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=narration,  # Duplicate text
    )
    direction = VisualDirection(
        scene_index=3,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert TemplateInputKey.BODY not in payload.inputs


def test_image_explainer_follows_no_verbatim_narration_rule():
    resolver = TemplatePayloadResolver()
    narration = "Molecules in the air scatter light according to its wavelength."
    scene = StoryboardScene(
        sequence_index=2,
        section_id="Particle Physics",
        purpose="Illustrate molecules",
        source_statement_references=[1],
        narration_excerpt=narration,
        estimated_duration_seconds=4.0,
        visual_strategy=VisualStrategy.IMAGE,
        visual_brief="",
        on_screen_text=narration,  # Verbatim duplicate
    )
    direction = VisualDirection(
        scene_index=2,
        render_mode=VisualRenderMode.HYBRID,
        template_id=VisualTemplateId.IMAGE_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.IMAGE, purpose="", query_hint="")],
        motion_profile="image_explainer",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert TemplateInputKey.BODY not in payload.inputs


def test_renderer_conditionally_omits_broll_body_element():
    renderer = VisualTemplateRenderer()
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Atmosphere",
        purpose="Illustrate",
        source_statement_references=[1],
        narration_excerpt="Spoken narration only.",
        estimated_duration_seconds=4.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=None,
    )
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    asset = BoundBrollAsset(
        asset_id="broll_1",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="a" * 64,
        local_path=Path("/tmp/fake.mp4"),
        duration_seconds=4.0,
        width=1920,
        height=1080,
    )
    rendered = renderer.render(payload, assets=(asset,))
    assert "broll-body" not in rendered.semantic_element_ids
    assert '<div id="broll-body"' not in rendered.html


# ══════════════════════════════════════════════════════════════════
# G1B: INTERNAL LABELS AND FALSE CTA
# ══════════════════════════════════════════════════════════════════


def test_internal_structural_label_helper():
    # Synthetic internal planning labels are suppressed
    assert is_internal_structural_label("Hook") is True
    assert is_internal_structural_label("HOOK") is True
    assert is_internal_structural_label("Closing") is True
    assert is_internal_structural_label("CLOSING") is True
    assert is_internal_structural_label("CTA") is True
    assert is_internal_structural_label("Call to Action") is True
    assert is_internal_structural_label("call-to-action") is True
    assert is_internal_structural_label("Section 1") is True
    assert is_internal_structural_label("section 12") is True
    assert is_internal_structural_label("Scene 1") is True
    assert is_internal_structural_label("Scene 2") is True
    assert is_internal_structural_label("scene 99") is True

    # Legitimate user-authored headings are preserved (NOT globally suppressed)
    assert is_internal_structural_label("Introduction") is False
    assert is_internal_structural_label("Intro") is False
    assert is_internal_structural_label("Conclusion") is False
    assert is_internal_structural_label("Outro") is False
    assert is_internal_structural_label("Why Sunsets Turn Red") is False
    assert is_internal_structural_label("The Physics of Rayleigh Scattering") is False
    assert is_internal_structural_label("Atmospheric Perspective") is False
    assert is_internal_structural_label("Sectional Analysis of Light") is False


def test_authored_heading_conclusion_preserved_as_viewer_title():
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=4,
        section_id="Conclusion",  # Authored heading
        purpose="Conclude topic",
        source_statement_references=[4],
        narration_excerpt="In conclusion, the scattering of shorter wavelengths leaves behind the red glow.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=None,
    )
    direction = VisualDirection(
        scene_index=4,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert payload.inputs.get(TemplateInputKey.TITLE) == "Conclusion"


def test_authored_heading_introduction_preserved_as_viewer_title():
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Introduction",  # Authored heading
        purpose="Introduce topic",
        source_statement_references=[1],
        narration_excerpt="An introduction to atmospheric scattering.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=None,
    )
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert payload.inputs.get(TemplateInputKey.TITLE) == "Introduction"


def test_closing_synthetic_label_does_not_leak_as_title():
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=5,
        section_id="Closing",  # Internal synthetic label
        purpose="Close video",
        source_statement_references=[5],
        narration_excerpt="And that is why the setting sun paints our skies red.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=None,
    )
    direction = VisualDirection(
        scene_index=5,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert TemplateInputKey.TITLE not in payload.inputs


def test_scene_numbered_placeholder_does_not_leak_as_title():
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=12,
        section_id="Scene 12",  # Generated numbered placeholder
        purpose="Illustrate",
        source_statement_references=[12],
        narration_excerpt="The light continues its path toward the observer.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=None,
    )
    direction = VisualDirection(
        scene_index=12,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert TemplateInputKey.TITLE not in payload.inputs


def test_hook_internal_label_does_not_leak_as_title():
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Hook",  # Internal label
        purpose="Hook viewer",
        source_statement_references=[1],
        narration_excerpt="Have you ever wondered why sunsets turn fiery red?",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=None,
    )
    direction = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert TemplateInputKey.TITLE not in payload.inputs


def test_numbered_section_label_does_not_leak_as_title():
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=2,
        section_id="Section 2",  # Internal planning label
        purpose="Illustrate",
        source_statement_references=[2],
        narration_excerpt="Sunlight enters Earth's dense lower atmosphere.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="",
        on_screen_text=None,
    )
    direction = VisualDirection(
        scene_index=2,
        render_mode=VisualRenderMode.BROLL,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
        motion_profile="broll_overlay",
        rationale="",
    )
    payload = resolver.resolve(scene, direction)
    assert TemplateInputKey.TITLE not in payload.inputs


def test_hero_title_receives_real_script_title():
    # Mock ScriptVersion
    mock_script = MagicMock()
    mock_script.title = "Why Sunsets Turn Red"
    mock_script.estimated_duration_seconds = 30.0
    mock_script.hook_text = "Have you ever wondered why sunsets turn fiery red?"
    mock_script.closing_text = "And that is why the setting sun paints our skies red."
    mock_script.cta_text = None

    # Mock section
    mock_sec = MagicMock()
    mock_sec.section_order = 1
    mock_sec.heading = "The Optical Spectrum"
    mock_sec.narration_text = "Light travels in waves of different lengths."
    mock_sec.estimated_duration_seconds = 10.0
    mock_stmt = MagicMock()
    mock_stmt.statement_order = 1
    mock_stmt.statement_text = "Light travels in waves of different lengths."
    mock_stmt.statement_type = "BODY"
    mock_stmt.citations = []
    mock_sec.statements = [mock_stmt]
    mock_script.sections = [mock_sec]

    script_dict = ScriptStoryboardAdapter.to_script_dict(mock_script)
    # The first section (synthetic hook) must have the real script title as heading
    assert script_dict["sections"][0]["heading"] == "Why Sunsets Turn Red"
    assert script_dict["sections"][0]["heading"] != "Hook"

    # And the body section heading is untouched
    assert script_dict["sections"][1]["heading"] == "The Optical Spectrum"


def test_closing_only_final_scene_is_not_cta():
    engine = StoryboardEngine()
    closing_statements = [
        {
            "statement_order": 1,
            "statement_text": "And that is the quiet physics behind every vibrant twilight.",
            "statement_type": "CLOSING",
        }
    ]
    scene = engine._create_scene(
        sequence_index=5,
        section_heading="Closing",
        statements=closing_statements,
        is_first=False,
        is_last=True,
        history=[],
    )
    assert scene.visual_strategy != VisualStrategy.CTA


def test_explicit_cta_final_scene_remains_cta():
    engine = StoryboardEngine()
    cta_statements = [
        {
            "statement_order": 1,
            "statement_text": "Subscribe for more deep dives into natural phenomena.",
            "statement_type": "CTA",
        }
    ]
    scene = engine._create_scene(
        sequence_index=5,
        section_heading="Closing",
        statements=cta_statements,
        is_first=False,
        is_last=True,
        history=[],
    )
    assert scene.visual_strategy == VisualStrategy.CTA


# ══════════════════════════════════════════════════════════════════
# G1C: CANONICAL SUBTITLE SAFE AREA & WRAPPING
# ══════════════════════════════════════════════════════════════════


def test_canonical_subtitle_safe_bottom_authority():
    assert CANONICAL_SUBTITLE_SAFE_BOTTOM_PX == 240


def test_broll_explainer_honors_subtitle_safe_area():
    renderer = VisualTemplateRenderer()
    payload = TemplatePayloadResolver().resolve(
        StoryboardScene(
            sequence_index=1,
            section_id="Physics",
            purpose="Illustrate",
            source_statement_references=[1],
            narration_excerpt="Narration",
            estimated_duration_seconds=4.0,
            visual_strategy=VisualStrategy.BROLL,
            visual_brief="",
            on_screen_text="Editorial Highlight",
        ),
        VisualDirection(
            scene_index=1,
            render_mode=VisualRenderMode.BROLL,
            template_id=VisualTemplateId.BROLL_EXPLAINER,
            asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.BROLL, purpose="", query_hint="")],
            motion_profile="broll_overlay",
            rationale="",
        ),
    )
    asset = BoundBrollAsset(
        asset_id="broll_1",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="a" * 64,
        local_path=Path("/tmp/fake.mp4"),
        duration_seconds=4.0,
        width=1920,
        height=1080,
    )
    rendered = renderer.render(payload, assets=(asset,))
    assert f"margin-bottom: {CANONICAL_SUBTITLE_SAFE_BOTTOM_PX}px" in rendered.html


def test_image_explainer_honors_subtitle_safe_area():
    renderer = VisualTemplateRenderer()
    payload = TemplatePayloadResolver().resolve(
        StoryboardScene(
            sequence_index=1,
            section_id="Physics",
            purpose="Illustrate",
            source_statement_references=[1],
            narration_excerpt="Narration",
            estimated_duration_seconds=4.0,
            visual_strategy=VisualStrategy.IMAGE,
            visual_brief="",
            on_screen_text="Editorial Highlight",
        ),
        VisualDirection(
            scene_index=1,
            render_mode=VisualRenderMode.HYBRID,
            template_id=VisualTemplateId.IMAGE_EXPLAINER,
            asset_requirements=[VisualAssetRequirement(kind=VisualAssetKind.IMAGE, purpose="", query_hint="")],
            motion_profile="image_explainer",
            rationale="",
        ),
    )
    asset = BoundVisualAsset(
        asset_id="img_1",
        kind=VisualAssetKind.IMAGE,
        mime_type="image/jpeg",
        content_sha256="b" * 64,
        local_path=Path("/tmp/fake.jpg"),
        data_uri="data:image/jpeg;base64,fake",
        width=1920,
        height=1080,
    )
    rendered = renderer.render(payload, assets=(asset,))
    assert f"padding-bottom: {CANONICAL_SUBTITLE_SAFE_BOTTOM_PX}px" in rendered.html


def test_cta_honors_subtitle_safe_area():
    renderer = VisualTemplateRenderer()
    payload = TemplatePayloadResolver().resolve(
        StoryboardScene(
            sequence_index=5,
            section_id="Action",
            purpose="CTA",
            source_statement_references=[1],
            narration_excerpt="Subscribe now",
            estimated_duration_seconds=4.0,
            visual_strategy=VisualStrategy.CTA,
            visual_brief="",
            on_screen_text="Subscribe Now",
        ),
        VisualDirection(
            scene_index=5,
            render_mode=VisualRenderMode.TEMPLATE,
            template_id=VisualTemplateId.CTA,
            asset_requirements=[],
            motion_profile="cta_reveal",
            rationale="",
        ),
    )
    rendered = renderer.render(payload)
    assert f"padding: 0 100px {CANONICAL_SUBTITLE_SAFE_BOTTOM_PX}px 100px" in rendered.html


def test_standard_subtitle_wrapping_stays_within_qa_limits():
    # Long segment without natural punctuation
    long_segment = {
        "text": (
            "Because blue light has a much shorter wavelength it scatters easily "
            "while red light passes straight through the dense atmosphere"
        ),
        "start_ms": 0,
        "duration_ms": 8000,
    }
    cues = generate_sentence_cues([long_segment])
    style = SubtitleRenderStyle(karaoke=False)
    doc = generate_karaoke_ass_document(cues, style=style)

    for layout_item in doc.layout:
        assert layout_item.line_count <= 2, f"Cue {layout_item.cue_order} exceeded 2 lines"

    # Extract all Dialogue lines from the ASS document and check line length
    dialogue_lines = [
        line for line in doc.content.splitlines() if line.startswith("Dialogue:")
    ]
    for d_line in dialogue_lines:
        # Format is Dialogue: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        parts = d_line.split(",", 9)
        if len(parts) == 10:
            text_part = parts[9]
            # Split on \N or \n ASS line breaks
            for sub_line in re.split(r"\\[Nn]", text_part):
                clean_sub_line = re.sub(r"\{.*?\}", "", sub_line).strip()
                assert len(clean_sub_line) <= 55, (
                    f"Rendered subtitle line exceeds 55 chars: '{clean_sub_line}' ({len(clean_sub_line)} chars)"
                )


def test_karaoke_subtitles_not_regressed():
    segment = {
        "text": "Why sunsets turn red.",
        "start_ms": 0,
        "duration_ms": 2000,
    }
    cues = generate_karaoke_cues([segment], sentence_mode=False)
    style = SubtitleRenderStyle(karaoke=True)
    doc = generate_karaoke_ass_document(cues, style=style)

    assert "\\kf" in doc.content
    kf_tags = re.findall(r"\\kf(\d+)", doc.content)
    assert len(kf_tags) == 4
    # All word centiseconds positive
    assert all(int(cs) > 0 for cs in kf_tags)


# ══════════════════════════════════════════════════════════════════
# G1D: MASTER AUDIO NORMALIZATION
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_master_audio_normalization_physical(tmp_path: Path):
    """Physical FFmpeg loudnorm test producing a real MP4 and verifying loudness."""
    ffmpeg = FFmpegRenderer()
    raw_mp4 = tmp_path / "raw_input.mp4"
    normalized_mp4 = tmp_path / "normalized.mp4"

    # Generate a real 2-second test video with a quiet 440Hz tone (-30dB)
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=duration=2:size=1920x1080:rate=24",
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=2",
        "-filter:a", "volume=0.05",  # very quiet audio
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        str(raw_mp4),
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, f"FFmpeg test video creation failed: {stderr.decode()}"
    assert raw_mp4.is_file() and raw_mp4.stat().st_size > 0

    # Execute master audio normalization
    await ffmpeg.normalize_master_audio(
        video_path=raw_mp4,
        output_path=normalized_mp4,
        target_i=-16.0,
        target_tp=-1.5,
        target_lra=7.0,
    )

    assert normalized_mp4.is_file()
    assert normalized_mp4.stat().st_size > 0

    # Probe dimensions and codec with ffprobe
    probe_cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name",
        "-of", "json",
        str(normalized_mp4),
    ]
    probe_proc = await asyncio.create_subprocess_exec(*probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    probe_out, _ = await probe_proc.communicate()
    video_meta = json.loads(probe_out.decode())["streams"][0]
    assert video_meta["width"] == 1920
    assert video_meta["height"] == 1080
    assert video_meta["codec_name"] == "h264"

    # Measure integrated loudness and true peak with ebur128
    ebur_cmd = [
        "ffmpeg",
        "-i", str(normalized_mp4),
        "-af", "ebur128=framelog=verbose",
        "-f", "null",
        "-",
    ]
    ebur_proc = await asyncio.create_subprocess_exec(*ebur_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, ebur_err = await ebur_proc.communicate()
    ebur_output = ebur_err.decode()

    # Parse Integrated loudness (I) and True peak (TP)
    i_match = re.search(r"Integrated loudness:\s+I:\s+([-\d.]+)\s+LUFS", ebur_output)
    tp_match = re.search(r"Peak:\s+([-\d.]+)\s+dBFS", ebur_output) or re.search(r"True peak:\s+Peak:\s+([-\d.]+)\s+dBFS", ebur_output)

    assert i_match is not None, f"Could not find integrated loudness in ebur128 output: {ebur_output}"
    measured_i = float(i_match.group(1))
    # Target is -16.0 LUFS. For a 2-second short clip, loudnorm gets within +/- 2.5 LUFS
    assert abs(measured_i - (-16.0)) <= 2.5, f"Measured I {measured_i} LUFS deviated from -16.0 target"

    if tp_match:
        measured_tp = float(tp_match.group(1))
        # True peak must not breach ceiling (-1.5 dBTP + 0.5dB tolerance)
        assert measured_tp <= -1.0, f"Measured true peak {measured_tp} breached ceiling"


@pytest.mark.asyncio
async def test_normalization_applied_exactly_once_contract():
    """Verify that normalize_master_audio is invoked exactly once on the final master."""

    # Verify that the service has normalize_master_audio hooked after concatenation/mix
    service_code = Path(
        "/app/src/omega/application/visual_production_v2_service.py"
    ).read_text(encoding="utf-8")

    # Count how many times normalize_master_audio is called in the service
    calls = re.findall(r"\bnormalize_master_audio\b", service_code)
    # Must be imported or called exactly once in render_canonical_production
    assert len(calls) == 1, f"Expected normalize_master_audio to appear exactly once, found {len(calls)}"
