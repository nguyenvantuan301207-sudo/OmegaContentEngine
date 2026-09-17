import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  classifyChannel,
  isChannelVisible,
  isClassificationInternal,
  filterVisibleChannels,
  getProvenanceBadgeLabel,
} from "../src/lib/channel-classification.ts";
import {
  DEFAULT_PREFERENCES,
  persistPreferences,
  PREFERENCES_EVENT,
  readPreferences,
  type LocalPreferences,
} from "../src/lib/preferences.ts";
import type { Channel } from "../src/lib/api.ts";

const mockChannel = (overrides: Partial<Channel>): Channel => ({
  id: "chan-" + Math.random().toString(36).substring(2, 9),
  name: "Default Channel",
  slug: "default-channel",
  description: null,
  platform: "YOUTUBE",
  platform_channel_id: null,
  primary_language: "en",
  target_region: "US",
  timezone: "UTC",
  state: "ACTIVE",
  dna: {} as unknown as Channel["dna"],
  metadata: {},
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  archived_at: null,
  ...overrides,
});

test("normal mode hides TEST_FIXTURE", () => {
  const fixture = mockChannel({
    name: "Smoke Test Channel",
    slug: "smoke-test-channel-99",
  });
  const res = classifyChannel(fixture);
  assert.equal(res.classification, "TEST_FIXTURE");
  assert.equal(isClassificationInternal(res.classification), true);
  assert.equal(isChannelVisible(fixture, false), false);
});

test("normal mode hides HISTORICAL_E2E", () => {
  // Canonical historical E2E channel
  const canonicalE2E = mockChannel({
    id: "fa8813c9-9e7b-43d0-b76e-1bb323ad5a7a",
    name: "FastAPI Masterclass",
    slug: "fastapi-masterclass",
  });
  const res = classifyChannel(canonicalE2E);
  assert.equal(res.classification, "HISTORICAL_E2E");
  assert.equal(isClassificationInternal(res.classification), true);
  assert.equal(isChannelVisible(canonicalE2E, false), false);

  // Canary channel
  const canary = mockChannel({
    name: "QA Canary Stream",
    slug: "qa-canary-stream",
  });
  const canaryRes = classifyChannel(canary);
  assert.equal(canaryRes.classification, "HISTORICAL_E2E");
  assert.equal(isChannelVisible(canary, false), false);
});

test("normal mode preserves UNKNOWN channels without dropping them", () => {
  const regularChannel = mockChannel({
    id: "custom-user-chan-1",
    name: "Pacific Tech Reviews",
    slug: "pacific-tech-reviews",
  });
  const res = classifyChannel(regularChannel);
  assert.equal(res.classification, "UNKNOWN");
  assert.equal(isClassificationInternal(res.classification), false);
  assert.equal(res.badgeLabel, "UNCLASSIFIED");
  // CRITICAL: UNKNOWN channels must NEVER be hidden in normal mode
  assert.equal(isChannelVisible(regularChannel, false), true);
  assert.equal(isChannelVisible(regularChannel, true), true);
});

test("internal mode exposes hidden classes (TEST_FIXTURE, HISTORICAL_E2E, DEMO, ORPHANED)", () => {
  const testFixture = mockChannel({ name: "matrix-chan-12", slug: "matrix-chan-12" });
  const e2eFixture = mockChannel({ id: "fa8813c9-9e7b-43d0-b76e-1bb323ad5a7a", name: "FastAPI Masterclass" });
  const demoChannel = mockChannel({ name: "Demo Gaming Hub", slug: "demo-gaming-hub" });
  const orphanedChannel = mockChannel({ metadata: { orphaned: true } });

  assert.equal(isChannelVisible(testFixture, false), false);
  assert.equal(isChannelVisible(e2eFixture, false), false);
  assert.equal(isChannelVisible(demoChannel, false), false);
  assert.equal(isChannelVisible(orphanedChannel, false), false);

  assert.equal(isChannelVisible(testFixture, true), true);
  assert.equal(isChannelVisible(e2eFixture, true), true);
  assert.equal(isChannelVisible(demoChannel, true), true);
  assert.equal(isChannelVisible(orphanedChannel, true), true);
});

