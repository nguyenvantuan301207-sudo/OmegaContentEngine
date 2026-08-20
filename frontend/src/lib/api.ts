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
  times_discovered: int;
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

export async function getChannels(state?: string, platform?: string): Promise<Channel[]> {
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  if (platform) params.set("platform", platform);
  const queryStr = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(`/api/v1/channels${queryStr}`);
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

export async function getMissions(): Promise<Mission[]> {
  return apiFetch("/api/v1/missions");
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
