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
      <div className="card" style={{ padding: "2rem 1.5rem", textAlign: "center" }}>
        <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>📊</div>
        <h3 style={{ fontSize: "1.05rem", fontWeight: 600, color: "var(--text-primary)", marginBottom: "0.5rem" }}>
          Content Performance Analytics
        </h3>
        <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", maxWidth: "540px", margin: "0 auto", lineHeight: 1.5 }}>
          No analytics observation recorded for this content item yet. Performance tracking automatically begins via scheduled background sweeps after a video is published and verified on YouTube.
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
    <div className="card" style={{ padding: "1.25rem" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "1rem",
          borderBottom: "1px solid var(--border-subtle)",
          paddingBottom: "0.85rem",
          marginBottom: "1.25rem",
        }}
      >
        <div>
          <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "var(--text-primary)" }}>
            Authoritative Performance Analytics
          </h3>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.6rem", fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
            <span className="text-mono">Video ID: {analytics.provider_video_id}</span>
            <span>•</span>
            <span className="badge badge-active" style={{ fontSize: "0.68rem" }}>{analytics.asset_status}</span>
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
          className="btn btn-secondary btn-sm"
          style={{ fontSize: "0.78rem", display: "flex", alignItems: "center", gap: "0.35rem" }}
        >
          <span>↻</span> {refreshing ? "Refreshing..." : "Refresh Now"}
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: "0.75rem 1rem",
            background: "var(--status-danger-bg)",
            border: "1px solid var(--status-danger-border)",
            borderRadius: "var(--radius-sm)",
            fontSize: "0.8rem",
            color: "var(--status-danger)",
            marginBottom: "1rem",
          }}
        >
          {error}
        </div>
      )}

      {/* Primary KPI Grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: "0.85rem",
          marginBottom: "1rem",
        }}
      >
        {/* Views */}
        <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Views</span>
          <p style={{ fontSize: "1.4rem", fontWeight: 700, color: "var(--text-primary)", marginTop: "0.2rem" }}>
            {views.valueText}
          </p>
          <span className="text-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>Quality: {views.quality}</span>
        </div>

        {/* Watch Time */}
        <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Watch Time</span>
          <p style={{ fontSize: "1.4rem", fontWeight: 700, color: "var(--status-success)", marginTop: "0.2rem" }}>
            {watchTime.valueText}
          </p>
          <span className="text-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>Quality: {watchTime.quality}</span>
        </div>

        {/* Average View Percentage */}
        <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Avg Retention</span>
          <p style={{ fontSize: "1.4rem", fontWeight: 700, color: "var(--text-primary)", marginTop: "0.2rem" }}>
            {avgViewPct.valueText}
          </p>
          <span className="text-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>Quality: {avgViewPct.quality}</span>
        </div>

        {/* Impression CTR */}
        <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Impression CTR</span>
          <p style={{ fontSize: "1.4rem", fontWeight: 700, color: "var(--text-primary)", marginTop: "0.2rem" }}>
            {ctr.valueText}
          </p>
          <span className="text-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>Quality: {ctr.quality}</span>
        </div>
      </div>

      {/* Secondary Metrics */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: "0.75rem",
        }}
      >
        <div style={{ padding: "0.65rem 0.85rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>Likes</span>
          <p style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary)", marginTop: "0.15rem" }}>{likes.valueText}</p>
        </div>
        <div style={{ padding: "0.65rem 0.85rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>Comments</span>
          <p style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary)", marginTop: "0.15rem" }}>{comments.valueText}</p>
        </div>
        <div style={{ padding: "0.65rem 0.85rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>Engagement Rate</span>
          <p style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary)", marginTop: "0.15rem" }}>{engagement.valueText}</p>
        </div>
      </div>
    </div>
  );
}
