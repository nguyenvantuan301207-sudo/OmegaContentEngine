"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Channel, getChannels } from "@/lib/api";

export default function ChannelsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadChannels = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getChannels();
      setChannels(data);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load channels");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadChannels();
  }, [loadChannels]);

  const getStatusBadge = (state: string) => {
    switch (state) {
      case "ACTIVE":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
      case "PAUSED":
        return "bg-amber-500/20 text-amber-400 border-amber-500/30";
      case "ARCHIVED":
        return "bg-rose-500/20 text-rose-400 border-rose-500/30";
      case "DRAFT":
      default:
        return "bg-slate-500/20 text-slate-400 border-slate-500/30";
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <div className="max-w-6xl mx-auto space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 pb-6">
          <div>
            <div className="flex items-center space-x-3">
              <Link
                href="/"
                className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
              >
                ← Back to Dashboard
              </Link>
            </div>
            <h1 className="text-3xl font-bold text-slate-100 tracking-tight mt-2">
              Channels & Operating Workspaces
            </h1>
            <p className="text-sm text-slate-400 mt-1">
              Autonomous channel identities, target audience profiles, brand voice, and DNA revisions.
            </p>
          </div>
          <Link
            href="/channels/new"
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg shadow-lg shadow-indigo-600/30 transition-all flex items-center space-x-2"
          >
            <span>+ New Channel</span>
          </Link>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-400 text-sm">
            {error}
          </div>
        )}

        {/* Loading State */}
        {loading ? (
          <div className="text-center py-20 text-slate-500 text-sm animate-pulse">
            Loading channels...
          </div>
        ) : channels.length === 0 ? (
          <div className="text-center py-20 bg-slate-900/50 border border-slate-800 rounded-2xl p-8">
            <p className="text-slate-400 text-sm">No channels found.</p>
            <Link
              href="/channels/new"
              className="inline-block mt-4 text-xs font-semibold text-indigo-400 hover:text-indigo-300 underline underline-offset-4"
            >
              Create your first operating channel
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {channels.map((c) => (
              <Link
                key={c.id}
                href={`/channels/${c.id}`}
                className="group block p-6 bg-slate-900/70 border border-slate-800/80 rounded-2xl hover:border-indigo-500/50 hover:bg-slate-900 transition-all duration-200"
              >
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <span className="text-xs font-mono text-slate-500 uppercase">
                      {c.platform}
                    </span>
                    <h2 className="text-lg font-semibold text-slate-100 group-hover:text-indigo-400 transition-colors">
                      {c.name}
                    </h2>
                    <p className="text-xs font-mono text-slate-400">/{c.slug}</p>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold border ${getStatusBadge(
                      c.state
                    )}`}
                  >
                    {c.state}
                  </span>
                </div>

                <p className="text-xs text-slate-400 mt-4 line-clamp-2 min-h-[32px]">
                  {c.description || (c.dna?.content_strategy?.niche ? `Niche: ${c.dna.content_strategy.niche}` : "No description provided.")}
                </p>

                <div className="mt-6 pt-4 border-t border-slate-800/60 flex items-center justify-between text-xs text-slate-500 font-mono">
                  <span>
                    Lang: <strong className="text-slate-300">{c.primary_language}</strong> ({c.target_region})
                  </span>
                  <span>{c.timezone}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
