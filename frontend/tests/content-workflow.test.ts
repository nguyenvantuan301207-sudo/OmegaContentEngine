import assert from "node:assert/strict";
import test from "node:test";
import {
  canFinalizeSelection, canMaterializeCampaign, canStartCampaignMission,
  isCanonicalSelectionCandidateEligible, isSelectionRunCampaignEligible, isValidSelectionCount,
  moveCampaignItem, normalizePlannedReleaseInput, summarizeHistoricalPerformanceEvidence,
  reuseSubmissionKey,
  validateCampaignDraftItems,
} from "../src/lib/content-workflow.ts";
import { getBreadcrumbs, getChannelNavigation } from "../src/lib/navigation.ts";
import { readFileSync } from "node:fs";
import {
  fetchTopicResearchBriefs,
  formatResearchConfidence,
  getBriefEmptyStateMessage,
  isSufficientResearchBrief,
  sortResearchBriefs,
} from "../src/lib/research-authority.ts";
import type {
  ProductionRequest,
  ResearchBriefSummary,
  ResearchRequest,
  ScriptVersionSummary,
} from "../src/lib/api.ts";
import {
  isScriptEligibleForProduction,
  proceedToProductionHandoff,
  resolveCurrentScript,
} from "../src/lib/content-production-handoff.ts";

test("research confidence preserves the backend 0..100 percentage scale", () => {
  assert.equal(formatResearchConfidence(50.45), "50.45%");
  assert.equal(formatResearchConfidence(0), "0%");
  assert.equal(formatResearchConfidence(100), "100%");
  assert.equal(formatResearchConfidence(0.5), "0.5%");
  assert.equal(formatResearchConfidence(Number.NaN), "Unavailable");
  for (const page of ["content", "research"]) {
    const source = readFileSync(new URL(`../src/app/channels/[id]/${page}/page.tsx`, import.meta.url), "utf8");
    assert.ok(source.includes("formatResearchConfidence("));
    assert.doesNotMatch(source, /(?:overall_confidence|confidence_score)\s*\*\s*100/);
  }
});

