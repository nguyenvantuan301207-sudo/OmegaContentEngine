"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
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
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";

type TabType = "script" | "intent_hooks" | "outline" | "citations" | "qa";

export default function ContentEnginePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const channelId = resolvedParams.id;
  const { setSelectedChannelId } = useOperatorContext();

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

  const loadRequestDetails = useCallback(
    async (req: ContentGenerationRequest) => {
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
      } catch {
        // Ignored for partial details
      }
    },
    [channelId]
  );

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setSelectedChannelId(channelId);
      const [ch, reqList, topList] = await Promise.all([
        getChannel(channelId).catch(() => null),
        listContentRequests(channelId).catch(() => []),
        listTopics(channelId).catch(() => []),
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
  }, [channelId, loadRequestDetails, setSelectedChannelId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleTopicChange = async (topicId: string) => {
    setSelectedTopicId(topicId);
    setSelectedBriefId("");
    if (!topicId) {
      setBriefs([]);
      return;
    }
    try {
      const bList = await listResearchBriefs(channelId, topicId).catch(() => []);
      setBriefs(bList);
      if (bList.length > 0) {
        setSelectedBriefId(bList[0].id);
      }
    } catch {
      setBriefs([]);
    }
  };

  const handleCreateRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTopicId || !selectedBriefId) return;

    try {
      setActionLoading(true);
      setError(null);
      const req = await createContentRequest(channelId, {
        topic_candidate_id: selectedTopicId,
        research_brief_id: selectedBriefId,
        content_type: contentType,
        target_duration_seconds: targetDuration,
        creative_direction: creativeDir || undefined,
      });

      setShowCreateModal(false);
      setSelectedTopicId("");
      setSelectedBriefId("");
      setCreativeDir("");
      await loadData();
      setSelectedReq(req);
      await loadRequestDetails(req);
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
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Content generation failed");
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
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Script regeneration failed");
    } finally {
      setActionLoading(false);
    }
  };

  const handleSelectHook = async (hookId: string) => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      await selectContentHook(channelId, selectedReq.id, hookId);
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
      setError(null);
      const res = await rerunScriptQA(channelId, selectedReq.id, currentScript.version);
      setQaResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "QA re-run failed");
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

  const getStatusBadgeClass = (status: string, outcome?: string | null) => {
    if (status === "SUCCEEDED") {
      return outcome === "BLOCKED" ? "badge-warning" : "badge-success";
    }
    if (status === "RUNNING") return "badge-active";
    if (status === "FAILED") return "badge-failed";
    return "badge-draft";
  };

  const isArchived = channel?.state === "ARCHIVED";

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "1.5rem" }}>
      {/* Channel Context Bar with Pipeline Tabs */}
      <ChannelContextBar currentTab="content" />

      {/* Header */}
      <div className="page-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.25rem" }}>
            <h1 className="page-title">✍️ Content Engine</h1>
            <span className="badge badge-active">OMEGA-006</span>
          </div>
          <p className="page-subtitle">
            Script generation, hook engineering, section retention beats, claim attribution, and local script QA.
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={() => setShowCreateModal(true)}
            disabled={isArchived}
            title={isArchived ? "Activate this channel before generating new content." : "New Content Request"}
            className="btn btn-primary btn-sm"
            style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}
          >
            <span>+</span> New Content Request
          </button>
          <button
            onClick={loadData}
            className="btn btn-secondary btn-sm"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: "0.75rem 1rem",
            background: "var(--status-danger-bg)",
            border: "1px solid var(--status-danger-border)",
            borderRadius: "var(--radius-sm)",
            fontSize: "0.82rem",
            color: "var(--status-danger)",
            marginBottom: "1.5rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{error}</span>
          <button onClick={() => setError(null)} className="btn btn-secondary btn-sm" style={{ padding: "0.15rem 0.45rem", fontSize: "0.72rem" }}>
            ✕
          </button>
        </div>
      )}

      {loading && !channel && (
        <div className="card" style={{ padding: "3rem", textAlign: "center", color: "var(--text-muted)", fontSize: "0.9rem" }}>
          Loading Content Engine workspace...
        </div>
      )}

      {/* Main Grid: Left Request List, Right Workspace Console */}
      <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: "1.5rem", alignItems: "start" }}>
        {/* Left: Content Generation Requests */}
        <div className="card" style={{ padding: "1.25rem" }}>
          <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)", marginBottom: "1rem" }}>
            Content Requests ({requests.length})
          </h3>

          {requests.length === 0 ? (
            <div
              style={{
                textAlign: "center",
                padding: "2rem 1rem",
                color: "var(--text-muted)",
                fontSize: "0.82rem",
                background: "var(--bg-input)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--border-subtle)",
              }}
            >
              No content requests created yet.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", maxHeight: "700px", overflowY: "auto" }}>
              {requests.map((r) => {
                const isSelected = selectedReq?.id === r.id;
                return (
                  <div
                    key={r.id}
                    onClick={() => {
                      setSelectedReq(r);
                      loadRequestDetails(r);
                    }}
                    style={{
                      padding: "0.75rem 0.85rem",
                      borderRadius: "var(--radius-sm)",
                      background: isSelected ? "var(--bg-card-hover)" : "var(--bg-input)",
                      border: isSelected ? "1px solid var(--accent-primary)" : "1px solid var(--border-subtle)",
                      cursor: "pointer",
                      transition: "all 0.15s ease",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
                      <span className="badge badge-neutral text-mono" style={{ fontSize: "0.68rem" }}>
                        {r.content_type}
                      </span>
                      <span className={`badge ${getStatusBadgeClass(r.status, r.outcome)}`} style={{ fontSize: "0.68rem" }}>
                        {r.outcome ? `${r.status} (${r.outcome})` : r.status}
                      </span>
                    </div>

                    <div style={{ fontSize: "0.82rem", fontWeight: 600, color: isSelected ? "var(--accent-secondary)" : "var(--text-primary)", lineHeight: 1.3 }}>
                      Request {r.id.slice(0, 8)}
                    </div>

                    <div className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.3rem", display: "flex", justifyContent: "space-between" }}>
                      <span>{r.target_duration_seconds}s</span>
                      <span>DNA {r.channel_dna_revision_id.slice(0, 6)}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right: Selected Request Workspace */}
        {selectedReq ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {/* Header Action Bar */}
            <div className="card" style={{ padding: "1.25rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem", marginBottom: "0.5rem" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.35rem" }}>
                    <h2 style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--text-primary)" }}>
                      {currentScript?.title || `Content Request ${selectedReq.id.slice(0, 8)}`}
                    </h2>
                    {currentScript && (
                      <span className="badge badge-active font-mono" style={{ fontSize: "0.72rem" }}>
                        v{currentScript.version}
                      </span>
                    )}
                  </div>
                  <p className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                    Target: {selectedReq.target_duration_seconds}s • Brief: {selectedReq.research_brief_id.slice(0, 8)} • DNA: {selectedReq.channel_dna_revision_id.slice(0, 8)}
                  </p>
                </div>

                <div style={{ display: "flex", gap: "0.5rem" }}>
                  {selectedReq.status === "DRAFT" && (
                    <button
                      onClick={handleGenerate}
                      disabled={actionLoading || isArchived}
                      title={isArchived ? "Activate this channel before generating content." : "Generate Content"}
                      className="btn btn-primary btn-sm"
                    >
                      {actionLoading ? "Generating..." : "⚡ Generate Content"}
                    </button>
                  )}

                  {selectedReq.status === "SUCCEEDED" && (
                    <>
                      <button
                        onClick={handleRegenerate}
                        disabled={actionLoading || isArchived}
                        title={isArchived ? "Activate this channel before regenerating content." : "Regenerate Script"}
                        className="btn btn-secondary btn-sm"
                      >
                        {actionLoading ? "Regenerating..." : "🔄 Regenerate (vN+1)"}
                      </button>
                      {isArchived ? (
                        <button
                          disabled
                          title="Activate this channel before proceeding to production."
                          className="btn btn-primary btn-sm"
                        >
                          Proceed to Production →
                        </button>
                      ) : (
                        <Link
                          href={`/channels/${channelId}/production`}
                          className="btn btn-primary btn-sm"
                        >
                          Proceed to Production →
                        </Link>
                      )}
                    </>
                  )}

                  {selectedReq.status === "RUNNING" && (
                    <button
                      onClick={handleCancel}
                      disabled={actionLoading}
                      className="btn btn-danger btn-sm"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Tabs */}
            <div className="card" style={{ padding: "1.25rem" }}>
              <div className="tab-group" style={{ marginBottom: "1.25rem" }}>
                <button
                  onClick={() => setActiveTab("script")}
                  className={`tab-item ${activeTab === "script" ? "active" : ""}`}
                >
                  📝 Script & Narrative
                </button>
                <button
                  onClick={() => setActiveTab("intent_hooks")}
                  className={`tab-item ${activeTab === "intent_hooks" ? "active" : ""}`}
                >
                  🎯 Intent & Hooks ({hooks.length})
                </button>
                <button
                  onClick={() => setActiveTab("outline")}
                  className={`tab-item ${activeTab === "outline" ? "active" : ""}`}
                >
                  📋 Outline ({outline?.sections?.length || 0})
                </button>
                <button
                  onClick={() => setActiveTab("citations")}
                  className={`tab-item ${activeTab === "citations" ? "active" : ""}`}
                >
                  🔗 Citations & Provenance
                </button>
                <button
                  onClick={() => setActiveTab("qa")}
                  className={`tab-item ${activeTab === "qa" ? "active" : ""}`}
                >
                  🛡️ QA Findings {qaResult ? `(${qaResult.status})` : ""}
                </button>
              </div>

              {/* Tab 1: Script & Narrative */}
              {activeTab === "script" && (
                <div style={{ maxWidth: "800px", margin: "0 auto" }}>
                  {currentScript ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
                      {/* Revision Switcher */}
                      {scriptSummaries.length > 1 && (
                        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.6rem 0.85rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                          <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontWeight: 600 }}>Revisions:</span>
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
                              className={`btn btn-sm ${currentScript.version === s.version ? "btn-primary" : "btn-secondary"}`}
                              style={{ fontSize: "0.72rem", padding: "0.2rem 0.5rem" }}
                            >
                              v{s.version} {s.is_current ? "★ Current" : ""}
                            </button>
                          ))}
                        </div>
                      )}

                      {/* Hook Card */}
                      <div style={{ padding: "1rem 1.25rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
                          <span style={{ fontSize: "0.72rem", textTransform: "uppercase", color: "var(--accent-secondary)", fontWeight: 700, letterSpacing: "0.06em" }}>
                            Opening Hook
                          </span>
                          <span className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                            ~{Math.ceil(currentScript.hook_text.split(" ").length / 2.4)}s
                          </span>
                        </div>
                        <p style={{ fontSize: "0.92rem", fontWeight: 600, color: "var(--text-primary)", lineHeight: 1.6 }}>
                          {currentScript.hook_text}
                        </p>
                      </div>

                      {/* Sections */}
                      <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                        <h4 style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                          Narrative Sections ({currentScript.sections.length})
                        </h4>

                        {currentScript.sections.map((sec) => (
                          <div
                            key={sec.id}
                            style={{
                              padding: "1rem 1.25rem",
                              background: "var(--bg-input)",
                              borderRadius: "var(--radius-sm)",
                              border: "1px solid var(--border-subtle)",
                              display: "flex",
                              flexDirection: "column",
                              gap: "0.75rem",
                            }}
                          >
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "0.5rem" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                                <span className="badge badge-neutral text-mono" style={{ fontSize: "0.7rem" }}>
                                  Section {sec.section_order}
                                </span>
                                <span style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--text-primary)" }}>{sec.heading}</span>
                              </div>
                              <span className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                                ~{sec.estimated_duration_seconds}s
                              </span>
                            </div>

                            {/* Retention Beat */}
                            {sec.retention_beat && (
                              <div style={{ padding: "0.5rem 0.75rem", background: "rgba(99, 102, 241, 0.1)", border: "1px solid rgba(99, 102, 241, 0.25)", borderRadius: "var(--radius-sm)", fontSize: "0.75rem", display: "flex", justifyContent: "space-between" }}>
                                <span style={{ fontWeight: 600, color: "var(--accent-secondary)" }}>
                                  ⏱️ Retention Beat: {sec.retention_beat.beat_type}
                                </span>
                                <span style={{ color: "var(--text-muted)" }}>{sec.retention_beat.purpose}</span>
                              </div>
                            )}

                            {/* Statements */}
                            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                              {sec.statements.map((stmt) => (
                                <div
                                  key={stmt.id}
                                  style={{
                                    padding: "0.6rem 0.85rem",
                                    background: "var(--bg-card)",
                                    borderRadius: "var(--radius-sm)",
                                    border: "1px solid var(--border-subtle)",
                                    fontSize: "0.85rem",
                                    lineHeight: 1.6,
                                  }}
                                >
                                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                                    <span className="badge badge-neutral text-mono" style={{ fontSize: "0.65rem" }}>
                                      {stmt.statement_type}
                                    </span>
                                    {stmt.citations.length > 0 && (
                                      <span className="text-mono" style={{ fontSize: "0.68rem", color: "var(--status-success)" }}>
                                        ✓ {stmt.citations.length} citation(s)
                                      </span>
                                    )}
                                  </div>
                                  <p style={{ color: "var(--text-primary)" }}>{stmt.statement_text}</p>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>

                      {/* Closing & CTA */}
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
                        <div style={{ padding: "1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                          <span style={{ fontSize: "0.72rem", textTransform: "uppercase", color: "var(--text-muted)", fontWeight: 700 }}>
                            Closing Narrative
                          </span>
                          <p style={{ fontSize: "0.85rem", color: "var(--text-primary)", marginTop: "0.35rem", lineHeight: 1.5 }}>
                            {currentScript.closing_text}
                          </p>
                        </div>
                        <div style={{ padding: "1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                          <span style={{ fontSize: "0.72rem", textTransform: "uppercase", color: "var(--status-purple)", fontWeight: 700 }}>
                            Call to Action
                          </span>
                          <p style={{ fontSize: "0.85rem", color: "var(--text-primary)", marginTop: "0.35rem", lineHeight: 1.5 }}>
                            {currentScript.cta_text}
                          </p>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No script generated yet for this request. Click &quot;Generate Content&quot; above to begin.
                    </div>
                  )}
                </div>
              )}

              {/* Tab 2: Intent & Hooks */}
              {activeTab === "intent_hooks" && (
                <div>
                  {intent ? (
                    <div style={{ padding: "1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)", marginBottom: "1.5rem" }}>
                      <h4 style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--accent-secondary)", textTransform: "uppercase", marginBottom: "0.75rem" }}>
                        Editorial Intent & Style
                      </h4>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", fontSize: "0.82rem" }}>
                        <div>
                          <span style={{ color: "var(--text-muted)", display: "block" }}>Primary Goal:</span>
                          <p style={{ color: "var(--text-primary)", fontWeight: 500, marginTop: "0.2rem" }}>{intent.primary_goal}</p>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", display: "block" }}>Viewer Promise:</span>
                          <p style={{ color: "var(--text-primary)", fontWeight: 500, marginTop: "0.2rem" }}>{intent.viewer_promise}</p>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", display: "block" }}>Central Question:</span>
                          <p style={{ color: "var(--text-primary)", fontWeight: 500, marginTop: "0.2rem" }}>{intent.central_question}</p>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", display: "block" }}>Core Takeaway:</span>
                          <p style={{ color: "var(--text-primary)", fontWeight: 500, marginTop: "0.2rem" }}>{intent.core_takeaway}</p>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No intent generated yet.
                    </div>
                  )}

                  {/* Hooks */}
                  <div>
                    <h4 style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", marginBottom: "0.75rem" }}>
                      Hook Variants ({hooks.length})
                    </h4>
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                      {hooks.map((h) => (
                        <div
                          key={h.id}
                          style={{
                            padding: "1rem",
                            background: h.selected ? "var(--bg-card-hover)" : "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: h.selected ? "1px solid var(--accent-primary)" : "1px solid var(--border-subtle)",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                              <span className="badge badge-neutral text-mono" style={{ fontSize: "0.7rem" }}>
                                {h.hook_type}
                              </span>
                              <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--status-success)" }}>
                                Score: {h.score}
                              </span>
                            </div>
                            <button
                              onClick={() => handleSelectHook(h.id)}
                              disabled={h.selected || actionLoading}
                              className={`btn btn-sm ${h.selected ? "btn-primary" : "btn-secondary"}`}
                              style={{ fontSize: "0.72rem" }}
                            >
                              {h.selected ? "✓ Selected" : "Select Hook"}
                            </button>
                          </div>
                          <p style={{ fontSize: "0.88rem", color: "var(--text-primary)", fontWeight: 500 }}>{h.text}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Tab 3: Outline */}
              {activeTab === "outline" && (
                <div>
                  {outline ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                      <div style={{ padding: "1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                        <span style={{ fontSize: "0.72rem", textTransform: "uppercase", color: "var(--text-muted)", fontWeight: 700 }}>
                          Opening Concept
                        </span>
                        <p style={{ fontSize: "0.85rem", color: "var(--text-primary)", marginTop: "0.25rem" }}>{outline.opening_description}</p>
                      </div>

                      {outline.sections.map((sec, idx) => (
                        <div
                          key={sec.section_id}
                          style={{
                            padding: "0.85rem 1rem",
                            background: "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid var(--border-subtle)",
                            fontSize: "0.82rem",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
                            <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>
                              {idx + 1}. {sec.title}
                            </span>
                            <span className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                              ~{sec.estimated_duration_seconds}s
                            </span>
                          </div>
                          <p style={{ color: "var(--text-secondary)", marginBottom: "0.5rem" }}>{sec.objective}</p>
                          <div style={{ display: "flex", justifyContent: "space-between", paddingTop: "0.35rem", borderTop: "1px solid var(--border-subtle)", fontSize: "0.72rem", color: "var(--text-muted)" }}>
                            <span>Transition: {sec.transition}</span>
                            <span style={{ color: "var(--accent-secondary)" }}>{sec.retention_goal}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No outline generated yet.
                    </div>
                  )}
                </div>
              )}

              {/* Tab 4: Citations */}
              {activeTab === "citations" && (
                <div>
                  <h4 style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", marginBottom: "0.75rem" }}>
                    Authoritative Claim Provenance
                  </h4>
                  {currentScript ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                      {currentScript.sections.flatMap((sec) =>
                        sec.statements
                          .filter((st) => st.citations.length > 0)
                          .map((st) => (
                            <div
                              key={st.id}
                              style={{
                                padding: "0.85rem 1rem",
                                background: "var(--bg-input)",
                                borderRadius: "var(--radius-sm)",
                                border: "1px solid var(--border-subtle)",
                              }}
                            >
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                                <span style={{ fontWeight: 600, fontSize: "0.8rem", color: "var(--status-success)" }}>
                                  Section {sec.section_order} Citation
                                </span>
                                <span className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                                  {st.citations.length} Verified Citation(s)
                                </span>
                              </div>
                              <p style={{ fontSize: "0.85rem", color: "var(--text-primary)", fontStyle: "italic", marginBottom: "0.5rem" }}>
                                &ldquo;{st.statement_text}&rdquo;
                              </p>
                              <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                                {st.citations.map((c) => (
                                  <div key={c.id} className="text-mono" style={{ padding: "0.35rem 0.5rem", background: "var(--bg-card)", borderRadius: "var(--radius-sm)", fontSize: "0.68rem", color: "var(--text-muted)", display: "flex", justifyContent: "space-between" }}>
                                    <span>Claim: {c.claim_id.slice(0, 8)} • Evidence: {c.evidence_id.slice(0, 8)}</span>
                                    <span style={{ color: "var(--accent-secondary)" }}>Brief: {c.research_brief_id.slice(0, 8)}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))
                      )}
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No script citations available.
                    </div>
                  )}
                </div>
              )}

              {/* Tab 5: QA Findings */}
              {activeTab === "qa" && (
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                    <h4 style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase" }}>
                      Local Content QA Results
                    </h4>
                    <button
                      onClick={handleRerunQA}
                      disabled={actionLoading}
                      className="btn btn-secondary btn-sm"
                    >
                      Re-run QA Checks
                    </button>
                  </div>

                  {qaResult ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                      <div
                        style={{
                          padding: "0.85rem 1rem",
                          background: qaResult.status === "PASSED" ? "var(--status-success-bg)" : "var(--status-warning-bg)",
                          border: qaResult.status === "PASSED" ? "1px solid var(--status-success-border)" : "1px solid var(--status-warning-border)",
                          borderRadius: "var(--radius-sm)",
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          fontSize: "0.85rem",
                          fontWeight: 700,
                          color: qaResult.status === "PASSED" ? "var(--status-success)" : "var(--status-warning)",
                        }}
                      >
                        <span>Overall QA Status: {qaResult.status}</span>
                        <span className="text-mono" style={{ fontSize: "0.72rem" }}>
                          {qaResult.findings.length} finding(s)
                        </span>
                      </div>

                      {qaResult.findings.map((f, i) => (
                        <div
                          key={i}
                          style={{
                            padding: "0.75rem 1rem",
                            background: "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid var(--border-subtle)",
                            fontSize: "0.8rem",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                            <span className="badge badge-warning" style={{ fontSize: "0.68rem" }}>
                              {f.severity} • {f.rule_code}
                            </span>
                            {f.section_index && (
                              <span className="text-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>
                                Sec {f.section_index}
                              </span>
                            )}
                          </div>
                          <p style={{ color: "var(--text-primary)" }}>{f.message}</p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No QA evaluated yet.
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="card" style={{ padding: "3rem 1.5rem", textAlign: "center", color: "var(--text-muted)" }}>
            <p style={{ fontSize: "0.95rem", marginBottom: "0.5rem" }}>No content request selected.</p>
            <p style={{ fontSize: "0.82rem" }}>Select a request on the left or create a new request above.</p>
          </div>
        )}
      </div>

      {/* Modal: Create Content Request */}
      {showCreateModal && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: "520px" }}>
            <div className="modal-header">
              <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                Create Content Generation Request
              </h3>
              <button onClick={() => setShowCreateModal(false)} className="btn btn-secondary btn-sm" style={{ padding: "0.2rem 0.5rem" }}>
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateRequest}>
              <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                <div className="form-group">
                  <label className="form-label">Select Topic Candidate *</label>
                  <select
                    value={selectedTopicId}
                    onChange={(e) => handleTopicChange(e.target.value)}
                    required
                    className="form-select"
                  >
                    <option value="">-- Choose a Topic --</option>
                    {topics.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.title} ({t.status})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label className="form-label">Select Research Brief *</label>
                  <select
                    value={selectedBriefId}
                    onChange={(e) => setSelectedBriefId(e.target.value)}
                    required
                    className="form-select"
                  >
                    <option value="">-- Choose a Brief --</option>
                    {briefs.map((b) => (
                      <option key={b.id} value={b.id}>
                        v{b.version} — {b.title} ({b.outcome})
                      </option>
                    ))}
                  </select>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
                  <div className="form-group">
                    <label className="form-label">Content Type</label>
                    <select
                      value={contentType}
                      onChange={(e) => setContentType(e.target.value as "YOUTUBE_LONGFORM" | "YOUTUBE_SHORT")}
                      className="form-select"
                    >
                      <option value="YOUTUBE_LONGFORM">YouTube Longform</option>
                      <option value="YOUTUBE_SHORT">YouTube Short</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Target Duration (s)</label>
                    <input
                      type="number"
                      value={targetDuration}
                      onChange={(e) => setTargetDuration(Number(e.target.value))}
                      min={30}
                      max={3600}
                      className="form-input"
                    />
                  </div>
                </div>

                <div className="form-group">
                  <label className="form-label">Creative Direction (Optional)</label>
                  <textarea
                    value={creativeDir}
                    onChange={(e) => setCreativeDir(e.target.value)}
                    rows={3}
                    placeholder="E.g., Emphasize practical benchmarks and real-world system caveats..."
                    className="form-textarea"
                  />
                </div>
              </div>

              <div className="modal-footer" style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="btn btn-secondary btn-sm"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading || !selectedTopicId || !selectedBriefId}
                  className="btn btn-primary btn-sm"
                >
                  {actionLoading ? "Creating..." : "Create Request"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
