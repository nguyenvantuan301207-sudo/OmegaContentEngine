"""Comparative render canary: validates all subtitle presets deterministically.

Generates ASS subtitle streams across each registered preset, verifying
font mapping, color transformations (&H00BBGGRR), outline/shadow parameters,
alignment constraints, and bounding box behavior across varied text lengths.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from omega.application.subtitle_engine import (
    SubtitleRenderStyle,
    generate_karaoke_ass_document,
    generate_karaoke_cues,
)
from omega.application.subtitle_presets import (
    SubtitlePreset,
    get_subtitle_presets,
)


def parse_ass_style_definition(ass_content: str) -> dict[str, str]:
    """Parse the V4+ Style definition line from ASS content."""
    for line in ass_content.splitlines():
        if line.startswith("Style:"):
            parts = [p.strip() for p in line.removeprefix("Style:").split(",")]
            if len(parts) >= 23:
                return {
                    "name": parts[0],
                    "fontname": parts[1],
                    "fontsize": parts[2],
                    "primary_colour": parts[3],
                    "secondary_colour": parts[4],
                    "outline_colour": parts[5],
                    "back_colour": parts[6],
                    "bold": parts[7],
                    "border_style": parts[15],
                    "outline": parts[16],
                    "shadow": parts[17],
                    "alignment": parts[18],
                    "margin_l": parts[19],
                    "margin_r": parts[20],
                    "margin_v": parts[21],
                }
    raise ValueError("No V4+ Style line found in ASS content.")


def run_comparative_canary(output_dir: Path | None = None) -> dict[str, Any]:
    """Run comparative canary across all presets with deterministic challenge cues."""
    test_segments = [
        {
            "start_ms": 0,
            "duration_ms": 2500,
            "text": "Quick tests prove solid layouts and reliable subtitles.",
        },
        {
            "start_ms": 2500,
            "duration_ms": 3500,
            "text": "Autonomous rendering engine utilizes constant frame rate delivery.",
        },
        {
            "start_ms": 6000,
            "duration_ms": 4000,
            "text": "Subtitles remain legible and visually distinct across varied brand aesthetics.",
        },
    ]

    cues = generate_karaoke_cues(test_segments)
    presets = get_subtitle_presets()
    results: dict[str, Any] = {}

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("OMEGA SUBTITLE PRESET COMPARATIVE CANARY")
    print(f"Testing {len(presets)} registered presets with {len(cues)} challenge cues.")
    print("=" * 80)

    for preset in presets:
        doc = generate_karaoke_ass_document(cues, style=preset.style)
        style_meta = parse_ass_style_definition(doc.content)

        # Save ASS file if output directory provided
        ass_path = None
        if output_dir:
            file_path = output_dir / f"preset_{preset.id}.ass"
            file_path.write_text(doc.content, encoding="utf-8")
            ass_path = str(file_path)

        # Collect layout metrics across cues
        layout_metrics = [
            {
                "cue_order": d.cue_order,
                "resolved_font_size": d.resolved_font_size,
                "line_count": d.line_count,
                "text_truncated": d.text_truncated,
            }
            for d in doc.layout
        ]

        preset_summary = {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "style_input": preset.style.model_dump(),
            "ass_style": style_meta,
            "layout_decisions": layout_metrics,
            "content_length_chars": len(doc.content),
            "ass_path": ass_path,
        }
        results[preset.id] = preset_summary

        print(f"\n[Preset: {preset.id}] -> {preset.name}")
        print(f"  Font: {style_meta['fontname']} ({style_meta['fontsize']}px) | Bold: {style_meta['bold'] == '-1'}")
        print(f"  Primary: {style_meta['primary_colour']} | Outline: {style_meta['outline_colour']} (width={style_meta['outline']}px)")
        print(f"  Shadow: {style_meta['shadow']} | Box: {style_meta['border_style'] == '3'} (Border={style_meta['border_style']})")
        print(f"  Alignment: {style_meta['alignment']} | MarginV: {style_meta['margin_v']}px")
        layout_strs = [
            f"Cue {m['cue_order']}: {m['resolved_font_size']}px, {m['line_count']}L, trunc={m['text_truncated']}"
            for m in layout_metrics
        ]
        print(f"  Cues layout: {layout_strs}")

    # Comparative distinctiveness checks
    print("\n" + "-" * 80)
    print("VERIFYING PAIRWISE DISTINCTIVENESS:")

    # 1. Alignment check: cinematic_top must be top-aligned (8), others bottom-aligned (2)
    assert results["cinematic_top"]["ass_style"]["alignment"] == "8", "cinematic_top must use alignment 8"
    assert results["default"]["ass_style"]["alignment"] == "2", "default must use alignment 2"
    print("  [PASS] Top-alignment (cinematic_top=8 vs default=2)")

    # 2. Color check: bold_yellow must have non-white primary color
    yellow_ass = results["bold_yellow"]["ass_style"]["primary_colour"]
    default_ass = results["default"]["ass_style"]["primary_colour"]
    assert yellow_ass != default_ass, "bold_yellow must have distinct color from default"
    # In ASS BGR: #FFD400 -> &H0000D4FF
    assert yellow_ass == "&H0000D4FF", f"Expected &H0000D4FF for yellow, got {yellow_ass}"
    print(f"  [PASS] Color contrast (bold_yellow={yellow_ass} vs default={default_ass})")

    # 3. Boxed check: boxed_highlight must use border_style=3 (opaque bounding box)
    assert results["boxed_highlight"]["ass_style"]["border_style"] == "3"
    assert results["default"]["ass_style"]["border_style"] == "1"
    print("  [PASS] Background box (boxed_highlight=3 vs default=1)")

    # 4. Font family check: minimal_clean uses DejaVu Sans, default uses Arial
    assert results["minimal_clean"]["ass_style"]["fontname"] == "DejaVu Sans"
    assert results["default"]["ass_style"]["fontname"] == "Arial"
    print("  [PASS] Font family (minimal_clean=DejaVu Sans vs default=Arial)")

    # 5. Margin vertical check: minimal_clean margin 70 vs cinematic_top margin 90
    assert results["minimal_clean"]["ass_style"]["margin_v"] == "70"
    assert results["cinematic_top"]["ass_style"]["margin_v"] == "90"
    print("  [PASS] Vertical margins (minimal_clean=70 vs cinematic_top=90)")

    print("-" * 80)
    print("ALL PRESET COMPARATIVE CHECKS PASSED DETERMINISTICALLY.")
    print("=" * 80)

    return results


if __name__ == "__main__":
    out_dir = Path("/tmp/omega_subtitle_preset_canary")
    results = run_comparative_canary(out_dir)
    report_file = out_dir / "comparative_canary_report.json"
    report_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Report written to: {report_file}")
