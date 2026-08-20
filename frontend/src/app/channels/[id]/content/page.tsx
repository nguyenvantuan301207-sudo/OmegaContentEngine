"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  cancelContentRequest,
  Channel,
  ContentGenerationRequest,
  ContentHook,
  ContentIntent,
  ContentOutline,
  ContentQAResult,
  createContentRequest,
  generateContent,
  getChannel,
  getContentIntent,
  getContentOutline,
  getScriptQAResult,
  getScriptVersion,
  listContentHooks,
  listContentRequests,
  listResearchBriefs,
  listScriptVersions,
  listTopics,
  regenerateScript,
  rerunScriptQA,
  ResearchBriefSummary,
  ScriptVersion,
  ScriptVersionSummary,
  selectContentHook,
  TopicCandidate,
} from "@/lib/api";

type TabType = "script" | "intent_hooks" | "outline" | "citations" | "qa";

export default function ContentEnginePage() {
  const params = useParams();
  const channelId = params.id as string;

  const [channel, setChannel] = useState<Channel | null>(null);
  const [requests, setRequests] = useState<ContentGenerationRequest[]>([]);
  const [selectedReq, setSelectedReq] = useState<ContentGenerationRequest | null>(null);
  const [activeTab, setActiveTab] = useState<TabType>("script");

  // Artifacts for selected request
  const [intent, setIntent] = useState<ContentIntent | null>(null);
  const [hooks, setHooks] = useState<ContentHook[]>([]);
  const [outline, setOutline] = useState<ContentOutline | null>(null);
  const [scriptSummaries, setScriptSummaries] = useState<ScriptVersionSummary[]>([]);
  const [currentScript, setCurrentScript] = useState<ScriptVersion | null>(null);
  const [qaResult, setQaResult] = useState<ContentQAResult | null>(null);

  // Available topics and briefs for modal
  const [topics, setTopics] = useState<TopicCandidate[]>([]);
  const [briefs, setBriefs] = useState<ResearchBriefSummary[]>([]);

  // Loading & Modal States
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  // Create Form State
  const [selectedTopicId, setSelectedTopicId] = useState("");
  const [selectedBriefId, setSelectedBriefId] = useState("");
  const [contentType, setContentType] = useState<"YOUTUBE_LONGFORM" | "YOUTUBE_SHORT">("YOUTUBE_LONGFORM");
  const [targetDuration, setTargetDuration] = useState(480);
  const [creativeDir, setCreativeDir] = useState("");

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const [ch, reqList, topList] = await Promise.all([
        getChannel(channelId),
        listContentRequests(channelId),
        listTopics(channelId),
      ]);
      setChannel(ch);
      setRequests(reqList);
      setTopics(topList.filter((t) => t.status !== "ARCHIVED" && t.status !== "DISCOVERED"));

      if (reqList.length > 0) {
        const first = reqList[0];
        setSelectedReq(first);
        await loadRequestDetails(first);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load content engine data");
    } finally {
      setLoading(false);
    }
  };

  const loadRequestDetails = async (req: ContentGenerationRequest) => {
    try {
      const [intentRes, hooksRes, outlineRes, scriptsRes] = await Promise.allSettled([
        getContentIntent(channelId, req.id),
        listContentHooks(channelId, req.id),
        getContentOutline(channelId, req.id),
        listScriptVersions(channelId, req.id),
      ]);

      setIntent(intentRes.status === "fulfilled" ? intentRes.value : null);
      setHooks(hooksRes.status === "fulfilled" ? hooksRes.value : []);
      setOutline(outlineRes.status === "fulfilled" ? outlineRes.value : null);

      if (scriptsRes.status === "fulfilled") {
        setScriptSummaries(scriptsRes.value);
        if (scriptsRes.value.length > 0) {
          const latestVer = scriptsRes.value[0].version;
          const [sc, qa] = await Promise.allSettled([
            getScriptVersion(channelId, req.id, latestVer),
            getScriptQAResult(channelId, req.id, latestVer),
          ]);
          setCurrentScript(sc.status === "fulfilled" ? sc.value : null);
          setQaResult(qa.status === "fulfilled" ? qa.value : null);
        } else {
          setCurrentScript(null);
          setQaResult(null);
        }
      }
    } catch (err: unknown) {
      console.error("Failed to load request artifacts:", err);
    }
  };

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channelId]);

  // Load briefs when topic is selected in create modal
  const handleTopicChange = async (topicId: string) => {
    setSelectedTopicId(topicId);
    setSelectedBriefId("");
    try {
      // Find a dummy or actual research request
      const topicObj = topics.find((t) => t.id === topicId);
      if (topicObj) {
        // Fetch briefs by checking research requests
        const res = await fetch(`/api/v1/channels/${channelId}/research`);
        if (res.ok) {
          const reqs = await res.json();
          const matchReq = reqs.find((r: { topic_candidate_id: string }) => r.topic_candidate_id === topicId);
          if (matchReq) {
            const bList = await listResearchBriefs(channelId, matchReq.id);
            setBriefs(bList);
            if (bList.length > 0) {
              setSelectedBriefId(bList[0].id);
            }
          }
        }
      }
    } catch (err) {
      console.error("Failed to load briefs for topic:", err);
    }
  };

  const handleCreateRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTopicId || !selectedBriefId) return;

    try {
      setActionLoading(true);
      setError(null);
      const newReq = await createContentRequest(channelId, {
        topic_candidate_id: selectedTopicId,
        research_brief_id: selectedBriefId,
        content_type: contentType,
        target_duration_seconds: Number(targetDuration),
        creative_direction: creativeDir.trim() || null,
      });

      setShowCreateModal(false);
      await loadData();
      setSelectedReq(newReq);
      await loadRequestDetails(newReq);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create content request");
    } finally {
      setActionLoading(false);
    }
  };

  const handleGenerate = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      setError(null);
      await generateContent(channelId, selectedReq.id);
      await loadData();
      await loadRequestDetails(selectedReq);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to generate content");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRegenerate = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      setError(null);
      await regenerateScript(channelId, selectedReq.id);
      await loadData();
      await loadRequestDetails(selectedReq);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to regenerate script");
    } finally {
      setActionLoading(false);
    }
  };

  const handleSelectHook = async (hookId: string) => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      await selectContentHook(channelId, selectedReq.id, hookId, true);
      const updatedHooks = await listContentHooks(channelId, selectedReq.id);
      setHooks(updatedHooks);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to select hook");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRerunQA = async () => {
    if (!selectedReq || !currentScript) return;
    try {
      setActionLoading(true);
      const qa = await rerunScriptQA(channelId, selectedReq.id, currentScript.version);
      setQaResult(qa);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to rerun QA");
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      await cancelContentRequest(channelId, selectedReq.id);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to cancel request");
    } finally {
      setActionLoading(false);
    }
  };

  if (loading && !channel) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 p-8 flex items-center justify-center">
        <div className="text-slate-400 animate-pulse text-sm">Loading Content Engine workspace...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Breadcrumb & Navigation */}
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2 text-xs">
            <Link href={`/channels/${channelId}`} className="text-indigo-400 hover:text-indigo-300">
              ← {channel?.name || "Channel"}
            </Link>
            <span className="text-slate-600">/</span>
            <span className="text-slate-300">Content Engine</span>
          </div>

          <div className="flex items-center space-x-3">
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-3.5 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-xs font-semibold rounded-lg shadow-lg shadow-violet-600/20 transition-all flex items-center space-x-1"
            >
              <span>+ New Content Request</span>
            </button>
            <button
              onClick={loadData}
              className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium rounded-lg border border-slate-700 transition-all"
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-400 text-xs flex justify-between items-center">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-slate-400 hover:text-white">✕</button>
          </div>
        )}

        {/* Main Grid: Left Request List, Right Workspace Console */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          {/* Left: Content Generation Requests */}
          <div className="lg:col-span-1 space-y-4">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Content Requests ({requests.length})
            </h2>

            <div className="space-y-2 max-h-[750px] overflow-y-auto pr-1">
              {requests.length === 0 ? (
                <div className="p-6 bg-slate-900/60 border border-slate-800 rounded-xl text-center text-slate-500 text-xs">
                  No content requests created yet.
                </div>
              ) : (
                requests.map((r) => {
                  const isSelected = selectedReq?.id === r.id;
                  return (
                    <div
                      key={r.id}
                      onClick={() => {
                        setSelectedReq(r);
                        loadRequestDetails(r);
                      }}
                      className={`p-3.5 rounded-xl border cursor-pointer transition-all ${
                        isSelected
                          ? "bg-violet-950/30 border-violet-500/50 shadow-md shadow-violet-950/40"
                          : "bg-slate-900/60 border-slate-800 hover:border-slate-700"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                          {r.content_type}
                        </span>
                        <span
                          className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                            r.status === "SUCCEEDED"
                              ? r.outcome === "BLOCKED"
                                ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                                : "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                              : r.status === "RUNNING"
                              ? "bg-blue-500/10 text-blue-400 border-blue-500/30 animate-pulse"
                              : r.status === "FAILED"
                              ? "bg-rose-500/10 text-rose-400 border-rose-500/30"
                              : "bg-slate-500/10 text-slate-400 border-slate-500/30"
                          }`}
                        >
                          {r.outcome ? `${r.status} (${r.outcome})` : r.status}
                        </span>
                      </div>

                      <div className="text-xs text-slate-300 font-medium line-clamp-1 mb-1">
                        Request {r.id.slice(0, 8)}
                      </div>

                      <div className="flex items-center justify-between text-[10px] text-slate-500 font-mono">
                        <span>{r.target_duration_seconds}s</span>
                        <span>DNA {r.channel_dna_revision_id.slice(0, 6)}</span>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Right: Selected Request Workspace */}
          <div className="lg:col-span-3 space-y-4">
            {selectedReq ? (
              <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6 space-y-6">
                {/* Header Action Bar */}
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-4">
                  <div>
                    <div className="flex items-center space-x-3">
                      <h3 className="text-base font-bold text-slate-100">
                        {currentScript?.title || `Content Request ${selectedReq.id.slice(0, 8)}`}
                      </h3>
                      {currentScript && (
                        <span className="text-xs font-mono px-2 py-0.5 rounded bg-violet-900/40 text-violet-300 border border-violet-700/40">
                          v{currentScript.version} (Current)
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-400 mt-1 font-mono">
                      Target: {selectedReq.target_duration_seconds}s • Pinned Brief: {selectedReq.research_brief_id.slice(0, 8)} • DNA: {selectedReq.channel_dna_revision_id.slice(0, 8)}
                    </p>
                  </div>

                  <div className="flex items-center space-x-2">
                    {selectedReq.status === "DRAFT" && (
                      <button
                        onClick={handleGenerate}
                        disabled={actionLoading}
                        className="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg shadow-lg shadow-emerald-600/20 transition-all"
                      >
                        {actionLoading ? "Generating..." : "⚡ Generate Content"}
                      </button>
                    )}

                    {selectedReq.status === "SUCCEEDED" && (
                      <button
                        onClick={handleRegenerate}
                        disabled={actionLoading}
                        className="px-3.5 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-xs font-semibold rounded-lg shadow-lg shadow-violet-600/20 transition-all"
                      >
                        {actionLoading ? "Regenerating..." : "🔄 Regenerate (vN+1)"}
                      </button>
                    )}

                    {selectedReq.status === "RUNNING" && (
                      <button
                        onClick={handleCancel}
                        disabled={actionLoading}
                        className="px-3.5 py-1.5 bg-rose-800 hover:bg-rose-700 text-white text-xs font-semibold rounded-lg transition-all"
                      >
                        Cancel
                      </button>
                    )}
                  </div>
                </div>

                {/* Tabs */}
                <div className="flex items-center space-x-2 border-b border-slate-800 pb-2">
                  <button
                    onClick={() => setActiveTab("script")}
                    className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                      activeTab === "script"
                        ? "bg-violet-600/30 text-violet-300 border border-violet-500/40"
                        : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    📝 Script & Narrative
                  </button>
                  <button
                    onClick={() => setActiveTab("intent_hooks")}
                    className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                      activeTab === "intent_hooks"
                        ? "bg-violet-600/30 text-violet-300 border border-violet-500/40"
                        : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    🎯 Intent & Hooks ({hooks.length})
                  </button>
                  <button
                    onClick={() => setActiveTab("outline")}
                    className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                      activeTab === "outline"
                        ? "bg-violet-600/30 text-violet-300 border border-violet-500/40"
                        : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    📋 Outline ({outline?.sections?.length || 0})
                  </button>
                  <button
                    onClick={() => setActiveTab("citations")}
                    className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                      activeTab === "citations"
                        ? "bg-violet-600/30 text-violet-300 border border-violet-500/40"
                        : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    🔗 Citations & Provenance
                  </button>
                  <button
                    onClick={() => setActiveTab("qa")}
                    className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all flex items-center space-x-1.5 ${
                      activeTab === "qa"
                        ? "bg-violet-600/30 text-violet-300 border border-violet-500/40"
                        : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    <span>🛡️ QA Findings</span>
                    {qaResult && (
                      <span
                        className={`text-[9px] px-1.5 py-0.2 rounded font-mono ${
                          qaResult.status === "PASSED"
                            ? "bg-emerald-500/20 text-emerald-300"
                            : qaResult.status === "PASSED_WITH_WARNINGS"
                            ? "bg-amber-500/20 text-amber-300"
                            : "bg-rose-500/20 text-rose-300"
                        }`}
                      >
                        {qaResult.status}
                      </span>
                    )}
                  </button>
                </div>

                {/* Tab 1: Script & Narrative */}
                {activeTab === "script" && (
                  <div className="space-y-6">
                    {currentScript ? (
                      <div className="space-y-6">
                        {/* Script Revisions Switcher */}
                        {scriptSummaries.length > 1 && (
                          <div className="flex items-center space-x-2 bg-slate-900/60 p-2.5 rounded-xl border border-slate-800/80">
                            <span className="text-xs text-slate-400 font-semibold pl-1">Revisions:</span>
                            {scriptSummaries.map((s) => (
                              <button
                                key={s.id}
                                onClick={async () => {
                                  if (!selectedReq) return;
                                  const [sc, qa] = await Promise.allSettled([
                                    getScriptVersion(channelId, selectedReq.id, s.version),
                                    getScriptQAResult(channelId, selectedReq.id, s.version),
                                  ]);
                                  setCurrentScript(sc.status === "fulfilled" ? sc.value : null);
                                  setQaResult(qa.status === "fulfilled" ? qa.value : null);
                                }}
                                className={`px-2.5 py-1 text-xs rounded-lg font-mono font-bold transition-all ${
                                  currentScript.version === s.version
                                    ? "bg-violet-600 text-white shadow-md shadow-violet-600/30"
                                    : "bg-slate-800 text-slate-400 hover:text-slate-200"
                                }`}
                              >
                                v{s.version} {s.is_current ? "★ Current" : ""}
                              </button>
                            ))}
                          </div>
                        )}

                        {/* Hook Card */}
                        <div className="p-4 bg-slate-950/70 border border-slate-800 rounded-xl space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-[10px] font-semibold text-violet-400 uppercase tracking-wider">
                              Opening Hook
                            </span>
                            <span className="text-[10px] font-mono text-slate-500">
                              ~{Math.ceil(currentScript.hook_text.split(" ").length / 2.4)}s
                            </span>
                          </div>
                          <p className="text-sm font-medium text-slate-100">{currentScript.hook_text}</p>
                        </div>

                        {/* Sections */}
                        <div className="space-y-4">
                          <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                            Narrative Sections ({currentScript.sections.length})
                          </h4>

                          {currentScript.sections.map((sec) => (
                            <div
                              key={sec.id}
                              className="p-4 bg-slate-950/50 border border-slate-800 rounded-xl space-y-3"
                            >
                              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                                <div className="flex items-center space-x-2">
                                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                                    Section {sec.section_order}
                                  </span>
                                  <span className="text-xs font-bold text-slate-200">{sec.heading}</span>
                                </div>
                                <span className="text-[10px] font-mono text-slate-500">
                                  ~{sec.estimated_duration_seconds}s
                                </span>
                              </div>

                              {/* Retention Beat Tag */}
                              {sec.retention_beat && (
                                <div className="p-2 bg-indigo-950/30 border border-indigo-500/20 rounded-lg flex items-center justify-between text-[10px]">
                                  <span className="font-semibold text-indigo-300">
                                    ⏱️ Retention Beat: {sec.retention_beat.beat_type}
                                  </span>
                                  <span className="text-slate-400">{sec.retention_beat.purpose}</span>
                                </div>
                              )}

                              {/* Statements with classification badges */}
                              <div className="space-y-2 pt-1">
                                {sec.statements.map((stmt) => {
                                  const badgeColor =
                                    stmt.statement_type === "FACTUAL"
                                      ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                                      : stmt.statement_type === "ATTRIBUTED"
                                      ? "bg-cyan-500/10 text-cyan-400 border-cyan-500/30"
                                      : stmt.statement_type === "INTERPRETIVE"
                                      ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                                      : stmt.statement_type === "TRANSITION"
                                      ? "bg-slate-500/10 text-slate-400 border-slate-500/30"
                                      : "bg-violet-500/10 text-violet-400 border-violet-500/30";

                                  return (
                                    <div
                                      key={stmt.id}
                                      className="p-2.5 bg-slate-900/60 border border-slate-800/80 rounded-lg text-xs space-y-1.5"
                                    >
                                      <div className="flex items-center justify-between">
                                        <span className={`text-[9px] font-semibold px-2 py-0.5 rounded-full border ${badgeColor}`}>
                                          {stmt.statement_type}
                                        </span>
                                        {stmt.citations.length > 0 && (
                                          <span className="text-[10px] font-mono text-emerald-400">
                                            ✓ {stmt.citations.length} citation(s)
                                          </span>
                                        )}
                                      </div>
                                      <p className="text-slate-200">{stmt.statement_text}</p>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          ))}
                        </div>

                        {/* Closing & CTA */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          <div className="p-4 bg-slate-950/70 border border-slate-800 rounded-xl space-y-1">
                            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                              Closing Narrative
                            </span>
                            <p className="text-xs text-slate-300">{currentScript.closing_text}</p>
                          </div>
                          <div className="p-4 bg-slate-950/70 border border-slate-800 rounded-xl space-y-1">
                            <span className="text-[10px] font-semibold text-pink-400 uppercase tracking-wider">
                              Call to Action
                            </span>
                            <p className="text-xs text-slate-300">{currentScript.cta_text}</p>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="p-12 text-center text-slate-500 text-xs">
                        No script generated yet for this request. Click &quot;Generate Content&quot; to begin.
                      </div>
                    )}
                  </div>
                )}

                {/* Tab 2: Intent & Hooks */}
                {activeTab === "intent_hooks" && (
                  <div className="space-y-6">
                    {/* Editorial Intent */}
                    {intent ? (
                      <div className="p-4 bg-slate-950/70 border border-slate-800 rounded-xl space-y-3">
                        <h4 className="text-xs font-semibold text-violet-400 uppercase tracking-wider">
                          Editorial Intent & Style
                        </h4>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                          <div>
                            <span className="text-slate-500">Primary Goal:</span>
                            <p className="text-slate-200 mt-0.5">{intent.primary_goal}</p>
                          </div>
                          <div>
                            <span className="text-slate-500">Viewer Promise:</span>
                            <p className="text-slate-200 mt-0.5">{intent.viewer_promise}</p>
                          </div>
                          <div>
                            <span className="text-slate-500">Central Question:</span>
                            <p className="text-slate-200 mt-0.5">{intent.central_question}</p>
                          </div>
                          <div>
                            <span className="text-slate-500">Core Takeaway:</span>
                            <p className="text-slate-200 mt-0.5">{intent.core_takeaway}</p>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="p-6 text-center text-slate-500 text-xs">
                        No intent generated yet.
                      </div>
                    )}

                    {/* Hooks */}
                    <div className="space-y-3">
                      <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                        Hook Variants ({hooks.length})
                      </h4>
                      {hooks.map((h) => (
                        <div
                          key={h.id}
                          className={`p-4 rounded-xl border transition-all ${
                            h.selected
                              ? "bg-violet-950/40 border-violet-500/60 shadow-md shadow-violet-950/30"
                              : "bg-slate-950/50 border-slate-800"
                          }`}
                        >
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center space-x-2">
                              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                                {h.hook_type}
                              </span>
                              <span className="text-[10px] font-mono text-emerald-400">Score: {h.score}</span>
                            </div>
                            <button
                              onClick={() => handleSelectHook(h.id)}
                              disabled={h.selected || actionLoading}
                              className={`px-2.5 py-1 text-[11px] font-semibold rounded-lg transition-all ${
                                h.selected
                                  ? "bg-violet-600 text-white"
                                  : "bg-slate-800 hover:bg-slate-700 text-slate-300"
                              }`}
                            >
                              {h.selected ? "✓ Selected" : "Select Hook"}
                            </button>
                          </div>
                          <p className="text-xs font-medium text-slate-100">{h.text}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Tab 3: Outline */}
                {activeTab === "outline" && (
                  <div className="space-y-4">
                    {outline ? (
                      <div className="space-y-4">
                        <div className="p-4 bg-slate-950/50 border border-slate-800 rounded-xl">
                          <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                            Opening Concept
                          </span>
                          <p className="text-xs text-slate-300 mt-1">{outline.opening_description}</p>
                        </div>

                        {outline.sections.map((sec, idx) => (
                          <div
                            key={sec.section_id}
                            className="p-4 bg-slate-950/50 border border-slate-800 rounded-xl space-y-2 text-xs"
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-bold text-slate-200">
                                {idx + 1}. {sec.title}
                              </span>
                              <span className="text-[10px] font-mono text-slate-500">
                                ~{sec.estimated_duration_seconds}s
                              </span>
                            </div>
                            <p className="text-slate-400">{sec.objective}</p>
                            <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px] text-slate-500">
                              <span>Transition: {sec.transition}</span>
                              <span className="text-violet-400">{sec.retention_goal}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="p-12 text-center text-slate-500 text-xs">
                        No outline generated yet.
                      </div>
                    )}
                  </div>
                )}

                {/* Tab 4: Citations & Provenance */}
                {activeTab === "citations" && (
                  <div className="space-y-4">
                    <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                      Authoritative Claim Provenance
                    </h4>

                    {currentScript ? (
                      <div className="space-y-3">
                        {currentScript.sections.flatMap((sec) =>
                          sec.statements
                            .filter((st) => st.citations.length > 0)
                            .map((st) => (
                              <div
                                key={st.id}
                                className="p-4 bg-slate-950/70 border border-slate-800 rounded-xl space-y-2 text-xs"
                              >
                                <div className="flex items-center justify-between">
                                  <span className="font-semibold text-emerald-400">
                                    Statement (Section {sec.section_order})
                                  </span>
                                  <span className="text-[10px] font-mono text-slate-500">
                                    {st.citations.length} Verified Citation(s)
                                  </span>
                                </div>
                                <p className="text-slate-200 italic">&quot;{st.statement_text}&quot;</p>

                                <div className="pt-2 border-t border-slate-800 space-y-1">
                                  {st.citations.map((c) => (
                                    <div
                                      key={c.id}
                                      className="p-2 bg-slate-900 rounded font-mono text-[10px] text-slate-400 flex justify-between"
                                    >
                                      <span>Claim: {c.claim_id.slice(0, 8)} • Evidence: {c.evidence_id.slice(0, 8)}</span>
                                      <span className="text-indigo-400">Brief: {c.research_brief_id.slice(0, 8)}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ))
                        )}
                      </div>
                    ) : (
                      <div className="p-12 text-center text-slate-500 text-xs">
                        No script citations available.
                      </div>
                    )}
                  </div>
                )}

                {/* Tab 5: QA Findings */}
                {activeTab === "qa" && (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                        Local Content QA Results
                      </h4>
                      <button
                        onClick={handleRerunQA}
                        disabled={actionLoading}
                        className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium rounded-lg border border-slate-700 transition-all"
                      >
                        Re-run QA Checks
                      </button>
                    </div>

                    {qaResult ? (
                      <div className="space-y-3">
                        <div
                          className={`p-4 rounded-xl border flex items-center justify-between ${
                            qaResult.status === "PASSED"
                              ? "bg-emerald-950/20 border-emerald-500/30 text-emerald-300"
                              : qaResult.status === "PASSED_WITH_WARNINGS"
                              ? "bg-amber-950/20 border-amber-500/30 text-amber-300"
                              : "bg-rose-950/20 border-rose-500/30 text-rose-300"
                          }`}
                        >
                          <span className="font-bold text-xs">Overall QA Status: {qaResult.status}</span>
                          <span className="text-[10px] font-mono">
                            {qaResult.findings.length} finding(s)
                          </span>
                        </div>

                        {qaResult.findings.map((f, i) => (
                          <div
                            key={i}
                            className="p-3.5 bg-slate-950/60 border border-slate-800 rounded-xl space-y-1 text-xs"
                          >
                            <div className="flex items-center justify-between">
                              <span
                                className={`text-[9px] font-semibold px-2 py-0.5 rounded-full border ${
                                  f.severity === "BLOCKING"
                                    ? "bg-rose-500/10 text-rose-400 border-rose-500/30"
                                    : f.severity === "WARNING"
                                    ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                                    : "bg-blue-500/10 text-blue-400 border-blue-500/30"
                                }`}
                              >
                                {f.severity} • {f.rule_code}
                              </span>
                              {f.section_index && (
                                <span className="text-[10px] font-mono text-slate-500">
                                  Sec {f.section_index}
                                </span>
                              )}
                            </div>
                            <p className="text-slate-200 mt-1">{f.message}</p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="p-12 text-center text-slate-500 text-xs">
                        No QA evaluated yet.
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="p-16 bg-slate-900/60 border border-slate-800 rounded-xl text-center text-slate-500 text-xs">
                Select a content request from the left or create a new request.
              </div>
            )}
          </div>
        </div>

        {/* Create Request Modal */}
        {showCreateModal && (
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 space-y-6 shadow-2xl">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <h3 className="text-sm font-bold text-slate-100">Create Content Generation Request</h3>
                <button onClick={() => setShowCreateModal(false)} className="text-slate-400 hover:text-white">✕</button>
              </div>

              <form onSubmit={handleCreateRequest} className="space-y-4 text-xs">
                <div>
                  <label className="block text-slate-400 mb-1">Select Topic Candidate</label>
                  <select
                    value={selectedTopicId}
                    onChange={(e) => handleTopicChange(e.target.value)}
                    required
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200"
                  >
                    <option value="">-- Choose a Topic --</option>
                    {topics.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.title} ({t.status})
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Select Research Brief</label>
                  <select
                    value={selectedBriefId}
                    onChange={(e) => setSelectedBriefId(e.target.value)}
                    required
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200"
                  >
                    <option value="">-- Choose a Brief --</option>
                    {briefs.map((b) => (
                      <option key={b.id} value={b.id}>
                        v{b.version} — {b.title} ({b.outcome})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-slate-400 mb-1">Content Type</label>
                    <select
                      value={contentType}
                      onChange={(e) => setContentType(e.target.value as "YOUTUBE_LONGFORM" | "YOUTUBE_SHORT")}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200"
                    >
                      <option value="YOUTUBE_LONGFORM">YouTube Longform</option>
                      <option value="YOUTUBE_SHORT">YouTube Short</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-slate-400 mb-1">Target Duration (s)</label>
                    <input
                      type="number"
                      value={targetDuration}
                      onChange={(e) => setTargetDuration(Number(e.target.value))}
                      min={30}
                      max={3600}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Creative Direction (Optional)</label>
                  <textarea
                    value={creativeDir}
                    onChange={(e) => setCreativeDir(e.target.value)}
                    rows={3}
                    placeholder="E.g., Emphasize practical benchmarks and real-world system caveats..."
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200"
                  />
                </div>

                <div className="flex justify-end space-x-2 pt-2 border-t border-slate-800">
                  <button
                    type="button"
                    onClick={() => setShowCreateModal(false)}
                    className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={actionLoading || !selectedTopicId || !selectedBriefId}
                    className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white font-semibold rounded-lg shadow-lg shadow-violet-600/20 disabled:opacity-50"
                  >
                    {actionLoading ? "Creating..." : "Create Request"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
