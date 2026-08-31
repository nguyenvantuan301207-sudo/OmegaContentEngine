"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useOperatorContext } from "@/lib/operator-context";
import {
  ChannelAnalyticsSummary,
  PublishIntent,
  VideoAnalyticsSummary,
  getChannelAnalytics,
  getVideoAnalytics,
  listPublishIntents,
} from "@/lib/api";
import { AnalyticsPerformanceCard } from "@/components/AnalyticsPerformanceCard";
import { ChannelContextBar } from "@/components/ChannelContextBar";

export default function AnalyticsPage() {
  const { selectedChannelId } = useOperatorContext();

  const [channelAnalytics, setChannelAnalytics] = useState<ChannelAnalyticsSummary | null>(null);
  const [videoAnalytics, setVideoAnalytics] = useState<VideoAnalyticsSummary | null>(null);
  const [publishedIntents, setPublishedIntents] = useState<PublishIntent[]>([]);
  const [selectedIntentId, setSelectedIntentId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!selectedChannelId) return;
    try {
      setLoading(true);
      setError(null);

      const [cAnalytics, intents] = await Promise.all([
        getChannelAnalytics(selectedChannelId).catch(() => null),
        listPublishIntents({ channel_id: selectedChannelId }).catch(() => []),
      ]);

      setChannelAnalytics(cAnalytics);
      setPublishedIntents(intents);

      const activeIntent = intents.find((i) => i.state === "PUBLISHED") || intents[0];
      if (activeIntent) {
        setSelectedIntentId(activeIntent.id);
        const vAnalytics = await getVideoAnalytics(activeIntent.id).catch(() => null);
        setVideoAnalytics(vAnalytics);
      } else {
        setSelectedIntentId("");
        setVideoAnalytics(null);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load analytics data");
    } finally {
      setLoading(false);
    }
  }, [selectedChannelId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSelectVideo = async (intentId: string) => {
    setSelectedIntentId(intentId);
    try {
      const vAnalytics = await getVideoAnalytics(intentId).catch(() => null);
      setVideoAnalytics(vAnalytics);
    } catch {
      setVideoAnalytics(null);
    }
  };

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "1.5rem" }}>
      {/* Universal Channel Context Bar */}
      <ChannelContextBar currentTab="analytics" />

      {/* Page Header */}
      <div className="page-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.25rem" }}>
            <h1 className="page-title">📊 Channel & Content Analytics</h1>
            <span className="badge badge-active">OMEGA-012</span>
          </div>
          <p className="page-subtitle">
            Authoritative YouTube Data API v3 and Analytics API v2 polling, multi-window performance curves, and quality metrics.
          </p>
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
          }}
        >
          {error}
        </div>
      )}

      {/* Section 1: Channel-Level High-Level KPIs */}
      <div
        className="card"
        style={{
          padding: "1.25rem",
          marginBottom: "1.5rem",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "0.75rem", marginBottom: "1rem" }}>
          <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)" }}>
            Channel Lifetime Snapshot
          </h3>
          <button onClick={loadData} disabled={loading} className="btn btn-secondary btn-sm" style={{ fontSize: "0.75rem" }}>
            ↻ Refresh Metrics
          </button>
        </div>

        {channelAnalytics ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.85rem",
            }}
          >
            <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
              <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Total Views</span>
              <p style={{ fontSize: "1.35rem", fontWeight: 700, color: "var(--text-primary)", marginTop: "0.2rem" }}>
                {channelAnalytics.total_views !== undefined && channelAnalytics.total_views !== null ? channelAnalytics.total_views.toLocaleString() : "—"}
              </p>
            </div>
            <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
              <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Subscribers</span>
              <p style={{ fontSize: "1.35rem", fontWeight: 700, color: "var(--accent-secondary)", marginTop: "0.2rem" }}>
                {channelAnalytics.subscriber_count !== undefined && channelAnalytics.subscriber_count !== null ? channelAnalytics.subscriber_count.toLocaleString() : "—"}
              </p>
            </div>
            <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
              <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Public Videos</span>
              <p style={{ fontSize: "1.35rem", fontWeight: 700, color: "var(--text-primary)", marginTop: "0.2rem" }}>
                {channelAnalytics.video_count !== undefined && channelAnalytics.video_count !== null ? channelAnalytics.video_count : "—"}
              </p>
            </div>
            <div style={{ padding: "0.85rem 1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
              <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Total Watch Time</span>
              <p style={{ fontSize: "1.35rem", fontWeight: 700, color: "var(--status-success)", marginTop: "0.2rem" }}>
                {channelAnalytics.aggregate_watch_time_seconds !== undefined && channelAnalytics.aggregate_watch_time_seconds !== null ? `${(channelAnalytics.aggregate_watch_time_seconds / 3600).toFixed(1)} hrs` : "—"}
              </p>
            </div>
          </div>
        ) : (
          <div style={{ textAlign: "center", padding: "1.5rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
            No channel-level metrics synced yet. Connect an active YouTube account to pull subscriber and view metrics.
          </div>
        )}
      </div>

      {/* Section 2: Individual Video Performance Tracker */}
      <div style={{ marginBottom: "1.5rem" }}>
        {publishedIntents.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1rem", flexWrap: "wrap" }}>
            <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--text-secondary)" }}>
              Select Published Content Item:
            </label>
            <select
              value={selectedIntentId}
              onChange={(e) => handleSelectVideo(e.target.value)}
              className="form-select"
              style={{ minWidth: "320px", fontSize: "0.85rem", padding: "0.4rem 0.75rem" }}
            >
              {publishedIntents.map((intent) => (
                <option key={intent.id} value={intent.id}>
                  {intent.title} [{intent.state}] (v{intent.revision_number})
                </option>
              ))}
            </select>
          </div>
        )}

        <AnalyticsPerformanceCard
          analytics={videoAnalytics}
          onRefresh={() => handleSelectVideo(selectedIntentId)}
        />
      </div>
    </div>
  );
}
