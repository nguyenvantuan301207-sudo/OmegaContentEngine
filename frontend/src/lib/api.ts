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

// ── Mission Engine API Functions ──

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
