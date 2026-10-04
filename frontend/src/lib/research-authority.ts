/** Research confidence is a percentage on the backend's 0..100 scale. */
export function formatResearchConfidence(value: number): string {
  if (!Number.isFinite(value)) return "Unavailable";
  return `${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
}

export function isSufficientResearchBrief(
  brief: { outcome: string } | null | undefined,
): boolean {
  return brief?.outcome === "SUFFICIENT";
}
