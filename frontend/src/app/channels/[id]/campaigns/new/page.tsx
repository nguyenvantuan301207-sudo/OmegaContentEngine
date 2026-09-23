"use client";

import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ApiError, createContentCampaign, getChannel, getContentSelectionRun, listContentSelectionRuns,
  type Channel, type ContentCampaignItemInput, type ContentSelectionRun, type ContentType,
} from "@/lib/api";
import { isSelectionRunCampaignEligible, moveCampaignItem, normalizePlannedReleaseInput, reuseSubmissionKey, selectedDecision, validateCampaignDraftItems } from "@/lib/content-workflow";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import { Alert, EmptyState, ErrorState, FormField, LoadingState, PageHeader, PageSection } from "@/components/ui";

interface DraftItem { selectionRunId: string; contentType: ContentType; localRelease: string }

export default function NewCampaignPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: channelId } = use(params);
  const router = useRouter();
  const query = useSearchParams();
  const preselectedRunId = query.get("selection_run_id");
  const { setSelectedChannelId } = useOperatorContext();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [runs, setRuns] = useState<ContentSelectionRun[]>([]);
  const [items, setItems] = useState<DraftItem[]>([]);
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [priority, setPriority] = useState(1);
  const [actor, setActor] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingKey = useRef<string | null>(null);

  useEffect(() => {
    pendingKey.current = null;
    setItems([]);
    setTitle("");
    setObjective("");
    setPriority(1);
    setActor("");
  }, [channelId, preselectedRunId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [channelData, listed] = await Promise.all([getChannel(channelId), listContentSelectionRuns(channelId, 100)]);
      const extra = preselectedRunId && !listed.some((run) => run.id === preselectedRunId)
        ? await getContentSelectionRun(channelId, preselectedRunId) : null;
      const all = extra ? [extra, ...listed] : listed;
      setChannel(channelData);
      setRuns(all);
      if (preselectedRunId && all.some((run) => run.id === preselectedRunId && isSelectionRunCampaignEligible(run))) {
        setItems((current) => current.length ? current : [{ selectionRunId: preselectedRunId, contentType: "YOUTUBE_LONGFORM", localRelease: "" }]);
      }
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Unable to load finalized selections.");
    } finally { setLoading(false); }
  }, [channelId, preselectedRunId]);
  useEffect(() => { setSelectedChannelId(channelId); void load(); }, [channelId, load, setSelectedChannelId]);

  const eligible = runs.filter(isSelectionRunCampaignEligible);
  const archived = channel?.state === "ARCHIVED";
  const validation = useMemo(() => {
    if (!title.trim() || !actor.trim()) return "Title and operator actor are required.";
    if (!Number.isInteger(priority) || priority < 1 || priority > 100) return "Priority must be between 1 and 100.";
    return validateCampaignDraftItems(items, runs);
  }, [actor, items, priority, runs, title]);

  function updateItems(next: DraftItem[]) { pendingKey.current = null; setItems(next); }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (archived || validation) return;
    setBusy(true);
    setError(null);
    try {
      pendingKey.current = reuseSubmissionKey(pendingKey.current, () => crypto.randomUUID());
      const payloadItems: ContentCampaignItemInput[] = items.map((item) => ({
        selection_run_id: item.selectionRunId,
        target_content_type: item.contentType,
        planned_release_at: normalizePlannedReleaseInput(item.localRelease),
      }));
      const campaign = await createContentCampaign(channelId, {
        title: title.trim(), objective: objective.trim() || null, priority, created_by: actor.trim(),
        idempotency_key: pendingKey.current, items: payloadItems,
      });
      router.push(`/channels/${channelId}/campaigns/${campaign.id}`);
    } catch (cause: unknown) {
      if (cause instanceof ApiError && cause.status === 409) setError(`Campaign conflict: ${cause.message}. A selection run may already belong to another campaign.`);
      else setError(cause instanceof Error ? cause.message : "Unable to create campaign.");
    } finally { setBusy(false); }
  }

  return <div className="workflow-page ui-page-stack">
    <ChannelContextBar currentTab="campaigns" />
    <PageHeader eyebrow="Channel workflow · Step 2" title="Create campaign" description="Compose an immutable ordered plan from finalized selection runs." />
    {archived && <Alert tone="warning">This archived channel is read-only; campaign creation is disabled.</Alert>}
    {error && <ErrorState title="Campaign creation unavailable" description={error} action={<button type="button" className="btn btn-secondary" onClick={() => void load()}>Retry loading selections</button>} />}
    {loading ? <LoadingState title="Loading finalized selections" /> : <form onSubmit={(event) => void submit(event)} onChange={() => { pendingKey.current = null; }} className="ui-page-stack">
      <PageSection title="Campaign details">
        <div className="workflow-dialog-form">
          <FormField id="campaign-title" label="Title" required><input className="input" value={title} maxLength={255} onChange={(event) => setTitle(event.target.value)} /></FormField>
          <FormField id="campaign-objective" label="Objective"><textarea className="input" value={objective} maxLength={2000} onChange={(event) => setObjective(event.target.value)} /></FormField>
          <FormField id="campaign-priority" label="Priority" required><input className="input" type="number" min={1} max={100} value={priority} onChange={(event) => setPriority(Number(event.target.value))} /></FormField>
          <FormField id="campaign-actor" label="Created by" required><input className="input" value={actor} maxLength={100} onChange={(event) => setActor(event.target.value)} /></FormField>
        </div>
      </PageSection>
      <PageSection title="Ordered campaign items" description="List order is the backend plan order. Release dates are planning metadata, not schedule reservations.">
        {eligible.length === 0 ? <EmptyState title="No finalized selections" description="Finalize a canonical selection run on the Topics page first." /> : <>
          {items.map((item, index) => {
            const run = runs.find((candidate) => candidate.id === item.selectionRunId);
            return <article className="workflow-card" key={`${index}-${item.selectionRunId}`}>
              <h3>Item {index + 1}: {run ? selectedDecision(run)?.candidate_title_snapshot || "Selected candidate" : "Choose selection"}</h3>
              <div className="workflow-dialog-form">
                <FormField id={`campaign-run-${index}`} label="Finalized selection run" required><select className="select" value={item.selectionRunId} onChange={(event) => updateItems(items.map((entry, at) => at === index ? { ...entry, selectionRunId: event.target.value } : entry))}>
                  <option value="">Choose selection</option>{eligible.map((option) => <option key={option.id} value={option.id}>{selectedDecision(option)?.candidate_title_snapshot || option.id} · {new Date(option.selected_at || option.created_at).toLocaleDateString()}</option>)}
                </select></FormField>
                <FormField id={`campaign-type-${index}`} label="Content type" required><select className="select" value={item.contentType} onChange={(event) => updateItems(items.map((entry, at) => at === index ? { ...entry, contentType: event.target.value as ContentType } : entry))}>
                  <option value="YOUTUBE_LONGFORM">YouTube longform</option><option value="YOUTUBE_SHORT">YouTube short</option>
                </select></FormField>
                <FormField id={`campaign-release-${index}`} label="Planned release (local time)"><input className="input" type="datetime-local" value={item.localRelease} onChange={(event) => updateItems(items.map((entry, at) => at === index ? { ...entry, localRelease: event.target.value } : entry))} /></FormField>
              </div>
              <div className="ui-inline-actions">
                <button type="button" className="btn btn-secondary btn-sm" disabled={index === 0} onClick={() => updateItems(moveCampaignItem(items, index, index - 1))}>Move up</button>
                <button type="button" className="btn btn-secondary btn-sm" disabled={index === items.length - 1} onClick={() => updateItems(moveCampaignItem(items, index, index + 1))}>Move down</button>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => updateItems(items.filter((_, at) => at !== index))}>Remove item</button>
              </div>
            </article>;
          })}
          <button type="button" className="btn btn-secondary" disabled={archived || items.length >= 50 || eligible.length <= items.length} onClick={() => {
            const next = eligible.find((run) => !items.some((item) => item.selectionRunId === run.id));
            if (next) updateItems([...items, { selectionRunId: next.id, contentType: "YOUTUBE_LONGFORM", localRelease: "" }]);
          }}>Add selection item</button>
        </>}
      </PageSection>
      {validation && <Alert tone="warning">{validation}</Alert>}
      <button type="submit" className="btn btn-primary" disabled={busy || archived || Boolean(validation)}>{busy ? "Creating…" : "Create immutable campaign"}</button>
    </form>}
  </div>;
}
