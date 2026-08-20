/**
 * Centralized API client.
 *
 * Uses NEXT_PUBLIC_API_BASE_URL environment variable.
 * No hard-coded localhost URLs in components.
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

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

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

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
