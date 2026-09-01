/**
 * Centralized API client.
 *
 * Uses NEXT_PUBLIC_API_BASE_URL environment variable.
 * No hard-coded localhost URLs in components.
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

// ── Foundation Types (OMEGA-001) ──

export interface SystemInfo {
  app: string;
  version: string;
  environment: string;
}

export interface HealthCheck {
  status: string;
  latency_ms?: number;
}

export interface SystemStatus {
  status: string;
  checks: {
    postgres: HealthCheck;
    redis: HealthCheck;
    worker: HealthCheck;
  };
}

export interface JobCreated {
  job_id: string;
  state: string;
}

export interface JobDetails {
  id: string;
  job_type: string;
  state: string;
  payload: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error: string | null;
  retry_count: number;
  max_retries: number;
  created_at: string;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

// ── Channel Manager Types (OMEGA-003) ──

export type ChannelState = "DRAFT" | "ACTIVE" | "PAUSED" | "ARCHIVED";
export type Platform = "YOUTUBE" | "TIKTOK" | "INSTAGRAM" | "X_TWITTER";
export type PrimaryGoal =
  | "GROWTH"
  | "ENGAGEMENT"
  | "WATCH_TIME"
  | "REVENUE"
  | "AUTHORITY"
  | "LEAD_GENERATION";
export type KPIMetricType =
  | "SUBSCRIBER_GROWTH"
  | "VIEWS"
  | "WATCH_TIME"
  | "CTR"
  | "RETENTION"
  | "ENGAGEMENT_RATE"
  | "REVENUE";
export type FrequencyPeriod = "DAY" | "WEEK" | "MONTH";

export interface KPITarget {
  metric: KPIMetricType;
  target_value: number;
  timeframe_days?: number | null;
}

export interface PublishingFrequency {
  count: number;
  period: FrequencyPeriod;
}

export interface AudienceProfile {
  age_range: string;
  interests: string[];
  knowledge_level: string;
  viewer_intent: string[];
  preferred_content_length: string;
  preferred_style: string[];
  geographic_focus: string[];
}

export interface BrandVoice {
  tone: string[];
  pace: string;
  complexity: string;
  humor_level: string;
  formality: string;
  narration_style: string;
  preferred_vocabulary: string[];
  avoid_vocabulary: string[];
}

export interface VisualStyle {
  visual_theme: string;
  thumbnail_style: string;
  color_preferences: string[];
  font_preferences: string[];
  editing_style: string;
  b_roll_style: string;
  caption_style: string;
}

export interface ContentStrategy {
  niche: string;
  subniches: string[];
  content_pillars: string[];
  preferred_formats: string[];
  default_duration_min_seconds: number;
  default_duration_max_seconds: number;
  evergreen_ratio: number;
}

export interface PublishingPreferences {
  target_timezone: string;
  preferred_days: string[];
  preferred_time_windows: string[];
  frequency_target: PublishingFrequency;
  approval_required_before_publish: boolean;
}

export interface GoalsAndKPIs {
  primary_goal: PrimaryGoal;
  secondary_goals: PrimaryGoal[];
  target_kpis: KPITarget[];
}

export interface Constraints {
  max_daily_videos: number;
  forbidden_topics: string[];
  content_safety_level: string;
  guidelines: string[];
}

export interface ChannelDNA {
  audience: AudienceProfile;
  brand_voice: BrandVoice;
  visual_style: VisualStyle;
  content_strategy: ContentStrategy;
  publishing_preferences: PublishingPreferences;
  goals_and_kpis: GoalsAndKPIs;
  constraints: Constraints;
}

export interface Channel {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  state: ChannelState;
  platform: Platform;
  platform_channel_id: string | null;
  primary_language: string;
  target_region: string;
  timezone: string;
  dna: ChannelDNA;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface ChannelCreatePayload {
  name: string;
  slug?: string | null;
  description?: string | null;
  platform?: Platform;
  platform_channel_id?: string | null;
  primary_language?: string;
  target_region?: string;
  timezone?: string;
  dna?: Partial<ChannelDNA>;
  metadata?: Record<string, unknown>;
}

export interface ChannelDNARevision {
  id: string;
  channel_id: string;
  version: number;
  snapshot: ChannelDNA;
  change_reason: string;
  actor: string;
  created_at: string;
}

export interface ChannelContext {
  channel_id: string;
  name: string;
  slug: string;
  platform: Platform;
  state: ChannelState;
  primary_language: string;
  target_region: string;
  timezone: string;
  dna: ChannelDNA;
  active_dna_version: number;
}

// ── Topic Intelligence Types (OMEGA-004) ──

export type TopicStatus =
  | "DISCOVERED"
  | "EVALUATED"
  | "RECOMMENDED"
  | "SELECTED"
  | "REJECTED"
  | "ARCHIVED";

export type TopicSourceType =
  | "MANUAL"
  | "SEED"
  | "HISTORICAL"
  | "IMPORT"
  | "SYSTEM_GENERATED";

export type DuplicateStatus =
  | "FRESH_TOPIC"
  | "RELATED_TOPIC"
  | "SAME_TOPIC_NEW_ANGLE"
  | "SEMANTIC_DUPLICATE"
  | "EXACT_DUPLICATE";

export interface TopicAngle {
  id: string;
  candidate_id?: string | null;
  memory_id?: string | null;
  angle: string;
  normalized_angle: string;
  hook?: string | null;
  audience_intent?: string | null;
  format_hint?: string | null;
  created_at: string;
}

export interface TopicCandidate {
  id: string;
  channel_id: string;
  title: string;
  normalized_title: string;
  summary: string | null;
  source_type: TopicSourceType;
  source_name: string;
  source_ref: string | null;
  language: string;
  region: string;
  entities: string[];
  keywords: string[];
  tags: string[];
  topic_fingerprint: string;
  audience_fit_score: number | null;
  strategic_fit_score: number | null;
  trend_score: number | null;
  novelty_score: number | null;
  content_gap_score: number | null;
  historical_performance_score: number | null;
  cost_efficiency_score: number | null;
  revenue_potential_score: number | null;
  final_score: number | null;
  duplicate_status: DuplicateStatus;
  similar_memory_id: string | null;
  similarity_score: number | null;
  status: TopicStatus;
  reasons: string[];
  score_breakdown: Record<string, number>;
  angles: TopicAngle[];
  idempotency_key: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  evaluated_at: string | null;
  archived_at: string | null;
}

export interface TopicCandidateCreatePayload {
  title: string;
  summary?: string | null;
  source_type?: TopicSourceType;
  source_name?: string;
  source_ref?: string | null;
  language?: string;
  region?: string;
  entities?: string[];
  keywords?: string[];
  tags?: string[];
  angles?: {
    angle: string;
    hook?: string | null;
    audience_intent?: string | null;
    format_hint?: string | null;
  }[];
  idempotency_key?: string | null;
  manual_trend_score?: number | null;
  manual_cost_efficiency_score?: number | null;
  manual_revenue_score?: number | null;
  metadata?: Record<string, unknown>;
}

export interface TopicMemory {
  id: string;
  channel_id: string;
  canonical_topic: string;
  normalized_topic: string;
  topic_fingerprint: string;
  entities: string[];
  keywords: string[];
  semantic_tags: string[];
  first_seen_at: string;
  last_seen_at: string;
  times_discovered: number;
  times_selected: number;
  times_produced: number;
  times_rejected: number;
  last_selected_at: string | null;
  last_produced_at: string | null;
  last_rejected_at: string | null;
  last_evaluation_score: number | null;
  angles: TopicAngle[];
  metadata: Record<string, unknown>;
}

// ── Mission Engine Types (OMEGA-002) ──

export type MissionState =
  | "DRAFT"
  | "READY"
  | "RUNNING"
  | "PAUSED"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export type AutonomyLevel =
  | "MANUAL"
  | "ASSISTED"
  | "SUPERVISED"
  | "AUTONOMOUS"
  | "STRATEGIC_AUTONOMOUS";

export type TaskState =
  | "PENDING"
  | "BLOCKED"
  | "READY"
  | "QUEUED"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export interface Mission {
  id: string;
  title: string;
  objective: string;
  channel_id: string | null;
  description: string | null;
  state: MissionState;
  autonomy_level: AutonomyLevel;
  priority: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  paused_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
}

export interface MissionCreatePayload {
  title: string;
  objective: string;
  channel_id?: string | null;
  description?: string | null;
  autonomy_level?: AutonomyLevel;
  priority?: number;
  metadata?: Record<string, unknown>;
}

export interface Task {
  id: string;
  mission_id: string;
  execution_id: string | null;
  task_type: string;
  title: string;
  description: string | null;
  state: TaskState;
  priority: number;
  requires_approval: boolean;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  error: string | null;
  retry_count: number;
  max_retries: number;
  created_at: string;
  updated_at: string;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface DecisionLog {
  id: string;
  mission_id: string;
  execution_id: string | null;
  task_id: string | null;
  decision_type: string;
  decision: string;
  reason: string;
  actor: string;
  context: Record<string, unknown> | null;
  created_at: string;
}

// ── Generic Fetch Helper ──

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    let errorDetail = res.statusText;
    try {
      const errJson = await res.json();
      if (errJson && errJson.detail) {
        errorDetail = typeof errJson.detail === "string" ? errJson.detail : JSON.stringify(errJson.detail);
      }
    } catch {
      // ignore json parse error
    }
    throw new Error(`API error (${res.status}): ${errorDetail}`);
  }
  return res.json();
}

// ── Foundation API Functions ──

export async function getHealth(): Promise<{ status: string }> {
  return apiFetch("/health");
}

export async function getSystemStatus(): Promise<SystemStatus> {
  return apiFetch("/api/v1/system/status");
}

export async function getSystemInfo(): Promise<SystemInfo> {
  return apiFetch("/api/v1/system/info");
}

export async function createTestJob(): Promise<JobCreated> {
  return apiFetch("/api/v1/jobs/test", { method: "POST" });
}

export async function getJob(jobId: string): Promise<JobDetails> {
  return apiFetch(`/api/v1/jobs/${jobId}`);
}

// ── Channel Manager API Functions (OMEGA-003) ──

export async function getChannels(
  state?: string,
  platform?: string,
  limit?: number,
  offset?: number,
  search?: string
): Promise<Channel[]> {
  const params = new URLSearchParams();
  if (state && state !== "ALL") params.set("state", state);
  if (platform) params.set("platform", platform);
  if (limit !== undefined) params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  if (search && search.trim()) params.set("search", search.trim());
  const queryStr = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(`/api/v1/channels${queryStr}`);
}

export async function getChannelCount(
  state?: string,
  platform?: string,
  search?: string
): Promise<number> {
  const params = new URLSearchParams();
  if (state && state !== "ALL") params.set("state", state);
  if (platform) params.set("platform", platform);
  if (search && search.trim()) params.set("search", search.trim());
  const queryStr = params.toString() ? `?${params.toString()}` : "";
  const data = await apiFetch<{ total: number }>(`/api/v1/channels/count${queryStr}`);
  return data.total ?? 0;
}

export async function getChannel(channelId: string): Promise<Channel> {
  return apiFetch(`/api/v1/channels/${channelId}`);
}

export async function createChannel(payload: ChannelCreatePayload): Promise<Channel> {
  return apiFetch("/api/v1/channels", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateChannel(
  channelId: string,
  payload: Partial<ChannelCreatePayload>,
): Promise<Channel> {
  return apiFetch(`/api/v1/channels/${channelId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function activateChannel(channelId: string): Promise<Channel> {
  return apiFetch(`/api/v1/channels/${channelId}/activate`, { method: "POST" });
}

export async function pauseChannel(channelId: string): Promise<Channel> {
  return apiFetch(`/api/v1/channels/${channelId}/pause`, { method: "POST" });
}

export async function archiveChannel(channelId: string): Promise<Channel> {
  return apiFetch(`/api/v1/channels/${channelId}/archive`, { method: "POST" });
}

export async function getChannelDNA(channelId: string): Promise<ChannelDNA> {
  return apiFetch(`/api/v1/channels/${channelId}/dna`);
}

export async function updateChannelDNA(
  channelId: string,
  dna: ChannelDNA,
  changeReason: string,
): Promise<ChannelDNA> {
  return apiFetch(`/api/v1/channels/${channelId}/dna`, {
    method: "PATCH",
    body: JSON.stringify({ dna, change_reason: changeReason, actor: "USER" }),
  });
}

export async function getChannelDNARevisions(channelId: string): Promise<ChannelDNARevision[]> {
  return apiFetch(`/api/v1/channels/${channelId}/dna/revisions`);
}

export async function getChannelContext(channelId: string): Promise<ChannelContext> {
  return apiFetch(`/api/v1/channels/${channelId}/context`);
}

// ── Topic Intelligence API Functions (OMEGA-004) ──

export async function getTopicCandidates(
  channelId: string,
  status?: string,
  sourceType?: string,
): Promise<TopicCandidate[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (sourceType) params.set("source_type", sourceType);
  const q = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(`/api/v1/channels/${channelId}/topics/candidates${q}`);
}

export async function listTopics(
  channelId: string,
  status?: string,
  sourceType?: string,
): Promise<TopicCandidate[]> {
  return getTopicCandidates(channelId, status, sourceType);
}

export async function listCandidates(
  channelId: string,
  status?: string,
  sourceType?: string,
): Promise<TopicCandidate[]> {
  return getTopicCandidates(channelId, status, sourceType);
}

export async function getTopicCandidate(channelId: string, candidateId: string): Promise<TopicCandidate> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/candidates/${candidateId}`);
}

export async function createTopicCandidate(
  channelId: string,
  payload: TopicCandidateCreatePayload,
): Promise<TopicCandidate> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/candidates`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function importTopicCandidates(
  channelId: string,
  candidates: TopicCandidateCreatePayload[],
): Promise<TopicCandidate[]> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/import`, {
    method: "POST",
    body: JSON.stringify({ candidates }),
  });
}

export async function archiveTopicCandidate(channelId: string, candidateId: string): Promise<TopicCandidate> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/candidates/${candidateId}/archive`, {
    method: "POST",
  });
}

export async function evaluateTopicCandidate(
  channelId: string,
  candidateId: string,
  mode: "INTERACTIVE" | "MISSION_EXECUTION" = "INTERACTIVE",
  missionExecutionId?: string | null,
): Promise<TopicCandidate> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/candidates/${candidateId}/evaluate`, {
    method: "POST",
    body: JSON.stringify({ mode, mission_execution_id: missionExecutionId }),
  });
}

export async function evaluateTopicBatch(
  channelId: string,
  mode: "INTERACTIVE" | "MISSION_EXECUTION" = "INTERACTIVE",
  missionExecutionId?: string | null,
): Promise<TopicCandidate[]> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/evaluate-batch`, {
    method: "POST",
    body: JSON.stringify({ mode, mission_execution_id: missionExecutionId }),
  });
}

export async function getTopicRecommendations(
  channelId: string,
  minScore: number = 60.0,
  limit: number = 10,
): Promise<TopicCandidate[]> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/recommendations?min_score=${minScore}&limit=${limit}`);
}

export async function selectTopicCandidate(channelId: string, candidateId: string): Promise<TopicCandidate> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/candidates/${candidateId}/select`, {
    method: "POST",
  });
}

export async function rejectTopicCandidate(
  channelId: string,
  candidateId: string,
  reason: string,
): Promise<TopicCandidate> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/candidates/${candidateId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function getTopicMemory(
  channelId: string,
  search?: string,
): Promise<TopicMemory[]> {
  const q = search ? `?search=${encodeURIComponent(search)}` : "";
  return apiFetch(`/api/v1/channels/${channelId}/topics/memory${q}`);
}

export async function getTopicMemoryRecord(channelId: string, memoryId: string): Promise<TopicMemory> {
  return apiFetch(`/api/v1/channels/${channelId}/topics/memory/${memoryId}`);
}

// ── Mission Engine API Functions (OMEGA-002) ──

export async function getMissions(
  limit?: number,
  offset?: number
): Promise<Mission[]> {
  const params = new URLSearchParams();
  if (limit !== undefined) params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  const queryStr = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(`/api/v1/missions${queryStr}`);
}

export async function getMission(missionId: string): Promise<Mission> {
  return apiFetch(`/api/v1/missions/${missionId}`);
}

export async function createMission(
  payload: MissionCreatePayload,
): Promise<Mission> {
  return apiFetch("/api/v1/missions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function planMission(missionId: string): Promise<Mission> {
  return apiFetch(`/api/v1/missions/${missionId}/plan`, { method: "POST" });
}

export async function startMission(missionId: string): Promise<Mission> {
  return apiFetch(`/api/v1/missions/${missionId}/start`, { method: "POST" });
}

export async function pauseMission(missionId: string): Promise<Mission> {
  return apiFetch(`/api/v1/missions/${missionId}/pause`, { method: "POST" });
}

export async function resumeMission(missionId: string): Promise<Mission> {
  return apiFetch(`/api/v1/missions/${missionId}/resume`, { method: "POST" });
}

export async function cancelMission(missionId: string): Promise<Mission> {
  return apiFetch(`/api/v1/missions/${missionId}/cancel`, { method: "POST" });
}

export async function getMissionTasks(missionId: string): Promise<Task[]> {
  return apiFetch(`/api/v1/missions/${missionId}/tasks`);
}

export async function getMissionDecisions(
  missionId: string,
): Promise<DecisionLog[]> {
  return apiFetch(`/api/v1/missions/${missionId}/decisions`);
}

export async function getTask(taskId: string): Promise<Task> {
  return apiFetch(`/api/v1/tasks/${taskId}`);
}

export async function approveTask(taskId: string): Promise<Task> {
  return apiFetch(`/api/v1/tasks/${taskId}/approve`, { method: "POST" });
}

export async function rejectTask(
  taskId: string,
  reason: string = "Rejected by user",
): Promise<Task> {
  return apiFetch(`/api/v1/tasks/${taskId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

// ── Research Engine Types (OMEGA-005) ──

export type ResearchRequestStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
export type ResearchOutcome = "SUFFICIENT" | "PARTIAL" | "INSUFFICIENT";
export type ResearchSourceType = "MANUAL" | "IMPORT" | "SYSTEM_SEED";
export type PrimarySourceStatus = "UNKNOWN" | "CLAIMED" | "CONFIRMED";
export type ClaimType = "FACT" | "STATISTIC" | "DATE" | "QUOTE" | "CAUSAL" | "DEFINITION" | "INTERPRETATION";
export type EvidenceDirection = "SUPPORTS" | "CONTRADICTS" | "CONTEXT_ONLY";
export type ConfidenceBand = "LOW" | "MEDIUM" | "HIGH" | "VERY_HIGH";
export type ConflictSeverity = "LOW" | "MEDIUM" | "HIGH";
export type ConflictStatus = "OPEN" | "RESOLVED" | "DISMISSED";

export interface ClaimEvidence {
  id: string;
  claim_id: string;
  source_id: string;
  support_direction: EvidenceDirection;
  excerpt: string;
  source_location?: string | null;
  strength_score: number;
  created_at: string;
}

export interface ResearchClaim {
  id: string;
  research_request_id: string;
  channel_id: string;
  claim_text: string;
  normalized_claim: string;
  claim_type: ClaimType;
  confidence_score: number;
  confidence_band: ConfidenceBand;
  supporting_sources_count: number;
  contradicting_sources_count: number;
  independent_sources_count: number;
  is_verified: boolean;
  reasons: string[];
  evidence: ClaimEvidence[];
  created_at: string;
  updated_at: string;
}

export interface ResearchSource {
  id: string;
  research_request_id: string;
  channel_id: string;
  source_type: ResearchSourceType;
  title: string;
  publisher: string;
  author?: string | null;
  url?: string | null;
  content_excerpt: string;
  content_hash: string;
  primary_source_status: PrimarySourceStatus;
  quality_score: number;
  relevance_score: number;
  freshness_score: number;
  quality_reasons: string[];
  independence_cluster_id?: string | null;
  published_at?: string | null;
  retrieved_at: string;
  language: string;
  region: string;
}

export interface ResearchConflict {
  id: string;
  research_request_id: string;
  claim_id?: string | null;
  conflict_type: string;
  severity: ConflictSeverity;
  status: ConflictStatus;
  description: string;
  involved_evidence_ids: string[];
  involved_source_ids: string[];
  resolution_note?: string | null;
  detected_at: string;
  resolved_at?: string | null;
}

export interface CitationRef {
  source_id: string;
  evidence_id: string;
  publisher: string;
  excerpt: string;
  source_location?: string | null;
}

export interface VerifiedClaimBrief {
  claim_id: string;
  text: string;
  type: ClaimType;
  confidence_score: number;
  confidence_band: ConfidenceBand;
  citations: CitationRef[];
}

export interface UncertainClaimBrief {
  claim_id: string;
  text: string;
  type: ClaimType;
  confidence_score: number;
  confidence_band: ConfidenceBand;
  uncertainty_reason: string;
}

export interface ConflictBrief {
  conflict_id: string;
  claim_id?: string | null;
  description: string;
  severity: ConflictSeverity;
  involved_source_ids: string[];
}

export interface ResearchBrief {
  id: string;
  research_request_id: string;
  topic_candidate_id: string;
  channel_id: string;
  version: number;
  supersedes_brief_id?: string | null;
  is_current: boolean;
  outcome: ResearchOutcome;
  overall_confidence: number;
  title: string;
  summary: string;
  verified_claims: VerifiedClaimBrief[];
  uncertain_claims: UncertainClaimBrief[];
  contradictions: ConflictBrief[];
  key_facts: string[];
  statistics: Record<string, unknown>[];
  dates: Record<string, unknown>[];
  quotes: Record<string, unknown>[];
  open_questions: string[];
  sources_summary: Record<string, unknown>;
  created_at: string;
}

export interface ResearchBriefSummary {
  id: string;
  research_request_id: string;
  version: number;
  supersedes_brief_id?: string | null;
  is_current: boolean;
  outcome: ResearchOutcome;
  overall_confidence: number;
  title?: string;
  verified_claims_count: number;
  contradictions_count: number;
  created_at: string;
}

export interface ResearchRequest {
  id: string;
  channel_id: string;
  topic_candidate_id: string;
  mission_execution_id?: string | null;
  mode: string;
  status: ResearchRequestStatus;
  outcome?: ResearchOutcome | null;
  research_question?: string | null;
  scope?: string | null;
  language: string;
  region: string;
  max_sources: number;
  minimum_source_quality: number;
  minimum_claim_confidence: number;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  failed_at?: string | null;
}

// ── Research Engine API Functions (OMEGA-005) ──

export async function createResearchRequest(
  channelId: string,
  payload: {
    topic_candidate_id: string;
    mission_execution_id?: string | null;
    research_question?: string;
    scope?: string;
    language?: string;
    region?: string;
    max_sources?: number;
    minimum_source_quality?: number;
    minimum_claim_confidence?: number;
  },
): Promise<ResearchRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/research`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listResearchRequests(
  channelId: string,
  status?: string,
  topicCandidateId?: string,
): Promise<ResearchRequest[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (topicCandidateId) params.set("topic_candidate_id", topicCandidateId);
  const q = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(`/api/v1/channels/${channelId}/research${q}`);
}

export async function getResearchRequest(channelId: string, requestId: string): Promise<ResearchRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}`);
}

export async function addResearchSource(
  channelId: string,
  requestId: string,
  payload: {
    source_type?: ResearchSourceType;
    title: string;
    publisher: string;
    author?: string;
    url?: string;
    content_excerpt: string;
    primary_source_status?: PrimarySourceStatus;
    published_at?: string;
    language?: string;
    region?: string;
  },
): Promise<ResearchSource> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/sources`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listResearchSources(channelId: string, requestId: string): Promise<ResearchSource[]> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/sources`);
}

export async function addResearchClaim(
  channelId: string,
  requestId: string,
  payload: {
    claim_text: string;
    claim_type?: ClaimType;
    evidence?: Array<{
      source_id: string;
      support_direction?: EvidenceDirection;
      excerpt: string;
      source_location?: string;
      strength_score?: number;
    }>;
  },
): Promise<ResearchClaim> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/claims`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listResearchClaims(channelId: string, requestId: string): Promise<ResearchClaim[]> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/claims`);
}

export async function listResearchConflicts(channelId: string, requestId: string): Promise<ResearchConflict[]> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/conflicts`);
}

export async function runResearchPipeline(
  channelId: string,
  requestId: string,
  idempotencyKey?: string,
): Promise<ResearchBrief> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/run`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  });
}

export async function getResearchBrief(
  channelId: string,
  requestId: string,
  version?: number,
): Promise<ResearchBrief> {
  const q = version ? `?version=${version}` : "";
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/brief${q}`);
}

export async function listResearchBriefs(
  channelId: string,
  requestId: string,
): Promise<ResearchBriefSummary[]> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/briefs`);
}

export async function cancelResearchRequest(
  channelId: string,
  requestId: string,
): Promise<ResearchRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/research/${requestId}/cancel`, {
    method: "POST",
  });
}

// ── OMEGA-006 Content Engine Types & Functions ──

export type ContentGenerationMode = "INTERACTIVE" | "MISSION_EXECUTION";
export type ContentRequestStatus = "DRAFT" | "READY" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
export type ContentOutcome = "GENERATED" | "BLOCKED";
export type ContentType = "YOUTUBE_LONGFORM" | "YOUTUBE_SHORT";
export type HookType = "QUESTION" | "CONTRARIAN" | "CURIOSITY" | "RESULT_FIRST" | "STORY" | "STATISTIC" | "PROBLEM";
export type ContentStatementType = "FACTUAL" | "ATTRIBUTED" | "INTERPRETIVE" | "CREATIVE" | "TRANSITION" | "CTA";
export type ScriptQAStatus = "PENDING" | "PASSED" | "PASSED_WITH_WARNINGS" | "BLOCKED";
export type QASeverity = "INFO" | "WARNING" | "ERROR" | "BLOCKING";

export interface ContentCitation {
  id: string;
  script_statement_id: string;
  research_brief_id: string;
  claim_id: string;
  evidence_id: string;
  source_id: string;
  created_at: string;
}

export interface ScriptStatement {
  id: string;
  script_section_id: string;
  statement_order: number;
  statement_text: string;
  statement_type: ContentStatementType;
  qualification_note?: string | null;
  citations: ContentCitation[];
  created_at: string;
}

export interface ScriptSection {
  id: string;
  script_version_id: string;
  section_order: number;
  heading: string;
  narration_text: string;
  estimated_duration_seconds: number;
  transition_text?: string | null;
  retention_beat?: {
    timestamp_estimate_seconds: number;
    beat_type: string;
    purpose: string;
    text_hint?: string | null;
  } | null;
  statements: ScriptStatement[];
  created_at: string;
}

export interface ScriptVersionSummary {
  id: string;
  content_request_id: string;
  version: number;
  is_current: boolean;
  supersedes_script_id?: string | null;
  title: string;
  estimated_word_count: number;
  estimated_duration_seconds: number;
  qa_status: ScriptQAStatus;
  created_at: string;
}

export interface ScriptVersion {
  id: string;
  content_request_id: string;
  version: number;
  is_current: boolean;
  supersedes_script_id?: string | null;
  title: string;
  hook_id?: string | null;
  hook_text: string;
  closing_text: string;
  cta_text: string;
  estimated_word_count: number;
  estimated_duration_seconds: number;
  qa_status: ScriptQAStatus;
  style_snapshot: Record<string, unknown>;
  sections: ScriptSection[];
  created_at: string;
}

export interface HookCitation {
  id: string;
  hook_id: string;
  research_brief_id: string;
  claim_id: string;
  evidence_id: string;
  source_id: string;
  created_at: string;
}

export interface ContentHook {
  id: string;
  content_request_id: string;
  hook_variant_index: number;
  text: string;
  hook_type: HookType;
  score: number;
  reason_codes: string[];
  selected: boolean;
  citations: HookCitation[];
  created_at: string;
}

export interface ContentIntent {
  id: string;
  content_request_id: string;
  primary_goal: string;
  audience_intent: string;
  viewer_promise: string;
  central_question: string;
  core_takeaway: string;
  tone: string;
  pace: string;
  complexity: string;
  desired_emotion: string;
  call_to_action_type: string;
  created_at: string;
}

export interface OutlineSection {
  section_id: string;
  title: string;
  objective: string;
  key_points: string[];
  claim_refs: string[];
  estimated_duration_seconds: number;
  transition: string;
  retention_goal: string;
}

export interface ContentOutline {
  id: string;
  content_request_id: string;
  opening_description: string;
  sections: OutlineSection[];
  closing_description: string;
  created_at: string;
}

export interface QAFinding {
  rule_code: string;
  severity: QASeverity;
  message: string;
  section_index?: number | null;
  statement_order?: number | null;
  details?: Record<string, unknown>;
}

export interface ContentQAResult {
  id: string;
  script_version_id: string;
  status: ScriptQAStatus;
  findings: QAFinding[];
  executed_at: string;
}

export interface ContentGenerationRequest {
  id: string;
  channel_id: string;
  topic_candidate_id: string;
  research_brief_id: string;
  channel_dna_revision_id: string;
  mission_execution_id?: string | null;
  mode: ContentGenerationMode;
  status: ContentRequestStatus;
  outcome?: ContentOutcome | null;
  content_type: ContentType;
  target_duration_seconds: number;
  target_word_count?: number | null;
  language: string;
  region: string;
  creative_direction?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  failed_at?: string | null;
}

export async function createContentRequest(
  channelId: string,
  payload: {
    topic_candidate_id: string;
    research_brief_id: string;
    mission_execution_id?: string | null;
    content_type?: ContentType;
    target_duration_seconds?: number;
    target_word_count?: number | null;
    language?: string;
    region?: string;
    creative_direction?: string | null;
  },
): Promise<ContentGenerationRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/content`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listContentRequests(
  channelId: string,
  status?: string,
): Promise<ContentGenerationRequest[]> {
  const q = status ? `?status=${status}` : "";
  return apiFetch(`/api/v1/channels/${channelId}/content${q}`);
}

export async function getContentRequest(
  channelId: string,
  requestId: string,
): Promise<ContentGenerationRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}`);
}

export async function cancelContentRequest(
  channelId: string,
  requestId: string,
): Promise<ContentGenerationRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/cancel`, {
    method: "POST",
  });
}

export async function generateContent(
  channelId: string,
  requestId: string,
  idempotencyKey?: string,
): Promise<ScriptVersion> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/generate`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  });
}

export async function regenerateScript(
  channelId: string,
  requestId: string,
  idempotencyKey?: string,
): Promise<ScriptVersion> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/regenerate`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  });
}

export async function getContentIntent(
  channelId: string,
  requestId: string,
): Promise<ContentIntent> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/intent`);
}

export async function listContentHooks(
  channelId: string,
  requestId: string,
): Promise<ContentHook[]> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/hooks`);
}

export async function selectContentHook(
  channelId: string,
  requestId: string,
  hookId: string,
  selected = true,
): Promise<ContentHook> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/hooks/${hookId}/select`, {
    method: "POST",
    body: JSON.stringify({ selected }),
  });
}

export async function getContentOutline(
  channelId: string,
  requestId: string,
): Promise<ContentOutline> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/outline`);
}

export async function listScriptVersions(
  channelId: string,
  requestId: string,
): Promise<ScriptVersionSummary[]> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/scripts`);
}

export async function getScriptVersion(
  channelId: string,
  requestId: string,
  version: number,
): Promise<ScriptVersion> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/scripts/${version}`);
}

export async function getScriptQAResult(
  channelId: string,
  requestId: string,
  version: number,
): Promise<ContentQAResult> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/scripts/${version}/qa`);
}

export async function rerunScriptQA(
  channelId: string,
  requestId: string,
  version: number,
): Promise<ContentQAResult> {
  return apiFetch(`/api/v1/channels/${channelId}/content/${requestId}/scripts/${version}/qa`, {
    method: "POST",
  });
}

// ── Production Engine Types & APIs (OMEGA-007) ──

export interface ProductionRequest {
  id: string;
  channel_id: string;
  script_version_id: string;
  content_request_id: string;
  channel_dna_revision_id: string;
  mission_execution_id?: string | null;
  mode: "INTERACTIVE" | "MISSION_EXECUTION";
  status: "DRAFT" | "READY" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  outcome?: "RENDERED" | "BLOCKED" | null;
  target_width: number;
  target_height: number;
  fps: number;
  video_codec: string;
  audio_codec: string;
  container_format: string;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  failed_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ProductionScene {
  id: string;
  production_request_id: string;
  scene_order: number;
  script_section_id?: string | null;
  start_statement_id?: string | null;
  end_statement_id?: string | null;
  scene_type: string;
  narration_text: string;
  estimated_duration_ms: number;
  visual_intent?: string | null;
  transition_in?: string | null;
  transition_out?: string | null;
  created_at: string;
}

export interface AssetRequirement {
  id: string;
  scene_id: string;
  asset_type: string;
  purpose: string;
  query_hint?: string | null;
  required: boolean;
  status: string;
  license_requirement: string;
  created_at: string;
}

export interface ProductionAsset {
  id: string;
  channel_id: string;
  production_request_id: string;
  asset_requirement_id?: string | null;
  asset_type: string;
  provider_type: string;
  storage_uri: string;
  content_hash: string;
  mime_type: string;
  width?: number | null;
  height?: number | null;
  duration_ms?: number | null;
  license_status: string;
  source_ref?: string | null;
  attribution?: string | null;
  created_at: string;
}

export interface NarrationSegment {
  id: string;
  production_request_id: string;
  scene_id: string;
  audio_asset_id?: string | null;
  text: string;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  created_at: string;
}

export interface SubtitleCue {
  id: string;
  production_request_id: string;
  scene_id: string;
  cue_order: number;
  start_ms: number;
  end_ms: number;
  text: string;
  created_at: string;
}

export interface RenderPlan {
  id: string;
  production_request_id: string;
  version: number;
  width: number;
  height: number;
  fps: number;
  video_codec: string;
  audio_codec: string;
  container: string;
  total_duration_ms: number;
  scene_manifest: Array<Record<string, unknown>>;
  audio_manifest: Array<Record<string, unknown>>;
  subtitle_manifest: Array<Record<string, unknown>>;
  created_at: string;
}

export interface ProductionRenderJob {
  id: string;
  production_request_id: string;
  render_plan_id: string;
  idempotency_key: string;
  state: string;
  attempt: number;
  max_attempts: number;
  error_code?: string | null;
  sanitized_error?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface MediaArtifact {
  id: string;
  production_request_id: string;
  render_job_id?: string | null;
  artifact_type: string;
  version: number;
  is_current: boolean;
  storage_uri: string;
  content_hash: string;
  file_size_bytes: number;
  mime_type: string;
  width?: number | null;
  height?: number | null;
  duration_ms?: number | null;
  created_at: string;
}

export interface ProductionQAFinding {
  rule_code: string;
  severity: "INFO" | "WARNING" | "ERROR" | "BLOCKING";
  message: string;
  details?: Record<string, unknown>;
}

export interface ProductionQAResult {
  id: string;
  production_request_id: string;
  artifact_id: string;
  status: "PENDING" | "PASSED" | "PASSED_WITH_WARNINGS" | "BLOCKED";
  findings: ProductionQAFinding[];
  executed_at: string;
}

export async function createProductionRequest(
  channelId: string,
  payload: {
    script_version_id: string;
    target_width?: number;
    target_height?: number;
    fps?: number;
    video_codec?: string;
    audio_codec?: string;
    container_format?: string;
    mission_execution_id?: string | null;
  },
): Promise<ProductionRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/production`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listProductionRequests(channelId: string): Promise<ProductionRequest[]> {
  return apiFetch(`/api/v1/channels/${channelId}/production`);
}

export async function getProductionRequest(channelId: string, requestId: string): Promise<ProductionRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}`);
}

export async function prepareProduction(channelId: string, requestId: string): Promise<ProductionRequest> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/prepare`, {
    method: "POST",
  });
}

export async function listProductionScenes(channelId: string, requestId: string): Promise<ProductionScene[]> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/scenes`);
}

export async function listProductionAssets(channelId: string, requestId: string): Promise<ProductionAsset[]> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/assets`);
}

export async function listNarrationSegments(channelId: string, requestId: string): Promise<NarrationSegment[]> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/narration`);
}

export async function listSubtitleCues(channelId: string, requestId: string): Promise<SubtitleCue[]> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/subtitles`);
}

export async function getRenderPlan(channelId: string, requestId: string): Promise<RenderPlan> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/render-plan`);
}

export async function renderProduction(
  channelId: string,
  requestId: string,
  idempotencyKey: string,
): Promise<ProductionRenderJob> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/render`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  });
}

export async function rerenderProduction(
  channelId: string,
  requestId: string,
  idempotencyKey: string,
  changeReason = "Explicit rerender",
): Promise<ProductionRenderJob> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/rerender`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: idempotencyKey, change_reason: changeReason }),
  });
}

export async function listRenderJobs(channelId: string, requestId: string): Promise<ProductionRenderJob[]> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/render-jobs`);
}

export async function listMediaArtifacts(channelId: string, requestId: string): Promise<MediaArtifact[]> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/artifacts`);
}

export function getMediaArtifactStreamUrl(channelId: string, requestId: string, artifactId: string): string {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
  return `${baseUrl}/api/v1/channels/${channelId}/production/${requestId}/artifacts/${artifactId}/media`;
}

export async function getProductionQAResult(channelId: string, requestId: string): Promise<ProductionQAResult> {
  return apiFetch(`/api/v1/channels/${channelId}/production/${requestId}/qa`);
}

// ── OMEGA-008 Guardian Subsystem Types & API ──

export type GuardianGateState = "OPEN" | "RESTRICTED" | "BLOCKED" | "WAITING_GUARDIAN";
export type GuardianAction = "ALLOW" | "ALLOW_WITH_WARNING" | "PAUSE" | "REQUIRE_REVIEW" | "FORCE_FAIL";

export interface GuardianFinding {
  id: string;
  guardian_check_id: string;
  detector_run_id: string;
  detector_type: string;
  detector_version: string;
  rule_id: string;
  severity: string;
  risk_type: string;
  confidence: number;
  evidence: Record<string, unknown>;
  location_reference: Record<string, unknown>;
  message: string;
  created_at: string;
}

export interface GuardianDetectorRun {
  id: string;
  guardian_check_id: string;
  detector_type: string;
  detector_version: string;
  status: string;
  failure_policy: string;
  attempt: number;
  max_attempts: number;
  idempotency_key: string;
  error_data?: Record<string, unknown>;
  started_at: string;
  completed_at?: string;
  created_at: string;
}

export interface GuardianDecision {
  id: string;
  guardian_check_id: string;
  action: GuardianAction;
  reason: string;
  resulting_gate_state: GuardianGateState;
  actor: string;
  created_at: string;
}

export interface GuardianCheck {
  id: string;
  mission_id: string;
  task_id?: string;
  production_request_id?: string;
  media_artifact_id?: string;
  trigger_type: string;
  checkpoint: string;
  ruleset_id: string;
  ruleset_version: string;
  ruleset_checksum: string;
  status: string;
  idempotency_key: string;
  guardian_epoch: number;
  diagnostic_context: Record<string, unknown>;
  started_at: string;
  completed_at?: string;
  created_at: string;
  decision?: GuardianDecision;
  findings: GuardianFinding[];
  detector_runs: GuardianDetectorRun[];
}

export interface GuardianConsolidatedStatus {
  mission_id: string;
  guardian_epoch: number;
  overall_gate_state: GuardianGateState;
  checkpoint_states: Record<string, GuardianGateState>;
  blocking_checkpoints: string[];
  open_findings_count: number;
  accumulated_cost_usd: number | string;
  budget_ceiling_usd: number | string;
  remaining_budget_usd: number | string;
}

export interface GuardianException {
  id: string;
  rule_id?: string;
  risk_type?: string;
  channel_id?: string;
  mission_id?: string;
  expires_at: string;
  created_by: string;
  created_reason: string;
  revoked_at?: string;
  revoked_by?: string;
  revocation_reason?: string;
  is_active: boolean;
  created_at: string;
}

export interface CostRecord {
  id: string;
  mission_id: string;
  task_id?: string;
  production_request_id?: string;
  cost_type: string;
  amount_usd: number;
  units: number;
  source_type: string;
  source_id: string;
  idempotency_key: string;
  recorded_at: string;
  created_at: string;
}

export async function getMissionGuardianStatus(missionId: string): Promise<GuardianConsolidatedStatus> {
  return apiFetch(`/api/v1/guardian/missions/${missionId}/status`);
}

export async function listMissionGuardianChecks(missionId: string): Promise<GuardianCheck[]> {
  return apiFetch(`/api/v1/guardian/missions/${missionId}/checks`);
}

export async function listGuardianExceptions(activeOnly = true): Promise<GuardianException[]> {
  return apiFetch(`/api/v1/guardian/exceptions?active_only=${activeOnly}`);
}

export async function createGuardianException(payload: {
  rule_id?: string;
  risk_type?: string;
  channel_id?: string;
  mission_id?: string;
  expires_at: string;
  created_by: string;
  created_reason: string;
}): Promise<GuardianException> {
  return apiFetch(`/api/v1/guardian/exceptions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function revokeGuardianException(
  exceptionId: string,
  revokedBy: string,
  revocationReason: string,
): Promise<GuardianException> {
  return apiFetch(`/api/v1/guardian/exceptions/${exceptionId}/revoke`, {
    method: "POST",
    body: JSON.stringify({ revoked_by: revokedBy, revocation_reason: revocationReason }),
  });
}

export async function triggerSafeResume(
  missionId: string,
  actor = "OPERATOR",
  reason = "Safe resume triggered via Dashboard",
): Promise<Mission> {
  return apiFetch(
    `/api/v1/guardian/missions/${missionId}/resume?actor=${encodeURIComponent(actor)}&reason=${encodeURIComponent(reason)}`,
    {
      method: "POST",
    },
  );
}

export async function listMissionCosts(missionId: string): Promise<CostRecord[]> {
  return apiFetch(`/api/v1/guardian/missions/${missionId}/costs`);
}

// ── Network Manager Types (OMEGA-009) ──

export interface NetworkProfile {
  id: string;
  name: string;
  description: string | null;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface NetworkRoute {
  id: string;
  profile_id: string;
  name: string;
  route_type: string;
  endpoint_url: string | null;
  credential_ref: string | null;
  allowed_service_categories: string[];
  tls_verify: boolean;
  connect_timeout_seconds: number;
  read_timeout_seconds: number;
  max_retries: number;
  priority_weight: number;
  is_enabled: boolean;
  config_version: number;
  config_checksum: string;
  created_at: string;
  updated_at: string;
}

export interface RouteHealth {
  route_id: string;
  service_category: string;
  circuit_state: "CLOSED" | "OPEN" | "HALF_OPEN";
  consecutive_failures: number;
  half_open_successes: number;
  cooldown_until: string | null;
  last_failure_at: string | null;
  last_success_at: string | null;
}

export interface NetworkPreflightResponse {
  id: string;
  mission_id: string | null;
  task_id: string | null;
  route_id: string;
  route_config_version: number;
  service_category: string;
  canonical_destination: string;
  idempotency_key: string;
  status: string;
  decision: {
    id: string;
    action: "ALLOW" | "ALLOW_DEGRADED" | "WAITING_NETWORK" | "BLOCKED_NETWORK";
    resulting_health_state: "HEALTHY" | "DEGRADED" | "UNHEALTHY" | "UNKNOWN";
    reason: string;
    actor: string;
    created_at: string;
  } | null;
}

export async function listNetworkProfiles(): Promise<NetworkProfile[]> {
  return apiFetch("/api/v1/network/profiles");
}

export async function listNetworkRoutes(profileId?: string): Promise<NetworkRoute[]> {
  const query = profileId ? `?profile_id=${profileId}` : "";
  return apiFetch(`/api/v1/network/routes${query}`);
}

export async function getRouteHealth(routeId: string, serviceCategory = "GENERAL_HTTP"): Promise<RouteHealth> {
  return apiFetch(`/api/v1/network/routes/${routeId}/health?service_category=${serviceCategory}`);
}

export async function executeNetworkPreflight(
  destinationUrl: string,
  serviceCategory = "GENERAL_HTTP",
  missionId?: string,
): Promise<NetworkPreflightResponse> {
  return apiFetch("/api/v1/network/preflight", {
    method: "POST",
    body: JSON.stringify({
      destination_url: destinationUrl,
      service_category: serviceCategory,
      mission_id: missionId,
    }),
  });
}

// ── Smart Scheduler Types (OMEGA-010) ──

export type ScheduleAction =
  | "RUN_NOW"
  | "SCHEDULE"
  | "DEFER"
  | "WAITING_DEPENDENCY"
  | "WAITING_CAPACITY"
  | "WAITING_GUARDIAN"
  | "WAITING_NETWORK"
  | "BLOCKED_SCHEDULE";

export type ReservationState =
  | "ACTIVE"
  | "DISPATCHING"
  | "CONSUMED"
  | "RELEASED"
  | "EXPIRED"
  | "CANCELLED";

export interface SchedulePolicy {
  id: string;
  workload_category: string;
  version: string;
  status: "DRAFT" | "ACTIVE" | "RETIRED";
  policy_config: Record<string, unknown>;
  checksum: string;
  effective_at: string | null;
  retired_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleDecision {
  id: string;
  mission_id: string;
  task_id: string | null;
  channel_id: string | null;
  target_type: string;
  target_id: string;
  workload_category: string;
  action: ScheduleAction;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  reason: string;
  policy_id: string;
  policy_version: string;
  policy_checksum: string;
  channel_dna_revision_id: string | null;
  reservation_id: string | null;
  guardian_epoch: number;
  idempotency_key: string;
  diagnostic_context: Record<string, unknown> | null;
  evaluated_at: string;
  expires_at: string | null;
}

export interface ScheduleReservation {
  id: string;
  decision_id: string;
  channel_id: string | null;
  mission_id: string;
  target_type: string;
  target_id: string;
  workload_category: string;
  scheduled_start_at: string;
  scheduled_end_at: string;
  state: ReservationState;
  priority_score: number;
  policy_id: string;
  policy_version: string;
  policy_checksum: string;
  channel_dna_revision_id: string | null;
  guardian_epoch: number;
  version: number;
  created_at: string;
  updated_at: string;
  dispatching_at: string | null;
  consumed_at: string | null;
  released_at: string | null;
  expires_at: string | null;
}

export interface ChannelTimelineResponse {
  channel_id: string;
  timezone: string;
  reservations: ScheduleReservation[];
  capacity_used_today: number;
  capacity_limit_today: number;
}

export async function listSchedulePolicies(workloadCategory?: string): Promise<SchedulePolicy[]> {
  const query = workloadCategory ? `?workload_category=${encodeURIComponent(workloadCategory)}` : "";
  return apiFetch(`/api/v1/scheduler/policies${query}`);
}

export async function createSchedulePolicy(payload: {
  workload_category: string;
  version: string;
  policy_config: Record<string, unknown>;
  activate?: boolean;
}): Promise<SchedulePolicy> {
  return apiFetch("/api/v1/scheduler/policies", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function activateSchedulePolicy(policyId: string): Promise<SchedulePolicy> {
  return apiFetch(`/api/v1/scheduler/policies/${policyId}/activate`, {
    method: "POST",
  });
}

export async function evaluateSchedule(payload: Record<string, unknown>): Promise<ScheduleDecision> {
  return apiFetch("/api/v1/scheduler/evaluate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getScheduleDecision(decisionId: string): Promise<ScheduleDecision> {
  return apiFetch(`/api/v1/scheduler/decisions/${decisionId}`);
}

export async function listScheduleReservations(params?: {
  channel_id?: string;
  state?: string;
  workload_category?: string;
}): Promise<ScheduleReservation[]> {
  const q = new URLSearchParams();
  if (params?.channel_id) q.set("channel_id", params.channel_id);
  if (params?.state) q.set("state", params.state);
  if (params?.workload_category) q.set("workload_category", params.workload_category);
  const qs = q.toString() ? `?${q.toString()}` : "";
  return apiFetch(`/api/v1/scheduler/reservations${qs}`);
}

export async function getScheduleReservation(reservationId: string): Promise<ScheduleReservation> {
  return apiFetch(`/api/v1/scheduler/reservations/${reservationId}`);
}

export async function releaseScheduleReservation(
  reservationId: string,
  reason = "Manual release from dashboard",
): Promise<ScheduleReservation> {
  return apiFetch(`/api/v1/scheduler/reservations/${reservationId}/release?reason=${encodeURIComponent(reason)}`, {
    method: "POST",
  });
}

export async function getChannelScheduleTimeline(channelId: string): Promise<ChannelTimelineResponse> {
  return apiFetch(`/api/v1/scheduler/channels/${channelId}/timeline`);
}

// ── Publisher Types (OMEGA-011) ──

export type PublishIntentState =
  | "DRAFT"
  | "APPROVED"
  | "CLAIMED"
  | "PUBLISHED"
  | "FAILED"
  | "SUPERSEDED"
  | "CANCELLED";

export type PublishAttemptState =
  | "CREATED"
  | "UPLOADING"
  | "FINALIZING"
  | "SUCCEEDED"
  | "RETRYABLE_FAILED"
  | "PERMANENT_FAILED"
  | "UNKNOWN"
  | "BLOCKED_GUARDIAN"
  | "CANCELLED";

export type PrivacyStatus = "PRIVATE" | "UNLISTED" | "PUBLIC";

export interface PlatformAccount {
  id: string;
  channel_id: string;
  platform: "YOUTUBE" | "TIKTOK" | "INSTAGRAM" | "X_TWITTER";
  account_display_name: string;
  external_account_id: string;
  status: "ACTIVE" | "EXPIRED" | "REVOKED";
  scopes: string[];
  created_at: string;
  updated_at: string;
}

export interface PublishIntent {
  id: string;
  mission_id: string;
  task_id: string;
  channel_id: string;
  platform_account_id: string;
  media_artifact_id: string;
  media_artifact_checksum: string;
  channel_dna_revision_id: string | null;
  revision_number: number;
  supersedes_intent_id: string | null;
  title: string;
  description: string;
  tags: string[];
  requested_privacy_status: PrivacyStatus;
  category_id: string;
  made_for_kids: boolean;
  platform_custom_options: Record<string, unknown>;
  intent_checksum: string;
  state: PublishIntentState;
  lease_expires_at: string | null;
  created_at: string;
  updated_at: string;
  superseded_at: string | null;
}

export interface PublishAttempt {
  id: string;
  publish_intent_id: string;
  attempt_number: number;
  idempotency_key: string;
  state: PublishAttemptState;
  provider_video_id: string | null;
  provider_url: string | null;
  effective_privacy_status: PrivacyStatus | null;
  error_category: string | null;
  error_message: string | null;
  retry_after_seconds: number | null;
  reconciliation_status: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface UploadProgress {
  publish_attempt_id: string;
  total_bytes: number;
  bytes_uploaded: number;
  progress_percentage: number;
  is_complete: boolean;
  expires_at: string;
}

export async function listPlatformAccounts(channelId?: string): Promise<PlatformAccount[]> {
  const q = channelId ? `?channel_id=${encodeURIComponent(channelId)}` : "";
  return apiFetch(`/api/v1/publisher/accounts${q}`);
}

export async function createYouTubeAuthorizeUrl(channelId: string): Promise<{ authorization_url: string }> {
  return apiFetch("/api/v1/publisher/accounts/youtube/authorize-url", {
    method: "POST",
    body: JSON.stringify({ channel_id: channelId, platform: "YOUTUBE" }),
  });
}

export async function disconnectPlatformAccount(
  accountId: string,
  confirmDisconnect = true,
): Promise<PlatformAccount> {
  return apiFetch(`/api/v1/publisher/accounts/${accountId}/disconnect`, {
    method: "POST",
    body: JSON.stringify({ confirm_disconnect: confirmDisconnect }),
  });
}

export async function createPublishIntent(payload: {
  mission_id: string;
  task_id: string;
  channel_id: string;
  platform_account_id: string;
  media_artifact_id: string;
  media_artifact_checksum: string;
  channel_dna_revision_id?: string | null;
  title: string;
  description?: string;
  tags?: string[];
  requested_privacy_status?: PrivacyStatus;
  category_id?: string;
  made_for_kids: boolean;
  platform_custom_options?: Record<string, unknown>;
}): Promise<PublishIntent> {
  return apiFetch("/api/v1/publisher/intents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listPublishIntents(params?: {
  channel_id?: string;
  mission_id?: string;
  state?: string;
}): Promise<PublishIntent[]> {
  const q = new URLSearchParams();
  if (params?.channel_id) q.set("channel_id", params.channel_id);
  if (params?.mission_id) q.set("mission_id", params.mission_id);
  if (params?.state) q.set("state", params.state);
  const qs = q.toString() ? `?${q.toString()}` : "";
  return apiFetch(`/api/v1/publisher/intents${qs}`);
}

export async function getPublishIntent(intentId: string): Promise<PublishIntent> {
  return apiFetch(`/api/v1/publisher/intents/${intentId}`);
}

export async function getPublishAttempt(attemptId: string): Promise<PublishAttempt> {
  return apiFetch(`/api/v1/publisher/attempts/${attemptId}`);
}

export async function getUploadProgress(attemptId: string): Promise<UploadProgress> {
  return apiFetch(`/api/v1/publisher/attempts/${attemptId}/progress`);
}

export async function executePublish(taskId: string): Promise<PublishAttempt> {
  return apiFetch("/api/v1/publisher/execute", {
    method: "POST",
    body: JSON.stringify({ task_id: taskId }),
  });
}

// ── Analytics Engine Types & Functions (OMEGA-012) ──

export interface MetricValue {
  metric_name: string;
  value: number | null;
  quality: string;
  classification: string;
}

export interface VideoAnalyticsSummary {
  asset_id: string;
  publish_intent_id: string;
  provider_video_id: string;
  asset_status: string;
  lifecycle_phase: string;
  published_at: string;
  last_polled_at: string | null;
  latest_metrics: Record<string, MetricValue>;
}

export interface TimelineWindowPoint {
  window_type: string;
  window_state: string;
  window_start_utc: string;
  window_end_utc: string;
  metrics: Record<string, MetricValue>;
}

export interface VideoAnalyticsTimeline {
  asset_id: string;
  publish_intent_id: string;
  provider_video_id: string;
  timeline: TimelineWindowPoint[];
}

export interface ChannelAnalyticsSummary {
  channel_id: string;
  snapshot_date: string;
  total_views: number;
  subscriber_count: number;
  video_count: number;
  aggregate_watch_time_seconds: number | null;
  revision_sequence: number;
}

export interface AnalyticsHealth {
  status: string;
  active_assets_count: number;
  quota_buckets: Array<{
    provider: string;
    quota_bucket: string;
    method: string;
    configured_daily_limit: number | null;
    internal_budget_limit: number | null;
    provider_authoritative_usage: number | null;
    estimated_internal_units: number;
    utilization_percent: number | null;
    next_reset_at_utc: string;
  }>;
}

export async function getVideoAnalytics(publishIntentId: string): Promise<VideoAnalyticsSummary> {
  return apiFetch(`/api/v1/analytics/videos/${publishIntentId}`);
}

export async function getVideoAnalyticsTimeline(publishIntentId: string): Promise<VideoAnalyticsTimeline> {
  return apiFetch(`/api/v1/analytics/videos/${publishIntentId}/timeline`);
}

export async function getChannelAnalytics(channelId: string): Promise<ChannelAnalyticsSummary> {
  return apiFetch(`/api/v1/analytics/channels/${channelId}`);
}

export async function refreshVideoAnalytics(publishIntentId: string): Promise<{
  status: string;
  manual_refresh_id: string;
  asset_id: string;
}> {
  return apiFetch(`/api/v1/analytics/videos/${publishIntentId}/refresh`, {
    method: "POST",
  });
}

export async function getAnalyticsHealth(): Promise<AnalyticsHealth> {
  return apiFetch("/api/v1/analytics/health");
}

// ── Learning Engine Types & Methods (OMEGA-013) ──

export interface LearningKnowledgeItem {
  knowledge_family_id: string;
  knowledge_item_id: string;
  channel_id: string;
  knowledge_type: string;
  structured_claim: Record<string, unknown>;
  human_readable_summary: string;
  evidence_type: string;
  confidence_class: "VERY_LOW" | "LOW" | "MODERATE" | "HIGH" | "VERY_HIGH";
  effect_size_absolute: number;
  effect_size_relative_percent: number | null;
  cliffs_delta: number;
  sample_size_treatment: number;
  sample_size_control: number;
  current_status: "ACTIVE" | "WEAKENED" | "STALE" | "SUPERSEDED" | "RETRACTED";
  status_reason: string | null;
  revision_number: number;
  created_at: string;
  updated_at: string;
}

export interface LearningHypothesisSummary {
  hypothesis_family_id: string;
  hypothesis_id: string;
  channel_id: string;
  cohort_id: string;
  hypothesis_slug: string;
  description: string;
  factor_name: string;
  treatment_definition: Record<string, unknown>;
  control_definition: Record<string, unknown>;
  target_outcome_metric: string;
  target_evaluation_window: string;
  current_version: number;
  current_status: "DRAFT" | "ACTIVE" | "INCONCLUSIVE" | "SUPPORTED" | "WEAKENED" | "CONTRADICTED" | "STALE" | "SUPERSEDED";

  updated_at: string;
}

export interface LearningHypothesisEvaluation {
  evaluation_id: string;
  hypothesis_id: string;
  evaluation_version: number;
  sample_size_treatment: number;
  sample_size_control: number;
  treatment_median: number;
  control_median: number;
  effect_size_absolute: number;
  effect_size_relative_percent: number | null;
  cliffs_delta: number;
  p_value_raw: number;
  p_value_adjusted: number;
  confidence_class: string;
  resulting_status: string;
  evaluated_at: string;
}

export interface LearningBaseline {
  baseline_id: string;
  cohort_id: string;
  outcome_metric: string;
  evaluation_window: string;
  baseline_version: number;
  member_count: number;
  metric_median: number;
  metric_mean: number;
  metric_stddev: number;
  metric_iqr: number;
  metric_mad: number;
  calculated_at: string;
}

export interface LearningRefreshResponse {
  job_id: string;
  channel_id: string;
  status: string;
  created_at: string;
}

export async function getChannelKnowledge(
  channelId: string,
  status?: string
): Promise<LearningKnowledgeItem[]> {
  const url = status
    ? `/api/v1/learning/channels/${channelId}/knowledge?status=${encodeURIComponent(status)}`
    : `/api/v1/learning/channels/${channelId}/knowledge`;
  return apiFetch(url);
}

export async function getChannelHypotheses(
  channelId: string
): Promise<LearningHypothesisSummary[]> {
  return apiFetch(`/api/v1/learning/channels/${channelId}/hypotheses`);
}

export async function getHypothesisDetail(
  familyId: string
): Promise<LearningHypothesisSummary> {
  return apiFetch(`/api/v1/learning/hypotheses/${familyId}`);
}

export async function getHypothesisHistory(
  familyId: string
): Promise<LearningHypothesisEvaluation[]> {
  return apiFetch(`/api/v1/learning/hypotheses/${familyId}/history`);
}

export async function getChannelBaselines(
  channelId: string
): Promise<LearningBaseline[]> {
  return apiFetch(`/api/v1/learning/channels/${channelId}/baselines`);
}

export async function triggerLearningRefresh(
  channelId: string,
  requestId?: string
): Promise<LearningRefreshResponse> {
  const url = requestId
    ? `/api/v1/learning/channels/${channelId}/refresh?request_id=${encodeURIComponent(requestId)}`
    : `/api/v1/learning/channels/${channelId}/refresh`;
  return apiFetch(url, { method: "POST" });
}

// ── Autonomous Loop Types (OMEGA-014) ──

export type LoopAutonomyLevel = "MANUAL" | "SUPERVISED" | "BOUNDED_AUTONOMOUS";

export type AutonomyLoopState =
  | "IDLE"
  | "OBSERVING"
  | "PLANNING"
  | "WAITING_APPROVAL"
  | "EXECUTING"
  | "VERIFYING"
  | "WAITING_SIGNAL"
  | "PAUSED"
  | "BLOCKED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface AutonomyLoopStatus {
  id: string;
  mission_id: string;
  channel_id: string;
  autonomy_level: LoopAutonomyLevel;
  operational_state: AutonomyLoopState;
  current_iteration_sequence: number;
  current_iteration_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AutonomyIterationSummary {
  id: string;
  iteration_sequence: number;
  state: string;
  started_at: string;
  completed_at: string | null;
  stop_reason: string | null;
}

export interface AutonomyIterationDetail {
  id: string;
  loop_id: string;
  iteration_sequence: number;
  state: string;
  started_at: string;
  completed_at: string | null;
  stop_reason: string | null;
  observation: Record<string, unknown>;
  action_plan: {
    action_type: string;
    risk_class: string;
    target_subsystem: string;
    estimated_cost_usd: string;
    requires_approval: boolean;
    advisory_rationale: string | null;
  } | null;
  attempts: Array<{
    attempt_id: string;
    attempt_sequence: number;
    correlation_key: string;
  }>;
}

export interface AutonomyApprovalItem {
  id: string;
  loop_id: string;
  action_plan_id: string;
  semantic_action_key: string;
  action_checksum: string;
  guardian_epoch: number;
  mission_state: string;
  requested_at: string;
  expires_at: string;
  latest_decision: string | null;
}

export async function createAutonomyLoop(payload: {
  mission_id: string;
  channel_id: string;
  autonomy_level?: LoopAutonomyLevel;
  daily_cap_usd?: string;
  lifetime_cap_usd?: string;
}): Promise<AutonomyLoopStatus> {
  return apiFetch("/api/v1/autonomy/loops", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getAutonomyLoop(id: string): Promise<AutonomyLoopStatus> {
  return apiFetch(`/api/v1/autonomy/loops/${id}`);
}

export async function getAutonomyIterations(
  id: string,
  limit: number = 20
): Promise<AutonomyIterationSummary[]> {
  return apiFetch(`/api/v1/autonomy/loops/${id}/iterations?limit=${limit}`);
}

export async function getAutonomyIteration(
  id: string
): Promise<AutonomyIterationDetail> {
  return apiFetch(`/api/v1/autonomy/iterations/${id}`);
}

export async function pauseAutonomyLoop(
  id: string,
  reason: string = "Manual user pause"
): Promise<{ status: string; loop_id: string }> {
  return apiFetch(`/api/v1/autonomy/loops/${id}/pause?reason=${encodeURIComponent(reason)}`, {
    method: "POST",
  });
}

export async function resumeAutonomyLoop(
  id: string
): Promise<{ status: string; loop_id: string }> {
  return apiFetch(`/api/v1/autonomy/loops/${id}/resume`, {
    method: "POST",
  });
}

export async function cancelAutonomyLoop(
  id: string,
  reason: string = "Manual user cancellation"
): Promise<{ status: string; loop_id: string }> {
  return apiFetch(`/api/v1/autonomy/loops/${id}/cancel?reason=${encodeURIComponent(reason)}`, {
    method: "POST",
  });
}

export async function resetAutonomyLoopFailure(
  id: string,
  reason: string = "Human failure reset"
): Promise<{ status: string; loop_id: string }> {
  return apiFetch(`/api/v1/autonomy/loops/${id}/reset-failure?reason=${encodeURIComponent(reason)}`, {
    method: "POST",
  });
}

export async function getAutonomyApprovals(
  loopId?: string
): Promise<AutonomyApprovalItem[]> {
  const url = loopId
    ? `/api/v1/autonomy/approvals?loop_id=${encodeURIComponent(loopId)}`
    : "/api/v1/autonomy/approvals";
  return apiFetch(url);
}

export async function approveAutonomyAction(
  id: string,
  reviewReason: string,
  reviewerUserId?: string
): Promise<{ status: string; approval_request_id: string }> {
  return apiFetch(`/api/v1/autonomy/approvals/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      review_reason: reviewReason,
      reviewer_user_id: reviewerUserId || "HUMAN_OPERATOR",
    }),
  });
}

export async function rejectAutonomyAction(
  id: string,
  reviewReason: string,
  reviewerUserId?: string
): Promise<{ status: string; approval_request_id: string }> {
  return apiFetch(`/api/v1/autonomy/approvals/${id}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      review_reason: reviewReason,
      reviewer_user_id: reviewerUserId || "HUMAN_OPERATOR",
    }),
  });
}
