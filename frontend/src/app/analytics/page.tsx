"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getChannelAnalytics,
  getVideoAnalytics,
  listPublishIntents,
  type ChannelAnalyticsSummary,
  type PublishIntent,
  type VideoAnalyticsSummary,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { AnalyticsPerformanceCard } from "@/components/AnalyticsPerformanceCard";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import { Alert, EmptyState, LoadingState, PageHeader, PageSection } from "@/components/ui";

export default function AnalyticsPage() {
  const { selectedChannelId, selectedChannel } = useOperatorContext();
  const [channelAnalytics, setChannelAnalytics] = useState<ChannelAnalyticsSummary | null>(null);
  const [videoAnalytics, setVideoAnalytics] = useState<VideoAnalyticsSummary | null>(null);
  const [intents, setIntents] = useState<PublishIntent[]>([]);
  const [selectedIntentId, setSelectedIntentId] = useState("");
  const [loading, setLoading] = useState(true);
  const [videoLoading, setVideoLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [videoError, setVideoError] = useState<string | null>(null);

  const selectIntent = useCallback(async (intentId: string) => {
    setSelectedIntentId(intentId);
    setVideoError(null);
    if (!intentId) {
      setVideoAnalytics(null);
      return;
    }
    setVideoLoading(true);
    try {
      setVideoAnalytics(await getVideoAnalytics(intentId));
    } catch (requestError: unknown) {
      setVideoAnalytics(null);
      setVideoError(requestError instanceof Error ? requestError.message : "Video analytics are unavailable.");
    } finally {
      setVideoLoading(false);
    }
  }, []);

  const loadData = useCallback(async () => {
    if (!selectedChannelId) {
      setLoading(false);
      setChannelAnalytics(null);
      setIntents([]);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const [analytics, publishIntents] = await Promise.all([
        getChannelAnalytics(selectedChannelId),
        listPublishIntents({ channel_id: selectedChannelId }),
      ]);
      setChannelAnalytics(analytics);
      setIntents(publishIntents);
      const activeIntent = publishIntents.find((intent) => intent.state === "PUBLISHED") || publishIntents[0];
      await selectIntent(activeIntent?.id || "");
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load analytics.");
    } finally {
      setLoading(false);
    }
  }, [selectIntent, selectedChannelId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  return (
    <div className="ui-page-stack">
      <ChannelContextBar currentTab="analytics" />
      <PageHeader
        eyebrow="Intelligence"
        title="Performance intelligence"
        description={selectedChannel ? `Authoritative channel and published-content analytics for ${selectedChannel.name}.` : "Select a channel to inspect analytics."}
        actions={<button type="button" className="btn btn-secondary" onClick={() => void loadData()} disabled={loading}>Refresh</button>}
      />

      {error && <Alert tone="danger" title="Analytics unavailable" actions={<button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadData()}>Retry</button>}>{error}</Alert>}

      {loading ? <LoadingState title="Loading analytics" /> : !selectedChannelId ? (
        <EmptyState title="No channel selected" description="Select an available channel before loading analytics." />
      ) : !error && (
        <>
          <PageSection title="Channel snapshot" description="Lifetime values returned by the channel analytics endpoint.">
            {channelAnalytics ? (
              <div className="dash-metrics-grid">
                <div className="dash-metric-card">
                  <div className="metric-k">Total Views</div>
                  <div className="metric-v">{channelAnalytics.total_views?.toLocaleString() ?? "—"}</div>
                  <div className="metric-sub">Lifetime audience impressions</div>
                </div>
                <div className="dash-metric-card">
                  <div className="metric-k">Subscribers</div>
                  <div className="metric-v">{channelAnalytics.subscriber_count?.toLocaleString() ?? "—"}</div>
                  <div className="metric-sub" style={{ color: "var(--success)" }}>Channel community size</div>
                </div>
                <div className="dash-metric-card">
                  <div className="metric-k">Public Videos</div>
                  <div className="metric-v">{channelAnalytics.video_count?.toLocaleString() ?? "—"}</div>
                  <div className="metric-sub">Published catalog</div>
                </div>
                <div className="dash-metric-card">
                  <div className="metric-k">Watch Time</div>
                  <div className="metric-v">
                    {channelAnalytics.aggregate_watch_time_seconds == null ? "—" : `${(channelAnalytics.aggregate_watch_time_seconds / 3600).toFixed(1)}h`}
                  </div>
                  <div className="metric-sub">Cumulative viewer engagement</div>
                </div>
              </div>
            ) : <EmptyState title="No channel analytics" description="The analytics service returned no channel summary." />}
          </PageSection>

          <PageSection title="Published content performance" description="Select a persisted publish intent to inspect its measured performance.">
            {intents.length === 0 ? (
              <EmptyState title="No publish intents" description="No published content is available for analytics." />
            ) : (
              <>
                <div className="ui-filter-field ui-select-field">
                  <label htmlFor="analytics-intent">Content item</label>
                  <select id="analytics-intent" className="select" value={selectedIntentId} onChange={(event) => void selectIntent(event.target.value)}>
                    {intents.map((intent) => <option key={intent.id} value={intent.id}>{intent.title} · {intent.state} · revision {intent.revision_number}</option>)}
                  </select>
                </div>
                {videoError && <Alert tone="danger" title="Video analytics unavailable">{videoError}</Alert>}
                {videoLoading ? <LoadingState title="Loading content analytics" /> : !videoError && (
                  <div className="ui-chart-container">
                    <AnalyticsPerformanceCard analytics={videoAnalytics} onRefresh={() => void selectIntent(selectedIntentId)} />
                  </div>
                )}
              </>
            )}
          </PageSection>
        </>
      )}
    </div>
  );
}
