export type PresentationTone =
  "neutral" | "info" | "success" | "warning" | "danger";

const SUCCESS_STATES = new Set([
  "SUCCEEDED",
  "SELECTED",
  "RECOMMENDED",
  "PASSED",
  "SUFFICIENT",
  "RENDERED",
]);
const INFO_STATES = new Set(["RUNNING", "READY", "EVALUATED", "CONFIRMED"]);
const WARNING_STATES = new Set([
  "PENDING",
  "DRAFT",
  "DISCOVERED",
  "PARTIAL",
  "INSUFFICIENT",
  "PASSED_WITH_WARNINGS",
]);
const DANGER_STATES = new Set([
  "FAILED",
  "BLOCKED",
  "REJECTED",
  "CANCELLED",
  "EXACT_DUPLICATE",
  "ERROR",
  "BLOCKING",
]);

export function statusTone(status?: string | null): PresentationTone {
  if (!status) return "neutral";
  if (SUCCESS_STATES.has(status)) return "success";
  if (INFO_STATES.has(status)) return "info";
  if (WARNING_STATES.has(status)) return "warning";
  if (DANGER_STATES.has(status)) return "danger";
  return "neutral";
}