test("only sufficient briefs are selectable and historical blocked evidence remains visible", () => {
  const briefs = [{ id: "partial", outcome: "PARTIAL" }, { id: "insufficient", outcome: "INSUFFICIENT" }, { id: "sufficient", outcome: "SUFFICIENT" }];
  assert.equal(isSufficientResearchBrief(briefs[0]), false);
  assert.equal(isSufficientResearchBrief(briefs[1]), false);
  assert.equal(isSufficientResearchBrief(briefs[2]), true);
  assert.equal(isSufficientResearchBrief(undefined), false);
  assert.equal(isSufficientResearchBrief({ outcome: "UNKNOWN" }), false);
  assert.equal(briefs.find(isSufficientResearchBrief)?.id, "sufficient");
  assert.equal(briefs.slice(0, 2).find(isSufficientResearchBrief), undefined);
  assert.equal(briefs.length, 3);
  const page = readFileSync(new URL("../src/app/channels/[id]/content/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes("disabled={!isSufficientResearchBrief(briefItem)}"));
  assert.ok(page.includes("values.find(isSufficientResearchBrief)"));
  assert.ok(page.includes("!isSufficientResearchBrief(selectedBrief)"));
  assert.ok(page.includes("No SUFFICIENT research brief"));
});

test("canonical selection eligibility and bounds", () => {
  for (const status of ["DISCOVERED", "EVALUATED", "RECOMMENDED"] as const) assert.equal(isCanonicalSelectionCandidateEligible({ status }), true);
  for (const status of ["SELECTED", "REJECTED", "ARCHIVED"] as const) assert.equal(isCanonicalSelectionCandidateEligible({ status }), false);
  for (const count of [1, 50]) assert.equal(isValidSelectionCount(count), true);
  for (const count of [0, 51, 1.5]) assert.equal(isValidSelectionCount(count), false);
});

test("submission keys survive retry and change only for a new attempt", () => {
  let generated = 0;
  const makeKey = () => `attempt-${++generated}`;
  const first = reuseSubmissionKey(null, makeKey);
  assert.equal(reuseSubmissionKey(first, makeKey), first);
  assert.equal(generated, 1);
  assert.notEqual(reuseSubmissionKey(null, makeKey), first);
});

test("POLICY and OVERRIDE finalization require considered winner and actor", () => {
  const run = { status: "READY" as const, recommended_candidate_id: "a", decisions: [{ candidate_id: "a" }, { candidate_id: "b" }] };
  assert.equal(canFinalizeSelection(run, "a", "operator", ""), true);
  assert.equal(canFinalizeSelection(run, "b", "operator", "because",), true);
  assert.equal(canFinalizeSelection(run, "b", "operator", "  "), false);
  assert.equal(canFinalizeSelection(run, "c", "operator", "reason"), false);
  assert.equal(canFinalizeSelection(run, "a", "  ", ""), false);
  assert.equal(canFinalizeSelection({ ...run, status: "SELECTED" }, "a", "operator", ""), false);
});

test("historical evidence labels neutral insufficiency accurately", () => {
  assert.deepEqual(summarizeHistoricalPerformanceEvidence({ historical_performance_status: "APPLIED", historical_performance_score: 72, historical_performance_match_count: 3, historical_performance_corpus_count: 7, historical_performance_policy_version: 1 }),
    { score: 72, status: "APPLIED", label: "Applied historical performance", matches: 3, corpus: 7, policyVersion: 1 });
  assert.match(summarizeHistoricalPerformanceEvidence({ historical_performance_status: "INSUFFICIENT_CORPUS", historical_performance_score: 50 }).label, /Insufficient history/);
  assert.match(summarizeHistoricalPerformanceEvidence({ historical_performance_status: "INSUFFICIENT_RELEVANT_HISTORY", historical_performance_score: 50 }).label, /Insufficient relevant history/);
});

test("campaign order remains array order without caller position", () => {
  const items = [{ selection_run_id: "A" }, { selection_run_id: "B" }, { selection_run_id: "C" }];
  assert.deepEqual(moveCampaignItem(items, 1, 0).map((item) => item.selection_run_id), ["B", "A", "C"]);
  assert.deepEqual(moveCampaignItem(items, 1, 2).map((item) => item.selection_run_id), ["A", "C", "B"]);
  assert.equal(Object.hasOwn(items[0], "position"), false);
  assert.deepEqual(items.map((item) => item.selection_run_id), ["A", "B", "C"]);
  assert.deepEqual(moveCampaignItem(items, 0, -1), items);
  assert.deepEqual(moveCampaignItem(items, 2, 3), items);
});

test("campaign draft rejects duplicate runs and winners, DNA mismatch, invalid release order", () => {
  const runs = [
    { id: "A", status: "SELECTED" as const, selected_candidate_id: "winner-A", channel_dna_revision_id: "dna" },
    { id: "B", status: "SELECTED" as const, selected_candidate_id: "winner-B", channel_dna_revision_id: "dna" },
    { id: "C", status: "READY" as const, selected_candidate_id: null, channel_dna_revision_id: "dna" },
  ];
  const a = { selectionRunId: "A", localRelease: "2026-09-20T10:00" };
  const b = { selectionRunId: "B", localRelease: "2026-09-21T10:00" };
  assert.equal(validateCampaignDraftItems([a, b], runs), null);
  assert.match(validateCampaignDraftItems([], runs) || "", /1–50/);
  assert.match(validateCampaignDraftItems(Array.from({ length: 51 }, () => a), runs) || "", /1–50/);
  assert.match(validateCampaignDraftItems([a, a], runs) || "", /distinct finalized/);
  assert.match(validateCampaignDraftItems([a, { selectionRunId: "C", localRelease: "" }], runs) || "", /distinct finalized/);
  assert.match(validateCampaignDraftItems([b, a], runs) || "", /increase/);
  assert.equal(validateCampaignDraftItems([{ ...a, localRelease: "" }, b], runs), null);
  assert.match(validateCampaignDraftItems([a, { ...b, localRelease: "not-a-date" }], runs) || "", /valid local release/);
  assert.match(validateCampaignDraftItems([a, b], [{ ...runs[0] }, { ...runs[1], selected_candidate_id: "winner-A" }]) || "", /different selected candidate/);
  assert.match(validateCampaignDraftItems([a, b], [{ ...runs[0] }, { ...runs[1], channel_dna_revision_id: "other" }]) || "", /same DNA revision/);
});

test("planned release is null or offset-aware UTC, never naive", () => {
  assert.equal(normalizePlannedReleaseInput(""), null);
  assert.match(normalizePlannedReleaseInput("2026-09-20T14:30") || "", /^\d{4}-\d\d-\d\dT\d\d:\d\d:.*Z$/);
  assert.throws(() => normalizePlannedReleaseInput("2026-02-30T12:00"), /valid local release/);
  assert.throws(() => normalizePlannedReleaseInput("2026-09-20T14:30Z"), /valid local release/);
});

test("materialization and independent Mission start eligibility", () => {
  assert.equal(canMaterializeCampaign(false, false), true);
  assert.equal(canMaterializeCampaign(true, false), false);
  assert.equal(canMaterializeCampaign(false, true), false);
  assert.equal(isSelectionRunCampaignEligible({ status: "SELECTED", selected_candidate_id: "a" }), true);
  assert.equal(isSelectionRunCampaignEligible({ status: "READY", selected_candidate_id: null }), false);
  assert.equal(canStartCampaignMission({ mission_state: "READY" }), true);
  for (const mission_state of ["RUNNING", "PAUSED", "SUCCEEDED", "FAILED", "CANCELLED", "DRAFT"] as const) {
    assert.equal(canStartCampaignMission({ mission_state }), false);
  }
  const siblings = [{ mission_state: "READY" as const }, { mission_state: "READY" as const }];
  const afterOne = [{ mission_state: "RUNNING" as const }, siblings[1]];
  assert.equal(afterOne[1], siblings[1]);
});

test("campaign navigation and breadcrumbs remain channel scoped", () => {
  assert.deepEqual(getChannelNavigation("channel").map((item) => item.key), ["dna", "branding", "topics", "campaigns", "research", "content", "production"]);
  assert.deepEqual(getBreadcrumbs("/channels/channel/campaigns/new", "Demo").map((item) => item.label), ["Channels", "Demo", "Campaigns", "New campaign"]);
  assert.deepEqual(getBreadcrumbs("/channels/channel/campaigns/123", "Demo").map((item) => item.label), ["Channels", "Demo", "Campaigns", "Campaign details"]);
});


test("Content Studio loads persisted Narrative Plan authority before the Script tab", () => {
  const page = readFileSync(new URL("../src/app/channels/[id]/content/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes("getNarrativePlan(channelId, request.id)"));
  assert.ok(page.includes('useState<ContentView>("narrative")'));
  assert.ok(page.indexOf('id: "narrative"') < page.indexOf('id: "script"'));
  assert.ok(page.includes("<NarrativePlanPanel plan={plan} script={script} />"));
  assert.ok(page.includes('planResult.status === "fulfilled"'));
});

test("Narrative Plan surface preserves section order, grounding, QA and script lineage", () => {
  const panel = readFileSync(new URL("../src/components/workflow/NarrativePlanPanel.tsx", import.meta.url), "utf8");
  for (const field of ["plan.version", "plan.status", "plan.format_profile", "plan.target_duration_seconds", "plan.estimated_duration_seconds", "section.role", "section.objective", "section.key_information", "section.grounding_references", "qa?.status", "qa?.findings", "script.narrative_plan_id", "script.narrative_plan_version"]) assert.ok(panel.includes(field), field);
  assert.ok(panel.includes("a.section_order - b.section_order"));
  assert.ok(panel.includes("Research Brief \u2192 Narrative Plan \u2192 Script"));
  assert.ok(panel.includes("Historical script without a narrative plan."));
});

test("topic candidate ID resolves through research requests and is not passed to listResearchBriefs", async () => {
  const channelId = "chan-omega-1";
  const topicCandidateId = "topic-concrete-cracks";
  const requestCalls: Array<{ channelId: string; status?: string; topicCandidateId?: string }> = [];
  const briefCalls: Array<{ channelId: string; requestId: string }> = [];

  const mockFetchRequests = async (cId: string, status?: string, tId?: string): Promise<ResearchRequest[]> => {
    requestCalls.push({ channelId: cId, status, topicCandidateId: tId });
    return [
      {
        id: "req-hist-1",
        channel_id: cId,
        topic_candidate_id: tId || "",
        mode: "INTERACTIVE",
        status: "SUCCEEDED",
        created_at: "2026-10-01T10:00:00Z",
        language: "en",
        region: "US",
        max_sources: 5,
        minimum_source_quality: 0.5,
        minimum_claim_confidence: 0.5,
      },
    ];
  };

  const mockFetchBriefs = async (cId: string, rId: string): Promise<ResearchBriefSummary[]> => {
    briefCalls.push({ channelId: cId, requestId: rId });
    return [
      {
        id: "brief-hist-1",
        research_request_id: rId,
        version: 1,
        is_current: false,
        outcome: "INSUFFICIENT",
        overall_confidence: 45.2,
        verified_claims_count: 2,
        contradictions_count: 1,
        created_at: "2026-10-01T10:05:00Z",
      },
    ];
  };

  const results = await fetchTopicResearchBriefs(channelId, topicCandidateId, mockFetchRequests, mockFetchBriefs);

  // 1. Research requests are first resolved by topic_candidate_id
  assert.equal(requestCalls.length, 1);
  assert.equal(requestCalls[0].channelId, channelId);
  assert.equal(requestCalls[0].topicCandidateId, topicCandidateId);

  // 2. TopicCandidate ID is NOT passed directly to listResearchBriefs; researchRequest.id IS passed
  assert.equal(briefCalls.length, 1);
  assert.equal(briefCalls[0].channelId, channelId);
  assert.equal(briefCalls[0].requestId, "req-hist-1");
  assert.notEqual(briefCalls[0].requestId, topicCandidateId);

  assert.equal(results.length, 1);
  assert.equal(results[0].id, "brief-hist-1");
});

test("briefs from multiple research requests for the same topic are aggregated and sorted newest first", async () => {
  const channelId = "chan-omega-1";
  const topicCandidateId = "topic-concrete-cracks";

  const mockRequests: ResearchRequest[] = [
    {
      id: "req-historical-insufficient",
      channel_id: channelId,
      topic_candidate_id: topicCandidateId,
      mode: "INTERACTIVE",
      status: "SUCCEEDED",
      created_at: "2026-10-01T10:00:00Z",
      language: "en",
      region: "US",
      max_sources: 5,
      minimum_source_quality: 0.5,
      minimum_claim_confidence: 0.5,
    },
    {
      id: "req-newer-sufficient",
      channel_id: channelId,
      topic_candidate_id: topicCandidateId,
      mode: "INTERACTIVE",
      status: "SUCCEEDED",
      created_at: "2026-10-02T10:00:00Z",
      language: "en",
      region: "US",
      max_sources: 5,
      minimum_source_quality: 0.5,
      minimum_claim_confidence: 0.5,
    },
  ];

  const briefMap: Record<string, ResearchBriefSummary[]> = {
    "req-historical-insufficient": [
      {
        id: "brief-v1-insufficient",
        research_request_id: "req-historical-insufficient",
        version: 1,
        is_current: false,
        outcome: "INSUFFICIENT",
        overall_confidence: 42.1,
        verified_claims_count: 1,
        contradictions_count: 2,
        created_at: "2026-10-01T10:30:00Z",
      },
    ],
    "req-newer-sufficient": [
      {
        id: "brief-v2-sufficient",
        research_request_id: "req-newer-sufficient",
        version: 2,
        is_current: true,
        outcome: "SUFFICIENT",
        overall_confidence: 72.35,
        verified_claims_count: 8,
        contradictions_count: 0,
        created_at: "2026-10-02T11:00:00Z",
      },
    ],
  };

  const results = await fetchTopicResearchBriefs(
    channelId,
    topicCandidateId,
    async () => mockRequests,
    async (_cid, reqId) => briefMap[reqId] || [],
  );

  // Aggregated from both requests without discarding historical briefs
  assert.equal(results.length, 2);

  // Sorted deterministically, newest first by created_at
  assert.equal(results[0].id, "brief-v2-sufficient");
  assert.equal(results[1].id, "brief-v1-insufficient");

  // SUFFICIENT brief is selectable
  assert.equal(isSufficientResearchBrief(results[0]), true);
  assert.equal(results[0].overall_confidence, 72.35);

  // INSUFFICIENT / PARTIAL brief is disabled (not sufficient)
  assert.equal(isSufficientResearchBrief(results[1]), false);

  // Selector finds the SUFFICIENT brief
  const selectedBrief = results.find(isSufficientResearchBrief);
  assert.equal(selectedBrief?.id, "brief-v2-sufficient");
});

test("zero research requests returns empty briefs and provides clear empty-state message", async () => {
  const emptyResults = await fetchTopicResearchBriefs(
    "chan-1",
    "topic-no-research",
    async () => [],
    async () => [],
  );
  assert.deepEqual(emptyResults, []);

  // Empty state messages
  assert.equal(
    getBriefEmptyStateMessage(true, 0, false),
    "No research requests or briefs found for this topic. Run research before generating content.",
  );
  assert.equal(
    getBriefEmptyStateMessage(true, 1, false),
    "No SUFFICIENT research brief is available for this topic. Complete research before creating content.",
  );
  assert.equal(getBriefEmptyStateMessage(true, 1, true), undefined);
  assert.equal(getBriefEmptyStateMessage(false, 0, false), undefined);
});

test("Content Studio page implements helper extraction, automatic initial load, and stale selection clearing", () => {
  const page = readFileSync(new URL("../src/app/channels/[id]/content/page.tsx", import.meta.url), "utf8");

  // Helper loadBriefsForTopic is extracted and reused
  assert.ok(page.includes("loadBriefsForTopic = useCallback"));
  assert.ok(page.includes("async function changeTopic(id: string)"));
  assert.ok(page.includes("await loadBriefsForTopic(id)"));

  // Initial load auto-selects default topic and calls loadBriefsForTopic immediately
  assert.ok(page.includes("await loadBriefsForTopic(initialTopicId)"));

  // Stale brief selection is cleared on topic change
  assert.ok(page.includes('setBriefId("")'));
  assert.ok(page.includes("setBriefs([])"));

  // TopicCandidate ID is NOT passed directly to listResearchBriefs in content/page.tsx
  assert.doesNotMatch(page, /listResearchBriefs\(\s*channelId\s*,\s*id\s*\)/);

  // Routes discovery through fetchTopicResearchBriefs
  assert.ok(page.includes("fetchTopicResearchBriefs("));

  // Empty-state messages in dialog FormField
  assert.ok(page.includes("No research requests or briefs found for this topic"));
  assert.ok(page.includes("No research briefs available"));
});

test("sortResearchBriefs sorts deterministically newest first with tiebreakers", () => {
  const b1: ResearchBriefSummary = {
    id: "brief-old",
    research_request_id: "req-1",
    version: 1,
    is_current: false,
    outcome: "INSUFFICIENT",
    overall_confidence: 40,
    verified_claims_count: 1,
    contradictions_count: 0,
    created_at: "2026-10-01T00:00:00Z",
  };
  const b2: ResearchBriefSummary = {
    id: "brief-new",
    research_request_id: "req-2",
    version: 1,
    is_current: true,
    outcome: "SUFFICIENT",
    overall_confidence: 72,
    verified_claims_count: 5,
    contradictions_count: 0,
    created_at: "2026-10-02T00:00:00Z",
  };
  const b3: ResearchBriefSummary = {
    id: "brief-same-time-v2",
    research_request_id: "req-3",
    version: 2,
    is_current: true,
    outcome: "SUFFICIENT",
    overall_confidence: 80,
    verified_claims_count: 6,
    contradictions_count: 0,
    created_at: "2026-10-02T00:00:00Z",
  };

  const sorted = sortResearchBriefs([b1, b2, b3]);
  assert.deepEqual(
    sorted.map((b) => b.id),
    ["brief-same-time-v2", "brief-new", "brief-old"],
  );
});

test("proceed to production uses current ScriptVersion and historical script v1 is excluded when script v2 is current", () => {
  const v1Historical: ScriptVersionSummary = {
    id: "script-v1-hist",
    content_request_id: "req-1",
    version: 1,
    is_current: false,
    title: "Script v1",
    estimated_word_count: 500,
    estimated_duration_seconds: 120,
    qa_status: "PASSED",
    created_at: "2026-10-01T00:00:00Z",
  };
  const v2Current: ScriptVersionSummary = {
    id: "script-v2-curr",
    content_request_id: "req-1",
    version: 2,
    is_current: true,
    title: "Script v2",
    estimated_word_count: 520,
    estimated_duration_seconds: 125,
    qa_status: "PASSED",
    created_at: "2026-10-02T00:00:00Z",
  };

  // When both v1 and v2 are present in any order, resolveCurrentScript always picks v2
  assert.equal(resolveCurrentScript([v1Historical, v2Current])?.id, "script-v2-curr");
  assert.equal(resolveCurrentScript([v2Current, v1Historical])?.id, "script-v2-curr");
  assert.equal(resolveCurrentScript([v1Historical, v2Current])?.is_current, true);

  // Single script fallback
  assert.equal(resolveCurrentScript([v1Historical])?.id, "script-v1-hist");
  assert.equal(resolveCurrentScript([]), null);
});

test("QA PASSED and PASSED_WITH_WARNINGS allow proceeding; BLOCKED and other states block proceeding", () => {
  assert.equal(isScriptEligibleForProduction("PASSED"), true);
  assert.equal(isScriptEligibleForProduction("PASSED_WITH_WARNINGS"), true);
  assert.equal(isScriptEligibleForProduction("BLOCKED"), false);
  assert.equal(isScriptEligibleForProduction("PENDING"), false);
  assert.equal(isScriptEligibleForProduction("FAILED"), false);
  assert.equal(isScriptEligibleForProduction(null), false);
  assert.equal(isScriptEligibleForProduction(undefined), false);
});

test("proceedToProductionHandoff reuses existing ProductionRequest without creating duplicate", async () => {
  const v1Historical: ScriptVersionSummary = {
    id: "script-v1-hist",
    content_request_id: "req-1",
    version: 1,
    is_current: false,
    title: "Script v1",
    estimated_word_count: 500,
    estimated_duration_seconds: 120,
    qa_status: "PASSED",
    created_at: "2026-10-01T00:00:00Z",
  };
  const v2Current: ScriptVersionSummary = {
    id: "script-v2-curr",
    content_request_id: "req-1",
    version: 2,
    is_current: true,
    title: "Script v2",
    estimated_word_count: 520,
    estimated_duration_seconds: 125,
    qa_status: "PASSED",
    created_at: "2026-10-02T00:00:00Z",
  };

  const existingProduction: ProductionRequest = {
    id: "prod-existing-1",
    channel_id: "chan-1",
    script_version_id: "script-v2-curr",
    content_request_id: "req-1",
    channel_dna_revision_id: "dna-1",
    mode: "INTERACTIVE",
    status: "READY",
    target_width: 1920,
    target_height: 1080,
    fps: 30,
    video_codec: "h264",
    audio_codec: "aac",
    container_format: "mp4",
    created_at: "2026-10-03T00:00:00Z",
  };

  let listCalled = 0;
  let createCalled = 0;

  const result = await proceedToProductionHandoff({
    channelId: "chan-1",
    scripts: [v1Historical, v2Current],
    listRequests: async (ch) => {
      listCalled++;
      assert.equal(ch, "chan-1");
      return [existingProduction];
    },
    createRequest: async () => {
      createCalled++;
      throw new Error("Should not be called when request already exists");
    },
  });

  assert.equal(listCalled, 1);
  assert.equal(createCalled, 0); // No duplicate created
  assert.equal(result.reused, true);
  assert.equal(result.requestId, "prod-existing-1");
  assert.equal(result.scriptVersionId, "script-v2-curr");
});

test("proceedToProductionHandoff creates a new ProductionRequest pinned to current ScriptVersion v2 when missing", async () => {
  const v1Historical: ScriptVersionSummary = {
    id: "script-v1-hist",
    content_request_id: "req-1",
    version: 1,
    is_current: false,
    title: "Script v1",
    estimated_word_count: 500,
    estimated_duration_seconds: 120,
    qa_status: "PASSED",
    created_at: "2026-10-01T00:00:00Z",
  };
  const v2Current: ScriptVersionSummary = {
    id: "script-v2-curr",
    content_request_id: "req-1",
    version: 2,
    is_current: true,
    title: "Script v2",
    estimated_word_count: 520,
    estimated_duration_seconds: 125,
    qa_status: "PASSED_WITH_WARNINGS",
    created_at: "2026-10-02T00:00:00Z",
  };

  // Existing production is pinned to historical v1, NOT v2
  const oldProduction: ProductionRequest = {
    id: "prod-old-v1",
    channel_id: "chan-1",
    script_version_id: "script-v1-hist",
    content_request_id: "req-1",
    channel_dna_revision_id: "dna-1",
    mode: "INTERACTIVE",
    status: "READY",
    target_width: 1920,
    target_height: 1080,
    fps: 30,
    video_codec: "h264",
    audio_codec: "aac",
    container_format: "mp4",
    created_at: "2026-10-01T05:00:00Z",
  };

  let createPayloadReceived: { script_version_id: string } | null = null;

  const result = await proceedToProductionHandoff({
    channelId: "chan-1",
    scripts: [v1Historical, v2Current],
    listRequests: async () => [oldProduction],
    createRequest: async (_ch, payload) => {
      createPayloadReceived = payload;
      return {
        id: "prod-new-v2",
        channel_id: "chan-1",
        script_version_id: payload.script_version_id,
        content_request_id: "req-1",
        channel_dna_revision_id: "dna-1",
        mode: "INTERACTIVE",
        status: "READY",
        target_width: 1920,
        target_height: 1080,
        fps: 30,
        video_codec: "h264",
        audio_codec: "aac",
        container_format: "mp4",
        created_at: "2026-10-04T00:00:00Z",
      };
    },
  });

  assert.equal(result.reused, false);
  assert.equal(result.requestId, "prod-new-v2");
  assert.equal(result.scriptVersionId, "script-v2-curr");
  assert.deepEqual(createPayloadReceived, { script_version_id: "script-v2-curr" });
  assert.notEqual(createPayloadReceived?.script_version_id, "script-v1-hist");
});

test("proceedToProductionHandoff creates new attempt when existing production for current script is FAILED", async () => {
  const v2Current: ScriptVersionSummary = {
    id: "script-v2-curr",
    content_request_id: "req-1",
    version: 2,
    is_current: true,
    title: "Script v2",
    estimated_word_count: 520,
    estimated_duration_seconds: 125,
    qa_status: "PASSED",
    created_at: "2026-10-02T00:00:00Z",
  };

  const failedProduction: ProductionRequest = {
    id: "prod-failed-v2",
    channel_id: "chan-1",
    script_version_id: "script-v2-curr",
    content_request_id: "req-1",
    channel_dna_revision_id: "dna-1",
    mode: "INTERACTIVE",
    status: "FAILED",
    outcome: "BLOCKED",
    target_width: 1920,
    target_height: 1080,
    fps: 30,
    video_codec: "h264",
    audio_codec: "aac",
    container_format: "mp4",
    created_at: "2026-10-03T00:00:00Z",
  };

  let createCalled = false;
  const result = await proceedToProductionHandoff({
    channelId: "chan-1",
    scripts: [v2Current],
    listRequests: async () => [failedProduction],
    createRequest: async (_ch, payload) => {
      createCalled = true;
      return {
        id: "prod-retry-v2",
        channel_id: "chan-1",
        script_version_id: payload.script_version_id,
        content_request_id: "req-1",
        channel_dna_revision_id: "dna-1",
        mode: "INTERACTIVE",
        status: "READY",
        target_width: 1920,
        target_height: 1080,
        fps: 30,
        video_codec: "h264",
        audio_codec: "aac",
        container_format: "mp4",
        created_at: "2026-10-04T00:00:00Z",
      };
    },
  });

  assert.equal(createCalled, true);
  assert.equal(result.reused, false);
  assert.equal(result.requestId, "prod-retry-v2");
});

test("proceedToProductionHandoff creates new attempt when existing production for current script is CANCELLED", async () => {
  const v2Current: ScriptVersionSummary = {
    id: "script-v2-curr",
    content_request_id: "req-1",
    version: 2,
    is_current: true,
    title: "Script v2",
    estimated_word_count: 520,
    estimated_duration_seconds: 125,
    qa_status: "PASSED",
    created_at: "2026-10-02T00:00:00Z",
  };

  const cancelledProduction: ProductionRequest = {
    id: "prod-cancelled-v2",
    channel_id: "chan-1",
    script_version_id: "script-v2-curr",
    content_request_id: "req-1",
    channel_dna_revision_id: "dna-1",
    mode: "INTERACTIVE",
    status: "CANCELLED",
    outcome: "BLOCKED",
    target_width: 1920,
    target_height: 1080,
    fps: 30,
    video_codec: "h264",
    audio_codec: "aac",
    container_format: "mp4",
    created_at: "2026-10-03T00:00:00Z",
  };

  let createCalled = false;
  const result = await proceedToProductionHandoff({
    channelId: "chan-1",
    scripts: [v2Current],
    listRequests: async () => [cancelledProduction],
    createRequest: async (_ch, payload) => {
      createCalled = true;
      return {
        id: "prod-retry2-v2",
        channel_id: "chan-1",
        script_version_id: payload.script_version_id,
        content_request_id: "req-1",
        channel_dna_revision_id: "dna-1",
        mode: "INTERACTIVE",
        status: "READY",
        target_width: 1920,
        target_height: 1080,
        fps: 30,
        video_codec: "h264",
        audio_codec: "aac",
        container_format: "mp4",
        created_at: "2026-10-04T00:00:00Z",
      };
    },
  });

  assert.equal(createCalled, true);
  assert.equal(result.reused, false);
  assert.equal(result.requestId, "prod-retry2-v2");
});

test("proceedToProductionHandoff rejects when current script is BLOCKED", async () => {
  const v2Blocked: ScriptVersionSummary = {
    id: "script-v2-blocked",
    content_request_id: "req-1",
    version: 2,
    is_current: true,
    title: "Script v2",
    estimated_word_count: 520,
    estimated_duration_seconds: 125,
    qa_status: "BLOCKED",
    created_at: "2026-10-02T00:00:00Z",
  };

  let listCalled = false;
  let createCalled = false;

  await assert.rejects(
    async () => {
      await proceedToProductionHandoff({
        channelId: "chan-1",
        scripts: [v2Blocked],
        listRequests: async () => {
          listCalled = true;
          return [];
        },
        createRequest: async () => {
          createCalled = true;
          throw new Error("unreachable");
        },
      });
    },
    /Script QA status is BLOCKED/,
  );

  assert.equal(listCalled, false);
  assert.equal(createCalled, false);
});

test("proceedToProductionHandoff surfaces API failures from listRequests or createRequest", async () => {
  const v2Current: ScriptVersionSummary = {
    id: "script-v2-curr",
    content_request_id: "req-1",
    version: 2,
    is_current: true,
    title: "Script v2",
    estimated_word_count: 520,
    estimated_duration_seconds: 125,
    qa_status: "PASSED",
    created_at: "2026-10-02T00:00:00Z",
  };

  await assert.rejects(
    async () => {
      await proceedToProductionHandoff({
        channelId: "chan-1",
        scripts: [v2Current],
        listRequests: async () => {
          throw new Error("Network connection dropped");
        },
        createRequest: async () => {
          throw new Error("unreachable");
        },
      });
    },
    /Network connection dropped/,
  );

  await assert.rejects(
    async () => {
      await proceedToProductionHandoff({
        channelId: "chan-1",
        scripts: [v2Current],
        listRequests: async () => [],
        createRequest: async () => {
          throw new Error("Backend production validation failure");
        },
      });
    },
    /Backend production validation failure/,
  );
});

test("Content Studio page statically verifies proceed to production wiring, navigation, and state safeguards", () => {
  const page = readFileSync(
    new URL("../src/app/channels/[id]/content/page.tsx", import.meta.url),
    "utf8",
  );

  // Must import handoff logic
  assert.ok(page.includes("proceedToProductionHandoff"));
  assert.ok(page.includes("resolveCurrentScript"));
  assert.ok(page.includes("isScriptEligibleForProduction"));
  assert.ok(page.includes("createProductionRequest"));
  assert.ok(page.includes("listProductionRequests"));

  // Passive Link must NOT be used for Proceed to production
  assert.doesNotMatch(page, /<Link[^>]*>[^<]*Proceed to production[^<]*<\/Link>/);

  // Must use action button
  assert.ok(page.includes("handleProceedToProduction"));
  assert.ok(page.includes("proceedingProduction"));
  assert.ok(page.includes("router.push(\"/production\")"));

  // Disabled guard checks
  assert.ok(page.includes("!isQaEligible"));
  assert.ok(page.includes("!currentScript"));
});

test("Production Studio page provides authoritative empty state guidance back to Content Studio", () => {
  const prodPage = readFileSync(
    new URL("../src/app/production/page.tsx", import.meta.url),
    "utf8",
  );

  assert.ok(
    prodPage.includes(
      "No persisted production exists for this channel. Return to Content Studio and proceed from an eligible current script.",
    ),
  );
  assert.ok(prodPage.includes("Return to Content Studio"));
});
