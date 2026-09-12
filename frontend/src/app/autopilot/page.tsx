"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getAutonomyApprovals,
  approveAutonomyAction,
  rejectAutonomyAction,
  getMissions,
  type AutonomyApprovalItem,
  type Mission,
} from "@/lib/api";
import { Alert, StatusBadge, type StatusTone } from "@/components/ui";

export default function AutopilotPage() {
  const [approvals, setApprovals] = useState<AutonomyApprovalItem[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [approvalData, missionData] = await Promise.all([
        getAutonomyApprovals().catch(() => []),
        getMissions(100, 0).catch(() => []),
      ]);
      setApprovals(approvalData);
      setMissions(missionData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load autonomy state.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleApprove = async (id: string) => {
    setActionBusy(id);
    setActionMessage(null);
    try {
      await approveAutonomyAction(id, "Approved by operator via Autopilot console");
      setActionMessage(`Approval request ${id.slice(0, 8)} successfully approved.`);
      void loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to approve action.");
    } finally {
      setActionBusy(null);
    }
  };

  const handleReject = async (id: string) => {
    setActionBusy(id);
    setActionMessage(null);
    try {
      await rejectAutonomyAction(id, "Rejected by operator via Autopilot console");
      setActionMessage(`Approval request ${id.slice(0, 8)} successfully rejected.`);
      void loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reject action.");
    } finally {
      setActionBusy(null);
    }
  };

  const pendingApprovals = approvals.filter((a) => !a.latest_decision);
  const resolvedApprovals = approvals.filter((a) => Boolean(a.latest_decision));

  // Autonomy level breakdown
  const autonomyCounts = useMemo(() => {
    const counts: Record<string, number> = {
      MANUAL: 0,
      ASSISTED: 0,
      SUPERVISED: 0,
      AUTONOMOUS: 0,
      STRATEGIC_AUTONOMOUS: 0,
    };
    for (const m of missions) {
      if (counts[m.autonomy_level] !== undefined) {
        counts[m.autonomy_level]++;
      }
    }
    return counts;
  }, [missions]);

  return (
    <div className="ui-page-stack" style={{ maxWidth: "1400px", margin: "0 auto" }}>
      {/* PAGE HEADER */}
      <div
        className="page-head"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: "16px",
          marginBottom: "16px",
        }}
      >
        <div>
          <div className="title" style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.03em" }}>
            Autopilot & Governance
          </div>
          <div className="sub" style={{ color: "var(--muted)", fontSize: "13px", marginTop: "4px" }}>
            Supervised autonomous loop governance, Guardian safety policies, human approvals, and execution boundaries.
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button
            type="button"
            className="btn"
            onClick={() => void loadData()}
            disabled={loading}
            style={{ fontSize: "12px", padding: "8px 12px" }}
          >
            Refresh
          </button>
        </div>
      </div>

      {actionMessage && (
        <Alert tone="success" title="Action completed">
          {actionMessage}
        </Alert>
      )}

      {error && (
        <Alert
          tone="danger"
          title="Autopilot state error"
          actions={
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => void loadData()}
            >
              Retry
            </button>
          }
        >
          {error}
        </Alert>
      )}

      {/* METRICS ROW */}
      <div className="dash-metrics-grid" aria-label="Autonomy Metrics">
        <div className="dash-metric-card">
          <div className="metric-k">Pending Approvals</div>
          <div className="metric-v">{pendingApprovals.length}</div>
          <div
            className="metric-sub"
            style={{ color: pendingApprovals.length > 0 ? "var(--warning)" : "var(--success)" }}
          >
            {pendingApprovals.length > 0 ? "Requires operator intervention" : "Approval queue clear"}
          </div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Audited Approvals</div>
          <div className="metric-v">{resolvedApprovals.length}</div>
          <div className="metric-sub">Historical human/policy decisions</div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Governed Missions</div>
          <div className="metric-v">{missions.length}</div>
          <div className="metric-sub">Active workflow configurations</div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Worker Daemon Status</div>
          <div className="metric-v" style={{ fontSize: "18px", color: "var(--warning)" }}>
            STOPPED
          </div>
          <div className="metric-sub">Supervised safety state</div>
        </div>
      </div>

      {/* MAIN TWO-COLUMN LAYOUT */}
      <div className="autogrid" style={{ marginTop: "16px" }}>
        {/* LEFT COLUMN: PENDING & RESOLVED APPROVALS */}
        <div className="card pad" style={{ border: "1px solid var(--line)", borderRadius: "var(--radius)" }}>
          <div className="between" style={{ marginBottom: "12px" }}>
            <div>
              <strong style={{ fontSize: "14px", color: "var(--text-primary)" }}>Human Approval Queue</strong>
              <span className="small muted" style={{ display: "block", fontSize: "11px", marginTop: "2px" }}>
                Actions flagged by Guardian or policy rules requiring human authorization.
              </span>
            </div>
            <span className="badge info">{approvals.length} Total</span>
          </div>

          {loading ? (
            <div style={{ padding: "30px", textAlign: "center", color: "var(--muted)" }}>
              Loading approval requests...
            </div>
          ) : approvals.length === 0 ? (
            <div style={{ padding: "30px 10px", textAlign: "center", color: "var(--muted)", fontSize: "13px" }}>
              No approval requests are currently recorded in the database.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {approvals.slice(0, 15).map((appr) => {
                const isResolved = Boolean(appr.latest_decision);
                const isPending = !isResolved;
                const decisionTone: StatusTone =
                  appr.latest_decision === "APPROVED"
                    ? "success"
                    : appr.latest_decision === "REJECTED"
                    ? "danger"
                    : appr.latest_decision === "EXPIRED"
                    ? "warning"
                    : "info";

                return (
                  <div
                    key={appr.id}
                    style={{
                      padding: "10px 12px",
                      background: "#0f151e",
                      border: "1px solid var(--line)",
                      borderRadius: "8px",
                    }}
                  >
                    <div className="between" style={{ alignItems: "center" }}>
                      <div>
                        <strong style={{ fontSize: "12px", color: "var(--text-primary)" }}>
                          {appr.semantic_action_key}
                        </strong>
                        <div className="small muted" style={{ fontSize: "10px", marginTop: "2px" }}>
                          Epoch {appr.guardian_epoch} · State: {appr.mission_state}
                        </div>
                      </div>
                      <StatusBadge tone={decisionTone}>
                        {appr.latest_decision || "PENDING"}
                      </StatusBadge>
                    </div>

                    <div className="small muted text-mono" style={{ fontSize: "10px", marginTop: "6px" }}>
                      Requested: {new Date(appr.requested_at).toLocaleString()}
                    </div>

                    {isPending && (
                      <div
                        style={{
                          marginTop: "8px",
                          display: "flex",
                          gap: "8px",
                          justifyContent: "flex-end",
                        }}
                      >
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={actionBusy === appr.id}
                          onClick={() => void handleReject(appr.id)}
                          style={{
                            fontSize: "11px",
                            padding: "4px 8px",
                            color: "var(--danger)",
                            borderColor: "rgba(251, 113, 133, 0.3)",
                          }}
                        >
                          Reject
                        </button>
                        <button
                          type="button"
                          className="btn primary btn-sm"
                          disabled={actionBusy === appr.id}
                          onClick={() => void handleApprove(appr.id)}
                          style={{ fontSize: "11px", padding: "4px 10px" }}
                        >
                          Approve
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* RIGHT COLUMN: AUTONOMY CONFIGURATION & STOP CONDITIONS */}
        <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          {/* AUTONOMOUS LOOP STAGES */}
          <div className="card pad" style={{ border: "1px solid var(--line)", borderRadius: "var(--radius)" }}>
            <b style={{ fontSize: "13px", color: "var(--text-primary)", display: "block", marginBottom: "8px" }}>
              Autonomous Progression Loop
            </b>
            <div className="flow" style={{ marginBottom: "12px" }}>
              <span className="flow-step">Research</span>
              <span className="arrow">→</span>
              <span className="flow-step">Topic</span>
              <span className="arrow">→</span>
              <span className="flow-step">Script</span>
              <span className="arrow">→</span>
              <span className="flow-step">Production</span>
              <span className="arrow">→</span>
              <span className="flow-step">QA</span>
              <span className="arrow">→</span>
              <span className="flow-step">Publish</span>
              <span className="arrow">→</span>
              <span className="flow-step">Learn</span>
            </div>

            <div className="field">
              <label>Stop Conditions & Safety Fences</label>
              <div className="box text" style={{ fontSize: "11px", lineHeight: "1.5" }}>
                1. Provider failure or SSRF egress violation<br />
                2. Guardian BLOCK severity QA finding<br />
                3. Lifetime or daily spend budget cap exceeded<br />
                4. Unapproved action plan timeout
              </div>
            </div>
          </div>

          {/* MISSION AUTONOMY DISTRIBUTION */}
          <div className="card pad" style={{ border: "1px solid var(--line)", borderRadius: "var(--radius)" }}>
            <b style={{ fontSize: "13px", color: "var(--text-primary)", display: "block", marginBottom: "8px" }}>
              Active Autonomy Levels Across Workspace
            </b>
            <div className="data-bars">
              {Object.entries(autonomyCounts).map(([lvl, count]) => {
                const pct = missions.length ? Math.round((count / missions.length) * 100) : 0;
                return (
                  <div key={lvl} className="barline">
                    <span style={{ fontSize: "11px", color: "var(--muted)" }}>
                      {lvl.replaceAll("_", " ")}
                    </span>
                    <div className="meter">
                      <span style={{ width: `${pct}%` }} />
                    </div>
                    <span style={{ fontSize: "11px", color: "var(--text-primary)", textAlign: "right" }}>
                      {count} ({pct}%)
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* REAL CAPABILITY NOTICE */}
          <div
            className="card pad"
            style={{
              border: "1px solid var(--line)",
              borderRadius: "var(--radius)",
              background: "#0d121a",
            }}
          >
            <div className="between" style={{ alignItems: "center" }}>
              <span className="cmd-category-tag">Runtime Governance</span>
              <span className="badge warn">Worker Halted</span>
            </div>
            <p style={{ fontSize: "12px", color: "var(--muted)", margin: "8px 0 0" }}>
              The Celery worker and beat daemons are intentionally halted in this environment.
              All autonomy policies, Guardian validations, and approval queues remain fully operational in supervised inspection mode.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
