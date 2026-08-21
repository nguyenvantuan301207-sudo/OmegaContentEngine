"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
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

type TabType = "scenes" | "assets" | "narration" | "subtitles" | "plan" | "artifacts" | "qa";

export default function ProductionEnginePage() {
  const params = useParams();
  const channelId = params.id as string;

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

  // Initial Load
  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const [ch, reqList, cReqList] = await Promise.all([
          getChannel(channelId),
          listProductionRequests(channelId),
          listContentRequests(channelId),
        ]);
        setChannel(ch);
        setRequests(reqList);
        setContentRequests(cReqList);
        if (reqList.length > 0) {
          setSelectedReq(reqList[0]);
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load production data");
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [channelId]);

  // Load Request Artifacts
  useEffect(() => {
    if (!selectedReq) return;

    async function loadArtifacts() {
      try {
        const [scList, astList, nList, subList, artList, jobList] = await Promise.all([
          listProductionScenes(channelId, selectedReq!.id).catch(() => []),
          listProductionAssets(channelId, selectedReq!.id).catch(() => []),
          listNarrationSegments(channelId, selectedReq!.id).catch(() => []),
          listSubtitleCues(channelId, selectedReq!.id).catch(() => []),
          listMediaArtifacts(channelId, selectedReq!.id).catch(() => []),
          listRenderJobs(channelId, selectedReq!.id).catch(() => []),
        ]);
        setScenes(scList);
        setAssets(astList);
        setNarration(nList);
        setSubtitles(subList);
        setArtifacts(artList);
        setRenderJobs(jobList);

        if (artList.length > 0) {
          const current = artList.find((a) => a.is_current) || artList[0];
          setSelectedArtifactVersion(current.version);
        } else {
          setSelectedArtifactVersion(null);
        }

        // Load plan & QA
        getRenderPlan(channelId, selectedReq!.id).then(setRenderPlan).catch(() => setRenderPlan(null));
        getProductionQAResult(channelId, selectedReq!.id).then(setQaResult).catch(() => setQaResult(null));
      } catch (err: unknown) {
        console.error("Failed to load artifacts", err);
      }
    }
    loadArtifacts();
  }, [channelId, selectedReq]);

  // Handler: Select Content Request to load scripts
  const handleContentReqChange = async (contentReqId: string) => {
    setSelectedContentReqId(contentReqId);
    if (!contentReqId) return;
    try {
      const scripts = await listScriptVersions(channelId, contentReqId);
      setScriptsByReq((prev) => ({ ...prev, [contentReqId]: scripts }));
      if (scripts.length > 0) {
        setSelectedScriptId(scripts[0].id);
      }
    } catch (err: unknown) {
      console.error(err);
    }
  };

  // Handler: Create Production Request
  const handleCreateRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedScriptId) return;
    try {
      setActionLoading(true);
      const newReq = await createProductionRequest(channelId, {
        script_version_id: selectedScriptId,
        target_width: 1920,
        target_height: 1080,
        fps: 30,
        video_codec: "h264",
        audio_codec: "aac",
        container_format: "mp4",
      });
      setRequests((prev) => [newReq, ...prev]);
      setSelectedReq(newReq);
      setIsCreating(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create production request");
    } finally {
      setActionLoading(false);
    }
  };

  // Handler: Prepare Production
  const handlePrepare = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      const updated = await prepareProduction(channelId, selectedReq.id);
      setSelectedReq(updated);
      setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to prepare production");
    } finally {
      setActionLoading(false);
    }
  };

  // Handler: Render v1
  const handleRender = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      const idempotencyKey = `render_${selectedReq.id}_${Date.now()}`;
      await renderProduction(channelId, selectedReq.id, idempotencyKey);
      // Reload request and artifacts
      const [updatedList, artList, jobList] = await Promise.all([
        listProductionRequests(channelId),
        listMediaArtifacts(channelId, selectedReq.id),
        listRenderJobs(channelId, selectedReq.id),
      ]);
      setRequests(updatedList);
      const updated = updatedList.find((r) => r.id === selectedReq.id);
      if (updated) setSelectedReq(updated);
      setArtifacts(artList);
      setRenderJobs(jobList);
      setActiveTab("artifacts");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Render failed");
    } finally {
      setActionLoading(false);
    }
  };

  // Handler: Explicit Rerender (vN+1)
  const handleRerender = async () => {
    if (!selectedReq) return;
    try {
      setActionLoading(true);
      const idempotencyKey = `rerender_${selectedReq.id}_${Date.now()}`;
      await rerenderProduction(channelId, selectedReq.id, idempotencyKey, "Manual rerender request");
      const [updatedList, artList, jobList] = await Promise.all([
        listProductionRequests(channelId),
        listMediaArtifacts(channelId, selectedReq.id),
        listRenderJobs(channelId, selectedReq.id),
      ]);
      setRequests(updatedList);
      const updated = updatedList.find((r) => r.id === selectedReq.id);
      if (updated) setSelectedReq(updated);
      setArtifacts(artList);
      setRenderJobs(jobList);
      setActiveTab("artifacts");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Rerender failed");
    } finally {
      setActionLoading(false);
    }
  };

  const selectedArtifact = artifacts.find((a) => a.version === selectedArtifactVersion) || artifacts[0];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      {/* Header Breadcrumb */}
      <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-800">
        <div>
          <div className="flex items-center space-x-2 text-sm text-slate-400 mb-1">
            <Link href="/" className="hover:text-cyan-400">Dashboard</Link>
            <span>/</span>
            <Link href={`/channels/${channelId}`} className="hover:text-cyan-400">
              {channel ? channel.name : "Channel"}
            </Link>
            <span>/</span>
            <span className="text-slate-200">Production Engine</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-3">
            🎬 Production Workspace
            <span className="text-xs px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono">
              OMEGA-007
            </span>
          </h1>
        </div>

        <div className="flex items-center space-x-3">
          <button
            onClick={() => setIsCreating(true)}
            className="px-4 py-2 bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white rounded-lg text-sm font-medium shadow-md shadow-cyan-950 transition"
          >
            + New Production Request
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-6 p-4 rounded-lg bg-red-950/60 border border-red-800 text-red-300 text-sm flex justify-between items-center">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-white">✕</button>
        </div>
      )}

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left Sidebar: Requests List */}
        <div className="lg:col-span-1 space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
              Production Requests ({requests.length})
            </h2>

            {loading ? (
              <div className="text-sm text-slate-500 py-6 text-center">Loading requests...</div>
            ) : requests.length === 0 ? (
              <div className="text-sm text-slate-500 py-6 text-center">No production requests yet.</div>
            ) : (
              <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-1">
                {requests.map((req) => (
                  <button
                    key={req.id}
                    onClick={() => setSelectedReq(req)}
                    className={`w-full text-left p-3 rounded-lg border transition ${
                      selectedReq?.id === req.id
                        ? "bg-slate-800 border-cyan-500/50 shadow-sm"
                        : "bg-slate-900/50 border-slate-800 hover:border-slate-700"
                    }`}
                  >
                    <div className="flex justify-between items-start mb-1.5">
                      <span className="font-mono text-xs text-slate-400">
                        {req.id.slice(0, 8)}...
                      </span>
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded font-semibold ${
                          req.status === "SUCCEEDED"
                            ? req.outcome === "BLOCKED"
                              ? "bg-amber-950 text-amber-400 border border-amber-800"
                              : "bg-emerald-950 text-emerald-400 border border-emerald-800"
                            : req.status === "FAILED"
                            ? "bg-red-950 text-red-400 border border-red-800"
                            : req.status === "RUNNING"
                            ? "bg-blue-950 text-blue-400 border border-blue-800 animate-pulse"
                            : "bg-slate-800 text-slate-300"
                        }`}
                      >
                        {req.outcome === "BLOCKED" ? "BLOCKED" : req.status}
                      </span>
                    </div>
                    <div className="text-xs text-slate-300">
                      {req.target_width}x{req.target_height} @ {req.fps}fps ({req.video_codec})
                    </div>
                    <div className="text-[10px] text-slate-500 mt-1">
                      {new Date(req.created_at).toLocaleTimeString()}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right Main Content */}
        <div className="lg:col-span-3 space-y-6">
          {selectedReq ? (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
              {/* Request Header Bar */}
              <div className="flex flex-wrap items-center justify-between gap-4 pb-6 border-b border-slate-800">
                <div>
                  <div className="flex items-center gap-3">
                    <h2 className="text-xl font-bold text-white">
                      Production Request {selectedReq.id.slice(0, 8)}
                    </h2>
                    <span className="text-xs px-2.5 py-0.5 rounded bg-slate-800 text-slate-300">
                      {selectedReq.mode}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 mt-1 font-mono">
                    Script Pinned: {selectedReq.script_version_id} | DNA: {selectedReq.channel_dna_revision_id.slice(0, 8)}...
                  </p>
                </div>

                <div className="flex items-center space-x-2">
                  <button
                    onClick={handlePrepare}
                    disabled={actionLoading || selectedReq.status === "RUNNING"}
                    className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-200 rounded-lg text-xs font-semibold border border-slate-700 transition"
                  >
                    {actionLoading ? "Processing..." : "1. Prepare Plan"}
                  </button>

                  <button
                    onClick={handleRender}
                    disabled={actionLoading || selectedReq.status === "RUNNING"}
                    className="px-3.5 py-1.5 bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white rounded-lg text-xs font-semibold shadow-sm transition"
                  >
                    {actionLoading ? "Rendering..." : "2. Render (v1)"}
                  </button>

                  <button
                    onClick={handleRerender}
                    disabled={actionLoading || selectedReq.status === "RUNNING" || artifacts.length === 0}
                    className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white rounded-lg text-xs font-semibold shadow-sm transition"
                  >
                    3. Rerender (vN+1)
                  </button>
                </div>
              </div>

              {/* Tabs Navigation */}
              <div className="flex space-x-2 border-b border-slate-800 mt-4 pb-2 overflow-x-auto text-xs">
                {(
                  [
                    ["scenes", `🎬 Scenes (${scenes.length})`],
                    ["assets", `🎨 Assets (${assets.length})`],
                    ["narration", `🎙️ Narration (${narration.length})`],
                    ["subtitles", `💬 Subtitles (${subtitles.length})`],
                    ["plan", "⚙️ Render Plan"],
                    ["artifacts", `📹 Media Artifacts (${artifacts.length})`],
                    ["qa", "🛡️ QA Findings"],
                  ] as const
                ).map(([tabKey, tabLabel]) => (
                  <button
                    key={tabKey}
                    onClick={() => setActiveTab(tabKey)}
                    className={`px-3 py-1.5 rounded-lg font-medium transition ${
                      activeTab === tabKey
                        ? "bg-slate-800 text-cyan-400 border border-slate-700"
                        : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    {tabLabel}
                  </button>
                ))}
              </div>

              {/* Tab Content Area */}
              <div className="mt-6">
                {/* 1. SCENES TAB */}
                {activeTab === "scenes" && (
                  <div className="space-y-4">
                    {scenes.length === 0 ? (
                      <div className="text-center py-12 text-slate-500 text-sm">
                        No scenes generated yet. Click <strong>1. Prepare Plan</strong> above.
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {scenes.map((scene) => (
                          <div key={scene.id} className="p-4 bg-slate-950 border border-slate-800 rounded-lg">
                            <div className="flex justify-between items-center mb-2">
                              <span className="font-bold text-sm text-cyan-400">
                                Scene {scene.scene_order} • {scene.scene_type}
                              </span>
                              <span className="text-xs font-mono text-slate-400">
                                {scene.estimated_duration_ms} ms
                              </span>
                            </div>
                            <p className="text-sm text-slate-200 mb-2">
                              {scene.narration_text}
                            </p>
                            {scene.visual_intent && (
                              <div className="text-xs text-slate-400 bg-slate-900/80 p-2 rounded border border-slate-800/60">
                                🎨 <em>Visual: {scene.visual_intent}</em>
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
                  <div className="space-y-4">
                    {assets.length === 0 ? (
                      <div className="text-center py-12 text-slate-500 text-sm">
                        No assets resolved yet.
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {assets.map((ast) => (
                          <div key={ast.id} className="p-4 bg-slate-950 border border-slate-800 rounded-lg">
                            <div className="flex justify-between items-start mb-2">
                              <span className="font-semibold text-xs text-slate-300 uppercase">
                                {ast.asset_type} ({ast.provider_type})
                              </span>
                              <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">
                                {ast.license_status}
                              </span>
                            </div>
                            <p className="text-xs text-slate-400 font-mono truncate mb-1">
                              URI: {ast.storage_uri}
                            </p>
                            <p className="text-[10px] text-slate-500 font-mono truncate">
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
                  <div className="space-y-3">
                    {narration.length === 0 ? (
                      <div className="text-center py-12 text-slate-500 text-sm">No narration segments generated.</div>
                    ) : (
                      narration.map((seg, idx) => (
                        <div key={seg.id} className="p-3 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-between">
                          <div className="space-y-1">
                            <div className="text-xs text-cyan-400 font-semibold">Segment #{idx + 1}</div>
                            <div className="text-sm text-slate-200">{seg.text}</div>
                          </div>
                          <div className="text-right text-xs font-mono text-slate-400">
                            <div>{seg.start_ms}ms → {seg.end_ms}ms</div>
                            <div className="text-slate-500">Δ {seg.duration_ms}ms</div>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {/* 4. SUBTITLES TAB */}
                {activeTab === "subtitles" && (
                  <div className="space-y-3">
                    {subtitles.length === 0 ? (
                      <div className="text-center py-12 text-slate-500 text-sm">No subtitle cues available.</div>
                    ) : (
                      subtitles.map((cue) => (
                        <div key={cue.id} className="p-3 bg-slate-950 border border-slate-800 rounded-lg flex justify-between items-center">
                          <div>
                            <span className="text-xs font-bold text-slate-400 mr-3">#{cue.cue_order}</span>
                            <span className="text-sm text-slate-200">{cue.text}</span>
                          </div>
                          <span className="text-xs font-mono text-slate-400">
                            {cue.start_ms}ms → {cue.end_ms}ms
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {/* 5. RENDER PLAN TAB */}
                {activeTab === "plan" && (
                  <div className="space-y-4">
                    {renderPlan ? (
                      <div className="space-y-4">
                        <div className="p-4 bg-slate-950 border border-slate-800 rounded-lg space-y-3">
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs font-mono">
                            <div className="bg-slate-900 p-3 rounded border border-slate-800">
                              <span className="text-slate-400 block mb-1">Dimensions</span>
                              <span className="text-slate-200 font-bold">{renderPlan.width}x{renderPlan.height}</span>
                            </div>
                            <div className="bg-slate-900 p-3 rounded border border-slate-800">
                              <span className="text-slate-400 block mb-1">FPS / Codec</span>
                              <span className="text-slate-200 font-bold">{renderPlan.fps} fps ({renderPlan.video_codec})</span>
                            </div>
                            <div className="bg-slate-900 p-3 rounded border border-slate-800">
                              <span className="text-slate-400 block mb-1">Total Duration</span>
                              <span className="text-slate-200 font-bold">{(renderPlan.total_duration_ms / 1000).toFixed(1)}s</span>
                            </div>
                            <div className="bg-slate-900 p-3 rounded border border-slate-800">
                              <span className="text-slate-400 block mb-1">Plan Version</span>
                              <span className="text-cyan-400 font-bold">v{renderPlan.version}</span>
                            </div>
                          </div>
                        </div>

                        {/* Render Jobs History */}
                        {renderJobs.length > 0 && (
                          <div className="p-4 bg-slate-950 border border-slate-800 rounded-lg space-y-2">
                            <h4 className="text-xs font-semibold uppercase text-slate-400">Execution Jobs ({renderJobs.length})</h4>
                            <div className="space-y-1.5 max-h-48 overflow-y-auto">
                              {renderJobs.map((j) => (
                                <div key={j.id} className="p-2.5 bg-slate-900 border border-slate-800 rounded flex justify-between items-center text-xs">
                                  <div className="space-y-0.5">
                                    <span className="font-mono text-cyan-400">{j.id.slice(0, 8)}...</span>
                                    <div className="text-[10px] text-slate-500 font-mono">Key: {j.idempotency_key}</div>
                                  </div>
                                  <div className="text-right">
                                    <span className="font-semibold text-slate-300">{j.state}</span>
                                    <div className="text-[10px] text-slate-500">Attempt {j.attempt}/{j.max_attempts}</div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="text-center py-12 text-slate-500 text-sm">No RenderPlan manifest available.</div>
                    )}
                  </div>
                )}

                {/* 6. MEDIA ARTIFACTS TAB */}
                {activeTab === "artifacts" && (
                  <div className="space-y-6">
                    {artifacts.length === 0 ? (
                      <div className="text-center py-12 text-slate-500 text-sm">
                        No rendered media artifacts found. Click <strong>2. Render</strong> to produce an MP4 video.
                      </div>
                    ) : (
                      <div>
                        {/* Version Switcher */}
                        <div className="flex items-center space-x-2 mb-4">
                          <span className="text-xs text-slate-400 font-medium">Revisions:</span>
                          {artifacts.map((art) => (
                            <button
                              key={art.id}
                              onClick={() => setSelectedArtifactVersion(art.version)}
                              className={`px-3 py-1 text-xs rounded font-mono font-semibold transition ${
                                selectedArtifactVersion === art.version
                                  ? "bg-cyan-600 text-white shadow-sm"
                                  : "bg-slate-800 text-slate-400 hover:text-white"
                              }`}
                            >
                              v{art.version} {art.is_current ? "(Current)" : ""}
                            </button>
                          ))}
                        </div>

                        {selectedArtifact && (
                          <div className="space-y-4">
                            {/* Video Player */}
                            <div className="aspect-video bg-black rounded-xl overflow-hidden border border-slate-800 shadow-xl relative">
                              <video
                                controls
                                className="w-full h-full object-contain"
                                src={getMediaArtifactStreamUrl(channelId, selectedReq.id, selectedArtifact.id)}
                              >
                                Your browser does not support HTML5 video streaming.
                              </video>
                            </div>

                            {/* Artifact Info Card */}
                            <div className="p-4 bg-slate-950 border border-slate-800 rounded-lg text-xs space-y-2">
                              <div className="flex justify-between">
                                <span className="text-slate-400">Dimensions:</span>
                                <span className="font-mono text-slate-200">{selectedArtifact.width}x{selectedArtifact.height}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-slate-400">Duration:</span>
                                <span className="font-mono text-slate-200">{selectedArtifact.duration_ms ? (selectedArtifact.duration_ms / 1000).toFixed(2) : "0"} s</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-slate-400">File Size:</span>
                                <span className="font-mono text-slate-200">{(selectedArtifact.file_size_bytes / 1024).toFixed(1)} KB</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-slate-400">SHA-256 Digest:</span>
                                <span className="font-mono text-cyan-400 truncate max-w-[300px]">{selectedArtifact.content_hash}</span>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* 7. QA TAB */}
                {activeTab === "qa" && (
                  <div className="space-y-4">
                    {qaResult ? (
                      <div>
                        <div className="flex items-center gap-3 mb-4">
                          <span className="text-sm font-semibold text-slate-300">Overall Status:</span>
                          <span
                            className={`text-xs px-3 py-1 rounded font-bold ${
                              qaResult.status === "PASSED"
                                ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                                : qaResult.status === "PASSED_WITH_WARNINGS"
                                ? "bg-amber-950 text-amber-400 border border-amber-800"
                                : "bg-red-950 text-red-400 border border-red-800"
                            }`}
                          >
                            {qaResult.status}
                          </span>
                        </div>

                        {qaResult.findings.length === 0 ? (
                          <div className="p-4 bg-emerald-950/40 border border-emerald-800 text-emerald-300 text-xs rounded-lg">
                            ✅ All 17 Production QA checks passed successfully with zero findings.
                          </div>
                        ) : (
                          <div className="space-y-2">
                            {qaResult.findings.map((f, i) => (
                              <div
                                key={i}
                                className={`p-3 rounded-lg border text-xs flex items-start gap-3 ${
                                  f.severity === "BLOCKING"
                                    ? "bg-red-950/40 border-red-800 text-red-300"
                                    : "bg-amber-950/40 border-amber-800 text-amber-300"
                                }`}
                              >
                                <span className="font-mono font-bold uppercase">{f.rule_code}</span>
                                <span>{f.message}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="text-center py-12 text-slate-500 text-sm">No QA results evaluated yet.</div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="p-12 text-center text-slate-500 bg-slate-900 border border-slate-800 rounded-xl">
              Select or create a production request to view workspace.
            </div>
          )}
        </div>
      </div>

      {/* Create Modal */}
      {isCreating && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-lg w-full p-6 space-y-4">
            <h3 className="text-lg font-bold text-white">Create Production Request</h3>
            <form onSubmit={handleCreateRequest} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-400 mb-1">
                  1. Select Content Generation Request
                </label>
                <select
                  value={selectedContentReqId}
                  onChange={(e) => handleContentReqChange(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-sm text-slate-200"
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
                <div>
                  <label className="block text-xs font-semibold text-slate-400 mb-1">
                    2. Select Pinned Script Version
                  </label>
                  <select
                    value={selectedScriptId}
                    onChange={(e) => setSelectedScriptId(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-sm text-slate-200"
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

              <div className="flex justify-end space-x-3 pt-4 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsCreating(false)}
                  className="px-4 py-2 text-xs font-semibold text-slate-400 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading || !selectedScriptId}
                  className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white rounded-lg text-xs font-semibold transition"
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
