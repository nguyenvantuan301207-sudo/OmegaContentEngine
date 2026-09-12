"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getChannels, getMediaArtifactStreamUrl, getProductionQAResult, getProductionRenderCapabilities, getRenderPlan, listMediaArtifacts,
  listNarrationSegments, listProductionAssets, listProductionRequests, listProductionScenes,
  listRenderJobs, listSubtitleCues, prepareProduction, renderProduction, updateProductionRenderSettings,
  type MediaArtifact, type NarrationSegment, type ProductionAsset, type ProductionQAResult,
  type ProductionRenderCapabilities, type ProductionRenderJob, type ProductionRequest, type ProductionScene, type RenderPlan,
  type SubtitleCue, type SubtitleRenderStyle, type Channel,
} from "@/lib/api";
import { resolveArtifactProvenance } from "@/lib/subtitle-render-ui";
import { useOperatorContext } from "@/lib/operator-context";
import { classifyChannel, isChannelVisible } from "@/lib/channel-classification";
import { PublishPreparationModal } from "@/components/PublishPreparationModal";
import { Alert, ErrorState } from "@/components/ui";
import { ProductionQAPanel, StoryboardPanel, SubtitlePanel, TimelinePanel } from "@/components/workflow/ProductionPanels";

type ProductionView = "overview" | "script" | "storyboard" | "subtitle" | "timeline" | "terminal" | "qa";
type PipelineState = "DONE" | "CURRENT" | "PENDING" | "FAILED" | "BLOCKED";
type TerminalDomain = "Mission" | "Research" | "Content" | "Production" | "Render" | "Assets" | "QA" | "Publisher";
type TerminalFilter = "All" | TerminalDomain | "Errors";
interface TerminalEvent { key: string; timestamp: string; domain: TerminalDomain; severity: "INFO" | "WARNING" | "ERROR"; status: string; message: string; entityRef?: string; }
interface RecentProduction { channelId: string; channelName: string; request: ProductionRequest; hasVideo: boolean; artifactCount: number; }

const TERMINAL_POLL_MS = 8000;
const terminalFilters: TerminalFilter[] = ["All", "Mission", "Research", "Content", "Production", "Render", "Assets", "QA", "Publisher", "Errors"];

function newest<T extends { created_at: string }>(items: T[]) { return [...items].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)); }
function displayDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); }
function displayTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "--:--:--" : date.toLocaleTimeString([], { hour12: false }); }
function safeStatus(value?: string | null) { return (value || "PENDING").replaceAll("_", " "); }
function stageState(condition: boolean, current = false): PipelineState { return condition ? "DONE" : current ? "CURRENT" : "PENDING"; }

