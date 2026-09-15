import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type {
  MediaArtifact,
  ProductionQAResult,
  ProductionRuntimeTruthResponse,
  ProductionScene,
  SubtitleCue,
} from "../src/lib/api.ts";
import {
  ArtifactTruthRequestGuard,
  buildProductionReadView,
  canPublishArtifact,
  initialArtifactTruthState,
  qaForArtifact,
} from "../src/lib/production-read-authority.ts";

const artifact = (id: string, version: number, isCurrent = true): MediaArtifact => ({
  id,
  production_request_id: "request-1",
  render_job_id: `job-${version}`,
  artifact_type: "VIDEO",
  version,
  is_current: isCurrent,
  storage_uri: `artifacts/${id}.mp4`,
  content_hash: "a".repeat(64),
  file_size_bytes: 100,
  mime_type: "video/mp4",
  width: 1080,
  height: 1920,
  duration_ms: 9999,
  created_at: "2026-09-15T00:00:00Z",
});

const truth = (
  artifactId: string,
  version: number,
  subtitleMode: "OFF" | "STANDARD" | "KARAOKE" = "STANDARD",
): ProductionRuntimeTruthResponse => ({
  truth_kind: "RENDERED",
  artifact_id: artifactId,
  render_job_id: `job-${version}`,
  render_plan_id: `plan-${version}`,
  render_version: version,
  runtime_snapshot: {
    schema_version: 1,
    lineage: {
      channel_id: "channel-1",
      production_request_id: "request-1",
      content_request_id: "content-1",
      script_version_id: "script-1",
      channel_dna_revision_id: "dna-1",
      render_plan_id: `plan-${version}`,
      render_job_id: `job-${version}`,
      media_artifact_id: artifactId,
      artifact_version: version,
    },
    scenes: [{
      sequence_index: 1,
      source_section_id: "section-1",
      source_statement_references: [1],
      narration_text: `rendered scene ${version}`,
      original_strategy: "B_ROLL",
      effective_strategy: "PROVIDER_VIDEO",
      template_id: "template-1",
      start_ms: 0,
      end_ms: 4200,
      duration_ms: 4200,
      scene_content_sha256: "b".repeat(64),
      visual_origin: "PROVIDER",
      visual_index: 0,
    }],
    narration: [{
      sequence_index: 1,
      scene_index: 1,
      text: `rendered narration ${version}`,
      start_ms: 0,
      end_ms: 4200,
      duration_ms: 4200,
      audio_content_sha256: "c".repeat(64),
      provider: "kokoro",
      voice_profile: {},
    }],
    subtitles: {
      requested_mode: subtitleMode,
      effective_mode: subtitleMode,
      fallback_applied: false,
      timing_source: subtitleMode === "OFF" ? "NONE" : "SENTENCE",
      semantics_version: 1,
      burn_applied: subtitleMode !== "OFF",
      style_applied: subtitleMode === "OFF" ? null : { font_family: "Arial" },
      cues: subtitleMode === "OFF" ? [] : [{ scene_index: 1, cue_order: 1, start_ms: 100, end_ms: 4000, text: `${subtitleMode} runtime cue` }],
      artifacts: subtitleMode === "OFF" ? [] : [{ scene_index: 1, artifact_kind: "ASS", content_sha256: "d".repeat(64) }],
    },
    visuals: [{
      scene_index: 1,
      origin: "PROVIDER",
      visual_mode: "PEXELS",
      provider: "pexels",
      provider_asset_id: `asset-${version}`,
      source_url: "https://images.example/rendered.mp4",
      license_name: "Pexels License",
      attribution: "Creator",
      content_sha256: "e".repeat(64),
      provider_metadata: { forward_compatible: true },
    }],
    branding: { policy_source: "channel", assets: [] },
    audio_mix: {},
    render_target: {
      duration_ms: 4200,
      width: 1080,
      height: 1920,
      fps: 24,
      fps_mode: "CFR",
      video_codec: "h264",
      audio_codec: "aac",
      has_audio: true,
      container: "mp4",
      file_size_bytes: 100,
      content_sha256: "a".repeat(64),
    },
    probe: { duration_ms: 4200 },
    artifact: { storage_reference: `artifacts/${artifactId}.mp4` },
    fingerprints: { canonical_contract: "f".repeat(64), manifest_run: "0".repeat(64), subtitle_semantics_version: 1 },
  },
});

const plannedScene: ProductionScene = {
  id: "planned-scene",
  production_request_id: "request-1",
  scene_order: 1,
  scene_type: "PLANNED",
  narration_text: "planned narration",
  estimated_duration_ms: 8000,
  created_at: "2026-09-14T00:00:00Z",
};
const plannedCue: SubtitleCue = {
  id: "planned-cue",
  production_request_id: "request-1",
  scene_id: "planned-scene",
  cue_order: 1,
  start_ms: 0,
  end_ms: 8000,
  text: "prepared subtitle that must not masquerade as rendered",
  created_at: "2026-09-14T00:00:00Z",
};
const planned = { scenes: [plannedScene], narration: [], subtitles: [plannedCue] };