test("active internal channel remains addressable even in normal mode", () => {
  const normalChannel = mockChannel({ id: "norm-1", name: "Production Channel" });
  const activeInternalChannel = mockChannel({
    id: "active-test-1",
    name: "Matrix Test Channel",
    slug: "matrix-chan-999",
  });
  const otherInternalChannel = mockChannel({
    id: "other-test-2",
    name: "Matrix Test Channel 2",
    slug: "matrix-chan-888",
  });

  const allChannels = [normalChannel, activeInternalChannel, otherInternalChannel];

  // In normal mode with active channel pinned
  const filtered = filterVisibleChannels(allChannels, false, activeInternalChannel.id);

  // The active channel is retained so it is not silently dropped or switched
  assert.equal(filtered.some((c) => c.id === activeInternalChannel.id), true);
  // Normal channel is retained
  assert.equal(filtered.some((c) => c.id === normalChannel.id), true);
  // Other non-active internal channels remain hidden
  assert.equal(filtered.some((c) => c.id === otherInternalChannel.id), false);
});

test("no single-name hardcoded special casing for Shadow Pub or Orch Channel", () => {
  // Test that classification relies on generic pattern matching, not a single exact name string
  const c1 = mockChannel({ name: "Shadow Pub Channel A", slug: "shadow-pub-chan-a" });
  const c2 = mockChannel({ name: "Shadow Pub Channel B", slug: "shadow-pub-chan-b" });
  const c3 = mockChannel({ name: "Orch Channel 101", slug: "orch-chan-101" });
  const c4 = mockChannel({ name: "Orch Channel Production Gate", slug: "orch-channel-prod-gate" });

  // All should classify generically through pattern rules
  assert.equal(classifyChannel(c1).classification, "TEST_FIXTURE");
  assert.equal(classifyChannel(c2).classification, "TEST_FIXTURE");
  assert.equal(classifyChannel(c3).classification, "TEST_FIXTURE");
  assert.equal(classifyChannel(c4).classification, "TEST_FIXTURE");
});

test("shared preference defaults showInternalChannels to false", () => {
  assert.equal(DEFAULT_PREFERENCES.showInternalChannels, false);
});

test("shared preference read and persist roundtrips", () => {
  // Mock localStorage in global scope if running in Node
  const storage = new Map<string, string>();
  const originalLocalStorage = globalThis.localStorage;
  globalThis.localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
    clear: () => storage.clear(),
    length: storage.size,
    key: (i: number) => Array.from(storage.keys())[i] ?? null,
  };

  try {
    const initial = readPreferences();
    assert.equal(initial.showInternalChannels, false);

    persistPreferences({ ...initial, showInternalChannels: true });
    const updated = readPreferences();
    assert.equal(updated.showInternalChannels, true);

    persistPreferences({ ...initial, showInternalChannels: false });
    const reset = readPreferences();
    assert.equal(reset.showInternalChannels, false);
  } finally {
    globalThis.localStorage = originalLocalStorage;
  }
});

