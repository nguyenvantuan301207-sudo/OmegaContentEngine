"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Channel,
  ChannelDNARevision,
  activateChannel,
  archiveChannel,
  getChannel,
  getChannelDNARevisions,
  pauseChannel,
  updateChannelDNA,
} from "@/lib/api";

export default function ChannelDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const channelId = resolvedParams.id;

  const [channel, setChannel] = useState<Channel | null>(null);
  const [revisions, setRevisions] = useState<ChannelDNARevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Edit DNA State
  const [isEditingDNA, setIsEditingDNA] = useState(false);
  const [dnaJson, setDnaJson] = useState("");
  const [changeReason, setChangeReason] = useState("");
  const [dnaError, setDnaError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [chanData, revsData] = await Promise.all([
        getChannel(channelId),
        getChannelDNARevisions(channelId),
      ]);
      setChannel(chanData);
      setRevisions(revsData);
      setDnaJson(JSON.stringify(chanData.dna, null, 2));
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load channel");
    } finally {
      setLoading(false);
    }
  }, [channelId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  async function handleAction(
    action: "activate" | "pause" | "archive"
  ) {
    try {
      setActionLoading(true);
      let updated: Channel;
      if (action === "activate") updated = await activateChannel(channelId);
      else if (action === "pause") updated = await pauseChannel(channelId);
      else updated = await archiveChannel(channelId);

      setChannel(updated);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `Action ${action} failed`);
    } finally {
      setActionLoading(false);
    }
  }

  async function handleSaveDNA(e: React.FormEvent) {
    e.preventDefault();
    if (!channel) return;
    if (!changeReason.trim() || changeReason.trim().length < 3) {
      setDnaError("A change reason of at least 3 characters is mandatory.");
      return;
    }

    try {
      setDnaError(null);
      const parsedDna = JSON.parse(dnaJson);
      await updateChannelDNA(channelId, parsedDna, changeReason.trim());
      setIsEditingDNA(false);
      setChangeReason("");
      await loadData();
    } catch (err: unknown) {
      setDnaError(err instanceof Error ? err.message : "Invalid DNA format");
    }
  }

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

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 p-8 flex items-center justify-center">
        <div className="text-slate-400 animate-pulse text-sm">
          Loading Channel workspace...
        </div>
      </div>
    );
  }

  if (error && !channel) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
        <div className="max-w-4xl mx-auto space-y-4">
          <Link href="/channels" className="text-xs text-indigo-400">
            ← Back to Channels
          </Link>
          <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-400 text-sm">
            {error}
          </div>
        </div>
      </div>
    );
  }

  if (!channel) return null;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <div className="max-w-6xl mx-auto space-y-8">
        {/* Navigation & Header */}
        <div>
          <Link
            href="/channels"
            className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            ← Back to Channels
          </Link>

          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mt-3 border-b border-slate-800 pb-6">
            <div>
              <div className="flex items-center space-x-3">
                <h1 className="text-2xl font-bold text-slate-100">
                  {channel.name}
                </h1>
                <span
                  className={`px-2.5 py-0.5 rounded-full text-xs font-semibold border ${getStatusBadge(
                    channel.state
                  )}`}
                >
                  {channel.state}
                </span>
                <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                  {channel.platform}
                </span>
              </div>
              <p className="text-xs font-mono text-slate-400 mt-1">
                Slug: /{channel.slug} • ID: {channel.id}
              </p>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center space-x-2">
              {(channel.state === "DRAFT" || channel.state === "PAUSED") && (
                <button
                  onClick={() => handleAction("activate")}
                  disabled={actionLoading}
                  className="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg shadow-lg shadow-emerald-600/20 transition-all"
                >
                  Activate Channel
                </button>
              )}

              {channel.state === "ACTIVE" && (
                <button
                  onClick={() => handleAction("pause")}
                  disabled={actionLoading}
                  className="px-3.5 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold rounded-lg shadow-lg shadow-amber-600/20 transition-all"
                >
                  Pause Channel
                </button>
              )}

              {channel.state !== "ARCHIVED" && (
                <button
                  onClick={() => handleAction("archive")}
                  disabled={actionLoading}
                  className="px-3.5 py-1.5 bg-rose-900/60 hover:bg-rose-800 text-rose-200 text-xs font-semibold rounded-lg border border-rose-700/50 transition-all"
                >
                  Archive
                </button>
              )}

              {channel.state !== "ARCHIVED" && (
                <Link
                  href={`/channels/${channel.id}/topics`}
                  className="px-3.5 py-1.5 bg-indigo-600/30 hover:bg-indigo-600/50 text-indigo-300 text-xs font-semibold rounded-lg border border-indigo-500/40 transition-all flex items-center space-x-1"
                >
                  <span>💡 Topics →</span>
                </Link>
              )}

              {channel.state !== "ARCHIVED" && (
                <Link
                  href={`/channels/${channel.id}/research`}
                  className="px-3.5 py-1.5 bg-cyan-600/30 hover:bg-cyan-600/50 text-cyan-300 text-xs font-semibold rounded-lg border border-cyan-500/40 transition-all flex items-center space-x-1"
                >
                  <span>🔬 Research →</span>
                </Link>
              )}

              {channel.state !== "ARCHIVED" && (
                <button
                  onClick={() => {
                    setIsEditingDNA(!isEditingDNA);
                    setDnaJson(JSON.stringify(channel.dna, null, 2));
                  }}
                  className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-indigo-400 text-xs font-semibold rounded-lg border border-slate-700 transition-all"
                >
                  {isEditingDNA ? "Close Editor" : "Edit DNA"}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Global Error */}
        {error && (
          <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-400 text-sm">
            {error}
          </div>
        )}

        {/* DNA Editor Modal/Panel */}
        {isEditingDNA && (
          <form
            onSubmit={handleSaveDNA}
            className="p-6 bg-slate-900 border border-indigo-500/40 rounded-2xl space-y-4 shadow-xl shadow-indigo-950/40"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold text-indigo-300 uppercase tracking-wide">
                Update Channel DNA (Creates New Revision)
              </h2>
              <span className="text-xs text-slate-400 font-mono">
                Current Active Version: v{revisions[0]?.version || 1}
              </span>
            </div>

            {dnaError && (
              <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-lg text-rose-400 text-xs">
                {dnaError}
              </div>
            )}

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Change Reason (Mandatory rationale for audit trail) *
              </label>
              <input
                type="text"
                required
                value={changeReason}
                onChange={(e) => setChangeReason(e.target.value)}
                placeholder="e.g. Pivoting primary content pillar to Deep Dive tutorials and updating tone to analytical"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Channel DNA JSON
              </label>
              <textarea
                rows={14}
                value={dnaJson}
                onChange={(e) => setDnaJson(e.target.value)}
                className="w-full bg-slate-950 font-mono text-xs text-indigo-200 border border-slate-800 rounded-lg p-3 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div className="flex justify-end space-x-3 pt-2">
              <button
                type="button"
                onClick={() => setIsEditingDNA(false)}
                className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium rounded-lg"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg shadow-lg shadow-indigo-600/30"
              >
                Save & Create Revision v{(revisions[0]?.version || 1) + 1}
              </button>
            </div>
          </form>
        )}

        {/* Structured DNA Display Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {/* Identity & Localization */}
          <div className="p-5 bg-slate-900/60 border border-slate-800/80 rounded-2xl space-y-3">
            <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Localization & Routing
            </h2>
            <div className="space-y-1 text-xs">
              <p>
                <span className="text-slate-500">Language:</span>{" "}
                <strong className="text-slate-200 font-mono">
                  {channel.primary_language}
                </strong>
              </p>
              <p>
                <span className="text-slate-500">Target Region:</span>{" "}
                <strong className="text-slate-200 font-mono">
                  {channel.target_region}
                </strong>
              </p>
              <p>
                <span className="text-slate-500">Timezone:</span>{" "}
                <strong className="text-slate-200 font-mono">
                  {channel.timezone}
                </strong>
              </p>
              <p>
                <span className="text-slate-500">Platform ID:</span>{" "}
                <span className="text-slate-400 font-mono">
                  {channel.platform_channel_id || "None (unlinked)"}
                </span>
              </p>
            </div>
          </div>

          {/* Audience Profile */}
          <div className="p-5 bg-slate-900/60 border border-slate-800/80 rounded-2xl space-y-3">
            <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Audience Profile
            </h2>
            <div className="space-y-1 text-xs">
              <p>
                <span className="text-slate-500">Age Range:</span>{" "}
                <span className="text-slate-200">
                  {channel.dna?.audience?.age_range || "N/A"}
                </span>
              </p>
              <p>
                <span className="text-slate-500">Knowledge Level:</span>{" "}
                <span className="text-slate-200">
                  {channel.dna?.audience?.knowledge_level || "ALL_LEVELS"}
                </span>
              </p>
              <p>
                <span className="text-slate-500">Interests:</span>{" "}
                <span className="text-slate-300">
                  {channel.dna?.audience?.interests?.join(", ") || "None"}
                </span>
              </p>
              <p>
                <span className="text-slate-500">Length:</span>{" "}
                <span className="text-slate-300">
                  {channel.dna?.audience?.preferred_content_length || "N/A"}
                </span>
              </p>
            </div>
          </div>

          {/* Brand Voice */}
          <div className="p-5 bg-slate-900/60 border border-slate-800/80 rounded-2xl space-y-3">
            <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Brand Voice & Tone
            </h2>
            <div className="space-y-1 text-xs">
              <p>
                <span className="text-slate-500">Tone:</span>{" "}
                <span className="text-slate-200">
                  {channel.dna?.brand_voice?.tone?.join(", ") || "Standard"}
                </span>
              </p>
              <p>
                <span className="text-slate-500">Pacing / Formality:</span>{" "}
                <span className="text-slate-300">
                  {channel.dna?.brand_voice?.pace} •{" "}
                  {channel.dna?.brand_voice?.formality}
                </span>
              </p>
              <p>
                <span className="text-slate-500">Narration:</span>{" "}
                <span className="text-slate-300">
                  {channel.dna?.brand_voice?.narration_style}
                </span>
              </p>
            </div>
          </div>

          {/* Content Strategy */}
          <div className="p-5 bg-slate-900/60 border border-slate-800/80 rounded-2xl space-y-3">
            <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Content Strategy
            </h2>
            <div className="space-y-1 text-xs">
              <p>
                <span className="text-slate-500">Niche:</span>{" "}
                <strong className="text-indigo-300">
                  {channel.dna?.content_strategy?.niche}
                </strong>
              </p>
              <p>
                <span className="text-slate-500">Duration Range:</span>{" "}
                <span className="text-slate-300 font-mono">
                  {channel.dna?.content_strategy?.default_duration_min_seconds}s -{" "}
                  {channel.dna?.content_strategy?.default_duration_max_seconds}s
                </span>
              </p>
              <div className="mt-2">
                <span className="text-slate-500 block mb-1">Pillars:</span>
                <div className="flex flex-wrap gap-1">
                  {channel.dna?.content_strategy?.content_pillars?.map(
                    (pillar, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 bg-slate-800 text-slate-300 text-[11px] rounded"
                      >
                        {idx + 1}. {pillar}
                      </span>
                    )
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Publishing Preferences */}
          <div className="p-5 bg-slate-900/60 border border-slate-800/80 rounded-2xl space-y-3">
            <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Publishing Preferences
            </h2>
            <div className="space-y-1 text-xs">
              <p>
                <span className="text-slate-500">Frequency:</span>{" "}
                <span className="text-emerald-400 font-semibold font-mono">
                  {channel.dna?.publishing_preferences?.frequency_target?.count}{" "}
                  per{" "}
                  {channel.dna?.publishing_preferences?.frequency_target?.period}
                </span>
              </p>
              <p>
                <span className="text-slate-500">Preferred Days:</span>{" "}
                <span className="text-slate-300">
                  {channel.dna?.publishing_preferences?.preferred_days?.join(
                    ", "
                  )}
                </span>
              </p>
              <p>
                <span className="text-slate-500">Windows:</span>{" "}
                <span className="text-slate-300 font-mono">
                  {channel.dna?.publishing_preferences?.preferred_time_windows?.join(
                    ", "
                  )}
                </span>
              </p>
            </div>
          </div>

          {/* Goals & KPIs */}
          <div className="p-5 bg-slate-900/60 border border-slate-800/80 rounded-2xl space-y-3">
            <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Goals & KPIs
            </h2>
            <div className="space-y-1 text-xs">
              <p>
                <span className="text-slate-500">Primary Goal:</span>{" "}
                <strong className="text-amber-300 font-semibold">
                  {channel.dna?.goals_and_kpis?.primary_goal}
                </strong>
              </p>
              <div className="mt-2 space-y-1">
                {channel.dna?.goals_and_kpis?.target_kpis?.map((kpi, idx) => (
                  <div
                    key={idx}
                    className="flex justify-between items-center text-[11px] py-0.5 border-b border-slate-800/50"
                  >
                    <span className="text-slate-400 font-mono">{kpi.metric}:</span>
                    <span className="text-slate-200 font-mono font-medium">
                      {kpi.target_value}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* DNA Revision History Stream */}
        <div className="space-y-4 pt-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <h2 className="text-base font-semibold text-slate-200">
              DNA Revision History ({revisions.length})
            </h2>
            <span className="text-xs text-slate-500">
              Immutable snapshots used by reproducible MissionExecutions
            </span>
          </div>

          <div className="space-y-3">
            {revisions.map((rev) => (
              <div
                key={rev.id}
                className="p-4 bg-slate-900/40 border border-slate-800/70 rounded-xl flex items-start justify-between"
              >
                <div className="space-y-1">
                  <div className="flex items-center space-x-2">
                    <span className="px-2 py-0.5 bg-indigo-900/40 text-indigo-300 font-mono font-bold text-xs rounded border border-indigo-700/40">
                      v{rev.version}
                    </span>
                    <span className="text-xs text-slate-400">
                      by <strong className="text-slate-300">{rev.actor}</strong>
                    </span>
                    <span className="text-xs text-slate-600">•</span>
                    <span className="text-xs text-slate-500 font-mono">
                      {new Date(rev.created_at).toLocaleString()}
                    </span>
                  </div>
                  <p className="text-xs text-slate-300 mt-1 font-medium">
                    {rev.change_reason}
                  </p>
                  <p className="text-[11px] text-slate-500 font-mono">
                    ID: {rev.id}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
