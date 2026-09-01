"use client";

import { use, useCallback, useEffect, useState } from "react";
import {
  Channel,
  ContentGenerationRequest,
  createProductionRequest,
  getChannel,
  getMediaArtifactStreamUrl,
  getProductionQAResult,
  getRenderPlan,
  listContentRequests,
  listMediaArtifacts,
  listNarrationSegments,
  listProductionAssets,
  listProductionRequests,
  listProductionScenes,
  listRenderJobs,
  listScriptVersions,
  listSubtitleCues,
  MediaArtifact,
  NarrationSegment,
  prepareProduction,
  ProductionAsset,
  ProductionQAResult,
  ProductionRenderJob,
  ProductionRequest,
  ProductionScene,
  RenderPlan,
  renderProduction,
  rerenderProduction,
  ScriptVersionSummary,
  SubtitleCue,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import { PublishPreparationModal } from "@/components/PublishPreparationModal";

type TabType = "scenes" | "assets" | "narration" | "subtitles" | "plan" | "artifacts" | "qa";

export default function ProductionEnginePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const channelId = resolvedParams.id;
  const { setSelectedChannelId } = useOperatorContext();

  const [channel, setChannel] = useState<Channel | null>(null);
  const [requests, setRequests] = useState<ProductionRequest[]>([]);
  const [selectedReq, setSelectedReq] = useState<ProductionRequest | null>(null);
  const [activeTab, setActiveTab] = useState<TabType>("scenes");

  // Production artifacts
  const [scenes, setScenes] = useState<ProductionScene[]>([]);
  const [assets, setAssets] = useState<ProductionAsset[]>([]);
  const [narration, setNarration] = useState<NarrationSegment[]>([]);
  const [subtitles, setSubtitles] = useState<SubtitleCue[]>([]);
  const [renderPlan, setRenderPlan] = useState<RenderPlan | null>(null);
  const [renderJobs, setRenderJobs] = useState<ProductionRenderJob[]>([]);
  const [artifacts, setArtifacts] = useState<MediaArtifact[]>([]);
  const [qaResult, setQaResult] = useState<ProductionQAResult | null>(null);
  const [selectedArtifactVersion, setSelectedArtifactVersion] = useState<number | null>(null);
  const [showPublishModal, setShowPublishModal] = useState(false);

  // New Request Form State
  const [isCreating, setIsCreating] = useState(false);
  const [contentRequests, setContentRequests] = useState<ContentGenerationRequest[]>([]);
  const [scriptsByReq, setScriptsByReq] = useState<Record<string, ScriptVersionSummary[]>>({});
  const [selectedContentReqId, setSelectedContentReqId] = useState<string>("");
  const [selectedScriptId, setSelectedScriptId] = useState<string>("");

  // Loading & Error States
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadArtifacts = useCallback(
    async (req: ProductionRequest) => {
      try {
        const [scList, astList, nList, subList, artList, jobList, plan, qa] = await Promise.allSettled([
          listProductionScenes(channelId, req.id),
          listProductionAssets(channelId, req.id),
          listNarrationSegments(channelId, req.id),
          listSubtitleCues(channelId, req.id),
          listMediaArtifacts(channelId, req.id),
          listRenderJobs(channelId, req.id),
          getRenderPlan(channelId, req.id),
          getProductionQAResult(channelId, req.id),
        ]);

        setScenes(scList.status === "fulfilled" ? scList.value : []);
        setAssets(astList.status === "fulfilled" ? astList.value : []);
        setNarration(nList.status === "fulfilled" ? nList.value : []);
        setSubtitles(subList.status === "fulfilled" ? subList.value : []);
        setRenderJobs(jobList.status === "fulfilled" ? jobList.value : []);
        setRenderPlan(plan.status === "fulfilled" ? plan.value : null);
        setQaResult(qa.status === "fulfilled" ? qa.value : null);

        if (artList.status === "fulfilled" && artList.value.length > 0) {
          setArtifacts(artList.value);
          const current = artList.value.find((a) => a.is_current) || artList.value[0];
          setSelectedArtifactVersion(current.version);
        } else {
          setArtifacts([]);
          setSelectedArtifactVersion(null);
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
      const [ch, reqList, cReqList] = await Promise.all([
        getChannel(channelId).catch(() => null),
        listProductionRequests(channelId).catch(() => []),
        listContentRequests(channelId).catch(() => []),
      ]);
      setChannel(ch);
      setRequests(reqList);
      setContentRequests(cReqList);
      if (reqList.length > 0) {
        setSelectedReq(reqList[0]);
        await loadArtifacts(reqList[0]);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load production data");
    } finally {
      setLoading(false);
    }
  }, [channelId, loadArtifacts, setSelectedChannelId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleContentReqChange = async (reqId: string) => {
    setSelectedContentReqId(reqId);
    setSelectedScriptId("");
    if (!reqId) return;

    if (!scriptsByReq[reqId]) {
      try {
        const sList = await listScriptVersions(channelId, reqId);
        setScriptsByReq((prev) => ({ ...prev, [reqId]: sList }));
        if (sList.length > 0) {
          setSelectedScriptId(sList[0].id);
        }
      } catch {
        // Handle gracefully
      }
    } else if (scriptsByReq[reqId].length > 0) {
      setSelectedScriptId(scriptsByReq[reqId][0].id);
    }
  };

  const handleCreateRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedContentReqId || !selectedScriptId) return;

    try {
      setActionLoading(true);
      setError(null);
      const newReq = await createProductionRequest(channelId, {
        script_version_id: selectedScriptId,
      });
      setIsCreating(false);
      await loadData();
      setSelectedReq(newReq);
      await loadArtifacts(newReq);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create production request");
    } finally {
      setActionLoading(false);
    }
  };

  const handlePrepare = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      setError(null);
      await prepareProduction(channelId, selectedReq.id);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Preparation failed");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRender = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      setError(null);
      await renderProduction(channelId, selectedReq.id, `render-${Date.now()}`);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Render submission failed");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRerender = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      setError(null);
      await rerenderProduction(channelId, selectedReq.id, `rerender-${Date.now()}`, "Operator requested rerender");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Rerender failed");
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

  const selectedArtifact = artifacts.find((a) => a.version === selectedArtifactVersion) || artifacts[0];

  const isArchived = channel?.state === "ARCHIVED";

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "1.5rem" }}>
      {/* Channel Context Bar with Pipeline Tabs */}
      <ChannelContextBar currentTab="production" />

      {/* Header */}
      <div className="page-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.25rem" }}>
            <h1 className="page-title">🎬 Production Workspace</h1>
            <span className="badge badge-active">OMEGA-007</span>
          </div>
          <p className="page-subtitle">
            Render plan compilation, audio narration synthesis, subtitle cue alignment, ffmpeg timeline rendering, and media delivery.
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={() => setIsCreating(true)}
            disabled={isArchived}
            title={isArchived ? "Activate this channel before creating production requests." : "New Production Request"}
            className="btn btn-primary btn-sm"
            style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}
          >
            <span>+</span> New Production Request
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
          Loading Production Engine workspace...
        </div>
      )}

      {/* Main Grid: Left Requests, Right Workspace Console */}
      <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: "1.5rem", alignItems: "start" }}>
        {/* Left Sidebar: Requests List */}
        <div className="card" style={{ padding: "1.25rem" }}>
          <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)", marginBottom: "1rem" }}>
            Production Requests ({requests.length})
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
              No production requests created yet.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", maxHeight: "700px", overflowY: "auto" }}>
              {requests.map((req) => {
                const isSelected = selectedReq?.id === req.id;
                return (
                  <div
                    key={req.id}
                    onClick={() => {
                      setSelectedReq(req);
                      loadArtifacts(req);
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
                      <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                        {req.id.slice(0, 8)}...
                      </span>
                      <span className={`badge ${getStatusBadgeClass(req.status, req.outcome)}`} style={{ fontSize: "0.68rem" }}>
                        {req.outcome === "BLOCKED" ? "BLOCKED" : req.status}
                      </span>
                    </div>

                    <div style={{ fontSize: "0.82rem", fontWeight: 600, color: isSelected ? "var(--accent-secondary)" : "var(--text-primary)" }}>
                      {req.target_width}x{req.target_height} @ {req.fps}fps
                    </div>

                    <div className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.25rem", display: "flex", justifyContent: "space-between" }}>
                      <span>{req.video_codec}</span>
                      <span>{new Date(req.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right Main Content */}
        {selectedReq ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {/* Action Header Card */}
            <div className="card" style={{ padding: "1.25rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem", marginBottom: "0.5rem" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.35rem" }}>
                    <h2 style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--text-primary)" }}>
                      Request {selectedReq.id.slice(0, 8)}
                    </h2>
                    <span className="badge badge-neutral text-mono" style={{ fontSize: "0.7rem" }}>
                      {selectedReq.mode}
                    </span>
                  </div>
                  <p className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                    Script: {selectedReq.script_version_id} • DNA: {selectedReq.channel_dna_revision_id.slice(0, 8)}
                  </p>
                </div>

                <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                  <button
                    onClick={handlePrepare}
                    disabled={actionLoading || selectedReq.status === "RUNNING" || isArchived}
                    title={isArchived ? "Activate this channel before preparing production plans." : "Prepare Production Plan"}
                    className="btn btn-secondary btn-sm"
                  >
                    {actionLoading ? "Processing..." : "1. Prepare Plan"}
                  </button>

                  <button
                    onClick={handleRender}
                    disabled={actionLoading || selectedReq.status === "RUNNING" || isArchived}
                    title={isArchived ? "Activate this channel before rendering media." : "Render Video"}
                    className="btn btn-primary btn-sm"
                  >
                    {actionLoading ? "Rendering..." : "2. Render (v1)"}
                  </button>

                  <button
                    onClick={handleRerender}
                    disabled={actionLoading || selectedReq.status === "RUNNING" || artifacts.length === 0 || isArchived}
                    title={isArchived ? "Activate this channel before rerendering media." : "Rerender Video Revision"}
                    className="btn btn-secondary btn-sm"
                    style={{ color: "var(--status-purple)" }}
                  >
                    3. Rerender (vN+1)
                  </button>

                  {artifacts.length > 0 && (
                    <button
                      onClick={() => setShowPublishModal(true)}
                      disabled={isArchived}
                      title={isArchived ? "Activate this channel before preparing publications." : "Prepare Video Publication"}
                      className="btn btn-success btn-sm"
                      style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}
                    >
                      <span>🚀</span> Prepare Publication
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Tabs */}
            <div className="card" style={{ padding: "1.25rem" }}>
              <div className="tab-group" style={{ marginBottom: "1.25rem" }}>
                <button
                  onClick={() => setActiveTab("scenes")}
                  className={`tab-item ${activeTab === "scenes" ? "active" : ""}`}
                >
                  🎬 Scenes ({scenes.length})
                </button>
                <button
                  onClick={() => setActiveTab("assets")}
                  className={`tab-item ${activeTab === "assets" ? "active" : ""}`}
                >
                  🎨 Assets ({assets.length})
                </button>
                <button
                  onClick={() => setActiveTab("narration")}
                  className={`tab-item ${activeTab === "narration" ? "active" : ""}`}
                >
                  🎙️ Narration ({narration.length})
                </button>
                <button
                  onClick={() => setActiveTab("subtitles")}
                  className={`tab-item ${activeTab === "subtitles" ? "active" : ""}`}
                >
                  💬 Subtitles ({subtitles.length})
                </button>
                <button
                  onClick={() => setActiveTab("plan")}
                  className={`tab-item ${activeTab === "plan" ? "active" : ""}`}
                >
                  ⚙️ Render Plan
                </button>
                <button
                  onClick={() => setActiveTab("artifacts")}
                  className={`tab-item ${activeTab === "artifacts" ? "active" : ""}`}
                >
                  📹 Media ({artifacts.length})
                </button>
                <button
                  onClick={() => setActiveTab("qa")}
                  className={`tab-item ${activeTab === "qa" ? "active" : ""}`}
                >
                  🛡️ QA {qaResult ? `(${qaResult.status})` : ""}
                </button>
              </div>

              {/* 1. SCENES TAB */}
              {activeTab === "scenes" && (
                <div>
                  {scenes.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No scenes generated yet. Click <strong>1. Prepare Plan</strong> above.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                      {scenes.map((scene) => (
                        <div
                          key={scene.id}
                          style={{
                            padding: "1rem 1.25rem",
                            background: "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid var(--border-subtle)",
                            display: "flex",
                            flexDirection: "column",
                            gap: "0.5rem",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <span style={{ fontWeight: 700, fontSize: "0.9rem", color: "var(--accent-secondary)" }}>
                              Scene {scene.scene_order} • {scene.scene_type}
                            </span>
                            <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                              {scene.estimated_duration_ms} ms
                            </span>
                          </div>
                          <p style={{ fontSize: "0.88rem", color: "var(--text-primary)", lineHeight: 1.5 }}>
                            {scene.narration_text}
                          </p>
                          {scene.visual_intent && (
                            <div style={{ padding: "0.5rem 0.75rem", background: "var(--bg-card)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)", fontSize: "0.78rem", color: "var(--text-muted)" }}>
                              🎨 <em>Visual Intent: {scene.visual_intent}</em>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* 2. ASSETS TAB */}
              {activeTab === "assets" && (
                <div>
                  {assets.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No assets resolved yet.
                    </div>
                  ) : (
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "0.75rem" }}>
                      {assets.map((ast) => (
                        <div
                          key={ast.id}
                          style={{
                            padding: "0.85rem 1rem",
                            background: "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid var(--border-subtle)",
                            display: "flex",
                            flexDirection: "column",
                            gap: "0.35rem",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <span style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--text-primary)", textTransform: "uppercase" }}>
                              {ast.asset_type} ({ast.provider_type})
                            </span>
                            <span className="badge badge-success" style={{ fontSize: "0.68rem" }}>
                              {ast.license_status}
                            </span>
                          </div>
                          <p className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", wordBreak: "break-all" }}>
                            URI: {ast.storage_uri}
                          </p>
                          <p className="text-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)", wordBreak: "break-all" }}>
                            SHA256: {ast.content_hash}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* 3. NARRATION TAB */}
              {activeTab === "narration" && (
                <div>
                  {narration.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No narration segments generated.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
                      {narration.map((seg, idx) => (
                        <div
                          key={seg.id}
                          style={{
                            padding: "0.85rem 1rem",
                            background: "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid var(--border-subtle)",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            gap: "1rem",
                          }}
                        >
                          <div>
                            <span style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--accent-secondary)", display: "block", marginBottom: "0.2rem" }}>
                              Segment #{idx + 1}
                            </span>
                            <p style={{ fontSize: "0.85rem", color: "var(--text-primary)" }}>{seg.text}</p>
                          </div>
                          <div className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)", textAlign: "right", whiteSpace: "nowrap" }}>
                            <div>{seg.start_ms}ms → {seg.end_ms}ms</div>
                            <div style={{ color: "var(--accent-secondary)" }}>Δ {seg.duration_ms}ms</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* 4. SUBTITLES TAB */}
              {activeTab === "subtitles" && (
                <div>
                  {subtitles.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No subtitle cues available.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                      {subtitles.map((cue) => (
                        <div
                          key={cue.id}
                          style={{
                            padding: "0.75rem 1rem",
                            background: "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid var(--border-subtle)",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                          }}
                        >
                          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                            <span className="text-mono" style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                              #{cue.cue_order}
                            </span>
                            <span style={{ fontSize: "0.85rem", color: "var(--text-primary)" }}>{cue.text}</span>
                          </div>
                          <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                            {cue.start_ms}ms → {cue.end_ms}ms
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* 5. RENDER PLAN TAB */}
              {activeTab === "plan" && (
                <div>
                  {renderPlan ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                          gap: "0.75rem",
                        }}
                      >
                        <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", display: "block" }}>Dimensions</span>
                          <span style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>{renderPlan.width}x{renderPlan.height}</span>
                        </div>
                        <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", display: "block" }}>FPS / Codec</span>
                          <span style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>{renderPlan.fps} fps ({renderPlan.video_codec})</span>
                        </div>
                        <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", display: "block" }}>Total Duration</span>
                          <span style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--status-success)" }}>{(renderPlan.total_duration_ms / 1000).toFixed(1)}s</span>
                        </div>
                        <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", display: "block" }}>Plan Version</span>
                          <span className="text-mono" style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--accent-secondary)" }}>v{renderPlan.version}</span>
                        </div>
                      </div>

                      {/* Render Jobs History */}
                      {renderJobs.length > 0 && (
                        <div>
                          <h4 style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", marginBottom: "0.5rem" }}>
                            Execution Jobs ({renderJobs.length})
                          </h4>
                          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                            {renderJobs.map((j) => (
                              <div
                                key={j.id}
                                style={{
                                  padding: "0.75rem 1rem",
                                  background: "var(--bg-input)",
                                  borderRadius: "var(--radius-sm)",
                                  border: "1px solid var(--border-subtle)",
                                  display: "flex",
                                  justifyContent: "space-between",
                                  alignItems: "center",
                                  fontSize: "0.8rem",
                                }}
                              >
                                <div>
                                  <span className="text-mono" style={{ color: "var(--accent-secondary)", fontWeight: 600 }}>
                                    {j.id.slice(0, 8)}...
                                  </span>
                                  <div className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
                                    Key: {j.idempotency_key}
                                  </div>
                                </div>
                                <div style={{ textAlign: "right" }}>
                                  <span className="badge badge-active" style={{ fontSize: "0.7rem" }}>{j.state}</span>
                                  <div className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
                                    Attempt {j.attempt}/{j.max_attempts}
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No RenderPlan manifest available.
                    </div>
                  )}
                </div>
              )}

              {/* 6. MEDIA ARTIFACTS TAB */}
              {activeTab === "artifacts" && (
                <div>
                  {artifacts.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No rendered media artifacts found. Click <strong>2. Render</strong> to produce an MP4 video.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                      {/* Version Switcher */}
                      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                        <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontWeight: 600 }}>Revisions:</span>
                        {artifacts.map((art) => (
                          <button
                            key={art.id}
                            onClick={() => setSelectedArtifactVersion(art.version)}
                            className={`btn btn-sm ${selectedArtifactVersion === art.version ? "btn-primary" : "btn-secondary"}`}
                            style={{ fontSize: "0.72rem", padding: "0.2rem 0.5rem" }}
                          >
                            v{art.version} {art.is_current ? "★ Current" : ""}
                          </button>
                        ))}
                      </div>

                      {selectedArtifact && (
                        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                          {/* Video Player */}
                          <div style={{ maxWidth: "720px", margin: "0 auto", width: "100%", background: "#000", borderRadius: "var(--radius-sm)", overflow: "hidden", border: "1px solid var(--border-subtle)" }}>
                            <video
                              controls
                              style={{ width: "100%", maxHeight: "420px", display: "block" }}
                              src={getMediaArtifactStreamUrl(channelId, selectedReq.id, selectedArtifact.id)}
                            >
                              Your browser does not support HTML5 video streaming.
                            </video>
                          </div>

                          {/* Artifact Info Card */}
                          <div
                            style={{
                              padding: "1rem",
                              background: "var(--bg-input)",
                              borderRadius: "var(--radius-sm)",
                              border: "1px solid var(--border-subtle)",
                              display: "grid",
                              gridTemplateColumns: "1fr 1fr",
                              gap: "0.75rem",
                              fontSize: "0.8rem",
                            }}
                          >
                            <div style={{ display: "flex", justifyContent: "space-between" }}>
                              <span style={{ color: "var(--text-muted)" }}>Dimensions:</span>
                              <span className="text-mono" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                                {selectedArtifact.width}x{selectedArtifact.height}
                              </span>
                            </div>
                            <div style={{ display: "flex", justifyContent: "space-between" }}>
                              <span style={{ color: "var(--text-muted)" }}>Duration:</span>
                              <span className="text-mono" style={{ fontWeight: 600, color: "var(--status-success)" }}>
                                {selectedArtifact.duration_ms ? (selectedArtifact.duration_ms / 1000).toFixed(2) : "0"} s
                              </span>
                            </div>
                            <div style={{ display: "flex", justifyContent: "space-between" }}>
                              <span style={{ color: "var(--text-muted)" }}>File Size:</span>
                              <span className="text-mono" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                                {(selectedArtifact.file_size_bytes / 1024).toFixed(1)} KB
                              </span>
                            </div>
                            <div style={{ display: "flex", justifyContent: "space-between" }}>
                              <span style={{ color: "var(--text-muted)" }}>SHA-256:</span>
                              <span className="text-mono" style={{ color: "var(--accent-secondary)", maxWidth: "160px", overflow: "hidden", textOverflow: "ellipsis" }}>
                                {selectedArtifact.content_hash}
                              </span>
                            </div>
                          </div>

                          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "0.25rem" }}>
                            <button
                              onClick={() => setShowPublishModal(true)}
                              disabled={isArchived}
                              title={isArchived ? "Activate this channel before preparing publications." : "Prepare Publication for this Video"}
                              className="btn btn-success btn-sm"
                              style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}
                            >
                              <span>🚀</span> Prepare Publication (v{selectedArtifact.version})
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* 7. QA TAB */}
              {activeTab === "qa" && (
                <div>
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

                      {qaResult.findings.length === 0 ? (
                        <div style={{ padding: "1rem", background: "var(--status-success-bg)", border: "1px solid var(--status-success-border)", borderRadius: "var(--radius-sm)", color: "var(--status-success)", fontSize: "0.85rem" }}>
                          ✅ All production QA checks passed successfully with zero blocking findings.
                        </div>
                      ) : (
                        qaResult.findings.map((f, i) => (
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
                            </div>
                            <p style={{ color: "var(--text-primary)" }}>{f.message}</p>
                          </div>
                        ))
                      )}
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No QA results evaluated yet.
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="card" style={{ padding: "3rem 1.5rem", textAlign: "center", color: "var(--text-muted)" }}>
            <p style={{ fontSize: "0.95rem", marginBottom: "0.5rem" }}>No production request selected.</p>
            <p style={{ fontSize: "0.82rem" }}>Select a request on the left or create a new request above.</p>
          </div>
        )}
      </div>

      {/* Modal: Create Production Request */}
      {isCreating && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: "520px" }}>
            <div className="modal-header">
              <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                Create Production Request
              </h3>
              <button onClick={() => setIsCreating(false)} className="btn btn-secondary btn-sm" style={{ padding: "0.2rem 0.5rem" }}>
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateRequest}>
              <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                <div className="form-group">
                  <label className="form-label">1. Select Content Generation Request *</label>
                  <select
                    value={selectedContentReqId}
                    onChange={(e) => handleContentReqChange(e.target.value)}
                    className="form-select"
                    required
                  >
                    <option value="">-- Choose Content Request --</option>
                    {contentRequests.map((cr) => (
                      <option key={cr.id} value={cr.id}>
                        {cr.id.slice(0, 8)}... ({cr.status})
                      </option>
                    ))}
                  </select>
                </div>

                {selectedContentReqId && (
                  <div className="form-group">
                    <label className="form-label">2. Select Pinned Script Version *</label>
                    <select
                      value={selectedScriptId}
                      onChange={(e) => setSelectedScriptId(e.target.value)}
                      className="form-select"
                      required
                    >
                      <option value="">-- Choose Script --</option>
                      {(scriptsByReq[selectedContentReqId] || []).map((s) => (
                        <option key={s.id} value={s.id}>
                          v{s.version}: {s.title} ({s.qa_status})
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>

              <div className="modal-footer" style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
                <button
                  type="button"
                  onClick={() => setIsCreating(false)}
                  className="btn btn-secondary btn-sm"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading || !selectedScriptId}
                  className="btn btn-primary btn-sm"
                >
                  {actionLoading ? "Creating..." : "Create Request"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Publish Preparation Modal */}
      {showPublishModal && selectedArtifact && channel && selectedReq && (
        <PublishPreparationModal
          channel={channel}
          productionRequest={selectedReq}
          artifact={selectedArtifact}
          isOpen={showPublishModal}
          onClose={() => setShowPublishModal(false)}
        />
      )}
    </div>
  );
}
