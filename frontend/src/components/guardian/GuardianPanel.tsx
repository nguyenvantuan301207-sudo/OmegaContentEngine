"use client";

import { useEffect, useState, useCallback } from "react";
import {
  GuardianCheck,
  GuardianConsolidatedStatus,
  GuardianException,
  getMissionGuardianStatus,
  listMissionGuardianChecks,
  listGuardianExceptions,
  revokeGuardianException,
  triggerSafeResume,
} from "@/lib/api";

const GATE_STATE_STYLES: Record<string, { bg: string; border: string; text: string; label: string }> = {
  OPEN: {
    bg: "bg-emerald-950/40",
    border: "border-emerald-800",
    text: "text-emerald-400",
    label: "ALL GATES OPEN",
  },
  RESTRICTED: {
    bg: "bg-amber-950/40",
    border: "border-amber-800",
    text: "text-amber-400",
    label: "RESTRICTED (MONITORED WARNINGS)",
  },
  BLOCKED: {
    bg: "bg-red-950/40",
    border: "border-red-800",
    text: "text-red-400",
    label: "GATE BLOCKED (DOWNSTREAM MUTATIONS PREVENTED)",
  },
  WAITING_GUARDIAN: {
    bg: "bg-indigo-950/40",
    border: "border-indigo-800",
    text: "text-indigo-400",
    label: "WAITING ON GUARDIAN EVALUATION",
  },
};

