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

const STATE_COLORS: Record<string, string> = {
  DRAFT: "bg-zinc-700 text-zinc-300 border-zinc-600",
  READY: "bg-blue-950 text-blue-400 border-blue-800",
  RUNNING: "bg-amber-950 text-amber-400 border-amber-800 animate-pulse",
  PAUSED: "bg-purple-950 text-purple-400 border-purple-800",
  SUCCEEDED: "bg-emerald-950 text-emerald-400 border-emerald-800",
  FAILED: "bg-red-950 text-red-400 border-red-800",
  CANCELLED: "bg-zinc-800 text-zinc-400 border-zinc-700",
};

const TASK_STATE_COLORS: Record<string, string> = {
  PENDING: "bg-zinc-800 text-zinc-400 border-zinc-700",
  BLOCKED: "bg-zinc-800 text-zinc-500 border-zinc-700",
  READY: "bg-blue-950 text-blue-400 border-blue-800",
  QUEUED: "bg-indigo-950 text-indigo-400 border-indigo-800",
  RUNNING: "bg-amber-950 text-amber-400 border-amber-800 animate-pulse",
  WAITING_APPROVAL: "bg-orange-950 text-orange-400 border-orange-700 animate-pulse",
  SUCCEEDED: "bg-emerald-950 text-emerald-400 border-emerald-800",
  FAILED: "bg-red-950 text-red-400 border-red-800",
  CANCELLED: "bg-zinc-800 text-zinc-500 border-zinc-700",
};

