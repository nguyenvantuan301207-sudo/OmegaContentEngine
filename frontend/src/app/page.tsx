"use client";

import { useEffect, useState, useCallback } from "react";
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
        getHealth().catch(() => ({ status: "error" })),
        getSystemInfo().catch(() => null),
        getSystemStatus().catch(() => null),
      ]);
      setHealth(h.status);
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
      // Poll for completion
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
        {name}
      </div>
      <span className="check-meta">
        {check ? `${check.latency_ms ?? "—"}ms` : "..."}
      </span>
    </div>
  );

  return (
    <div className="container">
      <header className="header">
        <div className="logo">
          <div className="logo-icon">Ω</div>
          <div>
            <h1>OMEGA</h1>
            <span>Autonomous Content Operating System</span>
          </div>
        </div>
        {systemInfo && (
          <span className="env-badge">{systemInfo.environment}</span>
        )}
      </header>

      <h2 className="section-title">System Overview</h2>
      <div className="grid">
        <div className="card">
          <div className="card-header">
            <span className="card-title">API Status</span>
            <span className={`status-dot ${loading ? "loading" : health === "ok" ? "healthy" : "unhealthy"}`} />
          </div>
          <div className="card-value">{loading ? "..." : health === "ok" ? "Online" : "Offline"}</div>
          <div className="card-subtitle">
            {systemInfo ? `v${systemInfo.version}` : "Connecting..."}
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <span className="card-title">System Health</span>
            <span className={`status-dot ${systemStatus?.status === "healthy" ? "healthy" : systemStatus ? "unhealthy" : "loading"}`} />
          </div>
          <div className="card-value">
            {systemStatus?.status === "healthy" ? "Healthy" : systemStatus ? "Degraded" : "..."}
          </div>
          <div className="card-subtitle">All dependencies</div>
        </div>
      </div>

      <h2 className="section-title">Dependency Checks</h2>
      <div className="card" style={{ marginBottom: "2.5rem" }}>
        {renderCheck("PostgreSQL", systemStatus?.checks?.postgres)}
        {renderCheck("Redis", systemStatus?.checks?.redis)}
        {renderCheck("Celery Worker", systemStatus?.checks?.worker)}
      </div>

      <h2 className="section-title">Test Job</h2>
      <div className="card">
        <p style={{ marginBottom: "1rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
          Dispatch a test job to verify the worker pipeline.
        </p>
        <button
          className="btn btn-primary"
          onClick={handleTestJob}
          disabled={jobLoading}
          id="test-job-button"
        >
          {jobLoading ? "⏳ Running..." : "▶ Run Test Job"}
        </button>

        {jobResult && (
          <div className="job-result">
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.75rem" }}>
              <span className={`state-badge ${jobResult.state}`}>{jobResult.state}</span>
              <span style={{ color: "var(--text-muted)" }}>{jobResult.id}</span>
            </div>
            {JSON.stringify(jobResult, null, 2)}
          </div>
        )}
      </div>

      <footer className="footer">
        OMEGA v{systemInfo?.version ?? "0.1.0"} · Foundation
      </footer>
    </div>
  );
}
