import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type {
  ProductionRenderCapabilities,
  ProductionRequest,
  SubtitleRenderStyle,
} from "../src/lib/api.ts";
import {
  hasUnsupportedSubtitleFields,
  resolveArtifactProvenance,
  resolveSubtitleDraft,
  subtitleHorizontalAlignment,
  subtitleVerticalAlignment,
} from "../src/lib/subtitle-render-ui.ts";

const defaults: SubtitleRenderStyle = {
  font_family: "Arial",
  font_size: 48,
  min_font_size: 32,
  bold: false,
  primary_color: "#FFFFFF",
  outline_color: "#000000",
  outline_width: 2,
  shadow: 2,
  background_box: false,
  alignment: 2,
  margin_v: 80,
  max_lines: 2,
  max_width_ratio: 0.82,
};

const capabilities: ProductionRenderCapabilities = {
  truth_states: ["RENDER_APPLIED", "PREVIEW_ONLY", "UNSUPPORTED"],
  subtitle: {
    defaults,
    fields: Object.keys(defaults).map((name) => ({
      name: name as keyof SubtitleRenderStyle,
      truth_state: "RENDER_APPLIED",
      user_editable: name !== "min_font_size",
    })),
    font_families: ["Arial", "DejaVu Sans"],
    alignments: { "2": "Bottom center", "5": "Center" },
  },
  text_fitting: {
    wrap: true,
    font_downscale: true,
    truncation_fallback: true,
    truncation_provenance: true,
  },
  video: { fps_mode: "CFR", target_fps: 12, user_editable: false },
  subtitle_timing_label: "Estimated word timing",
};

test("subtitle draft defaults come from backend capabilities", () => {
  assert.deepEqual(resolveSubtitleDraft(capabilities, null), defaults);
  assert.equal(hasUnsupportedSubtitleFields(capabilities), false);
});

test("non-default persisted style and artifact provenance remain distinct", () => {
  const selected = {
    ...defaults,
    font_size: 54,
    primary_color: "#FFD400",
    outline_width: 3,
    margin_v: 135,
  };
  const request = {
    metadata: {
      render_settings: { subtitle_style: selected },
      render_provenance: {
        subtitle_style_applied: selected,
        target_fps: 12,
        effective_fps_mode: "CFR",
        text_truncated: true,
        scenes: [{ sequence_index: 1, text_truncated: true }],
      },
    },
  } as ProductionRequest;

  assert.deepEqual(resolveSubtitleDraft(capabilities, request), selected);
  assert.equal(resolveArtifactProvenance(request)?.text_truncated, true);
});

test("ASS alignment values map to preview layout", () => {
  assert.equal(subtitleHorizontalAlignment(1), "left");
  assert.equal(subtitleHorizontalAlignment(2), "center");
  assert.equal(subtitleHorizontalAlignment(3), "right");
  assert.equal(subtitleVerticalAlignment(8), "flex-start");
  assert.equal(subtitleVerticalAlignment(5), "center");
  assert.equal(subtitleVerticalAlignment(2), "flex-end");
});

test("Production Studio contains truth labels without fake persistence or FPS controls", () => {
  const source = readFileSync(new URL("../src/components/workflow/ProductionPanels.tsx", import.meta.url), "utf8");
  assert.match(source, /Draft/);
  assert.match(source, /Render Applied/);
  assert.match(source, /From Artifact/);
  assert.match(source, /Estimated word timing|subtitle_timing_label/);
  assert.match(source, /FPS mode/);
  assert.match(source, /Target FPS/);
  assert.doesNotMatch(source, /localStorage/);
  assert.doesNotMatch(source, />Save</);
  assert.doesNotMatch(source, /word-accurate/i);
  assert.doesNotMatch(source, /24 fps|30 fps|60 fps/i);
});