test("planned-only requests retain explicit request-level planned data", () => {
  const view = buildProductionReadView(initialArtifactTruthState(null), planned, null);
  assert.equal(view.authority, "PLANNED");
  assert.equal(view.scenes[0].narration_text, "planned narration");
  assert.equal(view.durationMs, 8000);
});

test("typed client targets runtime truth for the exact artifact id", () => {
  const source = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
  assert.match(source, /function getArtifactRuntimeTruth/);
  assert.match(source, /production\/\$\{requestId\}\/artifacts\/\$\{artifactId\}\/runtime-truth/);
  assert.match(source, /Promise<ProductionRuntimeTruthResponse>/);
});

test("switching v1 to v2 prevents a late v1 response from becoming current", () => {
  const guard = new ArtifactTruthRequestGuard();
  const v1 = guard.begin("artifact-v1");
  const v2 = guard.begin("artifact-v2");
  assert.equal(guard.isCurrent(v1, "artifact-v2", "artifact-v1"), false);
  assert.equal(guard.isCurrent(v2, "artifact-v2", "artifact-v2"), true);
});

test("rendered scene duration, narration, and visual provenance come from runtime truth", () => {
  const selected = artifact("artifact-v1", 1);
  const response = truth(selected.id, 1);
  const view = buildProductionReadView({ status: "RENDERED", truth: response, artifactId: selected.id, message: null }, planned, selected);
  assert.equal(view.authority, "RENDERED");
  assert.equal(view.durationMs, 4200);
  assert.equal(view.scenes[0].estimated_duration_ms, 4200);
  assert.equal(view.narration[0].text, "rendered narration 1");
  assert.equal(view.visuals[0].provider, "pexels");
  assert.equal(view.visuals[0].provider_asset_id, "asset-1");
  assert.equal(view.visuals[0].license_name, "Pexels License");
});

test("OFF runtime truth keeps prepared subtitles out of rendered view", () => {
  const selected = artifact("artifact-off", 1);
  const response = truth(selected.id, 1, "OFF");
  const view = buildProductionReadView({ status: "RENDERED", truth: response, artifactId: selected.id, message: null }, planned, selected);
  assert.deepEqual(view.subtitles, []);
  assert.equal(response.runtime_snapshot.subtitles.effective_mode, "OFF");
});

test("STANDARD and KARAOKE use actual runtime cue timing without prepared cue leakage", () => {
  for (const mode of ["STANDARD", "KARAOKE"] as const) {
    const selected = artifact(`artifact-${mode}`, 1);
    const response = truth(selected.id, 1, mode);
    const view = buildProductionReadView({ status: "RENDERED", truth: response, artifactId: selected.id, message: null }, planned, selected);
    assert.equal(view.subtitles[0].text, `${mode} runtime cue`);
    assert.equal(view.subtitles[0].start_ms, 100);
    assert.equal(view.subtitles.some((cue) => cue.id === "planned-cue"), false);
  }
});

test("unavailable and failed runtime truth never fall back to planned rows", () => {
  const selected = artifact("artifact-missing", 2, false);
  for (const status of ["UNAVAILABLE", "ERROR"] as const) {
    const view = buildProductionReadView({ status, truth: null, artifactId: selected.id, message: "missing" }, planned, selected);
    assert.equal(view.authority, "UNAVAILABLE");
    assert.deepEqual(view.scenes, []);
    assert.deepEqual(view.subtitles, []);
  }
});

test("QA is shown only when it matches the selected artifact", () => {
  const qa: ProductionQAResult = { id: "qa-1", production_request_id: "request-1", artifact_id: "artifact-v1", status: "PASSED", findings: [], executed_at: "2026-09-15T00:00:00Z" };
  assert.equal(qaForArtifact(qa, artifact("artifact-v1", 1))?.id, "qa-1");
  assert.equal(qaForArtifact(qa, artifact("artifact-v2", 2)), null);
});

test("blocked or non-current forensic artifacts are never treated as current or publishable", () => {
  const blocked = artifact("artifact-v3", 3, false);
  const qa: ProductionQAResult = { id: "qa-3", production_request_id: "request-1", artifact_id: blocked.id, status: "BLOCKED", findings: [], executed_at: "2026-09-15T00:00:00Z" };
  const view = buildProductionReadView({ status: "RENDERED", truth: truth(blocked.id, 3), artifactId: blocked.id, message: null }, planned, blocked);
  assert.equal(view.authority, "RENDERED");
  assert.equal(view.scenes[0].narration_text, "rendered scene 3");
  assert.equal(canPublishArtifact(blocked, qa), false);
});

test("Production UI distinguishes planned profile, rendered output, and unavailable states", () => {
  const source = readFileSync(new URL("../src/app/production/page.tsx", import.meta.url), "utf8");
  assert.match(source, /PLANNED read authority/);
  assert.match(source, /RENDERED read authority/);
  assert.match(source, /Planned render profile/);
  assert.match(source, /Rendered output/);
  assert.match(source, /Rendered subtitles: OFF \/ none/);
  assert.match(source, /NON-CURRENT \/ FORENSIC/);
  assert.match(source, /Planned rows are not substituted/);
  assert.doesNotMatch(source, /provider word timing/i);
});
