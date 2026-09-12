"use client";

import { useState } from "react";
import { refreshVideoAnalytics, type VideoAnalyticsSummary } from "@/lib/api";
import { toFiniteNumber } from "@/lib/formatters";
import {
  EmptyState,
  ErrorState,
  PageSection,
  StatusBadge,
} from "@/components/ui";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
  statusTone,
} from "@/components/workflow/WorkflowPrimitives";

interface Props {
  analytics: VideoAnalyticsSummary | null;
  onRefresh?: () => void;
}

export function AnalyticsPerformanceCard({ analytics, onRefresh }: Props) {
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!analytics)
    return (
      <EmptyState
        title="No performance observation"
        description="Analytics begins after a published video is verified and scheduled metric collection runs."
      />
    );
  const currentAnalytics = analytics;

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      await refreshVideoAnalytics(currentAnalytics.publish_intent_id);
      onRefresh?.();
    } catch (reason: unknown) {
      setError(
        reason instanceof Error ? reason.message : "Analytics refresh failed.",
      );
    } finally {
      setRefreshing(false);
    }
  }

  function metric(
    name: string,
    formatter: (value: number) => string = (value) => value.toLocaleString(),
  ) {
    const value = currentAnalytics.latest_metrics[name];
    if (!value || value.value === null)
      return {
        value: value?.quality || "Pending",
        quality: value?.quality || "NOT_READY",
      };
    const number = toFiniteNumber(value.value);
    return {
      value: number === null ? String(value.value) : formatter(number),
      quality: value.quality,
    };
  }

  const metrics = [
    ["Views", metric("views")],
    [
      "Watch time",
      metric(
        "watch_time_seconds",
        (value) => `${(value / 3600).toFixed(1)} hrs`,
      ),
    ],
    [
      "Average retention",
      metric("average_percentage_viewed", (value) => `${value.toFixed(1)}%`),
    ],
    [
      "Impression CTR",
      metric("impression_ctr_percent", (value) => `${value.toFixed(2)}%`),
    ],
    ["Likes", metric("likes")],
    ["Comments", metric("comments")],
    [
      "Engagement",
      metric("engagement_rate_percent", (value) => `${value.toFixed(2)}%`),
    ],
  ] as const;

  return (
    <PageSection
      title="Authoritative performance analytics"
      description={`Video ${analytics.provider_video_id} · ${analytics.lifecycle_phase}`}
      actions={
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={refreshing}
          onClick={() => void refresh()}
        >
          {refreshing ? "Refreshing…" : "Refresh analytics"}
        </button>
      }
    >
      {error ? (
        <ErrorState title="Analytics refresh failed" description={error} />
      ) : null}
      <WorkflowStatusSummary
        metrics={[
          {
            label: "Asset state",
            value: analytics.asset_status,
            status: analytics.asset_status,
          },
          { label: "Lifecycle", value: analytics.lifecycle_phase },
          {
            label: "Last synchronized",
            value: analytics.last_polled_at
              ? new Date(analytics.last_polled_at).toLocaleString()
              : "Never",
          },
        ]}
      />
      <div className="workflow-card-grid">
        {metrics.map(([label, item]) => (
          <article className="workflow-card" key={label}>
            <span className="ui-eyebrow">{label}</span>
            <strong className="ui-kpi-value">{item.value}</strong>
            <StatusBadge tone={statusTone(item.quality)}>
              {item.quality}
            </StatusBadge>
          </article>
        ))}
      </div>
      <TechnicalDetails data={analytics} />
    </PageSection>
  );
}
