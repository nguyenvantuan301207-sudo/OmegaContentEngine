import assert from "node:assert/strict";
import test from "node:test";

import {
  assertNoForbiddenFields,
  calculateProgress,
  FORBIDDEN_KEYS,
  formatBytes,
  formatDuration,
  getAttemptStateBadge,
  getHoldSourceBadge,
  isActionAllowed,
} from "../src/lib/publisher-operations-helpers.ts";

test("getAttemptStateBadge classifies domain states without COMMITTING", () => {
  const uploading = getAttemptStateBadge("UPLOADING");
  assert.equal(uploading.label, "Uploading");
  assert.equal(uploading.isInFlight, true);
  assert.equal(uploading.tone, "info");

  const finalizing = getAttemptStateBadge("FINALIZING");
  assert.equal(finalizing.label, "Finalizing");
  assert.equal(finalizing.isInFlight, true);

  const unknown = getAttemptStateBadge("UNKNOWN");
  assert.equal(unknown.label, "Ambiguous (Unknown)");
  assert.equal(unknown.isInFlight, true);
  assert.equal(unknown.tone, "warning");

  const succeeded = getAttemptStateBadge("SUCCEEDED");
  assert.equal(succeeded.label, "Published");
  assert.equal(succeeded.isTerminal, true);
  assert.equal(succeeded.isInFlight, false);

  const failed = getAttemptStateBadge("PERMANENT_FAILED");
  assert.equal(failed.label, "Permanent Failure");
  assert.equal(failed.isTerminal, true);

  // Assert COMMITTING is not treated as a valid canonical active state
  const committing = getAttemptStateBadge("COMMITTING");
  assert.equal(committing.isInFlight, false);
  assert.equal(committing.label, "COMMITTING");
});

test("getHoldSourceBadge cleanly distinguishes recovery and schedule holds", () => {
  const recoveryHold = getHoldSourceBadge("RECOVERY");
  assert.equal(recoveryHold.label, "Provider Recovery Hold");
  assert.equal(recoveryHold.tone, "danger");
  assert.match(recoveryHold.tooltip, /reconciliation/i);

  const scheduleHold = getHoldSourceBadge("SCHEDULE");
  assert.equal(scheduleHold.label, "Calendar Schedule Hold");
  assert.equal(scheduleHold.tone, "warning");
  assert.match(scheduleHold.tooltip, /editorial/i);

  assert.notEqual(recoveryHold.label, scheduleHold.label);
});

test("formatBytes converts sizes safely", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1024), "1.0 KB");
  assert.equal(formatBytes(1048576), "1.0 MB");
  assert.equal(formatBytes(1073741824), "1.0 GB");
  assert.equal(formatBytes(null), "0 B");
  assert.equal(formatBytes(-50), "0 B");
});

test("calculateProgress handles boundaries and formatting", () => {
  const zero = calculateProgress(0, 0);
  assert.equal(zero.percentage, 0);

  const half = calculateProgress(500, 1000);
  assert.equal(half.percentage, 50);
  assert.equal(half.label, "50% (500 B / 1000 B)");

  const full = calculateProgress(1048576, 1048576);
  assert.equal(full.percentage, 100);
  assert.equal(full.label, "100% (1.0 MB / 1.0 MB)");
});

test("formatDuration formats seconds accurately", () => {
  assert.equal(formatDuration(null), "-");
  assert.equal(formatDuration(45), "45s");
  assert.equal(formatDuration(125), "2m 5s");
  assert.equal(formatDuration(3660), "1h 1m");
});

test("isActionAllowed derives permissions strictly from backend contract", () => {
  const allowed = ["EXPLICIT_EXTERNAL_RECONCILIATION", "AUTHORIZE_EXISTING_SESSION_RESUME"];

  assert.equal(isActionAllowed(allowed, "EXPLICIT_EXTERNAL_RECONCILIATION"), true);
  assert.equal(isActionAllowed(allowed, "AUTHORIZE_EXISTING_SESSION_RESUME"), true);

  // UNKNOWN must NEVER allow generic RETRY action
  assert.equal(isActionAllowed(allowed, "RETRY"), false);
  assert.equal(isActionAllowed([], "RETRY"), false);
  assert.equal(isActionAllowed(undefined, "RETRY"), false);
});

test("assertNoForbiddenFields recursively detects credential and session leaks", () => {
  // Safe payload passes
  const safePayload = {
    attempt_id: "123",
    status: "SUCCEEDED",
    provider_video_id: "yt_safe_vid_99",
    progress: { percentage: 100 },
  };
  assert.doesNotThrow(() => assertNoForbiddenFields(safePayload));

  // Forbidden keys must throw
  for (const forbidden of FORBIDDEN_KEYS) {
    const leakKeyObj = { [forbidden]: "some_secret_value" };
    assert.throws(() => assertNoForbiddenFields(leakKeyObj), /Forbidden field key detected/);
  }

  // Sensitive values must throw
  const leakBearer = { note: "Authorization: Bearer secret_token_xyz" };
  assert.throws(() => assertNoForbiddenFields(leakBearer), /Sensitive content leaked/);

  const leakUri = { url: "https://upload.youtube.com/upload/secret_session" };
  assert.throws(() => assertNoForbiddenFields(leakUri), /Sensitive content leaked/);
});
