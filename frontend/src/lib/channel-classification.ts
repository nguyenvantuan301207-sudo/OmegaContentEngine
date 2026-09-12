import type { Channel } from "./api";

export type ChannelClassification =
  | "REAL_USER"
  | "TEST_FIXTURE"
  | "DEMO"
  | "HISTORICAL_E2E"
  | "ORPHANED"
  | "UNKNOWN";

export interface ChannelProvenance {
  classification: ChannelClassification;
  isInternal: boolean;
  label: string;
  badgeLabel: string;
  tone: "neutral" | "info" | "warning" | "success" | "danger" | "purple";
  evidence: string;
}

// Canonical historical core E2E validation ID (FastAPI Masterclass)
// Preserves prior L4 validation evidence with 9 succeeded render jobs and video MP4s
const CANONICAL_HISTORICAL_E2E_IDS = new Set<string>([
  "fa8813c9-9e7b-43d0-b76e-1bb323ad5a7a",
]);

// Test fixture slug prefixes produced by integration and smoke test harnesses
const TEST_SLUG_PREFIXES: readonly string[] = [
  "matrix-chan-",
  "smoke-",
  "content-smoke-",
  "concurrency-",
  "draft-gate-",
  "monotonic-",
  "pinned-ctx-",
  "select-conc-",
  "service-pub-chan-",
  "shadow-pub-chan-",
  "orch-chan-",
  "orch-channel-",
  "api-pub-chan-",
  "media-res-",
  "mission-linkage-",
  "discovery-test-",
  "topic-test-",
  "test-channel-",
  "test-pub-chan-",
  "test-",
  "fixture-",
  "chan-",
  "ch-a-",
  "ch-b-",
  "c1-",
  "c2-",
];

// Structural test pattern expressions (generic harness patterns, NOT single-channel special cases)
const TEST_NAME_PATTERNS: readonly RegExp[] = [
  /^Matrix Channel/i,
  /^Test Channel/i,
  /^(Channel|Chan) [A-Z0-9]+$/i,
  /\b(Test|Services|Concurrency|Pinning|Invariants|DNA|Hook|Revision|Idempotency|Operating)\b.*Channel/i,
  /\b(Shadow|Orch|Publisher|Pub)\b.*Channel/i,
  /Channel\b.*(Test|Fixture|Mock|Harness|Gate)/i,
];

export interface ChannelDependencyCounts {
  missions?: number;
  productions?: number;
  renderJobs?: number;
  artifacts?: number;
  publishIntents?: number;
  topicCandidates?: number;
  researchBriefs?: number;
}

/**
 * Authoritatively classify a channel based on provenance evidence.
 * Conservative: if evidence is incomplete or ambiguous, classifies UNKNOWN.
 */