const SEVERITY_BADGES: Record<string, string> = {
  CRITICAL: "bg-red-950 text-red-400 border-red-800",
  HIGH: "bg-orange-950 text-orange-400 border-orange-800",
  MEDIUM: "bg-amber-950 text-amber-400 border-amber-800",
  LOW: "bg-blue-950 text-blue-400 border-blue-800",
  INFO: "bg-zinc-800 text-zinc-400 border-zinc-700",
};

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
  const [revokePromptId, setRevokePromptId] = useState<string | null>(null);
  const [revokeReason, setRevokeReason] = useState("");

  const loadGuardianData = useCallback(async () => {
    try {
      const [st, chk, exc] = await Promise.all([
        getMissionGuardianStatus(missionId),
        listMissionGuardianChecks(missionId),
        listGuardianExceptions(true),
      ]);
      setStatus(st);
      setChecks(chk);
      setExceptions(exc);
      setErrorMsg(null);
    } catch (err: unknown) {
      console.error("Failed loading Guardian data", err);
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
    setActionLoading(true);
    setErrorMsg(null);
    try {
      await triggerSafeResume(missionId, "OPERATOR", "Safe resume approved from mission UI");
      await loadGuardianData();
      if (onStateChanged) onStateChanged();
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Safe resume failed");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRevokeException = async (exceptionId: string) => {
    if (!revokeReason.trim()) return;
    setActionLoading(true);
    try {
      await revokeGuardianException(exceptionId, "OPERATOR", revokeReason);
      setRevokePromptId(null);
      setRevokeReason("");
      await loadGuardianData();
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to revoke exception");
    } finally {
      setActionLoading(false);
    }
  };

  if (loading && !status) {
    return (
      <div className="p-6 rounded-lg bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-500 animate-pulse">
        Loading Guardian Safety & Quality Subsystem...
      </div>
    );
  }

  const gateStyle =
    GATE_STATE_STYLES[status?.overall_gate_state || "OPEN"] || GATE_STATE_STYLES.OPEN;

  return (
    <div className="space-y-6">
      {/* ── Status Banner ── */}
      <div className={`p-5 rounded-lg border ${gateStyle.bg} ${gateStyle.border}`}>
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono uppercase tracking-widest text-zinc-400">
                Guardian Autonomous Gate
              </span>
              <span className={`px-2.5 py-0.5 rounded text-xs font-mono font-bold border ${gateStyle.text} ${gateStyle.border}`}>
                {gateStyle.label}
              </span>
            </div>
            <p className="text-xs text-zinc-300">
              Deterministic invariant control plane protecting dispatch, rendering, and side effects.
            </p>
          </div>

          <div className="flex items-center gap-3">
            {isPaused && (
              <button
                disabled={actionLoading}
                onClick={handleSafeResume}
                className="px-4 py-2 text-xs font-mono font-bold rounded bg-emerald-600 hover:bg-emerald-500 text-black disabled:opacity-50 transition-colors shadow"
              >
                {actionLoading ? "Verifying..." : "⚡ Safe Resume Recheck"}
              </button>
            )}
          </div>
        </div>

        {errorMsg && (
          <div className="mt-3 p-3 rounded bg-red-950/80 border border-red-800 text-red-200 text-xs font-mono">
            {errorMsg}
          </div>
        )}

        {/* ── Metrics Bar ── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 pt-4 border-t border-zinc-800/80">
          <div>
            <div className="text-[10px] uppercase font-mono text-zinc-400">Guardian Epoch</div>
            <div className="text-sm font-mono font-bold text-white">v{status?.guardian_epoch ?? 1}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase font-mono text-zinc-400">Cost Incurred</div>
            <div className="text-sm font-mono font-bold text-white">
              ${(status?.accumulated_cost_usd ?? 0).toFixed(2)} / ${(status?.budget_ceiling_usd ?? 50).toFixed(2)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase font-mono text-zinc-400">Remaining Budget</div>
            <div className="text-sm font-mono font-bold text-emerald-400">
              ${(status?.remaining_budget_usd ?? 0).toFixed(2)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase font-mono text-zinc-400">Open Findings</div>
            <div className="text-sm font-mono font-bold text-amber-400">
              {status?.open_findings_count ?? 0}
            </div>
          </div>
        </div>

        {/* ── Checkpoint Gate States ── */}
        {status?.checkpoint_states && Object.keys(status.checkpoint_states).length > 0 && (
          <div className="flex flex-wrap gap-2 mt-4 pt-3 border-t border-zinc-800/80">
            {Object.entries(status.checkpoint_states).map(([cp, state]) => {
              const cs = GATE_STATE_STYLES[state] || GATE_STATE_STYLES.OPEN;
              return (
                <div
                  key={cp}
                  className={`px-2.5 py-1 rounded border text-[11px] font-mono flex items-center gap-1.5 ${cs.bg} ${cs.border} ${cs.text}`}
                >
                  <span>{cp}</span>
                  <span className="font-bold">[{state}]</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Active Exceptions Drawer ── */}
      {exceptions.length > 0 && (
        <div className="p-4 rounded-lg bg-zinc-950 border border-zinc-800 space-y-3">
          <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-400">
            Active Scoped Exceptions ({exceptions.length})
          </h3>
          <div className="space-y-2">
            {exceptions.map((exc) => (
              <div
                key={exc.id}
                className="p-3 rounded bg-zinc-900 border border-zinc-800 text-xs font-mono flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2"
              >
                <div>
                  <div className="text-zinc-200 font-semibold">
                    Rule: <span className="text-amber-400">{exc.rule_id || "ALL"}</span> | Risk:{" "}
                    <span className="text-purple-400">{exc.risk_type || "ALL"}</span>
                  </div>
                  <div className="text-zinc-400 text-[11px]">
                    Reason: {exc.created_reason} (By: {exc.created_by})
                  </div>
                </div>

                <div>
                  {revokePromptId === exc.id ? (
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        placeholder="Revocation reason..."
                        value={revokeReason}
                        onChange={(e) => setRevokeReason(e.target.value)}
                        className="px-2 py-1 bg-black border border-zinc-700 rounded text-xs text-white"
                      />
                      <button
                        onClick={() => handleRevokeException(exc.id)}
                        disabled={actionLoading || !revokeReason.trim()}
                        className="px-2.5 py-1 rounded bg-red-800 text-white hover:bg-red-700 text-xs"
                      >
                        Confirm Revoke
                      </button>
                      <button
                        onClick={() => setRevokePromptId(null)}
                        className="text-zinc-400 text-xs hover:text-white"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setRevokePromptId(exc.id)}
                      className="px-2 py-1 rounded bg-zinc-800 border border-zinc-700 hover:bg-zinc-700 text-zinc-300 text-xs"
                    >
                      Revoke Exception
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Recent Guardian Checks ── */}
      <div className="p-4 rounded-lg bg-zinc-950 border border-zinc-800 space-y-3">
        <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-400">
          Recent Guardian Evaluations ({checks.length})
        </h3>
        {checks.length === 0 ? (
          <div className="text-xs text-zinc-600 font-mono">No evaluation checks recorded yet.</div>
        ) : (
          <div className="space-y-3">
            {checks.slice(0, 5).map((chk) => (
              <div
                key={chk.id}
                className="p-3.5 rounded bg-zinc-900 border border-zinc-800 space-y-2 text-xs font-mono"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 pb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-white">{chk.checkpoint}</span>
                    <span className="text-zinc-400">[{chk.trigger_type}]</span>
                  </div>
                  {chk.decision && (
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-bold border ${
                        GATE_STATE_STYLES[chk.decision.resulting_gate_state]?.text || "text-zinc-400"
                      } ${GATE_STATE_STYLES[chk.decision.resulting_gate_state]?.border || "border-zinc-700"}`}
                    >
                      {chk.decision.action} → {chk.decision.resulting_gate_state}
                    </span>
                  )}
                </div>

                {chk.decision && (
                  <p className="text-zinc-300 text-xs">{chk.decision.reason}</p>
                )}

                {/* Findings Drawer */}
                {chk.findings && chk.findings.length > 0 && (
                  <div className="mt-2 space-y-1.5">
                    <div className="text-[11px] text-zinc-400 font-semibold">
                      Findings ({chk.findings.length}):
                    </div>
                    {chk.findings.map((f) => (
                      <div
                        key={f.id}
                        className="p-2 rounded bg-black/40 border border-zinc-800 flex items-start gap-2"
                      >
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${
                            SEVERITY_BADGES[f.severity] || "bg-zinc-800 text-zinc-400"
                          }`}
                        >
                          {f.severity}
                        </span>
                        <div className="space-y-0.5 flex-1">
                          <div className="text-zinc-200">
                            <strong>{f.rule_id}</strong> — {f.risk_type}
                          </div>
                          <div className="text-zinc-400 text-[11px]">{f.message}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