const ACTOR_COLORS: Record<string, string> = {
  USER: "text-cyan-400",
  PLANNER: "text-purple-400",
  ORCHESTRATOR: "text-amber-400",
  WORKER: "text-emerald-400",
  SYSTEM: "text-zinc-400",
};

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
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      setError(err instanceof Error ? err.message : "Failed to load mission details");
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

  if (loading && !mission) {
    return (
      <div className="min-h-screen bg-black text-white p-8">
        <div className="max-w-6xl mx-auto text-zinc-500 font-mono text-sm">
          Loading mission {missionId}...
        </div>
      </div>
    );
  }

  if (!mission) {
    return (
      <div className="min-h-screen bg-black text-white p-8">
        <div className="max-w-6xl mx-auto space-y-4">
          <div className="text-red-400">Mission not found</div>
          <Link href="/missions" className="text-zinc-400 text-sm hover:underline">
            ← Back to Missions
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white p-8">
      <div className="max-w-6xl mx-auto space-y-8">
        {/* Header & Navigation */}
        <div className="space-y-4 border-b border-zinc-800 pb-6">
          <div className="flex items-center justify-between">
            <Link
              href="/missions"
              className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors uppercase tracking-widest font-mono"
            >
              ← All Missions
            </Link>
            <div className="flex items-center gap-3">
              <span
                className={`px-3 py-1 rounded text-xs font-mono font-medium border ${
                  STATE_COLORS[mission.state] || "bg-zinc-800 text-zinc-400"
                }`}
              >
                {mission.state}
              </span>
            </div>
          </div>

          <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
            <div className="space-y-2">
              <h1 className="text-3xl font-bold tracking-tight text-white">
                {mission.title}
              </h1>
              <p className="text-sm text-zinc-300 max-w-3xl">
                {mission.objective}
              </p>
              {mission.description && (
                <p className="text-xs text-zinc-500">{mission.description}</p>
              )}
            </div>

            {/* Action Buttons */}
            <div className="flex flex-wrap items-center gap-2">
              {mission.state === "DRAFT" && (
                <button
                  disabled={actionLoading}
                  onClick={() => handleAction(() => planMission(mission.id))}
                  className="px-4 py-2 text-xs font-mono font-semibold rounded bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 transition-colors"
                >
                  Plan DAG
                </button>
              )}

              {mission.state === "READY" && (
                <button
                  disabled={actionLoading}
                  onClick={() => handleAction(() => startMission(mission.id))}
                  className="px-4 py-2 text-xs font-mono font-semibold rounded bg-emerald-600 hover:bg-emerald-500 text-black disabled:opacity-50 transition-colors"
                >
                  Start Execution
                </button>
              )}

              {mission.state === "RUNNING" && (
                <button
                  disabled={actionLoading}
                  onClick={() => handleAction(() => pauseMission(mission.id))}
                  className="px-4 py-2 text-xs font-mono font-semibold rounded bg-purple-600 hover:bg-purple-500 text-white disabled:opacity-50 transition-colors"
                >
                  Pause
                </button>
              )}

              {mission.state === "PAUSED" && (
                <button
                  disabled={actionLoading}
                  onClick={() => handleAction(() => resumeMission(mission.id))}
                  className="px-4 py-2 text-xs font-mono font-semibold rounded bg-emerald-600 hover:bg-emerald-500 text-black disabled:opacity-50 transition-colors"
                >
                  Resume
                </button>
              )}

              {["READY", "RUNNING", "PAUSED"].includes(mission.state) && (
                <button
                  disabled={actionLoading}
                  onClick={() => handleAction(() => cancelMission(mission.id))}
                  className="px-4 py-2 text-xs font-mono font-semibold rounded bg-red-950 border border-red-800 hover:bg-red-900 text-red-300 disabled:opacity-50 transition-colors"
                >
                  Cancel
                </button>
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-4 text-xs font-mono text-zinc-400 pt-2">
            <span>Autonomy: <strong className="text-zinc-200">{mission.autonomy_level}</strong></span>
            <span>Priority: <strong className="text-zinc-200">{mission.priority}</strong></span>
            <span>ID: <code className="text-zinc-400">{mission.id}</code></span>
            <span>Created: {new Date(mission.created_at).toLocaleTimeString()}</span>
          </div>
        </div>

        {/* Error Notification */}
        {error && (
          <div className="p-4 rounded border border-red-800 bg-red-950/50 text-red-300 text-sm">
            {error}
          </div>
        )}

        {/* OMEGA-008 Guardian Subsystem Panel */}
        <GuardianPanel
          missionId={mission.id}
          isPaused={mission.state === "PAUSED"}
          onStateChanged={loadData}
        />

        {/* Main Content Grid: Tasks DAG & Decision Log */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Tasks DAG Column (2 cols) */}
          <div className="lg:col-span-2 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold tracking-tight text-white">
                Task DAG ({tasks.length})
              </h2>
              <span className="text-xs font-mono text-zinc-500">Auto-refreshing</span>
            </div>

            {tasks.length === 0 ? (
              <div className="p-8 rounded border border-zinc-800 bg-zinc-950 text-center text-zinc-500 text-sm">
                No tasks planned yet. Click &quot;Plan DAG&quot; above.
              </div>
            ) : (
              <div className="space-y-3">
                {tasks.map((task, idx) => (
                  <div
                    key={task.id}
                    className="p-4 rounded-lg border border-zinc-800 bg-zinc-950/70 space-y-3"
                  >
                    <div className="flex items-start justify-between">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-mono text-zinc-500">
                            #{idx + 1}
                          </span>
                          <h3 className="text-sm font-semibold text-white">
                            {task.title}
                          </h3>
                        </div>
                        <p className="text-xs text-zinc-400 font-mono">
                          type: {task.task_type}
                        </p>
                      </div>
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-mono font-medium border ${
                          TASK_STATE_COLORS[task.state] || "bg-zinc-800 text-zinc-400"
                        }`}
                      >
                        {task.state}
                      </span>
                    </div>

                    {task.description && (
                      <p className="text-xs text-zinc-400">{task.description}</p>
                    )}

                    {/* Pre-Execution Approval Gate */}
                    {task.state === "WAITING_APPROVAL" && (
                      <div className="p-3 rounded border border-orange-800 bg-orange-950/40 flex items-center justify-between gap-4">
                        <div className="text-xs text-orange-300">
                          <strong>Pre-Execution Approval Required:</strong> Review editorial sign-off before dispatching to publishing.
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            disabled={actionLoading}
                            onClick={() => handleAction(() => approveTask(task.id))}
                            className="px-3 py-1.5 text-xs font-mono font-bold rounded bg-emerald-600 hover:bg-emerald-500 text-black transition-colors"
                          >
                            Approve
                          </button>
                          <button
                            disabled={actionLoading}
                            onClick={() => handleAction(() => rejectTask(task.id, "Rejected by editor"))}
                            className="px-3 py-1.5 text-xs font-mono font-bold rounded bg-red-950 border border-red-800 hover:bg-red-900 text-red-300 transition-colors"
                          >
                            Reject
                          </button>
                        </div>
                      </div>
                    )}

                    {/* Error Output */}
                    {task.error && (
                      <div className="p-2.5 rounded bg-red-950/40 border border-red-900 text-xs font-mono text-red-300">
                        Error: {task.error}
                      </div>
                    )}

                    {/* Task Result Summary */}
                    {task.output && (
                      <div className="p-2.5 rounded bg-zinc-900 border border-zinc-800 text-xs font-mono text-zinc-300">
                        <span className="text-zinc-500">Output:</span> {JSON.stringify(task.output)}
                      </div>
                    )}

                    <div className="flex items-center gap-4 text-[10px] font-mono text-zinc-500">
                      <span>Retries: {task.retry_count}/{task.max_retries}</span>
                      {task.started_at && (
                        <span>Started: {new Date(task.started_at).toLocaleTimeString()}</span>
                      )}
                      {task.completed_at && (
                        <span>Completed: {new Date(task.completed_at).toLocaleTimeString()}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Decision Log Column (1 col) */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold tracking-tight text-white">
                Decision History ({decisions.length})
              </h2>
            </div>

            <div className="space-y-2 max-h-[650px] overflow-y-auto pr-1">
              {decisions.length === 0 ? (
                <div className="p-6 rounded border border-zinc-800 bg-zinc-950 text-center text-zinc-500 text-xs">
                  No decisions recorded yet.
                </div>
              ) : (
                decisions.map((dec) => (
                  <div
                    key={dec.id}
                    className="p-3 rounded border border-zinc-900 bg-zinc-950 text-xs space-y-1"
                  >
                    <div className="flex items-center justify-between">
                      <span
                        className={`font-mono font-bold text-[11px] ${
                          ACTOR_COLORS[dec.actor] || "text-zinc-400"
                        }`}
                      >
                        [{dec.actor}]
                      </span>
                      <span className="text-[10px] font-mono text-zinc-600">
                        {new Date(dec.created_at).toLocaleTimeString()}
                      </span>
                    </div>
                    <div className="font-semibold text-zinc-200">{dec.decision}</div>
                    <div className="text-zinc-400 text-[11px]">{dec.reason}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
