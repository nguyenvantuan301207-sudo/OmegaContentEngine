import type {
  MediaArtifact,
  NarrationSegment,
  ProductionAsset,
  ProductionQAResult,
  ProductionRuntimeTruthResponse,
  ProductionScene,
  SubtitleCue,
} from "./api.ts";

export type ProductionReadAuthority = "PLANNED" | "RENDERED";

export type ArtifactRuntimeTruthState =
  | { status: "PLANNED"; truth: null; artifactId: null; message: null }
  | { status: "LOADING"; truth: null; artifactId: string; message: null }
  | { status: "RENDERED"; truth: ProductionRuntimeTruthResponse; artifactId: string; message: null }
  | { status: "UNAVAILABLE"; truth: null; artifactId: string; message: string }
  | { status: "ERROR"; truth: null; artifactId: string; message: string };

export interface ProductionReadView {
  authority: ProductionReadAuthority | "UNAVAILABLE";
  scenes: ProductionScene[];
  narration: NarrationSegment[];
  subtitles: SubtitleCue[];
  visuals: ProductionRuntimeTruthResponse["runtime_snapshot"]["visuals"];
  durationMs: number;
}

export interface AutomaticArtifactSelection {
  artifactId: string | null;
  error: string | null;
}

export interface ArtifactTruthTicket {
  artifactId: string;
  generation: number;
}

export class ArtifactTruthRequestGuard {
  private generation = 0;

  begin(artifactId: string): ArtifactTruthTicket {
    return { artifactId, generation: ++this.generation };
  }

  invalidate(): void {
    this.generation += 1;
  }

  isCurrent(ticket: ArtifactTruthTicket, selectedArtifactId: string | null, responseArtifactId?: string): boolean {
    return ticket.generation === this.generation
      && ticket.artifactId === selectedArtifactId
      && (!responseArtifactId || responseArtifactId === ticket.artifactId);
  }
}

export function initialArtifactTruthState(artifact: MediaArtifact | null): ArtifactRuntimeTruthState {
  return artifact
    ? { status: "LOADING", truth: null, artifactId: artifact.id, message: null }
    : { status: "PLANNED", truth: null, artifactId: null, message: null };
}

export function resolveAutomaticArtifactSelection(artifacts: MediaArtifact[]): AutomaticArtifactSelection {
  const current = artifacts.filter((artifact) => artifact.is_current);
  if (current.length === 1) return { artifactId: current[0].id, error: null };
  if (current.length === 0) return { artifactId: null, error: null };
  return {
    artifactId: null,
    error: "Multiple artifacts are marked current. Automatic artifact selection is unavailable.",
  };
}

export function getReadAuthorityCounts(view: ProductionReadView): { storyboard: number; subtitles: number } {
  return { storyboard: view.scenes.length, subtitles: view.subtitles.length };
}

export function buildProductionReadView(
  state: ArtifactRuntimeTruthState,
  planned: { scenes: ProductionScene[]; narration: NarrationSegment[]; subtitles: SubtitleCue[] },
  artifact: MediaArtifact | null,
): ProductionReadView {
  if (state.status === "PLANNED") {
    return {
      authority: "PLANNED",
      scenes: planned.scenes,
      narration: planned.narration,
      subtitles: planned.subtitles,
      visuals: [],
      durationMs: planned.scenes.reduce((total, scene) => total + scene.estimated_duration_ms, 0),
    };
  }
  if (state.status !== "RENDERED" || !artifact || state.artifactId !== artifact.id || state.truth.artifact_id !== artifact.id) {
    return { authority: "UNAVAILABLE", scenes: [], narration: [], subtitles: [], visuals: [], durationMs: 0 };
  }

  const snapshot = state.truth.runtime_snapshot;
  const sceneIds = new Map(snapshot.scenes.map((scene) => [scene.sequence_index, `runtime-${artifact.id}-scene-${scene.sequence_index}`]));
  const createdAt = artifact.created_at;
  return {
    authority: "RENDERED",
    scenes: snapshot.scenes.map((scene) => ({
      id: sceneIds.get(scene.sequence_index)!,
      production_request_id: artifact.production_request_id,
      scene_order: scene.sequence_index,
      script_section_id: scene.source_section_id,
      scene_type: scene.effective_strategy,
      narration_text: scene.narration_text ?? "",
      estimated_duration_ms: scene.duration_ms,
      visual_intent: snapshot.visuals[scene.visual_index]
        ? describeRuntimeVisual(snapshot.visuals[scene.visual_index])
        : null,
      transition_in: null,
      transition_out: null,
      created_at: createdAt,
    })),
    narration: snapshot.narration.map((segment) => ({
      id: `runtime-${artifact.id}-narration-${segment.sequence_index}`,
      production_request_id: artifact.production_request_id,
      scene_id: sceneIds.get(segment.scene_index) ?? `runtime-${artifact.id}-scene-${segment.scene_index}`,
      audio_asset_id: segment.audio_asset_id,
      text: segment.text,
      start_ms: segment.start_ms,
      end_ms: segment.end_ms,
      duration_ms: segment.duration_ms,
      created_at: createdAt,
    })),
    subtitles: snapshot.subtitles.cues.map((cue) => ({
      id: `runtime-${artifact.id}-subtitle-${cue.cue_order}`,
      production_request_id: artifact.production_request_id,
      scene_id: sceneIds.get(cue.scene_index) ?? `runtime-${artifact.id}-scene-${cue.scene_index}`,
      cue_order: cue.cue_order,
      start_ms: cue.start_ms,
      end_ms: cue.end_ms,
      text: cue.text,
      created_at: createdAt,
    })),
    visuals: snapshot.visuals,
    durationMs: snapshot.render_target.duration_ms,
  };
}

export function describeRuntimeVisual(visual: ProductionReadView["visuals"][number]): string {
  const identity = visual.provider
    ? `${visual.provider}${visual.provider_asset_id ? ` · ${visual.provider_asset_id}` : ""}`
    : visual.template_id ?? visual.kind ?? visual.visual_mode;
  return `${visual.origin} · ${identity}`;
}

export function qaForArtifact(qa: ProductionQAResult | null, artifact: MediaArtifact | null): ProductionQAResult | null {
  return qa && artifact && qa.artifact_id === artifact.id ? qa : null;
}

export function canPublishArtifact(artifact: MediaArtifact | null, qa: ProductionQAResult | null): boolean {
  return Boolean(artifact?.is_current && qa && qa.artifact_id === artifact.id && (qa.status === "PASSED" || qa.status === "PASSED_WITH_WARNINGS"));
}

export function plannedAssetSummary(assets: ProductionAsset[]): string {
  return assets.length ? `${assets.length} planned production assets` : "No planned production assets";
}
