"use client";

import { useEffect, useState, use, useCallback } from "react";
import Link from "next/link";
import {
  approveTask,
  cancelMission,
  DecisionLog,
  getMission,
  getMissionDecisions,
  getMissionTasks,
  Mission,
  pauseMission,
  planMission,
  rejectTask,
  resumeMission,
  startMission,
  Task,
} from "@/lib/api";
import { GuardianPanel } from "@/components/guardian/GuardianPanel";

export default function MissionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const missionId = resolvedParams.id;

  const [mission, setMission] = useState<Mission | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [decisions, setDecisions] = useState<DecisionLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [m, t, d] = await Promise.all([
        getMission(missionId),
        getMissionTasks(missionId),
        getMissionDecisions(missionId),
      ]);
      setMission(m);
      setTasks(t);
      setDecisions(d);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load mission");
    } finally {
      setLoading(false);
    }
  }, [missionId]);

  useEffect(() => {
    loadData();
    const interval = setInterval(() => {
      loadData();
    }, 3000);
    return () => clearInterval(interval);
  }, [loadData]);

  const handleAction = async (actionFn: () => Promise<unknown>) => {
    setActionLoading(true);
    setError(null);
    try {
      await actionFn();
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActionLoading(false);
    }
  };

  const getMissionBadgeClass = (state: string) => {
    switch (state) {
      case "SUCCEEDED":
        return "badge-succeeded";
      case "RUNNING":
        return "badge-running";
      case "READY":
        return "badge-ready";
      case "WAITING_APPROVAL":
        return "badge-waiting";
      case "FAILED":
        return "badge-failed";
      case "PAUSED":
        return "badge-paused";
      case "DRAFT":
      default:
        return "badge-draft";
    }
  };

  const getTaskBadgeClass = (state: string) => {
    switch (state) {
      case "SUCCEEDED":
        return "badge-succeeded";
      case "RUNNING":
        return "badge-running";
      case "READY":
      case "QUEUED":
        return "badge-ready";
      case "WAITING_APPROVAL":
        return "badge-waiting";
      case "FAILED":
      case "BLOCKED":
        return "badge-failed";
      case "CANCELLED":
      case "PENDING":
      default:
        return "badge-draft";
    }
  };

  if (loading && !mission) {
    return (
      <div style={{ textAlign: "center", padding: "5rem 0", color: "var(--text-muted)", fontSize: "0.9rem" }}>
        Loading mission {missionId}...
      </div>
    );
  }

  if (!mission) {
    return (
      <div>
        <div style={{ marginBottom: "1rem" }}>
          <Link href="/missions" style={{ fontSize: "0.78rem", color: "var(--accent-secondary)" }}>
            ← Back to Missions
          </Link>
        </div>
        <div style={{ padding: "1.25rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", fontSize: "0.85rem" }}>
          Mission not found
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Header & Navigation */}
      <div className="page-header">
        <div>
          <div style={{ marginBottom: "0.5rem" }}>
            <Link
              href="/missions"
              style={{ fontSize: "0.78rem", color: "var(--accent-secondary)", textDecoration: "none" }}
            >
              ← Back to Missions
            </Link>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
            <h1 className="page-title">{mission.title}</h1>
            <span className={`badge ${getMissionBadgeClass(mission.state)}`}>
              {mission.state}
            </span>
          </div>
          <p className="page-subtitle" style={{ maxWidth: "800px" }}>
            {mission.objective}
          </p>
          {mission.description && (
            <p style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: "0.35rem" }}>
              {mission.description}
            </p>
          )}

          <div style={{ display: "flex", gap: "1rem", marginTop: "0.75rem", fontSize: "0.75rem", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
            <span>Autonomy: <strong style={{ color: "var(--accent-secondary)" }}>{mission.autonomy_level}</strong></span>
            <span>Priority: <strong style={{ color: "var(--text-primary)" }}>{mission.priority}</strong></span>
            <span>ID: <code>{mission.id}</code></span>
            <span>Created: {new Date(mission.created_at).toLocaleTimeString()}</span>
          </div>
        </div>

        {/* Action Controls */}
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
          {mission.state === "DRAFT" && (
            <button
              disabled={actionLoading}
              onClick={() => handleAction(() => planMission(mission.id))}
              className="btn btn-primary btn-sm"
            >
              Plan DAG
            </button>
          )}

          {mission.state === "READY" && (
            <button
              disabled={actionLoading}
              onClick={() => handleAction(() => startMission(mission.id))}
              className="btn btn-success btn-sm"
            >
              ▶ Start Execution
            </button>
          )}

          {mission.state === "RUNNING" && (
            <button
              disabled={actionLoading}
              onClick={() => handleAction(() => pauseMission(mission.id))}
              className="btn btn-secondary btn-sm"
            >
              ❚❚ Pause
            </button>
          )}

          {mission.state === "PAUSED" && (
            <button
              disabled={actionLoading}
              onClick={() => handleAction(() => resumeMission(mission.id))}
              className="btn btn-success btn-sm"
            >
              ▶ Resume
            </button>
          )}

          {["READY", "RUNNING", "PAUSED"].includes(mission.state) && (
            <button
              disabled={actionLoading}
              onClick={() => handleAction(() => cancelMission(mission.id))}
              className="btn btn-danger btn-sm"
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div style={{ padding: "1rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", marginBottom: "1.5rem", fontSize: "0.85rem" }}>
          {error}
        </div>
      )}

      {/* Guardian Subsystem Panel */}
      <div style={{ marginBottom: "2rem" }}>
        <GuardianPanel
          missionId={mission.id}
          isPaused={mission.state === "PAUSED"}
          onStateChanged={loadData}
        />
      </div>

      {/* Main Grid: Task DAG + Decision Log */}
      <div className="grid grid-cols-3" style={{ gap: "1.5rem" }}>
        {/* Task DAG Column (Span 2) */}
        <div style={{ gridColumn: "span 2", display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div className="flex-between">
            <h2 style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>
              Task Execution DAG ({tasks.length})
            </h2>
            <span className="text-mono text-muted" style={{ fontSize: "0.75rem" }}>
              Auto-refreshing (3s)
            </span>
          </div>

          {tasks.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">⚡</div>
              <h3>No Tasks Planned</h3>
              <p>Click &quot;Plan DAG&quot; to formulate the execution sequence for this mission.</p>
            </div>
          ) : (
            tasks.map((task, idx) => (
              <div key={task.id} className="card" style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                <div className="flex-between">
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span className="text-mono text-muted" style={{ fontSize: "0.75rem" }}>#{idx + 1}</span>
                      <h3 style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--text-primary)" }}>
                        {task.title}
                      </h3>
                    </div>
                    <div className="text-mono text-muted" style={{ fontSize: "0.75rem", marginTop: "0.2rem" }}>
                      type: {task.task_type}
                    </div>
                  </div>
                  <span className={`badge ${getTaskBadgeClass(task.state)}`}>
                    {task.state}
                  </span>
                </div>

                {task.description && (
                  <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                    {task.description}
                  </p>
                )}

                {/* Editorial / Pre-Execution Approval Gate */}
                {task.state === "WAITING_APPROVAL" && (
                  <div style={{ padding: "1rem", background: "rgba(245, 158, 11, 0.1)", border: "1px solid rgba(245, 158, 11, 0.3)", borderRadius: "var(--radius-sm)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem" }}>
                    <div style={{ fontSize: "0.82rem", color: "var(--status-warning)" }}>
                      <strong>Pre-Execution Approval Required:</strong> Editorial sign-off required before advancing to subsequent stages.
                    </div>
                    <div style={{ display: "flex", gap: "0.5rem" }}>
                      <button
                        disabled={actionLoading}
                        onClick={() => handleAction(() => approveTask(task.id))}
                        className="btn btn-success btn-sm"
                      >
                        Approve
                      </button>
                      <button
                        disabled={actionLoading}
                        onClick={() => handleAction(() => rejectTask(task.id, "Rejected by operator"))}
                        className="btn btn-danger btn-sm"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                )}

                {/* Error Output */}
                {task.error && (
                  <div style={{ padding: "0.75rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", fontSize: "0.8rem", fontFamily: "var(--font-mono)" }}>
                    Error: {task.error}
                  </div>
                )}

                {/* Task Result Summary */}
                {task.output && (
                  <div className="panel" style={{ fontSize: "0.78rem", fontFamily: "var(--font-mono)", color: "var(--text-secondary)", maxHeight: "160px", overflowY: "auto" }}>
                    <span style={{ color: "var(--text-muted)", display: "block", marginBottom: "0.25rem", textTransform: "uppercase", fontWeight: 700 }}>
                      Output Artifact Payload
                    </span>
                    <pre style={{ margin: 0 }}>
                      {JSON.stringify(task.output, null, 2)}
                    </pre>
                  </div>
                )}

                <div className="flex-between" style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)", paddingTop: "0.5rem", borderTop: "1px solid var(--border-subtle)" }}>
                  <span>Retries: {task.retry_count}/{task.max_retries}</span>
                  <span>ID: {task.id.slice(0, 8)}...</span>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Decision Log Column (Span 1) */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>
            Decision History ({decisions.length})
          </h2>

          {decisions.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>No decisions logged yet.</p>
          ) : (
            decisions.map((d) => (
              <div key={d.id} className="card" style={{ padding: "1rem", fontSize: "0.82rem" }}>
                <div className="flex-between" style={{ marginBottom: "0.35rem" }}>
                  <span className="text-mono" style={{ color: "var(--accent-secondary)", fontWeight: 600 }}>
                    {d.actor}
                  </span>
                  <span className="badge badge-draft text-mono" style={{ fontSize: "0.65rem" }}>
                    {d.decision_type}
                  </span>
                </div>
                <div style={{ color: "var(--text-primary)", fontWeight: 500, marginBottom: "0.25rem" }}>
                  {d.decision}
                </div>
                {d.reason && (
                  <div style={{ color: "var(--text-muted)", fontSize: "0.78rem", fontStyle: "italic" }}>
                    &ldquo;{d.reason}&rdquo;
                  </div>
                )}
                <div className="text-mono text-muted" style={{ fontSize: "0.72rem", marginTop: "0.5rem" }}>
                  {new Date(d.created_at).toLocaleTimeString()}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
