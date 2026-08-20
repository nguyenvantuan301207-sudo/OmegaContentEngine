"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AutonomyLevel, createMission } from "@/lib/api";

export default function NewMissionPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [description, setDescription] = useState("");
  const [autonomyLevel, setAutonomyLevel] = useState<AutonomyLevel>("SUPERVISED");
  const [priority, setPriority] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !objective.trim()) {
      setError("Title and objective are required.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const created = await createMission({
        title: title.trim(),
        objective: objective.trim(),
        description: description.trim() || undefined,
        autonomy_level: autonomyLevel,
        priority: Number(priority),
      });
      router.push(`/missions/${created.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create mission");
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white p-8">
      <div className="max-w-3xl mx-auto space-y-8">
        <div className="space-y-1 border-b border-zinc-800 pb-6">
          <Link
            href="/missions"
            className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors uppercase tracking-widest font-mono"
          >
            ← Back to Missions
          </Link>
          <h1 className="text-3xl font-bold tracking-tight text-white mt-2">
            Create New Mission
          </h1>
          <p className="text-sm text-zinc-400">
            Define a high-level goal and autonomy constraints for the orchestrator.
          </p>
        </div>

        {error && (
          <div className="p-4 rounded border border-red-800 bg-red-950/50 text-red-300 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="space-y-2">
            <label className="block text-xs font-mono uppercase text-zinc-300">
              Mission Title *
            </label>
            <input
              type="text"
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g., Q3 Developer Intelligence Campaign"
              className="w-full px-4 py-3 rounded bg-zinc-950 border border-zinc-800 text-white placeholder-zinc-600 focus:outline-none focus:border-emerald-500 text-sm font-sans"
            />
          </div>

          <div className="space-y-2">
            <label className="block text-xs font-mono uppercase text-zinc-300">
              Objective / Core Mandate *
            </label>
            <textarea
              required
              rows={3}
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              placeholder="Detail the fundamental goal, topic focus, or strategic outcome..."
              className="w-full px-4 py-3 rounded bg-zinc-950 border border-zinc-800 text-white placeholder-zinc-600 focus:outline-none focus:border-emerald-500 text-sm font-sans"
            />
          </div>

          <div className="space-y-2">
            <label className="block text-xs font-mono uppercase text-zinc-300">
              Description (Optional)
            </label>
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Additional background context, target audiences, or channel notes..."
              className="w-full px-4 py-3 rounded bg-zinc-950 border border-zinc-800 text-white placeholder-zinc-600 focus:outline-none focus:border-emerald-500 text-sm font-sans"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-2">
              <label className="block text-xs font-mono uppercase text-zinc-300">
                Autonomy Level
              </label>
              <select
                value={autonomyLevel}
                onChange={(e) => setAutonomyLevel(e.target.value as AutonomyLevel)}
                className="w-full px-4 py-3 rounded bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-emerald-500 text-sm font-mono"
              >
                <option value="SUPERVISED">SUPERVISED (Auto-dispatch with approval gates)</option>
                <option value="ASSISTED">ASSISTED (Human-guided milestones)</option>
                <option value="MANUAL">MANUAL (Explicit step execution)</option>
              </select>
            </div>

            <div className="space-y-2">
              <label className="block text-xs font-mono uppercase text-zinc-300">
                Priority (1 = Normal, 10 = Urgent)
              </label>
              <input
                type="number"
                min={1}
                max={10}
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value))}
                className="w-full px-4 py-3 rounded bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-emerald-500 text-sm font-mono"
              />
            </div>
          </div>

          <div className="flex items-center justify-end gap-3 pt-6 border-t border-zinc-800">
            <Link
              href="/missions"
              className="px-4 py-2 text-xs font-mono rounded bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-zinc-300 transition-colors"
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={submitting}
              className="px-6 py-2.5 text-sm font-semibold rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-black transition-colors"
            >
              {submitting ? "Creating..." : "Create Draft Mission"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
