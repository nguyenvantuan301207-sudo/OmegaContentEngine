import type {
  ProductionRenderCapabilities,
  ProductionRequest,
  RenderArtifactProvenance,
  SubtitlePreset,
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

export function getSubtitlePresets(
  capabilities: ProductionRenderCapabilities,
): SubtitlePreset[] {
  return capabilities.subtitle.presets ?? [];
}

export function getPresetById(
  capabilities: ProductionRenderCapabilities,
  presetId: string,
): SubtitlePreset | undefined {
  return (capabilities.subtitle.presets ?? []).find((preset) => preset.id === presetId);
}

export function matchesPreset(
  style: SubtitleRenderStyle,
  preset: SubtitlePreset,
): boolean {
  const p = preset.style;
  return (
    style.font_family === p.font_family &&
    style.font_size === p.font_size &&
    style.bold === p.bold &&
    style.primary_color.toUpperCase() === p.primary_color.toUpperCase() &&
    style.outline_color.toUpperCase() === p.outline_color.toUpperCase() &&
    style.outline_width === p.outline_width &&
    style.shadow === p.shadow &&
    style.background_box === p.background_box &&
    style.alignment === p.alignment &&
    style.margin_v === p.margin_v &&
    style.max_lines === p.max_lines &&
    Math.abs(style.max_width_ratio - p.max_width_ratio) < 0.001 &&
    (style.karaoke ?? false) === (p.karaoke ?? false)
  );
}

export function findMatchingPresetId(
  capabilities: ProductionRenderCapabilities,
  style: SubtitleRenderStyle,
): string | null {
  const match = (capabilities.subtitle.presets ?? []).find((preset) =>
    matchesPreset(style, preset),
  );
  return match?.id ?? null;
}
