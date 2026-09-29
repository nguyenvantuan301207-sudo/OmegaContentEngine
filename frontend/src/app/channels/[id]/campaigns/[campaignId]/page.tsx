"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError, campaignRuntimeAction, getChannel, getContentCampaign, getContentCampaignExecution, materializeContentCampaign, startMission,
  type Channel, type ContentCampaign, type ContentCampaignExecution, type ContentCampaignItemExecution,
} from "@/lib/api";
import { canMaterializeCampaign, canStartCampaignMission } from "@/lib/content-workflow";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import { Alert, ConfirmDialog, ErrorState, FormField, LoadingState, PageHeader, PageSection, StatusBadge } from "@/components/ui";
import { TechnicalDetails } from "@/components/workflow/WorkflowPrimitives";

export default function CampaignDetailPage({ params }: { params: Promise<{ id: string; campaignId: string }> }) {
  const { id: channelId, campaignId } = use(params);
  const { mode, setSelectedChannelId } = useOperatorContext();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [campaign, setCampaign] = useState<ContentCampaign | null>(null);
  const [execution, setExecution] = useState<ContentCampaignExecution | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actor, setActor] = useState("");
  const [materializeOpen, setMaterializeOpen] = useState(false);
  const [startBinding, setStartBinding] = useState<ContentCampaignItemExecution | null>(null);

  useEffect(() => {
    setMaterializeOpen(false);
    setStartBinding(null);
    setActor("");
  }, [channelId, campaignId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [channelData, campaignData] = await Promise.all([getChannel(channelId), getContentCampaign(channelId, campaignId)]);
      setChannel(channelData);
      setCampaign(campaignData);
      try { setExecution(await getContentCampaignExecution(channelId, campaignId)); }
      catch (cause: unknown) {
        if (cause instanceof ApiError && cause.status === 404) setExecution(null);
        else throw cause;
      }
      setError(null);
    } catch (cause: unknown) {
      setCampaign(null);
      setExecution(null);
      setError(cause instanceof Error ? cause.message : "Unable to load campaign lineage.");
    } finally { setLoading(false); }
  }, [campaignId, channelId]);
  useEffect(() => { setSelectedChannelId(channelId); void load(); }, [channelId, load, setSelectedChannelId]);
  const archived = channel?.state === "ARCHIVED";

  async function materialize() {
    if (!channel || !campaign || !actor.trim() || !canMaterializeCampaign(Boolean(execution), Boolean(archived))) return;
    setBusy(true); setError(null);
    try {
      setExecution(await materializeContentCampaign(channelId, campaign.id, actor.trim()));
      setMaterializeOpen(false);
      await load();
    } catch (cause: unknown) { setError(cause instanceof Error ? cause.message : "Unable to materialize campaign."); }
    finally { setBusy(false); }
  }

  async function startOne() {
    if (!startBinding || !canStartCampaignMission(startBinding)) return;
    setBusy(true); setError(null);
    try {
      await startMission(startBinding.mission_id);
      setStartBinding(null);
      await load();
    } catch (cause: unknown) { setError(cause instanceof Error ? cause.message : "Unable to start Mission."); }
    finally { setBusy(false); }
  }

  async function runtimeAction(action: "start" | "pause" | "resume" | "cancel" | "archive") {
    if (!campaign) return;
    setBusy(true); setError(null);
    try { await campaignRuntimeAction(channelId, campaign.id, action, actor.trim() || "OPERATOR"); await load(); }
    catch (cause: unknown) { setError(cause instanceof Error ? cause.message : `Unable to ${action} campaign.`); }
    finally { setBusy(false); }
  }

  return <div className="workflow-page ui-page-stack">
    <ChannelContextBar currentTab="campaigns" />
    <PageHeader eyebrow="Channel workflow · Step 2" title={campaign?.title || "Campaign details"}
      description="Immutable campaign plan and independent Mission handoff." actions={<Link className="btn btn-secondary" href={`/channels/${channelId}/campaigns`}>All campaigns</Link>} />
    {archived && <Alert tone="warning">This archived channel is read-only; materialization is disabled.</Alert>}
    {error && <ErrorState title="Campaign action unavailable" description={error} action={<button type="button" className="btn btn-secondary" onClick={() => void load()}>Retry</button>} />}
    {loading ? <LoadingState title="Loading campaign" /> : campaign && <>
      <PageSection title="Immutable plan" description={campaign.objective || "No objective supplied."}>
        <p>Priority {campaign.priority} · {campaign.item_count} items · <StatusBadge>{campaign.status}</StatusBadge> · Created {new Date(campaign.created_at).toLocaleString()}</p>
        <p className="small muted">Pinned DNA revision: {campaign.channel_dna_revision_id}</p>
        <p>Mode {campaign.orchestration_mode} · max concurrency {campaign.max_concurrent_missions ?? "legacy"}</p>
        {campaign.orchestration_mode === "LAZY_ADMISSION_V1" && !campaign.orchestration_enabled && <Alert tone="warning">Campaign orchestration is disabled. Growth actions are unavailable; cancellation and terminal convergence remain active.</Alert>}
        <div className="ui-inline-actions">
          {campaign.orchestration_mode === "LAZY_ADMISSION_V1" && campaign.status === "READY" && <button className="btn btn-primary" disabled={busy || !campaign.orchestration_enabled} onClick={() => void runtimeAction("start")}>Start</button>}
          {campaign.status === "RUNNING" && <button className="btn btn-secondary" disabled={busy} onClick={() => void runtimeAction("pause")}>Pause</button>}
          {campaign.status === "PAUSED" && <button className="btn btn-primary" disabled={busy || !campaign.orchestration_enabled} onClick={() => void runtimeAction("resume")}>Resume</button>}
          {(["READY", "RUNNING", "PAUSED", "CANCELLING"] as string[]).includes(campaign.status) && <button className="btn btn-secondary" disabled={busy} onClick={() => void runtimeAction("cancel")}>Cancel</button>}
          {(["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"] as string[]).includes(campaign.status) && !campaign.archived_at && <button className="btn btn-secondary" disabled={busy} onClick={() => void runtimeAction("archive")}>Archive</button>}
        </div>
        <div className="workflow-card-grid">{[...campaign.items].sort((a, b) => a.position - b.position).map((item) => <article className="workflow-card" key={item.id}>
          <h3>#{item.position} {item.candidate_title_snapshot}</h3>
          <p>{item.target_content_type.replaceAll("_", " ")} · {item.selection_mode} selection</p>
          <p>Admission: <StatusBadge>{item.admission_state}</StatusBadge>{item.materialization_error_code ? ` · ${item.materialization_error_code}` : ""}</p>
          {item.mission_id && <p>Mission: <StatusBadge>{item.mission_state || "UNKNOWN"}</StatusBadge> · <Link href={`/missions/${item.mission_id}`}>Open Mission</Link></p>}
          {item.sanitized_materialization_error && <p className="small muted">{item.sanitized_materialization_error}</p>}
          <p>Planned release: {item.planned_release_at ? new Date(item.planned_release_at).toLocaleString() : "Not set"}</p>
          <TechnicalDetails data={{ campaign_item_id: item.id, item_key: item.item_key, selection_run_id: item.selection_run_id, selection_decision_id: item.selection_decision_id, topic_candidate_id: item.topic_candidate_id }} />
        </article>)}</div>
        <TechnicalDetails data={{ campaign_id: campaign.id, plan_checksum: campaign.plan_checksum, created_by: campaign.created_by }} />
      </PageSection>

      <PageSection title="Mission handoff" description="Execution status MATERIALIZED records fan-out lineage; it does not represent campaign runtime success.">
        {!execution ? <>
          <Alert tone="info" title="Not yet materialized">No Missions have been created from this campaign.</Alert>
          {campaign.orchestration_mode === "LEGACY_UPFRONT" && channel && canMaterializeCampaign(false, Boolean(archived)) && <div className="workflow-dialog-form">
            <FormField id="materialize-actor" label="Operator actor" required><input className="input" value={actor} maxLength={100} onChange={(event) => setActor(event.target.value)} /></FormField>
            <button type="button" className="btn btn-primary" disabled={busy || !actor.trim()} onClick={() => setMaterializeOpen(true)}>Materialize missions</button>
          </div>}
        </> : <>
          <p><StatusBadge>{execution.status}</StatusBadge> · {execution.item_count} independent Missions · by {execution.materialized_by} · {new Date(execution.created_at).toLocaleString()}</p>
          <div className="workflow-card-grid">{[...execution.items].sort((a, b) => a.position - b.position).map((binding) => {
            const item = campaign.items.find((candidate) => candidate.id === binding.campaign_item_id);
            return <article className="workflow-card" key={binding.id}>
              <h3>#{binding.position} {item?.candidate_title_snapshot || "Campaign item"}</h3>
              <p>{item?.target_content_type.replaceAll("_", " ") || "Content"} · planned release {item?.planned_release_at ? new Date(item.planned_release_at).toLocaleString() : "not set"}</p>
              <p>Mission: <StatusBadge>{binding.mission_state}</StatusBadge> · Mission execution: <StatusBadge>{binding.mission_execution_state}</StatusBadge></p>
              <div className="ui-inline-actions">
                <Link className="btn btn-secondary btn-sm" href={`/missions/${binding.mission_id}`}>Open Mission</Link>
                {campaign.orchestration_mode === "LEGACY_UPFRONT" && canStartCampaignMission(binding) && <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => setStartBinding(binding)}>Start Mission</button>}
              </div>
              {mode === "DEVELOPMENT" && <TechnicalDetails data={{ mission_id: binding.mission_id, mission_execution_id: binding.mission_execution_id, selection_run_id: item?.selection_run_id, selection_decision_id: item?.selection_decision_id }} />}
            </article>;
          })}</div>
          <TechnicalDetails data={{ execution_id: execution.id, fanout_policy_name: execution.fanout_policy_name, fanout_policy_version: execution.fanout_policy_version, fanout_policy_checksum: execution.fanout_policy_checksum }} />
        </>}
      </PageSection>
    </>}
    <ConfirmDialog open={materializeOpen} title="Materialize independent Missions?"
      description="One planned Mission will be created per campaign item. Missions will not start; materialization performs no provider, render, or publish work."
      confirmLabel="Materialize missions" busy={busy} onCancel={() => setMaterializeOpen(false)} onConfirm={() => void materialize()} />
    <ConfirmDialog open={Boolean(startBinding)} title="Start this Mission?"
      description="Only this READY Mission will start through the existing Mission lifecycle API. Sibling Missions remain independent."
      confirmLabel="Start Mission" busy={busy} onCancel={() => setStartBinding(null)} onConfirm={() => void startOne()} />
  </div>;
}