function buildTerminalEvents(request: ProductionRequest | null, scenes: ProductionScene[], assets: ProductionAsset[], narration: NarrationSegment[], subtitles: SubtitleCue[], plan: RenderPlan | null, jobs: ProductionRenderJob[], artifacts: MediaArtifact[], qa: ProductionQAResult | null): TerminalEvent[] {
  if (!request) return [];
  const events: TerminalEvent[] = [{ key: `request-${request.id}`, timestamp: request.created_at, domain: "Production", severity: "INFO", status: request.status, message: `Production request created in ${request.mode.toLowerCase().replaceAll("_", " ")} mode.`, entityRef: request.id }];
  if (request.started_at) events.push({ key: `request-start-${request.id}`, timestamp: request.started_at, domain: "Production", severity: "INFO", status: "STARTED", message: "Production execution started.", entityRef: request.id });
  if (request.completed_at) events.push({ key: `request-complete-${request.id}`, timestamp: request.completed_at, domain: "Production", severity: "INFO", status: request.outcome || request.status, message: "Production execution completed.", entityRef: request.id });
  if (request.failed_at) events.push({ key: `request-failed-${request.id}`, timestamp: request.failed_at, domain: "Production", severity: "ERROR", status: "FAILED", message: "Production execution failed. Open the inspector for the persisted state.", entityRef: request.id });
  if (scenes.length) events.push({ key: `scenes-${request.id}`, timestamp: newest(scenes)[0].created_at, domain: "Production", severity: "INFO", status: "STORYBOARD_READY", message: `${scenes.length} persisted scene${scenes.length === 1 ? "" : "s"} available.`, entityRef: request.id });
  if (assets.length) events.push({ key: `assets-${request.id}`, timestamp: newest(assets)[0].created_at, domain: "Assets", severity: "INFO", status: "ASSETS_AVAILABLE", message: `${assets.length} persisted production asset${assets.length === 1 ? "" : "s"} available.`, entityRef: request.id });
  if (narration.length) events.push({ key: `voice-${request.id}`, timestamp: newest(narration)[0].created_at, domain: "Assets", severity: "INFO", status: "NARRATION_READY", message: `${narration.length} narration segment${narration.length === 1 ? "" : "s"} available.`, entityRef: request.id });
  if (subtitles.length) events.push({ key: `subtitles-${request.id}`, timestamp: newest(subtitles)[0].created_at, domain: "Assets", severity: "INFO", status: "SUBTITLES_READY", message: `${subtitles.length} persisted subtitle cue${subtitles.length === 1 ? "" : "s"} available.`, entityRef: request.id });
  if (plan) events.push({ key: `plan-${plan.id}`, timestamp: plan.created_at, domain: "Render", severity: "INFO", status: "PLAN_READY", message: `Render plan version ${plan.version} is available.`, entityRef: plan.id });
  for (const job of jobs) {
    events.push({ key: `job-${job.id}`, timestamp: job.created_at, domain: "Render", severity: job.state === "FAILED" ? "ERROR" : "INFO", status: job.state, message: job.state === "FAILED" ? (job.sanitized_error || "Render job failed.") : `Render attempt ${job.attempt} recorded.`, entityRef: job.id });
    if (job.completed_at) events.push({ key: `job-complete-${job.id}`, timestamp: job.completed_at, domain: "Render", severity: job.state === "FAILED" ? "ERROR" : "INFO", status: job.state, message: job.state === "FAILED" ? "Render attempt completed with failure." : "Render attempt completed.", entityRef: job.id });
  }
  for (const artifact of artifacts) events.push({ key: `artifact-${artifact.id}`, timestamp: artifact.created_at, domain: "Assets", severity: "INFO", status: artifact.is_current ? "VIDEO_READY" : "ARTIFACT_CREATED", message: `${artifact.artifact_type.replaceAll("_", " ")} version ${artifact.version} is available.`, entityRef: artifact.id });
  if (qa) events.push({ key: `qa-${qa.id}`, timestamp: qa.executed_at, domain: "QA", severity: qa.status === "BLOCKED" ? "ERROR" : qa.status === "PASSED_WITH_WARNINGS" ? "WARNING" : "INFO", status: qa.status, message: qa.findings.length ? `QA completed with ${qa.findings.length} finding${qa.findings.length === 1 ? "" : "s"}.` : "QA completed with no findings.", entityRef: qa.id });
  return events.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

function ProductionWorkspace({ forcedChannelId }: { forcedChannelId?: string }) {
  const { channels, visibleChannels, selectedChannelId, setSelectedChannelId, channelsLoading, showInternalChannels, setShowInternalChannels } = useOperatorContext();
  const channelId = forcedChannelId || selectedChannelId || visibleChannels[0]?.id || channels[0]?.id || "";
  const activeChannel = channels.find((channel) => channel.id === channelId) ?? null;
  const activeChannelProvenance = activeChannel ? classifyChannel(activeChannel) : null;
  const [requests, setRequests] = useState<ProductionRequest[]>([]);
  const [recent, setRecent] = useState<RecentProduction[]>([]);
  const [discoveredProductionCounts, setDiscoveredProductionCounts] = useState<Record<string, number>>({});
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [scenes, setScenes] = useState<ProductionScene[]>([]);
  const [assets, setAssets] = useState<ProductionAsset[]>([]);
  const [narration, setNarration] = useState<NarrationSegment[]>([]);
  const [subtitles, setSubtitles] = useState<SubtitleCue[]>([]);
  const [plan, setPlan] = useState<RenderPlan | null>(null);
  const [jobs, setJobs] = useState<ProductionRenderJob[]>([]);
  const [artifacts, setArtifacts] = useState<MediaArtifact[]>([]);
  const [qa, setQa] = useState<ProductionQAResult | null>(null);
  const [renderCapabilities, setRenderCapabilities] = useState<ProductionRenderCapabilities | null>(null);
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [artifactVersion, setArtifactVersion] = useState<number | null>(null);
  const [view, setView] = useState<ProductionView>("overview");
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [terminalLive, setTerminalLive] = useState(true);
  const [terminalAutoScroll, setTerminalAutoScroll] = useState(true);
  const [terminalSearch, setTerminalSearch] = useState("");
  const [terminalFilter, setTerminalFilter] = useState<TerminalFilter>("All");
  const [terminalClearedAt, setTerminalClearedAt] = useState<number | null>(null);
  const [terminalNotice, setTerminalNotice] = useState<string | null>(null);
  const terminalEndRef = useRef<HTMLDivElement | null>(null);
  const recentDiscoveryStarted = useRef(false);

  const selectedRequest = requests.find((request) => request.id === selectedRequestId) ?? requests[0] ?? null;
  const selectedScene = scenes.find((scene) => scene.id === selectedSceneId) ?? null;
  const latestJob = newest(jobs)[0] ?? null;
  const selectedArtifact = artifacts.find((artifact) => artifact.version === artifactVersion) ?? artifacts.find((artifact) => artifact.is_current) ?? newest(artifacts)[0] ?? null;

  const clearDetails = useCallback(() => { setScenes([]); setAssets([]); setNarration([]); setSubtitles([]); setPlan(null); setJobs([]); setArtifacts([]); setQa(null); setArtifactVersion(null); setSelectedSceneId(null); }, []);
  const loadDetails = useCallback(async (targetChannelId: string, request: ProductionRequest, silent = false) => {
    if (!silent) setDetailsLoading(true);
    const results = await Promise.allSettled([listProductionScenes(targetChannelId, request.id), listProductionAssets(targetChannelId, request.id), listNarrationSegments(targetChannelId, request.id), listSubtitleCues(targetChannelId, request.id), getRenderPlan(targetChannelId, request.id), listRenderJobs(targetChannelId, request.id), listMediaArtifacts(targetChannelId, request.id), getProductionQAResult(targetChannelId, request.id)]);
    setScenes(results[0].status === "fulfilled" ? results[0].value : []); setAssets(results[1].status === "fulfilled" ? results[1].value : []); setNarration(results[2].status === "fulfilled" ? results[2].value : []); setSubtitles(results[3].status === "fulfilled" ? results[3].value : []); setPlan(results[4].status === "fulfilled" ? results[4].value : null); setJobs(results[5].status === "fulfilled" ? results[5].value : []);
    const foundArtifacts = results[6].status === "fulfilled" ? results[6].value : []; setArtifacts(foundArtifacts); setArtifactVersion((current) => current ?? foundArtifacts.find((artifact) => artifact.is_current)?.version ?? newest(foundArtifacts)[0]?.version ?? null); setQa(results[7].status === "fulfilled" ? results[7].value : null);
    if (!silent) setDetailsLoading(false);
  }, []);

  const loadChannel = useCallback(async (targetChannelId: string, preferredRequestId?: string | null) => {
    if (!targetChannelId) { setRequests([]); clearDetails(); setLoading(false); return; }
    setLoading(true); setError(null);
    try {
      const found = newest(await listProductionRequests(targetChannelId)); setRequests(found);
      const target = found.find((request) => request.id === preferredRequestId) ?? found[0] ?? null;
      setSelectedRequestId(target?.id ?? null); setTerminalClearedAt(null);
      if (target) await loadDetails(targetChannelId, target); else clearDetails();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Failed to load the production workspace."); clearDetails(); }
    finally { setLoading(false); }
  }, [clearDetails, loadDetails]);

  useEffect(() => { if (forcedChannelId) void setSelectedChannelId(forcedChannelId); }, [forcedChannelId, setSelectedChannelId]);
  useEffect(() => { void getProductionRenderCapabilities().then(setRenderCapabilities).catch((caught) => setError(caught instanceof Error ? caught.message : "Renderer capabilities are unavailable.")); }, []);
  useEffect(() => { void loadChannel(channelId); }, [channelId, loadChannel]);
  useEffect(() => {
    if (!channels.length || recentDiscoveryStarted.current) return;
    recentDiscoveryStarted.current = true;
    void getChannels(undefined, undefined, 150, 0).then((discoveryChannels) =>
      Promise.allSettled(discoveryChannels.map(async (channel) => ({ channel, requests: newest(await listProductionRequests(channel.id)) })))
    ).then(async (results) => {
      const allDiscovered = results.flatMap((result) => result.status === "fulfilled" ? result.value.requests.map((request) => ({ channelId: result.value.channel.id, channelName: result.value.channel.name, request })) : []).sort((a, b) => Date.parse(b.request.created_at) - Date.parse(a.request.created_at));
      setDiscoveredProductionCounts(allDiscovered.reduce<Record<string, number>>((counts, item) => { counts[item.channelId] = (counts[item.channelId] || 0) + 1; return counts; }, {}));
      const discovered = allDiscovered.slice(0, 12);
      const enriched = await Promise.all(discovered.map(async (item) => {
        const foundArtifacts = await listMediaArtifacts(item.channelId, item.request.id).catch(() => []);
        return { ...item, artifactCount: foundArtifacts.length, hasVideo: foundArtifacts.some((artifact) => artifact.mime_type.startsWith("video/") || artifact.artifact_type.toLowerCase().includes("video")) };
      }));
      setRecent(enriched);
    }).catch(() => setRecent([]));
  }, [channels]);
  useEffect(() => { if (view !== "terminal" || !terminalLive || !selectedRequest || !channelId) return; const timer = window.setInterval(() => void loadDetails(channelId, selectedRequest, true), TERMINAL_POLL_MS); return () => window.clearInterval(timer); }, [channelId, loadDetails, selectedRequest, terminalLive, view]);

  const terminalEvents = useMemo(() => buildTerminalEvents(selectedRequest, scenes, assets, narration, subtitles, plan, jobs, artifacts, qa), [selectedRequest, scenes, assets, narration, subtitles, plan, jobs, artifacts, qa]);
  const visibleEvents = useMemo(() => terminalEvents.filter((event) => (!terminalClearedAt || Date.parse(event.timestamp) > terminalClearedAt) && (terminalFilter === "All" || (terminalFilter === "Errors" ? event.severity === "ERROR" : event.domain === terminalFilter)) && (!terminalSearch.trim() || `${event.domain} ${event.status} ${event.message}`.toLowerCase().includes(terminalSearch.trim().toLowerCase()))), [terminalEvents, terminalClearedAt, terminalFilter, terminalSearch]);
  useEffect(() => { if (view === "terminal" && terminalAutoScroll) terminalEndRef.current?.scrollIntoView({ block: "nearest" }); }, [view, visibleEvents, terminalAutoScroll]);

  const chooseRequest = async (request: ProductionRequest) => { setSelectedRequestId(request.id); setSelectedSceneId(null); setTerminalClearedAt(null); await loadDetails(channelId, request); };
  const openRecent = async (value: string) => { const item = recent.find((candidate) => `${candidate.channelId}:${candidate.request.id}` === value); if (!item) return; setSelectedRequestId(item.request.id); await setSelectedChannelId(item.channelId); if (forcedChannelId) return; await loadChannel(item.channelId, item.request.id); };
  const perform = async (action: () => Promise<unknown>) => { if (!selectedRequest) return; setBusy(true); setError(null); try { await action(); await loadChannel(channelId, selectedRequest.id); } catch (caught) { setError(caught instanceof Error ? caught.message : "Production action failed."); } finally { setBusy(false); } };
  const applySubtitleStyle = async (style: SubtitleRenderStyle) => {
    if (!selectedRequest) throw new Error("Select a production before applying render settings.");
    setBusy(true);
    setError(null);
    try {
      const updated = await updateProductionRenderSettings(channelId, selectedRequest.id, style);
      setRequests((current) => current.map((request) => request.id === updated.id ? updated : request));
    } finally {
      setBusy(false);
    }
  };

  const pipeline: Array<[string, PipelineState]> = [
    ["Research", "PENDING"], ["Script / Content", stageState(Boolean(selectedRequest))], ["Storyboard / Planning", stageState(scenes.length > 0, Boolean(selectedRequest))], ["Assets", stageState(assets.length > 0, scenes.length > 0)], ["Voice", stageState(narration.length > 0, scenes.length > 0)],
    ["Render", latestJob?.state === "FAILED" || selectedRequest?.status === "FAILED" ? "FAILED" : artifacts.length > 0 || latestJob?.state === "SUCCEEDED" ? "DONE" : latestJob?.state === "RUNNING" || selectedRequest?.status === "RUNNING" ? "CURRENT" : "PENDING"],
    ["QA", qa?.status === "BLOCKED" ? "BLOCKED" : qa?.status === "PASSED" || qa?.status === "PASSED_WITH_WARNINGS" ? "DONE" : qa ? "CURRENT" : "PENDING"], ["Publish", "PENDING"],
  ];
  const productionCounts = discoveredProductionCounts;
  const toChannelStub = (id: string, name: string): Channel => ({
    id,
    name,
    slug: id,
    description: null,
    platform: "YOUTUBE",
    platform_channel_id: null,
    primary_language: "en",
    target_region: "US",
    timezone: "UTC",
    state: "ACTIVE",
    dna: {} as unknown as Channel["dna"],
    metadata: {},
    created_at: "",
    updated_at: "",
    archived_at: null,
  });
  const baseChannels = showInternalChannels ? channels : visibleChannels;
  const availableChannels = [
    ...baseChannels,
    ...recent
      .filter((item) => !baseChannels.some((channel) => channel.id === item.channelId))
      .filter((item) => showInternalChannels || item.channelId === channelId)
      .map((item) => toChannelStub(item.channelId, item.channelName)),
  ].filter((channel, index, all) => all.findIndex((candidate) => candidate.id === channel.id) === index);

  const visibleRecent = useMemo(() => {
    if (showInternalChannels) return recent;
    return recent.filter((item) => {
      const ch = channels.find((c) => c.id === item.channelId) || toChannelStub(item.channelId, item.channelName);
      return isChannelVisible(ch, false);
    });
  }, [recent, showInternalChannels, channels]);

  return <div className="ui-page-stack production-page">
    <header className="production-head"><div><div className="eyebrow">MISSION WORKSPACE</div><h1>Production Studio</h1><p>{activeChannel ? `${activeChannel.name}${activeChannelProvenance?.isInternal ? ` · [${activeChannelProvenance.badgeLabel}]` : ""} · ${activeChannel.platform}` : "Select a channel to inspect persisted production"}</p></div><div className="production-head-actions"><label><span>Channel</span><select className="select" value={channelId} onChange={(event) => { setSelectedRequestId(null); void setSelectedChannelId(event.target.value); }} disabled={channelsLoading || Boolean(forcedChannelId)}>{availableChannels.map((channel) => { const prov = classifyChannel(channel); const badge = prov.isInternal ? ` [${prov.badgeLabel}]` : ""; return <option key={channel.id} value={channel.id}>{channel.name}{badge} · {productionCounts[channel.id] || 0} production{productionCounts[channel.id] === 1 ? "" : "s"}</option>; })}</select></label><label><span>Production</span><select className="select" value={selectedRequest?.id ?? ""} onChange={(event) => { const request = requests.find((item) => item.id === event.target.value); if (request) void chooseRequest(request); }} disabled={!requests.length}><option value="">No production in this channel</option>{requests.map((request, index) => { const discovered = recent.find((item) => item.request.id === request.id); const videoLabel = discovered?.hasVideo || (request.id === selectedRequest?.id && artifacts.some((artifact) => artifact.mime_type.startsWith("video/"))) ? " · Video ready" : ""; return <option key={request.id} value={request.id}>Production {requests.length - index} · {safeStatus(request.outcome || request.status)}{videoLabel} · {displayDate(request.created_at)}</option>; })}</select></label><button type="button" className="btn" disabled={!channelId || loading} onClick={() => void loadChannel(channelId, selectedRequest?.id)}>Refresh</button></div></header>

    <div className="recent-production-bar"><div><span className="eyebrow">AVAILABLE PRODUCTION</span><strong>{visibleRecent.length > 0 ? (requests.length ? "Switch persisted work" : `No production in ${activeChannel?.name || "this channel"}`) : "No visible production in current product channels"}</strong></div>{visibleRecent.length > 0 ? <select className="select" defaultValue="" onChange={(event) => void openRecent(event.target.value)}><option value="">Choose a persisted production…</option>{visibleRecent.map((item) => { const ch = channels.find((c) => c.id === item.channelId) || toChannelStub(item.channelId, item.channelName); const prov = classifyChannel(ch); const badge = prov.isInternal ? ` [${prov.badgeLabel}]` : ""; return <option key={`${item.channelId}:${item.request.id}`} value={`${item.channelId}:${item.request.id}`}>{item.channelName}{badge} · {safeStatus(item.request.outcome || item.request.status)} · {item.hasVideo ? "Video ready" : `${item.artifactCount} artifacts`} · {displayDate(item.request.created_at)}</option>; })}</select> : <button type="button" className="btn btn-secondary btn-sm" onClick={() => setShowInternalChannels(true)}>Show internal/test production</button>}</div>
    {error && <ErrorState title="Production data unavailable" description={error} action={<button type="button" className="btn" onClick={() => void loadChannel(channelId, selectedRequest?.id)}>Retry</button>} />}
    {latestJob?.state === "FAILED" && <Alert tone="danger" title="Latest render failed">{latestJob.sanitized_error || "The persisted render job reports a failure."}</Alert>}

    <section className={`mission-studio ${detailsLoading ? "is-loading" : ""}`} aria-busy={loading || detailsLoading}>
      <div className="pipeline" aria-label="Production pipeline">{pipeline.map(([label, state]) => <div className={`stage ${state.toLowerCase()}`} key={label}><span>{state === "DONE" ? "✓" : state === "CURRENT" ? "●" : state === "FAILED" ? "×" : state === "BLOCKED" ? "!" : "○"}</span><b>{label}</b><small>{state}</small></div>)}</div>
      <nav className="workspace-tabs" aria-label="Production workspace tabs">{([ ["overview", "Overview"], ["script", "Script Studio"], ["storyboard", `Storyboard ${scenes.length ? `(${scenes.length})` : ""}`], ["subtitle", `Subtitle ${subtitles.length ? `(${subtitles.length})` : ""}`], ["timeline", "Timeline"], ["terminal", "Terminal"], ["qa", "QA / Guardian"] ] as Array<[ProductionView, string]>).map(([id, label]) => <button type="button" className={`wtab ${view === id ? "active" : ""}`} onClick={() => setView(id)} key={id}>{label}{id === "terminal" && terminalLive && <span className="live-dot" />}</button>)}</nav>
      <div className="mission-studio-body"><main className="mission-canvas">
        {view === "overview" && <Overview request={selectedRequest} artifact={selectedArtifact} artifacts={artifacts} artifactVersion={artifactVersion} setArtifactVersion={setArtifactVersion} channelId={channelId} channelName={activeChannel?.name || "this channel"} availableProduction={recent} openProduction={(item) => void openRecent(`${item.channelId}:${item.request.id}`)} scenes={scenes} narration={narration} subtitles={subtitles} plan={plan} selectedSceneId={selectedSceneId} setSelectedSceneId={setSelectedSceneId} loading={loading} onRender={() => selectedRequest && void perform(() => renderProduction(channelId, selectedRequest.id, `render-${Date.now()}`))} />}
        {view === "script" && <div className="studio-tab-pane"><PanelHeading eyebrow="SCRIPT STUDIO" title="Read-only production script" detail={`${scenes.length} scene blocks`} />{scenes.length ? <div className="script-blocks">{scenes.map((scene) => <button type="button" key={scene.id} className="script-block" onClick={() => { setSelectedSceneId(scene.id); setView("overview"); }}><span>Scene {scene.scene_order} · {scene.scene_type}</span><p>{scene.narration_text}</p>{scene.visual_intent && <small>{scene.visual_intent}</small>}</button>)}</div> : <PanelEmpty title="No script blocks available" description="This production has no persisted scene narration yet." />}</div>}
        {view === "storyboard" && <div className="studio-tab-pane"><PanelHeading eyebrow="STORYBOARD" title="Scene planning" detail={`${scenes.length} persisted scenes`} />{scenes.length ? <StoryboardPanel scenes={scenes} onSelectScene={(scene) => { setSelectedSceneId(scene.id); setView("overview"); }} /> : <PanelEmpty title="No storyboard available" description="Scene planning data has not been persisted for this production." />}</div>}
        {view === "subtitle" && <div className="studio-tab-pane"><PanelHeading eyebrow="SUBTITLE" title="Timed subtitle cues" detail={`${subtitles.length} persisted cues`} /><SubtitlePanel subtitles={subtitles} request={selectedRequest} capabilities={renderCapabilities} busy={busy} onApply={applySubtitleStyle} /></div>}
        {view === "timeline" && <div className="studio-tab-pane"><PanelHeading eyebrow="TIMELINE" title="Track detail" detail="Read-only persisted timing" />{scenes.length || narration.length || subtitles.length ? <TimelinePanel scenes={scenes} narration={narration} subtitles={subtitles} /> : <PanelEmpty title="Timeline has no clips" description="Tracks will appear when real scene, narration, or subtitle timing exists." />}</div>}
        {view === "terminal" && <TerminalPanel events={visibleEvents} totalEvents={terminalEvents.length} live={terminalLive} setLive={setTerminalLive} autoScroll={terminalAutoScroll} setAutoScroll={setTerminalAutoScroll} search={terminalSearch} setSearch={setTerminalSearch} filter={terminalFilter} setFilter={setTerminalFilter} onCopy={async () => { try { await navigator.clipboard.writeText(visibleEvents.map((event) => `${displayTime(event.timestamp)} ${event.domain.toUpperCase()} ${event.status} ${event.message}`).join("\n")); setTerminalNotice(`${visibleEvents.length} visible entries copied`); } catch { setTerminalNotice("Clipboard permission was unavailable"); } }} onClear={() => { setTerminalClearedAt(Date.now()); setTerminalNotice("View cleared locally; persisted records were not changed"); }} notice={terminalNotice} endRef={terminalEndRef} />}
        {view === "qa" && <div className="studio-tab-pane"><PanelHeading eyebrow="QA / GUARDIAN" title="Production assurance" detail={qa?.status ? safeStatus(qa.status) : "No result"} /><ProductionQAPanel qa={qa} renderProvenance={resolveArtifactProvenance(selectedRequest)} /></div>}
      </main><Inspector request={selectedRequest} scene={selectedScene} artifact={selectedArtifact} plan={plan} latestJob={latestJob} qa={qa} assets={assets} narration={narration} subtitles={subtitles} busy={busy} onClearScene={() => setSelectedSceneId(null)} onPrepare={() => selectedRequest && void perform(() => prepareProduction(channelId, selectedRequest.id))} onRender={() => selectedRequest && void perform(() => renderProduction(channelId, selectedRequest.id, `render-${Date.now()}`))} onPublish={() => setPublishOpen(true)} />
      </div>
    </section>
    {publishOpen && selectedArtifact && activeChannel && selectedRequest && <PublishPreparationModal channel={activeChannel} productionRequest={selectedRequest} artifact={selectedArtifact} isOpen={publishOpen} onClose={() => setPublishOpen(false)} />}
  </div>;
}

export default function GlobalProductionPage() { return <ProductionWorkspace />; }

function PanelHeading({ eyebrow, title, detail }: { eyebrow: string; title: string; detail: string }) { return <div className="studio-panel-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div><span>{detail}</span></div>; }
function PanelEmpty({ title, description }: { title: string; description: string }) { return <div className="studio-panel-empty"><div className="empty-glyph">◇</div><strong>{title}</strong><p>{description}</p></div>; }

function Overview({ request, artifact, artifacts, artifactVersion, setArtifactVersion, channelId, channelName, availableProduction, openProduction, scenes, narration, subtitles, plan, selectedSceneId, setSelectedSceneId, loading, onRender }: { request: ProductionRequest | null; artifact: MediaArtifact | null; artifacts: MediaArtifact[]; artifactVersion: number | null; setArtifactVersion: (version: number) => void; channelId: string; channelName: string; availableProduction: RecentProduction[]; openProduction: (item: RecentProduction) => void; scenes: ProductionScene[]; narration: NarrationSegment[]; subtitles: SubtitleCue[]; plan: RenderPlan | null; selectedSceneId: string | null; setSelectedSceneId: (id: string | null) => void; loading: boolean; onRender: () => void; }) {
  const durationMs = artifact?.duration_ms ?? plan?.total_duration_ms ?? scenes.reduce((sum, scene) => sum + scene.estimated_duration_ms, 0);
  return <div className="overview-workspace">{!request && <section className="production-empty-notice"><div><span className="eyebrow">NO PRODUCTION IN THIS CHANNEL</span><h2>No production for {channelName}</h2><p>The Studio remains available. Open a persisted production from another channel below.</p></div><div className="available-production-list">{availableProduction.slice(0, 4).map((item) => <button type="button" key={`${item.channelId}:${item.request.id}`} onClick={() => openProduction(item)}><span><b>{item.channelName}</b><small>{safeStatus(item.request.outcome || item.request.status)} · {displayDate(item.request.created_at)}</small></span><span className={item.hasVideo ? "has-video" : ""}>{item.hasVideo ? "Video ready" : `${item.artifactCount} artifacts`} →</span></button>)}</div></section>}<section className="preview-panel"><PanelHeading eyebrow="VIDEO PREVIEW" title={artifact ? `Artifact v${artifact.version}` : "Program monitor"} detail={request ? `${request.target_width}×${request.target_height} · ${request.fps} fps` : "Awaiting production"} />
    <div className="preview-stage">{artifact && request ? <video controls preload="metadata" src={getMediaArtifactStreamUrl(channelId, request.id, artifact.id)}>Your browser cannot play this persisted video artifact.</video> : <div className="preview-empty"><button type="button" className="preview-play" disabled={!request || loading} onClick={onRender}>▶</button><strong>{request ? "No rendered video artifact" : "No production selected"}</strong><span>{request ? "Prepare or render this persisted request to create a preview." : "The Studio remains ready. Select a real production above."}</span>{!request && <Link href={channelId ? `/channels/${channelId}/content` : "/channels"} className="btn btn-sm">Open content workspace</Link>}</div>}</div>
    <div className="preview-transport"><button type="button" disabled={!artifact}>▶</button><span>00:00</span><div className="transport-line"><span /></div><span>{durationMs ? `${(durationMs / 1000).toFixed(1)}s` : "--:--"}</span>{artifacts.length > 1 && <select value={artifactVersion ?? ""} onChange={(event) => setArtifactVersion(Number(event.target.value))}>{newest(artifacts).map((item) => <option key={item.id} value={item.version}>v{item.version}{item.is_current ? " · current" : ""}</option>)}</select>}</div>
  </section><SceneTimeline scenes={scenes} narration={narration} subtitles={subtitles} durationMs={durationMs} selectedSceneId={selectedSceneId} setSelectedSceneId={setSelectedSceneId} /></div>;
}

function SceneTimeline({ scenes, narration, subtitles, durationMs, selectedSceneId, setSelectedSceneId }: { scenes: ProductionScene[]; narration: NarrationSegment[]; subtitles: SubtitleCue[]; durationMs: number; selectedSceneId: string | null; setSelectedSceneId: (id: string | null) => void; }) {
  const marks = Array.from({ length: 6 }, (_, index) => Math.round((durationMs / 1000 / 5) * index));
  return <section className="scene-timeline"><PanelHeading eyebrow="SCENE TIMELINE" title="Editorial tracks" detail={scenes.length ? `${scenes.length} scenes · ${(durationMs / 1000).toFixed(1)} sec` : "No persisted clips"} /><div className="timeline-ruler"><span>TRACK</span>{marks.map((mark, index) => <span key={index}>{String(Math.floor(mark / 60)).padStart(2, "0")}:{String(mark % 60).padStart(2, "0")}</span>)}</div><Track label="Video">{scenes.length ? scenes.map((scene) => <button type="button" key={scene.id} className={`timeline-clip video ${selectedSceneId === scene.id ? "selected" : ""}`} style={{ flexGrow: Math.max(scene.estimated_duration_ms, 1) }} onClick={() => setSelectedSceneId(selectedSceneId === scene.id ? null : scene.id)}><b>S{scene.scene_order}</b><span>{scene.scene_type}</span><small>{(scene.estimated_duration_ms / 1000).toFixed(1)}s</small></button>) : <span className="track-empty">No scene clips</span>}</Track>{narration.length > 0 && <Track label="Voice"><button type="button" className="timeline-clip voice"><b>Narration</b><span>{narration.length} segments</span></button></Track>}{subtitles.length > 0 && <Track label="Subtitle"><button type="button" className="timeline-clip subtitle"><b>Captions</b><span>{subtitles.length} cues</span></button></Track>}</section>;
}
function Track({ label, children }: { label: string; children: React.ReactNode }) { return <div className="timeline-track"><span className="timeline-track-label">{label}</span><div className="timeline-track-clips">{children}</div></div>; }

function Inspector({ request, scene, artifact, plan, latestJob, qa, assets, narration, subtitles, busy, onClearScene, onPrepare, onRender, onPublish }: { request: ProductionRequest | null; scene: ProductionScene | null; artifact: MediaArtifact | null; plan: RenderPlan | null; latestJob: ProductionRenderJob | null; qa: ProductionQAResult | null; assets: ProductionAsset[]; narration: NarrationSegment[]; subtitles: SubtitleCue[]; busy: boolean; onClearScene: () => void; onPrepare: () => void; onRender: () => void; onPublish: () => void; }) {
  const sceneNarration = scene ? narration.find((item) => item.scene_id === scene.id) : null;
  const sceneSubtitles = scene ? subtitles.filter((item) => item.scene_id === scene.id) : [];
  return <aside className="production-inspector"><div className="inspector-heading"><div><span className="eyebrow">INSPECTOR</span><h2>{scene ? `Scene ${scene.scene_order}` : request ? "Production context" : "No selection"}</h2></div>{scene && <button type="button" onClick={onClearScene}>×</button>}</div>{scene ? <div className="inspector-body"><Fact label="Strategy / type" value={scene.scene_type} /><Fact label="Duration" value={`${(scene.estimated_duration_ms / 1000).toFixed(1)} sec`} /><Fact label="Narration" value={sceneNarration?.text || scene.narration_text || "No narration"} long /><Fact label="Visual direction" value={scene.visual_intent || "No visual direction persisted"} long /><Fact label="Transition" value={[scene.transition_in, scene.transition_out].filter(Boolean).join(" → ") || "Not specified"} /><Fact label="Asset / provenance" value={assets.length ? `${assets.length} production assets; scene binding unavailable` : "No production assets persisted"} /><Fact label="Subtitle context" value={sceneSubtitles.length ? `${sceneSubtitles.length} timed cues` : "No subtitle cues for this scene"} /><Fact label="QA status" value={qa?.status || "No production QA result"} /><details className="technical-details"><summary>Technical details</summary><code>Scene: {scene.id}</code><code>Production: {request?.id}</code></details></div> : request ? <div className="inspector-body"><Fact label="Production state" value={safeStatus(request.outcome || request.status)} /><Fact label="Render state" value={latestJob?.state || (artifact ? "SUCCEEDED" : "Not started")} /><Fact label="Artifact" value={artifact ? `${artifact.artifact_type.replaceAll("_", " ")} v${artifact.version}` : "No artifact"} /><Fact label="Duration" value={artifact?.duration_ms ? `${(artifact.duration_ms / 1000).toFixed(1)} sec` : plan?.total_duration_ms ? `${(plan.total_duration_ms / 1000).toFixed(1)} sec` : "Unavailable"} /><Fact label="QA / Guardian" value={qa?.status || "Not run"} /><Fact label="Publish readiness" value={artifact && (qa?.status === "PASSED" || qa?.status === "PASSED_WITH_WARNINGS") ? "Artifact and QA evidence available" : "Awaiting eligible artifact and QA"} /><Fact label="Provenance" value={`${assets.length} persisted assets · artifact ${artifact ? "hash recorded" : "not available"}`} /><div className="inspector-actions"><button type="button" className="btn" disabled={busy || request.status === "RUNNING"} onClick={onPrepare}>Prepare plan</button><button type="button" className="btn primary" disabled={busy || request.status === "RUNNING"} onClick={onRender}>Render video</button>{artifact && <button type="button" className="btn success" onClick={onPublish}>Prepare publish</button>}</div><details className="technical-details"><summary>Technical details</summary><code>Production: {request.id}</code><code>Channel: {request.channel_id}</code>{latestJob && <code>Job: {latestJob.id}</code>}{artifact && <code>Artifact: {artifact.id}</code>}</details></div> : <div className="inspector-body"><PanelEmpty title="Nothing selected" description="Choose a real production to inspect its state, artifact, QA, and provenance." /><Fact label="Production" value="No request in active channel" /><Fact label="Render" value="No render selected" /><Fact label="QA / Guardian" value="No result selected" /></div>}</aside>;
}
function Fact({ label, value, long = false }: { label: string; value: string; long?: boolean }) { return <div className={`inspector-fact ${long ? "long" : ""}`}><span>{label}</span><strong>{value}</strong></div>; }

function TerminalPanel({ events, totalEvents, live, setLive, autoScroll, setAutoScroll, search, setSearch, filter, setFilter, onCopy, onClear, notice, endRef }: { events: TerminalEvent[]; totalEvents: number; live: boolean; setLive: (value: boolean) => void; autoScroll: boolean; setAutoScroll: (value: boolean) => void; search: string; setSearch: (value: string) => void; filter: TerminalFilter; setFilter: (value: TerminalFilter) => void; onCopy: () => void; onClear: () => void; notice: string | null; endRef: React.RefObject<HTMLDivElement | null>; }) {
  return <section className="activity-terminal"><header><div><span className="eyebrow">READ-ONLY ACTIVITY CONSOLE</span><h2>Production Terminal</h2><p>Scoped to the active channel and selected production · polls every {TERMINAL_POLL_MS / 1000} seconds while live</p></div><button type="button" className={`terminal-live ${live ? "is-live" : ""}`} onClick={() => setLive(!live)}><span />{live ? "LIVE" : "PAUSED"}</button></header><div className="terminal-toolbar"><label className="terminal-search"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search activity…" /></label><label className="terminal-check"><input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} /> Auto-scroll</label><button type="button" onClick={onCopy} disabled={!events.length}>Copy visible</button><button type="button" onClick={onClear} disabled={!events.length}>Clear view</button></div><div className="terminal-filters">{terminalFilters.map((item) => <button type="button" key={item} className={filter === item ? "active" : ""} onClick={() => setFilter(item)}>{item}</button>)}</div>{notice && <div className="terminal-notice">{notice}</div>}<div className="terminal-screen" aria-live="polite">{events.length ? events.map((event) => <div className={`terminal-entry severity-${event.severity.toLowerCase()}`} key={event.key}><time>{displayTime(event.timestamp)}</time><b>{event.domain.toUpperCase()}</b><strong>{event.status}</strong><span>{event.message}</span><details className="technical-details"><summary>ref</summary><code>{event.entityRef}</code></details></div>) : <div className="terminal-empty"><span>omega/activity</span><strong>{totalEvents ? "No entries match this view" : "No authoritative activity available"}</strong><p>{totalEvents ? "Adjust search or filters. Clear view never deletes persisted records." : "The console will show persisted production, render, asset, and QA events when available."}</p></div>}<div ref={endRef} /></div></section>;
}
