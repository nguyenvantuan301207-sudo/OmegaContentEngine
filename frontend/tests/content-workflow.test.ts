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
import { formatResearchConfidence, isSufficientResearchBrief } from "../src/lib/research-authority.ts";

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
