import type {
  ResearchBriefSummary,
  ResearchRequest,
} from "./api.ts";

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

/**
 * Sorts research briefs deterministically:
 * newest first by created_at, falling back to version descending, then id descending.
 */
export function sortResearchBriefs(
  briefs: readonly ResearchBriefSummary[],
): ResearchBriefSummary[] {
  return [...briefs].sort((a, b) => {
    const timeA = a.created_at ? new Date(a.created_at).getTime() : 0;
    const timeB = b.created_at ? new Date(b.created_at).getTime() : 0;
    if (timeB !== timeA) return timeB - timeA;
    if (b.version !== a.version) return b.version - a.version;
    return b.id.localeCompare(a.id);
  });
}

/**
 * Empty-state message for the research brief selector in Content Studio.
 */
export function getBriefEmptyStateMessage(
  topicSelected: boolean,
  briefCount: number,
  hasSufficient: boolean,
): string | undefined {
  if (!topicSelected) return undefined;
  if (briefCount === 0) {
    return "No research requests or briefs found for this topic. Run research before generating content.";
  }
  if (!hasSufficient) {
    return "No SUFFICIENT research brief is available for this topic. Complete research before creating content.";
  }
  return undefined;
}

/**
 * Discovers and aggregates research briefs for a given topic candidate by:
 * 1. Loading all research requests for the topic candidate ID (?topic_candidate_id=...)
 * 2. Fetching briefs for each matching research request ID
 * 3. Flattening, deduplicating, and sorting deterministically (newest first)
 * 4. Preserving historical/immutable briefs from all requests
 */
export async function fetchTopicResearchBriefs(
  channelId: string,
  topicCandidateId: string,
  fetchRequests: (
    channelId: string,
    status?: string,
    topicCandidateId?: string,
  ) => Promise<ResearchRequest[]>,
  fetchBriefs: (
    channelId: string,
    requestId: string,
  ) => Promise<ResearchBriefSummary[]>,
): Promise<ResearchBriefSummary[]> {
  if (!topicCandidateId) return [];
  const requests = await fetchRequests(channelId, undefined, topicCandidateId);
  if (!requests || requests.length === 0) return [];

  const results = await Promise.allSettled(
    requests.map((req) => fetchBriefs(channelId, req.id)),
  );

  const aggregated: ResearchBriefSummary[] = [];
  let hasSuccess = false;
  for (const res of results) {
    if (res.status === "fulfilled" && Array.isArray(res.value)) {
      hasSuccess = true;
      aggregated.push(...res.value);
    }
  }

  if (requests.length > 0 && !hasSuccess) {
    const firstRejection = results.find(
      (r) => r.status === "rejected",
    ) as PromiseRejectedResult | undefined;
    throw firstRejection?.reason instanceof Error
      ? firstRejection.reason
      : new Error("Research briefs could not be loaded");
  }

  const seen = new Set<string>();
  const uniqueBriefs: ResearchBriefSummary[] = [];
  for (const brief of aggregated) {
    if (brief && brief.id && !seen.has(brief.id)) {
      seen.add(brief.id);
      uniqueBriefs.push(brief);
    }
  }

  return sortResearchBriefs(uniqueBriefs);
}
