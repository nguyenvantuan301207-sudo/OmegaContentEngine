/**
 * Pure helper utilities for Publisher Operations dashboard.
 * Encapsulates status mapping, progress calculation, hold source distinctions,
 * action eligibility rules, and security sanitization checks.
 */

export const FORBIDDEN_KEYS = [
  "access_token",
  "refresh_token",
  "encrypted_access_token",
  "encrypted_refresh_token",
  "session_uri",
  "authorization",
  "client_secret",
  "oauth_code",
  "pkce_verifier",
  "code_verifier",
] as const;

export type AttemptStateTone = "neutral" | "info" | "success" | "warning" | "danger";

export interface StateBadge {
  label: string;
  tone: AttemptStateTone;
  isTerminal: boolean;
  isInFlight: boolean;
}

/**
 * Maps attempt state to human-readable labels and UI tones.
 * Enforces canonical domain states: CREATED, UPLOADING, FINALIZING, UNKNOWN, SUCCEEDED,
 * RETRYABLE_FAILED, PERMANENT_FAILED. Rejecting any COMMITTING state.
 */
export function getAttemptStateBadge(state: string): StateBadge {
  const upper = (state || "").toUpperCase();
  switch (upper) {
    case "CREATED":
      return { label: "Created", tone: "info", isTerminal: false, isInFlight: true };
    case "UPLOADING":
      return { label: "Uploading", tone: "info", isTerminal: false, isInFlight: true };
    case "FINALIZING":
      return { label: "Finalizing", tone: "info", isTerminal: false, isInFlight: true };
    case "UNKNOWN":
      return { label: "Ambiguous (Unknown)", tone: "warning", isTerminal: false, isInFlight: true };
    case "SUCCEEDED":
      return { label: "Published", tone: "success", isTerminal: true, isInFlight: false };
    case "RETRYABLE_FAILED":
      return { label: "Retryable Failure", tone: "warning", isTerminal: false, isInFlight: false };
    case "PERMANENT_FAILED":
      return { label: "Permanent Failure", tone: "danger", isTerminal: true, isInFlight: false };
    default:
      return { label: state || "Unknown", tone: "neutral", isTerminal: false, isInFlight: false };
  }
}

/**
 * Distinguishes the two different operational holds:
 * 1. Provider recovery hold (reconciliation_status == MANUAL_HOLD)
 * 2. Calendar / schedule hold (P17-A scheduler reservation held)
 */
export function getHoldSourceBadge(source: "RECOVERY" | "SCHEDULE" | string): {
  label: string;
  tone: AttemptStateTone;
  tooltip: string;
} {
  const upper = (source || "").toUpperCase();
  if (upper === "RECOVERY") {
    return {
      label: "Provider Recovery Hold",
      tone: "danger",
      tooltip: "Upload outcome was ambiguous; provider reconciliation required before retry.",
    };
  }
  if (upper === "SCHEDULE") {
    return {
      label: "Calendar Schedule Hold",
      tone: "warning",
      tooltip: "Publication schedule reservation was manually held by editorial decision.",
    };
  }
  return {
    label: "Unknown Hold",
    tone: "neutral",
    tooltip: "Unspecified hold source.",
  };
}

/**
 * Format bytes into human-readable representation.
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || isNaN(bytes) || bytes < 0) {
    return "0 B";
  }
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1);
  return `${val} ${units[i]}`;
}

/**
 * Computes upload progress safely without division by zero.
 */
export function calculateProgress(
  uploaded: number | null | undefined,
  total: number | null | undefined
): { percentage: number; label: string } {
  const up = Math.max(0, Number(uploaded) || 0);
  const tot = Math.max(0, Number(total) || 0);
  if (tot <= 0) {
    return { percentage: 0, label: "0% (0 B / 0 B)" };
  }
  const pct = Math.min(100, Math.round((up / tot) * 1000) / 10);
  return {
    percentage: pct,
    label: `${pct}% (${formatBytes(up)} / ${formatBytes(tot)})`,
  };
}

/**
 * Format duration in seconds into human-readable string.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || isNaN(seconds) || seconds < 0) {
    return "-";
  }
  const sec = Math.round(seconds);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return `${h}h ${remM}m`;
}

/**
 * Validates backend-derived action eligibility.
 * Backend is the source of truth for allowed operations.
 * Never allow generic retry for UNKNOWN states.
 */
export function isActionAllowed(
  allowedOperations: string[] | undefined,
  targetAction: string
): boolean {
  if (!allowedOperations || !Array.isArray(allowedOperations)) {
    return false;
  }
  return allowedOperations.includes(targetAction.toUpperCase());
}

/**
 * Verifies that an object does not leak forbidden credentials or session URIs.
 * Throws an Error if any forbidden key or sensitive pattern is encountered.
 */
export function assertNoForbiddenFields(obj: unknown, path = "root"): void {
  if (!obj || typeof obj !== "object") {
    if (typeof obj === "string") {
      const lower = obj.toLowerCase();
      if (lower.includes("bearer ") || lower.includes("client_secret") || lower.includes("upload.youtube.com")) {
        throw new Error(`Sensitive content leaked at ${path}: ${obj}`);
      }
    }
    return;
  }

  if (Array.isArray(obj)) {
    obj.forEach((item, idx) => assertNoForbiddenFields(item, `${path}[${idx}]`));
    return;
  }

  const record = obj as Record<string, unknown>;
  for (const [key, value] of Object.entries(record)) {
    const lowerKey = key.toLowerCase();
    for (const forbidden of FORBIDDEN_KEYS) {
      if (lowerKey.includes(forbidden)) {
        throw new Error(`Forbidden field key detected at ${path}.${key}`);
      }
    }
    assertNoForbiddenFields(value, `${path}.${key}`);
  }
}
