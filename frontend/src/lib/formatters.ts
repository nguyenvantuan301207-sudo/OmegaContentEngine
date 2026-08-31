/**
 * Safe numeric parsing and formatting utilities for OMEGA frontend.
 * Guarantees zero crashes on untrusted API values (e.g. strings serialized by Pydantic Decimals).
 */

/**
 * Safely parses a value (number, numeric string, null, undefined) into a finite number.
 * Returns null if the value is not a valid finite number.
 */
export function toFiniteNumber(
  value: number | string | null | undefined
): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed === "") return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/**
 * Format currency safely as USD.
 * Returns e.g. "$0.00", "$50.00", or fallback (default "N/A") if invalid.
 */
export function formatCurrencyUsd(
  value: number | string | null | undefined,
  fallback = "N/A"
): string {
  const num = toFiniteNumber(value);
  if (num === null) return fallback;
  return `$${num.toFixed(2)}`;
}

/**
 * Format a number to fixed decimal places safely.
 * Returns e.g. "12.3", "0.00", or fallback (default "N/A") if invalid.
 */
export function formatDecimal(
  value: number | string | null | undefined,
  decimals = 2,
  fallback = "N/A"
): string {
  const num = toFiniteNumber(value);
  if (num === null) return fallback;
  return num.toFixed(decimals);
}
