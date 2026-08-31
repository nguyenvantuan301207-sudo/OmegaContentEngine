"use client";

import React, { useState } from "react";
import { refreshVideoAnalytics, VideoAnalyticsSummary } from "@/lib/api";
import { toFiniteNumber } from "@/lib/formatters";

interface AnalyticsPerformanceCardProps {
  analytics: VideoAnalyticsSummary | null;
  onRefresh?: () => void;
}

export function AnalyticsPerformanceCard({
  analytics,
  onRefresh,
}: AnalyticsPerformanceCardProps) {
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!analytics) {
    return (
      <div className="rounded-xl border border-border/40 bg-card p-6 shadow-sm">
        <h3 className="text-lg font-semibold tracking-tight text-foreground">Content Analytics</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          No analytics data available yet. Content must be published and verified on YouTube before performance tracking begins.
        </p>
      </div>
    );
  }

  const handleRefresh = async () => {
    try {
      setRefreshing(true);
      setError(null);
      await refreshVideoAnalytics(analytics.publish_intent_id);
      if (onRefresh) onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to trigger on-demand refresh.");
    } finally {
      setRefreshing(false);
    }
  };

  const getMetricDisplay = (name: string, formatFn?: (val: number) => string) => {
    const item = analytics.latest_metrics[name];
    if (!item) {
      return { valueText: "Pending", quality: "NOT_READY", isMissing: true };
    }
    if (item.value === null || item.value === undefined) {
      return { valueText: item.quality, quality: item.quality, isMissing: true };
    }
    const num = toFiniteNumber(item.value);
    if (num === null) {
      return { valueText: String(item.value), quality: item.quality, isMissing: false };
    }
    const formatted = formatFn ? formatFn(num) : num.toLocaleString();
    return { valueText: formatted, quality: item.quality, isMissing: false };
  };

  const views = getMetricDisplay("views");
  const watchTime = getMetricDisplay("watch_time_seconds", (secs) => {
    const hours = (secs / 3600).toFixed(1);
    return `${hours} hrs`;
  });
  const ctr = getMetricDisplay("impression_ctr_percent", (val) => `${val.toFixed(2)}%`);
  const avgViewPct = getMetricDisplay("average_percentage_viewed", (val) => `${val.toFixed(1)}%`);
  const engagement = getMetricDisplay("engagement_rate_percent", (val) => `${val.toFixed(2)}%`);
  const likes = getMetricDisplay("likes");
  const comments = getMetricDisplay("comments");

  return (
    <div className="rounded-xl border border-border/40 bg-card p-6 shadow-sm">
      <div className="flex flex-col justify-between gap-4 border-b border-border/40 pb-4 sm:flex-row sm:items-center">
        <div>
          <h3 className="text-lg font-semibold tracking-tight text-foreground">
            Authoritative Performance Analytics
          </h3>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>Video ID: {analytics.provider_video_id}</span>
            <span>•</span>
            <span>Status: {analytics.asset_status}</span>
            <span>•</span>
            <span>Phase: {analytics.lifecycle_phase}</span>
            <span>•</span>
            <span>
              Last Synced:{" "}
              {analytics.last_polled_at
                ? new Date(analytics.last_polled_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                : "Never"}
            </span>
          </div>
        </div>

        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="inline-flex items-center justify-center rounded-lg border border-border/60 bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted/80 disabled:opacity-50"
        >
          {refreshing ? "Refreshing..." : "↻ Refresh Now"}
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {/* Views */}
        <div className="rounded-lg border border-border/30 bg-muted/20 p-3">
          <span className="text-xs font-medium text-muted-foreground">Views</span>
          <p className="mt-1 text-xl font-bold tracking-tight text-foreground">
            {views.valueText}
          </p>
          <span className="text-[10px] text-muted-foreground">Quality: {views.quality}</span>
        </div>

        {/* Watch Time */}
        <div className="rounded-lg border border-border/30 bg-muted/20 p-3">
          <span className="text-xs font-medium text-muted-foreground">Watch Time</span>
          <p className="mt-1 text-xl font-bold tracking-tight text-foreground">
            {watchTime.valueText}
          </p>
          <span className="text-[10px] text-muted-foreground">Quality: {watchTime.quality}</span>
        </div>

        {/* Average View Percentage */}
        <div className="rounded-lg border border-border/30 bg-muted/20 p-3">
          <span className="text-xs font-medium text-muted-foreground">Avg View %</span>
          <p className="mt-1 text-xl font-bold tracking-tight text-foreground">
            {avgViewPct.valueText}
          </p>
          <span className="text-[10px] text-muted-foreground">Quality: {avgViewPct.quality}</span>
        </div>

        {/* Impression CTR */}
        <div className="rounded-lg border border-border/30 bg-muted/20 p-3">
          <span className="text-xs font-medium text-muted-foreground">Impression CTR</span>
          <p className="mt-1 text-xl font-bold tracking-tight text-foreground">
            {ctr.valueText}
          </p>
          <span className="text-[10px] text-muted-foreground">Quality: {ctr.quality}</span>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-4">
        <div className="rounded-lg border border-border/30 bg-muted/10 p-3">
          <span className="text-xs font-medium text-muted-foreground">Likes</span>
          <p className="mt-1 text-base font-semibold text-foreground">{likes.valueText}</p>
        </div>
        <div className="rounded-lg border border-border/30 bg-muted/10 p-3">
          <span className="text-xs font-medium text-muted-foreground">Comments</span>
          <p className="mt-1 text-base font-semibold text-foreground">{comments.valueText}</p>
        </div>
        <div className="rounded-lg border border-border/30 bg-muted/10 p-3">
          <span className="text-xs font-medium text-muted-foreground">Engagement Rate</span>
          <p className="mt-1 text-base font-semibold text-foreground">{engagement.valueText}</p>
        </div>
      </div>
    </div>
  );
}
