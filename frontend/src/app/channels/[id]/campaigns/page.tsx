"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getChannel, listContentCampaigns, type Channel, type ContentCampaign } from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import { Alert, EmptyState, ErrorState, LoadingState, PageHeader, PageSection, StatusBadge } from "@/components/ui";
import { TechnicalDetails } from "@/components/workflow/WorkflowPrimitives";

export default function CampaignListPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: channelId } = use(params);
  const { setSelectedChannelId } = useOperatorContext();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [campaigns, setCampaigns] = useState<ContentCampaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [channelData, campaignData] = await Promise.all([getChannel(channelId), listContentCampaigns(channelId)]);
      setChannel(channelData);
      setCampaigns(campaignData);
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Unable to load campaigns.");
    } finally { setLoading(false); }
  }, [channelId]);
  useEffect(() => { setSelectedChannelId(channelId); void load(); }, [channelId, load, setSelectedChannelId]);
  const archived = channel?.state === "ARCHIVED";
  return <div className="workflow-page ui-page-stack">
    <ChannelContextBar currentTab="campaigns" />
    <PageHeader eyebrow="Channel workflow · Step 2" title="Campaigns" description="Immutable plans built from finalized canonical selections."
      actions={!archived && <Link className="btn btn-primary" href={`/channels/${channelId}/campaigns/new`}>Create campaign</Link>} />
    {archived && <Alert tone="warning">This archived channel is read-only. Historical campaigns remain visible.</Alert>}
    {error && <ErrorState title="Campaigns unavailable" description={error} action={<button type="button" className="btn btn-secondary" onClick={() => void load()}>Retry</button>} />}
    {loading ? <LoadingState title="Loading campaigns" /> : <PageSection title="Campaign plans" description="Materialization and Mission handoff are controlled from each campaign detail page.">
      {campaigns.length === 0 ? <EmptyState title="No campaigns yet" description="Finalize a selection run, then compose a campaign." /> : <div className="workflow-card-grid">
        {campaigns.map((campaign) => <article className="workflow-card" key={campaign.id}>
          <div className="workflow-card-header"><h3>{campaign.title}</h3><StatusBadge>{campaign.status}</StatusBadge></div>
          <p>{campaign.objective || "No objective supplied."}</p>
          <p>Priority {campaign.priority} · {campaign.item_count} items · Created {new Date(campaign.created_at).toLocaleString()}</p>
          <p className="small muted">Pinned DNA revision: {campaign.channel_dna_revision_id}</p>
          <Link className="btn btn-secondary btn-sm" href={`/channels/${channelId}/campaigns/${campaign.id}`}>View campaign</Link>
          <TechnicalDetails data={{ campaign_id: campaign.id, plan_checksum: campaign.plan_checksum, created_by: campaign.created_by }} />
        </article>)}
      </div>}
    </PageSection>}
  </div>;
}
