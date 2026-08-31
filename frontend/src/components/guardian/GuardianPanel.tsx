"use client";

import { useEffect, useState, useCallback } from "react";
import {
  GuardianCheck,
  GuardianConsolidatedStatus,
  GuardianException,
  getMissionGuardianStatus,
  listMissionGuardianChecks,
  listGuardianExceptions,
  triggerSafeResume,
} from "@/lib/api";
import { formatCurrencyUsd } from "@/lib/formatters";

export function GuardianPanel({
  missionId,
  isPaused,
  onStateChanged,
}: {
  missionId: string;
  isPaused: boolean;
  onStateChanged?: () => void;
}) {
  const [status, setStatus] = useState<GuardianConsolidatedStatus | null>(null);
  const [checks, setChecks] = useState<GuardianCheck[]>([]);
  const [exceptions, setExceptions] = useState<GuardianException[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadGuardianData = useCallback(async () => {
    try {
      const [st, chk, exc] = await Promise.all([
        getMissionGuardianStatus(missionId).catch(() => null),
        listMissionGuardianChecks(missionId).catch(() => []),
        listGuardianExceptions(true).catch(() => []),
      ]);
      setStatus(st);
      setChecks(chk);
      setExceptions(exc);
      setErrorMsg(null);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to load Guardian data");
    } finally {
      setLoading(false);
    }
  }, [missionId]);

  useEffect(() => {
    loadGuardianData();
    const interval = setInterval(loadGuardianData, 5000);
    return () => clearInterval(interval);
  }, [loadGuardianData]);

  const handleSafeResume = async () => {
    try {
      setActionLoading(true);
      setErrorMsg(null);
      await triggerSafeResume(missionId);
      await loadGuardianData();
      if (onStateChanged) onStateChanged();
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Safe Resume failed");
    } finally {
      setActionLoading(false);
    }
  };

  if (loading && !status) {
    return (
      <div style={{ textAlign: "center", padding: "1.5rem", color: "var(--text-muted)", fontSize: "0.82rem", fontFamily: "var(--font-mono)" }}>
        Loading Guardian Safety & Quality Subsystem...
      </div>
    );
  }

  const gateState = status?.overall_gate_state || "OPEN";
  const isBlocked = gateState === "BLOCKED";
  const isRestricted = gateState === "RESTRICTED";

  return (
    <div className="card" style={{ borderColor: isBlocked ? "var(--status-danger-border)" : isRestricted ? "var(--status-warning-border)" : "var(--border-subtle)" }}>
      {/* Status Banner */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "1rem", marginBottom: "1rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span className="section-title">Guardian Control Plane</span>
            <span className={`badge ${isBlocked ? "badge-failed" : isRestricted ? "badge-waiting" : "badge-succeeded"}`}>
              {isBlocked ? "GATE BLOCKED" : isRestricted ? "RESTRICTED (WARNINGS)" : "ALL GATES OPEN"}
            </span>
          </div>
          <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginTop: "0.2rem" }}>
            Deterministic invariant control plane protecting dispatch, rendering, and external side effects.
          </p>
        </div>

        {isPaused && (
          <button
            disabled={actionLoading}
            onClick={handleSafeResume}
            className="btn btn-success btn-sm"
          >
            {actionLoading ? "Verifying..." : "⚡ Safe Resume Recheck"}
          </button>
        )}
      </div>

      {errorMsg && (
        <div style={{ padding: "0.75rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", fontSize: "0.8rem", marginBottom: "1rem" }}>
          {errorMsg}
        </div>
      )}

      {/* Metrics Bar */}
      <div className="grid grid-cols-4" style={{ padding: "0.75rem 0", borderTop: "1px solid var(--border-subtle)", borderBottom: "1px solid var(--border-subtle)", marginBottom: "1rem" }}>
        <div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Epoch</div>
          <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
            v{status?.guardian_epoch ?? 1}
          </div>
        </div>
        <div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Accumulated Cost</div>
          <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
            {formatCurrencyUsd(status?.accumulated_cost_usd, "$0.00")} / {formatCurrencyUsd(status?.budget_ceiling_usd, "$50.00")}
          </div>
        </div>
        <div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Open Findings</div>
          <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
            {status?.open_findings_count ?? 0}
          </div>
        </div>
        <div>
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Active Exceptions</div>
          <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
            {exceptions.length}
          </div>
        </div>
      </div>

      {/* Checks Table */}
      {checks.length > 0 && (
        <div style={{ marginTop: "0.5rem" }}>
          <div className="section-title" style={{ marginBottom: "0.5rem" }}>Evaluated Invariant Checks ({checks.length})</div>
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: "22%" }}>Checkpoint</th>
                  <th style={{ width: "16%" }}>Decision</th>
                  <th style={{ width: "42%" }}>Rationale</th>
                  <th style={{ width: "20%", textAlign: "right" }}>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {checks.slice(0, 8).map((chk) => {
                  const decisionAction = chk.decision?.action || chk.status;
                  const isAllow = decisionAction === "ALLOW";
                  const isWarn = decisionAction === "WARN";
                  return (
                    <tr key={chk.id}>
                      <td className="text-mono" style={{ fontSize: "0.75rem", color: "var(--text-primary)" }}>
                        {chk.checkpoint}
                      </td>
                      <td>
                        <span className={`badge ${isAllow ? "badge-succeeded" : isWarn ? "badge-waiting" : "badge-failed"}`}>
                          {String(decisionAction)}
                        </span>
                      </td>
                      <td style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                        {chk.decision?.reason || "Passed invariant"}
                      </td>
                      <td style={{ textAlign: "right", fontSize: "0.75rem", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                        {new Date(chk.created_at).toLocaleTimeString()}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