test("settings persists preferences outside React state updaters", () => {
  const source = readFileSync(
    new URL("../src/app/settings/page.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(source, /setPrefs\s*\(\s*\(/);
  assert.match(source, /setPrefs\(updated\);\s*persistPreferences\(updated\);/);
  assert.doesNotMatch(source, /setShowInternalChannels\s*\(/);
});

test("settings preference updates synchronize OperatorProvider without render-phase side effects", () => {
  const storage = new Map<string, string>();
  const originalLocalStorage = globalThis.localStorage;
  const originalWindow = (globalThis as unknown as { window?: unknown }).window;

  const eventTarget = new EventTarget();
  globalThis.localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
    clear: () => storage.clear(),
    length: storage.size,
    key: (i: number) => Array.from(storage.keys())[i] ?? null,
  };

  let isSettingsRendering = false;
  let crossComponentUpdateDuringRenderDetected = false;
  let operatorShowInternalChannels = false;
  let eventCount = 0;

  const handlePrefs = (event: Event) => {
    eventCount++;
    if (isSettingsRendering) {
      crossComponentUpdateDuringRenderDetected = true;
    }
    const customEvent = event as CustomEvent<Partial<LocalPreferences>>;
    if (customEvent.detail && typeof customEvent.detail.showInternalChannels === "boolean") {
      operatorShowInternalChannels = customEvent.detail.showInternalChannels;
    }
  };

  eventTarget.addEventListener(PREFERENCES_EVENT, handlePrefs);

  (globalThis as unknown as { window: unknown }).window = {
    localStorage: globalThis.localStorage,
    dispatchEvent: (event: Event) => eventTarget.dispatchEvent(event),
    addEventListener: (type: string, listener: EventListenerOrEventListenerObject) =>
      eventTarget.addEventListener(type, listener),
    removeEventListener: (type: string, listener: EventListenerOrEventListenerObject) =>
      eventTarget.removeEventListener(type, listener),
  };

  try {
    // 6. Initial preference hydration does not trigger a feedback loop or event dispatches
    const initialPrefs = readPreferences();
    assert.equal(initialPrefs.showInternalChannels, false);
    assert.equal(eventCount, 0, "Initial hydration must not dispatch preference events");

    // 1 & 4. Simulating SettingsPage updatePref outside render (clean contract)
    // SettingsPage render phase begins
    isSettingsRendering = true;
    // Pure render calculation (e.g. evaluating JSX / reading prefs)
    const currentPrefs = readPreferences();
    assert.equal(currentPrefs.showInternalChannels, false);
    // Render phase ends
    isSettingsRendering = false;

    // Event handler executes outside render
    const updatedPrefs: LocalPreferences = { ...currentPrefs, showInternalChannels: true };
    persistPreferences(updatedPrefs);

    // 1. Verifies no cross-component state update occurred during render
    assert.equal(crossComponentUpdateDuringRenderDetected, false);

    // 2. The preference still persists
    const persisted = readPreferences();
    assert.equal(persisted.showInternalChannels, true);

    // 3 & 4. OperatorProvider receives preference change and showInternalChannels updates correctly
    assert.equal(operatorShowInternalChannels, true);

    // 5. No duplicate preference event is produced (exactly 1 event for 1 update)
    assert.equal(eventCount, 1, "Exactly one event should be dispatched per preference persist");

    // Toggle back (true -> false)
    const toggledBack: LocalPreferences = { ...readPreferences(), showInternalChannels: false };
    persistPreferences(toggledBack);
    assert.equal(readPreferences().showInternalChannels, false);
    assert.equal(operatorShowInternalChannels, false);
    assert.equal(eventCount, 2, "Second update dispatches exactly one additional event");
    assert.equal(crossComponentUpdateDuringRenderDetected, false);

    // Invariant verification: if persistPreferences were invoked inside render, cross-component update is flagged
    isSettingsRendering = true;
    persistPreferences({ ...readPreferences(), showInternalChannels: true });
    isSettingsRendering = false;
    assert.equal(crossComponentUpdateDuringRenderDetected, true, "Calling persistPreferences during render must be detected as an invariant violation");
  } finally {
    eventTarget.removeEventListener(PREFERENCES_EVENT, handlePrefs);
    globalThis.localStorage = originalLocalStorage;
    (globalThis as unknown as { window?: unknown }).window = originalWindow;
  }
});

test("rapid sequential preference updates preserve unrelated preferences against stale closures", () => {
  const storage = new Map<string, string>();
  const originalLocalStorage = globalThis.localStorage;
  const originalWindow = (globalThis as unknown as { window?: unknown }).window;

  const eventTarget = new EventTarget();
  globalThis.localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
    clear: () => storage.clear(),
    length: storage.size,
    key: (i: number) => Array.from(storage.keys())[i] ?? null,
  };

  (globalThis as unknown as { window: unknown }).window = {
    localStorage: globalThis.localStorage,
    dispatchEvent: (event: Event) => eventTarget.dispatchEvent(event),
    addEventListener: (type: string, listener: EventListenerOrEventListenerObject) =>
      eventTarget.addEventListener(type, listener),
    removeEventListener: (type: string, listener: EventListenerOrEventListenerObject) =>
      eventTarget.removeEventListener(type, listener),
  };

  try {
    // Initial state: A (showInternalChannels)=false, B (reduceMotion)=false
    persistPreferences({ ...readPreferences(), showInternalChannels: false, reduceMotion: false });
    const initial = readPreferences();
    assert.equal(initial.showInternalChannels, false);
    assert.equal(initial.reduceMotion, false);

    // Simulate updatePref implementation in SettingsPage:
    const simulateUpdatePref = <K extends keyof LocalPreferences>(key: K, value: LocalPreferences[K]) => {
      const updated: LocalPreferences = {
        ...readPreferences(),
        [key]: value,
      };
      persistPreferences(updated);
    };

    // Scenario 1: Rapid sequential local updates
    // Update A=true
    simulateUpdatePref("showInternalChannels", true);
    // Immediately update B=true without SettingsPage having re-rendered (closure still has stale initial)
    simulateUpdatePref("reduceMotion", true);

    const afterRapid = readPreferences();
    assert.equal(afterRapid.showInternalChannels, true, "showInternalChannels must remain true");
    assert.equal(afterRapid.reduceMotion, true, "reduceMotion must be true");

    // Reset
    persistPreferences({ ...readPreferences(), showInternalChannels: false, reduceMotion: false });
    assert.equal(readPreferences().showInternalChannels, false);
    assert.equal(readPreferences().reduceMotion, false);

    // Scenario 2: External update A=true followed immediately by local update B=true
    // External update from OperatorProvider / Channels
    persistPreferences({ ...readPreferences(), showInternalChannels: true });

    // Local update B=true (even if local component had not re-rendered)
    simulateUpdatePref("reduceMotion", true);

    const afterExternalAndLocal = readPreferences();
    assert.equal(afterExternalAndLocal.showInternalChannels, true, "External update A=true must be preserved");
    assert.equal(afterExternalAndLocal.reduceMotion, true, "Local update B=true must be applied");
  } finally {
    globalThis.localStorage = originalLocalStorage;
    (globalThis as unknown as { window?: unknown }).window = originalWindow;
  }
});

test("production visibility correctly segments internal vs product records", () => {
  const normalChannel = mockChannel({ id: "norm-chan", name: "Main Stream" });
  const testChannel = mockChannel({ id: "test-chan", name: "matrix-chan-1", slug: "matrix-chan-1" });

  const productions = [
    { id: "prod-1", channel_id: normalChannel.id, title: "Episode 1" },
    { id: "prod-2", channel_id: testChannel.id, title: "Test Run 1" },
  ];

  const normalVisible = productions.filter((p) => {
    const channel = p.channel_id === normalChannel.id ? normalChannel : testChannel;
    return isChannelVisible(channel, false);
  });

  const internalVisible = productions.filter((p) => {
    const channel = p.channel_id === normalChannel.id ? normalChannel : testChannel;
    return isChannelVisible(channel, true);
  });

  assert.equal(normalVisible.length, 1);
  assert.equal(normalVisible[0].id, "prod-1");
  assert.equal(internalVisible.length, 2);
});

test("getProvenanceBadgeLabel maps classifications to clean product badges", () => {
  assert.equal(getProvenanceBadgeLabel("TEST_FIXTURE"), "TEST");
  assert.equal(getProvenanceBadgeLabel("HISTORICAL_E2E"), "E2E");
  assert.equal(getProvenanceBadgeLabel("DEMO"), "DEMO");
  assert.equal(getProvenanceBadgeLabel("ORPHANED"), "INTERNAL");
  assert.equal(getProvenanceBadgeLabel("UNKNOWN"), "UNCLASSIFIED");
  assert.equal(getProvenanceBadgeLabel("REAL_USER"), null);
});
