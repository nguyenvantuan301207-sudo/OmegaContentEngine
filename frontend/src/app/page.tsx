"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  getHealth,
  getSystemStatus,
  getSystemInfo,
  createTestJob,
  getJob,
  type SystemStatus,
  type SystemInfo,
  type JobDetails,
} from "@/lib/api";

export default function Home() {
  const [health, setHealth] = useState<string | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [jobResult, setJobResult] = useState<JobDetails | null>(null);
  const [jobLoading, setJobLoading] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const [h, info, status] = await Promise.all([
        getHealth().catch(() => null),
        getSystemInfo().catch(() => null),
        getSystemStatus().catch(() => null),
      ]);
      const effectiveHealth = h?.status === "ok" || status?.status === "healthy" ? "ok" : (h?.status || "error");
      setHealth(effectiveHealth);
      setSystemInfo(info);
      setSystemStatus(status);
    } catch {
      setHealth("error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 15000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const handleTestJob = async () => {
    setJobLoading(true);
    setJobResult(null);
    try {
      const created = await createTestJob();
      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const job = await getJob(created.job_id);
        setJobResult(job);
        if (job.state === "SUCCEEDED" || job.state === "FAILED" || job.state === "DEAD") {
          break;
        }
      }
    } catch (err) {
      console.error("Job test failed:", err);
    } finally {
      setJobLoading(false);
    }
  };

  const renderCheck = (name: string, check: { status: string; latency_ms?: number } | undefined) => (
    <div className="check-row" key={name}>
      <div className="check-label">
        <span className={`status-dot ${check?.status || "loading"}`} />
        <span>{name}</span>
      </div>
      <span className="check-meta">
        {check ? `${check.latency_ms ?? "—"}ms` : "..."}
      </span>
    </div>
  );

  return (
    <div>
      {/* Page Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">System Telemetry & Overview</h1>
          <p className="page-subtitle">
            Authoritative status of foundation services, distributed task workers, and persistent data engines.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.75rem" }}>
          <button
            type="button"
            onClick={fetchStatus}
            className="btn btn-secondary"
          >
            ↻ Refresh
          </button>
          <button
            type="button"
            onClick={handleTestJob}
            disabled={jobLoading}
            className="btn btn-primary"
          >
            {jobLoading ? "Processing Job..." : "+ Test Celery Worker"}
          </button>
        </div>
      </div>

      {/* Primary Metrics Grid */}
      <div className="grid grid-cols-3" style={{ marginBottom: "2rem" }}>
        {/* Foundation Info Card */}
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Core Foundation</h3>
            <span className="env-badge">{systemInfo?.environment || "local"}</span>
          </div>
          <div className="card-body">
            <div className="flex-between" style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border-subtle)" }}>
              <span className="text-secondary">App Name</span>
              <span className="text-mono" style={{ fontWeight: 600 }}>{systemInfo?.app || "omega-api"}</span>
            </div>
            <div className="flex-between" style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border-subtle)" }}>
              <span className="text-secondary">Version</span>
              <span className="text-mono">{systemInfo?.version || "0.1.0"}</span>
            </div>
            <div className="flex-between" style={{ padding: "0.5rem 0" }}>
              <span className="text-secondary">API Health</span>
              <span
                className={`badge ${
                  health === "ok"
                    ? "badge-succeeded"
                    : health === null
                    ? "badge-ready"
                    : "badge-failed"
                }`}
              >
                {health === "ok" ? "OPERATIONAL" : health === null ? "CHECKING..." : "UNHEALTHY"}
              </span>
            </div>
          </div>
        </div>

        {/* Subsystem Health Card */}
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Subsystem Infrastructure</h3>
            <span className="badge badge-ready">POSTGRESQL + REDIS</span>
          </div>
          <div className="card-body" style={{ gap: "0.5rem" }}>
            {renderCheck("PostgreSQL", systemStatus?.checks?.postgres)}
            {renderCheck("Redis Cache & Broker", systemStatus?.checks?.redis)}
            {renderCheck("Celery Worker", systemStatus?.checks?.worker)}
          </div>
        </div>

        {/* Quick Operations Card */}
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Canary Fleet Workspace</h3>
            <span className="badge badge-canary">DmYTB Active</span>
          </div>
          <div className="card-body">
            <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>
              Canary YouTube account connected with upload, readonly, and analytics scopes.
            </p>
            <div style={{ display: "flex", gap: "0.75rem", marginTop: "auto", paddingTop: "0.5rem" }}>
              <Link href="/channels" className="btn btn-secondary btn-sm" style={{ flex: 1 }}>
                View Channels →
              </Link>
              <Link href="/missions" className="btn btn-secondary btn-sm" style={{ flex: 1 }}>
                View Missions →
              </Link>
            </div>
          </div>
        </div>
      </div>

      {/* Asynchronous Celery Job Result */}
      {jobResult && (
        <div className="card" style={{ marginBottom: "2rem" }}>
          <div className="card-header">
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <h3 className="card-title">Celery Execution Verification</h3>
              <span
                className={`badge ${
                  jobResult.state === "SUCCEEDED"
                    ? "badge-succeeded"
                    : jobResult.state === "RUNNING"
                    ? "badge-running"
                    : "badge-failed"
                }`}
              >
                {jobResult.state}
              </span>
            </div>
            <span className="text-mono text-muted" style={{ fontSize: "0.75rem" }}>
              Job ID: {jobResult.id}
            </span>
          </div>
          <div className="card-body">
            <div className="flex-between">
              <span className="text-secondary">Task Type:</span>
              <span className="text-mono">{jobResult.job_type}</span>
            </div>
            {jobResult.result && (
              <div className="panel" style={{ marginTop: "0.5rem" }}>
                <span className="text-muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", fontWeight: 700, display: "block", marginBottom: "0.35rem" }}>
                  Worker Output Payload
                </span>
                <pre style={{ margin: 0, fontSize: "0.8rem", color: "var(--accent-secondary)", overflowX: "auto" }}>
                  {JSON.stringify(jobResult.result, null, 2)}
                </pre>
              </div>
            )}
            {jobResult.error && (
              <div style={{ padding: "0.75rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", fontSize: "0.85rem" }}>
                {jobResult.error}
              </div>
            )}
          </div>
        </div>
      )}

      {loading && (
        <div style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
          Polling system telemetry...
        </div>
      )}
    </div>
  );
}
