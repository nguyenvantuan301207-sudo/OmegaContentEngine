import type {
  ProductionRenderCapabilities,
  ProductionRequest,
  RenderArtifactProvenance,
  SubtitleRenderStyle,
} from "./api.ts";

export function resolveSubtitleDraft(
  capabilities: ProductionRenderCapabilities,
  request: ProductionRequest | null,
): SubtitleRenderStyle {
  return request?.metadata?.render_settings?.subtitle_style ?? capabilities.subtitle.defaults;
}

export function resolveArtifactProvenance(
  request: ProductionRequest | null,
): RenderArtifactProvenance | null {
  return request?.metadata?.render_provenance ?? null;
}

export function subtitleHorizontalAlignment(alignment: number): "left" | "center" | "right" {
  if ([1, 4, 7].includes(alignment)) return "left";
  if ([3, 6, 9].includes(alignment)) return "right";
  return "center";
}

export function subtitleVerticalAlignment(alignment: number): "flex-start" | "center" | "flex-end" {
  if ([7, 8, 9].includes(alignment)) return "flex-start";
  if ([4, 5, 6].includes(alignment)) return "center";
  return "flex-end";
}

export function hasUnsupportedSubtitleFields(
  capabilities: ProductionRenderCapabilities,
): boolean {
  return capabilities.subtitle.fields.some((field) => field.truth_state === "UNSUPPORTED");
}