export function classifyChannel(
  channel: Channel,
  deps?: ChannelDependencyCounts
): ChannelProvenance {
  const id = channel.id;
  const name = channel.name?.trim() || "";
  const slug = channel.slug?.trim() || "";
  const metadata = (channel.metadata && typeof channel.metadata === "object" ? channel.metadata : {}) as Record<string, unknown>;

  // 1. Check for Historical E2E validation canary / pipeline evidence
  if (CANONICAL_HISTORICAL_E2E_IDS.has(id)) {
    return {
      classification: "HISTORICAL_E2E",
      isInternal: true,
      label: "Historical E2E Core Validation",
      badgeLabel: "E2E",
      tone: "purple",
      evidence: "Canonical L4 Historical E2E Core Validation channel (FastAPI Masterclass with 9 completed render jobs, video artifacts, and QA results)",
    };
  }

  const isCanary =
    name.toLowerCase().includes("canary") ||
    slug.toLowerCase().includes("canary") ||
    name.startsWith("P8 Long-Form") ||
    (typeof metadata.purpose === "string" && (metadata.purpose.includes("canary") || metadata.purpose.includes("P12-F")));

  if (isCanary) {
    return {
      classification: "HISTORICAL_E2E",
      isInternal: true,
      label: "Historical Canary Validation",
      badgeLabel: "E2E",
      tone: "purple",
      evidence: `Historical validation run or canary execution: '${name}'`,
    };
  }

  // 2. Check for explicit disposable test metadata
  if (metadata.disposable === true || metadata.fixture === true) {
    return {
      classification: "TEST_FIXTURE",
      isInternal: true,
      label: "Disposable Test Fixture",
      badgeLabel: "TEST",
      tone: "neutral",
      evidence: `Explicit disposable test metadata: ${JSON.stringify(metadata)}`,
    };
  }

  // 3. Check for demo metadata
  if (metadata.demo === true || slug.startsWith("demo-")) {
    return {
      classification: "DEMO",
      isInternal: true,
      label: "Demo Channel",
      badgeLabel: "DEMO",
      tone: "info",
      evidence: "Channel marked as demo environment showcase",
    };
  }

  // 4. Check for Orphaned channel (explicit metadata or dependency counts provided and confirmed 0)
  if (metadata.orphaned === true || metadata.orphan === true) {
    return {
      classification: "ORPHANED",
      isInternal: true,
      label: "Orphaned Channel",
      badgeLabel: "INTERNAL",
      tone: "warning",
      evidence: "Channel explicitly marked as orphan",
    };
  }

  if (deps) {
    const totalDeps =
      (deps.missions ?? 0) +
      (deps.productions ?? 0) +
      (deps.renderJobs ?? 0) +
      (deps.artifacts ?? 0) +
      (deps.publishIntents ?? 0) +
      (deps.topicCandidates ?? 0) +
      (deps.researchBriefs ?? 0);
    if (totalDeps === 0) {
      return {
        classification: "ORPHANED",
        isInternal: true,
        label: "Orphaned Channel",
        badgeLabel: "INTERNAL",
        tone: "warning",
        evidence: "Zero dependencies across all activity and pipeline records",
      };
    }
  }

  // 5. Test fixture pattern matching (deterministic harness patterns)
  const matchesTestSlug = TEST_SLUG_PREFIXES.some((prefix) => slug.startsWith(prefix));
  const matchesTestName = TEST_NAME_PATTERNS.some((pattern) => pattern.test(name));

  if (matchesTestSlug || matchesTestName) {
    return {
      classification: "TEST_FIXTURE",
      isInternal: true,
      label: "Automated Test Fixture",
      badgeLabel: "TEST",
      tone: "neutral",
      evidence: matchesTestSlug
        ? `Slug '${slug}' matches automated test harness prefix`
        : `Name '${name}' matches test fixture naming pattern`,
    };
  }

  // 6. Explicit real user evidence (e.g. user metadata)
  if (metadata.real_user === true || metadata.user_created === true) {
    return {
      classification: "REAL_USER",
      isInternal: false,
      label: "Product Channel",
      badgeLabel: "PRODUCT",
      tone: "success",
      evidence: "Explicit user-created product channel",
    };
  }

  // 7. Safety fallback: UNKNOWN
  // Rule 8: UNKNOWN channels must NEVER silently disappear.
  // Safety > cosmetic cleanup.
  return {
    classification: "UNKNOWN",
    isInternal: false,
    label: "Unclassified Channel",
    badgeLabel: "UNCLASSIFIED",
    tone: "neutral",
    evidence: "Unverified provenance; retained visible in Normal mode for data safety",
  };
}

/**
 * Evaluates whether a channel should be visible under the given visibility mode.
 * In Normal mode (showInternal === false):
 * - REAL_USER and UNKNOWN are visible.
 * - TEST_FIXTURE, DEMO, HISTORICAL_E2E, and ORPHANED are hidden.
 * In Internal mode (showInternal === true):
 * - All channels are visible.
 */
export function isChannelVisible(channel: Channel, showInternal: boolean): boolean {
  if (showInternal) return true;
  const provenance = classifyChannel(channel);
  return !provenance.isInternal;
}

/**
 * Returns whether a classification represents internal/test provenance.
 */
export function isClassificationInternal(classification: ChannelClassification): boolean {
  return (
    classification === "TEST_FIXTURE" ||
    classification === "DEMO" ||
    classification === "HISTORICAL_E2E" ||
    classification === "ORPHANED"
  );
}

/**
 * Filters a list of channels based on internal visibility preference,
 * while ensuring that a currently selected channel ID remains pinned and addressable.
 */
export function filterVisibleChannels(
  channels: Channel[],
  showInternal: boolean,
  activeChannelId?: string,
): Channel[] {
  return channels.filter((ch) => {
    if (activeChannelId && ch.id === activeChannelId) return true;
    return isChannelVisible(ch, showInternal);
  });
}

/**
 * Maps an internal/provenance classification into a clean product-facing badge label.
 * Returns null for REAL_USER where no badge is required.
 */
export function getProvenanceBadgeLabel(classification: ChannelClassification): string | null {
  switch (classification) {
    case "TEST_FIXTURE":
      return "TEST";
    case "HISTORICAL_E2E":
      return "E2E";
    case "DEMO":
      return "DEMO";
    case "ORPHANED":
      return "INTERNAL";
    case "UNKNOWN":
      return "UNCLASSIFIED";
    case "REAL_USER":
      return null;
  }
}
