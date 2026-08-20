"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Channel,
  ClaimEvidence,
  PrimarySourceStatus,
  ResearchBrief,
  ResearchClaim,
  ResearchConflict,
  ResearchRequest,
  ResearchSource,
  TopicCandidate,
  addResearchSource,
  createResearchRequest,
  getChannel,
  getResearchBrief,
  listCandidates,
  listResearchClaims,
  listResearchConflicts,
  listResearchRequests,
  listResearchSources,
  runResearchPipeline,
} from "@/lib/api";

type Tab = "requests" | "brief" | "sources" | "claims" | "conflicts";

export default function ResearchEnginePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const channelId = resolvedParams.id;

  const [channel, setChannel] = useState<Channel | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("requests");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Core Data
  const [requests, setRequests] = useState<ResearchRequest[]>([]);
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [currentBrief, setCurrentBrief] = useState<ResearchBrief | null>(null);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [claims, setClaims] = useState<ResearchClaim[]>([]);
  const [conflicts, setConflicts] = useState<ResearchConflict[]>([]);
  const [eligibleTopics, setEligibleTopics] = useState<TopicCandidate[]>([]);

  // Action states
  const [actionLoading, setActionLoading] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showSourceModal, setShowSourceModal] = useState(false);

  // Form States
  const [selectedTopicId, setSelectedTopicId] = useState("");
  const [researchQuestion, setResearchQuestion] = useState("");
  const [researchScope, setResearchScope] = useState("");

  const [sourceTitle, setSourceTitle] = useState("");
  const [sourcePublisher, setSourcePublisher] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceExcerpt, setSourceExcerpt] = useState("");
  const [sourcePrimaryStatus, setSourcePrimaryStatus] = useState<PrimarySourceStatus>("UNKNOWN");

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [chanData, reqsData, topicsData] = await Promise.all([
        getChannel(channelId),
        listResearchRequests(channelId),
        listCandidates(channelId),
      ]);
      setChannel(chanData);
      setRequests(reqsData);
      // Eligible topics for research: EVALUATED, RECOMMENDED, SELECTED
      const eligible = topicsData.filter(
        (t) => t.status === "EVALUATED" || t.status === "RECOMMENDED" || t.status === "SELECTED"
      );
      setEligibleTopics(eligible);
      if (eligible.length > 0 && !selectedTopicId) {
        setSelectedTopicId(eligible[0].id);
      }

      if (reqsData.length > 0 && !selectedRequestId) {
        setSelectedRequestId(reqsData[0].id);
      }
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load research data");
    } finally {
      setLoading(false);
    }
  }, [channelId, selectedRequestId, selectedTopicId]);

  const loadRequestDetails = useCallback(async (reqId: string) => {
    try {
      const [srcs, clms, cnflcts] = await Promise.all([
        listResearchSources(channelId, reqId),
        listResearchClaims(channelId, reqId),
        listResearchConflicts(channelId, reqId),
      ]);
      setSources(srcs);
      setClaims(clms);
      setConflicts(cnflcts);

      try {
        const brief = await getResearchBrief(channelId, reqId);
        setCurrentBrief(brief);
      } catch {
        setCurrentBrief(null);
      }
    } catch {
      // Ignored for partial loads
    }
  }, [channelId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (selectedRequestId) {
      loadRequestDetails(selectedRequestId);
    }
  }, [selectedRequestId, loadRequestDetails]);

  async function handleCreateRequest(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedTopicId) return;

    try {
      setActionLoading(true);
      const newReq = await createResearchRequest(channelId, {
        topic_candidate_id: selectedTopicId,
        research_question: researchQuestion.trim() || undefined,
        scope: researchScope.trim() || undefined,
      });
      setShowCreateModal(false);
      setResearchQuestion("");
      setResearchScope("");
      setSelectedRequestId(newReq.id);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create research request");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleAddSource(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedRequestId || !sourceTitle || !sourcePublisher || !sourceExcerpt) return;

    try {
      setActionLoading(true);
      await addResearchSource(channelId, selectedRequestId, {
        title: sourceTitle.trim(),
        publisher: sourcePublisher.trim(),
        url: sourceUrl.trim() || undefined,
        content_excerpt: sourceExcerpt.trim(),
        primary_source_status: sourcePrimaryStatus,
      });
      setShowSourceModal(false);
      setSourceTitle("");
      setSourcePublisher("");
      setSourceUrl("");
      setSourceExcerpt("");
      setSourcePrimaryStatus("UNKNOWN");
      await loadRequestDetails(selectedRequestId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add source");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleRunResearch(reqId: string) {
    try {
      setActionLoading(true);
      const brief = await runResearchPipeline(channelId, reqId);
      setCurrentBrief(brief);
      setActiveTab("brief");
      await loadData();
      await loadRequestDetails(reqId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to execute research pipeline");
    } finally {
      setActionLoading(false);
    }
  }

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "SUCCEEDED":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
      case "RUNNING":
        return "bg-indigo-500/20 text-indigo-400 border-indigo-500/30 animate-pulse";
      case "PENDING":
        return "bg-amber-500/20 text-amber-400 border-amber-500/30";
      case "FAILED":
        return "bg-rose-500/20 text-rose-400 border-rose-500/30";
      default:
        return "bg-slate-800 text-slate-400 border-slate-700";
    }
  };

  const getOutcomeBadge = (outcome?: string | null) => {
    switch (outcome) {
      case "SUFFICIENT":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
      case "PARTIAL":
        return "bg-amber-500/20 text-amber-400 border-amber-500/30";
      case "INSUFFICIENT":
        return "bg-rose-500/20 text-rose-400 border-rose-500/30";
      default:
        return "bg-slate-800 text-slate-400 border-slate-700";
    }
  };

  const getConfidenceBadge = (band: string) => {
    switch (band) {
      case "VERY_HIGH":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
      case "HIGH":
        return "bg-cyan-500/20 text-cyan-400 border-cyan-500/30";
      case "MEDIUM":
        return "bg-amber-500/20 text-amber-400 border-amber-500/30";
      case "LOW":
        return "bg-rose-500/20 text-rose-400 border-rose-500/30";
      default:
        return "bg-slate-800 text-slate-400 border-slate-700";
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 p-8 flex items-center justify-center">
        <div className="flex items-center space-x-3 text-slate-400">
          <div className="w-5 h-5 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin" />
          <span>Loading Research Engine...</span>
        </div>
      </div>
    );
  }

  if (!channel) return null;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <div className="max-w-7xl mx-auto space-y-8">
        {/* Navigation & Header */}
        <div>
          <div className="flex items-center space-x-2 text-xs text-slate-400 mb-2">
            <Link href="/channels" className="hover:text-cyan-400 transition-colors">
              Channels
            </Link>
            <span>/</span>
            <Link href={`/channels/${channel.id}`} className="hover:text-cyan-400 transition-colors">
              {channel.name}
            </Link>
            <span>/</span>
            <span className="text-cyan-400">Research Engine</span>
          </div>

          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 border-b border-slate-800 pb-6">
            <div>
              <div className="flex items-center space-x-3">
                <h1 className="text-2xl font-bold text-slate-100">
                  🔬 Research Engine
                </h1>
                <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold border bg-cyan-500/20 text-cyan-400 border-cyan-500/30">
                  OMEGA-005
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                Deterministic evidence extraction, contradiction detection, and versioned ResearchBriefs.
              </p>
            </div>

            <div className="flex items-center space-x-3">
              <button
                onClick={() => setShowCreateModal(true)}
                disabled={eligibleTopics.length === 0}
                className="px-3.5 py-1.5 bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white text-xs font-semibold rounded-lg shadow-lg shadow-cyan-600/20 transition-all flex items-center space-x-1.5"
              >
                <span>+ New Research Request</span>
              </button>
            </div>
          </div>
        </div>

        {error && (
          <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs flex justify-between items-center">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-rose-400 hover:text-rose-200">
              ✕
            </button>
          </div>
        )}

        {/* Tab Navigation */}
        <div className="flex border-b border-slate-800 space-x-8">
          <button
            onClick={() => setActiveTab("requests")}
            className={`pb-3 text-xs font-medium transition-colors relative ${
              activeTab === "requests"
                ? "text-cyan-400 border-b-2 border-cyan-400"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            📋 Requests ({requests.length})
          </button>
          <button
            onClick={() => setActiveTab("brief")}
            className={`pb-3 text-xs font-medium transition-colors relative ${
              activeTab === "brief"
                ? "text-cyan-400 border-b-2 border-cyan-400"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            📄 Research Brief {currentBrief ? `(v${currentBrief.version})` : ""}
          </button>
          <button
            onClick={() => setActiveTab("sources")}
            className={`pb-3 text-xs font-medium transition-colors relative ${
              activeTab === "sources"
                ? "text-cyan-400 border-b-2 border-cyan-400"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            📚 Sources ({sources.length})
          </button>
          <button
            onClick={() => setActiveTab("claims")}
            className={`pb-3 text-xs font-medium transition-colors relative ${
              activeTab === "claims"
                ? "text-cyan-400 border-b-2 border-cyan-400"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            🔍 Claims & Evidence ({claims.length})
          </button>
          <button
            onClick={() => setActiveTab("conflicts")}
            className={`pb-3 text-xs font-medium transition-colors relative ${
              activeTab === "conflicts"
                ? "text-cyan-400 border-b-2 border-cyan-400"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            ⚠️ Conflicts ({conflicts.length})
          </button>
        </div>

        {/* Tab 1: Requests List */}
        {activeTab === "requests" && (
          <div className="space-y-4">
            {requests.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl bg-slate-900/30">
                <p className="text-sm text-slate-400">No research requests yet for this Channel.</p>
                <p className="text-xs text-slate-500 mt-1">
                  Select an eligible topic candidate to initiate automated research.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-4">
                {requests.map((r) => (
                  <div
                    key={r.id}
                    className={`p-5 rounded-xl border transition-all ${
                      selectedRequestId === r.id
                        ? "bg-slate-900/80 border-cyan-500/40 ring-1 ring-cyan-500/20"
                        : "bg-slate-900/40 border-slate-800 hover:border-slate-700"
                    }`}
                  >
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                      <div>
                        <div className="flex items-center space-x-2">
                          <span className={`px-2 py-0.5 text-xs font-semibold rounded-md border ${getStatusBadge(r.status)}`}>
                            {r.status}
                          </span>
                          {r.outcome && (
                            <span className={`px-2 py-0.5 text-xs font-semibold rounded-md border ${getOutcomeBadge(r.outcome)}`}>
                              {r.outcome}
                            </span>
                          )}
                          <span className="text-xs font-mono text-slate-400">{r.mode}</span>
                        </div>
                        <p className="text-sm font-semibold text-slate-200 mt-2">
                          Topic Candidate ID: <span className="font-mono text-xs text-cyan-300">{r.topic_candidate_id}</span>
                        </p>
                        {r.research_question && (
                          <p className="text-xs text-slate-300 mt-1 italic">
                            Question: &quot;{r.research_question}&quot;
                          </p>
                        )}
                        <p className="text-xs text-slate-500 mt-1 font-mono">
                          ID: {r.id} • Created: {new Date(r.created_at).toLocaleString()}
                        </p>
                      </div>

                      <div className="flex items-center space-x-2">
                        <button
                          onClick={() => {
                            setSelectedRequestId(r.id);
                            setShowSourceModal(true);
                          }}
                          className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium rounded-lg border border-slate-700 transition-colors"
                        >
                          + Add Source
                        </button>
                        <button
                          onClick={() => handleRunResearch(r.id)}
                          disabled={actionLoading}
                          className="px-3.5 py-1.5 bg-cyan-600/30 hover:bg-cyan-600/50 text-cyan-300 text-xs font-semibold rounded-lg border border-cyan-500/40 transition-all flex items-center space-x-1"
                        >
                          <span>⚡ Run Pipeline</span>
                        </button>
                        <button
                          onClick={() => {
                            setSelectedRequestId(r.id);
                            setActiveTab("brief");
                          }}
                          className="px-3 py-1.5 bg-indigo-600/30 hover:bg-indigo-600/50 text-indigo-300 text-xs font-semibold rounded-lg border border-indigo-500/40 transition-all"
                        >
                          Inspect Brief →
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tab 2: Research Brief Inspector */}
        {activeTab === "brief" && (
          <div className="space-y-6">
            {!currentBrief ? (
              <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl bg-slate-900/30">
                <p className="text-sm text-slate-400">No Research Brief generated yet for this request.</p>
                <p className="text-xs text-slate-500 mt-1">
                  Add sources and click &quot;Run Pipeline&quot; to synthesize an immutable brief.
                </p>
              </div>
            ) : (
              <div className="space-y-6">
                {/* Brief Header */}
                <div className="p-6 rounded-2xl bg-gradient-to-r from-slate-900 via-slate-900/90 to-cyan-950/40 border border-cyan-500/30 shadow-xl">
                  <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div>
                      <div className="flex items-center space-x-2">
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                          Revision v{currentBrief.version}
                        </span>
                        <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold border ${getOutcomeBadge(currentBrief.outcome)}`}>
                          {currentBrief.outcome}
                        </span>
                        {currentBrief.is_current && (
                          <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-xs font-mono">
                            CURRENT
                          </span>
                        )}
                      </div>
                      <h2 className="text-xl font-bold text-slate-100 mt-2">{currentBrief.title}</h2>
                      <p className="text-xs text-slate-300 mt-1 max-w-3xl leading-relaxed">{currentBrief.summary}</p>
                    </div>

                    <div className="text-right">
                      <div className="text-3xl font-black text-cyan-400">
                        {currentBrief.overall_confidence.toFixed(1)}
                        <span className="text-xs font-normal text-slate-400">/100</span>
                      </div>
                      <p className="text-xs text-slate-400 uppercase tracking-wider mt-0.5">Overall Confidence</p>
                    </div>
                  </div>
                </div>

                {/* Verified Claims Section */}
                <div className="space-y-3">
                  <h3 className="text-sm font-bold text-slate-200 flex items-center space-x-2">
                    <span>✅ Verified Claims ({currentBrief.verified_claims.length})</span>
                  </h3>
                  <div className="grid grid-cols-1 gap-3">
                    {currentBrief.verified_claims.map((vc) => (
                      <div key={vc.claim_id} className="p-4 rounded-xl bg-slate-900/50 border border-emerald-500/30 space-y-2">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center space-x-2">
                            <span className="px-2 py-0.5 text-xs font-mono rounded bg-slate-800 text-slate-300">
                              {vc.type}
                            </span>
                            <span className={`px-2 py-0.5 text-xs font-semibold rounded border ${getConfidenceBadge(vc.confidence_band)}`}>
                              {vc.confidence_score.toFixed(1)} • {vc.confidence_band}
                            </span>
                          </div>
                          <span className="text-xs font-mono text-slate-500">ID: {vc.claim_id.slice(0, 8)}</span>
                        </div>
                        <p className="text-sm text-slate-100 font-medium">{vc.text}</p>
                        {vc.citations.length > 0 && (
                          <div className="pt-2 border-t border-slate-800/80 space-y-1">
                            <p className="text-xs text-slate-400 font-semibold uppercase tracking-wider">Citations:</p>
                            {vc.citations.map((cit, idx) => (
                              <div key={idx} className="p-2 rounded bg-slate-950/60 border border-slate-800 text-xs text-slate-300 font-mono">
                                <span className="text-cyan-400 font-semibold">{cit.publisher}:</span> &quot;{cit.excerpt}&quot;
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Uncertain Claims */}
                {currentBrief.uncertain_claims.length > 0 && (
                  <div className="space-y-3">
                    <h3 className="text-sm font-bold text-slate-300">⚠️ Uncertain / Unverified Claims ({currentBrief.uncertain_claims.length})</h3>
                    <div className="grid grid-cols-1 gap-3">
                      {currentBrief.uncertain_claims.map((uc) => (
                        <div key={uc.claim_id} className="p-4 rounded-xl bg-slate-900/40 border border-amber-500/30 space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="px-2 py-0.5 text-xs font-mono rounded bg-slate-800 text-slate-300">{uc.type}</span>
                            <span className={`px-2 py-0.5 text-xs font-semibold rounded border ${getConfidenceBadge(uc.confidence_band)}`}>
                              {uc.confidence_score.toFixed(1)} • {uc.confidence_band}
                            </span>
                          </div>
                          <p className="text-xs text-slate-200">{uc.text}</p>
                          <p className="text-xs text-amber-400 font-mono italic">Reason: {uc.uncertainty_reason}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Tab 3: Sources */}
        {activeTab === "sources" && (
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <h3 className="text-sm font-bold text-slate-200">Normalized Research Sources</h3>
              <button
                onClick={() => setShowSourceModal(true)}
                disabled={!selectedRequestId}
                className="px-3 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold rounded-lg transition-colors"
              >
                + Add Source
              </button>
            </div>

            {sources.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl bg-slate-900/30">
                <p className="text-sm text-slate-400">No sources ingested yet for this request.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3">
                {sources.map((s) => (
                  <div key={s.id} className="p-4 rounded-xl bg-slate-900/40 border border-slate-800 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="font-semibold text-slate-100 text-sm">{s.title}</span>
                        <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400">{s.source_type}</span>
                        <span className="text-xs px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-400 border border-cyan-500/30">
                          {s.primary_source_status}
                        </span>
                      </div>
                      <div className="text-right">
                        <span className="text-xs font-bold text-cyan-400">Quality: {s.quality_score.toFixed(1)}/100</span>
                      </div>
                    </div>
                    <p className="text-xs text-slate-400">Publisher: <span className="text-slate-200">{s.publisher}</span> {s.url && `• ${s.url}`}</p>
                    <div className="p-2.5 rounded bg-slate-950/60 border border-slate-800 text-xs text-slate-300 font-mono line-clamp-3">
                      &quot;{s.content_excerpt}&quot;
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tab 4: Claims & Evidence */}
        {activeTab === "claims" && (
          <div className="space-y-4">
            <h3 className="text-sm font-bold text-slate-200">Extracted Claims & Evidence</h3>
            {claims.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl bg-slate-900/30">
                <p className="text-sm text-slate-400">No claims extracted yet.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3">
                {claims.map((c) => (
                  <div key={c.id} className="p-4 rounded-xl bg-slate-900/40 border border-slate-800 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="px-2 py-0.5 text-xs font-mono rounded bg-slate-800 text-slate-300">{c.claim_type}</span>
                        <span className={`px-2 py-0.5 text-xs font-semibold rounded border ${getConfidenceBadge(c.confidence_band)}`}>
                          {c.confidence_score.toFixed(1)} • {c.confidence_band}
                        </span>
                        {c.is_verified && (
                          <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 text-xs font-bold">
                            VERIFIED
                          </span>
                        )}
                      </div>
                      <span className="text-xs text-slate-400">
                        {c.supporting_sources_count} supporting • {c.contradicting_sources_count} conflicting
                      </span>
                    </div>
                    <p className="text-sm text-slate-100">{c.claim_text}</p>
                    {c.evidence.length > 0 && (
                      <div className="pt-2 border-t border-slate-800 space-y-1">
                        <p className="text-xs text-slate-400 uppercase font-semibold">Evidence Items ({c.evidence.length}):</p>
                        {c.evidence.map((ev: ClaimEvidence) => (
                          <div key={ev.id} className="p-2 rounded bg-slate-950/60 border border-slate-800 text-xs text-slate-300 font-mono">
                            <span className={ev.support_direction === "SUPPORTS" ? "text-emerald-400" : "text-rose-400"}>
                              [{ev.support_direction}]
                            </span>{" "}
                            &quot;{ev.excerpt}&quot;
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tab 5: Conflicts */}
        {activeTab === "conflicts" && (
          <div className="space-y-4">
            <h3 className="text-sm font-bold text-slate-200">Detected Research Contradictions</h3>
            {conflicts.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl bg-slate-900/30">
                <p className="text-sm text-slate-400">No contradictions detected.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3">
                {conflicts.map((conf) => (
                  <div key={conf.id} className="p-4 rounded-xl bg-slate-900/40 border border-rose-500/30 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="px-2 py-0.5 text-xs font-semibold rounded bg-rose-500/20 text-rose-400 border border-rose-500/30">
                          {conf.severity} SEVERITY
                        </span>
                        <span className="text-xs font-mono text-slate-400">{conf.conflict_type}</span>
                      </div>
                      <span className="text-xs font-mono text-slate-500">{conf.status}</span>
                    </div>
                    <p className="text-xs text-slate-200 font-medium">{conf.description}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Modal: Create Research Request */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
            <h3 className="text-lg font-bold text-slate-100">Initiate Research Request</h3>
            <form onSubmit={handleCreateRequest} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Select Topic Candidate</label>
                <select
                  value={selectedTopicId}
                  onChange={(e) => setSelectedTopicId(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200"
                >
                  {eligibleTopics.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.title} ({t.status})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Research Question (Optional)</label>
                <input
                  type="text"
                  value={researchQuestion}
                  onChange={(e) => setResearchQuestion(e.target.value)}
                  placeholder="e.g. What are the core performance trade-offs of AsyncIO?"
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Research Scope</label>
                <textarea
                  value={researchScope}
                  onChange={(e) => setResearchScope(e.target.value)}
                  placeholder="e.g. Python 3.12, database connections, event loop internals"
                  rows={2}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200"
                />
              </div>
              <div className="flex justify-end space-x-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="px-3 py-1.5 bg-slate-800 text-slate-400 hover:text-slate-200 text-xs rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading || !selectedTopicId}
                  className="px-4 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold rounded-lg"
                >
                  Create Request
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Add Source */}
      {showSourceModal && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
            <h3 className="text-lg font-bold text-slate-100">Add Research Source</h3>
            <form onSubmit={handleAddSource} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Title</label>
                <input
                  type="text"
                  required
                  value={sourceTitle}
                  onChange={(e) => setSourceTitle(e.target.value)}
                  placeholder="e.g. Python 3.12 Official Documentation"
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Publisher</label>
                <input
                  type="text"
                  required
                  value={sourcePublisher}
                  onChange={(e) => setSourcePublisher(e.target.value)}
                  placeholder="e.g. Python Software Foundation"
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">URL (Optional)</label>
                <input
                  type="text"
                  value={sourceUrl}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  placeholder="https://docs.python.org/3/library/asyncio.html"
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Primary Source Declaration</label>
                <select
                  value={sourcePrimaryStatus}
                  onChange={(e) => setSourcePrimaryStatus(e.target.value as PrimarySourceStatus)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200"
                >
                  <option value="UNKNOWN">UNKNOWN</option>
                  <option value="CLAIMED">CLAIMED (Manual input)</option>
                  <option value="CONFIRMED">CONFIRMED (Verified primary)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Content Excerpt (Max 5,000 chars)</label>
                <textarea
                  required
                  rows={4}
                  value={sourceExcerpt}
                  onChange={(e) => setSourceExcerpt(e.target.value)}
                  placeholder="Verifiable text fragment or facts..."
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 font-mono"
                />
              </div>
              <div className="flex justify-end space-x-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowSourceModal(false)}
                  className="px-3 py-1.5 bg-slate-800 text-slate-400 hover:text-slate-200 text-xs rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading || !sourceTitle || !sourcePublisher || !sourceExcerpt}
                  className="px-4 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold rounded-lg"
                >
                  Add Source
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
