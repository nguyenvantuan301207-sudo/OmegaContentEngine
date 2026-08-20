"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getMissions, Mission } from "@/lib/api";

const STATE_COLORS: Record<string, string> = {
  DRAFT: "bg-zinc-700 text-zinc-300 border-zinc-600",
  READY: "bg-blue-950 text-blue-400 border-blue-800",
  RUNNING: "bg-amber-950 text-amber-400 border-amber-800 animate-pulse",
  PAUSED: "bg-purple-950 text-purple-400 border-purple-800",
  SUCCEEDED: "bg-emerald-950 text-emerald-400 border-emerald-800",
  FAILED: "bg-red-950 text-red-400 border-red-800",
  CANCELLED: "bg-zinc-800 text-zinc-400 border-zinc-700",
};

export default function MissionsPage() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMissions = async () => {
    try {
      const data = await getMissions();
      setMissions(data);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load missions");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMissions();
  }, []);

  return (
    <div className="min-h-screen bg-black text-white p-8">
      <div className="max-w-6xl mx-auto space-y-8">
        {/* Navigation & Header */}
        <div className="flex items-center justify-between border-b border-zinc-800 pb-6">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <Link
                href="/"
                className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors uppercase tracking-widest font-mono"
              >
                ← OMEGA System
              </Link>
            </div>
            <h1 className="text-3xl font-bold tracking-tight text-white">
              Missions Engine
            </h1>
            <p className="text-sm text-zinc-400">
              Autonomous orchestration, DAG planning, and decision history.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={fetchMissions}
              className="px-3 py-2 text-xs font-mono rounded bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-zinc-300 transition-colors"
            >
              Refresh
            </button>
            <Link
              href="/missions/new"
              className="px-4 py-2 text-sm font-semibold rounded bg-emerald-600 hover:bg-emerald-500 text-black transition-colors"
            >
              + Create Mission
            </Link>
          </div>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="p-4 rounded border border-red-800 bg-red-950/50 text-red-300 text-sm">
            {error}
          </div>
        )}

        {/* Missions List */}
        {loading ? (
          <div className="p-12 text-center text-zinc-500 font-mono text-sm">
            Loading missions...
          </div>
        ) : missions.length === 0 ? (
          <div className="p-12 rounded border border-zinc-800 bg-zinc-950 text-center space-y-4">
            <div className="text-zinc-400 text-sm">No missions found.</div>
            <Link
              href="/missions/new"
              className="inline-block px-4 py-2 text-xs font-semibold rounded bg-emerald-600 hover:bg-emerald-500 text-black transition-colors"
            >
              Create First Mission
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {missions.map((mission) => (
              <Link
                key={mission.id}
                href={`/missions/${mission.id}`}
                className="group block p-6 rounded-lg border border-zinc-800 bg-zinc-950/80 hover:bg-zinc-900/80 hover:border-zinc-700 transition-all space-y-3"
              >
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <h2 className="text-lg font-semibold text-white group-hover:text-emerald-400 transition-colors">
                      {mission.title}
                    </h2>
                    <p className="text-sm text-zinc-400 line-clamp-2">
                      {mission.objective}
                    </p>
                  </div>
                  <span
                    className={`px-2.5 py-1 rounded text-xs font-mono font-medium border ${
                      STATE_COLORS[mission.state] || "bg-zinc-800 text-zinc-400 border-zinc-700"
                    }`}
                  >
                    {mission.state}
                  </span>
                </div>

                <div className="flex items-center gap-4 text-xs font-mono text-zinc-500 pt-2 border-t border-zinc-900">
                  <span>Autonomy: <strong className="text-zinc-300">{mission.autonomy_level}</strong></span>
                  <span>Priority: <strong className="text-zinc-300">{mission.priority}</strong></span>
                  <span>Created: {new Date(mission.created_at).toLocaleString()}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
