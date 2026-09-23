import type {
  ContentCampaignItemExecution,
  ContentSelectionRun,
  HistoricalPerformanceEvidence,
  TopicCandidate,
} from "./api";

export function isCanonicalSelectionCandidateEligible(candidate: Pick<TopicCandidate, "status">): boolean {
  return candidate.status === "DISCOVERED" || candidate.status === "EVALUATED" || candidate.status === "RECOMMENDED";
}

export function isValidSelectionCount(count: number): boolean {
  return Number.isInteger(count) && count >= 1 && count <= 50;
}

export function reuseSubmissionKey(current: string | null, generate: () => string): string {
  return current || generate();
}

export function isSelectionRunCampaignEligible(run: Pick<ContentSelectionRun, "status" | "selected_candidate_id">): boolean {
  return run.status === "SELECTED" && Boolean(run.selected_candidate_id);
}

export function canFinalizeSelection(
  run: Pick<ContentSelectionRun, "status" | "recommended_candidate_id"> & { decisions: { candidate_id: string }[] },
  selectedCandidateId: string,
  actor: string,
  reason: string,
): boolean {
  if (run.status !== "READY" || !actor.trim()) return false;
  if (!run.decisions.some((decision) => decision.candidate_id === selectedCandidateId)) return false;
  return selectedCandidateId === run.recommended_candidate_id || Boolean(reason.trim());
}

export function normalizePlannedReleaseInput(localValue: string): string | null {
  if (!localValue.trim()) return null;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(localValue)) throw new Error("Enter a valid local release date and time.");
  const parsed = new Date(localValue);
  if (Number.isNaN(parsed.getTime())) throw new Error("Enter a valid local release date and time.");
  // Reject browser date rollover, such as February 30, rather than silently changing intent.
  const [date, time] = localValue.split("T");
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = time.split(":").map(Number);
  if (parsed.getFullYear() !== year || parsed.getMonth() + 1 !== month || parsed.getDate() !== day || parsed.getHours() !== hour || parsed.getMinutes() !== minute) {
    throw new Error("Enter a valid local release date and time.");
  }
  return parsed.toISOString();
}

export function moveCampaignItem<T>(items: readonly T[], from: number, to: number): T[] {
  if (from < 0 || from >= items.length || to < 0 || to >= items.length) return [...items];
  const result = [...items];
  const [item] = result.splice(from, 1);
  result.splice(to, 0, item);
  return result;
}

export interface CampaignDraftItem {
  selectionRunId: string;
  localRelease: string;
}

type CampaignDraftRun = Pick<ContentSelectionRun, "id" | "status" | "selected_candidate_id" | "channel_dna_revision_id">;

export function validateCampaignDraftItems(items: readonly CampaignDraftItem[], runs: readonly CampaignDraftRun[]): string | null {
  if (!isValidSelectionCount(items.length)) return "Campaign requires 1–50 items.";
  const selectedRuns = items.map((item) => runs.find((run) => run.id === item.selectionRunId));
  if (new Set(items.map((item) => item.selectionRunId)).size !== items.length || selectedRuns.some((run) => !run || !isSelectionRunCampaignEligible(run))) {
    return "Each item must use a distinct finalized selection run.";
  }
  if (new Set(selectedRuns.map((run) => run?.selected_candidate_id)).size !== items.length) {
    return "Each campaign item must represent a different selected candidate.";
  }
  if (new Set(selectedRuns.map((run) => run?.channel_dna_revision_id)).size !== 1) {
    return "All selections must pin the same DNA revision.";
  }
  try {
    const scheduled = items.map((item) => normalizePlannedReleaseInput(item.localRelease)).filter((value): value is string => value !== null);
    if (scheduled.some((value, index) => index > 0 && value <= scheduled[index - 1])) {
      return "Planned release times must increase in item order.";
    }
  } catch (cause: unknown) {
    return cause instanceof Error ? cause.message : "Invalid planned release date.";
  }
  return null;
}

export function canMaterializeCampaign(hasExecution: boolean, archived: boolean): boolean {
  return !hasExecution && !archived;
}

export function canStartCampaignMission(binding: Pick<ContentCampaignItemExecution, "mission_state">): boolean {
  return binding.mission_state === "READY";
}

export interface HistoricalPerformanceSummary {
  score: number | null;
  status: string;
  label: string;
  matches: number;
  corpus: number;
  policyVersion: number | null;
}

export function summarizeHistoricalPerformanceEvidence(evidence: HistoricalPerformanceEvidence): HistoricalPerformanceSummary {
  const status = evidence.historical_performance_status || "UNKNOWN";
  const label = status === "APPLIED" ? "Applied historical performance" : status === "INSUFFICIENT_CORPUS" ? "Insufficient history" : status === "INSUFFICIENT_RELEVANT_HISTORY" ? "Insufficient relevant history" : "Historical performance unavailable";
  return {
    score: typeof evidence.historical_performance_score === "number" ? evidence.historical_performance_score : null,
    status,
    label,
    matches: evidence.historical_performance_match_count ?? 0,
    corpus: evidence.historical_performance_corpus_count ?? 0,
    policyVersion: evidence.historical_performance_policy_version ?? null,
  };
}

export function selectedDecision(run: ContentSelectionRun): ContentSelectionRun["decisions"][number] | undefined {
  return run.decisions.find((decision) => decision.candidate_id === run.selected_candidate_id);
}
