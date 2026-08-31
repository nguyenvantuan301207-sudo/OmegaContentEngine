"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AutonomyApprovalItem,
  AutonomyIterationSummary,
  AutonomyLoopStatus,
  approveAutonomyAction,
  cancelAutonomyLoop,
  getAutonomyApprovals,
  getAutonomyIterations,
  getAutonomyLoop,
  pauseAutonomyLoop,
  rejectAutonomyAction,
  resetAutonomyLoopFailure,
  resumeAutonomyLoop,
} from "@/lib/api";

interface Props {
  loopId: string;
}

export function AutonomyControlCard({ loopId }: Props) {
  const [loop, setLoop] = useState<AutonomyLoopStatus | null>(null);
  const [iterations, setIterations] = useState<AutonomyIterationSummary[]>([]);
  const [approvals, setApprovals] = useState<AutonomyApprovalItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [actionLoading, setActionLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [loopData, iterData, appData] = await Promise.all([
        getAutonomyLoop(loopId),
        getAutonomyIterations(loopId, 10),
        getAutonomyApprovals(loopId),
      ]);
      setLoop(loopData);
      setIterations(iterData);
      setApprovals(appData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load autonomy data");
    } finally {
      setLoading(false);
    }
  }, [loopId]);

  useEffect(() => {
    if (loopId) {
      loadData();
    }
  }, [loopId, loadData]);

  const handlePause = async () => {
    try {
      setActionLoading(true);
      await pauseAutonomyLoop(loopId);
      setSuccessMsg("Loop paused successfully");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to pause loop");
    } finally {
      setActionLoading(false);
    }
  };

  const handleResume = async () => {
    try {
      setActionLoading(true);
      await resumeAutonomyLoop(loopId);
      setSuccessMsg("Loop resumed successfully");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to resume loop");
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!confirm("Are you sure you want to permanently cancel this autonomous loop?")) return;
    try {
      setActionLoading(true);
      await cancelAutonomyLoop(loopId);
      setSuccessMsg("Loop cancelled");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to cancel loop");
    } finally {
      setActionLoading(false);
    }
  };

  const handleReset = async () => {
    try {
      setActionLoading(true);
      await resetAutonomyLoopFailure(loopId);
      setSuccessMsg("Loop failure reset to IDLE");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reset failure");
    } finally {
      setActionLoading(false);
    }
  };

  const handleApprove = async (approvalId: string) => {
    const reason = prompt("Enter approval justification:", "Operator approved via dashboard");
    if (!reason) return;
    try {
      setActionLoading(true);
      await approveAutonomyAction(approvalId, reason);
      setSuccessMsg("Action approved");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to approve action");
    } finally {
      setActionLoading(false);
    }
  };

  const handleReject = async (approvalId: string) => {
    const reason = prompt("Enter rejection reason:", "Operator rejected via dashboard");
    if (!reason) return;
    try {
      setActionLoading(true);
      await rejectAutonomyAction(approvalId, reason);
      setSuccessMsg("Action rejected");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reject action");
    } finally {
      setActionLoading(false);
    }
  };

  const getStateBadge = (st: string) => {
    switch (st) {
      case "IDLE":
        return "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300";
      case "EXECUTING":
      case "OBSERVING":
      case "PLANNING":
        return "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300";
      case "WAITING_APPROVAL":
        return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300";
      case "BLOCKED":
      case "FAILED":
        return "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300";
      case "COMPLETED":
        return "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300";
      default:
        return "bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300";
    }
  };

  if (loading && !loop) {
    return (
      <div className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
        <div className="animate-pulse space-y-4">
          <div className="h-6 w-1/3 rounded bg-zinc-200 dark:bg-zinc-800" />
          <div className="h-20 w-full rounded bg-zinc-100 dark:bg-zinc-900" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-zinc-100 pb-4 dark:border-zinc-800">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-zinc-900 dark:text-zinc-100">
            OMEGA-014 Autonomous Loop
          </h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Governed Autonomous Execution & Dispatch Operations
          </p>
        </div>
        {loop && (
          <div className="flex items-center gap-3">
            <span
              className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wider ${getStateBadge(
                loop.operational_state
              )}`}
            >
              {loop.operational_state}
            </span>
            <span className="rounded-full bg-zinc-100 px-3 py-1 text-xs font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
              Level: {loop.autonomy_level}
            </span>
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {error}
        </div>
      )}

      {successMsg && (
        <div className="rounded-lg bg-green-50 p-4 text-sm text-green-700 dark:bg-green-950/40 dark:text-green-300">
          {successMsg}
        </div>
      )}

      {/* Control Actions Bar */}
      {loop && (
        <div className="flex flex-wrap items-center gap-3">
          {loop.operational_state === "PAUSED" ? (
            <button
              onClick={handleResume}
              disabled={actionLoading}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow hover:bg-emerald-700 disabled:opacity-50"
            >
              Resume Loop
            </button>
          ) : (
            <button
              onClick={handlePause}
              disabled={actionLoading || ["COMPLETED", "CANCELLED", "FAILED"].includes(loop.operational_state)}
              className="rounded-lg bg-zinc-800 px-4 py-2 text-sm font-medium text-white shadow hover:bg-zinc-900 disabled:opacity-50 dark:bg-zinc-700 dark:hover:bg-zinc-600"
            >
              Pause Loop
            </button>
          )}

          {loop.operational_state === "FAILED" && (
            <button
              onClick={handleReset}
              disabled={actionLoading}
              className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white shadow hover:bg-amber-700 disabled:opacity-50"
            >
              Reset Failure
            </button>
          )}

          {!["COMPLETED", "CANCELLED"].includes(loop.operational_state) && (
            <button
              onClick={handleCancel}
              disabled={actionLoading}
              className="rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-900/60 dark:text-red-400 dark:hover:bg-red-950/30"
            >
              Cancel Loop
            </button>
          )}

          <button
            onClick={loadData}
            disabled={actionLoading}
            className="ml-auto rounded-lg border border-zinc-200 px-3 py-2 text-sm text-zinc-600 hover:bg-zinc-50 dark:border-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-900"
          >
            Refresh
          </button>
        </div>
      )}

      {/* Pending Approvals */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-700 dark:text-zinc-300">
          Pending Approvals ({approvals.filter((a) => !a.latest_decision).length})
        </h3>
        {approvals.filter((a) => !a.latest_decision).length === 0 ? (
          <p className="text-sm italic text-zinc-500 dark:text-zinc-400">No pending approval requests.</p>
        ) : (
          <div className="divide-y divide-zinc-100 rounded-lg border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
            {approvals
              .filter((a) => !a.latest_decision)
              .map((app) => (
                <div key={app.id} className="flex flex-wrap items-center justify-between gap-4 p-4">
                  <div>
                    <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                      Ticket: {app.semantic_action_key.slice(0, 16)}...
                    </div>
                    <div className="text-xs text-zinc-500">
                      Requested: {new Date(app.requested_at).toLocaleTimeString()} | Epoch: {app.guardian_epoch} |
                      Mission: {app.mission_state}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleApprove(app.id)}
                      disabled={actionLoading}
                      className="rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => handleReject(app.id)}
                      disabled={actionLoading}
                      className="rounded bg-red-600 px-3 py-1 text-xs font-semibold text-white hover:bg-red-700"
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* Iterations History */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-700 dark:text-zinc-300">
          Recent Iterations ({iterations.length})
        </h3>
        {iterations.length === 0 ? (
          <p className="text-sm italic text-zinc-500 dark:text-zinc-400">No iterations recorded yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-zinc-50 text-xs font-semibold text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
                <tr>
                  <th className="p-3">Seq</th>
                  <th className="p-3">State</th>
                  <th className="p-3">Started</th>
                  <th className="p-3">Completed</th>
                  <th className="p-3">Reason / Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {iterations.map((it) => (
                  <tr key={it.id} className="hover:bg-zinc-50/50 dark:hover:bg-zinc-900/50">
                    <td className="p-3 font-mono font-medium">#{it.iteration_sequence}</td>
                    <td className="p-3">
                      <span className={`rounded px-2 py-0.5 text-xs font-medium ${getStateBadge(it.state)}`}>
                        {it.state}
                      </span>
                    </td>
                    <td className="p-3 text-xs text-zinc-500">
                      {new Date(it.started_at).toLocaleTimeString()}
                    </td>
                    <td className="p-3 text-xs text-zinc-500">
                      {it.completed_at ? new Date(it.completed_at).toLocaleTimeString() : "—"}
                    </td>
                    <td className="p-3 text-xs text-zinc-600 dark:text-zinc-400">
                      {it.stop_reason || "In progress or nominal completion"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
