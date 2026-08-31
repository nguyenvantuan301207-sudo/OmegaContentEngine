"use client";

import React, { useEffect, useState } from "react";
import {
  LearningKnowledgeItem,
  LearningHypothesisSummary,
  getChannelKnowledge,
  getChannelHypotheses,
  triggerLearningRefresh,
} from "@/lib/api";
import { formatDecimal, toFiniteNumber } from "@/lib/formatters";

interface Props {
  channelId: string;
}

export function LearningInsightsCard({ channelId }: Props) {
  const [knowledge, setKnowledge] = useState<LearningKnowledgeItem[]>([]);
  const [hypotheses, setHypotheses] = useState<LearningHypothesisSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshSuccess, setRefreshSuccess] = useState<boolean>(false);

  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        setError(null);
        const [kList, hList] = await Promise.all([
          getChannelKnowledge(channelId),
          getChannelHypotheses(channelId),
        ]);
        setKnowledge(kList);
        setHypotheses(hList);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load learning data");
      } finally {
        setLoading(false);
      }
    }
    if (channelId) {
      loadData();
    }
  }, [channelId]);

  const handleRefresh = async () => {
    try {
      setRefreshing(true);
      setRefreshSuccess(false);
      await triggerLearningRefresh(channelId);
      setRefreshSuccess(true);
      setTimeout(() => setRefreshSuccess(false), 4000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Refresh request failed");
    } finally {
      setRefreshing(false);
    }
  };

  const getConfidenceBadge = (conf: string) => {
    switch (conf) {
      case "VERY_HIGH":
        return "bg-emerald-950/80 text-emerald-300 border-emerald-500/50";
      case "HIGH":
        return "bg-teal-950/80 text-teal-300 border-teal-500/50";
      case "MODERATE":
        return "bg-amber-950/80 text-amber-300 border-amber-500/50";
      case "LOW":
        return "bg-slate-900/80 text-slate-300 border-slate-700";
      default:
        return "bg-zinc-900 text-zinc-400 border-zinc-700";
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "SUPPORTED":
      case "ACTIVE":
        return "bg-emerald-950/60 text-emerald-400 border-emerald-800";
      case "WEAKENED":
        return "bg-amber-950/60 text-amber-400 border-amber-800";
      case "CONTRADICTED":
        return "bg-rose-950/60 text-rose-400 border-rose-800";
      case "INCONCLUSIVE":
        return "bg-blue-950/60 text-blue-400 border-blue-800";
      default:
        return "bg-zinc-900 text-zinc-400 border-zinc-800";
    }
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/80 p-6 text-zinc-400 animate-pulse">
        <div className="h-6 w-48 bg-zinc-800 rounded mb-4"></div>
        <div className="h-20 bg-zinc-900 rounded"></div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-zinc-800 bg-gradient-to-b from-zinc-900/90 to-zinc-950 p-6 shadow-2xl space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-zinc-800/80 pb-4">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-semibold tracking-tight text-white">
              Institutional Channel Memory
            </h2>
            <span className="rounded-md border border-amber-500/40 bg-amber-950/40 px-2.5 py-0.5 text-xs font-medium text-amber-300">
              OBSERVATIONAL ASSOCIATION — NOT PROVEN CAUSATION
            </span>
          </div>
          <p className="mt-1 text-sm text-zinc-400">
            Factual historical relationships derived from settled multi-window performance evidence.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {refreshSuccess && (
            <span className="text-xs text-emerald-400 font-medium">
              Sweep queued successfully
            </span>
          )}
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-800/90 px-3 py-1.5 text-xs font-medium text-zinc-200 transition hover:bg-zinc-700 hover:text-white disabled:opacity-50"
          >
            {refreshing ? "Queuing..." : "Request Learning Sweep"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-900/50 bg-rose-950/30 p-3 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* Section: Validated Knowledge Items */}
      <div>
        <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-400 mb-3">
          Active Knowledge Claims ({knowledge.length})
        </h3>
        {knowledge.length === 0 ? (
          <p className="text-xs text-zinc-500 italic">
            No knowledge claims have met sample size and effect size thresholds yet.
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
            {knowledge.map((item) => (
              <div
                key={item.knowledge_item_id}
                className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 transition hover:border-zinc-700"
              >
                <div className="flex items-center justify-between gap-2 mb-2">
                  <span className="text-xs font-mono font-medium text-zinc-300 uppercase">
                    {item.knowledge_type}
                  </span>
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded border px-2 py-0.5 text-[11px] font-semibold tracking-wide ${getConfidenceBadge(
                        item.confidence_class
                      )}`}
                    >
                      {item.confidence_class}
                    </span>
                    <span
                      className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${getStatusBadge(
                        item.current_status
                      )}`}
                    >
                      {item.current_status}
                    </span>
                  </div>
                </div>

                <p className="text-sm text-zinc-200 leading-snug">
                  {item.human_readable_summary}
                </p>

                <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-zinc-400 border-t border-zinc-800/60 pt-2.5">
                  <div>
                    <span className="text-zinc-500">Effect:</span>{" "}
                    <span className="font-mono text-zinc-200">
                      {(toFiniteNumber(item.effect_size_absolute) ?? 0) > 0 ? "+" : ""}
                      {formatDecimal(item.effect_size_absolute, 1)}
                    </span>
                    {item.effect_size_relative_percent !== null && (
                      <span className="text-zinc-400 font-mono ml-1">
                        ({(toFiniteNumber(item.effect_size_relative_percent) ?? 0) > 0 ? "+" : ""}
                        {formatDecimal(item.effect_size_relative_percent, 1)}%)
                      </span>
                    )}
                  </div>
                  <div>
                    <span className="text-zinc-500">Cliff&apos;s Delta:</span>{" "}
                    <span className="font-mono text-zinc-200">
                      {formatDecimal(item.cliffs_delta, 2)}
                    </span>
                  </div>
                  <div>
                    <span className="text-zinc-500">Sample:</span>{" "}
                    <span className="font-mono text-zinc-300">
                      Nt={item.sample_size_treatment}, Nc={item.sample_size_control}
                    </span>
                  </div>
                  <div>
                    <span className="text-zinc-500">Revision:</span>{" "}
                    <span className="font-mono text-zinc-400">r{item.revision_number}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Section: Active Hypotheses */}
      <div>
        <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-400 mb-3">
          Investigational Hypotheses ({hypotheses.length})
        </h3>
        {hypotheses.length === 0 ? (
          <p className="text-xs text-zinc-500 italic">
            No hypotheses are currently defined for this channel.
          </p>
        ) : (
          <div className="space-y-2.5">
            {hypotheses.map((hyp) => (
              <div
                key={hyp.hypothesis_family_id}
                className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-zinc-800/70 bg-zinc-900/30 px-4 py-3 text-xs"
              >
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-medium text-zinc-300">
                      {hyp.hypothesis_slug}
                    </span>
                    <span className="text-zinc-500">v{hyp.current_version}</span>
                  </div>
                  <p className="text-zinc-400">{hyp.description}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-zinc-400 font-mono">
                    Target: {hyp.target_outcome_metric} ({hyp.target_evaluation_window})
                  </span>
                  <span
                    className={`rounded border px-2 py-0.5 text-[11px] font-semibold ${getStatusBadge(
                      hyp.current_status
                    )}`}
                  >
                    {hyp.current_status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
