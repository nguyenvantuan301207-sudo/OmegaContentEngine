"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Channel,
  TopicCandidate,
  TopicMemory,
  archiveTopicCandidate,
  createTopicCandidate,
  evaluateTopicBatch,
  evaluateTopicCandidate,
  getChannel,
  getTopicCandidates,
  getTopicMemory,
  getTopicRecommendations,
  rejectTopicCandidate,
  selectTopicCandidate,
} from "@/lib/api";

export default function ChannelTopicsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const channelId = resolvedParams.id;

  const [channel, setChannel] = useState<Channel | null>(null);
  const [activeTab, setActiveTab] = useState<"recommendations" | "candidates" | "memory">("recommendations");
  const [recommendations, setRecommendations] = useState<TopicCandidate[]>([]);
  const [candidates, setCandidates] = useState<TopicCandidate[]>([]);
  const [memories, setMemories] = useState<TopicMemory[]>([]);
  const [searchMemory, setSearchMemory] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Ingestion Modal State
  const [showIngestModal, setShowIngestModal] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newSummary, setNewSummary] = useState("");
  const [newKeywords, setNewKeywords] = useState("");
  const [newAngle, setNewAngle] = useState("");
  const [ingestError, setIngestError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [chanData, recsData, candsData, memsData] = await Promise.all([
        getChannel(channelId),
        getTopicRecommendations(channelId, 50.0, 20),
        getTopicCandidates(channelId),
        getTopicMemory(channelId, searchMemory || undefined),
      ]);
      setChannel(chanData);
      setRecommendations(recsData);
      setCandidates(candsData);
      setMemories(memsData);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load topic intelligence data");
    } finally {
      setLoading(false);
    }
  }, [channelId, searchMemory]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  async function handleIngest(e: React.FormEvent) {
    e.preventDefault();
    if (!newTitle.trim()) {
      setIngestError("Topic title is required.");
      return;
    }

    try {
      setActionLoading(true);
      setIngestError(null);

      const parsedKeywords = newKeywords
        .split(",")
        .map((k) => k.trim())
        .filter(Boolean);

      const angles = newAngle.trim()
        ? [{ angle: newAngle.trim(), hook: "Primary hook" }]
        : [];

      await createTopicCandidate(channelId, {
        title: newTitle.trim(),
        summary: newSummary.trim() || undefined,
        keywords: parsedKeywords,
        angles,
      });

      setShowIngestModal(false);
      setNewTitle("");
      setNewSummary("");
      setNewKeywords("");
      setNewAngle("");
      await loadData();
    } catch (err: unknown) {
      setIngestError(err instanceof Error ? err.message : "Failed to ingest candidate");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleEvaluateSingle(candidateId: string) {
    try {
      setActionLoading(true);
      await evaluateTopicCandidate(channelId, candidateId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to evaluate candidate");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleEvaluateBatch() {
    try {
      setActionLoading(true);
      await evaluateTopicBatch(channelId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to run batch evaluation");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleSelect(candidateId: string) {
    try {
      setActionLoading(true);
      await selectTopicCandidate(channelId, candidateId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to select candidate");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleReject(candidateId: string) {
    const reason = prompt("Enter rejection reason:", "Not aligned with current sprint");
    if (!reason) return;

    try {
      setActionLoading(true);
      await rejectTopicCandidate(channelId, candidateId, reason);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reject candidate");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleArchive(candidateId: string) {
    try {
      setActionLoading(true);
      await archiveTopicCandidate(channelId, candidateId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to archive candidate");
    } finally {
      setActionLoading(false);
    }
  }

  const getDuplicateBadge = (dup: string) => {
    switch (dup) {
      case "FRESH_TOPIC":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
      case "SAME_TOPIC_NEW_ANGLE":
        return "bg-cyan-500/20 text-cyan-300 border-cyan-500/30";
      case "RELATED_TOPIC":
        return "bg-blue-500/20 text-blue-300 border-blue-500/30";
      case "SEMANTIC_DUPLICATE":
        return "bg-amber-500/20 text-amber-400 border-amber-500/30";
      case "EXACT_DUPLICATE":
      default:
        return "bg-rose-500/20 text-rose-400 border-rose-500/30";
    }
  };

  const getStatusBadge = (st: string) => {
    switch (st) {
      case "SELECTED":
        return "bg-emerald-500/20 text-emerald-300 border-emerald-500/30";
      case "RECOMMENDED":
        return "bg-indigo-500/20 text-indigo-300 border-indigo-500/30";
      case "EVALUATED":
        return "bg-slate-500/20 text-slate-300 border-slate-500/30";
      case "REJECTED":
        return "bg-rose-900/30 text-rose-400 border-rose-800/40";
      case "ARCHIVED":
        return "bg-slate-800 text-slate-500 border-slate-700";
      case "DISCOVERED":
      default:
        return "bg-amber-500/20 text-amber-300 border-amber-500/30";
    }
  };

  if (loading && !channel) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 p-8 flex items-center justify-center">
        <div className="text-slate-400 animate-pulse text-sm">
          Loading Topic Intelligence Engine...
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* Navigation & Header */}
        <div>
          <div className="flex items-center space-x-3">
            <Link
              href={`/channels/${channelId}`}
              className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              ← Back to Channel Workspace
            </Link>
          </div>

          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mt-3 border-b border-slate-800 pb-6">
            <div>
              <div className="flex items-center space-x-3">
                <h1 className="text-2xl font-bold text-slate-100">
                  Topic Intelligence
                </h1>
                <span className="text-xs font-mono px-2 py-0.5 rounded bg-indigo-900/40 text-indigo-300 border border-indigo-700/40">
                  {channel?.name}
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                Transparent candidate scoring, local deduplication, content gap analysis, and Channel Topic Memory.
              </p>
            </div>

            <div className="flex items-center space-x-2">
              <button
                onClick={handleEvaluateBatch}
                disabled={actionLoading}
                className="px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold rounded-lg shadow-lg shadow-indigo-600/20 transition-all flex items-center space-x-1.5"
              >
                <span>⚡ Evaluate All Discovered</span>
              </button>
              <button
                onClick={() => setShowIngestModal(true)}
                className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-indigo-300 text-xs font-semibold rounded-lg border border-slate-700 transition-all"
              >
                + Ingest Candidate
              </button>
            </div>
          </div>
        </div>

        {/* Global Error Banner */}
        {error && (
          <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-400 text-sm">
            {error}
          </div>
        )}

        {/* Tabs Bar */}
        <div className="flex items-center space-x-2 border-b border-slate-800 pb-1">
          <button
            onClick={() => setActiveTab("recommendations")}
            className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors ${
              activeTab === "recommendations"
                ? "bg-slate-900 text-indigo-400 border-b-2 border-indigo-500"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            🎯 Top Recommendations ({recommendations.length})
          </button>
          <button
            onClick={() => setActiveTab("candidates")}
            className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors ${
              activeTab === "candidates"
                ? "bg-slate-900 text-indigo-400 border-b-2 border-indigo-500"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            📥 All Candidates ({candidates.length})
          </button>
          <button
            onClick={() => setActiveTab("memory")}
            className={`px-4 py-2 text-xs font-semibold rounded-t-lg transition-colors ${
              activeTab === "memory"
                ? "bg-slate-900 text-indigo-400 border-b-2 border-indigo-500"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            🧠 Topic Memory ({memories.length})
          </button>
        </div>

        {/* TAB 1: RECOMMENDATIONS */}
        {activeTab === "recommendations" && (
          <div className="space-y-4">
            {recommendations.length === 0 ? (
              <div className="text-center py-16 bg-slate-900/40 border border-slate-800 rounded-2xl p-8">
                <p className="text-slate-400 text-sm">No evaluated topic recommendations available.</p>
                <button
                  onClick={handleEvaluateBatch}
                  className="mt-3 text-xs text-indigo-400 underline font-medium"
                >
                  Evaluate pending candidates now
                </button>
              </div>
            ) : (
              <div className="space-y-4">
                {recommendations.map((rec, idx) => (
                  <div
                    key={rec.id}
                    className="p-6 bg-slate-900/70 border border-slate-800/80 rounded-2xl hover:border-indigo-500/40 transition-all space-y-4"
                  >
                    <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
                      <div className="space-y-1">
                        <div className="flex items-center space-x-2">
                          <span className="px-2 py-0.5 bg-indigo-900/40 text-indigo-300 font-mono text-xs font-bold rounded border border-indigo-700/30">
                            Rank #{idx + 1}
                          </span>
                          <span
                            className={`px-2 py-0.5 rounded text-[11px] font-semibold border ${getDuplicateBadge(
                              rec.duplicate_status
                            )}`}
                          >
                            {rec.duplicate_status}
                          </span>
                          <span
                            className={`px-2 py-0.5 rounded text-[11px] font-semibold border ${getStatusBadge(
                              rec.status
                            )}`}
                          >
                            {rec.status}
                          </span>
                        </div>
                        <h2 className="text-lg font-bold text-slate-100">{rec.title}</h2>
                        {rec.summary && (
                          <p className="text-xs text-slate-400">{rec.summary}</p>
                        )}
                      </div>

                      {/* Final Score Badge & Actions */}
                      <div className="flex items-center space-x-3">
                        <div className="text-right">
                          <div className="text-2xl font-mono font-black text-indigo-400">
                            {rec.final_score?.toFixed(1)}
                          </div>
                          <span className="text-[10px] text-slate-500 uppercase tracking-widest font-mono">
                            Final Score
                          </span>
                        </div>

                        {rec.status !== "SELECTED" && (
                          <button
                            onClick={() => handleSelect(rec.id)}
                            disabled={actionLoading}
                            className="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg shadow-lg shadow-emerald-600/20"
                          >
                            Select for Mission
                          </button>
                        )}

                        {rec.status !== "REJECTED" && (
                          <button
                            onClick={() => handleReject(rec.id)}
                            disabled={actionLoading}
                            className="px-3 py-1.5 bg-rose-900/40 hover:bg-rose-800 text-rose-300 text-xs font-semibold rounded-lg border border-rose-800/40"
                          >
                            Reject
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Operational Reasons Badges */}
                    {rec.reasons.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-2 border-t border-slate-800/60">
                        {rec.reasons.map((r, i) => (
                          <span
                            key={i}
                            className="px-2.5 py-0.5 bg-slate-800/80 text-slate-300 text-[11px] font-mono rounded border border-slate-700/50"
                          >
                            ✓ {r}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Score Breakdown Bar */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 pt-2 border-t border-slate-800/40 text-[11px] font-mono">
                      <div>
                        <span className="text-slate-500 block">Audience Fit:</span>
                        <strong className="text-slate-200">{rec.score_breakdown?.audience_fit}</strong>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Strategy Fit:</span>
                        <strong className="text-slate-200">{rec.score_breakdown?.strategic_fit}</strong>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Trend:</span>
                        <strong className="text-slate-200">{rec.score_breakdown?.trend}</strong>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Novelty:</span>
                        <strong className="text-slate-200">{rec.score_breakdown?.novelty}</strong>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Content Gap:</span>
                        <strong className="text-slate-200">{rec.score_breakdown?.content_gap}</strong>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Hist. Perf:</span>
                        <strong className="text-slate-200">{rec.score_breakdown?.historical_performance}</strong>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Feasibility:</span>
                        <strong className="text-slate-200">{rec.score_breakdown?.cost_efficiency}</strong>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Revenue:</span>
                        <strong className="text-slate-200">{rec.score_breakdown?.revenue_potential}</strong>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 2: ALL CANDIDATES */}
        {activeTab === "candidates" && (
          <div className="space-y-4">
            <div className="overflow-x-auto bg-slate-900/60 border border-slate-800 rounded-2xl">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-950 border-b border-slate-800 text-slate-400 font-mono">
                  <tr>
                    <th className="p-4">Title & Details</th>
                    <th className="p-4">Source</th>
                    <th className="p-4">Status</th>
                    <th className="p-4">Duplicate Check</th>
                    <th className="p-4">Final Score</th>
                    <th className="p-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {candidates.map((c) => (
                    <tr key={c.id} className="hover:bg-slate-900/40">
                      <td className="p-4 space-y-1">
                        <span className="font-semibold text-slate-200">{c.title}</span>
                        {c.keywords.length > 0 && (
                          <div className="text-[10px] text-slate-500">
                            Keywords: {c.keywords.join(", ")}
                          </div>
                        )}
                      </td>
                      <td className="p-4 font-mono text-slate-400">{c.source_type}</td>
                      <td className="p-4">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${getStatusBadge(c.status)}`}>
                          {c.status}
                        </span>
                      </td>
                      <td className="p-4">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${getDuplicateBadge(c.duplicate_status)}`}>
                          {c.duplicate_status}
                        </span>
                      </td>
                      <td className="p-4 font-mono font-bold text-slate-200">
                        {c.final_score != null ? c.final_score.toFixed(1) : "—"}
                      </td>
                      <td className="p-4 text-right space-x-2">
                        {c.status === "DISCOVERED" && (
                          <button
                            onClick={() => handleEvaluateSingle(c.id)}
                            disabled={actionLoading}
                            className="px-2.5 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-[11px] font-medium"
                          >
                            Evaluate
                          </button>
                        )}
                        {c.status !== "ARCHIVED" && (
                          <button
                            onClick={() => handleArchive(c.id)}
                            disabled={actionLoading}
                            className="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-400 rounded text-[11px]"
                          >
                            Archive
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* TAB 3: TOPIC MEMORY */}
        {activeTab === "memory" && (
          <div className="space-y-4">
            <div className="flex items-center space-x-3">
              <input
                type="text"
                value={searchMemory}
                onChange={(e) => setSearchMemory(e.target.value)}
                placeholder="Search Channel Topic Memory..."
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500 font-mono"
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {memories.map((m) => (
                <div
                  key={m.id}
                  className="p-4 bg-slate-900/50 border border-slate-800 rounded-xl space-y-2 text-xs"
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="font-bold text-slate-200">{m.canonical_topic}</h3>
                      <p className="text-[10px] font-mono text-slate-500 mt-0.5">
                        FP: {m.topic_fingerprint.slice(0, 16)}...
                      </p>
                    </div>
                    {m.last_evaluation_score != null && (
                      <span className="px-2 py-0.5 bg-slate-800 text-indigo-300 font-mono rounded text-[11px]">
                        Last Score: {m.last_evaluation_score.toFixed(1)}
                      </span>
                    )}
                  </div>

                  <div className="grid grid-cols-3 gap-2 pt-2 border-t border-slate-800/60 font-mono text-[11px]">
                    <div>
                      <span className="text-slate-500 block">Discovered:</span>
                      <strong className="text-slate-300">{m.times_discovered}x</strong>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Selected:</span>
                      <strong className="text-emerald-400">{m.times_selected}x</strong>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Rejected:</span>
                      <strong className="text-rose-400">{m.times_rejected}x</strong>
                    </div>
                  </div>

                  <div className="text-[10px] text-slate-500 pt-1 font-mono">
                    Last Seen: {new Date(m.last_seen_at).toLocaleDateString()}
                    {m.last_selected_at && ` • Selected: ${new Date(m.last_selected_at).toLocaleDateString()}`}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* INGESTION MODAL */}
        {showIngestModal && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <form
              onSubmit={handleIngest}
              className="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl"
            >
              <h2 className="text-sm font-bold text-slate-100 uppercase tracking-wide">
                Ingest Topic Candidate
              </h2>

              {ingestError && (
                <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-lg text-rose-400 text-xs">
                  {ingestError}
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Topic Title *
                </label>
                <input
                  type="text"
                  required
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="e.g. How LangChain Agents Work Internally"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Summary (Optional)
                </label>
                <textarea
                  rows={2}
                  value={newSummary}
                  onChange={(e) => setNewSummary(e.target.value)}
                  placeholder="Brief synopsis..."
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Keywords (comma-separated)
                </label>
                <input
                  type="text"
                  value={newKeywords}
                  onChange={(e) => setNewKeywords(e.target.value)}
                  placeholder="AI, LangChain, Agents, Architecture"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Specific Angle / Hook (Optional)
                </label>
                <input
                  type="text"
                  value={newAngle}
                  onChange={(e) => setNewAngle(e.target.value)}
                  placeholder="e.g. Memory and execution graph deep dive"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div className="flex justify-end space-x-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowIngestModal(false)}
                  className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg shadow-lg shadow-indigo-600/30"
                >
                  {actionLoading ? "Ingesting..." : "Ingest Candidate"}
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
